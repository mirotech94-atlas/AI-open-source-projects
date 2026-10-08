"""ATLAS Academy: Miro's own store at https://shop.mapfront.io/.

Replaces the Shopify store. Four products (Build a Safe AI Operator, 30
Business Lessons, the Claude + Obsidian Vault Kit, AI for Every Age) and an
"All four" bundle, sold through Stripe Payment Links on the ATLAS Academy
Stripe account.

* `catalog.json` is the one source for what is sold. A price, a payment
  link or a refund term Miro hasn't set stays null or empty, and the item
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
  their link the same page, to the address Stripe holds, on Miro's tap.

Money comes in here. Nothing refunds, pays out or changes a price: those
stay Tier 3 or Miro's own, in the Stripe dashboard.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

from ...kernel.contracts import Action, Check, Health, Observation, Severity, Tier
from . import delivery, publish, reel
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
        # Miro, 2026-10-06 (store ops, no card): a deploy that changed what
        # the store would render republishes it by itself, through the
        # executor (academy.publish_shop is Tier 1). Off the loop; a failure
        # is logged and the next deploy tries again.
        from ...kernel import background
        background.spawn(publish.republish_if_changed(), "the store's self-republish")
    async def teardown(self) -> None: ...

    def checks(self):
        return (Check("delivery", check_delivery, timedelta(hours=6), Severity.NORMAL,
                      "every item on sale can be checked with Stripe and "
                      "downloaded (key set, files on disk)", baseline_sigma=None),)

    def actions(self):
        return (
            Action(name="academy.auto_approve_review", tier=Tier.AUTO,
                   # Miro, 2026-10-06: buyer reviews show themselves when they
                   # pass reviews.clean_refusal (no link, no contact, plain
                   # opinion); anything else stays his card. Same rollback.
                   summary="Show a clean verified-buyer review on its product page",
                   run=auto_approve_review, dry_run=auto_approve_review_dry,
                   rollback=approve_review_rollback, vet=vet_review,
                   params={"review_id": "the stored review's id"},
                   max_per_hour=20),
            Action(name="academy.render_promo_reel", tier=Tier.AUTO,
                   # Miro, 2026-10-06: a daily reel about the Academy for the
                   # clips accounts, made here from the catalog (reel.py). It
                   # writes a file and a content_media row; posting is
                   # clips.autopublish_promo's, gated there.
                   summary="Render one product's Academy reel from the catalog "
                           "(its cover, its counted facts, the lessons' voice)",
                   run=reel.render_promo_reel, dry_run=reel.render_promo_reel_dry,
                   rollback=reel.unrender_promo_reel,
                   params={"slug": "the product's catalog slug"},
                   runs_in_background=True, max_per_hour=2),
            Action(name="academy.resend_access", tier=Tier.ASK, irreversible=True,
                   summary="Email an ATLAS Academy buyer the link to their "
                           "downloads again (to the address on their Stripe "
                           "checkout)",
                   run=resend_access, dry_run=resend_access_dry, vet=vet,
                   params={"session_id": "the Stripe checkout session id, "
                                         "cs_live_... (from the purchases list)"},
                   max_per_hour=6),
            # Tier AUTO since 2026-09-30 (Miro: "anything that is not urgent,
            # you approve it"): the release is a symlink swap with the previous
            # release kept, so the rollback restores it.
            # A real buyer's words, shown only on Miro's tap (reviews.py).
            Action(name="academy.approve_review", tier=Tier.ASK,
                   summary="Show a verified buyer's review on its product page at "
                           "the next publish of the ATLAS Academy store",
                   run=approve_review, dry_run=approve_review_dry,
                   rollback=approve_review_rollback, vet=vet_review,
                   params={"review_id": "the stored review's id (from its card)"},
                   max_per_hour=20),
            Action(name="academy.publish_shop", tier=Tier.AUTO,
                   summary="Publish the ATLAS Academy storefront at "
                           "shop.mapfront.io from the checked-in catalog",
                   run=publish_shop, dry_run=publish_shop_dry,
                   rollback=publish_shop_rollback, params={}, max_per_hour=3),
        )

    async def signals(self):
        return ()


MODULE = AcademyModule()
