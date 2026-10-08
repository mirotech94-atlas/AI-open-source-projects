"""The Academy store's promo reel on the clips accounts.

Miro, 2026-10-05, after the store reel went out on the AI-stories accounts:
"post on clips too". Until then the clips accounts -- Instagram
@atlas.clipsdrop, TikTok @atlasclips12 and the clips YouTube channel --
took one thing only: a campaign clip the clips lane cut and checked
(`posting.publish_clip*`), and every doorway onto them refused anything
else ("two TikTok accounts, never crossed", 2026-09-28).

This is the one other thing they post, and it is explicit at every layer
rather than a hole in that rule:

  * the file is a finished render of ATLAS's own (`content_media`, status
    done, its `.mp4` directly in `content.DATA_DIR`), read by the same
    media link the stories reels use -- never a campaign clip, never a
    file from anywhere else (`instagram._source_refusal`,
    `postiz._promo_file`);
  * the caption ends with the AI label, as every ATLAS-made reel does,
    and has no campaign disclosure (it is not an ad for a brand);
  * the Instagram leg runs as `instagram.publish` under this action as its
    parent (`kernel.legs.PARENTS`), with the `store_promo` content kind
    the clips account now lists;
  * it posts once per render (`idempotency`), and the card is Tier ASK:
    nothing reaches the clips accounts without Miro's tap.

Rollback takes the Instagram post down and deletes the Postiz posts while
they are still queued; a published Postiz post is a manual note, as for
clips (deleting it is Tier 3).
"""
from __future__ import annotations

import json
import os
import re

from ...kernel.contracts import ActionResult
from ..content.drafts import AI_LABEL
from . import posting

ACTION = "clips.publish_promo"
CONTENT_KIND = "store_promo"
IG_LEG = "instagram.publish"
YT_TAGS = ["ATLAS Academy", "AI"]
POSTED, ID_PENDING, REFUSED, NOT_CONFIGURED = (
    "posted", "posted, id pending", "refused", "not configured")
NAMES = {"instagram": f"Instagram {posting.ACCOUNT}",
         "tiktok": f"TikTok {posting.TT_HANDLE}",
         "youtube": "YouTube clips (Short)"}


def vet(params: dict) -> str | None:
    """Before the card exists: the label, no disclosure, a sane length."""
    caption = str(params.get("caption") or "")
    if params.get("legs"):
        bad = [x.strip() for x in str(params["legs"]).split(",")
               if x.strip() and x.strip().lower() not in LEGS]
        if bad:
            return f"unknown legs {', '.join(bad)}; the legs are {', '.join(LEGS)}"
    if not caption.rstrip().endswith(AI_LABEL):
        return f"the caption must end with {AI_LABEL!r}"
    if len(caption) > 2200:
        return "caption exceeds 2200 characters"
    from ..instagram import DISCLOSURES
    if any(d.lower() in caption.lower() for d in DISCLOSURES):
        return "a store promo is not a campaign post; it carries no #Ad disclosure"
    try:
        int(params.get("media_id"))
    except (TypeError, ValueError):
        return "media_id must be a content_media id"
    return None


LEGS = ("instagram", "tiktok", "youtube")
_SENTENCE = re.compile(r"(.+?[.!?])(?:\s|$)")


def youtube_title(caption: str) -> str:
    """The caption's first sentence, whole, as the Short's title (a caption
    is one paragraph more often than not); a sentence over YouTube's limit
    is refused by the leg, never cut."""
    first = " ".join(caption.strip().split("\n")[0].split())
    m = _SENTENCE.match(first)
    return m.group(1) if m else first


def legs_of(value: object) -> tuple[str, ...]:
    """The legs a run posts to: all three unless `legs` names fewer, so a
    leg refused once (a bad title) can be re-run alone while the others'
    once-per-render claim stays settled."""
    names = [x.strip().lower() for x in str(value or "").split(",") if x.strip()]
    return tuple(leg for leg in LEGS if leg in names) or LEGS


async def _ready(media_id: int, caption: str) -> tuple[dict | None, str | None]:
    from ..content import crosspost
    if (why := vet({"media_id": media_id, "caption": caption})) is not None:
        return None, f"refused: {why}"
    row, why = await crosspost._media(media_id)
    if row is None:
        return None, why
    if not os.getenv("ATLAS_HOST", "").strip():
        return None, "ATLAS_HOST is unset, so Instagram could not fetch the file"
    return row, None


async def publish_promo_dry(media_id: int, caption: str, legs: str = "") -> ActionResult:
    row, why = await _ready(media_id, caption)
    if row is None:
        return ActionResult(False, why or "refused")
    from ..content import postiz
    from ..instagram import NoAccount, _cfg
    wanted = legs_of(legs)
    lines = []
    if "instagram" in wanted:
        try:
            _cfg(posting.IG_ACCOUNT)
            lines.append(f"{NAMES['instagram']}: ready")
        except NoAccount as exc:
            lines.append(f"{NAMES['instagram']}: {exc}")
    for account, leg in (("clips", "tiktok"), (postiz.YT_ACCOUNT, "youtube")):
        if leg in wanted:
            ok, said = await postiz.readiness(account)
            lines.append(f"{NAMES[leg]}: {'ready' if ok else said}")
    title = youtube_title(caption)
    if "youtube" in wanted and not postiz.YT_TITLE_MIN <= len(title) <= postiz.YT_TITLE_MAX:
        return ActionResult(False, f"the YouTube title ({len(title)} characters) "
                                   f"would be refused: {title[:80]}…")
    return ActionResult(True, f"would post render {media_id} to the clips "
                              f"accounts: " + " · ".join(lines))


async def _instagram(media_id: int, caption: str, url: str
                     ) -> tuple[str, str, str | None]:
    from ...kernel import legs
    from .. import instagram
    try:
        instagram._cfg(posting.IG_ACCOUNT)
    except instagram.NoAccount as exc:
        return NOT_CONFIGURED, str(exc), None
    if (why := await legs.gate(IG_LEG, ACTION, account=posting.IG_ACCOUNT,
                               media_id=media_id)) is not None:
        return REFUSED, why, None
    await legs.start(IG_LEG, ACTION, media_id=media_id)
    try:
        result = await instagram.publish(caption, url, kind="reel",
                                         account=posting.IG_ACCOUNT,
                                         content_kind=CONTENT_KIND)
    except Exception as exc:                            # noqa: BLE001
        said = instagram._why(exc)
        await legs.finish(IG_LEG, ACTION, False, said, media_id=media_id)
        return REFUSED, said, None
    await legs.finish(IG_LEG, ACTION, result.ok, result.detail, media_id=media_id)
    if not result.ok:
        return REFUSED, result.detail, None
    return POSTED, result.detail, result.rollback_token


async def publish_promo(media_id: int, caption: str, legs: str = "") -> ActionResult:
    row, why = await _ready(media_id, caption)
    if row is None:
        return ActionResult(False, why or "refused")
    from ...kernel import idempotency
    from ..content import crosspost, postiz
    wanted = legs_of(legs)
    key = f"promo:{int(media_id)}" + ("" if wanted == LEGS else ":" + ",".join(wanted))
    state = await idempotency.claim(ACTION, key)
    if state != idempotency.CLAIMED:
        return ActionResult(False, f"render {media_id} on the clips accounts: "
                                   f"{idempotency.refusal(ACTION, state)}")
    url, why = await crosspost._public_url(row)
    if url is None:
        await idempotency.release(ACTION, key)
        return ActionResult(False, why or "no public URL")
    path = str(row["path"])
    results = {}
    if "instagram" in wanted:
        results["instagram"] = await _instagram(int(media_id), caption, url)
    if "tiktok" in wanted:
        status, said, post_id, link = await postiz.post_tiktok_promo(path, caption)
        results["tiktok"] = (status, f"{said}{f' at {link}' if link else ''}", post_id)
    if "youtube" in wanted:
        status, said, post_id, link = await postiz.post_youtube_promo(
            path, youtube_title(caption), caption, YT_TAGS)
        results["youtube"] = (status, f"{said}{f' at {link}' if link else ''}", post_id)
    posted = {leg: token for leg, (status, _, token) in results.items()
              if status in (POSTED, ID_PENDING) and token}
    detail = f"render {media_id}: " + " · ".join(
        f"{NAMES[leg]}: {status}" + (f" — {said}" if said else "")
        for leg, (status, said, _) in results.items())
    evidence = {leg: {"status": status, "detail": said}
                for leg, (status, said, _) in results.items()}
    if not posted:
        await idempotency.finish(ACTION, key, "posted nowhere")
        return ActionResult(False, f"posted nowhere — {detail}", evidence=evidence)
    await idempotency.settle(ACTION, key, detail)
    return ActionResult(True, detail, evidence=evidence,
                        rollback_token=json.dumps(posted, sort_keys=True))


async def unpublish_promo(token: str) -> ActionResult:
    from ..content import postiz
    from ..instagram import unpublish
    posted = json.loads(token)
    said = []
    if posted.get("instagram"):
        down = await unpublish(str(posted["instagram"]))
        said.append(f"Instagram: {down.detail}")
    for leg, where in (("tiktok", "TikTok"), ("youtube", "YouTube")):
        if posted.get(leg):
            _ok, what = await postiz.undo(str(posted[leg]), where=where)
            said.append(f"{where}: {what}")
    return ActionResult(True, "; ".join(said) or "nothing to take down")


# ------------------------------------------------------------- automatic
#
# Miro, 2026-10-06: "make continuous reels about ATLAS Academy" on the clips
# accounts. One a day posts itself (academy/reel.py `daily()`), Tier 1 in
# code and gated here, the way the stories lane's autopublish is:
#
#   * `ACADEMY_PROMO_AUTOPOST` off -> nothing posts itself; the reel becomes
#     the ordinary `clips.publish_promo` card (Tier 2) with the reason first;
#   * at most `CLIPS_PROMO_PER_DAY` (default 1) automatic posts a day, read
#     from academy_promo_posts;
#   * the caption ends with the AI label, names no brand or person
#     (`content.guard`), and the file is a finished 1080x1920 render with
#     sound between 12 and 60 s, read by ffprobe (`content.quality`).
#
# Anything that fails a check is not skipped: it is carded, reason first.

PER_DAY_DEFAULT = 1
SECONDS = (12.0, 60.0)


def autopost_enabled() -> bool:
    return os.getenv("ACADEMY_PROMO_AUTOPOST", "on").strip().lower() not in (
        "0", "off", "false", "no")


def per_day() -> int:
    try:
        return max(0, int(os.getenv("CLIPS_PROMO_PER_DAY", str(PER_DAY_DEFAULT))))
    except ValueError:
        return PER_DAY_DEFAULT


async def posted_today() -> int:
    from datetime import UTC, datetime, timedelta

    from ...kernel.state import fetch_one
    row = await fetch_one(
        "SELECT count(*) AS n FROM academy_promo_posts WHERE at > $1",
        datetime.now(UTC) - timedelta(hours=24))
    return int(row["n"]) if row else 0


async def autopublish_check(media_id: int, caption: str) -> str | None:
    """None when this reel may post itself now; otherwise why not."""
    if not autopost_enabled():
        return "automatic posting is off (ACADEMY_PROMO_AUTOPOST)"
    if (why := vet({"media_id": media_id, "caption": caption})) is not None:
        return why
    from ..content import guard, quality
    if (why := guard.refusal(caption)) is not None:
        return why
    if (n := await posted_today()) >= per_day():
        return f"{n} Academy reel(s) already posted today (CLIPS_PROMO_PER_DAY)"
    # The Academy's editions are composed frames by Miro's choice (2026-10-06,
    # "make it more designable"); the stories lane's still-frame rule is off.
    return await quality.check_media(media_id, caption, series=False, seconds=SECONDS,
                                     motion=False)


async def _card(media_id: int, caption: str, slug: str, why: str) -> ActionResult:
    from ...kernel import approvals
    pid = await approvals.fresh_card(
        ACTION, {"media_id": media_id, "caption": caption},
        f"Held, not posted itself: {why}. The Academy reel for {slug or media_id}.",
        dedup_key=f"clips.promo.{media_id}")
    return ActionResult(True, f"held ({why}); card #{pid} raised" if pid else
                        f"held ({why}); no card could be raised")


async def autopublish_promo_dry(media_id: int, caption: str, slug: str = "") -> ActionResult:
    why = await autopublish_check(media_id, caption)
    if why is not None:
        return ActionResult(True, f"would hold ({why}) and raise the card instead")
    return await publish_promo_dry(media_id, caption)


async def autopublish_promo(media_id: int, caption: str, slug: str = "") -> ActionResult:
    """Post the Academy reel to the clips accounts by itself, or card it."""
    why = await autopublish_check(media_id, caption)
    if why is not None:
        return await _card(media_id, caption, slug, why)
    result = await publish_promo(media_id, caption)
    if result.ok:
        from ...kernel.state import execute_sql
        await execute_sql(
            """INSERT INTO academy_promo_posts (slug, media_id, detail)
               VALUES ($1, $2, $3)""", slug or "", int(media_id), result.detail[:1000])
    return result
