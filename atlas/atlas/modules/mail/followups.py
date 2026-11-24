"""Follow-up tracker and triage buckets for mail.

Two behaviours live here:

1. **Follow-ups.** A thread where the owner sent the last message and it reads as
   an ask (a question, or a stock asking phrase) is recorded in
   `mail_followups`. It clears the moment anyone else's message lands in the
   thread. One that has sat unanswered for `min_days` business days (default
   3 — weekends do not count against him) surfaces as an inbox item — and,
   once `min_days` have passed with still no reply, `MODULE.signals()` in
   `__init__.py` offers `mail.draft_nudge`, the one Tier AUTO action that
   actually writes a Gmail draft (through the existing `draft_reply`) and
   records `nudged_at`. `check_followups` and `open_followups` here only
   read Gmail and this module's own bookkeeping table; nothing in this file
   calls `draft_reply` or writes to Gmail — that stays in `__init__.py`,
   behind the one action, run only by the executor.

2. **Triage buckets.** `bucket()` turns the local classifier's reading of a
   thread (from `classify()` in `__init__.py`) into one of five inbox
   buckets: `reply_today`, `reply_this_week`, `fyi`, `noise`, and
   `unclassified` for mail the local model could not read.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from ...kernel.contracts import Health, Observation
from ...kernel.state import execute_sql, fetch_all, fetch_one

log = logging.getLogger("atlas.mail.followups")

# '?' alone catches most; the phrase list catches the asks people forget the
# question mark on ("let me know when you're free.").
_ASK_RE = re.compile(
    r"\b(can you|could you|please|let me know|confirm|send me)\b", re.IGNORECASE)

# Cleared and repopulated by check_followups. brief() is synchronous and
# cannot query Gmail or Postgres itself, so it reads whatever the last run
# of the check found rather than guessing a number (invariant 6) or
# reporting a stale one as current (invariant 7's spirit: say when it's from).
_last: dict[str, Any] = {}

# Logged once, not every cycle: check_followups runs every 2h, and the same
# missing env var does not need saying again each time.
_warned_missing_email = False


def looks_like_an_ask(text: str) -> bool:
    """A question mark, or one of the stock asking phrases, case-insensitive."""
    text = text or ""
    return "?" in text or bool(_ASK_RE.search(text))


def business_days_since(sent_at: datetime, now: datetime | None = None) -> int:
    """Weekdays in (sent_at's date, now's date] — the send day doesn't count
    (he hasn't been waiting on the day he asked), and neither does a weekend.

    Friday to Monday is 1. Saturday to Monday is also 1 (Monday is the only
    weekday in the range). Same day is 0.
    """
    now = now or datetime.now(UTC)
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    day = sent_at.astimezone(UTC).date()
    end = now.astimezone(UTC).date()
    count = 0
    while day < end:
        day += timedelta(days=1)
        if day.weekday() < 5:               # Mon-Fri
            count += 1
    return count


async def record_sent(thread_id: str, sent_at: datetime, to_address: str,
                      subject: str, asked: str | None) -> None:
    """Record (or re-open) a thread where his last message looks like an ask.

    `nudged_at` is deliberately absent from the SET list: re-scanning the
    same still-open thread must not reset the re-nudge cooldown. `resolved_at`
    IS reset to NULL — if this fires, his last message in the thread is once
    again the one nobody has answered, even if an earlier round of the same
    thread was resolved.
    """
    await execute_sql(
        """INSERT INTO mail_followups (thread_id, sent_at, to_address, subject, asked)
           VALUES ($1,$2,$3,$4,$5)
           ON CONFLICT (thread_id) DO UPDATE
             SET sent_at = EXCLUDED.sent_at,
                 to_address = EXCLUDED.to_address,
                 subject = EXCLUDED.subject,
                 asked = EXCLUDED.asked,
                 resolved_at = NULL""",
        thread_id, sent_at, to_address, subject, asked)


async def _mark_resolved(thread_id: str, at: datetime) -> None:
    """Someone else's message is now the last one in the thread."""
    await execute_sql(
        """UPDATE mail_followups SET resolved_at = $2
             WHERE thread_id = $1 AND resolved_at IS NULL""",
        thread_id, at)


async def followup_row(thread_id: str) -> dict | None:
    """The recorded ask for one thread, for `mail.draft_nudge` to compose
    from. `None` if nothing was ever recorded for it."""
    row = await fetch_one(
        """SELECT thread_id, sent_at, to_address, subject, asked,
                  nudged_at, resolved_at
             FROM mail_followups WHERE thread_id = $1""",
        thread_id)
    if row is None:
        return None
    return {
        "thread_id": row["thread_id"],
        "sent_at": row["sent_at"],
        "to_address": row["to_address"],
        "subject": row["subject"],
        "asked": row["asked"],
        "nudged_at": row["nudged_at"],
        "resolved_at": row["resolved_at"],
    }


async def mark_nudged(thread_id: str, at: datetime) -> None:
    """A nudge draft was saved. Recorded so `open_followups` stops offering
    this thread for `min_days` business days, and so a later read can say
    when it was nudged instead of pretending nothing happened."""
    await execute_sql(
        "UPDATE mail_followups SET nudged_at = $2 WHERE thread_id = $1",
        thread_id, at)


async def clear_nudge(thread_id: str) -> None:
    """`mail.draft_nudge`'s rollback: the Gmail draft is gone, so the record
    of having nudged must go with it, or the thread stays hidden from
    `open_followups` for a nudge that no longer exists."""
    await execute_sql(
        "UPDATE mail_followups SET nudged_at = NULL WHERE thread_id = $1",
        thread_id)


# The UTC calendar date a follow-up Signal last fired. Module-level and
# unpersisted: `signals()` runs every scheduler cycle (minutes), and one
# Signal per open follow-up per cycle is exactly the repeated-approval-card
# failure `dedup_key` exists to prevent in kernel/approvals.py -- capping the
# whole group to once a day avoids it without changing approvals.py. A
# process restart re-arms this, which costs at most one extra signal that
# day, not a flood.
_signalled_on: date | None = None


def claim_daily_signal(now: datetime | None = None) -> bool:
    """True at most once per UTC calendar day; False (and no change) if a
    follow-up Signal already fired today. Callers should only call this
    once they know there is something to report -- it is not free to call
    speculatively, since a call on an empty day would burn the day's claim
    for nothing."""
    global _signalled_on
    today = (now or datetime.now(UTC)).date()
    if _signalled_on == today:
        return False
    _signalled_on = today
    return True


# Both "how long before a follow-up counts as open" and "how long after a
# nudge before another one is allowed" -- a nudge is itself a message he
# sent, so the same waiting period applies before it, too, counts as overdue
# again. One number so `mail.draft_nudge`'s cooldown guard and
# `open_followups`'s default can't drift apart.
DEFAULT_MIN_DAYS = 3


async def open_followups(*, min_days: int = DEFAULT_MIN_DAYS) -> list[dict]:
    """Not resolved, aged at least `min_days` business days since it was
    sent, and — if it was nudged before — not nudged within the last
    `min_days` business days either, so a still-silent thread is eligible
    again eventually rather than being nudged once and then never again."""
    now = datetime.now(UTC)
    rows = await fetch_all(
        """SELECT thread_id, sent_at, to_address, subject, asked,
                  nudged_at, resolved_at
             FROM mail_followups
            WHERE resolved_at IS NULL""")
    out: list[dict] = []
    for row in rows:
        # The WHERE clause above already asks Postgres for this; checked
        # again here so a caller that hands `open_followups` a raw table
        # dump (a test's monkeypatched `fetch_all`, or a future caller that
        # widens the query) cannot surface a resolved thread by accident.
        if row["resolved_at"] is not None:
            continue
        sent_at = row["sent_at"]
        if business_days_since(sent_at, now) < min_days:
            continue
        nudged_at = row["nudged_at"]
        if nudged_at is not None and business_days_since(nudged_at, now) < min_days:
            continue
        out.append({
            "thread_id": row["thread_id"],
            "sent_at": sent_at,
            "to_address": row["to_address"],
            "subject": row["subject"],
            "asked": row["asked"],
            "nudged_at": nudged_at,
            "resolved_at": row["resolved_at"],
        })
    return out


async def check_followups() -> Observation:
    """Scans `from:me newer_than:14d`: records a thread where his message is
    last and reads as an ask, resolves one where it no longer is.

    Read-only against Gmail. The only writes are this module's own
    bookkeeping in `mail_followups` — not a Gmail write, not anything a
    third party sees. `_api` is imported here, at call time, from the
    package `__init__` rather than at module load: `__init__.py` imports
    this module, so a module-level `from . import _api` would be a circular
    import, and it would also freeze whatever `_api` was bound to at import
    time instead of picking up a test's `monkeypatch.setattr(mail, "_api", ...)`.
    """
    try:
        from . import _api, _body_text, _header

        data = await _api("/threads", q="from:me newer_than:14d", maxResults=50)
        if not isinstance(data, dict) or not (
                "threads" in data or "resultSizeEstimate" in data):
            # An answer that is neither a thread list nor an honest empty one
            # (Gmail sends `resultSizeEstimate` with no `threads` when there
            # are none) is not a reading: zero follow-ups would be a guess.
            return Observation(
                "followups", Health.UNKNOWN,
                detail="Gmail's thread list had no threads and no size "
                       "estimate, so 'none waiting' cannot be told from a "
                       "bad answer")
        threads = data.get("threads", [])
        gate = asyncio.Semaphore(6)

        async def _one(tid: str) -> dict:
            async with gate:
                # `full`, not `metadata`: the ask-test reads the body, and a
                # metadata thread has none, so every thread read as "not an
                # ask" and the check said zero forever.
                return await _api(f"/threads/{tid}", format="full")

        fulls = await asyncio.gather(*(_one(t["id"]) for t in threads))
    except Exception as exc:                            # noqa: BLE001
        from .._http import redact
        return Observation("followups", Health.UNKNOWN,
                           detail=f"Gmail unreachable: {redact(exc)}")

    me = os.getenv("GOOGLE_ACCOUNT_EMAIL", "")
    if not me:
        # `if me and me in sender` below is false for EVERY sender when `me`
        # is empty, so every thread would fall into the "someone else
        # replied" branch and get marked resolved -- an unset "me" would
        # silently mass-resolve the whole table rather than doing nothing.
        global _warned_missing_email
        if not _warned_missing_email:
            log.warning("GOOGLE_ACCOUNT_EMAIL is not set; cannot tell which "
                       "message in a thread is his, so check_followups is "
                       "refusing to run rather than mass-resolving")
            _warned_missing_email = True
        return Observation(
            "followups", Health.UNKNOWN,
            detail="GOOGLE_ACCOUNT_EMAIL is not set -- cannot tell his "
                  "messages from anyone else's, so nothing was recorded or "
                  "resolved this run")
    now = datetime.now(UTC)
    for full in fulls:
        msgs = full.get("messages", [])
        if not msgs:
            continue
        thread_id = full.get("id", "")
        if not thread_id:
            continue
        last = msgs[-1]
        sender = _header(last, "From")
        if me and me in sender:
            subject = _header(last, "Subject")
            body = _body_text(last.get("payload", {}))
            if looks_like_an_ask(f"{subject}\n{body}"):
                sent_at = datetime.fromtimestamp(
                    int(last.get("internalDate", 0)) / 1000, UTC)
                first_line = next(
                    (ln.strip() for ln in body.splitlines() if ln.strip()), "")
                await record_sent(thread_id, sent_at, _header(last, "To"),
                                  subject, first_line[:200] or None)
        else:
            await _mark_resolved(thread_id, now)

    open_items = await open_followups()
    obs = Observation(
        "followups", Health.OK, float(len(open_items)), "threads",
        f"{len(open_items)} follow-up(s) waiting 3+ business days for a reply"
        if open_items else "no follow-ups waiting",
        evidence={"subjects": [i["subject"] for i in open_items][:10]})
    _last["count"] = len(open_items)
    _last["at"] = obs.at
    return obs


def brief_line() -> str:
    """One clause for `MailModule.brief()`, which is synchronous and cannot
    query Gmail or Postgres itself. Reads the last `check_followups` result;
    says nothing if the check has never run rather than guessing a count."""
    if "count" not in _last:
        return ""
    n = _last["count"]
    at = _last["at"]
    if n == 0:
        return f"no follow-ups waiting (as of {at:%H:%M} UTC)"
    return f"{n} follow-up(s) waiting on a reply (as of {at:%H:%M} UTC)"


def bucket(classification: dict, age_h: float) -> str:
    """Where a thread belongs in the inbox, from the classifier's own
    category and urgency:

        needs_reply, urgency >= 2  -> reply_today
        needs_reply, otherwise     -> reply_this_week
        fyi / bill / client / school / vendor -> fyi
        noise                      -> noise
        {"unclassified": reason}   -> unclassified, at any age

    Unclassified mail was never read by the model, so it is never filed as
    fyi or noise: that would be a guess shown as a reading.

    `age_h` breaks the tie for a category that is none of the above (an
    unexpected category, or `{}` from an exhausted classify budget): an old,
    unrecognised thread is `noise` — likely something that was never going
    to need a reply — but a fresh one is `fyi` rather than being thrown away
    sight unseen.
    """
    if "unclassified" in classification:
        return "unclassified"
    category = classification.get("category")
    urgency = classification.get("urgency") or 0
    if category == "needs_reply":
        return "reply_today" if urgency >= 2 else "reply_this_week"
    if category == "noise":
        return "noise"
    if category in ("fyi", "bill", "client", "school", "vendor"):
        return "fyi"
    return "noise" if age_h > 72 else "fyi"
