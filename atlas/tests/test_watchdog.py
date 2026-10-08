"""ops/watchdog/atlas_watchdog.py (RA-F4): the independent watchdog.

Every network call is mocked: urlopen for /healthz and the Slack webhook,
peer_cert for TLS. No request leaves the machine.
"""
from __future__ import annotations

import importlib.util
import io
import json
import logging
import ssl
import sys
import time
import urllib.error
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "ops" / "watchdog" / "atlas_watchdog.py"
_spec = importlib.util.spec_from_file_location("atlas_watchdog", _PATH)
wd = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["atlas_watchdog"] = wd   # dataclasses resolve through it
_spec.loader.exec_module(wd)

HOOK = "https://hooks.slack.com/services/T000/B000/secretpart"
URL = "https://atlas.example.test/healthz"


class _Resp:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.raw = body
        self._body = io.BytesIO(body)

    def read(self, n: int = -1) -> bytes:
        return self._body.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a) -> None:
        return None


class Net:
    """Stands in for urlopen. Records Slack posts; answers /healthz."""

    def __init__(self) -> None:
        self.healthz: object = _body({"ok": True, "modules": 31, "checks": 117})
        self.slack_fails = False
        self.posts: list[str] = []

    def urlopen(self, req, timeout=None):
        url = req.full_url
        if url.startswith("https://hooks.slack.com/"):
            if self.slack_fails:
                raise urllib.error.URLError(f"connection reset talking to {url}")
            self.posts.append(json.loads(req.data)["text"])
            return _Resp(200, b"ok")
        h = self.healthz
        if isinstance(h, Exception):
            raise h
        return _Resp(h.status, h.raw)             # a fresh body per request


def _body(obj: dict, status: int = 200) -> _Resp:
    return _Resp(status, json.dumps(obj).encode())


def _cert(days: float) -> dict:
    exp = time.gmtime(time.time() + days * 86400)
    return {"notAfter": time.strftime("%b %d %H:%M:%S %Y GMT", exp)}


@pytest.fixture
def net(monkeypatch):
    n = Net()
    monkeypatch.setattr(wd.urllib.request, "urlopen", n.urlopen)
    monkeypatch.setattr(wd, "peer_cert", lambda host, port: _cert(60))
    return n


def _cfg(tmp_path: Path, webhook: str | None = HOOK) -> object:
    hook, problem = wd.check_webhook(webhook)
    return wd.Config(url=URL, webhook=hook, webhook_problem=problem,
                     state_path=tmp_path / "state.json", tls_warn_days=14,
                     heartbeat_max_s=300, label="test-host")


def _healthy_with_heartbeat(age: int = 20) -> _Resp:
    return _body({"ok": True, "heartbeat": {"cycle_age_s": age,
                                            "observation_age_s": age}})


# ------------------------------------------------------------- checks

def test_healthz_ok(net):
    r, body = wd.fetch_healthz(URL)
    assert r.state == wd.OK and body["ok"] is True


@pytest.mark.parametrize("answer,detail", [
    (urllib.error.HTTPError(URL, 502, "Bad Gateway", {}, None), "HTTP 502"),
    (urllib.error.URLError("timed out"), "unreachable"),
    (_Resp(200, b"not json"), "not {\"ok\": true}"),
    (_body({"ok": False}), "not {\"ok\": true}"),
])
def test_healthz_failures(net, answer, detail):
    net.healthz = answer
    r, _ = wd.fetch_healthz(URL)
    assert r.state == wd.FAIL and detail in r.detail


def test_heartbeat_absent_is_unknown_never_ok(net):
    h, body = wd.fetch_healthz(URL)
    r = wd.check_heartbeat(h, body, 300)
    assert r.state == wd.UNKNOWN and "no heartbeat field" in r.detail


def test_heartbeat_unknown_when_healthz_down():
    h = wd.Result("healthz", wd.FAIL, "HTTP 502")
    assert wd.check_heartbeat(h, None, 300).state == wd.UNKNOWN


def test_heartbeat_fresh_and_stale():
    h = wd.Result("healthz", wd.OK, "")
    ok = wd.check_heartbeat(
        h, {"heartbeat": {"cycle_age_s": 31, "observation_age_s": 99}}, 300)
    assert ok.state == wd.OK
    stale = wd.check_heartbeat(
        h, {"heartbeat": {"cycle_age_s": 30, "observation_age_s": 5400}}, 300)
    assert stale.state == wd.FAIL and "observation_age_s=5400s" in stale.detail


@pytest.mark.parametrize("age,state", [(900, "OK"), (901, "FAIL")])
def test_heartbeat_limit_is_900s_by_default(age, state, tmp_path, monkeypatch):
    """The shipped default: 900 s is still fresh, one second more is FAIL,
    for either age."""
    monkeypatch.delenv("ATLAS_WATCHDOG_HEARTBEAT_MAX_S", raising=False)
    env = tmp_path / "watchdog.env"
    env.write_text("")
    cfg = wd.load_config(str(env), str(tmp_path / "s.json"))
    assert cfg.heartbeat_max_s == 900
    h = wd.Result("healthz", wd.OK, "")
    for hb in ({"cycle_age_s": age, "observation_age_s": 5},
               {"cycle_age_s": 5, "observation_age_s": age}):
        r = wd.check_heartbeat(h, {"heartbeat": hb}, cfg.heartbeat_max_s)
        assert r.state == getattr(wd, state), (hb, r)


def test_heartbeat_missing_age_is_unknown():
    """Both ages are required now that the field ships: one fresh age alone
    proves only half of liveness."""
    h = wd.Result("healthz", wd.OK, "")
    r = wd.check_heartbeat(h, {"heartbeat": {"cycle_age_s": 31}}, 300)
    assert r.state == wd.UNKNOWN and "observation_age_s missing" in r.detail


def test_heartbeat_stale_wins_over_a_null_partner():
    h = wd.Result("healthz", wd.OK, "")
    r = wd.check_heartbeat(
        h, {"heartbeat": {"cycle_age_s": 5000, "observation_age_s": None}}, 900)
    assert r.state == wd.FAIL and "cycle_age_s=5000s" in r.detail
    assert "observation_age_s is None" in r.detail


def test_heartbeat_null_age_is_unknown():
    """ATLAS sends null when it could not read an age (a database error);
    that is UNKNOWN, never OK -- for either field."""
    h = wd.Result("healthz", wd.OK, "")
    for hb in ({"cycle_age_s": None, "observation_age_s": 10},
               {"cycle_age_s": 10, "observation_age_s": None}):
        r = wd.check_heartbeat(h, {"heartbeat": hb}, 300)
        assert r.state == wd.UNKNOWN, hb


def test_tls_ok_near_expiry_and_verify_failure(monkeypatch):
    monkeypatch.setattr(wd, "peer_cert", lambda h, p: _cert(60))
    assert wd.check_tls(URL, 14).state == wd.OK
    monkeypatch.setattr(wd, "peer_cert", lambda h, p: _cert(5))
    r = wd.check_tls(URL, 14)
    assert r.state == wd.FAIL and "expires in" in r.detail

    def bad(h, p):
        e = ssl.SSLCertVerificationError("verify failed")
        e.verify_message = "certificate has expired"
        raise e
    monkeypatch.setattr(wd, "peer_cert", bad)
    r = wd.check_tls(URL, 14)
    assert r.state == wd.FAIL and "does not verify" in r.detail


def test_tls_unreachable_is_unknown(monkeypatch):
    def refused(h, p):
        raise ConnectionRefusedError("refused")
    monkeypatch.setattr(wd, "peer_cert", refused)
    assert wd.check_tls(URL, 14).state == wd.UNKNOWN


# ------------------------------------------------------ webhook config

def test_slack_token_is_refused_as_a_route():
    hook, problem = wd.check_webhook("xoxb-123-456-abc")
    assert hook is None and "token" in problem and wd.NO_ROUTE in problem


def test_non_slack_url_is_refused():
    hook, problem = wd.check_webhook("https://evil.example/hook")
    assert hook is None and wd.NO_ROUTE in problem


def test_env_file_is_read(tmp_path, monkeypatch):
    monkeypatch.delenv("ATLAS_WATCHDOG_WEBHOOK", raising=False)
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    env = tmp_path / "watchdog.env"
    env.write_text(f"# comment\nATLAS_WATCHDOG_WEBHOOK=\"{HOOK}\"\n"
                   "ATLAS_WATCHDOG_TLS_WARN_DAYS=21\n")
    env.chmod(0o600)
    cfg = wd.load_config(str(env), str(tmp_path / "s.json"))
    assert cfg.webhook == HOOK and cfg.tls_warn_days == 21


# ----------------------------------------------------- transitions

def test_no_webhook_runs_checks_logs_and_exits_nonzero(net, tmp_path, caplog):
    cfg = _cfg(tmp_path, webhook=None)
    with caplog.at_level(logging.INFO):
        code = wd.run(cfg)
    assert code == wd.EXIT_NO_ROUTE != 0
    assert "no alternate route configured" in caplog.text
    assert "healthz OK" in caplog.text            # the checks still ran
    assert not cfg.state_path.exists()            # no state advanced
    assert net.posts == []


def test_first_run_all_ok_is_silent(net, tmp_path):
    net.healthz = _healthy_with_heartbeat()
    cfg = _cfg(tmp_path)
    assert wd.run(cfg) == wd.EXIT_OK
    assert net.posts == []
    assert json.loads(cfg.state_path.read_text())["checks"]["healthz"]["state"] == "OK"


def test_first_run_announces_unknown_heartbeat_once(net, tmp_path):
    cfg = _cfg(tmp_path)
    assert wd.run(cfg) == wd.EXIT_NOT_OK
    assert len(net.posts) == 1 and "heartbeat UNKNOWN" in net.posts[0]
    wd.run(cfg)                                   # unchanged: no second alert
    assert len(net.posts) == 1


def test_ok_to_fail_to_ok(net, tmp_path):
    net.healthz = _healthy_with_heartbeat()
    cfg = _cfg(tmp_path)
    wd.run(cfg)
    assert net.posts == []

    net.healthz = urllib.error.HTTPError(URL, 502, "Bad Gateway", {}, None)
    assert wd.run(cfg) == wd.EXIT_NOT_OK
    assert len(net.posts) == 1
    assert "PROBLEM" in net.posts[0] and "healthz FAIL: HTTP 502" in net.posts[0]
    assert "heartbeat UNKNOWN" in net.posts[0]

    wd.run(cfg)                                   # still down: silent
    assert len(net.posts) == 1

    net.healthz = _healthy_with_heartbeat()
    assert wd.run(cfg) == wd.EXIT_OK
    assert len(net.posts) == 2 and "recovered" in net.posts[1]


def test_failed_post_keeps_state_and_retries(net, tmp_path, caplog):
    net.healthz = _healthy_with_heartbeat()
    cfg = _cfg(tmp_path)
    wd.run(cfg)
    before = cfg.state_path.read_text()

    net.healthz = urllib.error.URLError("timed out")
    net.slack_fails = True
    with caplog.at_level(logging.INFO):
        assert wd.run(cfg) == wd.EXIT_SEND_FAILED
    assert cfg.state_path.read_text() == before   # transition not swallowed
    assert "secretpart" not in caplog.text        # webhook never logged
    assert "<webhook>" in caplog.text

    net.slack_fails = False
    assert wd.run(cfg) == wd.EXIT_NOT_OK          # retried on the next run
    assert len(net.posts) == 1 and "healthz FAIL" in net.posts[0]


def test_state_since_is_kept_while_unchanged(net, tmp_path):
    net.healthz = _healthy_with_heartbeat()
    cfg = _cfg(tmp_path)
    wd.run(cfg)
    first = json.loads(cfg.state_path.read_text())["checks"]["healthz"]["since"]
    wd.run(cfg)
    again = json.loads(cfg.state_path.read_text())["checks"]["healthz"]["since"]
    assert first == again


def test_crash_is_never_a_success_code(net, tmp_path, monkeypatch):
    env = tmp_path / "watchdog.env"
    env.write_text(f"ATLAS_WATCHDOG_WEBHOOK={HOOK}\n")
    env.chmod(0o600)
    monkeypatch.delenv("ATLAS_WATCHDOG_WEBHOOK", raising=False)

    def boom(*a, **k):
        raise PermissionError("state dir not writable")
    monkeypatch.setattr(wd, "save_state", boom)
    code = wd.main(["--env-file", str(env), "--state", str(tmp_path / "s.json")])
    assert code == wd.EXIT_CRASHED
    assert code not in (wd.EXIT_OK, wd.EXIT_NOT_OK)
