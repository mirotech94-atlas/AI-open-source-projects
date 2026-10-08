from fastapi.testclient import TestClient

from guardian_hub.config import settings
from guardian_hub.main import create_app


def test_web_static_mount(tmp_path, monkeypatch):
    web_dist = tmp_path / "webdist"
    (web_dist / "static").mkdir(parents=True)
    (web_dist / "index.html").write_text("<html><body>guardian web</body></html>")
    (web_dist / "static" / "x.js").write_text("console.log('x')")

    monkeypatch.setattr(settings, "web_dist", web_dist)

    with TestClient(create_app()) as c:
        r = c.get("/")
        assert r.status_code == 200
        assert "guardian web" in r.text

        r = c.get("/static/x.js")
        assert r.status_code == 200
        assert "console.log" in r.text

        r = c.get("/login")
        assert r.status_code == 200, "SPA fallback should serve index.html for client routes"
        assert "guardian web" in r.text

        r = c.get("/api/v1/nope")
        assert r.status_code == 404
        assert r.headers["content-type"].startswith("application/json")


def test_web_static_mount_blocks_path_traversal(tmp_path, monkeypatch):
    """A path that tries to escape web_dist (via the SPA catch-all's own
    file lookup, not the /static StaticFiles mount) must never leak a file
    from outside web_dist — it should fall back to the SPA index.html (or
    404), never return the outside file's contents.

    Note: an httpx/requests client (what TestClient uses) collapses plain
    "/../" dot-segments in the URL *before the request is even sent* — so a
    literal `GET /../../etc/passwd` never actually reaches the server with
    ".." in its path; it arrives as `GET /etc/passwd`. That still must not
    escape web_dist (covered below), but it doesn't exercise the server-side
    `relative_to()` guard in `main.py`'s `spa()`. The percent-encoded forms
    (`%2e%2e`) are NOT collapsed client-side, decode to literal ".." once
    Starlette resolves the `{path:path}` route param, and are what actually
    proves the guard: deleting the `relative_to()` check makes the
    `%2e%2e` case below leak the planted secret file's contents (verified
    manually while writing this test), while the guard in place always
    falls back to index.html/404 instead.
    """
    web_dist = tmp_path / "webdist"
    (web_dist / "static").mkdir(parents=True)
    (web_dist / "index.html").write_text("<html><body>guardian web</body></html>")
    (web_dist / "static" / "x.js").write_text("console.log('x')")

    # A "secret" file living just outside web_dist (its parent dir) — this
    # must never be returned by any request below.
    secret = tmp_path / "secret.txt"
    secret.write_text("outside web_dist -- must never be served")

    monkeypatch.setattr(settings, "web_dist", web_dist)

    with TestClient(create_app()) as c:
        # Literal "/../../etc/passwd"-style traversal. The HTTP client
        # normalizes this to "/etc/passwd" before sending, but the result
        # must still never be real /etc/passwd content — only 404 or the
        # SPA's own index.html.
        r = c.get("/../../etc/passwd")
        assert r.status_code in (200, 404)
        assert "root:" not in r.text

        # Literal "/static/../index.html"-style traversal, normalized by
        # the client to "/index.html" — must resolve to the SPA's own
        # index.html (which happens to be the right answer here), never
        # anything from outside web_dist.
        r = c.get("/static/../index.html")
        assert r.status_code == 200
        assert "guardian web" in r.text

        # Percent-encoded dot-segments are NOT normalized by the client, so
        # these actually reach the server with ".." in the decoded path —
        # the real test of the `relative_to()` guard in `spa()`.
        for path in (
            f"/%2e%2e/{secret.name}",
            f"/static/%2e%2e/{secret.name}",
            f"/%2e%2e%2f{secret.name}",
        ):
            r = c.get(path)
            assert r.status_code in (200, 404), path
            assert "must never be served" not in r.text, path

        # A non-traversal SPA route still resolves normally.
        r = c.get("/some/client/route")
        assert r.status_code == 200
        assert "guardian web" in r.text


def test_web_static_mount_skipped_when_no_web_dist(monkeypatch):
    # With web_dist explicitly unset, the SPA catch-all is never registered:
    # an unknown non-API path 404s like any other undeclared route, rather
    # than falling back to an index.html that doesn't exist.
    monkeypatch.setattr(settings, "web_dist", None)
    with TestClient(create_app()) as c:
        r = c.get("/some-unknown-path")
        assert r.status_code == 404
