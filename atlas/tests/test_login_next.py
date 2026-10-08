"""The login's `next` redirect stays on this site (audit #26).

`next` comes from a link anyone can craft; redirecting straight to it made
ATLAS's own login page an open redirect. Only a same-site relative path is
followed -- anything else lands on "/".
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from atlas.api import auth
from atlas.api.views import safe_next

SECRET = "s" * 40
PASSWORD = "a-long-enough-password"

HOSTILE = [
    "//evil.com",
    "//evil.com/path",
    "/\\evil.com",
    "/\\/evil.com",
    "https://evil.com",
    "http://evil.com/",
    "evil.com",
    "javascript:alert(1)",
    "/\t/evil.com",
    "/\n/evil.com",
    "/ /evil.com",
    "",
]
FINE = ["/", "/ok?x=1", "/finding/42", "/brief#today", "/a/b/c"]


@pytest.mark.parametrize("target", HOSTILE)
def test_anything_off_site_becomes_the_root(target):
    assert safe_next(target) == "/"


@pytest.mark.parametrize("target", FINE)
def test_a_same_site_path_is_kept(target):
    assert safe_next(target) == target


def test_none_becomes_the_root():
    assert safe_next(None) == "/"


@pytest.fixture
def client(monkeypatch, memory_backend):
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", auth.hash_password(PASSWORD))
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "127.0.0.0/8")
    monkeypatch.delenv("DASHBOARD_HTTPS", raising=False)
    auth._attempts.clear()
    from atlas.api.main import app

    async def no_boot() -> None:
        return None
    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    with TestClient(app, client=("127.0.0.1", 51000)) as c:
        yield c


def _post_login(client, target: str):
    return client.post("/login", data={"password": PASSWORD, "next": target},
                       follow_redirects=False)


@pytest.mark.parametrize("target", ["//evil.com", "/\\evil.com", "https://evil.com",
                                    "evil.com"])
def test_post_login_never_redirects_off_site(client, target):
    r = _post_login(client, target)
    assert r.status_code == 303
    assert r.headers["location"] == "/"


def test_post_login_follows_a_same_site_path(client):
    r = _post_login(client, "/ok?x=1")
    assert r.status_code == 303
    assert r.headers["location"] == "/ok?x=1"


@pytest.mark.parametrize("target", ["//evil.com", "/\\evil.com", "https://evil.com",
                                    "evil.com"])
def test_get_login_when_signed_in_never_redirects_off_site(client, target):
    assert _post_login(client, "/").status_code == 303        # now signed in
    r = client.get("/login", params={"next": target}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"


def test_get_login_when_signed_in_follows_a_same_site_path(client):
    assert _post_login(client, "/").status_code == 303
    r = client.get("/login", params={"next": "/ok?x=1"}, follow_redirects=False)
    assert r.headers["location"] == "/ok?x=1"


def test_the_login_form_carries_only_the_safe_value(client):
    r = client.get("/login", params={"next": "//evil.com"})
    assert r.status_code == 200
    assert "evil.com" not in r.text
