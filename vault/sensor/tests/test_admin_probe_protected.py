"""An admin path that redirects away, or answers with a login form, is a
PROTECTED panel, not an exposed one.

The owner's report: "FlyCheap: /admin is reachable from the internet" for a
site whose /admin answers 307 -> https://admin.flycheap.live/ (a login page).
`urlopen` followed the redirect, saw a 200 that differed from the catch-all
baseline, and called it a hit. Only a panel that renders WITHOUT
authentication (or a leaked file matching its signature) is a finding.
"""
from guardian_sensor import sites


def _fetcher(table):
    def fetch(url):
        for suffix, reply in table.items():
            if url.endswith(suffix):
                return reply
        return (404, "", "")
    return fetch


SITE = [{"name": "FlyCheap", "url": "https://flycheap.example.com/"}]


def test_a_redirect_is_not_a_hit(monkeypatch):
    monkeypatch.setattr(sites, "_fetch", _fetcher({"/admin": (307, "", ""), "/console": (404, "", "")}))
    out = sites.admin_probe(SITE, {})
    assert out["FlyCheap"]["hits"] == {}


def test_a_login_form_is_not_a_hit(monkeypatch):
    login = '<html><form method="post"><input name="email"><input type="password" name="pw"></form></html>'
    monkeypatch.setattr(sites, "_fetch", _fetcher({"/admin": (200, "text/html", login)}))
    out = sites.admin_probe(SITE, {})
    assert out["FlyCheap"]["hits"] == {}


def test_an_open_panel_is_still_a_hit(monkeypatch):
    page = "<html><h1>Admin</h1><a href='/admin/users'>Users</a><a href='/admin/settings'>Settings</a></html>"
    monkeypatch.setattr(sites, "_fetch", _fetcher({"/admin": (200, "text/html", page)}))
    out = sites.admin_probe(SITE, {})
    assert out["FlyCheap"]["hits"] == {"/admin": "INFO"}


def test_a_leaked_file_is_still_a_hit_even_if_it_redirected_elsewhere_first(monkeypatch):
    monkeypatch.setattr(sites, "_fetch", _fetcher({"/.git/config": (200, "text/plain", "[core]\n\trepositoryformatversion = 0")}))
    out = sites.admin_probe(SITE, {})
    assert out["FlyCheap"]["hits"] == {"/.git/config": "CRIT"}
