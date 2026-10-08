"""Retention job: keeps `metric`, `event`, `presence_sample`, `dns_query`,
`dns_rollup`, `flow`, `flow_rollup`, `audit` and `report` rows/files from
growing without bound.

Policy is read per tenant from `Tenant.settings` (flat keys, same convention
as `report_schedule`/`retention_days` in `seed.py`), with these defaults --
the only knobs a tenant can set:

- `retention_days` (90): applies to `metric`, `event`, `presence_sample`
  and `report` rows/files.
- `dns_raw_days` (7): raw `dns_query` rows older than this are rolled up
  into `DnsRollup` first, then deleted.
- `dns_rollup_days` (365): `dns_rollup` rows older than this are deleted.
- `flow_raw_days` (7): raw `flow` rows older than this are rolled up into
  `FlowRollup` first, then deleted -- the same `dns_raw_days` treatment
  applied to traffic.
- `flow_rollup_days` (365): `flow_rollup` rows older than this are deleted.
- `audit_days` (365): `audit` rows older than this are deleted.

`prune_tenant`/`prune_all` never touch `Scan`, `Device`, `Alert`,
`Incident`, `Recommendation`, `Fix`, `Network` or anything a human curated
-- only the append-only history tables above. Deletes run in batches of
`BATCH_SIZE` with a commit per batch, so a large first run on a long-lived
tenant never holds one long SQLite write lock.

`_now()` is a seam for tests: `monkeypatch.setattr(retention, "_now", ...)`
pins "the current instant" without touching the wall clock -- same pattern
as `scheduler._now`.
"""

import datetime as dt
import pathlib

from sqlalchemy.orm import Session as DbSession

from . import models as m

BATCH_SIZE = 5000
DNS_ROLLUP_TOP_N = 200
DNS_ROLLUP_OTHER = "(other)"
DNS_ROLLUP_MIN_EXTRA_DAYS = 30

_POLICY_DEFAULTS = {
    "retention_days": 90,
    "dns_raw_days": 7,
    "dns_rollup_days": 365,
    "flow_raw_days": 7,
    "flow_rollup_days": 365,
    "audit_days": 365,
}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def policy(tenant: "m.Tenant") -> dict:
    """The effective retention policy for `tenant`: `Tenant.settings`
    values where present, `_POLICY_DEFAULTS` otherwise. Shared with
    `payloads/dnsview.py` (it needs `dns_raw_days` to decide when a DNS
    view must read `DnsRollup` instead of/along with raw rows) and
    `routers/dns.py` (the `hours` clamp).

    Every value is coerced to a positive int -- garbage (non-numeric,
    missing, or <= 0) falls back to its default. `dns_rollup_days` is
    additionally floored at `dns_raw_days + DNS_ROLLUP_MIN_EXTRA_DAYS`:
    `prune_tenant` rolls up raw `dns_query` rows older than `dns_raw_days`
    (step 1) and then deletes `DnsRollup` rows older than
    `dns_rollup_days` (step 3). Flooring at *equality* (`dns_rollup_days ==
    dns_raw_days`) is not enough -- the day that step 1 just rolled up
    sits exactly at the raw cutoff, which is also exactly the rollup
    cutoff, so step 3 deletes it in the same run and rollups never outlive
    the raw data they were derived from. Floor it `DNS_ROLLUP_MIN_EXTRA_DAYS`
    days past `dns_raw_days` instead, so every freshly-rolled-up day has a
    real margin before it is eligible for deletion. `flow_rollup_days`
    gets the identical floor against `flow_raw_days`, for the identical
    reason -- `rollup_flows`/raw-delete/rollup-delete is the same three-step
    shape as the DNS steps, just over `flow`/`flow_rollup`. `retention_days`
    and `audit_days` are clamped to >= 1 so a garbage-but-still-truthy value
    (e.g. 0) can't disable pruning of everything at once.
    """
    s = tenant.settings or {}
    out = {}
    for key, default in _POLICY_DEFAULTS.items():
        try:
            value = int(s.get(key) or default)
        except (TypeError, ValueError):
            value = default
        if value <= 0:
            value = default
        out[key] = value
    dns_floor = out["dns_raw_days"] + DNS_ROLLUP_MIN_EXTRA_DAYS
    if out["dns_rollup_days"] < dns_floor:
        out["dns_rollup_days"] = dns_floor
    flow_floor = out["flow_raw_days"] + DNS_ROLLUP_MIN_EXTRA_DAYS
    if out["flow_rollup_days"] < flow_floor:
        out["flow_rollup_days"] = flow_floor
    return out


def _iso(when: dt.datetime) -> str:
    return when.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def _build_rollup_rows(db: DbSession, tenant_id: str, before_day: str) -> list["m.DnsRollup"]:
    """Pure computation (no writes): the `DnsRollup` rows that rolling up
    every raw `dns_query` row for `tenant_id` with `ts < before_day` would
    produce, grouped by (day, network_id, mac, name) and capped at the top
    `DNS_ROLLUP_TOP_N` domains per (day, network_id) by query count, with
    the remainder collapsed into one `name="(other)"` row per (day,
    network_id) (mac dropped -- the tail is a single bucket, not a
    per-client breakdown).

    `network_id` is the raw row's OWN attribution (`dns_query.network_id`,
    added by `0007_networks`), so a rolled-up day stays per-network and the
    DNS tab's `?network=` filter keeps working past `dns_raw_days`. It is
    None only for rows the sensor could not attribute. Task 55 wrote this
    grouping with the key pinned to `None` because the networks work was on
    another branch at the time; the phase-5 closeout, where both landed,
    reads the real column.
    """
    rows = (
        db.query(m.DnsQuery.ts, m.DnsQuery.mac, m.DnsQuery.name, m.DnsQuery.blocked,
                 m.DnsQuery.network_id)
        .filter(m.DnsQuery.tenant_id == tenant_id, m.DnsQuery.ts < before_day)
        .all()
    )

    # day -> (network_id, mac, name) -> {"queries": n, "blocked": n}
    by_day: dict[str, dict[tuple, dict]] = {}
    for ts, mac, name, blocked, network_id in rows:
        day = (ts or "")[:10]
        if not day:
            continue
        bucket = by_day.setdefault(day, {})
        key = (network_id, mac, name)
        entry = bucket.setdefault(key, {"queries": 0, "blocked": 0})
        entry["queries"] += 1
        if blocked:
            entry["blocked"] += 1

    out: list[m.DnsRollup] = []
    for day, groups in by_day.items():
        # Rank domains by total queries within each (day, network_id),
        # summed across mac -- the cap is "per day per network", not per
        # (day, network, mac).
        totals: dict[tuple, int] = {}
        for (network_id, _mac, name), agg in groups.items():
            totals[(network_id, name)] = totals.get((network_id, name), 0) + agg["queries"]

        by_network: dict[object, list[tuple[str, int]]] = {}
        for (network_id, name), total in totals.items():
            by_network.setdefault(network_id, []).append((name, total))

        top_names: set[tuple] = set()
        for network_id, lst in by_network.items():
            lst.sort(key=lambda x: -x[1])
            for name, _total in lst[:DNS_ROLLUP_TOP_N]:
                top_names.add((network_id, name))

        other: dict[object, dict] = {}   # network_id -> {"queries": n, "blocked": n}
        for (network_id, mac, name), agg in groups.items():
            if (network_id, name) in top_names:
                out.append(m.DnsRollup(
                    tenant_id=tenant_id, day=day, network_id=network_id, mac=mac,
                    name=name, queries=agg["queries"], blocked=agg["blocked"],
                ))
            else:
                o = other.setdefault(network_id, {"queries": 0, "blocked": 0})
                o["queries"] += agg["queries"]
                o["blocked"] += agg["blocked"]

        for network_id, o in other.items():
            if o["queries"] <= 0:
                continue
            out.append(m.DnsRollup(
                tenant_id=tenant_id, day=day, network_id=network_id, mac=None,
                name=DNS_ROLLUP_OTHER, queries=o["queries"], blocked=o["blocked"],
            ))

    return out


def rollup_dns(db: DbSession, tenant: "m.Tenant", before_day: str) -> int:
    """Roll up every raw `dns_query` row for `tenant` older than
    `before_day` (a "YYYY-MM-DD" string) into `DnsRollup`. Returns the
    number of rollup rows written.

    Idempotent: for every day touched, any `DnsRollup` rows already on
    record for that (tenant, day) are deleted and replaced -- re-running
    for the same `before_day` against unchanged raw data produces the same
    rows (this does not itself delete the raw `dns_query` rows; that is
    `prune_tenant`'s job, run *after* this).
    """
    rows = _build_rollup_rows(db, tenant.id, before_day)
    days = {r.day for r in rows}
    if days:
        db.query(m.DnsRollup).filter(
            m.DnsRollup.tenant_id == tenant.id, m.DnsRollup.day.in_(days),
        ).delete(synchronize_session=False)
    db.add_all(rows)
    db.commit()
    return len(rows)


def _build_flow_rollup_rows(db: DbSession, tenant_id: str, before_day: str) -> list["m.FlowRollup"]:
    """Group raw flows older than `before_day` by (day, network, mac, peer,
    port, proto). `peer` is the DNS name when the sensor saw one, else the
    IP -- a month later "142.250.1.1" means nothing and "googleapis.com"
    means everything. Idempotent per day like `_build_rollup_rows`."""
    from sqlalchemy import func
    q = (db.query(func.substr(m.Flow.ts, 1, 10).label("day"), m.Flow.network_id, m.Flow.mac,
                  func.coalesce(func.nullif(m.Flow.peer_name, ""), m.Flow.peer_ip).label("peer"),
                  m.Flow.peer_port, m.Flow.proto,
                  func.sum(m.Flow.bytes_in), func.sum(m.Flow.bytes_out), func.sum(m.Flow.pkts_in + m.Flow.pkts_out))
           .filter(m.Flow.tenant_id == tenant_id, m.Flow.ts < before_day)
           .group_by("day", m.Flow.network_id, m.Flow.mac, "peer", m.Flow.peer_port, m.Flow.proto))
    return [m.FlowRollup(tenant_id=tenant_id, day=d, network_id=n, mac=mac, peer=p, peer_port=pp, proto=pr,
                         bytes_in=int(bi or 0), bytes_out=int(bo or 0), pkts=int(pk or 0))
            for d, n, mac, p, pp, pr, bi, bo, pk in q.all()]


def rollup_flows(db: DbSession, tenant: "m.Tenant", before_day: str) -> int:
    """Roll up every raw `flow` row for `tenant` older than `before_day` (a
    "YYYY-MM-DD" string) into `FlowRollup`. Returns the number of rollup
    rows written.

    Idempotent: for every day touched, any `FlowRollup` rows already on
    record for that (tenant, day) are deleted and replaced -- same contract
    as `rollup_dns`. This does not itself delete the raw `flow` rows; that
    is `prune_tenant`'s job, run *after* this.
    """
    rows = _build_flow_rollup_rows(db, tenant.id, before_day)
    days = {r.day for r in rows}
    if days:
        db.query(m.FlowRollup).filter(m.FlowRollup.tenant_id == tenant.id, m.FlowRollup.day.in_(days)).delete(synchronize_session=False)
    db.add_all(rows); db.flush()
    return len(rows)


def _batched_delete_by_id(db: DbSession, model, filter_expr, batch_size: int = BATCH_SIZE) -> int:
    """Delete every row of `model` matching `filter_expr` in batches of
    `batch_size`, committing after each batch so no single delete holds a
    long-running SQLite write lock."""
    total = 0
    while True:
        ids = [row[0] for row in db.query(model.id).filter(filter_expr).limit(batch_size).all()]
        if not ids:
            break
        db.query(model).filter(model.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
        total += len(ids)
        if len(ids) < batch_size:
            break
    return total


def _prune_reports(db: DbSession, tenant_id: str, cutoff: dt.datetime, dry_run: bool) -> int:
    """Delete `Report` rows (and their HTML file on disk, if present)
    created before `cutoff`. A missing file is not an error -- the row is
    still deleted."""
    q = db.query(m.Report).filter(m.Report.tenant_id == tenant_id, m.Report.created < cutoff)
    if dry_run:
        return q.count()
    total = 0
    while True:
        batch = q.limit(BATCH_SIZE).all()
        if not batch:
            break
        for r in batch:
            try:
                p = pathlib.Path(r.path)
                if p.exists():
                    p.unlink()
            except Exception:
                pass
            db.delete(r)
        db.commit()
        total += len(batch)
        if len(batch) < BATCH_SIZE:
            break
    return total


def prune_tenant(db: DbSession, tenant: "m.Tenant", now: dt.datetime | None = None,
                  dry_run: bool = False) -> dict:
    """Apply the retention policy for one tenant. Returns
    `{"metric": n, "event": n, "dns_query": n, "dns_rollup": n, "flow": n,
    "flow_rollup": n, "presence_sample": n, "audit": n, "report": n,
    "rolled_up": n, "flows_rolled_up": n}` -- the number of rows deleted
    (or, when `dry_run`, the number that *would* be) for each, plus
    `rolled_up` (the number of `DnsRollup` rows the DNS rollup step wrote,
    or would write) and `flows_rolled_up` (the same, for `FlowRollup`).

    Order matters: DNS rows are rolled up *before* the matching raw rows
    are deleted, so a crash between the two steps never loses DNS history
    that hadn't been rolled up yet. Flow rows get the identical treatment,
    for the identical reason.

    Never touches `Scan`, `Device`, `Alert`, `Incident`, `Recommendation`,
    `Fix`, `Network` or any other human-curated table -- only the
    append-only history rows the policy above names.
    """
    now = now or _now()
    pol = policy(tenant)

    retention_cutoff_ts = _iso(now - dt.timedelta(days=pol["retention_days"]))
    dns_raw_cutoff_day = (now - dt.timedelta(days=pol["dns_raw_days"])).date().isoformat()
    dns_rollup_cutoff_day = (now - dt.timedelta(days=pol["dns_rollup_days"])).date().isoformat()
    flow_raw_cutoff_day = (now - dt.timedelta(days=pol["flow_raw_days"])).date().isoformat()
    flow_rollup_cutoff_day = (now - dt.timedelta(days=pol["flow_rollup_days"])).date().isoformat()
    audit_cutoff = now - dt.timedelta(days=pol["audit_days"])
    report_cutoff = now - dt.timedelta(days=pol["retention_days"])

    counts = {
        "metric": 0, "event": 0, "dns_query": 0, "dns_rollup": 0,
        "flow": 0, "flow_rollup": 0,
        "presence_sample": 0, "audit": 0, "report": 0,
        "rolled_up": 0, "flows_rolled_up": 0,
    }

    # 1. Roll up DNS *before* deleting the raw rows it is derived from.
    if dry_run:
        counts["rolled_up"] = len(_build_rollup_rows(db, tenant.id, dns_raw_cutoff_day))
    else:
        counts["rolled_up"] = rollup_dns(db, tenant, dns_raw_cutoff_day)

    # 2. Delete the raw dns_query rows that were just (or previously) rolled up.
    dns_query_filter = (m.DnsQuery.tenant_id == tenant.id) & (m.DnsQuery.ts < dns_raw_cutoff_day)
    if dry_run:
        counts["dns_query"] = db.query(m.DnsQuery).filter(dns_query_filter).count()
    else:
        counts["dns_query"] = _batched_delete_by_id(db, m.DnsQuery, dns_query_filter)

    # 3. Delete rollup rows themselves once they age past dns_rollup_days.
    dns_rollup_filter = (m.DnsRollup.tenant_id == tenant.id) & (m.DnsRollup.day < dns_rollup_cutoff_day)
    if dry_run:
        counts["dns_rollup"] = db.query(m.DnsRollup).filter(dns_rollup_filter).count()
    else:
        counts["dns_rollup"] = _batched_delete_by_id(db, m.DnsRollup, dns_rollup_filter)

    # 3b. Flows: same rollup-then-delete-raw-then-delete-rollup shape as DNS above.
    if dry_run:
        counts["flows_rolled_up"] = len(_build_flow_rollup_rows(db, tenant.id, flow_raw_cutoff_day))
    else:
        counts["flows_rolled_up"] = rollup_flows(db, tenant, flow_raw_cutoff_day)

    flow_filter = (m.Flow.tenant_id == tenant.id) & (m.Flow.ts < flow_raw_cutoff_day)
    if dry_run:
        counts["flow"] = db.query(m.Flow).filter(flow_filter).count()
    else:
        counts["flow"] = _batched_delete_by_id(db, m.Flow, flow_filter)

    # 3c. Known peers silent for longer than raw retention are forgotten, so
    # one that returns is "new" again -- the lower bound the old raw-table
    # "seen before" scan applied (`flowpeers.seen_before`).
    if not dry_run:
        from . import flowpeers
        counts["flow_peer"] = flowpeers.prune(db, tenant.id, now - dt.timedelta(days=pol["flow_raw_days"]))
        db.commit()   # like every step here: no delete holds the write lock past its own step

    flow_rollup_filter = (m.FlowRollup.tenant_id == tenant.id) & (m.FlowRollup.day < flow_rollup_cutoff_day)
    if dry_run:
        counts["flow_rollup"] = db.query(m.FlowRollup).filter(flow_rollup_filter).count()
    else:
        counts["flow_rollup"] = _batched_delete_by_id(db, m.FlowRollup, flow_rollup_filter)

    # 4. metric / event / presence_sample: retention_days, ts is a string column.
    for model, key in ((m.Metric, "metric"), (m.Event, "event"), (m.PresenceSample, "presence_sample")):
        filter_expr = (model.tenant_id == tenant.id) & (model.ts < retention_cutoff_ts)
        if dry_run:
            counts[key] = db.query(model).filter(filter_expr).count()
        else:
            counts[key] = _batched_delete_by_id(db, model, filter_expr)

    # 5. audit: audit_days, ts is a TZDateTime column.
    audit_filter = (m.Audit.tenant_id == tenant.id) & (m.Audit.ts < audit_cutoff)
    if dry_run:
        counts["audit"] = db.query(m.Audit).filter(audit_filter).count()
    else:
        counts["audit"] = _batched_delete_by_id(db, m.Audit, audit_filter)

    # 6. report: retention_days, created is a TZDateTime column; also removes the HTML file.
    counts["report"] = _prune_reports(db, tenant.id, report_cutoff, dry_run)

    return counts


def prune_all(db: DbSession, now: dt.datetime | None = None, dry_run: bool = False) -> dict[str, dict]:
    """`prune_tenant` for every tenant, keyed by slug."""
    now = now or _now()
    return {t.slug: prune_tenant(db, t, now=now, dry_run=dry_run) for t in db.query(m.Tenant).all()}
