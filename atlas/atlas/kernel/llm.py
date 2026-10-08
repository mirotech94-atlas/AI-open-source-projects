"""Multi-provider reasoning core.

ATLAS does not have "a model". It has a routing table. Work is classified by
what it actually needs — speed, depth, privacy, or a second opinion — and sent
to the cheapest provider that satisfies it. Three reasons this matters:

  cost      classifying 400 emails a day on a frontier model is absurd
  privacy   personal mail and browser history can stay on a local model
  accuracy  a critical, irreversible call deserves two models that disagree
"""
from __future__ import annotations

import dataclasses
import enum
import json
import logging
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol, overload

from ..modules._http import redact, redact_stored

log = logging.getLogger("atlas.llm")


class Need(enum.StrEnum):
    """What a piece of work needs. Modules and agents declare this; they never
    name a model directly, so the routing table can change without touching
    any caller."""

    TRIVIAL = "trivial"      # classify, extract, tag. High volume, low stakes.
    DRAFT = "draft"          # write a reply, summarise a thread.
    REASON = "reason"        # diagnose an incident, correlate evidence.
    HARD = "hard"            # novel failure, long synthesis, plan a change.
    PRIVATE = "private"      # never leaves the box. Personal mail, history.
    CRITICAL = "critical"    # two providers, and they must agree.


@dataclass(frozen=True, slots=True)
class Message:
    role: str                # "user" | "assistant"
    content: str


@dataclass(frozen=True, slots=True)
class Image:
    """One still for a vision model (Air Glass's "ATLAS, look"). Held in
    memory for the length of one call and never written anywhere. Its repr
    is its size, so a log line or a traceback can never carry the bytes."""

    data: bytes
    mime: str = "image/jpeg"

    def b64(self) -> str:
        import base64
        return base64.b64encode(self.data).decode("ascii")

    def __repr__(self) -> str:
        return f"Image({len(self.data)} bytes, {self.mime})"


@dataclass(frozen=True, slots=True)
class Completion:
    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: list[dict[str, Any]] | None = None


@dataclass(frozen=True, slots=True)
class ToolsUnsupported:
    """The model was asked to call tools and cannot. Returned, never raised,
    so the caller has to decide what an answer without its reads is worth --
    and is never handed one that looks like a fresh read."""

    model: str
    provider: str = "ollama"


class LocalModelUnavailable(RuntimeError):
    """PRIVATE work had nowhere local to run. It is never sent anywhere else,
    so the caller gets this instead of an answer. A RuntimeError, so callers
    that already treat "no provider" as a failure keep doing so."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"local model unavailable: {reason}")


class Provider(Protocol):
    name: str
    async def complete(
        self,
        system: str,
        messages: Sequence[Message],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 2048,
    ) -> Completion | ToolsUnsupported: ...


def function_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The harness's tool schemas (Anthropic's shape: name, description,
    input_schema) in the OpenAI/Ollama function-calling shape."""
    return [
        {"type": "function",
         "function": {"name": t["name"],
                      "description": t["description"],
                      "parameters": t["input_schema"]}}
        for t in tools
    ]


def _arguments(raw: Any) -> dict[str, Any]:
    """Tool-call arguments: an object from Ollama, a JSON string from the
    OpenAI-shaped APIs. Anything unreadable is an empty call, not a crash."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


# ---------------------------------------------------------------- providers

class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str) -> None:
        self.model = model
        self._client = None

    async def complete(self, system, messages, tools=None, max_tokens=2048):
        if self._client is None:
            from anthropic import AsyncAnthropic
            self._client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if tools:
            kwargs["tools"] = tools
        resp = await self._client.messages.create(**kwargs)
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [
            {"name": b.name, "input": b.input, "id": b.id}
            for b in resp.content if b.type == "tool_use"
        ]
        return Completion(
            text=text, provider=self.name, model=self.model,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            tool_calls=calls or None,
        )


class OpenAIProvider:
    """Second opinion on CRITICAL work. A single model agreeing with itself is
    not verification."""

    name = "openai"

    def __init__(self, model: str) -> None:
        self.model = model
        self._client = None

    async def complete(self, system, messages, tools=None, max_tokens=2048):
        if self._client is None:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
        resp = await self._client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[{"role": "system", "content": system}]
            + [{"role": m.role, "content": m.content} for m in messages],
        )
        return Completion(
            text=resp.choices[0].message.content or "",
            provider=self.name, model=self.model,
            input_tokens=resp.usage.prompt_tokens,
            output_tokens=resp.usage.completion_tokens,
        )


class OllamaProvider:
    """Local, on the ATLAS box. Nothing sent here leaves the machine — this is
    what makes it acceptable to feed personal mail and browser history to a
    model at all.

    Tools go over Ollama's native tool calling (`/api/chat`). A model that
    cannot call tools answers HTTP 400 "... does not support tools"; that
    comes back as `ToolsUnsupported`, never as an answer made without the
    reads it was asked to make.
    """

    name = "ollama"
    local = True        # the only kind of provider PRIVATE work may reach

    def __init__(self, model: str) -> None:
        self.model = model
        self.base = os.getenv("OLLAMA_URL", "http://ollama:11434")
        # Measured on atlas.example.com: 5.1 tok/s on one vCPU with no GPU, and a
        # cold load costs two minutes before the first token. 120s timed out
        # before a real answer could arrive.
        self.timeout = _local_timeout()

    async def complete(self, system, messages, tools=None, max_tokens=2048):
        if tools:
            return await self._chat(system, messages, tools, max_tokens)
        import httpx
        prompt = "\n\n".join(f"{m.role}: {m.content}" for m in messages)
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.post(f"{self.base}/api/generate", json={
                "model": self.model,
                "system": system,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": max_tokens},
            })
            r.raise_for_status()
            data = r.json()
        # ollama answers a missing model with a JSON error and a 200, so
        # raise_for_status() lets it through as an empty completion. An empty
        # answer that looks like a successful one is worse than a failure.
        if err := data.get("error"):
            raise RuntimeError(f"ollama: {err}")
        return Completion(text=data.get("response", ""),
                          provider=self.name, model=self.model)

    async def _chat(
        self,
        system: str,
        messages: Sequence[Message],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> Completion | ToolsUnsupported:
        """A turn that carries tools: Ollama's native tool calling on
        /api/chat, mapped to the same tool-call shape AnthropicProvider
        returns, so the harness needs no Ollama-specific code."""
        import httpx
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}]
            + [{"role": m.role, "content": m.content} for m in messages],
            "tools": function_tools(tools),
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.post(f"{self.base}/api/chat", json=payload)
        if r.status_code == 400 and _NO_TOOLS in r.text:
            LOCAL_TOOLS[self.model] = False
            return ToolsUnsupported(self.model)
        r.raise_for_status()
        data = r.json()
        # Ollama answers some failures (a model that is not pulled) with an
        # error body on a 200. That is a failure, and says nothing about
        # whether the model can call tools.
        if err := data.get("error"):
            raise RuntimeError(f"ollama error: {err}")
        LOCAL_TOOLS[self.model] = True
        message = data.get("message") or {}
        calls = []
        for i, c in enumerate(message.get("tool_calls") or []):
            if not isinstance(c, dict):
                log.warning("%s returned a tool call that is not an object; "
                            "skipped", self.model)
                continue
            function = c.get("function") or {}
            calls.append({"name": function.get("name", ""),
                          "input": _arguments(function.get("arguments")),
                          "id": c.get("id") or f"ollama-{i}"})
        return Completion(
            text=message.get("content") or "",
            provider=self.name, model=self.model,
            input_tokens=int(data.get("prompt_eval_count") or 0),
            output_tokens=int(data.get("eval_count") or 0),
            tool_calls=calls or None,
        )

    async def available(self) -> bool:
        """Is the configured model actually pulled?

        Asked once at boot. `ATLAS_MODEL_LOCAL` defaulted to a model nobody had
        pulled, so every local call failed and every one of them fell through
        to the API — including PRIVATE work, which is the one route that exists
        to keep personal mail off someone else's server. It looked identical to
        a working local model from the outside: a warning per call, and answers
        that kept arriving.
        """
        import httpx
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(f"{self.base}/api/tags")
                r.raise_for_status()
                names = {m.get("name", "") for m in r.json().get("models", ())}
        except Exception as exc:                        # noqa: BLE001
            log.error("ollama unreachable at %s (%s: %s)",
                      self.base, type(exc).__name__, exc)
            return False
        if self.model in names or f"{self.model}:latest" in names:
            return True
        log.error("local model %r is not pulled — have %s. Run: "
                  "docker compose exec ollama ollama pull %s",
                  self.model, sorted(names) or "nothing", self.model)
        return False


_NO_TOOLS = "does not support tools"


def _local_timeout() -> float:
    """Seconds to wait for the local model. The box is slow (a few tokens a
    second, with a cold load of minutes), so the default is generous."""
    try:
        return float(os.getenv("ATLAS_LOCAL_TIMEOUT_S", "600"))
    except ValueError:
        return 600.0


def private_cloud_allowed() -> bool:
    """`ATLAS_PRIVATE_ALLOW_CLOUD`: Miro's explicit opt-in to letting PRIVATE
    work fall through to a cloud model when the local one fails. Off unless
    set to 1, true or yes; unset means never. The one reader of the flag:
    `build_routes`, `_think_private` and `self_status` all ask here."""
    return os.getenv("ATLAS_PRIVATE_ALLOW_CLOUD", "").strip().lower() in (
        "1", "true", "yes")


_WARNED_PRIVATE_CLOUD: list[bool] = []      # once per process

# model -> whether it answered a request that carried tools. Absent means
# never tried, which `local_model_tools()` reports as unknown, not as yes.
LOCAL_TOOLS: dict[str, bool] = {}


def local_model() -> str | None:
    """The model PRIVATE work runs on, or None when there is none."""
    for provider in ROUTES.get(Need.PRIVATE, []):
        if getattr(provider, "local", False):
            return str(getattr(provider, "model", "")) or None
    return None


def local_model_tools() -> bool | None:
    """Whether the local model can call tools: True / False once a request
    carrying tools has been answered this process, None until then."""
    model = local_model()
    return LOCAL_TOOLS.get(model) if model else None


# ------------------------------------------------------------------- router

def _env(key: str, default: str) -> str:
    return os.getenv(key, default)


ROUTES: dict[Need, list[Provider]] = {}


# Checked live 2026-10-05: each answered and called a tool on a key with no
# credit. OpenRouter retires free models without notice; a dead one is skipped
# by its breaker, and ATLAS_MODELS_OPENROUTER_FREE replaces the list.
OPENROUTER_FREE_DEFAULT = (
    "nvidia/nemotron-3.5-lightning:free",
    "inclusionai/ling-3.0-flash-sante:free",
    "apodex/apodex-1.1-mini:free",
    "dots-studio/dots-3-note-preview:free",
)


def free_only() -> bool:
    return os.getenv(
        "ATLAS_FREE_ONLY", "false").strip().lower() in ("1", "true", "yes")


def openrouter_free() -> list[Provider]:
    """The zero-price OpenRouter models, in the order they are asked. A name
    that does not end in `:free` is refused here, so a typo in the env can
    never turn the free bench into a bill."""
    if not os.getenv("OPENROUTER_API_KEY"):
        return []
    raw = os.getenv("ATLAS_MODELS_OPENROUTER_FREE", "").strip()
    names = ([n.strip() for n in raw.split(",") if n.strip()] if raw
             else list(OPENROUTER_FREE_DEFAULT))
    out: list[Provider] = []
    for name in names:
        if not name.endswith(":free"):
            log.error("ATLAS_MODELS_OPENROUTER_FREE: %r is not a :free model; "
                      "left out", name)
            continue
        out.append(OpenRouterProvider(name))
    return out


def build_routes() -> None:
    """Called once at boot. Providers with no key are simply absent, and any
    Need that routes to them falls back to the next entry."""
    ROUTES.clear()
    have_anthropic = bool(os.getenv("ANTHROPIC_API_KEY"))
    have_openai = bool(os.getenv("OPENAI_API_KEY"))
    have_local = bool(os.getenv("OLLAMA_URL"))

    have_groq = bool(os.getenv("GROQ_API_KEY"))
    have_gemini = bool(os.getenv("GEMINI_API_KEY"))
    # Free tiers ahead of paid ones for work that does not need judgment. A
    # free tier is a daily cap, not a promise, so a free provider is never the
    # only entry for a Need — the paid model sits behind it and the day carries
    # on when the cap is hit. Set ATLAS_PREFER_FREE=false to put Anthropic
    # first everywhere.
    prefer_free = os.getenv(
        "ATLAS_PREFER_FREE", "true").strip().lower() in ("1", "true", "yes")

    fast = AnthropicProvider(_env("ATLAS_MODEL_FAST", "claude-haiku-4-5"))
    mid = AnthropicProvider(_env("ATLAS_MODEL_REASON", "claude-sonnet-4-5"))
    deep = AnthropicProvider(_env("ATLAS_MODEL_DEEP", "claude-opus-4-1"))
    local = OllamaProvider(_env("ATLAS_MODEL_LOCAL", "llama3.2:3b"))
    other = OpenAIProvider(_env("ATLAS_MODEL_SECOND", "gpt-4o"))
    # Free tier. Model names checked live: the gemini-2.5-* family answers 404
    # "no longer available to new users" on a current key. On a free-tier key
    # (prod, 2026-10-06) gemini-flash-lite-latest answers, gemini-flash-latest
    # answers when not 503 "high demand" (transient: fallen through, never
    # dropped), and gemini-pro-latest is 429 with no free quota at all.
    gem_fast = GeminiProvider(_env("ATLAS_MODEL_GEMINI_FAST", "gemini-flash-lite-latest"))
    gem_deep = GeminiProvider(_env("ATLAS_MODEL_GEMINI_DEEP", "gemini-flash-latest"))
    groq = GroqProvider(_env("ATLAS_MODEL_GROQ", "openai/gpt-oss-120b"))
    groq_fast = GroqProvider(_env("ATLAS_MODEL_GROQ_FAST", "openai/gpt-oss-20b"))
    cohere = CohereProvider(_env("ATLAS_MODEL_COHERE", "command-a-03-2025"))
    cohere_fast = CohereProvider(_env("ATLAS_MODEL_COHERE_FAST", "command-r-08-2024"))
    cerebras = CerebrasProvider(_env("ATLAS_MODEL_CEREBRAS", "gpt-oss-120b"))
    # Mistral's free tier (prod, 2026-10-06): ministral-8b-2512 and
    # ministral-14b-2512 answer; mistral-small-latest is 429 at its 20K
    # tokens a minute, mistral-large is tier_not_allowed. Its limits page
    # (read 2026-10-06) gives ministral-3b-2512 1.3M tokens a minute at 12.5
    # requests a second -- the fast voice/TRIVIAL entry -- and the 14B
    # 937.5K a minute at 0.5 a second, for DRAFT and depth.
    mistral = MistralProvider(_env("ATLAS_MODEL_MISTRAL", "ministral-14b-2512"))
    mistral_fast = MistralProvider(_env("ATLAS_MODEL_MISTRAL_FAST", "ministral-3b-2512"))
    have_mistral = bool(os.getenv("MISTRAL_API_KEY"))
    have_cerebras = bool(os.getenv("CEREBRAS_API_KEY"))
    extra_fast = [cohere_fast] if os.getenv("COHERE_API_KEY") else []
    extra_mid = ([cohere] if os.getenv("COHERE_API_KEY") else []) + (
        [mistral] if have_mistral else [])

    # Groq meters each model on its own (its rate-limit docs, read
    # 2026-10-06: 1K requests and 200K tokens a day apiece for gpt-oss-120b,
    # gpt-oss-20b and qwen3.8-27b), and a cycle asks several HARD questions
    # in the same few seconds: three models are three answers a minute where
    # one model was one. Miro: "pick all the free tiers".
    groq_alt = GroqProvider(_env("ATLAS_MODEL_GROQ_ALT", "qwen/qwen3.8-27b"))
    # Groq retired llama-3.1-8b-instant and llama-3.3-70b-versatile: both
    # answered 404 at the 2026-10-07 boot, and its /models list that day had
    # only gpt-oss-20b/120b, qwen3.8-27b and allam-2-7b for chat. A small or
    # big Groq model is now opt-in by name; discover_groq_models() still adds
    # any new chat model Groq lists.
    groq_small = ([GroqProvider(os.environ["ATLAS_MODEL_GROQ_SMALL"])]
                  if have_groq and os.getenv("ATLAS_MODEL_GROQ_SMALL") else [])
    groq_big = ([GroqProvider(os.environ["ATLAS_MODEL_GROQ_BIG"])]
                if have_groq and os.getenv("ATLAS_MODEL_GROQ_BIG") else [])
    groq_deep = [groq, groq_alt, groq_fast, *groq_big] if have_groq else []
    have_cloudflare = bool(os.getenv("CLOUDFLARE_API_TOKEN")
                           and os.getenv("CLOUDFLARE_ACCOUNT_ID"))
    cloudflare = ([CloudflareProvider(_env(
        "ATLAS_MODEL_CLOUDFLARE", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"))]
        if have_cloudflare else [])
    # Workers AI's free 10,000 neurons a day reset at 00:00 UTC (pricing
    # page, read 2026-10-06). The 8B fp8-fast model costs 4,119 neurons per
    # million input tokens and 34,868 per million output, the 70B 26,668 and
    # 204,805: the fast lane spends a sixth of the 70B's share per answer.
    cloudflare_fast = ([CloudflareProvider(_env(
        "ATLAS_MODEL_CLOUDFLARE_FAST", "@cf/meta/llama-3.1-8b-instruct-fp8-fast"))]
        if have_cloudflare else [])
    # Hugging Face's router: $0.10 of free credit a month for a free account
    # (pricing page, read 2026-10-06) -- a few hundred short answers, so it
    # is the last free entry before OpenRouter, never a lead.
    hf = [HuggingFaceProvider(_env("ATLAS_MODEL_HF", "openai/gpt-oss-120b:cheapest"))
          ] if os.getenv("HF_TOKEN") else []
    # GitHub Models is NOT here: GitHub retired it, inference API included,
    # on 2026-07-30 (github.blog changelog, read 2026-10-06).

    # The free bench, in the order it is tried. Empty when no key is set,
    # which leaves every chain below exactly as it was.
    #
    # free_fast (TRIVIAL, and so every voice turn) leads with the small,
    # non-reasoning models, each on its own allowance: Cerebras when the
    # account has it, Workers AI's 8B, Ministral 3B, Gemini Flash. The gpt-oss
    # and Qwen models follow, so a spent pool is one step in a long lane,
    # and the load spreads over five vendors' allowances, not one.
    free_fast = ([cerebras] if have_cerebras else []) + (
        groq_small) + cloudflare_fast + (
        [mistral_fast] if have_mistral else []) + (
        [gem_fast] if have_gemini else []) + (
        [groq_fast, groq_alt] if have_groq else []) + extra_fast + (
        [mistral] if have_mistral else [])
    free_mid = ([cerebras] if have_cerebras else []) + (
        [groq, *groq_big] if have_groq else []) + (
        [gem_fast] if have_gemini else []) + extra_mid + cloudflare + (
        [groq_alt] if have_groq else [])
    free_deep = ([gem_deep] if have_gemini else []) + groq_deep + cloudflare + (
        extra_mid) + ([cerebras] if have_cerebras else [])
    # OpenRouter's zero-price models: more free depth, and the only free
    # entries with a context wide enough for the prompts Groq answers 413 to.
    or_free = openrouter_free()
    free_fast, free_mid, free_deep = (
        free_fast + hf + or_free, free_mid + hf + or_free, free_deep + hf + or_free)

    # PRIVATE is personal mail and browser history. The whole reason it is its
    # own Need is that this content does not leave the box, so an API provider
    # is not a failover for it — it is the thing being avoided. Falling back to
    # Haiku here meant that while the local model was misconfigured, every
    # private request went to Anthropic and the only sign was a warning per
    # call. It fails closed, and says why -- unless Miro has explicitly opted
    # in with ATLAS_PRIVATE_ALLOW_CLOUD, which is warned about once.
    cloud_ok = private_cloud_allowed() and have_anthropic
    if cloud_ok and not _WARNED_PRIVATE_CLOUD:
        _WARNED_PRIVATE_CLOUD.append(True)
        log.warning("ATLAS_PRIVATE_ALLOW_CLOUD is set: PRIVATE work falls "
                    "through to %s when the local model fails", fast.model)
    if have_local:
        ROUTES[Need.PRIVATE] = [local] + (
            [fast] if cloud_ok else [])
        # The local model serves PRIVATE work and nothing else it does not
        # have to. On the box it runs a 3B model on one CPU with a 600 s
        # deadline (ATLAS_LOCAL_TIMEOUT_S); put first in TRIVIAL it made every
        # cheap classify wait on a swapping Ollama -- a reel-format extract
        # sat eleven minutes on 2026-09-25 and filed nothing. So TRIVIAL is
        # the free bench, then Haiku; the local model is the LAST resort,
        # only when no cloud key exists at all.
        cloud_trivial = free_fast + ([fast] if have_anthropic else [])
        ROUTES[Need.TRIVIAL] = cloud_trivial or [local]
    elif cloud_ok:
        log.warning("no local model — PRIVATE work will use the API, because "
                    "ATLAS_PRIVATE_ALLOW_CLOUD is set")
        ROUTES[Need.PRIVATE] = [fast]
        ROUTES[Need.TRIVIAL] = free_fast + [fast]
    elif have_anthropic:
        log.error("no local model — PRIVATE work has nowhere to run and will "
                  "be refused. Pull the model, or set "
                  "ATLAS_PRIVATE_ALLOW_CLOUD=true to send personal content to "
                  "the API deliberately.")
        ROUTES[Need.TRIVIAL] = free_fast + [fast]

    if have_anthropic:
        # A draft is rewritten by hand or approved before it goes anywhere, so
        # it is the natural place for the free bench to go first.
        ROUTES[Need.DRAFT] = (free_mid + [mid, fast] if prefer_free
                              else [mid, fast] + free_mid)
        # REASON and HARD decide things about his businesses. Anthropic leads
        # regardless of ATLAS_PREFER_FREE; the free models are depth behind it,
        # so an outage degrades quality instead of stopping ATLAS.
        ROUTES[Need.REASON] = [mid, deep] + free_deep
        ROUTES[Need.HARD] = [deep, mid] + free_deep
        # CRITICAL means two INDEPENDENT providers, not two Anthropic models.
        # Padding with `mid` here made think_twice compare Opus against Sonnet:
        # same vendor, same API, correlated failures — which satisfies neither
        # the cross-check nor the outage resilience it exists for. If there is
        # no second vendor, the chain is left short and think_twice escalates
        # rather than pretending to have agreed with itself.
        # Gemini is a genuinely separate vendor, which is what this needs — and
        # it goes ahead of OpenAI because that key answers 429 "no credits
        # remaining", so the cross-check was falling through it.
        ROUTES[Need.CRITICAL] = (
            [deep] + ([gem_deep] if have_gemini else [])
            + ([other] if have_openai else []))

    # Miro, 2026-10-05: "use free tiers ai models without credit not paid
    # tiers". With ATLAS_FREE_ONLY set, no paid model sits in any cloud chain
    # (PRIVATE is local and untouched). It is refused, loudly, when there is
    # no free bench: an instruction about cost must not leave ATLAS mute.
    if free_only():
        if free_deep:
            ROUTES[Need.TRIVIAL] = list(free_fast)
            ROUTES[Need.DRAFT] = list(free_mid)
            for need in (Need.REASON, Need.HARD, Need.CRITICAL):
                ROUTES[need] = list(free_deep)
            if have_local and Need.PRIVATE in ROUTES:
                ROUTES[Need.PRIVATE] = [local]
            log.warning("ATLAS_FREE_ONLY is set: every cloud chain is the "
                        "free bench (%s deployment(s)); no paid model is asked",
                        len(free_deep))
        else:
            log.error("ATLAS_FREE_ONLY is set but no free provider has a key; "
                      "keeping the usual chains")


async def verify_local() -> bool:
    """Prove the local model is really there, and drop it from every chain if
    it is not. Called once at boot, after build_routes().

    A model that is configured but not pulled failed on every call and fell
    through to the API, which looked like working failover and was in fact the
    privacy promise quietly inverted.
    """
    local = next((p for chain in ROUTES.values() for p in chain
                  if p.name == "ollama"), None)
    if local is None:
        return False
    for key in [k for k in DROPPED if k[1] == "ollama"]:
        del DROPPED[key]
    if await local.available():
        log.info("local model %s is pulled and serving", local.model)
        return True
    _record_dropped({"ollama"}, {"ollama": "not pulled or not serving at boot"})
    for need, chain in list(ROUTES.items()):
        ROUTES[need] = [p for p in chain if p.name != "ollama"]
    if not ROUTES.get(Need.PRIVATE):
        ROUTES.pop(Need.PRIVATE, None)
        log.error("PRIVATE work is now refused rather than sent to an API "
                  "provider — see the pull command above")
    return False


# Provider names verify_cloud() dropped at the last boot probe (401, 402,
# 403 or 404 -- configuration, not an outage). Read by `_resolve_pin()`, so
# a per-agent pin naming a provider already known dead at boot is skipped
# immediately with the fallback prefix, rather than retried against the
# same dead endpoint for up to the client's 120s timeout on every turn of
# every run (M3, Task 53 review fix round 1).
DEAD_PROVIDERS: set[str] = set()

# Every deployment the boot probes took out of a chain, so a status screen
# can still show it as unavailable instead of it silently vanishing:
# (need, provider, model) -> why, in words with the status code only (never
# the response body, which may echo an account). `verify_cloud` and
# `verify_local` each replace their own entries.
DROPPED: dict[tuple[str, str, str], str] = {}


def _record_dropped(names: set[str], why: dict[str, str]) -> None:
    for need, chain in ROUTES.items():
        for provider in chain:
            if provider.name in names:
                DROPPED[(str(need), provider.name,
                         str(getattr(provider, "model", "") or ""))] = why[provider.name]


async def verify_cloud() -> dict[str, str]:
    """Prove each optional provider actually serves, once, at boot.

    A key being present is not the same as a provider working, and this session
    found four counts of the difference: `hermes-4-70b` answers 404 "No
    endpoints found", the OpenAI key answers 429 "no credits remaining",
    Cerebras answers 402 `payment_required` because the free quota is not
    active on the account, and a fresh Mistral key answers 429 code 1300 until
    the free tier is activated. Every one of them sat in the chain looking like
    failover and delivering none.

    Only providers marked `optional` are probed — the paid primaries are not
    dropped on the strength of one request.

    Dropped on 401, 402, 403 and 404: a revoked key, an account with no
    allowance, a forbidden model, a model name that does not exist. Those are
    configuration, and they will not fix themselves before the next restart.

    **Not** dropped on 429, deliberately. A free tier that has spent its daily
    cap answers 429, and so does a tier that was never enabled — the status
    code cannot tell them apart. Dropping on it would take a provider that is
    merely rationed out of the chain until the next restart, when the per-call
    fallthrough already handles a spent cap and the cap resets by itself. A
    timeout or a 5xx is kept for the same reason: that is the outage the chain
    exists for, not evidence of misconfiguration.
    """
    probed: dict[str, str] = {}
    seen: dict[str, Any] = {}
    for chain in ROUTES.values():
        for provider in chain:
            if getattr(provider, "optional", False):
                seen.setdefault(_probe_key(provider), provider)

    dead: set[str] = set()
    dead_why: dict[str, str] = {}
    for name, provider in seen.items():
        try:
            # Generous enough to measure the provider rather than the
            # budget: an 8-token probe made Gemini fail its own check on
            # every boot, because thinking tokens are spent before any text
            # is written. GeminiProvider now enforces its own floor too.
            await provider.complete("Reply with one word.",
                                    [Message("user", "ready")], max_tokens=256)
            probed[name] = "ok"
        except Exception as exc:                        # noqa: BLE001
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (401, 402, 403, 404):
                body = redact(getattr(exc.response, "text", ""))[:120]
                probed[name] = f"dropped: HTTP {status} {body}"
                dead.add(name)
                dead_why[name] = f"refused at boot: HTTP {status}"
                log.error("provider %s (%s) refused at boot — HTTP %s %s; "
                          "leaving it out of every chain",
                          name, provider.model, status, body)
            elif status == 429:
                probed[name] = "kept: rationed (HTTP 429)"
                log.warning("provider %s (%s) answered 429 at boot — rationed "
                            "or never enabled, and those look identical. "
                            "Keeping it: the per-call fallthrough handles a "
                            "spent cap, and tomorrow restores it.",
                            name, provider.model)
            else:
                probed[name] = f"kept despite {type(exc).__name__}"
                log.warning("provider %s did not answer at boot (%s: %s); "
                            "keeping it, since that is the outage the chain "
                            "is for", name, type(exc).__name__, exc)
    for key in [k for k in DROPPED if k[1] != "ollama"]:
        del DROPPED[key]
    if dead:
        _record_dropped(dead, dead_why)
        for need, chain in list(ROUTES.items()):
            ROUTES[need] = [p for p in chain if _probe_key(p) not in dead]
    # A pin names a provider, not a model: it is dead only when every model
    # of that provider was refused.
    alive = {p.name for chain in ROUTES.values() for p in chain}
    DEAD_PROVIDERS.clear()
    DEAD_PROVIDERS.update(k.split(":", 1)[0] for k in dead
                          if k.split(":", 1)[0] not in alive)
    # OpenRouter's own count of the key's free requests left today
    # (kernel/quota.py). No model is called; it never raises.
    from . import quota
    await quota.refresh_openrouter(force=True)
    return probed


# Groq models that are not chat: speech in and out, and the guard models
# (its models page, read 2026-10-06). Preview models are for evaluation
# only by Groq's own word, and minimax-m2.7 is one; ATLAS_GROQ_SKIP adds
# more names to leave out.
# "compound" models are Groq's systems that search the web and run code on
# Groq's side: the model would act, not pick from the catalog (invariant 2).
_GROQ_NOT_CHAT = re.compile(r"whisper|orpheus|tts|playai|guard|safeguard|compound",
                            re.IGNORECASE)
_GROQ_PREVIEW = frozenset({"minimaxai/minimax-m2.7"})


async def discover_groq_models() -> list[str]:
    """Ask Groq which models this key can see (GET /openai/v1/models) and put
    every chat model not already in a chain into the voice/TRIVIAL lane,
    ahead of the last-resort entries. Each Groq model has its own daily
    allowance, so each one found is one more lane for voice. Called at boot
    before verify_cloud(), which then proves each one alone. Returns the
    ids added; never raises."""
    key = os.getenv("GROQ_API_KEY", "")
    if not key or Need.TRIVIAL not in ROUTES:
        return []
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://api.groq.com/openai/v1/models",
                            headers={"Authorization": f"Bearer {key}"})
            r.raise_for_status()
            listed = r.json().get("data") or []
    except Exception as exc:                            # noqa: BLE001
        log.warning("groq model list unread (%s); keeping the configured "
                    "models", type(exc).__name__)
        return []
    skip = _GROQ_PREVIEW | {s.strip() for s in os.getenv(
        "ATLAS_GROQ_SKIP", "").split(",") if s.strip()}
    have = {getattr(p, "model", "") for chain in ROUTES.values() for p in chain
            if p.name == "groq"}
    found = sorted(
        str(m.get("id")) for m in listed
        if isinstance(m, dict) and m.get("id") and m.get("active", True)
        and not _GROQ_NOT_CHAT.search(str(m["id"]))
        and str(m["id"]) not in skip and str(m["id"]) not in have)
    if not found:
        return []
    chain = ROUTES[Need.TRIVIAL]
    at = next((i for i, p in enumerate(chain)
               if p.name in ("huggingface", "openrouter", "anthropic", "ollama")),
              len(chain))
    ROUTES[Need.TRIVIAL] = chain[:at] + [GroqProvider(m) for m in found] + chain[at:]
    log.info("groq lists %s more chat model(s), added to the voice lane: %s",
             len(found), ", ".join(found))
    return found


def _probe_key(provider: Any) -> str:
    """One boot probe per provider -- or per model, for a provider whose
    models are allowed separately (Groq, Workers AI): there one model this
    key cannot use must not take the others out of every chain."""
    if getattr(provider, "probe_each_model", False):
        return f"{provider.name}:{getattr(provider, 'model', '')}"
    return str(provider.name)



@overload
async def think(
    need: Need,
    system: str,
    messages: Sequence[Message],
    tools: None = None,
    max_tokens: int = 2048,
    *,
    model: str | None = None,
    interactive: bool | None = None,
) -> Completion: ...
@overload
async def think(
    need: Need,
    system: str,
    messages: Sequence[Message],
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int = 2048,
    *,
    model: str | None = None,
    interactive: bool | None = None,
) -> Completion | ToolsUnsupported: ...
def _secret_free(system: str, messages: Sequence[Message]
                 ) -> tuple[str, list[Message]]:
    """What a model is shown, with every current secret VALUE and every
    credential query parameter masked (plan S01: a credential in a provider
    or log payload never reaches model context). The collectors redact
    their own details and `record_observation` redacts again; this is the
    net at the one door every prompt goes through -- a tool result, a
    pasted error or a fetched page carries whatever it carries. The
    stored-text rule, not `redact`'s Bearer heuristic, so prose is left
    alone."""
    return (redact_stored(system) if isinstance(system, str) else system,
            [dataclasses.replace(m, content=redact_stored(m.content))
             if isinstance(m.content, str) else m for m in messages])



class EmptyCompletion(RuntimeError):
    """A provider answered 200 with no text and no tool call. A reasoning
    model (gpt-oss, Gemini thinking) given a tight max_tokens spends the
    whole budget thinking and returns '' while looking healthy -- prod,
    2026-09-25: reel_research filed 0 formats from an extract that read ''.
    `think` treats it as a failure and falls through the chain."""


def _empty(reply: Completion | ToolsUnsupported) -> bool:
    return (isinstance(reply, Completion) and not reply.text.strip()
            and not reply.tool_calls)


async def think(
    need: Need,
    system: str,
    messages: Sequence[Message],
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int = 2048,
    *,
    model: str | None = None,
    interactive: bool | None = None,
) -> Completion | ToolsUnsupported:
    """Route one piece of work. Falls through the chain on provider failure.

    Except PRIVATE, which runs on the local model or not at all: no cloud
    provider in the chain, no REASON fallback, and a local failure raises
    `LocalModelUnavailable` rather than trying anything else.

    `model`, when given, is a `"provider:model"` pin (Task 53: each business
    agent owns its model, Hermes by default) tried BEFORE the Need's own
    chain. It never applies to PRIVATE -- the check above runs first, so a
    pin carried on a PRIVATE agent's Task object is simply never looked at.
    When the pin cannot be tried (unrecognised provider, its key unset) or
    fails outright, the Need route answers instead, and that answer's text
    is prefixed `"(answered by <fallback>; <pin> was unavailable: <reason>)"`
    so the switch is visible rather than silent (invariant 6: never let a
    configured name stand in for what actually answered).

    `ToolsUnsupported` comes back only when `tools` were passed and the
    model cannot call them; `harness.think_turn` handles it.

    `interactive` says whether Miro is waiting on this answer; None reads
    the turn's own flag (`quota.interactive()`). A deployment whose known
    daily allowance is under its reserve answers only interactive calls;
    background work that finds nothing else raises `quota.ReservedForMiro`
    without asking anyone (kernel/quota.py).
    """
    system, messages = _secret_free(system, messages)
    if need == Need.PRIVATE:
        return await _think_private(system, messages, tools, max_tokens)

    from . import quota
    pin_failure: str | None = None
    pin_identity: tuple[str, str | None] | None = None
    if model:
        pinned, pin_failure = _resolve_pin(model)
        if pinned is not None and not quota.allowed(
                pinned, interactive_call=interactive):
            pin_identity = (pinned.name, getattr(pinned, "model", None))
            pin_failure = f"{model} is {quota.verdict(pinned)} today"
            pinned = None
        if pinned is not None:
            pin_identity = (pinned.name, getattr(pinned, "model", None))
            try:
                reply = await pinned.complete(system, messages, tools, max_tokens)
                if _empty(reply):
                    raise EmptyCompletion(
                        f"empty completion at max_tokens={max_tokens}")
                return reply
            except Exception as exc:                        # noqa: BLE001
                from ..modules._http import explain
                pin_failure = explain(exc)
                log.warning("pinned model %s unavailable (%s); falling back "
                           "to the %s route", model, pin_failure, need)

    # A chain that starts with the local model (TRIVIAL) returns
    # ToolsUnsupported rather than falling through to a provider that can
    # call tools. No TRIVIAL agent carries tools today; if one ever does,
    # decide then whether it should fall through.
    #
    # Any other Need may borrow the general-purpose chain. PRIVATE never
    # reaches this line: `or ROUTES.get(Need.REASON)` sent personal mail to
    # Sonnet the moment the local route was empty.
    chain = ROUTES.get(need) or ROUTES.get(Need.REASON) or []
    if pin_identity is not None:
        # The pin already failed once; do not let a failover chain that
        # happens to end on the same provider/model (Hermes via OpenRouter,
        # appended by extend_routes_with_failover) hit the same dead
        # endpoint a second time.
        chain = [p for p in chain
                if (p.name, getattr(p, "model", None)) != pin_identity]
    if not chain:
        raise RuntimeError("no LLM provider configured")
    last: Exception | None = None
    # The free allowance first (kernel/quota.py): background work never
    # spends what is kept for Miro, and nobody asks a deployment the
    # provider itself said is spent until its reset. Before the breakers,
    # so "every breaker is open, try them all" below can never re-admit a
    # reserved deployment for background work.
    from . import breaker
    if quota.openrouter_due() and any(
            quota.pool_of(p) == ("openrouter", ":free") for p in chain):
        # OpenRouter's own count of the key's free requests left, read at
        # most every ten minutes; no model is called. Asked whether or not
        # an OpenRouter model answers, so a spent count can come back.
        from .background import spawn
        spawn(quota.refresh_openrouter(), "quota.openrouter")
    for_miro = quota.is_interactive() if interactive is None else interactive
    rationed = [p for p in chain if quota.allowed(p, interactive_call=for_miro)]
    if not rationed:
        if not for_miro:
            log.info("%s: every deployment is spent or kept for Miro; this "
                     "background call waits for its next cycle", need)
            raise quota.ReservedForMiro(
                f"every {need} deployment is spent or kept for Miro")
        # Miro is waiting: ask anyway. A provider may have come back early,
        # and "no model answered" must mean they were asked.
        rationed = list(chain)
    elif len(rationed) < len(chain):
        log.info("skipping %s rationed deployment(s) for %s (%s)",
                 len(chain) - len(rationed), need,
                 "interactive" if for_miro else "background")
    # A deployment whose breaker is open is skipped, not waited on
    # (kernel/breaker.py). If that would leave nothing to ask, the whole
    # chain is tried anyway: a breaker reorders the asking, it never turns
    # "the models did not answer" into "ATLAS did not ask".
    asked = [p for p in rationed if breaker.allow(breaker.key_of(p))]
    if len(asked) < len(rationed):
        log.info("skipping %s open-circuit deployment(s) for %s",
                 len(rationed) - len(asked), need)
    for provider in asked or rationed:
        try:
            reply = await provider.complete(system, messages, tools, max_tokens)
            if _empty(reply):
                raise EmptyCompletion(
                    f"empty completion at max_tokens={max_tokens} (a reasoning "
                    f"model spends the budget thinking and answers nothing)")
        except Exception as exc:                        # noqa: BLE001
            too_large = getattr(getattr(exc, "response", None),
                                "status_code", None) == 413
            if not isinstance(exc, EmptyCompletion) and not too_large:
                # An empty answer is about this request's budget, and a 413
                # about its size (a free tier's per-minute token cap), not
                # the deployment's health; everything else counts.
                breaker.failure(breaker.key_of(provider), exc)
            # Some clients stringify to nothing at all — httpx.ConnectError is
            # the common one — so a bare %s printed "failed ()" and said
            # nothing about why the chain was degrading.
            detail = str(exc) or repr(exc)
            if (resp := getattr(exc, "response", None)) is not None:
                detail = (f"HTTP {resp.status_code}: "
                          f"{redact(resp.text)[:200]}")
            log.warning("provider %s (%s) failed — %s: %s; falling through",
                        provider.name, getattr(provider, "model", "?"),
                        type(exc).__name__, detail)
            last = exc
            continue
        breaker.success(breaker.key_of(provider))
        if not isinstance(reply, ToolsUnsupported):
            from . import outage
            outage.cleared(reply.provider, reply.model)
        if pin_failure is not None and not isinstance(reply, ToolsUnsupported):
            note = (f"(answered by {reply.provider}:{reply.model}; "
                   f"{model} was unavailable: {pin_failure}) ")
            reply = dataclasses.replace(reply, text=note + reply.text)
        return reply
    # One message for the whole outage, not one failure per agent
    # (kernel/outage.py). It never raises and never delays this raise.
    from . import outage
    outage.report(str(getattr(need, "name", need)), last)
    raise RuntimeError(f"all providers failed for {need}") from last


async def _think_private(
    system: str,
    messages: Sequence[Message],
    tools: list[dict[str, Any]] | None,
    max_tokens: int,
) -> Completion | ToolsUnsupported:
    from ..modules._http import explain

    chain = ROUTES.get(Need.PRIVATE, [])
    if not private_cloud_allowed():
        chain = [p for p in chain if getattr(p, "local", False)]
    if not chain:
        raise LocalModelUnavailable(
            "no local model configured; refusing to send personal content "
            "to an API provider. Pull ATLAS_MODEL_LOCAL, or set "
            "ATLAS_PRIVATE_ALLOW_CLOUD=true")
    reasons: list[str] = []
    for provider in chain:
        try:
            return await provider.complete(system, messages, tools, max_tokens)
        except Exception as exc:                        # noqa: BLE001
            model = getattr(provider, "model", provider.name)
            # Redacted: this reason reaches logs, transcripts and what Miro
            # reads or hears.
            reason = f"{model}: {explain(exc)}"[:200]
            log.warning("PRIVATE provider failed (%s)", reason)
            reasons.append(reason)
    raise LocalModelUnavailable("; ".join(reasons))


async def think_twice(
    system: str, messages: Sequence[Message], max_tokens: int = 2048
) -> tuple[Completion, Completion, bool]:
    """Two providers, in parallel, for anything irreversible. Returns both
    answers and whether they agree. Disagreement does not pick a winner — it
    escalates to Miro, which is the correct behaviour for a system that is
    about to do something it cannot undo."""
    system, messages = _secret_free(system, messages)
    chain = ROUTES.get(Need.CRITICAL, [])
    if len(chain) < 2:
        # A check that cannot run is never a pass. Returning agree=True here
        # let one model unanimously agree with itself and a proposal proceed.
        log.error(
            "CRITICAL cross-check unavailable: %d provider(s) configured. "
            "Escalating the finding unresolved instead of proposing.",
            len(chain))
        one = await think(Need.HARD, system, messages, max_tokens=max_tokens)
        return one, one, False

    if chain[0].name == chain[1].name:
        # Two models from one vendor is not a second opinion.
        log.error(
            "CRITICAL cross-check degraded: both providers are %r. "
            "Escalating unresolved.", chain[0].name)
        one = await chain[0].complete(system, messages, None, max_tokens)
        return one, one, False
    # Two answers from two vendors -- but a vendor that is down (OpenAI out
    # of credit, for one whole night) must not take the diagnosis with it.
    # Each provider in the chain is tried until two different vendors have
    # answered; if only one can, that is the unavailable case above.
    answers: list[Completion] = []
    vendors: set[str] = set()
    for provider in chain:
        if provider.name in vendors:
            continue
        try:
            answers.append(await provider.complete(system, messages, None, max_tokens))
            vendors.add(provider.name)
        except Exception as exc:                        # noqa: BLE001
            log.warning("CRITICAL cross-check: %s failed (%s), trying the next",
                        provider.name, type(exc).__name__)
        if len(answers) == 2:
            break
    if len(answers) < 2:
        log.error("CRITICAL cross-check: only %d vendor(s) answered. "
                  "Escalating unresolved.", len(answers))
        if not answers:
            raise RuntimeError("no provider answered the CRITICAL cross-check")
        return answers[0], answers[0], False
    a, b = answers
    # The adjudicator decides whether two frontier models concur about an
    # irreversible production action. It does not run on the 8B local model.
    verdict = await think(
        Need.REASON,
        "Answer only AGREE or DIFFER.",
        [Message("user",
                 f"Do these two answers reach the same conclusion?\n\n"
                 f"A: {a.text}\n\nB: {b.text}")],
        max_tokens=8,
    )
    return a, b, verdict.text.strip().upper().startswith("AGREE")


_TOO_LARGE = re.compile(r"Limit (\d+), Requested (\d+)")
MIN_ANSWER_TOKENS = 384


def _fitting_max_tokens(reply: Any, asked: int) -> int | None:
    """The `max_tokens` that brings a 413 "Limit L, Requested R" request under
    its limit, or None when the reply is not that, or the room left for an
    answer would be under MIN_ANSWER_TOKENS (then the next provider is asked;
    a truncated answer is worse than a fall-through)."""
    if getattr(reply, "status_code", 0) != 413:
        return None
    found = _TOO_LARGE.search(getattr(reply, "text", "") or "")
    if not found:
        return None
    limit, requested = int(found.group(1)), int(found.group(2))
    fits = asked - (requested - limit) - 64
    return fits if fits >= MIN_ANSWER_TOKENS else None


class _OpenAICompatible:
    """Shared client for every /chat/completions endpoint.

    OpenRouter and Groq speak the same wire format, and the tool-call parsing
    below is the fiddly part. One implementation means a failover provider
    cannot quietly disagree with the primary about how a tool call is shaped.
    """

    name: ClassVar[str] = "openai-compatible"
    url: ClassVar[str] = ""
    key_env: ClassVar[str] = ""
    extra_headers: ClassVar[dict[str, str]] = {}
    # An optional provider is one worth proving at boot: a free tier that is
    # not actually enabled looks exactly like a working one until the first
    # real call. See verify_cloud().
    optional: ClassVar[bool] = False

    def __init__(self, model: str) -> None:
        self.model = model

    def extra_payload(self) -> dict[str, Any]:
        """Provider-specific request fields; none by default."""
        return {}

    def endpoint(self) -> str:
        return self.url

    async def complete(self, system, messages, tools=None, max_tokens=2048):
        import httpx
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}]
            + [{"role": m.role, "content": m.content} for m in messages],
        }
        if tools:
            payload["tools"] = function_tools(tools)
        payload.update(self.extra_payload())
        from . import quota
        async with httpx.AsyncClient(timeout=120) as c:
            for attempt in (0, 1):
                r = await c.post(
                    self.endpoint(),
                    headers={"Authorization": f"Bearer {os.environ[self.key_env]}",
                             **self.extra_headers},
                    json=payload)
                # What the provider says it has left today (kernel/quota.py),
                # read from every answer, a refusal included.
                quota.observe(self, r)
                fits = _fitting_max_tokens(r, max_tokens) if attempt == 0 else None
                if fits is None:
                    break
                # A free tier counts prompt + max_tokens against one request
                # limit (Groq: 8,000). ATLAS's HARD prompts ran 700-1,500 over
                # it only because of the 2,048 reserved for the answer, so the
                # same prompt is asked once more with the answer room that fits.
                payload["max_tokens"] = fits
            r.raise_for_status()
            data = r.json()
        choice = data["choices"][0]["message"]
        calls = [
            {"name": c["function"]["name"],
             "input": json.loads(c["function"]["arguments"] or "{}"),
             "id": c.get("id", "")}
            for c in (choice.get("tool_calls") or [])
        ]
        usage = data.get("usage") or {}
        quota.spent(self, int(usage.get("total_tokens")
                              or (usage.get("prompt_tokens") or 0)
                              + (usage.get("completion_tokens") or 0)))
        return Completion(
            text=choice.get("content") or "",
            provider=self.name, model=self.model,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            tool_calls=calls or None,
        )

    async def see(self, system: str, prompt: str, image: Image,
                  max_tokens: int = 400) -> Completion:
        """One image and a question, as an OpenAI-shaped `image_url` part
        carrying a base64 data URL (Groq, read 2026-10-06,
        console.groq.com/docs/vision: up to 3 images, 20 MB a request)."""
        import httpx
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {
                        "url": f"data:{image.mime};base64,{image.b64()}"}},
                ]},
            ],
        }
        payload.update(self.extra_payload())
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                self.endpoint(),
                headers={"Authorization": f"Bearer {os.environ[self.key_env]}",
                         **self.extra_headers},
                json=payload)
            r.raise_for_status()
            data = r.json()
        text = (data["choices"][0]["message"].get("content") or "").strip()
        usage = data.get("usage", {})
        return Completion(text=text, provider=self.name, model=self.model,
                          input_tokens=usage.get("prompt_tokens", 0),
                          output_tokens=usage.get("completion_tokens", 0))


class OpenRouterProvider(_OpenAICompatible):
    """One key, many models: Hermes (Nous Research), Llama, Qwen, DeepSeek and
    others join the failover chain without a separate integration each.

    Hermes is a good failover choice specifically because it is trained for
    tool-calling and follows a system prompt tightly, which is what the harness
    needs. It is a model, not a voice engine — voice is Fish Audio, separately.
    """

    name = "openrouter"
    url = "https://openrouter.ai/api/v1/chat/completions"
    key_env = "OPENROUTER_API_KEY"
    extra_headers: ClassVar[dict[str, str]] = {
        "HTTP-Referer": "https://atlas.example.com", "X-Title": "ATLAS"}


class GroqProvider(_OpenAICompatible):
    """Free tier, and fast enough that it is worth having ahead of a paid model
    for work that does not need judgment. Same wire format as OpenAI.

    A free tier means a daily cap, so this is a provider that will start
    refusing partway through a busy day — which is exactly what the chain below
    it is for. It must never be the only entry for a Need.
    """

    name = "groq"
    url = "https://api.groq.com/openai/v1/chat/completions"
    key_env = "GROQ_API_KEY"
    optional = True
    # Proved model by model at boot: a model this key cannot use is dropped
    # alone, never taking the other Groq models with it.
    probe_each_model: ClassVar[bool] = True

    def extra_payload(self) -> dict[str, Any]:
        # gpt-oss reasons before it writes, and at its default effort spent a
        # whole 1,200-token budget thinking: finish_reason "length", empty
        # content (measured 2026-10-05, the agency's site copy). At "low" the
        # same request used 133 reasoning tokens and answered. Other Groq
        # models reject or ignore the field, so only gpt-oss is sent it.
        effort = os.getenv("ATLAS_GROQ_REASONING", "low").strip().lower()
        if "gpt-oss" in self.model and effort in ("low", "medium", "high"):
            return {"reasoning_effort": effort}
        return {}


class CloudflareProvider(_OpenAICompatible):
    """Cloudflare Workers AI: a free daily allowance (10,000 neurons) on any
    account, OpenAI-compatible, and it calls tools on the Llama 3.3 70B the
    InRoots bot already uses as its first fallback (read from its
    /api/ai/models, 2026-10-05). The account id is part of the URL."""

    name = "cloudflare"
    key_env: ClassVar[str] = "CLOUDFLARE_API_TOKEN"
    optional: ClassVar[bool] = True
    probe_each_model: ClassVar[bool] = True

    def endpoint(self) -> str:
        account = os.environ["CLOUDFLARE_ACCOUNT_ID"].strip()
        return (f"https://api.cloudflare.com/client/v4/accounts/{account}"
                "/ai/v1/chat/completions")

    async def see(self, system: str, prompt: str, image: Image,
                  max_tokens: int = 400) -> Completion:
        """Workers AI's native /ai/run endpoint: the OpenAI-compatible one
        documents no image input (read 2026-10-06). `messages` with an
        `image_url` data URL is the documented shape for the vision models;
        the top-level `image` byte array is marked deprecated. Note
        `@cf/meta/llama-3.2-11b-vision-instruct` needs a one-time Meta
        licence "agree" sent from the account first -- Miro's to send, not
        ATLAS's -- so it is only asked when ATLAS_MODEL_CLOUDFLARE_VISION
        names it."""
        import httpx
        account = os.environ["CLOUDFLARE_ACCOUNT_ID"].strip()
        url = (f"https://api.cloudflare.com/client/v4/accounts/{account}"
               f"/ai/run/{self.model}")
        payload = {
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {
                        "url": f"data:{image.mime};base64,{image.b64()}"}},
                ]},
            ],
        }
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                url, headers={"Authorization": f"Bearer {os.environ[self.key_env]}"},
                json=payload)
            r.raise_for_status()
            data = r.json()
        result = data.get("result") or {}
        text = str(result.get("response") or "").strip()
        return Completion(text=text, provider=self.name, model=self.model)


class HuggingFaceProvider(_OpenAICompatible):
    """Hugging Face Inference Providers through its OpenAI-compatible router
    (https://router.huggingface.co/v1, docs read 2026-10-06). The model id
    may carry a routing policy: `:cheapest` stretches the free account's
    $0.10 a month furthest. Needs a fine-grained token with "Make calls to
    Inference Providers"."""

    name = "huggingface"
    url: ClassVar[str] = "https://router.huggingface.co/v1/chat/completions"
    key_env: ClassVar[str] = "HF_TOKEN"
    optional: ClassVar[bool] = True


class CerebrasProvider(_OpenAICompatible):
    """Free tier when the account has one. Fastest inference of the free
    options; OpenAI-compatible wire format."""

    name = "cerebras"
    url: ClassVar[str] = "https://api.cerebras.ai/v1/chat/completions"
    key_env: ClassVar[str] = "CEREBRAS_API_KEY"
    optional: ClassVar[bool] = True


class MistralProvider(_OpenAICompatible):
    """La Plateforme's free tier. A brand-new key answers 429 code 1300 until
    the tier is activated on the account, which is not the same thing as
    having run out."""

    name = "mistral"
    url: ClassVar[str] = "https://api.mistral.ai/v1/chat/completions"
    key_env: ClassVar[str] = "MISTRAL_API_KEY"
    optional: ClassVar[bool] = True
    # Its free tier allows some models and not others (tier_not_allowed), so
    # each is proved alone at boot.
    probe_each_model: ClassVar[bool] = True


class CohereProvider(_OpenAICompatible):
    """Cohere through its OpenAI-compatibility endpoint, so it shares the
    client above rather than needing its own /v2/chat translation."""

    name = "cohere"
    url: ClassVar[str] = "https://api.cohere.ai/compatibility/v1/chat/completions"
    key_env: ClassVar[str] = "COHERE_API_KEY"
    optional: ClassVar[bool] = True


class GeminiProvider:
    """Google AI Studio, free tier. The independent second vendor for CRITICAL
    cross-checks now that the OpenAI key has no credits.

    Note the model names: the `gemini-2.5-*` family answers 404 "no longer
    available to new users" on a current key, so the names here are the ones
    that actually serve, checked live rather than assumed.
    """

    name = "gemini"
    optional = True
    # A free key serves some models and not others (gemini-pro-latest has no
    # free quota): each is proved alone at boot.
    probe_each_model = True

    def __init__(self, model: str) -> None:
        self.model = model
        self.base = os.getenv(
            "GEMINI_URL", "https://generativelanguage.googleapis.com/v1beta")
        self.min_tokens = int(os.getenv("ATLAS_GEMINI_MIN_TOKENS", "256"))

    async def see(self, system: str, prompt: str, image: Image,
                  max_tokens: int = 400) -> Completion:
        """One image and a question (inline data, never a URL or an upload
        that the provider keeps). Read 2026-10-06, ai.google.dev/api/
        generate-content: an `inline_data` part, base64, 20 MB per request."""
        payload: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [
                {"inline_data": {"mime_type": image.mime, "data": image.b64()}},
                {"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": max(max_tokens, self.min_tokens)},
        }
        return await self._generate(payload)

    async def _generate(self, payload: dict[str, Any]) -> Completion:
        import httpx
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(
                f"{self.base}/models/{self.model}:generateContent",
                params={"key": os.environ["GEMINI_API_KEY"]},
                json=payload)
            from . import quota
            quota.observe(self, r)
            r.raise_for_status()
            data = r.json()
        if not (candidates := data.get("candidates")):
            raise RuntimeError(
                f"gemini returned no candidate: {str(data)[:200]}")
        candidate = candidates[0]
        parts = candidate.get("content", {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts)
        calls = [
            {"name": fc["name"], "input": fc.get("args") or {}, "id": ""}
            for p in parts if (fc := p.get("functionCall"))
        ]
        # The thinking models spend the budget before writing anything, so a
        # 200 can carry MAX_TOKENS and no text at all. An empty answer that
        # looks successful is the failure mode this whole file keeps hitting.
        if not text and not calls:
            raise RuntimeError(
                "gemini produced no text "
                f"(finishReason={candidate.get('finishReason')})")
        usage = data.get("usageMetadata", {})
        return Completion(
            text=text, provider=self.name, model=self.model,
            input_tokens=usage.get("promptTokenCount", 0),
            output_tokens=usage.get("candidatesTokenCount", 0),
            tool_calls=calls or None,
        )

    async def complete(self, system, messages, tools=None, max_tokens=2048):
        payload: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            # Gemini names the assistant "model", not "assistant".
            "contents": [
                {"role": "model" if m.role == "assistant" else "user",
                 "parts": [{"text": m.content}]}
                for m in messages
            ],
            # Thinking tokens count against maxOutputTokens, so a small
            # budget buys reasoning and no answer: measured on
            # gemini-3.8-flash AND gemini-pro-latest, a one-word reply needs
            # 256 and produces nothing at 64. Any caller asking for a short
            # answer would otherwise get an empty one, every time, from a
            # provider that looks healthy. The floor is a floor, not a cap —
            # a caller asking for more still gets it.
            "generationConfig": {
                "maxOutputTokens": max(max_tokens, self.min_tokens)},
        }
        if tools:
            payload["tools"] = [{"functionDeclarations": [
                {"name": t["name"], "description": t["description"],
                 "parameters": t["input_schema"]} for t in tools]}]
        return await self._generate(payload)


# --------------------------------------------------- per-agent model pins

# provider prefix ("provider:model") -> the class that speaks for it. Every
# entry here is a name `businesses.py`'s default, an
# `ATLAS_AGENT_MODEL_<NAME>` override, or a future caller may pin a Need's
# work to. "ollama" is included for completeness (a pin naming the local
# model is redundant but not wrong); it carries no key.
PIN_PROVIDERS: dict[str, type] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "ollama": OllamaProvider,
    "openrouter": OpenRouterProvider,
    "groq": GroqProvider,
    "gemini": GeminiProvider,
    "cerebras": CerebrasProvider,
    "mistral": MistralProvider,
    "cohere": CohereProvider,
    "cloudflare": CloudflareProvider,
    "huggingface": HuggingFaceProvider,
}

# provider prefix -> the env var that must be set for it to be reachable.
# Anything not listed here (just "ollama" today) needs no key.
_PROVIDER_KEYS: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "groq": "GROQ_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "cohere": "COHERE_API_KEY",
    "cloudflare": "CLOUDFLARE_API_TOKEN",
    "huggingface": "HF_TOKEN",
}


def providers_present() -> list[str]:
    """Which providers have a key configured, by name only -- never the
    value (invariant: no secret reaches a log, a prompt, or a status page).
    Read by `selfmodel.status()` and mirrored in CONFIGURE.md's own table."""
    return sorted(name for name, env in _PROVIDER_KEYS.items() if os.getenv(env))


def _resolve_pin(model: str) -> tuple[Provider | None, str | None]:
    """Parse a `"provider:model"` pin into a live Provider, or `(None,
    reason)` when it cannot even be tried: an unrecognised prefix, a
    malformed pin, a provider whose key is not set, or one `verify_cloud()`
    already proved dead at boot (M3: without this a pin to a dropped
    provider -- the docstring's own example, Cerebras on 402 -- would be
    retried against the same dead endpoint for up to 120s on every turn of
    every run instead of failing over immediately). Never raises -- a typo
    in `ATLAS_AGENT_MODEL_<NAME>` degrades to the Need's own route rather
    than crashing the run, per `think()`'s contract."""
    prefix, sep, name = model.partition(":")
    if not sep or not name:
        return None, f"{model!r} is not a provider:model pin"
    cls = PIN_PROVIDERS.get(prefix)
    if cls is None:
        return None, f"{prefix!r} is not a recognised provider"
    if prefix in DEAD_PROVIDERS:
        return None, f"{prefix} was dropped by verify_cloud() at boot"
    key_env = _PROVIDER_KEYS.get(prefix)
    if key_env and not os.getenv(key_env):
        return None, f"{key_env} not set"
    return cls(name), None


def extend_routes_with_failover() -> None:
    """Called after build_routes(). Appends open-weight failovers so a provider
    outage degrades quality instead of stopping ATLAS.

    Chain, in order: Claude -> Hermes -> GPT -> local. Cerebras, Mistral and
    Cohere are NOT added here: `build_routes()` already places all three into
    TRIVIAL, DRAFT, REASON and HARD (Cerebras leading the free/fast bench,
    the other two via `extra_fast`/`extra_mid`), so doing it again here would
    just make each of them answer twice in the same chain.
    CRITICAL is the one Need `build_routes()` leaves them out of (it wants
    Anthropic, Gemini and OpenAI specifically), so this is where Mistral and
    Cohere join it -- more independent vendors `think_twice` can fall
    through to before it escalates unresolved. None of this ever reaches
    PRIVATE (`think()` branches to `_think_private` before any chain here is
    consulted).
    """
    if os.getenv("OPENROUTER_API_KEY") and not free_only():
        # The open-weight failover. Named Hermes for history: since
        # 2026-09-28 OpenRouter serves no Hermes model with tool calls
        # ("No endpoints found that support tool use"), so an agent run that
        # fell through to it 404'd. Qwen3 235B calls tools (tested live).
        hermes = OpenRouterProvider(_env("ATLAS_MODEL_HERMES",
                                         "qwen/qwen3-235b-a22b-2507"))
        # hermes-4-70b answers 404 "No endpoints found" on OpenRouter, so the
        # TRIVIAL failover was decorative. hermes-3-llama-3.1-70b serves, and
        # keeps the reason Hermes was chosen: trained for tool-calling, holds
        # a system prompt tightly.
        hermes_fast = OpenRouterProvider(_env(
            "ATLAS_MODEL_HERMES_FAST", "nousresearch/hermes-3-llama-3.1-70b"))
        for need, extra in (
            (Need.TRIVIAL, hermes_fast),
            (Need.DRAFT, hermes),
            (Need.REASON, hermes),
            (Need.HARD, hermes),
            (Need.CRITICAL, hermes),
        ):
            ROUTES.setdefault(need, []).append(extra)
        log.info("failover chain extended with Hermes via OpenRouter")

    if os.getenv("MISTRAL_API_KEY"):
        mistral = MistralProvider(_env("ATLAS_MODEL_MISTRAL", "ministral-14b-2512"))
        ROUTES.setdefault(Need.CRITICAL, []).append(mistral)
        log.info("CRITICAL cross-check gained Mistral as further vendor depth")

    if os.getenv("COHERE_API_KEY"):
        cohere = CohereProvider(_env("ATLAS_MODEL_COHERE", "command-a-03-2025"))
        ROUTES.setdefault(Need.CRITICAL, []).append(cohere)
        log.info("CRITICAL cross-check gained Cohere as further vendor depth")


# ------------------------------------------------------------------ vision

# Air Glass's "ATLAS, look": one still and a question. Its own chain, and
# never `think()`'s: a text-only model in a fallback chain would answer "I
# can't see images" and look like success. Free tiers only, checked against
# each provider's docs on 2026-10-06:
#   gemini:gemini-3.5-flash-lite  image input, free of charge (Google
#       recommends 3.5 Flash-Lite / 3.8 Flash for new projects); the free
#       per-model RPM/RPD are shown only inside AI Studio now
#   gemini:gemini-3.8-flash       the same, as the second Gemini
#   groq:qwen/qwen3.8-27b         Groq's only vision model (preview): 30 RPM,
#       1K RPD, 8K TPM, 200K TPD on the free tier; 2048 tokens an image
# Cloudflare's `@cf/meta/llama-3.2-11b-vision-instruct` is listed (10,000
# neurons a day free) but needs a one-time Meta licence "agree" from the
# account, so it joins only when ATLAS_MODEL_CLOUDFLARE_VISION names it.
# Ollama never: the local model is text-only here, and PRIVATE is a promise
# about content that stays on the box -- a camera still is not routed there.
VISION_DEFAULT = ("gemini:gemini-3.5-flash-lite", "gemini:gemini-3.8-flash",
                  "groq:qwen/qwen3.8-27b")


class VisionUnavailable(RuntimeError):
    """No vision model answered. Raised, never papered over: the page says
    so in ATLAS's voice instead of guessing what was in his hand."""

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        detail = "; ".join(reasons) or "none configured"
        super().__init__(f"no vision model answered: {detail}")


def vision_chain() -> list[Any]:
    """The vision models that can be asked right now, in order. Read from
    env at call time: ATLAS_MODELS_VISION ("provider:model,...") replaces
    the default list; a provider with no key, or dropped at boot, is left
    out; Ollama is refused whatever the list says."""
    raw = os.getenv("ATLAS_MODELS_VISION", "").strip()
    pins = ([p.strip() for p in raw.split(",") if p.strip()] if raw
            else list(VISION_DEFAULT))
    extra = os.getenv("ATLAS_MODEL_CLOUDFLARE_VISION", "").strip()
    if extra:
        pins.append(f"cloudflare:{extra}")
    chain: list[Any] = []
    for pin in pins:
        if pin.partition(":")[0] == "ollama":
            log.error("ATLAS_MODELS_VISION: %r is local; a camera still is never "
                      "sent to the local route. Left out", pin)
            continue
        provider, _why = _resolve_pin(pin)
        if provider is not None and hasattr(provider, "see"):
            chain.append(provider)
    if "cloudflare" in {p.name for p in chain} and not os.getenv("CLOUDFLARE_ACCOUNT_ID"):
        chain = [p for p in chain if p.name != "cloudflare"]
    return chain


async def look(system: str, prompt: str, image: Image,
               max_tokens: int = 400) -> Completion:
    """Ask a vision model about one image. Falls through the vision chain;
    raises VisionUnavailable when none answers. The image is passed to the
    provider and dropped; nothing here logs it or keeps it."""
    from ..modules._http import explain
    from . import breaker
    system = redact_stored(system)
    prompt = redact_stored(prompt)
    chain = vision_chain()
    if not chain:
        raise VisionUnavailable([])
    asked = [p for p in chain if breaker.allow(breaker.key_of(p))] or chain
    reasons: list[str] = []
    for provider in asked:
        try:
            reply = await provider.see(system, prompt, image, max_tokens)
            if not reply.text.strip():
                raise EmptyCompletion("the vision model answered nothing")
        except Exception as exc:                        # noqa: BLE001
            if not isinstance(exc, EmptyCompletion):
                breaker.failure(breaker.key_of(provider), exc)
            reason = f"{provider.name}:{getattr(provider, 'model', '?')}: {explain(exc)}"
            log.warning("vision %s", reason[:240])
            reasons.append(reason[:240])
            continue
        breaker.success(breaker.key_of(provider))
        return reply
    raise VisionUnavailable(reasons)
