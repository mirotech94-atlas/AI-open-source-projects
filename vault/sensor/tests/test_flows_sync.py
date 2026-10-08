# sensor/tests/test_flows_sync.py
"""Flows leave the sensor the way dns_queries do: per-day jsonl, offset in
state["hub_sent"], advanced only after a successful push."""
import json, os, datetime as dt
import urllib.error
from guardian_sensor import sync

def _client(monkeypatch, tmp_path, responses):
    monkeypatch.setattr(sync.paths, "p", lambda n: str(tmp_path / n))
    (tmp_path / "flows").mkdir(); (tmp_path / "dns").mkdir()
    sent = []
    def fake_try(self, method, path, body=None):
        sent.append(body)
        r = responses.pop(0)
        if isinstance(r, Exception): raise r
        return r
    monkeypatch.setattr(sync.HubClient, "_try", fake_try)
    return sync.HubClient("http://hub", "tok"), sent

def _write_flows(tmp_path, n):
    name = "flows-" + dt.date.today().isoformat() + ".jsonl"
    with open(tmp_path / "flows" / name, "w") as f:
        for i in range(n):
            f.write(json.dumps({"ts": "2026-09-20T10:00:00+00:00", "mac": f"aa:bb:cc:00:00:{i:02x}",
                                "ip": f"192.168.1.{i+10}", "peer_ip": "1.1.1.1", "peer_port": 443,
                                "proto": "tcp", "bytes_in": 1, "bytes_out": 1, "pkts_in": 1, "pkts_out": 1,
                                "hints": {}, "source": "mirror"}) + "\n")

def test_flows_ride_the_push_and_the_offset_advances_only_on_success(monkeypatch, tmp_path):
    # _client() first: it creates the flows/ and dns/ dirs that _write_flows needs.
    client, sent = _client(monkeypatch, tmp_path, [RuntimeError("down"), {"ok": True}])
    _write_flows(tmp_path, 3)
    state = {}
    payload = {"version": "t", "last": {"lan": {}, "networks": []}, "state": state}
    assert client.push(payload) is None
    assert "flows" not in (state.get("hub_sent") or {})          # failed: nothing advanced
    assert client.push(payload)["ok"] and len(sent[1]["flows"]) == 3
    assert state["hub_sent"]["flows"]["off"] == 3                  # succeeded: advanced

def test_flows_are_attributed_to_a_network_by_ip(monkeypatch, tmp_path):
    # _client() first: it creates the flows/ and dns/ dirs that _write_flows needs.
    client, sent = _client(monkeypatch, tmp_path, [{"ok": True}])
    _write_flows(tmp_path, 1)
    payload = {"version": "t", "state": {}, "last": {"lan": {},
               "networks": [{"slug": "main", "subnet_prefix": "192.168.1", "primary": True}]}}
    client.push(payload)
    assert sent[0]["flows"][0]["net"] == "main"

def test_413_halves_the_flow_cap_and_never_drops_the_scan(monkeypatch, tmp_path):
    # A first attempt too big for the hub (413) must not lose the scan: the
    # retry keeps the rest of the payload and only shrinks the flows, and the
    # offset advances by exactly what the SECOND (accepted) body actually sent.
    #
    # A small FLOW_MAX_PER_PUSH keeps the fixture cheap (hundreds, not
    # thousands, of jsonl lines) while still landing the halved cap above the
    # 500-row floor, so the second attempt is observably smaller than the
    # first rather than both being clipped to the same floor value.
    monkeypatch.setattr(sync.paths, "p", lambda n: str(tmp_path / n))
    (tmp_path / "flows").mkdir(); (tmp_path / "dns").mkdir()
    monkeypatch.setattr(sync, "FLOW_MAX_PER_PUSH", 1000)
    responses = [urllib.error.HTTPError("http://hub", 413, "too big", {}, None), {"ok": True}]
    sent = []
    def fake_try(self, method, path, body=None):
        # A snapshot copy, not the live `body` reference: the production
        # 413 handler reassigns `body["flows"]` on the SAME dict and resends
        # it, so appending `body` itself would leave `sent[0]` and `sent[1]`
        # aliased to one object and both showing the post-halving state.
        sent.append(dict(body))
        r = responses.pop(0)
        if isinstance(r, Exception): raise r
        return r
    monkeypatch.setattr(sync.HubClient, "_try", fake_try)
    client = sync.HubClient("http://hub", "tok")
    _write_flows(tmp_path, 600)
    state = {}
    payload = {"version": "t", "last": {"lan": {}, "networks": []}, "state": state}
    resp = client.push(payload)
    assert resp == {"ok": True}
    assert len(sent) == 2
    assert len(sent[1]["flows"]) < len(sent[0]["flows"])
    assert state["hub_sent"]["flows"]["off"] == len(sent[1]["flows"])
