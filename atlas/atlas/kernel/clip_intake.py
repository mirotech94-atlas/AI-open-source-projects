"""The clips lane, unattended: official footage in, cut clips out.

Every `CLIPS_INTAKE_MIN` minutes (default 30), from the scheduler, off the
loop. For each clip campaign that may be clipped -- active, permission
named, content rule passed, format known, rules reviewed
(`clips.intake.gate`):

  1. **Register** -- when its `source/` folder holds a file whose sha256 is
     not on file for that campaign in any state, `clips.register_sources`
     runs. Never otherwise: a Tier 1 run spends the clips module's hourly
     allowance, which Miro's card approvals share.
  2. **Cut** -- the oldest registered source never cut, while the
     campaign's daily cap (`CLIPS_DAILY_CAP`, clips since midnight UTC) has
     room: `clips.cut` with `max_clips` set to what is left. At most ONE
     cut per tick across all campaigns: a cut is minutes of transcription
     and ffmpeg.

  3. **Post** (Miro, 2026-09-30: clips post themselves, the reels lane's
     way) -- the oldest waiting clip per platform whose account ATLAS can
     reach (`posting.ready_to_post`): `clips.publish_clip`,
     `clips.publish_clip_tiktok` or `clips.publish_clip_youtube_postiz`,
     each Tier 1 and gated in code (`posting.py`, "automatic": the
     campaign, the `#Ad` caption, a 1080x1920 probe with sound inside the
     campaign's band, the account, then the holds `CLIPS_AUTOPUBLISH` and
     `CLIPS_DAILY_CAP` per platform per day). One post per platform per
     tick, so the actions' own hourly allowance is never spent at once. A
     clip the executor's dry run refuses -- a hold or a failed check -- is
     never skipped in silence: it becomes the ordinary post card
     (`posting.post_card`) with the reason first, through
     `approvals.propose`; approving it answers a hold and runs every check
     again. With `CLIPS_AUTOPUBLISH` off every clip is that card, and
     nothing is executed. A clip whose card is pending waits for the
     answer; one whose card was declined is left alone; one whose card
     expired is not offered again for `approvals.QUIET_AFTER_EXPIRY`
     (silence is not a yes). Every post is the executor's audit line and
     one receipt in the clips channel: the platform, the clip id and the
     caption's first line.

All of it goes through `executor.execute` -- the one path: its tier check
(every action here is Tier 1; nothing here can reach a Tier 2 action,
`ALLOWED` is the whole list), dry run, rate limits, pause and audit.

Under ATLAS_WATCH_ONLY nothing is executed at all -- the executor would
refuse it, and an audit line every half hour says nothing new -- and the
campaign's intake row says what is waiting, so `clips.status` reads
UNKNOWN rather than quiet. Every outcome is recorded per campaign
(`clips.intake.record_run`); a folder that cannot be read, a registration
or a cut that failed, is UNKNOWN there with its reason, never OK.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from .approvals import QUIET_AFTER_EXPIRY, propose, topic_for_action
from .comms import Outbound, attach_to_card, notify
from .contracts import Severity, Signal, Tier
from .executor import ExecutionRequest, execute, watch_only
from .registry import REGISTRY
from .state import fetch_one

log = logging.getLogger("atlas.clip_intake")

# Every action this job may run. Tier 1 only; checked again below.
ALLOWED = ("clips.register_sources", "clips.cut", "clips.publish_clip",
           "clips.publish_clip_tiktok", "clips.publish_clip_youtube_postiz")
REGISTER, CUT = ALLOWED[:2]
POSTERS = ALLOWED[2:]
TOPIC = "clips"


def _ready() -> str | None:
    """Why the job cannot run at all, or None."""
    for name in ALLOWED:
        decision = REGISTRY.decisions.get(name)
        if name not in REGISTRY.actions or decision is None or not decision.allowed:
            return f"{name} is not in the catalog (clips module not loaded)"
        if decision.tier is not Tier.AUTO:
            return f"{name} is not Tier 1; the intake runs nothing that needs Miro"
    return None


async def _run(action: str, params: dict, reason: str):
    if action not in ALLOWED:
        raise ValueError(f"the clips intake may not run {action}")
    return await execute(ExecutionRequest(action=action, params=params,
                                          reason=reason))


async def tick() -> list[str]:
    """One pass over every campaign. Returns a line per campaign worked."""
    from ..modules._http import explain
    from ..modules.clips import campaigns, cut, intake, sources
    if (why := _ready()) is not None:
        log.info("clips intake not run: %s", why)
        return [why]
    try:
        found = await campaigns.load_all()
    except Exception as exc:                            # noqa: BLE001
        log.warning("clips intake: campaigns not read: %s", explain(exc))
        return [f"campaigns not read: {explain(exc)}"]
    cap = intake.daily_cap()
    cuts_left = 1
    said: list[str] = []
    posted = await _post_ready(found)
    for campaign in found:
        slug = campaign.slug
        if intake.gate(campaign) is not None:
            if slug in posted:
                ok, lines = posted.pop(slug)
                await _record(intake, slug, ok, "; ".join(lines))
                said.append(f"{slug}: {'; '.join(lines)}")
            continue
        try:
            fresh = await intake.new_files(slug)
            made = await intake.made_today(slug)
            waiting = await intake.next_source(slug)
        except OSError as exc:
            await _record(intake, slug, False,
                          f"the footage folder {sources.source_dir(slug)} "
                          f"could not be read: {exc.strerror or exc}")
            continue
        except Exception as exc:                        # noqa: BLE001
            await _record(intake, slug, False,
                          f"the intake could not read its state: {explain(exc)}")
            continue
        if watch_only():
            parts = []
            if fresh:
                parts.append(f"{len(fresh)} new file(s) to register")
            if waiting is not None:
                parts.append(f"source {waiting['id']} waiting to be cut")
            parts += posted.pop(slug, (True, []))[1]
            await _record(intake, slug, False,
                          "ATLAS_WATCH_ONLY is on, so nothing was registered, "
                          "cut or posted" + (f" ({', '.join(parts)})" if parts
                                             else ""))
            continue
        ok, lines = posted.pop(slug, (True, []))
        if fresh:
            result = await _run(REGISTER, {"campaign": slug, "only_new": True},
                                f"new official footage for {campaign.name}: "
                                f"{', '.join(fresh)}")
            ok &= result.ok
            lines.append(("registered: " if result.ok else "not registered: ")
                         + result.detail)
            if result.ok:
                waiting = await intake.next_source(slug)
        room = cap - made
        if room <= 0:
            lines.append(f"daily cap reached ({made} of {cap} clips today, UTC)")
        elif waiting is not None and cuts_left > 0:
            cuts_left -= 1
            result = await _run(
                CUT, {"campaign": slug, "source_id": int(waiting["id"]),
                      "max_clips": min(cut.MAX_CLIPS, room)},
                f"source {waiting['id']} ({waiting['filename']}) of "
                f"{campaign.name} was never cut; {room} clip(s) left today")
            await intake.record_attempt(int(waiting["id"]), result.ok, result.detail)
            ok &= result.ok
            lines.append(("cut: " if result.ok else
                          f"source {waiting['id']} not cut: ") + result.detail)
            if not result.ok and cut.BRAND_MISSING in result.detail:
                # The campaign's rules want its logo or banner on every clip
                # and the file is not in its brand/ folder. ATLAS never
                # fetches one: Miro places it and answers the card, which
                # runs the cut again.
                lines.append(await _brand_card(campaign, waiting, room,
                                               _held(result.detail)))
        elif waiting is not None:
            lines.append(f"source {waiting['id']} waits for the next tick "
                         f"(one cut per tick)")
        detail = "; ".join(lines) or "nothing new"
        await _record(intake, slug, ok, detail)
        said.append(f"{slug}: {detail}")
    return said


# ------------------------------------------------------------------ posting

async def _post_ready(found: list[Any]) -> dict[str, tuple[bool, list[str]]]:
    """Post the oldest waiting clip per platform through the executor, or
    hold it as a card with the reason. Returns, per campaign slug, whether
    all of it went as designed and a line per clip considered. A clip
    held for a card is by design (ok); a post that failed, a card that
    could not be raised or a list that could not be read is not
    (`clips.status` reads UNKNOWN)."""
    from ..modules._http import explain
    from ..modules.clips import posting
    out: dict[str, tuple[bool, list[str]]] = {}

    def say(slug: str, ok: bool, line: str) -> None:
        was_ok, lines = out.get(slug, (True, []))
        out[slug] = (was_ok and ok, lines + [line])

    try:
        ready = await posting.ready_to_post(found)
    except Exception as exc:                            # noqa: BLE001
        log.warning("clips intake: waiting clips not read: %s", explain(exc))
        for campaign in found:
            say(campaign.slug, False, f"waiting clips not read: {explain(exc)}")
        return out
    tried: set[str] = set()
    for campaign, row, action in ready:
        slug, cid = campaign.slug, int(row["id"])
        platform = str(row.get("platform") or "instagram")
        if platform in tried:
            continue
        card = posting.post_card(campaign, row)
        if card.suggests != action or action not in POSTERS:
            say(slug, False, f"clip {cid}: its card names {card.suggests}, "
                             f"not {action}; not posted")
            continue
        try:
            state = await _card_state(f"{action}@{card.title}")
        except Exception as exc:                        # noqa: BLE001
            say(slug, False, f"clip {cid}: its card could not be looked up "
                             f"({explain(exc)}), so it was not posted")
            continue
        if state is not None:
            status, pid = state
            if status == "pending":
                say(slug, True, f"clip {cid} waits on card #{pid}")
            elif status == "declined":
                say(slug, True, f"clip {cid}: card #{pid} was declined; left alone")
            else:
                say(slug, True, f"clip {cid}: card #{pid} expired unanswered; "
                                f"not offered again yet")
            continue
        tried.add(platform)
        if watch_only():
            say(slug, True, f"clip {cid} ready for {platform}")
            continue
        if not posting.autopublish_enabled():
            hold = "automatic posting is off (CLIPS_AUTOPUBLISH)"
        else:
            result = await _run(action, {"clip_id": cid},
                                f"clip {cid} of {campaign.name} is cut and checked; "
                                f"it posts itself (Miro, 2026-09-30)")
            if result.ok:
                await _receipt(campaign, row, platform, result.detail)
                say(slug, True, f"clip {cid} posted on {platform}: {result.detail}")
                continue
            if _global(result.detail):
                say(slug, False, f"clip {cid} not posted yet: {result.detail}")
                continue
            hold = _held(result.detail)
            if not result.detail.startswith("dry run refused"):
                # The run itself failed: recorded as such, and still a card,
                # so Miro sees the clip and can try it by hand.
                say(slug, False, f"clip {cid} not posted: {result.detail}")
        try:
            pid = await _card(card, hold)
        except ValueError as exc:
            say(slug, True, f"clip {cid} not posted ({hold}); no card: {exc}")
            continue
        except Exception as exc:
            log.exception("clips intake: card for clip %s not raised", cid)
            say(slug, False, f"clip {cid} not posted ({hold}); its card could "
                             f"not be raised: {explain(exc)}")
            continue
        say(slug, True, f"clip {cid} held for you ({hold}): card #{pid}")
    return out


async def _brand_card(campaign: Any, waiting: dict[str, Any], room: int,
                      hold: str) -> str:
    """One Tier 2 `clips.cut` card per campaign whose brand asset is
    missing, with the reason first; approving it runs the cut once the file
    is there. Returns the line for the run record."""
    from ..modules.clips import cut
    card = Signal(
        title=f"Brand asset missing for {campaign.name}",
        body=(f"Source {waiting['id']} ({waiting['filename']}) of {campaign.name} "
              f"is not cut: {hold}. Its rules require the brand assets on every "
              f"clip, and ATLAS never fetches one from a brand's site. Put the "
              f"file there and approve: the cut runs with them composited "
              f"(logo bottom-right, banner as a top strip) on every clip."),
        severity=Severity.NORMAL, suggests=CUT,
        params={"campaign": campaign.slug, "source_id": int(waiting["id"]),
                "max_clips": min(cut.MAX_CLIPS, max(1, room))})
    try:
        pid = await _card(card, hold)
    except ValueError as exc:
        return f"no card: {exc}"
    except Exception as exc:
        from ..modules._http import explain
        log.exception("clips intake: brand card for %s not raised", campaign.slug)
        return f"its card could not be raised: {explain(exc)}"
    return f"card #{pid}"


def _global(detail: str) -> bool:
    """An executor refusal that is ATLAS's state (watch-only, a pause, an
    hourly limit), not this clip's: it waits for the next tick, no card."""
    from ..modules.clips import intake
    return intake.is_global(detail)


def _held(detail: str) -> str:
    """The reason in the executor's refusal, without its prefixes."""
    from ..modules.clips import posting
    for prefix in ("dry run refused: ", f"{posting.HELD}: "):
        detail = detail.removeprefix(prefix)
    return detail


async def _card_state(dedup_key: str) -> tuple[str, int] | None:
    """The latest card for this cause, as (status, id): 'pending' while it
    can still be answered, 'declined', or 'expired' while
    `QUIET_AFTER_EXPIRY` has not passed; None when there is none, or it is
    done with (executed, failed, or long expired)."""
    row = await fetch_one(
        """SELECT id, status, decided_at, expires_at FROM proposals
            WHERE dedup_key = $1 ORDER BY created_at DESC LIMIT 1""", dedup_key)
    if row is None:
        return None
    status, pid = str(row["status"]), int(row["id"])
    now = datetime.now(UTC)
    expires = row.get("expires_at")
    if status == "pending":
        return (status, pid) if expires is None or expires > now else None
    if status == "declined":
        return status, pid
    if status == "expired" and expires is not None and (
            expires > now - QUIET_AFTER_EXPIRY):
        return status, pid
    return None


async def _card(card: Signal, hold: str) -> int:
    """The ordinary post card, with the reason first. `propose` dedups on
    the cause while a card is pending and keeps quiet after an expiry or a
    decline (`automatic`)."""
    lead = (f"Held for you: {hold}. Approving runs every check again and posts "
            f"it if it passes now.\n")
    pid = await propose(
        card.suggests or "", card.params, lead + card.body,
        severity=Severity.NORMAL, evidence={"signal": card.title, "held": hold},
        dedup_key=f"{card.suggests}@{card.title}", automatic=True,
        rollback_plan="the action's rollback: the post is deleted (Instagram), "
                      "or the Postiz post while it is still queued")
    if card.attachment:
        import os
        try:
            outcome = await attach_to_card(
                pid, topic=topic_for_action(card.suggests or "", card.params),
                path=card.attachment, filename=os.path.basename(card.attachment),
                title=card.title[:150], note="approve or decline on the card above")
            if outcome != "uploaded":
                log.info("clip card #%s attachment not uploaded: %s", pid, outcome)
        except Exception:
            log.exception("clip card #%s: could not attach the clip", pid)
    return pid


async def _receipt(campaign: Any, row: dict[str, Any], platform: str,
                   detail: str) -> None:
    """One line in the clips channel per automatic post: the platform, the
    clip id and the caption's first line. Best effort: the post is out."""
    from ..modules.clips import posting
    first = str(row.get("caption") or "").split("\n")[0]
    try:
        await notify(Outbound(
            title=f"Posted clip {row['id']} on its own",
            body=(f"{posting.ACCOUNTS.get(platform, platform)} -- clip {row['id']} "
                  f"for {campaign.name}: {first}\n{detail}\n\nIt passed every "
                  f"automatic check. CLIPS_AUTOPUBLISH=off goes back to a card "
                  f"per clip; the action's rollback takes it down."),
            topic=TOPIC, severity=Severity.INFO, kind="alert"))
    except Exception:                                   # noqa: BLE001
        log.warning("clips intake: clip %s posted, but the receipt was not sent",
                    row.get("id"))


async def _record(intake, slug: str, ok: bool, detail: str) -> None:
    try:
        await intake.record_run(slug, ok, detail)
    except Exception as exc:                            # noqa: BLE001
        from ..modules._http import explain
        log.warning("clips intake: outcome for %s not recorded: %s", slug,
                    explain(exc))
