"""RA-E4: one synthetic Guardian alert, end to end, on mocked transports.

detection  -> `check_open_incidents` sees the incident the hub reports
diagnosis  -> `incident.analyse` builds the plan from read-scope reads only
response   -> exactly one registered Tier 2 step becomes an approval
              request; nothing is written to the hub
observation-> the check is read again: still open is still not OK, and OK
              only once the hub itself shows the incident closed

And the failure branch: when the alert cannot be investigated, the reading
says "incomplete" and stays not-OK -- it is never reported as resolved.

Throughout: every request goes to the one hub with the read token, no
request names another tenant, and no trust label is written by anything.
The beacon carries no mac, which is what the live sensor sends today
(`incident.py` docstring), so the plan holds no trust-label step at all.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from atlas.kernel.contracts import Health, Tier
from atlas.kernel.permissions import classify
from atlas.modules import guardian
from atlas.modules.guardian import incident

READ = "read-token-synthetic"
WRITE = "write-token-synthetic"
KEY = "dns:beacon:10.0.0.5:c2.synthetic.example"
NET = {"id": "net_1", "slug": "office", "name": "Office"}
T0 = datetime(2026, 9, 28, 3, 0, tzinfo=UTC).isoformat()


class Hub:
    """The Guardian hub, as the transports see it. One tenant, one incident."""

    def __init__(self, *, evidence_fails: bool = False,
                 alert_keys: list[str] | None = None) -> None:
        self.open = True
        self.evidence_fails = evidence_fails
        self.alert_keys = [KEY] if alert_keys is None else alert_keys
        self.reads: list[dict] = []
        self.writes: list[dict] = []

    def _alert(self) -> dict:
        return {"key": KEY, "sev": "WARN", "msg": "beacon", "first_seen": T0,
                "last_seen": T0, "count": 9,
                "status": "open" if self.open else "resolved", "network": NET}

    async def get_json(self, path, *, base_url=None, headers=None, params=None):
        self.reads.append({"path": path, "base_url": base_url,
                           "headers": dict(headers or {}),
                           "params": dict(params or {})})
        if path == "/v1/incidents":
            items = ([{"iid": "501", "entity": "10.0.0.5", "sev": "WARN",
                       "opened": T0, "alert_keys": self.alert_keys}]
                     if self.open else [])
            return {"items": items, "total": len(items)}, None
        if path.endswith("/evidence"):
            if self.evidence_fails:
                return None, "HTTP 502 from /v1/alerts/…/evidence"
            return {"alert": {**self._alert(), "evidence": {}}}, None
        if path == "/v1/alerts":
            wanted = (params or {}).get("status")
            rows = [self._alert()] if (wanted != "open" or self.open) else []
            return {"items": rows, "total": len(rows)}, None
        if path == "/v1/fixes":
            return {"items": [], "total": 0}, None
        raise AssertionError(f"unexpected read {path}")

    async def post_json(self, path, **kw):
        self.writes.append({"path": path, **kw})
        raise AssertionError(f"nothing in this path may write: {path}")


@pytest.fixture
def hub(monkeypatch):
    monkeypatch.setenv("GUARDIAN_API_TOKEN", READ)
    monkeypatch.setenv("GUARDIAN_WRITE_TOKEN", WRITE)
    fake = Hub()
    monkeypatch.setattr(guardian, "get_json", fake.get_json)
    monkeypatch.setattr(incident, "post_json", fake.post_json)
    return fake


@pytest.fixture
def catalog(monkeypatch):
    """The guardian actions registered the way the registry would."""
    from atlas.kernel.registry import REGISTRY
    for action in guardian.MODULE.actions():
        monkeypatch.setitem(REGISTRY.actions, action.name, action)
        monkeypatch.setitem(REGISTRY.decisions, action.name, classify(action))
    return REGISTRY


@pytest.fixture
def cards(monkeypatch, memory_backend):
    from atlas.kernel import approvals, executor
    sent: list = []

    async def notify(msg):
        sent.append(msg)
        return ["slack"]

    async def no_execute(*a, **kw):
        raise AssertionError("an approval request must not execute")
    monkeypatch.setattr(approvals, "notify", notify)
    monkeypatch.setattr(executor, "execute", no_execute)
    memory_backend.responses["SELECT id FROM proposals"] = None
    # No expired or declined card for this cause (RA-B6's quiet window).
    memory_backend.responses[
        "SELECT id, status, decided_at, expires_at FROM proposals"] = None
    memory_backend.responses["INSERT INTO proposals"] = {"id": 77}
    memory_backend.responses["FROM lessons"] = None
    memory_backend.responses["SELECT"] = []
    return sent


def _one_tenant(fake: Hub) -> None:
    assert fake.reads, "nothing was read"
    for r in fake.reads:
        assert r["base_url"] == guardian.HUB
        assert r["path"].startswith("/v1/")
        assert r["headers"] == {"Authorization": f"Bearer {READ}"}
        assert WRITE not in str(r)
        assert not {"account", "tenant", "org", "network"} & set(r["params"])


async def test_synthetic_alert_detection_to_observation(hub, catalog, cards,
                                                        memory_backend):
    from atlas.kernel.approvals import propose

    # 1. Detection.
    seen = await guardian.check_open_incidents()
    assert seen.health is Health.DEGRADED            # a WARN incident is open
    assert seen.value == 1.0
    assert seen.evidence["investigation"] == "complete"

    # 2. Diagnosis: read-scope reads only, one beacon vector, no guesses.
    plan = seen.evidence["plan"]
    assert plan["alert_id"] == KEY
    assert [v["kind"] for v in plan["vectors"]] == ["beacon"]
    assert plan["vectors"][0]["reputation"] is None
    assert plan["vectors"][0]["mac"] is None
    assert plan["operator"] == "unknown"
    # One registered step, and it is not a trust-label write.
    assert [s["action"] for s in plan["steps"]] == ["guardian.rescan"]
    assert plan["manual"][0]["kind"] == "mark_untrusted"   # Miro's, by hand
    step = plan["steps"][0]
    assert step["params"] == {"network": "office", "alert_key": KEY}
    assert catalog.decisions[step["action"]].tier is Tier.ASK

    # The dry run shows the exact call: no network, no tenant, nothing sent.
    dry = await catalog.actions[step["action"]].dry_run(**step["params"])
    assert dry.ok
    assert dry.evidence["call"]["json"] == {}
    assert dry.evidence["call"]["url"] == f"{guardian.HUB}/v1/scans"
    assert hub.writes == []

    # 3. Response: exactly one approval request, pending, nothing run.
    pid = await propose(step["action"], step["params"], reason=step["why"],
                        evidence={"alert": KEY},
                        dedup_key=f"{step['action']}@{KEY}", automatic=True)
    assert pid == 77
    inserted = [args for sql, args in memory_backend.sql_log
                if sql.startswith("INSERT INTO proposals")]
    assert len(inserted) == 1
    assert inserted[0][2] == "guardian.rescan"
    assert inserted[0][10] == "pending"
    assert len(cards) == 1
    assert hub.writes == []

    # 4. Post-action observation. Asking is not resolving: still open.
    after = await guardian.check_open_incidents()
    assert after.health is not Health.OK
    assert after.value == 1.0

    # Only the hub closing it makes the reading OK.
    hub.open = False
    closed = await guardian.check_open_incidents()
    assert closed.health is Health.OK and closed.value == 0.0

    _one_tenant(hub)
    assert not any(r["path"].startswith("/v1/devices") for r in hub.reads)
    assert hub.writes == []


@pytest.mark.parametrize("fake", [Hub(evidence_fails=True), Hub(alert_keys=[])],
                         ids=["evidence-unreadable", "no-alert-to-analyse"])
async def test_an_alert_that_cannot_be_investigated_is_incomplete(
        monkeypatch, catalog, fake):
    monkeypatch.setenv("GUARDIAN_API_TOKEN", READ)
    monkeypatch.setenv("GUARDIAN_WRITE_TOKEN", WRITE)
    monkeypatch.setattr(guardian, "get_json", fake.get_json)
    monkeypatch.setattr(incident, "post_json", fake.post_json)

    obs = await guardian.check_open_incidents()
    assert obs.health is not Health.OK
    assert obs.evidence["investigation"] == "incomplete"
    assert obs.evidence["plan"] is None
    assert "investigation incomplete" in obs.detail
    assert "not resolved" in obs.detail
    # The hub's own words stay out of the detail the reasoner reads.
    assert "HTTP 502" not in obs.detail
    if fake.evidence_fails:
        assert "502" in obs.evidence["plan_unavailable"]
    _one_tenant(fake)
    assert fake.writes == []


def test_no_trust_label_write_exists():
    """RA-C6 (Miro, 2026-09-28: "suggest only"): no action writes a trust
    label, with or without the write token."""
    import os
    saved = os.environ.pop("GUARDIAN_WRITE_TOKEN", None)
    try:
        for token in ("", WRITE):
            if token:
                os.environ["GUARDIAN_WRITE_TOKEN"] = token
            assert "guardian.mark_untrusted" not in {
                a.name for a in guardian.MODULE.actions()}
    finally:
        os.environ.pop("GUARDIAN_WRITE_TOKEN", None)
        if saved is not None:
            os.environ["GUARDIAN_WRITE_TOKEN"] = saved