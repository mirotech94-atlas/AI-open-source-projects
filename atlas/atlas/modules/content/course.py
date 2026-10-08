"""The course-promo lane: two reels a week for Miro's own course, on the
AI-stories accounts, honest by rule.

Miro, relayed 2026-09-29: "The AI That Can't Burn Your Business: Build a
Safe AI Operator", sold on his Shopify store. One lesson per reel, rotating
through the lessons that carry a hook in his outline
(`calendars/course-promo.json`, copied from the vault, which the server
cannot read). Each reel: the lesson's hook spoken in the reels' voice (Fish,
`REELS_FISH_VOICE_ID`) over one or two abstract Veo beats, the three real
product images animated (a slow Ken Burns push, one sliding), bold captions
of the script's own words, and an end card with the price and "LINK IN BIO".
20-35 s, vertical.

The price is never in the data file. It is read from the store's public
product JSON (`<product url>.json`, https, the store's own host, a public
address) when the reel is DRAFTED, and the read -- price, compare-at price,
the time it was read -- is stored on the draft's PRICE line (invariant 6).
A compare-at above the price reads "$20 (was $49)"; otherwise "$20". When
the store cannot be read, the reel carries no price at all -- the caption
and the end card say "link in bio" only -- and the draft says "price not
read"; no stored or remembered number stands in for it. (The store moved
from $49 to $20, compare-at $49, on 2026-09-29.)

Where it posts: the AI-stories path only -- `kernel.reels.post_or_card`,
i.e. `content.autopublish_reel` / `content.publish_reel`, whose legs are the
Instagram `stories` account, the Atlas Stories Facebook Page and the Postiz
TikTok integration `atlasstories7`. Nothing here names or reaches a clips
integration (Instagram `clips`, TikTok @atlasclips12, YouTube clips).

The rules, in code, tested:

  * **No income claims.** The script, the on-screen text and the caption pass
    `guard.income_refusal` ("make $", income, passive, earn, get rich, ...)
    when drafted, and again before any clip is asked for.
  * **No fake screens.** A beat's video prompt is a shot from `SHOTS`, a
    closed catalog of abstract motion written here -- light, glass, stone,
    water, particles. Nobody appears. Every course prompt starts with
    `guard.COURSE_PROMPT_HEAD`, and both clip doors refuse one with a screen
    word in it (`guard.screen_refusal`). The product images are the only
    UI-like visuals, and they are the real product's.
  * **Clearly his own.** The voice says "my course", the caption says "my
    course", and a MY COURSE label sits on every frame.
  * **The CTA.** The caption carries "link in bio" and the product URL, and
    ends with the AI label like every AI-stories reel.
  * **Never guess.** The script is composed in code from the data file and
    the store read; no model writes it. The render recomposes it from the
    file and the draft's stored read, and refuses a draft that differs.

Cadence: `COURSE_REELS_PER_WEEK` (default 2) on fixed New York weekdays --
Tuesday and Friday for two. A slot missed (the day's automatic-post cap
already spent, no Veo clips left) moves to the next day of the same week,
never into the next week; at most one attempt a day. The reels count toward
`REELS_AUTOPUBLISH_PER_DAY` like any other. Drafts are tagged with the
`course_promo` format row (source 'course'), so `metrics.score_bonus` and
the results digest measure them; the stories rotation never sees that row.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from ...kernel.contracts import ActionResult
from ...kernel.state import fetch_one
from . import guard, reel_script
from .drafts import draft_rollback

log = logging.getLogger("atlas.content")

HERE = Path(__file__).parent
DATA = HERE / "calendars" / "course-promo.json"
ASSETS = HERE / "assets" / "course"
ZONE = ZoneInfo("America/New_York")

FORMAT = "course_promo"
SOURCE = "course"
SEED_WEIGHT = 1.0
MIN_BEATS, MAX_BEATS = 1, 2
SECONDS = (20.0, 35.0)          # the reel's length band, checked on the file
MAX_WORDS = 62                  # the spoken script; ~2.6 words a second
PER_WEEK_DEFAULT = 2
# The course lane's closed list (`hashtags.COURSE`): no income word, ever.
HASHTAGS = "#aiagents #automation #smallbusiness #aitools"
LABEL = "MY COURSE"

# The weekdays (Mon = 0) a week's reels are due on, by how many a week.
SLOTS: dict[int, tuple[int, ...]] = {
    1: (1,), 2: (1, 4), 3: (0, 2, 4), 4: (0, 1, 3, 4), 5: (0, 1, 2, 3, 4),
    6: (0, 1, 2, 3, 4, 5), 7: (0, 1, 2, 3, 4, 5, 6)}

# The closed catalog of shots: abstract motion only. No person, no face, no
# device, nothing with writing on it (guard.screen_refusal holds the rest).
SHOTS: dict[str, str] = {
    "light_lattice": (
        "Glowing amber threads weave themselves into a slowly turning lattice "
        "in a dark void, each joint lighting up as it locks into place."),
    "glass_shield": (
        "A curved pane of frosted glass rises out of dark water and deflects a "
        "drifting swarm of red sparks, which scatter and fade."),
    "stone_gate": (
        "A heavy stone gate in a misty valley swings slowly shut, warm light "
        "spilling through the gap until it closes with a soft thud."),
    "calm_water": (
        "A single drop falls onto perfectly still dark water; rings spread out "
        "in slow motion under a cool blue rim light."),
    "particle_order": (
        "A chaotic cloud of glowing particles settles, grain by grain, into "
        "neat parallel rows of light, the camera gliding slowly past."),
    "lantern_watch": (
        "A lone lantern hangs over a dark harbour at night, its beam sweeping "
        "slowly across calm water and moored boats."),
    "locked_vault": (
        "The round steel door of an old vault turns and seals shut, bolts "
        "sliding home one by one in dramatic slow motion."),
}

STYLE = ("LOOK: premium abstract motion design, cinematic lighting, deep "
         "shadows, soft volumetric haze, rich colour grade. Nobody appears in "
         "the picture: no person, no hands, no face. No writing, letters or "
         "numbers anywhere in the picture; the captions are added later. "
         "MOTION: slow, confident camera moves -- a glide, an orbit, a push-in; "
         "never a still frame. SOUND: a calm, low cinematic score and soft "
         "ambience; nobody speaks.")


# ------------------------------------------------------------------ data

@lru_cache(maxsize=1)
def data() -> dict[str, Any]:
    with open(DATA, encoding="utf-8") as fh:
        return json.load(fh)


def products() -> list[dict[str, Any]]:
    """Every course the lane advertises (Miro, 2026-10-05: all of them, as
    reels): the first is the data file's own `product`, with its lessons
    and images; the rest are `products[]`, each with its own."""
    d = data()
    first = {**d["product"], "lessons": d["lessons"],
             "images": [str(Path("assets") / "course" / n) for n in d["images"]]}
    first.setdefault("slug", "")
    return [first, *[dict(x) for x in d.get("products", [])]]


def product(item: dict[str, str] | None = None) -> dict[str, str]:
    """The course a lesson belongs to; the first course when none is named."""
    slug = (item or {}).get("product")
    found = next((x for x in products() if slug and x.get("slug") == slug), None)
    return {k: v for k, v in (found or products()[0]).items()
            if k not in ("lessons", "images")}


@lru_cache(maxsize=1)
def _rotation() -> tuple[dict[str, str], ...]:
    """The lessons in the order they are drafted: one course after another
    in turn, so every course is on screen every few reels. A lesson of the
    first course keeps its bare id; another course's is `<slug>:<id>`, and
    `no` is always the number the voice says."""
    queues = []
    for k, prod in enumerate(products()):
        rows = []
        for x in prod["lessons"]:
            row = {**x, "no": x["id"], "product": prod.get("slug", "")}
            if k:
                row["id"] = f"{prod['slug']}:{x['id']}"
            rows.append(row)
        queues.append(rows)
    out: list[dict[str, str]] = []
    for i in range(max(len(q) for q in queues)):
        out += [q[i] for q in queues if i < len(q)]
    return tuple(out)


def lessons() -> list[dict[str, str]]:
    return [dict(x) for x in _rotation()]


def lesson(lesson_id: str) -> dict[str, str] | None:
    return next((x for x in lessons() if x["id"] == lesson_id), None)


def images(item: dict[str, str] | None = None) -> list[Path]:
    slug = (item or {}).get("product")
    prod = next((x for x in products() if slug and x.get("slug") == slug),
                products()[0])
    return [(HERE / name).resolve() for name in prod["images"]]


def per_week() -> int:
    try:
        return max(0, min(7, int(os.getenv("COURSE_REELS_PER_WEEK",
                                           str(PER_WEEK_DEFAULT)))))
    except ValueError:
        return PER_WEEK_DEFAULT


# ------------------------------------------------------------------ the price

PRICE_TIMEOUT = 10.0
CURRENCY = "USD"
_AMOUNT = re.compile(r"^\d{1,6}(\.\d{1,2})?$")
_PRICE_LINE = re.compile(
    r"^(?:(?P<amount>\d{1,6}\.\d{2})(?: was (?P<was>\d{1,6}\.\d{2}))?, read"
    r"|(?P<none>not read), tried) (?P<at>\S+)$")


@dataclass(frozen=True, slots=True)
class Price:
    """One read of the store. `amount` None: the store was not read, and the
    reel names no price. `why` is for the notes; it is never stored."""
    amount: Decimal | None
    compare_at: Decimal | None
    read_at: datetime
    why: str | None = None

    @property
    def read(self) -> bool:
        return self.amount is not None

    @property
    def discounted(self) -> bool:
        return (self.amount is not None and self.compare_at is not None
                and self.compare_at > self.amount)

    def line(self) -> str:
        """The draft's PRICE line (without the "PRICE:" mark)."""
        at = self.read_at.astimezone(UTC).isoformat(timespec="seconds")
        if self.amount is None:
            return f"not read, tried {at}"
        was = f" was {self.compare_at:.2f}" if self.discounted else ""
        return f"{self.amount:.2f}{was}, read {at}"

    def record(self) -> dict[str, Any]:
        return {"price": None if self.amount is None else f"{self.amount:.2f}",
                "compare_at": (None if self.compare_at is None
                               else f"{self.compare_at:.2f}"),
                "read_at": self.read_at.astimezone(UTC).isoformat(timespec="seconds"),
                "read": self.read}

    def note(self) -> str:
        at = f"{self.read_at.astimezone(UTC):%Y-%m-%d %H:%M} UTC"
        if self.amount is None:
            return f"price not read ({self.why or 'no reason given'}), tried {at}"
        return f"price {price_words(self)} read from the store at {at}"


def not_read(why: str, at: datetime | None = None) -> Price:
    return Price(None, None, at or datetime.now(UTC), why)


def parse_price_line(text: str) -> Price | None:
    """The stored read back from a draft's PRICE line; None when the line is
    missing or not in the shape `Price.line` writes."""
    m = _PRICE_LINE.match((text or "").strip())
    if m is None:
        return None
    try:
        at = datetime.fromisoformat(m.group("at"))
    except ValueError:
        return None
    if at.tzinfo is None:
        return None
    if m.group("none"):
        return Price(None, None, at)
    amount = Decimal(m.group("amount"))
    was = Decimal(m.group("was")) if m.group("was") else None
    if amount <= 0:
        return None
    return Price(amount, was, at)


def money(value: Decimal) -> str:
    """$20 for 20.00, $19.99 for 19.99."""
    return f"${value:.0f}" if value == value.to_integral_value() else f"${value:.2f}"


def price_words(price: Price | None) -> str | None:
    """On screen and in the caption: "$20 (was $49)", "$20", or None."""
    if price is None or price.amount is None:
        return None
    if price.discounted and price.compare_at is not None:
        return f"{money(price.amount)} (was {money(price.compare_at)})"
    return money(price.amount)


def price_said(price: Price | None) -> str | None:
    """In the voice (and so in the burned captions): "$20, down from $49"."""
    if price is None or price.amount is None:
        return None
    if price.discounted and price.compare_at is not None:
        return f"{money(price.amount)}, down from {money(price.compare_at)}"
    return money(price.amount)


def store_json_url(item: dict[str, str] | None = None) -> str:
    """Where the price is read: the store's public catalog JSON when the
    data file names one (ATLAS Academy, shop.mapfront.io/catalog.json),
    else Shopify's `<product url>.json`."""
    p = product(item)
    return p.get("price_json") or f"{p['url'].rstrip('/')}.json"


def _amount(value: Any) -> Decimal | None:
    # ATLAS Academy's catalog.json states `price_usd` as a JSON number (20.0,
    # seen live 2026-10-05); Shopify states a string ("20.00"). A number is
    # read through its own text, so 20.0 is "20.0" and never a float sum.
    if isinstance(value, int | float) and not isinstance(value, bool):
        value = str(value)              # 19.999 stays three decimals: not read
    if not isinstance(value, str) or not _AMOUNT.match(value.strip()):
        return None
    try:
        return Decimal(value.strip())
    except InvalidOperation:
        return None


def parse_catalog(payload: Any, at: datetime, slug: str) -> Price:
    """The product's price from ATLAS Academy's public catalog.json
    (modules/academy/site.py): `products[].slug`, `price_usd`, `currency`,
    `buy_live`. No compare-at there. Off-shape, unpriced or not on sale is
    "not read", never a guess."""
    entries = payload.get("products") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return not_read("the store's catalog JSON has no products", at)
    e = next((x for x in entries if isinstance(x, dict) and x.get("slug") == slug), None)
    if e is None:
        return not_read(f"the store's catalog has no {slug!r}", at)
    cur = e.get("currency")
    if cur not in (None, "") and str(cur).upper() != CURRENCY.upper():
        return not_read(f"the store's currency is {str(cur)[:8]!r}, not {CURRENCY}", at)
    if e.get("buy_live") is not True:
        return not_read("the store does not sell it yet", at)
    amount = _amount(e.get("price_usd"))
    if amount is None or amount <= 0:
        return not_read("the store's price is not a plain positive amount", at)
    return Price(amount, None, at)


def parse_product(payload: Any, at: datetime, slug: str | None = None) -> Price:
    """The first variant's price and compare-at price from Shopify's
    product JSON, or the product's price from the store's catalog JSON
    (`parse_catalog`). Anything off-shape is "not read", never a guess."""
    if isinstance(payload, dict) and "products" in payload and "product" not in payload:
        return parse_catalog(payload, at, product().get("slug", "") if slug is None
                             else slug)
    prod = payload.get("product") if isinstance(payload, dict) else None
    variants = prod.get("variants") if isinstance(prod, dict) else None
    if not isinstance(variants, list) or not variants or not isinstance(
            variants[0], dict):
        return not_read("the store's product JSON has no variant", at)
    v = variants[0]
    for key in ("price_currency", "compare_at_price_currency"):
        cur = v.get(key)
        if cur not in (None, "") and cur != CURRENCY:
            return not_read(f"the store's {key} is {str(cur)[:8]!r}, not "
                            f"{CURRENCY}", at)
    amount = _amount(v.get("price"))
    if amount is None or amount <= 0:
        return not_read("the store's price is not a plain positive amount", at)
    raw_was = v.get("compare_at_price")
    was = None
    if raw_was not in (None, ""):
        was = _amount(raw_was)
        if was is None:
            return not_read("the store's compare-at price is not a plain amount", at)
    return Price(amount, was, at)


async def read_price(url: str | None = None, *,
                     item: dict[str, str] | None = None) -> Price:
    """The price, read now from the store's public product JSON. Never
    raises: a failure is a `Price` that is not read, with the reason."""
    import httpx

    from .._http import explain
    from .._netguard import assert_public

    prod = product(item)
    url = url or store_json_url(item)
    parts = urlsplit(url)
    host = (urlsplit(prod["store"]).hostname or "").lower()
    if parts.scheme.lower() != "https":
        return not_read(f"only https is read, not {parts.scheme or 'no scheme'!r}")
    if (parts.hostname or "").lower() != host:
        return not_read("only the course's own store is read")
    if (why := await assert_public(url)) is not None:
        return not_read(why)
    try:
        async with httpx.AsyncClient(timeout=PRICE_TIMEOUT,
                                     follow_redirects=False) as client:
            r = await client.get(url, headers={"User-Agent": "ATLAS/1.0",
                                               "Accept": "application/json"})
    except Exception as exc:                            # noqa: BLE001
        return not_read(explain(exc))
    at = datetime.now(UTC)
    if r.status_code != 200:
        return not_read(f"the store answered {r.status_code}", at)
    try:
        payload = r.json()
    except ValueError:
        return not_read("the store's answer was not JSON", at)
    # A course sold as several offers names the one whose price it shows.
    return parse_product(payload, at, prod.get("price_slug") or prod.get("slug") or None)


# ------------------------------------------------------------------ the script

def cta_line(price: Price | None = None,
             item: dict[str, str] | None = None) -> str:
    p = product(item)
    said = price_said(price)
    if said is None:
        return f"It's in my course, {p['short']}. Link in bio."
    return f"It's in my course, {p['short']}. {said}, link in bio."


def spoken(item: dict[str, str], price: Price | None = None) -> list[str]:
    """The script, sentence by sentence, as the voice says it."""
    return [item["hook"], item["line"],
            f"That's lesson {item.get('no', item['id'])}.", cta_line(price, item)]


def shots_for(item: dict[str, str], beats: int) -> list[str]:
    """The lesson's shots: a fixed rotation through the catalog."""
    names = list(SHOTS)
    start = [x["id"] for x in lessons()].index(item["id"]) if lesson(
        item["id"]) else 0
    return [names[(start + k) % len(names)] for k in range(beats)]


def scenes(item: dict[str, str], beats: int, price: Price | None = None
           ) -> list[str]:
    """The draft's beats: one or two, each carrying its share of the spoken
    script and its shot as [[shot id]]."""
    lines, shots = spoken(item, price), shots_for(item, beats)
    if beats == 1:
        return [f"{' '.join(lines)} [[{shots[0]}]]"]
    return [f"{lines[0]} [[{shots[0]}]]", f"{' '.join(lines[1:])} [[{shots[1]}]]"]


def caption(item: dict[str, str], price: Price | None = None) -> str:
    """One line (the draft body keeps only the CAPTION line), without the AI
    label, which `reel_script` appends and keeps last. No price when the
    store was not read."""
    p = product(item)
    words = price_words(price)
    cost = f"{words}, link in bio" if words else "link in bio"
    return (f"{item['hook']} {item['line']} Lesson {item.get('no', item['id'])} of my course, "
            f"\"{p['title']}\": {cost} or {p['url']} {HASHTAGS}")


def fmt(item: dict[str, str]) -> str:
    return f"{FORMAT}: lesson {item['id']}"


def is_course(script: reel_script.Script | None) -> bool:
    return str(getattr(script, "format", "") or "").lower().startswith(FORMAT)


def lesson_of(script: reel_script.Script) -> dict[str, str] | None:
    m = re.match(rf"{FORMAT}: lesson (\S+)$", str(script.format or "").strip())
    return lesson(m.group(1)) if m else None


def screen_lines(script: reel_script.Script) -> list[str]:
    return [reel_script._SCENE.sub("", b).strip() for b in script.scenes]


def narration(script: reel_script.Script) -> str:
    return " ".join(screen_lines(script))


def shots_of(script: reel_script.Script) -> list[str]:
    return [reel_script.scene_of(b) or "" for b in script.scenes]


def beat_prompt(shot: str, index: int, total: int) -> str:
    """One beat's video prompt, entirely from this file's catalog."""
    return (f"{guard.COURSE_PROMPT_HEAD} Shot {index} of {total}.\n{SHOTS[shot]}\n"
            f"{STYLE}\nVertical 9:16, cinematic.")


def honesty_refusal(item: dict[str, str], beats: int,
                    price: Price | None = None) -> str | None:
    """Everything a lesson's reel would say, show or send, checked: the
    income rule on the words, the screen rule on every prompt, the length."""
    said = spoken(item, price)
    words = " ".join([*said, caption(item, price), price_words(price) or ""])
    if (why := guard.income_refusal(words)) is not None:
        return why
    if (why := guard.refusal(" ".join(said))) is not None:
        return why
    n = len(" ".join(said).split())
    if n > MAX_WORDS:
        return (f"lesson {item['id']}'s script is {n} words; a course reel "
                f"speaks at most {MAX_WORDS} (it must fit {SECONDS[1]:.0f} s)")
    for i, shot in enumerate(shots_for(item, beats), 1):
        prompt = beat_prompt(shot, i, beats)
        if (why := guard.screen_refusal(prompt) or guard.ip_refusal(prompt)):
            return why
    cap = caption(item, price).lower()
    p = product(item)
    if "link in bio" not in cap or p["url"].lower() not in cap or "my course" not in cap:
        return "the caption must say \"my course\", \"link in bio\" and the URL"
    return None


def compose(item: dict[str, str], beats: int, price: Price | None = None) -> str:
    """The draft body. Its PRICE line stores the store read the words were
    made from; with no read at all, the line says "not read"."""
    price = price or not_read("no store read was given")
    return reel_script.compose(fmt(item), scenes(item, beats, price),
                               caption(item, price), dialogue=False,
                               price=price.line())


# ------------------------------------------------------------------ cadence

def slot_days(n: int) -> tuple[int, ...]:
    return SLOTS.get(max(0, min(7, n)), ())


def entitled(day: date, n: int) -> int:
    """How many course reels the week owes by the end of `day`."""
    return sum(1 for d in slot_days(n) if d <= day.weekday())


def due(day: date, n: int, done_this_week: int) -> bool:
    """A reel is due while the week's slots so far outnumber what went out
    (posted or carded) this week -- so a slot the cap or the clips made us
    miss moves to the next day, and a week's shortfall never carries over."""
    return done_this_week < entitled(day, n)


def local_today(now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(ZONE).date()


def week_start(day: date) -> datetime:
    monday = day - timedelta(days=day.weekday())
    return datetime.combine(monday, time(0), tzinfo=ZONE)


def day_start(day: date) -> datetime:
    return datetime.combine(day, time(0), tzinfo=ZONE)


# ------------------------------------------------------------------ records

async def ensure_format() -> int:
    """The one `course_promo` reel_formats row (source 'course')."""
    row = await fetch_one(
        """INSERT INTO reel_formats (format, evidence, source, score, max_scenes)
           SELECT $1, $2, $3, $4, $5
           WHERE NOT EXISTS (SELECT 1 FROM reel_formats r
                              WHERE r.source = $3 AND lower(r.format) = lower($1)
                                AND r.retired_at IS NULL)
           RETURNING id""",
        FORMAT, "chosen by Miro, 2026-09-29: two course-promo reels a week for "
                "Build a Safe AI Operator", SOURCE, SEED_WEIGHT, MAX_BEATS)
    if row is None:
        row = await fetch_one(
            """SELECT id FROM reel_formats WHERE source = $1
                 AND lower(format) = lower($2) AND retired_at IS NULL
               ORDER BY id LIMIT 1""", SOURCE, FORMAT)
    if row is None:
        raise RuntimeError("the course_promo format row could not be read")
    return int(row["id"])


async def next_lesson(format_id: int) -> dict[str, str]:
    """The lesson after the last one drafted, round the list."""
    row = await fetch_one(
        """SELECT title FROM content_drafts WHERE source = $1
            ORDER BY id DESC LIMIT 1""", f"reel_format:{format_id}")
    ids = [x["id"] for x in lessons()]
    m = re.search(r"lesson (\S+)", str(row["title"]) if row else "")
    if m and m.group(1) in ids:
        return lessons()[(ids.index(m.group(1)) + 1) % len(ids)]
    return lessons()[0]


async def attempted_today(format_id: int, day: date) -> int:
    row = await fetch_one(
        """SELECT count(*) AS n FROM content_drafts
            WHERE source = $1 AND at >= $2""",
        f"reel_format:{format_id}", day_start(day))
    return int(row["n"]) if row else 0


async def done_this_week(format_id: int, day: date) -> int:
    """Course reels that went out this New York week: posted, or on a card
    (a declined or expired card counts -- Miro's no is not re-asked daily)."""
    row = await fetch_one(
        """SELECT count(*) AS n FROM content_drafts
            WHERE source = $1 AND at >= $2
              AND status IN ('published', 'carded')""",
        f"reel_format:{format_id}", week_start(day))
    return int(row["n"]) if row else 0


async def waiting(format_id: int) -> dict[str, Any] | None:
    """A course reel already rendered this week that has not gone out (held
    by the cap or an hourly allowance): it goes before any new draft, so no
    Veo clip is spent twice."""
    row = await fetch_one(
        """SELECT d.id AS draft_id, d.body, m.id AS media_id
             FROM content_drafts d
             JOIN content_media m ON m.draft_id = d.id AND m.status = 'done'
            WHERE d.source = $1 AND d.status = 'rendered'
              AND d.at >= now() - interval '6 days'
              AND NOT EXISTS (SELECT 1 FROM reel_posts p WHERE p.media_id = m.id)
            ORDER BY m.id DESC LIMIT 1""", f"reel_format:{format_id}")
    return dict(row) if row else None


async def is_course_media(media_id: int) -> bool:
    row = await fetch_one(
        """SELECT f.source FROM content_media m
             JOIN content_drafts d ON d.id = m.draft_id
             JOIN reel_formats f ON d.source = 'reel_format:' || f.id
            WHERE m.id = $1""", media_id)
    return bool(row) and str(row["source"]) == SOURCE


# ------------------------------------------------------------------ the action

def _beats(beats: Any) -> tuple[int | None, str | None]:
    try:
        n = int(beats)
    except (TypeError, ValueError):
        return None, f"beats must be {MIN_BEATS} or {MAX_BEATS}"
    if not MIN_BEATS <= n <= MAX_BEATS:
        return None, f"beats must be {MIN_BEATS} or {MAX_BEATS}, not {n}"
    return n, None


def _assets_missing(item: dict[str, str] | None = None) -> str | None:
    gone = [p.name for p in images(item) if not p.is_file()]
    return f"product image(s) missing: {', '.join(gone)}" if gone else None


async def draft_course_reel(beats: int = MAX_BEATS) -> ActionResult:
    """Draft the next lesson's course reel (Tier AUTO: one draft row; it
    never posts). Composed in code from the data file; no model writes it."""
    n, why = _beats(beats)
    if n is None:
        return ActionResult(False, f"refused: {why}")
    if (why := await asyncio.to_thread(_assets_missing)) is not None:
        return ActionResult(False, f"refused: {why}")
    format_id = await ensure_format()
    item = await next_lesson(format_id)
    if (why := await asyncio.to_thread(_assets_missing, item)) is not None:
        return ActionResult(False, f"refused: {why}")
    # The price is read now, from the store, never from a file or memory.
    price = await read_price(item=item)
    if (why := honesty_refusal(item, n, price)) is not None:
        return ActionResult(False, f"refused: lesson {item['id']}: {why}")
    body = compose(item, n, price)
    row = await fetch_one(
        """INSERT INTO content_drafts (kind, source, title, body, status)
           VALUES ('story_script', $1, $2, $3, 'draft') RETURNING id""",
        f"reel_format:{format_id}", f"Course reel: lesson {item['id']}", body)
    draft_id = int(row["id"])
    script = reel_script.parse(body, max_scenes=MAX_BEATS)
    return ActionResult(
        True, f"drafted course reel {draft_id}: lesson {item['id']} "
              f"({item['title']}), {n} Veo beat(s) and the three product "
              f"images; {price.note()}",
        evidence={"id": draft_id, "format": fmt(item), "lesson": item["id"],
                  "caption": script.caption, "scenes": screen_lines(script),
                  "price": price.record(), "notes": [price.note()]},
        rollback_token=str(draft_id))


async def draft_course_reel_dry(beats: int = MAX_BEATS) -> ActionResult:
    n, why = _beats(beats)
    if n is None:
        return ActionResult(False, f"refused: {why}")
    if (why := await asyncio.to_thread(_assets_missing)) is not None:
        return ActionResult(False, f"refused: {why}")
    return ActionResult(True, f"would draft the next lesson's course reel with "
                              f"{n} Veo beat(s)")


draft_course_reel_rollback = draft_rollback


# ------------------------------------------------------------------ render

def text_refusal(script: reel_script.Script) -> str | None:
    """The synchronous half, for `content._prompt_refusal`."""
    for i, shot in enumerate(shots_of(script), 1):
        if shot not in SHOTS:
            return f"course beat {i} has no catalog shot ({shot!r})"
    words = "\n".join([script.caption, *screen_lines(script)])
    return guard.income_refusal(words) or guard.refusal(" ".join(screen_lines(script)))


async def verify(draft_id: int, script: reel_script.Script) -> str | None:
    """Before any clip: the draft is exactly what the data file composes for
    its lesson, every rule holds, and the product images are on disk."""
    item = lesson_of(script)
    if item is None:
        return f"course draft {draft_id} names no lesson in the course data"
    beats = len(script.scenes)
    if not MIN_BEATS <= beats <= MAX_BEATS:
        return f"course draft {draft_id} has {beats} beat(s), not 1-2"
    price = parse_price_line(script.price)
    if price is None:
        return (f"course draft {draft_id} carries no store price read; "
                f"draft it again")
    expected = reel_script.parse(compose(item, beats, price), max_scenes=MAX_BEATS)
    if expected.price != script.price:
        return f"course draft {draft_id}'s stored price read was changed"
    if expected.scenes != script.scenes:
        return (f"course draft {draft_id} no longer matches lesson {item['id']} "
                f"in the course data")
    if expected.caption != script.caption:
        return f"course draft {draft_id}'s caption was changed"
    if (why := honesty_refusal(item, beats, price)) is not None:
        return why
    return await asyncio.to_thread(_assets_missing, item)


def end_lines(words: str | None, item: dict[str, str] | None = None) -> list[str]:
    """The end card: the label, the course, the price when it was read,
    LINK IN BIO."""
    p = product(item)
    return [LABEL, p["short"], *([words] if words else []), "LINK IN BIO"]


class CourseRenderError(Exception):
    """A step of the course assembly failed; the message is safe to show."""


VEO_SECONDS = 8.0
END_SECONDS = 3.0
MIN_IMAGE_SECONDS = 2.5
BG = "0x0b0d12"


def layout(spoken_s: float, veo_beats: int) -> tuple[float, float]:
    """(each product image's seconds, the whole reel's seconds). The voice
    runs over the clips and the images; the end card follows it."""
    total = max(SECONDS[0] + 0.5, min(SECONDS[1] - 0.5, spoken_s + END_SECONDS))
    left = total - veo_beats * VEO_SECONDS - END_SECONDS
    each = left / 3
    if each < MIN_IMAGE_SECONDS:
        each = MIN_IMAGE_SECONDS
        total = veo_beats * VEO_SECONDS + 3 * each + END_SECONDS
    if total > SECONDS[1] - 0.2 or spoken_s > total - 0.5:
        raise CourseRenderError(
            f"the voice runs {spoken_s:.1f} s; with {veo_beats} clip(s) the reel "
            f"would not fit {SECONDS[0]:.0f}-{SECONDS[1]:.0f} s")
    return each, total


def assemble_argv(clips: list[str], pictures: list[str], voice: str,
                  out_path: str, *, image_s: float, total: float) -> list[str]:
    """One ffmpeg command: clip 1, the three product images (a slow push in,
    a slide, a pull out), clip 2 when there is one, then the end card's
    plain background; the voice over all of it, silence after."""
    from . import ffmpeg
    fps = ffmpeg.FPS
    w, h = ffmpeg.WIDTH, ffmpeg.HEIGHT
    argv = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for c in clips:
        argv += ["-i", c]
    for p in pictures:
        argv += ["-loop", "1", "-framerate", str(fps), "-t", f"{image_s:.3f}",
                 "-i", p]
    argv += ["-f", "lavfi", "-t", f"{END_SECONDS:.3f}",
             "-i", f"color=c={BG}:s={w}x{h}:r={fps}"]
    voice_idx = len(clips) + len(pictures) + 1
    argv += ["-i", voice]
    chains, order = [], []
    for i in range(len(clips)):
        chains.append(f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
                      f"crop={w}:{h},fps={fps},trim=duration={VEO_SECONDS:g},"
                      f"setpts=PTS-STARTPTS,setsar=1[c{i}]")
    frames = max(1, round(image_s * fps))
    side = w - 80
    moves = (  # push in / slide left to right / pull out
        ("min(1+0.0009*on,1.15)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
        ("1.12", f"(iw-iw/zoom)*on/{frames}", "ih/2-(ih/zoom/2)"),
        ("max(1.15-0.0009*on,1)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
    )
    for k in range(len(pictures)):
        z, x, y = moves[k % len(moves)]
        idx = len(clips) + k
        chains.append(
            f"[{idx}:v]scale={side * 2}:{side * 2},zoompan=z='{z}':x='{x}':"
            f"y='{y}':d=1:s={side}x{side}:fps={fps},"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color={BG},setsar=1,"
            f"trim=duration={image_s:.3f},setpts=PTS-STARTPTS[p{k}]")
    end_idx = len(clips) + len(pictures)
    chains.append(f"[{end_idx}:v]setsar=1[e]")
    order = ["[c0]"] + [f"[p{k}]" for k in range(len(pictures))]
    if len(clips) > 1:
        order += [f"[c{i}]" for i in range(1, len(clips))]
    order.append("[e]")
    chains.append("".join(order) + f"concat=n={len(order)}:v=1:a=0[v]")
    chains.append(f"[{voice_idx}:a]apad,atrim=0:{total:.3f}[a]")
    argv += ["-filter_complex", ";".join(chains), "-map", "[v]", "-map", "[a]",
             "-t", f"{total:.3f}"]
    return argv + ffmpeg._encode_tail(out_path)


async def assemble(clips: list[str], script: reel_script.Script, work: str
                   ) -> tuple[str, list[str], bool]:
    """The finished course reel from its Veo clips: (path, notes, captions
    burned). Raises CourseRenderError with the reason."""
    from . import captions, ffmpeg, stills

    def write(path: str, payload: bytes | str) -> None:
        with open(path, "wb" if isinstance(payload, bytes) else "w") as fh:
            fh.write(payload)

    item = lesson_of(script) or {}
    if (why := await asyncio.to_thread(_assets_missing, item or None)) is not None:
        raise CourseRenderError(why)
    text = narration(script)
    audio, voice_note, no_audio = await stills.narration(text)
    if audio is None:
        raise CourseRenderError(f"Fish narration: {no_audio}")
    voice = os.path.join(work, "voice.mp3")
    await asyncio.to_thread(write, voice, audio)
    try:
        said = await ffmpeg.duration(voice)
        image_s, total = layout(said, len(clips))
        built = os.path.join(work, "course.mp4")
        await ffmpeg.run(assemble_argv(clips, [str(p) for p in images(item or None)],
                                       voice,
                                       built, image_s=image_s, total=total))
        price = parse_price_line(script.price)
        sentences = spoken(item, price) if item else [text]
        # The script's own words, spread over the voice by word count: a
        # transcription would caption "$20" as whatever the voice said.
        cues = captions.course_cues(None, sentences, said)
        end_start = total - END_SECONDS
        # The end card's price is the draft's stored store read, or none.
        words = price_words(price)
        ass = os.path.join(work, "course.ass")
        await asyncio.to_thread(write, ass, captions.build_course_ass(
            cues, total, end_start=end_start, label=LABEL, price=words or "",
            end_lines=end_lines(words, item or None)))
        out = os.path.join(work, "out.mp4")
        await ffmpeg.run(ffmpeg.burn_argv(built, ass, out),
                         timeout=ffmpeg.BURN_TIMEOUT_S)
    except ffmpeg.FfmpegError as exc:
        raise CourseRenderError(f"ffmpeg: {exc}") from None
    notes = [(f"course reel: {len(clips)} Veo clip(s), the three product images "
              f"at {image_s:.1f} s each, a {END_SECONDS:.0f} s end card "
              f"({', '.join([*([words] if words else []), 'LINK IN BIO'])}); "
              f"{total:.1f} s"),
             price.note() if price else "price not read: no stored read",
             "bold captions of the script's own words, spread over the voice",
             f"narrated by {voice_note}", "loudness normalised to -14 LUFS"]
    return out, notes, bool(cues)
