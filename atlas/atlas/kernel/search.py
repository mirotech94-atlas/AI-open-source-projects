"""ATLAS's own search: what it recorded, found by words.

the owner, 2026-10-06: "if I say something in a channel, generic or something,
then he should have its own search engine for me". Before this a question
in Slack was answered from the live checks alone; anything ATLAS had done,
proposed, posted or seen earlier was out of reach unless he remembered the
card number.

`search(words)` reads the tables that hold ATLAS's own record -- cards,
findings, incidents, actions, drafts, clips, course posts, lessons -- for
rows that carry every word, newest first, and returns them as `Hit`s with
the time each was recorded. `render()` is the Slack list; `context()` is the
same hits as grounding for a conversational answer, quoted as data.

Only ATLAS's own tables are read; nothing here reaches the web, and a hit's
text is third-party where the row was (a mail subject, a page title), so it
is wrapped untrusted before any prompt sees it (invariant 4).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .state import fetch_all

log = logging.getLogger("atlas.search")

STOP = frozenset((
    "a", "an", "and", "are", "at", "be", "by", "did", "do", "does", "for", "from",
    "has", "have", "how", "i", "in", "is", "it", "its", "me", "my", "of", "on",
    "or", "that", "the", "this", "to", "was", "what", "when", "where", "which",
    "who", "why", "with", "you", "your", "atlas", "search", "find", "about",
    "records", "please"))
MAX_WORDS = 6
MIN_WORD = 2
LIMIT = 8
PER_TABLE = 6
SNIPPET = 160


@dataclass(frozen=True, slots=True)
class Hit:
    source: str             # "card", "finding", ...
    at: datetime
    title: str
    detail: str
    ref: str                # "#123", "clip 9", ...
    times: int = 1          # how many records this line stands for


def fold(hits: list[Hit]) -> list[Hit]:
    """One line per (source, title): a check that was degraded six times in
    two days is one record six times, not six records. The newest stays,
    with the count. Read live 2026-10-06: "cloudco balance" was six identical
    finding lines."""
    seen: dict[tuple[str, str], int] = {}
    out: list[Hit] = []
    for h in hits:
        key = (h.source, h.title)
        if key in seen:
            i = seen[key]
            out[i] = Hit(out[i].source, out[i].at, out[i].title, out[i].detail,
                         out[i].ref, out[i].times + 1)
            continue
        seen[key] = len(out)
        out.append(h)
    return out


def words_of(text: str) -> list[str]:
    """The search words in what he typed: lower-cased, stop words and the
    address dropped, at most MAX_WORDS, in his order."""
    out: list[str] = []
    for raw in re.findall(r"[\w.#@-]+", (text or "").lower()):
        w = raw.strip(".#@-")
        if len(w) < MIN_WORD or w in STOP or w in out:
            continue
        out.append(w)
        if len(out) == MAX_WORDS:
            break
    return out


# Each table: the SQL that finds rows carrying every word (ILIKE against the
# row's text), newest first, and how a row becomes a Hit. `$1` is the list
# of patterns; `text ILIKE ALL($1::text[])` needs every pattern to match.
_QUERIES: dict[str, tuple[str, Any]] = {
    "card": (
        """SELECT id, created_at AS at, action, status, left(reason, 400) AS reason
             FROM proposals
            WHERE (action || ' ' || coalesce(reason, '') || ' ' || status
                   || ' ' || coalesce(params::text, '')) ILIKE ALL($1::text[])
            ORDER BY created_at DESC LIMIT $2""",
        lambda r: Hit("card", r["at"], f"{r['action']} ({r['status']})",
                      str(r["reason"] or ""), f"#{r['id']}")),
    "finding": (
        """SELECT id, at, check_key, health, title, left(detail, 400) AS detail
             FROM findings
            WHERE (check_key || ' ' || coalesce(title, '') || ' '
                   || coalesce(detail, '')) ILIKE ALL($1::text[])
            ORDER BY at DESC LIMIT $2""",
        lambda r: Hit("finding", r["at"], f"{r['check_key']} {r['health']}",
                      str(r["title"] or r["detail"] or ""), f"finding {r['id']}")),
    "incident": (
        """SELECT id, opened_at AS at, resolved_at, title, left(summary, 400) AS summary
             FROM incidents
            WHERE (coalesce(title, '') || ' ' || coalesce(summary, '')) ILIKE ALL($1::text[])
            ORDER BY opened_at DESC LIMIT $2""",
        lambda r: Hit("incident", r["at"],
                      f"{r['title']} ({'resolved' if r['resolved_at'] else 'open'})",
                      str(r["summary"] or ""), f"incident {r['id']}")),
    "action": (
        """SELECT id, at, event, subject, left(payload::text, 300) AS payload
             FROM actions_log
            WHERE event NOT LIKE 'execute.dry_run%'
              AND (event || ' ' || coalesce(subject, '') || ' '
                   || coalesce(payload::text, '')) ILIKE ALL($1::text[])
            ORDER BY at DESC LIMIT $2""",
        lambda r: Hit("action", r["at"], f"{r['event']} {r['subject'] or ''}".strip(),
                      str(r["payload"] or ""), f"log {r['id']}")),
    "draft": (
        """SELECT id, at, kind, status, title, left(body, 300) AS body
             FROM content_drafts
            WHERE (coalesce(title, '') || ' ' || coalesce(body, '') || ' ' || kind
                   || ' ' || status) ILIKE ALL($1::text[])
            ORDER BY at DESC LIMIT $2""",
        lambda r: Hit("draft", r["at"], f"{r['title'] or r['kind']} ({r['status']})",
                      str(r["body"] or ""), f"draft {r['id']}")),
    "clip": (
        """SELECT id, at, campaign, platform, status, post_url, left(caption, 300) AS caption
             FROM clip_outputs
            WHERE (campaign || ' ' || coalesce(platform, '') || ' ' || status || ' '
                   || coalesce(caption, '') || ' ' || coalesce(post_url, '')) ILIKE ALL($1::text[])
            ORDER BY at DESC LIMIT $2""",
        lambda r: Hit("clip", r["at"], f"{r['campaign']} {r['platform'] or ''} ({r['status']})",
                      str(r["post_url"] or r["caption"] or ""), f"clip {r['id']}")),
    "course post": (
        """SELECT id, at, platform, status, link, left(body, 300) AS body
             FROM course_posts
            WHERE (coalesce(platform, '') || ' ' || status || ' ' || coalesce(body, '')
                   || ' ' || coalesce(link, '') || ' ' || coalesce(product, '')) ILIKE ALL($1::text[])
            ORDER BY at DESC LIMIT $2""",
        lambda r: Hit("course post", r["at"], f"{r['platform'] or ''} ({r['status']})",
                      str(r["link"] or r["body"] or ""), f"course post {r['id']}")),
    "lesson": (
        """SELECT id, last_at AS at, agent, action, lesson, times
             FROM lessons
            WHERE cleared_at IS NULL
              AND (coalesce(agent, '') || ' ' || coalesce(action, '') || ' '
                   || lesson) ILIKE ALL($1::text[])
            ORDER BY last_at DESC LIMIT $2""",
        lambda r: Hit("lesson", r["at"], f"{r['agent'] or 'atlas'} {r['action'] or ''}"
                      f" (x{r['times']})", str(r["lesson"]), f"lesson {r['id']}")),
}


def _patterns(words: list[str]) -> list[str]:
    return ["%" + w.replace("%", "").replace("_", r"\_") + "%" for w in words]


async def search(text: str, limit: int = LIMIT) -> list[Hit]:
    """Every record carrying all the words of `text`, newest first, at most
    `limit`. Empty words find nothing (never everything)."""
    words = words_of(text)
    if not words:
        return []
    patterns = _patterns(words)
    hits: list[Hit] = []
    for sql, make in _QUERIES.values():
        try:
            rows = await fetch_all(sql, patterns, PER_TABLE)
        except Exception as exc:                        # noqa: BLE001
            # One table unreadable (a migration not yet run) costs its
            # hits, not the search.
            log.debug("records search skipped a table: %s", type(exc).__name__)
            continue
        for r in rows or []:
            if r.get("at") is None:
                continue
            hits.append(make(r))
    hits.sort(key=lambda h: h.at, reverse=True)
    return fold(hits)[:limit]


def _when(at: datetime, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    age = now - at
    if age.days >= 1:
        return f"{at:%b %-d}"
    return f"{at:%H:%M} UTC"


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:SNIPPET]


def render(hits: list[Hit], text: str, now: datetime | None = None) -> str:
    """The Slack answer: one line per hit, newest first."""
    words = " ".join(words_of(text))
    if not hits:
        return (f"Nothing in my records matches \"{words}\". I searched cards, "
                f"findings, incidents, the action log, drafts, clips, course "
                f"posts and lessons.")
    lines = [f"{len(hits)} record(s) for \"{words}\", newest first:"]
    for h in hits:
        lines.append(f"• {_when(h.at, now)} · {h.source} {h.ref} — {_clean(h.title)}"
                     + (f": {_clean(h.detail)}" if h.detail else "")
                     + (f" (×{h.times}, newest shown)" if h.times > 1 else ""))
    return "\n".join(lines)


async def context(text: str) -> str:
    """The same hits as grounding for a conversational answer: a block the
    model may cite, with each record's time, quoted as data."""
    from .permissions import wrap_untrusted
    hits = await search(text)
    if not hits:
        return ""
    now = datetime.now(UTC)
    body = "\n".join(f"- {_when(h.at, now)} {h.source} {h.ref}: {_clean(h.title)}"
                     + (f" — {_clean(h.detail)}" if h.detail else "") for h in hits)
    return ("RECORDS: what ATLAS itself recorded that matches his words, newest "
            "first, each with the time it was recorded. Cite a record by its "
            "ref when it answers him:\n" + wrap_untrusted(body, "records"))
