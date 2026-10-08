"""The NVR enumerator against a local fake that speaks the documented Reolink
HTTP API (`/cgi-bin/api.cgi`: Login -> token, GetChannelstatus -> channels).

Covered: a successful channel list, a rejected login, the HTTP-interface-off
case (nothing listening at all -- which is exactly the owner's real recorder,
where only the proprietary port 9000 answers), a non-API answer on the port,
a recorder outside the tenant's configured subnets, the baseline diff
(silent first read, then new / offline / vanished channels) and the promise
that no probe result ever carries a credential.

The owner's real NVR is never contacted: this fake is the only recorder these
tests speak to.
"""
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from guardian_sensor import nvr

CHANNELS = [
    {"channel": 0, "name": "Front Door", "online": 1, "typeInfo": "RLC-810A"},
    {"channel": 1, "name": "Driveway", "online": 1, "typeInfo": "RLC-520A"},
    {"channel": 2, "name": "Back Garden", "online": 0, "typeInfo": "RLC-520A"},
]


class FakeReolink(BaseHTTPRequestHandler):
    """One recorder. `mode` on the server decides how it behaves."""

    protocol_version = "HTTP/1.0"

    def log_message(self, *a):
        pass

    def _json(self, payload, code=200):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        mode = self.server.mode
        if mode == "no_api":
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if mode == "garbage":
            self.send_response(200)
            self.send_header("Content-Length", "4")
            self.end_headers()
            self.wfile.write(b"\x00\x01\x02\x03")
            return
        cmd = ""
        if "cmd=" in self.path:
            cmd = self.path.split("cmd=", 1)[1].split("&", 1)[0]
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"[]")
        self.server.seen.append((cmd, body))
        if cmd == "Login":
            user = ((body[0].get("param") or {}).get("User") or {})
            if mode == "bad_login" or user.get("password") != "s3cret":
                return self._json([{"cmd": "Login", "code": 1,
                                    "error": {"detail": "login failed", "rspCode": -7}}])
            return self._json([{"cmd": "Login", "code": 0,
                                "value": {"Token": {"name": "tok-123", "leaseTime": 3600}}}])
        if cmd == "GetDevInfo":
            return self._json([{"cmd": "GetDevInfo", "code": 0,
                                "value": {"DevInfo": {"model": "RLN8-410", "firmVer": "v3.0.0.0",
                                                      "channelNum": len(self.server.channels)}}}])
        if cmd == "GetChannelstatus":
            return self._json([{"cmd": "GetChannelstatus", "code": 0,
                                "value": {"count": len(self.server.channels),
                                          "status": self.server.channels}}])
        if cmd == "Logout":
            return self._json([{"cmd": "Logout", "code": 0, "value": {"rspCode": 200}}])
        return self._json([{"cmd": cmd, "code": 1, "error": {"detail": "unknown", "rspCode": -1}}])


@pytest.fixture
def fake():
    def start(mode="ok", channels=None):
        srv = HTTPServer(("127.0.0.1", 0), FakeReolink)
        srv.mode = mode
        srv.channels = [dict(c) for c in (channels if channels is not None else CHANNELS)]
        srv.seen = []
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv
    servers = []

    def make(mode="ok", channels=None):
        s = start(mode, channels)
        servers.append(s)
        return s
    yield make
    for s in servers:
        s.shutdown()


def rec(srv, **over):
    base = {"id": "rec1", "vendor": "reolink", "host": "127.0.0.1",
            "port": srv.server_address[1] if srv else 1, "scheme": "http",
            "username": "admin", "password": "s3cret", "name": "Garage NVR",
            "net": "garage-nvr-cameras", "enabled": True}
    base.update(over)
    return base


PREFIXES = ["127.0.0"]


# ---------------------------------------------------------------- happy path
def test_lists_every_channel_behind_the_recorder(fake):
    srv = fake()
    out = nvr.probe(rec(srv), PREFIXES)
    assert out["ok"] is True, out
    assert out["error_kind"] == ""
    assert [c["channel"] for c in out["channels"]] == [0, 1, 2]
    assert [c["name"] for c in out["channels"]] == ["Front Door", "Driveway", "Back Garden"]
    assert [c["online"] for c in out["channels"]] == [True, True, False]
    assert out["reported_count"] == 3
    assert out["model"] == "RLN8-410"
    assert out["firmware"] == "v3.0.0.0"
    # the documented sequence, and the lease is given back
    assert [c for c, _ in srv.seen] == ["Login", "GetDevInfo", "GetChannelstatus", "Logout"]


def test_devices_are_ordinary_device_rows_with_a_stable_identity(fake):
    srv = fake()
    out = nvr.probe(rec(srv), PREFIXES)
    devs = nvr.devices_for(rec(srv), out, "2026-09-10T10:00:00+00:00")
    assert len(devs) == 3
    for mac, d in devs.items():
        assert mac.startswith("02:") and len(mac) == 17   # locally administered, never a real OUI
        assert d["kind"] == "camera"
        assert d["ip"] == ""                              # they genuinely have no address we can see
        assert d["behind_recorder"] == "rec1"
    # stable across calls, and distinct per channel
    again = nvr.devices_for(rec(srv), out, "2026-09-10T11:00:00+00:00")
    assert set(again) == set(devs)
    assert nvr.synthetic_mac("rec1", 0) != nvr.synthetic_mac("rec2", 0)
    assert nvr.synthetic_mac("rec1", 0) != nvr.synthetic_mac("rec1", 1)
    presence = {d["name"]: d["presence"] for d in devs.values()}
    assert presence == {"Front Door": "ONLINE", "Driveway": "ONLINE", "Back Garden": "OFFLINE"}


def test_a_channel_with_no_name_still_gets_one(fake):
    srv = fake(channels=[{"channel": 4, "name": "", "online": 1, "typeInfo": ""}])
    out = nvr.probe(rec(srv), PREFIXES)
    devs = nvr.devices_for(rec(srv), out, "t")
    assert [d["name"] for d in devs.values()] == ["Channel 5"]


# ---------------------------------------------------------------- failures
def test_login_failure_is_reported_as_auth_failed(fake):
    srv = fake(mode="bad_login")
    out = nvr.probe(rec(srv), PREFIXES)
    assert out["ok"] is False
    assert out["error_kind"] == "auth_failed"
    assert "login failed" in out["error"]


def test_wrong_password_is_auth_failed_not_a_silent_zero(fake):
    srv = fake()
    out = nvr.probe(rec(srv, password="wrong"), PREFIXES)
    assert out["error_kind"] == "auth_failed"
    assert out["channels"] == []


def test_http_interface_switched_off(fake):
    """The owner's real case: nothing is listening on the HTTP port at all
    (only the proprietary client port answers). That must be its own
    error_kind, because it is the one with an actionable setting behind it."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()                                  # nothing listening -> ECONNREFUSED
    out = nvr.probe(rec(None, port=port), PREFIXES, timeout=3)
    assert out["ok"] is False
    assert out["error_kind"] == "http_disabled", out


def test_api_path_missing_is_api_disabled(fake):
    srv = fake(mode="no_api")
    out = nvr.probe(rec(srv), PREFIXES)
    assert out["error_kind"] == "api_disabled"


def test_a_non_api_answer_on_the_port_is_a_protocol_error(fake):
    srv = fake(mode="garbage")
    out = nvr.probe(rec(srv), PREFIXES)
    assert out["error_kind"] == "protocol"


def test_no_credentials_and_unsupported_vendor_are_stated_not_swallowed(fake):
    srv = fake()
    assert nvr.probe(rec(srv, password=""), PREFIXES)["error_kind"] == "no_credentials"
    assert nvr.probe(rec(srv, vendor="hikvision"), PREFIXES)["error_kind"] == "unsupported"


def test_an_address_outside_the_tenants_networks_is_never_contacted(fake):
    srv = fake()
    out = nvr.probe(rec(srv, host="8.8.8.8"), PREFIXES)
    assert out["error_kind"] == "out_of_scope"
    assert srv.seen == []                      # not one request left the sensor
    assert nvr.in_scope("192.168.1.182", ["192.168.1"]) is True
    assert nvr.in_scope("192.168.2.182", ["192.168.1"]) is False
    assert nvr.in_scope("not-an-ip", ["192.168.1"]) is False


def test_no_probe_result_ever_carries_a_credential(fake):
    for mode in ("ok", "bad_login", "no_api", "garbage"):
        srv = fake(mode=mode)
        out = nvr.probe(rec(srv), PREFIXES)
        blob = json.dumps(out)
        assert "s3cret" not in blob
        assert "admin" not in blob
    assert "<redacted>" in nvr._redact("http://admin:s3cret@10.0.0.5/x")
    assert "s3cret" not in nvr._redact("GET /cgi-bin/api.cgi?cmd=Login&password=s3cret")


# ---------------------------------------------------------------- the diff
def test_the_first_read_of_a_recorder_is_silent(fake):
    srv = fake()
    out = nvr.probe(rec(srv), PREFIXES)
    assert nvr.diff(None, out, "2026-09-10T10:00:00+00:00") == {"new": [], "offline": [], "vanished": []}


def test_new_offline_and_vanished_channels():
    ts0 = "2026-09-10T10:00:00+00:00"
    ts1 = "2026-09-10T11:00:00+00:00"      # an hour later: past GONE_AFTER_S
    first = {"channels": [{"channel": 0, "name": "Front Door", "online": True},
                          {"channel": 1, "name": "Driveway", "online": True}]}
    base = nvr.update_baseline(None, first, ts0)
    assert set(base) == {"0", "1"}

    # channel 1 drops off, channel 2 appears, and nothing has vanished yet
    second = {"channels": [{"channel": 0, "name": "Front Door", "online": True},
                           {"channel": 1, "name": "Driveway", "online": False},
                           {"channel": 2, "name": "Shed", "online": True}]}
    d = nvr.diff(base, second, ts1)
    assert [c["channel"] for c in d["new"]] == [2]
    assert [c["channel"] for c in d["offline"]] == [1]
    assert d["offline"][0]["last_online"] == ts0
    assert d["vanished"] == []

    # a camera unplugged for a MINUTE is not yet a finding
    assert nvr.diff(base, second, "2026-09-10T10:01:00+00:00")["offline"] == []

    # the recorder stops listing channel 1 altogether
    third = {"channels": [{"channel": 0, "name": "Front Door", "online": True}]}
    d3 = nvr.diff(base, third, ts1)
    assert [c["channel"] for c in d3["vanished"]] == [1]


def test_scan_and_commit_never_promote_a_failed_probe_into_a_baseline(fake):
    srv = fake()
    state = {}
    ts = "2026-09-10T10:00:00+00:00"
    got = nvr.scan([rec(srv)], PREFIXES, state, ts)
    assert len(got) == 1 and got[0]["ok"]
    assert len(got[0]["devices"]) == 3
    nvr.commit(got, state, ts)
    assert set(state["recorders"]["rec1"]["channels"]) == {"0", "1", "2"}

    # a later cycle where the recorder cannot be read leaves the baseline alone
    bad = nvr.scan([rec(srv, password="wrong")], PREFIXES, state, "2026-09-10T10:05:00+00:00")
    assert bad[0]["devices"] == {}
    nvr.commit(bad, state, "2026-09-10T10:05:00+00:00")
    assert set(state["recorders"]["rec1"]["channels"]) == {"0", "1", "2"}


def test_scan_skips_a_disabled_recorder(fake):
    srv = fake()
    assert nvr.scan([rec(srv, enabled=False)], PREFIXES, {}, "t") == []
    assert srv.seen == []


# ---------------------------------------------------------------- the findings
def test_the_five_findings(fake):
    from guardian_sensor import alerts

    srv = fake()
    state = {}
    ts0 = "2026-09-10T10:00:00+00:00"
    ts1 = "2026-09-10T11:00:00+00:00"

    def build(results):
        A = []
        alerts._nvr_alerts({"recorders": results}, A)
        return {a["key"]: a for a in A}

    # first read of a new recorder: silent, and the baseline is learned
    first = nvr.scan([rec(srv, expected_channels=3)], PREFIXES, state, ts0)
    assert build(first) == {}
    nvr.commit(first, state, ts0)

    # channel 1 goes offline for an hour, a new channel 3 appears
    srv.channels[1]["online"] = 0
    srv.channels.append({"channel": 3, "name": "Shed", "online": 1, "typeInfo": "RLC-520A"})
    second = nvr.scan([rec(srv, expected_channels=3)], PREFIXES, state, ts1)
    got = build(second)
    rid = "rec1"
    assert got[f"nvr:cam-new:{rid}:3"]["sev"] == "INFO"
    assert "Shed" in got[f"nvr:cam-new:{rid}:3"]["msg"]
    assert got[f"nvr:cam-offline:{rid}:1"]["sev"] == "WARN"
    assert "Driveway" in got[f"nvr:cam-offline:{rid}:1"]["msg"]
    # 4 listed vs 3 expected
    assert "expect 3" in got[f"nvr:count:{rid}"]["msg"]
    # every finding is attributed to the recorder's own camera network
    assert {a["net"] for a in got.values()} == {"garage-nvr-cameras"}

    # the recorder stops listing channel 2 entirely
    nvr.commit(second, state, ts1)
    srv.channels = [c for c in srv.channels if c["channel"] != 2]
    third = build(nvr.scan([rec(srv)], PREFIXES, state, ts1))
    assert third[f"nvr:cam-gone:{rid}:2"]["sev"] == "WARN"
    assert "Back Garden" in third[f"nvr:cam-gone:{rid}:2"]["msg"]


def test_an_unreadable_recorder_says_which_setting_is_off(fake):
    from guardian_sensor import alerts

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    got = nvr.scan([rec(None, port=port)], PREFIXES, {}, "t", timeout=3)
    A = []
    alerts._nvr_alerts({"recorders": got}, A)
    assert len(A) == 1
    assert A[0]["key"] == "nvr:http-off:rec1"
    assert "HTTP interface looks switched off" in A[0]["msg"]
    assert "undocumented protocol" in A[0]["msg"]


def test_every_error_kind_maps_to_a_finding():
    from guardian_sensor import alerts
    assert set(nvr.ERROR_KINDS) == set(alerts.NVR_ERRORS)
