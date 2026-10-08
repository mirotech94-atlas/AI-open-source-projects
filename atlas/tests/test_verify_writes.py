"""Read-back verifiers for the riskier externally visible writes (RA-D1, second
batch): stripe, Instagram DMs and comments, WhatsApp, Matrix, agency
outreach, Guardian, nightshift and delegate.

Same rules as tests/test_verify.py: every provider is faked at
`httpx.AsyncClient.send`, and the fake refuses any method but GET. DB reads
are faked at `state.fetch_one`, and the fake refuses anything but a SELECT.
Each verifier is covered for verified, contradicted, unknown and raises.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from atlas.kernel import state
from atlas.kernel import verify as v
from atlas.kernel.contracts import ActionResult

SECRET = "sk-verify-writes-supersecret-0123456789"
T0 = datetime(2026, 9, 28, 10, 0, tzinfo=UTC)


class Net:
    """Routes by a regex on the request path; GET only. A body may be a
    callable taking the request."""

    def __init__(self) -> None:
        self.routes: list[tuple[str, object]] = []
        self.seen: list[str] = []

    def on(self, pattern: str, status: int = 200, body=None, *, raises=None) -> None:
        self.routes.insert(0, (pattern, (status, body, raises)))

    async def send(self, client, request: httpx.Request, **_kw) -> httpx.Response:
        assert request.method == "GET", (
            f"a verifier tried to write: {request.method} {request.url.path}")
        path = request.url.path
        self.seen.append(path)
        for pattern, (status, body, raises) in self.routes:
            if re.search(pattern, path):
                if raises is not None:
                    raise raises
                if callable(body):
                    body = body(request)
                return httpx.Response(status, json=body, request=request)
        return httpx.Response(404, json={"error": "no route"}, request=request)


@pytest.fixture
def net(monkeypatch):
    fake = Net()

    async def send(client, request, **kw):
        return await fake.send(client, request, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    return fake


@pytest.fixture
def db(monkeypatch):
    """`state.fetch_one` answering SELECTs from a dict of {table: row}."""
    rows: dict[str, dict | None] = {}

    async def fetch_one(sql, *args):
        assert sql.lstrip().upper().startswith("SELECT"), f"a verifier wrote: {sql}"
        table = re.search(r"FROM\s+(\w+)", sql).group(1)
        return rows.get(table)

    monkeypatch.setattr(state, "fetch_one", fetch_one)
    return rows


def _no_secret(result: v.Verification) -> None:
    assert SECRET not in json.dumps(result.as_audit(), default=str)


# ================================================================== loader

async def test_stripe_verifier_loads_through_the_registry(monkeypatch):
    """`stripe.*` lives in `stripe_billing`: the namespace import misses,
    and the loader must fall back to the package the action's run is in."""
    from atlas.kernel.registry import REGISTRY
    from atlas.modules import stripe_billing
    monkeypatch.delitem(sys.modules, "atlas.modules.stripe_billing.readback",
                        raising=False)
    monkeypatch.setattr(v, "_LOADED", set())
    monkeypatch.setattr(v, "_VERIFIERS", {k: f for k, f in v._VERIFIERS.items()
                                          if not k.startswith("stripe.")})
    monkeypatch.setattr(v, "_RECONCILERS", dict(v._RECONCILERS))
    action = next(a for a in stripe_billing.MODULE.actions()
                  if a.name == "stripe.retry_invoice")
    monkeypatch.setitem(REGISTRY.actions, action.name, action)
    assert v.has_verifier("stripe.retry_invoice")
    assert v.has_reconciler("stripe.retry_invoice")


# ================================================================== stripe

ST_PARAMS = {"business": "guardian", "invoice_id": "in_1"}


@pytest.fixture
def stripe_env(monkeypatch):
    monkeypatch.setenv("STRIPE_GUARDIAN_KEY", SECRET)
    import importlib

    from atlas.modules.stripe_billing import readback
    if "stripe.retry_invoice" not in v._VERIFIERS:     # the loader test ran first
        importlib.reload(readback)


def _invoice(**over):
    return {"id": "in_1", "status": "paid", "amount_paid": 900, "amount_due": 900,
            "attempt_count": 2, "customer_email": "buyer@example.com",
            "status_transitions": {"paid_at": int(T0.timestamp()) + 60},
            "payment_intent": "pi_1", **over}


def _intent(**over):
    return {"id": "pi_1", "status": "succeeded", "created": int(T0.timestamp()) + 30,
            "latest_charge": "ch_1", **over}


def _charge(**over):
    return {"id": "ch_1", "status": "succeeded", "receipt_number": "1234-5678",
            "receipt_email": "buyer@example.com", **over}


async def test_stripe_verified(net, stripe_env):
    net.on(r"/v1/invoices/in_1$", body=_invoice())
    net.on(r"/v1/payment_intents/pi_1$", body=_intent())
    net.on(r"/v1/charges/ch_1$", body=_charge())
    got = await v.verify("stripe.retry_invoice", ST_PARAMS, ActionResult(True, "paid"))
    assert got.state is v.State.VERIFIED
    assert got.evidence["attempt"]["payment_intent"] == "pi_1"
    assert got.evidence["attempt"]["receipt"] == {
        "sent": True, "receipt_number": "1234-5678", "receipt_email_set": True}
    assert "buyer@example.com" not in json.dumps(got.as_audit())
    _no_secret(got)


async def test_stripe_no_receipt_is_evidence_not_a_failure(net, stripe_env):
    net.on(r"/v1/invoices/in_1$", body=_invoice())
    net.on(r"/v1/payment_intents/pi_1$", body=_intent())
    net.on(r"/v1/charges/ch_1$", body=_charge(receipt_number=None, receipt_email=None))
    got = await v.verify("stripe.retry_invoice", ST_PARAMS, ActionResult(True, "paid"))
    assert got.state is v.State.VERIFIED
    assert got.evidence["attempt"]["receipt"]["sent"] is False

    net.on(r"/v1/charges/ch_1$", 403, {"error": {"message": "no charges scope"}})
    got = await v.verify("stripe.retry_invoice", ST_PARAMS, ActionResult(True, "paid"))
    assert got.state is v.State.VERIFIED
    assert got.evidence["attempt"]["receipt"]["sent"] is None


async def test_stripe_contradicted(net, stripe_env):
    net.on(r"/v1/invoices/in_1$", body=_invoice(status="open"))
    net.on(r"/v1/payment_intents/pi_1$", body=_intent(
        status="requires_payment_method",
        last_payment_error={"decline_code": "insufficient_funds"}))
    got = await v.verify("stripe.retry_invoice", ST_PARAMS, ActionResult(True, "open"))
    assert got.state is v.State.CONTRADICTED and "insufficient_funds" in got.reason

    net.on(r"/v1/invoices/in_1$", 404, {"error": {"message": "No such invoice"}})
    got = await v.verify("stripe.retry_invoice", ST_PARAMS, ActionResult(True))
    assert got.state is v.State.CONTRADICTED


async def test_stripe_unknown(net, stripe_env, monkeypatch):
    net.on(r"/v1/invoices/in_1$", body=_invoice(status="open"))
    net.on(r"/v1/payment_intents/pi_1$", body=_intent(status="processing"))
    got = await v.verify("stripe.retry_invoice", ST_PARAMS, ActionResult(True))
    assert got.state is v.State.UNKNOWN and got.failure_class is v.FailureClass.STALE

    net.on(r"/v1/invoices/in_1$", 401, {"error": {"message": "bad key"}})
    got = await v.verify("stripe.retry_invoice", ST_PARAMS, ActionResult(True))
    assert got.state is v.State.UNKNOWN and got.failure_class is v.FailureClass.AUTH
    _no_secret(got)

    monkeypatch.delenv("STRIPE_GUARDIAN_KEY")
    got = await v.verify("stripe.retry_invoice", ST_PARAMS, ActionResult(True))
    assert got.state is v.State.UNKNOWN and got.failure_class is v.FailureClass.DISABLED


async def test_stripe_raises_is_unknown(stripe_env, monkeypatch):
    from atlas.modules.stripe_billing import readback

    async def boom(*_a, **_kw):
        raise RuntimeError(f"parse error near {SECRET}")

    monkeypatch.setattr(readback, "_get", boom)
    got = await v.verify("stripe.retry_invoice", ST_PARAMS, ActionResult(True))
    assert got.state is v.State.UNKNOWN and "raised" in got.reason
    _no_secret(got)


async def test_stripe_reconcile(net, stripe_env):
    row = {"at": T0}
    net.on(r"/v1/invoices/in_1$", body=_invoice())
    net.on(r"/v1/payment_intents/pi_1$", body=_intent())
    got = await v.reconcile_unknown("stripe.retry_invoice", ST_PARAMS, row)
    assert got.state is v.State.VERIFIED

    # Open, and the latest attempt predates the run: never retried.
    net.on(r"/v1/invoices/in_1$", body=_invoice(status="open"))
    net.on(r"/v1/payment_intents/pi_1$", body=_intent(
        status="requires_payment_method", created=int(T0.timestamp()) - 3600))
    got = await v.reconcile_unknown("stripe.retry_invoice", ST_PARAMS, row)
    assert got.state is v.State.CONTRADICTED and "no payment attempt" in got.reason


# ================================================================== messages.reply

@pytest.fixture
def dm_env(monkeypatch):
    from atlas.modules.messages import instagram_dm
    monkeypatch.setenv("INSTAGRAM_ACCOUNT_ID", "1789")
    monkeypatch.setenv("INSTAGRAM_TOKEN", SECRET)
    monkeypatch.setenv("INSTAGRAM_PAGE_ID", "4242")
    monkeypatch.setattr(instagram_dm, "_PAGE_TOKEN", {})


def _thread(*messages, more=False):
    box = {"data": list(messages)}
    if more:
        box["paging"] = {"next": "https://graph.facebook.com/next"}
    return {"id": "t_1", "messages": box}


def _dm(mid, text="see you at 5", sender="1789", at="2026-09-28T10:01:00+0000"):
    return {"id": mid, "from": {"id": sender}, "message": text, "created_time": at}


DM_RESULT = ActionResult(True, "replied on instagram",
                         evidence={"platform": "instagram", "thread_id": "t_1",
                                   "message_id": "m_9"})
DM_PARAMS = {"inbound_id": 3, "text": "see you at 5"}


def _page(net):
    net.on(r"/4242$", body={"access_token": SECRET + "-page"})


async def test_dm_verified(net, dm_env):
    _page(net)
    net.on(r"/t_1$", body=_thread(_dm("m_9"), _dm("m_8", "hi", sender="555")))
    got = await v.verify("messages.reply", DM_PARAMS, DM_RESULT)
    assert got.state is v.State.VERIFIED and got.evidence["message_id"] == "m_9"
    _no_secret(got)


async def test_dm_contradicted(net, dm_env):
    _page(net)
    net.on(r"/t_1$", body=_thread(_dm("m_8", "hi", sender="555")))
    got = await v.verify("messages.reply", DM_PARAMS, DM_RESULT)
    assert got.state is v.State.CONTRADICTED

    net.on(r"/t_1$", body=_thread(_dm("m_9", "something else")))
    got = await v.verify("messages.reply", DM_PARAMS, DM_RESULT)
    assert got.state is v.State.CONTRADICTED and "text" in got.reason


async def test_dm_unknown(net, dm_env):
    _page(net)
    # Not on the first page, and more pages exist: absence proves nothing.
    net.on(r"/t_1$", body=_thread(_dm("m_8", "hi", sender="555"), more=True))
    got = await v.verify("messages.reply", DM_PARAMS, DM_RESULT)
    assert got.state is v.State.UNKNOWN

    net.on(r"/t_1$", 401, {"error": {"message": "expired"}})
    got = await v.verify("messages.reply", DM_PARAMS, DM_RESULT)
    assert got.state is v.State.UNKNOWN and got.failure_class is v.FailureClass.AUTH
    _no_secret(got)

    got = await v.verify("messages.reply", DM_PARAMS,
                         ActionResult(True, evidence={"platform": "instagram"}))
    assert got.state is v.State.UNKNOWN


async def test_dm_raises_is_unknown(dm_env, monkeypatch):
    from atlas.modules.messages import instagram_dm

    async def boom(*_a, **_kw):
        raise RuntimeError("graph parse error")

    monkeypatch.setattr(instagram_dm, "_page_token", boom)
    got = await v.verify("messages.reply", DM_PARAMS, DM_RESULT)
    assert got.state is v.State.UNKNOWN and "raised" in got.reason


async def test_dm_reconcile(net, dm_env, db):
    _page(net)
    db["messages_inbound"] = {"platform": "instagram", "thread_id": "t_1",
                              "draft": "see you at 5"}
    net.on(r"/t_1$", body=_thread(_dm("m_9")))
    got = await v.reconcile_unknown("messages.reply", {"inbound_id": 3, "text": ""},
                                    {"at": T0})
    assert got.state is v.State.VERIFIED and got.evidence["message_id"] == "m_9"

    got = await v.reconcile_unknown("messages.reply", {"inbound_id": 3},
                                    {"at": T0 + timedelta(hours=1)})
    assert got.state is v.State.CONTRADICTED


# ================================================================== matrix

@pytest.fixture
def mx(monkeypatch):
    from atlas.modules import matrix_rooms
    monkeypatch.setattr(matrix_rooms, "HOMESERVER", "https://hs.k9.test")
    monkeypatch.setenv("MATRIX_TOKEN", SECRET)


MX_PARAMS = {"room_id": "!r:example.org", "body": "deployed at 4", "room_name": "ops",
             "to": "@sam:example.org"}
MX_RESULT = ActionResult(True, "sent to ops", rollback_token="!r:example.org|$e1")


def _event(**over):
    return {"event_id": "$e1", "sender": "@miro:example.org", "type": "m.room.message",
            "content": {"msgtype": "m.text", "body": "deployed at 4"},
            "origin_server_ts": int(T0.timestamp() * 1000) + 5000, **over}


def _whoami(net):
    net.on(r"/account/whoami$", body={"user_id": "@miro:example.org"})


async def test_matrix_verified(net, mx):
    _whoami(net)
    net.on(r"/event/", body=_event())
    got = await v.verify("matrix_rooms.send_reply", MX_PARAMS, MX_RESULT)
    assert got.state is v.State.VERIFIED and got.evidence["event_id"] == "$e1"
    _no_secret(got)


async def test_matrix_contradicted(net, mx):
    _whoami(net)
    net.on(r"/event/", body=_event(unsigned={"redacted_because": {"type": "x"}}))
    got = await v.verify("matrix_rooms.send_reply", MX_PARAMS, MX_RESULT)
    assert got.state is v.State.CONTRADICTED and "redacted" in got.reason

    net.on(r"/event/", body=_event(content={"body": "other"}))
    got = await v.verify("matrix_rooms.send_reply", MX_PARAMS, MX_RESULT)
    assert got.state is v.State.CONTRADICTED

    net.on(r"/event/", 404, {"errcode": "M_NOT_FOUND"})
    got = await v.verify("matrix_rooms.send_reply", MX_PARAMS, MX_RESULT)
    assert got.state is v.State.CONTRADICTED


async def test_matrix_unknown(net, mx):
    net.on(r"/account/whoami$", 401, {"errcode": "M_UNKNOWN_TOKEN"})
    got = await v.verify("matrix_rooms.send_reply", MX_PARAMS, MX_RESULT)
    assert got.state is v.State.UNKNOWN and got.failure_class is v.FailureClass.AUTH
    _no_secret(got)
    got = await v.verify("matrix_rooms.send_reply", MX_PARAMS, ActionResult(True))
    assert got.state is v.State.UNKNOWN


async def test_matrix_raises_is_unknown(net, mx, monkeypatch):
    monkeypatch.delenv("MATRIX_TOKEN")          # _auth() raises KeyError
    got = await v.verify("matrix_rooms.send_reply", MX_PARAMS, MX_RESULT)
    assert got.state is v.State.UNKNOWN and "raised" in got.reason


async def test_matrix_reconcile(net, mx):
    _whoami(net)
    older = _event(event_id="$e0", content={"body": "earlier"},
                   origin_server_ts=int(T0.timestamp() * 1000) - 60_000)
    net.on(r"/messages$", body={"chunk": [_event(), older]})
    got = await v.reconcile_unknown("matrix_rooms.send_reply", MX_PARAMS, {"at": T0})
    assert got.state is v.State.VERIFIED and got.evidence["event_id"] == "$e1"

    net.on(r"/messages$", body={"chunk": [older]})
    got = await v.reconcile_unknown("matrix_rooms.send_reply", MX_PARAMS, {"at": T0})
    assert got.state is v.State.CONTRADICTED

    # A short chunk with an `end` token that does not reach the start is not
    # the whole room: unknown, never contradicted.
    newer = _event(event_id="$e2", content={"body": "later"})
    net.on(r"/messages$", body={"chunk": [newer], "end": "t_next"})
    got = await v.reconcile_unknown("matrix_rooms.send_reply", MX_PARAMS, {"at": T0})
    assert got.state is v.State.UNKNOWN


# ================================================================== instagram comments

@pytest.fixture
def ig_env(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_ACCOUNT_ID", "1789")
    monkeypatch.setenv("INSTAGRAM_TOKEN", SECRET)


HIDE_PARAMS = {"account": "main", "comment_id": "c1"}
HIDE_RESULT = ActionResult(True, "hid comment c1 on the main account",
                           rollback_token="main:c1")


async def test_hide_verified(net, ig_env):
    net.on(r"/c1$", body={"id": "c1", "hidden": True})
    got = await v.verify("instagram.hide_comment", HIDE_PARAMS, HIDE_RESULT)
    assert got.state is v.State.VERIFIED
    _no_secret(got)


async def test_hide_contradicted(net, ig_env):
    net.on(r"/c1$", body={"id": "c1", "hidden": False})
    got = await v.verify("instagram.hide_comment", HIDE_PARAMS, HIDE_RESULT)
    assert got.state is v.State.CONTRADICTED

    net.on(r"/c1$", 400, {"error": {"message": "Object with ID 'c1' does not exist"}})
    got = await v.verify("instagram.hide_comment", HIDE_PARAMS, HIDE_RESULT)
    assert got.state is v.State.CONTRADICTED


async def test_hide_unknown(net, ig_env):
    net.on(r"/c1$", 401, {"error": {"message": "token expired"}})
    got = await v.verify("instagram.hide_comment", HIDE_PARAMS, HIDE_RESULT)
    assert got.state is v.State.UNKNOWN and got.failure_class is v.FailureClass.AUTH
    _no_secret(got)
    net.on(r"/c1$", body={"id": "c1"})
    got = await v.verify("instagram.hide_comment", HIDE_PARAMS, HIDE_RESULT)
    assert got.state is v.State.UNKNOWN


async def test_hide_raises_and_reconcile(net, ig_env, monkeypatch):
    net.on(r"/c1$", body={"id": "c1", "hidden": True})
    got = await v.reconcile_unknown("instagram.hide_comment", HIDE_PARAMS, {"at": T0})
    assert got.state is v.State.VERIFIED

    from atlas.modules.instagram import readback

    async def boom(*_a, **_kw):
        raise RuntimeError("parse error")

    monkeypatch.setattr(readback, "_get", boom)
    got = await v.verify("instagram.hide_comment", HIDE_PARAMS, HIDE_RESULT)
    assert got.state is v.State.UNKNOWN and "raised" in got.reason


REPLY_PARAMS = {"account": "main", "comment_id": "c1", "text": "thanks, glad it helped"}
REPLY_RESULT = ActionResult(True, "replied to comment c1 on main",
                            evidence={"reply_id": "r7"})


def _reply(rid="r7", text="thanks, glad it helped", at="2026-09-28T10:02:00+0000"):
    return {"id": rid, "text": text, "timestamp": at}


async def test_reply_comment_verified(net, ig_env):
    net.on(r"/c1/replies$", body={"data": [_reply()]})
    got = await v.verify("instagram.reply_comment", REPLY_PARAMS, REPLY_RESULT)
    assert got.state is v.State.VERIFIED and got.evidence["reply_id"] == "r7"
    _no_secret(got)


async def test_reply_comment_contradicted(net, ig_env):
    net.on(r"/c1/replies$", body={"data": []})
    got = await v.verify("instagram.reply_comment", REPLY_PARAMS, REPLY_RESULT)
    assert got.state is v.State.CONTRADICTED

    net.on(r"/c1/replies$", body={"data": [_reply(text="nope")]})
    got = await v.verify("instagram.reply_comment", REPLY_PARAMS, REPLY_RESULT)
    assert got.state is v.State.CONTRADICTED and "text" in got.reason


async def test_reply_comment_unknown(net, ig_env):
    net.on(r"/c1/replies$", body={"data": [_reply("r1")],
                                  "paging": {"next": "https://graph/next"}})
    got = await v.verify("instagram.reply_comment", REPLY_PARAMS, REPLY_RESULT)
    assert got.state is v.State.UNKNOWN
    net.on(r"/c1/replies$", 500, {"error": {"message": "oops"}})
    got = await v.verify("instagram.reply_comment", REPLY_PARAMS, REPLY_RESULT)
    assert got.state is v.State.UNKNOWN
    got = await v.verify("instagram.reply_comment", REPLY_PARAMS, ActionResult(True))
    assert got.state is v.State.UNKNOWN


async def test_reply_comment_raises_and_reconcile(net, ig_env, monkeypatch):
    net.on(r"/c1/replies$", body={"data": [_reply()]})
    got = await v.reconcile_unknown("instagram.reply_comment", REPLY_PARAMS,
                                    {"at": T0})
    assert got.state is v.State.VERIFIED
    got = await v.reconcile_unknown("instagram.reply_comment", REPLY_PARAMS,
                                    {"at": T0 + timedelta(hours=1)})
    assert got.state is v.State.CONTRADICTED

    from atlas.modules.instagram import readback

    async def boom(*_a, **_kw):
        raise RuntimeError("parse error")

    monkeypatch.setattr(readback, "_get", boom)
    got = await v.verify("instagram.reply_comment", REPLY_PARAMS, REPLY_RESULT)
    assert got.state is v.State.UNKNOWN and "raised" in got.reason


# ================================================================== guardian

WRITE_SECRET = SECRET + "-write"


@pytest.fixture
def gd(monkeypatch, net):
    monkeypatch.setenv("GUARDIAN_API_TOKEN", SECRET)
    monkeypatch.setenv("GUARDIAN_WRITE_TOKEN", WRITE_SECRET)
    accounts = {SECRET: "acme", WRITE_SECRET: "acme"}

    def whoami(request):
        token = request.headers["Authorization"].removeprefix("Bearer ")
        return {"account": accounts[token], "scope": "read", "role": "owner"}

    net.on(r"/v1/whoami$", body=whoami)
    return accounts


AO_PARAMS = {"alert_key": "dns:beacon:1.2.3.4:evil.example", "op": "ack"}


async def test_guardian_alert_op_verified_and_contradicted(net, gd):
    net.on(r"/v1/alerts/", body={"status": "acked"})
    got = await v.verify("guardian.alert_op", AO_PARAMS, ActionResult(True))
    assert got.state is v.State.VERIFIED and got.evidence["account"] == "acme"
    assert "evil.example" not in json.dumps(got.as_audit())
    _no_secret(got)
    assert WRITE_SECRET not in json.dumps(got.as_audit())

    net.on(r"/v1/alerts/", body={"status": "open"})
    got = await v.verify("guardian.alert_op", AO_PARAMS, ActionResult(True))
    assert got.state is v.State.CONTRADICTED
    got = await v.reconcile_unknown("guardian.alert_op", AO_PARAMS, {"at": T0})
    assert got.state is v.State.CONTRADICTED


async def test_guardian_other_tenant_is_unknown_security(net, gd):
    gd[WRITE_SECRET] = "someone-else"
    net.on(r"/v1/alerts/", body={"status": "acked"})
    got = await v.verify("guardian.alert_op", AO_PARAMS, ActionResult(True))
    assert got.state is v.State.UNKNOWN
    assert got.failure_class is v.FailureClass.SECURITY
    assert not any("/v1/alerts/" in p for p in net.seen)


async def test_guardian_mark_untrusted_has_no_verifier():
    """Trust changes are suggest-only (Miro, 2026-09-28): nothing to verify."""
    assert not v.has_verifier("guardian.mark_untrusted")
    assert not v.has_reconciler("guardian.mark_untrusted")


async def test_guardian_rescan(net, gd):
    result = ActionResult(True, "queued", evidence={"job": "j1"})
    net.on(r"/v1/jobs/j1$", body={"id": "j1", "status": "running", "ok": None})
    got = await v.verify("guardian.rescan", {"network": "home"}, result)
    assert got.state is v.State.VERIFIED
    net.on(r"/v1/jobs/j1$", body={"id": "j1", "status": "failed", "ok": False})
    got = await v.verify("guardian.rescan", {"network": "home"}, result)
    assert got.state is v.State.CONTRADICTED
    net.on(r"/v1/jobs/j1$", body={"id": "j1", "status": "weird"})
    got = await v.verify("guardian.rescan", {"network": "home"}, result)
    assert got.state is v.State.UNKNOWN
    net.on(r"/v1/jobs/j1$", 404, {"detail": "job not found"})
    got = await v.verify("guardian.rescan", {"network": "home"}, result)
    assert got.state is v.State.CONTRADICTED
    got = await v.verify("guardian.rescan", {}, ActionResult(True))
    assert got.state is v.State.UNKNOWN


async def test_guardian_rescan_raises_is_unknown(net, gd, monkeypatch):
    def boom(_data):
        raise RuntimeError("hub parse error")

    monkeypatch.setattr("atlas.modules.guardian.incident.safe_job_id", boom)
    got = await v.verify("guardian.rescan", {"network": "home"},
                         ActionResult(True, evidence={"job": "j1"}))
    assert got.state is v.State.UNKNOWN and "raised" in got.reason


async def test_guardian_contain_can_never_have_happened(net):
    got = await v.verify("guardian.contain",
                         {"alert_id": "a", "target": "1.2.3.4", "kind": "block_ip"},
                         ActionResult(True))
    assert got.state is v.State.CONTRADICTED and net.seen == []
    got = await v.reconcile_unknown("guardian.contain", {"kind": "<script>"},
                                    {"at": T0})
    assert got.state is v.State.CONTRADICTED and got.evidence["kind"] is None


async def test_guardian_raises_is_unknown(gd, monkeypatch):
    from atlas.modules.guardian import readback

    async def boom():
        raise RuntimeError("hub parse error")

    monkeypatch.setattr(readback, "same_tenant", boom)
    got = await v.verify("guardian.alert_op", AO_PARAMS, ActionResult(True))
    assert got.state is v.State.UNKNOWN and "raised" in got.reason


# ================================================================== delegate

DG_RESULT = ActionResult(True, "queued", evidence={"handoff_id": 4},
                         rollback_token="4")


def _handoff(**over):
    return {"status": "queued", "queued_at": T0 + timedelta(seconds=1),
            "claimed_at": None, "claimed_by": None, "reported_at": None, **over}


async def test_delegate_verified_contradicted_unknown(db):
    db["handoffs"] = _handoff()
    got = await v.verify("delegate.claude_code", {"handoff_id": "4"}, DG_RESULT)
    assert got.state is v.State.VERIFIED
    db["handoffs"] = _handoff(status="cancelled")
    got = await v.verify("delegate.claude_code", {"handoff_id": "4"}, DG_RESULT)
    assert got.state is v.State.CONTRADICTED
    db["handoffs"] = None
    got = await v.verify("delegate.claude_code", {"handoff_id": "4"}, DG_RESULT)
    assert got.state is v.State.CONTRADICTED
    got = await v.verify("delegate.claude_code", {}, ActionResult(True))
    assert got.state is v.State.UNKNOWN


async def test_delegate_reconcile_and_raises(db, monkeypatch):
    db["handoffs"] = _handoff(status="claimed", claimed_by="mac")
    got = await v.reconcile_unknown("delegate.claude_code", {"handoff_id": "4"},
                                    {"at": T0})
    assert got.state is v.State.VERIFIED
    db["handoffs"] = _handoff(status="draft", queued_at=None)
    got = await v.reconcile_unknown("delegate.claude_code", {"handoff_id": "4"},
                                    {"at": T0})
    assert got.state is v.State.CONTRADICTED

    async def boom(*_a, **_kw):
        raise RuntimeError("db gone")

    monkeypatch.setattr(state, "fetch_one", boom)
    got = await v.verify("delegate.claude_code", {"handoff_id": "4"}, DG_RESULT)
    assert got.state is v.State.UNKNOWN and "raised" in got.reason


# ================================================================== agency outreach

AG_RESULT = ActionResult(True, "sent outreach 7 to owner@example.com",
                         evidence={"message_id": "<abc@send.example>"})


@pytest.fixture
def ag(monkeypatch, db):
    monkeypatch.setenv("AGENCY_SMTP_PASSWORD", SECRET)
    db["agency_outreach"] = {"id": 7, "to_address": "owner@example.com",
                             "subject": "Your site", "status": "sent", "sent_at": T0}
    return db


def _sent(monkeypatch, found):
    from atlas.modules.agency import readback
    calls = []

    async def search_sent(**kw):
        calls.append(kw)
        if isinstance(found, BaseException):
            raise found
        return found

    monkeypatch.setattr(readback, "search_sent", search_sent)
    return calls


async def test_agency_verified(ag, monkeypatch):
    calls = _sent(monkeypatch, [{"message-id": "<abc@send.example>",
                                 "subject": "Your site"}])
    got = await v.verify("agency.send_outreach", {"outreach_id": 7}, AG_RESULT)
    assert got.state is v.State.VERIFIED and got.evidence["in_sent_folder"] is True
    assert calls == [{"message_id": "<abc@send.example>"}]
    _no_secret(got)


async def test_agency_contradicted_on_a_bounce(ag, monkeypatch):
    ag["agency_outreach"]["status"] = "bounced"
    _sent(monkeypatch, [])
    got = await v.verify("agency.send_outreach", {"outreach_id": 7}, AG_RESULT)
    assert got.state is v.State.CONTRADICTED


async def test_agency_unknown(ag, monkeypatch):
    from atlas.modules.agency import readback
    monkeypatch.delenv("AGENCY_IMAP_HOST", raising=False)
    got = await v.verify("agency.send_outreach", {"outreach_id": 7}, AG_RESULT)
    assert got.state is v.State.UNKNOWN
    assert got.failure_class is v.FailureClass.DISABLED and readback.NO_IMAP in got.reason

    _sent(monkeypatch, [])          # not in Sent: a relay need not keep a copy
    got = await v.verify("agency.send_outreach", {"outreach_id": 7}, AG_RESULT)
    assert got.state is v.State.UNKNOWN
    got = await v.verify("agency.send_outreach", {"outreach_id": 7}, ActionResult(True))
    assert got.state is v.State.UNKNOWN


async def test_agency_raises_and_reconcile(ag, monkeypatch):
    calls = _sent(monkeypatch, [{"message-id": "<x@y>", "subject": "Your site"}])
    got = await v.reconcile_unknown("agency.send_outreach", {"outreach_id": 7},
                                    {"at": T0})
    assert got.state is v.State.VERIFIED
    assert calls[-1] == {"to": "owner@example.com", "since": T0}

    _sent(monkeypatch, OSError(f"imap login failed for {SECRET}"))
    got = await v.verify("agency.send_outreach", {"outreach_id": 7}, AG_RESULT)
    assert got.state is v.State.UNKNOWN and "raised" in got.reason
    _no_secret(got)


def test_agency_imap_read_is_read_only(monkeypatch):
    """EXAMINE (readonly select), SEARCH, and BODY.PEEK only -- no STORE,
    no plain BODY[] fetch that would set \\Seen."""
    from atlas.modules.agency import readback
    seen = []

    class Conn:
        def __init__(self, *a, **kw):
            pass

        def login(self, *a):
            seen.append("login")

        def select(self, folder, readonly=False):
            assert readonly is True
            seen.append(("select", folder))
            return "OK", [b"1"]

        def search(self, charset, *criteria):
            seen.append(("search",) + criteria)
            return "OK", [b"3"]

        def fetch(self, num, what):
            assert "PEEK" in what
            head = b"Message-ID: <abc@send.example>\r\nSubject: Your site\r\n"
            return "OK", [(b"3", head)]

        def logout(self):
            seen.append("logout")

        def __getattr__(self, name):
            raise AssertionError(f"IMAP {name} is not a read")

    import imaplib
    monkeypatch.setattr(imaplib, "IMAP4_SSL", Conn)
    got = readback._search_sent(("imap.test", 993, "u", "p", "Sent"),
                                message_id="<abc@send.example>")
    assert got == [{"message-id": "<abc@send.example>", "subject": "Your site"}]
    assert ("select", '"Sent"') in seen and seen[-1] == "logout"


async def test_send_outreach_stamps_and_records_a_message_id(monkeypatch):
    from atlas.modules.agency import _store, outreach
    sent = {}

    async def smtp(from_address, to_address, subject, body, message_id=None):
        sent["message_id"] = message_id
        return True, None

    async def guarded(oid):
        return {"id": 7, "lead_id": 1, "kind": "email", "to_address": "o@x.example",
                "subject": "s", "body": "hi " + outreach.TRAILER}, None

    async def nothing(*_a, **_kw):
        return None

    monkeypatch.setenv("AGENCY_SEND_FROM", "out@send.example")
    monkeypatch.setattr(outreach, "_guarded", guarded)
    monkeypatch.setattr(outreach, "_smtp_send", smtp)
    monkeypatch.setattr(outreach, "_lead_features_for", nothing)
    monkeypatch.setattr(_store, "mark_outreach_sent", nothing)
    got = await outreach.send_outreach(7)
    assert got.ok
    assert sent["message_id"].endswith("@send.example>")
    assert got.evidence["message_id"] == sent["message_id"]
