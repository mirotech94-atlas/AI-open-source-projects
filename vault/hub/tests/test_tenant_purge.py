"""Deleting a tenant has to take the tenant's rows with it.

It did not. The models declare `ondelete="CASCADE"` on every `tenant_id`
foreign key, which makes `db.delete(tenant)` look sufficient, but SQLite
enforces foreign keys only with `PRAGMA foreign_keys=ON` (off here) and the
constraints are not in the live schema at all. So one `tenant.delete` of a
demo tenant removed exactly one row and stranded 70,604 others -- including
live sessions and an enabled Slack integration -- in tables no view could
reach.

The load-bearing test is `test_purge_clears_every_tenant_scoped_table`: it is
written against the metadata rather than a hand-written list, so a table added
later that scopes by tenant is covered the day it is added, and fails here if
`purge_tenant` stops covering it.
"""
from sqlalchemy import text

from guardian_hub import cli, models as m
from guardian_hub.accounts import NOT_TENANT_SCOPED, TENANT_SCOPED_TABLES, purge_tenant
from guardian_hub.db import SessionLocal

from tests.test_tenants import make_super


def _stub_value(col, marker):
    """A value that satisfies one NOT NULL column, whatever its type.

    The point of the sweep below is to put a row in *every* tenant-scoped
    table without naming any of them, so the columns cannot be known here.
    String values carry `marker` so the two tenants' rows never collide on a
    UNIQUE constraint.
    """
    t = str(col.type).upper()
    if "BOOL" in t:
        return 0
    if "INT" in t and "POINT" not in t:
        return 1
    if "FLOAT" in t or "REAL" in t or "NUMERIC" in t or "DECIMAL" in t:
        return 1.0
    if "JSON" in t:
        return "{}"
    if "DATE" in t or "TIME" in t:
        return "2026-01-01 00:00:00"
    value = f"{marker}-{col.name}"
    length = getattr(col.type, "length", None)
    return value[-length:] if length else value


def _seed_one_row(db, table, tenant_id, marker, user_id, seeded=None):
    """Insert a single minimal row into `table` owned by `tenant_id`.

    Raw SQL bypasses SQLAlchemy's Python-side column defaults, so every
    NOT NULL column needs a value here even when the model declares one.
    Unlike production, the test schema is built by `create_all` and so has
    every declared foreign key, enforced -- a NOT NULL one needs a real
    referent, not a stub.

    `seeded` maps (table name, tenant id) to the primary key this helper last
    inserted there, so a row can point at a parent already seeded for the SAME
    tenant -- `ticket_message.ticket_id` is the first of these. The caller
    walks `sorted_tables`, which is dependency-ordered, so a parent is always
    seeded before its child. Scoping the lookup by tenant matters: a child
    hung off the OTHER tenant's parent would be deleted by that tenant's
    cascade rather than by the purge, and the sweep would pass while proving
    nothing.
    """
    seeded = {} if seeded is None else seeded
    cols = {"tenant_id": tenant_id}
    for col in table.c:
        if col.name in cols or col.server_default is not None:
            continue
        if col.primary_key:
            # Let an integer PK autoincrement; give a string PK the marker.
            if "INT" not in str(col.type).upper():
                cols[col.name] = _stub_value(col, marker)
            continue
        if col.nullable:
            continue
        target = next((fk.column.table.name for fk in col.foreign_keys), None)
        if target == "user":
            cols[col.name] = user_id
        elif (target, tenant_id) in seeded:
            cols[col.name] = seeded[(target, tenant_id)]
        elif target is not None:
            # A new NOT NULL foreign key to something other than user/tenant
            # would need its own referent seeded; fail loudly rather than
            # quietly skipping the table this sweep exists to cover.
            raise AssertionError(
                f"{table.name}.{col.name} references {target}; teach this helper about it"
            )
        else:
            cols[col.name] = _stub_value(col, marker)
    names = ", ".join(cols)
    binds = ", ".join(f":{k}" for k in cols)
    result = db.execute(text(f"INSERT INTO {table.name} ({names}) VALUES ({binds})"), cols)
    pk = next((c for c in table.c if c.primary_key), None)
    if pk is not None:
        seeded[(table.name, tenant_id)] = cols.get(pk.name, result.lastrowid)


def _count(db, table, tenant_id):
    return db.execute(
        text(f"SELECT COUNT(*) FROM {table} WHERE tenant_id = :t"), {"t": tenant_id}
    ).scalar()


def test_tenant_scoped_tables_is_read_off_the_metadata(client):
    # Derived, not hand-listed -- these are the ones whose absence caused real
    # damage, so they are named here to catch a regression to a static list.
    for name in ("alert", "session", "integration", "metric", "audit",
                 "recommendation", "chat_thread", "presence_sample"):
        assert name in TENANT_SCOPED_TABLES
    # And nothing that has no tenant to scope by.
    for name in ("tenant", "user", "otp", "ip_geo", "lead"):
        assert name not in TENANT_SCOPED_TABLES


def test_purge_clears_every_tenant_scoped_table(client):
    db = SessionLocal()
    doomed = m.Tenant(slug="doomed", name="Doomed")
    keeper = m.Tenant(slug="keeper", name="Keeper")
    db.add_all([doomed, keeper])
    db.commit()

    user = m.User(username="purge-probe", email="p@example.com")
    db.add(user)
    db.commit()

    tables = [
        t for t in m.Base.metadata.sorted_tables
        if "tenant_id" in t.c and t.name not in NOT_TENANT_SCOPED
    ]
    assert len(tables) == len(TENANT_SCOPED_TABLES)
    seeded: dict[tuple[str, str], object] = {}
    for table in tables:
        _seed_one_row(db, table, doomed.id, f"d-{table.name}"[:32], user.id, seeded)
        _seed_one_row(db, table, keeper.id, f"k-{table.name}"[:32], user.id, seeded)
    db.commit()

    # Every table really did get a row, or the sweep below proves nothing.
    for table in tables:
        assert _count(db, table.name, doomed.id) == 1, table.name

    purged = purge_tenant(db, doomed.id)
    db.commit()

    # A table whose parent is itself tenant-scoped -- ticket_message under
    # ticket -- can be emptied by the parent's cascade before the purge loop
    # reaches it, and then honestly reports nothing deleted. That cascade is an
    # artefact of the TEST schema: `create_all` emits every declared foreign
    # key, while `purge_tenant`'s docstring records that only 12 of these
    # tables carry one in the deployed schema. So the report is allowed to be
    # silent for a child, and required for everything else, which is where
    # production has no cascade to lean on.
    parented = {
        t.name for t in tables
        if any(fk.column.table.name in {x.name for x in tables}
               for col in t.c for fk in col.foreign_keys)
    }
    for table in tables:
        assert _count(db, table.name, doomed.id) == 0, f"{table.name} kept rows"
        assert _count(db, table.name, keeper.id) == 1, f"{table.name} lost a neighbour"
        # Either way the table must be one the purge sweeps by name, so the
        # deployed schema -- cascade or no cascade -- still reaches its rows.
        assert table.name in TENANT_SCOPED_TABLES, table.name
        if table.name in parented:
            assert purged.get(table.name, 0) in (0, 1), table.name
        else:
            assert purged[table.name] == 1
    db.close()


def test_purge_reports_only_tables_it_actually_emptied(client):
    db = SessionLocal()
    t = m.Tenant(slug="sparse", name="Sparse")
    db.add(t)
    db.commit()
    db.add(m.Alert(tenant_id=t.id, key="k", sev="INFO", msg="m", first_seen="2026-01-01", last_seen="2026-01-01"))
    db.commit()

    purged = purge_tenant(db, t.id)
    db.commit()
    assert purged == {"alert": 1}
    db.close()


def test_purge_leaves_the_tenant_row_for_the_caller_to_delete(client):
    db = SessionLocal()
    t = m.Tenant(slug="still-here", name="Still Here")
    db.add(t)
    db.commit()
    tid = t.id

    purge_tenant(db, tid)
    db.commit()
    assert db.get(m.Tenant, tid) is not None
    db.close()


def test_purge_unlinks_leads_but_keeps_them(client):
    """A lead is the owner's sales-pipeline record, not the tenant's data --
    it must survive the tenant it produced being deleted, just pointing at
    no tenant any more."""
    db = SessionLocal()
    t = m.Tenant(slug="produced-by-lead", name="Produced By Lead")
    db.add(t)
    db.commit()
    tid = t.id

    lead = m.Lead(name="Dana", email="dana@example.com", status="approved", tenant_id=tid)
    db.add(lead)
    db.commit()
    lead_id = lead.id

    purged = purge_tenant(db, tid)
    db.commit()
    assert "lead" not in purged

    db.expire_all()
    kept = db.get(m.Lead, lead_id)
    assert kept is not None
    assert kept.tenant_id is None
    assert kept.status == "approved"
    db.close()


def test_deleting_a_tenant_over_the_api_strands_nothing(client):
    make_super(client)
    new_id = client.post(
        "/api/v1/settings/tenants", json={"name": "Northwind", "slug": "northwind"}
    ).json()["id"]

    db = SessionLocal()
    db.add(m.Alert(tenant_id=new_id, key="k", sev="WARN", msg="m", first_seen="2026-01-01", last_seen="2026-01-01"))
    db.add(m.Session(id="tok-orphan", user_id=db.query(m.User).first().id,
                     tenant_id=new_id, device_label="curl", expires=m.utcnow()))
    db.commit()
    db.close()

    r = client.delete(f"/api/v1/settings/tenants/{new_id}")
    assert r.status_code == 200
    assert r.json()["purged"]["alert"] == 1
    assert r.json()["purged"]["session"] == 1

    db = SessionLocal()
    assert db.get(m.Tenant, new_id) is None
    for table in TENANT_SCOPED_TABLES:
        assert _count(db, table, new_id) == 0, f"{table} outlived its tenant"
    db.close()


def test_the_deletion_is_still_on_the_record_afterwards(client):
    # The audit row used to be written under the tenant being deleted, so the
    # purge erased the only evidence the deletion happened. It belongs to the
    # superadmin's own tenant instead.
    make_super(client)
    home = client.get("/api/v1/me").json()["active_tenant"]["id"]
    new_id = client.post(
        "/api/v1/settings/tenants", json={"name": "Gone", "slug": "gone"}
    ).json()["id"]
    assert client.delete(f"/api/v1/settings/tenants/{new_id}").status_code == 200

    db = SessionLocal()
    row = db.query(m.Audit).filter_by(action="tenant.delete").one()
    assert row.tenant_id == home
    assert row.target == "gone"
    assert row.body["tenant_id"] == new_id
    db.close()


# --------------------------------------------------------------------------
# `guardian tenant purge-orphans` -- the cleanup for rows the old delete left
# behind. This is the command that gets pointed at a production database, so
# the thing worth proving is that the default run changes nothing.
# --------------------------------------------------------------------------

def _strand_rows(db, how_many=3):
    """Reproduce the old bug: a tenant's rows outliving the tenant row."""
    t = m.Tenant(slug="ghost", name="Ghost")
    db.add(t)
    db.commit()
    tid = t.id
    for i in range(how_many):
        db.add(m.Alert(tenant_id=tid, key=f"k{i}", sev="INFO", msg="m",
                       first_seen="2026-01-01", last_seen="2026-01-01"))
    db.commit()
    # Delete ONLY the tenant row, which is exactly what the old endpoint did.
    db.execute(text("DELETE FROM tenant WHERE id = :t"), {"t": tid})
    db.commit()
    return tid


def test_purge_orphans_reports_without_changing_anything_by_default(client, capsys):
    db = SessionLocal()
    tid = _strand_rows(db)
    db.close()

    cli.main(["tenant", "purge-orphans"])
    out = capsys.readouterr().out
    assert "would purge 3 row(s)" in out and tid in out
    assert "re-run with --apply" in out

    db = SessionLocal()
    assert _count(db, "alert", tid) == 3, "a dry run must not delete anything"
    db.close()


def test_purge_orphans_apply_clears_them_and_spares_live_tenants(client, capsys):
    db = SessionLocal()
    tid = _strand_rows(db)
    live = m.Tenant(slug="alive", name="Alive")
    db.add(live)
    db.commit()
    db.add(m.Alert(tenant_id=live.id, key="k", sev="INFO", msg="m",
                   first_seen="2026-01-01", last_seen="2026-01-01"))
    db.commit()
    live_id = live.id
    db.close()

    cli.main(["tenant", "purge-orphans", "--apply"])
    assert "purged 3 row(s)" in capsys.readouterr().out

    db = SessionLocal()
    assert _count(db, "alert", tid) == 0
    assert _count(db, "alert", live_id) == 1, "a living tenant lost rows"
    db.close()


def test_purge_orphans_says_so_when_there_is_nothing_to_do(client, capsys):
    cli.main(["tenant", "purge-orphans"])
    assert "no orphaned rows" in capsys.readouterr().out
