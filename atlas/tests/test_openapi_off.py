"""The API schema and interactive docs are not served in production (audit #8).

`/openapi.json` lists every route and parameter the mesh surface has. Nothing
in ATLAS reads it, so it is off unless `ATLAS_OPENAPI=on` asks for it.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas.api import main

SECRET = "s" * 40


@pytest.fixture
def mesh_client(monkeypatch, memory_backend):
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "127.0.0.0/8")

    async def no_boot() -> None:
        return None
    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    # A real mesh source, so a 404 below is the route being absent and not
    # the mesh guard refusing the caller.
    with TestClient(main.app, client=("127.0.0.1", 51000)) as c:
        yield c


def test_the_production_app_serves_no_schema_or_docs():
    assert main.app.openapi_url is None
    assert main.app.docs_url is None
    assert main.app.redoc_url is None


@pytest.mark.parametrize("path", ["/openapi.json", "/docs", "/redoc"])
def test_schema_and_docs_are_404_from_the_mesh(mesh_client, path):
    # The same client reaches a mesh-only route, so the guard is letting it in.
    assert mesh_client.get("/api/session").status_code == 200
    r = mesh_client.get(path)
    body = r.text.lower()
    assert '"openapi"' not in body
    assert "swagger-ui" not in body and "redoc.standalone" not in body
    if r.status_code != 404:
        # With a built UI the SPA catch-all answers unknown paths with
        # index.html; that is fine, the schema and docs pages are not.
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")


@pytest.mark.parametrize("value", ["", "off", "false", "0", "nonsense"])
def test_docs_stay_off_unless_explicitly_on(monkeypatch, value):
    monkeypatch.setenv("ATLAS_OPENAPI", value)
    assert main._docs_kwargs() == {
        "openapi_url": None, "docs_url": None, "redoc_url": None}


@pytest.mark.parametrize("value", ["on", "ON", "true", "1", "yes"])
def test_atlas_openapi_on_turns_them_back_on(monkeypatch, value):
    monkeypatch.setenv("ATLAS_OPENAPI", value)
    dev = FastAPI(**main._docs_kwargs())
    with TestClient(dev) as c:
        assert c.get("/openapi.json").status_code == 200
        assert c.get("/docs").status_code == 200
        assert c.get("/redoc").status_code == 200
