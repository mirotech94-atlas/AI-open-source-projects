from tests.test_seed_helpers import seeded_demo


def test_build_library_share_and_rotate(client):
    seeded_demo(client)
    payload = client.get("/api/v1/reports").json()
    # 11 ordinary (section-subset) presets + `exec_report`, the one special
    # preset outside that contract (`reports/presets.py`'s `SPECIAL_PRESETS`).
    assert len(payload["presets"]) == 14 and len(payload["sections"]) == 20
    card = client.post("/api/v1/reports/build",
                       json={"days": 7, "preset": "security", "sections": [], "title": "Week 37"}).json()
    assert card["id"] and card["preset"] == "security" and card["title"] == "Week 37"
    assert any(c["id"] == card["id"] for c in client.get("/api/v1/reports").json()["library"])
    first = client.post(f"/api/v1/reports/{card['id']}/share", json={"hours": 168}).json()["share_url"]
    second = client.post(f"/api/v1/reports/{card['id']}/share", json={"hours": 168}).json()["share_url"]
    assert first != second
    assert client.post(f"/api/v1/reports/{card['id']}/share", json={"hours": 10_000}).status_code == 422


def test_preview_is_not_persisted(client):
    seeded_demo(client)
    before = len(client.get("/api/v1/reports").json()["library"])
    html = client.get("/api/v1/reports/preview?days=7&preset=executive").text
    assert "Executive summary" in html
    assert len(client.get("/api/v1/reports").json()["library"]) == before


def test_scheduler_is_idempotent_within_a_period(client, monkeypatch):
    tenant_id = seeded_demo(client)
    from guardian_hub import scheduler
    from guardian_hub.db import SessionLocal
    import datetime as dt
    fixed_now = dt.datetime(2026, 9, 7, 8, tzinfo=dt.timezone.utc)  # a Monday
    monkeypatch.setattr(scheduler, "_now", lambda: fixed_now)
    # The hour gate compares against the tenant-LOCAL hour (seeded fixture
    # data carries a -04:00 offset), not the UTC hour of `fixed_now` --
    # compute the expected local hour the same way the scheduler does
    # rather than hard-coding the UTC hour (Task 38 carried fix).
    db = SessionLocal()
    offset = scheduler._tenant_offset(db, tenant_id)
    db.close()
    local_hour = (fixed_now + offset).hour
    client.post("/api/v1/reports/schedule", json={"weekly": True, "monthly": False,
                                                  "preset": "executive", "hour": local_hour, "ntfy": True})
    scheduler.reports_job(); scheduler.reports_job()
    weekly = [c for c in client.get("/api/v1/reports").json()["library"] if c["kind"] == "weekly"]
    assert len(weekly) == 1


def test_share_can_push_the_link_and_does_not_by_default(client, monkeypatch):
    """Task 58 (G5): the predecessor's `/api/report/push`. Sharing a link and
    sending it to the phone are separate intents, so `push` is opt-in and
    reuses the scheduler's own ntfy helper."""
    seeded_demo(client)
    from guardian_hub import scheduler

    pushes: list[str] = []
    monkeypatch.setattr(scheduler, "_push_ntfy",
                        lambda db, tenant, report, token, background=False: pushes.append(token))

    card = client.post("/api/v1/reports/build",
                       json={"days": 7, "preset": "executive", "sections": [], "title": "Wk"}).json()

    quiet = client.post(f"/api/v1/reports/{card['id']}/share", json={"hours": 24}).json()
    assert quiet["pushed"] is False and pushes == []

    loud = client.post(f"/api/v1/reports/{card['id']}/share",
                       json={"hours": 24, "push": True}).json()
    assert loud["pushed"] is True and len(pushes) == 1
    # The token pushed is the one just minted, not a stale earlier link.
    assert loud["share_url"].endswith(pushes[0])
    assert quiet["share_url"] != loud["share_url"]


def test_share_push_is_audited(client, monkeypatch):
    seeded_demo(client)
    from guardian_hub import scheduler
    monkeypatch.setattr(scheduler, "_push_ntfy", lambda *a, **k: None)
    card = client.post("/api/v1/reports/build",
                       json={"days": 7, "preset": "executive", "sections": [], "title": "Wk"}).json()
    client.post(f"/api/v1/reports/{card['id']}/share", json={"hours": 24, "push": True})
    rows = client.get("/api/v1/audit").json()
    shares = [r for r in (rows if isinstance(rows, list) else rows.get("rows", []))
              if r.get("action") == "report.share"]
    assert shares and shares[0]["body"]["push"] is True


def test_share_push_is_handed_off_in_the_background(client, monkeypatch):
    """A request must never block on an HTTP POST to every integration -- the
    same reason `deliver_background` exists (Task 40 fix round 1, finding 2).
    The scheduler still uses the synchronous path; only the route does not."""
    seeded_demo(client)
    from guardian_hub.integrations import registry

    calls: list[str] = []
    monkeypatch.setattr(registry, "deliver_background", lambda tid, msg: calls.append("background"))
    monkeypatch.setattr(registry, "deliver", lambda db, tenant, msg: calls.append("blocking") or [])

    card = client.post("/api/v1/reports/build",
                       json={"days": 7, "preset": "executive", "sections": [], "title": "Wk"}).json()
    client.post(f"/api/v1/reports/{card['id']}/share", json={"hours": 24, "push": True})
    assert calls == ["background"]


def test_building_a_report_rejects_a_parameter_it_does_not_understand(client):
    """A report is a document someone reads and files.

    The list endpoint returns the window as `period_days`, so asking to build
    one with that name is an easy mistake -- and it used to be silently
    ignored, producing a seven-day report while the caller believed they had
    asked for thirty. The reader then has a document that answers a different
    question from the one asked, with nothing saying so.
    """
    seeded_demo(client)
    r = client.post("/api/v1/reports/build",
                    json={"preset": "exec_report", "period_days": 30, "title": "Q3"})
    assert r.status_code == 422, (
        "an unknown parameter was accepted and quietly replaced with a default: "
        f"{r.status_code} {r.text}"
    )

    ok = client.post("/api/v1/reports/build",
                     json={"preset": "exec_report", "days": 30, "title": "Q3"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["period_days"] == 30
