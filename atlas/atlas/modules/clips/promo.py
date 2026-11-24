"""The Academy store's promo reel on the clips accounts.

the owner, 2026-10-05, after the store reel went out on the AI-stories accounts:
"post on clips too". Until then the clips accounts -- Instagram
@example_handle, TikTok @example_handle and the clips YouTube channel --
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
  * it posts once per render (`idempotency`) and once per product and
    platform inside ACADEMY_PROMO_REPOST_DAYS (`academy_promo_claims`),
    and the card is Tier ASK: nothing reaches the clips accounts without
    the owner's tap.

Rollback takes the Instagram post down and deletes the Postiz posts while
they are still queued; a published Postiz post is a manual note, as for
clips (deleting it is Tier 3).
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime, timedelta

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
    """Before the card exists: the label, no disclosure, a sane length --
    for the caption and, when one is given, the YouTube caption."""
    if params.get("youtube_caption"):
        yt = {**params, "caption": params["youtube_caption"], "youtube_caption": ""}
        if (why := vet(yt)) is not None:
            return f"YouTube caption: {why}"
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
log = logging.getLogger("atlas.clips.promo")

# the owner, 2026-10-11: "only keep YouTube and TikTok". A run that names no
# legs posts to these; `legs="instagram"` still names it explicitly.
DEFAULT_LEGS = ("tiktok", "youtube")
_SENTENCE = re.compile(r"(.+?[.!?])(?:\s|$)")


def youtube_title(caption: str) -> str:
    """The caption's first sentence, whole, as the Short's title (a caption
    is one paragraph more often than not); a sentence over YouTube's limit
    is refused by the leg, never cut."""
    first = " ".join(caption.strip().split("\n")[0].split())
    m = _SENTENCE.match(first)
    return m.group(1) if m else first


def youtube_tags(slug: str) -> list[str]:
    """The Short's tags: a teaching Short's lesson tags (academy.tutorials
    .teaching_tags, 2026-10-18), the product's own (academy/promo_copy, 15-25,
    within YouTube's 500-character count), or the old pair for a render
    that is neither."""
    from ..academy import promo_copy, tutorials
    if (lesson := tutorials.teaching_tags(slug)) is not None:
        return lesson
    return promo_copy.tags_for(slug) if promo_copy.for_slug(slug) else list(YT_TAGS)


async def youtube_description(caption: str, slug: str) -> str:
    """The caption as the Short's description, with two additions only the
    video site gets. The first is #Shorts, at the head of the hashtag line.
    The second is the matching tutorial playlist, when the channel really
    has a public one (academy_tutorial_posts). Both are held in code, so the
    card's caption stays the text that was approved."""
    from ..academy import promo_copy
    lines = caption.rstrip().split("\n")
    tutorial = await promo_copy.tutorial_url(slug) if slug else None
    out: list[str] = []
    for line in lines:
        if line.startswith("Free step-by-step tutorials") and tutorial:
            line = f"Free step-by-step tutorials: {tutorial}"
        if line.startswith("#") and "#shorts" not in line.lower():
            line = f"#Shorts {line}"
        out.append(line)
    return "\n".join(out)[:5000]


def legs_of(value: object) -> tuple[str, ...]:
    """The legs a run posts to: all three unless `legs` names fewer, so a
    leg refused once (a bad title) can be re-run alone while the others'
    once-per-render claim stays settled."""
    names = [x.strip().lower() for x in str(value or "").split(",") if x.strip()]
    return tuple(leg for leg in LEGS if leg in names) or DEFAULT_LEGS


async def _ready(media_id: int, caption: str) -> tuple[dict | None, str | None]:
    from ..content import crosspost
    if (why := vet({"media_id": media_id, "caption": caption})) is not None:
        return None, f"refused: {why}"
    if (why := await old_design_refusal(media_id)) is not None:
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


async def publish_promo(media_id: int, caption: str, legs: str = "",
                        youtube_caption: str = "") -> ActionResult:
    """`youtube_caption`: the Short's own title (its first line) and
    description, when the YouTube leg gets different words from TikTok's
    caption (the growth plan, 2026-10-12: each platform its own CTA)."""
    row, why = await _ready(media_id, caption)
    if row is None:
        return ActionResult(False, why or "refused")
    from ...kernel import idempotency
    from ..content import crosspost, postiz
    wanted = legs_of(legs)
    key = f"promo:{int(media_id)}" + ("" if wanted == DEFAULT_LEGS else ":" + ",".join(wanted))
    state = await idempotency.claim(ACTION, key)
    if state != idempotency.CLAIMED:
        return ActionResult(False, f"render {media_id} on the clips accounts: "
                                   f"{idempotency.refusal(ACTION, state)}")
    url, why = await crosspost._public_url(row)
    if url is None:
        await idempotency.release(ACTION, key)
        return ActionResult(False, why or "no public URL")
    slug = await slug_of(int(media_id))
    subject = slug or f"render:{int(media_id)}"
    results: dict[str, tuple[str, str, str | None]] = {}
    if slug and (why := await recently_posted(slug, int(media_id))) is not None:
        await idempotency.finish(ACTION, key, why)
        return ActionResult(False, f"render {media_id}: {why}")
    claimed = []
    for leg in wanted:
        if await claim_leg(subject, leg, int(media_id)):
            claimed.append(leg)
        else:
            results[leg] = (REFUSED, (f"{subject} was already posted here in the last "
                                      f"{repost_days()} days (academy_promo_claims)"), None)
    path = str(row["path"])
    if "instagram" in claimed:
        results["instagram"] = await _instagram(int(media_id), caption, url)
    if "tiktok" in claimed:
        status, said, post_id, link = await postiz.post_tiktok_promo(path, caption)
        results["tiktok"] = (status, f"{said}{f' at {link}' if link else ''}", post_id)
    if "youtube" in claimed:
        yt = youtube_caption or caption
        status, said, post_id, link = await postiz.post_youtube_promo(
            path, youtube_title(yt), await youtube_description(yt, slug),
            youtube_tags(slug))
        results["youtube"] = (status, f"{said}{f' at {link}' if link else ''}", post_id)
    for leg in claimed:
        status, said, _ = results[leg]
        if status in (REFUSED, NOT_CONFIGURED):
            await release_leg(subject, leg, int(media_id))
        else:
            await settle_leg(subject, leg, int(media_id), said)
    results = {leg: results[leg] for leg in LEGS if leg in results}
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
    if slug:
        await _record(slug, int(media_id), detail)
    return ActionResult(True, detail, evidence=evidence,
                        rollback_token=json.dumps(posted, sort_keys=True))


# ------------------------------------------------- once per product and platform
#
# The per-render claim above (`promo:<media_id>`) stops one render from being
# posted twice. It did not stop two renders of one product. On 2026-10-10
# "AI for Every Age" went out twice on every platform. Render 348 was held by
# the daily cap and carded, and the card was approved at 17:13. Restarts had
# meanwhile re-run the daily job, which made render 356 and auto-posted it at
# 18:03. Each render had its own key. So each leg now also claims
# (product, platform) in `academy_promo_claims`, in one atomic statement,
# before anything is sent. The claim is refused while an earlier one is
# younger than ACADEMY_PROMO_REPOST_DAYS. The rotation brings a product back
# about once a week (7 on sale), so the default of 5 days leaves room for
# the daily hour to drift.

REPOST_DAYS_DEFAULT = 5


def repost_days() -> int:
    try:
        return max(1, int(os.getenv("ACADEMY_PROMO_REPOST_DAYS", str(REPOST_DAYS_DEFAULT))))
    except ValueError:
        return REPOST_DAYS_DEFAULT


async def slug_of(media_id: int) -> str:
    """The Academy product a render is about, from its draft's source
    (`academy:<slug>`, academy/reel.py). It is read from the database, not
    from the card's params, so a card raised before this check existed is
    still matched to its product. '' for any other render."""
    from ...kernel.state import fetch_one
    row = await fetch_one(
        """SELECT d.source FROM content_media m
             JOIN content_drafts d ON d.id = m.draft_id
            WHERE m.id = $1""", int(media_id))
    source = str(row["source"] or "") if row is not None else ""
    if source.startswith("academy:"):
        return source.split(":", 1)[1]
    # Lesson and concept Shorts are keyed by their whole source
    # ("lesson:sell/1.2", "concept:reply-ready"): the same key academy/reel.py
    # rotates on. Without it an automatic Short went out unrecorded, so the
    # rotation picked it again and the weekly log never saw it (2026-10-12).
    if source.startswith(("lesson:", "concept:")):
        return source
    return ""


async def recently_posted(slug: str, media_id: int) -> str | None:
    """Why not, when another render of `slug` was posted inside the window
    (academy_promo_posts, which predates the claims table). The same render
    re-run for one leg, as card 306 did, is not counted."""
    from ...kernel.state import fetch_one
    days = repost_days()
    row = await fetch_one(
        """SELECT media_id, at FROM academy_promo_posts
            WHERE slug = $1 AND media_id <> $2 AND at > $3
            ORDER BY at DESC LIMIT 1""",
        slug, int(media_id), datetime.now(UTC) - timedelta(days=days))
    if row is None:
        return None
    return (f"{slug} was already posted (render {row['media_id']}, "
            f"{row['at']:%Y-%m-%d %H:%M} UTC) in the last {days} days; "
            f"a product is not posted twice")


async def claim_leg(slug: str, leg: str, media_id: int) -> bool:
    """Atomically take (slug, leg). True only when no other claim from the
    last `repost_days()` holds it. A released claim, or one past the window,
    is taken over. If the database cannot answer, the leg does not post:
    a missing post can be retried, but a duplicate cannot be taken back."""
    from ...kernel.state import fetch_one
    try:
        row = await fetch_one(
            """INSERT INTO academy_promo_claims (slug, leg, media_id)
                    VALUES ($1, $2, $3)
               ON CONFLICT (slug, leg) DO UPDATE
                  SET media_id = EXCLUDED.media_id, claimed_at = now(),
                      status = 'sending', detail = ''
                WHERE academy_promo_claims.status = 'released'
                   OR academy_promo_claims.claimed_at < now() - $4::interval
               RETURNING slug""",
            slug, leg, int(media_id), timedelta(days=repost_days()))
    except Exception as exc:                            # noqa: BLE001
        log.warning("%s on %s: the claim could not be made (%s); not posting",
                    slug, leg, type(exc).__name__)
        return False
    return row is not None


async def release_leg(slug: str, leg: str, media_id: int) -> None:
    """The leg definitely did not leave (refused, not configured): a later
    render may post it."""
    from ...kernel.state import execute_sql
    try:
        await execute_sql(
            """UPDATE academy_promo_claims SET status = 'released'
                WHERE slug = $1 AND leg = $2 AND media_id = $3""",
            slug, leg, int(media_id))
    except Exception as exc:                            # noqa: BLE001
        log.warning("%s on %s: could not release (%s)", slug, leg, type(exc).__name__)


async def settle_leg(slug: str, leg: str, media_id: int, detail: str) -> None:
    from ...kernel.state import execute_sql
    try:
        await execute_sql(
            """UPDATE academy_promo_claims SET status = 'sent', detail = $4
                WHERE slug = $1 AND leg = $2 AND media_id = $3""",
            slug, leg, int(media_id), detail[:500])
    except Exception as exc:                            # noqa: BLE001
        log.warning("%s on %s: could not settle (%s)", slug, leg, type(exc).__name__)


async def _record(slug: str, media_id: int, detail: str) -> None:
    """The day's post, for the rotation and the daily cap, whichever way it
    went out (automatically, or by an approved card)."""
    from ...kernel.state import execute_sql
    await execute_sql(
        """INSERT INTO academy_promo_posts (slug, media_id, detail)
           VALUES ($1, $2, $3)""", slug, int(media_id), detail[:1000])


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
# the owner, 2026-10-06: "make continuous reels about ATLAS Academy" on the clips
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

CAP_HELD = "(CLIPS_PROMO_PER_DAY)"
PER_DAY_DEFAULT = 2                  # the owner, 2026-10-11: two a day, each a different lesson
SECONDS = (12.0, 60.0)
# the owner 2026-10-19: a NotebookLM Short may run 60-90 s (academy/
# notebooklm_shorts.py); every other gate is the same. Read from the
# render's draft source in the database, never from a caller's params.
NOTEBOOKLM_SECONDS = (50.0, 95.0)
_NOTEBOOKLM_SOURCE = re.compile(r"^academy:tutorial:[a-z][a-z0-9]*:\d{1,3}:notebooklm$")


async def draft_source(media_id: int) -> str | None:
    """The render's draft source (content_drafts.source), or None when it
    can't be read. Read from the database, never from a caller's params."""
    from ...kernel.state import fetch_one
    try:
        row = await fetch_one(
            """SELECT d.source FROM content_media m
                 JOIN content_drafts d ON d.id = m.draft_id
                WHERE m.id = $1""", int(media_id))
    except Exception:                                   # noqa: BLE001
        return None
    return None if row is None else str(row["source"] or "")


async def seconds_for(media_id: int) -> tuple[float, float]:
    """The length window for this render: NOTEBOOKLM_SECONDS when its draft
    is a NotebookLM Short's, else SECONDS -- also when the record can't be
    read (never the longer window on an unknown)."""
    source = await draft_source(media_id) or ""
    return NOTEBOOKLM_SECONDS if _NOTEBOOKLM_SOURCE.match(source) else SECONDS


NOTEBOOKLM_ONLY = "NotebookLM only: ACADEMY_NOTEBOOKLM_FALLBACK is off"


async def old_design_refusal(media_id: int) -> str | None:
    """the owner, 2026-10-19: every Academy Short is NotebookLM's. While
    ACADEMY_NOTEBOOKLM_FALLBACK is off, only a NotebookLM Short's render
    (academy/notebooklm_shorts.py) may post; an ATLAS render -- a teaching
    cut, a lesson, concept or product reel, all ATLAS-drawn and Fish-voiced
    -- is refused, and so is a render whose source can't be read (fail
    closed). Asked by the post itself, card or automatic, not only by the
    passes that call it."""
    from ..academy import notebooklm
    if notebooklm.fallback_enabled():
        return None
    source = await draft_source(media_id)
    if not source:
        return (f"render {media_id}: its source can't be read, so it is not known "
                f"to be a NotebookLM Short; not posted ({NOTEBOOKLM_ONLY})")
    if _NOTEBOOKLM_SOURCE.match(source):
        return None
    return (f"render {media_id} ({source}) is an ATLAS render, not a NotebookLM "
            f"Short; not posted ({NOTEBOOKLM_ONLY})")


def autopost_enabled() -> bool:
    return os.getenv("ACADEMY_PROMO_AUTOPOST", "on").strip().lower() not in (
        "0", "off", "false", "no")


def per_day() -> int:
    try:
        return max(0, int(os.getenv("CLIPS_PROMO_PER_DAY", str(PER_DAY_DEFAULT))))
    except ValueError:
        return PER_DAY_DEFAULT


DAY_TZ = "America/New_York"


def day_start(now: datetime | None = None) -> datetime:
    """Midnight today in New York, as a UTC instant. "Today" is a calendar
    date, never a rolling 24 h: on 2026-10-10 a rolling window held the
    16:03 reel because the previous day's had gone out at 16:06. And it is
    New York's date, where the owner and the audience are (2026-10-12): two
    Shorts posted by hand at 9 pm New York fell on the next UTC date and
    would have spent that day's noon and 7 pm slots."""
    from zoneinfo import ZoneInfo
    now = now or datetime.now(UTC)
    local = now.astimezone(ZoneInfo(DAY_TZ))
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


async def posted_today() -> int:
    from ...kernel.state import fetch_one
    row = await fetch_one(
        "SELECT count(*) AS n FROM academy_promo_posts WHERE at >= $1", day_start())
    return int(row["n"]) if row else 0


async def autopublish_check(media_id: int, caption: str,
                            youtube_caption: str = "") -> str | None:
    """None when this reel may post itself now; otherwise why not."""
    if not autopost_enabled():
        return "automatic posting is off (ACADEMY_PROMO_AUTOPOST)"
    if (why := vet({"media_id": media_id, "caption": caption,
                    "youtube_caption": youtube_caption})) is not None:
        return why
    from ..content import guard, quality
    for text in (caption, youtube_caption):
        if text and (why := guard.refusal(text)) is not None:
            return why
    if (n := await posted_today()) >= per_day():
        return f"{n} Academy reel(s) already posted today {CAP_HELD}"
    # The Academy's editions are composed frames by the owner's choice (2026-10-06,
    # "make it more designable"); the stories lane's still-frame rule is off.
    return await quality.check_media(media_id, caption, series=False,
                                     seconds=await seconds_for(media_id), motion=False)


async def _card(media_id: int, caption: str, slug: str, why: str,
                youtube_caption: str = "") -> ActionResult:
    from ...kernel import approvals
    params = {"media_id": media_id, "caption": caption}
    if youtube_caption:
        params["youtube_caption"] = youtube_caption
    pid = await approvals.fresh_card(
        ACTION, params,
        f"Held, not posted itself: {why}. The Academy reel for {slug or media_id}.",
        dedup_key=f"clips.promo.{media_id}")
    return ActionResult(True, f"held ({why}); card #{pid} raised" if pid else
                        f"held ({why}); no card could be raised")


async def autopublish_promo_dry(media_id: int, caption: str, slug: str = "",
                                youtube_caption: str = "", legs: str = "") -> ActionResult:
    if (why := await old_design_refusal(media_id)) is not None:
        return ActionResult(False, why)
    why = await autopublish_check(media_id, caption, youtube_caption)
    if why is not None:
        return ActionResult(True, f"would hold ({why}) and raise the card instead")
    return await publish_promo_dry(media_id, caption, legs)


async def autopublish_promo(media_id: int, caption: str, slug: str = "",
                            youtube_caption: str = "", legs: str = "") -> ActionResult:
    """Post the Academy reel to the clips accounts by itself, or card it.
    An ATLAS render while the NotebookLM fallback is off is refused before
    any check: never carded, since the card would ask the owner to post it."""
    if (why := await old_design_refusal(media_id)) is not None:
        return ActionResult(False, why)
    why = await autopublish_check(media_id, caption, youtube_caption)
    if why is not None and CAP_HELD in why:
        # The day's count, not the reel: it is not posted and not carded. A
        # card here only asked the owner to break CLIPS_PROMO_PER_DAY (2026-10-12,
        # memory approval-policy: routine holds wait, only real problems are
        # cards). The lesson was not shown, so a later day picks it again.
        return ActionResult(True, f"not posted today ({why}); no card -- the "
                                  f"cap holds and a later day shows it")
    if why is not None:
        return await _card(media_id, caption, slug, why, youtube_caption)
    # publish_promo records the post (academy_promo_posts) itself, so a
    # card approved later is counted the same as an automatic post.
    return await publish_promo(media_id, caption, legs, youtube_caption=youtube_caption)
