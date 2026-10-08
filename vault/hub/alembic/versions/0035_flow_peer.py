"""`flow_peer`: when each destination was first and last seen, so "is this
peer new?" stops scanning a week of raw flows (70+ s per page on a real home
network, 2026-10-06). See `guardian_hub/flowpeers.py`.

The backfill reads the raw table ONCE, grouped by (tenant, network, label,
UTC offset): within one offset `ts` text sorts chronologically, so MIN/MAX
per offset are exact, and each is parsed to UTC in Python before the
per-peer min/max is taken. The result is a few thousand rows however many
flows there are.
"""
import datetime as dt

from alembic import op
import sqlalchemy as sa

revision = "0035_flow_peer"
down_revision = "0034_session_via_demo"
branch_labels = None
depends_on = None


def _utc(ts: str):
    try:
        when = dt.datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        return None
    return when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def upgrade() -> None:
    # READ FIRST, create after. The grouped scan takes ~160 s on the
    # production home network (3.9M flows, 2026-10-06). SQLite takes its
    # write lock at the first DDL or DML, so creating the table first would
    # hold that lock through the whole scan and lock every sensor ingest and
    # portal write out for minutes. A plain SELECT takes no write lock.
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT tenant_id, COALESCE(network_id, ''), "
        "       COALESCE(NULLIF(peer_name, ''), peer_ip) AS label, "
        "       substr(ts, 20) AS off, MIN(ts), MAX(ts) "
        "FROM flow WHERE COALESCE(NULLIF(peer_name, ''), peer_ip) <> '' "
        "GROUP BY tenant_id, COALESCE(network_id, ''), label, off")).fetchall()
    acc: dict[tuple, list[str]] = {}
    for tenant_id, net, label, _off, lo, hi in rows:
        a, b = _utc(lo), _utc(hi)
        if a is None or b is None:
            continue
        key = (tenant_id, net, label[:255])
        span = acc.get(key)
        if span is None:
            acc[key] = [a, b]
        else:
            span[0] = min(span[0], a); span[1] = max(span[1], b)

    op.create_table(
        "flow_peer",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("network_id", sa.String(32), nullable=False, server_default=""),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("first_seen", sa.String(32), nullable=False),
        sa.Column("last_seen", sa.String(32), nullable=False),
        sa.UniqueConstraint("tenant_id", "network_id", "label", name="uq_flow_peer"),
    )
    op.create_index("ix_flow_peer_label", "flow_peer", ["tenant_id", "label"])
    table = sa.table("flow_peer", sa.column("tenant_id"), sa.column("network_id"), sa.column("label"),
                     sa.column("first_seen"), sa.column("last_seen"))
    batch = [{"tenant_id": k[0], "network_id": k[1], "label": k[2], "first_seen": v[0], "last_seen": v[1]}
             for k, v in acc.items()]
    for i in range(0, len(batch), 1000):
        op.bulk_insert(table, batch[i:i + 1000])


def downgrade() -> None:
    op.drop_index("ix_flow_peer_label", table_name="flow_peer")
    op.drop_table("flow_peer")
