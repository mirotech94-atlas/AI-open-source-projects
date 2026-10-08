"""Keep `FlowPeer` current, and answer "which of these peers were seen before?"

The question used to be answered by a DISTINCT over every raw flow in the
retention window. On the production home network that was 3.9 million rows
and 70-76 seconds for each Live connections or Topology load, on a one-vCPU
hub whose database no longer fits in memory (2026-10-06).

`FlowPeer` turns it into a lookup: one row per (tenant, network, peer) with
the first and last time it was seen. Two rules make it trustworthy:

- **Every new `Flow` updates it**, from a flush hook rather than from each
  writer. Ingest, the demo trickle, seeding and tests all add `Flow` rows,
  and a table that one writer forgets to update reads every peer it carries
  as "new" -- silently, and only on the networks that writer serves.
- **Times are stored in UTC.** `Flow.ts` keeps the sensor's own offset, so
  comparing it as text is lexicographic, not chronological (CLAUDE.md,
  history.py). Normalising here once is what lets the read side compare in
  SQL and be exact.

The upsert is a single INSERT ... ON CONFLICT that widens first/last, so two
ingests noting the same new peer at once cannot collide on the unique key.
"""
from __future__ import annotations

import datetime as dt
from typing import Iterable

from sqlalchemy import event, func
from sqlalchemy.orm import Session

UTC_FMT = "%Y-%m-%dT%H:%M:%S+00:00"


def utc_text(when: dt.datetime) -> str:
    """`when` as the UTC text `FlowPeer` stores and compares."""
    return when.astimezone(dt.timezone.utc).strftime(UTC_FMT)


def label_of(peer_name: str | None, peer_ip: str | None) -> str:
    """The same label the traffic payloads show: the name the sensor saw,
    else the address. Kept here so the two can never disagree."""
    return (peer_name or "") or (peer_ip or "")


def _upsert(conn, rows: list[dict]) -> None:
    from .models import FlowPeer
    if conn.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
        lo, hi = func.least, func.greatest
    else:
        from sqlalchemy.dialects.sqlite import insert
        lo, hi = func.min, func.max          # SQLite's two-argument scalar forms
    stmt = insert(FlowPeer.__table__)
    stmt = stmt.on_conflict_do_update(
        index_elements=["tenant_id", "network_id", "label"],
        set_={"first_seen": lo(FlowPeer.__table__.c.first_seen, stmt.excluded.first_seen),
              "last_seen": hi(FlowPeer.__table__.c.last_seen, stmt.excluded.last_seen)},
    )
    for i in range(0, len(rows), 500):
        conn.execute(stmt, rows[i:i + 500])


def note(conn, flows: Iterable) -> int:
    """Record the peers of `flows` (any objects with tenant_id, network_id,
    ts, peer_name, peer_ip). Rows whose `ts` cannot be parsed are skipped:
    they cannot say when anything was seen. Returns peers written."""
    from .scans import parse_ts
    acc: dict[tuple, list[str]] = {}
    for f in flows:
        label = label_of(f.peer_name, f.peer_ip)
        when = parse_ts(f.ts) if f.ts else None
        if not label or when is None:
            continue
        t = utc_text(when)
        key = (f.tenant_id, f.network_id or "", label[:255])
        span = acc.get(key)
        if span is None:
            acc[key] = [t, t]
        else:
            span[0] = min(span[0], t); span[1] = max(span[1], t)
    if acc:
        _upsert(conn, [{"tenant_id": k[0], "network_id": k[1], "label": k[2],
                        "first_seen": v[0], "last_seen": v[1]} for k, v in acc.items()])
    return len(acc)


@event.listens_for(Session, "after_flush")
def _note_new_flows(session, flush_context) -> None:
    from .models import Flow
    new = [o for o in session.new if isinstance(o, Flow)]
    if new:
        note(session.connection(), new)


def seen_before(db, tenant_id: str, labels: Iterable[str], before: dt.datetime,
                network_id: str | None = None) -> set[str]:
    """Which of `labels` were first seen before `before`.

    Only asks about the peers given -- the ones on screen -- never about the
    whole history. A peer stays known until `prune` drops it for having been
    silent longer than raw flow retention, so "not seen before" means "not
    seen in roughly the last week", the meaning the raw-table scan had."""
    from .models import FlowPeer
    labels = list({x for x in labels if x})
    out: set[str] = set()
    cut = utc_text(before)
    for i in range(0, len(labels), 500):
        q = db.query(FlowPeer.label).filter(FlowPeer.tenant_id == tenant_id,
                                            FlowPeer.label.in_(labels[i:i + 500]),
                                            FlowPeer.first_seen < cut)
        if network_id is not None:
            q = q.filter(FlowPeer.network_id == network_id)
        out.update(x for (x,) in q.distinct().all())
    return out


def prune(db, tenant_id: str, silent_since: dt.datetime) -> int:
    """Forget peers not seen since `silent_since`, so one that comes back
    after longer than raw retention is new again."""
    from .models import FlowPeer
    return (db.query(FlowPeer)
            .filter(FlowPeer.tenant_id == tenant_id, FlowPeer.last_seen < utc_text(silent_since))
            .delete(synchronize_session=False))
