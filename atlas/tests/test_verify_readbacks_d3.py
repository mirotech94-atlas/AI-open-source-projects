"""Read-back verifiers for linkedin.reshare_post, linkedin.update_page_identity,
agency.deploy_site, shop.create_payment_link and browser.submit_form (RA-D1).

Each one: verified, contradicted, unknown, and a raising read that becomes
unknown. Providers are faked at `httpx.AsyncClient.send`, and the fake
refuses any method but GET. LinkedIn is faked at the module's read
functions, so no browser is launched. No network, no database.
"""
from __future__ import annotations

import json
import re

import httpx
import pytest

from atlas.kernel import verify as v
from atlas.kernel.contracts import ActionResult

SECRET = "sk-d3-supersecret-0123456789"


class Net:
    """Routes by a regex on the URL; GET only."""

    def __init__(self) -> None:
        self.routes: list[tuple[str, tuple]] = []
        self.seen: list[str] = []

    def on(self, pattern: str, status: int = 200, body=None, *, text=None,
           raises=None) -> None:
        self.routes.append((pattern, (status, body, text, raises)))

    async def send(self, client, request: httpx.Request, **_kw) -> httpx.Response:
        assert request.method == "GET", (
            f"a verifier tried to write: {request.method} {request.url}")
        url = str(request.url)
        self.seen.append(url)
        for pattern, (status, body, text, raises) in self.routes:
            if re.search(pattern, url):
                if raises is not None:
                    raise raises
                if text is not None:
                    return httpx.Response(status, text=text, request=request)
                return httpx.Response(status, json=body, request=request)
        return httpx.Response(404, json={"error": "no route"}, request=request)


@pytest.fixture
def net(monkeypatch):
    fake = Net()

    async def send(client, request, **kw):
        return await fake.send(client, request, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    return fake


@pytest.fixture(autouse=True)
def secret_env(monkeypatch):
    """Redaction masks secrets it can see in the environment."""
    monkeypatch.setenv("VERIFY_TEST_API_KEY", SECRET)


def _no_secret(got: v.Verification) -> None:
    assert SECRET not in json.dumps(got.as_audit(), default=str)


@pytest.mark.parametrize("action", [
    "linkedin.reshare_post", "linkedin.update_page_identity", "agency.deploy_site",
    "shop.create_payment_link", "browser.submit_form", "nightshift.merge"])
def test_each_action_has_a_verifier(action):
    assert v.has_verifier(action)


# ================================================================ linkedin

LI_TEXT = "Cheap flights to Cairo this week, from New York and Boston"
LI_ENTRY = {"id": "B5", "status": "published", "text": LI_TEXT,
            "activity_url": "https://www.linkedin.com/feed/update/urn:li:activity:5",
            "reshared": True, "reshared_at": "2026-09-28T10:00-04:00"}


@pytest.fixture
def li_state(monkeypatch, tmp_path):
    from atlas.modules import linkedin
    path = tmp_path / "state.json"

    def write(entry: dict) -> None:
        path.write_text(json.dumps({"improver": {"post_bank": [entry], "posting": {}}}))

    monkeypatch.setattr(linkedin, "STATE", path)
    return write


def _li(monkeypatch, name: str, value=None, *, raises=None):
    from atlas.modules.linkedin import readback
    calls = []

    async def read(*args):
        calls.append(args)
        if raises is not None:
            raise raises
        return value

    monkeypatch.setattr(readback, name, read)
    return calls


def _no_session_writes(monkeypatch):
    """The read-back must never save the session or the bank."""
    from atlas.modules.linkedin import browser, store

    def boom(*a, **k):
        raise AssertionError("a read-back wrote LinkedIn state")

    monkeypatch.setattr(browser, "keep_session", boom)
    monkeypatch.setattr(store, "save", boom)
    monkeypatch.setattr(store, "log_run", boom)


RS = {"page": "flycheap", "entry_id": "B5"}


async def test_reshare_verified(li_state, monkeypatch):
    li_state(LI_ENTRY)
    _no_session_writes(monkeypatch)
    _li(monkeypatch, "activity_text", "Activity\n" + LI_TEXT + "\n3 reactions")
    got = await v.verify("linkedin.reshare_post", RS, ActionResult(True))
    assert got.state is v.State.VERIFIED, got.reason
    assert got.evidence["on_miros_activity"] is True


async def test_reshare_contradicted(li_state, monkeypatch):
    li_state(LI_ENTRY)
    _li(monkeypatch, "activity_text", "Activity\nsomething else")
    got = await v.verify("linkedin.reshare_post", RS, ActionResult(True))
    assert got.state is v.State.CONTRADICTED
    li_state({**LI_ENTRY, "reshared": "unverified"})
    got = await v.verify("linkedin.reshare_post", RS, ActionResult(True))
    assert got.state is v.State.CONTRADICTED and "reshared" in got.reason


async def test_reshare_unknown_when_signed_out(li_state, monkeypatch):
    from atlas.modules.linkedin.readback import Unreadable
    li_state(LI_ENTRY)
    _li(monkeypatch, "activity_text", raises=Unreadable("signed out", v.FailureClass.AUTH))
    got = await v.verify("linkedin.reshare_post", RS, ActionResult(True))
    assert got.state is v.State.UNKNOWN and got.failure_class is v.FailureClass.AUTH


async def test_reshare_raises_is_unknown(li_state, monkeypatch):
    li_state(LI_ENTRY)
    _li(monkeypatch, "activity_text", raises=RuntimeError(f"crash {SECRET}"))
    got = await v.verify("linkedin.reshare_post", RS, ActionResult(True))
    assert got.state is v.State.UNKNOWN and "raised" in got.reason
    _no_secret(got)


ID = {"page": "guardian", "name": "Vault"}


async def test_identity_verified(monkeypatch):
    _no_session_writes(monkeypatch)
    calls = _li(monkeypatch, "page_name", "Vault")
    got = await v.verify("linkedin.update_page_identity", ID, ActionResult(True))
    assert got.state is v.State.VERIFIED, got.reason
    assert calls == [("guardian",)]


async def test_identity_contradicted(monkeypatch):
    _li(monkeypatch, "page_name", "Guardian")
    got = await v.verify("linkedin.update_page_identity", ID, ActionResult(True))
    assert got.state is v.State.CONTRADICTED and "Guardian" in got.reason


async def test_identity_unknown_for_a_logo_or_a_closed_session(monkeypatch):
    from atlas.modules.linkedin.readback import Unreadable
    _li(monkeypatch, "page_name", "Vault")
    got = await v.verify("linkedin.update_page_identity",
                         {**ID, "logo": "vault.png"}, ActionResult(True))
    assert got.state is v.State.UNKNOWN and "logo" in got.reason
    _li(monkeypatch, "page_name", raises=Unreadable("no session", v.FailureClass.AUTH))
    got = await v.verify("linkedin.update_page_identity", ID, ActionResult(True))
    assert got.state is v.State.UNKNOWN and got.failure_class is v.FailureClass.AUTH


async def test_identity_raises_is_unknown(monkeypatch):
    _li(monkeypatch, "page_name", raises=RuntimeError(f"crash {SECRET}"))
    got = await v.verify("linkedin.update_page_identity", ID, ActionResult(True))
    assert got.state is v.State.UNKNOWN and "raised" in got.reason
    _no_secret(got)


async def test_the_linkedin_reads_share_the_module_lock(monkeypatch):
    """Every read goes through `_read`, which holds `browser.lock()`."""
    from atlas.modules.linkedin import browser, readback
    seen = []

    class Lock:
        async def __aenter__(self):
            seen.append("locked")

        async def __aexit__(self, *a):
            seen.append("released")

    async def no_browser():
        raise browser.NoSession("no saved session")

    monkeypatch.setattr(browser, "lock", lambda: Lock())
    monkeypatch.setattr(browser, "browser", no_browser)
    for read in (readback.activity_text(), readback.page_name("flycheap")):
        with pytest.raises(readback.Unreadable):
            await read
    assert seen == ["locked", "released"] * 2


# ================================================================== agency

SITE = {"slug": "joes", "domain": "joes.example", "path": ""}


@pytest.fixture
def agency(monkeypatch, tmp_path):
    from atlas.modules.agency import _store, deploy
    rel = tmp_path / "releases" / "joes" / "current"
    rel.mkdir(parents=True)
    (rel / "index.html").write_text("<html><head><title>Joe's  Plumbing</title>"
                                    "</head><body>hi</body></html>")
    monkeypatch.setattr(deploy, "RELEASES_DIR", tmp_path / "releases")
    site = dict(SITE)

    async def get_site(slug):
        return dict(site) if slug == "joes" else None

    monkeypatch.setattr(_store, "get_site", get_site)
    return site


PAGE = "<html><head><title>Joe's Plumbing</title></head><body>new</body></html>"
DEPLOYED = ActionResult(True, "deployed", evidence={"slug": "joes", "host": "web1"})


async def test_deploy_verified(net, agency):
    net.on(r"^https://joes\.example/$", text=PAGE)
    got = await v.verify("agency.deploy_site", {"slug": "joes"}, DEPLOYED)
    assert got.state is v.State.VERIFIED, got.reason
    assert got.evidence["title"] == "Joe's Plumbing"


async def test_deploy_contradicted(net, agency):
    net.on(r"^https://joes\.example/$", text="<title>Old site</title>")
    got = await v.verify("agency.deploy_site", {"slug": "joes"}, DEPLOYED)
    assert got.state is v.State.CONTRADICTED and "Old site" in got.reason
    net.routes.clear()
    net.on(r"joes\.example", status=404, text="gone")
    got = await v.verify("agency.deploy_site", {"slug": "joes"}, DEPLOYED)
    assert got.state is v.State.CONTRADICTED and "404" in got.reason


async def test_deploy_unknown(net, agency):
    net.on(r"joes\.example", status=502, text="bad gateway")
    got = await v.verify("agency.deploy_site", {"slug": "joes"}, DEPLOYED)
    assert got.state is v.State.UNKNOWN and got.failure_class is v.FailureClass.TRANSIENT
    agency["domain"] = None
    got = await v.verify("agency.deploy_site", {"slug": "joes"}, DEPLOYED)
    assert got.state is v.State.UNKNOWN and "no domain" in got.reason


async def test_deploy_raises_is_unknown(net, agency, monkeypatch):
    from atlas.modules.agency import _store

    async def boom(slug):
        raise RuntimeError(f"db down {SECRET}")

    monkeypatch.setattr(_store, "get_site", boom)
    got = await v.verify("agency.deploy_site", {"slug": "joes"}, DEPLOYED)
    assert got.state is v.State.UNKNOWN and "raised" in got.reason
    _no_secret(got)


# ==================================================================== shop

LINK = "plink_1Abc"
SHOP_PARAMS = {"product_key": "vault_kit", "account": "miro", "price_cents": 1900}
SHOP_RESULT = ActionResult(True, "on sale", evidence={"payment_link": LINK},
                           rollback_token=f"miro:{LINK}:vault_kit")


@pytest.fixture
def shop(monkeypatch):
    monkeypatch.setenv("STRIPE_SHOP_MIRO_KEY", SECRET)


def _link(**over):
    return {"id": LINK, "active": True, "url": "https://buy.stripe.com/test_1",
            "metadata": {"atlas_product": "vault_kit"},
            "after_completion": {"type": "redirect", "redirect": {
                "url": "https://atlas.example/deliver/{CHECKOUT_SESSION_ID}"}},
            **over}


def _items(amount=1900, qty=1, currency="usd"):
    return {"data": [{"quantity": qty, "price": {"unit_amount": amount,
                                                 "currency": currency}}]}


def _shop_routes(net, link=None, items=None):
    net.on(rf"/v1/payment_links/{LINK}/line_items", body=items or _items())
    net.on(rf"/v1/payment_links/{LINK}$", body=link or _link())


async def test_payment_link_verified(net, shop):
    _shop_routes(net)
    got = await v.verify("shop.create_payment_link", SHOP_PARAMS, SHOP_RESULT)
    assert got.state is v.State.VERIFIED, got.reason
    assert got.evidence["unit_amount"] == 1900
    _no_secret(got)


async def test_payment_link_contradicted(net, shop):
    _shop_routes(net, link=_link(active=False), items=_items(amount=900))
    got = await v.verify("shop.create_payment_link", SHOP_PARAMS, SHOP_RESULT)
    assert got.state is v.State.CONTRADICTED
    assert "inactive" in got.reason and "priced 900" in got.reason
    net.routes.clear()
    net.on(rf"/v1/payment_links/{LINK}", status=404, body={"error": {}})
    got = await v.verify("shop.create_payment_link", SHOP_PARAMS, SHOP_RESULT)
    assert got.state is v.State.CONTRADICTED


async def test_payment_link_unknown(net, shop, monkeypatch):
    net.on(r"/v1/payment_links", status=401, body={"error": {}})
    got = await v.verify("shop.create_payment_link", SHOP_PARAMS, SHOP_RESULT)
    assert got.state is v.State.UNKNOWN and got.failure_class is v.FailureClass.AUTH
    got = await v.verify("shop.create_payment_link", SHOP_PARAMS, ActionResult(True))
    assert got.state is v.State.UNKNOWN and "no payment link" in got.reason
    monkeypatch.delenv("STRIPE_SHOP_MIRO_KEY")
    got = await v.verify("shop.create_payment_link", SHOP_PARAMS, SHOP_RESULT)
    assert got.state is v.State.UNKNOWN and "no shop key" in got.reason


async def test_payment_link_raises_is_unknown(net, shop):
    net.on(r"/v1/payment_links", raises=ValueError(f"bad json {SECRET}"))
    got = await v.verify("shop.create_payment_link", SHOP_PARAMS, SHOP_RESULT)
    assert got.state is v.State.UNKNOWN and "raised" in got.reason
    _no_secret(got)


# ================================================================= browser

FORM = {"url": "https://www.example.org/contact", "fields": "#name=Miro"}


def _submitted(**over):
    ev = {"url": "https://www.example.org/contact", "found": ["#name"], "missing": [],
          "action": "https://www.example.org/send", "method": "post",
          "after": "https://www.example.org/thanks", **over}
    return ActionResult(True, "submitted", ev)


async def test_submit_form_is_never_verified_and_reads_nothing(net):
    """Verified is unreachable by design: there is nothing to read back."""
    got = await v.verify("browser.submit_form", FORM, _submitted())
    assert got.state is v.State.UNKNOWN
    assert "no record ATLAS can read back" in got.reason
    assert got.evidence["after"] == "https://www.example.org/thanks"
    assert net.seen == []


async def test_submit_form_contradicted_by_its_own_record(net):
    got = await v.verify("browser.submit_form", FORM,
                         _submitted(url="https://evil.example/contact"))
    assert got.state is v.State.CONTRADICTED
    assert got.failure_class is v.FailureClass.POLICY
    got = await v.verify("browser.submit_form", FORM, _submitted(missing=["#email"]))
    assert got.state is v.State.CONTRADICTED and "#email" in got.reason
    assert net.seen == []


async def test_submit_form_unknown_without_a_record(net):
    got = await v.verify("browser.submit_form", FORM, ActionResult(True, "submitted"))
    assert got.state is v.State.UNKNOWN and "no page" in got.reason
    got = await v.verify("browser.submit_form", {}, _submitted())
    assert got.state is v.State.UNKNOWN
    assert got.failure_class is v.FailureClass.VALIDATION


async def test_submit_form_raises_is_unknown(monkeypatch):
    from atlas.modules.browser import readback

    def boom(url):
        raise RuntimeError(f"parse {SECRET}")

    monkeypatch.setattr(readback, "_host", boom)
    got = await v.verify("browser.submit_form", FORM, _submitted())
    assert got.state is v.State.UNKNOWN and "raised" in got.reason
    _no_secret(got)
