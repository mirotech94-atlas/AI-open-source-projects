"""Parity tests: the ported alerts engine must reproduce the recorded Nexus
output for the same state.json + config.json inputs (fixtures/last.json was
produced by Nexus main() from that same state).

Excluded keys (documented, not silently dropped):
  - lan:unusual-hour:* — added by Nexus main() separately from build_alerts
    (via unusual_hours()); build_alerts itself never emits this key.
"""
import copy
import json
import pathlib

from guardian_sensor import alerts

FX = pathlib.Path(__file__).parent / "fixtures"


def load():
    with open(FX / "last.json") as f:
        last = json.load(f)
    with open(FX / "config.json") as f:
        cfg = json.load(f)
    with open(FX / "state.json") as f:
        state = json.load(f)
    return last, cfg, state


def test_build_alerts_parity():
    last, cfg, state = load()
    got = alerts.build_alerts(copy.deepcopy(last), cfg, copy.deepcopy(state))
    exp = {a["key"]: a["sev"] for a in last["alerts"]}
    assert {a["key"]: a["sev"] for a in got if not a["key"].startswith("lan:unusual-hour")} == exp


def test_score_parity():
    last, cfg, state = load()
    assert alerts.security_score(copy.deepcopy(last), cfg, copy.deepcopy(state))["total"] == last["score"]["total"]


def test_recommendations_parity():
    last, cfg, state = load()
    assert {r["key"] for r in alerts.recommendations(copy.deepcopy(last), cfg, copy.deepcopy(state))} == {r["key"] for r in last["recommendations"]}
