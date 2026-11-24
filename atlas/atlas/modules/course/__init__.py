"""course -- Miro's own products, marketed organically, one approved post at a time.

The products: the closed catalog in `catalog.py` -- "Build a Safe AI Operator",
"Everyday AI for Adults", "Safe and Simple AI 60+", the AI for Every Age
bundle and the free Family Scam-Safety Kit (the email lead magnet) -- sold on
his Shopify store (atlasacademy-ai.myshopify.com). A product not in the
catalog is never marketed. The marketing agent
(Nora) proposes a post as a measured experiment; the growth agent (Leo) can
draft it again; Miro approves every post on its own card. Organic only:
nothing here spends, boosts or buys, and no such action exists.

What it watches (reads only):
  * `price` / `price_<product>` -- each catalog product's price and
                          availability, read live from its public product
                          JSON with the time it was read. Unreadable is
                          UNKNOWN; readable but not sellable on its catalog
                          terms is DEGRADED.
  * `posts_this_week`  -- course posts out this New York week against the cap.

What it may do:
  * `course.propose` (Tier 1) -- an experiment row and a drafted post.
  * `course.draft_post`   (Tier 1) -- draft a not-yet-carded post again.
  * `course.publish_post` (Tier 2, card only, irreversible) -- post one
    approved text through Postiz to a non-clips text channel.
  * `course.hand_off`     (Tier 2, card only) -- the LinkedIn post or the
    product's one Shopify blog article (SEO title, meta description, 700-1,200
    words of its own lesson excerpts), whole, for Miro to post by hand.

How Nora picks what to post: `course_catalog` (the products, their channels,
which are due by rotation, and `playbook.md` -- organic tactics distilled
from two Shopify articles, handed to her as quoted data).

The honesty rules (`honesty.py`), the channels (`channels.py`), the price
(`price.py`), the words (`compose.py`), the caps (`posts.py`) and the
measurement (`measure.py`, read by `kernel.marketing.experiments`) each say
what they refuse. Endpoints assumed: the store's public `<product>.json`
(no credential) and the Postiz Public API (POSTIZ_API_KEY), whose text-post
shape is taken from its docs and unverified live.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from ...kernel.contracts import (
    Action,
    Check,
    Health,
    Observation,
    Severity,
    Signal,
    Tier,
)
from . import catalog, channels, data, honesty, posts
from . import price as live
from .catalog import OPERATOR

log = logging.getLogger("atlas.course")

LESSON_IDS = "; ".join(
    f"{k}: {', '.join(x['id'] for x in data.lessons(k))}" for k in catalog.KEYS)


def check_name(product: str) -> str:
    """The Operator keeps its original check name, `price`."""
    return "price" if product == OPERATOR else f"price_{product}"


async def check_price(product: str = OPERATOR) -> Observation:
    name = check_name(product)
    short = catalog.CATALOG[product].short
    read = await live.read_price(product=product)
    if not read.read or read.amount is None:
        return Observation(name, Health.UNKNOWN,
                           detail=f"the price of {short} could not be read: "
                                  f"{read.why}; tried {read.stamp()}")
    if read.available is None:
        return Observation(name, Health.UNKNOWN,
                           detail=f"whether {short} can be bought could not be "
                                  f"read: {read.why or 'no availability'}; tried "
                                  f"{read.stamp()}")
    detail = (f"{short}: {read.words()} on the store, "
              f"{'available' if read.available else 'NOT available'}, read "
              f"{read.stamp()}")
    why = live.sellable_refusal(read, product)
    return Observation(
        name, Health.OK if why is None else Health.DEGRADED, float(read.amount),
        "USD", detail if why is None else f"{detail} -- not marketed: {why}",
        evidence=read.record(), at=read.read_at)


def _price_check(product: str):
    async def run() -> Observation:
        return await check_price(product)
    run.__name__ = f"check_{check_name(product)}"
    return run


async def check_posts_this_week() -> Observation:
    try:
        week = await posts.week()
    except Exception as exc:  # noqa: BLE001
        return Observation("posts_this_week", Health.UNKNOWN,
                           detail=f"could not count course posts: {exc}")
    return Observation(
        "posts_this_week", Health.OK, float(week["out"]), "posts",
        f"{week['out']} course post(s) out and {week['proposed']} proposed this "
        f"week (week of {week['week_of']}), cap {week['cap']}")


class CourseModule:
    name = "course"
    title = "Course marketing (the ATLAS Academy catalog)"
    requires_env: tuple[str, ...] = ()
    optional_env: tuple[str, ...] = ("POSTIZ_API_KEY", "POSTHOG_PERSONAL_KEY",
                                     "COURSE_POSTS_PER_WEEK")
    setup_note = ""

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        prices = tuple(
            Check(check_name(k), _price_check(k), timedelta(hours=6), Severity.INFO,
                  f"the price and availability of {p.short}, read live from the "
                  f"store", baseline_sigma=None)
            for k, p in catalog.CATALOG.items())
        return (
            *prices,
            Check("posts_this_week", check_posts_this_week, timedelta(hours=6),
                  Severity.INFO, "course posts out this week against the cap",
                  baseline_sigma=None),
        )

    def actions(self):
        channel_doc = (f"{' | '.join(channels.HANDOFF_CHANNELS)} | "
                       f"postiz:<integration id> (course_channels lists them)")
        return (
            Action(
                name="course.propose",
                tier=Tier.AUTO,     # two of ATLAS's own rows; nothing leaves it
                summary="Propose one post for one catalog product as a measured "
                        "experiment and draft it",
                run=posts.propose, dry_run=posts.propose_dry,
                rollback=posts.propose_rollback,
                params={
                    "product": f"one of: {', '.join(catalog.KEYS)} (the catalog; "
                               f"course_catalog says which are due)",
                    "channel": channel_doc,
                    "lesson_id": f"that product's lesson, one of -- {LESSON_IDS}",
                    "language": (f"one of: {', '.join(honesty.LANGUAGES)}; only "
                                 f"{OPERATOR} is posted in other than en"),
                    "hypothesis": "what this post would show, falsifiable by "
                                  "store visits tagged with its UTM campaign",
                    "variant": "optional: the variant's name in the playbook",
                },
                max_per_hour=8,
            ),
            Action(
                name="course.draft_post",
                tier=Tier.AUTO,     # rewrites ATLAS's own draft row
                summary="Draft a course post again from a fresh price read",
                run=posts.draft_post, dry_run=posts.draft_post_dry,
                rollback=posts.draft_post_rollback,
                params={"post_id": "course_posts.id, not yet on a card"},
                max_per_hour=8,
            ),
            Action(
                name="course.publish_post",
                # Tier 2 every time: a public post under Miro's name. There
                # is no rollback, because taking a post down is deleting it.
                tier=Tier.ASK,
                summary="Post one course post to a Postiz channel",
                run=posts.publish_post, dry_run=posts.publish_post_dry,
                irreversible=True,
                params={"post_id": "course_posts.id",
                        "text": "the exact text the card shows"},
                max_per_hour=4, verbose_card=True, card_only=True,
                card_detail=posts.card_detail, vet=posts.vet,
            ),
            Action(
                name="course.hand_off",
                # Tier 2: the card IS the review of text he will post under
                # his own name. The rollback only reopens ATLAS's row.
                tier=Tier.ASK,
                summary="Hand Miro a course post to publish by hand (LinkedIn "
                        "or the Shopify blog)",
                run=posts.hand_off, dry_run=posts.hand_off_dry,
                rollback=posts.hand_off_rollback,
                params={"post_id": "course_posts.id",
                        "text": "the exact text the card shows"},
                max_per_hour=4, verbose_card=True, card_only=True,
                card_detail=posts.card_detail, vet=posts.vet,
            ),
        )

    async def signals(self):
        """One card per drafted post. The title carries the post id, so the
        kernel's dedup key is per post; a post that already has a card of
        either kind is never carded again."""
        try:
            waiting = await posts.waiting_for_card()
        except Exception:
            log.exception("could not read course posts waiting for a card")
            return ()
        out = []
        for post in waiting:
            target = channels.static(post["channel"])
            name = target.name if target else f"{post['channel']} (Postiz)"
            action = posts.HAND_OFF if target else posts.PUBLISH
            lang = honesty.LANGUAGES.get(post["language"], post["language"])
            found = catalog.get(str(post.get("product") or OPERATOR))
            short = found.short if found else str(post.get("product"))
            out.append(Signal(
                title=(f"Course post #{post['id']}: {short}, lesson "
                       f"{post['lesson_id']}, {lang}, {name}"),
                body=posts.reason_for(post, name),
                severity=Severity.NORMAL,
                suggests=action,
                params={"post_id": int(post["id"]), "text": post["body"]},
            ))
        return tuple(out)

    def brief(self) -> str:
        names = ", ".join(p.short for p in catalog.CATALOG.values())
        return (f"Course: organic posts for the catalog ({names}), rotated "
                f"fairly, each on its own card, measured by UTM campaign")


MODULE = CourseModule()
