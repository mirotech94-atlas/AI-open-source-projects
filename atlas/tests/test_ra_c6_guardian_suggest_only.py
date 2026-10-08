"""RA-C6: ATLAS never writes a Vault trust label.

Miro, 2026-09-28: "suggest only". `guardian.mark_untrusted` was a Tier 2
action that wrote `trusted: false` on the hub. It is gone -- the action, its
tool, and the functions that sent the request -- and an incident plan now
carries a suggestion instead: which device, on which network, why, and a
link to the device in Vault, for Miro to mark himself.

What is load-bearing:

* No action, with or without the write token, and no tool writes a trust
  label; no code under `atlas/` sends a request to the hub's device route.
* A beacon the hub names by mac and network yields a suggestion carrying
  the device, the network, the reason and the Vault link, and the
  `open_incidents` reading names it -- while nothing is written, and every
  read stays on the one tenant's hub with the read token.
"""
from __future__ import annotations

import ast
import pathlib
from datetime import UTC, datetime

import pytest

from atlas.kernel.contracts import Health
from atlas.modules import guardian
from atlas.modules.guardian import incident

ROOT = pathlib.Path(__file__).resolve().parent.parent / "atlas"
READ, WRITE = "read-token-c6", "write-token-c6"
MAC = "AA:BB:CC:DD:EE:07"
KEY = "dns:beacon:10.0.0.7:c2.example"
NET = {"id": "net_1", "slug": "office", "name": "Office"}
T0 = datetime(2026, 9, 28, 3, 0, tzinfo=UTC).isoformat()


class Hub:
    """One tenant, one open incident, a beacon whose evidence names a mac."""

    def __init__(self) -> None:
        self.reads: list[dict] = []
        self.writes: list[dict] = []

    def _alert(self) -> dict:
        return {"key": KEY, "sev": "WARN", "msg": "beacon", "first_seen": T0,
                "last_seen": T0, "count": 9, "status": "open", "network": NET}

    async def get_json(self, path, *, base_url=None, headers=None, params=None):
        self.reads.append({"path": path, "base_url": base_url,
                           "headers": dict(headers or {}),
                           "params": dict(params or {})})
        if path == "/v1/incidents":
            return {"items": [{"iid": "9", "entity": "10.0.0.7", "sev": "WARN",
                               "opened": T0, "alert_keys": [KEY]}],
                    "total": 1}, None
        if path.endswith("/evidence"):
            return {"alert": {**self._alert(), "evidence": {"mac": MAC}}}, None
        if path == "/v1/alerts":
            return {"items": [self._alert()], "total": 1}, None
        if path == "/v1/fixes":
            return {"items": [], "total": 0}, None
        raise AssertionError(f"unexpected read {path}")

    async def post_json(self, path, **kw):
        self.writes.append({"path": path, **kw})
        raise AssertionError(f"nothing here may write: {path}")


@pytest.fixture
def hub(monkeypatch):
    monkeypatch.setenv("GUARDIAN_API_TOKEN", READ)
    monkeypatch.setenv("GUARDIAN_WRITE_TOKEN", WRITE)
    fake = Hub()
    monkeypatch.setattr(guardian, "get_json", fake.get_json)
    monkeypatch.setattr(incident, "post_json", fake.post_json)
    return fake


# ------------------------------------------------------------ no write path

@pytest.mark.parametrize("token", ["", WRITE])
def test_no_action_writes_a_trust_label(monkeypatch, token):
    if token:
        monkeypatch.setenv("GUARDIAN_WRITE_TOKEN", token)
    else:
        monkeypatch.delenv("GUARDIAN_WRITE_TOKEN", raising=False)
    names = {a.name for a in guardian.MODULE.actions()}
    assert not any("trust" in n for n in names)
    assert names <= {"guardian.contain", "guardian.alert_op", "guardian.rescan"}


def test_no_tool_and_no_write_function_remains():
    from atlas.kernel.businesses import BY_NAME
    from atlas.kernel.tools import TOOLS
    from atlas.tools.builtin import register_all

    register_all()
    assert "guardian_mark_untrusted" not in TOOLS
    assert "guardian_mark_untrusted" not in BY_NAME["guardian"].writes
    for gone in ("mark_untrusted", "trust_again", "_device_write",
                 "mark_untrusted_call"):
        assert not hasattr(incident, gone), gone


def test_nothing_under_atlas_posts_to_the_hubs_device_route():
    """Every write call (post_json, or an httpx post/put/patch) whose URL
    mentions `/devices` -- the route that carries the trust label."""
    offenders = []
    for path in ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (func.attr if isinstance(func, ast.Attribute)
                    else func.id if isinstance(func, ast.Name) else "")
            if name not in ("post_json", "post", "put", "patch"):
                continue
            if node.args and "/devices" in ast.unparse(node.args[0]):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert offenders == []


# ------------------------------------------------------------ the suggestion

async def test_the_plan_suggests_the_device_with_a_vault_link(hub):
    plan, why = await incident.analyse(KEY)
    assert why is None
    (suggestion,) = [m for m in plan.manual if m.get("link")]
    assert suggestion["kind"] == "mark_untrusted"
    assert suggestion["target"] == MAC
    assert suggestion["network"] == "office"
    assert suggestion["link"] == f"{guardian.HUB}/assets/devices/AA%3ABB%3ACC%3ADD%3AEE%3A07"
    assert suggestion["why"].startswith("vector 1: beacon")
    assert "never changes a Vault trust label" in suggestion["note"]
    assert all(s["action"] != "guardian.mark_untrusted" for s in plan.steps)
    assert hub.writes == []


async def test_the_reading_tells_miro_and_writes_nothing(hub):
    obs = await guardian.check_open_incidents()
    assert obs.health is Health.DEGRADED
    assert "suggested for you (ATLAS changes no trust label)" in obs.detail
    assert f"mark {MAC} untrusted on office in Vault" in obs.detail
    assert "/assets/devices/" in obs.detail
    assert hub.writes == []
    # One tenant, the read token, no device route read or written.
    for r in hub.reads:
        assert r["base_url"] == guardian.HUB
        assert r["headers"] == {"Authorization": f"Bearer {READ}"}
        assert not r["path"].startswith("/v1/devices")


def test_a_suggestion_needs_a_real_mac_and_network():
    """Shapes are checked before a value reaches the link or the detail:
    sensor data an attacker shaped does not become a URL."""
    vector = incident.Vector(
        kind="beacon", target="10.0.0.7", reputation=None, first_seen=None,
        evidence={"alert": KEY, "host": "10.0.0.7"}, mac="not-a-mac",
        network=NET)
    assert incident._suggestion_for(vector) is None
