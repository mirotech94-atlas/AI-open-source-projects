"""The WhatsApp linked-device bridge: routing, honesty, notify_owner, no leaks.

A fake bridge stands in for `wa-bridge` (the Node service has its own tests in
whatsapp-bridge/test/). Nothing here touches the network or a database.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from atlas.kernel import executor
from atlas.kernel.contracts import Health
from atlas.kernel.permissions import classify
from atlas.kernel.registry import REGISTRY
from atlas.modules.agency import contact, owner_wa, wa_bridge

from test_agency_contact import GOOD, SECRET_TOKEN, FakeStore

SECRET = "bridge-secret-value-0123456789"
OWNER = "+1 555 010 0100"


class FakeBridge:
    def __init__(self) -> None:
        self.reachable = True
        self.connected = True
        self.owner_match = True
        self.sent: list[tuple[str, str]] = []
        self.acks: dict[str, str] = {}

    async def status(self) -> wa_bridge.BridgeStatus:
        if not self.reachable:
            return wa_bridge.BridgeStatus(True, False, False, False,
                                          reason="the WhatsApp bridge is unreachable (x)")
        reason = "" if self.connected else "WhatsApp is not linked or not connected (scan the QR)"
        return wa_bridge.BridgeStatus(True, True, self.connected, self.owner_match,
                                      "13412033334", reason)

    async def send(self, to: str, text: str) -> tuple[bool, str]:
        self.sent.append((to, text))
        return True, f"wa:ID{len(self.sent)}"

    async def message_status(self, sid: str) -> str:
        return self.acks.get(sid, "unknown")


class FakeOwnerStore:
    def __init__(self) -> None:
        self.rows: dict[int, dict[str, Any]] = {}

    async def insert(self, kind, text, status, now):
        key = owner_wa.dedupe_key(kind, text)
        if any(r["dedupe_key"] == key and r["created_at"] > now - owner_wa.DEDUPE_FOR
               for r in self.rows.values()):
            return None
        i = len(self.rows) + 1
        self.rows[i] = {"id": i, "kind": kind, "text": text, "dedupe_key": key,
                        "status": status, "attempts": 0, "created_at": now,
                        "updated_at": now, "sent_at": None}
        return i

    async def peek(self, i):
        return dict(self.rows[i]) if i in self.rows else None

    async def waiting(self, limit, now):
        return [i for i, r in self.rows.items() if r["status"] == "pending"][:limit]

    async def claim(self, i, now):
        r = self.rows[i]
        if r["status"] not in ("pending", "sending"):
            return None
        r.update(status="sending", attempts=r["attempts"] + 1)
        return dict(r)

    async def sent(self, i, wa_id, now):
        self.rows[i].update(status="sent", wa_id=wa_id, sent_at=now)

    async def failed(self, i, reason, now):
        r = self.rows[i]
        r.update(status="failed" if r["attempts"] >= owner_wa.MAX_ATTEMPTS else "pending")

    async def sent_in_last_hour(self, now):
        return sum(1 for r in self.rows.values()
                   if r["sent_at"] and r["sent_at"] > now - timedelta(hours=1))

    async def digest_rows(self):
        return [dict(r) for r in self.rows.values() if r["status"] == "digest"]

    async def fold(self, ids, now):
        for i in ids:
            self.rows[i]["status"] = "folded"


NOON_NY = datetime(2026, 11, 16, 17, 0, tzinfo=UTC)        # 12:00 ET
NIGHT_NY = datetime(2026, 11, 16, 5, 0, tzinfo=UTC)        # 00:00 ET


@pytest.fixture
def bridge(monkeypatch):
    fake = FakeBridge()
    monkeypatch.setenv(wa_bridge.SECRET_ENV, SECRET)
    for name in ("status", "send", "message_status"):
        monkeypatch.setattr(wa_bridge, name, getattr(fake, name))
    monkeypatch.delenv(contact.TWILIO_JOINED_ENV, raising=False)
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("MMT_WHATSAPP_FROM", raising=False)
    monkeypatch.delenv("TWILIO_FROM", raising=False)
    monkeypatch.delenv(contact.OWNER_WA_ENV, raising=False)
    monkeypatch.delenv(contact.WA_CAP_ENV, raising=False)
    monkeypatch.setenv(contact.WA_ENABLE_ENV, "1")      # these tests cover WhatsApp
    return fake


@pytest.fixture
def stores(bridge):
    cs, ow = FakeStore(), FakeOwnerStore()
    old_c, old_o = contact.use_store(cs), owner_wa.use_store(ow)
    yield cs, ow
    contact.use_store(old_c)
    owner_wa.use_store(old_o)


@pytest.fixture
def installed(stores, monkeypatch, memory_backend):
    REGISTRY.actions.clear()
    REGISTRY.decisions.clear()
    executor._recent.clear()
    for a in contact.actions() + owner_wa.actions():
        REGISTRY.actions[a.name] = a
        REGISTRY.decisions[a.name] = classify(a)
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    monkeypatch.setattr(owner_wa, "_now", lambda: NOON_NY)
    yield
    REGISTRY.actions.clear()
    REGISTRY.decisions.clear()
    executor._recent.clear()


async def _row(cs):
    cid = await contact.submit(GOOD | {"topic": "preview", "source": "landing"}, "h", now=NOON_NY)
    return cid


# ------------------------------------------------------------------ the client

_REAL_CLIENT = httpx.AsyncClient


def _mock(handler, monkeypatch):
    monkeypatch.setattr(wa_bridge.httpx, "AsyncClient",
                        lambda **kw: _REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))


async def test_client_sends_the_secret_header_and_owner_only(monkeypatch):
    monkeypatch.setenv(wa_bridge.SECRET_ENV, SECRET)
    seen: list[httpx.Request] = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"ok": True, "id": "ABC"})
    _mock(handler, monkeypatch)
    assert await wa_bridge.send(OWNER, "hi") == (True, "wa:ABC")
    assert seen[0].headers["x-bridge-secret"] == SECRET
    ok, why = await wa_bridge.send("+19175550100", "hi")
    assert not ok and "owner" in why and len(seen) == 1       # never even asked


async def test_client_status_cases(monkeypatch):
    monkeypatch.setenv(wa_bridge.SECRET_ENV, SECRET)
    _mock(lambda r: httpx.Response(200, json={"connected": True, "owner_match": True,
                                              "number": "13412033334"}), monkeypatch)
    assert (await wa_bridge.status()).usable
    _mock(lambda r: httpx.Response(200, json={"connected": False}), monkeypatch)
    st = await wa_bridge.status()
    assert st.reachable and not st.usable and "scan" in st.reason
    _mock(lambda r: httpx.Response(200, json={"connected": True, "owner_match": False}),
          monkeypatch)
    assert not (await wa_bridge.status()).usable
    _mock(lambda r: httpx.Response(401, json={}), monkeypatch)
    assert "secret" in (await wa_bridge.status()).reason

    def boom(req):
        raise httpx.ConnectError("no route")
    _mock(boom, monkeypatch)
    st = await wa_bridge.status()
    assert not st.reachable and not st.usable


async def test_the_secret_never_reaches_a_log_or_a_reason(monkeypatch, caplog):
    monkeypatch.setenv(wa_bridge.SECRET_ENV, SECRET)

    def boom(req):
        raise httpx.ConnectError(f"cannot reach with {req.headers['x-bridge-secret']}")
    _mock(boom, monkeypatch)
    with caplog.at_level(logging.DEBUG):
        ok, why = await wa_bridge.send(OWNER, "hi")
        st = await wa_bridge.status()
    assert not ok and SECRET not in why and SECRET not in st.reason
    assert SECRET not in caplog.text


# ------------------------------------------------------------------ contact routing

async def test_connected_bridge_carries_the_contact_message(installed, stores, bridge):
    cs, _ = stores
    cid = await _row(cs)
    res = await executor.execute(executor.ExecutionRequest(
        contact.ACTION, {"contact_id": cid, "channel": "whatsapp"}, reason="t"))
    assert res.ok
    assert bridge.sent and bridge.sent[0][1].startswith("[Contact] New website message")
    row = cs.rows[cid]
    assert row["whatsapp_status"] == "sent" and row["whatsapp_sid"] == "wa:ID1"


async def test_disconnected_is_refused_not_sent_and_costs_no_attempt(installed, stores, bridge):
    cs, _ = stores
    bridge.connected = False
    cid = await _row(cs)
    res = await contact.deliver(cid, "whatsapp")
    assert not res.ok and "scan" in res.detail
    assert bridge.sent == []
    assert cs.rows[cid]["whatsapp_status"] == "pending" and cs.rows[cid]["whatsapp_attempts"] == 0


async def test_wrong_account_is_not_a_route(installed, stores, bridge):
    bridge.owner_match = False
    assert (await contact.wa_route())[0] is None


async def test_unreachable_falls_back_to_twilio_only_when_the_sandbox_is_joined(
        installed, stores, bridge, monkeypatch):
    cs, _ = stores
    bridge.reachable = False
    sent: list[str] = []

    async def twilio(to, body, media_url=None):
        sent.append(body)
        return True, "SM" + "b" * 32
    monkeypatch.setattr("atlas.modules.mmt.notify.send_whatsapp", twilio)
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", SECRET_TOKEN)
    monkeypatch.setenv("MMT_WHATSAPP_FROM", "whatsapp:+14155550000")
    cid = await _row(cs)
    # configured but not joined: failed honestly, nothing sent
    res = await contact.deliver(cid, "whatsapp")
    assert not res.ok and "unreachable" in res.detail and sent == []
    # joined: Twilio carries it
    monkeypatch.setenv(contact.TWILIO_JOINED_ENV, "1")
    res = await contact.deliver(cid, "whatsapp")
    assert res.ok and len(sent) == 1 and cs.rows[cid]["whatsapp_sid"].startswith("SM")
    assert bridge.sent == []


async def test_reachable_but_unlinked_never_falls_back(installed, stores, bridge, monkeypatch):
    bridge.connected = False
    monkeypatch.setenv(contact.TWILIO_JOINED_ENV, "1")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", SECRET_TOKEN)
    monkeypatch.setenv("MMT_WHATSAPP_FROM", "whatsapp:+14155550000")
    assert (await contact.wa_route())[0] is None


async def test_delivery_is_confirmed_from_the_bridge_ack(installed, stores, bridge):
    cs, _ = stores
    cid = await _row(cs)
    await contact.deliver(cid, "whatsapp")
    assert await contact.poll_whatsapp() == 0                  # unknown: still "sent"
    assert cs.rows[cid]["whatsapp_status"] == "sent"
    bridge.acks["wa:ID1"] = "delivered"
    assert await contact.poll_whatsapp() == 1
    assert cs.rows[cid]["whatsapp_status"] == "delivered"


async def test_health_is_unknown_when_the_bridge_is_not_connected(installed, stores, bridge,
                                                                  monkeypatch):
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "gmailapppass1234")
    monkeypatch.setattr("atlas.channels.slack.notice_problem", lambda topic: None)
    ok = await contact.check_contact_delivery()
    assert ok.health != Health.UNKNOWN or "email" in ok.detail   # email config may be absent
    bridge.connected = False
    obs = await contact.check_contact_delivery()
    assert obs.health == Health.UNKNOWN and "WhatsApp" in obs.detail


async def test_the_shared_cap_counts_portal_messages_too(installed, stores, bridge, monkeypatch):
    cs, ow = stores
    monkeypatch.setenv(contact.WA_CAP_ENV, "2")
    ow.rows[1] = {"id": 1, "kind": "portal", "text": "x", "dedupe_key": "k", "status": "sent",
                  "attempts": 1, "created_at": NOON_NY, "updated_at": NOON_NY,
                  "sent_at": datetime.now(UTC)}
    ow.rows[2] = dict(ow.rows[1], id=2, dedupe_key="k2")
    cid = await _row(cs)
    res = await contact.deliver(cid, "whatsapp")
    assert not res.ok and "cap" in res.detail and bridge.sent == []


# ------------------------------------------------------------------ notify_owner

async def test_notify_owner_sends_tagged_through_the_executor(installed, stores, bridge):
    _, ow = stores
    assert await owner_wa.notify_owner("portal", "Acme paid $500 (Stripe)")
    assert bridge.sent == [(OWNER, "[Portal] Acme paid $500 (Stripe)")]
    assert ow.rows[1]["status"] == "sent"


async def test_it_is_tier_one_and_owner_only(installed):
    act = REGISTRY.actions[owner_wa.ACTION]
    assert act.tier.name == "AUTO" and "to" not in act.params and "recipient" not in act.params


async def test_identical_text_is_sent_once_per_hour(installed, stores, bridge):
    await owner_wa.notify_owner("alert", "same thing")
    assert await owner_wa.notify_owner("alert", "same thing") is None
    assert len(bridge.sent) == 1


async def test_quiet_hours_hold_everything_but_alerts(installed, stores, bridge, monkeypatch):
    _, ow = stores
    monkeypatch.setattr(owner_wa, "_now", lambda: NIGHT_NY)
    await owner_wa.notify_owner("portal", "a lead wrote")
    await owner_wa.notify_owner("alert", "the contact notice failed")
    assert [t for _, t in bridge.sent] == ["[Alert] the contact notice failed"]
    assert ow.rows[1]["status"] == "pending"
    monkeypatch.setattr(owner_wa, "_now", lambda: NOON_NY)      # morning: the drain sends it
    await owner_wa.drain()
    assert ow.rows[1]["status"] == "sent"


async def test_low_value_items_are_folded_into_one_message(installed, stores, bridge,
                                                           monkeypatch):
    for i in range(owner_wa.DIGEST_COUNT):
        await owner_wa.notify_owner("portal_digest", f"item {i}")
    assert bridge.sent == []                                   # nothing one by one
    await owner_wa.drain()
    assert len(bridge.sent) == 1
    text = bridge.sent[0][1]
    assert text.startswith("[Portal] Digest of 8 updates") and "item 0" in text and "item 7" in text


async def test_a_few_old_digest_items_flush_after_an_hour(installed, stores, bridge,
                                                          monkeypatch):
    await owner_wa.notify_owner("portal_digest", "one")
    await owner_wa.drain()
    assert bridge.sent == []
    monkeypatch.setattr(owner_wa, "_now", lambda: NOON_NY + timedelta(hours=1, minutes=1))
    await owner_wa.drain()
    assert len(bridge.sent) == 1


async def test_no_route_keeps_the_row_waiting(installed, stores, bridge):
    _, ow = stores
    bridge.connected = False
    assert await owner_wa.notify_owner("portal", "a payment")
    assert bridge.sent == [] and ow.rows[1]["status"] == "pending" and ow.rows[1]["attempts"] == 0


async def test_outside_text_is_defanged_and_capped(installed, stores, bridge):
    await owner_wa.notify_owner("portal", "Lead https://evil.example/x said hi " + "x" * 2000)
    text = bridge.sent[0][1]
    assert "https://" not in text and len(text) < 700


async def test_bad_input_never_raises(installed, stores, bridge):
    assert await owner_wa.notify_owner("nope", "x") is None
    assert await owner_wa.notify_owner("portal", "   ") is None
    assert await owner_wa.notify_owner("portal", None) is None      # type: ignore[arg-type]


async def test_a_contact_channel_that_gives_up_raises_an_alert(installed, stores, bridge,
                                                              monkeypatch):
    cs, _ = stores
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "gmailapppass1234")

    async def bad(message, host, port, user, password):
        return False, "smtp down"
    monkeypatch.setattr("atlas.modules.agency.outreach._deliver", bad)
    cid = await _row(cs)
    for _ in range(contact.MAX_ATTEMPTS):
        await contact.deliver(cid, "email")
    texts = [t for _, t in bridge.sent]
    assert any(t.startswith("[Alert]") and f"#{cid}" in t and "email" in t for t in texts)
    assert GOOD["message"] not in " ".join(texts)                   # never the visitor's text
