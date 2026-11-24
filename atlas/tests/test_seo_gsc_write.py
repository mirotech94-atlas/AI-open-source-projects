"""Search Console registration, Tier 2 (2026-11-17).

Adding a property, verifying it and submitting a sitemap are outside-world
writes: each is a catalogued Tier 2 action with a dry run and a rollback, and
each refuses, naming what to consent, while the Google token lacks the write
scopes. Nothing here touches DNS (Tier 3) and no test reaches the network."""
from __future__ import annotations

from typing import Any

import pytest

from atlas.kernel.contracts import Tier
from atlas.modules import _http
from atlas.modules.seo import MODULE, gsc, gsc_write

SITE = "https://tutoring.example/"
TAG = '<meta name="google-site-verification" content="abc123" />'
TOKEN_URL_PART = "siteVerification/v1/token"


class Google:
    def __init__(self, scope: str, *, page: str = f"<head>{TAG}</head>",
                 sites: list[dict[str, Any]] | None = None) -> None:
        self.scope = scope
        self.page = page
        self.sites = sites if sites is not None else []
        self.sent: list[tuple[str, str]] = []

    async def post(self, url, **kw):
        if "oauth2" in url:
            return {"access_token": "tok", "expires_in": 3600, "scope": self.scope}, None
        if TOKEN_URL_PART in url:
            return {"method": "META", "token": TAG}, None
        self.sent.append(("POST", url))
        return {"id": "https%3A%2F%2Fmeetmytutor.com%2F"}, None

    async def get(self, url, **kw):
        if url.endswith("/sites"):
            return {"siteEntry": self.sites}, None
        return {"sitemap": []}, None

    async def send(self, method, url, **kw):
        self.sent.append((method, url))
        return None, None

    async def page_text(self, url, **kw):
        return self.page, None


@pytest.fixture
def google(monkeypatch):
    def make(scope: str = gsc.SCOPE, **kw) -> Google:
        g = Google(scope, **kw)
        monkeypatch.setattr(_http, "post_json", g.post)
        monkeypatch.setattr(_http, "get_json", g.get)
        monkeypatch.setattr(_http, "send_body", g.send)
        monkeypatch.setattr(gsc_write, "_page", g.page_text)
        return g
    for key in gsc.ENV:
        monkeypatch.setenv(key, "x")
    monkeypatch.setattr(gsc, "_token", None)
    monkeypatch.setattr(gsc, "_scopes", set())
    return make


WRITE = f"{gsc.SCOPE} {gsc_write.SCOPE_SITES} {gsc_write.SCOPE_VERIFY}"


def actions() -> dict[str, Any]:
    return {a.name: a for a in MODULE.actions()}


def test_the_three_actions_are_tier_2_with_a_dry_run_and_a_rollback():
    acts = actions()
    for name in ("seo.gsc_add_site", "seo.gsc_verify_site", "seo.gsc_submit_sitemap"):
        a = acts[name]
        assert a.tier is Tier.ASK
        assert a.dry_run is not None and a.rollback is not None
    assert not any("dns" in n for n in acts)


@pytest.mark.parametrize("fn", ["add_site_dry", "verify_site_dry"])
async def test_a_read_only_consent_refuses_and_says_what_to_consent(google, fn):
    google(gsc.SCOPE)
    res = await getattr(gsc_write, fn)(SITE)
    assert not res.ok
    assert "ATLAS_GOOGLE_SEARCH_CONSOLE_WRITE=1" in res.detail
    assert "--reconsent" in res.detail


async def test_a_vercel_preview_is_never_registered(google):
    google(WRITE)
    res = await gsc_write.add_site_dry("https://demo-site.vercel.example/")
    assert not res.ok and "preview" in res.detail


async def test_only_an_https_url_on_a_public_host_is_accepted(google):
    google(WRITE)
    for bad in ("http://tutoring.example/", "https://localhost/", "https://10.0.0.1/",
                "tutoring.example", "https://tutoring.example/a/b"):
        res = await gsc_write.add_site_dry(bad)
        assert not res.ok, bad


async def test_add_site_dry_run_writes_nothing_and_run_puts_then_rolls_back(google):
    g = google(WRITE)
    dry = await gsc_write.add_site_dry(SITE)
    assert dry.ok and "would add" in dry.detail and g.sent == []
    res = await gsc_write.add_site(SITE)
    assert res.ok and res.rollback_token == SITE
    assert g.sent == [("PUT", f"{gsc.API}/sites/https%3A%2F%2Fmeetmytutor.com%2F")]
    back = await gsc_write.remove_site(res.rollback_token)
    assert back.ok and g.sent[-1][0] == "DELETE"


async def test_an_already_added_property_is_a_no_op_not_a_second_write(google):
    g = google(WRITE, sites=[{"siteUrl": SITE, "permissionLevel": "siteOwner"}])
    res = await gsc_write.add_site(SITE)
    assert res.ok and "already" in res.detail and g.sent == []
    assert res.rollback_token is None


async def test_verify_refuses_until_the_tag_is_live_on_the_page(google):
    g = google(WRITE, page="<head></head>")
    dry = await gsc_write.verify_site_dry(SITE)
    assert not dry.ok and "not on the page" in dry.detail
    assert TAG in dry.detail               # the exact tag to put in the site head
    res = await gsc_write.verify_site(SITE)
    assert not res.ok and g.sent == []


async def test_verify_goes_ahead_when_the_tag_is_live(google):
    g = google(WRITE)
    dry = await gsc_write.verify_site_dry(SITE)
    assert dry.ok and "is live" in dry.detail
    res = await gsc_write.verify_site(SITE)
    assert res.ok and g.sent and g.sent[0][0] == "POST"


async def test_sitemap_must_sit_on_the_same_host(google):
    google(WRITE)
    res = await gsc_write.submit_sitemap_dry(SITE, "https://other.example/sitemap.xml")
    assert not res.ok and "same host" in res.detail


async def test_sitemap_submit_puts_and_rolls_back(google):
    g = google(WRITE, page='<?xml version="1.0"?><urlset></urlset>')
    res = await gsc_write.submit_sitemap(SITE, "https://tutoring.example/sitemap.xml")
    assert res.ok
    assert g.sent[0][0] == "PUT" and g.sent[0][1].endswith(
        "/sitemaps/https%3A%2F%2Fmeetmytutor.com%2Fsitemap.xml")
    back = await gsc_write.remove_sitemap(res.rollback_token)
    assert back.ok and g.sent[-1][0] == "DELETE"


def test_the_write_scopes_are_opt_in_in_the_consent_script():
    import importlib.util
    import pathlib
    path = pathlib.Path(__file__).resolve().parents[1] / "bootstrap/google_login.py"
    spec = importlib.util.spec_from_file_location("google_login_t", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.WRITE_SCOPES == (gsc_write.SCOPE_SITES, gsc_write.SCOPE_VERIFY)


# ---------------------------------------------------------- raising the cards

@pytest.fixture
def cards(monkeypatch):
    from atlas.kernel import approvals
    raised: list[tuple[str, dict, str | None]] = []

    async def fake(action, params, reason, **kw):
        raised.append((action, params, kw.get("dedup_key")))
        return len(raised)
    monkeypatch.setattr(approvals, "propose", fake)
    return raised


@pytest.mark.asyncio
async def test_missing_properties_raise_add_cards_for_our_own_sites_only(google, cards):
    google()
    out = await gsc_write.raise_cards("agency.example.com")
    assert [(a, p["site_url"]) for a, p, _ in cards] == [
        ("seo.gsc_add_site", "https://agency.example.com/"),
        ("seo.gsc_add_site", "https://tutoring.example/")]
    assert all(k.startswith("seo.gsc_add_site:") for _, _, k in cards)
    assert set(out) == {"agency.example.com", "tutoring.example"}


@pytest.mark.asyncio
async def test_an_unverified_property_raises_the_verify_card(google, cards):
    google(sites=[{"siteUrl": SITE, "permissionLevel": "siteUnverifiedUser"}])
    await gsc_write.raise_cards("")
    assert [a for a, _, _ in cards] == ["seo.gsc_verify_site"]


@pytest.mark.asyncio
async def test_a_verified_property_without_a_sitemap_raises_the_sitemap_card(google, cards):
    google(sites=[{"siteUrl": SITE, "permissionLevel": "siteOwner"}])
    await gsc_write.raise_cards("")
    assert cards[0][0] == "seo.gsc_submit_sitemap"
    assert cards[0][1]["sitemap_url"] == "https://tutoring.example/sitemap.xml"


@pytest.mark.asyncio
async def test_unreadable_google_raises_nothing(monkeypatch, cards):
    monkeypatch.setattr(gsc, "_token", None)
    for key in gsc.ENV:
        monkeypatch.delenv(key, raising=False)
    out = await gsc_write.raise_cards("agency.example.com")
    assert cards == [] and "not raised" in out["cards"]


def test_previews_and_client_domains_are_never_ours():
    assert gsc_write.own_hosts("x.vercel.app") == ["tutoring.example"]
