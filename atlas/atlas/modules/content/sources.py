"""content.sources — raw material for a draft, gathered and wrapped.

Nothing here writes. Each source is read from Postgres or built from the
argument itself, then handed back wrapped as untrusted (invariant 4): a
voice transcript, a weekly report and a Slack thread are all someone else's
words captured earlier, and a draft written from them must never treat that
text as an instruction.

The one exception is `note:<text>`: Miro dictating a note straight into
this module, on the dashboard behind the mesh — a channel that
authenticates him. That is wrapped with `principal=True`, which is what
lets the drafting model treat it as his request rather than a quote to be
summarised (see `wrap_untrusted`'s docstring for why the tag and the
trailer must agree).

`gather` never raises. A source it cannot resolve comes back as an empty
wrapped string plus a reason, the same contract as `_web.fetch_text` and
`_http.get_json` — the caller decides what an unmet source means, this
module only reports it.
"""
from __future__ import annotations

from ...kernel.permissions import wrap_untrusted
from ...kernel.state import fetch_all, fetch_one

NOTE_PREFIX = "note:"
VOICE_PREFIX = "voice:"
SLACK_PREFIX = "slack:"
WEEKLY = "weekly"


def _empty(label: str, *, principal: bool = False) -> str:
    return wrap_untrusted("", label, principal=principal)


async def _from_voice(count_text: str) -> tuple[str, str | None]:
    try:
        n = int(count_text)
    except ValueError:
        return _empty("voice_turns"), (
            f"'voice:{count_text}' does not name a number of turns")
    if n <= 0:
        return _empty("voice_turns"), "voice turn count must be positive"

    rows = await fetch_all(
        "SELECT transcript, answer FROM voice_turns ORDER BY at DESC LIMIT $1",
        n)
    if not rows:
        return _empty("voice_turns"), "no voice turns recorded"

    lines: list[str] = []
    for row in reversed(list(rows)):
        lines.append(f"Miro: {row['transcript']}")
        if row["answer"]:
            lines.append(f"ATLAS: {row['answer']}")
    return wrap_untrusted("\n".join(lines), "voice_turns"), None


async def _from_weekly() -> tuple[str, str | None]:
    row = await fetch_one(
        "SELECT title, body FROM reports WHERE kind = 'weekly' "
        "ORDER BY at DESC LIMIT 1")
    if row is None:
        return _empty("reports"), "no weekly report found"
    text = f"{row['title']}\n\n{row['body']}"
    return wrap_untrusted(text, "reports"), None


async def _from_slack(thread_ts: str) -> tuple[str, str | None]:
    if not thread_ts:
        return _empty("slack"), "no Slack thread id given"

    rows = await fetch_all(
        "SELECT content FROM memories "
        "WHERE namespace = 'conversations' AND meta ->> 'thread' = $1 "
        "ORDER BY at",
        thread_ts)
    if not rows:
        return _empty("slack"), f"no memory found for Slack thread {thread_ts!r}"
    text = "\n".join(row["content"] for row in rows)
    return wrap_untrusted(text, "slack"), None


def _from_note(text: str) -> tuple[str, str | None]:
    """Miro's own words, spoken straight into this module. The channel
    authenticates him, so this is the one source wrapped as a request
    rather than data (invariant 4's stated exception)."""
    stripped = text.strip()
    if not stripped:
        return _empty("note", principal=True), "note is empty"
    return wrap_untrusted(stripped, "note", principal=True), None


async def gather(source: str) -> tuple[str, str | None]:
    """Resolve one of: `voice:<n>`, `weekly`, `slack:<thread_ts>`,
    `note:<text>`. Returns `(wrapped_text, reason)`; `reason` is set
    whenever the source could not be produced, and `wrapped_text` is then
    an empty wrap rather than nothing at all."""
    if source.startswith(NOTE_PREFIX):
        return _from_note(source[len(NOTE_PREFIX):])
    if source.startswith(VOICE_PREFIX):
        return await _from_voice(source[len(VOICE_PREFIX):])
    if source == WEEKLY:
        return await _from_weekly()
    if source.startswith(SLACK_PREFIX):
        return await _from_slack(source[len(SLACK_PREFIX):])
    return _empty(source), f"unrecognised source: {source!r}"
