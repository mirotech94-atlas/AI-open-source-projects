"""The one job this module does: search, fetch, write a cited report, and say
plainly which figures could not be verified.

`run_report` is the only path from a question to a filed report, and it does
the whole thing in one call — search, fetch up to six sources, ask a model
(`Need.HARD`), check its numbers against the raw source text, and store the
result. There is no separate queue worker: `research_reports.status` starts
at 'queued' only as far as the schema default goes; every row this module
writes lands already 'done', 'failed' or (via `withdraw`) 'withdrawn'.

Invariant 6, enforced here rather than trusted to the model: after `think`
returns, `uncited_numbers` re-checks every number-shaped token in the body
against the concatenated raw source text. Anything not found there is listed
under "Unverified figures" rather than silently kept — a model that invents a
number is worse than one that admits it could not find one.

Perplexity (`PERPLEXITY_API_KEY`, `_web.search`'s fallback backend when
`BRAVE_API_KEY` is unset) is a wrapped source here, never an oracle: its
citations extend the ordinary source list, and its own answer text is one
more `wrap_untrusted`-quoted source labelled "perplexity" — it never
becomes the report body directly. But it is a lead, not evidence: Perplexity
did not fetch anything itself, so its answer is deliberately left OUT of the
text `uncited_numbers` checks the body against. A figure that appears only
in Perplexity's own words, and in none of the pages actually fetched (its
own citations included), is exactly the "invented number" invariant 6
exists to catch — it must be listed under "Unverified figures", not pass
because Perplexity said it. `run_report` also refuses the whole report,
never counting Perplexity's answer as a fetched page, if not one real page
was retrieved: an answer with no fetched evidence behind it is the oracle
case this module is not supposed to have a route into.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import UTC, datetime

from ...kernel.contracts import ActionResult, Severity, Signal
from ...kernel.llm import Message, Need, think
from ...kernel.permissions import wrap_untrusted
from ...kernel.state import execute_sql, fetch_all, fetch_one
from .._perplexity import perplexity_leads
from .._web import fetch_text, search

log = logging.getLogger("atlas.research")

MAX_SOURCES = 6

SYSTEM = (
    "You are ATLAS's overnight researcher. You are given a question and a "
    "numbered set of sources. Most are pages fetched from the web moments "
    "ago; any source explicitly labelled 'Perplexity' is not a fetched "
    "page — it is a third party's own answer, offered as a lead to what "
    "might be true, not as evidence. Answer the question using ONLY what "
    "the sources say. Write in markdown. Cite every claim inline with its "
    "source number in brackets, e.g. [1], right after the sentence it "
    "supports. Never state a number, percentage or amount that is not "
    "written in one of the FETCHED sources — a figure that appears only "
    "in the Perplexity source is not verified, and must be stated as "
    "unconfirmed or left out rather than repeated as fact. If you cannot "
    "find a number in a fetched source, say the figure is not available "
    "rather than estimating it."
)

# Perplexity's own [n] citation markers refer to ITS numbering, not this
# report's -- left in place, they read as this report's own citations once
# quoted inside "Source [n] -- Perplexity", and a model can copy one
# straight into the body as if it pointed at an ATLAS source.
_CITATION_MARKER = re.compile(r"\s*\[\d+\]")


def _strip_perplexity_citations(text: str) -> str:
    """Removes Perplexity's own `[n]` markers, and the space left behind
    where one sat right before punctuation ("quarter [1]." -> "quarter.")."""
    return _CITATION_MARKER.sub("", text)

# A number-shaped token: an optional currency sign, digits (with thousands
# separators and a decimal point), an optional magnitude/multiplier suffix
# (K, M, B, k, bn, x, %). The lookaround refuses to touch a word character
# (letter, digit or underscore) or a square bracket on either side, which is
# what keeps a citation marker like "[12]" from reading as the figure "12",
# and a quarter label like "Q1" or a duration like "24h" from reading as
# "1"/"24" — a plain letter-only lookaround let "24h" backtrack to a bare "2"
# once the "h" failed the suffix; requiring \w on both sides blocks that
# retry at the source, because the digit that follows is itself a word
# character.
#
# The digit run is `\d(?:,?\d)*`, not `\d[\d,]*`: the latter lets a comma
# match with no digit after it, so "...on 2026-09-24, following..." matched
# "24," — comma and all — as one token, which is neither a real number nor
# something `_ISO_DATE`'s span excludes (the trailing comma pushes the
# match's end one character past the date's `\b`). Requiring a digit after
# every comma stops the run before a comma that isn't a separator.
_NUMBER = re.compile(
    r"(?<![\w\[])[$€£]?\d(?:,?\d)*(?:\.\d+)?(?:\s?(?:[KMBk]|bn|x|%))?(?![\w\]])")

# A digit run immediately after "<letters>-" with no recognised suffix reads
# as a product code ("PA-440", "GPT-4"), not a figure. A suffix still wins —
# "PA-3220x" would be a strange thing to write, but if it happens it is a
# multiplier, not a label — which is why this only fires when `_HAS_SUFFIX`
# does not match.
_LABEL_PREFIX = re.compile(r"[A-Za-z]-\Z")
_HAS_SUFFIX = re.compile(r"(?:[KMBk]|bn|x|%)\Z")

# 4-digit years and ISO dates are not figures needing a citation. A bare year
# is excluded by value (1900-2099); an ISO date is excluded by span, because
# "2026-09-24" tokenises as three separate digit runs ("2026", "09", "24")
# once hyphens split them — none of the three carries a suffix that would
# turn it into a real quantity.
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_BARE_YEAR = re.compile(r"\A(?:19|20)\d{2}\Z")

_CURRENCY = re.compile(r"[$€£]")


def _normalize(text: str) -> str:
    """Strip currency signs and thousands separators, collapse a space
    before a suffix, and case-fold — so "$50M" in a report body and "50m" in
    a source read as the same figure."""
    text = _CURRENCY.sub("", text)
    text = text.replace(",", "")
    text = re.sub(r"(\d)\s+([KMBk]|bn|x|%)", r"\1\2", text)
    return text.casefold()

# `Module.brief()` is synchronous and takes no argument, so it cannot itself
# query "since when" — the same constraint `judgment.nightly.take_lines`
# solves by draining an in-process buffer. `run_report` appends here on
# success; `take_finished` (called from `brief()`) drains it. In-process
# only: a restart between two brief cycles loses anything not yet drained,
# the same trade the judgment buffer makes for its own handover lines.
_FINISHED: list[str] = []


def take_finished() -> list[str]:
    """Reports filed since the last call. Drains the buffer, so calling this
    twice in a row returns the second batch, empty if nothing ran between."""
    out, _FINISHED[:] = list(_FINISHED), []
    return out


def cite(sources: list[dict]) -> str:
    """"[1] title — url (fetched HH:MM)", one line per source, in order."""
    lines = []
    for i, s in enumerate(sources, start=1):
        lines.append(f"[{i}] {s.get('title', '')} — {s.get('url', '')} "
                     f"(fetched {_hhmm(s.get('fetched_at'))})")
    return "\n".join(lines)


def _hhmm(fetched_at) -> str:
    if isinstance(fetched_at, datetime):
        return fetched_at.strftime("%H:%M")
    if fetched_at:
        try:
            return datetime.fromisoformat(str(fetched_at)).strftime("%H:%M")
        except ValueError:
            return str(fetched_at)
    return "??:??"


def uncited_numbers(body: str, sources_text: str) -> list[str]:
    """Number-shaped tokens in `body` that do not appear, after normalising
    currency signs/separators/case, anywhere in `sources_text`.
    Order-preserving, deduplicated. Citation markers ("[1]"), product codes
    ("PA-440"), quarter labels ("Q1"), durations ("24h"), 4-digit years and
    ISO dates are not figures and are never returned."""
    normalized_sources = _normalize(sources_text)
    date_spans = [m.span() for m in _ISO_DATE.finditer(body)]
    found: list[str] = []
    seen: set[str] = set()
    for match in _NUMBER.finditer(body):
        token = match.group(0)
        if any(start <= match.start() and match.end() <= end
               for start, end in date_spans):
            continue
        if _BARE_YEAR.match(token):
            continue
        # `\Z` anchors to the end of the string it is run against, not to
        # `endpos` — passing `body` with a `pos`/`endpos` window would leave
        # `\Z` pinned to the end of the whole body, not the match, and only
        # ever match a label immediately before the story's last character.
        # Slicing first gives `\Z` an actual end to anchor to.
        if (_LABEL_PREFIX.search(body[:match.start()])
                and not _HAS_SUFFIX.search(token)):
            continue
        if token in seen:
            continue
        seen.add(token)
        if _normalize(token) not in normalized_sources:
            found.append(token)
    return found


async def _failed(question: str, reason: str) -> ActionResult:
    await fetch_one(
        """INSERT INTO research_reports (question, status)
           VALUES ($1, 'failed') RETURNING id""",
        question)
    return ActionResult(False, reason)


async def run_report(question: str, *, _perplexity_first: bool = True
                     ) -> ActionResult:
    """Tier 1: read-only research, filed as a durable report.

    search → fetch ≤6 sources → think(Need.HARD) → markdown with [n]
    citations → a `reports` row (kind='research') → a `research_reports`
    row, done or failed.

    The source list comes from Perplexity's deep research first (Task 42),
    when it is configured and the question is about the world rather than
    the estate; the web search is the fallback. Only its citation URLs are
    used, each fetched fresh — Perplexity's own prose never becomes a source
    text, so a figure only it states is still reported as unverified. If
    not one of its citations can be fetched, the report is run again from
    the web search alone — a report never fails because Perplexity's
    citations did.
    """
    hits, reason = (await perplexity_leads(question) if _perplexity_first
                    else ([], None))
    from_perplexity = bool(hits)
    if not hits:
        hits, reason = await search(question, limit=MAX_SOURCES)
    if reason is not None:
        return await _failed(question, reason)
    if not hits:
        return await _failed(question, f"no search results for {question!r}")

    sources: list[dict] = []
    raw_texts: list[str] = []      # only text actually FETCHED -- what uncited_numbers checks
    blocks: list[str] = []
    fetched_count = 0
    for hit in hits[:MAX_SOURCES]:
        url = hit.get("url", "")
        if not url:
            # `_web.search`'s Perplexity backend has no page to fetch for its
            # own synthetic first hit -- the "page" is the answer it already
            # wrote. That answer reaches the model as a labelled, wrapped
            # source (leads, not evidence), but it is deliberately kept OUT
            # of `raw_texts`: Perplexity is not a fetched page, so a number
            # that appears only in its own words must still be flagged
            # unverified, and it must never let `run_report` treat "an
            # answer with nothing fetched behind it" as a successful report
            # (see `fetched_count` below). Its own [n] markers are stripped
            # -- they are Perplexity's numbering, not this report's.
            answer = hit.get("snippet") or ""
            if not answer:
                continue
            fetched_at = datetime.now(UTC)
            n = len(sources) + 1
            sources.append({"title": "Perplexity answer (leads only, not a fetched page)",
                            "url": "", "fetched_at": fetched_at.isoformat()})
            clean_answer = _strip_perplexity_citations(answer)
            blocks.append(
                f"Source [{n}] — Perplexity's own answer (NOT a fetched page; "
                "leads only, not verified evidence):\n"
                + wrap_untrusted(clean_answer, "perplexity"))
            continue
        text, _reason = await fetch_text(url)
        if text is None:
            continue
        fetched_at = datetime.now(UTC)
        n = len(sources) + 1
        sources.append({"title": hit.get("title") or url, "url": url,
                        "fetched_at": fetched_at.isoformat()})
        raw_texts.append(text)
        fetched_count += 1
        blocks.append(f"Source [{n}] — {hit.get('title') or url} ({url}):\n"
                      + wrap_untrusted(text, url))

    if fetched_count == 0 and from_perplexity:
        # Every Perplexity citation failed to fetch: fall back to the plain
        # web-search source path before giving up.
        return await run_report(question, _perplexity_first=False)
    if fetched_count == 0:
        # Perplexity's own answer, with no fetched page behind it, must
        # never stand in for a source: that is the oracle case by a side
        # route, reached even though `sources` is non-empty.
        return await _failed(question, "every source failed to fetch")

    prompt = (f"Question: {question}\n\n" + "\n\n".join(blocks) +
              "\n\nWrite the report now, citing sources inline as [n].")
    completion = await think(Need.HARD, SYSTEM, [Message("user", prompt)],
                             max_tokens=2000)
    body = completion.text

    sources_text = "\n".join(raw_texts)
    unverified = uncited_numbers(body, sources_text)
    if unverified:
        body += ("\n\n## Unverified figures (not found in sources)\n"
                 + "\n".join(f"- {n}" for n in unverified))
    body += "\n\n## Sources\n" + cite(sources)

    report_row = await fetch_one(
        """INSERT INTO reports (kind, title, body, sections, modules)
           VALUES ('research', $1, $2, $3, $4) RETURNING id""",
        question, body, json.dumps([]), ["research"])
    report_id = report_row["id"] if report_row else None

    rr_row = await fetch_one(
        """INSERT INTO research_reports (question, status, body, sources, report_id)
           VALUES ($1, 'done', $2, $3, $4) RETURNING id""",
        question, body, json.dumps(sources, default=str), report_id)

    # The library counts a finished report too (Task 60) -- best-effort,
    # same as `keep_research`'s own internal guard: filing the report must
    # never depend on this succeeding.
    try:
        from ..vault import library
        entry_source = (f"reports:{report_id}" if report_id is not None
                        else "research_reports")
        await library.keep_research(question, entry_source, body)
    except Exception:
        log.exception("could not add the research report to the library")

    _FINISHED.append(
        f"{question!r} — {len(sources)} source(s)"
        + (f", {len(unverified)} unverified" if unverified else ""))

    return ActionResult(
        True,
        f"filed a research report on {question!r} from {len(sources)} "
        f"source(s)" + (f", {len(unverified)} figure(s) unverified"
                        if unverified else ""),
        evidence={"report_id": report_id, "sources": len(sources),
                 "unverified": unverified,
                 # The research_reports row and its body's hash, for the
                 # read-back (research/readback.py).
                 "research_id": rr_row["id"] if rr_row else None,
                 "body_sha256": hashlib.sha256(body.encode()).hexdigest()[:12]},
        rollback_token=str(rr_row["id"]) if rr_row else None)


async def report_dry(question: str) -> ActionResult:
    """Lists the query it would run and says whether a search provider is
    configured — the one fact that decides whether the real run can do
    anything at all. Brave first, Perplexity as the fallback backend
    (`_web.search`'s own order) — whichever key is actually set is named,
    so the message points at the provider that will really be used."""
    if os.getenv("BRAVE_API_KEY"):
        return ActionResult(
            True, f"would search for {question!r} and fetch up to "
                  f"{MAX_SOURCES} sources (BRAVE_API_KEY is set)")
    if os.getenv("PERPLEXITY_API_KEY"):
        return ActionResult(
            True, f"would ask Perplexity for {question!r} and fetch up to "
                  f"{MAX_SOURCES} sources from its citations "
                  f"(PERPLEXITY_API_KEY is set)")
    return ActionResult(
        False, f"would search for {question!r}, but no BRAVE_API_KEY or "
               f"PERPLEXITY_API_KEY is set — the real run would fail "
               f"before fetching anything")


async def withdraw(token: str) -> ActionResult:
    """Rollback: the report stays on record, marked withdrawn rather than
    deleted — invariant 3 forbids destruction, not disagreement."""
    try:
        report_id = int(token)
    except (TypeError, ValueError):
        return ActionResult(False, f"not a research report id: {token!r}")
    status = await execute_sql(
        "UPDATE research_reports SET status = 'withdrawn' WHERE id = $1",
        report_id)
    # asyncpg's `execute` returns the command tag ("UPDATE 0" / "UPDATE 1"),
    # which is the only way to tell "nothing had that id" from "it worked" —
    # both otherwise return without raising.
    if status == "UPDATE 0":
        return ActionResult(False, f"no such report #{report_id}")
    return ActionResult(True, f"withdrew research report #{report_id}")


async def due_signals() -> tuple[Signal, ...]:
    """One Signal per report finished in the last 24h, so a report filed
    overnight surfaces without Miro having to go looking for it. The filed
    time rides along in the title — every figure that reaches a signal
    carries the time it was read, and a summary quoting one is no different.
    """
    rows = await fetch_all(
        """SELECT at, question, body FROM research_reports
           WHERE status = 'done' AND at > now() - interval '24 hours'
           ORDER BY at DESC""")
    out = []
    for row in rows:
        body = row["body"] or ""
        first = body.split("\n\n", 1)[0].strip() or "(no summary)"
        out.append(Signal(
            title=f"Research: {row['question']} (filed {_hhmm(row['at'])})",
            body=first, severity=Severity.INFO))
    return tuple(out)
