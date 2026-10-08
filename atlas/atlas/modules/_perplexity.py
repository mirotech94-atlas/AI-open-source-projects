"""Perplexity over MCP: research, reasoning and understanding, as a *source*
with a budget (Phase 20.5, Task 42).

Miro: "use MCP for Perplexity; use Perplexity for deep thinking and
understanding and research and everything that it is good for." This is the
one place ATLAS talks to Perplexity. Every seam that wants research —
knowledge mode, the research report, the weekly monetisation pass, agency
lead reputation, the night shift's research job, school references, and the
three agent read tools — goes through `call()`.

**How it is reached.** Perplexity's remote MCP server (`PERPLEXITY_MCP_URL`,
default `https://api.perplexity.ai/mcp`, bearer `PERPLEXITY_API_KEY`) through
the unified client, `atlas.modules._mcp.McpClient`, always constructed with
an explicit url *and* token — left `None`, that client falls back to
GetLayers' settings and would hand GetLayers' token to Perplexity. The tools
it exposes are discovered with `tools/list` at first use and cached per URL
for the life of the process; a tool the server does not list is refused
with a reason, never an exception. The argument shape is read off each
tool's `inputSchema` too (`messages` for ask/reason/research, `query` for
search) rather than assumed.

**The MCP server is the only door (Task 50).** Perplexity's chat-completions
endpoint was checked live on 2026-09-25 and returns `403
chat_completions_not_available` — it is dead, not a fallback, and nothing
here calls it. When the MCP server cannot be reached, the token is refused,
or a tool is missing, `call()` returns `(None, reason)`; there is no second
attempt against another endpoint. A caller that needs a number treats that
reason as UNKNOWN, never as licence to guess one (invariant 6).

**Result shape.** The tool names (`perplexity_search`, `perplexity_ask`,
`perplexity_reason`, `perplexity_research`) and the fact that a
`perplexity_ask` call returns citations are VERIFIED against a live
`tools/list` and a live call on 2026-09-25. Which of the three citation
paths below that live answer actually used was not recorded, so all three
stay assumed: a `tools/call` result is read raw (`call_tool`, not the
GetLayers-style `call`): its `content` text items are the answer; citations
come from `structuredContent.citations` / `structuredContent.search_
results[].url` / top-level `citations` when present, else from a trailing
"Citations:" / "Sources:" block in the text, else from every URL in the
text (which is what `perplexity_search`'s list of results reduces to).

**A source, never an oracle.** Every answer comes back as
`wrap_untrusted(text, "perplexity")` (invariant 4), its citations kept as a
list and shown as "Sources:", with the time it was read. Nothing Perplexity
says is a figure ATLAS read from its own systems (invariant 6), and a
question that names one of his own modules, businesses, agents or checks is
refused *before* the call — "how is FlyCheap doing" goes to the estate,
never to Perplexity. The refusal reuses Task 28's `_mentions_a_name` from
`atlas/api/voice.py`, imported lazily; a caller whose subject is a third
party by construction (an agency lead's business name) may turn it off.

**His private data does not leave the box.** Callers send a topic, never a
document, a message body, a coursework item's text or a name from his
contacts. `scrub_for_research` strips email addresses, phone numbers and
anything already wrapped by `wrap_untrusted` (principal or not — a wrapped
block is somebody's words, not a topic) before the request is built, and a
question longer than `MAX_QUERY` characters is refused as a document.

**Cost is bounded.** `PERPLEXITY_MONTHLY_CAP` calls a calendar month
(default 300), counted in `perplexity_calls(at, tool, caller, chars_in,
chars_out)`, one row per logical call that reached the `tools/call` stage.
At the cap every caller gets `(None, "perplexity cap reached: N/N this
month")` and nothing is sent. A budget that cannot be read spends nothing
either. The key is checked first of all, so an unconfigured ATLAS never
touches the database on Perplexity's account.

**The key never appears** in a reason, a log line or an Observation: the MCP
client reports a refusal by variable name, and every other reason goes
through `redact()` and an explicit replace of the key's value.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..kernel.contracts import Health, Observation
from ..kernel.permissions import wrap_untrusted
from ..kernel.state import execute_sql, fetch_one
from ._http import redact
from ._mcp import McpClient

log = logging.getLogger("atlas.perplexity")

DEFAULT_URL = "https://api.perplexity.ai/mcp"
KEY_ENV = "PERPLEXITY_API_KEY"
DEFAULT_CAP = 300
SOURCE = "perplexity"

TOOLS: tuple[str, ...] = ("perplexity_search", "perplexity_ask",
                          "perplexity_reason", "perplexity_research")

# Deep research runs for minutes; knowledge mode passes its own, much
# shorter, timeout because it is answered out loud.
TIMEOUTS: dict[str, float] = {
    "perplexity_search": 30.0,
    "perplexity_ask": 45.0,
    "perplexity_reason": 90.0,
    "perplexity_research": 600.0,
}

# The MCP client applies its timeout to each request, not the whole call:
# initialize, the initialized notification and tools/list each get this
# much, and only tools/call gets the tool's own (long) timeout. A black-holed
# server is then known to be down in seconds, not after ten minutes of deep
# research's timeout, so a caller waiting on a `deadline` gets its reason
# promptly instead of the tool's own (long) timeout.
HANDSHAKE_TIMEOUT = 12.0

# A topic or a question, not a document. Enforced here rather than asked
# for in a prompt.
MAX_QUERY = 500

# Tools each server listed, by URL, cached after the first successful
# `tools/list`: name -> the tool's own definition (for its inputSchema).
_OFFERED: dict[str, dict[str, dict]] = {}


@dataclass(frozen=True, slots=True)
class Answer:
    tool: str
    text: str                       # already wrap_untrusted(..., "perplexity")
    citations: tuple[str, ...]
    read_at: datetime
    via: str                        # always "mcp" (Task 50: the only door)
    # The same answer, unwrapped. Only for a caller whose own contract is to
    # hand back raw text for ITS caller to wrap (`_web.search`: "never
    # wraps"), via `search_hits`. Never put into a prompt as it is.
    unwrapped: str = field(default="", repr=False)

    def sources_line(self) -> str:
        return sources_line(self.citations)

    def read_line(self) -> str:
        return (f"(read {self.read_at:%H:%M} UTC from Perplexity — a source, "
                f"not a measurement of ATLAS's own systems)")

    def render(self) -> str:
        """Sources and the read time first, then the wrapped answer — the
        form every prompt and every tool result gets. In that order because
        an agent loop clips a tool result at a fixed length: whatever is cut
        must never be the citations, the read time, or the closing tag and
        trailer of the wrapper (callers that clip also cap the answer text
        before it is wrapped: `call(max_chars=...)`)."""
        return f"{self.sources_line()}\n{self.read_line()}\n{self.text}"


def sources_line(citations: tuple[str, ...] | list[str]) -> str:
    return ("Sources: " + "; ".join(citations)) if citations else \
        "Sources: none given"


# ------------------------------------------------------------ configuration

def configured() -> bool:
    return bool(os.getenv(KEY_ENV))


def url() -> str:
    return os.getenv("PERPLEXITY_MCP_URL") or DEFAULT_URL


def cap() -> int:
    """Read fresh every call, like `agency.prospect.cap`."""
    try:
        return int(os.getenv("PERPLEXITY_MONTHLY_CAP", str(DEFAULT_CAP)))
    except ValueError:
        return DEFAULT_CAP


async def calls_this_month() -> int:
    """Raises if the count cannot be read — callers decide what that means
    (refuse to spend; report UNKNOWN)."""
    row = await fetch_one(
        "SELECT count(*) AS n FROM perplexity_calls "
        "WHERE at >= date_trunc('month', now())")
    if row is None:
        raise RuntimeError("the perplexity_calls count returned nothing")
    return int(row["n"])


# ------------------------------------------------------------------ scrub

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# A digit run that could be a phone number: optional +, digits with the
# usual separators. Whether it IS one is decided by `_phone_like` on the
# digit count, so "1990-1995" and "2026" survive.
_PHONEISH = re.compile(r"(?<![\w.])\+?\(?\d[\d\s().-]{5,}\d(?![\w])")
_LOCAL_PHONE = re.compile(r"\A\(?\d{3}\)?[\s.-]\d{4}\Z")
_WRAPPED = re.compile(
    r"<(request|untrusted_data) source=[^>]*>.*?</\1>\n?", re.DOTALL)


def _trailers() -> tuple[str, ...]:
    out = []
    for principal in (False, True):
        sample = wrap_untrusted("", "x", principal=principal)
        out.append(sample.rsplit(">\n", 1)[-1])
    return tuple(out)


_TRAILERS = _trailers()


def _phone_like(match: re.Match) -> str:
    token = match.group(0)
    digits = re.sub(r"\D", "", token)
    if token.startswith("+") and 8 <= len(digits) <= 15:
        return "[phone withheld]"
    if 10 <= len(digits) <= 11:
        return "[phone withheld]"
    if len(digits) == 7 and _LOCAL_PHONE.match(token.strip()):
        return "[phone withheld]"
    return token


def scrub_for_research(text: str) -> str:
    """What may leave the box: the text with email addresses, phone numbers
    and every `wrap_untrusted` block (and its trailer) removed."""
    out = _WRAPPED.sub(" ", text or "")
    for trailer in _TRAILERS:
        out = out.replace(trailer, " ")
    out = _EMAIL.sub("[email withheld]", out)
    out = _PHONEISH.sub(_phone_like, out)
    return re.sub(r"\s+", " ", out).strip()


def names_own_system(text: str) -> bool:
    """Does the question name one of ATLAS's own modules, businesses, agents
    or checks? Task 28's rule, reused rather than restated. Fails closed:
    if the rule cannot be evaluated, the question is treated as naming one."""
    try:
        from ..api.voice import _mentions_a_name
        return _mentions_a_name(text)
    except Exception:                                   # noqa: BLE001
        log.warning("could not evaluate the name rule; refusing to ask")
        return True


# ------------------------------------------------------------------- call

# Reasons that quote the server's own words: `_mcp`'s "MCP error: <detail>"
# and this module's "Perplexity tool error: <detail>". That detail is
# third-party text, so it is wrapped like any other ingested byte before a
# reason carrying it can reach an agent's prompt (invariant 4).
_SERVER_WORDS = ("MCP error: ", "Perplexity tool error: ")


def _clean(reason: str) -> str:
    key = os.getenv(KEY_ENV)
    out = redact(reason)
    out = out.replace(key, "<redacted>") if key else out
    for prefix in _SERVER_WORDS:
        if out.startswith(prefix):
            detail = out[len(prefix):][:200]
            return prefix.rstrip() + " " + wrap_untrusted(detail, SOURCE)
    return out


async def call(tool: str, text: str, *, caller: str, guard_names: bool = True,
               timeout: float | None = None, deadline: float | None = None,
               max_chars: int | None = None) -> tuple[Answer | None, str | None]:
    """One question to Perplexity. Never raises. `(Answer, None)` or
    `(None, reason)`. Order matters: key, scrub, name rule, cap — nothing
    reads the database or the network before the key is known to be set,
    and nothing reaches Perplexity past a refusal.

    `timeout` is the per-request limit for the tool call itself (the
    handshake always gets `HANDSHAKE_TIMEOUT` at most). `deadline`, when
    given, bounds the whole handshake-plus-call together — for a caller
    that is answering out loud; running past it is a reason, and counts as
    a call, since the request may already have been billed.

    `max_chars` caps the answer text BEFORE it is wrapped, marked "…
    (truncated)", for a caller whose result is clipped downstream (the agent
    tools): the wrapper, Sources and read time then always survive whole."""
    if tool not in TOOLS:
        return None, f"{tool!r} is not a Perplexity tool ({', '.join(TOOLS)})"
    key = os.getenv(KEY_ENV)
    if not key:
        return None, f"{KEY_ENV} not set"

    query = scrub_for_research(text)
    if not query:
        return None, "nothing left to ask once private details were removed"
    if len(query) > MAX_QUERY:
        return None, (f"send Perplexity a topic or a question, not a document "
                      f"({len(query)} characters; the limit is {MAX_QUERY})")
    if guard_names and names_own_system(query):
        return None, ("the question names one of ATLAS's own modules, "
                      "businesses, agents or checks — that is asked of the "
                      "estate, never of Perplexity")

    try:
        used = await calls_this_month()
    except Exception as exc:                            # noqa: BLE001
        return None, _clean(f"perplexity budget unreadable, so nothing was "
                            f"spent: {type(exc).__name__}: {exc}")
    limit = cap()
    if used >= limit:
        return None, f"perplexity cap reached: {used}/{limit} this month"

    wait = timeout if timeout is not None else TIMEOUTS[tool]
    try:
        async with asyncio.timeout(deadline):
            raw, citations, reason, stage = await _via_mcp(tool, query, key, wait)
    except TimeoutError:
        await _record(tool, caller, len(query), 0)
        return None, f"Perplexity did not answer within {deadline:g}s"
    if stage == "called":
        # One row per logical call that reached Perplexity, answered or not
        # — a request it may bill for counts against the cap.
        await _record(tool, caller, len(query), len(raw or ""))
    if reason is not None:
        return None, _clean(reason)
    if not raw or not raw.strip():
        return None, "Perplexity returned no text"
    body = raw.strip()
    if max_chars is not None and len(body) > max_chars:
        body = body[:max_chars].rstrip() + " … (truncated)"
    return Answer(tool, wrap_untrusted(body, SOURCE), tuple(citations),
                  datetime.now(UTC), "mcp", unwrapped=body), None


async def _record(tool: str, caller: str, chars_in: int, chars_out: int) -> None:
    try:
        await execute_sql(
            "INSERT INTO perplexity_calls (tool, caller, chars_in, chars_out) "
            "VALUES ($1, $2, $3, $4)", tool, caller, chars_in, chars_out)
    except Exception as exc:                            # noqa: BLE001
        log.warning("could not count a Perplexity call against the cap: %s",
                    _clean(str(exc)))


def _arguments(spec: dict, query: str) -> dict[str, Any]:
    """Build the call's arguments from the tool's own inputSchema."""
    schema = spec.get("inputSchema") if isinstance(spec, dict) else None
    props = (schema or {}).get("properties") or {}
    messages = {"messages": [{"role": "user", "content": query}]}
    if "messages" in props:
        return messages
    for name in ("query", "question", "topic", "prompt"):
        if name in props:
            if (props[name] or {}).get("type") == "array":
                return {name: [query]}
            return {name: query}
    required = (schema or {}).get("required") or []
    if len(required) == 1:
        return {required[0]: query}
    return messages


async def _handshake(key: str, timeout: float
                     ) -> tuple[McpClient | None, dict[str, dict], str | None, str]:
    """`initialize()` then (when not already cached) `tools/list` — the two
    steps `_via_mcp`'s call and `check_budget`'s live probe both need, since
    Task 50 removed the chat-completions door: there is no second endpoint
    to try, so both callers need the same honest read of whether the MCP
    server answers at all right now.

    Returns `(client, offered, reason, stage)`. `stage` is "refused" for a
    refused token, "unreachable" for any other handshake failure, or "ok"
    with `offered` (tool name -> its `tools/list` definition, possibly
    empty) once the handshake and listing succeeded."""
    client = McpClient(url(), key, timeout=min(HANDSHAKE_TIMEOUT, timeout),
                       service="Perplexity", token_env=KEY_ENV)
    _init, reason = await client.initialize()
    if reason is not None:
        if reason.startswith("Perplexity refused the token"):
            return None, {}, reason, "refused"
        return None, {}, reason, "unreachable"

    offered = _OFFERED.get(client.url)
    if offered is None:
        tools, reason = await client.list_tools()
        if reason is not None:
            return None, {}, reason, "unreachable"
        offered = {t["name"]: t for t in tools
                   if isinstance(t, dict) and isinstance(t.get("name"), str)}
        if offered:
            # An empty list is not cached: one bad reply would otherwise
            # make every tool "missing" until a restart.
            _OFFERED[client.url] = offered
    return client, offered, None, "ok"


async def _via_mcp(tool: str, query: str, key: str, timeout: float
                   ) -> tuple[str | None, list[str], str | None, str]:
    """(text, citations, reason, stage). `stage` is "unreachable" when the
    handshake failed for any reason but a refused token, "refused" for a
    refused token, "missing" when the server does not offer the tool, and
    "called" once a `tools/call` was actually sent. There is no fallback
    stage any more (Task 50): the MCP server is the only door, so any stage
    but "called" reaches `call()`'s caller as a plain reason."""
    client, offered, reason, stage = await _handshake(key, timeout)
    if stage != "ok":
        return None, [], reason, stage
    if tool not in offered:
        return None, [], (
            f"the Perplexity MCP server has no {tool} tool (it offers: "
            f"{', '.join(sorted(offered)) or 'nothing'})"), "missing"

    client.timeout = timeout            # read per request: the call gets the long one
    result, reason = await client.call_tool(tool, _arguments(offered[tool], query))
    if reason is not None:
        return None, [], reason, "called"
    text, citations, reason = _parse_mcp(result)
    return text, citations, reason, "called"


_URL = re.compile(r"https?://[^\s<>\"'\])]+")
_CITE_HEADER = re.compile(r"^\s*(?:#+\s*)?(citations|sources|references)\s*:?\s*$",
                          re.IGNORECASE)


def _dedupe(urls: list[str]) -> list[str]:
    return list(dict.fromkeys(u.rstrip(".,;") for u in urls if u))


def _split_citations(text: str) -> tuple[str, list[str]]:
    """A trailing "Citations:" block, split off; else every URL in the text."""
    lines = text.splitlines()
    for i in range(len(lines) - 1, -1, -1):
        if _CITE_HEADER.match(lines[i]):
            body = "\n".join(lines[:i]).rstrip()
            return body, _dedupe(_URL.findall("\n".join(lines[i + 1:])))
    return text, _dedupe(_URL.findall(text))


def _structured_citations(obj: Any) -> list[str]:
    if not isinstance(obj, dict):
        return []
    urls: list[str] = []
    for c in obj.get("citations") or []:
        if isinstance(c, str):
            urls.append(c)
        elif isinstance(c, dict) and isinstance(c.get("url"), str):
            urls.append(c["url"])
    for r in obj.get("search_results") or []:
        if isinstance(r, dict) and isinstance(r.get("url"), str):
            urls.append(r["url"])
    return _dedupe(urls)


def _parse_mcp(result: Any) -> tuple[str | None, list[str], str | None]:
    """Never raises: a shape it does not expect is a reason."""
    try:
        return _parse_mcp_shape(result)
    except Exception:                                   # noqa: BLE001
        return None, [], "unexpected result shape from Perplexity's tools/call"


def _parse_mcp_shape(result: Any) -> tuple[str | None, list[str], str | None]:
    if isinstance(result, str):
        text, cites = _split_citations(result)
        return text, cites, None
    if not isinstance(result, dict):
        return None, [], "Perplexity's tools/call result had no content"
    content = result.get("content")
    if not isinstance(content, list):
        content = []
    texts = [c["text"] for c in content
             if isinstance(c, dict) and c.get("type") == "text"
             and isinstance(c.get("text"), str)]
    joined = "\n".join(texts)
    if result.get("isError"):
        return None, [], f"Perplexity tool error: {joined[:200] or 'no detail'}"
    structured = (_structured_citations(result.get("structuredContent"))
                  or _structured_citations(result))
    text, found = _split_citations(joined)
    return text, structured or found, None


# --------------------------------------------------------------- seams

async def perplexity_leads(topic: str) -> tuple[list[dict], str | None]:
    """The research report's seam: `perplexity_research`'s citations as a
    source list, in `_web.search()`'s shape (`title`, `url`, `snippet`).
    Only the URLs — the report fetches each one fresh and writes its own
    synthesis over them; Perplexity's prose is not a source text, so a
    figure only it states never passes the report's number check."""
    answer, reason = await call("perplexity_research", topic, caller="research")
    if answer is None:
        return [], reason
    if not answer.citations:
        return [], "Perplexity cited no sources"
    return [{"title": u, "url": u, "snippet": ""} for u in answer.citations], None


async def search_hits(query: str, limit: int = 5) -> tuple[list[dict], str | None]:
    """`perplexity_search` in `_web.search()`'s hit shape, for `_web`'s
    Perplexity backend to delegate to (Task 36 added that backend calling
    Perplexity directly; Task 50 removed that direct call in favour of this
    one seam — the MCP path with the cap, the scrub and the name rule, and
    no fallback of its own). As Task 36 shapes it: the answer text as the
    `snippet` of a synthetic first hit with `url=""` — raw, because
    `_web.search` never wraps; `research.report` wraps it as "perplexity" —
    then one hit per citation, never more than `limit` in all."""
    answer, reason = await call("perplexity_search", query, caller="search")
    if answer is None:
        return [], reason
    hits = [{"title": "Perplexity", "url": "", "snippet": answer.unwrapped}]
    hits += [{"title": u, "url": u, "snippet": ""}
             for u in answer.citations[:max(limit - 1, 0)]]
    return hits[:limit], None


# ------------------------------------------------------------- the budget

async def check_budget() -> Observation:
    """`perplexity.budget`: this month's calls against the cap, with the time
    it was read, and (Task 50) whether the MCP server itself is actually
    reachable right now and still offers the four tools this module calls.
    There is no chat-completions door to fall back through any more, so a
    dead MCP must be visible here — not just as a reason handed back to
    whichever caller happens to ask next — or invariant 7 is broken: a
    broken collector must never read as healthy.

    UNKNOWN without the key, when the count cannot be read, when the MCP
    handshake fails or refuses the token, or when a tool has gone missing
    from `tools/list` — never a zero it did not measure, and never OK on
    the strength of an old, possibly stale, call. The probe sends
    `initialize` and (when not already cached) `tools/list` only — never a
    `tools/call` — so checking the budget never spends one and never counts
    against the cap."""
    if not configured():
        return Observation("budget", Health.UNKNOWN, detail=(
            f"{KEY_ENV} not set — Perplexity is not configured, nothing is "
            "counted"))
    try:
        used = await calls_this_month()
    except Exception as exc:                            # noqa: BLE001
        return Observation("budget", Health.UNKNOWN, detail=_clean(
            f"could not read perplexity_calls: {type(exc).__name__}: {exc}"))

    key = os.getenv(KEY_ENV)
    _client, offered, reason, stage = await _handshake(key, HANDSHAKE_TIMEOUT)
    if stage != "ok":
        return Observation("budget", Health.UNKNOWN, detail=_clean(reason or ""))
    missing = [t for t in TOOLS if t not in offered]
    if missing:
        return Observation("budget", Health.UNKNOWN, detail=(
            "the Perplexity MCP server no longer offers: "
            f"{', '.join(missing)}"))

    limit = cap()
    now = datetime.now(UTC)
    if used >= limit:
        return Observation("budget", Health.DEGRADED, float(used), "calls",
                           f"perplexity cap reached: {used}/{limit} calls this "
                           f"month, read {now:%H:%M} UTC — every caller is "
                           "refused until next month or the cap is raised",
                           at=now)
    return Observation("budget", Health.OK, float(used), "calls",
                       f"{used}/{limit} Perplexity calls this month, read "
                       f"{now:%H:%M} UTC", at=now)


async def budget_status() -> dict[str, Any]:
    """The Tools screen's view: count and cap with the read time, or the
    reason there is no count."""
    limit = cap()
    if not configured():
        return {"configured": False, "used": None, "cap": limit,
                "read_at": None, "reason": f"{KEY_ENV} not set"}
    try:
        used = await calls_this_month()
    except Exception as exc:                            # noqa: BLE001
        return {"configured": True, "used": None, "cap": limit, "read_at": None,
                "reason": _clean(f"could not read perplexity_calls: {exc}")}
    return {"configured": True, "used": used, "cap": limit,
            "read_at": datetime.now(UTC).isoformat(), "reason": None}
