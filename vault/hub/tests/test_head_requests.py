"""HEAD is what an uptime monitor sends.

FastAPI's `APIRoute` does not add HEAD alongside GET the way Starlette's plain
`Route` does, so every HEAD request to this app answered 405 -- including the
health endpoint and the dashboard's own root. Any external HEAD-based monitor
pointed at Vault would have reported it down while it was serving every GET
fine.

A HEAD response carries the headers of the GET and no body; the ASGI server
drops the body itself, so what these tests assert is the status and the
content type, which is all a monitor reads.
"""


def test_head_on_the_health_endpoint_is_not_405(client):
    r = client.head("/api/v1/health")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/json")


def test_head_on_the_dashboard_root_is_not_405(client):
    r = client.head("/")
    assert r.status_code in (200, 404), r.text
    assert r.status_code != 405


def test_head_on_the_api_reference_is_not_405(client):
    r = client.head("/v1/docs")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/html")


def test_the_spa_document_is_never_served_from_cache_without_revalidating(client):
    """index.html names the content-hashed bundle to load.

    A browser holding a cached copy of it keeps running yesterday's app --
    silently, for as long as its heuristic cache lasts, because FileResponse
    sends an ETag and no Cache-Control at all. That is not theoretical: a
    deployed fix was invisible in a real browser tab for exactly this reason.

    `no-store`, not `no-cache`: revalidation is skipped by back/forward
    navigation and restored tabs, which was the second time the same class of
    bug shipped -- a redesigned console, deployed, and still the old page when
    reached by the back button.
    """
    r = client.get("/")
    if r.status_code == 404:
        return  # no built web/dist in this checkout; nothing to serve
    assert r.headers.get("cache-control") == "no-store"
