"""clips -- official brand clipping campaigns, cut from their own footage.

the owner joined Clipping Culture on Whop (Content Rewards): brands pay per
1,000 views for short clips made ONLY from their official footage, under
strict rules. This is that lane, beside reels and separate from it. Plan:
"ATLAS Vault/Plans/Clipping lane.md". Posting accounts: Instagram
@example_handle, TikTok @example_handle (through Postiz) and the YouTube
`clips` channel (Shorts).
Content rule: gaming, trends, business and streamer clips only; no dating,
nothing edgy -- `campaigns.refusal`. Permission rule: a campaign names who
allowed the clipping (`permission_source`: a Whop program or a written OK)
or nothing of it is registered, activated or cut. There is no way here to
fetch a creator's video: footage is only ever a registered file of a
campaign that names its permission (no URL ripping, no reposting).

The flow, and the tier of each step:

  1. **Campaigns** (`clip_campaigns`), seeded from each public rules doc,
     parsed and reviewed by hand. The doc text is untrusted data.
     `clips.set_campaign_status` (Tier 2) pauses, ends or activates one;
     activation passes the content rule, names a permission source and
     needs reviewed rules. Each campaign carries its format `preset`
     (blur | crop | split) and `max_accounts` per platform.
  2. **Footage**: the owner (or a Mac-side step) downloads the official folder
     into `data/clips/<slug>/source/`; ATLAS never scrapes frame.io,
     MediaSilo, WeTransfer or Drive. `clips.register_sources` (Tier 1)
     records each file's sha256 against the campaign.
  3. **Cut** -- `clips.cut` (Tier 1): transcribe, let the model pick 20-60 s
     windows (timestamps only, from a closed schema), cut 1080x1920 in the
     campaign's preset with the original audio and burned captions -- and,
     for a campaign whose rules require them (`brand_assets`: Irubyana's
     "include brand logo", "twitch banner"), its own logo.png (bottom-right)
     and banner.png (a top strip) from `data/clips/<slug>/brand/`, placed
     by the owner and never fetched; without them nothing is cut and the intake
     raises a card saying which file to put where -- into
     `data/clips/<slug>/out/` -- one row per platform the lane posts to and
     the campaign pays for -- or, when its rules forbid the same video on
     several accounts (both live ones), ONE row, for the platform paying
     the most. Rollback moves its own files to `.trash/`, never deletes.
  4. **Post** -- on its own (the owner, 2026-09-30: "the same way the AI reels
     lane does it", no card per post). `clips.publish_clip` (Tier 1) posts
     through the existing `instagram.publish` to the `clips` account
     (@example_handle, content kind `campaign_clip`, the #Ad rule); a
     TikTok row through `clips.publish_clip_tiktok` (Tier 1): Postiz, the
     reels lane's path, to @example_handle only; a YouTube row of a campaign
     that pays on YouTube through `clips.publish_clip_youtube_postiz`
     (Tier 1): Postiz, to the clips channel AtlasClips (@example_handle) that
     `POSTIZ_YOUTUBE_CLIPS_INTEGRATION` names, as a Short (9:16, at most
     60 s). Each is gated in code (`posting.py`, "automatic"): the campaign
     active with its permission named and reviewed rules, the caption in
     the campaign's layout with its `#Ad` line, the file a 1080x1920 lane
     cut with sound inside the campaign's length band (ffprobe, now), the
     account configured and within the campaign's account and one-platform
     rules, then the holds -- `CLIPS_AUTOPUBLISH=off` (on by default) and
     `CLIPS_DAILY_CAP` posts per platform per UTC day. The intake runs them
     through the executor; a clip that fails any of it is never skipped in
     silence: it is a Tier 2 card carrying the reason, whose approval
     answers a hold and runs every check again. Each leg is claimed once
     (`effect_claims`) before it posts, so nothing posts twice. When a
     campaign's reviewed rules allow the same clip on more than one
     account (`cross_post_allowed`), the cut makes a row per platform
     from the one file and each posts as its own leg -- Instagram AND
     TikTok, a link per platform for Whop; a campaign whose rules forbid
     it (both live ones do) stays on one platform per clip, the
     highest-paying, ties taken in turn. While an account is not
     configured the card is `clips.hand_off` instead: the file and exact
     caption, for the owner to post by hand, his URL coming back through
     `clips.record_link` (his word only). A YouTube row may still post
     through `clips.publish_clip_youtube` (Tier 2, one card per clip): the
     youtube module's Data API upload to its `clips` channel, then public
     on that same approval, when Postiz does not reach the channel.
  4b. **Draft** (Irubyana, 2026-10-01: "submit a draft for review; drafts
     need brand approval before you post"): a clip of a campaign with
     `approval_before_post` is cut as `draft_pending`, not `made`. The
     agent on the owner's Mac reads it in `GET /clips/whop/pending` (`drafts`:
     a signed, temporary download link and the file's size against the
     campaign's `max_file_mb` -- the cut's encode is sized under it, and a
     file over it is never offered), uploads it on Whop and records that
     (`POST .../draft_submitted` -> `clips.record_draft_submitted`, Tier
     1, `whop.py`); the brand's answer comes back the same way
     (`POST .../draft_decided` -> `clips.record_draft_decision`): approved
     -> `draft_approved`, which step 4 posts like any waiting clip;
     rejected -> `withdrawn`, the note said in the clips channel. Nothing
     of such a campaign posts before `draft_approved`.
  5. **Claim**: the agent on the owner's Mac submits each post on Whop in his own
     Chrome (the owner, 2026-09-29) -- ATLAS never submits there itself --
     reading `GET /clips/whop/pending` and recording each through
     `POST /clips/whop/{id}/submitted` (`clips.record_whop_submission`,
     Tier 1, `whop.py`). Only a post URL from the platform's own response or
     the owner's `clips.record_link`, for a campaign with a permission source and
     a `whop_url`, is offered. A clip it is offered gets the Tier 2
     `clips.submit_to_whop` card only as the fallback, 48 h after it posted
     unsubmitted; any other posted clip gets that card at once.

**Unattended** (`kernel.clip_intake`, every `CLIPS_INTAKE_MIN` minutes):
for each active campaign with a permission source, new files in its
`source/` folder are registered and one waiting source is cut, both
through the executor, within `CLIPS_DAILY_CAP` clips a day; and one
waiting clip per platform is posted through the executor (the three Tier
1 legs above) or held as a card with the reason. Every automatic post is
a receipt in the clips channel. The hand-off and Whop cards follow from
the signals pass on their own: the owner only drops footage and answers cards.

**Links** (`links.py`, every `CLIPS_LINKS_MIN` minutes): a clip Postiz
posted without its URL gets it -- source the platform's, so step 5 offers
it -- once Postiz's post listing reports the platform's own post URL for
it. Read only on Postiz; never a URL built from an id.

`clips.status` reports each campaign's sources, clips, cards, posts and
budget; `clips.legs` which posting accounts ATLAS can reach;
`clips.clips_earnings` the counts posted, submitted and awaiting (no money
figure: ATLAS does not read Whop).
"""
from __future__ import annotations

import logging
from datetime import timedelta

from ...kernel.contracts import Action, Check, Severity, Tier
from . import campaigns, cut, posting, promo, sources, status, whop

log = logging.getLogger("atlas.clips")


class ClipsModule:
    name = "clips"
    title = "Clips (brand clipping campaigns)"
    requires_env: tuple[str, ...] = ()
    optional_env = ("GROQ_API_KEY", "CLIPS_DATA_DIR", "CLIPS_DAILY_CAP",
                    "CLIPS_INTAKE_MIN", "CLIPS_LINKS_MIN", "CLIPS_AUTOPUBLISH",
                    "POSTIZ_TIKTOK_CLIPS_INTEGRATION",
                    "POSTIZ_YOUTUBE_CLIPS_INTEGRATION")

    async def setup(self) -> None:
        try:
            refused = await campaigns.ensure_seeds()
        except Exception as exc:                        # noqa: BLE001
            log.warning("clip campaign seeds not applied: %s", exc)
            return
        for slug, why in refused.items():
            log.error("clip campaign %s not seeded: %s", slug, why)
        try:
            resumed = await campaigns.keep_active()
        except Exception as exc:                        # noqa: BLE001
            log.warning("clip campaign pauses not checked: %s", exc)
            return
        for slug in resumed:
            log.warning("clip campaign %s was paused; resumed (never paused: "
                        "the owner, 2026-10-01 and 2026-10-05)", slug)

    async def teardown(self) -> None: ...

    def checks(self):
        return (
            Check("status", status.check_status, timedelta(hours=6), Severity.INFO,
                  "clip campaigns: sources, clips made and posted, budget left",
                  baseline_sigma=None),
            Check("legs", status.check_legs, timedelta(hours=6), Severity.INFO,
                  "which clips accounts ATLAS can post to (configuration only)",
                  baseline_sigma=None),
            Check("clips_earnings", whop.check_earnings, timedelta(hours=6),
                  Severity.INFO,
                  "clips posted, submitted to Whop and awaiting: counts only",
                  baseline_sigma=None),
            Check("clips_posted", whop.check_posted, timedelta(hours=6),
                  Severity.INFO, "clips posted so far, as a number of its own",
                  baseline_sigma=None),
        )

    def actions(self):
        return (
            Action(
                name="clips.register_sources",
                tier=Tier.AUTO,     # hashes files the owner placed; writes rows only
                summary="Register the official footage the owner placed in a clip "
                        "campaign's source folder (sha256 per file)",
                run=sources.register_sources, dry_run=sources.register_sources_dry,
                rollback=sources.register_sources_rollback,
                params={"campaign": "campaign slug, e.g. cod-mw4-warzone-operator",
                        "only_new": "optional: true to skip a file on file in any "
                                    "state, so a rolled-back registration stays "
                                    "back (the intake always sets it)"},
                max_per_hour=6,
            ),
            Action(
                name="clips.cut",
                tier=Tier.AUTO,     # writes files under data/clips; posts nothing
                summary="Cut one registered source into 9:16 campaign clips with "
                        "burned captions, ready to post",
                run=cut.cut, dry_run=cut.cut_dry, rollback=cut.cut_rollback,
                params={"campaign": "campaign slug",
                        "source_id": "id of a clip_sources row of that campaign",
                        "caption_key": "optional: which approved caption sample",
                        "hashtags": "optional: up to 3 of the campaign's hashtags",
                        "max_clips": "optional: at most this many clips, 1-3 "
                                     "(default 3)"},
                max_per_hour=4,
            ),
            Action(
                name="clips.set_campaign_status",
                tier=Tier.ASK,      # decides what ATLAS clips for money
                summary="Activate, pause or end a clip campaign",
                run=campaigns.set_status, dry_run=campaigns.set_status_dry,
                rollback=campaigns.set_status_rollback,
                params={"campaign": "campaign slug",
                        "status": "active | paused | ended"},
                vet=campaigns.vet_status,
                max_per_hour=6,
            ),
            Action(
                name="clips.record_budget",
                tier=Tier.AUTO,     # one number on one row, as the owner said it
                summary="Record the budget left on a clip campaign, as the owner "
                        "read it on Whop",
                run=campaigns.record_budget, dry_run=campaigns.record_budget_dry,
                rollback=campaigns.record_budget_rollback,
                params={"campaign": "campaign slug",
                        "usd": "budget left in US dollars, as the owner read it"},
                # A number ATLAS reports must be one the owner gave, never one a
                # model read somewhere (invariant 6).
                principal_only=True,
                max_per_hour=10,
            ),
            Action(
                name="clips.hand_off",
                tier=Tier.ASK,
                summary="Clips ready to post by hand on @example_handle, "
                        "@example_handle or the clips YouTube channel: files and "
                        "captions for the owner",
                run=posting.hand_off, dry_run=posting.hand_off_dry,
                rollback=posting.hand_off_rollback,
                params={"campaign": "campaign slug",
                        "clip_ids": "comma-separated clip_outputs ids"},
                card_only=True, verbose_card=True,
                max_per_hour=10,
            ),
            Action(
                name="clips.publish_clip",
                tier=Tier.AUTO,     # the owner, 2026-09-30: clips post themselves.
                                    # Only past posting._publish_check (the
                                    # campaign, the #Ad caption, a 1080x1920
                                    # probe with sound, the account) and its
                                    # holds (CLIPS_AUTOPUBLISH, CLIPS_DAILY_CAP);
                                    # the rollback deletes the post
                summary="Post one campaign clip to Instagram @example_handle, "
                        "once it passes every automatic check",
                run=posting.publish_clip, dry_run=posting.publish_clip_dry,
                rollback=posting.unpublish_clip,
                params={"clip_id": "id of a clip_outputs row waiting to post"},
                card_only=True, verbose_card=True,
                # Instagram processes a Reel before it can publish: minutes.
                runs_in_background=True,
                max_per_hour=2,
            ),
            Action(
                name="clips.publish_clip_youtube",
                tier=Tier.ASK,      # a public post under the owner's name; per clip
                summary="Post one campaign clip as a YouTube Short on the clips "
                        "channel: upload, then public",
                run=posting.publish_clip_youtube,
                dry_run=posting.publish_clip_youtube_dry,
                rollback=posting.unpublish_clip_youtube,
                params={"clip_id": "id of a YouTube clip_outputs row waiting to post"},
                card_only=True, verbose_card=True,
                # A resumable upload, then a publish and a read-back.
                runs_in_background=True,
                max_per_hour=2,
            ),
            Action(
                name="clips.publish_clip_tiktok",
                tier=Tier.AUTO,     # the owner, 2026-09-30: the same gates and holds
                                    # (posting._tiktok_check); the rollback
                                    # deletes the Postiz post while queued
                summary="Post one campaign clip to TikTok @example_handle through "
                        "Postiz, once it passes every automatic check",
                run=posting.publish_clip_tiktok,
                dry_run=posting.publish_clip_tiktok_dry,
                rollback=posting.unpublish_clip_tiktok,
                params={"clip_id": "id of a TikTok clip_outputs row waiting to post"},
                card_only=True, verbose_card=True,
                # An upload to Postiz, then the post.
                runs_in_background=True,
                max_per_hour=2,
            ),
            Action(
                name="clips.publish_clip_youtube_postiz",
                tier=Tier.AUTO,     # the owner, 2026-09-30: the same gates and holds
                                    # plus the Shorts rule
                                    # (posting._youtube_postiz_check); the
                                    # rollback deletes the Postiz post while
                                    # queued
                summary="Post one campaign clip as a YouTube Short on the clips "
                        "channel AtlasClips through Postiz, once it passes every "
                        "automatic check",
                run=posting.publish_clip_youtube_postiz,
                dry_run=posting.publish_clip_youtube_postiz_dry,
                rollback=posting.unpublish_clip_youtube_postiz,
                params={"clip_id": "id of a YouTube clip_outputs row waiting to post"},
                card_only=True, verbose_card=True,
                # An upload to Postiz, then the post.
                runs_in_background=True,
                max_per_hour=2,
            ),
            Action(
                name="clips.record_link",
                tier=Tier.AUTO,     # a URL on a row; nothing leaves ATLAS
                summary="Record the URL a clip was posted at",
                run=posting.record_link, dry_run=posting.record_link_dry,
                rollback=posting.record_link_rollback,
                params={"clip_id": "id of a clip_outputs row",
                        "post_url": "the post's public URL"},
                # It feeds a payout claim: only the owner's own word.
                principal_only=True,
                max_per_hour=20,
            ),
            Action(
                name="clips.record_whop_submission",
                tier=Tier.AUTO,     # a status on ATLAS's own row; nothing
                                    # leaves ATLAS. Never recorded twice.
                summary="Record that the agent on the owner's Mac submitted a posted "
                        "clip to its campaign on Whop",
                run=whop.record_submission, dry_run=whop.record_submission_dry,
                rollback=whop.record_submission_rollback,
                params={"clip_id": "id of a posted clip_outputs row",
                        "post_url": "the URL it was posted at"},
                # Called by POST /clips/whop/{id}/submitted only (through
                # kernel.unattended); a model never records a payout claim.
                card_only=True,
                max_per_hour=20,
            ),
            Action(
                name="clips.record_draft_submitted",
                tier=Tier.AUTO,     # a status on ATLAS's own row; nothing
                                    # leaves ATLAS. Never recorded twice.
                summary="Record that the agent on the owner's Mac uploaded a clip as "
                        "a draft on Whop, for the brand's approval",
                run=whop.record_draft_submitted,
                dry_run=whop.record_draft_submitted_dry,
                rollback=whop.record_draft_submitted_rollback,
                params={"clip_id": "id of a draft_pending clip_outputs row"},
                # POST /clips/whop/{id}/draft_submitted only (kernel.unattended).
                card_only=True,
                max_per_hour=20,
            ),
            Action(
                name="clips.record_draft_decision",
                tier=Tier.AUTO,     # a status on ATLAS's own row, as the brand
                                    # decided it on Whop; a rejected draft is
                                    # withdrawn, never posted. Never twice.
                summary="Record the brand's decision on a clip's Whop draft: "
                        "approved (it posts on its own) or rejected (withdrawn)",
                run=whop.record_draft_decision,
                dry_run=whop.record_draft_decision_dry,
                rollback=whop.record_draft_decision_rollback,
                params={"clip_id": "id of a draft_submitted clip_outputs row",
                        "approved": "true or false, as the brand decided on Whop",
                        "note": "optional: the brand's note, verbatim"},
                # POST /clips/whop/{id}/draft_decided only (kernel.unattended).
                card_only=True,
                max_per_hour=20,
            ),
            Action(
                name="clips.submit_to_whop",
                tier=Tier.AUTO,     # the owner, 2026-10-06: the clips lane runs
                                    # with no card, submissions included
                                    # (was Tier 2 for his payout account); at
                                    # once for a clip the Mac agent never
                                    # sees, else the fallback 48 h after it
                                    # posted. The rollback withdraws nothing
                                    # on Whop; it marks the row unsubmitted.
                summary="Submit a posted clip's URL to its campaign on Whop",
                run=posting.submit_to_whop, dry_run=posting.submit_to_whop_dry,
                rollback=posting.submit_to_whop_rollback,
                params={"clip_id": "id of a posted clip_outputs row",
                        "post_url": "the URL it was posted at"},
                card_only=True, verbose_card=True,
                max_per_hour=20,
            ),
            Action(
                name="clips.publish_promo",
                tier=Tier.ASK,      # the owner, 2026-10-05 ("post on clips too"):
                                    # the Academy store's own promo reel on
                                    # the clips accounts, by his tap only.
                                    # The campaign-clip doorways stay as
                                    # they are (clips/promo.py).
                summary="Post an Academy store promo render to the clips "
                        "accounts: Instagram, TikTok and a YouTube Short",
                run=promo.publish_promo, dry_run=promo.publish_promo_dry,
                rollback=promo.unpublish_promo, vet=promo.vet,
                params={"media_id": "id of a finished content_media render",
                        "caption": "the caption, ending with the AI label",
                        "youtube_caption": "optional: the YouTube Short's title (first "
                                           "line) and description, ending with the "
                                           "AI label",
                        "legs": "optional: a subset of instagram,tiktok,youtube "
                                "to post to (a leg refused once, re-run alone)"},
                card_only=True, verbose_card=True,
                runs_in_background=True,
                max_per_hour=1,
            ),
            Action(
                name="clips.autopublish_promo",
                tier=Tier.AUTO,     # the owner, 2026-10-06: a daily Academy reel on
                                    # the clips accounts posts itself. Only
                                    # past promo.autopublish_check (the AI
                                    # label, the brand guard, the daily cap,
                                    # a probed 1080x1920 file with sound);
                                    # anything held is a clips.publish_promo
                                    # card with the reason first.
                summary="Post the day's Academy reel to the clips accounts by itself, "
                        "once it passes every automatic check",
                run=promo.autopublish_promo, dry_run=promo.autopublish_promo_dry,
                rollback=promo.unpublish_promo, vet=promo.vet,
                params={"media_id": "id of a finished content_media render",
                        "caption": "the caption, ending with the AI label",
                        "youtube_caption": "optional: the YouTube Short's title (first "
                                           "line) and description, ending with the "
                                           "AI label",
                        "slug": "the Academy product the reel is about",
                        "legs": "optional: a subset of tiktok,youtube to post to "
                                "(default both); the daily reel sends 'youtube' "
                                "when its product was posted in the last 48 h"},
                runs_in_background=True,
                max_per_hour=1,
            ),
        )

    async def signals(self):
        try:
            return await posting.signals()
        except Exception as exc:                        # noqa: BLE001
            log.warning("clips signals not read: %s", exc)
            return ()

    def brief(self) -> str:
        return "Clips: brand campaigns, cut from official footage"


MODULE = ClipsModule()
