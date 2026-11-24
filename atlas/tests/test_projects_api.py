"""`/api/projects`, `/api/projects/{id}/tasks`, `/api/remediation`: signed-in
GETs only; the board is written only through `projects.*` actions."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from atlas.api import auth
from atlas.modules.projects import store

SECRET = "s" * 40
PASSWORD = "a-long-enough-password"
PATHS = ("/api/projects", "/api/projects/1/tasks", "/api/remediation")


@pytest.fixture
def client(monkeypatch, memory_backend):
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", auth.hash_password(PASSWORD))
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "127.0.0.1/32")
    auth._attempts.clear()
    from atlas.api.main import app

    async def no_boot() -> None:
        return None

    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    with TestClient(app, client=("127.0.0.1", 51013)) as test_client:
        yield test_client


def _login(client):
    assert client.post("/api/login", json={"password": PASSWORD}).status_code == 200


def test_every_read_needs_a_session(client):
    for path in PATHS:
        assert client.get(path).status_code == 401, path


def test_only_get_routes_are_registered():
    from atlas.api.projects import router
    for route in router.routes:
        assert route.methods == {"GET"}, route.path


def test_remediation_lists_open_first_with_what_to_fix(client, monkeypatch):
    t0 = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
    rows = [
        {"id": 2, "project_id": None, "title": "Broken booking link", "kind": "remediation",
         "owner": "qa", "status": "todo", "evidence": {"url": "https://x.com/book"},
         "suggested_fix": "point it at /book", "source_action": "qa.check_site",
         "source_ref": "joes", "due_at": None, "created_at": t0, "done_at": None},
        {"id": 1, "project_id": None, "title": "Old one", "kind": "remediation",
         "owner": "qa", "status": "done", "evidence": {}, "suggested_fix": "",
         "source_action": "qa.verify_runs", "source_ref": "brief", "due_at": None,
         "created_at": t0, "done_at": t0},
    ]

    async def remediation(limit=200):
        return rows
    monkeypatch.setattr(store, "remediation", remediation)
    _login(client)
    body = client.get("/api/remediation").json()
    assert body["open"] == 1 and body["served_at"]
    first = body["tasks"][0]
    assert first["open"] is True and first["owner"] == "qa"
    assert first["evidence"] == {"url": "https://x.com/book"}
    assert first["suggested_fix"] == "point it at /book" and first["created_at"]
    assert body["tasks"][1]["open"] is False


def test_an_unreadable_board_is_a_503_not_an_empty_list(client, monkeypatch):
    async def broken(*a, **kw):
        raise ConnectionRefusedError("db down")
    monkeypatch.setattr(store, "projects", broken)
    monkeypatch.setattr(store, "remediation", broken)
    _login(client)
    assert client.get("/api/projects").status_code == 503
    assert client.get("/api/remediation").status_code == 503


def test_a_missing_project_is_a_404(client, monkeypatch):
    async def none(_pid):
        return None
    monkeypatch.setattr(store, "project", none)
    _login(client)
    assert client.get("/api/projects/99/tasks").status_code == 404
