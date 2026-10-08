"""An alert reaches a channel at its onset, its escalation and its clearing --
never once per scan -- and WHICH severities a channel takes is that channel's
own setting.

Two owner reports shaped this, in this order.

1. A Slack channel got "FlyCheap: /admin is reachable from the internet" (an
   INFO advisory) every ~6 minutes, because the sensor bumps an alert's
   `count` each scan a condition persists and the delivery gate re-fired on
   every increment. The fix was onset/escalation/clearing only. That is the
   guarantee that actually stopped the storm and it is still asserted here.

   A second gate went in alongside it: INFO was dropped in `ingest` before
   the per-channel routes were consulted at all.

2. The owner's phone was flapping and Slack said nothing, with the Slack
   card's INFO delivery ticked ON. `lan:flapping:` is raised at INFO, so that
   second gate meant the checkbox could never do anything. He asked to be
   told. A per-channel switch that silently does nothing is worse than no
   switch, so the blanket INFO drop is gone and `routes[sev]` in `deliver()`
   decides -- which is where he can see and change it.

   These tests exercise the INGEST gate, so an INFO now shows up here. The
   channel that does not want it still never receives it; that is
   `deliver()`'s job and `test_integrations_core` covers it.
"""
import time

from tests.test_seed_helpers import seeded_demo
from guardian_hub.db import SessionLocal
from guardian_hub import models as m
from guardian_hub.integrations import registry


def enrol(client, tid):
    code = client.post("/api/v1/sensors/enrol-code").json()["code"]
    r = client.post("/api/v1/sensor/enrol", json={"code": code, "name": "s", "platform": "linux", "version": "0"})
    return r.json()["token"]


def _wait_for(predicate, timeout=2.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _payload(key, sev, count, ts, state="open"):
    return {
        "last": {"ts": ts, "alerts": [{"key": key, "sev": sev, "msg": "x", "count": count}]},
        "state": {"alerts_db": {key: {"key": key, "sev": sev, "msg": "x", "count": count,
                                      "state": state, "first_seen": ts, "last_seen": ts}}},
        "events": [], "history": [], "fixes": [], "dns_queries": [],
    }


def _harness(client, monkeypatch):
    tid = seeded_demo(client)
    calls = []
    monkeypatch.setattr(registry, "deliver",
                        lambda db, t, msg: calls.append((msg.kind, msg.severity, msg.dedup_key)))
    H = {"Authorization": f"Bearer {enrol(client, tid)}"}
    return H, calls


def _post(client, H, payload):
    client.post("/api/v1/sensor/ingest", json=payload, headers=H)


def test_an_info_advisory_is_offered_to_the_channels(client, monkeypatch):
    """It used to be dropped here, which is why a flapping phone never
    reached Slack. Whether it is actually SENT is the channel's routes."""
    H, calls = _harness(client, monkeypatch)
    _post(client, H, _payload("sec:panel:FlyCheap:/admin", "INFO", 1, "2026-09-16T09:00:00"))
    assert _wait_for(lambda: len(calls) == 1)
    assert calls == [("alert", "INFO", "sec:panel:FlyCheap:/admin")]
    # And it is genuinely stored (available when the owner looks).
    assert SessionLocal().query(m.Alert).filter_by(key="sec:panel:FlyCheap:/admin").count() == 1


def test_a_persistent_info_is_offered_once_not_once_per_scan(client, monkeypatch):
    """The original complaint, and still the real guarantee: a standing
    condition is announced when it starts, not every six minutes forever."""
    H, calls = _harness(client, monkeypatch)
    for i, c in enumerate([1, 2, 3, 4], start=1):
        _post(client, H, _payload("sec:panel:FlyCheap:/admin", "INFO", c, f"2026-09-16T09:0{i}:00"))
    assert _wait_for(lambda: len(calls) == 1)
    time.sleep(0.15)
    assert len(calls) == 1, calls


def test_a_new_warn_is_pushed_once_then_never_re_pushed_while_open(client, monkeypatch):
    H, calls = _harness(client, monkeypatch)
    _post(client, H, _payload("site:Foo:down", "WARN", 1, "2026-09-16T09:00:00"))
    assert _wait_for(lambda: len(calls) == 1)
    assert calls == [("alert", "WARN", "site:Foo:down")]
    # Persist across three more scans with rising count -> no re-push.
    for i, c in enumerate([2, 3, 4], start=1):
        _post(client, H, _payload("site:Foo:down", "WARN", c, f"2026-09-16T09:0{i}:00"))
    time.sleep(0.15)
    assert len(calls) == 1


def test_escalation_is_announced_once_at_the_step_up(client, monkeypatch):
    H, calls = _harness(client, monkeypatch)
    # Opens as INFO -> offered once (the channel's own routes decide whether
    # that one actually goes anywhere).
    _post(client, H, _payload("k:esc", "INFO", 1, "2026-09-16T09:00:00"))
    assert _wait_for(lambda: len(calls) == 1)
    # Gets worse -> WARN while still open -> one MORE, at the step up. A
    # condition that deteriorates is news even when its onset was not.
    _post(client, H, _payload("k:esc", "WARN", 2, "2026-09-16T09:01:00"))
    assert _wait_for(lambda: len(calls) == 2)
    assert calls[-1] == ("alert", "WARN", "k:esc")
    # Stays WARN -> no re-push. This is the part that must never regress.
    _post(client, H, _payload("k:esc", "WARN", 3, "2026-09-16T09:02:00"))
    time.sleep(0.15)
    assert len(calls) == 2


def test_resolve_of_a_problem_is_pushed_once(client, monkeypatch):
    H, calls = _harness(client, monkeypatch)
    _post(client, H, _payload("site:Bar:down", "CRIT", 1, "2026-09-16T09:00:00"))
    assert _wait_for(lambda: len(calls) == 1)
    _post(client, H, _payload("site:Bar:down", "CRIT", 1, "2026-09-16T09:05:00", state="resolved"))
    assert _wait_for(lambda: len(calls) == 2)
    assert calls[-1] == ("resolve", "CRIT", "site:Bar:down")


def test_an_info_that_clears_says_so(client, monkeypatch):
    """Symmetry: a channel told an advisory started is told when it stops.
    Being left with a standing worry that has quietly gone away is its own
    small failure."""
    H, calls = _harness(client, monkeypatch)
    _post(client, H, _payload("adv:thing", "INFO", 1, "2026-09-16T09:00:00"))
    assert _wait_for(lambda: len(calls) == 1)
    _post(client, H, _payload("adv:thing", "INFO", 1, "2026-09-16T09:05:00", state="resolved"))
    assert _wait_for(lambda: len(calls) == 2)
    assert calls[-1] == ("resolve", "INFO", "adv:thing")
