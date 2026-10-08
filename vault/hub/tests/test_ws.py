import pytest
from starlette.websockets import WebSocketDisconnect

from tests.test_seed_helpers import seeded_demo


def test_ws_streams_writes_and_gates_on_the_session(client):
    seeded_demo(client)
    with client.websocket_connect("/api/v1/ws") as ws:
        assert ws.receive_json()["type"] == "ready"
        key = client.get("/api/v1/inbox?view=alerts").json()["rows"][0]["id"]
        client.post(f"/api/v1/alerts/{key}/ack")
        frame = ws.receive_json()
        assert frame["type"] == "alert" and frame["data"]["status"] == "acked"


def test_ws_rejects_a_signed_out_client(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/v1/ws") as ws:
            ws.receive_json()


def test_ws_closes_with_tenant_changed_when_the_session_switches_tenant(client, monkeypatch):
    from guardian_hub.routers import live as live_router
    from guardian_hub.db import SessionLocal
    from guardian_hub import models as m

    # Shorten the ping ticker so the socket's per-iteration tenant re-check
    # (which piggybacks on every ping tick / client message) fires quickly
    # instead of waiting up to the real 25s cadence.
    monkeypatch.setattr(live_router, "PING_INTERVAL_S", 0.05)

    tid1 = seeded_demo(client)
    db = SessionLocal()
    u = db.query(m.User).filter_by(username="demoadmin").first()
    t2 = m.Tenant(slug="second-tenant", name="Second Tenant")
    db.add(t2)
    db.flush()
    db.add(m.Membership(user_id=u.id, tenant_id=t2.id, role="admin"))
    db.commit()
    tid2 = t2.id
    db.close()

    with client.websocket_connect("/api/v1/ws") as ws:
        assert ws.receive_json()["type"] == "ready"

        # An event on the ORIGINAL (bound) tenant is still delivered right
        # up until the switch is observed.
        r = client.post("/api/v1/me/tenant", json={"tenant_id": tid2})
        assert r.status_code == 200, r.text

        with pytest.raises(WebSocketDisconnect) as exc:
            while True:
                frame = ws.receive_json()
                assert frame["type"] in ("ping",), frame  # nothing else should still be arriving
        assert exc.value.code == live_router.TENANT_CHANGED_CODE

    # A fresh connection now authenticates against the session's NEW tenant.
    with client.websocket_connect("/api/v1/ws") as ws2:
        ready = ws2.receive_json()
        assert ready["type"] == "ready" and ready["data"]["tenant"] == "second-tenant"


def test_ping_fires_on_a_fixed_cadence_even_under_client_traffic(client, monkeypatch):
    from guardian_hub.routers import live as live_router

    monkeypatch.setattr(live_router, "PING_INTERVAL_S", 0.05)
    seeded_demo(client)

    with client.websocket_connect("/api/v1/ws") as ws:
        assert ws.receive_json()["type"] == "ready"
        pings = 0
        for _ in range(60):
            # A chatty client (one frame per loop) must not starve the ping
            # ticker -- it runs on its own task, not only "when idle".
            ws.send_json({"type": "pong"})
            frame = ws.receive_json()
            if frame["type"] == "ping":
                pings += 1
                if pings >= 3:
                    break
        assert pings >= 3


def test_dns_subscribe_replays_recent_seeded_queries(client):
    # The demo tenant has no live sensor pushing `dns` frames -- without a
    # replay, the DNS tab's Live query stream would sit empty until a real
    # query arrives. Subscribing should immediately get some of the 3000
    # seeded `DnsQuery` rows back as synthetic `dns` frames.
    seeded_demo(client)
    with client.websocket_connect("/api/v1/ws") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"subscribe": ["dns"]})
        frame = ws.receive_json()
        assert frame["type"] == "dns"
        assert frame["data"]["name"] != ""
        assert frame["data"]["qtype"] != ""

    # Re-subscribing on a fresh connection must not depend on the first
    # socket having done anything -- each connection replays independently.
    with client.websocket_connect("/api/v1/ws") as ws2:
        assert ws2.receive_json()["type"] == "ready"
        ws2.send_json({"subscribe": ["dns"]})
        assert ws2.receive_json()["type"] == "dns"


def test_dns_replay_only_fires_once_per_subscribe_transition(client, monkeypatch):
    from guardian_hub.routers import live as live_router

    monkeypatch.setattr(live_router, "PING_INTERVAL_S", 0.05)
    seeded_demo(client)
    with client.websocket_connect("/api/v1/ws") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"subscribe": ["dns"]})
        replayed = 0
        while True:
            frame = ws.receive_json()
            if frame["type"] == "dns":
                replayed += 1
            elif frame["type"] == "ping":
                break
        assert replayed == live_router.DNS_REPLAY_LIMIT

        # Sending the exact same subscribe list again is a no-op transition
        # (already subscribed) -- it must not replay a second burst.
        ws.send_json({"subscribe": ["dns"]})
        frame = ws.receive_json()
        assert frame["type"] == "ping"


def test_queue_status_is_pollable(client):
    seeded_demo(client)
    qid = client.post("/api/v1/fix", json={"host": "prod", "action": "security_updates"}).json()["id"]
    body = client.get(f"/api/v1/queue/{qid}").json()
    assert body["id"] == qid and body["status"] == "queued" and body["path"] == "/api/fix"


def test_a_client_that_never_answers_pings_is_not_disconnected(client, monkeypatch):
    """The dashboard shipped without a pong reply, and a browser that loaded
    that bundle keeps running it until it reloads. The hub used to close any
    socket that had not ponged within PONG_TIMEOUT_S, so every such browser
    was cut off and reconnected every ~77 s all day, losing whatever was
    published in each gap. The docstring always said nothing the client sends
    is required; now that is true."""
    from guardian_hub.routers import live as live_router

    monkeypatch.setattr(live_router, "PING_INTERVAL_S", 0.02)
    monkeypatch.setattr(live_router, "PONG_TIMEOUT_S", 0.05)
    seeded_demo(client)
    with client.websocket_connect("/api/v1/ws") as ws:
        assert ws.receive_json()["type"] == "ready"
        # Ten pings is 0.2 s -- four times the timeout -- with no pong sent.
        for _ in range(10):
            assert ws.receive_json()["type"] == "ping"


def test_a_client_that_answered_and_then_went_silent_is_disconnected(client, monkeypatch):
    """Dead-connection detection still works for a client that speaks the
    heartbeat: once it has ponged, silence past the timeout means it is gone."""
    from guardian_hub.routers import live as live_router

    monkeypatch.setattr(live_router, "PING_INTERVAL_S", 0.02)
    monkeypatch.setattr(live_router, "PONG_TIMEOUT_S", 0.05)
    seeded_demo(client)
    with client.websocket_connect("/api/v1/ws") as ws:
        assert ws.receive_json()["type"] == "ready"
        assert ws.receive_json()["type"] == "ping"
        ws.send_json({"type": "pong"})
        with pytest.raises(WebSocketDisconnect):
            for _ in range(50):
                ws.receive_json()
