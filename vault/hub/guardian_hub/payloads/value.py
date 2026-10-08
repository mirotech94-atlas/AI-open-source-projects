"""The Reports value strip: what Vault did for you, over a window.

This is the most tempting surface in the product to overstate, so two of the
four numbers the v2 design asks for are deliberately not the numbers it
names:

- "Median time to fix" would read as "how long a problem lasted". Nothing
  here measures that: an `Alert` carries no resolved-at timestamp, so the
  span from a problem appearing to it being over cannot be computed. What
  CAN be measured is how long a repair took to run, once started, and that
  is what this reports -- under that name.
- "Your time spent" cannot be measured at all. Vault does not know how
  long an owner looked at anything. Inventing minutes-saved from a count of
  approvals would be a sales figure wearing a metric's clothes. What is
  measurable is how many decisions the owner was actually asked for, so
  that is what the fourth stat reports.

Everything here counts rows, and every row can be found in `fix` or `queue`.
"""

import datetime as dt

from sqlalchemy.orm import Session as DbSession

from .. import models as m
from . import account as account_payload
from ..schemas_dash import ValueStat, ValueStrip, WorkRow

#: Repair actions grouped into something an owner recognises. A bucket is
#: named after the OUTCOME, not the command -- "free disk space" means
#: nothing to somebody who has never run `journalctl --vacuum-size`.
WORK_BUCKETS: list[tuple[str, tuple[str, ...]]] = [
    ("Service restarts", ("restart_service", "restart_container", "reboot")),
    ("Disk and log cleanup", ("cleanup_disk",)),
    ("Certificate renewals", ("renew_cert", "certbot_renew")),
    ("DNS corrections", ("fix_dns", "dns_repair")),
    ("Security updates", ("security_updates", "full_upgrade", "enable_unattended",
                          "install_debsecan")),
    ("Firewall and access", ("enable_ufw", "block_ip", "block_subnet", "ban_long",
                             "limit_ssh", "enable_fail2ban", "harden_fail2ban",
                             "disable_ssh_password")),
    ("Malware removal", ("kill_miner",)),
    ("Website hardening", ("security_headers",)),
]

_BUCKET_OF = {action: label for label, actions in WORK_BUCKETS for action in actions}


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    v = sorted(values)
    mid = len(v) // 2
    return v[mid] if len(v) % 2 else (v[mid - 1] + v[mid]) / 2


def _human_secs(s: float) -> str:
    if s < 120:
        return f"{round(s)}s"
    if s < 5400:
        return f"{round(s / 60)} min"
    return f"{s / 3600:.1f} h"


def build(db: DbSession, tenant_id: str, days: int = 30) -> ValueStrip:
    since_dt = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    since = since_dt.isoformat(timespec="seconds")

    fixes = (db.query(m.Fix)
               .filter(m.Fix.tenant_id == tenant_id, m.Fix.ts >= since, m.Fix.ok.is_(True))
               .all())
    # Same split as `overview.fixed_without_you` and the console's chart:
    # `account.is_person` -- a person's yes is stamped `hub:`/`chat:`/
    # `dashboard`, and any other stamp is Vault alone. `by != "auto"`
    # counted the sensor's own stamps as approvals (61 "test" rows on the
    # live hub), which is how the Reports strip and the Overview could
    # disagree about the same fortnight.
    automatic = [f for f in fixes if not account_payload.is_person(f.by)]
    approved = [f for f in fixes if account_payload.is_person(f.by)]

    runs = (db.query(m.Queue)
              .filter(m.Queue.tenant_id == tenant_id, m.Queue.status == "done",
                      m.Queue.started_at.isnot(None), m.Queue.finished_at.isnot(None),
                      m.Queue.ts >= since_dt)
              .all())
    spans = [(q.finished_at - q.started_at).total_seconds() for q in runs]
    med = _median([s for s in spans if s >= 0])

    share = f"{round(100 * len(automatic) / len(fixes))}%" if fixes else "—"
    stats = [
        ValueStat(key="actions", label="Actions taken", value=str(len(fixes)),
                  note=f"repairs that succeeded in {days} days"),
        ValueStat(key="alone", label="Handled alone", value=share,
                  note=f"{len(automatic)} of {len(fixes)} needed no decision from you"
                       if fixes else "nothing needed fixing"),
        ValueStat(key="runtime", label="Median repair time",
                  value=_human_secs(med) if med is not None else "—",
                  # Named for what it measures. It is NOT how long a problem
                  # lasted -- Vault does not record when a problem began
                  # being a problem.
                  note="how long a repair took once it started"
                       if med is not None else "no repair has run in this window"),
        ValueStat(key="decisions", label="Decisions you made", value=str(len(approved)),
                  # The design asked for "your time spent". Vault cannot
                  # see a clock on the owner's side, and minutes-saved from an
                  # approval count would be a sales figure, not a measurement.
                  note=f"times Vault asked before acting, in {days} days"),
    ]

    tally: dict[str, int] = {}
    for f in fixes:
        label = _BUCKET_OF.get(f.action)
        if label is None:
            # Never silently dropped: an unbucketed action still happened, and
            # a total that does not match the rows above it is a bug report
            # waiting to happen.
            label = "Other repairs"
        tally[label] = tally.get(label, 0) + 1

    work = [WorkRow(label=k, count=v) for k, v in sorted(tally.items(), key=lambda kv: -kv[1])]
    return ValueStrip(days=days, stats=stats, work=work)
