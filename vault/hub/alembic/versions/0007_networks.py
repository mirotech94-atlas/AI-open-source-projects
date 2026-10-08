"""network table + network_id on device/event/alert/dns_query/presence_sample,
and incident.sensor_id

Task 51 / spec S3.1: a tenant watches several networks (home Wi-Fi + a lab
private network), each with its own sensor, baseline, presence and DNS
attribution.

Three tables have to be RECREATED rather than altered, because SQLite cannot
drop an unnamed UNIQUE constraint:

- `device` loses `UNIQUE(tenant_id, mac)` and gains
  `uq_device_net_mac (tenant_id, network_id, mac)` -- the same MAC on two
  networks is two devices.
- `presence_sample` goes from `UNIQUE(tenant_id, ts, mac)` to
  `uq_pres_ts_mac_net (tenant_id, ts, mac, network_id)` -- presence is per
  network, so `payloads/presence.screen_time` de-duplicates on
  `distinct (ts, mac)` for its per-tenant totals.
- `incident` gains `sensor_id` and goes from `UNIQUE(tenant_id, iid)` to
  `uq_incident_sensor_iid (tenant_id, sensor_id, iid)` -- each sensor numbers
  its own incidents from `state.incident_seq` starting at 100, so two sensors
  on one tenant used to overwrite each other's rows on `iid` collision, and
  one sensor's push closed the other's live incidents.

The `copy_from` literals below describe the tables as they stand at 0006 --
nothing between 0002 and 0006 touched any of the three -- MINUS the constraint
being dropped. A column omitted from one of those literals would be silently
dropped by the recreate, so they are exhaustive on purpose.

Data steps (all idempotent, all guarded so a hand-edited row cannot fail the
whole migration):

1. Every existing tenant gets exactly ONE network: `slug="primary"`,
   `kind="home"` (the migration genuinely cannot tell Wi-Fi from wired, and
   `networks.KINDS` accepts `home` for exactly this reason), name from the
   tenant's current `config["lan"]` block, `sensor_id` = the tenant's only
   sensor when it has exactly one. `is_primary=True`, so `ensure_primary`
   matches it on the very next ingest instead of creating a second network.
2. Every device/event/alert/dns_query/presence_sample row of that tenant is
   attached to it.
3. `incident.sensor_id` is backfilled with the tenant's only sensor where
   exactly one exists, and left NULL otherwise.
4. LAN alert keys are namespaced per network: `lan:new:<mac>` ->
   `lan:primary:new:<mac>`. `ingest.store_scan` does the same rewrite on every
   push, because the sensor keeps emitting the legacy shape until Task 52
   ships -- without both, the next push after this migration would re-fire
   every LAN alert as a new row and leave the rewritten ones stale-open.

Revision ID: 0007_networks
Revises: 0006_integration_secrets
Create Date: 2026-09-10

"""
import datetime as dt
import json
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0007_networks'
down_revision: Union[str, Sequence[str], None] = '0006_integration_secrets'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Mirrors `guardian_hub.networks.LAN_SUBKEYS`; duplicated rather than imported
#: so this migration keeps meaning the same thing if that vocabulary grows.
LAN_SUBKEYS = ("baseline", "rotated", "new", "missing", "port", "offline",
               "flapping", "unverified", "unusual-hour")

SCOPED_TABLES = ("device", "event", "alert", "dns_query", "presence_sample")


def _device_table(with_network: bool) -> sa.Table:
    cols = [
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32), nullable=False),
    ]
    if with_network:
        cols.append(sa.Column("network_id", sa.String(32), nullable=True))
    cols += [
        sa.Column("mac", sa.String(17), nullable=False),
        sa.Column("ip", sa.String(45), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("vendor", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("trusted", sa.Boolean(), nullable=False),
        sa.Column("always_on", sa.Boolean(), nullable=False),
        sa.Column("quarantined", sa.Boolean(), nullable=False),
        sa.Column("parent", sa.String(17), nullable=True),
        sa.Column("risk", sa.Integer(), nullable=False),
        sa.Column("presence", sa.String(12), nullable=False),
        sa.Column("ports", sa.JSON(), nullable=False),
        sa.Column("first_seen", sa.String(32), nullable=False),
        sa.Column("last_seen", sa.String(32), nullable=False),
        sa.Column("extra", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="CASCADE"),
    ]
    return sa.Table("device", sa.MetaData(), *cols)


def _presence_table(with_network: bool) -> sa.Table:
    cols = [
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(32), nullable=False),
    ]
    if with_network:
        cols.append(sa.Column("network_id", sa.String(32), nullable=True))
    cols += [
        sa.Column("ts", sa.String(32), nullable=False),
        sa.Column("mac", sa.String(17), nullable=False),
    ]
    return sa.Table("presence_sample", sa.MetaData(), *cols)


def _incident_table(with_sensor: bool) -> sa.Table:
    cols = [
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32), nullable=False),
    ]
    if with_sensor:
        cols.append(sa.Column("sensor_id", sa.String(32), nullable=True))
    cols += [
        sa.Column("iid", sa.String(32), nullable=False),
        sa.Column("entity", sa.String(120), nullable=False),
        sa.Column("sev", sa.String(8), nullable=False),
        sa.Column("story", sa.Text(), nullable=False),
        sa.Column("actions", sa.JSON(), nullable=False),
        sa.Column("alert_keys", sa.JSON(), nullable=False),
        sa.Column("opened", sa.String(32), nullable=False),
        sa.Column("closed", sa.String(32), nullable=True),
    ]
    return sa.Table("incident", sa.MetaData(), *cols)


def _namespaced(key: str, slug: str) -> str:
    if not key or not key.startswith("lan:"):
        return key
    rest = key[4:]
    if rest.split(":", 1)[0] not in LAN_SUBKEYS:
        return key          # already namespaced
    return f"lan:{slug}:{rest}"


def _legacy(key: str) -> str:
    """`lan:<slug>:new:<mac>` -> `lan:new:<mac>`; anything else unchanged.
    The inverse of `_namespaced` (mirrors `networks.canonical_lan_key`)."""
    if not key or not key.startswith("lan:"):
        return key
    parts = key.split(":")
    if len(parts) >= 3 and parts[1] not in LAN_SUBKEYS and parts[2] in LAN_SUBKEYS:
        return "lan:" + ":".join(parts[2:])
    return key


def _try_write(conn, table: str, row_id, sql: str, params: dict) -> None:
    """Belt-and-braces for `downgrade`'s "never raise" contract: the de-dup and
    clash guards above make an IntegrityError here unreachable, but if a
    hand-edited row still produces one, the offending row is dropped rather
    than aborting a rollback half-way through. SQLite does not abort the
    surrounding transaction on a statement error, so continuing is safe."""
    try:
        conn.execute(sa.text(sql), params)
    except sa.exc.SQLAlchemyError:
        try:
            conn.execute(sa.text(f"DELETE FROM {table} WHERE id = :id"), {"id": row_id})
        except sa.exc.SQLAlchemyError:
            pass


def upgrade() -> None:
    op.create_table(
        "network",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("slug", sa.String(24), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False, server_default="wifi"),
        sa.Column("subnet_prefix", sa.String(15), nullable=False, server_default=""),
        sa.Column("expected_gateway", sa.String(45), nullable=False, server_default=""),
        sa.Column("ssid", sa.String(64), nullable=False, server_default=""),
        sa.Column("sensor_id", sa.String(32), nullable=True),
        sa.Column("colour", sa.String(16), nullable=False, server_default=""),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "slug"),
    )
    op.create_index(op.f("ix_network_tenant_id"), "network", ["tenant_id"], unique=False)

    for table in ("event", "alert", "dns_query"):
        op.add_column(table, sa.Column("network_id", sa.String(32), nullable=True))
        op.create_index(f"ix_{table}_network_id", table, ["network_id"], unique=False)

    with op.batch_alter_table("device", copy_from=_device_table(False), recreate="always") as b:
        b.add_column(sa.Column("network_id", sa.String(32), nullable=True))
        b.create_unique_constraint("uq_device_net_mac", ["tenant_id", "network_id", "mac"])
    op.create_index(op.f("ix_device_tenant_id"), "device", ["tenant_id"], unique=False)
    op.create_index("ix_device_network_id", "device", ["network_id"], unique=False)

    with op.batch_alter_table("presence_sample", copy_from=_presence_table(False), recreate="always") as b:
        b.add_column(sa.Column("network_id", sa.String(32), nullable=True))
        b.create_unique_constraint("uq_pres_ts_mac_net", ["tenant_id", "ts", "mac", "network_id"])
    op.create_index("ix_pres_t", "presence_sample", ["tenant_id", "ts"], unique=False)
    op.create_index("ix_pres_mac", "presence_sample", ["tenant_id", "mac", "ts"], unique=False)
    op.create_index("ix_presence_sample_network_id", "presence_sample", ["network_id"], unique=False)

    with op.batch_alter_table("incident", copy_from=_incident_table(False), recreate="always") as b:
        b.add_column(sa.Column("sensor_id", sa.String(32), nullable=True))
        b.create_unique_constraint("uq_incident_sensor_iid", ["tenant_id", "sensor_id", "iid"])
    op.create_index(op.f("ix_incident_tenant_id"), "incident", ["tenant_id"], unique=False)
    op.create_index("ix_incident_sensor_id", "incident", ["sensor_id"], unique=False)

    # ---- data -----------------------------------------------------------
    conn = op.get_bind()
    for tenant_id, config_raw in conn.execute(sa.text("SELECT id, config FROM tenant")).fetchall():
        try:
            cfg = json.loads(config_raw) if isinstance(config_raw, str) else (config_raw or {})
        except Exception:
            cfg = {}
        lan = (cfg.get("lan") or {}) if isinstance(cfg, dict) else {}
        name = lan.get("name") or "Home network"
        prefix = lan.get("subnet_prefix") or ""
        if not prefix:
            subnet = str(lan.get("subnet") or "")
            parts = subnet.split("/")[0].split(".")
            prefix = ".".join(parts[:3]) if len(parts) == 4 else ""
        sensors = conn.execute(sa.text("SELECT id FROM sensor WHERE tenant_id = :t"),
                               {"t": tenant_id}).fetchall()
        sensor_id = sensors[0][0] if len(sensors) == 1 else None

        existing = conn.execute(sa.text("SELECT id FROM network WHERE tenant_id = :t AND slug = 'primary'"),
                                {"t": tenant_id}).first()
        if existing:
            net_id = existing[0]
        else:
            net_id = uuid.uuid4().hex
            conn.execute(sa.text(
                "INSERT INTO network (id, tenant_id, name, slug, kind, subnet_prefix, "
                "expected_gateway, ssid, sensor_id, colour, is_primary, status, created) "
                "VALUES (:id, :t, :name, 'primary', 'home', :prefix, :gw, '', :sid, '', 1, "
                ":status, :created)"),
                {"id": net_id, "t": tenant_id, "name": name, "prefix": prefix,
                 "gw": lan.get("expected_gateway") or "", "sid": sensor_id,
                 "status": "active" if sensor_id else "no-sensor",
                 "created": dt.datetime.now(dt.timezone.utc)})

        for table in SCOPED_TABLES:
            conn.execute(sa.text(
                f"UPDATE {table} SET network_id = :nid WHERE tenant_id = :t AND network_id IS NULL"),
                {"nid": net_id, "t": tenant_id})
        if sensor_id:
            conn.execute(sa.text(
                "UPDATE incident SET sensor_id = :sid WHERE tenant_id = :t AND sensor_id IS NULL"),
                {"sid": sensor_id, "t": tenant_id})

        # LAN alert keys -> lan:primary:<sub>:<rest>
        rows = conn.execute(sa.text(
            "SELECT id, key FROM alert WHERE tenant_id = :t AND key LIKE 'lan:%'"),
            {"t": tenant_id}).fetchall()
        for row_id, key in rows:
            new_key = _namespaced(key, "primary")
            if new_key == key:
                continue
            clash = conn.execute(sa.text(
                "SELECT id FROM alert WHERE tenant_id = :t AND key = :k"),
                {"t": tenant_id, "k": new_key}).first()
            if clash:
                # A rewritten twin already exists (a partially-applied run):
                # drop the legacy row rather than violating (tenant_id, key).
                conn.execute(sa.text("DELETE FROM alert WHERE id = :id"), {"id": row_id})
                continue
            conn.execute(sa.text("UPDATE alert SET key = :k WHERE id = :id"),
                         {"k": new_key, "id": row_id})


def downgrade() -> None:
    """Mirror of `upgrade`, and it must NEVER raise: a rollback that dies
    half-way leaves a hub with three recreated tables and no `network` table.

    Going down, `network_id` disappears, so the only real hazards are the two
    uniques that come BACK -- `device UNIQUE(tenant_id, mac)` and
    `presence_sample UNIQUE(tenant_id, ts, mac)` -- plus `alert`'s
    `UNIQUE(tenant_id, key)` once the LAN keys are stripped back to their
    legacy shape (two networks holding `lan:<slug>:new:<mac>` for the same MAC
    both strip to one legacy key).

    So, per tenant, before any table is recreated:

    1. `device` and `presence_sample` are walked primary-first and de-duplicated
       on `(tenant, mac)` / `(tenant, ts, mac)`: the keeper is reassigned to the
       primary, and only a row that would VIOLATE the returning unique is
       deleted. Driven from `tenant` rather than from `network` so rows with a
       dangling or NULL `network_id` are de-duplicated too.
    2. `event` and `dns_query` rows are reassigned to the primary and never
       deleted -- history survives a rollback (`network_id` is dropped moments
       later, so this is about row SURVIVAL, not about the column's value).
    3. `alert` rows are reassigned and their LAN keys rewritten back to the
       legacy shape with the same clash guard `upgrade` uses at :244-246: the
       loser is deleted only when a legacy-shaped twin already holds
       `(tenant_id, key)`.
    """
    conn = op.get_bind()

    tenants = [r[0] for r in conn.execute(sa.text("SELECT id FROM tenant")).fetchall()]
    for tenant_id in tenants:
        prim = conn.execute(sa.text(
            "SELECT id FROM network WHERE tenant_id = :t AND is_primary = 1 "
            "ORDER BY created LIMIT 1"), {"t": tenant_id}).first()
        if prim is None:
            prim = conn.execute(sa.text(
                "SELECT id FROM network WHERE tenant_id = :t ORDER BY created LIMIT 1"),
                {"t": tenant_id}).first()
        prim_id = prim[0] if prim else None
        # Primary-first without a join, so the row that survives a de-dup is the
        # primary network's one; `id` only breaks ties deterministically.
        order = "CASE WHEN network_id = :prim THEN 0 ELSE 1 END, id"
        params = {"t": tenant_id, "prim": prim_id}

        # 1. device / presence_sample: keep one row per returning unique key.
        for table, key_cols in (("device", ("mac",)), ("presence_sample", ("ts", "mac"))):
            cols = ", ".join(key_cols)
            rows = conn.execute(sa.text(
                f"SELECT id, {cols} FROM {table} WHERE tenant_id = :t ORDER BY {order}"),
                params).fetchall()
            seen: set = set()
            for row in rows:
                row_id, key = row[0], tuple(row[1:])
                if key in seen:
                    conn.execute(sa.text(f"DELETE FROM {table} WHERE id = :id"), {"id": row_id})
                    continue
                seen.add(key)
                if prim_id:
                    _try_write(conn, table, row_id,
                               f"UPDATE {table} SET network_id = :nid WHERE id = :id",
                               {"nid": prim_id, "id": row_id})

        # 2. event / dns_query: nothing can collide, so reassign in bulk.
        if prim_id:
            for table in ("event", "dns_query"):
                conn.execute(sa.text(
                    f"UPDATE {table} SET network_id = :nid WHERE tenant_id = :t"),
                    {"nid": prim_id, "t": tenant_id})

        # 3. alert: reassign AND strip the network segment off every LAN key.
        rows = conn.execute(sa.text(
            f"SELECT id, key FROM alert WHERE tenant_id = :t ORDER BY {order}"), params).fetchall()
        for row_id, key in rows:
            legacy = _legacy(key)
            if legacy != key:
                clash = conn.execute(sa.text(
                    "SELECT id FROM alert WHERE tenant_id = :t AND key = :k AND id != :id"),
                    {"t": tenant_id, "k": legacy, "id": row_id}).first()
                if clash:
                    # The same finding on a second network, or a legacy twin the
                    # upgrade's own clash branch left behind: one of them has to
                    # go, and `UNIQUE(tenant_id, key)` decides which.
                    conn.execute(sa.text("DELETE FROM alert WHERE id = :id"), {"id": row_id})
                    continue
                _try_write(conn, "alert", row_id,
                           "UPDATE alert SET key = :k WHERE id = :id",
                           {"k": legacy, "id": row_id})
            if prim_id:
                _try_write(conn, "alert", row_id,
                           "UPDATE alert SET network_id = :nid WHERE id = :id",
                           {"nid": prim_id, "id": row_id})

    op.drop_index("ix_incident_sensor_id", table_name="incident")
    # `copy_from` makes the literal the authoritative shape, so the unique
    # being removed is simply absent from it rather than dropped by name.
    with op.batch_alter_table("incident", copy_from=_incident_table(True), recreate="always") as b:
        b.drop_column("sensor_id")
        b.create_unique_constraint("uq_incident_tenant_iid", ["tenant_id", "iid"])
    op.create_index(op.f("ix_incident_tenant_id"), "incident", ["tenant_id"], unique=False)

    op.drop_index("ix_presence_sample_network_id", table_name="presence_sample")
    with op.batch_alter_table("presence_sample", copy_from=_presence_table(True), recreate="always") as b:
        b.drop_column("network_id")
        b.create_unique_constraint("uq_pres_ts_mac", ["tenant_id", "ts", "mac"])
    op.create_index("ix_pres_t", "presence_sample", ["tenant_id", "ts"], unique=False)
    op.create_index("ix_pres_mac", "presence_sample", ["tenant_id", "mac", "ts"], unique=False)

    op.drop_index("ix_device_network_id", table_name="device")
    with op.batch_alter_table("device", copy_from=_device_table(True), recreate="always") as b:
        b.drop_column("network_id")
        b.create_unique_constraint("uq_device_tenant_mac", ["tenant_id", "mac"])
    op.create_index(op.f("ix_device_tenant_id"), "device", ["tenant_id"], unique=False)

    for table in ("dns_query", "alert", "event"):
        op.drop_index(f"ix_{table}_network_id", table_name=table)
        with op.batch_alter_table(table) as b:
            b.drop_column("network_id")

    op.drop_index(op.f("ix_network_tenant_id"), table_name="network")
    op.drop_table("network")
