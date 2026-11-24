"""ATLAS Academy: the owner's own store at https://agency.example.com/.

Replaces the Shopify store. Four products (Build a Safe AI Operator, 30
Business Lessons, the Claude + Obsidian Vault Kit, AI for Every Age) and an
"AI Foundations Bundle - 4 Titles" bundle (slug all-four), sold through
Stripe Payment Links on the ATLAS Academy Stripe account.

* `catalog.json` is the one source for what is sold. A price, a payment
  link or a refund term the owner hasn't set stays null or empty, and the item
  is not on sale.
* `site.py` generates the storefront from it; `academy.publish_shop`
  (Tier 2) puts it live as a Mapfront release under the slug `shop`. Agency
  client slugs always end in "-<lead id>", so they can never be `shop`.
* After paying, a buyer lands on `/public/academy/access` (atlas/api/
  academy.py), which asks Stripe -- with a READ-only restricted key -- that
  the checkout is paid and not refunded, and hands out signed, expiring
  download links to files in a private directory. Nothing is emailed and
  nothing is written to Stripe.
* `academy.resend_access` (Tier 2, irreversible) emails a buyer who lost
  their link the same page, to the address Stripe holds, on the owner's tap.
* A buyer who lost it can also recover it themselves (2026-10-16): the
  store's recover/ form posts their email and Stripe receipt number to
  `/public/academy/recover`, which checks both against Stripe (read-only)
  and opens a one-hour library of their purchases. No email is sent.

Money comes in here. Nothing refunds, pays out or changes a price: those
stay Tier 3 or the owner's own, in the Stripe dashboard.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

from ...kernel.contracts import Action, Check, Health, Observation, Severity, Tier
from . import (
    content_plan,
    delivery,
    growth,
    lab_playlist,
    lesson_youtube,
    metadata,
    notebooklm,
    notebooklm_shorts,
    notebooklm_thumbs,
    publish,
    reel,
    subscribers,
    support,
    synthetic,
    tutorials,
)
from .catalog import CatalogError, load
from .publish import publish_shop, publish_shop_dry, publish_shop_rollback
from .resend import resend_access, resend_access_dry, vet
from .reviews import (
    approve_review,
    approve_review_dry,
    approve_review_rollback,
    auto_approve_review,
    auto_approve_review_dry,
)
from .reviews import vet as vet_review
from .shop_remote import (
    publish_shop_remote,
    publish_shop_remote_dry,
    publish_shop_remote_rollback,
)
from .stripe_read import KEY_ENV, read_key

ACTION_VERSION = "1"


async def check_delivery() -> Observation:
    """Can a buyer of every item on sale be served right now? Local reads
    only (env and disk): no Stripe call, so it cannot cost or be rate-limited.
    Nothing on sale is OK and says so; an unreadable catalog or files
    directory is UNKNOWN, never OK."""
    try:
        cat = load()
    except (CatalogError, OSError, ValueError) as exc:
        return Observation("delivery", Health.UNKNOWN,
                           detail=f"catalog unreadable: {exc}")
    live = [o for o in cat.offers() if o.buy_live]
    root = delivery.files_root()
    try:
        missing_live = await asyncio.to_thread(delivery.missing_files, cat,
                                               live_only=True)
        missing_all = await asyncio.to_thread(delivery.missing_files, cat)
    except OSError as exc:
        return Observation("delivery", Health.UNKNOWN,
                           detail=f"files directory {root} unreadable: {exc}")
    total = len({f.key for o in cat.offers() for f in o.files})
    on_disk = f"{total - len(missing_all)} of {total} files on disk in {root}"
    if not live:
        return Observation("delivery", Health.OK, float(len(live)), "offers on sale",
                           f"nothing on sale yet; {on_disk}",
                           evidence={"missing": missing_all})
    problems = []
    if not read_key():
        problems.append(f"{KEY_ENV} is unset: buyers' payments can't be checked")
    if delivery.signing_key() is None:
        problems.append("no download signing key (ACADEMY_DOWNLOAD_KEY or "
                        "SESSION_SECRET)")
    if missing_live:
        problems.append(f"files for items on sale are missing: {', '.join(missing_live)}")
    names = ", ".join(o.name for o in live)
    if problems:
        return Observation("delivery", Health.FAILING, float(len(live)), "offers on sale",
                           f"on sale: {names}. " + "; ".join(problems),
                           evidence={"missing": missing_live})
    return Observation("delivery", Health.OK, float(len(live)), "offers on sale",
                       f"on sale: {names}; {on_disk}")


class AcademyModule:
    name = "academy"
    title = "ATLAS Academy"
    requires_env: tuple[str, ...] = ()

    async def setup(self) -> None:
        # the owner, 2026-10-06 (store ops, no card): a deploy that changed what
        # the store would render republishes it by itself, through the
        # executor (academy.publish_shop is Tier 1). Off the loop; a failure
        # is logged and the next deploy tries again.
        from ...kernel import background
        background.spawn(publish.republish_if_changed(), "the store's self-republish")
        # Support cards keep going to a channel ATLAS made earlier (support.py).
        await support.load_saved_channel()
    async def teardown(self) -> None: ...

    def checks(self):
        return (Check("delivery", check_delivery, timedelta(hours=6), Severity.NORMAL,
                      "every item on sale can be checked with Stripe and "
                      "downloaded (key set, files on disk)", baseline_sigma=None),
                # 2026-10-19: lessons post only as NotebookLM renders; a lane
                # with none landing says so, and an unreadable one is UNKNOWN.
                Check("notebooklm_lane", notebooklm.check_lane, timedelta(hours=6),
                      Severity.NORMAL, "NotebookLM lesson renders are landing "
                      "(an ingest within 3 days)", baseline_sigma=None),
                ) + support.checks() + growth.checks()

    def actions(self):
        return (
            Action(name="academy.auto_approve_review", tier=Tier.AUTO,
                   # the owner, 2026-10-06: buyer reviews show themselves when they
                   # pass reviews.clean_refusal (no link, no contact, plain
                   # opinion); anything else stays his card. Same rollback.
                   summary="Show a clean verified-buyer review on its product page",
                   run=auto_approve_review, dry_run=auto_approve_review_dry,
                   rollback=approve_review_rollback, vet=vet_review,
                   params={"review_id": "the stored review's id"},
                   max_per_hour=20),
            Action(name="academy.render_promo_reel", tier=Tier.AUTO,
                   # the owner, 2026-10-06: a daily reel about the Academy for the
                   # clips accounts, made here from the catalog (reel.py). It
                   # writes a file and a content_media row; posting is
                   # clips.autopublish_promo's, gated there.
                   summary="Render one product's Academy reel from the catalog "
                           "(its cover, its counted facts, the lessons' voice)",
                   run=reel.render_promo_reel, dry_run=reel.render_promo_reel_dry,
                   rollback=reel.unrender_promo_reel,
                   params={"slug": "the product's catalog slug"},
                   runs_in_background=True, max_per_hour=2),
            # the owner, 2026-10-09: long-form tutorials (n8n, Claude & MCP) on
            # YouTube in playlist series (tutorials.py). The same shape as
            # the reel: render is Tier 1 (files on our own disk), posting is
            # Tier 1 only past tutorials.autopublish_check, and anything
            # held is the Tier 2 academy.publish_tutorial card.
            Action(name="academy.render_tutorial", tier=Tier.AUTO,
                   summary="Render one catalog tutorial episode (slides, the Fish "
                           "voice, the cached intro and ending, its thumbnail)",
                   run=tutorials.render_tutorial, dry_run=tutorials.render_tutorial_dry,
                   rollback=tutorials.unrender_tutorial, vet=tutorials.vet,
                   params={"series": "the tutorial series key: 'channel', 'claude', 'n8n', "
                                     "'project', 'business', 'agents' or 'local'",
                           "episode": "the episode number in that series"},
                   runs_in_background=True, max_per_hour=2),
            # the owner, 2026-10-19: lessons are rendered in NotebookLM on the Mac
            # and dropped into the tutorials inbox; the ingest checks a drop
            # and moves it into its episode directory (files on our own disk,
            # nothing leaves the box). Its rollback puts the drop back.
            Action(name=notebooklm.ACTION, tier=Tier.AUTO,
                   summary="Check a NotebookLM lesson render from the inbox and move "
                           "it into its episode directory with its manifest, "
                           "thumbnail and description, or refuse it",
                   run=notebooklm.ingest, dry_run=notebooklm.ingest_dry,
                   rollback=notebooklm.uningest, vet=tutorials.vet,
                   params={"series": "the tutorial series key: 'channel', 'claude', 'n8n', "
                                     "'project', 'business', 'agents' or 'local'",
                           "episode": "the episode number in that series"},
                   runs_in_background=True, max_per_hour=8),
            # the owner, 2026-10-19: NotebookLM Shorts (60-90 s) from the Mac runner,
            # in the Shorts inbox. Tier 1 as the teaching Short's render: a
            # file on our own disk and a content_media row; the post is
            # clips.autopublish_promo's, gated there. Its rollback withdraws
            # the row and puts the drop back.
            Action(name=notebooklm_shorts.ACTION, tier=Tier.AUTO,
                   summary="Check a NotebookLM Short from the inbox and make it a "
                           "finished 1080x1920 Short render for the clips post, or "
                           "refuse it",
                   run=notebooklm_shorts.ingest, dry_run=notebooklm_shorts.ingest_dry,
                   rollback=notebooklm_shorts.uningest, vet=tutorials.vet,
                   params={"series": "the tutorial series key: 'channel', 'claude', 'n8n', "
                                     "'project', 'business', 'agents' or 'local'",
                           "episode": "the episode number in that series"},
                   runs_in_background=True, max_per_hour=4),
            # the owner, 2026-10-20: a NotebookLM thumbnail for every video,
            # posted ones too. Tier 1 by his approval policy, the shape of
            # academy.refresh_video_metadata (routine, reversible): one
            # thumbnails.set on ATLAS's own public lesson (the id on record,
            # never guessed), the image decoded only by ffmpeg; the dry run
            # checks the image and finds the id; the rollback sets the kept
            # previous thumbnail again. A 403 holds every one for 7 days.
            Action(name=notebooklm_thumbs.ACTION, tier=Tier.AUTO,
                   summary="Set a NotebookLM-made thumbnail on one of the Academy's "
                           "own public lessons, keeping the previous one for the "
                           "rollback",
                   run=notebooklm_thumbs.apply, dry_run=notebooklm_thumbs.apply_dry,
                   rollback=notebooklm_thumbs.unapply, vet=tutorials.vet,
                   params={"series": "the tutorial series key: 'channel', 'claude', 'n8n', "
                                     "'project', 'business', 'agents' or 'local'",
                           "episode": "the episode number in that series"},
                   max_per_hour=notebooklm_thumbs.PER_PASS),
            Action(name="academy.autopublish_tutorial", tier=Tier.AUTO,
                   summary="Post a rendered tutorial to YouTube by itself (private "
                           "upload, thumbnail, series playlist, then public), once it "
                           "passes every automatic check",
                   run=tutorials.autopublish_tutorial,
                   dry_run=tutorials.autopublish_tutorial_dry,
                   rollback=tutorials.unpublish_tutorial, vet=tutorials.vet,
                   params={"series": "the tutorial series key: 'channel', 'claude', 'n8n', "
                                     "'project', 'business', 'agents' or 'local'",
                           "episode": "the episode number in that series"},
                   # One daily run posts one per series (2026-10-15): the
                   # hourly ceiling matches the daily cap held in code.
                   runs_in_background=True, max_per_hour=tutorials.PER_DAY_DEFAULT),
            # 2026-10-13 (growth plan, task 6): Tier 1 by the owner's standing
            # policy; its rollback edits the comment, never deletes it.
            Action(name="academy.pin_comment", tier=Tier.AUTO,
                   summary="Post the quiz + free lab-file comment under a public "
                           "tutorial (the owner pins it in Studio)",
                   run=tutorials.pin_comment, dry_run=tutorials.pin_comment_dry,
                   rollback=tutorials.unpin_comment, vet=tutorials.vet,
                   params={"series": "the tutorial series key: 'channel', 'claude', 'n8n', "
                                     "'project', 'business', 'agents' or 'local'",
                           "episode": "the episode number in that series"},
                   max_per_hour=4),
            # the owner, 2026-10-17: "yes add the AI flag for Shorts too". Tier 1 by
            # his approval policy (routine, reversible): status.containsSynthetic
            # Media on an Academy Short Postiz posted, every other status field
            # resent as read; the rollback sets the flag back.
            Action(name=synthetic.ACTION, tier=Tier.AUTO,
                   summary="Mark one of the Academy's own YouTube Shorts as made "
                           "with a synthetic (AI) voice",
                   run=synthetic.flag, dry_run=synthetic.flag_dry,
                   rollback=synthetic.unflag, vet=synthetic.vet,
                   params={"slug": "the Short's recorded slug (tutorial:, lesson: or "
                                   "concept:)",
                           "video_id": "the Short's YouTube video id, as recorded"},
                   max_per_hour=synthetic.MAX_PER_HOUR),
            # the owner, 2026-10-18: the already-public lessons and teaching Shorts
            # brought up to the current tags, hashtags and description. Tier 1
            # by his approval policy (routine, reversible): tags and
            # description only, the title resent as read; the rollback writes
            # the previous ones back. A few a day (metadata.PER_DAY).
            Action(name=metadata.ACTION, tier=Tier.AUTO,
                   summary="Bring one public Academy lesson or teaching Short's "
                           "YouTube tags and description up to the current rules",
                   run=metadata.refresh, dry_run=metadata.refresh_dry,
                   rollback=metadata.unrefresh, vet=metadata.vet,
                   params={"series": "the tutorial series key: 'channel', 'claude', 'n8n', "
                                     "'project', 'business', 'agents' or 'local'",
                           "episode": "the episode number in that series",
                           "kind": "'lesson' for the lesson itself, or the teaching "
                                   "Short's kind: 'payoff', 'stuck' or 'quiz'"},
                   max_per_hour=metadata.PER_DAY),
            # 2026-10-13 (growth plan task 7): a teaching Short cut from a
            # public tutorial is a file and a content_media row on our own
            # disk; the post is clips.autopublish_promo's, gated there.
            Action(name="academy.render_teaching_short", tier=Tier.AUTO,
                   summary="Cut one teaching Short (payoff, stuck or quiz) from a "
                           "public tutorial's render",
                   run=tutorials.render_teaching_short,
                   dry_run=tutorials.render_teaching_short_dry,
                   rollback=tutorials.unrender_teaching_short,
                   vet=tutorials.vet_teaching,
                   params={"series": "the tutorial series key: 'channel', 'claude', 'n8n', "
                                     "'project', 'business', 'agents' or 'local'",
                           "episode": "the episode number in that series",
                           "kind": "the cut: 'payoff', 'stuck' or 'quiz'"},
                   runs_in_background=True, max_per_hour=3),
            # the owner, 2026-11-14: lesson videos on YouTube. Free previews public,
            # paid lessons unlisted and embedded on the buyer's page only. One
            # card per lesson; the audience and the source are decided in
            # code (lesson_youtube), the rollback sets it private.
            Action(name=lesson_youtube.ACTION, tier=Tier.ASK,
                   summary="Upload one gated Academy video to YouTube: the full "
                           "lesson (unlisted), a short public preview of a lesson "
                           "or ebook/PDF product, or a vertical Short (public)",
                   run=lesson_youtube.upload_lesson,
                   dry_run=lesson_youtube.upload_lesson_dry,
                   rollback=lesson_youtube.upload_lesson_rollback,
                   vet=lesson_youtube.vet,
                   params={"kind": "full (default), preview or short",
                           "course": "operator, adults, 60plus or sell",
                           "num": "the lesson number like 3.2",
                           "product": "a catalog slug, for a preview of an "
                                      "ebook/PDF product (no course or num)"},
                   runs_in_background=True,
                   max_per_hour=lesson_youtube.PER_DAY_MAX),
            Action(name="academy.publish_tutorial", tier=Tier.ASK,
                   summary="Post a rendered tutorial to YouTube: private upload, "
                           "thumbnail, its series playlist, then public",
                   run=tutorials.publish_tutorial, dry_run=tutorials.publish_tutorial_dry,
                   rollback=tutorials.unpublish_tutorial, vet=tutorials.vet,
                   params={"series": "the tutorial series key: 'channel', 'claude', 'n8n', "
                                     "'project', 'business', 'agents' or 'local'",
                           "episode": "the episode number in that series"},
                   card_only=True, verbose_card=True, runs_in_background=True,
                   # the owner, 2026-10-10: every draft is reviewed in
                   # #atlas-academy-videos before it reaches YouTube.
                   topic_for=lambda _p: tutorials.REVIEW_TOPIC,
                   # Two: the first day's two drafts may be approved together.
                   max_per_hour=2),
            # 2026-10-13 (growth plan task 9): a stale lesson relaunched -- a
            # new upload of today's render, the old video private (never
            # deleted). Tier 2: it changes which video viewers have.
            Action(name="academy.relaunch_tutorial", tier=Tier.ASK,
                   summary="Relaunch a public tutorial rendered before today's "
                           "script: upload the new render as a new video, then "
                           "make the old one private (nothing is deleted)",
                   run=tutorials.relaunch_tutorial,
                   dry_run=tutorials.relaunch_tutorial_dry,
                   rollback=tutorials.unrelaunch_tutorial, vet=tutorials.vet,
                   params={"series": "the tutorial series key: 'channel', 'claude', 'n8n', "
                                     "'project', 'business', 'agents' or 'local'",
                           "episode": "the episode number in that series"},
                   card_only=True, verbose_card=True, runs_in_background=True,
                   topic_for=lambda _p: tutorials.REVIEW_TOPIC,
                   max_per_hour=2),
            Action(name="academy.add_to_series_playlist", tier=Tier.ASK,
                   summary="Add a YouTube video to a tutorial series' playlist "
                           "(made if missing); undone by taking it out again",
                   run=tutorials.add_to_series_playlist,
                   dry_run=tutorials.add_to_series_playlist_dry,
                   rollback=tutorials.undo_series_playlist, vet=tutorials.vet_video,
                   params={"series": "the tutorial series key: 'channel', 'claude', 'n8n', "
                                     "'project', 'business', 'agents' or 'local'",
                           "episode": "the episode number the video is",
                           "video_id": "the 11-character YouTube video id"},
                   max_per_hour=6),
            # the owner, 2026-10-22: every lab / live build also in one "Hands-On
            # Labs & Live Builds" playlist, grouped by course. Tier 1 by his
            # approval policy (promotions are made in code, never at runtime):
            # routine and reversible -- one playlist item on ATLAS's own
            # public lesson (the id on record, never a param); the rollback
            # takes out exactly that item and never deletes the playlist.
            Action(name=lab_playlist.ACTION, tier=Tier.AUTO,
                   summary="Add one of the Academy's own public lab / live-build "
                           "lessons to the \"Hands-On Labs & Live Builds\" playlist "
                           "in course order (made if missing); undone by taking "
                           "that item out",
                   run=lab_playlist.add_to_lab_playlist,
                   dry_run=lab_playlist.add_to_lab_playlist_dry,
                   rollback=lab_playlist.undo_lab_playlist, vet=lab_playlist.vet,
                   params={"series": "the tutorial series key of a lab: 'claude', "
                                     "'n8n', 'project', 'business', 'local' or "
                                     "'security'",
                           "episode": "the episode number in that series"},
                   max_per_hour=lab_playlist.MAX_PER_HOUR),
            # Named take_out, not remove: "remove" is a Tier 3 deny-list verb
            # (destroying data). This deletes nothing: the playlist entry goes,
            # the video stays, and the rollback puts the entry back -- the same
            # reversible edit as api.take_out_of_playlist.
            # Security review of 3108ed1e (2026-10-22): a withdrawn or
            # relaunched lab must not stay in the public lab playlist. Tier 1
            # by the same policy: one playlist item of OUR post of that lesson
            # that is no longer its current post (checked against the record,
            # never an item we cannot map); the video itself is untouched and
            # the rollback adds it back at its sorted place.
            Action(name=lab_playlist.TAKE_OUT_ACTION, tier=Tier.AUTO,
                   summary="Take a withdrawn or superseded post of an Academy lab "
                           "lesson out of the \"Hands-On Labs & Live Builds\" "
                           "playlist; undone by adding it back",
                   run=lab_playlist.take_out_of_lab_playlist,
                   dry_run=lab_playlist.take_out_of_lab_playlist_dry,
                   rollback=lab_playlist.undo_take_out_of_lab_playlist,
                   vet=lab_playlist.vet_take_out,
                   params={"series": "the tutorial series key of a lab: 'claude', "
                                     "'n8n', 'project', 'business', 'local' or "
                                     "'security'",
                           "episode": "the episode number in that series",
                           "video_id": "the 11-character id of our older post of it"},
                   max_per_hour=lab_playlist.TAKE_OUT_MAX_PER_HOUR),
            Action(name="academy.resend_access", tier=Tier.ASK, irreversible=True,
                   summary="Email an ATLAS Academy buyer the link to their "
                           "downloads again (to the address on their Stripe "
                           "checkout)",
                   run=resend_access, dry_run=resend_access_dry, vet=vet,
                   params={"session_id": "the Stripe checkout session id, "
                                         "cs_live_... (from the purchases list)"},
                   max_per_hour=6),
            # Tier AUTO since 2026-09-30 (the owner: "anything that is not urgent,
            # you approve it"): the release is a symlink swap with the previous
            # release kept, so the rollback restores it.
            # A real buyer's words, shown only on the owner's tap (reviews.py).
            Action(name="academy.approve_review", tier=Tier.ASK,
                   summary="Show a verified buyer's review on its product page at "
                           "the next publish of the ATLAS Academy store",
                   run=approve_review, dry_run=approve_review_dry,
                   rollback=approve_review_rollback, vet=vet_review,
                   params={"review_id": "the stored review's id (from its card)"},
                   max_per_hour=20),
            Action(name="academy.publish_shop", tier=Tier.AUTO,
                   summary="Publish the ATLAS Academy storefront at "
                           "agency.example.com from the checked-in catalog",
                   run=publish_shop, dry_run=publish_shop_dry,
                   rollback=publish_shop_rollback, params={}, max_per_hour=3),
            # 2026-10-27 (plan 4.3): ATLAS runs on the Mac; the storefront is
            # the one public surface, static files on the store VM. Tier 2:
            # the owner approves every push of the `shop-build` output; the
            # rollback re-points the release that was live before.
            Action(name="academy.publish_shop_remote", tier=Tier.ASK,
                   summary="Push the built ATLAS Academy storefront (shop-build) "
                           "to the store VM and make it the live release there",
                   run=publish_shop_remote, dry_run=publish_shop_remote_dry,
                   rollback=publish_shop_remote_rollback, params={},
                   max_per_hour=3),
        ) + (support.actions() + content_plan.actions() + growth.actions()
             + subscribers.actions())

    async def signals(self):
        return ()


MODULE = AcademyModule()
