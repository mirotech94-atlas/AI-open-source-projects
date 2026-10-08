"""Nothing subnet-wide runs without the owner saying yes.

`block_subnet` blocks a whole /24: 256 addresses, not one attacker. The owner's
auto-fix policy note lists "block attacker IPs" among the safe unattended
actions, and a 256-address range is wider than that. Asked, he chose approval
for anything subnet-wide.

The rule lives in `remediate`'s own `go()`, not only in the consent sweep. The
attacker auto-response reaches `apply_fix` down a different path and skipped
the consent check entirely, which is how a subnet block could run unattended
even though `block_subnet` was already in `CONSENT_REQUIRES_CONFIRM`. A rule
enforced in one of two doorways is not a rule.
"""

import copy
import json
import pathlib

from guardian_sensor import fix, paths

FX = pathlib.Path(__file__).parent / "fixtures"


def _load():
    with open(FX / "last.json") as f:
        last = json.load(f)
    with open(FX / "config.json") as f:
        cfg = json.load(f)
    return last, cfg


def _attacker_scan(days_seen: int):
    """A scan whose attacker has been at it long enough that
    `pick_attacker_method` escalates past a single-address block."""
    last, cfg = _load()
    last = copy.deepcopy(last)
    host = next(h for h in last["ssh"] if h.get("reachable"))
    host.setdefault("sec", {})["ufw"] = "active"
    host["sec"]["top_attackers"] = {"192.0.2.107": 5000}
    state = {"attacker_hist": {"192.0.2.107": [f"2026-09-{d:02d}" for d in range(1, days_seen + 1)]}}
    return last, cfg, state, host["name"]


def test_a_subnet_block_is_never_run_unattended(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    last, cfg, state, host = _attacker_scan(days_seen=10)
    # Confirm the scenario really does escalate to a subnet block, otherwise
    # this test would pass by never reaching the rule at all.
    assert fix.pick_attacker_method({"days": 10, "fails": 5000}, "auto") == "block_subnet"

    calls = []
    def runner(h, cmd, timeout=120):
        calls.append(cmd)
        return (0, "ok")

    done = fix.remediate(last, cfg, state, runner=runner)

    assert not any(d["action"] == "block_subnet" for d in done), \
        "a subnet block ran unattended"
    assert not any("/24" in c for c in calls), \
        f"a /24 reached the host anyway: {[c for c in calls if '/24' in c]}"
    awaiting = last.get("awaiting_approval") or []
    assert any(a["action"] == "block_subnet" for a in awaiting), \
        "the subnet block was dropped silently rather than put to the owner"
    asked = next(a for a in awaiting if a["action"] == "block_subnet")
    assert asked["arg"].endswith("/24") and asked["host"] == host
    assert "256" in asked["why"], "the reason does not say how wide the block is"


def test_blocking_one_address_is_still_automatic(tmp_path, monkeypatch):
    """The decision was about subnet-wide actions, not about blocking at all."""
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    last, cfg, state, _ = _attacker_scan(days_seen=1)
    # A first-day attacker at this volume earns a long ban of that ONE address,
    # not a subnet block. Either single-address answer is fine here; what
    # matters is that it is not subnet-wide and it still runs on its own.
    chosen = fix.pick_attacker_method({"days": 1, "fails": 5000}, "auto")
    assert chosen in ("block_ip", "ban_long") and chosen not in fix.NEVER_UNATTENDED

    calls = []
    def runner(h, cmd, timeout=120):
        calls.append(cmd)
        return (0, "ok")

    done = fix.remediate(last, cfg, state, runner=runner)
    assert any(d["action"] == chosen for d in done), \
        f"the single-address response {chosen} stopped happening automatically"
    assert not (last.get("awaiting_approval") or []), \
        "a single-address block should need no approval"


def test_the_sensor_and_the_hub_agree_it_is_disruptive():
    """Two halves of one rule: the sensor refuses it unattended, the hub
    demands a confirmation. They must not drift apart."""
    assert fix.NEVER_UNATTENDED == {"block_subnet"}
    assert "block_subnet" in fix.CONSENT_REQUIRES_CONFIRM
