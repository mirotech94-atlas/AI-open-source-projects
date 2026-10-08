"""What a leaver still has on the network, a fortnight after they left.

When someone leaves, their devices usually do not. The MSP spec calls this
a small feature with a memorable demo, and it is right: "your former office
manager's laptop has been on this network for six weeks" sells a
subscription in one sentence.

It is also a real control. Unauthorised device detection and access review
both appear on every questionnaire, and a signed-off offboarding checklist
is an evidence artifact rather than just tidiness.

OWNERSHIP IS SET BY A PERSON, NEVER INFERRED. Vault can see a laptop; it
cannot see whose it is. A hostname like `sarah-macbook` is a guess, and a
guess on an offboarding checklist puts the wrong name against a device --
in the one document whose entire value is that someone can sign it. So
`Device.owner` is only ever what a human typed, and a device nobody tagged
is reported as untagged rather than attributed to the nearest plausible
name.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session as DbSession

from . import models as m
from .payloads._util import _now
from .scans import parse_ts

#: A device still appearing this long after a leave date has stopped being
#: an oversight and started being a finding. Two weeks is long enough to
#: cover a notice period, a handover and a holiday, and short enough that
#: the answer still matters.
STILL_HERE_DAYS = 14


def _seen_since(device: m.Device, cutoff: dt.datetime) -> bool:
    when = parse_ts(device.last_seen) if device.last_seen else None
    return when is not None and when >= cutoff


def sweep(db: DbSession, tenant, owner: str, left_on: str | None = None,
          now: dt.datetime | None = None) -> dict:
    """Everything still associated with `owner`, and what is still active.

    `left_on` is the recorded leave date. Without it the sweep still lists
    what they hold -- useful before someone leaves, when the point is to
    know what will need collecting -- but makes no claim about anything
    being overdue, because there is no date to be overdue against.
    """
    now = now or _now()
    devices = (db.query(m.Device)
               .filter(m.Device.tenant_id == tenant.id,
                       m.Device.owner != "",
                       m.Device.owner.ilike(owner))
               .order_by(m.Device.label, m.Device.mac).all())

    left = None
    if left_on:
        try:
            left = dt.datetime.fromisoformat(left_on + "T00:00:00+00:00")
        except ValueError:
            left = None

    rows = []
    for d in devices:
        seen = parse_ts(d.last_seen) if d.last_seen else None
        overdue = bool(left and seen and seen >= left + dt.timedelta(days=STILL_HERE_DAYS))
        rows.append({
            "mac": d.mac, "label": d.label or "", "ip": getattr(d, "ip", "") or "",
            "owner": d.owner, "trusted": bool(d.trusted),
            "last_seen": d.last_seen or None,
            "seen_since_they_left": bool(left and seen and seen >= left),
            "still_here_after_notice": overdue,
        })

    return {
        "owner": owner,
        "left_on": left_on if left else None,
        "as_of": now.isoformat(timespec="seconds"),
        "notice_days": STILL_HERE_DAYS,
        "devices": rows,
        "counts": {
            "tagged": len(rows),
            "seen_since_they_left": sum(1 for r in rows if r["seen_since_they_left"]),
            "still_here_after_notice": sum(1 for r in rows if r["still_here_after_notice"]),
        },
    }


def owners(db: DbSession, tenant) -> list[dict]:
    """Who has devices tagged to them, and how many.

    Also reports how many devices are untagged, because a sweep is only as
    good as the tagging behind it and a page that hides the untagged count
    implies a completeness it has not got.
    """
    rows = db.query(m.Device).filter_by(tenant_id=tenant.id).all()
    by: dict[str, int] = {}
    untagged = 0
    for d in rows:
        if d.owner:
            by[d.owner] = by.get(d.owner, 0) + 1
        else:
            untagged += 1
    return [{"owner": k, "devices": v} for k, v in sorted(by.items())] + \
           ([{"owner": "", "devices": untagged, "untagged": True}] if untagged else [])
