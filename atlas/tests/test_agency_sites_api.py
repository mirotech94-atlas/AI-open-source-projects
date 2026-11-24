"""`/api/agency/sites`: the Client Sites tab's reads, and the brief line.

Behind the sign-in; every row carries its kind, hostname, live release and
the newest `site_<slug>` reading with the time it was read -- or UNKNOWN
with the reason. An unreadable registry is a 503, never an empty list.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from atlas.api import auth
from atlas.modules.agency import AgencyModule, _store, demo_host, hosting, sites_view

SECRET = "s" * 40
PASSWORD = "a-long-enough-password"
READ = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)


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
    with TestClient(app, client=("127.0.0.1", 51012)) as test_client:
        yield test_client


def _login(client) -> None:
    assert client.post("/api/login", json={"password": PASSWORD}).status_code == 200


@pytest.fixture
def registry(monkeypatch, tmp_path):
    monkeypatch.setattr(demo_host, "PUBLISH_ROOT", tmp_path / "demos")
    monkeypatch.setattr(hosting, "PUBLISH_ROOT", tmp_path / "sites")
    monkeypatch.delenv("AGENCY_DEPLOY_HOST", raising=False)
    release = tmp_path / "demos" / ".releases" / "examplesalon" / "20261027T090000000000Z"
    release.mkdir(parents=True)
    (tmp_path / "demos" / "examplesalon").symlink_to(
        ".releases/examplesalon/20261027T090000000000Z")

    async def rows():
        return [
            {"slug": "examplesalon", "kind": "demo", "domain": "examplesalon.storefront.test",
             "deployed_at": READ, "source": "royal@176c272", "managed": True},
            {"slug": "joes-7", "kind": "built", "domain": None, "deployed_at": None,
             "source": None, "managed": True},
        ]

    async def health(slug):
        if slug == "examplesalon":
            return {"at": READ, "health": "ok", "detail": "up, 80 days left"}
        return None

    monkeypatch.setattr(_store, "list_sites", rows)
    monkeypatch.setattr(_store, "site_health", health)


def test_the_reads_need_a_session(client):
    assert client.get("/api/agency/sites").status_code == 401
    assert client.get("/api/agency/sites/examplesalon/events").status_code == 401


def test_the_sites_list(client, registry):
    _login(client)
    body = client.get("/api/agency/sites").json()
    assert datetime.fromisoformat(body["read_at"]).tzinfo is not None
    assert body["check_every_s"] == 1800
    by = {s["slug"]: s for s in body["sites"]}
    royal = by["examplesalon"]
    assert royal["kind"] == "demo" and royal["hostname"] == "examplesalon.storefront.test"
    assert royal["live_release"] == "20261027T090000000000Z"
    assert royal["health"] == "ok" and royal["health_read_at"] == READ.isoformat()
    joe = by["joes-7"]
    assert joe["health"] == "unknown" and joe["health_read_at"] is None
    assert joe["health_detail"]          # the reason is named
    assert joe["live_release"] is None


def test_an_unreadable_registry_is_a_503(client, monkeypatch):
    async def down():
        raise ConnectionRefusedError("db down")
    monkeypatch.setattr(_store, "list_sites", down)
    _login(client)
    response = client.get("/api/agency/sites")
    assert response.status_code == 503 and "db down" in response.json()["detail"]


def test_an_unreadable_health_reading_is_unknown_not_ok(client, registry, monkeypatch):
    async def broken(_slug):
        raise TimeoutError("slow")
    monkeypatch.setattr(_store, "site_health", broken)
    _login(client)
    sites = client.get("/api/agency/sites").json()["sites"]
    assert {s["health"] for s in sites} == {"unknown"}


def test_the_events(client, monkeypatch):
    seen: list = []

    async def events(slug, limit=20):
        seen.append((slug, limit))
        return [{"at": READ, "event": "deployed", "release": "r", "detail": "intake ab"},
                {"at": READ - timedelta(hours=1), "event": "deploy_failed",
                 "release": None, "detail": "audit refused"}]
    monkeypatch.setattr(_store, "site_events", events)
    _login(client)
    body = client.get("/api/agency/sites/examplesalon/events").json()
    assert seen == [("examplesalon", 50)]
    assert [e["event"] for e in body["events"]] == ["deployed", "deploy_failed"]
    assert body["events"][0]["at"] == READ.isoformat() and body["read_at"]


def test_events_refuse_a_bad_slug(client):
    _login(client)
    assert client.get("/api/agency/sites/..%2Fetc/events").status_code in (404, 422)
    assert client.get("/api/agency/sites/Bad_Slug/events").status_code == 422


def test_unreadable_events_are_a_503(client, monkeypatch):
    async def down(*_a, **_k):
        raise ConnectionRefusedError("db down")
    monkeypatch.setattr(_store, "site_events", down)
    _login(client)
    assert client.get("/api/agency/sites/examplesalon/events").status_code == 503


# -------------------------------------------------------------------- brief

def test_the_brief_before_any_read():
    sites_view.reset()
    assert "client sites: not read yet" in AgencyModule().brief()


async def test_the_brief_carries_each_site_with_its_read_time(registry):
    sites_view.reset()
    await sites_view.refresh()
    brief = AgencyModule().brief()
    assert brief.startswith("Agency: prospecting")
    line = next(x for x in brief.splitlines() if "examplesalon.storefront.test" in x)
    assert "ok" in line and "09:00" in line and "20261027T090000000000Z" in line
    joe = next(x for x in brief.splitlines() if "joes-7" in x)
    assert "UNKNOWN" in joe


async def test_the_brief_says_when_the_registry_is_unreadable(monkeypatch):
    sites_view.reset()

    async def down():
        raise ConnectionRefusedError("db down")
    monkeypatch.setattr(_store, "list_sites", down)
    await sites_view.refresh()
    assert "client sites: registry unreadable" in AgencyModule().brief()
