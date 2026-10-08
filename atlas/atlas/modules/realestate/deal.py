"""The deal arithmetic and the card -- pure, no I/O.

Everything here is a function of what Miro typed and what RentCast returned
(with the time it was read). A figure that is not known stays None and is
printed as "unknown", never 0: a missing ARV, repair figure or rent makes
every number that depends on it unknown too (invariant 6).

The wholesaling formula, as the reel states it:

    MAO = ARV_FACTOR x ARV - repairs - assignment fee      (ARV_FACTOR = 0.70)

and the rental view beside it: rent estimate, a rough cap rate at an assumed
expense ratio, and the 1% rule. Both use an all-in basis of list price plus
repairs, and say so.

The offer draft and the contract checklist are TEXT for Miro. ATLAS never
contacts an agent, never sends a message to a third party, and never signs or
generates a binding contract.
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import unquote, urlsplit

from ...kernel.permissions import wrap_untrusted

DISCLAIMER = ("Estimates only — not an appraisal or advice. Verify comps, title, "
              "and your state's rules on wholesaling/assignment (some states "
              "require a license or disclosures) before making an offer.")

# $/sqft for each repair level -- assumptions, stated on every card and
# editable with REALESTATE_REPAIR_BANDS ("turnkey:0,light:15,medium:35,heavy:60").
DEFAULT_BANDS: dict[str, float] = {"turnkey": 0.0, "light": 15.0, "medium": 35.0,
                                   "heavy": 60.0}
DEFAULT_ARV_FACTOR = 0.70
DEFAULT_FEE = 10_000.0
EXPENSE_RATIO = 0.50        # the "50% rule": half the rent goes to costs
NOTES_MAX = 1500
ADDRESS_MAX = 200


def _env_float(name: str, default: float, lo: float, hi: float) -> float:
    try:
        value = float(os.getenv(name, "") or default)
    except ValueError:
        return default
    return value if lo <= value <= hi else default


def arv_factor() -> float:
    """REALESTATE_ARV_FACTOR, default 0.70 (the "70% rule")."""
    return _env_float("REALESTATE_ARV_FACTOR", DEFAULT_ARV_FACTOR, 0.3, 0.95)


def default_fee() -> float:
    """REALESTATE_ASSIGNMENT_FEE, default $10,000."""
    return _env_float("REALESTATE_ASSIGNMENT_FEE", DEFAULT_FEE, 0, 1_000_000)


def repair_bands() -> dict[str, float]:
    bands = dict(DEFAULT_BANDS)
    for pair in os.getenv("REALESTATE_REPAIR_BANDS", "").split(","):
        name, _, value = pair.partition(":")
        name = name.strip().lower()
        if name in bands:
            try:
                got = float(value)
            except ValueError:
                continue
            if 0 <= got <= 1000:
                bands[name] = got
    return bands


# ------------------------------------------------------------------ parsing

_MONEY = re.compile(r"^\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*([km])?$", re.IGNORECASE)


def money(value: Any) -> float | None:
    """'$250k', '250,000', '1.2m', 250000 -> a float; anything else None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value) if math.isfinite(value) and value >= 0 else None
    text = str(value).strip().replace(" ", "")
    if not text:
        return None
    m = _MONEY.match(text)
    if not m:
        return None
    try:
        number = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    suffix = (m.group(2) or "").lower()
    number *= {"k": 1_000, "m": 1_000_000}.get(suffix, 1)
    return number if math.isfinite(number) else None


def number(value: Any) -> float | None:
    """A plain positive number ('1,400', 1400) or None."""
    got = money(value)
    return got if got and got > 0 else None


class NotAnAddress(ValueError):
    """The input cannot be used as an address without opening a web page."""


def address_from(text: str) -> str:
    """A street address from what Miro gave. A zillow.com link is read for
    the address its PATH spells out ('/homedetails/12-Oak-St-Austin-TX-78701/
    123_zpid/') -- the page itself is never requested: Zillow has no public
    API and its terms forbid scraping. Any other link is refused."""
    text = " ".join(str(text or "").split())
    if not text:
        raise NotAnAddress("no address given")
    if re.match(r"^(https?://|www\.)", text, re.IGNORECASE) or "zillow.com" in text.lower():
        url = text if "://" in text else f"https://{text}"
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if host == "zillow.com" or host.endswith(".zillow.com"):
            segs = [unquote(s) for s in parts.path.split("/") if s]
            if len(segs) >= 2 and segs[0].lower() == "homedetails":
                slug = segs[1]
                if not re.fullmatch(r"\d+_zpid", slug) and "-" in slug:
                    return slug.replace("-", " ")[:ADDRESS_MAX]
            raise NotAnAddress("that Zillow link has no address in it; paste the "
                               "street address (ATLAS never opens zillow.com)")
        raise NotAnAddress("paste the street address, not a link — ATLAS does not "
                           "open listing pages")
    return text[:ADDRESS_MAX]


_KV = re.compile(r"^(arv|repairs?|sqft|fee|rent)=(\S+)$", re.IGNORECASE)


# Only when an address (a house number) or a link follows: "Deal Island Rd"
# is an address, not a command word.
_COMMAND = re.compile(r"^\s*(?:/atlas\s+)?deal\b[:\s]*(?=\d|https?:|www\.)",
                      re.IGNORECASE)


def strip_command(text: str) -> str:
    """'/atlas deal 12 Oak St' or 'deal: 12 Oak St' -> '12 Oak St'."""
    return _COMMAND.sub("", str(text or ""), count=1)


def parse_line(line: str) -> dict[str, Any]:
    """`<address> | <list price> | <notes>` -> fields. A segment made only of
    key=value tokens (arv=310k repairs=medium sqft=1400 fee=8k rent=off) sets
    those inputs; anything else after the address and price is notes."""
    segs = [s.strip() for s in strip_command(str(line or "")).split("|")]
    out: dict[str, Any] = {"address": segs[0] if segs else ""}
    notes: list[str] = []
    for i, seg in enumerate(segs[1:]):
        if not seg:
            continue
        if i == 0 and money(seg) is not None:
            out["list_price"] = seg
            continue
        tokens = seg.split()
        found = [_KV.match(t) for t in tokens]
        if tokens and all(found):
            for m in found:
                assert m is not None
                key, value = m.group(1).lower(), m.group(2)
                if key in ("repair", "repairs"):
                    if value.lower() in DEFAULT_BANDS:
                        out["repair_level"] = value.lower()
                    else:
                        out["repairs"] = value
                elif key == "rent":
                    out["skip_rent"] = value.lower() in ("off", "no", "skip", "0")
                elif key == "fee":
                    out["assignment_fee"] = value
                else:
                    out[key] = value
            continue
        notes.append(seg)
    if notes:
        out["notes"] = " | ".join(notes)
    return out


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on", "skip")


# ------------------------------------------------------------------ the numbers

@dataclass(frozen=True, slots=True)
class Estimate:
    """One figure from a source, with when it was read. `value` None means
    unknown, and `why` says why."""

    value: float | None
    source: str = ""
    read_at: datetime | None = None
    why: str = ""
    comps: int | None = None
    low: float | None = None
    high: float | None = None
    cached: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Repairs:
    value: float | None
    basis: str                 # how it was worked out, in words


def repairs_for(level: str | None, dollars: Any, sqft: float | None,
                sqft_source: str = "") -> Repairs:
    """A dollar figure wins; otherwise level x sqft from the bands. A level
    with no sqft is unknown, except turnkey ($0 whatever the size). Nothing
    at all is unknown -- never an assumed level."""
    fixed = money(dollars)
    if fixed is not None:
        return Repairs(fixed, f"entered: {usd(fixed)}")
    lvl = (level or "").strip().lower()
    bands = repair_bands()
    if not lvl:
        return Repairs(None, "unknown — give a repair level (turnkey/light/medium/"
                             "heavy) or a dollar figure")
    if lvl not in bands:
        return Repairs(None, f"unknown — {lvl!r} is not a repair level "
                             f"({'/'.join(bands)})")
    rate = bands[lvl]
    if rate == 0:
        return Repairs(0.0, f"{lvl}: assumed $0/sqft")
    if not sqft:
        return Repairs(None, f"unknown — {lvl} is ${rate:,.0f}/sqft but the square "
                             f"footage is unknown (add sqft=…)")
    src = f", sqft from {sqft_source}" if sqft_source else ""
    return Repairs(rate * sqft, f"{lvl}: assumed ${rate:,.0f}/sqft × "
                                f"{sqft:,.0f} sqft{src}")


def mao(arv: float | None, repairs: float | None, fee: float | None,
        factor: float | None = None) -> float | None:
    """ARV_FACTOR x ARV - repairs - fee; None when any input is unknown."""
    if arv is None or repairs is None or fee is None:
        return None
    return (arv_factor() if factor is None else factor) * arv - repairs - fee


@dataclass(frozen=True, slots=True)
class Rental:
    rent: float | None
    basis: float | None          # list price + repairs
    cap_rate: float | None       # annual NOI / basis
    one_percent: float | None    # monthly rent / basis
    why: str = ""


def rental(rent: float | None, list_price: float | None,
           repairs: float | None) -> Rental:
    if rent is None:
        return Rental(None, None, None, None, "rent unknown")
    if list_price is None or repairs is None:
        return Rental(rent, None, None, None,
                      "no all-in basis (needs the list price and repairs)")
    basis = list_price + repairs
    if basis <= 0:
        return Rental(rent, None, None, None, "all-in basis is $0")
    noi = rent * 12 * (1 - EXPENSE_RATIO)
    return Rental(rent, basis, noi / basis, rent / basis)


def round_down(value: float, step: float = 500.0) -> float:
    return math.floor(value / step) * step


def verdict(max_offer: float | None, list_price: float | None, why: str = "") -> str:
    if max_offer is None:
        return f"Can't say — {why or 'the MAO is unknown'}."
    if max_offer <= 0:
        return "Numbers don't work at any price (the MAO is $0 or less)."
    cap = usd(round_down(max_offer))
    if list_price is None:
        return f"Numbers work at ≤ {cap} (no list price given to compare)."
    if list_price <= max_offer:
        return f"Numbers work at list ({usd(list_price)} ≤ MAO {usd(max_offer)})."
    return f"Numbers don't work at list — they work at ≤ {cap}."


# ------------------------------------------------------------------ rendering

def usd(value: float | None) -> str:
    if value is None:
        return "unknown"
    sign = "−" if value < 0 else ""
    return f"{sign}${abs(value):,.0f}"


def pct(value: float | None, digits: int = 1) -> str:
    return "unknown" if value is None else f"{value * 100:.{digits}f}%"


def slack_safe(text: str, limit: int) -> str:
    """Pasted text shown in Slack mrkdwn: no <!channel>, no <@U…> links,
    no stray formatting control, truncated."""
    text = " ".join(str(text or "").split())
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def at(when: datetime | None) -> str:
    return "time unknown" if when is None else f"{when:%Y-%m-%d %H:%M} UTC"


@dataclass(frozen=True, slots=True)
class Analysis:
    address: str
    list_price: float | None
    notes: str
    arv: Estimate
    rent: Estimate
    repairs: Repairs
    fee: float
    fee_source: str
    factor: float
    mao: float | None
    spread: float | None          # MAO - list price; positive = list under MAO
    rental: Rental
    verdict: str
    calls: str                    # "ATLAS counted 3/50 RentCast calls this month"
    sqft: float | None = None


def analyse(*, address: str, list_price: float | None, notes: str, arv: Estimate,
            rent: Estimate, repair_level: str | None, repairs_dollars: Any,
            sqft: float | None, sqft_source: str, fee: Any, calls: str) -> Analysis:
    rep = repairs_for(repair_level, repairs_dollars, sqft, sqft_source)
    given_fee = money(fee)
    fee_value = given_fee if given_fee is not None else default_fee()
    fee_source = "entered" if given_fee is not None else "default, REALESTATE_ASSIGNMENT_FEE"
    factor = arv_factor()
    max_offer = mao(arv.value, rep.value, fee_value, factor)
    spread = (max_offer - list_price
              if max_offer is not None and list_price is not None else None)
    why = ("the ARV is unknown" if arv.value is None
           else "the repairs are unknown" if rep.value is None else "")
    return Analysis(address, list_price, notes, arv, rent, rep, fee_value,
                    fee_source, factor, max_offer, spread,
                    rental(rent.value, list_price, rep.value),
                    verdict(max_offer, list_price, why), calls, sqft)


def _arv_line(a: Analysis) -> str:
    e = a.arv
    if e.value is None:
        return f"*ARV:* unknown — {e.why}"
    comps = "comparables: unknown" if e.comps is None else f"{e.comps} comparables"
    rng = (f", range {usd(e.low)}–{usd(e.high)}"
           if e.low is not None and e.high is not None else "")
    warn = " ⚠ no comparables behind it" if e.comps == 0 else ""
    cached = " (cached)" if e.cached else ""
    note = ("" if e.source == "entered" else
            "\n    _RentCast's value estimate is for the property as it stands, "
            "used here as the ARV — check the comps' condition against the "
            "after-repair house._")
    return (f"*ARV:* {usd(e.value)} — {e.source}, {comps}{rng}, "
            f"read {at(e.read_at)}{cached}{warn}{note}")


def card(a: Analysis) -> str:
    lines = [f"*Deal check: {slack_safe(a.address, ADDRESS_MAX)}*",
             f"*List price:* {usd(a.list_price) if a.list_price is not None else 'not given'}",
             _arv_line(a),
             f"*Repairs:* {usd(a.repairs.value)} — {a.repairs.basis}",
             "    _Bands ($/sqft, REALESTATE_REPAIR_BANDS): "
             + ", ".join(f"{k} ${v:,.0f}" for k, v in repair_bands().items()) + "_",
             f"*Assignment fee:* {usd(a.fee)} ({a.fee_source})"]
    if a.mao is None:
        lines.append(f"*MAO* = {a.factor:.0%} × ARV − repairs − fee = unknown "
                     f"(ARV {usd(a.arv.value)}, repairs {usd(a.repairs.value)})")
    else:
        lines.append(f"*MAO* = {a.factor:.0%} × {usd(a.arv.value)} − "
                     f"{usd(a.repairs.value)} − {usd(a.fee)} = *{usd(a.mao)}*")
    if a.spread is not None:
        side = "under" if a.spread >= 0 else "over"
        lines.append(f"*Spread vs list:* {usd(a.spread)} (list is {side} the MAO)")
    else:
        lines.append("*Spread vs list:* unknown")
    r, rent = a.rental, a.rent
    if rent.value is None:
        lines.append(f"*Rental view:* rent unknown — {rent.why}")
    else:
        head = (f"*Rental view:* rent {usd(rent.value)}/mo — {rent.source}, "
                f"read {at(rent.read_at)}{' (cached)' if rent.cached else ''}")
        if r.basis is None:
            lines.append(f"{head}; cap rate and 1% rule unknown — {r.why}")
        else:
            ok = "passes" if (r.one_percent or 0) >= 0.01 else "fails"
            lines.append(f"{head}; all-in {usd(r.basis)} (list + repairs); rough cap "
                         f"rate {pct(r.cap_rate)} (assumes {EXPENSE_RATIO:.0%} of rent "
                         f"to expenses); 1% rule {ok} ({pct(r.one_percent, 2)} of "
                         f"all-in per month)")
    lines.append(f"*Verdict:* {a.verdict}")
    if a.notes:
        lines.append(f"*Your notes (as pasted):* {slack_safe(a.notes, 300)}")
    lines.append(f"_{a.calls}._")
    lines.append(f"_{DISCLAIMER}_")
    return "\n".join(lines)


def offer_amount(a: Analysis) -> float | None:
    if a.mao is None or a.mao <= 0:
        return None
    top = a.mao if a.list_price is None else min(a.mao, a.list_price)
    got = round_down(top)
    return got if got > 0 else None


def offer_draft(a: Analysis) -> str | None:
    """A message Miro may send himself. ATLAS sends nothing."""
    amount = offer_amount(a)
    if amount is None:
        return None
    return (f"Hi — I'm interested in {a.address}. I'd like to offer {usd(amount)} "
            f"cash, closing in about 21–30 days, subject to an inspection "
            f"contingency and clear title. The contract will include an "
            f"assignment clause (\"and/or assigns\"), so I may assign it to "
            f"another buyer before closing. Happy to talk it through. — Miro")


CHECKLIST = (
    ("Title company or closing attorney chosen (one that handles assignments and "
     "double closes in this state)"),
    "Earnest money: amount, who holds it, and when it becomes non-refundable",
    "Inspection contingency with a clear deadline, so you can walk away",
    "Assignment clause (\"and/or assigns\") and no clause forbidding assignment",
    ("Your state's wholesaling rules checked (licence, disclosure of intent to "
     "assign, marketing the contract vs the property)"),
    "Title search ordered: liens, judgments, probate, HOA",
    "Comps re-checked by hand before signing",
    ("A real estate attorney reviews the contract — ATLAS does not draft or sign "
     "contracts"),
)


def checklist_text() -> str:
    return "\n".join(f"☐ {item}" for item in CHECKLIST)


def full_text(a: Analysis) -> str:
    """The card, then the text-only drafts."""
    parts = [card(a)]
    draft = offer_draft(a)
    if draft is not None:
        parts.append("*Draft offer (text only — ATLAS sends nothing; send it "
                     "yourself if you choose):*\n>" + slack_safe(draft, 1000))
    parts.append("*Contract step checklist:*\n" + checklist_text())
    return "\n\n".join(parts)


def notes_for_prompt(notes: str) -> str:
    """The pasted listing blurb, for any model that ever reads it: quoted
    as outside data (invariant 4), never as Miro's instruction -- it is a
    listing agent's text he copied."""
    return wrap_untrusted(str(notes or "")[:NOTES_MAX], "listing", principal=False)
