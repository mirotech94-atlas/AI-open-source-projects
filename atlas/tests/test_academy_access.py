"""ATLAS Academy delivery (atlas/api/academy.py, modules/academy).

The buyer's page asks Stripe on every visit and hands over files only for a
checkout that is paid, not refunded, not disputed, and bought a catalog
price. When Stripe can't be asked -- no key, no network, a refused key --
the page says so and grants nothing. Download links are signed, expire, and
name a catalog file key, never a path; the file endpoint asks Stripe again.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import html
import json
import pathlib
import re
import time
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import httpx
import pytest
from fastapi.testclient import TestClient

from atlas.api import academy as api
from atlas.modules.academy import catalog as cat_mod
from atlas.modules.academy import delivery, stripe_read

SID = "cs_test_a1B2c3D4e5F6g7H8i9J0"
PRICE_KIT = "price_KIT123456"
PRICE_BUNDLE = "price_ALL123456"


def _catalog(**changes) -> cat_mod.Catalog:
    raw = json.loads(cat_mod.CATALOG_PATH.read_text())
    raw = copy.deepcopy(raw)
    for p in raw["products"]:
        for o in p["offers"]:
            if o["id"] == "claude-obsidian-vault-kit":
                o["stripe_price_id"] = PRICE_KIT
    raw["bundle"]["stripe_price_id"] = PRICE_BUNDLE
    raw["store"].update(changes)
    return cat_mod.parse(raw)


def _session(*, paid=True, price=PRICE_KIT, refunded=False, amount_refunded=0,
             disputed=False, pi_status="succeeded", created=None) -> dict:
    return {
        "id": SID, "object": "checkout.session", "livemode": False,
        "payment_status": "paid" if paid else "unpaid",
        "amount_total": 1900, "currency": "usd",
        "created": created or int(time.time()) - 60,
        "customer_details": {"email": "buyer@example.com"},
        "line_items": {"object": "list", "has_more": False,
                       "data": [{"price": {"id": price}, "quantity": 1}]},
        "payment_intent": {"id": "pi_123", "status": pi_status,
                           "latest_charge": {"id": "ch_1", "refunded": refunded,
                                             "amount_refunded": amount_refunded,
                                             "disputed": disputed}},
    }


# ------------------------------------------------------------------ the verdict

@pytest.mark.parametrize("session,status", [
    (_session(), "paid"),
    (_session(paid=False), "unpaid"),
    (_session(refunded=True, amount_refunded=1900), "refunded"),
    (_session(disputed=True), "disputed"),
    (_session(price="price_NOTOURS999"), "unknown_price"),
    (_session(pi_status="processing"), "unpaid"),
])
def test_the_verdict_is_what_stripe_said(session, status):
    v = stripe_read.judge(session, _catalog())
    assert v.status == status
    assert v.paid is (status == "paid")


def test_a_partial_refund_keeps_access_and_is_recorded_as_such():
    v = stripe_read.judge(_session(amount_refunded=500), _catalog())
    assert v.paid and v.refunded_minor == 500


def test_the_bundle_grants_every_file_of_every_title():
    v = stripe_read.judge(_session(price=PRICE_BUNDLE), _catalog())
    assert v.paid
    keys = v.file_keys()
    videos = {k for k in keys if k.startswith("operator-video-")}
    assert len(videos) == 28
    assert keys - videos >= {"operator-pdf", "operator-starter", "lessons-pdf",
                             "vault-kit-zip", "aiea-adults-zip", "aiea-60plus-pdf"}


def test_an_access_window_when_set_expires_old_purchases():
    old = int((datetime.now(UTC) - timedelta(days=40)).timestamp())
    v = stripe_read.judge(_session(created=old), _catalog(access_valid_days=30))
    assert v.status == "expired" and not v.paid
    # null (the default): no window.
    assert stripe_read.judge(_session(created=old), _catalog()).paid


class _Resp:
    def __init__(self, status: int, body: dict):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


@pytest.fixture
def stripe(monkeypatch):
    """Fake Stripe: set .answer to a (status, body) or an exception."""
    class S:
        answer: object = (200, _session())
        calls: ClassVar[list] = []
    monkeypatch.setenv(stripe_read.KEY_ENV, "rk_test_SECRETKEY123")

    async def get(self, url, params=None, headers=None):
        S.calls.append((url, params, headers))
        if isinstance(S.answer, Exception):
            raise S.answer
        status, body = S.answer
        return _Resp(status, body)
    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    S.calls = []
    return S


async def test_it_reads_the_session_with_line_items_and_the_charge(stripe):
    v = await stripe_read.read_session(SID, _catalog())
    assert v.paid
    url, params, headers = stripe.calls[0]
    assert url.endswith(f"/checkout/sessions/{SID}")
    assert ("expand[]", "line_items") in params
    assert ("expand[]", "payment_intent.latest_charge") in params
    assert headers["Authorization"] == "Bearer rk_test_SECRETKEY123"


async def test_no_key_means_nothing_is_asked_and_nothing_is_granted(stripe, monkeypatch):
    monkeypatch.delenv(stripe_read.KEY_ENV)
    v = await stripe_read.read_session(SID, _catalog())
    assert v.status == "unconfigured" and not v.paid and not stripe.calls


async def test_stripe_down_is_said_never_assumed_paid(stripe):
    stripe.answer = httpx.ConnectError("down")
    v = await stripe_read.read_session(SID, _catalog())
    assert v.status == "unreachable" and not v.paid
    stripe.answer = (500, {})
    assert (await stripe_read.read_session(SID, _catalog())).status == "unreachable"


async def test_a_refused_key_keeps_stripes_message_but_never_the_key(stripe):
    stripe.answer = (403, {"error": {"message": (
        "The provided key 'rk_test_SECRETKEY123' does not have the required "
        "permissions for this endpoint. Having the 'rak_charge_read' permission "
        "would allow this request to continue.")}})
    v = await stripe_read.read_session(SID, _catalog())
    assert v.status == "refused" and "rak_charge_read" in v.detail
    assert "SECRETKEY123" not in v.detail


async def test_an_unknown_session_is_not_found(stripe):
    stripe.answer = (404, {"error": {"message": "No such checkout.session"}})
    assert (await stripe_read.read_session(SID, _catalog())).status == "not_found"
    assert (await stripe_read.read_session("../../etc", _catalog())).status == "not_found"


# ------------------------------------------------------------------ signed links

KEY = b"k" * 32


def test_a_signed_link_verifies_and_names_its_session_and_file():
    token = delivery.mint(SID, "vault-kit-zip", days=7, now=1000.0, key=KEY)
    assert delivery.verify(token, now=1001.0, key=KEY) == ("ok", SID, "vault-kit-zip")


def test_a_link_expires():
    token = delivery.mint(SID, "vault-kit-zip", days=7, now=1000.0, key=KEY)
    assert delivery.verify(token, now=1000.0 + 7 * 86400 + 1, key=KEY)[0] == "expired"


@pytest.mark.parametrize("tamper", [
    lambda t: t.replace("vault-kit-zip", "operator-pdf"),
    lambda t: t.replace(".1604", ".9604") if ".1604" in t else t[:-1] + "0",
    lambda t: t.replace(SID, "cs_test_ZZZZZZZZZZZZZZZZZZZZ"),
    lambda t: t[:-2] + ("00" if not t.endswith("00") else "11"),
    lambda t: "v2" + t[2:],
    lambda t: t + ".x",
    lambda t: "",
])
def test_a_tampered_link_is_bad(tamper):
    token = delivery.mint(SID, "vault-kit-zip", days=7, now=1000.0, key=KEY)
    assert delivery.verify(tamper(token), now=1001.0, key=KEY)[0] == "bad"


def test_a_link_signed_with_another_key_is_bad():
    token = delivery.mint(SID, "vault-kit-zip", days=7, now=1000.0, key=b"x" * 32)
    assert delivery.verify(token, now=1001.0, key=KEY)[0] == "bad"


def test_no_signing_key_means_no_link(monkeypatch):
    monkeypatch.delenv(delivery.DOWNLOAD_KEY_ENV, raising=False)
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    assert delivery.signing_key() is None
    assert delivery.mint(SID, "x", days=1) is None
    assert delivery.verify("v1.x.1.cs_test_aaaaaaaaaaaa.00")[0] == "bad"


def test_the_download_key_is_domain_separated_from_the_session_secret(monkeypatch):
    monkeypatch.delenv(delivery.DOWNLOAD_KEY_ENV, raising=False)
    monkeypatch.setenv("SESSION_SECRET", "s3cret")
    assert delivery.signing_key() != b"s3cret"
    assert delivery.signing_key() == hmac.new(b"s3cret", b"atlas-academy-download-v1",
                                              hashlib.sha256).digest()


# ------------------------------------------------------------------ files on disk

def test_resolve_stays_inside_the_files_directory(tmp_path):
    root = tmp_path / "files"
    (root / "vault-kit").mkdir(parents=True)
    (root / "vault-kit" / "kit.zip").write_bytes(b"zip")
    (tmp_path / "secret.txt").write_text("nope")
    (root / "vault-kit" / "link.zip").symlink_to(tmp_path / "secret.txt")
    (root / "vault-kit" / "inner.zip").symlink_to(root / "vault-kit" / "kit.zip")
    ok = cat_mod.File("k", "Kit", "vault-kit/kit.zip")
    assert delivery.resolve(ok, root) == (root / "vault-kit" / "kit.zip").resolve()
    for path in ("../secret.txt", "vault-kit/../../secret.txt", "vault-kit/link.zip",
                 "vault-kit/inner.zip", "vault-kit", "missing.pdf", "/etc/passwd"):
        assert delivery.resolve(cat_mod.File("k", "x", path), root) is None, path


# ------------------------------------------------------------------ the page

@pytest.fixture
def served(monkeypatch, tmp_path, memory_backend):
    """The API with a fake Stripe verdict, a files dir, and Slack captured."""
    cat = _catalog()
    monkeypatch.setattr(api, "_catalog", lambda: cat)
    root = tmp_path / "files"
    (root / "vault-kit").mkdir(parents=True)
    (root / "vault-kit" / "claude-obsidian-vault-kit.zip").write_bytes(b"PK-kit")
    monkeypatch.setenv(delivery.FILES_ENV, str(root))
    monkeypatch.setenv(delivery.DOWNLOAD_KEY_ENV, "download-key")
    api._pages.clear()
    api._files.clear()

    class State:
        verdict = stripe_read.judge(_session(), cat)
        slack: ClassVar[list] = []

    async def fake_read(session_id, catalog, **_kw):
        v = State.verdict
        return stripe_read.Verdict(**{**v.__dict__, "session_id": session_id})
    monkeypatch.setattr(api, "read_session", fake_read)

    async def notify(msg):
        State.slack.append(msg)
        return ["slack"]
    monkeypatch.setattr("atlas.kernel.comms.notify", notify)
    memory_backend.responses["INSERT INTO academy_purchases"] = {"session_id": SID}
    State.slack = []
    State.cat = cat
    State.db = memory_backend
    from atlas.api.main import app
    State.client = TestClient(app, client=("203.0.113.9", 40000))
    return State


def _access(served, sid=SID):
    return served.client.get("/public/academy/access", params={"session_id": sid})


def test_a_paid_buyer_gets_signed_links_and_a_bookmark_note(served):
    r = _access(served)
    assert r.status_code == 200
    assert "Bookmark this page" in r.text and "Claude + Obsidian Vault Kit" in r.text
    assert "/public/academy/file/v1.vault-kit-zip." in r.text
    assert r.headers["cache-control"] == "no-store"
    assert "Checked with Stripe at" in r.text


def test_a_first_paid_visit_is_recorded_and_posted_once(served):
    _access(served)
    [msg] = served.slack
    assert "Claude + Obsidian Vault Kit" in msg.title and "19.00 USD" in msg.title
    assert "read" in msg.title and msg.topic == "academy"
    inserts = [a for s, a in served.db.sql_log if "INSERT INTO academy_purchases" in s]
    assert inserts[0][0] == SID and inserts[0][2] == "paid" and inserts[0][5] == 1900
    # A second visit updates the row, posts nothing.
    served.db.responses["INSERT INTO academy_purchases"] = None
    _access(served)
    assert len(served.slack) == 1
    assert any("UPDATE academy_purchases" in s for s, _ in served.db.sql_log)


@pytest.mark.parametrize("status,code,words", [
    ("unpaid", 402, "isn't paid"),
    ("refunded", 403, "refunded"),
    ("disputed", 403, "dispute"),
    ("unknown_price", 409, "can't match it"),
    ("unconfigured", 503, "can't check your payment"),
    ("unreachable", 503, "can't reach Stripe"),
    ("refused", 503, "can't reach Stripe"),
    ("not_found", 404, "couldn't find"),
])
def test_anything_but_paid_gets_an_honest_page_and_no_link(served, status, code, words):
    served.verdict = stripe_read.Verdict(status, SID, detail="x",
                                         offers=served.verdict.offers,
                                         amount_minor=1900, currency="usd")
    r = _access(served)
    assert r.status_code == code
    assert words in html.unescape(r.text)
    assert "/public/academy/file/" not in r.text


def test_a_refund_is_recorded_but_not_posted_as_a_purchase_twice(served):
    served.db.responses["INSERT INTO academy_purchases"] = None
    served.verdict = stripe_read.judge(_session(refunded=True, amount_refunded=1900),
                                       served.cat)
    _access(served)
    assert not served.slack
    updates = [a for s, a in served.db.sql_log if "UPDATE academy_purchases" in s]
    assert updates[0][1] == "refunded"


def test_an_unknown_price_is_posted_so_miro_can_fix_the_catalog(served):
    served.verdict = stripe_read.judge(_session(price="price_NOTOURS999"), served.cat)
    r = _access(served)
    assert r.status_code == 409
    [msg] = served.slack
    assert "matched no catalog price" in msg.title and "price_NOTOURS999" in msg.body


def test_a_malformed_session_id_never_reaches_stripe(served):
    for bad in ("", "../../etc/passwd", "cs_live_short", "pi_123456789012345"):
        assert _access(served, bad).status_code == 404
    assert not served.db.sql_log


def test_no_signing_key_says_so(served, monkeypatch):
    monkeypatch.delenv(delivery.DOWNLOAD_KEY_ENV)
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    r = _access(served)
    assert r.status_code == 503 and "download key" in html.unescape(r.text)


def test_a_file_not_on_the_server_says_so_instead_of_a_link(served):
    served.verdict = stripe_read.judge(_session(price=PRICE_BUNDLE), served.cat)
    r = _access(served)
    assert r.status_code == 200
    assert "Not on the server yet" in r.text
    assert "/public/academy/file/v1.vault-kit-zip." in r.text
    assert "/public/academy/file/v1.operator-pdf." not in r.text


def test_the_access_page_is_rate_limited(served):
    for _ in range(12):
        _access(served)
    assert _access(served).status_code == 429


# ------------------------------------------------------------------ downloads

def _link(served, file_key="vault-kit-zip", **kw):
    token = delivery.mint(SID, file_key, days=7, **kw)
    return f"/public/academy/file/{token}"


def test_a_signed_link_downloads_the_file(served):
    r = served.client.get(_link(served))
    assert r.status_code == 200 and r.content == b"PK-kit"
    assert "attachment" in r.headers["content-disposition"]
    assert "claude-obsidian-vault-kit.zip" in r.headers["content-disposition"]


def test_an_expired_link_says_so(served):
    r = served.client.get(_link(served, now=time.time() - 8 * 86400))
    assert r.status_code == 410 and "expired" in r.text


def test_a_tampered_link_is_refused(served):
    path = _link(served)
    flipped = path[:-1] + ("0" if path[-1] != "0" else "1")
    assert served.client.get(flipped).status_code == 404
    assert served.client.get("/public/academy/file/..%2F..%2Fetc%2Fpasswd").status_code \
        in (404,)


def test_a_link_for_a_file_this_purchase_did_not_include_is_refused(served, tmp_path):
    r = served.client.get(_link(served, "operator-pdf"))
    assert r.status_code == 404


def test_a_refund_after_the_link_was_made_stops_the_download(served):
    path = _link(served)
    served.verdict = stripe_read.judge(_session(refunded=True, amount_refunded=1900),
                                       served.cat)
    r = served.client.get(path)
    assert r.status_code == 403 and r.content != b"PK-kit"


def test_stripe_down_at_download_time_serves_nothing(served):
    path = _link(served)
    served.verdict = stripe_read.Verdict("unreachable", SID, detail="down")
    r = served.client.get(path)
    assert r.status_code == 503 and b"PK-kit" not in r.content


# ------------------------------------------------------------------ public surface

def test_only_the_exact_academy_routes_are_public():
    from atlas.api import main
    token = delivery.mint(SID, "vault-kit-zip", days=7, key=KEY)
    assert main._is_public("/public/academy/access")
    assert main._is_public(f"/public/academy/file/{token}")
    assert main._is_public("/webhooks/stripe-academy")
    for path in ("/public/academy/", "/public/academy/access/x",
                 "/public/academy/file/", "/public/academy/file/../../api/overview",
                 f"/public/academy/file/{token}/x", "/public/academy/purchases"):
        assert not main._is_public(path), path


# ------------------------------------------------------------------ webhook

def _signed(body: bytes, secret: str) -> str:
    t = str(int(time.time()))
    sig = hmac.new(secret.encode(), t.encode() + b"." + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={sig}"


def _event(kind="checkout.session.completed") -> bytes:
    return json.dumps({"id": "evt_1", "type": kind,
                       "data": {"object": {"id": SID, "object": "checkout.session"}}}
                      ).encode()


def test_the_webhook_needs_its_secret_and_a_valid_signature(served, monkeypatch):
    monkeypatch.delenv(api.WEBHOOK_SECRET_ENV, raising=False)
    r = served.client.post(api.WEBHOOK_PATH, content=_event())
    assert r.status_code == 503
    monkeypatch.setenv(api.WEBHOOK_SECRET_ENV, "whsec_x")
    r = served.client.post(api.WEBHOOK_PATH, content=_event(),
                           headers={"stripe-signature": _signed(_event(), "whsec_y")})
    assert r.status_code == 401
    assert not served.slack


def test_a_signed_checkout_event_records_and_posts_from_a_fresh_read(served, monkeypatch):
    monkeypatch.setenv(api.WEBHOOK_SECRET_ENV, "whsec_x")
    body = _event()
    r = served.client.post(api.WEBHOOK_PATH, content=body,
                           headers={"stripe-signature": _signed(body, "whsec_x")})
    assert r.status_code == 200 and r.text == "recorded"
    [msg] = served.slack
    assert "19.00 USD" in msg.title
    inserts = [a for s, a in served.db.sql_log if "INSERT INTO academy_purchases" in s]
    assert inserts[0][1] == "webhook"


def test_the_webhook_asks_stripe_to_retry_when_it_cannot_read(served, monkeypatch):
    monkeypatch.setenv(api.WEBHOOK_SECRET_ENV, "whsec_x")
    served.verdict = stripe_read.Verdict("unconfigured", SID)
    body = _event()
    r = served.client.post(api.WEBHOOK_PATH, content=body,
                           headers={"stripe-signature": _signed(body, "whsec_x")})
    assert r.status_code == 503 and not served.slack


def test_other_events_are_acknowledged_and_ignored(served, monkeypatch):
    monkeypatch.setenv(api.WEBHOOK_SECRET_ENV, "whsec_x")
    body = _event("customer.created")
    r = served.client.post(api.WEBHOOK_PATH, content=body,
                           headers={"stripe-signature": _signed(body, "whsec_x")})
    assert r.status_code == 200 and r.text == "ignored"


def test_a_video_lesson_plays_on_the_page_and_still_downloads(served, monkeypatch):
    """The operator course is video: each lesson's mp4 gets a player on the
    access page, with the same signed link as its download."""
    cat = _catalog()
    offer = next(o for p in cat.products for o in p.offers if o.id == "safe-ai-operator")
    served.verdict = stripe_read.Verdict(**{**served.verdict.__dict__, "offers": (offer,),
                                            "price_ids": (offer.stripe_price_id or "price_OP",)})
    import os
    root = pathlib.Path(os.environ[delivery.FILES_ENV])
    for f in offer.files:                             # the files exist on disk
        (root / f.path).parent.mkdir(parents=True, exist_ok=True)
        (root / f.path).write_bytes(b"x")
    r = _access(served)
    assert r.status_code == 200
    players = re.findall(r'<video controls preload="metadata" src="([^"]+)"></video>', r.text)
    assert len(players) == 28
    assert all("/public/academy/file/v1.operator-video-" in p for p in players)
    assert r.text.count("<video") == 28           # the PDF and the zip get no player
