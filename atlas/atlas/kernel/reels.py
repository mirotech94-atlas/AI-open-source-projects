"""The AI-stories account's day, once a day, into #instagram-reels.

What lands in the `instagram_stories` topic (#instagram-reels, through
`SLACK_INSTAGRAM_REELS_CHANNEL`; see `atlas.channels.slack`): the stories
account's publish cards (their `topic_for`) and this file's one daily
digest — the account's overnight moderation line, read fresh from
`instagram.check_overnight("stories")` with the time it was read, the day's
format research, and what became of the day's reels.

The day, in order, every action through the executor (the single path, its
audit line, its dry run and ATLAS_WATCH_ONLY — which refuses all of it by
default, and the digest says so):

  1. `content.reel_research` — Perplexity (90 s deadline) and the stories
     account's insights (UNKNOWN until its token may read them).
  2. For each of today's `REELS_PER_DAY` best formats (less any already
     drafted today): `content.draft_reel`, then `content.render_video`,
     then a dry run of `content.publish_reel` for its per-platform status,
     then ONE Tier 2 card for that reel. Nothing is posted: the card waits
     for the owner, and silence lets it expire.
  3. The preview, so he can WATCH it in Slack before he decides (the owner,
     2026-09-25): the MP4 is uploaded into the card's own thread in
     #instagram-reels (`SlackChannel.upload`, the external-upload flow,
     which needs `files:write`). On `missing_scope` or any other upload
     failure the thread gets the render's public `/media/{token}` link
     instead, with "preview upload needs the files:write scope; link below"
     -- the card itself never fails over its preview. The link is read from
     `content_media` and never written into the card's reason: a proposal's
     reason is audited for good, and a token must not be.

**Cadence.** The goal is at least one post a day on each of the stories
account and TikTok, so `REELS_PER_DAY` defaults to 2: one to post and a
spare. At `REELS_NUDGE_HOUR` (17:00 local by default), if no
`content.publish_reel` card has been approved today, ONE message goes to
#instagram-reels -- "2 reels waiting — approve one to keep today's post" --
once a day (stored in `reports` as `NUDGE_KIND`, so a restart does not
repeat it). Nothing is ever posted on silence (invariant 5): an unanswered
card expires, it never publishes itself.

Once a day, and it survives a restart: the digest is stored in `reports`
(kind `REPORT_KIND`) and `daily()` does nothing when one already exists for
today. A deploy that rebuilds the container mid-morning must not send the
day twice.

Every figure in the digest carries the time it was read (invariant 6).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import UTC, datetime

from . import comms
from .approvals import propose
from .comms import Outbound, notify
from .contracts import Health, Severity
from .executor import ExecutionRequest, execute, preview, watch_only
from .state import execute_sql, fetch_all, fetch_one

log = logging.getLogger("atlas.reels")

TOPIC = "instagram_stories"
REPORT_KIND = "instagram_reels"
TITLE = "Instagram stories — the day"
NUDGE_KIND = "instagram_reels_nudge"
NUDGE_HOUR_DEFAULT = 17


def nudge_hour() -> int:
    import os
    try:
        return max(0, min(23, int(os.getenv("REELS_NUDGE_HOUR",
                                            str(NUDGE_HOUR_DEFAULT)))))
    except ValueError:
        return NUDGE_HOUR_DEFAULT


async def already_sent_today(now: datetime | None = None,
                             kind: str = REPORT_KIND) -> bool | None:
    """Whether today's digest (or nudge, by `kind`) is already on record.
    None when `reports` cannot be read -- the caller then does nothing,
    since a second Perplexity spend and a second set of cards is the worse
    mistake."""
    now = now or datetime.now().astimezone()
    try:
        row = await fetch_one(
            "SELECT max(at) AS at FROM reports WHERE kind = $1", kind)
    except Exception as exc:                            # noqa: BLE001
        log.warning("reels: reports unreadable, not running today: %s", exc)
        return None
    at = row["at"] if row else None
    if at is None:
        return False
    return at.astimezone(now.tzinfo).date() == now.date()


async def overnight_lines() -> list[str]:
    """The stories account's overnight line, read now. UNKNOWN is said in
    words (invariant 7): the line is never presented as fully measured when
    part of it was not."""
    from ..modules import instagram
    try:
        obs = await instagram.check_overnight("stories")
    except Exception as exc:                            # noqa: BLE001
        from ..modules._http import explain
        return [f"Overnight on the stories account: not read — {explain(exc)}"]
    line = f"Overnight on the stories account (read {obs.at:%H:%M} UTC): {obs.detail}"
    if obs.health == Health.UNKNOWN:
        line += " — not fully measured"
    return [line]


async def tiktok_lines() -> list[str]:
    """The AI-stories TikTok, from what `content.reel_metrics` read through
    Postiz in the last 7 days (latest reading per post). Comment moderation
    is not in Postiz's API, so it is said to be unknown, never zero."""
    from .state import fetch_all
    rows = await fetch_all(
        """SELECT DISTINCT ON (post_ref) post_ref, at, views, likes, comments
             FROM reel_metrics
            WHERE platform = 'tiktok' AND at > now() - interval '7 days'
            ORDER BY post_ref, at DESC""")
    moderation = "comment moderation: unknown (Postiz has no moderation API)"
    if not rows:
        return [f"TikTok: no post measured in the last 7 days; {moderation}"]
    read = max(r["at"] for r in rows)

    def total(key: str) -> str:
        got = [r[key] for r in rows if r[key] is not None]
        return f"{sum(got):,.0f}" if got else "not measured"

    return [(f"TikTok (read {read:%H:%M} UTC): {len(rows)} post(s) measured, "
            f"{total('views')} views, {total('likes')} likes, "
            f"{total('comments')} comments over 7 days; {moderation}")]


DIGEST_ENV = "REELS_DIGEST"


def digest_cadence() -> str:
    """the owner, 2026-10-06 ("yes" to content running with no card, reported in
    the weekly): the day's content digest is stored every day but said in
    Slack only in the weekly summary. REELS_DIGEST=daily brings the daily
    #reels message back."""
    return "daily" if os.getenv(DIGEST_ENV, "weekly").strip().lower() == "daily" \
        else "weekly"


async def send_digest(lines: list[str]) -> list[str]:
    """The day's digest, stored so the day is not sent twice (and so the
    weekly can read it). Sent to #instagram-reels only with REELS_DIGEST=daily.
    Returns the channels used."""
    body = "\n".join(lines) or "Nothing to report."
    if digest_cadence() == "daily":
        used = await notify(Outbound(title=TITLE, body=body, topic=TOPIC,
                                     severity=Severity.INFO, kind="digest"))
    else:
        used = []
    try:
        await execute_sql(
            """INSERT INTO reports (kind, title, body, delivered_via)
               VALUES ($1, $2, $3, $4)""",
            REPORT_KIND, TITLE, body, ",".join(used) or "weekly")
    except Exception as exc:                            # noqa: BLE001
        log.warning("reels: the digest went out but was not stored: %s", exc)
    return used


async def week_section(days: int = 7) -> str:
    """The weekly summary's content paragraph: what posted itself this week,
    by platform and lane, from the posting ledgers (never from memory)."""
    reels_by = await fetch_all(
        """SELECT platform, count(*) AS n FROM reel_posts
            WHERE at > now() - ($1 || ' days')::interval GROUP BY platform
            ORDER BY platform""", str(int(days)))
    courses = await fetch_one(
        """SELECT count(*) AS n FROM course_posts
            WHERE posted_at > now() - ($1 || ' days')::interval""", str(int(days)))
    clips = await fetch_one(
        """SELECT count(*) AS n FROM clip_outputs
            WHERE posted_at > now() - ($1 || ' days')::interval""", str(int(days)))
    gated = await fetch_one(
        """SELECT count(*) AS n FROM proposals
            WHERE action = 'content.publish_reel'
              AND created_at > now() - ($1 || ' days')::interval""", str(int(days)))
    read_at = datetime.now(UTC)
    lines = [f"*Content this week* (read {read_at:%H:%M} UTC, last {int(days)} days)"]
    if reels_by:
        lines.append("AI reels posted: " + ", ".join(
            f"{r['platform']} {int(r['n'])}" for r in reels_by))
    else:
        lines.append("AI reels posted: none")
    lines.append(f"Course promos posted: {int((courses or {}).get('n') or 0)}")
    lines.append(f"Campaign clips posted: {int((clips or {}).get('n') or 0)}")
    n_gated = int((gated or {}).get("n") or 0)
    lines.append(f"Held at a gate (carded): {n_gated}" if n_gated
                 else "Held at a gate (carded): none")
    return "\n".join(lines)


async def research_lines() -> list[str]:
    result = await execute(ExecutionRequest(
        action="content.reel_research", params={},
        reason="the daily reel format research"))
    if result.ok:
        return [f"Format research: {result.detail}"]
    return [f"Format research not run: {result.detail}"]


def _tiktok_caption(caption: str) -> str:
    from ..modules.content.crosspost import platform_caption
    return platform_caption(str(caption or ""), "tiktok")


def _card_reason(fmt: dict, drafted: dict, rendered: str, status: str) -> str:
    beats = "\n".join(f"{i}. {b}" for i, b in enumerate(drafted.get("scenes") or [], 1))
    head = (f"Today's AI-stories reel — format: {fmt['format']} (ranking "
            f"weight {float(fmt.get('weight') or 0):g}, {fmt.get('source')})")
    return "\n".join([
        *_card_status(rendered),
        head,
        beats,
        f"Caption: {drafted['caption']}",
        f"TikTok caption: {_tiktok_caption(drafted['caption'])}",
        f"Render: {rendered}",
        f"Per platform: {status}",
        "Nothing posts until you approve; the preview is in this card's thread.",
    ])


def _card_status(rendered: str) -> list[str]:
    """The first lines of a reel card: its quality, then its state, above
    everything else. Creative quality and technical readiness are separate
    things; the 2026-09-27 review found a stills fallback buried mid-card
    while every destination read "ready". (Since dc8b3a9 a Veo failure fails
    the render instead, so the exception line only fires for older media.)"""
    lines = []
    m = re.search(r"stills fallback(?: \(([^)]*)\))?", rendered or "")
    if m:
        why = f" — {m.group(1)}" if m.group(1) else ""
        lines.append(f"*Quality exception: still-image fallback{why}.*")
    lines.append("Status: draft only; nothing is published until you approve.")
    return lines


SCOPE_NOTE = "preview upload needs the files:write scope; link below"


async def _read(path: str) -> bytes:
    import asyncio

    def read() -> bytes:
        with open(path, "rb") as fh:
            return fh.read()

    return await asyncio.to_thread(read)


async def preview_under_card(pid: int, media_id: int) -> str:
    """Upload the render into card #pid's Slack thread; on any failure, put
    the public link there with the reason. Never raises -- the card stands
    whatever becomes of its preview. Returns what happened, for the digest."""
    from ..modules import content
    url, until = await content.public_link(media_id)
    link = (f"{url}\n(link good until {until:%Y-%m-%d %H:%M} UTC; approving "
            f"later re-mints it)" if url else
            "no public link (ATLAS_HOST unset, or the link has lapsed)")
    thread = comms.sent_ref(f"#{pid} — ", "slack")
    slack = comms.CHANNELS.get("slack")
    why: str | None
    if slack is None or not hasattr(slack, "upload"):
        why = "Slack is not configured"
    elif thread is None:
        why = "the card did not go to Slack"
    else:
        path = await content.rendered_path(media_id)
        if path is None:
            why = "the rendered file is no longer on disk"
        else:
            try:
                # Into the channel itself, not the card's thread: the owner
                # (2026-09-27) wants #instagram-reels to show the video, and a
                # file in a thread is invisible from the channel.
                why = await slack.upload(
                    channel=slack.channel_for(TOPIC), thread=None,
                    filename=f"reel-{media_id}.mp4", data=await _read(path),
                    title=f"#{pid} preview",
                    comment=f"#{pid} — approve or skip on the card above. Link: {link}")
            except Exception as exc:                    # noqa: BLE001
                from ..modules._http import explain
                why = f"Slack upload failed: {explain(exc)}"
            if why is None:
                return "preview uploaded to the channel"
    note = SCOPE_NOTE if why and "files:write" in why else (
        f"preview not uploaded ({why}); link below")
    try:
        await notify(Outbound(
            title=f"#{pid} — watch the reel before you decide",
            body=f"{note}\n{link}", topic=TOPIC, thread=thread,
            severity=Severity.NORMAL, kind="receipt"))
    except Exception as exc:                            # noqa: BLE001
        log.warning("reels: the preview link for #%s was not sent: %s", pid, exc)
    return f"link in its place: {why}"


# ----------------------------------------------------------- the 30-day calendar

CALENDAR_ROLLBACK = ("deletes the Instagram and Facebook posts, and the TikTok "
                     "post while Postiz still has it queued; a published TikTok "
                     "post gets a MANUAL note")
# Only the owner's explicit no holds the story. An expired card (nobody answered)
# or a failed one (a platform hiccup) goes back to 'rendered' and is offered
# again the next New York day -- never the same day, so a failing platform
# cannot turn into a card every half hour.
_CARD_DONE = {"executed": "published", "declined": "declined",
              "expired": "rendered", "failed": "rendered"}


PLATFORMS = "\x00platforms\x00"   # filled with the dry run's per-platform line


CAP_REACHED = "automatic post(s) are done"
# The tail of every executor rate-limit refusal (kernel/executor.py).
RATE_LIMITED = "this hour; escalating instead of repeating"
SWITCHED_OFF = "automatic posting is off"


async def post_or_card(params: dict, reason: str, *, dedup: str, media_id: int,
                       rollback_plan: str, series: bool, on_fail: str = "card",
                       seconds: tuple[float, float] | None = None,
                       best_hour: bool = False
                       ) -> tuple[str, int | None, str]:
    """the owner, 2026-09-27: fully automated. The reel posts itself through
    `content.autopublish_reel` (Tier AUTO, its checks in code). the owner,
    2026-09-28: "no need for me to meddle" -- so a reel held only by the
    day's cap WAITS for tomorrow (no card), and a reel that failed a quality
    check is handed back to the caller (`on_fail`): 'redo' to be made again
    once, 'skip' to be dropped, or 'card' -- only then does he see it. With
    automatic posting switched off, every reel is a card as before.
    Returns (outcome, proposal id, detail); outcome is 'posted', 'carded',
    'wait', 'redo', 'skip' or 'none'.

    RA-E5: before either path, the file itself goes through the one quality
    gate (`content.quality.check_media`: native 1080x1920, a playable length
    inside `seconds` -- the series or other band unless the caller passes
    its own -- real sound, the AI disclosure, captions on a series
    episode). A failure is a quality exception, never a silent pass, with or
    without automatic posting: made again once ('redo'), dropped ('skip'),
    or a card whose first line is the exception.

    Reach (the owner, 2026-09-29): the caption gets its lane's niche hashtags
    (`content.hashtags`, closed lists), and with `best_hour` -- only for a
    caller that retries within the day, the calendar tick and the series --
    a reel ready now may WAIT for a better posting hour today
    (`content.reach.best_hour_hold`), never past the day and never around
    the daily cap."""
    from ..modules.content import crosspost
    # The Seller (Plans/Money per reel): every reel ends with its reel type's
    # call to action, before the AI label -- on the card and in the post
    # alike, so what posts is what the card showed.
    try:
        from ..modules.shop import caption_with_cta
        params = {**params, "caption": await caption_with_cta(
            media_id, str(params.get("caption") or ""))}
    except Exception:                                   # noqa: BLE001
        log.warning("reels: no call to action added to media %s", media_id)
    from ..modules.content import hashtags
    params = {**params, "caption": await hashtags.with_lane_tags(
        media_id, str(params.get("caption") or ""))}
    held = ""
    from ..modules.content import quality
    flaw = await quality.check_media(
        media_id, str(params.get("caption") or ""), series=series,
        seconds=seconds or quality.band(series))
    # The content slice (kernel/flows/content.py): with ATLAS_DURABLE_CORE on
    # and `content` in ATLAS_DURABLE_FLOWS, the reel is a workflow and its
    # automatic post goes out through the outbox. Off, nothing below changes.
    from . import flows
    durable_flow = flows.enabled("content")
    if flaw is not None:
        held = f"{quality.EXCEPTION}: {flaw}"
        log.warning("reels: media %s failed the quality gate: %s", media_id, flaw)
        if on_fail in ("redo", "skip"):
            if durable_flow:
                from .flows import content as content_flow
                await content_flow.gate_failed(params, dedup=dedup,
                                               media_id=media_id, flaw=flaw)
            return on_fail, None, held
    elif crosspost.autopublish_enabled() and best_hour and (
            hold := await _best_hour_hold(media_id)) is not None:
        return "wait", None, hold
    elif crosspost.autopublish_enabled():
        said = reason.replace(PLATFORMS, "Instagram, the Facebook Page and TikTok")
        if durable_flow:
            from .flows import content as content_flow
            posted = await content_flow.autopost(params, said[:300], dedup=dedup,
                                                 media_id=media_id)
        else:
            posted = await execute(ExecutionRequest(
                action="content.autopublish_reel", params=params,
                reason=said[:300]))
        if posted.ok and durable_flow:
            # The workflow sends one reconciled receipt once every leg is
            # read back (kernel/flows/content.settle).
            return "posted", None, posted.detail
        if posted.ok:
            try:
                await notify(Outbound(
                    title="Posted a reel on its own",
                    body=(f"{said}\n\n{posted.detail}\n\nIt passed every "
                          f"automatic check. REELS_AUTOPUBLISH=off goes back to "
                          f"a card per reel; the action's rollback deletes the "
                          f"posts."),
                    topic=TOPIC, severity=Severity.INFO, kind="alert"))
            except Exception:                           # noqa: BLE001
                log.warning("reels: posted, but the receipt was not sent")
            return "posted", None, posted.detail
        held = posted.detail
        if crosspost.ALREADY_POSTED in held:
            # An earlier run posted this render (RA-B7): it is done. No
            # receipt -- that run sent one -- and never a remake or a card.
            return "posted", None, held
        # Held by a count, not by the reel: the day's cap, a platform's
        # hourly allowance (RA-C2), or the executor's own hourly limits.
        # It waits; a remake or a card would not change a count.
        # Or by series order: the part before it is still missing a leg
        # the leg sync will post (content.legsync) -- it waits for that.
        from ..modules.content import legsync
        if (CAP_REACHED in held or crosspost.RATE_HELD in held
                or RATE_LIMITED in held or legsync.SERIES_HELD in held):
            return "wait", None, held
        if durable_flow:
            from .flows import content as content_flow
            if content_flow.HELD in held:
                # Queued, uncertain or in flight under its workflow: a
                # remake or a card would post it twice.
                return "wait", None, held
        if SWITCHED_OFF not in held and on_fail in ("redo", "skip"):
            return on_fail, None, held
    status = await preview("content.publish_reel", params)
    if not status.ok:
        if crosspost.ALREADY_POSTED in status.detail:
            return "posted", None, status.detail
        return "none", None, status.detail
    lead = (f"*Quality exception: {flaw}.*\n" if flaw is not None else
            f"Held for you: {held}. " if held else "")
    reason = reason.replace(PLATFORMS, status.detail)
    try:
        pid = await propose("content.publish_reel", params, lead + reason,
                            severity=Severity.NORMAL, dedup_key=dedup,
                            rollback_plan=rollback_plan)
    except ValueError as exc:
        return "none", None, str(exc)
    if durable_flow:
        from .flows import content as content_flow
        await content_flow.carded(params, dedup=dedup, media_id=media_id, pid=pid,
                                  flaw=flaw)
    return "carded", pid, await preview_under_card(pid, media_id)


async def _best_hour_hold(media_id: int) -> str | None:
    from ..modules.content import reach
    try:
        return await reach.best_hour_hold(media_id)
    except Exception:                                   # noqa: BLE001
        return None                 # a nudge, never a gate


async def _sync_calendar_cards() -> None:
    """A carded day becomes published when its card executed, declined when
    it was declined, expired or failed. Read from `proposals`, never assumed."""
    carded = await fetch_all(
        """SELECT c.job_id, p.status FROM reel_calendar c
             JOIN proposals p ON p.id = c.proposal_id
            WHERE c.status = 'carded'""")
    for row in carded:
        after = _CARD_DONE.get(str(row["status"]))
        if after:
            note = (None if after != "rendered" else
                    f"its card {row['status']}; offered again tomorrow")
            await execute_sql(
                """UPDATE reel_calendar SET status = $2, note = $3,
                          updated_at = now() WHERE job_id = $1""",
                row["job_id"], after, note)


def _calendar_reason(j: dict, slot: datetime | None, rendered: str,
                     status: str) -> str:
    when = (f"{slot:%a %d %b %H:%M} New York" if slot else
            "none -- ready now, post it whenever you like")
    from ..modules.content import calendar as cal
    what = "business lesson" if cal.is_business(j) else "reels package"
    hook = j.get("hook_a") or j.get(f"hook_a_{cal.language(j)}") or ""
    return "\n".join(_card_status(rendered)) + "\n" + (
            f"{what}, day {j['day']} ({j.get('series', j.get('lane', ''))} "
            f"{j.get('episode', '')}): {j['title']}. Slot: {when}; approving "
            f"posts it now. "
            f"Hook: {hook}. {rendered} Where it goes: {status}. "
            f"Nothing posts unless you approve; an unanswered card expires.")


_CALENDAR_LOCK = asyncio.Lock()
CALENDAR_TICK_MIN = 30


async def calendar_tick(now: datetime | None = None) -> list[str]:
    """One tick at a time: the scheduler's and the digest's never overlap."""
    if _CALENDAR_LOCK.locked():
        return ["Calendar: a tick is already running"]
    async with _CALENDAR_LOCK:
        # First, whatever the calendar's switch: a reel that posted without
        # one of its legs gets that leg (content.legsync), before any later
        # part of its series is tried in this same tick.
        synced = await _leg_sync()
        return synced + await _calendar_tick(now)


async def _leg_sync() -> list[str]:
    try:
        return await leg_sync()
    except Exception:
        log.exception("reels: the leg sync failed")
        return ["Leg sync: failed (logged)"]


async def leg_sync() -> list[str]:
    """the owner, 2026-09-29: an AI reel is on every configured platform. Each
    missing leg `content.legsync.due()` names -- a retryable refusal of a
    reel posted in the last 96 h, oldest first, one per platform a tick --
    goes through the executor as `content.post_missing_leg` (Tier 1, its
    dry run re-deriving the gap and the series order)."""
    from ..modules._http import explain
    from ..modules.content import legsync
    from ..modules.content.crosspost import NAMES
    try:
        todo = await legsync.due()
    except Exception as exc:                            # noqa: BLE001
        return [f"Leg sync: the posted reels could not be read ({explain(exc)})"]
    lines = []
    for gap in todo:
        name = NAMES[gap.platform]
        result = await execute(ExecutionRequest(
            action=legsync.ACTION,
            params={"media_id": gap.media_id, "platform": gap.platform},
            reason=(f"media {gap.media_id} posted without its {name} leg "
                    f"({gap.said[:120]}); posting only that leg")))
        lines.append(f"Leg sync, media {gap.media_id} on {name}: {result.detail}")
    return lines


async def _calendar_tick(now: datetime | None = None) -> list[str]:
    """Bank clips for the next unfinished days, then raise today's card.

    Runs on the scheduler every `CALENDAR_TICK_MIN` minutes and inside the
    daily digest. Every render is `content.render_calendar_job` through the
    executor; every card is a Tier 2 `content.publish_reel` proposal. A
    RETURN sequel is carded only once the episode before it PUBLISHED --
    a declined or unanswered episode holds the story, it is never skipped."""
    from ..modules.content import calendar as cal
    if not cal.enabled():
        return ["Calendar: off (REELS_CALENDAR)"]
    await cal.ensure_loaded()
    await cal.load_mix(cal.today_local(now))     # the Improver's ratio; 2:1 if none
    await _sync_calendar_cards()
    today = cal.today_local(now)
    state = await _calendar_state(cal)
    lines: list[str] = []
    # An imported episode held by a used daily cap posts first, once the cap
    # frees (content.importer.post_waiting) -- it is already made.
    try:
        from ..modules.content import importer
        lines += await importer.post_waiting()
    except Exception:
        log.exception("reels: posting a waiting imported episode failed")
    # A course reel held by a count (the rolling hourly allowance, the day's
    # cap) posts once the count allows -- it is already made.
    try:
        lines += await course_waiting_lines()
    except Exception:
        log.exception("reels: posting a waiting course reel failed")
    # The series being made goes first (the owner: the hospital comedy is next);
    # while its part waits for a whole day's clips, the packages hold off.
    try:
        said, hold = await series_step()
        lines += said
    except Exception as exc:
        from ..modules._http import explain
        log.exception("reels: the series step failed")
        lines.append(f"Series: failed -- {explain(exc)}")
        hold = False
    for j in ([] if hold else _render_order(cal, state)):
        row = state.get(j["job_id"]) or {}
        if row.get("status") not in ("draft", "rendering"):
            continue                    # blocked days are skipped, not waited on
        result = await execute(ExecutionRequest(
            action="content.render_calendar_job", params={"job_id": j["job_id"]},
            reason=f"30-day calendar day {j['day']}: {j['title']}"))
        lines.append(f"Calendar day {j['day']}: {result.detail}")
        ev = result.evidence or {}
        if ev.get("blocked"):
            continue                    # a refused day does not stall the month
        if result.ok and not ev.get("complete") and ev.get("left") == 0:
            continue                    # all made, still smoothing: bank ahead
        if not result.ok or not ev.get("complete"):
            break                       # the day's allowance is spent here
        state = await _calendar_state(cal)
    for j in cal.scheduled():
        row = state.get(j["job_id"]) or {}
        slot = None                     # a card goes up the moment a reel is ready
        if row.get("status") != "rendered":
            continue
        if row.get("card_day") is not None and row["card_day"] >= today:
            continue                    # one card a day, however it ended
        held = [d for d in cal.dependencies(j)
                if (state.get(d) or {}).get("status") != "published"]
        if held:
            note = f"waiting for {', '.join(held)} to publish first"
            await execute_sql("UPDATE reel_calendar SET note = $2 WHERE job_id = $1",
                              j["job_id"], note)
            lines.append(f"Calendar day {j['day']}: rendered, no card -- {note}")
            continue
        params = {"media_id": row["media_id"], "caption": cal.caption(j)}
        outcome, pid, said = await post_or_card(
            params, _calendar_reason(j, slot, f"Media {row['media_id']}.",
                                     PLATFORMS),
            dedup=f"calendar:{j['job_id']}", media_id=row["media_id"],
            rollback_plan=CALENDAR_ROLLBACK, series=cal.is_series(j),
            seconds=_calendar_seconds(j), best_hour=True,
            on_fail="redo" if int(row.get("redos") or 0) == 0 else "card")
        if outcome == "wait":
            # The day's cap (posts tomorrow) or an hourly allowance (the
            # next tick): the row is left as it is, so every tick retries.
            lines.append(f"Calendar {j['job_id']}: ready, waiting -- {said}")
            continue
        if outcome == "redo":
            await cal.redo(j["job_id"], said)
            lines.append(f"Calendar {j['job_id']}: failed a check, being made "
                         f"again (once) -- {said}")
            continue
        if outcome == "none":
            lines.append(f"Calendar {j['job_id']}: rendered, not posted -- {said}")
            continue
        if outcome == "posted":
            await execute_sql(
                """UPDATE reel_calendar SET status = 'published', card_day = $2,
                          note = NULL, updated_at = now() WHERE job_id = $1""",
                j["job_id"], today)
            lines.append(f"Calendar {j['job_id']}: posted -- {said}")
            state = await _calendar_state(cal)      # a sequel may now go
            continue
        await execute_sql(
            """UPDATE reel_calendar SET status = 'carded', proposal_id = $2,
                      card_day = $3, note = NULL, updated_at = now()
                WHERE job_id = $1""",
            j["job_id"], pid, today)
        lines.append(f"Calendar {j['job_id']}: card #{pid} ({said})")
    return lines or [f"Calendar: nothing due (read {datetime.now(UTC):%H:%M} UTC)"]


# ------------------------------------------------------------- the series

async def series_step() -> tuple[list[str], bool]:
    """Move the series being made one step: draft its next part, render it,
    post it (or card it). Returns (lines, hold) -- `hold` is True while a
    drafted part waits for a whole day's Veo clips, so the packages do not
    spend them first. A part is made only after the one before it posted;
    when the finale posts, the next queued series starts, and when none is
    queued one is planned in the top-priority serial format."""
    from ..modules.content import calendar as cal
    from ..modules.content import reel_script, series
    from ..modules.content import reels as playbook
    await series.ensure_seeds()
    await _sync_series_cards()
    current = await series.current()
    if current is None:
        formats = [f for f in await playbook.top_formats(200)
                   if playbook.bucket_of(f["format"]) == "serial"]
        # the owner's priority first; within a rank, the format whose earlier
        # series earned more (series.format_bonus). Unreadable results rank
        # as before -- never a penalty.
        try:
            earned = series.format_bonus(await series.results_digest())
        except Exception as exc:                        # noqa: BLE001
            log.warning("series results unreadable, choosing the format "
                        "without them: %s", exc)
            earned = {}
        formats.sort(key=lambda f: (playbook.priority(f["format"]),
                                    -earned.get(str(f["format"]).lower(), 0.0)))
        if not formats:
            return ["Series: none queued and no serial format to plan one"], False
        planned = await execute(ExecutionRequest(
            action="content.plan_series",
            params={"format_name": formats[0]["format"]},
            reason="the last series ended; planning the next"))
        return [f"Series: {planned.detail}"], False
    sid, title = int(current["id"]), current["title"]
    made = await series.parts(sid)
    last = made[-1] if made else None
    if last is None or last["status"] == "posted":
        drafted = await execute(ExecutionRequest(
            action="content.draft_series_part", params={"series_id": sid},
            reason=f"the next part of {title}"))
        if not drafted.ok:
            return [f"Series {title}: {drafted.detail}"], False
        made = await series.parts(sid)
        last = made[-1]
    n = int(last["part_no"])
    if last["status"] == "drafted" and await _series_rendered_recently():
        # About one finished reel a day, alternating: a series part one day,
        # a packaged or regular reel the next (relayed, 2026-09-28).
        return [(f"Series {title} part {n}: drafted; today is a regular reel's "
                 f"day (one finished reel a day, alternating)")], False
    if last["status"] == "drafted":
        row = await fetch_one("SELECT body FROM content_drafts WHERE id = $1",
                              last["draft_id"])
        beats = len(reel_script.parse(row["body"] if row else "",
                                      max_scenes=20).scenes)
        used = await cal.clips_today()
        left = cal.daily_clips() - used
        if left < beats:
            # Hold the packages only while today's clips are untouched and
            # still short (a lower allowance); once anything else has spent
            # some, the leftovers go to the packages and this part renders
            # first thing tomorrow, when the tick reaches it before them.
            return [(f"Series {title} part {n}: drafted, waiting for {beats} Veo "
                     f"clips ({max(0, left)} left today)")], used == 0
        rendered = await execute(ExecutionRequest(
            action="content.render_video", params={"draft_id": last["draft_id"]},
            reason=f"{title} part {n}"))
        if not rendered.ok:
            return [f"Series {title} part {n}: not rendered -- {rendered.detail}"], False
        media_id = int((rendered.evidence or {})["media_id"])
        await series.mark(sid, n, "rendered", media_id)
        last = {**last, "status": "rendered", "media_id": media_id}
    if last["status"] == "rendered":
        caption = reel_script.parse((await fetch_one(
            "SELECT body FROM content_drafts WHERE id = $1",
            last["draft_id"]))["body"]).caption
        reason = (f"{title} -- part {n} of {current['total_parts']}. Media "
                  f"{last['media_id']}. Where it goes: {PLATFORMS}.")
        outcome, pid, said = await post_or_card(
            {"media_id": last["media_id"], "caption": caption}, reason,
            dedup=f"series:{sid}:{n}", media_id=last["media_id"],
            rollback_plan=CALENDAR_ROLLBACK, series=True, best_hour=True,
            on_fail="redo" if int(last.get("redos") or 0) == 0 else "card")
        if outcome == "wait":
            return [f"Series {title} part {n}: ready, waiting -- {said}"], False
        if outcome == "redo":
            await series.redo(sid, n)
            return [(f"Series {title} part {n}: failed a check, rendering it "
                     f"again (once) -- {said}")], False
        if outcome == "posted":
            await series.mark(sid, n, "posted")
            return [f"Series {title} part {n}: posted -- {said}"], False
        if outcome == "carded":
            await execute_sql(
                """UPDATE reel_series_parts SET status = 'carded', proposal_id = $3
                    WHERE series_id = $1 AND part_no = $2""", sid, n, pid)
            return [f"Series {title} part {n}: card #{pid} ({said})"], False
        return [f"Series {title} part {n}: rendered, not posted -- {said}"], False
    return [f"Series {title} part {n}: {last['status']}"], False


ALTERNATE_HOURS = 20


async def _series_rendered_recently() -> bool:
    row = await fetch_one(
        f"""SELECT count(*) AS n FROM content_media m
              JOIN content_drafts d ON d.id = m.draft_id
             WHERE d.source LIKE 'series:%' AND m.status = 'done'
               AND m.at >= now() - interval '{ALTERNATE_HOURS} hours'""")
    return bool(row) and int(row["n"]) > 0


async def _sync_series_cards() -> None:
    """A carded part becomes posted when its card executed; an expired or
    failed one goes back to rendered to be tried again; a no is kept."""
    rows = await fetch_all(
        """SELECT s.series_id, s.part_no, p.status FROM reel_series_parts s
             JOIN proposals p ON p.id = s.proposal_id
            WHERE s.status = 'carded'""")
    from ..modules.content import series
    for row in rows:
        after = _CARD_DONE.get(str(row["status"]))
        if after == "published":
            await series.mark(int(row["series_id"]), int(row["part_no"]), "posted")
        elif after:
            await series.mark(int(row["series_id"]), int(row["part_no"]), after)


def _render_order(cal, state: dict[str, dict]) -> list[dict]:
    """Which packaged reel gets today's clips first. the owner, 2026-09-28:
    "continue with other days, don't wait for day 1". A reel whose sequel
    rule is not met yet (its episode has not posted) waits; among the rest,
    the one closest to finished goes first -- so a short reel is not held
    behind a long one, and every banked clip keeps counting (a long reel
    finishes as soon as it is the cheapest to finish). Ties keep the
    package order."""
    order = {j["job_id"]: i for i, j in enumerate(cal.scheduled())}
    ready = []
    for j in cal.scheduled():
        row = state.get(j["job_id"]) or {}
        if row.get("status") not in ("draft", "rendering"):
            continue
        if any((state.get(d) or {}).get("status") != "published"
               for d in cal.dependencies(j)):
            continue
        ready.append(j)
    return sorted(ready, key=lambda j: (len(cal.missing(j)) + len(cal.unsmoothed(j)),
                                       order[j["job_id"]]))


def _calendar_seconds(j: dict) -> tuple[float, float]:
    """A calendar reel's length band: its package's own plan (RA-E5)."""
    from ..modules.content import quality
    return quality.calendar_band(j)


async def _calendar_state(cal) -> dict[str, dict]:
    rows = await cal.rows("stories") + await cal.rows("business")
    return {r["job_id"]: r for r in rows}


BACKUP_CLIPS = 3


async def no_platform_line() -> str | None:
    """None while at least one platform could take a reel; otherwise the
    line that says none can, with each leg's reason and the read time."""
    from ..modules.content import crosspost
    try:
        ready, legs = await crosspost.legs_ready()
    except Exception as exc:                            # noqa: BLE001
        # Unreadable is UNKNOWN, not "no platform": the reels go on, and the
        # card's own dry run still says where each one could go.
        log.warning("reels: platform readiness unreadable: %s", type(exc).__name__)
        return None
    if ready:
        return None
    return (f"No reel made today: no platform can take a post (read "
            f"{datetime.now(UTC):%H:%M} UTC) -- " + " · ".join(legs)
            + ". Fix an account and the daily reels resume.")


async def calendar_covers_today(now: datetime | None = None) -> tuple[bool, str]:
    """Whether a packaged reel is waiting for the owner right now (a card up, or a
    finished render about to get one) -- and, when not, why, in words."""
    from ..modules.content import calendar as cal
    try:
        state = await _calendar_state(cal)
    except Exception as exc:                            # noqa: BLE001
        from ..modules._http import explain
        return False, f"the packages are unreadable: {explain(exc)}"
    ready = [r for r in state.values()
             if r.get("status") == "carded"
             or (r.get("status") == "rendered" and not r.get("note"))]
    if ready:
        return True, f"{len(ready)} packaged reel(s) ready or on a card"
    making = [r for r in state.values() if r.get("status") == "rendering"]
    if making:
        first = min(making, key=lambda r: r.get("day") or 0)
        return False, (f"the next packaged reel ({first['job_id']}) is still "
                       f"being made" + (f": {first['note']}" if first.get("note")
                                        else ""))
    return False, "no packaged reel is ready"


async def calendar_lines() -> list[str]:
    """The digest's view of the month: the tick's own lines, then one
    count line per package, read now."""
    from ..modules.content import calendar as cal
    lines = await calendar_tick()
    read = datetime.now(UTC)
    lines.append("Pace: Veo's free tier makes 10 clips a day -- about one "
                 "finished reel a day, a series part and a regular reel on "
                 "alternating days; nothing is padded, and a day without clips "
                 "posts nothing.")
    for name in cal.PACKAGES:
        counts: dict[str, int] = {}
        for r in await cal.rows(name):
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        said = ", ".join(f"{n} {k}" for k, n in sorted(counts.items())) or "not loaded"
        lines.append(f"Calendar ({name}, read {read:%H:%M} UTC): {said}")
        for r in await cal.rows(name):
            if r["status"] in ("blocked", "declined") or (
                    r["status"] == "rendered" and r.get("note")):
                lines.append(f"  day {r['day']} {r['status']}: "
                             f"{r.get('note') or 'declined by the owner; its sequels wait'}")
    return lines


async def reel_lines() -> list[str]:
    """Draft, render and propose today's reels. One line per reel, and a
    reel that stops at any step says where and why -- no card for it."""
    from ..modules.content import calendar as cal
    from ..modules.content import reels as playbook
    backend = "auto"
    backup = ""
    if await cal.active():
        covered, why = await calendar_covers_today()
        if covered:
            return [("Reels rotation paused: today's post is the calendar's "
                     "(REELS_CALENDAR=off resumes the rotation)")]
        # the owner, 2026-09-27: a post every day, and never a slideshow. The
        # calendar has nothing ready for today, so ONE backup reel is drafted
        # on Veo -- but only from clips left over today, so the calendar's
        # own days never fall behind for it.
        left = cal.daily_clips() - await cal.clips_today()
        need = BACKUP_CLIPS
        if left < need:
            return [(f"No backup reel today: the calendar has none ready ({why}) "
                     f"and only {max(0, left)} of today's {cal.daily_clips()} Veo "
                     f"clips are left (a backup needs {need}). A higher Veo tier "
                     f"would cover days like this.")]
        backup = f"backup reel, the calendar has none today ({why})"
    # the owner, 2026-09-27: a post every day -- unless no platform can take one.
    # Then nothing is drafted or rendered, and the line says which accounts
    # need fixing, rather than raising cards nothing could post.
    if (stopped := await no_platform_line()) is not None:
        return [stopped]
    try:
        await playbook.ensure_seeds()
        done = await playbook.drafted_today()
    except Exception as exc:                            # noqa: BLE001
        from ..modules._http import explain
        return [f"Reels: not drafted — the playbook is unreadable: {explain(exc)}"]
    limit = 1 if backup else playbook.per_day()
    if backup:
        # A short one: at most BACKUP_CLIPS beats, so it fits what is left.
        formats_cap = BACKUP_CLIPS
    else:
        formats_cap = 0
    wanted = max(0, limit - done)
    if wanted == 0:
        return [f"Reels: {done} already drafted today (REELS_PER_DAY={limit})"]
    formats = await playbook.pick_today(wanted if not backup else 4)
    if backup:
        # Short formats only: the backup must fit the clips left over.
        formats = [f for f in formats
                   if int(f.get("max_scenes") or 4) <= formats_cap][:wanted]
    lines: list[str] = []
    cards = 0
    for fmt in formats:
        name = fmt["format"]
        drafted = await execute(ExecutionRequest(
            action="content.draft_reel", params={"format_id": fmt["id"]},
            reason=f"today's reel in the {name!r} format"))
        if not drafted.ok:
            lines.append(f"{name}: not drafted — {drafted.detail}")
            continue
        ev = drafted.evidence or {}
        draft_id, caption = ev.get("id"), ev.get("caption")
        params = ({"draft_id": draft_id, "backend": backend}
                  if backend != "auto" else {"draft_id": draft_id})
        rendered = await execute(ExecutionRequest(
            action="content.render_video", params=params,
            reason=f"today's reel, draft {draft_id}"
                   + (f" ({backup})" if backup else "")))
        if not rendered.ok:
            lines.append(f"{name}: draft {draft_id} written, not rendered — "
                         f"{rendered.detail}; no card")
            continue
        media_id = (rendered.evidence or {}).get("media_id")
        params = {"media_id": media_id, "caption": caption}
        outcome, pid, said = await post_or_card(
            params, _card_reason(fmt, ev, rendered.detail,
                                 PLATFORMS),
            dedup=f"reel:{draft_id}", media_id=media_id,
            rollback_plan=CALENDAR_ROLLBACK, on_fail="skip",
            series=playbook.bucket_of(name) == "serial")
        if outcome in ("wait", "skip"):
            lines.append(f"{name}: media {media_id} not posted today "
                         f"({'waits for tomorrow' if outcome == 'wait' else 'dropped'})"
                         f" — {said}")
            continue
        if outcome == "none":
            lines.append(f"{name}: media {media_id} rendered, not posted — {said}")
            continue
        cards += 1
        if outcome == "posted":
            lines.append(f"{name}: posted (draft {draft_id}, media {media_id}) — "
                         f"{said}")
        else:
            lines.append(f"{name}: card #{pid} (draft {draft_id}, media "
                         f"{media_id}; {said})")
    lines.insert(0, f"Reels: {cards} posted or carded of {wanted} wanted today "
                    + (f"({backup})" if backup else
                       f"(REELS_PER_DAY={limit}, {done} drafted earlier)"))
    return lines


COURSE_ROLLBACK = CALENDAR_ROLLBACK


def _course_reason(ev: dict, rendered: str) -> str:
    beats = "\n".join(f"{i}. {b}" for i, b in enumerate(ev.get("scenes") or [], 1))
    return "\n".join([
        *_card_status(rendered),
        f"Course-promo reel (Build a Safe AI Operator) -- {ev.get('format')}",
        beats,
        f"Caption: {ev.get('caption')}",
        f"Render: {rendered}",
        f"Per platform: {PLATFORMS}",
        "The AI-stories accounts only; never the clips accounts.",
    ])


_COURSE_LOCK = asyncio.Lock()


async def _post_course(format_id: int, draft_id: int, media_id: int,
                       ev: dict, rendered: str) -> str:
    """One course reel through the AI-stories path (`post_or_card`: the
    stories Instagram, the Atlas Stories Page and TikTok @atlasstories7),
    under the day's automatic-post cap like any other reel. One at a time,
    and only while its draft is still waiting: the daily step and the
    calendar tick's retry never both post it."""
    from ..modules.content import course
    async with _COURSE_LOCK:
        row = await fetch_one("SELECT status FROM content_drafts WHERE id = $1",
                              draft_id)
        if row is None or row["status"] not in ("draft", "rendered"):
            return (f"Course: draft {draft_id} is already "
                    f"{row['status'] if row else 'gone'}; nothing posted")
        outcome, pid, said = await post_or_card(
            {"media_id": media_id, "caption": ev.get("caption")},
            _course_reason(ev, rendered), dedup=f"reel:{draft_id}",
            media_id=media_id, rollback_plan=COURSE_ROLLBACK, series=False,
            on_fail="skip", seconds=course.SECONDS)
        after = {"posted": "published", "carded": "carded",
                 "skip": "dropped"}.get(outcome)
        if after:
            await execute_sql("UPDATE content_drafts SET status = $2 WHERE id = $1",
                              draft_id, after)
    if outcome == "posted":
        return f"Course: posted (draft {draft_id}, media {media_id}) — {said}"
    if outcome == "carded":
        return f"Course: card #{pid} (draft {draft_id}, media {media_id}; {said})"
    if outcome == "wait":
        return (f"Course: media {media_id} ready, waiting for the next eligible "
                f"day — {said}")
    return f"Course: media {media_id} not posted ({outcome}) — {said}"


async def _post_held_course(format_id: int, held: dict) -> str:
    from ..modules.content import course, reel_script
    script = reel_script.parse(held["body"], max_scenes=course.MAX_BEATS)
    ev = {"format": script.format, "caption": script.caption,
          "scenes": course.screen_lines(script)}
    return await _post_course(format_id, int(held["draft_id"]),
                              int(held["media_id"]), ev,
                              f"Media {held['media_id']}, rendered earlier.")


async def course_waiting_lines() -> list[str]:
    """From the calendar tick, every `CALENDAR_TICK_MIN`: a course reel that
    was rendered but held by a count (an hourly allowance is a rolling hour,
    and the daily step posts the stories just before it) is posted as soon as
    the count allows. Posts only; never drafts or renders."""
    from ..modules.content import course, crosspost
    if course.per_week() == 0 or not crosspost.autopublish_enabled():
        return []
    format_id = await course.ensure_format()
    held = await course.waiting(format_id)
    if held is None:
        return []
    return [await _post_held_course(format_id, held)]


async def course_lines(now: datetime | None = None) -> list[str]:
    """The course-promo lane (the owner, 2026-09-29): `COURSE_REELS_PER_WEEK`
    (default 2, Tuesday and Friday New York) reels for his course, on the
    AI-stories accounts. Runs before the football step, so a course day's
    reel takes the day's third automatic post; when the cap is already
    spent, or no Veo clip is left, the slot moves to the next day of the
    same week. At most one attempt a day."""
    from ..modules.content import calendar as cal
    from ..modules.content import course, crosspost
    wanted = course.per_week()
    if wanted == 0:
        return ["Course: off (COURSE_REELS_PER_WEEK=0)"]
    today = course.local_today(now)
    read = datetime.now(UTC)
    try:
        format_id = await course.ensure_format()
        done = await course.done_this_week(format_id, today)
        tried = await course.attempted_today(format_id, today)
    except Exception as exc:                            # noqa: BLE001
        from ..modules._http import explain
        return [f"Course: not run — unreadable: {explain(exc)}"]
    owed = course.entitled(today, wanted)
    if not course.due(today, wanted, done):
        return [(f"Course: none due today ({done} of {owed} this week so far, "
                 f"COURSE_REELS_PER_WEEK={wanted}; read {read:%H:%M} UTC)")]
    if (stopped := await no_platform_line()) is not None:
        return [f"Course: {stopped}"]
    if crosspost.autopublish_enabled():
        try:
            posted = await crosspost.posted_today()
        except Exception:                               # noqa: BLE001
            return ["Course: not made — today's posts could not be counted"]
        if posted >= crosspost.autopublish_per_day():
            return [(f"Course: due, skipped today — {posted} of "
                     f"{crosspost.autopublish_per_day()} automatic posts already "
                     f"made (REELS_AUTOPUBLISH_PER_DAY; read {read:%H:%M} UTC); "
                     f"the next eligible day this week takes it")]
    held = await course.waiting(format_id)
    if held is not None:
        # Rendered earlier and held by a count: it goes first, no new clips.
        return [await _post_held_course(format_id, held)]
    if tried:
        return [f"Course: already tried today ({done} of {owed} this week)"]
    try:
        left = cal.daily_clips() - await cal.clips_today()
    except Exception as exc:                            # noqa: BLE001
        from ..modules._http import explain
        return [f"Course: not made — Veo clips unreadable: {explain(exc)}"]
    if left < course.MIN_BEATS:
        return [(f"Course: due, no reel today — {max(0, left)} of today's "
                 f"{cal.daily_clips()} Veo clips left (read {read:%H:%M} UTC); "
                 f"the next eligible day this week takes it")]
    drafted = await execute(ExecutionRequest(
        action="content.draft_course_reel",
        params={"beats": min(course.MAX_BEATS, left)},
        reason="this week's course-promo reel"))
    if not drafted.ok:
        return [f"Course: not drafted — {drafted.detail}"]
    ev = drafted.evidence or {}
    draft_id = int(ev["id"])
    rendered = await execute(ExecutionRequest(
        action="content.render_video", params={"draft_id": draft_id},
        reason=f"this week's course-promo reel, draft {draft_id}"))
    if not rendered.ok:
        return [f"Course: draft {draft_id} written, not rendered — {rendered.detail}"]
    media_id = int((rendered.evidence or {})["media_id"])
    return [await _post_course(format_id, draft_id, media_id, ev, rendered.detail)]


async def shorts_lines(now: datetime | None = None) -> list[str]:
    """YouTube Shorts (the owner, 2026-09-29): `SHORTS_PER_WEEK` (default 3,
    Monday/Wednesday/Friday New York) a week, each the best AI reel of the
    last seven days not on YouTube yet (`content.shorts`), posted through
    the executor as `content.publish_short`. A slot with nothing eligible
    moves to the next day of the same week. Configuration is read first, so
    an unset lane touches nothing."""
    from ..modules.content import course, postiz, shorts
    n = shorts.per_week()
    if n == 0:
        return [f"Shorts: off ({shorts.PER_WEEK_ENV}=0)"]
    if (gap := postiz.youtube_gap()) is not None:
        return [f"Shorts: not configured — {gap}"]
    today = course.local_today(now)
    try:
        done = await shorts.done_this_week(today)
    except Exception as exc:                            # noqa: BLE001
        from ..modules._http import explain
        return [f"Shorts: not run — unreadable: {explain(exc)}"]
    read = datetime.now(UTC)
    if not course.due(today, n, done):
        return [(f"Shorts: none due today ({done} of {course.entitled(today, n)} "
                 f"this week so far, {shorts.PER_WEEK_ENV}={n}; read "
                 f"{read:%H:%M} UTC)")]
    try:
        short, note, skipped = await shorts.pick()
    except Exception as exc:                            # noqa: BLE001
        from ..modules._http import explain
        return [f"Shorts: not run — the week's reels unreadable: {explain(exc)}"]
    if short is None:
        why = "; ".join(skipped[:4]) or (f"no AI reel posted in the last "
                                         f"{shorts.WINDOW_DAYS} days is off YouTube")
        return [(f"Shorts: due, none posted — {why} (read {read:%H:%M} UTC); "
                 f"the next day this week tries again")]
    result = await execute(ExecutionRequest(
        action=shorts.ACTION, params={"media_id": short.media_id},
        reason=f"this week's YouTube Short: media {short.media_id} ({note})"))
    word = "posted" if result.ok else "not posted"
    return [f"Shorts: {word} — {result.detail} ({note})"]


FOOTBALL_ROLLBACK = CALENDAR_ROLLBACK


async def football_lines() -> list[str]:
    """The football (soccer) lane (the owner, 2026-09-29): at most
    `FOOTBALL_REELS_PER_DAY` stat reels a day, after the day's stories and
    only from Veo clips left over, drafted from API-Football's numbers,
    rendered with every number verified, and posted on the AI reels path
    (the same autopublish checks). Idle, and says so, without its key."""
    from ..modules import football as fb
    from ..modules.content import calendar as cal
    from ..modules.content import football as lane
    wanted = lane.per_day()
    if wanted == 0:
        return ["Football: off (FOOTBALL_REELS_PER_DAY=0)"]
    if not fb.api.configured():
        return [f"Football: idle — {fb.api.KEY_ENV} not set"]
    if (stopped := await no_platform_line()) is not None:
        return [f"Football: {stopped}"]
    try:
        done = await lane.drafted_today()
        left = cal.daily_clips() - await cal.clips_today()
    except Exception as exc:                            # noqa: BLE001
        from ..modules._http import explain
        return [f"Football: not drafted — unreadable: {explain(exc)}"]
    if done >= wanted:
        return [(f"Football: {done} already drafted today "
                 f"(FOOTBALL_REELS_PER_DAY={wanted})")]
    from ..modules.content import crosspost
    if crosspost.autopublish_enabled():
        # Football shares the day's automatic-post cap with the stories. A
        # reel rendered after the cap is used would wait, and nothing makes it
        # post later: its API requests and Veo clips would be wasted.
        try:
            posted = await crosspost.posted_today()
        except Exception:                               # noqa: BLE001
            posted = None
        if posted is None:
            return ["Football: not drafted — today's posts could not be counted"]
        if posted >= crosspost.autopublish_per_day():
            return [(f"Football: no reel today — {posted} of "
                     f"{crosspost.autopublish_per_day()} automatic posts already "
                     f"made (REELS_AUTOPUBLISH_PER_DAY; read "
                     f"{datetime.now(UTC):%H:%M} UTC)")]
    if left < lane.MIN_BEATS:
        return [(f"Football: no reel today — {max(0, left)} of today's "
                 f"{cal.daily_clips()} Veo clips left, a reel needs "
                 f"{lane.MIN_BEATS} (read {datetime.now(UTC):%H:%M} UTC)")]
    drafted = await execute(ExecutionRequest(
        action="content.draft_football_reel",
        params={"max_beats": min(lane.MAX_BEATS, left)},
        reason="today's football (soccer) stats reel"))
    if not drafted.ok:
        return [f"Football: not drafted — {drafted.detail}"]
    ev = drafted.evidence or {}
    draft_id, caption = ev.get("id"), ev.get("caption")
    rendered = await execute(ExecutionRequest(
        action="content.render_video", params={"draft_id": draft_id},
        reason=f"today's football reel, draft {draft_id}"))
    if not rendered.ok:
        return [(f"Football: draft {draft_id} written, not rendered — "
                 f"{rendered.detail}")]
    media_id = (rendered.evidence or {}).get("media_id")
    fmt = {"format": ev.get("format") or "football", "weight": 0,
           "source": "API-Football"}
    outcome, pid, said = await post_or_card(
        {"media_id": media_id, "caption": caption},
        _card_reason(fmt, ev, rendered.detail, PLATFORMS),
        dedup=f"reel:{draft_id}", media_id=media_id,
        rollback_plan=FOOTBALL_ROLLBACK, series=False, on_fail="skip")
    if outcome == "posted":
        return [f"Football: posted (draft {draft_id}, media {media_id}) — {said}"]
    if outcome == "carded":
        return [f"Football: card #{pid} (draft {draft_id}, media {media_id}; {said})"]
    return [(f"Football: media {media_id} not posted "
             f"({'waits for tomorrow' if outcome == 'wait' else outcome}) — {said}")]


PROMOTE_KIND = "reel_promote"


async def promote_lines() -> list[str]:
    """The daily promote-candidate cards (`content.reach`): ONE Slack message
    per outperforming stories reel, ever -- claimed in `reports` (kind
    `reel_promote:<platform>:<post ref>`) before it is sent, so a restart or
    a second run never repeats it. Information only: no buttons, no
    proposal, no action; ATLAS has no promote or ads action and moves no
    money. Numbers unreadable -> no card, and the line says so."""
    from ..modules.content import reach
    try:
        found, unknown, at = await reach.read_candidates()
    except Exception as exc:                            # noqa: BLE001
        from ..modules._http import explain
        return [f"Promote: not judged — reel metrics unreadable ({explain(exc)})"]
    th = reach.Thresholds.from_env()
    sent: list[str] = []
    for c in found:
        kind = f"{PROMOTE_KIND}:{c.platform}:{c.post_ref}"
        try:
            prior = await fetch_one(
                "SELECT count(*) AS n FROM reports WHERE kind = $1", kind)
        except Exception as exc:                        # noqa: BLE001
            # Cannot tell whether it went: send nothing.
            log.warning("reels: promote card for %s not sent: %s", kind,
                        type(exc).__name__)
            continue
        if prior is None or int(prior["n"]) > 0:
            continue
        title, body = reach.card_text(
            c, await reach.reel_link(c.platform, c.post_ref), th)
        try:
            await execute_sql(
                """INSERT INTO reports (kind, title, body, delivered_via)
                   VALUES ($1, $2, $3, $4)""", kind, title, body, "notify")
        except Exception as exc:                        # noqa: BLE001
            # Unclaimed: never risk a second card.
            log.warning("reels: promote card for %s not claimed: %s", kind,
                        type(exc).__name__)
            continue
        await notify(Outbound(title=title, body=body, topic=TOPIC,
                              severity=Severity.INFO, kind="alert", buttons=()))
        sent.append(f"{reach.APPS[c.platform]} {c.post_ref}")
    line = (f"Promote (read {at:%H:%M} UTC): {len(found)} stories reel(s) "
            f"outperforming; {len(sent)} new card(s)")
    if unknown:
        line += "; not judged — " + "; ".join(unknown[:3])
    return [line]


async def nudge(now: datetime | None = None) -> bool:
    """The 17:00 nudge: once a day, and only when no reel card was approved
    today. Returns whether it was sent. It asks; it never posts."""
    now = now or datetime.now().astimezone()
    if watch_only():
        # No card can have been raised: every draft and render was refused.
        return False
    sent = await already_sent_today(now, NUDGE_KIND)
    if sent is None or sent:
        return False
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        approved = await fetch_one(
            """SELECT count(*) AS n FROM proposals
                WHERE action = 'content.publish_reel'
                  AND status IN ('approved', 'executed', 'failed')
                  AND decided_at >= $1""", midnight)
        waiting = await fetch_one(
            """SELECT count(*) AS n FROM proposals
                WHERE action = 'content.publish_reel' AND status = 'pending'
                  AND expires_at > now()""")
    except Exception as exc:                            # noqa: BLE001
        log.warning("reels: proposals unreadable, no nudge: %s", exc)
        return False
    if approved and int(approved["n"]) > 0:
        return False
    try:
        from ..modules.content import crosspost
        if await crosspost.posted_today() > 0:
            return False                # a reel went out on its own today
    except Exception as exc:                            # noqa: BLE001
        log.warning("reels: today's posts unreadable for the nudge: %s",
                    type(exc).__name__)
    n = int(waiting["n"]) if waiting else 0
    read = datetime.now(UTC)
    if n:
        title = (f"{n} reel{'s' if n != 1 else ''} waiting — approve one to "
                 f"keep today's post")
    else:
        title = "No reel is waiting and none was approved today"
    # the owner, 2026-09-27: at least every other day. Two days with nothing out
    # is said first, in the title -- still a message, never a post.
    try:
        posted = await fetch_one(
            """SELECT count(*) AS n FROM reel_posts
                WHERE at >= now() - interval '48 hours'
                  AND platform <> 'youtube'""")
    except Exception:                                   # noqa: BLE001
        posted = None
    if posted is not None and int(posted["n"]) == 0:
        title = f"Nothing has posted for 2 days. {title}"
    if (stopped := await no_platform_line()) is not None:
        title = "No platform can take a post — the accounts need fixing"
        body_extra = f"\n\n{stopped}"
    else:
        body_extra = ""
    body = (f"{title} (read {read:%H:%M} UTC). Nothing posts on its own: an "
            f"unanswered card expires, it never publishes itself.{body_extra}")
    await notify(Outbound(title=title, body=body, topic=TOPIC,
                          severity=Severity.NORMAL, kind="alert"))
    try:
        await execute_sql(
            """INSERT INTO reports (kind, title, body, delivered_via)
               VALUES ($1, $2, $3, $4)""", NUDGE_KIND, title, body, "notify")
    except Exception as exc:                            # noqa: BLE001
        log.warning("reels: the nudge went out but was not stored: %s", exc)
    return True


async def daily(now: datetime | None = None) -> None:
    """The whole day: once, and never twice.

    Refuses outside a booted process: run by hand with an empty registry
    (2026-09-25, a first-run check) it filed a digest saying "no such action"
    and queued it for the brief -- a day's record written from nothing. The
    catalog must be loaded, or nothing is written."""
    from .registry import REGISTRY
    if not REGISTRY.actions:
        log.warning("reels.daily: the action catalog is empty (not a booted "
                    "process); writing nothing")
        return
    sent = await already_sent_today(now)
    if sent is None or sent:
        return
    lines = await overnight_lines()
    # The course step before football: on a course day it takes the day's
    # third automatic post (the owner's 2+1 split gives way twice a week).
    for part in (tiktok_lines, research_lines, calendar_lines, reel_lines,
                 course_lines, football_lines, shorts_lines, promote_lines):
        try:
            lines += await part()
        except Exception as exc:
            from ..modules._http import explain
            log.exception("reels: %s failed", part.__name__)
            lines.append(f"{part.__name__.replace('_lines', '')}: failed — "
                         f"{explain(exc)}")
    lines.append(f"(digest composed {datetime.now(UTC):%H:%M} UTC)")
    await send_digest(lines)
