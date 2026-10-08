"""agency.pricing -- the figures a lead may be quoted, decided in code.

Miro, 2026-09-29: "$500 for a fully working website, SEO and an AI chat
included. Negotiation floor $300, never lower. While the agency has zero
closed or paid deals, a lead who says $500 is too much may be quoted $100.
Once any deal is recorded as won or paid, the floor goes back to $300."

    AGENCY_PRICE_LIST        default 500   the anchor
    AGENCY_PRICE_FLOOR       default 300   never quoted lower
    AGENCY_FIRST_SALE_PRICE  default 100   only on a price objection, and
                                           only while no deal is won or paid
    AGENCY_CONCESSIONS       default "a smaller first phase|a payment plan"
                                           the ladder between list and floor,
                                           '|'-separated, never a discount

Unset means Miro's defaults above. Set to "" / "off" / "none" means no
price is configured: a draft then says "[price]" and may name no figure at
all (invariant 6: never guess a number).

**What is enforced, and where.** Every currency figure in a draft, an SMS
or a spoken call line is extracted (`find_prices`: "$500", "500 dollars",
"USD 300", "$1.5k", "three hundred dollars") and each one must be in the
allowed set for that moment (`allowed`): the list price, the regular floor,
and the first-sale price only when (a) a fresh read of `agency_deals` found
no won or paid deal -- an unreadable table is never read as zero -- and
(b) the lead's own words object to the price (`OBJECTION`). Anything else is
refused (`price_refusal`): a figure below the floor that applies, or a
figure that is not configured at all, which is what stops an invented
anchor, an invented discount or a per-instalment amount. A payment plan is
therefore described in words ("split into two payments"), never figures.
The check runs when a draft is written and again when it is sent -- a deal
recorded in between takes the first-sale price off the table.

**What the site actually includes** (`DELIVERABLES`) is what `build_site`
builds today, read from `sitekit.py` / `launchcheck.py`: mobile-first pages,
SEO basics (titles, meta descriptions, LocalBusiness and FAQ JSON-LD,
robots.txt, sitemap, share images), a contact page with an enquiry form,
privacy and terms pages. There is NO AI chat widget anywhere in
`agency/`, so until `AGENCY_AI_CHAT_BUILT` is set a draft that promises
one is refused (`promise_refusal`) -- Miro's package names it, the build
does not deliver it yet.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

DEFAULT_LIST = 500
DEFAULT_FLOOR = 300
DEFAULT_FIRST_SALE = 100
DEFAULT_CONCESSIONS = ("a smaller first phase (the home page first, the rest after)",
                       "a payment plan (the price split into two payments)")
PLACEHOLDER = "[price]"


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
    return _env_price("AGENCY_PRICE_LIST", DEFAULT_LIST)


def floor_price() -> int | None:
    return _env_price("AGENCY_PRICE_FLOOR", DEFAULT_FLOOR)


def first_sale_price() -> int | None:
    return _env_price("AGENCY_FIRST_SALE_PRICE", DEFAULT_FIRST_SALE)


def concessions() -> tuple[str, ...]:
    raw = os.getenv("AGENCY_CONCESSIONS")
    if raw is None:
        return DEFAULT_CONCESSIONS
    return tuple(step.strip() for step in raw.split("|") if step.strip())


def ai_chat_built() -> bool:
    return os.getenv("AGENCY_AI_CHAT_BUILT", "").strip().lower() in (
        "1", "true", "yes", "on")


# ------------------------------------------------------------- the terms

@dataclass(frozen=True, slots=True)
class Terms:
    """The price rules at one moment. `deals` is the won/paid count read
    from `agency_deals`, None when it could not be read -- never assumed 0."""

    list_price: int | None
    floor: int | None
    first_sale: int | None
    deals: int | None
    read_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    ladder: tuple[str, ...] = DEFAULT_CONCESSIONS

    @property
    def configured(self) -> bool:
        return self.list_price is not None and self.floor is not None

    @property
    def first_sale_open(self) -> bool:
        """The first-sale price exists and no deal is on record -- a read that
        failed (None) keeps it closed."""
        return self.first_sale is not None and self.deals == 0

    def floor_now(self, objection: bool) -> int | None:
        """The lowest figure that may be quoted at this moment."""
        if not self.configured:
            return None
        if objection and self.first_sale_open:
            return min(self.first_sale, self.floor)       # type: ignore[type-var]
        return self.floor

    def allowed(self, objection: bool) -> frozenset[int]:
        if not self.configured:
            return frozenset()
        prices = {self.list_price, self.floor}
        if objection and self.first_sale_open:
            prices.add(self.first_sale)
        return frozenset(p for p in prices if p is not None)

    def describe(self, objection: bool) -> str:
        """The block a drafting prompt is given: the only figures it may use."""
        if not self.configured:
            return (f"No price is configured. Never name a figure: write "
                    f"{PLACEHOLDER} wherever a price would go.")
        lines = [f"List price (anchor with this): ${self.list_price}.",
                 f"Floor: ${self.floor}. Never lower."]
        if self.ladder:
            lines.append("Concessions, in this order, before moving from the list "
                         "price toward the floor: " + "; then ".join(self.ladder)
                         + ". Describe a payment plan in words, never as "
                           "per-payment figures.")
        if objection and self.first_sale_open:
            lines.append(f"First-sale price: ${self.first_sale}. Offer it only "
                         "because they said the price is too much, and say "
                         "plainly it is because they would be among Miro's "
                         "first clients.")
        lines.append("These are the only figures you may write. No other number "
                     "with a currency, no percentages off, no discounts.")
        return "\n".join(lines)


async def terms() -> Terms:
    """Fresh terms: config read now, deals read from the database now."""
    from . import _store
    try:
        deals: int | None = await _store.count_closed_deals()
    except Exception:                                   # noqa: BLE001
        deals = None
    return Terms(list_price(), floor_price(), first_sale_price(), deals,
                 datetime.now(UTC), concessions())


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


def accepted_price(text: str, terms_: Terms, *, objection: bool,
                   quoted: list[int] | None = None) -> int | None:
    """The allowed price a lead's words accept, or None when they do not
    clearly accept one. `quoted` is every figure our last sent reply named."""
    words = text or ""
    if not terms_.configured or not ACCEPTANCE.search(words):
        return None
    if HESITATION.search(words) or objected(words) or counter_offer(words):
        return None
    allowed = terms_.allowed(objection)
    named = sorted(set(find_prices(words)))
    if named:
        return named[0] if len(named) == 1 and named[0] in allowed else None
    ours = sorted({int(p) for p in (quoted or [])})
    return ours[0] if len(ours) == 1 and ours[0] in allowed else None


def price_refusal(text: str, terms_: Terms, *, objection: bool) -> str | None:
    """Why `text` may not go out on price grounds, or None."""
    prices = find_prices(text)
    if not prices:
        return None
    if not terms_.configured:
        return (f"it names ${prices[0]} but no price is configured "
                f"(AGENCY_PRICE_LIST / AGENCY_PRICE_FLOOR); write {PLACEHOLDER}")
    floor = terms_.floor_now(objection)
    allowed = terms_.allowed(objection)
    for price in prices:
        if floor is not None and price < floor:
            return (f"it quotes ${price}, below the ${floor} floor that applies "
                    f"now (deals on record: "
                    f"{'unreadable' if terms_.deals is None else terms_.deals})")
        if price not in allowed:
            return (f"it quotes ${price}, which is not a configured price "
                    f"(allowed now: {', '.join(f'${p}' for p in sorted(allowed))})")
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
