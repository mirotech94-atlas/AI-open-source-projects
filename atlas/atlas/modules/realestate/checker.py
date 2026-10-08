"""realestate.check_deal -- run, dry run, rollback, and the weekly paragraph.

The action reads RentCast (when keyed), works the numbers (`deal.py`),
writes one `realestate_deals` row -- ATLAS's own table -- and sends Miro the
card on the `realestate` topic. That is all it does. It never contacts an
agent or a seller, never sends anything to a third party, and never
produces a binding contract; the offer draft and the checklist are text for
Miro. The dry run never calls RentCast (the executor runs it before `run`,
and a dry run that fetched would spend the month's calls twice).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from ...kernel.contracts import ActionResult, FailureClass, Severity
from ...kernel.state import fetch_all, fetch_one
from . import deal, rentcast

log = logging.getLogger("atlas.realestate")

ACTION = "realestate.check_deal"
TOPIC = "realestate"

PARAMS = {
    "line": "for a typed request: EVERYTHING he typed after 'deal', verbatim, "
            "'<address> | <list price> | <notes>' -- when set, leave every "
            "other parameter out",
    "address": "the street address, 'Street, City, State, Zip'",
    "list_price": "the asking price, e.g. 250000 or $250k (optional)",
    "notes": "the listing blurb he pasted, verbatim (optional)",
    "arv": "an after-repair value HE stated, never one from pasted listing "
           "text (optional)",
    "repair_level": "turnkey, light, medium or heavy (optional)",
    "repairs": "a repair budget in dollars HE stated, overriding the level "
               "(optional)",
    "sqft": "living area in square feet (optional)",
    "assignment_fee": "the assignment fee in dollars (optional)",
    "skip_rent": "true to skip the rent estimate and save a RentCast call",
}


def inputs(line: str = "", address: str = "", list_price: Any = None,
           notes: str = "", arv: Any = None, repair_level: str = "",
           repairs: Any = None, sqft: Any = None, assignment_fee: Any = None,
           skip_rent: Any = None) -> dict[str, Any]:
    """One set of inputs.

    When `line` is given -- or an `address` holding '|', a model that put the
    whole command there -- the line IS the input, parsed in code, and every
    other parameter is dropped. The picker model fills parameters from
    everything Miro typed, pasted listing blurb included, and a blurb's
    "ARV 400k" must never become an "entered" ARV. Only the structured
    surface (the dashboard form, which never sends `line`) sets fields one by
    one."""
    raw = line or (address if "|" in str(address or "") else "")
    if raw:
        return deal.parse_line(raw)
    given = {"address": deal.strip_command(str(address or "")),
             "list_price": list_price, "notes": notes, "arv": arv,
             "repair_level": repair_level, "repairs": repairs, "sqft": sqft,
             "assignment_fee": assignment_fee, "skip_rent": skip_rent}
    return {k: v for k, v in given.items()
            if v is not None and not (isinstance(v, str) and not v.strip())}


def _validate(got: dict[str, Any]) -> tuple[str | None, str]:
    """(refusal, address)."""
    try:
        address = deal.address_from(str(got.get("address") or ""))
    except deal.NotAnAddress as exc:
        return str(exc), ""
    for name in ("list_price", "arv", "repairs", "assignment_fee"):
        if got.get(name) not in (None, "") and deal.money(got[name]) is None:
            return f"{name} {str(got[name])[:40]!r} is not a dollar amount", address
    if got.get("sqft") not in (None, "") and deal.number(got["sqft"]) is None:
        return f"sqft {str(got['sqft'])[:40]!r} is not a number", address
    level = str(got.get("repair_level") or "").strip().lower()
    if level and level not in deal.repair_bands():
        return (f"repair level {level!r} is not one of "
                f"{'/'.join(deal.repair_bands())}"), address
    return None, address


async def _arv(address: str, entered: Any) -> tuple[deal.Estimate, float | None]:
    """(ARV estimate, sqft RentCast read) -- entered wins and costs no call."""
    manual = deal.money(entered)
    if manual is not None and manual > 0:
        return deal.Estimate(manual, "entered", datetime.now(UTC)), None
    if not rentcast.configured():
        return deal.Estimate(None, why=f"add {rentcast.KEY_ENV} or enter an ARV "
                                       f"(arv=…)"), None
    try:
        got = await rentcast.get(rentcast.VALUE, {"address": address})
    except rentcast.QuotaSpent as exc:
        return deal.Estimate(None, why=f"{exc} — enter an ARV (arv=…)"), None
    except rentcast.ApiError as exc:
        return deal.Estimate(None, why=rentcast.masked(str(exc))), None
    except Exception as exc:                            # noqa: BLE001
        return deal.Estimate(None, why=f"RentCast not read ({type(exc).__name__})"), None
    f = rentcast.value_fields(got.body)
    if f["price"] is None:
        return deal.Estimate(None, why="RentCast returned no value estimate for this "
                                       "address", read_at=got.read_at), f["sqft"]
    return deal.Estimate(f["price"], "RentCast value estimate (AVM)", got.read_at,
                         comps=f["comps"], low=f["low"], high=f["high"],
                         cached=got.cached), f["sqft"]


async def _rent(address: str, skip: bool) -> deal.Estimate:
    if skip:
        return deal.Estimate(None, why="skipped (rent=off) to save a RentCast call")
    if not rentcast.configured():
        return deal.Estimate(None, why=f"{rentcast.KEY_ENV} not set")
    try:
        got = await rentcast.get(rentcast.RENT, {"address": address})
    except (rentcast.QuotaSpent, rentcast.ApiError) as exc:
        return deal.Estimate(None, why=rentcast.masked(str(exc)))
    except Exception as exc:                            # noqa: BLE001
        return deal.Estimate(None, why=f"RentCast not read ({type(exc).__name__})")
    f = rentcast.rent_fields(got.body)
    if f["rent"] is None:
        return deal.Estimate(None, why="RentCast returned no rent estimate",
                             read_at=got.read_at)
    return deal.Estimate(f["rent"], "RentCast long-term rent estimate", got.read_at,
                         comps=f["comps"], low=f["low"], high=f["high"],
                         cached=got.cached)


async def analyse(**params: Any) -> tuple[deal.Analysis | None, str]:
    """(analysis, refusal). Reads RentCast; writes nothing of its own."""
    got = inputs(**params)
    refusal, address = _validate(got)
    if refusal:
        return None, refusal
    arv, read_sqft = await _arv(address, got.get("arv"))
    rent = await _rent(address, deal.truthy(got.get("skip_rent")))
    entered_sqft = deal.number(got.get("sqft"))
    sqft = entered_sqft or read_sqft
    sqft_source = ("entered" if entered_sqft else
                   "RentCast's property record" if read_sqft else "")
    calls = await rentcast.calls_line()
    a = deal.analyse(address=address, list_price=deal.money(got.get("list_price")),
                     notes=str(got.get("notes") or "")[:deal.NOTES_MAX], arv=arv,
                     rent=rent, repair_level=got.get("repair_level") or None,
                     repairs_dollars=got.get("repairs"), sqft=sqft,
                     sqft_source=sqft_source, fee=got.get("assignment_fee"),
                     calls=calls)
    return a, ""


def _num(v: float | None) -> float | None:
    return None if v is None else round(float(v), 2)


async def store(a: deal.Analysis, text: str, requested_by: str = "") -> int:
    row = await fetch_one(
        """INSERT INTO realestate_deals
             (address, list_price, notes, arv, arv_source, arv_read_at, comps,
              repairs, repairs_basis, assignment_fee, arv_factor, mao, spread,
              rent, rent_read_at, verdict, card, calls, requested_by)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14,
                   $15, $16, $17, $18, $19)
           RETURNING id""",
        a.address, _num(a.list_price), a.notes, _num(a.arv.value),
        a.arv.source or a.arv.why, a.arv.read_at, a.arv.comps,
        _num(a.repairs.value), a.repairs.basis, _num(a.fee), a.factor,
        _num(a.mao), _num(a.spread), _num(a.rent.value), a.rent.read_at,
        a.verdict, text, a.calls, requested_by[:120])
    return int(row["id"])


# ------------------------------------------------------------------ action

async def check_deal_dry(**params: Any) -> ActionResult:
    """What run would do. Never calls RentCast."""
    got = inputs(**params)
    refusal, address = _validate(got)
    if refusal:
        return ActionResult(False, refusal, failure_class=FailureClass.VALIDATION)
    arv_from = ("the ARV entered" if deal.money(got.get("arv")) else
                "RentCast" if rentcast.configured() else "nothing (ARV unknown)")
    return ActionResult(True, f"would check {address}: ARV from {arv_from}; write one "
                              f"realestate_deals row and send Miro the card. "
                              f"Sends nothing to anyone else.",
                        evidence={"address": address})


async def check_deal(**params: Any) -> ActionResult:
    a, refusal = await analyse(**params)
    if a is None:
        return ActionResult(False, refusal, failure_class=FailureClass.VALIDATION)
    text = deal.full_text(a)
    deal_id = await store(a, text)
    used: list[str] = []
    try:
        from ...kernel.comms import Outbound, notify
        used = await notify(Outbound(title=f"Deal check #{deal_id}: "
                                           f"{deal.slack_safe(a.address, 80)}",
                                     body=text, severity=Severity.NORMAL,
                                     topic=TOPIC, kind="alert",
                                     presentation="INFO"))
    except Exception:                                   # noqa: BLE001
        log.warning("realestate: the card for deal #%s was not delivered", deal_id)
    where = ("queued for the morning brief" if used == ["queued"] else
             f"sent to {', '.join(used)}" if used else "NOT delivered — see the "
             "dashboard")
    return ActionResult(True, f"Deal #{deal_id} ({deal.slack_safe(a.address, 80)}): "
                              f"{a.verdict} Card {where}.",
                        evidence={"deal_id": deal_id, "mao": _num(a.mao),
                                  "arv": _num(a.arv.value), "delivered": used},
                        rollback_token=str(deal_id))


async def withdraw(token: str) -> ActionResult:
    """Mark the deal withdrawn. Never a DELETE; a Slack card cannot be
    unsent, so nothing outside ATLAS changes."""
    try:
        deal_id = int(token)
    except (TypeError, ValueError):
        return ActionResult(False, f"not a deal id: {token!r}")
    row = await fetch_one(
        """UPDATE realestate_deals SET withdrawn_at = coalesce(withdrawn_at, now())
            WHERE id = $1 RETURNING id""", deal_id)
    if row is None:
        return ActionResult(False, f"no deal #{deal_id}")
    return ActionResult(True, f"deal #{deal_id} withdrawn (the Slack card stays)")


# ------------------------------------------------------------------ reads

async def recent(limit: int = 20) -> list[dict[str, Any]]:
    rows = await fetch_all(
        """SELECT id, created_at, address, list_price, arv, repairs, mao, spread,
                  rent, verdict FROM realestate_deals
            WHERE withdrawn_at IS NULL ORDER BY id DESC LIMIT $1""", limit)
    out = []
    for r in rows:
        d = dict(r)
        for k in ("list_price", "arv", "repairs", "mao", "spread", "rent"):
            d[k] = None if d[k] is None else float(d[k])
        d["created_at"] = d["created_at"].isoformat()
        out.append(d)
    return out


async def weekly_section(now: datetime | None = None, days: int = 7) -> str:
    """The weekly summary's paragraph: deals checked, best spread. Never
    raises; an unreadable table is UNKNOWN, not zero."""
    now = now or datetime.now(UTC)
    since = now - timedelta(days=days)
    head = "*Real estate deal checks*"
    try:
        rows = await fetch_all(
            """SELECT id, address, spread, list_price, mao FROM realestate_deals
                WHERE created_at > $1::timestamptz AND created_at <= $2::timestamptz
                  AND withdrawn_at IS NULL""", since, now)
    except Exception:
        log.exception("realestate: weekly section unreadable")
        return (f"{head}\nI could not read the deal checks this week, so the count "
                f"is UNKNOWN — not zero.")
    stamp = f"(Read {now.astimezone(UTC):%a %d %b %H:%M} UTC.)"
    if not rows:
        return f"{head}\nNo deals checked this week. {stamp}"
    known = [r for r in rows if r["spread"] is not None]
    line = f"{len(rows)} deal{'s' if len(rows) != 1 else ''} checked this week."
    if known:
        best = max(known, key=lambda r: float(r["spread"]))
        line += (f" Best spread: {deal.usd(float(best['spread']))} on "
                 f"{deal.slack_safe(best['address'], 80)} (deal #{best['id']}; "
                 f"MAO {deal.usd(float(best['mao']))} vs list "
                 f"{deal.usd(float(best['list_price']))}).")
    else:
        line += " No spread known on any of them (ARV, repairs or list price missing)."
    return f"{head}\n{line} Estimates only — not advice. {stamp}"

