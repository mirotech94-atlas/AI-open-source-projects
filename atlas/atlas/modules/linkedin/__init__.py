"""LinkedIn — run by ATLAS, on the server.

Until 2026-09-24 this module was a read-only mirror of a LinkedIn agent that
ran twice a day inside the Claude desktop app on Miro's Mac. That agent is
gone. Its state file moved here whole and ATLAS is now the only thing that
reads LinkedIn or writes to it.

What stayed settled, from the agent's handoff, and is not re-litigated:

* There is no LinkedIn API and no stored credential. Miro signs in once
  through `bootstrap/linkedin-login.sh`; the saved browser session is the
  only authentication, and if it is signed out everything stops and says so.
* No scraping, no bulk collection, no auto-accepting invitations, and no
  unattended first contact: ATLAS never opens a conversation. The sweep
  reads the surfaces a person would read, at a person's pace.
* One narrow exception to "no unattended DMs", Miro's words on 2026-10-07:
  "go to guardian and flycheap pages on linkedin and track the services
  that clients want and need and reply to them always based on what they
  want" and "make sure when requests come for either page you will see it
  and reply to it". ATLAS replies, unattended, ONLY to someone who wrote to
  the Guardian or FlyCheap page or requested its services first; once per
  inbound message; from a fact sheet held in code; every rule checked in
  code at send time (`requests.py`: no figures, prices, dates, promises or
  guarantees, no link but the page's own, never claiming to be Miro or a
  person, a daily cap, `LINKEDIN_REPLY_AUTOSEND`). A service request is
  never sent from here -- its proposal form has not been seen -- so it is
  a card with the draft. Every send posts a Slack receipt.
* Only a post-bank entry marked `approved` is ever published, byte for byte.

The pieces: `sweep.py` reads and diffs (a collector: it reads LinkedIn and
writes ATLAS's own state file); `publish.py` holds the one LinkedIn write,
publishing an approved post, as a Tier 2 catalog action -- the daily card is
the approval, and there is no rollback because deleting a post is
permanently denied; `linkedin.approve_draft` turns a draft into an approved
entry, from a card, so the post bank has exactly one source of "approved".
`requests.py` reads the pages' service requests and inboxes (the `requests`
check, its own table `linkedin_requests`), tracks what people ask for by a
closed label set per page, and holds the two Tier 1 reply actions
(`linkedin.draft_request_reply`, `linkedin.send_request_reply`) that
`kernel.linkedin_replies` drives.

Every check here reads the state file; if it is missing every check reports
UNKNOWN with the path it looked at. UNKNOWN is not OK.
"""
from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ...kernel.contracts import (
    AgendaItem,
    Check,
    Health,
    Observation,
    Severity,
    Signal,
)

STATE = Path(os.getenv("LINKEDIN_STATE_PATH",
                       "/app/data/linkedin-agent/state.json"))

# The two company pages, and where each one's post bank actually lives.
# These differ: flycheap's bank sits under `improver`, Guardian's under its
# own page object. Guessing one shape for both silently reads an empty bank
# and reports infinite runway, which is the most dangerous wrong answer here.
PAGES: dict[str, dict[str, str]] = {
    "flycheap": {"page_key": "page", "bank_key": "improver",
                 "followers_field": "followers_count",
                 "id": "116049014", "name": "FlyCheap",
                 "link": "https://flycheap.live"},
    # Display name only: "Vault" is the product's new name (Task 64), but
    # the internal key, the state-file location, the Slack topic
    # (`topic_param="page"` reads `params["page"]`, still "guardian") and
    # the id/link are unchanged -- renaming those would move where pending
    # cards, the follower ledger and the post bank already live. The
    # LinkedIn page's own name changes only through
    # `linkedin.update_page_identity`, a Tier 2 card (Miro asked, 2026-09-28).
    "guardian": {"page_key": "guardian_page", "bank_key": "guardian_page",
                 "followers_field": "followers",
                 "id": "143909038", "name": "Vault",
                 "link": "https://guarder.io"},
}

# Miro, 2026-10-06: "advertise on LinkedIn the course page, ATLAS Academy
# website" and "(a) create the atlas academy page". The lane exists the
# moment the page does: its LinkedIn id goes in LINKEDIN_ACADEMY_PAGE_ID and
# the page joins PAGES at boot. Without the id nothing changes, so no sweep
# ever reads a page that is not there. Store link, never a price in the
# post: the catalog holds the prices and they change.
ACADEMY_PAGE_ENV = "LINKEDIN_ACADEMY_PAGE_ID"


def _academy_page() -> dict[str, str] | None:
    page_id = os.getenv(ACADEMY_PAGE_ENV, "").strip()
    if not page_id.isdigit():
        return None
    return {"page_key": "academy_page", "bank_key": "academy_page",
            "followers_field": "followers", "id": page_id,
            "name": "ATLAS Academy", "link": "https://shop.mapfront.io"}


if (_academy := _academy_page()) is not None:
    PAGES["academy"] = _academy

# Alerts the agent itself labels as not being a LinkedIn problem. It writes
# scheduler stalls and permission-classifier refusals into the same list as
# genuine account restrictions, so a bare "alerts is non-empty" rule would
# page Miro about a resolved tooling issue from eleven days ago.
NOT_LINKEDIN = "not a linkedin alert"

# A restriction is the one thing here that is genuinely urgent: it means the
# account all of this runs on is in trouble.
HARD_STOP_WORDS = ("restrict", "unusual activity", "captcha", "verify it's you",
                   "limit reached", "suspend")

# ...but the agent writes prose, and its most common sentence about a bad run
# is "NOT a LinkedIn restriction/CAPTCHA/limit warning". A bare keyword scan
# fires on exactly the text that exists to say the opposite: all three
# hard-stop keyword hits in the real file are negations, every one of them a
# browser failure. A longer list of negation phrases does not fix this —
# the file contains "NOT a LinkedIn restriction/CAPTCHA/limit warning", "NO
# LinkedIn-side restriction, CAPTCHA or limit warning was seen", and
# "LinkedIn was never reached" — so the negator is found by proximity to the
# keyword instead of by matching whole phrases.
NEGATORS = ("not", "no", "never", "n't", "without", "nothing")
# How far back to look for a negator. Long enough for "NO LinkedIn-side
# restriction", short enough not to cross into a neighbouring sentence.
NEGATION_WINDOW = 48

ALERT_WINDOW = timedelta(days=int(os.getenv("LINKEDIN_ALERT_WINDOW_DAYS", "7")))
# A sweep is not repeated within this many hours of a clean one.
SWEEP_MIN_GAP_HOURS = float(os.getenv("LINKEDIN_SWEEP_MIN_GAP_HOURS", "10"))



class StateUnavailable(Exception):
    """The agent's state file could not be read. Never treated as healthy."""


def _load() -> dict[str, Any]:
    try:
        with STATE.open() as fh:
            data = json.load(fh)
    except FileNotFoundError as exc:
        raise StateUnavailable(
            f"no LinkedIn state at {STATE}. The sweep writes it after its "
            f"first run; until then nothing here can be measured.") from exc
    except json.JSONDecodeError as exc:
        # The handoff says state is written atomically (.tmp then mv). Invalid
        # JSON therefore means a genuinely interrupted write, not a race.
        raise StateUnavailable(f"{STATE} is not valid JSON: {exc}") from exc
    except OSError as exc:
        raise StateUnavailable(f"cannot read {STATE}: {exc}") from exc
    if not isinstance(data, dict):
        raise StateUnavailable(f"{STATE} is not a JSON object")
    return data


def _unknown(check: str, exc: StateUnavailable) -> Observation:
    return Observation(check, Health.UNKNOWN, detail=str(exc))


def _parse_when(raw: Any) -> datetime | None:
    """The agent writes local ISO timestamps with an offset. Anything without
    one is ambiguous, so it is refused rather than assumed to be UTC."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


# ------------------------------------------------------------ post runway

def _bank(state: dict[str, Any], page: str) -> tuple[list[dict], dict]:
    spec = PAGES[page]
    holder = state.get(spec["bank_key"]) or {}
    bank = holder.get("post_bank") or []
    posting = holder.get("posting") or {}
    entries = [e for e in bank if isinstance(e, dict)]
    return entries, posting if isinstance(posting, dict) else {}


async def post_runway(page: str) -> Observation:
    """Days of approved posts left before the page goes quiet.

    The standing instruction is one post per page per day, forever. Only an
    entry with status 'approved' is publishable — a draft never goes out. So
    the runway is simply how many approved entries remain, and when it reaches
    zero the page stops posting with no error anywhere: the agent's gate check
    just finds nothing to publish and moves on.
    """
    check = f"{page}_post_runway"
    try:
        state = _load()
    except StateUnavailable as exc:
        return _unknown(check, exc)

    entries, posting = _bank(state, page)
    if not entries:
        return Observation(
            check, Health.UNKNOWN,
            detail=f"no post bank found for {page} under "
                   f"{PAGES[page]['bank_key']}.post_bank")

    approved = [e for e in entries if e.get("status") == "approved"]
    # Anything not approved and not already out is a draft waiting on Miro.
    waiting = [e for e in entries
               if e.get("status") not in ("approved", "published")]

    if not posting.get("enabled", False):
        return Observation(
            check, Health.OK, float(len(approved)), "days",
            f"posting is disabled for {page}; {len(approved)} approved "
            f"entr{'y' if len(approved) == 1 else 'ies'} held",
            evidence={"approved": len(approved), "enabled": False})

    per_day = max(1, int(posting.get("max_per_day") or 1))
    days = len(approved) // per_day

    health = (Health.FAILING if days == 0
              else Health.DEGRADED if days <= 2
              else Health.OK)
    ids = [str(e.get("id")) for e in approved][:6]
    detail = (f"{len(approved)} approved post(s) for {page} "
              f"= {days} day(s) of runway at {per_day}/day")
    if ids:
        detail += f" ({', '.join(ids)})"
    if days <= 2 and waiting:
        names = ", ".join(str(e.get("id")) for e in waiting[:8])
        detail += (f". {len(waiting)} draft(s) are already written and waiting "
                   f"on your approval ({names}) — writing more would not help, "
                   f"they need a yes. Each one is an approval card in Slack.")
    elif days <= 2:
        detail += (". The bank is empty; the next sweep drafts three, and "
                   "each becomes an approval card.")
    return Observation(
        check, health, float(days), "days", detail,
        evidence={"approved": len(approved), "waiting_on_approval": len(waiting),
                  "published": sum(1 for e in entries
                                   if e.get("status") == "published"),
                  "ids": ids, "last_post": posting.get("last_post")})


# ------------------------------------------------------- follower ledger

async def follower_ledger(page: str) -> Observation:
    """The stored count must equal the number of names actually recorded.

    This invariant has already caught a real silent bug: Guardian held a count
    of 8 against 3 names, so five followers were invisible to every diff and
    no follow-up could ever fire for them. A count that outruns the list is an
    accounting gap, not an arrival, and it fails quietly forever.
    """
    check = f"{page}_follower_ledger"
    try:
        state = _load()
    except StateUnavailable as exc:
        return _unknown(check, exc)

    spec = PAGES[page]
    obj = state.get(spec["page_key"]) or {}
    count = obj.get(spec["followers_field"])
    seen = obj.get("followers_seen")

    if not isinstance(seen, list) or not isinstance(count, int):
        return Observation(
            check, Health.UNKNOWN,
            detail=f"{page}: cannot compare "
                   f"{spec['followers_field']}={count!r} against "
                   f"followers_seen={type(seen).__name__}")

    gap = count - len(seen)
    return Observation(
        check, Health.OK if gap == 0 else Health.DEGRADED,
        float(count), "followers",
        f"{page}: {count} followers, {len(seen)} recorded by name"
        + ("" if gap == 0 else
           f" — {gap} invisible to every diff; the list read was truncated. "
           f"Re-read it with a larger max_chars. Do not retro-queue messages "
           f"off the backfill: a gap being closed is not an arrival."),
        evidence={"count": count, "seen": len(seen), "gap": gap})


# --------------------------------------------------------------- the agent

async def agent_run() -> Observation:
    """Is the sweep still running?

    It runs on the server now, so an old `last_run` means one thing: the
    sweep has not completed. A stalled sweep looks identical to a calm
    week -- nothing posts, nothing is reported, and the silence reads as
    health -- which is why this check exists.
    """
    check = "agent_run"
    try:
        state = _load()
    except StateUnavailable as exc:
        return _unknown(check, exc)

    interval = float(os.getenv("LINKEDIN_AGENT_INTERVAL_HOURS", "12"))
    now = datetime.now(UTC)
    when = _parse_when(state.get("last_run"))
    if when is None:
        return Observation(check, Health.UNKNOWN,
                           detail=f"last_run is missing or has no timezone "
                                  f"offset: {state.get('last_run')!r}")
    hours = (now - when).total_seconds() / 3600
    last = state.get("last_run_result") or {}
    outcome = str(last.get("outcome") or "")
    health = (Health.FAILING if hours > interval * 2
              else Health.DEGRADED if hours > interval
              else Health.OK)
    if health is Health.OK and outcome and outcome != "ok":
        health = Health.DEGRADED
    detail = (f"the LinkedIn sweep last ran {hours:.1f}h ago "
              f"({state.get('runs', '?')} runs total)")
    if outcome and outcome != "ok":
        detail += f"; its outcome was: {outcome[:160]}"
    if health is not Health.OK and hours > interval:
        detail += (f" — expected every {interval:.0f}h. Posting, invite tracking "
                   f"and reply detection are all frozen until it runs again.")
    return Observation(
        check, health, round(hours, 1), "hours", detail,
        evidence={"last_run": state.get("last_run"), "runs": state.get("runs"),
                  "outcome": outcome or None})


def _is_negated(text: str, at: int) -> bool:
    """Is the keyword at `at` inside a sentence denying it?"""
    start = max(0, at - NEGATION_WINDOW)
    window = text[start:at].lower()
    # Don't read across a sentence boundary into the previous statement.
    for stop in (". ", "! ", "\n"):
        cut = window.rfind(stop)
        if cut != -1:
            window = window[cut + len(stop):]
    return any(re.search(rf"\b{re.escape(word)}\b", window)
               for word in NEGATORS)


def _restriction_candidates(alerts: list[dict]) -> list[tuple[dict, str]]:
    """Alerts that look like a LinkedIn-side restriction, with the phrase."""
    out: list[tuple[dict, str]] = []
    for alert in alerts:
        text = str(alert.get("what", ""))
        low = text.lower()
        if low.strip().startswith(NOT_LINKEDIN):
            continue
        for word in HARD_STOP_WORDS:
            at = low.find(word)
            if at != -1 and not _is_negated(text, at):
                out.append((alert, word))
                break
    return out


async def account_alerts() -> Observation:
    """Does the agent's log describe a LinkedIn-side restriction?

    This is the one thing here that would be genuinely urgent — a restricted
    account is the failure the whole no-scraping, no-stored-credential design
    exists to avoid. But the evidence is free prose written by another agent,
    and prose defeats keyword matching in a specific, repeatable way: the
    agent's standard sentence for a failed run is "NOT a LinkedIn
    restriction/CAPTCHA/limit warning", which contains three of the keywords
    and means the opposite. Every hard-stop hit in the real file is of that
    shape.

    So two deliberate choices:

    * A keyword is ignored when a negator sits within a few dozen characters
      before it, in the same sentence.
    * A hit reports DEGRADED, never FAILING, and quotes the alert verbatim.
      A heuristic over someone else's prose must not be the thing that rings
      Miro's phone at 3am — the check says "read this", not "this happened".
    """
    check = "account_alerts"
    try:
        state = _load()
    except StateUnavailable as exc:
        return _unknown(check, exc)

    alerts = [a for a in (state.get("alerts") or []) if isinstance(a, dict)]
    cutoff = datetime.now(UTC) - ALERT_WINDOW
    recent = []
    for alert in alerts:
        when = _parse_when(alert.get("when"))
        if when is not None and when >= cutoff:
            recent.append(alert)

    candidates = _restriction_candidates(recent)
    if candidates:
        alert, word = candidates[-1]
        return Observation(
            check, Health.DEGRADED, float(len(candidates)), "alerts",
            f"an alert from {alert.get('when')} mentions {word!r} without a "
            f"negation, so it may describe a LinkedIn-side restriction. "
            f"ATLAS cannot confirm this from prose — read it and judge. "
            f"Verbatim: \u201c{str(alert.get('what'))[:400]}\u201d",
            evidence={"when": alert.get("when"), "keyword": word,
                      "candidates": len(candidates),
                      "window_days": ALERT_WINDOW.days})

    return Observation(
        check, Health.OK, 0.0, "alerts",
        f"nothing in the last {ALERT_WINDOW.days} days reads as a LinkedIn "
        f"restriction ({len(recent)} alert(s) in window, {len(alerts)} logged "
        f"in total; the rest are the agent's own tooling failures, which it "
        f"labels as such)",
        evidence={"logged": len(alerts), "in_window": len(recent),
                  "window_days": ALERT_WINDOW.days})


# ---------------------------------------------------------------- the sweep

async def sweep_check() -> Observation:
    """Run the sweep and report how it went. It reads LinkedIn and writes
    ATLAS's own state file; nothing on LinkedIn changes."""
    from .sweep import run_sweep
    check = "sweep"
    # The scheduler's timers live in memory, so every restart runs every
    # due check at boot. A browser sweep of LinkedIn every deploy is both
    # wasteful and, on one session, exactly the pattern that got it ended.
    # If the last sweep finished recently and cleanly, say so and wait.
    try:
        state = _load()
        when = _parse_when(state.get("last_run"))
        outcome = str((state.get("last_run_result") or {}).get("outcome") or "")
        if when is not None and outcome == "ok":
            age = (datetime.now(UTC) - when).total_seconds() / 3600
            if age < SWEEP_MIN_GAP_HOURS:
                return Observation(
                    check, Health.OK, round(age, 1), "hours",
                    f"last sweep {age:.1f}h ago was clean; next after "
                    f"{SWEEP_MIN_GAP_HOURS:.0f}h, not on restart")
    except StateUnavailable:
        pass
    try:
        result = await run_sweep()
    except FileNotFoundError as exc:
        return Observation(check, Health.UNKNOWN, detail=f"no state file: {exc}")
    except Exception as exc:                            # noqa: BLE001
        return Observation(check, Health.UNKNOWN,
                           detail=f"the sweep could not run: "
                                  f"{type(exc).__name__}: {str(exc)[:200]}")
    outcome = str(result.get("outcome") or "")
    news = list(result.get("news") or [])
    unread = list(result.get("unread") or [])
    if outcome.startswith("hard stop"):
        return Observation(check, Health.FAILING, detail=outcome,
                           evidence={"news": news[:10]})
    if outcome != "ok":
        return Observation(check, Health.UNKNOWN, detail=outcome,
                           evidence={"news": news[:10], "unread": unread})
    health = Health.DEGRADED if unread else Health.OK
    detail = (f"sweep done: {len(news)} new thing(s)"
              + (f"; {len(unread)} surface(s) could not be read: "
                 f"{', '.join(unread)}" if unread else ""))
    if news:
        detail += " — " + " | ".join(n[:90] for n in news[:5])
    return Observation(check, health, float(len(news)), "changes", detail,
                       evidence={"news": news[:20], "unread": unread})


async def _requests_check() -> Observation:
    """The page requests check (requests.py), imported late like the sweep."""
    from .requests import requests_check
    return await requests_check()


# ------------------------------------------------------------ media fill

async def media_fill_check() -> Observation:
    """Generate the picture for every `needs_media` draft, on both pages.

    Deliberately its own Check rather than folded into `sweep_check`: a
    Gemini still or a slideshow's ffmpeg encode is not a LinkedIn read, so
    it needs no browser session and no browser lock, and it must not share
    the sweep's `timeout=timedelta(minutes=12)` -- a Gemini call already
    takes up to two minutes on its own (`content.stills`'s own timeout),
    and image plus video for two pages could exceed the sweep's budget
    without ever touching LinkedIn.
    """
    check = "media_fill"
    try:
        state = _load()
    except StateUnavailable as exc:
        return _unknown(check, exc)

    pending_before = sum(
        1 for page in PAGES for e in _bank(state, page)[0]
        if e.get("status") == "needs_media")
    if pending_before == 0:
        return Observation(check, Health.OK, 0.0, "pending",
                           "no draft is waiting on a generated picture")

    from .sweep import fill_media_for
    made = 0
    notes: list[str] = []
    for page in PAGES:
        try:
            result = await fill_media_for(page)
            made += int(result.get("made", 0))
        except Exception as exc:                        # noqa: BLE001
            notes.append(f"{PAGES[page]['name']}: {type(exc).__name__}: "
                         f"{str(exc)[:150]}")

    try:
        state = _load()
    except StateUnavailable as exc:
        return _unknown(check, exc)
    pending_after = sum(
        1 for page in PAGES for e in _bank(state, page)[0]
        if e.get("status") == "needs_media")
    health = Health.OK if pending_after == 0 else Health.DEGRADED
    detail = f"{made} of {pending_before} pending image/video generated"
    if pending_after:
        detail += f"; {pending_after} still waiting (retried next cycle)"
    if notes:
        detail += "; " + "; ".join(notes)
    return Observation(check, health, float(made), "generated", detail,
                       evidence={"pending_before": pending_before,
                                 "pending_after": pending_after, "made": made})


# ---------------------------------------------------- kind-aware cards

def _card_body(entry: dict[str, Any], name: str) -> str:
    """What a card shows for one bank entry: the kind, the text or poll
    fields, and (for image/video) whether the picture is already attached.
    Pictures speak louder than words (Task 64) -- the words on the card are
    deliberately short; the picture itself travels as the signal's
    `attachment`, not as anything printed here."""
    kind = entry.get("kind") or "text"
    if kind == "poll":
        poll = entry.get("poll") or {}
        options = "\n".join(f"- {o}" for o in poll.get("options") or [])
        return (f"[poll for {name}, {poll.get('duration', '1w')}, "
                f"{poll.get('visibility', 'public')}]\n"
                f"{poll.get('intro', '')}\n\n{poll.get('question', '')}\n{options}")
    text = str(entry.get("text") or "")
    if kind in ("image", "video"):
        picture = ("picture attached above" if entry.get("media") else
                   "no picture yet — this should not have a card without one")
        return f"[{kind} for {name}, {picture}]\n{text}"
    return text


def _attachment_for(entry: dict[str, Any]) -> str | None:
    """The local file the scheduler uploads into this entry's card (see
    `kernel.comms.attach_to_card`), or None. A poll carries no media -- the
    composer's own poll UI is the picture -- and an image/video entry with
    no `media` yet is never offered a card in the first place, but this
    stays defensive rather than assume that invariant always held."""
    kind = entry.get("kind") or "text"
    media = entry.get("media")
    return media if kind in ("image", "video") and media else None


# --------------------------------------------------------------- open items

def _open_items(state: dict[str, Any]) -> list[dict]:
    """`open_items_*` — the key carries the date it was written, so the name
    is not fixed. Carried-forward work, each entry with a `blocked_by`."""
    out: list[dict] = []
    for key, value in state.items():
        if key.startswith("open_items") and isinstance(value, list):
            out += [item for item in value if isinstance(item, dict)]
    return out


def _days_ago(n: int) -> str:
    return (datetime.now(UTC) - timedelta(days=n)).strftime("%Y-%m-%d")


class LinkedInModule:
    name = "linkedin"
    title = "LinkedIn pages"
    # Nothing required: with no state file every check says UNKNOWN and names
    # the path, which is more useful than refusing to boot.
    requires_env = ()
    # Not required to load, but these unlock checks that are
    # UNKNOWN without them. `atlas.cli connect linkedin` says which.
    # Where: the session comes from bootstrap/linkedin-login.sh
    optional_env = ()

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        def bind(fn, page):
            async def _run() -> Observation:
                return await fn(page)
            return _run

        out = [
            # The sweep itself. Reads LinkedIn at a person's pace, so it is
            # slow; the timeout is its time budget, not a network guess.
            Check("sweep", sweep_check, timedelta(hours=12), Severity.HIGH,
                  "the twice-daily LinkedIn sweep: invites, messages, "
                  "followers, post analytics, profile",
                  baseline_sigma=None, timeout=timedelta(minutes=12)),
            Check("agent_run", agent_run, timedelta(hours=3), Severity.HIGH,
                  "the LinkedIn sweep is still completing",
                  baseline_sigma=None),
            # HIGH, not CRITICAL: this check reads prose heuristically and
            # can be wrong. CRITICAL is the only severity that breaks quiet
            # hours and rings the phone, and a keyword match has not earned
            # that. A confirmed restriction is Miro's judgement to make.
            Check("account_alerts", account_alerts, timedelta(hours=2),
                  Severity.HIGH,
                  "nothing in the agent's log reads as a LinkedIn restriction",
                  baseline_sigma=None),
            # Its own Check, own budget: a Gemini still or an ffmpeg encode
            # is not a LinkedIn read and must not share the sweep's browser
            # lock or its 12-minute timeout.
            Check("media_fill", media_fill_check, timedelta(hours=1),
                  Severity.NORMAL,
                  "the picture for every image/video draft still waiting "
                  "on one has been generated",
                  baseline_sigma=None, timeout=timedelta(minutes=5)),
            # Requests and messages to the Guardian and FlyCheap pages
            # (requests.py). Its own light read, its own budget: four pages
            # at a person's pace, not the sweep's twelve minutes. FAILING
            # when one is unanswered past LINKEDIN_REQUEST_STALE_HOURS.
            Check("requests", _requests_check, timedelta(minutes=30), Severity.HIGH,
                  "requests and messages to the Guardian and FlyCheap pages are "
                  "seen and answered",
                  baseline_sigma=None, timeout=timedelta(minutes=5)),
        ]
        for page in PAGES:
            out += [
                Check(f"{page}_post_runway", bind(post_runway, page),
                      timedelta(hours=6), Severity.HIGH,
                      f"approved posts left before the {page} page goes quiet",
                      baseline_sigma=None),
                Check(f"{page}_follower_ledger", bind(follower_ledger, page),
                      timedelta(hours=12), Severity.NORMAL,
                      f"{page} follower count matches the names recorded",
                      baseline_sigma=None),
            ]
        return tuple(out)

    def actions(self):
        from ...kernel.contracts import Action, Tier
        from . import requests as page_requests
        from .identity import restore_identity, update_identity, update_identity_dry
        from .publish import (
            approve_draft,
            approve_draft_dry,
            publish,
            publish_dry,
            reshare,
            reshare_dry,
            unapprove_draft,
        )
        return (
            Action(
                name="linkedin.publish_post",
                # Tier 2: a post cannot be rolled back without deleting it,
                # and deletion is permanently denied. The daily card is the
                # approval; nothing goes out without a tap.
                tier=Tier.ASK,
                summary="Publish the next approved post to a LinkedIn page "
                        "and reshare it to Miro's profile",
                run=publish, dry_run=publish_dry,
                irreversible=True,
                params={"page": "'flycheap', 'guardian' or 'academy'",
                        "entry_id": "the post-bank id, e.g. B13 (optional; "
                                    "the next approved entry is used)"},
                max_per_hour=2,
                topic_param="page", verbose_card=True,
                # A post takes minutes in a real browser; Slack gives a
                # button three seconds. Run inline, the click read as failed,
                # it was clicked again ("#181 was already approved"), and a
                # deploy mid-run killed it with no record: #181 and #182
                # (2026-09-28 00:14) sat at "approved" forever, unposted. In
                # the background the click is answered at once, the run is
                # bounded, and a shutdown marks it failed with a receipt.
                runs_in_background=True,
            ),
            Action(
                name="linkedin.reshare_post",
                # A reshare is public and cannot be undone without deleting
                # it (permanently denied): Tier 2, one tap. It exists because
                # publishing promised "I'll retry it next sweep" and nothing
                # did (B5, G8 on 2026-09-28).
                tier=Tier.ASK,
                summary="Reshare a published LinkedIn page post to Miro's profile",
                run=reshare, dry_run=reshare_dry,
                irreversible=True,
                params={"page": "'flycheap', 'guardian' or 'academy'",
                        "entry_id": "the published post's id, e.g. B5"},
                max_per_hour=4,
                topic_param="page", verbose_card=True,
                runs_in_background=True,
            ),
            Action(
                name="linkedin.update_page_identity",
                # Tier 2: a page's public name and logo are its identity.
                # Miro asked for it (2026-09-27/28: Guardian -> Vault, the
                # Vault mark); the card is still the one tap that does it.
                tier=Tier.ASK,
                summary="Rename a LinkedIn page and/or replace its logo with "
                        "a file from the brand folder",
                run=update_identity, dry_run=update_identity_dry,
                rollback=restore_identity,
                params={"page": "'flycheap', 'guardian' or 'academy'",
                        "name": "the new page name (optional)",
                        "logo": "a PNG file name in the LinkedIn brand folder, "
                                "square, at least 300px (optional)"},
                max_per_hour=2,
                topic_param="page", verbose_card=True,
                runs_in_background=True,
            ),
            Action(
                name="linkedin.approve_draft",
                # Also Tier 2, for the opposite reason: the card IS the
                # approval UI. Approving flips a draft to approved in the
                # bank; the rollback flips it back.
                tier=Tier.ASK,
                summary="Approve a drafted LinkedIn post so it can be published",
                run=approve_draft, dry_run=approve_draft_dry,
                rollback=unapprove_draft,
                params={"page": "'flycheap', 'guardian' or 'academy'",
                        "entry_id": "the draft's id, e.g. B14",
                        "text_sha256": "sha256 of the text the card showed; "
                                       "a mismatch means the draft changed "
                                       "since and the approval is refused"},
                max_per_hour=6,
                topic_param="page", verbose_card=True,
            ),
            Action(
                name="linkedin.draft_request_reply",
                # Tier 1: writes a draft row on ATLAS's own table; nothing
                # leaves the box. The rollback discards the draft.
                tier=Tier.AUTO,
                summary="Draft a reply to someone who messaged the Guardian or "
                        "FlyCheap page or requested its services, from the page's "
                        "fact sheet; never sends it",
                run=page_requests.draft_request_reply,
                dry_run=page_requests.draft_request_reply_dry,
                rollback=page_requests.draft_request_reply_rollback,
                params={"request_id": "the linkedin_requests.id to answer"},
                max_per_hour=20,
            ),
            Action(
                name="linkedin.send_request_reply",
                # Tier 1 since 2026-10-07 (Miro: "make sure when requests come
                # for either page you will see it and reply to it"). Only a
                # draft id -- never free text (invariant 2). Gated in code at
                # send time (requests._guard_send, the dry run): a page_message
                # thread under the page's own inbox, one reply per inbound, not
                # suppressed, every content rule again, the daily cap and the
                # LINKEDIN_REPLY_AUTOSEND hold; a service request is refused
                # (its proposal form has not been seen) and becomes a card. A
                # refusal is the Tier 2 card with the reason. The rollback is
                # honest: a sent message cannot be unsent (deletion is Tier
                # 3); it suppresses the thread and records why.
                tier=Tier.AUTO,
                summary="Post a drafted reply in the Guardian or FlyCheap page's "
                        "own inbox thread",
                run=page_requests.send_request_reply,
                dry_run=page_requests.send_request_reply_dry,
                rollback=page_requests.send_request_reply_rollback,
                params={"draft_id": "the linkedin_request_drafts.id to send"},
                verbose_card=True,
                max_per_hour=10,
            ),
        )

    async def signals(self):
        try:
            state = _load()
        except StateUnavailable:
            # The checks already report this as UNKNOWN. Emitting a signal too
            # would notify twice for one fact.
            return ()

        # No signal for post runway. `{page}_post_runway` already reports it
        # as DEGRADED/FAILING at Severity.HIGH, which is actionable and
        # routes to Miro on its own. A signal as well is two notifications
        # for one fact -- the same reasoning applied above to missing state.
        # The draft list the signal used to carry now lives in the check's
        # detail, so nothing was lost by dropping it.
        out: list[Signal] = []

        # The daily post, as a card. When a page's gate is open and an
        # approved entry exists, offer it; the scheduler turns a signal that
        # names an action into a proposal with the dry run on the card.
        from .publish import gate, payload_sha256
        from .store import today
        for page in PAGES:
            entry, _why = gate(state, page)
            if entry is not None:
                kind = entry.get("kind") or "text"
                reshare_line = (", then reshares to your profile"
                                if kind != "poll" else "")
                out.append(Signal(
                    title=f"Publish {entry.get('id')} to {PAGES[page]['name']} "
                          f"({today()})",
                    body=(f"Next approved {kind} post for the "
                          f"{PAGES[page]['name']} page. Approve and it goes "
                          f"out now{reshare_line}.\n\n"
                          + _card_body(entry, PAGES[page]["name"])[:600]),
                    severity=Severity.NORMAL,
                    suggests="linkedin.publish_post",
                    params={"page": page, "entry_id": str(entry.get("id"))},
                    attachment=_attachment_for(entry)))
            # A post that went out but whose reshare did not verify, in the
            # last week: one card to reshare it. (The reshare is the
            # distribution: 1 impression without it, 10 with it.)
            for e in _bank(state, page)[0]:
                published = str(e.get("published") or "")
                if (e.get("status") == "published" and e.get("activity_url")
                        and e.get("reshared") is False and published
                        and published >= _days_ago(7)):
                    out.append(Signal(
                        title=f"Reshare {e.get('id')} to your profile",
                        body=(f"{e.get('id')} is live on the {PAGES[page]['name']} "
                              f"page, but its reshare to your profile did not "
                              f"verify. Approve to reshare it.\n{e.get('activity_url')}"),
                        severity=Severity.NORMAL,
                        suggests="linkedin.reshare_post",
                        params={"page": page, "entry_id": str(e.get("id"))}))
            # Drafts waiting: one card each, oldest first, three at a time.
            # A draft still `needs_media` gets no card at all -- an image or
            # video post is never offered for approval before it has its
            # picture.
            entries, _ = _bank(state, page)
            drafts = [e for e in entries if e.get("status") == "draft"][:3]
            for e in drafts:
                # The title is the dedup key, both in the scheduler's own
                # 24h check and in `propose()`'s pending lookup -- so it is
                # keyed on the entry id, stable across sweeps even when the
                # draft's payload is unchanged, never on a hash of it (that
                # used to make an untouched draft's title drift for no
                # reason a person could see). `card_rev` is the one thing
                # that moves it: `approve_draft` bumps it when the payload
                # it is asked to confirm no longer matches the bank, so
                # exactly one fresh card follows a refused, stale approval.
                rev = int(e.get("card_rev") or 1)
                title = f"Approve draft {e.get('id')} for {PAGES[page]['name']}"
                if rev > 1:
                    title += f" (rev {rev})"
                out.append(Signal(
                    title=title,
                    body=_card_body(e, PAGES[page]["name"])[:600],
                    severity=Severity.NORMAL,
                    suggests="linkedin.approve_draft",
                    params={"page": page, "entry_id": str(e.get("id")),
                            "text_sha256": payload_sha256(e)},
                    attachment=_attachment_for(e)))

        blocked = [i for i in _open_items(state) if i.get("blocked_by")]
        if blocked:
            lines = [f"- {str(i.get('what', ''))[:150]}\n  blocked by: "
                     f"{str(i.get('blocked_by'))[:100]}" for i in blocked[:7]]
            # A fixed title: signals dedup on title for 24 h, and a count in
            # it ("3 item(s)" then "4 item(s)") raised this seven times in a
            # week (Miro, 2026-10-06: less chatter). The count is in the body.
            out.append(Signal(
                title="LinkedIn: items waiting on you",
                body=f"{len(blocked)} item(s):\n" + "\n".join(lines)
                     + "\n\nNothing happens on these "
                     "unattended — each one names you as the blocker.",
                severity=Severity.NORMAL,
                # Things waiting on his decision; `linkedin` has no channel
                # and fell to the default.
                params={"topic": "decisions"}))
        return tuple(out)

    async def agenda(self, within: timedelta = timedelta(days=7)):
        """A page with approved posts left has a date it goes quiet."""
        try:
            state = _load()
        except StateUnavailable:
            return ()
        out: list[AgendaItem] = []
        now = datetime.now(UTC)
        for page in PAGES:
            entries, posting = _bank(state, page)
            if not entries or not posting.get("enabled", False):
                continue
            approved = sum(1 for e in entries if e.get("status") == "approved")
            per_day = max(1, int(posting.get("max_per_day") or 1))
            goes_quiet = timedelta(days=approved // per_day)
            if goes_quiet > within:
                continue
            out.append(AgendaItem(
                when=now + goes_quiet,
                title=f"{page} LinkedIn page goes quiet",
                kind="deadline",
                detail=f"last approved post publishes on this day; "
                       f"{approved} left in the bank"))
        return tuple(out)

    def brief(self) -> str:
        from .requests import demand_line
        line = ("LinkedIn: the twice-daily sweep, post runway per page, "
                "account restrictions, follower ledger, page requests answered")
        demand = demand_line()
        return f"{line}; {demand}" if demand else line


MODULE = LinkedInModule()
