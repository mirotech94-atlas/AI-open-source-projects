"""The Academy's growth work (Content agent, six agents phase 8).

* A/B title tests, automatic (Miro, 2026-10-27, final answers): title A's
  views per day over 7 days, then title B's over the next 7, each window
  read from `youtube_videos` (the youtube module's refresh) with the time
  it was read. No reading, no decision; only a public Academy video; the
  switch goes through `youtube.api.update_title`, the same snippet path as
  `academy.refresh_video_metadata` (every other field resent as read), and
  the rollback writes title A back.
* Comment replies: drafted (the comment stored wrapped, untrusted), each a
  Tier 2 card; `academy.send_comment_reply` posts it with
  `youtube.api.post_comment`, once (an idempotency claim), after QA.
* The weekly growth report (Monday 08:00 New York): every figure carries
  the time it was read; a figure that could not be read says so, never 0
  (invariants 6 and 7). Diagnosis and tips are code rules, free tier only.
* `posting_health` and `runway` checks, and the alerts the content job
  sends to #agency-alerts. Alerts are computed here as pure rules over
  reads; only the scheduler job sends them.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ...kernel.contracts import (
    Action,
    ActionResult,
    Check,
    FailureClass,
    Health,
    Observation,
    Severity,
    Tier,
)
from ...kernel.state import execute_sql, fetch_all, fetch_one
from .. import store_origin

log = logging.getLogger("atlas.academy.growth")

WINDOW = timedelta(days=7)
FRESH = timedelta(days=1)          # a reading counts only this close to "now"
SEND_ACTION = "academy.send_comment_reply"
NOT_MEASURED = "not measured"
REPLY = ("Thanks for watching and for the question. The lab files and the quiz are "
         "in the pinned comment; if a step does not work, reply with the step number "
         "and what you saw, and the next lesson will cover it.")
TIPS = (
    ("Put the time promise in the title (\"in 10 minutes\"): it is the rule that "
     "every Academy title follows."),
    ("Use the episode's lesson-specific search phrases as tags; the channel and "
     "series tags are added at upload."),
    ("A real person on camera, even for the first 3 seconds, earns more trust than "
     "a voice alone."),
    "Answer every comment within a day; YouTube counts replies as engagement.",
    "Cut a 30-second Short from each lesson's payoff and link the lesson.",
    ("Share each lesson once in a community that allows it (free), with the "
     "disclosure line."),
)
_TIME_PROMISE = re.compile(r"\b\d+\s*(?:min(?:ute)?s?|hours?|seconds?)\b", re.IGNORECASE)


def _refused(why: str) -> ActionResult:
    return ActionResult(False, why, failure_class=FailureClass.VALIDATION)


def _channel() -> str:
    from . import tutorials
    return tutorials.channel()


def stamp(at: Any) -> str:
    if not isinstance(at, datetime):
        return "read time unknown"
    return "read " + at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


async def public_academy(video_id: str) -> bool:
    row = await fetch_one(
        """SELECT 1 AS ok FROM academy_tutorial_posts
            WHERE video_id=$1 AND public AND NOT withdrawn LIMIT 1""", str(video_id))
    return row is not None


async def reading(video_id: str) -> dict[str, Any] | None:
    """The youtube module's latest snapshot of one video: views and when."""
    row = await fetch_one(
        "SELECT title, views, measured_at FROM youtube_videos WHERE video_id=$1",
        str(video_id))
    if row is None or row["views"] is None or row["measured_at"] is None:
        return None
    return {"title": str(row["title"] or ""), "views": float(row["views"]),
            "at": row["measured_at"]}


# ------------------------------------------------------------ title tests

def variant(title: str) -> str:
    """Title B from title A, by rule: the two halves of "A: B" swapped, else
    the time promise moved to the front, else a beginner's framing."""
    a = " ".join(str(title or "").split())
    if ": " in a:
        head, tail = a.split(": ", 1)
        b = f"{tail}: {head}"
    elif (m := _TIME_PROMISE.search(a)) is not None:
        rest = (a[:m.start()] + a[m.end():]).replace("  ", " ").strip(" -,")
        b = f"In {m.group(0)}: {rest}"
    else:
        b = f"Beginner's guide: {a}"
    b = b[:100].rstrip()
    return b if b.lower() != a.lower() else f"{a[:80]} (step by step)"


def rate(start_views: float, start_at: datetime, end_views: float,
         end_at: datetime) -> float | None:
    days = (end_at - start_at).total_seconds() / 86400
    if days < WINDOW.days or end_views < start_views:
        return None
    return (end_views - start_views) / days


async def _open_test(video_id: str) -> dict[str, Any] | None:
    row = await fetch_one(
        """SELECT * FROM academy_title_tests WHERE video_id=$1
              AND status IN ('drafted', 'running_b') ORDER BY at DESC LIMIT 1""",
        str(video_id))
    return dict(row) if row else None


async def _test_refusal(video_id: str) -> str | None:
    if not await public_academy(video_id):
        return f"{video_id} is not a public Academy video"
    if await _open_test(video_id) is not None:
        return f"{video_id} already has a title test running"
    got = await reading(video_id)
    if got is None or datetime.now(UTC) - got["at"] > FRESH:
        return (f"{video_id}: no views reading in the last day (youtube_videos); "
                "no test starts without one")
    return None


async def draft_title_test_dry(video_id: str) -> ActionResult:
    if (why := await _test_refusal(video_id)) is not None:
        return _refused(why)
    got = await reading(video_id)
    return ActionResult(True, "would start a 7 + 7 day title test",
                        evidence={"title_a": got["title"], "title_b": variant(got["title"])})


async def draft_title_test(video_id: str) -> ActionResult:
    if (why := await _test_refusal(video_id)) is not None:
        return _refused(why)
    got = await reading(video_id)
    row = await fetch_one(
        """INSERT INTO academy_title_tests (video_id, title_a, title_b,
                                            a_views_start, a_read_start)
           VALUES ($1,$2,$3,$4,$5) RETURNING id""",
        str(video_id), got["title"], variant(got["title"]), got["views"], got["at"])
    tid = int(row["id"])
    return ActionResult(True, f"title test {tid} started on title A "
                              f"({got['views']:.0f} views, {stamp(got['at'])})",
                        evidence={"test_id": tid, "title_b": variant(got["title"])},
                        rollback_token=f"draft:{tid}")


async def _switch_plan(video_id: str) -> tuple[dict[str, Any] | None, str]:
    """(what to do, why): {"test", "reading", "to", "status"} or None."""
    test = await _open_test(video_id)
    if test is None:
        return None, f"{video_id} has no title test running"
    if not await public_academy(video_id):
        return None, f"{video_id} is no longer a public Academy video"
    got = await reading(video_id)
    now = datetime.now(UTC)
    if got is None or now - got["at"] > FRESH:
        return None, f"{video_id}: no views reading in the last day; no decision"
    if test["status"] == "drafted":
        start = test["a_read_start"]
        if got["at"] - start < WINDOW:
            return None, (f"window A has {(got['at'] - start).days} of "
                          f"{WINDOW.days} days read")
        return {"test": test, "reading": got, "to": test["title_b"],
                "status": "running_b"}, "window A done: switching to title B"
    start = test["b_read_start"]
    if got["at"] - start < WINDOW:
        return None, f"window B has {(got['at'] - start).days} of {WINDOW.days} days read"
    a = rate(test["a_views_start"], test["a_read_start"], test["a_views_end"],
             test["a_read_end"])
    b = rate(test["b_views_start"], start, got["views"], got["at"])
    if a is None or b is None:
        return None, "a window is unreadable (views went down or too short); no decision"
    if b > a:
        return {"test": test, "reading": got, "to": None, "status": "kept_b",
                "a": a, "b": b}, f"title B wins: {b:.1f} vs {a:.1f} views/day"
    return {"test": test, "reading": got, "to": test["title_a"], "status": "kept_a",
            "a": a, "b": b}, f"title A wins: {a:.1f} vs {b:.1f} views/day"


async def switch_title_dry(video_id: str) -> ActionResult:
    plan, why = await _switch_plan(video_id)
    if plan is None:
        return _refused(why)
    return ActionResult(True, f"would: {why}",
                        evidence={"to": plan["to"], "status": plan["status"]})


async def switch_title(video_id: str) -> ActionResult:
    from ..youtube import api
    plan, why = await _switch_plan(video_id)
    if plan is None:
        return _refused(why)
    test, got = plan["test"], plan["reading"]
    if plan["to"] is not None:
        _before, err = await api.update_title(_channel(), video_id, plan["to"])
        if err is not None:
            return ActionResult(False, f"title not changed: {err}")
    if plan["status"] == "running_b":
        await execute_sql(
            """UPDATE academy_title_tests SET a_views_end=$2, a_read_end=$3,
                      b_views_start=$2, b_read_start=$3, switched_at=now(),
                      status='running_b' WHERE id=$1""",
            int(test["id"]), got["views"], got["at"])
    else:
        await execute_sql(
            """UPDATE academy_title_tests SET b_views_end=$2, b_read_end=$3,
                      decided_at=now(), status=$4 WHERE id=$1""",
            int(test["id"]), got["views"], got["at"], plan["status"])
    return ActionResult(True, why, evidence={"test_id": int(test["id"]),
                                             "status": plan["status"],
                                             "read_at": got["at"].isoformat()},
                        rollback_token=f"title:{int(test['id'])}")


async def restore_title(token: str) -> ActionResult:
    """Rollback for both: a test only drafted is withdrawn; one that switched
    gets title A written back, then is withdrawn."""
    from ..youtube import api
    kind, _, rest = str(token).partition(":")
    if not rest.isdigit():
        return _refused("not a title test token")
    test = await fetch_one("SELECT * FROM academy_title_tests WHERE id=$1", int(rest))
    if test is None:
        return _refused(f"no title test {rest}")
    if kind == "title" and test["switched_at"] is not None:
        _before, err = await api.update_title(_channel(), test["video_id"],
                                              test["title_a"])
        if err is not None:
            return ActionResult(False, f"title A not restored: {err}")
    await execute_sql("UPDATE academy_title_tests SET status='withdrawn' WHERE id=$1",
                      int(rest))
    return ActionResult(True, f"title test {rest} withdrawn; title A stands")


async def open_tests() -> list[str]:
    rows = await fetch_all(
        "SELECT DISTINCT video_id FROM academy_title_tests "
        "WHERE status IN ('drafted', 'running_b')")
    return [str(r["video_id"]) for r in rows]


# --------------------------------------------------------- comment replies

async def draft_comment_reply_dry(video_id: str, comment_id: str) -> ActionResult:
    if not await public_academy(video_id):
        return _refused(f"{video_id} is not a public Academy video")
    return ActionResult(True, f"would read comment {comment_id} and draft a reply",
                        evidence={"text": REPLY})


async def draft_comment_reply(video_id: str, comment_id: str) -> ActionResult:
    from ...kernel.permissions import wrap_untrusted
    from ..youtube import api
    if not await public_academy(video_id):
        return _refused(f"{video_id} is not a public Academy video")
    threads, why = await api.comment_threads(_channel(), video_id)
    if why is not None:
        return ActionResult(False, f"comments unreadable: {why}")
    found = next((t for t in threads if t["comment_id"] == str(comment_id)), None)
    if found is None:
        return _refused(f"no comment {comment_id} under {video_id}")
    wrapped = wrap_untrusted(str(found["text"])[:2000], "youtube comment")
    row = await fetch_one(
        """INSERT INTO academy_comment_drafts (video_id, comment_id, comment_untrusted,
                                               reply)
           VALUES ($1,$2,$3,$4) ON CONFLICT (comment_id) DO NOTHING RETURNING id""",
        str(video_id), str(comment_id), wrapped, REPLY)
    if row is None:
        return _refused(f"comment {comment_id} already has a reply drafted")
    did = int(row["id"])
    from ...kernel.lead_followup import _send_card
    carded = await _send_card(
        SEND_ACTION, {"draft_id": did},
        f"Reply under Academy video {video_id} (the comment is in the evidence, "
        "as untrusted text)",
        {"agent": "Content", "client": "Atlas Academy", "text": REPLY,
         "comment": wrapped}, f"{SEND_ACTION}:{did}")
    return ActionResult(True, f"reply {did} drafted" + ("; card raised" if carded else ""),
                        evidence={"draft_id": did}, rollback_token=str(did))


async def withdraw_reply(token: str) -> ActionResult:
    if not str(token).isdigit():
        return _refused("not a reply draft id")
    await execute_sql(
        """UPDATE academy_comment_drafts SET status='withdrawn'
            WHERE id=$1 AND status='drafted'""", int(token))
    return ActionResult(True, f"reply draft {token} withdrawn")


async def _reply_row(draft_id: int) -> tuple[dict[str, Any] | None, str | None]:
    row = await fetch_one("SELECT * FROM academy_comment_drafts WHERE id=$1",
                          int(draft_id))
    if row is None:
        return None, f"no reply draft {draft_id}"
    if row["status"] != "drafted":
        return None, f"reply draft {draft_id} is {row['status']}"
    if not await public_academy(str(row["video_id"])):
        return None, f"{row['video_id']} is not a public Academy video"
    return dict(row), None


async def send_comment_reply_dry(draft_id: int) -> ActionResult:
    row, why = await _reply_row(draft_id)
    if row is None:
        return _refused(str(why))
    return ActionResult(True, f"would reply under comment {row['comment_id']}",
                        evidence={"text": row["reply"], "to": row["comment_id"]})


async def send_comment_reply(draft_id: int) -> ActionResult:
    from ...kernel import idempotency
    from ..youtube import api
    row, why = await _reply_row(draft_id)
    if row is None:
        return _refused(str(why))
    key = f"reply:{row['comment_id']}"
    state = await idempotency.claim(SEND_ACTION, key)
    if state != idempotency.CLAIMED:
        return ActionResult(False, idempotency.refusal(SEND_ACTION, state))
    made, why = await api.post_comment(_channel(), str(row["video_id"]), row["reply"],
                                       parent_id=str(row["comment_id"]))
    if made is None:
        await idempotency.finish(SEND_ACTION, key, why)
        return ActionResult(False, f"reply not posted: {why}")
    try:
        await execute_sql(
            "UPDATE academy_comment_drafts SET status='sent', reply_id=$2 WHERE id=$1",
            int(draft_id), made["id"])
    finally:
        await idempotency.settle(SEND_ACTION, key, f"reply {made['id']}")
    return ActionResult(True, f"reply {made['id']} posted", evidence={"id": made["id"]})


# ------------------------------------------------------- the weekly report

@dataclass
class Reads:
    """Every figure the report shows, each with its read time or why not."""
    videos: list[dict[str, Any]] | None
    videos_why: str | None
    subs: dict[str, Any] | None
    subs_why: str | None
    plan: list[dict[str, Any]] | None
    posting: dict[str, Any] | None
    posting_why: str | None
    clicks: dict[str, Any] | None
    clicks_why: str | None


def diagnose(videos: list[dict[str, Any]], now: datetime) -> list[str]:
    """Why views are low, by code rule, per video."""
    out = []
    for v in videos:
        title = str(v.get("title") or "")
        pub = v.get("published_at")
        if isinstance(pub, datetime) and v.get("views") is not None:
            days = max(1.0, (now - pub).total_seconds() / 86400)
            if float(v["views"]) / days < 5:
                out.append(f"{title[:60]}: under 5 views a day since it went up")
        if not _TIME_PROMISE.search(title):
            out.append(f"{title[:60]}: no time promise in the title")
        if len(title) > 70:
            out.append(f"{title[:60]}: the title is cut on phones (over 70 characters)")
    return out


def compose_report(r: Reads, now: datetime) -> tuple[str, list[dict[str, Any]]]:
    """The report's text and sections. Every number carries its read time;
    a source that could not be read says so instead of a number."""
    sections: list[dict[str, Any]] = []

    if r.videos is None:
        lines = [f"UNKNOWN: {r.videos_why}"]
    elif not r.videos:
        lines = ["No Academy video has been measured yet."]
    else:
        lines = [f"- {v['title'][:70]}: views {v['views'] if v['views'] is not None else 'UNKNOWN'}"
                 f", watch minutes {v['watch_minutes'] if v['watch_minutes'] is not None else 'UNKNOWN'}"
                 f" ({stamp(v.get('measured_at'))})" for v in r.videos]
    sections.append({"title": "Views and watch time per video", "lines": lines})

    if r.subs is None or r.subs.get("subscribers") is None:
        subs = [f"Subscribers: UNKNOWN ({r.subs_why or 'hidden'})"]
    else:
        subs = [f"Subscribers: {int(r.subs['subscribers'])} ({stamp(r.subs.get('read_at'))})"]
    sections.append({"title": "Subscribers", "lines": subs})

    nxt = ([f"- {p['title'][:70]} [{p['ai_tag']}] week of {p['week_of']}"
            for p in r.plan] if r.plan else
           ["Nothing planned: run academy.plan_content."])
    sections.append({"title": "What to make next", "lines": nxt})

    if r.posting is None:
        post = [f"UNKNOWN: {r.posting_why}"]
    else:
        p = r.posting
        post = [(f"Due {p['due']} (one a day while lessons are on), posted "
                 f"{p['posted']}, failed runs {p['failed']} ({stamp(p['read_at'])})")]
    sections.append({"title": "Posting health", "lines": post})

    if r.clicks is None:
        rev = [f"Store clicks: {NOT_MEASURED} ({r.clicks_why})",
               f"Sales from videos: {NOT_MEASURED} (no join from a video to a sale yet)"]
    else:
        rev = [f"Store page views: {r.clicks['views']} ({stamp(r.clicks['read_at'])})",
               f"Sales from videos: {NOT_MEASURED} (no join from a video to a sale yet)"]
    sections.append({"title": "Revenue link", "lines": rev})

    why = diagnose(r.videos or [], now)
    sections.append({"title": "Why views are low (code rules)",
                     "lines": why or ["No rule fired this week."]})
    sections.append({"title": "Free-tier tips", "lines": list(TIPS)})

    body = "\n\n".join(f"{s['title']}\n" + "\n".join(s["lines"]) for s in sections)
    return body, sections


async def _reads(now: datetime) -> Reads:
    from ..youtube import api
    channel = _channel()
    try:
        rows = await fetch_all(
            """SELECT title, views, watch_minutes, published_at, measured_at
                 FROM youtube_videos WHERE channel=$1 AND privacy='public'
                ORDER BY published_at DESC NULLS LAST LIMIT 25""", channel)
        videos, videos_why = [dict(r) for r in rows], None
    except Exception as exc:  # noqa: BLE001
        videos, videos_why = None, f"youtube_videos unreadable ({type(exc).__name__})"
    subs, subs_why = await api.channel_stats(channel)
    try:
        plan = [dict(r) for r in await fetch_all(
            """SELECT title, ai_tag, week_of FROM academy_content_plan
                WHERE status IN ('planned', 'scripted') ORDER BY week_of, id LIMIT 10""")]
    except Exception:  # noqa: BLE001
        plan = None
    posting, posting_why = await posting_counts(now)
    clicks, clicks_why = await store_clicks(now)
    return Reads(videos, videos_why, subs, subs_why, plan, posting, posting_why,
                 clicks, clicks_why)


async def posting_counts(now: datetime) -> tuple[dict[str, Any] | None, str | None]:
    from . import tutorials
    try:
        posted = await fetch_one(
            """SELECT count(*) AS n FROM academy_tutorial_posts
                WHERE public AND at > $1""", now - WINDOW)
        failed = await fetch_one(
            """SELECT count(*) AS n FROM schedule_runs
                WHERE status='failed' AND claimed_at > $1
                  AND job IN ('content_weekly', 'community')""", now - WINDOW)
    except Exception as exc:  # noqa: BLE001
        return None, f"posting records unreadable ({type(exc).__name__})"
    return {"due": WINDOW.days if tutorials.enabled() else 0,
            "posted": int(posted["n"]), "failed": int(failed["n"]),
            "read_at": now}, None


async def store_clicks(now: datetime) -> tuple[dict[str, Any] | None, str | None]:
    """Store page views from PostHog; "not measured" without the key."""
    if not os.getenv("POSTHOG_PERSONAL_KEY", ""):
        return None, "POSTHOG_PERSONAL_KEY is not set"
    from ..posthog import pageviews_between
    got, why = await pageviews_between(now - WINDOW, now)
    if got is None:
        return None, str(why)
    own = store_origin.host()           # the store's host, not "shop." (gone)
    shop = sum(n for host, n in got.get("sites") or []
               if str(host or "").lower().removeprefix("www.") == own)
    return {"views": shop, "read_at": now}, None


async def weekly_growth_report_dry() -> ActionResult:
    return ActionResult(True, "would read the Academy's videos, subscribers, plan, "
                              "posting and store clicks and write the weekly report")


async def weekly_growth_report() -> ActionResult:
    now = datetime.now(UTC)
    body, sections = compose_report(await _reads(now), now)
    title = f"Academy weekly growth report, {now:%Y-%m-%d}"
    row = await fetch_one(
        """INSERT INTO reports (kind, title, body, sections, modules)
           VALUES ('weekly', $1, $2, $3, $4) RETURNING id""",
        title, body, json.dumps(sections, default=str), ["academy"])
    return ActionResult(True, f"{title} written (report {row['id']})",
                        evidence={"report_id": int(row["id"])},
                        rollback_token=str(int(row["id"])))


async def withdraw_report(token: str) -> ActionResult:
    if not str(token).isdigit():
        return _refused("not a report id")
    await execute_sql(
        "UPDATE reports SET kind='withdrawn' WHERE id=$1 AND kind='weekly'", int(token))
    return ActionResult(True, f"report {token} withdrawn")


# ------------------------------------------------------- health and alerts

async def problems(now: datetime | None = None) -> tuple[list[str], list[str]]:
    """(problems, unreadable): the alert rules over fresh reads. Pure reads."""
    from . import notebooklm, tutorials
    now = now or datetime.now(UTC)
    found: list[str] = []
    unread: list[str] = []
    try:
        if tutorials.enabled() and now.hour >= tutorials.hour_utc() + 2:
            row = await fetch_one(
                "SELECT count(*) AS n FROM academy_tutorial_posts WHERE at >= $1",
                now.replace(hour=0, minute=0, second=0, microsecond=0))
            if int(row["n"]) == 0:
                found.append(f"no Academy tutorial posted today after the "
                             f"{tutorials.hour_utc():02d}:00 UTC tick")
        dupes = await fetch_all(
            """SELECT series, episode, count(*) AS n FROM academy_tutorial_posts
                WHERE public AND NOT withdrawn GROUP BY 1, 2 HAVING count(*) > 1""")
        found += [f"{d['series']} #{d['episode']} has {d['n']} public videos"
                  for d in dupes]
        stalled = await fetch_one(
            """SELECT count(*) AS n FROM handoffs
                WHERE (status='claimed' AND claimed_at < $1)
                   OR (status='queued' AND queued_at < $1)""", now - timedelta(hours=6))
        if int(stalled["n"]):
            found.append(f"the Mac runner has {stalled['n']} job(s) stalled over 6 h")
        # The community module switched off (ATLAS_DISABLED_MODULES) means no
        # AMSM slots are in scope: off by choice, not unmeasured.
        uncovered = [] if community_off() else await fetch_all(
            """SELECT platform, publish_at FROM community_posts
                WHERE publish_at BETWEEN $1 AND $2
                  AND status IN ('planned', 'rendered', 'awaiting_review')""",
            now, now + timedelta(hours=24))
        # Without YOUTUBE_AMSM_* ATLAS cannot schedule or see YouTube uploads
        # (Miro uploads in Studio): those slots are not measured, not missing.
        # check_posting_health says so; Facebook slots still count.
        yt_off = amsm_youtube_unconfigured() is not None
        found += [f"AMSM {u['platform']} slot at {u['publish_at']:%Y-%m-%d %H:%M} UTC "
                  "is not scheduled" for u in uncovered
                  if not (yt_off and u["platform"] == "youtube")]
    except Exception as exc:  # noqa: BLE001
        unread.append(f"posting records ({type(exc).__name__})")
    try:
        twice = await asyncio.to_thread(_refused_twice, notebooklm)
        found += [f"the NotebookLM drop {n} was refused twice" for n in twice]
    except OSError as exc:
        unread.append(f"the NotebookLM refusals ({type(exc).__name__})")
    return found, unread


def _refused_twice(notebooklm: Any) -> list[str]:
    box = notebooklm.inbox() / notebooklm.REFUSED
    if not box.is_dir():
        return []
    seen: dict[str, int] = {}
    for d in box.iterdir():
        m = re.match(r"^(.+)-\d{8}T\d{6}Z$", d.name)
        if m and d.is_dir() and not d.is_symlink():
            seen[m.group(1)] = seen.get(m.group(1), 0) + 1
    return sorted(n for n, c in seen.items() if c >= 2)


def community_off() -> bool:
    """The registry's rule: the community module named in ATLAS_DISABLED_MODULES."""
    from importlib.util import find_spec
    if find_spec("atlas.modules.community") is None:      # not shipped in this build
        return True
    return "community" in {m.strip().lower() for m in
                        os.getenv("ATLAS_DISABLED_MODULES", "").split(",")}


def amsm_youtube_unconfigured() -> str | None:
    """Why ATLAS cannot see AMSM YouTube slots, or None when it can (or when
    the community module is switched off, so there are no slots to see)."""
    if community_off():
        return None
    from ..community import posting
    why = posting.yt_unconfigured()
    return (f"AMSM YouTube slots not measured: channel not configured ({why}); "
            "uploads go through Studio") if why else None


async def check_posting_health() -> Observation:
    found, unread = await problems()
    if unread:
        return Observation("posting_health", Health.UNKNOWN,
                           detail="could not read " + ", ".join(unread),
                           evidence={"problems": found})
    blind = amsm_youtube_unconfigured()
    if found:
        detail = "; ".join(found[:5]) + (f"; {blind}" if blind else "")
        return Observation("posting_health", Health.FAILING, float(len(found)),
                           "problems", detail, evidence={"problems": found})
    if blind:
        # Invariant 7: what cannot be measured is UNKNOWN, never OK.
        return Observation("posting_health", Health.UNKNOWN,
                           detail=f"tutorials, runner, NotebookLM and AMSM Facebook "
                                  f"slots look right; {blind}",
                           evidence={"problems": [], "not_configured": blind})
    if community_off():
        return Observation("posting_health", Health.OK, 0.0, "problems",
                           "tutorials, runner and NotebookLM look right; AMSM "
                           "slots not in scope (community module switched off)")
    return Observation("posting_health", Health.OK, 0.0, "problems",
                       "tutorials, runner, NotebookLM and AMSM slots look right")


async def check_runway() -> Observation:
    from . import tutorials
    days = await tutorials.runway()
    if not days or any(v is None for v in days.values()):
        return Observation("runway", Health.UNKNOWN,
                           detail="the script runway could not be read")
    total = sum(int(v or 0) for v in days.values())
    detail = ", ".join(f"{k} {v}" for k, v in days.items())
    health = Health.OK if total >= 7 else Health.DEGRADED
    return Observation("runway", health, float(total), "days of scripts", detail)


_sent: dict[str, datetime] = {}


async def send_alerts(now: datetime | None = None) -> list[str]:
    """Called by the content job only: each problem to #agency-alerts at most
    once a day (process memory). Nothing is sent for an unreadable source
    but the fact that it could not be read."""
    from ...kernel.comms import Outbound, notify
    now = now or datetime.now(UTC)
    found, unread = await problems(now)
    lines = found + [f"could not read {u}" for u in unread]
    fresh = [p for p in lines if now - _sent.get(p, now - timedelta(days=2))
             >= timedelta(days=1)]
    if fresh:
        await notify(Outbound(title="Content: posting needs a look",
                              body="\n".join(f"- {p}" for p in fresh),
                              topic="agency_alerts"))
        for p in fresh:
            _sent[p] = now
    return fresh


# ------------------------------------------------------------ the job

async def run_weekly() -> dict[str, Any]:
    """The content_weekly job (Monday 08:00 New York): every step an executor
    call, so watch-only holds it; then the deduplicated alerts."""
    from ...kernel import executor
    out: dict[str, Any] = {}

    async def run(action: str, params: dict[str, Any], why: str) -> bool:
        got = await executor.execute(executor.ExecutionRequest(action, params,
                                                               reason=why))
        out[f"{action}:{params.get('video_id', '')}".rstrip(":")] = got.detail
        return bool(got.ok)

    await run("academy.weekly_growth_report", {}, "the Monday growth report")
    await run("academy.plan_content", {"weeks": 2}, "keep two weeks of topics planned")
    for vid in await open_tests():
        await run("academy.switch_title", {"video_id": vid}, "a title test's next step")
    out["alerts"] = len(await send_alerts())
    return out


# --------------------------------------------------------------- catalog

def checks() -> tuple[Check, ...]:
    return (
        Check("posting_health", check_posting_health, timedelta(hours=1),
              Severity.NORMAL, "tutorials post when due, once each; the Mac runner "
              "and NotebookLM ingests move; AMSM slots are scheduled",
              baseline_sigma=None),
        Check("runway", check_runway, timedelta(hours=6), Severity.INFO,
              "days of scripted Academy lessons left to post", baseline_sigma=None),
    )


def actions() -> tuple[Action, ...]:
    video = {"video_id": "a public Academy video's YouTube id"}
    return (
        Action("academy.draft_title_test", Tier.AUTO,
               "Start an A/B title test on a public Academy video (title A is "
               "measured first; nothing changes on YouTube yet)",
               run=draft_title_test, dry_run=draft_title_test_dry,
               rollback=restore_title, params=video, max_per_hour=4),
        Action("academy.switch_title", Tier.AUTO,
               "Move a title test on: to title B after 7 measured days, then keep "
               "the winner; rollback writes title A back",
               run=switch_title, dry_run=switch_title_dry, rollback=restore_title,
               params=video, max_per_hour=4),
        Action("academy.draft_comment_reply", Tier.AUTO,
               "Draft a reply to one comment under a public Academy video",
               run=draft_comment_reply, dry_run=draft_comment_reply_dry,
               rollback=withdraw_reply,
               params={**video, "comment_id": "the top-level comment's id"},
               max_per_hour=10),
        Action(SEND_ACTION, Tier.ASK,
               "Post an approved reply under an Academy video comment",
               run=send_comment_reply, dry_run=send_comment_reply_dry,
               irreversible=True, card_only=True, verbose_card=True,
               topic_for=lambda _p: "agency_decisions",
               params={"draft_id": "the academy_comment_drafts id"}, max_per_hour=10),
        Action("academy.weekly_growth_report", Tier.AUTO,
               "Write the Academy weekly growth report (every figure stamped)",
               run=weekly_growth_report, dry_run=weekly_growth_report_dry,
               rollback=withdraw_report, params={}, max_per_hour=2),
    )
