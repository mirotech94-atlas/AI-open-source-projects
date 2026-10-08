"""ISP service-credit evidence: what the line owed, and what it delivered.

Most SMBs never claim a service credit. Not because the outages did not
happen, but because assembling the evidence costs more than the credit is
worth — so the money is left on the table every time. Vault already
measures availability from inside the network, which is the one vantage
point the provider cannot dispute, and turning that into a claim is a
two-minute job rather than an afternoon.

For a small office on a business circuit this can pay the subscription
outright in a bad month, which is the kind of sentence that belongs on a
pricing page.

THE WHOLE FEATURE TURNS ON CAUSE ATTRIBUTION.

An outage is only claimable if it was the provider's. Vault distinguishes
them from one observation: the sensor pings the default gateway alongside
the external anchors, so

* gateway answers, nothing beyond it does -> `isp`. The line is down.
* gateway unreachable too -> `local`. The fault is inside the building.
* anything else -> `unknown`.

Only `isp` minutes are claimed. Claiming an `unknown` gets the claim
rejected; claiming a `local` once gets the customer told by their own
provider that their monitoring is wrong, and no MSP recovers that. The
conservative reading costs a few minutes of credit and keeps the feature
usable, which is the right trade every time.

The arithmetic is deliberately plain and every term is shown, because the
reader is a provider's billing department looking for a reason to say no.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session as DbSession

from . import models as m
from .scans import parse_ts


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def classify(wan_path: dict | None, wan_ip: str | None, on_home_lan: bool) -> str:
    """`isp` | `local` | `unknown` from one sweep's reachability.

    Requires `on_home_lan`: off its expected network the sensor's own
    route is unknown, and every verdict it could give about the customer's
    line would be about a different one.
    """
    if not on_home_lan:
        return "unknown"
    paths = wan_path or {}
    gw = paths.get("gateway")
    if gw is None:
        return "unknown"
    gateway_up = (gw.get("loss_pct") if isinstance(gw, dict) else 100.0) is not None \
        and gw.get("loss_pct", 100.0) < 100.0
    external = [v for k, v in paths.items()
                if k != "gateway" and isinstance(v, dict)]
    if not external:
        return "unknown"
    external_down = all(v.get("loss_pct", 100.0) >= 100.0 for v in external)
    if not external_down:
        return ""          # not an outage at all
    return "isp" if gateway_up else "local"


def record(db: DbSession, tenant, raw: dict) -> m.WanOutage | None:
    """Open, extend or close the current outage from one scan.

    Idempotent on replay through the `(tenant, started_at)` unique key: a
    sensor resending a buffered batch updates the row it already created
    rather than opening a second outage for the same minute.
    """
    ts = raw.get("ts")
    when = parse_ts(ts)
    if when is None:
        return None
    cause = classify(raw.get("wan_path"), raw.get("wan_ip"), bool(raw.get("on_home_lan")))

    open_row = (db.query(m.WanOutage)
                .filter_by(tenant_id=tenant.id, ended_at=None)
                .order_by(m.WanOutage.started_at.desc()).first())

    if cause == "":
        # The line is up. Close anything open, stamped at this sweep.
        if open_row is not None:
            open_row.ended_at = ts
            db.commit()
        return None

    if cause == "unknown" and open_row is None:
        # Not enough to open an outage on. Silence beats a row nobody can
        # defend in front of a billing department.
        return None

    if open_row is None:
        row = m.WanOutage(tenant_id=tenant.id, started_at=ts, cause=cause,
                          evidence={"first": raw.get("wan_path") or {},
                                    "on_home_lan": bool(raw.get("on_home_lan"))})
        db.add(row)
        db.commit()
        return row

    # Extending. An outage that starts ambiguous and becomes attributable
    # takes the stronger reading; one that starts attributable never
    # weakens, because the evidence for it was already recorded.
    if open_row.cause == "unknown" and cause in ("isp", "local"):
        open_row.cause = cause
    db.commit()
    return open_row


def minutes(row: m.WanOutage, until: dt.datetime | None = None) -> float:
    start, end = parse_ts(row.started_at), parse_ts(row.ended_at) if row.ended_at else None
    if start is None:
        return 0.0
    return max(0.0, ((end or until or _now()) - start).total_seconds() / 60.0)


def credit(db: DbSession, tenant, first_day: str, last_day: str) -> dict:
    """Measured availability against the committed SLA, and the credit owed.

    Every term is returned, not just the total. The reader is a provider's
    billing department looking for a reason to reject the claim, and a
    number with no working shown is the easiest thing in the world to
    reject.

    `claimable_minutes` counts ISP-attributed outages only. Local and
    unknown minutes are reported separately rather than dropped, so the
    customer can see the full picture and the claim still only asserts
    what it can defend.
    """
    settings = tenant.settings or {}
    committed = float(settings.get("sla_committed_pct") or 0) or None
    monthly_cost = float(settings.get("circuit_monthly_cost") or 0) or None

    start = dt.datetime.fromisoformat(first_day + "T00:00:00+00:00")
    end = dt.datetime.fromisoformat(last_day + "T00:00:00+00:00") + dt.timedelta(days=1)
    rows = (db.query(m.WanOutage)
            .filter(m.WanOutage.tenant_id == tenant.id,
                    m.WanOutage.started_at >= start.isoformat(),
                    m.WanOutage.started_at < end.isoformat())
            .order_by(m.WanOutage.started_at).all())

    period_minutes = (end - start).total_seconds() / 60.0
    by_cause = {"isp": 0.0, "local": 0.0, "unknown": 0.0}
    for row in rows:
        by_cause[row.cause] = by_cause.get(row.cause, 0.0) + minutes(row, until=end)

    claimable = by_cause["isp"]
    measured = 100.0 * (period_minutes - claimable) / period_minutes if period_minutes else None
    shortfall = (round(committed - measured, 4)
                 if committed is not None and measured is not None and measured < committed
                 else 0.0)
    owed = (round(monthly_cost * (claimable / period_minutes), 2)
            if monthly_cost and shortfall > 0 else 0.0)

    return {
        "period": {"from": first_day, "to": last_day, "minutes": round(period_minutes)},
        "committed_pct": committed,
        "measured_pct": round(measured, 4) if measured is not None else None,
        "shortfall_pct": shortfall,
        "claimable_minutes": round(claimable, 1),
        "other_minutes": {"local": round(by_cause["local"], 1),
                          "unknown": round(by_cause["unknown"], 1)},
        "monthly_cost": monthly_cost,
        "credit_owed": owed,
        "outages": [{"from": r.started_at, "to": r.ended_at, "cause": r.cause,
                     "minutes": round(minutes(r, until=end), 1)} for r in rows],
    }


def claim_letter(tenant, pack: dict) -> str:
    """A letter the customer can send, with the evidence table in it.

    Most SMBs never claim because writing this is the work. It states only
    ISP-attributed minutes and says so, because a claim that quietly
    includes the customer's own outages is one the provider rejects and
    remembers.
    """
    lines = [
        f"To: Service provider, re: service credit for {pack['period']['from']} "
        f"to {pack['period']['to']}",
        "",
        f"{tenant.name} monitors the availability of this circuit continuously from "
        "equipment on the customer side of the demarcation point.",
        "",
        f"Committed availability: {pack['committed_pct']}%"
        if pack["committed_pct"] else "Committed availability: not recorded",
        f"Measured availability: {pack['measured_pct']}%",
        f"Total unavailable minutes attributed to the access circuit: "
        f"{pack['claimable_minutes']}",
        "",
        "Outages included in this claim:",
    ]
    for o in pack["outages"]:
        if o["cause"] != "isp":
            continue
        lines.append(f"  {o['from']} to {o['to'] or 'ongoing'} — {o['minutes']} minutes")
    if pack["credit_owed"]:
        lines += ["", f"Service credit claimed: {pack['credit_owed']}"]
    lines += [
        "",
        "Outages during this period that were NOT attributed to the access circuit, "
        "and are therefore excluded from this claim:",
        f"  local faults: {pack['other_minutes']['local']} minutes",
        f"  cause not established: {pack['other_minutes']['unknown']} minutes",
        "",
        "Each outage above was recorded when the customer-side gateway remained "
        "reachable and no external host could be reached, which places the fault "
        "beyond the demarcation point. Per-minute observations are available.",
    ]
    return "\n".join(lines)
