"""Whop: which posted clips wait to be claimed, the record that one was, and
the counts the lane can honestly report.

Miro, 2026-09-29, in writing: submitting a posted clip to its campaign on
Whop is done by an agent on his Mac, in his own Chrome -- never by ATLAS,
which has no verified way into his payout account and never tries one.
ATLAS gives that agent two things, both mesh-only and behind the
`HANDOFF_TOKEN` bearer (`atlas/api/clips.py`):

  * `pending()` -- every posted clip it may submit. A clip is listed only
    when its post URL came from Miro's own word (`clips.record_link`) or
    from the platform's own publish response (`post_url_source`, set by
    each writer in `posting.py`) -- never a URL a model wrote -- and only
    for a campaign with a usable `permission_source` AND a `whop_url` on
    file. A row whose URL source is not recorded is never listed.
  * `clips.record_whop_submission` (Tier 1, through the executor) -- the
    agent's word that it submitted one. A URL that is not the clip's own is
    refused; so is a clip this listing would not offer. A clip already
    recorded as submitted is never recorded again: the identical repeat by
    the agent returns ok with `already` and changes nothing (no rollback
    token, so undoing the repeat cannot undo the first); anything else --
    Miro's card having recorded it first included -- is refused as a second
    submission. Its rollback puts the clip back to posted in ATLAS's own
    records, and only a record the agent made.

The Tier 2 `clips.submit_to_whop` card stays (`card_due`): at once, as
before, for a posted clip the agent will never see (`offered` is false: no
whop_url, no valid permission source, or no recorded URL source); for a
clip the agent is offered, only as the fallback once it is still
unsubmitted `FALLBACK_AFTER` after it posted, so an agent that is off or
broken never loses a claim silently.

**Counts** (`clips_earnings`): ATLAS cannot read Whop, so it reports the
counts it does know -- clips posted, submitted and awaiting submission --
with the time they were read. There is no money figure in it at all: one
would appear only if read from Whop with its read time, and nothing here
reads Whop (invariant 6). UNKNOWN when the counts cannot be read.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from ...kernel.contracts import ActionResult, Health, Observation
from ...kernel.state import execute_sql, fetch_all, fetch_one
from . import campaigns

FALLBACK_AFTER = timedelta(hours=48)
SUBMITTED_BY = "mac-agent"
# Where a post URL may have come from for the agent to claim it: the
# platform's own publish response, or Miro's word through clips.record_link.
TRUSTED_SOURCES = ("instagram", "youtube", "tiktok", "record_link")


def _eligible(campaign: campaigns.Campaign | None) -> str | None:
    """Why this campaign's clips are not offered to the agent, or None."""
    if campaign is None:
        return "its campaign is not on file"
    if campaigns.permission_refusal(campaign) is not None:
        return f"{campaign.name} names no permission source"
    if not (campaign.whop_url or "").strip():
        return f"{campaign.name} has no whop_url on file"
    return None


async def pending() -> list[dict[str, Any]]:
    """Posted clips the agent may submit, oldest first."""
    found = {c.slug: c for c in await campaigns.load_all()}
    rows = await fetch_all(
        """SELECT id, campaign, platform, post_url, posted_at, post_url_source
             FROM clip_outputs
            WHERE status = 'posted' AND post_url IS NOT NULL
              AND post_url_source = ANY($1::text[])
            ORDER BY posted_at NULLS LAST, id""", list(TRUSTED_SOURCES))
    out = []
    for r in rows:
        campaign = found.get(str(r["campaign"]))
        if campaign is None or not offered(campaign, dict(r)):
            continue
        posted = r["posted_at"]
        out.append({
            "clip_id": int(r["id"]),
            "campaign": campaign.slug,
            "campaign_name": campaign.name,
            "whop_url": str(campaign.whop_url),
            "platform": str(r["platform"]),
            "post_url": str(r["post_url"]),
            "posted_at": posted.isoformat() if posted else None,
        })
    return out


async def _row(clip_id: int) -> dict[str, Any] | None:
    row = await fetch_one(
        """SELECT id, campaign, status, post_url, post_url_source, submitted_at,
                  submitted_by
             FROM clip_outputs WHERE id = $1""", int(clip_id))
    return dict(row) if row is not None else None


async def _check(clip_id: int, post_url: str
                 ) -> tuple[dict[str, Any] | None, bool, str | None]:
    """(row, identical repeat already on file, why refused)."""
    try:
        cid = int(clip_id)
    except (TypeError, ValueError):
        return None, False, f"{clip_id!r} is not a clip id"
    row = await _row(cid)
    if row is None:
        return None, False, f"no clip {cid}"
    url = str(post_url or "").strip()
    if not url or str(row["post_url"] or "") != url:
        return None, False, (f"{url or '(no URL)'} is not clip {cid}'s own post "
                             f"URL")
    if row["status"] == "submitted":
        if row.get("submitted_by") == SUBMITTED_BY:
            return row, True, None
        return None, False, (f"clip {cid} was already recorded as submitted "
                             f"({row.get('submitted_by') or 'by Miro'}, "
                             f"{row.get('submitted_at')}); a second submission "
                             f"is refused")
    if row["status"] != "posted":
        return None, False, f"clip {cid} is {row['status']}, not posted"
    if str(row.get("post_url_source") or "") not in TRUSTED_SOURCES:
        return None, False, (f"clip {cid}'s post URL did not come from the "
                             f"platform or from Miro; it is not offered for Whop")
    campaign = await campaigns.load(str(row["campaign"]))
    why = _eligible(campaign)
    if why is not None:
        return None, False, f"clip {cid} is not offered for Whop: {why}"
    return row, False, None


async def record_submission_dry(clip_id: int, post_url: str) -> ActionResult:
    row, already, why = await _check(clip_id, post_url)
    if row is None:
        return ActionResult(False, why or "refused")
    if already:
        return ActionResult(True, f"clip {row['id']} is already recorded as "
                                  f"submitted; nothing would change")
    return ActionResult(True, f"would record clip {row['id']} ({post_url}) as "
                              f"submitted to {row['campaign']} on Whop by the "
                              f"Mac agent")


async def record_submission(clip_id: int, post_url: str) -> ActionResult:
    """The Mac agent's word that it submitted this clip on Whop."""
    row, already, why = await _check(clip_id, post_url)
    if row is None:
        return ActionResult(False, why or "refused")
    cid = int(row["id"])
    if already:
        return ActionResult(True, f"clip {cid} was already recorded as submitted "
                                  f"({row['submitted_at']}); unchanged",
                            evidence={"already": True})
    done = await fetch_one(
        """UPDATE clip_outputs SET status = 'submitted', submitted_at = now(),
                  submitted_by = $2
            WHERE id = $1 AND status = 'posted' AND post_url = $3
        RETURNING id""", cid, SUBMITTED_BY, str(row["post_url"]))
    if done is None:
        # Raced by another record: read it back rather than assume.
        again = await _row(cid)
        if (again is not None and again["status"] == "submitted"
                and again.get("submitted_by") == SUBMITTED_BY
                and again.get("post_url") == row["post_url"]):
            return ActionResult(True, f"clip {cid} was already recorded as "
                                      f"submitted; unchanged",
                                evidence={"already": True})
        return ActionResult(False, f"clip {cid} changed while it was recorded; "
                                   f"not recorded")
    return ActionResult(True, f"clip {cid} ({post_url}) recorded as submitted to "
                              f"{row['campaign']} on Whop by the Mac agent",
                        evidence={"already": False},
                        rollback_token=json.dumps({"id": cid}))


async def record_submission_rollback(token: str) -> ActionResult:
    cid = int(json.loads(token)["id"])
    await execute_sql(
        """UPDATE clip_outputs SET status = 'posted', submitted_at = NULL,
                  submitted_by = NULL
            WHERE id = $1 AND status = 'submitted' AND submitted_by = $2""",
        cid, SUBMITTED_BY)
    return ActionResult(True, f"clip {cid} back to posted (ATLAS's record only; "
                              f"a submission on Whop is withdrawn there)")


def offered(campaign: campaigns.Campaign | None, row: dict[str, Any]) -> bool:
    """Whether `pending()` would list this posted clip to the agent: a URL
    from a trusted source, of a campaign with permission and a whop_url.
    Pure -- the same rule, so Miro's card waits only for what the agent
    will actually see."""
    return (bool(row.get("post_url"))
            and str(row.get("post_url_source") or "") in TRUSTED_SOURCES
            and _eligible(campaign) is None)


def card_due(campaign: campaigns.Campaign | None, row: dict[str, Any],
             now: datetime | None = None) -> bool:
    """Whether Miro's clips.submit_to_whop card is due for a posted clip:
    at once when the agent will never see it, else `FALLBACK_AFTER` after
    it posted."""
    if not offered(campaign, row):
        return True
    return fallback_due(row.get("posted_at"), now)


def fallback_due(posted_at: datetime | None, now: datetime | None = None) -> bool:
    """Whether a posted, unsubmitted clip is old enough for Miro's card. A
    clip with no posting time is due: nothing says the agent has it."""
    if posted_at is None:
        return True
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=UTC)
    return (now or datetime.now(UTC)) - posted_at >= FALLBACK_AFTER


async def check_earnings() -> Observation:
    """Clips posted, submitted and awaiting submission, as ATLAS's own
    records say, with the time they were read. Counts only: no money."""
    now = datetime.now(UTC)
    try:
        rows = await fetch_all(
            """SELECT campaign, status, count(*) AS n FROM clip_outputs
                WHERE status IN ('posted', 'submitted') GROUP BY campaign, status""")
    except Exception as exc:  # noqa: BLE001
        from .._http import explain
        return Observation("clips_earnings", Health.UNKNOWN,
                           detail=f"clip posts could not be read: {explain(exc)}")
    per: dict[str, dict[str, int]] = {}
    for r in rows:
        per.setdefault(str(r["campaign"]), {})[str(r["status"])] = int(r["n"])
    awaiting = sum(v.get("posted", 0) for v in per.values())
    submitted = sum(v.get("submitted", 0) for v in per.values())
    posted = awaiting + submitted
    parts = [f"{slug}: {v.get('posted', 0) + v.get('submitted', 0)} posted, "
             f"{v.get('submitted', 0)} submitted, {v.get('posted', 0)} awaiting"
             for slug, v in sorted(per.items())]
    detail = (f"{posted} clip(s) posted, {submitted} submitted to Whop, "
              f"{awaiting} awaiting submission"
              + (f" ({'; '.join(parts)})" if parts else "")
              + f", read {now:%d %b %H:%M} UTC. Counts only: ATLAS does not "
                f"read Whop, so it shows no paid figure")
    evidence = {"read_at": now.isoformat(), "posted": posted,
                "submitted": submitted, "awaiting": awaiting,
                "per_campaign": per}
    return Observation("clips_earnings", Health.OK, float(submitted),
                       "clips submitted", detail=detail, evidence=evidence)


async def check_posted() -> Observation:
    """Clips posted so far (submitted to Whop or still awaiting it), as a
    number of its own: `clips_earnings` carries the submitted count as its
    value, and a figure on the home page is a check's value, never a number
    lifted out of a sentence. Read fresh from `clip_outputs` each run."""
    now = datetime.now(UTC)
    try:
        row = await fetch_one(
            """SELECT count(*) AS n FROM clip_outputs
                WHERE status IN ('posted', 'submitted')""")
    except Exception as exc:  # noqa: BLE001
        from .._http import explain
        return Observation("clips_posted", Health.UNKNOWN,
                           detail=f"clip posts could not be read: {explain(exc)}")
    if row is None:
        return Observation("clips_posted", Health.UNKNOWN,
                           detail="clip posts could not be read: no answer")
    posted = int(row["n"])
    return Observation("clips_posted", Health.OK, float(posted), "clips posted, all time",
                       detail=f"{posted} clip(s) posted, all time, read {now:%d %b %H:%M} UTC",
                       evidence={"read_at": now.isoformat(), "posted": posted})


# ---------------------------------------------------------------- drafts
#
# Irubyana's campaign on Whop (verified 2026-10-01): "submit a draft for
# review; drafts need brand approval before you post; once approved, post
# and submit the live link". A clip of a campaign with
# `approval_before_post` is cut as `draft_pending`; the agent on Miro's Mac
# reads `drafts()` -- the file behind a signed, temporary `/media/clip.<token>`
# link (the same link a post would use), its size against the campaign's
# `max_file_mb` (Whop's form refuses more) -- uploads it in Miro's own Chrome
# to the connected account (TikTok @atlasclips12) and records that through
# `clips.record_draft_submitted` (`draft_submitted`); the brand's answer comes
# back through `clips.record_draft_decision`: approved -> `draft_approved`,
# which the automatic legs post like any `made` clip; rejected -> `withdrawn`
# with the brand's note, said in the clips channel. Both are Tier 1 statuses
# on ATLAS's own row, mesh-only behind the HANDOFF_TOKEN bearer, never
# recorded twice. Submitting the live link afterwards is `pending()` as
# before.

DRAFT_LINK_TTL = timedelta(days=7)
DRAFT_LINK_SLACK = timedelta(hours=1)
MB = 1024 * 1024
TOKEN_PREFIX = "clip."
# The accounts Miro connected on Whop (read there 2026-10-01): a draft is
# submitted from one of these. YouTube is not among them, so a YouTube leg
# of a campaign that wants drafts is never cut (`cut._plan` says why) and
# an older one holds (`posting.approval_refusal`); nothing lists it here.
CONNECTED_ACCOUNTS = ("tiktok", "instagram")
_PLATFORM_NAMES = {"tiktok": "TikTok", "instagram": "Instagram", "youtube": "YouTube"}


def not_connected(platform: str) -> str | None:
    """Why no draft of this platform's leg can be submitted on Whop, or
    None when its account is connected there. Pure."""
    if platform in CONNECTED_ACCOUNTS:
        return None
    return (f"{_PLATFORM_NAMES.get(platform, platform)} isn't connected on Whop, "
            f"so no draft of it can be submitted")


def draft_wanted(campaign: campaigns.Campaign | None, row: dict[str, Any]) -> bool:
    """Does the agent upload this clip as a draft: a campaign that asks for
    approval before posting, and a row cut (made, before the rule) or
    waiting as draft_pending. Pure."""
    return bool(campaign is not None and campaign.approval_before_post
                and str(row.get("status") or "") in ("made", "draft_pending"))


async def _draft_link(row: dict[str, Any]) -> tuple[str | None, datetime | None]:
    """The clip's signed download link and when it lapses: the token on
    its row while that is live for another DRAFT_LINK_SLACK, else a fresh
    one minted on ATLAS's own row for DRAFT_LINK_TTL."""
    import secrets

    from ..content import _media_url
    token = str(row.get("media_token") or "")
    expires = row.get("media_token_expires_at")
    now = datetime.now(UTC)
    if not token or expires is None or expires < now + DRAFT_LINK_SLACK:
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        expires = now + DRAFT_LINK_TTL
        await execute_sql(
            """UPDATE clip_outputs SET media_token = $2, media_token_expires_at = $3
                WHERE id = $1""", int(row["id"]), token, expires)
    return _media_url(token), expires


async def approved() -> list[dict[str, Any]]:
    """Drafts the brand approved that ATLAS has not posted yet, oldest
    first: what the Mac agent may ask to post now (`/post_now`) so the live
    link reaches Whop inside its window. Length and caption are what the
    agent matches against the approved card on Whop."""
    rows = await fetch_all(
        """SELECT id, campaign, platform, caption, start_s, end_s, draft_decided_at
             FROM clip_outputs
            WHERE status = 'draft_approved'
            ORDER BY draft_decided_at NULLS LAST, id""")
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            seconds = round(float(r["end_s"]) - float(r["start_s"]))
        except (TypeError, ValueError):
            seconds = None
        out.append({"clip_id": int(r["id"]), "campaign": str(r["campaign"]),
                    "platform": str(r["platform"] or ""), "seconds": seconds,
                    "caption": str(r["caption"] or "")[:200],
                    "approved_at": (r["draft_decided_at"].isoformat()
                                    if r.get("draft_decided_at") else None)})
    return out


async def drafts() -> list[dict[str, Any]]:
    """The clips the agent uploads as drafts on Whop, oldest first. Each
    carries a signed, temporary download link (`media_url`, lapsing at
    `link_expires_at`) and its size; a file over the campaign's
    `max_file_mb`, or one no longer on disk, is listed with `why` and no
    link -- never handed to Whop."""
    import asyncio
    import os

    from . import sources
    found = {c.slug: c for c in await campaigns.load_all()}
    rows = await fetch_all(
        """SELECT id, campaign, platform, filename, status, media_token,
                  media_token_expires_at
             FROM clip_outputs
            WHERE status IN ('made', 'draft_pending')
            ORDER BY id""")
    out = []
    for r in map(dict, rows):
        campaign = found.get(str(r["campaign"]))
        if (campaign is None or not draft_wanted(campaign, r)
                or _eligible(campaign) is not None
                or not_connected(str(r["platform"])) is not None):
            continue
        entry: dict[str, Any] = {
            "clip_id": int(r["id"]), "campaign": campaign.slug,
            "campaign_name": campaign.name, "whop_url": str(campaign.whop_url),
            "platform": str(r["platform"]), "filename": str(r["filename"]),
            "max_mb": int(campaign.max_file_mb), "bytes": None,
            "media_url": None, "link_expires_at": None, "why": None,
        }
        folder = sources.out_dir(campaign.slug)
        path = await asyncio.to_thread(sources.confined, folder / str(r["filename"]),
                                       folder)
        if path is None:
            entry["why"] = f"its file is no longer in {folder}"
            out.append(entry)
            continue
        size = await asyncio.to_thread(os.path.getsize, path)
        entry["bytes"] = int(size)
        if size > int(campaign.max_file_mb) * MB:
            entry["why"] = (f"{size / MB:.0f} MB is over {campaign.name}'s "
                            f"{campaign.max_file_mb} MB draft limit; cut again")
            out.append(entry)
            continue
        url, expires = await _draft_link(r)
        if url is None:
            entry["why"] = "ATLAS_HOST is unset, so no download link resolves"
        entry["media_url"] = url
        entry["link_expires_at"] = expires.isoformat() if expires else None
        out.append(entry)
    return out


async def _draft_row(clip_id: int) -> dict[str, Any] | None:
    row = await fetch_one(
        """SELECT id, campaign, status, draft_submitted_at, draft_decided_at,
                  draft_note
             FROM clip_outputs WHERE id = $1""", int(clip_id))
    return dict(row) if row is not None else None


async def _draft_campaign(row: dict[str, Any]) -> tuple[campaigns.Campaign | None,
                                                        str | None]:
    cid = int(row["id"])
    campaign = await campaigns.load(str(row["campaign"]))
    if campaign is None:
        return None, f"clip {cid}'s campaign is not on file"
    if not campaign.approval_before_post:
        return None, (f"{campaign.name} asks for no draft approval; clip {cid} "
                      f"posts on its own")
    if (why := _eligible(campaign)) is not None:
        return None, f"clip {cid} is not offered for Whop: {why}"
    return campaign, None


async def _submitted_check(clip_id: int) -> tuple[dict[str, Any] | None, bool,
                                                  str | None]:
    """(row, identical repeat already on file, why refused)."""
    try:
        cid = int(clip_id)
    except (TypeError, ValueError):
        return None, False, f"{clip_id!r} is not a clip id"
    row = await _draft_row(cid)
    if row is None:
        return None, False, f"no clip {cid}"
    _campaign, why = await _draft_campaign(row)
    if why is not None:
        return None, False, why
    status = str(row["status"])
    if status == "draft_submitted":
        return row, True, None
    if status not in ("made", "draft_pending"):
        return None, False, f"clip {cid} is {status}, not a draft waiting to be uploaded"
    return row, False, None


async def record_draft_submitted_dry(clip_id: int) -> ActionResult:
    row, already, why = await _submitted_check(clip_id)
    if row is None:
        return ActionResult(False, why or "refused")
    if already:
        return ActionResult(True, f"clip {row['id']}'s draft is already recorded "
                                  f"as uploaded; nothing would change")
    return ActionResult(True, f"would record clip {row['id']}'s draft as uploaded "
                              f"to {row['campaign']} on Whop by the Mac agent")


async def record_draft_submitted(clip_id: int) -> ActionResult:
    """The Mac agent's word that it uploaded this clip as a draft on Whop."""
    row, already, why = await _submitted_check(clip_id)
    if row is None:
        return ActionResult(False, why or "refused")
    cid = int(row["id"])
    if already:
        return ActionResult(True, f"clip {cid}'s draft was already recorded as "
                                  f"uploaded ({row['draft_submitted_at']}); unchanged",
                            evidence={"already": True})
    done = await fetch_one(
        """UPDATE clip_outputs SET status = 'draft_submitted',
                  draft_submitted_at = now()
            WHERE id = $1 AND status IN ('made', 'draft_pending')
        RETURNING id""", cid)
    if done is None:
        again = await _draft_row(cid)
        if again is not None and again["status"] == "draft_submitted":
            return ActionResult(True, f"clip {cid}'s draft was already recorded as "
                                      f"uploaded; unchanged",
                                evidence={"already": True})
        return ActionResult(False, f"clip {cid} changed while it was recorded; "
                                   f"not recorded")
    return ActionResult(True, f"clip {cid}'s draft recorded as uploaded to "
                              f"{row['campaign']} on Whop; it posts once the brand "
                              f"approves it", evidence={"already": False},
                        rollback_token=json.dumps({"id": cid}))


async def record_draft_submitted_rollback(token: str) -> ActionResult:
    cid = int(json.loads(token)["id"])
    await execute_sql(
        """UPDATE clip_outputs SET status = 'draft_pending', draft_submitted_at = NULL
            WHERE id = $1 AND status = 'draft_submitted'""", cid)
    return ActionResult(True, f"clip {cid} back to a draft waiting to be uploaded "
                              f"(ATLAS's record only; the draft on Whop stays)")


async def _decision_check(clip_id: int, approved: Any
                          ) -> tuple[dict[str, Any] | None, bool, str | None]:
    """(row, the same decision already on file, why refused)."""
    try:
        cid = int(clip_id)
    except (TypeError, ValueError):
        return None, False, f"{clip_id!r} is not a clip id"
    if not isinstance(approved, bool):
        return None, False, "approved must be true or false"
    row = await _draft_row(cid)
    if row is None:
        return None, False, f"no clip {cid}"
    _campaign, why = await _draft_campaign(row)
    if why is not None:
        return None, False, why
    status = str(row["status"])
    if row.get("draft_decided_at") is not None:
        same = (status == "draft_approved") if approved else (status == "withdrawn")
        if same:
            return row, True, None
        return None, False, (f"clip {cid}'s draft was already decided "
                             f"({status}, {row['draft_decided_at']}); the "
                             f"opposite decision is refused")
    if status not in ("draft_pending", "draft_submitted"):
        return None, False, f"clip {cid} is {status}, not a draft awaiting a decision"
    return row, False, None


def _decision_words(approved: bool) -> str:
    return "approved" if approved else "rejected"


async def record_draft_decision_dry(clip_id: int, approved: bool,
                                    note: str = "") -> ActionResult:
    row, already, why = await _decision_check(clip_id, approved)
    if row is None:
        return ActionResult(False, why or "refused")
    if already:
        return ActionResult(True, f"clip {row['id']}'s draft is already recorded as "
                                  f"{_decision_words(approved)}; nothing would change")
    return ActionResult(True, f"would record clip {row['id']}'s draft as "
                              f"{_decision_words(approved)} by {row['campaign']} on "
                              f"Whop" + (": it then posts on its own" if approved
                                         else ": it is withdrawn, never posted"))


async def record_draft_decision(clip_id: int, approved: bool,
                                note: str = "") -> ActionResult:
    """The brand's decision on a draft, as the Mac agent read it on Whop:
    approved -> draft_approved (the legs post it); rejected -> withdrawn,
    with the brand's note, said in the clips channel."""
    row, already, why = await _decision_check(clip_id, approved)
    if row is None:
        return ActionResult(False, why or "refused")
    cid = int(row["id"])
    said = _decision_words(approved)
    if already:
        return ActionResult(True, f"clip {cid}'s draft was already recorded as "
                                  f"{said}; unchanged", evidence={"already": True})
    note = " ".join(str(note or "").split())[:500]
    done = await fetch_one(
        """UPDATE clip_outputs SET status = $2, draft_decided_at = now(),
                  draft_note = $3
            WHERE id = $1 AND status IN ('draft_pending', 'draft_submitted')
        RETURNING id""", cid, "draft_approved" if approved else "withdrawn", note)
    if done is None:
        return ActionResult(False, f"clip {cid} changed while it was recorded; "
                                   f"not recorded")
    await _say_decision(row, approved, note)
    detail = (f"clip {cid}'s draft {said} by {row['campaign']} on Whop"
              + (f": {note}" if note else "")
              + ("; it posts on its own once every check passes" if approved
                 else "; withdrawn, never posted"))
    return ActionResult(True, detail, evidence={"already": False, "note": note},
                        rollback_token=json.dumps({"id": cid, "approved": approved}))


async def _say_decision(row: dict[str, Any], approved: bool, note: str) -> None:
    """One line in the clips channel. Best effort: the record stands."""
    import logging

    from ...kernel.comms import Outbound, notify
    from ...kernel.contracts import Severity
    cid = int(row["id"])
    title = (f"Whop approved the draft of clip {cid}" if approved
             else f"Whop rejected the draft of clip {cid}")
    body = (f"{row['campaign']}: the brand {_decision_words(approved)} clip {cid}'s "
            f"draft" + (f' -- "{note}"' if note else "")
            + (". It posts on its own once every check passes." if approved
               else ". It is withdrawn and will not be posted; the next cut of "
                    "that source is a fresh draft."))
    try:
        await notify(Outbound(title=title, body=body, topic="clips",
                              severity=Severity.INFO if approved else Severity.NORMAL,
                              kind="alert"))
    except Exception:  # noqa: BLE001
        logging.getLogger("atlas.clips").warning(
            "clip %s: the draft decision was recorded but not announced", cid)


async def record_draft_decision_rollback(token: str) -> ActionResult:
    data = json.loads(token)
    cid, approved = int(data["id"]), bool(data["approved"])
    await execute_sql(
        """UPDATE clip_outputs SET status = 'draft_submitted', draft_decided_at = NULL,
                  draft_note = NULL
            WHERE id = $1 AND status = $2 AND draft_decided_at IS NOT NULL""",
        cid, "draft_approved" if approved else "withdrawn")
    return ActionResult(True, f"clip {cid} back to a draft awaiting the brand's "
                              f"decision (ATLAS's record only)")
