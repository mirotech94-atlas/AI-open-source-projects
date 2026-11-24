"""agency.pricing -- the figures a lead may be quoted, decided in code.

Miro, 2026-10-27 (six agents): "NEW customers $500 fixed; RETURNING
customers always $300; free demo as sales asset" (the old cheaper tier is
dropped).

    AGENCY_PRICE_LIST   default 500   a new client, the only figure
    AGENCY_PRICE_FLOOR  default 300   a returning client, the only figure

Unset means Miro's defaults above. Set to "" / "off" / "none" means no
price is configured: a draft then says "[price]" and may name no figure at
all (invariant 6: never guess a number). AGENCY_FIRST_SALE_PRICE is no
longer read; when it is still set, one log line says it is ignored.

**Returning** (`_store.lead_returning`, read fresh for each lead): a won or
paid `agency_deals` row on the same lead, or a lead Miro linked to an
earlier client (`agency_leads.returning_of_lead_id`, principal-only). Never
"the same email domain": that would make every gmail.com lead returning.
A read that fails is `returning=None`: then no figure is allowed, the
drafting prompt names none, and a send that names one is refused.

**What is enforced, and where.** Every currency figure in a draft, an SMS
or a spoken call line is extracted (`find_prices`: "$500", "500 dollars",
"USD 300", "$1.5k", "three hundred dollars") and each one must be the one
allowed figure for that lead (`allowed`); anything else is refused
(`price_refusal`). A payment plan is described in words ("split into two
payments"), never figures. The check runs when a draft is written and
again when it is sent -- a deal recorded in between changes the figure.

**What the site actually includes** (`DELIVERABLES`) is what `build_site`
builds today, read from `sitekit.py` / `launchcheck.py`: mobile-first pages,
SEO basics (titles, meta descriptions, LocalBusiness and FAQ JSON-LD,
robots.txt, sitemap, share images), a contact page with an enquiry form,
privacy and terms pages. There is NO AI chat widget anywhere in
`agency/`, so until `AGENCY_AI_CHAT_BUILT` is set a draft that promises
one is refused (`promise_refusal`).
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

log = logging.getLogger("atlas.agency.pricing")

DEFAULT_LIST = 500        # a new client
DEFAULT_FLOOR = 300       # a returning client
PLACEHOLDER = "[price]"
DEMO = "a free demo first"
RETIRED_ENV = "AGENCY_FIRST_SALE_PRICE"


def _env_price(name: str, default: int) -> int | None:
    raw = os.getenv(name)
    if raw is None:
        return default
    text = raw.strip().replace("$", "").replace(",", "")
    if text.lower() in ("", "off", "none", "unset", "no"):
        return None
    try:
        value = int(float(text))
    except ValueError:
        return None
    return value if value > 0 else None


def list_price() -> int | None:
    """A new client's price."""
    return _env_price("AGENCY_PRICE_LIST", DEFAULT_LIST)


def floor_price() -> int | None:
    """A returning client's price."""
    return _env_price("AGENCY_PRICE_FLOOR", DEFAULT_FLOOR)


_WARNED: set[str] = set()


def warn_retired_env() -> None:
    """One log line when the retired first-sale variable is still set."""
    if os.getenv(RETIRED_ENV) is not None and RETIRED_ENV not in _WARNED:
        _WARNED.add(RETIRED_ENV)
        log.info("%s is set but ignored: the agency quotes $%s new / $%s returning",
                 RETIRED_ENV, list_price(), floor_price())


def ai_chat_built() -> bool:
    return os.getenv("AGENCY_AI_CHAT_BUILT", "").strip().lower() in (
        "1", "true", "yes", "on")


# ------------------------------------------------------------- the terms

@dataclass(frozen=True, slots=True)
class Terms:
    """The price rules for one lead at one moment. `returning` is read from
    the lead's own deals and link; None when it could not be read (or no
    lead was named) -- never assumed new."""

    list_price: int | None          # a new client
    floor: int | None               # a returning client
    returning: bool | None = None
    read_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def configured(self) -> bool:
        return self.list_price is not None and self.floor is not None

    @property
    def price(self) -> int | None:
        """The one figure this lead may be quoted, or None."""
        if not self.configured or self.returning is None:
            return None
        return self.floor if self.returning else self.list_price

    def allowed(self) -> frozenset[int]:
        price = self.price
        return frozenset() if price is None else frozenset({price})

    def describe(self) -> str:
        """The block a drafting prompt is given: the only figure it may use."""
        if not self.configured:
            return (f"No price is configured. Never name a figure: write "
                    f"{PLACEHOLDER} wherever a price would go. Offer {DEMO}.")
        if self.returning is None:
            return ("Whether this is a new or a returning client could not be read. "
                    f"Never name a figure: write {PLACEHOLDER} wherever a price "
                    f"would go. Offer {DEMO}.")
        who = "a returning client" if self.returning else "a new client"
        return "\n".join([
            (f"Pricing: ${self.list_price} for a new client, ${self.floor} for a "
             f"returning client, {DEMO}."),
            f"This lead is {who}: the price is ${self.price}. It is fixed.",
            "Describe a payment plan in words, never as per-payment figures.",
            ("This is the only figure you may write. No other number with a "
             "currency, no percentages off, no discounts."),
        ])


async def terms(lead_id: int | None = None) -> Terms:
    """Fresh terms for one lead: config read now, returning read now."""
    from . import _store
    warn_retired_env()
    returning: bool | None = None
    if lead_id is not None:
        try:
            returning = bool(await _store.lead_returning(int(lead_id)))
        except Exception:  # noqa: BLE001
            returning = None
    return Terms(list_price(), floor_price(), returning, datetime.now(UTC))


# ------------------------------------------------------- reading figures

_WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
             "seven": 7, "eight": 8, "nine": 9, "a": 1}
_AMOUNT = r"(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?\s*(k\b)?"
_PRICE_PATTERNS = (
    re.compile(r"\$\s?" + _AMOUNT, re.IGNORECASE),
    re.compile(r"\busd\s?\$?\s?" + _AMOUNT, re.IGNORECASE),
    re.compile(_AMOUNT + r"\s*(?:dollars?|bucks|usd)\b", re.IGNORECASE),
)
_WORDS_PRICE = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|a)\s+hundred"
    r"(?:\s+and\s+(\w+))?\s+(?:dollars?|bucks)\b", re.IGNORECASE)
_TENS = {"fifty": 50, "twenty": 20, "thirty": 30, "forty": 40, "sixty": 60,
         "seventy": 70, "eighty": 80, "ninety": 90, "ten": 10}


def _value(whole: str, cents: str | None, k: str | None) -> int:
    number = float(whole.replace(",", "") + (f".{cents}" if cents else ""))
    if k:
        number *= 1000
    return round(number)


def find_prices(text: str) -> list[int]:
    """Every currency figure named in `text`, in dollars, in order found."""
    found: list[tuple[int, int]] = []
    seen_spans: list[tuple[int, int]] = []
    for pattern in _PRICE_PATTERNS:
        for match in pattern.finditer(text or ""):
            span = match.span()
            if any(s <= span[0] < e or s < span[1] <= e for s, e in seen_spans):
                continue
            seen_spans.append(span)
            found.append((span[0], _value(match.group(1), match.group(2),
                                          match.group(3))))
    for match in _WORDS_PRICE.finditer(text or ""):
        value = _WORD_NUM[match.group(1).lower()] * 100
        extra = (match.group(2) or "").lower()
        value += _TENS.get(extra, 0)
        found.append((match.start(), value))
    return [value for _pos, value in sorted(found)]


# A figure the lead names is a counter-offer only beside words that make it
# one: "we pay $50 a month now" or "my last site cost $200" is not.
_COUNTER = re.compile(
    r"\bwould\s+you\s+(?:do|take|accept|go)\b|\bhow\s+about\b|\bcan\s+you\s+do\b|"
    r"\bcould\s+you\s+do\b|\b(?:my|our)\s+budget\b|\bbudget\s+(?:is|of)\b|"
    r"\bi(?:'ll|\s+will|\s+can|\s+could)\s+(?:pay|offer|do)\b|\boffer(?:ing)?\b|"
    r"\bwilling\s+to\s+pay\b|\bbest\s+i\s+can\s+do\b|\bmeet\s+(?:me|us)\b|"
    r"\bfor\s+\$\s?\d", re.IGNORECASE)


def counter_offer(text: str) -> list[int]:
    """The figures a lead offers, or [] when they are not offering one."""
    return find_prices(text) if _COUNTER.search(text or "") else []


_ONES = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
         "seven": 7, "eight": 8, "nine": 9}
_SPOKEN_HUNDREDS = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine)\s+(hundred|thousand)"
    r"(?:\s+(?:and\s+)?(\w+))?", re.IGNORECASE)
_SPOKEN_SHORT = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine)[\s-]+(ten|twenty|thirty|"
    r"forty|fifty|sixty|seventy|eighty|ninety)\b", re.IGNORECASE)
_BARE = re.compile(r"(?<![\d.,])(\d{3,}(?:,\d{3})*)(?![\d])")


def spoken_prices(text: str) -> list[int]:
    """Figures a spoken line can carry without a currency word: "two
    hundred", "three-fifty", "five hundred and fifty", or a bare number of
    three or more digits. For the call only, where "$" is never said."""
    found: list[int] = []
    for m in _SPOKEN_HUNDREDS.finditer(text or ""):
        unit = 100 if m.group(2).lower() == "hundred" else 1000
        found.append(_ONES[m.group(1).lower()] * unit
                     + _TENS.get((m.group(3) or "").lower(), 0))
    for m in _SPOKEN_SHORT.finditer(text or ""):
        found.append(_ONES[m.group(1).lower()] * 100 + _TENS[m.group(2).lower()])
    found += [int(m.group(1).replace(",", "")) for m in _BARE.finditer(text or "")]
    return found


OBJECTION = re.compile(
    r"\btoo\s+(?:much|expensive|pricey|high|steep|rich)\b|\bcan'?t\s+afford\b"
    r"|\bcannot\s+afford\b|\bout\s+of\s+(?:my|our)\s+budget\b|\bover\s+(?:my|our)"
    r"\s+budget\b|\bcheaper\b|\blower\s+(?:the\s+)?price\b|\bany\s+(?:wiggle|flex)"
    r"|\bbest\s+(?:price|you\s+can\s+do)\b|\bexpensive\b|\bno\s+budget\b"
    r"|\bthat'?s\s+a\s+lot\b|\bless\s+than\s+(?:that|\$)", re.IGNORECASE)


def objected(*texts: str) -> bool:
    """Did the lead's own words object to the price? Code, never a model."""
    return any(OBJECTION.search(t or "") for t in texts)


# ------------------------------------------------------ the lead says yes
#
# 2026-10-06 (Miro: "approve invoices too"): a lead's own words accepting
# the price, read in code, never by a model. Plain assent -- with no
# counter-offer, no objection, no open question and no "but" -- is an
# acceptance of the one allowed figure they named, else of the one figure
# our last sent reply quoted. Never of a price nobody put to them: "Great
# work!" in answer to a free preview is not a yes to $500. Anything less
# clear is left to the drafted reply.

ACCEPTANCE = re.compile(
    r"^\W*(?:yes|yep|yeah|sure|ok(?:ay)?|absolutely|definitely|perfect|great|deal"
    r"|sounds\s+good)\b"
    r"|\blet'?s\s+(?:do\s+it|go|get\s+started|move\s+forward|proceed)\b|\bgo\s+ahead\b"
    r"|\bit'?s\s+a\s+deal\b|\b(?:i|we)'?ll\s+take\s+it\b|\bsign\s+(?:me|us)\s+up\b"
    r"|\b(?:i'?m|we'?re)\s+in\b|\bcount\s+(?:me|us)\s+in\b"
    r"|\bsend\s+(?:me\s+|us\s+|over\s+)*(?:the|that|your)\s+(?:link|agreement|invoice"
    r"|contract|payment\s+link|paperwork)\b"
    r"|\b(?:where|how)\s+do\s+(?:i|we)\s+pay\b|\bready\s+to\s+(?:pay|start|go|sign)\b",
    re.IGNORECASE)
HESITATION = re.compile(
    r"\?|\bthink\s+(?:about|it\s+over)\b|\bnot\s+(?:sure|yet|now|ready|today)\b"
    r"|\bmaybe\b|\blater\b|\bbut\b|\bhowever\b|\bbefore\s+(?:i|we)\b|\bfirst\s+(?:i|we)\b"
    r"|\bquestion\b|\bwhat\s+(?:about|if)\b|\b(?:can|could|would)\s+(?:you|we|i|it)\b"
    r"|\bif\b|\bunless\b|\bno\b|\bnot\s+interested\b", re.IGNORECASE)


def accepted_price(text: str, terms_: Terms, *,
                   quoted: list[int] | None = None) -> int | None:
    """The allowed price a lead's words accept, or None when they do not
    clearly accept one. `quoted` is every figure our last sent reply named."""
    words = text or ""
    if not terms_.configured or not ACCEPTANCE.search(words):
        return None
    if HESITATION.search(words) or objected(words) or counter_offer(words):
        return None
    allowed = terms_.allowed()
    named = sorted(set(find_prices(words)))
    if named:
        return named[0] if len(named) == 1 and named[0] in allowed else None
    ours = sorted({int(p) for p in (quoted or [])})
    return ours[0] if len(ours) == 1 and ours[0] in allowed else None


def price_refusal(text: str, terms_: Terms) -> str | None:
    """Why `text` may not go out on price grounds, or None."""
    prices = find_prices(text)
    if not prices:
        return None
    if not terms_.configured:
        return (f"it names ${prices[0]} but no price is configured "
                f"(AGENCY_PRICE_LIST / AGENCY_PRICE_FLOOR); write {PLACEHOLDER}")
    if terms_.returning is None:
        return (f"it names ${prices[0]} but whether the lead is new or returning "
                f"could not be read; write {PLACEHOLDER}")
    allowed = terms_.allowed()
    for price in prices:
        if price not in allowed:
            who = "returning" if terms_.returning else "new"
            return (f"it quotes ${price}, which is not a price for a {who} client "
                    f"(the price is ${terms_.price})")
    return None


# ----------------------------------------------- what the site includes

DELIVERABLES: tuple[str, ...] = (
    "a fast, mobile-first website built for their business",
    ("SEO basics: a unique title and description on every page, LocalBusiness "
     "and FAQ structured data, a sitemap and robots.txt, and share images"),
    "a contact page with an enquiry form, plus privacy and terms pages",
)
AI_CHAT = "an AI chat assistant on the site"

_CHAT_PROMISE = re.compile(
    r"\b(?:ai[\s-]?chat\w*|chat\s?bots?|chat\s+widgets?|live\s+chat|chat\s+assistant"
    r"|ai\s+assistant\s+on\s+(?:the|your)\s+site|virtual\s+assistant)\b",
    re.IGNORECASE)
_GUARANTEE = re.compile(
    r"\bguarantee[sd]?\b.{0,40}\b(?:rank|#\s?1|first\s+page|top\s+of\s+google|"
    r"customers|sales|leads|traffic|results)\b", re.IGNORECASE)


def deliverables() -> tuple[str, ...]:
    return DELIVERABLES + ((AI_CHAT,) if ai_chat_built() else ())


def promise_refusal(text: str) -> str | None:
    """A promise the build does not keep."""
    if not ai_chat_built() and _CHAT_PROMISE.search(text or ""):
        return ("it promises an AI chat on the site, which build_site does not "
                "deliver yet (AGENCY_AI_CHAT_BUILT is not set)")
    if _GUARANTEE.search(text or ""):
        return "it guarantees a ranking or a business result nobody can promise"
    return None


def deliverables_block() -> str:
    lines = "\n".join(f"- {d}" for d in deliverables())
    extra = ("" if ai_chat_built() else
             "\nNot included yet, never mention or promise it: an AI chat on the "
             "site.")
    return f"What the website includes (promise nothing else):\n{lines}{extra}"
