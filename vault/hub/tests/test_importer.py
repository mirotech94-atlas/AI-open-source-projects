import json
import pathlib

from guardian_hub.config import settings
from guardian_hub.db import SessionLocal
from guardian_hub import models as m
from guardian_hub.importer import import_nexus
from tests import scanfixture



def _build_src(tmp_path: pathlib.Path) -> pathlib.Path:
    src = tmp_path / "nexus-hub"
    tdir = src / "tenants" / "myron"
    (tdir / "reports").mkdir(parents=True)

    hub_json = {
        "users": {"miro": {"tenant": "myron", "role": "admin"}},
        "tenants": {
            "myron": {
                "name": "Miro Home",
                "ingest_token": "super-secret-token-should-never-be-copied",
                "ntfy_topic": "myron-alerts",
                "brand": {"name": "Nexus", "accent": "#1f6feb"},
            }
        },
    }
    (src / "hub.json").write_text(json.dumps(hub_json))

    config = {
        "owner": "Miro",
        "sites": [],
        "ssh_hosts": [],
        "roles": {"miro": "admin"},
        "public_url": "x",
    }
    (tdir / "config.json").write_text(json.dumps(config))

    # The rebased pair, not a straight copy of the on-disk fixture: the
    # importer's scan lands with whatever `last["ts"]` says, and the history/
    # event/fix lines below are all derived from that same instant, so a
    # fixed one would make every imported row age out of any window.
    last = scanfixture.load_last()
    (tdir / "last.json").write_text(json.dumps(last))
    (tdir / "state.json").write_text(json.dumps(scanfixture.load_state()))
    ts = last["ts"]
    hist_lines = [
        {
            "ts": ts,
            "sites": {s["name"]: {"ok": s.get("ok"), "lat": s.get("latency_s"), "cert": s.get("cert_days_left")} for s in last["sites"]},
            "hosts": {h["name"]: {"disk": h.get("disk_pct"), "mem": h.get("mem_pct"), "load": h.get("load1"), "failed": h.get("failed_ssh_24h")} for h in last["ssh"]},
            "lan": 9,
            "dns_ms": 12,
            "speed": None,
            "score": 83,
        },
        {
            "ts": ts,
            "sites": {},
            "hosts": {},
            "lan": 9,
            "dns_ms": 14,
            "speed": None,
            "score": 84,
        },
    ]
    (tdir / "history.jsonl").write_text("\n".join(json.dumps(l) for l in hist_lines) + "\n")

    events_lines = [{"ts": ts, "type": "join", "mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.1.250", "vendor": "Test"}]
    (tdir / "events.jsonl").write_text("\n".join(json.dumps(l) for l in events_lines) + "\n")

    remediation_lines = [{"ts": ts, "host": "demo", "action": "reboot", "label": "Reboot demo", "arg": None, "ok": True, "by": "auto"}]
    (tdir / "remediation.jsonl").write_text("\n".join(json.dumps(l) for l in remediation_lines) + "\n")

    (tdir / "audit.jsonl").write_text("")

    (tdir / "reports" / "2026-W36-weekly.html").write_text("<html><body>weekly report</body></html>")

    return src


def test_import_nexus_creates_tenant_users_scan_and_reports(client, tmp_path):
    settings.data_dir = tmp_path / "gdata"
    src = _build_src(tmp_path)

    import_nexus(str(src), {"myron": "home"})

    db = SessionLocal()
    tenant = db.query(m.Tenant).filter_by(slug="home").first()
    assert tenant is not None
    assert "roles" not in tenant.config
    assert "public_url" not in tenant.config
    assert tenant.settings.get("profile", {}).get("owner") == "Miro"
    assert tenant.ntfy_topic == "myron-alerts"

    assert db.query(m.Device).filter_by(tenant_id=tenant.id).count() >= 20
    assert db.query(m.Scan).filter_by(tenant_id=tenant.id).count() == 1
    assert db.query(m.Metric).filter_by(tenant_id=tenant.id).count() > 0
    assert db.query(m.Event).filter_by(tenant_id=tenant.id).count() == 1
    assert db.query(m.Fix).filter_by(tenant_id=tenant.id).count() == 1

    user = db.query(m.User).filter_by(username="miro").first()
    assert user is not None
    mb = db.query(m.Membership).filter_by(user_id=user.id, tenant_id=tenant.id).first()
    assert mb is not None and mb.role == "admin"

    reports = db.query(m.Report).filter_by(tenant_id=tenant.id).all()
    assert len(reports) == 1
    assert reports[0].kind == "weekly"
    assert pathlib.Path(reports[0].path).exists()

    # ingest_token must never be written anywhere in the tenant row
    assert "super-secret-token-should-never-be-copied" not in json.dumps(tenant.config)
    assert "super-secret-token-should-never-be-copied" not in json.dumps(tenant.settings)

    # --- run again: must be idempotent, no duplicates -----------------
    import_nexus(str(src), {"myron": "home"})

    db2 = SessionLocal()
    tenants = db2.query(m.Tenant).filter_by(slug="home").all()
    assert len(tenants) == 1
    assert db2.query(m.Device).filter_by(tenant_id=tenant.id).count() == db.query(m.Device).filter_by(tenant_id=tenant.id).count()
    assert db2.query(m.Scan).filter_by(tenant_id=tenant.id).count() == 1
    assert db2.query(m.Event).filter_by(tenant_id=tenant.id).count() == 1
    assert db2.query(m.Fix).filter_by(tenant_id=tenant.id).count() == 1
    users = db2.query(m.User).filter_by(username="miro").all()
    assert len(users) == 1
    mbs = db2.query(m.Membership).filter_by(user_id=user.id, tenant_id=tenant.id).all()
    assert len(mbs) == 1
    assert db2.query(m.Report).filter_by(tenant_id=tenant.id).count() == 1


def test_import_nexus_skips_corrupt_jsonl_line_but_imports_the_rest(client, tmp_path):
    settings.data_dir = tmp_path / "gdata3"
    src = _build_src(tmp_path)
    tdir = src / "tenants" / "myron"

    # Append one corrupt (non-JSON) line to the events tail -- must not
    # abort the import; it's skipped and counted instead.
    with open(tdir / "events.jsonl", "a") as f:
        f.write("{not valid json,,,\n")

    summary = import_nexus(str(src), {"myron": "home"})

    assert summary["skipped_lines"] == 1
    assert summary["tenants"] == 1

    db = SessionLocal()
    tenant = db.query(m.Tenant).filter_by(slug="home").first()
    # the one well-formed events line before the corrupt one was still imported
    assert db.query(m.Event).filter_by(tenant_id=tenant.id).count() == 1


def test_import_nexus_unknown_email_gets_invalid_local_and_warns(client, tmp_path, capsys):
    settings.data_dir = tmp_path / "gdata2"
    src = _build_src(tmp_path)

    import_nexus(str(src), {"myron": "home"})

    db = SessionLocal()
    user = db.query(m.User).filter_by(username="miro").first()
    assert user.email == "miro@invalid.local"

    out = capsys.readouterr().out
    assert "miro" in out


def test_relabel_also_corrects_the_previous_rename_not_only_the_oldest(client):
    """A tenant relabelled under the LAST rename must still be correctable.

    `STALE_SITE_LABEL_PREFIXES` used to carry only the Nexus leg. Any tenant
    that migrate had already moved to "Guardian public" was then invisible to
    it for ever -- `_relabelled` returned None and no future deploy could fix
    it -- while the label kept showing on the Overview strip, the Security
    scorecards and the public status page. This is the leg that was missing.
    """
    from guardian_hub.importer import relabel_imported_sites

    db = SessionLocal()
    tenant = m.Tenant(slug="relabel2", name="Relabel2")
    db.add(tenant); db.flush()
    old, new = "Guardian public (safehome.example.com)", "Vault public (safehome.example.com)"
    tenant.config = {"sites": [{"name": old, "url": "https://safehome.example.com/"}]}
    db.add_all([
        m.Site(tenant_id=tenant.id, name=old, url="https://safehome.example.com/", public=True),
        m.Metric(tenant_id=tenant.id, ts="2026-09-10T00:00:00+00:00", name="up", entity=old, value=1.0),
        m.Alert(tenant_id=tenant.id, key=f"site:{old}:down", sev="WARN", msg="down",
                first_seen="x", last_seen="x", status="open"),
    ])
    db.commit()

    relabel_imported_sites(db)
    db.commit()

    assert db.query(m.Site).filter_by(tenant_id=tenant.id).one().name == new
    assert db.query(m.Metric).filter_by(tenant_id=tenant.id).one().entity == new
    assert db.query(m.Alert).filter_by(tenant_id=tenant.id).one().key == f"site:{new}:down"
    assert [s["name"] for s in db.query(m.Tenant).filter_by(id=tenant.id).one().config["sites"]] == [new]
    db.close()


def test_relabel_fixes_the_site_name_in_all_four_places_and_is_idempotent(client):
    """Task 56: the imported label "Nexus public (…)" is a live, client-facing
    string (Overview health strip, Security scorecards, public status page).
    A site's NAME is its key in four places and fixing fewer than all four is
    worse than not renaming at all -- see `relabel_imported_sites`' docstring.
    """
    from guardian_hub.importer import relabel_imported_sites

    db = SessionLocal()
    tenant = m.Tenant(slug="relabel", name="Relabel")
    db.add(tenant)
    db.flush()
    old, new = "Nexus public (safehome.example.com)", "Vault public (safehome.example.com)"
    tenant.config = {"sites": [{"name": old, "url": "https://safehome.example.com/"},
                               {"name": "Untouched", "url": "https://other/"}]}
    db.add_all([
        m.Site(tenant_id=tenant.id, name=old, url="https://safehome.example.com/", public=True),
        m.Metric(tenant_id=tenant.id, ts="2026-09-10T00:00:00+00:00", name="up", entity=old, value=1.0),
        m.Metric(tenant_id=tenant.id, ts="2026-09-10T00:01:00+00:00", name="latency_ms", entity=old, value=42.0),
        m.Metric(tenant_id=tenant.id, ts="2026-09-10T00:01:00+00:00", name="up", entity="Untouched", value=1.0),
        m.Alert(tenant_id=tenant.id, key=f"site:{old}:slow", sev="WARN",
                msg=f"{old} is slow", first_seen="x", last_seen="x", status="open"),
    ])
    db.commit()

    n = relabel_imported_sites(db)
    assert n > 0
    db.expire_all()

    site = db.query(m.Site).filter_by(tenant_id=tenant.id).one()
    assert site.name == new and site.public is True and site.url == "https://safehome.example.com/"
    cfg = db.query(m.Tenant).filter_by(id=tenant.id).one().config
    assert [s["name"] for s in cfg["sites"]] == [new, "Untouched"]
    # History follows the name, or the renamed site's strip and report go blank.
    assert db.query(m.Metric).filter_by(tenant_id=tenant.id, entity=new).count() == 2
    assert db.query(m.Metric).filter_by(tenant_id=tenant.id, entity=old).count() == 0
    assert db.query(m.Metric).filter_by(tenant_id=tenant.id, entity="Untouched").count() == 1
    # The open alert is renamed, not orphaned under a key that can never
    # arrive again (and so could never be resolved).
    al = db.query(m.Alert).filter_by(tenant_id=tenant.id).one()
    assert al.key == f"site:{new}:slow" and al.status == "open"
    assert "Nexus" not in al.msg

    assert relabel_imported_sites(db) == 0     # idempotent


def test_relabel_drops_the_stale_alert_when_the_new_key_already_exists(client):
    """`UniqueConstraint(tenant_id, key)`: a scan may already have created the
    new-name alert row. That one is live; the stale-named one is the duplicate
    and must go, not blow up the relabel."""
    from guardian_hub.importer import relabel_imported_sites

    db = SessionLocal()
    tenant = m.Tenant(slug="relabel2", name="Relabel2")
    db.add(tenant)
    db.flush()
    old, new = "Nexus public (x)", "Vault public (x)"
    db.add_all([
        m.Alert(tenant_id=tenant.id, key=f"site:{old}:slow", sev="WARN", msg="stale",
                first_seen="a", last_seen="a", status="open"),
        m.Alert(tenant_id=tenant.id, key=f"site:{new}:slow", sev="WARN", msg="live",
                first_seen="b", last_seen="b", status="open"),
    ])
    db.commit()

    relabel_imported_sites(db)
    db.expire_all()
    rows = db.query(m.Alert).filter_by(tenant_id=tenant.id).all()
    assert [r.key for r in rows] == [f"site:{new}:slow"]
    assert rows[0].msg == "live"
