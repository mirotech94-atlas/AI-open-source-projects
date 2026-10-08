"""The Vault chat provider ladder: Groq -> Anthropic -> `rules.answer`.

Port of Nexus `call_groq`/`call_anthropic`/`chat` (`server.py:582-668`),
adapted so a single `chat()` call is one round of one conversation (the
caller -- `routers/chat.py` -- owns the tool-round loop and message
history; this module never sees a `Session`, `Tenant` or the tool
registry directly, only what `chat()`'s `db`/`tenant`/`question`/`history`/`role`
kwargs forward to `rules.answer` on the final fallback).

Every provider call has a 30s timeout. Groq gets one retry on 429
(respecting `Retry-After`); a hard failure (network error, non-2xx other
than 429, malformed response) is caught and the ladder just moves to the
next provider -- `chat()` itself never raises and never surfaces a 500 to
the caller, worst case it falls all the way through to the deterministic
rules answer.
"""

from __future__ import annotations

import json
import re
import logging
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from ..config import settings
from . import rules

log = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
TIMEOUT_S = 30
#: Hard cap on how long a 429's `Retry-After` may park a worker. The header
#: is attacker/misconfiguration-controlled, so an uncapped sleep is a
#: worker-exhaustion primitive; `routers/chat.py` also holds a whole-turn
#: deadline above this.
RETRY_AFTER_CAP_S = 10.0
_SSL_CTX = ssl.create_default_context()


@dataclass
class ProviderReply:
    text: str = ""
    tool_calls: list[dict] = field(default_factory=list)  # [{"name": ..., "args": {...}}, ...]
    provider: str = ""   # "groq/<model>" | "anthropic/<model>" | "rules"


def _describe_failure(exc: Exception) -> str:
    """A safe-to-log one-liner for a provider failure -- HTTP status plus
    the response body (the account/billing/model errors both providers
    return are plain JSON, never the key we sent), or just the exception
    text for a network-level failure. Never touches `settings.*_api_key`
    or the request we built, only what came back from the wire."""
    if isinstance(exc, urllib.error.HTTPError):
        try:
            body = exc.read().decode("utf-8", "replace")[:500]
        except Exception:
            body = "<no body>"
        return f"HTTP {exc.code}: {body}"
    return f"{type(exc).__name__}: {exc}"


def _groq_available() -> bool:
    return bool(settings.groq_api_key)


def _anthropic_available() -> bool:
    return bool(settings.anthropic_api_key)


def configured_ladder() -> list[str]:
    """The provider ladder this install will actually walk, by name, in order
    -- what the chat panel's header caption reports ("via anthropic -> groq ->
    rules"). Only *which* providers are configured, never a key, a key
    fragment or a model name: the caption is rendered for every signed-in
    user, so it must stay free of anything secret. `rules` is always the last
    rung because `chat()` below always falls through to it."""
    ladder: list[str] = []
    if _groq_available():
        ladder.append("groq")
    if _anthropic_available():
        ladder.append("anthropic")
    ladder.append("rules")
    return ladder


def _tool_specs_openai(tools) -> list[dict]:
    return [{"type": "function", "function": {"name": t.name, "description": t.description,
                                               "parameters": t.schema}} for t in (tools or [])]


def _tool_specs_anthropic(tools) -> list[dict]:
    return [{"name": t.name, "description": t.description, "input_schema": t.schema} for t in (tools or [])]


#: Added to whatever wait a 429 asks for. Groq reports the remaining window to
#: four decimal places and the clock keeps moving while we parse it, so
#: retrying at exactly the stated instant lands just inside the window and
#: earns a second 429.
RETRY_MARGIN_S = 0.75

#: Groq's per-model on-demand limits (console.groq.com/settings/limits,
#: 2026-09-16). `tpm` is the one that bites: a chat turn carries ~5K tokens
#: of DATA, so an 8K model has room for one turn a minute and the second
#: earns a 429. Rather than sleep behind it, the ladder hands the turn to the
#: next `chat` model whose window is open. Only `chat`-role models answer
#: users: the prompt-guard pair are classifiers, safeguard is a moderator
#: and allam is Arabic-first -- none of them should ever author a reply.
GROQ_MODELS: dict[str, dict] = {
    "openai/gpt-oss-120b": {"role": "chat", "rpm": 30, "rpd": 1_000, "tpm": 8_000, "tpd": 200_000},
    "qwen/qwen3.8-27b": {"role": "chat", "rpm": 30, "rpd": 1_000, "tpm": 8_000, "tpd": 200_000},
    "openai/gpt-oss-20b": {"role": "chat", "rpm": 30, "rpd": 1_000, "tpm": 8_000, "tpd": 200_000},
    "groq/compound": {"role": "chat", "rpm": 30, "rpd": 250, "tpm": 70_000, "tpd": None},
    "groq/compound-mini": {"role": "chat", "rpm": 30, "rpd": 250, "tpm": 70_000, "tpd": None},
    "openai/gpt-oss-safeguard-20b": {"role": "moderation", "rpm": 30, "rpd": 1_000, "tpm": 8_000, "tpd": 200_000},
    "allam-2-7b": {"role": "chat-ar", "rpm": 30, "rpd": 7_000, "tpm": 6_000, "tpd": 500_000},
    "meta-llama/llama-prompt-guard-2-22m": {"role": "guard", "rpm": 30, "rpd": 14_400, "tpm": 15_000, "tpd": 500_000},
    "meta-llama/llama-prompt-guard-2-86m": {"role": "guard", "rpm": 30, "rpd": 14_400, "tpm": 15_000, "tpd": 500_000},
}

#: A 429 asking for at most this long is waited out on the same model (the
#: window is about to roll); a longer one parks the model and moves on.
QUICK_RETRY_S = 3.0

#: How long a model that answered 404 (the account has no access to it) is
#: left out of the pool. See `_call_groq`.
MODEL_MISSING_PARK_S = 3600.0

#: model id -> monotonic time its rate-limit window reopens. Process-local:
#: each worker learns its own, which is fine because the limit is per org
#: and every worker hitting it sees the same 429.
_GROQ_COOLDOWN: dict[str, float] = {}


class GroqRateLimited(Exception):
    """Every model in the pool is rate-limited (or the one tried was, for
    longer than `QUICK_RETRY_S`); the ladder should move on."""

    def __init__(self, model: str, wait: float):
        super().__init__(f"groq {model} rate-limited for {wait:.1f}s")
        self.model, self.wait = model, wait


def failover_status() -> dict:
    """What the Integrations page shows under "AI failover": the ladder with
    which rungs are configured, and every Groq model with its limits, its
    role, whether it answers users, and how long it is still parked behind a
    429. Names only -- no key, key fragment or header ever leaves here."""
    groq_ok, anth_ok = _groq_available(), _anthropic_available()
    ladder = [
        {"name": "groq", "configured": groq_ok,
         "note": "Model pool; a rate-limited model hands the turn to the next one." if groq_ok
                 else "No GROQ API key set on the hub."},
        {"name": "anthropic", "configured": anth_ok,
         "note": "Answers when every Groq model is parked." if anth_ok
                 else "No Anthropic API key set on the hub."},
        {"name": "rules", "configured": True,
         "note": "Built-in answers; always the last rung."},
    ]
    now = time.monotonic()
    models = []
    for model, spec in GROQ_MODELS.items():
        preferred = ("first" if model == settings.chat_model_first
                     else "tools" if model == settings.chat_model_tools else "")
        models.append({
            "model": model, "role": spec["role"], "in_pool": spec["role"] == "chat",
            "preferred": preferred, "rpm": spec["rpm"], "rpd": spec["rpd"],
            "tpm": spec["tpm"], "tpd": spec["tpd"],
            "cooling_s": round(max(0.0, _GROQ_COOLDOWN.get(model, 0.0) - now), 1),
        })
    return {"ladder": ladder, "models": models}


def _groq_pool(round_idx: int) -> list[str]:
    """Models to try this round, preferred first: the configured model for
    the round, then every other `chat`-role model in table order."""
    preferred = settings.chat_model_first if round_idx == 0 else settings.chat_model_tools
    rest = [m for m, spec in GROQ_MODELS.items() if spec["role"] == "chat" and m != preferred]
    return [preferred] + rest


def _retry_after(e: urllib.error.HTTPError) -> float:
    """How long to wait before retrying a 429.

    The `retry-after` header is the polite answer, but Groq's token-per-minute
    429 does not send one -- it puts the wait in the error body ("Please try
    again in 8.1525s"). The old code defaulted to a flat 8s in that case,
    which was very slightly too short: every TPM rejection retried a fraction
    of a second early, got refused again, and fell through to the rule-based
    answerer. Roughly every other chat turn was answered by keyword matching
    rather than a model, which is most of what "the bot does not understand"
    actually was.

    Reads the header, then the body, then falls back -- always with a margin,
    always capped so a turn cannot stall behind a long window.
    """
    header = e.headers.get("retry-after") if e.headers else None
    if header:
        try:
            return min(float(header) + RETRY_MARGIN_S, RETRY_AFTER_CAP_S)
        except (TypeError, ValueError):
            pass
    try:
        # `e.read()` is single-shot; the caller has not consumed it here.
        body = e.read().decode("utf-8", "replace")
    except Exception:
        body = ""
    hit = re.search(r"try again in ([0-9.]+)\s*s", body, re.I)
    if hit:
        try:
            return min(float(hit.group(1)) + RETRY_MARGIN_S, RETRY_AFTER_CAP_S)
        except ValueError:
            pass
    return min(8.0 + RETRY_MARGIN_S, RETRY_AFTER_CAP_S)


def _call_groq_model(model: str, messages: list[dict], tools, want_tools: bool) -> ProviderReply:
    body = {
        # Groq counts `max_tokens` toward the per-minute budget as if it were
        # spent, so this reservation is paid on every turn. The answer format
        # is one verdict line, at most six bullets and a Next line -- 300
        # tokens is a long one -- and the largest tool call (25 MACs) is
        # ~250. 550 keeps a comfortable margin without pre-paying 700.
        "model": model, "max_tokens": 550, "temperature": 0.2, "messages": messages,
    }
    if want_tools:
        body["tools"] = _tool_specs_openai(tools)
        body["tool_choice"] = "auto"
    if model.startswith("openai/"):
        body["reasoning_effort"] = "low"

    # Groq's edge (Cloudflare) rejects Python's default User-Agent -- present as curl, same as Nexus.
    headers = {"Authorization": f"Bearer {settings.groq_api_key}", "Content-Type": "application/json",
               "User-Agent": "curl/8.7"}
    req = urllib.request.Request(GROQ_URL, data=json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S, context=_SSL_CTX) as r:
            j = json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code != 429:
            raise
        wait = _retry_after(e)
        if wait > QUICK_RETRY_S:
            _GROQ_COOLDOWN[model] = time.monotonic() + wait
            raise GroqRateLimited(model, wait) from e
        time.sleep(wait)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S, context=_SSL_CTX) as r:
                j = json.loads(r.read())
        except urllib.error.HTTPError as e2:
            if e2.code != 429:
                raise
            wait = _retry_after(e2)
            _GROQ_COOLDOWN[model] = time.monotonic() + wait
            raise GroqRateLimited(model, wait) from e2

    msg = j["choices"][0]["message"]
    calls = []
    for c in msg.get("tool_calls") or []:
        try:
            args = json.loads(c["function"].get("arguments") or "{}")
        except Exception:
            args = {}
        if not isinstance(args, dict):
            # A payload that parses to a list or a string would otherwise
            # reach `dispatch(**args)` as a non-mapping.
            args = {}
        calls.append({"name": c["function"]["name"], "args": args})
    return ProviderReply(text=(msg.get("content") or "").strip(), tool_calls=calls, provider=f"groq/{model}")


def _call_groq(messages: list[dict], tools, want_tools: bool, round_idx: int) -> ProviderReply:
    """Try the round's pool in order, skipping models whose window is still
    closed. A model that 429s for longer than `QUICK_RETRY_S` is parked and
    the next one takes the turn; a non-429 HTTP error (a model that rejects
    our tool schema, say) is logged and also skipped. Raises the last error
    when nothing in the pool could answer, so the ladder falls through."""
    last: Exception | None = None
    now = time.monotonic()
    # The per-minute budget of the largest model that has already said this
    # request is too large for it. A 413 "Request too large ... tokens per
    # minute" is about the SIZE of the request, not the moment it was sent, so
    # every model whose budget is no larger will refuse it the same way. On
    # production that was all three 8k-TPM models, one after another, on
    # every turn -- round trips spent learning the same thing three times.
    too_big_at = 0
    for model in _groq_pool(round_idx):
        if _GROQ_COOLDOWN.get(model, 0.0) > now:
            continue
        if GROQ_MODELS.get(model, {}).get("tpm") and GROQ_MODELS[model]["tpm"] <= too_big_at:
            continue
        try:
            return _call_groq_model(model, messages, tools, want_tools)
        except GroqRateLimited as e:
            log.info("groq %s rate-limited for %.1fs; trying next model", model, e.wait)
            last = e
        except urllib.error.HTTPError as e:
            log.warning("groq %s failed: %s; trying next model", model, _describe_failure(e))
            last = e
            if e.code == 404:
                # model_not_found: this account cannot use the model at all.
                # `groq/compound` and `-mini` answered this on every turn.
                # Park it for an hour rather than for good, so an account
                # upgrade or a model coming back is picked up without a
                # restart.
                _GROQ_COOLDOWN[model] = now + MODEL_MISSING_PARK_S
            elif e.code == 413:
                too_big_at = max(too_big_at, GROQ_MODELS.get(model, {}).get("tpm") or 0)
    if last is not None:
        raise last
    raise GroqRateLimited("*", max((t - now for t in _GROQ_COOLDOWN.values()), default=0.0))


#: Wraps a mid-conversation instruction from Vault's own loop once it has
#: become a `user` turn, so the model can tell it from what the customer typed.
_LOOP_NOTE = "<vault_instruction>{}</vault_instruction>"
_TOOL_OUTPUT_PREFIX = "<untrusted_tool_output"


def _anthropic_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    """`(system, messages)` for the Messages API, from the one conversation
    list `routers/chat.py` keeps for every provider.

    That list is in the shape Groq's OpenAI-compatible endpoint takes, and
    three things in it are invalid here. Each broke a real turn:

    - **A `system` entry after the first.** The chat loop appends one (the
      do-it-now nudge) right after the model promised a change instead of
      making it. The Messages API only accepts a mid-conversation system
      message directly after a `user` turn, and Claude Sonnet 5 not at all,
      so the request 400'd -- on exactly the turn where a customer had said
      "both" to "which devices do you recognise?" -- and the answer fell
      through to the rule set, which cannot trust a device. It is sent as a
      `user` turn wrapped in `<vault_instruction>`, which is what it is: an
      instruction to the model that the customer did not type.
    - **Tool output as an `assistant` turn.** After any tool round the list
      ended on the model's side, which the Messages API treats as a prefill
      and current models reject. Tool output is something the model READS,
      so it goes out as `user`, still inside its `<untrusted_tool_output>`
      wrapper -- the system prompt's rule that those are observations, not
      instructions, is unchanged.
    - **An empty `assistant` turn**, appended whenever a tool call came back
      with no text. Dropped; it said nothing.

    Adjacent turns that land on the same role are joined, and a conversation
    that still ends on the model gets a closing instruction to continue.
    Groq keeps the untranslated list: this is a boundary adapter for one
    provider, not a change to what the chat loop builds.
    """
    system_parts: list[str] = []
    convo: list[dict] = []
    for i, msg in enumerate(messages):
        role = msg.get("role")
        content = msg.get("content") or ""
        if role == "system" and i == 0:
            system_parts.append(content)
            continue
        if role == "system":
            role, content = "user", _LOOP_NOTE.format(content)
        elif role == "assistant" and content.lstrip().startswith(_TOOL_OUTPUT_PREFIX):
            role = "user"
        elif role not in ("user", "assistant"):
            role = "user"
        if not content.strip():
            continue
        if convo and convo[-1]["role"] == role:
            convo[-1] = {"role": role, "content": convo[-1]["content"] + "\n\n" + content}
        else:
            convo.append({"role": role, "content": content})
    if convo and convo[-1]["role"] == "assistant":
        convo.append({"role": "user", "content": _LOOP_NOTE.format("Continue.")})
    return "\n\n".join(system_parts), convo


def _call_anthropic(messages: list[dict], tools, want_tools: bool) -> ProviderReply:
    model = settings.chat_model_anthropic
    system, convo = _anthropic_messages(messages)

    body = {"model": model, "max_tokens": 900, "system": system, "messages": convo}
    if want_tools:
        body["tools"] = _tool_specs_anthropic(tools)
    headers = {"x-api-key": settings.anthropic_api_key, "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    req = urllib.request.Request(ANTHROPIC_URL, data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT_S, context=_SSL_CTX) as r:
        j = json.loads(r.read())

    content = j.get("content") or []
    calls = [{"name": b["name"], "args": b.get("input") or {}} for b in content if b.get("type") == "tool_use"]
    text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
    return ProviderReply(text=text.strip(), tool_calls=calls, provider=f"anthropic/{model}")


def chat(
    messages: list[dict], tools=None, want_tools: bool = False, *, round_idx: int = 0,
    db=None, tenant=None, question: str = "", history=None, role: str = "viewer",
) -> ProviderReply:
    """One round of one conversation. Tries Groq, then Anthropic, then
    `rules.answer` -- the first that doesn't raise wins. `round_idx` picks
    which Groq model this round uses (0 = `chat_model_first`, carries the
    DATA blob; >=1 = `chat_model_tools`, the caller has already dropped the
    blob from `messages[0]` by then -- see routers/chat.py)."""
    if _groq_available():
        try:
            return _call_groq(messages, tools, want_tools, round_idx)
        except Exception as exc:
            log.warning("groq provider failed, falling through: %s", _describe_failure(exc))
    if _anthropic_available():
        try:
            return _call_anthropic(messages, tools, want_tools)
        except Exception as exc:
            log.warning("anthropic provider failed, falling through: %s", _describe_failure(exc))
    # `history`/`role` are for the rules rung only: the model providers get
    # the conversation through `messages` and the role through the tool set
    # the caller hands them, but `rules.answer` sees neither -- and without
    # the thread it cannot tell a follow-up from an opening question, which
    # is what made it answer "what can I do to help" with a topic list.
    text = (rules.answer(db, tenant, question, history=history, role=role)
            if db is not None and tenant is not None else "")
    return ProviderReply(text=text, tool_calls=[], provider="rules")
