"""`GET /export/alerts?fmt=` serves the alert log in three formats."""
import io
import zipfile

import pytest

from guardian_hub.db import SessionLocal
from guardian_hub import models as m
from tests.test_auth import login, seed_user


def _alert(tid, key="sec:ufw:vps1", sev="WARN"):
    return m.Alert(tenant_id=tid, key=key, sev=sev, msg="firewall is off", why="",
                   evidence={}, confidence="firm", first_seen="2026-09-20T10:00:00-04:00",
                   last_seen="2026-09-20T18:00:00-04:00", count=2, status="open")


def _seed(client):
    _uid, tid = seed_user()
    login(client)
    db = SessionLocal()
    db.add_all([_alert(tid), _alert(tid, key="sec:ssh:vps1", sev="CRIT")])
    db.commit(); db.close()
    return tid


def test_csv_xlsx_and_pdf_all_carry_the_same_alerts(client):
    _seed(client)
    csv_r = client.get("/api/v1/export/alerts?fmt=csv")
    assert csv_r.status_code == 200
    assert "sec:ufw:vps1" in csv_r.text and "sec:ssh:vps1" in csv_r.text

    xlsx_r = client.get("/api/v1/export/alerts?fmt=xlsx")
    assert xlsx_r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(xlsx_r.content))
    sheet = z.read("xl/worksheets/sheet1.xml").decode()
    assert "sec:ufw:vps1" in sheet and "sec:ssh:vps1" in sheet

    pdf_r = client.get("/api/v1/export/alerts?fmt=pdf")
    assert pdf_r.status_code == 200
    assert "sec:ufw:vps1" in pdf_r.text and "Save as PDF" in pdf_r.text


@pytest.mark.parametrize("fmt,frag", [("csv", ".csv"), ("xlsx", ".xlsx"), ("pdf", ".html")])
def test_each_format_names_its_download(client, fmt, frag):
    _seed(client)
    r = client.get(f"/api/v1/export/alerts?fmt={fmt}")
    assert frag in r.headers["content-disposition"]


def test_pdf_opens_in_the_tab_rather_than_downloading(client):
    # The person presses Print there; an .html in the Downloads folder is a
    # file they have to find and open by hand first.
    _seed(client)
    assert client.get("/api/v1/export/alerts?fmt=pdf").headers["content-disposition"].startswith("inline")
    assert client.get("/api/v1/export/alerts?fmt=csv").headers["content-disposition"].startswith("attachment")


def test_an_unknown_format_is_refused(client):
    _seed(client)
    assert client.get("/api/v1/export/alerts?fmt=docx").status_code == 422


def test_the_original_csv_route_still_works_for_anything_pointed_at_it(client):
    _seed(client)
    r = client.get("/api/v1/export/alerts.csv")
    assert r.status_code == 200 and "sec:ufw:vps1" in r.text


def test_every_export_is_audit_logged_with_its_format(client):
    tid = _seed(client)
    client.get("/api/v1/export/alerts?fmt=xlsx")
    db = SessionLocal()
    rows = db.query(m.Audit).filter_by(tenant_id=tid, action="export.alerts").all()
    bodies = [r.body for r in rows]
    db.close()
    assert any((b or {}).get("fmt") == "xlsx" for b in bodies), \
        "an export is data leaving the hub whichever button produced it"


def test_the_status_filter_still_narrows_the_rows(client):
    tid = _seed(client)
    db = SessionLocal()
    row = db.query(m.Alert).filter_by(tenant_id=tid, key="sec:ssh:vps1").one()
    row.status = "resolved"
    db.commit(); db.close()
    r = client.get("/api/v1/export/alerts?fmt=csv&status=open")
    assert "sec:ufw:vps1" in r.text and "sec:ssh:vps1" not in r.text


def test_the_generic_route_does_not_shadow_the_alert_routes(client):
    """Registration order matters: a catch-all `/export/{dataset}` declared
    before `/export/alerts` would swallow it and 404, because "alerts" is
    deliberately not in the registry (it has filters of its own)."""
    _seed(client)
    assert client.get("/api/v1/export/alerts?fmt=csv").status_code == 200
    assert client.get("/api/v1/export/alerts.csv").status_code == 200


def test_history_activity_devices_and_fixes_all_export(client):
    _seed(client)
    for dataset in ("history", "activity", "devices", "fixes"):
        for fmt in ("csv", "xlsx", "pdf"):
            r = client.get(f"/api/v1/export/{dataset}?fmt={fmt}")
            assert r.status_code == 200, f"{dataset} as {fmt}: {r.status_code}"
            assert r.content, f"{dataset} as {fmt} came back empty"


def test_an_export_nobody_registered_is_a_404_not_an_empty_file(client):
    _seed(client)
    assert client.get("/api/v1/export/passwords?fmt=csv").status_code == 404


def test_a_dataset_export_is_audit_logged_under_its_own_action(client):
    tid = _seed(client)
    client.get("/api/v1/export/devices?fmt=csv")
    db = SessionLocal()
    actions = {r.action for r in db.query(m.Audit).filter_by(tenant_id=tid).all()}
    db.close()
    assert "export.devices" in actions


def test_history_export_carries_real_events(client):
    tid = _seed(client)
    db = SessionLocal()
    db.add(m.Event(tenant_id=tid, ts="2026-09-21T10:00:00-04:00", type="port_open",
                   entity="10.0.0.5", payload={"detail": "telnet reachable"}))
    db.commit(); db.close()
    body = client.get("/api/v1/export/history?fmt=csv").text
    assert "port_open" in body and "telnet reachable" in body


def test_the_report_library_exports_as_a_table(client):
    """A report is a narrative document that prints to PDF; the LIBRARY is
    the tabular thing an auditor asks for."""
    tid = _seed(client)
    import datetime as dt
    db = SessionLocal()
    db.add(m.Report(tenant_id=tid, kind="monthly", period_days=30, preset="executive",
                    sections=["a", "b"], created=dt.datetime.now(dt.timezone.utc), path="/x.html"))
    db.commit(); db.close()
    body = client.get("/api/v1/export/reports?fmt=csv").text
    assert "monthly" in body and "executive" in body
    for fmt in ("xlsx", "pdf"):
        assert client.get(f"/api/v1/export/reports?fmt={fmt}").status_code == 200
