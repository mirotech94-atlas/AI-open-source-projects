"""A won or prevented dispute restores access (Miro, 2026-10-16).

Stripe keeps a charge's `disputed` flag true after a dispute closes, so when
it is set the reader lists the charge's disputes (GET /v1/disputes?charge=,
read-only). Access returns only when every dispute is `won`,
`warning_closed` or `prevented` (Miro, 2026-10-16) and the charge is not
refunded; any other status stays disputed; a dispute read that fails is "can't check", never paid.
"""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from atlas.api import academy as api
from atlas.modules.academy import delivery, stripe_read
from tests.test_academy_access import SID, _catalog, _session, _signed


class Stripe:
    def __init__(self, session: dict, disputes: list[str] | None = None) -> None:
        self.session = session
        self.disputes = disputes or []
        self.fail_disputes: object = None
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == f"/v1/checkout/sessions/{SID}":
            return httpx.Response(200, json=self.session)
        if path == "/v1/disputes":
            if isinstance(self.fail_disputes, Exception):
                raise self.fail_disputes
            if isinstance(self.fail_disputes, int):
                return httpx.Response(self.fail_disputes, json={"error": {}})
            assert request.url.params["charge"] == "ch_1"
            rows = [{"id": f"du_{i}", "object": "dispute", "charge": "ch_1",
                     "status": st} for i, st in enumerate(self.disputes)]
            return httpx.Response(200, json={"object": "list", "has_more": False,
                                             "data": rows})
        if path == "/v1/checkout/sessions":
            return httpx.Response(200, json={"object": "list", "has_more": False,
                                             "data": [{"id": SID}]})
        return httpx.Response(404, json={"error": {"message": "no"}})


@pytest.fixture
def stripe(monkeypatch):
    monkeypatch.setenv(stripe_read.KEY_ENV, "rk_test_SECRETKEY123")

    def make(disputes, *, refunded=False):
        s = Stripe(_session(disputed=True, refunded=refunded,
                            amount_refunded=1900 if refunded else 0), disputes)
        monkeypatch.setattr(stripe_read, "_transport", httpx.MockTransport(s.handler))
        return s
    return make


@pytest.mark.parametrize("disputes,status", [
    (["won"], "paid"),
    (["lost"], "disputed"),
    (["warning_closed"], "paid"),
    (["prevented"], "paid"),
    (["prevented", "lost"], "disputed"),
    (["won", "lost"], "disputed"),
    (["won", "warning_closed"], "paid"),
    (["needs_response"], "disputed"),
    (["under_review"], "disputed"),
    (["warning_needs_response"], "disputed"),
    (["warning_under_review"], "disputed"),
    ([], "disputed"),                   # flag set, no dispute listed: unsure
])
async def test_only_disputes_all_won_or_closed_restore_access(stripe, disputes, status):
    s = stripe(disputes)
    v = await stripe_read.read_session(SID, _catalog())
    assert v.status == status
    assert all(r.method == "GET" for r in s.requests)
    assert [r.url.path for r in s.requests] == [f"/v1/checkout/sessions/{SID}",
                                                "/v1/disputes"]


async def test_won_but_refunded_is_refunded(stripe):
    s = stripe(["won"], refunded=True)
    v = await stripe_read.read_session(SID, _catalog())
    assert v.status == "refunded"
    assert all(r.url.path != "/v1/disputes" for r in s.requests)


@pytest.mark.parametrize("fail", [httpx.ConnectError("down"), 500, 403])
async def test_a_failed_dispute_read_is_never_paid(stripe, fail):
    s = stripe(["won"])
    s.fail_disputes = fail
    v = await stripe_read.read_session(SID, _catalog())
    assert v.status in ("unreachable", "refused") and not v.paid


async def test_an_undisputed_charge_asks_nothing_more(monkeypatch):
    monkeypatch.setenv(stripe_read.KEY_ENV, "rk_test_SECRETKEY123")
    s = Stripe(_session())
    monkeypatch.setattr(stripe_read, "_transport", httpx.MockTransport(s.handler))
    assert (await stripe_read.read_session(SID, _catalog())).paid
    assert len(s.requests) == 1


# ------------------------------------------------------------------ the API

@pytest.fixture
def client(monkeypatch, tmp_path, memory_backend):
    cat = _catalog()
    monkeypatch.setattr(api, "_catalog", lambda: cat)
    root = tmp_path / "files" / "vault-kit"
    root.mkdir(parents=True)
    (root / "claude-obsidian-vault-kit.zip").write_bytes(b"PK-kit")
    monkeypatch.setenv(delivery.FILES_ENV, str(tmp_path / "files"))
    monkeypatch.setenv(delivery.DOWNLOAD_KEY_ENV, "download-key")
    api._pages.clear()

    async def notify(msg):
        return ["slack"]
    monkeypatch.setattr("atlas.kernel.comms.notify", notify)
    memory_backend.responses["INSERT INTO academy_purchases"] = None
    from atlas.api.main import app
    c = TestClient(app, client=("203.0.113.9", 40000))
    c.db = memory_backend
    return c


def test_a_won_dispute_serves_downloads_again(stripe, client):
    stripe(["won"])
    r = client.get(delivery.ACCESS_PATH, params={"session_id": SID})
    assert r.status_code == 200 and "/public/academy/file/v1.vault-kit-zip." in r.text


def test_a_lost_dispute_keeps_downloads_paused(stripe, client):
    stripe(["lost"])
    r = client.get(delivery.ACCESS_PATH, params={"session_id": SID})
    assert r.status_code == 403 and "/public/academy/file/" not in r.text


def test_a_failed_dispute_read_says_it_could_not_check(stripe, client):
    stripe(["won"]).fail_disputes = httpx.ConnectError("down")
    r = client.get(delivery.ACCESS_PATH, params={"session_id": SID})
    assert r.status_code == 503 and "/public/academy/file/" not in r.text


@pytest.mark.parametrize("disputes,status", [(["won"], "paid"), (["lost"], "disputed")])
def test_dispute_closed_updates_the_recorded_status(stripe, client, monkeypatch,
                                                     disputes, status):
    monkeypatch.setenv(api.WEBHOOK_SECRET_ENV, "whsec_x")
    stripe(disputes)
    body = json.dumps({"id": "evt_3", "type": "charge.dispute.closed", "data": {
        "object": {"id": "dp_1", "object": "dispute", "charge": "ch_1",
                   "payment_intent": "pi_12345678"}}}).encode()
    r = client.post(api.WEBHOOK_PATH, content=body,
                    headers={"stripe-signature": _signed(body, "whsec_x")})
    assert r.status_code == 200 and r.text == status
    [args] = [a for s_, a in client.db.sql_log if "UPDATE academy_purchases" in s_]
    assert args[0] == SID and args[1] == status
