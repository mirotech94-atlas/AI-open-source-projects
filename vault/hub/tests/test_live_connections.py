"""Live connections: the flows the sensor reports, streamed as they land.

Miro asked for a tab showing "all the packets Wireshark is watching, live".
Vault never stores or forwards a packet body (CLAUDE.md, `payloads/traffic`),
and Miro chose to keep that: this streams CONNECTIONS -- device, destination,
port, protocol, bytes, and the TLS server name when the sensor saw one -- the
same fields a stored `Flow` row already holds, nothing more.

Opt-in per socket like the DNS stream (`{"subscribe": ["flow"]}`), because a
busy mirror port produces far more rows than a dashboard that is not showing
them should be sent, and replayed on subscribe so the tab is not empty until
the sensor's next minute file.
"""
import pytest

from guardian_hub import schemas_dash as sd
from guardian_hub.routers import live as live_router
from tests.test_seed_helpers import enrol_as_tenant_admin, seeded_demo


@pytest.fixture(autouse=True)
def _fast_pings(monkeypatch):
    # Every loop below is bounded by frame count, and a socket with nothing
    # to say still sends a ping every PING_INTERVAL_S -- so a missing frame
    # fails the test in a fraction of a second instead of blocking forever.
    monkeypatch.setattr(live_router, "PING_INTERVAL_S", 0.02)


def _flow(peer="203.0.113.7", port=443, sni="example.org"):
    return {"ts": "2026-09-27T17:31:00-04:00", "mac": "aa:bb:cc:dd:ee:01", "ip": "192.168.1.20",
            "peer_ip": peer, "peer_port": port, "proto": "tcp", "bytes_in": 5000, "bytes_out": 700,
            "pkts_in": 9, "pkts_out": 7, "peer_name": "", "hints": {"sni": sni}}


def test_a_new_flow_reaches_a_subscribed_socket_as_a_connection(client):
    seeded_demo(client)
    with client.websocket_connect("/api/v1/ws") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"subscribe": ["flow"]})
        # Drain the replay: it ends before any live frame can arrive.
        H = {"Authorization": f"Bearer {enrol_as_tenant_admin(client)}"}
        r = client.post("/api/v1/sensor/ingest", headers=H, json={
            "last": {}, "state": {}, "events": [], "history": [], "fixes": [],
            "flows": [_flow()]})
        assert r.status_code == 200, r.text
        for _ in range(200):
            frame = ws.receive_json()
            if frame["type"] == "flow" and frame["data"]["peer_ip"] == "203.0.113.7":
                break
        else:
            raise AssertionError("the ingested flow never reached the subscriber")
        data = frame["data"]
        assert set(data) >= set(sd.ConnStreamRow.model_fields)
        assert data["peer"] == "example.org"          # the name, when the sensor saw one
        assert data["peer_port"] == 443 and data["proto"] == "tcp"
        assert data["bytes_in"] == 5000 and data["bytes_out"] == 700


def test_flow_frames_are_opt_in(client):
    seeded_demo(client)
    with client.websocket_connect("/api/v1/ws") as ws:
        assert ws.receive_json()["type"] == "ready"
        H = {"Authorization": f"Bearer {enrol_as_tenant_admin(client)}"}
        client.post("/api/v1/sensor/ingest", headers=H, json={
            "last": {}, "state": {}, "events": [], "history": [], "fixes": [],
            "flows": [_flow(peer="198.51.100.9")]})
        seen = []
        for _ in range(40):
            frame = ws.receive_json()
            seen.append(frame["type"])
        assert "flow" not in seen, seen


def test_subscribing_replays_recent_connections(client):
    seeded_demo(client)  # the demo seeds flows
    with client.websocket_connect("/api/v1/ws") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"subscribe": ["flow"]})
        frames = [ws.receive_json() for _ in range(5)]
        flows = [f for f in frames if f["type"] == "flow"]
        assert flows, [f["type"] for f in frames]
        assert flows[0]["data"]["peer"]


def test_a_frame_never_carries_more_than_a_stored_flow_does():
    """No payload bytes, ever: every field is one a `Flow` row already has."""
    allowed = {"ts", "mac", "ip", "device", "peer", "peer_ip", "peer_port", "proto",
               "bytes_in", "bytes_out", "network"}
    assert set(sd.ConnStreamRow.model_fields) <= allowed
