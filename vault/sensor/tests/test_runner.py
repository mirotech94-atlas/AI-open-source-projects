"""Runner tests: one full run_once cycle with every collector stubbed, plus
the hub work queue.

No network, no SSH, no LAN sweep, no filesystem outside tmp_path: every
collector the runner calls is monkeypatched, and paths.DATA_DIR points at a
tmp dir so last.json/state.json/history.jsonl land there.
"""
import datetime as dt
import json
import pathlib
import time

import pytest

from guardian_sensor import (VERSION, alerts, checks, dnsstats, domains, fix, home, hosts,
                             lan, notify, paths, router_fios, runner, sites, sync)

FX = pathlib.Path(__file__).parent / "fixtures"


def _read_json(path):
    with open(path) as f:
        return json.load(f)


def _read_text(path):
    with open(path) as f:
        return f.read()


class FakeClient:
    """Stands in for sync.HubClient: records pull/push/result calls."""

    url = "https://hub.example.invalid"

    def __init__(self, queued=None, pull_config=None):
        self.pulls = []
        self.pushes = []
        self.results = []
        self._queued = queued or []
        self._pull_config = pull_config

    def pull(self, state):
        self.pulls.append(state)
        return {"config": self._pull_config, "edits": [], "queued": self._queued, "errors": []}

    def push(self, payload):
        self.pushes.append(payload)
        return {"ok": True, "stored": {"scan": 1}}

    def result(self, qid, ok, out="", result=None):
        self.results.append((qid, ok, out, result))
        return {"ok": True}

    # The router credential Test branch fetches GET /router on the fly.
    router_reply: dict = {"router": None}

    def _try(self, method, path, body=None):
        if path == "/router":
            return self.router_reply
        return {}


@pytest.fixture
def stubbed(monkeypatch, tmp_path):
    """A sensor with a tmp data dir and every collector replaced by a fixture
    slice. Returns (cfg, args, client)."""
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    with open(FX / "last.json") as f:
        last = json.load(f)

    cfg = {
        "sites": [{"name": "Site A", "url": "https://a.example/health", "host": "a.example"}],
        "ssh_hosts": [{"name": "prod", "target": "root@10.0.0.1"}],
        "lan": {"enabled": True, "expected_gateway": "192.168.1.1"},
        "tailscale": {"enabled": False},
        "thresholds": {"fire_after_runs": 2, "resend_hours": 6},
        "auto_fix": {"enabled": False, "digest_hour": 99},   # 99: never digest during a test
        "home": {}, "heartbeats": {}, "checks": [], "dns": {"enabled": False},
    }

    site0 = dict(last["sites"][0], name="Site A")
    host0 = dict(last["ssh"][0], name="prod")

    # off the home LAN and --no-lan: no sweep, no port scan, no speed test
    monkeypatch.setattr(lan, "default_gateway", lambda: "10.0.0.1")
    monkeypatch.setattr(sites, "check_site", lambda s, th: site0)
    monkeypatch.setattr(hosts, "check_ssh", lambda h, th: host0)
    monkeypatch.setattr(hosts, "mac_updates", lambda: {"ts": None})
    monkeypatch.setattr(home, "dns_check", lambda: {"cloudflare": {"ok": True, "ms": 12}})
    monkeypatch.setattr(home, "wan_ip", lambda: None)
    monkeypatch.setattr(home, "wan_path", lambda cfg: None)
    monkeypatch.setattr(home, "tailscale_status", lambda: None)
    monkeypatch.setattr(home, "wifi_info", lambda: None)
    monkeypatch.setattr(home, "rogue_checks", lambda *a: None)
    monkeypatch.setattr(home, "home_security", lambda *a: None)
    monkeypatch.setattr(home, "speed_test", lambda: {"ts": "now", "down_mbps": 300.0, "up_mbps": 20.0, "latency_ms": 12})
    monkeypatch.setattr(domains, "domain_expiry", lambda hostnames, state: {"a.example": {"days_left": 200}})
    monkeypatch.setattr(domains, "email_security", lambda doms, state: {})
    monkeypatch.setattr(checks, "heartbeats_check", lambda cfg, state: {})
    monkeypatch.setattr(checks, "extra_checks", lambda cfg: [])
    monkeypatch.setattr(dnsstats, "dns_analyse", lambda cfg, state: {"enabled": False})
    monkeypatch.setattr(sites, "admin_probe", lambda s, state: {})
    monkeypatch.setattr(sites, "external_sites", lambda cfg, s, state: {})
    monkeypatch.setattr(alerts, "build_alerts", lambda res, cfg, state: [])
    monkeypatch.setattr(alerts, "unusual_hours", lambda res, state: [])
    monkeypatch.setattr(alerts, "security_score", lambda res, cfg, state: {"total": 88, "grade": "B"})
    monkeypatch.setattr(alerts, "recommendations", lambda res, cfg, state: [])
    monkeypatch.setattr(alerts, "correlate_incidents", lambda res, state: [])
    monkeypatch.setattr(fix, "remediate", lambda res, cfg, state, runner=None: [])
    # a notification in a test would page a real phone
    monkeypatch.setattr(notify, "notify", lambda *a, **k: [])

    client = FakeClient()
    monkeypatch.setattr(sync, "client_from_disk", lambda: client)

    args = type("A", (), {"once": True, "no_lan": True})()
    return cfg, args, client


# ---------------- one cycle ----------------

def test_run_once_writes_last_state_history_and_pushes(stubbed, tmp_path):
    cfg, args, client = stubbed
    state = {}
    res = runner.run_once(cfg, state, args)

    last = _read_json(paths.p("last.json"))
    assert last["ts"] == res["ts"]
    assert last["self"]["version"] == VERSION
    assert last["health"]["sensor"] == "online"
    assert last["sites"][0]["name"] == "Site A"
    assert last["ssh"][0]["name"] == "prod"
    assert last["on_home_lan"] is False
    assert last["lan"] is None                      # --no-lan and off the home LAN

    on_disk_state = _read_json(paths.p("state.json"))
    assert on_disk_state["last_run"] == res["ts"]
    assert on_disk_state["sites"]["Site A"]["ok"] == res["sites"][0]["ok"]
    assert on_disk_state["ssh"]["prod"]["reachable"] == res["ssh"][0]["reachable"]

    lines = _read_text(paths.p("history.jsonl")).strip().splitlines()
    assert len(lines) == 1
    hist = json.loads(lines[0])
    assert hist["ts"] == res["ts"]
    assert set(hist["sites"]) == {"Site A"}          # dict keyed by name, the shape the hub ingests
    assert set(hist["hosts"]) == {"prod"}
    assert hist["score"] == 88
    assert hist["alerts"] == {"CRIT": 0, "WARN": 0, "INFO": 0}

    assert len(client.pushes) == 1
    payload = client.pushes[0]
    assert payload["version"] == VERSION
    assert payload["last"]["ts"] == res["ts"]
    assert payload["state"]["last_run"] == res["ts"]
    assert res["hub_sync"] == "ok"


def test_run_once_pull_happens_before_the_scan(stubbed):
    cfg, args, client = stubbed
    state = {"hub_edits_offset": 4}
    runner.run_once(cfg, state, args)
    assert client.pulls == [state]


def test_second_cycle_appends_one_more_history_line(stubbed):
    cfg, args, client = stubbed
    state = {}
    runner.run_once(cfg, state, args)
    runner.run_once(cfg, state, args)
    lines = _read_text(paths.p("history.jsonl")).strip().splitlines()
    assert len(lines) == 2
    assert len(client.pushes) == 2


def test_run_once_without_a_hub_still_writes_everything(monkeypatch, stubbed):
    cfg, args, _client = stubbed
    monkeypatch.setattr(sync, "client_from_disk", lambda: None)
    res = runner.run_once(cfg, {}, args)
    assert "hub_sync" not in res
    assert _read_json(paths.p("state.json"))["last_run"] == res["ts"]


def test_run_once_survives_a_collector_blowing_up(monkeypatch, stubbed):
    cfg, args, client = stubbed

    def boom(*a, **k):
        raise RuntimeError("whois is not installed")

    monkeypatch.setattr(domains, "domain_expiry", boom)
    res = runner.run_once(cfg, {}, args)
    assert "whois is not installed" in res["collect_error"]
    assert len(client.pushes) == 1          # the cycle still completed and pushed


# ---------------- hub work queue ----------------

def _res():
    return {"ts": "2026-09-09T12:00:00-04:00", "fixes": [], "speed": None}


def test_queue_fix_runs_apply_fix_and_reports_the_result(monkeypatch):
    seen = {}

    def fake_apply_fix(cfg, host, action, arg=None, by="auto", lock=True, runner=None):
        seen.update(host=host, action=action, arg=arg, by=by, lock=lock)
        return {"ts": "t", "host": host, "action": action, "label": "Enable UFW", "arg": arg, "ok": True, "out": "done"}

    monkeypatch.setattr(fix, "apply_fix", fake_apply_fix)
    client = FakeClient()
    res = _res()
    runner.run_queue([{"id": "q1", "path": "/api/fix", "who": "alice",
                       "body": {"host": "prod", "action": "enable_ufw"}}], {}, res, client)
    assert seen["host"] == "prod" and seen["action"] == "enable_ufw" and seen["by"] == "hub:alice"
    # the runner already holds the run lock: apply_fix must not wait on it
    assert seen["lock"] is False
    assert res["fixes"][0]["ok"] is True
    assert client.results == [("q1", True, "done", res["fixes"][0])]


def test_queue_refuses_an_unknown_fix_action_but_still_reports(monkeypatch):
    called = []
    monkeypatch.setattr(fix, "apply_fix", lambda *a, **k: called.append(a))
    client = FakeClient()
    res = _res()
    runner.run_queue([{"id": "q2", "path": "/api/fix", "who": "bob",
                       "body": {"host": "prod", "action": "rm_minus_rf"}}], {}, res, client)
    assert called == []
    qid, ok, out, detail = client.results[0]
    assert (qid, ok) == ("q2", False) and "not a known fix action" in out


def test_queue_speedtest_runs_and_updates_res(monkeypatch):
    monkeypatch.setattr(home, "speed_test", lambda: {"ts": "now", "down_mbps": 250.0})
    client = FakeClient()
    res = _res()
    assert runner.run_queue([{"id": "q3", "path": "/api/speedtest", "body": {}}], {}, res, client) is True
    assert res["speed"]["down_mbps"] == 250.0
    assert client.results[0][:2] == ("q3", True)


def test_queue_run_is_a_noop_that_still_reports(monkeypatch):
    client = FakeClient()
    runner.run_queue([{"id": "q4", "path": "/api/run", "body": {}}], {}, _res(), client)
    assert client.results[0][:2] == ("q4", True)


def test_queue_reboot_plan_inside_the_window_reboots(monkeypatch):
    seen = {}
    monkeypatch.setattr(fix, "apply_fix", lambda cfg, host, action, arg=None, by="auto", lock=True, runner=None:
                        seen.update(host=host, action=action, lock=lock) or
                        {"ts": "t", "host": host, "action": action, "label": "Reboot", "ok": True, "out": "rebooting"})
    now = dt.datetime.now().astimezone()
    client = FakeClient()
    runner.run_queue([{"id": "q5", "path": "/api/reboot-plan", "who": "carol", "body": {
        "host": "prod",
        "window_start": (now - dt.timedelta(minutes=5)).isoformat(timespec="seconds"),
        "window_end": (now + dt.timedelta(minutes=5)).isoformat(timespec="seconds"),
    }}], {}, _res(), client)
    assert seen["action"] == "reboot" and seen["lock"] is False
    assert client.results[0][:2] == ("q5", True)


def test_queue_reboot_plan_outside_the_window_is_refused(monkeypatch):
    called = []
    monkeypatch.setattr(fix, "apply_fix", lambda *a, **k: called.append(a))
    now = dt.datetime.now().astimezone()
    client = FakeClient()
    runner.run_queue([{"id": "q6", "path": "/api/reboot-plan", "body": {
        "host": "prod",
        "window_start": (now + dt.timedelta(hours=2)).isoformat(timespec="seconds"),
        "window_end": (now + dt.timedelta(hours=3)).isoformat(timespec="seconds"),
    }}], {}, _res(), client)
    assert called == []
    assert client.results[0][:3] == ("q6", False, "outside window")


def test_queue_unsupported_path_is_reported_not_silently_dropped():
    client = FakeClient()
    runner.run_queue([{"id": "q7", "path": "/api/launch-missiles", "body": {}}], {}, _res(), client)
    qid, ok, out, _ = client.results[0]
    assert (qid, ok) == ("q7", False) and "unsupported queue path" in out


def test_queue_router_test_fetches_the_credential_and_logs_in(monkeypatch):
    seen = {}
    monkeypatch.setattr(router_fios, "test_credential",
                        lambda base_url, password, username="", **k:
                        seen.update(base_url=base_url, password=password, username=username)
                        or {"ok": True, "error": ""})
    client = FakeClient()
    client.router_reply = {"router": {"vendor": "fios", "base_url": "https://192.168.1.1",
                                      "username": "", "password": "s3cret"}}
    runner.run_queue([{"id": "rt1", "path": "/api/router/test", "body": {}}], {}, _res(), client)
    # The credential came from GET /router, not the queue body.
    assert seen == {"base_url": "https://192.168.1.1", "password": "s3cret", "username": ""}
    qid, ok, out, _ = client.results[0]
    assert (qid, ok) == ("rt1", True) and "accepted" in out


def test_queue_router_test_with_no_credential_is_refused(monkeypatch):
    calls = []
    monkeypatch.setattr(router_fios, "test_credential", lambda *a, **k: calls.append(a))
    client = FakeClient()  # default router_reply is {"router": None}
    runner.run_queue([{"id": "rt2", "path": "/api/router/test", "body": {}}], {}, _res(), client)
    assert calls == []
    qid, ok, out, _ = client.results[0]
    assert (qid, ok) == ("rt2", False) and "no router credential" in out


def test_queue_router_test_reports_a_rejected_credential(monkeypatch):
    monkeypatch.setattr(router_fios, "test_credential",
                        lambda *a, **k: {"ok": False, "error": "router rejected the credential (403)"})
    client = FakeClient()
    client.router_reply = {"router": {"vendor": "fios", "base_url": "https://192.168.1.1",
                                      "username": "", "password": "wrong"}}
    runner.run_queue([{"id": "rt3", "path": "/api/router/test", "body": {}}], {}, _res(), client)
    qid, ok, out, _ = client.results[0]
    assert (qid, ok) == ("rt3", False) and "rejected the credential" in out


def test_queue_router_disable_fetches_the_credential_and_applies(monkeypatch):
    seen = {}
    monkeypatch.setattr(router_fios, "disable_remote_admin",
                        lambda base_url, password, username="", **k:
                        seen.update(base_url=base_url, password=password, username=username)
                        or {"ok": True, "out": "remote administration disabled", "error": ""})
    client = FakeClient()
    client.router_reply = {"router": {"vendor": "fios", "base_url": "https://192.168.1.1",
                                      "username": "", "password": "s3cret"}}
    runner.run_queue([{"id": "rd1", "path": "/api/router/disable-remote-admin", "body": {}}], {}, _res(), client)
    assert seen == {"base_url": "https://192.168.1.1", "password": "s3cret", "username": ""}
    qid, ok, out, _ = client.results[0]
    assert (qid, ok) == ("rd1", True) and "disabled" in out


def test_queue_router_disable_reports_a_failure(monkeypatch):
    monkeypatch.setattr(router_fios, "disable_remote_admin",
                        lambda *a, **k: {"ok": False, "out": "", "error": "apply failed (500)"})
    client = FakeClient()
    client.router_reply = {"router": {"vendor": "fios", "base_url": "https://192.168.1.1",
                                      "username": "", "password": "s3cret"}}
    runner.run_queue([{"id": "rd2", "path": "/api/router/disable-remote-admin", "body": {}}], {}, _res(), client)
    qid, ok, out, _ = client.results[0]
    assert (qid, ok) == ("rd2", False) and "apply failed" in out


def test_queue_reports_a_failure_when_the_fix_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("ssh exploded")

    monkeypatch.setattr(fix, "apply_fix", boom)
    client = FakeClient()
    runner.run_queue([{"id": "q8", "path": "/api/fix", "body": {"host": "prod", "action": "enable_ufw"}}],
                     {}, _res(), client)
    qid, ok, out, _ = client.results[0]
    assert (qid, ok) == ("q8", False) and "ssh exploded" in out


# ---------------- the queue's fast path ----------------
# poll_queue_once / queue_poll_loop: the latency fix (jobs must start in
# seconds, not at the tail of the next multi-minute scan) and the
# no-double-execution guarantee.

class FakeQueueClient:
    """A HubClient stand-in that mimics the hub's claim-on-read contract:
    `poll_queue()` hands back the configured items exactly ONCE, then []
    forever after -- exactly what GET /queue does by flipping each row to
    "running" as it is returned. Lets a test prove a second tick cannot
    re-run an item the first tick already claimed."""

    def __init__(self, batches=None, fail_times=0):
        self._batches = list(batches or [])
        self.poll_calls = 0
        self.results = []
        self.fail_times = fail_times   # poll_queue() raises this many times before succeeding

    def poll_queue(self):
        self.poll_calls += 1
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ConnectionError("hub unreachable")
        return self._batches.pop(0) if self._batches else []

    def result(self, qid, ok, out="", result=None):
        self.results.append((qid, ok, out, result))
        return {"ok": True}


def test_run_once_no_longer_pulls_or_executes_the_queue(monkeypatch, stubbed):
    """The latency fix: a scan must not be the thing that fetches or runs
    dashboard-queued work any more (that used to happen at the very end of
    `run_once`, after everything else). `pull()` no longer even carries a
    "queued" key, and `run_queue` must never be called from inside a scan."""
    cfg, args, client = stubbed
    called = []
    monkeypatch.setattr(runner, "run_queue", lambda *a, **k: called.append(a))
    runner.run_once(cfg, {}, args)
    assert called == []


def test_poll_queue_once_runs_a_queued_item_and_reports_it(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(fix, "apply_fix", lambda cfg, host, action, arg=None, by="auto", lock=True, runner=None:
                        {"ts": "t", "host": host, "action": action, "ok": True, "out": "done"})
    client = FakeQueueClient(batches=[[{"id": "q1", "path": "/api/fix",
                                        "body": {"host": "prod", "action": "enable_ufw"}}]])
    n = runner.poll_queue_once(client=client, cfg={})
    assert n == 1
    assert client.results[0][:2] == ("q1", True)


def test_poll_queue_once_is_a_noop_with_nothing_queued_or_no_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    assert runner.poll_queue_once(client=FakeQueueClient(batches=[[]]), cfg={}) == 0
    assert runner.poll_queue_once(client=None, cfg={}) == 0   # sync.client_from_disk() -> not enrolled -> None


def test_poll_queue_once_wakes_run_now_event_for_api_run(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    import threading
    client = FakeQueueClient(batches=[[{"id": "q2", "path": "/api/run", "body": {}}]])
    ev = threading.Event()
    runner.poll_queue_once(client=client, cfg={}, run_now_event=ev)
    assert ev.is_set()
    assert client.results[0][:2] == ("q2", True)


def test_poll_queue_once_does_not_wake_run_now_for_other_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    import threading
    monkeypatch.setattr(fix, "apply_fix", lambda *a, **k: {"ts": "t", "host": "prod", "action": "enable_ufw", "ok": True, "out": "done"})
    client = FakeQueueClient(batches=[[{"id": "q3", "path": "/api/fix", "body": {"host": "prod", "action": "enable_ufw"}}]])
    ev = threading.Event()
    runner.poll_queue_once(client=client, cfg={}, run_now_event=ev)
    assert not ev.is_set()


def test_a_claimed_item_is_never_run_twice_across_ticks(tmp_path, monkeypatch):
    """No-double-execution guarantee: the hub only ever hands a row back
    ONCE (see FakeQueueClient's docstring). A second poll tick must run
    nothing for the same item, mirroring GET /queue's claim-on-read."""
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(home, "speed_test", lambda: {"ts": "now", "down_mbps": 500.0})
    client = FakeQueueClient(batches=[[{"id": "q4", "path": "/api/speedtest", "body": {}}]])
    first = runner.poll_queue_once(client=client, cfg={})
    second = runner.poll_queue_once(client=client, cfg={})
    assert (first, second) == (1, 0)
    assert len(client.results) == 1
    assert client.results[0][:2] == ("q4", True)


def test_queue_poll_loop_never_starts_a_second_tick_while_one_is_running(tmp_path, monkeypatch):
    """The other half of the no-double-execution guarantee: the background
    loop is a strict sequence of (fetch -> run every item -> wait), so a
    tick can never be re-entered while the previous one is still busy --
    there is no timer that fires on its own independent schedule."""
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    import threading
    in_flight = {"n": 0, "max_seen": 0}
    lock = threading.Lock()

    class SlowClient(FakeQueueClient):
        def poll_queue(self):
            with lock:
                in_flight["n"] += 1
                in_flight["max_seen"] = max(in_flight["max_seen"], in_flight["n"])
            time.sleep(0.02)   # long enough that a re-entrant tick would overlap
            try:
                return super().poll_queue()
            finally:
                with lock:
                    in_flight["n"] -= 1

    client = SlowClient(batches=[[]] * 3)
    stop_event = threading.Event()
    run_now_event = threading.Event()
    monkeypatch.setattr(runner, "sync", type("S", (), {"client_from_disk": staticmethod(lambda: client)}))
    monkeypatch.setattr(runner, "QUEUE_POLL_SECONDS", 0.01)
    monkeypatch.setattr(runner, "QUEUE_POLL_JITTER", 0)
    t = threading.Thread(target=runner.queue_poll_loop, args=(stop_event, run_now_event))
    t.start()
    time.sleep(0.15)
    stop_event.set()
    t.join(timeout=2)
    assert in_flight["max_seen"] == 1
    assert client.poll_calls >= 2


def test_queue_poll_loop_backs_off_on_repeated_failures_and_recovers(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    import threading
    client = FakeQueueClient(batches=[[]], fail_times=2)
    monkeypatch.setattr(runner, "sync", type("S", (), {"client_from_disk": staticmethod(lambda: client)}))
    monkeypatch.setattr(runner, "QUEUE_POLL_SECONDS", 0.01)
    monkeypatch.setattr(runner, "QUEUE_POLL_JITTER", 0)
    monkeypatch.setattr(runner, "QUEUE_POLL_MAX_BACKOFF", 0.05)
    waits = []
    real_wait = threading.Event.wait
    stop_event = threading.Event()
    run_now_event = threading.Event()

    def spying_wait(self, timeout=None):
        # Patching `Event.wait` globally also intercepts unrelated waits
        # (e.g. `Thread.start()`'s own internal Event, called with
        # timeout=None) -- only touch `stop_event`'s own calls, which is
        # exactly what `queue_poll_loop` waits on between ticks.
        if self is not stop_event:
            return real_wait(self, timeout)
        waits.append(timeout)
        return real_wait(self, min(timeout, 0.01))   # keep the test itself fast

    monkeypatch.setattr(threading.Event, "wait", spying_wait)
    t = threading.Thread(target=runner.queue_poll_loop, args=(stop_event, run_now_event))
    t.start()
    time.sleep(0.1)
    stop_event.set()
    t.join(timeout=2)
    # two failures back the interval off above the base cadence before the
    # third (successful) poll resets it back down
    assert max(waits[:2]) > 0.01
    assert client.fail_times == 0   # both failures were consumed; it recovered


# ---------------- the loop ----------------

def test_main_once_runs_a_single_cycle(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    cycles = []
    monkeypatch.setattr(runner, "run_once", lambda cfg, state, args: cycles.append(1) or {"ts": "t", "alerts": []})
    rc = runner.main(type("A", (), {"once": True, "no_lan": True})())
    assert rc == 0 and len(cycles) == 1


def test_main_skips_when_another_run_holds_the_lock(monkeypatch, tmp_path, capsys):
    import fcntl
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    paths.ensure()
    held = open(paths.p(".run.lock"), "w")
    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        called = []
        monkeypatch.setattr(runner, "run_once", lambda *a: called.append(1))
        rc = runner.main(type("A", (), {"once": True, "no_lan": True})())
        assert rc == 0 and called == []
        assert "another collector run is in progress" in capsys.readouterr().out
    finally:
        fcntl.flock(held, fcntl.LOCK_UN)
        held.close()


def test_main_logs_and_returns_when_a_cycle_raises(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))

    def boom(*a):
        raise RuntimeError("config.json is a banana")

    monkeypatch.setattr(runner, "run_once", boom)
    rc = runner.main(type("A", (), {"once": True, "no_lan": True})())
    assert rc == 0
    cap = capsys.readouterr()
    assert "config.json is a banana" in cap.err   # logs go to stderr
    assert cap.out == ""                          # stdout stays a clean JSON channel


# ---------------- retention vs the hub offsets ----------------
# The push tracks how many LINES of each log the hub has already seen, and
# retention trims those logs from the front. Every test below uses a real
# sync.HubClient (only a fake _req) because the slicing under test lives inside
# HubClient.push — FakeClient records the payload before any of it happens.

def _real_client(monkeypatch, ingest_ok=True):
    """A real HubClient whose _req answers the three pull GETs and records every
    POST /ingest body. GET /config returns {} so it never rewrites config.json."""
    monkeypatch.setattr(sync, "RETRY_DELAY", 0)
    client = sync.HubClient("https://hub.example.invalid", "tok")
    posts = []

    def fake_req(method, path, body=None):
        key = (method, path.split("?")[0])
        if key == ("GET", "/config"):
            return {}
        if key == ("GET", "/edits"):
            return {"edits": [], "last_id": 0}
        if key == ("GET", "/queue"):
            return {"queued": []}
        if key == ("POST", "/ingest"):
            posts.append(body)
            return {"ok": bool(ingest_ok)}
        raise AssertionError(f"unexpected request {method} {path}")

    monkeypatch.setattr(client, "_req", fake_req)
    return client, posts


def _ago(days):
    return (dt.datetime.now().astimezone() - dt.timedelta(days=days)).isoformat(timespec="seconds")


def _hist_line(ts, **extra):
    # `{"ts": "` must be the first 8 characters: the retention predicate reads
    # the timestamp positionally as line[8:33].
    return json.dumps({"ts": ts, **extra})


def _line_count(name):
    return len(_read_text(paths.p(name)).strip().splitlines())


def test_retention_shifts_the_history_offset_so_new_lines_still_reach_the_hub(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    # 10 lines already pushed, the oldest of them past the 30-day window
    with open(paths.p("history.jsonl"), "w") as f:
        f.write(_hist_line(_ago(40)) + "\n")
        for _ in range(9):
            f.write(_hist_line(_ago(1)) + "\n")
    state = {"hub_sent": {"events": 0, "history": 10, "fixes": 0}}

    # exactly what a cycle does: append this run's line, then run retention
    runner._append_jsonl("history.jsonl", [{"ts": _ago(0), "new": True}])
    assert runner.apply_retention(state)["history"] == 1
    assert state["hub_sent"]["history"] == 9        # offset followed the trim
    assert _line_count("history.jsonl") == 10       # length unchanged: 1 dropped, 1 appended

    client, posts = _real_client(monkeypatch)
    client.push({"version": VERSION, "last": {}, "state": state})
    # without the shift lines[10:] would be empty and history would never sync again
    assert len(posts[0]["history"]) == 1
    assert posts[0]["history"][0]["new"] is True
    assert state["hub_sent"]["history"] == 10


def test_retention_shift_stops_events_resending_all_5000_lines(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    with open(paths.p("events.jsonl"), "w") as f:
        for i in range(runner.EVENTS_MAX_LINES):
            f.write(json.dumps({"n": i}) + "\n")
    state = {"hub_sent": {"events": runner.EVENTS_MAX_LINES, "history": 0, "fixes": 0}}

    runner._append_jsonl("events.jsonl", [{"n": 5000}, {"n": 5001}])
    assert runner.apply_retention(state)["events"] == 2
    assert state["hub_sent"]["events"] == 4998
    assert _line_count("events.jsonl") == runner.EVENTS_MAX_LINES

    client, posts = _real_client(monkeypatch)
    client.push({"version": VERSION, "last": {}, "state": state})
    # a stale-high offset would trip the truncation guard and resend all 5000
    assert [e["n"] for e in posts[0]["events"]] == [5000, 5001]
    assert state["hub_sent"]["events"] == runner.EVENTS_MAX_LINES


def test_retention_never_drives_an_offset_negative(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    with open(paths.p("history.jsonl"), "w") as f:
        for _ in range(3):
            f.write(_hist_line(_ago(40)) + "\n")
    state = {"hub_sent": {"history": 1}}          # only 1 of the 3 had been sent
    assert runner.apply_retention(state)["history"] == 3
    assert state["hub_sent"]["history"] == 0


def test_retention_leaves_hub_sent_alone_when_nothing_is_trimmed(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    with open(paths.p("history.jsonl"), "w") as f:
        f.write(_hist_line(_ago(1)) + "\n")
    state = {}
    assert runner.apply_retention(state) == {"history": 0, "events": 0}
    assert "hub_sent" not in state


def test_two_cycles_push_only_the_new_history_line_after_a_trim(monkeypatch, stubbed, tmp_path):
    cfg, args, _fake = stubbed
    # three lines the hub already has, the oldest past the retention window:
    # cycle 1 appends one and trims that one, leaving the file the same length
    with open(paths.p("history.jsonl"), "w") as f:
        f.write(_hist_line(_ago(40)) + "\n")
        f.write(_hist_line(_ago(2)) + "\n")
        f.write(_hist_line(_ago(1)) + "\n")
    client, posts = _real_client(monkeypatch)
    monkeypatch.setattr(sync, "client_from_disk", lambda: client)

    state = {"hub_sent": {"events": 0, "history": 3, "fixes": 0}}
    r1 = runner.run_once(cfg, state, args)
    r2 = runner.run_once(cfg, state, args)

    assert [h["ts"] for h in posts[0]["history"]] == [r1["ts"]]   # aged line gone, offset shifted with it
    assert [h["ts"] for h in posts[1]["history"]] == [r2["ts"]]   # exactly the new line, nothing re-sent
    assert state["hub_sent"]["history"] == 4
    assert r2["hub_sync"] == "ok"


# ---------------- a failing push must not cost the cycle its state ----------------

def test_state_is_written_even_when_the_push_raises(monkeypatch, stubbed):
    cfg, args, _fake = stubbed

    class Exploding(FakeClient):
        def push(self, payload):
            raise RuntimeError("hub ingest exploded")

    monkeypatch.setattr(sync, "client_from_disk", lambda: Exploding())
    state = {}
    res = runner.run_once(cfg, state, args)

    assert res["hub_sync"] == "failed"
    assert "hub ingest exploded" in res["hub_error"]
    on_disk = _read_json(paths.p("state.json"))
    assert on_disk["last_run"] == res["ts"]        # offsets/baselines survived the failure
    assert _read_json(paths.p("last.json"))["hub_sync"] == "failed"


# ---------------- notification action buttons ----------------

NEW_DEVICE = {"sev": "WARN", "key": "lan:new:aa:bb:cc:dd:ee:ff", "msg": "New device on Wi-Fi: unknown vendor"}


def _capture_notifications(monkeypatch):
    seen = []
    monkeypatch.setattr(notify, "notify", lambda *a, **k: seen.append((a, k)) or [])
    return seen


def _device_pushes(seen):
    return [k for a, k in seen if a and a[0] == "Vault: new device on Wi-Fi"]


def test_new_device_push_has_no_buttons_without_a_public_url(monkeypatch, stubbed):
    cfg, args, _client = stubbed        # cfg has no public_url
    monkeypatch.setattr(alerts, "build_alerts", lambda res, c, state: [dict(NEW_DEVICE)])
    seen = _capture_notifications(monkeypatch)
    state = {}
    runner.run_once(cfg, state, args)

    kw = _device_pushes(seen)
    assert len(kw) == 1
    # the hub base URL serves no /act/ routes, so the buttons must be omitted
    assert kw[0]["actions"] is None
    assert kw[0]["click"] == "https://hub.example.invalid/"    # the body still links to the hub
    assert state["pending_actions"] == {}                      # and no token was minted for nobody


def test_new_device_push_gets_buttons_when_public_url_is_set(monkeypatch, stubbed):
    cfg, args, _client = stubbed
    cfg["public_url"] = "https://guardian.example/"
    monkeypatch.setattr(alerts, "build_alerts", lambda res, c, state: [dict(NEW_DEVICE)])
    seen = _capture_notifications(monkeypatch)
    state = {}
    runner.run_once(cfg, state, args)

    acts = _device_pushes(seen)[0]["actions"]
    tok = list(state["pending_actions"])[0]
    assert state["pending_actions"][tok]["mac"] == "aa:bb:cc:dd:ee:ff"
    assert acts == [f"http, Trust (mine), https://guardian.example/act/{tok}/trust, method=GET, clear=true",
                    f"http, Ignore 24h, https://guardian.example/act/{tok}/snooze, method=GET, clear=true",
                    "view, Open Vault, https://guardian.example/"]


# --------------------------------------------------------------------------
# Regression: a trust replayed from the hub at the start of a run must
# survive the end of that run. The old end-of-run "merge from disk" copied
# the pre-run `trusted: None` back over the live `True`, so every trust from
# chat or the inbox was consumed and undone.
# --------------------------------------------------------------------------

def test_trust_applied_during_a_run_survives_settling(monkeypatch, tmp_path):
    from guardian_sensor import runner, sync
    state = {"lan": {"networks": {"home": {"known_macs": {
        "aa:aa:aa:aa:aa:aa": {"name": "tv", "trusted": None, "label": None}}}}}}
    # the hub edit lands on the live state at pull time...
    sync.apply_edit({"id": 1, "path": "/api/device",
                     "body": {"mac": "aa:aa:aa:aa:aa:aa", "net": "primary", "trusted": True, "label": "TV"}},
                    state, {})
    assert state["lan"]["networks"]["home"]["known_macs"]["aa:aa:aa:aa:aa:aa"]["trusted"] is True
    # ...and the settle step at the end of the run leaves it alone
    runner.settle_dashboard_edits(state)
    d = state["lan"]["networks"]["home"]["known_macs"]["aa:aa:aa:aa:aa:aa"]
    assert d["trusted"] is True and d["label"] == "TV"


def test_a_forget_is_settled_once_and_the_list_cleared():
    from guardian_sensor import runner
    state = {"forgotten": ["bb:bb:bb:bb:bb:bb"],
             "lan": {"known_macs": {"bb:bb:bb:bb:bb:bb": {}},
                     "networks": {"home": {"known_macs": {"bb:bb:bb:bb:bb:bb": {}, "cc:cc:cc:cc:cc:cc": {}}}}}}
    runner.settle_dashboard_edits(state)
    assert "bb:bb:bb:bb:bb:bb" not in state["lan"]["networks"]["home"]["known_macs"]
    assert "bb:bb:bb:bb:bb:bb" not in state["lan"]["known_macs"]
    assert "cc:cc:cc:cc:cc:cc" in state["lan"]["networks"]["home"]["known_macs"]
    assert state["forgotten"] == []



def test_every_domain_is_checked_and_the_untouchable_ones_are_advisory():
    """Miro's ruling: check them all, but a domain he cannot fix must never
    raise anything.

    `email.exclude` used to mean "do not look", so a domain somebody else
    owns simply had no answer — and a spoofable domain in the estate went
    unmeasured. It now means "look, but never nag": the records are read and
    reported, and the result carries `advisory` so the hub knows not to
    alert, deduct or nudge on it.
    """
    from guardian_sensor import runner
    cfg = {"sites": [{"url": "https://status.example.org/health", "host": "status.example.org"},
                     {"url": "https://flycheap.example.com/", "host": "flycheap.example.com"}],
           "email": {"exclude": ["example.org "]}}
    # Nothing is dropped any more.
    assert runner.email_domains_of(cfg) == ["example.com", "example.org"]
    # And the one he does not control is named, case- and space-insensitively.
    assert runner.email_advisory_domains(cfg) == {"example.org"}
    cfg.pop("email")
    assert runner.email_advisory_domains(cfg) == set()


def test_advisory_is_restamped_every_pass_not_only_on_a_fresh_lookup():
    """A domain moved in or out of the list has to change behaviour on the
    next scan, not whenever its 24h cache happens to expire."""
    from guardian_sensor import domains
    import datetime as dt
    fresh = dt.datetime.now().astimezone().isoformat()
    state = {"email_sec": {"example.org": {"spf": "v=spf1 -all", "ts": fresh, "advisory": False}}}
    out = domains.email_security(["example.org"], state, advisory={"example.org"})
    assert out["example.org"]["advisory"] is True, "the cached row must pick up the new ruling"
    assert out["example.org"]["spf"] == "v=spf1 -all", "and must not be re-looked-up for it"


# ---------------- confirm before alarm ----------------
#
# A site is not declared down on the strength of one failed check. Fake
# outages are the trust plan's #1 credibility killer (§2.5A) and the cheapest
# source of one is a single flaky check -- a DNS hiccup, a TLS handshake that
# timed out, a router mid-reboot.
#
# The gate already existed in `pushable()`: `site:*:down`, `ssh:*:down` and
# `check:*` are held until the alert has fired on `fire_after_runs`
# consecutive sweeps (default 2). Nothing tested it, which for a rule this
# load-bearing means one refactor away from paging on the first blip again.
#
# WHAT IS AND IS NOT DELAYED is the whole design. The ALERT is recorded on
# the first failure and is visible in the app at once; it is the PUSH that
# waits. An owner who is looking loses nothing; an owner who is asleep is
# woken only once the failure has survived a second sweep. At a five-minute
# cadence that costs one sweep of notification latency, which is the price
# of not crying wolf.


@pytest.fixture
def pushes(monkeypatch):
    """Capture what would have been pushed to a phone."""
    sent = []
    monkeypatch.setattr(notify, "notify", lambda title, body, *a, **k: sent.append(title) or [])
    return sent


def _down(res):
    for s in res["sites"]:
        s["ok"] = False
        s["code"] = 0
    return res


def _sweep(stubbed, state, alerts_mod, down):
    """One cycle whose alert list contains a site-down finding, or nothing."""
    cfg, args, _client = stubbed
    key = "site:Site A:down"
    monkey = [{"sev": "CRIT", "key": key, "msg": "Site A DOWN"}] if down else []
    alerts_mod.build_alerts = lambda res, cfg, state: list(monkey)
    return runner.run_once(cfg, state, args)


def test_the_first_failed_check_records_the_alert_but_pages_nobody(stubbed, pushes, monkeypatch):
    from guardian_sensor import alerts as alerts_mod
    state = {}
    _sweep(stubbed, state, alerts_mod, down=True)

    adb = state["alerts_db"]
    assert "site:Site A:down" in adb, "the finding is recorded immediately"
    assert adb["site:Site A:down"]["count"] == 1
    assert "site:Site A:down" not in (state.get("notified") or {}), (
        "one failed check must not page anyone"
    )


def test_a_second_consecutive_failure_confirms_it_and_pages(stubbed, pushes, monkeypatch):
    from guardian_sensor import alerts as alerts_mod
    state = {}
    _sweep(stubbed, state, alerts_mod, down=True)
    _sweep(stubbed, state, alerts_mod, down=True)

    assert state["alerts_db"]["site:Site A:down"]["count"] == 2
    assert "site:Site A:down" in (state.get("notified") or {}), (
        "a failure that survived a second sweep is a real outage"
    )


def test_two_blips_either_side_of_a_recovery_never_add_up(stubbed, pushes, monkeypatch):
    """The reason the count has to be consecutive. A site that fails, comes
    back, and fails again next week has had two bad moments, not one
    confirmed outage — and summing them would page on the second blip."""
    from guardian_sensor import alerts as alerts_mod
    state = {}
    _sweep(stubbed, state, alerts_mod, down=True)
    _sweep(stubbed, state, alerts_mod, down=False)     # recovered
    _sweep(stubbed, state, alerts_mod, down=True)

    assert state["alerts_db"]["site:Site A:down"]["count"] == 1, (
        "the recovery resolved the alert, so the new failure starts over"
    )
    assert "site:Site A:down" not in (state.get("notified") or {})


def test_the_confirmation_threshold_is_configurable(stubbed, pushes, monkeypatch):
    """An owner with a flaky upstream can demand more evidence; one who
    wants the old behaviour can set it to 1."""
    from guardian_sensor import alerts as alerts_mod
    cfg, _args, _client = stubbed
    cfg["thresholds"]["fire_after_runs"] = 1
    state = {}
    _sweep(stubbed, state, alerts_mod, down=True)
    assert "site:Site A:down" in (state.get("notified") or {})
