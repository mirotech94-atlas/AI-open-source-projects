"""The course posts: proposed, drafted, carded one by one, capped, measured.

  1. **Propose** (`course.propose`, Tier 1, the marketing agent's): one
     `marketing_experiments` row (business `course`, its id is the post's
     `utm_campaign`) and one `course_posts` row for one catalog product, one
     channel, one lesson, one language. The product's price and availability
     are read live now; the text is composed and drafted at once
     (`compose.compose`). A product whose store read fails, that is not
     available, or whose read breaks its catalog terms is not drafted, and
     the row says why. Writes only ATLAS's own rows.
  2. **Draft again** (`course.draft_post`, Tier 1, the growth agent's): a post
     that could not be drafted (a translation refused, the model unreachable)
     or that has not reached a card yet is drafted again from a fresh price.
  3. **Card**: the module's `signals()` raise exactly one card per drafted
     post -- `course.publish_post` for a Postiz channel, `course.hand_off`
     for LinkedIn and the Shopify blog. Both are Tier 2 and `card_only`: no
     model can fill their parameters, and the text on the card is the text
     that is sent, checked byte for byte.
  4. **Publish or hand off**, on his tap: the honesty rules again, the price
     read again (a card whose price no longer matches the store is refused),
     the caps claimed under one advisory lock, then the post -- and the
     experiment's measurement clock starts (`MEASURE_AFTER`, seven days: a
     course sale lags a post).

**Caps.** `COURSE_POSTS_PER_WEEK` (default 7) across every product, channel
and language, and at most one post per channel per day, both by the New York
calendar. **Rotation:** within that cap every catalog product gets its fair
share -- a product may be proposed only while its count this week is the
lowest among the products the store can sell right now (`rotation_refusal`),
so with the default cap of 7 and five products each gets one before any gets
a second. A product that cannot be sold (unreadable, unavailable, off its
catalog terms) is refused before any row is written and is left out of the
floor. A product whose allowed channels are all used today still holds the
floor until tomorrow. **One blog draft
per product:** a product with a Shopify blog post that was not dropped or
failed gets no second one. A Spanish, a Portuguese and an Arabic variant of one lesson are
three posts. The weekly count at proposal counts every post not rolled back
(a declined card still used its place: his no is not re-asked); at publish
it counts what actually went out or was handed off.

Nothing here spends, and nothing here can: no ads, no boosts, no paid
promotion exist as actions. `utm_medium` is always `organic`.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ...kernel.contracts import ActionResult
from ...kernel.state import execute_sql, fetch_all, fetch_one, fetch_one_locked
from . import catalog, channels, compose, data, honesty
from . import price as live
from .catalog import OPERATOR

log = logging.getLogger("atlas.course")

ZONE = ZoneInfo("America/New_York")
METRIC = "store_visits"
MEASURE_AFTER = timedelta(days=7)
CLAIM_LOCK = "atlas.course_posts.claim"
OUT = ("publishing", "published", "handed_off")    # counted by the publish caps
PUBLISH = "course.publish_post"
HAND_OFF = "course.hand_off"


# ------------------------------------------------------------------ calendar

def local_day(now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(ZONE).date()


def day_start(day: date) -> datetime:
    return datetime.combine(day, time(0), tzinfo=ZONE)


def week_start(day: date) -> datetime:
    return day_start(day - timedelta(days=day.weekday()))


def sha(text: str) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()


# ------------------------------------------------------------------ checks shared

def _param_refusal(product: str, channel: str, lesson_id: str, language: str
                   ) -> str | None:
    """Pure: the parameters, before anything is read."""
    if (why := catalog.product_refusal(product)) is not None:
        return why
    found = catalog.CATALOG[product]
    if (why := channels.shape_refusal(str(channel or ""))) is not None:
        return why
    if data.lesson(str(lesson_id or ""), product) is None:
        ids = ", ".join(x["id"] for x in data.lessons(product))
        return f"no lesson {lesson_id!r} with a hook in {found.short}; one of {ids}"
    if language not in honesty.LANGUAGES:
        return (f"unknown language {language!r}; one of "
                f"{', '.join(honesty.LANGUAGES)}")
    if language not in found.languages:
        return (f"{found.short} is posted in {', '.join(found.languages)} only, "
                f"not {honesty.LANGUAGES[language]}")
    if channel == channels.SHOPIFY_BLOG and language != "en":
        return "the Shopify blog article is drafted in English only"
    if (static := channels.static(channel)) is not None and (
            static.provider.platform not in found.platforms):
        return platform_refusal(product, static.provider.platform)
    return None


def platform_refusal(product: str, platform: str) -> str | None:
    """Pure: whether `product` may be posted on `platform`."""
    found = catalog.CATALOG[product]
    if platform in found.platforms:
        return None
    return (f"{found.short} is not posted on {platform}; its channels are "
            f"{', '.join(sorted(found.platforms))}")


def rotation_refusal(product: str, counts: dict[str, int],
                     eligible: list[str] | None = None) -> str | None:
    """Pure: fair share. `counts` is this week's posts per product (not
    dropped); a product may be proposed only while its count is the lowest
    among the `eligible` products -- those sellable right now (default: the
    whole catalog). A product the store cannot sell is left out of the
    floor, so it neither burns the weekly cap nor holds the others back."""
    pool = [k for k in catalog.KEYS if eligible is None or k in eligible
            or k == product]
    floor = min(int(counts.get(k, 0)) for k in pool)
    mine = int(counts.get(product, 0))
    if mine <= floor:
        return None
    due = [k for k in pool if int(counts.get(k, 0)) == floor]
    return (f"fair share: {product} already has {mine} post(s) this week and "
            f"{', '.join(due)} {'has' if len(due) == 1 else 'have'} {floor}; "
            f"propose {'it' if len(due) == 1 else 'one of those'} first")


def due_products(counts: dict[str, int],
                 eligible: list[str] | None = None) -> list[str]:
    """The products rotation allows next, in catalog order."""
    return [k for k in catalog.KEYS if (eligible is None or k in eligible)
            and rotation_refusal(k, counts, eligible) is None]


async def sellable_now() -> dict[str, str | None]:
    """Every catalog product read now: None when it can be marketed, else
    why not (unreadable, unavailable, or off its catalog terms)."""
    reads = await asyncio.gather(*(live.read_price(product=k)
                                   for k in catalog.KEYS))
    return {k: live.sellable_refusal(r, k) for k, r in zip(catalog.KEYS, reads,
                                                           strict=True)}


async def week_counts(now: datetime | None = None) -> dict[str, int]:
    """This New York week's posts per product, not counting dropped ones."""
    rows = await fetch_all(
        """SELECT product, count(*) AS n FROM course_posts
            WHERE at >= $1 AND status <> 'dropped' GROUP BY product""",
        week_start(local_day(now)))
    return {str(r["product"]): int(r["n"]) for r in rows}


async def blog_refusal(product: str, channel: str) -> str | None:
    """One Shopify blog draft per product: none while another stands."""
    if channel != channels.SHOPIFY_BLOG:
        return None
    row = await fetch_one(
        """SELECT count(*) AS n FROM course_posts
            WHERE product = $1 AND channel = 'shopify_blog'
              AND status NOT IN ('dropped', 'failed')""", product)
    if row and int(row["n"]) >= 1:
        return (f"{catalog.CATALOG[product].short} already has its Shopify blog "
                f"draft; one per product")
    return None


async def _proposal_refusal(product: str, channel: str) -> str | None:
    """The caps, the product's own live read, fair rotation among the
    products that can be sold now, and one blog draft per product -- all
    before a row is written, so a proposal refused here writes nothing."""
    if (why := await proposal_cap_refusal(channel)) is not None:
        return why
    sellable = await sellable_now()
    if (why := sellable[product]) is not None:
        return f"not proposed: {why}"
    eligible = [k for k, v in sellable.items() if v is None]
    if (why := rotation_refusal(product, await week_counts(), eligible)) is not None:
        return why
    return await blog_refusal(product, channel)


async def proposal_cap_refusal(channel: str, now: datetime | None = None
                               ) -> str | None:
    """The caps as a proposal sees them: every post this New York week that
    was not rolled back, and one per channel per day."""
    day = local_day(now)
    cap = channels.weekly_cap()
    week = await fetch_one(
        """SELECT count(*) AS n FROM course_posts
            WHERE at >= $1 AND status <> 'dropped'""", week_start(day))
    n = int(week["n"]) if week else 0
    if n >= cap:
        return (f"the week's course posts are used: {n} of {cap} "
                f"(COURSE_POSTS_PER_WEEK) since Monday, New York time")
    today = await fetch_one(
        """SELECT count(*) AS n FROM course_posts
            WHERE channel = $1 AND at >= $2 AND status <> 'dropped'""",
        channel, day_start(day))
    if today and int(today["n"]) >= 1:
        return f"{channel} already has a course post today; one per channel a day"
    return None


async def _prices(product: str = OPERATOR
                  ) -> tuple[live.Price, dict[str, str | None]]:
    read = await live.read_price(product=product)
    return read, {lang: read.words(lang) for lang in honesty.LANGUAGES}


# ------------------------------------------------------------------ propose

async def propose_dry(product: str, channel: str, lesson_id: str, language: str,
                      hypothesis: str, variant: str = "") -> ActionResult:
    if (why := _param_refusal(product, channel, lesson_id, language)) is not None:
        return ActionResult(False, why)
    if not (hypothesis or "").strip():
        return ActionResult(False, "an experiment needs a hypothesis")
    if (why := await _proposal_refusal(product, channel)) is not None:
        return ActionResult(False, why)
    return ActionResult(True, f"would propose a {honesty.LANGUAGES[language]} post "
                              f"for {catalog.CATALOG[product].short}, lesson "
                              f"{lesson_id}, on {channel}, read its price live and "
                              f"draft it")


async def propose(product: str, channel: str, lesson_id: str, language: str,
                  hypothesis: str, variant: str = "") -> ActionResult:
    """One experiment, one post for one catalog product, drafted now if it
    can be."""
    if (why := _param_refusal(product, channel, lesson_id, language)) is not None:
        return ActionResult(False, why)
    if not (hypothesis or "").strip():
        return ActionResult(False, "an experiment needs a hypothesis")
    if (why := await _proposal_refusal(product, channel)) is not None:
        return ActionResult(False, why)
    try:
        target = await channels.resolve(channel)
    except channels.ChannelError as exc:
        return ActionResult(False, f"refused: {exc}")
    if (why := platform_refusal(product, target.provider.platform)) is not None:
        return ActionResult(False, why)
    item = data.lesson(lesson_id, product) or {}
    variant = (variant or "").strip() or (
        f"{product} lesson {lesson_id} {target.provider.fmt} post ({language})")

    row = await fetch_one(
        """INSERT INTO marketing_experiments
             (business, platform, hypothesis, variant, metric)
           VALUES ($1,$2,$3,$4,$5) RETURNING id""",
        data.BUSINESS, target.provider.platform, hypothesis.strip(), variant, METRIC)
    if row is None:
        return ActionResult(False, "the experiment row was not written")
    experiment_id = int(row["id"])
    link = data.utm_url(data.product(product)["url"], target.provider.platform,
                        experiment_id)
    post = await fetch_one(
        """INSERT INTO course_posts
             (experiment_id, channel, platform, fmt, language, lesson_id, product,
              link)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING id""",
        experiment_id, channel, target.provider.platform, target.provider.fmt,
        language, item["id"], product, link)
    if post is None:
        return ActionResult(False, "the post row was not written")
    post_id = int(post["id"])
    drafted, why = await _draft(post_id, target, item, language, link, product)
    token = f"{post_id}:{experiment_id}"
    base = (f"course post #{post_id} (experiment #{experiment_id}): "
            f"{catalog.CATALOG[product].short}, lesson {item['id']} in "
            f"{honesty.LANGUAGES[language]} for {target.name}")
    if not drafted:
        return ActionResult(
            True, f"{base} -- proposed but not drafted: {why}. The growth agent "
                  f"can draft it again with course_draft_post.",
            evidence={"post_id": post_id, "experiment_id": experiment_id,
                      "drafted": False}, rollback_token=token)
    return ActionResult(
        True, f"{base} -- drafted; its card comes next ({why})",
        evidence={"post_id": post_id, "experiment_id": experiment_id,
                  "drafted": True, "link": link}, rollback_token=token)


async def propose_rollback(token: str) -> ActionResult:
    """The post is dropped and the experiment withdrawn -- both rows kept."""
    post_id, _, experiment_id = token.partition(":")
    await execute_sql(
        """UPDATE course_posts SET status = 'dropped'
            WHERE id = $1 AND status IN ('proposed', 'drafted')""", int(post_id))
    await execute_sql(
        """UPDATE marketing_experiments
              SET outcome = 'unmeasured', lesson = 'withdrawn before publishing'
            WHERE id = $1 AND posted_ref IS NULL""", int(experiment_id))
    return ActionResult(True, f"course post #{post_id} dropped and experiment "
                              f"#{experiment_id} withdrawn")


# ------------------------------------------------------------------ draft

async def _draft(post_id: int, target: channels.Target, item: dict[str, str],
                 language: str, link: str, product: str = OPERATOR
                 ) -> tuple[bool, str]:
    """Compose the post now, from a fresh read of the product's price and
    availability; store it or the reason. Nothing is drafted on a read that
    failed or breaks the product's catalog terms."""
    read, words = await _prices(product)
    if (unsellable := live.sellable_refusal(read, product)) is not None:
        await execute_sql(
            """UPDATE course_posts SET status = 'proposed', note = $2, price = $3
                WHERE id = $1""", post_id, unsellable, json.dumps(read.record()))
        return False, unsellable
    fmt = target.provider.fmt
    composed, why = await compose.compose(fmt, item, language, words, link, product,
                                          age_notice=read.age_notice)
    if (composed is not None and fmt == "social"
            and channels.length_refusal(composed["text"], target.provider)):
        # Too long for a 500-character feed: the short shape, if it fits.
        shorter, _ = await compose.compose("short", item, language, words, link,
                                           product, age_notice=read.age_notice)
        if shorter is not None and not channels.length_refusal(shorter["text"],
                                                               target.provider):
            composed = shorter
    if composed is None:
        await execute_sql(
            """UPDATE course_posts SET status = 'proposed', note = $2, price = $3
                WHERE id = $1""", post_id, why, json.dumps(read.record()))
        return False, why or "not drafted"
    if (too_long := channels.length_refusal(composed["text"], target.provider)):
        await execute_sql(
            """UPDATE course_posts SET status = 'proposed', note = $2, price = $3
                WHERE id = $1""", post_id, too_long, json.dumps(read.record()))
        return False, too_long
    await execute_sql(
        """UPDATE course_posts
              SET status = 'drafted', body = $2, english = $3, translated = $4,
                  price = $5, note = NULL
            WHERE id = $1""",
        post_id, composed["text"], composed["english"], composed["translated"],
        json.dumps(read.record()))
    return True, read.card_line(product)


async def _carded(post_id: int) -> bool:
    row = await fetch_one(
        """SELECT count(*) AS n FROM proposals
            WHERE action IN ($1, $2) AND params->>'post_id' = $3""",
        PUBLISH, HAND_OFF, str(post_id))
    return bool(row) and int(row["n"]) > 0


async def _post(post_id: Any) -> dict[str, Any] | None:
    try:
        pid = int(post_id)
    except (TypeError, ValueError):
        return None
    row = await fetch_one(
        """SELECT id, experiment_id, channel, platform, fmt, language, lesson_id,
                  product, link, body, english, translated, price, status, note,
                  posted_ref, posted_at
             FROM course_posts WHERE id = $1""", pid)
    return dict(row) if row else None


async def draft_post_dry(post_id: int) -> ActionResult:
    post = await _post(post_id)
    if post is None:
        return ActionResult(False, f"no course post #{post_id}")
    if post["status"] not in ("proposed", "drafted"):
        return ActionResult(False, f"course post #{post_id} is {post['status']}")
    if await _carded(int(post["id"])):
        return ActionResult(False, f"course post #{post_id} is already on a card; "
                                   f"decline it and propose a new one instead")
    return ActionResult(True, f"would draft course post #{post_id} again from a "
                              f"fresh price read")


async def draft_post(post_id: int) -> ActionResult:
    """Draft a post again: one not yet carded, from a fresh price."""
    post = await _post(post_id)
    if post is None:
        return ActionResult(False, f"no course post #{post_id}")
    if post["status"] not in ("proposed", "drafted"):
        return ActionResult(False, f"course post #{post_id} is {post['status']}")
    if await _carded(int(post["id"])):
        return ActionResult(False, f"course post #{post_id} is already on a card; "
                                   f"decline it and propose a new one instead")
    try:
        target = await channels.resolve(post["channel"])
    except channels.ChannelError as exc:
        return ActionResult(False, f"refused: {exc}")
    product = str(post.get("product") or OPERATOR)
    if catalog.get(product) is None:
        return ActionResult(False, f"{product!r} is no longer in the catalog; a "
                                   f"product not in the catalog is never marketed")
    item = data.lesson(post["lesson_id"], product)
    if item is None:
        return ActionResult(False, f"lesson {post['lesson_id']} is no longer in "
                                   f"the {product} data")
    before = {k: post.get(k) for k in ("status", "body", "english", "translated",
                                       "note")}
    before["price"] = _price_json(post.get("price"))
    ok, why = await _draft(int(post["id"]), target, item, post["language"],
                           post["link"], product)
    token = json.dumps({"id": int(post["id"]), "before": before}, default=str)
    if not ok:
        return ActionResult(False, f"course post #{post_id} not drafted: {why}",
                            rollback_token=token)
    return ActionResult(True, f"course post #{post_id} drafted again; {why}",
                        evidence={"post_id": int(post["id"])}, rollback_token=token)


def _price_json(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value or {})


async def draft_post_rollback(token: str) -> ActionResult:
    saved = json.loads(token)
    b = saved["before"]
    await execute_sql(
        """UPDATE course_posts
              SET status = $2, body = $3, english = $4, translated = $5,
                  note = $6, price = $7
            WHERE id = $1 AND status IN ('proposed', 'drafted')""",
        int(saved["id"]), b["status"], b["body"], b["english"],
        b["translated"] or "", b["note"], b["price"])
    return ActionResult(True, f"course post #{saved['id']} restored to its "
                              f"previous draft")


# ------------------------------------------------------------------ the card

def card_detail(params: dict[str, Any]) -> str:
    """The exact words, whole: the text IS the decision."""
    return (f"Course post #{params.get('post_id')}, exactly as it goes out:\n"
            f"```\n{params.get('text', '')}\n```")


def vet(params: dict[str, Any]) -> str | None:
    """Pure: the card's text passes the language-blind rules before a card
    exists (the full check, with the live price, runs on approval)."""
    text = str(params.get("text") or "")
    if not text.strip():
        return "the post is empty"
    try:
        int(params.get("post_id"))
    except (TypeError, ValueError):
        return f"not a post id: {params.get('post_id')!r}"
    for patterns in (honesty.INCOME, honesty.TESTIMONIAL, honesty.URGENCY):
        for pattern in patterns.values():
            if (hit := pattern.search(text)) is not None:
                return f"the post says {hit.group(0).strip()!r}"
    for pattern in honesty.BLIND:
        if (hit := pattern.search(text)) is not None:
            return f"the post says {hit.group(0).strip()!r}"
    return None


def reason_for(post: dict[str, Any], target_name: str) -> str:
    """The card's body: the disclosure first, then the price with its read
    time, the link, and for a translation the English original."""
    lang = honesty.LANGUAGES.get(post["language"], post["language"])
    product = str(post.get("product") or OPERATOR)
    found = catalog.get(product)
    short = found.short if found else product
    head = (f"AI-DRAFTED TRANSLATION ({lang}) -- the model translated this from "
            f"the English below; review it before approving."
            if post.get("translated")
            else f"English, composed in code from {short}'s own lesson text.")
    rec = live.from_record(_as_dict(post.get("price")))
    price_line = (rec.card_line(product) if rec and found
                  else "Price: UNKNOWN (no read recorded)")
    how = ("the owner posts this by hand; approving marks it handed off and starts "
           "its measurement." if post["channel"] in channels.HANDOFF
           else "Approving posts it now through Postiz.")
    where = (f"Course post #{post['id']} -- {short}, lesson {post['lesson_id']} "
             f"for {target_name}, experiment #{post['experiment_id']}.")
    link = (f"Link: {post['link']} (utm_source={post['platform']}, "
            f"utm_medium=organic, utm_campaign={post['experiment_id']})")
    rules = ("the owner's own product; no income, grades, accuracy or accreditation "
             "claims, no reviews, countdowns or discounts, never Claude for kids "
             "(checked in code).")
    lines = [head, where, price_line, link, rules, how]
    if post.get("translated") and post.get("english"):
        lines += ["", "English original:", str(post["english"])]
    return "\n".join(lines)


def _as_dict(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


async def waiting_for_card(now: datetime | None = None) -> list[dict[str, Any]]:
    """Drafted posts with no card yet, skipping a channel that already has a
    post out today (it is carded tomorrow)."""
    day = local_day(now)
    rows = await fetch_all(
        """SELECT p.id, p.experiment_id, p.channel, p.platform, p.fmt, p.language,
                  p.lesson_id, p.product, p.link, p.body, p.english, p.translated,
                  p.price, p.status
             FROM course_posts p
            WHERE p.status = 'drafted' AND p.body IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM proposals q
                               WHERE q.action IN ($1, $2)
                                 AND q.params->>'post_id' = p.id::text)
              AND NOT EXISTS (SELECT 1 FROM course_posts o
                               WHERE o.channel = p.channel
                                 AND o.status IN ('publishing', 'published',
                                                  'handed_off')
                                 AND o.posted_at >= $3)
            ORDER BY p.id LIMIT 10""",
        PUBLISH, HAND_OFF, day_start(day))
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ approve

async def _approval_refusal(post: dict[str, Any] | None, post_id: Any, text: str,
                            *, handoff: bool) -> tuple[str | None, live.Price | None]:
    """Everything that must still hold when he taps: the same text, the
    rules, the channel kind, and the live price."""
    if post is None:
        return f"no course post #{post_id}", None
    if post["status"] != "drafted":
        return f"course post #{post_id} is {post['status']}, not waiting", None
    if (post.get("body") or "") != text:
        return (f"course post #{post_id} changed since the card was raised; "
                f"nothing was sent"), None
    if handoff != (post["channel"] in channels.HANDOFF):
        return (f"course post #{post_id} is for {post['channel']}, which is "
                f"{'posted by ATLAS' if handoff else 'handed to the owner'}"), None
    product = str(post.get("product") or OPERATOR)
    if (why := catalog.product_refusal(product)) is not None:
        return why, None
    stored = live.from_record(_as_dict(post.get("price")))
    now = await live.read_price(product=product)
    if stored is not None and stored.read:
        if not now.read:
            return (f"the price could not be re-read ({now.why}); the card's "
                    f"{stored.words()} is not confirmed, so nothing was sent"), now
        if not now.same_as(stored):
            return (f"the store's price is now {now.words()} (read "
                    f"{now.stamp()}), not the card's {stored.words()}; draft "
                    f"it again"), now
    if (why := live.sellable_refusal(now, product)) is not None:
        return why, now
    words = now.words(post["language"]) if (stored and stored.read) else None
    if (why := honesty.refusal(text, post["language"], words, product,
                               age_notice=now.age_notice)) is not None:
        return why, now
    if post["channel"] == channels.SHOPIFY_BLOG and (
            why := compose.article_refusal(text, post.get("link"))) is not None:
        return why, now
    return None, now


CLAIM_SQL = """
UPDATE course_posts SET status = $2, posted_at = $3
 WHERE id = $1 AND status = 'drafted'
   AND (SELECT count(*) FROM course_posts w
         WHERE w.status IN ('publishing', 'published', 'handed_off')
           AND w.posted_at >= $4) < $5
   AND NOT EXISTS (SELECT 1 FROM course_posts d
                    WHERE d.channel = $6
                      AND d.status IN ('publishing', 'published', 'handed_off')
                      AND d.posted_at >= $7)
RETURNING id"""


async def claim(post: dict[str, Any], status: str, now: datetime | None = None
                ) -> str | None:
    """Take the post under the caps, atomically: None when claimed, else why."""
    now = now or datetime.now(UTC)
    day = local_day(now)
    cap = channels.weekly_cap()
    row = await fetch_one_locked(
        CLAIM_LOCK, CLAIM_SQL, int(post["id"]), status, now, week_start(day), cap,
        post["channel"], day_start(day))
    if row is not None:
        return None
    return (f"the caps hold it back: at most {cap} course posts a week "
            f"(COURSE_POSTS_PER_WEEK) and one per channel a day, New York time -- "
            f"{post['channel']} or the week is already used")


async def _start_clock(post: dict[str, Any], ref: str, at: datetime) -> None:
    from ...kernel.marketing import experiments
    await experiments.attach(int(post["experiment_id"]), ref, posted_at=at,
                             after=MEASURE_AFTER)


async def publish_post_dry(post_id: int, text: str) -> ActionResult:
    post = await _post(post_id)
    why, _ = await _approval_refusal(post, post_id, text, handoff=False)
    if why is not None:
        return ActionResult(False, why)
    try:
        target = await channels.resolve(post["channel"])      # type: ignore[index]
    except channels.ChannelError as exc:
        return ActionResult(False, f"refused: {exc}")
    return ActionResult(True, f"would post course post #{post_id} to {target.name}")


async def publish_post(post_id: int, text: str) -> ActionResult:
    """Post one approved course post through Postiz. Irreversible: a
    published post can only be taken down by deleting it, which is Tier 3."""
    post = await _post(post_id)
    why, _ = await _approval_refusal(post, post_id, text, handoff=False)
    if why is not None or post is None:
        return ActionResult(False, f"refused: {why}")
    try:
        target = await channels.resolve(post["channel"])
    except channels.ChannelError as exc:
        return ActionResult(False, f"refused: {exc}")
    if (too_long := channels.length_refusal(text, target.provider)):
        return ActionResult(False, f"refused: {too_long}")
    at = datetime.now(UTC)
    if (held := await claim(post, "publishing", at)) is not None:
        return ActionResult(False, f"refused: {held}")
    status, detail, ref = await channels.post_text(target, text)
    if status != channels.POSTED or not ref:
        await execute_sql(
            """UPDATE course_posts SET status = 'failed', note = $2
                WHERE id = $1 AND status = 'publishing'""", int(post["id"]), detail)
        return ActionResult(False, f"course post #{post_id} not posted: {detail}")
    await execute_sql(
        """UPDATE course_posts SET status = 'published', posted_ref = $2
            WHERE id = $1 AND status = 'publishing'""", int(post["id"]), ref)
    await _start_clock(post, ref, at)
    return ActionResult(
        True, f"course post #{post_id} posted: {detail}; measured in "
              f"{MEASURE_AFTER.days} days by utm_campaign={post['experiment_id']}",
        evidence={"post_id": int(post["id"]), "posted_ref": ref,
                  "experiment_id": int(post["experiment_id"]),
                  "text_sha256": sha(text)})


async def hand_off_dry(post_id: int, text: str) -> ActionResult:
    post = await _post(post_id)
    why, _ = await _approval_refusal(post, post_id, text, handoff=True)
    if why is not None:
        return ActionResult(False, why)
    return ActionResult(True, f"would mark course post #{post_id} handed to the owner "
                              f"to post on {post['channel']}")   # type: ignore[index]


async def hand_off(post_id: int, text: str) -> ActionResult:
    """Mark a LinkedIn or blog post as handed to the owner, who posts it himself;
    its measurement clock starts now. Nothing leaves ATLAS."""
    post = await _post(post_id)
    why, _ = await _approval_refusal(post, post_id, text, handoff=True)
    if why is not None or post is None:
        return ActionResult(False, f"refused: {why}")
    at = datetime.now(UTC)
    if (held := await claim(post, "handed_off", at)) is not None:
        return ActionResult(False, f"refused: {held}")
    ref = f"handoff:{post['id']}"
    await execute_sql("UPDATE course_posts SET posted_ref = $2 WHERE id = $1",
                      int(post["id"]), ref)
    await _start_clock(post, ref, at)
    where = ("your LinkedIn profile" if post["channel"] == channels.LINKEDIN
             else "the Shopify blog, as a hidden draft first")
    return ActionResult(
        True, f"course post #{post_id} is with you to post on {where}, exactly as "
              f"the card shows; measured in {MEASURE_AFTER.days} days by "
              f"utm_campaign={post['experiment_id']}",
        evidence={"post_id": int(post["id"]), "posted_ref": ref,
                  "text_sha256": sha(text)},
        rollback_token=f"{post['id']}:{post['experiment_id']}")


async def hand_off_rollback(token: str) -> ActionResult:
    post_id, _, experiment_id = token.partition(":")
    await execute_sql(
        """UPDATE course_posts SET status = 'drafted', posted_at = NULL,
                  posted_ref = NULL
            WHERE id = $1 AND status = 'handed_off'""", int(post_id))
    await execute_sql(
        """UPDATE marketing_experiments
              SET posted_ref = NULL, posted_at = NULL, measure_at = NULL
            WHERE id = $1 AND outcome IS NULL""", int(experiment_id))
    return ActionResult(True, f"course post #{post_id} back to waiting")


# ------------------------------------------------------------------ reads

async def week(now: datetime | None = None) -> dict[str, Any]:
    """This New York week's course posts and the caps, read now."""
    day = local_day(now)
    rows = await fetch_all(
        """SELECT id, experiment_id, product, channel, language, lesson_id, status,
                  note, posted_at FROM course_posts
            WHERE at >= $1 ORDER BY id""", week_start(day))
    posts = [dict(r) for r in rows]
    used = sum(1 for p in posts if p["status"] != "dropped")
    out = sum(1 for p in posts if p["status"] in OUT)
    counts = {k: sum(1 for p in posts if p.get("product") == k
                     and p["status"] != "dropped") for k in catalog.KEYS}
    return {"week_of": week_start(day).date().isoformat(),
            "cap": channels.weekly_cap(), "proposed": used, "out": out,
            "by_product": counts, "due_next": due_products(counts),
            "posts": posts,
            "read_at": datetime.now(UTC).isoformat(timespec="seconds")}
