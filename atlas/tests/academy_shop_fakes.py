"""Shared fakes for the ATLAS Academy shop backend tests: Stripe's session
list on an httpx.MockTransport (every request kept, with its method), each
checkout's verdict scripted per session id, Slack captured."""
from __future__ import annotations

from typing import ClassVar

import httpx
import pytest
from fastapi.testclient import TestClient

from atlas.api import academy as api
from atlas.modules.academy import delivery, stripe_read
from tests.test_academy_access import _catalog, _session

SID = "cs_test_a1B2c3D4e5F6g7H8i9J0"
SID2 = "cs_test_Z9y8X7w6V5u4T3s2R1q0"
EMAIL = "buyer@example.com"
RECEIPT = "1234-5678"


def _listed(sid=SID, receipt=RECEIPT) -> dict:
    return {"id": sid, "object": "checkout.session", "status": "complete",
            "payment_intent": {"id": "pi_12345678", "status": "succeeded",
                               "latest_charge": {"id": "ch_1",
                                                 "receipt_number": receipt}}}


class FakeStripe:
    """An httpx.MockTransport standing in for api.stripe.com: every request
    is recorded with its method; sessions are listed per filtered email."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.by_email: dict[str, list[dict]] = {}
        self.by_pi: dict[str, list[dict]] = {}
        self.fail: object = None
        self.page_size = 0                  # 0: one page, has_more false

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.fail, Exception):
            raise self.fail
        if isinstance(self.fail, int):
            return httpx.Response(self.fail, json={"error": {"message": "nope"}})
        q = request.url.params
        if request.url.path == "/v1/checkout/sessions":
            if "payment_intent" in q:
                rows = self.by_pi.get(q["payment_intent"], [])
            else:
                rows = self.by_email.get(q.get("customer_details[email]", ""), [])
            more = False
            if self.page_size:                         # Stripe's cursor paging
                after = q.get("starting_after")
                start = next((i + 1 for i, r in enumerate(rows) if r["id"] == after), 0)
                more = start + self.page_size < len(rows)
                rows = rows[start:start + self.page_size]
            return httpx.Response(200, json={"object": "list", "has_more": more,
                                             "data": rows})
        return httpx.Response(404, json={"error": {"message": "No such thing"}})


@pytest.fixture
def fake(monkeypatch):
    f = FakeStripe()
    monkeypatch.setenv(stripe_read.KEY_ENV, "rk_test_SECRETKEY123")
    monkeypatch.setattr(stripe_read, "_transport", httpx.MockTransport(f.handler))
    return f



OTHER = "someone@example.org"


@pytest.fixture
def served(monkeypatch, tmp_path, memory_backend, fake):
    """The API with Stripe's session list on the fake transport, each
    checkout's verdict scripted per session id, and Slack captured."""
    cat = _catalog()
    monkeypatch.setattr(api, "_catalog", lambda: cat)
    monkeypatch.setenv(delivery.DOWNLOAD_KEY_ENV, "download-key")
    monkeypatch.setenv(delivery.FILES_ENV, str(tmp_path / "files"))
    # The response-time floor is real in production (1.5 s); tests set it.
    monkeypatch.setattr(api, "RECOVER_FLOOR_S", 0.0, raising=False)
    for name in ("_recover_ip", "_recover_email", "_library", "_pages"):
        if hasattr(api, name):
            getattr(api, name).clear()

    class State:
        verdicts: ClassVar[dict] = {}
        reads: ClassVar[list] = []
        slack: ClassVar[list] = []

    paid = stripe_read.judge(_session(), cat)
    State.verdicts = {SID: paid}
    State.reads = []

    async def fake_read(session_id, catalog, **_kw):
        State.reads.append(session_id)
        v = State.verdicts.get(session_id) or stripe_read.Verdict("not_found", session_id)
        return stripe_read.Verdict(**{**v.__dict__, "session_id": session_id})
    monkeypatch.setattr(api, "read_session", fake_read)

    async def notify(msg):
        State.slack.append(msg)
        return ["slack"]
    monkeypatch.setattr("atlas.kernel.comms.notify", notify)
    memory_backend.responses["INSERT INTO academy_purchases"] = {"session_id": SID}
    memory_backend.responses["FROM academy_purchases WHERE email IS NOT NULL"] = []
    fake.by_email[EMAIL] = [_listed()]
    State.slack = []
    State.cat, State.db, State.fake = cat, memory_backend, fake
    from atlas.api.main import app
    State.client = TestClient(app, client=("203.0.113.9", 40000),
                              follow_redirects=False)
    return State


_clients: dict[str, TestClient] = {}


def _recover(served, email=EMAIL, receipt=RECEIPT, ip="203.0.113.9", **extra):
    """One form post from `ip` (the TestClient's peer address: a forwarded
    header counts only from ATLAS's own proxy)."""
    from atlas.api.main import app
    client = _clients.setdefault(ip, TestClient(app, client=(ip, 40000),
                                                follow_redirects=False))
    data = {"email": email, "receipt": receipt, **extra}
    return client.post(api.RECOVER_PATH, data=data)


def _token_of(r) -> str:
    loc = r.headers["location"]
    assert loc.startswith(delivery.api_base() + delivery.LIBRARY_PATH + "?t=")
    return loc.split("?t=", 1)[1]

