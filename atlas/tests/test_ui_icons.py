"""The app icons and the manifest are real files (2026-10-29).

They were answered by the SPA catch-all with index.html, so an installed
ATLAS Agency showed no icon. Each is an exact route serving the built file's
bytes with its own type; nothing else in the build root is reachable this way."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from atlas.api import ui_paths

FILES = {"apple-touch-icon.png": (b"\x89PNG touch", "image/png"),
         "icon-192.png": (b"\x89PNG 192", "image/png"),
         "icon-512.png": (b"\x89PNG 512", "image/png"),
         "manifest.webmanifest": (b'{"name": "ATLAS Agency"}', "application/manifest+json"),
         "favicon.svg": (b"<svg/>", "image/svg+xml")}


@pytest.fixture
def client(monkeypatch, tmp_path, memory_backend):
    # imported before STATIC moves
    from atlas.api.main import app
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "127.0.0.0/8")
    for name, (body, _type) in FILES.items():
        (tmp_path / name).write_bytes(body)
    (tmp_path / "index.html").write_text("<!doctype html><title>shell</title>")
    (tmp_path / "secret.txt").write_text("not served")
    monkeypatch.setattr(ui_paths, "STATIC", tmp_path)

    async def no_boot() -> None:
        return None
    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    with TestClient(app, client=("127.0.0.1", 52000)) as c:
        yield c


@pytest.mark.parametrize("name", sorted(FILES))
def test_each_icon_is_the_file_not_the_shell(client, name):
    body, ctype = FILES[name]
    r = client.get(f"/{name}")
    assert r.status_code == 200
    assert r.content == body
    assert r.headers["content-type"].split(";")[0] == ctype


def test_a_missing_icon_is_a_404_not_the_shell(client, tmp_path):
    (tmp_path / "icon-512.png").unlink()
    r = client.get("/icon-512.png")
    assert r.status_code == 404 and b"shell" not in r.content


@pytest.mark.parametrize("path", ["/icon-1.png", "/icon-192.png/../secret.txt",
                                  "/%2e%2e/secret.txt", "/secret.txt"])
def test_nothing_else_is_served_as_a_file(client, path):
    r = client.get(path)
    assert b"not served" not in r.content


def test_spa_shell_is_revalidated_on_every_load(tmp_path):
    """index.html names the current build's bundles: never served stale."""
    from atlas.api.main import spa_shell
    index = tmp_path / "index.html"
    index.write_text("<!doctype html><title>shell</title>")
    resp = spa_shell(index)
    assert resp.headers["cache-control"] == "no-cache"


def test_spa_route_uses_the_revalidated_shell():
    """The catch-all answers with spa_shell, not a bare FileResponse."""
    import inspect

    from atlas.api import main
    src = inspect.getsource(main)
    route = src[src.index("async def spa("):]
    route = route[:route.index("else:")]
    assert "return spa_shell(index)" in route
    assert "return FileResponse(index)" not in route
