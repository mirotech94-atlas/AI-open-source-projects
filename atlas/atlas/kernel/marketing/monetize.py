"""Ways of making money, composed only from facts something measured.

This is the part of Phase 17 most likely to turn into a spreadsheet of made-up
numbers, so the rules are strict and the tests enforce them:

* **Every idea rests on measured facts.** Each fact carries `observed_at`, the
  time it was read (invariant 6). No fact, no idea.
* **When nothing is measured, the answer is zero ideas and a note saying so.**
  Not one hedged idea, not a plausible-sounding list. The note names each
  source and why it could not be read, which is the thing that tells the owner
  what to fix.
* **No revenue figures.** `est_note` is prose about the mechanism and what
  would have to be true. A number nobody measured would be an estimate
  wearing a measurement's clothes.
* **Nothing here spends.** An idea that needs money to try is listed with its
  mechanism and `requires = "money out — Tier 3, you decide"`. It is never
  emitted as something ATLAS could run, and this file cannot run anything.

What each source actually measures — read from the modules, not assumed:

  youtube   `channel_stats` gives subscribers, views, video count, and
            `partner_eligibility` compares subscribers against YouTube's own
            published 1,000 threshold. The watch-hours and Shorts-views paths
            are measured channel-wide by `academy.ypp.ypp_progress` (Analytics),
            not by `partner_eligibility`, so nothing here claims them; Studio →
            Earn is the authority.
  exampleco  `price_check` returns eBay *active asks*, and its own result text
            says these are not sold prices. Any spread quoted here carries
            that caveat verbatim.
  agency    `agency_leads` gives the pipeline: how many leads are waiting.

Ranking is evidence strength times how little the idea needs from the owner. An
idea resting on two measured facts that he can act on in one click outranks a
single-fact idea that needs him to negotiate a contract.

**Background reading (Task 42).** Once there are ideas to file — never
instead of measured facts, and never when there are none — the weekly pass
asks Perplexity's deep research how businesses like this one make money this
year, and files the citations with each idea: in `sources`, and in
`est_note` as "Sources:" with the time they were read, labelled as reading,
not measurement. They never enter `evidence`, which is measured facts only
and is what the score counts. The question sent is a description of the kind
of business (`DESCRIPTIONS`), never its name — his businesses are the
estate's to answer for, not Perplexity's.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from ..state import fetch_one

log = logging.getLogger("atlas.marketing")

BUSINESSES: tuple[str, ...] = ("exampleco", "agency")

# How much a requirement costs the owner, as a multiplier on evidence strength. A
# toggle in a console he already owns is cheap; a signature is not.
EFFORT: dict[str, float] = {
    "toggle": 1.0,        # flip something in a console he already has
    "signup": 0.7,        # create an account or apply to a programme
    "decide": 0.5,        # price something, choose a direction
    "negotiate": 0.3,     # a contract, a counterparty, a signature
    "spend": 0.15,        # money out: listed, never proposed
}

MONEY_OUT = "money out — Tier 3, you decide"

# What a source reader returns for a business it has nothing to say about —
# eBay listings for an agency question, a channel that does not exist. It is
# not a failure, so it never appears in the "unreadable" note: listing four
# sources that were never relevant buries the one line that matters.
NOT_APPLICABLE = "not applicable to this business"

# What each business is, in words that name no module, business, agent or
# check of ATLAS's own — so the question passes Perplexity's name rule and
# asks about the world, not about him.
DESCRIPTIONS: dict[str, str] = {
    "exampleco": "a used enterprise IT and networking hardware reseller on eBay",
    "agency": "a one-person web design studio selling new websites to local shops",
}


def background_question(business: str) -> str:
    what = DESCRIPTIONS.get(business, "a small online business")
    year = datetime.now(UTC).year
    return (f"How do businesses like {what} make money in {year}? Which "
            "revenue models work now, with real examples?")


# Deep research may run for minutes; the weekly pass gives each business
# this long, all in (handshake, call, fallback), and files the ideas without
# the reading when it runs out.
BACKGROUND_TIMEOUT = 90.0
TIMED_OUT = "research timed out"


async def _background(business: str) -> tuple[dict | None, str | None]:
    """Perplexity's citations on how businesses like this make money, or a
    reason. Reading, not measurement: nothing here is a fact about his
    business. `(None, None)` when Perplexity is not configured at all."""
    try:
        from ...modules import _perplexity
    except Exception as exc:                            # noqa: BLE001
        return None, f"perplexity unavailable: {exc}"
    if not _perplexity.configured():
        return None, None
    found, reason = await _perplexity.call(
        "perplexity_research", background_question(business), caller="marketing",
        deadline=BACKGROUND_TIMEOUT)
    if found is None:
        if reason and "did not answer within" in reason:
            return None, TIMED_OUT
        return None, reason
    return {"sources": list(found.citations), "read_at": found.read_at,
            "line": (f"Background reading, not a measurement: how businesses "
                     f"like this make money, per Perplexity (read "
                     f"{found.read_at:%H:%M} UTC). {found.sources_line()}")}, None


def _fact(value, observed_at: datetime, detail: str) -> dict:
    """One measured fact. `observed_at` is not optional — a fact without the
    time it was read is exactly what invariant 6 forbids."""
    return {"value": value, "observed_at": observed_at.isoformat(),
            "detail": detail}


def _score(evidence: dict, effort: str) -> float:
    strength = min(len(evidence), 3) / 3
    return round(strength * EFFORT.get(effort, 0.5), 3)


# --------------------------------------------------------- measured sources

async def _youtube_facts(business: str) -> tuple[dict | None, str | None]:
    """Partner-programme eligibility, as the youtube module measured it.

    Not recomputed here: `partner_eligibility` refuses to claim the
    watch-hours and Shorts-views paths (those are measured channel-wide by
    `academy.ypp.ypp_progress`, with Studio → Earn as the authority), and duplicating its
    comparison would be a second place for the threshold to drift.
    """
    try:
        from ...modules.youtube import CHANNELS, partner_eligibility
    except Exception as exc:                            # noqa: BLE001
        return None, f"youtube module unavailable: {exc}"
    if business not in CHANNELS:
        return None, NOT_APPLICABLE
    try:
        data = await partner_eligibility(business)
    except Exception as exc:                            # noqa: BLE001
        return None, f"youtube: {exc}"
    if data.get("read_at") is None or data.get("subscribers") is None:
        return None, data.get("detail", "youtube: subscriber count not read")
    return {"channel": business, "subscribers": data["subscribers"],
            "eligible_on_subscribers": data.get("eligible_on_subscribers"),
            "observed_at": data["read_at"], "detail": data["detail"]}, None


async def _ownertech_facts(business: str) -> tuple[dict | None, str | None]:
    """Active listings. eBay *sold* prices are not measured by the module —
    `exampleco.price_check` returns active asks and says so in its own text."""
    if business != "exampleco":
        return None, NOT_APPLICABLE
    try:
        from ...modules.exampleco import check_listings
        obs = await check_listings()
    except Exception as exc:                            # noqa: BLE001
        return None, f"exampleco: {exc}"
    from ..contracts import Health
    if obs.health is Health.UNKNOWN or obs.value is None:
        return None, f"exampleco: {obs.detail or 'listings not read'}"
    at = obs.at or datetime.now(UTC)
    return {"listings": obs.value, "sold_prices_measured": False,
            "observed_at": at, "detail": obs.detail}, None


async def _agency_facts(business: str) -> tuple[dict | None, str | None]:
    """The lead pipeline, straight from `agency_leads`."""
    if business != "agency":
        return None, NOT_APPLICABLE
    try:
        row = await fetch_one(
            "SELECT count(*) AS n FROM agency_leads WHERE status = 'new'")
    except Exception as exc:                            # noqa: BLE001
        return None, f"agency_leads unreadable: {exc}"
    if row is None:
        return None, "agency_leads count query returned nothing"
    now = datetime.now(UTC)
    return {"leads_waiting": int(row["n"]), "observed_at": now,
            "detail": f"{int(row['n'])} lead(s) waiting, read {now:%H:%M} UTC"}, None


# ------------------------------------------------------------ the composer

def _youtube_ideas(facts: dict) -> list[dict]:
    at = facts["observed_at"]
    evidence = {"subscribers": _fact(facts["subscribers"], at, facts["detail"])}
    eligible = facts.get("eligible_on_subscribers")
    if eligible:
        est = ("The subscriber threshold is met as measured. The watch-hours "
               "and Shorts-views paths are not measured by the youtube "
               "module, so eligibility is not confirmed — YouTube Studio is "
               "the only place that can confirm it.")
    else:
        est = ("The subscriber threshold is not met as measured. Nothing here "
               "estimates when it would be; that depends on growth this "
               "module has not projected and will not.")
    return [{
        "business": facts["channel"],
        "idea": "YouTube Partner Program",
        "mechanism": "partner_program",
        "evidence": evidence,
        "requires": "enable monetisation in YouTube Studio — only you can",
        "est_note": est,
        "effort": "toggle",
        "costs_money": False,
        "score": _score(evidence, "toggle"),
    }]


def _ownertech_ideas(facts: dict) -> list[dict]:
    at = facts["observed_at"]
    evidence = {"listings": _fact(facts["listings"], at, facts["detail"])}
    return [{
        "business": "exampleco",
        "idea": "Reprice the slowest listings against completed sales",
        "mechanism": "product",
        "evidence": evidence,
        "requires": "set the prices — changing what buyers pay is yours",
        "est_note": ("Rests on the measured listing count above. Sold prices "
                     "are not measured: `exampleco.price_check` returns eBay "
                     "active asks and says so in its own result, so no spread "
                     "is quoted here."),
        "effort": "decide",
        "costs_money": False,
        "score": _score(evidence, "decide"),
    }]


def _agency_ideas(facts: dict) -> list[dict]:
    at = facts["observed_at"]
    evidence = {"leads_waiting": _fact(facts["leads_waiting"], at,
                                       facts["detail"])}
    return [{
        "business": "agency",
        "idea": "A fixed-price package for the lead type already arriving",
        "mechanism": "service",
        "evidence": evidence,
        "requires": "price the package — only you can",
        "est_note": ("Rests on the measured pipeline count above. No revenue "
                     "figure is offered: nothing measures close rate."),
        "effort": "decide",
        "costs_money": False,
        "score": _score(evidence, "decide"),
    }]


def _sources():
    """The three sources, resolved at call time.

    Built here rather than as a module constant so each reader is looked up
    when `ideas()` runs. A tuple captured at import time would freeze the
    function objects, and a test that replaces one would silently be testing
    the original — the kind of green that means nothing.
    """
    return (
        ("youtube", _youtube_facts, _youtube_ideas),
        ("exampleco", _ownertech_facts, _ownertech_ideas),
        ("agency", _agency_facts, _agency_ideas),
    )


async def ideas(business: str, *, limit: int = 3) -> dict:
    """Ranked monetization ideas for one business, or none and a reason.

    Nothing here writes a row or runs anything. `cycle.weekly()` hands what
    comes back to `marketing.propose_idea`, the Tier AUTO action that records
    it, and the owner reads it in the brief.
    """
    found: list[dict] = []
    unmeasured: list[str] = []

    for name, read_facts, compose in _sources():
        try:
            facts, reason = await read_facts(business)
        except Exception as exc:                        # noqa: BLE001
            facts, reason = None, f"{name}: {type(exc).__name__}: {exc}"
        if facts is None:
            if reason != NOT_APPLICABLE:
                unmeasured.append(f"{name}: {reason}")
            continue
        found.extend(compose(facts))

    if not found:
        return {
            "business": business,
            "ideas": [],
            "note": ("no measured facts: every source this would rest on is "
                     "unreadable right now — " + "; ".join(unmeasured)),
            "unmeasured": unmeasured,
        }

    found.sort(key=lambda idea: -idea["score"])
    chosen = found[:limit]
    note = ""
    if unmeasured:
        note = "not measured, so not used: " + "; ".join(unmeasured)

    background, why = await _background(business)
    if background is not None:
        for idea in chosen:
            idea["sources"] = list(background["sources"])
            idea["est_note"] = f"{idea['est_note']} {background['line']}"
            idea["background"] = {
                "status": "read", "sources": list(background["sources"]),
                "read_at": background["read_at"].isoformat(),
                "detail": "Perplexity background reading — not a measurement"}
    elif why:
        note = "; ".join(filter(None, [note, f"no background reading: {why}"]))
        for idea in chosen:
            # Filed with the idea, so "why are there no sources" has its
            # answer on the row itself — never an empty list read as none.
            idea["background"] = {
                "status": why, "sources": [], "read_at": None,
                "detail": "Perplexity background reading — not a measurement"}
    return {"business": business, "ideas": chosen, "note": note,
            "unmeasured": unmeasured}
