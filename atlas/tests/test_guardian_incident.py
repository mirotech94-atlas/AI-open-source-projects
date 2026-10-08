"""Guardian incident response (Phase 18.6, reel DZteY4hJs29; rewritten Task 52
against the hub's real write-scope surface).

The reel narrated an assistant blocking C2 beacons, killing processes and
disabling an account "without asking". What transfers is the playbook, the
classification and the narration. What does not is the acting: containment on
a network Miro or a client depends on is externally visible, so every write
here is Tier 2, and none of the three actions exists unless a separate write
token is deliberately set.

Task 52 re-read the hub's write-scope surface end to end
(`hub/guardian_hub/routers/api_v1.py`, Guardian commit bb6c439) and found
`POST /v1/containments` was never real: v1 has no containment route at all.
`guardian.contain` now refuses every kind, unconditionally, and the real
write surface `POST /v1/alerts/{key}/{op}` and `POST /v1/scans` gets its own
action each: `guardian.alert_op`, `guardian.rescan`. The third,
`POST /v1/devices/{mac}` (a trust label), is never written by ATLAS (Miro,
2026-09-28, RA-C6: "suggest only"); tests/test_ra_c6_guardian_suggest_only.py
pins that.
"""
from __future__ import annotations

import ast
import pathlib
from datetime import UTC, datetime, timedelta

import pytest

from atlas.kernel.contracts import Health, Tier
from atlas.kernel.permissions import classify
from atlas.modules import guardian
from atlas.modules.guardian import incident


@pytest.fixture
def one_tenant(monkeypatch):
    """Both Guardian tokens name one account (`readback.same_tenant`), which
    every write now confirms first; tests/test_acceptance_access.py covers
    the refusal when they do not."""
    async def same():
        return None

    monkeypatch.setattr(guardian, "_tenant_refusal", same)

ROOT = pathlib.Path(__file__).resolve().parent.parent / "atlas"
PKG = ROOT / "modules" / "guardian"

T0 = datetime(2026, 9, 24, 3, 14, 0, tzinfo=UTC)

NET_OFFICE = {"id": "net_1", "slug": "office", "name": "Office", "colour": "#4af"}
NET_GUEST = {"id": "net_2", "slug": "guest", "name": "Guest", "colour": "#fa4"}
MAC = "AA:BB:CC:DD:EE:01"


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _bundle(key: str, *, evidence: dict | None = None,
            first_seen: datetime = T0) -> dict:
    return {
        "exported_at": _iso(T0 + timedelta(minutes=5)),
        "alert": {
            "key": key, "severity": "WARN",
            "message": f"finding {key}", "why": "because",
            "evidence": evidence or {}, "confidence": 70,
            "first_seen": _iso(first_seen), "last_seen": _iso(first_seen),
            "count": 4, "status": "open",
            # Verified absent from the real evidence bundle
            # (`api_v1.py::alert_evidence` hand-builds this dict without a
            # `network` key) -- nothing here ever adds one.
        },
    }


def _alert(key: str, *, first_seen: datetime = T0, status: str = "open",
           network: dict | None = None) -> dict:
    out = {"key": key, "sev": "WARN", "msg": f"finding {key}", "why": "",
           "confidence": "70", "first_seen": _iso(first_seen),
           "last_seen": _iso(first_seen), "count": 3, "status": status}
    if network is not None:
        out["network"] = network
    return out


def _fix(host: str, at: datetime, action: str = "block_ip",
         ok: bool = True) -> dict:
    return {"ts": _iso(at), "host": host, "action": action, "label": "",
            "arg": "203.0.113.9", "by": "auto", "ok": ok,
            "verified_at": None, "reboot_needed": False, "detail": ""}


def hub(monkeypatch, *, bundle: dict | None = None, alerts: list | None = None,
        fixes: list | None = None, down: str | None = None,
        open_total: int | None = None) -> list[tuple]:
    """Stand the hub in for `incident._read`, recording every path asked."""
    seen: list[tuple] = []

    async def fake_read(path: str, **params):
        seen.append((path, params))
        if down:
            return None, down
        if path.endswith("/evidence"):
            return bundle, None
        if path == "/alerts":
            total = len(alerts or [])
            if params.get("status") == "open" and open_total is not None:
                total = open_total
            return {"items": alerts or [], "total": total}, None
        if path == "/fixes":
            return {"items": fixes or [], "total": len(fixes or [])}, None
        raise AssertionError(f"unexpected hub path {path}")

    monkeypatch.setattr(incident, "_read", fake_read)
    return seen


def _action(name: str):
    return next(a for a in guardian.MODULE.actions() if a.name == name)


# ------------------------------------------------------------------ analyse

async def test_analyse_says_why_when_the_hub_is_unreachable(monkeypatch):
    hub(monkeypatch, down="unreachable: ConnectError")
    plan, reason = await incident.analyse("dns:beacon:10.0.0.5:evil.example")
    assert plan is None
    assert "unreachable" in reason


async def test_analyse_never_invents_a_reputation(monkeypatch):
    """The hub's public Alert has no reputation field. None is the answer
    when the sensor did not supply one — not 'unknown', not 'suspicious',
    and never a lookup ATLAS made up."""
    key = "dns:beacon:10.0.0.5:evil.example"
    hub(monkeypatch, bundle=_bundle(key), alerts=[_alert(key)])
    plan, reason = await incident.analyse(key)
    assert reason is None and plan is not None
    assert plan.vectors[0].reputation is None


async def test_analyse_reads_reputation_from_the_hubs_own_field(monkeypatch):
    key = "dns:beacon:10.0.0.5:evil.example"
    hub(monkeypatch, bundle=_bundle(key, evidence={"reputation": "hostile"}),
        alerts=[_alert(key)])
    plan, _ = await incident.analyse(key)
    assert plan.vectors[0].reputation == "hostile"


async def test_every_fact_carries_the_time_it_was_read(monkeypatch):
    key = "sec:attacker:web1:203.0.113.9"
    hub(monkeypatch, bundle=_bundle(key), alerts=[_alert(key)])
    plan, _ = await incident.analyse(key)
    for vector in plan.vectors:
        assert vector.evidence.get("observed_at")
        assert vector.evidence.get("source") == "guardian hub"
    for step in plan.steps:
        assert step.get("observed_at")
    for entry in plan.manual:
        assert entry.get("observed_at")


async def test_an_account_nobody_named_is_not_guessed(monkeypatch):
    """`sec:users:{host}` names the host, not the account. Without the
    hub's evidence naming it there is no account to propose disabling."""
    key = "sec:users:web1"
    hub(monkeypatch, bundle=_bundle(key), alerts=[_alert(key)])
    plan, _ = await incident.analyse(key)
    assert not any(s["params"].get("kind") == "disable_account" for s in plan.steps)
    assert plan.steps == []
    assert plan.manual == []


# ------------------------------------------------------ contain is manual now

async def test_a_beacon_is_the_client_and_the_domain_never_a_resolved_ip(
        monkeypatch):
    """`dns:beacon:{ip}:{domain}` — the ip is the CLIENT that is beaconing.
    Without a network the hub's own records name, the domain is never
    resolved to an address to block, and there is no automatic route:
    isolating the client becomes Miro's, on the router."""
    key = "dns:beacon:10.0.0.5:evil.example"
    hub(monkeypatch, bundle=_bundle(key), alerts=[_alert(key)])
    plan, _ = await incident.analyse(key)
    v = plan.vectors[0]
    assert v.kind == "beacon"
    assert v.target == "evil.example"
    assert v.evidence["host"] == "10.0.0.5"
    assert plan.steps == []
    (entry,) = plan.manual
    assert entry["kind"] == "isolate_host"
    assert entry["target"] == "10.0.0.5"
    assert "no network" in entry["note"]


async def test_steps_are_smallest_first_and_every_one_is_tier_2(monkeypatch):
    """No beacon here, so nothing v1 exposes can automate any of these three
    -- an attacker IP, a miner's host, a named account -- and all three land
    as MANUAL, never as a step."""
    key = "sec:users:web1"
    alerts = [
        _alert(key),
        _alert("sec:miner:web1"),
        _alert("sec:attacker:web1:203.0.113.9"),
        _alert("sec:attacker:other:198.51.100.7"),     # another host: not this incident
    ]
    hub(monkeypatch, bundle=_bundle(key, evidence={"account": "backupsvc"}),
        alerts=alerts)
    plan, _ = await incident.analyse(key)
    assert plan.steps == []
    kinds = sorted(m["kind"] for m in plan.manual)
    assert kinds == ["block_ip", "disable_account", "isolate_host"]
    targets = {m["target"] for m in plan.manual}
    assert "198.51.100.7" not in targets
    # The named account is Miro's: credential revocation is Tier 3 for ATLAS.
    account = next(m for m in plan.manual if m["kind"] == "disable_account")
    assert account["target"] == "backupsvc" and account["note"] == incident.MANUAL_NOTE


async def test_attacker_and_login_vectors_are_manual_block_ip(monkeypatch):
    key = "sec:attacker:web1:203.0.113.9"
    hub(monkeypatch, bundle=_bundle(key), alerts=[_alert(key)])
    plan, _ = await incident.analyse(key)
    assert plan.steps == []
    (entry,) = plan.manual
    assert entry == {"kind": "block_ip", "target": "203.0.113.9",
                     "why": entry["why"], "note": incident.CONTAIN_MANUAL_NOTE,
                     "observed_at": entry["observed_at"]}
    assert "no containment API" in entry["note"]
    assert "router" in entry["note"]


async def test_two_vectors_naming_the_same_host_share_one_manual_entry(
        monkeypatch):
    """A miner and a persistence finding on one host would once have shared
    one isolate_host STEP; with no containment API, they now share one
    isolate_host MANUAL entry instead, and both vectors still point at it."""
    key = "sec:miner:web1"
    hub(monkeypatch, bundle=_bundle(key),
        alerts=[_alert(key), _alert("sec:cron:web1")])
    plan, _ = await incident.analyse(key)
    assert len(plan.vectors) == 2
    assert plan.steps == []
    assert len(plan.manual) == 1                 # deduplicated
    assert plan.manual[0]["kind"] == "isolate_host"
    assert plan.manual[0]["target"] == "web1"
    out = incident.as_dict(plan)
    for vector in out["vectors"]:
        assert vector["step"] is None
        assert vector["manual"] == {"kind": "isolate_host", "target": "web1",
                                    "note": incident.CONTAIN_MANUAL_NOTE}
    lines = incident.narrate(plan).splitlines()
    assert all("MANUAL — isolate web1" in line for line in lines)


# ---------------------------------------------- the beacon playbook (task 52)

async def test_active_beacon_with_mac_and_network_suggests_and_rescans(
        monkeypatch):
    """RA-C6: the trust change is a suggestion with a Vault link, never a
    step; the rescan is still proposed."""
    key = "dns:beacon:10.0.0.5:evil.example"
    hub(monkeypatch, bundle=_bundle(key, evidence={"mac": MAC}),
        alerts=[_alert(key, network=NET_OFFICE)])
    plan, _ = await incident.analyse(key)
    assert [s["action"] for s in plan.steps] == ["guardian.rescan"]
    assert plan.steps[0]["params"] == {"network": "office", "alert_key": key}
    (entry,) = plan.manual
    assert entry["kind"] == "mark_untrusted" and entry["target"] == MAC
    assert entry["network"] == "office"
    assert entry["link"] == incident.device_link(MAC)
    assert "never changes a Vault trust label" in entry["note"]
    out = incident.as_dict(plan)
    assert out["vectors"][0]["step"] is None
    assert out["vectors"][0]["manual"]["link"] == entry["link"]
    line = incident.narrate(plan)
    assert "SUGGESTED — mark AA:BB:CC:DD:EE:01 untrusted on office in Vault" in line
    assert "awaiting Miro" not in line


async def test_active_beacon_with_network_but_no_mac_still_rescans(monkeypatch):
    """The hub named no mac for this device (the sensor's evidence blob had
    none). mark_untrusted cannot fire honestly, but a rescan does not need a
    mac, so it still runs, and the missing mac is named plainly."""
    key = "dns:beacon:10.0.0.5:evil.example"
    hub(monkeypatch, bundle=_bundle(key), alerts=[_alert(key, network=NET_OFFICE)])
    plan, _ = await incident.analyse(key)
    assert [s["action"] for s in plan.steps] == ["guardian.rescan"]
    assert plan.steps[0]["params"] == {"network": "office", "alert_key": key}
    (entry,) = plan.manual
    assert entry["kind"] == "mark_untrusted"
    assert entry["target"] == "10.0.0.5"
    assert "no MAC" in entry["note"]


async def test_active_beacon_with_no_network_at_all_is_fully_manual(monkeypatch):
    key = "dns:beacon:10.0.0.5:evil.example"
    hub(monkeypatch, bundle=_bundle(key, evidence={"mac": MAC}), alerts=[_alert(key)])
    plan, _ = await incident.analyse(key)
    assert plan.steps == []
    (entry,) = plan.manual
    assert entry["kind"] == "isolate_host"
    assert "no network" in entry["note"]


async def test_the_networks_own_record_is_cross_referenced_not_guessed(
        monkeypatch):
    """The evidence bundle's own `alert` dict never carries `network`
    (verified absent). This alert's row in the open-alerts page — the SAME
    alert, read a different way — does, and that is where it comes from."""
    key = "dns:beacon:10.0.0.5:evil.example"
    hub(monkeypatch, bundle=_bundle(key, evidence={"mac": MAC}),
        alerts=[_alert(key, network=NET_GUEST)])
    plan, _ = await incident.analyse(key)
    assert plan.vectors[0].network == NET_GUEST
    assert plan.steps[0]["params"]["network"] == "guest"


async def test_a_beacon_alert_missing_from_both_pages_gets_no_network(
        monkeypatch):
    """The primary alert's own row is absent from the open-alerts page here
    (a stale test double, or a hub whose evidence route lags the alert
    list) -- network stays unknown rather than being assumed from anywhere."""
    key = "dns:beacon:10.0.0.5:evil.example"
    hub(monkeypatch, bundle=_bundle(key, evidence={"mac": MAC}), alerts=[])
    plan, _ = await incident.analyse(key)
    assert plan.vectors[0].network is None
    assert plan.steps == []


async def test_process_and_persistence_vectors_never_get_the_beacon_route(
        monkeypatch):
    """mark_untrusted/rescan are proposed only for an actual beacon vector,
    never for a miner or a persistence finding, even with mac/network
    present in the primary's own evidence (they would only ever apply to
    the primary anyway, and these are not beacons)."""
    key = "sec:miner:web1"
    hub(monkeypatch, bundle=_bundle(key, evidence={"mac": MAC}),
        alerts=[_alert(key, network=NET_OFFICE)])
    plan, _ = await incident.analyse(key)
    assert plan.steps == []
    assert plan.manual and plan.manual[0]["kind"] == "isolate_host"


# ------------------------------------------------------------ the operator

@pytest.mark.parametrize("gap, host, expected", [
    (119, "10.0.0.5", "live"),
    (120, "10.0.0.5", "live"),
    (121, "10.0.0.5", "unknown"),
    (30, "10.0.0.99", "unknown"),        # a block on another host
    (-30, "10.0.0.5", "unknown"),        # the beacon came BEFORE the block
])
async def test_operator_is_live_only_on_the_120_second_rule(
        monkeypatch, gap, host, expected):
    key = "dns:beacon:10.0.0.5:evil.example"
    block_at = T0
    alerts = [
        _alert(key, first_seen=T0 - timedelta(hours=1)),
        _alert("dns:beacon:10.0.0.5:next.example",
               first_seen=block_at + timedelta(seconds=gap)),
    ]
    hub(monkeypatch, bundle=_bundle(key, first_seen=T0 - timedelta(hours=1)),
        alerts=alerts, fixes=[_fix(host, block_at)])
    plan, _ = await incident.analyse(key)
    assert plan.operator == expected


async def test_operator_is_never_called_a_bot_by_inference(monkeypatch):
    """The brief gives no positive rule for 'bot', so it is never emitted:
    a block that nothing reacted to is absence of evidence, not a verdict."""
    key = "dns:beacon:10.0.0.5:evil.example"
    hub(monkeypatch, bundle=_bundle(key), alerts=[_alert(key)],
        fixes=[_fix("10.0.0.5", T0 + timedelta(hours=2))])
    plan, _ = await incident.analyse(key)
    assert plan.operator == "unknown"


async def test_a_failed_block_is_not_a_block(monkeypatch):
    key = "dns:beacon:10.0.0.5:evil.example"
    alerts = [_alert(key, first_seen=T0 - timedelta(hours=1)),
              _alert("dns:beacon:10.0.0.5:b.example",
                     first_seen=T0 + timedelta(seconds=10))]
    hub(monkeypatch, bundle=_bundle(key), alerts=alerts,
        fixes=[_fix("10.0.0.5", T0, ok=False)])
    plan, _ = await incident.analyse(key)
    assert plan.operator == "unknown"


# ---------------------------------------------------------------- narrate

async def test_narrate_says_awaiting_miro_not_severed(monkeypatch):
    key = "sec:attacker:web1:203.0.113.9"
    hub(monkeypatch, bundle=_bundle(key, evidence={"reputation": "hostile"}),
        alerts=[_alert(key), _alert("dns:beacon:web1:evil.example")])
    plan, _ = await incident.analyse(key)
    text = incident.narrate(plan)
    lines = text.splitlines()
    assert len(lines) == len(plan.vectors)
    assert lines[0].startswith(f"VECTOR 1/{len(plan.vectors)} — ")
    assert "203.0.113.9" in lines[0]
    assert "reputation: hostile (hub)" in lines[0]
    assert "MANUAL — block 203.0.113.9" in lines[0]
    low = text.lower()
    for word in ("severed", "neutralised", "neutralized", "blocked", "killed",
                 "disabled"):
        assert word not in low, word


async def test_narrate_says_unknown_when_the_hub_gave_no_reputation(monkeypatch):
    key = "dns:beacon:10.0.0.5:evil.example"
    hub(monkeypatch, bundle=_bundle(key), alerts=[_alert(key)])
    plan, _ = await incident.analyse(key)
    line = incident.narrate(plan)
    assert "reputation: unknown" in line
    assert "(hub)" not in line


# ---------------------------------------------------------------- contain

async def test_contain_always_refuses_and_never_posts(monkeypatch):
    monkeypatch.setenv("GUARDIAN_WRITE_TOKEN", "write-token-value-456")
    posted: list = []

    async def fake_post(*a, **k):
        posted.append((a, k))
        return {"id": "should-never-happen"}, None

    monkeypatch.setattr(incident, "post_json", fake_post)
    for kind, target in (("block_ip", "203.0.113.9"), ("isolate_host", "web1")):
        data, reason = await incident.contain("k", target, kind)
        assert data is None
        assert reason == incident.CONTAIN_UNAVAILABLE
    assert posted == []


async def test_contain_refuses_without_the_write_token_too(monkeypatch):
    monkeypatch.delenv("GUARDIAN_WRITE_TOKEN", raising=False)
    data, reason = await incident.contain("k", "203.0.113.9", "block_ip")
    assert data is None
    assert reason == incident.CONTAIN_UNAVAILABLE


@pytest.mark.parametrize("kind, target", [
    ("drop_table", "203.0.113.9"),          # not a containment kind at all
    ("block_ip", "evil.example"),           # a domain is a DNS change, not an IP
    ("block_ip", ""),
])
async def test_contain_still_validates_the_values_via_bad_params(
        monkeypatch, kind, target):
    """`_bad_params` stays for the closed-catalog value check even though
    `refusal` no longer calls it (every valid kind refuses regardless of
    the target's shape) -- other callers may still want it."""
    reason = incident._bad_params("k", target, kind)
    assert reason


def test_all_three_actions_exist_only_when_the_write_token_is_set(monkeypatch):
    monkeypatch.delenv("GUARDIAN_WRITE_TOKEN", raising=False)
    monkeypatch.setenv("GUARDIAN_API_TOKEN", "read-token-value-123")
    assert guardian.MODULE.actions() == ()

    monkeypatch.setenv("GUARDIAN_WRITE_TOKEN", "write-token-value-456")
    names = {a.name for a in guardian.MODULE.actions()}
    assert names == {"guardian.contain", "guardian.alert_op", "guardian.rescan"}
    for action in guardian.MODULE.actions():
        assert action.tier is Tier.ASK
        assert action.dry_run is not None
        decision = classify(action)
        assert decision.allowed and decision.tier is Tier.ASK


def test_contain_action_shape(monkeypatch):
    monkeypatch.setenv("GUARDIAN_WRITE_TOKEN", "write-token-value-456")
    action = _action("guardian.contain")
    assert action.rollback is not None
    assert action.irreversible
    assert set(incident.KINDS) == {"block_ip", "isolate_host"}
    assert set(action.params) == {"alert_id", "target", "kind"}


def test_the_write_token_is_optional_env_and_never_required(monkeypatch):
    assert "GUARDIAN_WRITE_TOKEN" in guardian.MODULE.optional_env
    assert "GUARDIAN_WRITE_TOKEN" not in guardian.MODULE.requires_env


async def test_the_contain_dry_run_never_says_would_post(monkeypatch):
    secret = "write-token-value-456"
    monkeypatch.setenv("GUARDIAN_WRITE_TOKEN", secret)
    action = _action("guardian.contain")
    result = await action.dry_run(alert_id="sec:attacker:web1:203.0.113.9",
                                  target="203.0.113.9", kind="block_ip")
    assert result.ok is False
    assert "would" not in result.detail.lower()
    assert incident.CONTAIN_UNAVAILABLE in result.detail
    assert secret not in result.detail
    assert secret not in repr(result.evidence)


async def test_contain_run_and_rollback_both_refuse(monkeypatch):
    monkeypatch.setenv("GUARDIAN_WRITE_TOKEN", "write-token-value-456")
    action = _action("guardian.contain")
    ran = await action.run(alert_id="k", target="203.0.113.9", kind="block_ip")
    assert ran.ok is False
    assert incident.CONTAIN_UNAVAILABLE in ran.detail
    rolled = await action.rollback('{"id": "c1"}')
    assert rolled.ok is False


async def test_reads_never_carry_the_write_token(monkeypatch):
    """The two tokens are separate on purpose: every check reads with the
    read token, and nothing but the write actions may see the write one."""
    monkeypatch.setenv("GUARDIAN_API_TOKEN", "read-token-value-123")
    monkeypatch.setenv("GUARDIAN_WRITE_TOKEN", "write-token-value-456")
    seen: list = []

    async def fake_get(url, **kw):
        seen.append(kw.get("headers"))
        return {"items": [], "total": 0}, None

    monkeypatch.setattr(guardian, "get_json", fake_get)
    await guardian.check_open_incidents()
    assert seen
    assert all("write-token-value-456" not in str(h) for h in seen)


# ------------------------------------------------------------- alert_op (T2)

async def test_alert_op_dry_run_shows_the_exact_call(monkeypatch):
    secret = "write-token-value-456"
    monkeypatch.setenv("GUARDIAN_WRITE_TOKEN", secret)
    action = _action("guardian.alert_op")
    result = await action.dry_run(alert_key="dns:beacon:10.0.0.5:evil.example",
                                  op="snooze", hours=8)
    assert result.ok is True
    assert "would POST" in result.detail
    assert "/alerts/" in result.detail and "/snooze" in result.detail
    assert '"hours": 8' in result.detail
    assert "<GUARDIAN_WRITE_TOKEN>" in result.detail
    assert secret not in result.detail


async def test_alert_op_refuses_without_the_write_token(monkeypatch):
    monkeypatch.delenv("GUARDIAN_WRITE_TOKEN", raising=False)
    data, reason = await incident.alert_op("k", "ack")
    assert data is None
    assert reason == incident.NO_WRITE_TOKEN


@pytest.mark.parametrize("op, hours, why_fragment", [
    ("delete", None, "op must be one of"),
    ("snooze", None, "needs hours"),
    ("snooze", 0, "between"),
    ("snooze", 169, "between"),
    ("snooze", 8.5, "whole number"),
    ("ack", 8, "only for snooze"),
])
async def test_alert_op_validates_values(monkeypatch, op, hours, why_fragment):
    monkeypatch.setenv("GUARDIAN_WRITE_TOKEN", "write-token-value-456")
    data, reason = await incident.alert_op("k", op, hours)
    assert data is None
    assert why_fragment in reason


@pytest.mark.parametrize("bad_key", ["", "has space", "has\ttab", "has?query",
                                      "has#frag", "line\nbreak"])
async def test_alert_op_refuses_a_malformed_key(monkeypatch, bad_key):
    monkeypatch.setenv("GUARDIAN_WRITE_TOKEN", "write-token-value-456")
    data, reason = await incident.alert_op(bad_key, "ack")
    assert data is None and reason


async def test_alert_op_quotes_the_key_and_never_posts_on_refusal(monkeypatch):
    monkeypatch.delenv("GUARDIAN_WRITE_TOKEN", raising=False)
    posted = []

    async def fake_post(*a, **k):
        posted.append((a, k))
        return {}, None

    monkeypatch.setattr(incident, "post_json", fake_post)
    await incident.alert_op("dns:beacon:10.0.0.5:evil.example", "ack")
    assert posted == []
    call = incident.alert_op_call("has space/slash", "ack", None)
    assert "has space/slash" not in call["url"]
    assert "%20" in call["url"] or "%2F" in call["url"]


async def test_alert_op_run_posts_and_returns_a_rollback_token(monkeypatch, one_tenant):
    monkeypatch.setenv("GUARDIAN_WRITE_TOKEN", "write-token-value-456")
    posted = []

    async def fake_post(url, **kw):
        posted.append((url, kw))
        return {"key": "k", "status": "acked"}, None

    monkeypatch.setattr(incident, "post_json", fake_post)
    action = _action("guardian.alert_op")
    ran = await action.run(alert_key="k", op="ack")
    assert ran.ok is True
    assert ran.rollback_token
    assert posted[0][0] == f"{incident.API}/alerts/k/ack"
    assert "write-token-value-456" in str(posted[0][1]["headers"])


async def test_alert_op_run_reads_the_alerts_prior_status_first(monkeypatch, one_tenant):
    """A fixed op-to-op pairing (ack -> reopen) would reopen an alert that
    was actually only snoozed. The rollback token must carry the status the
    alert ACTUALLY held, read before `alert_op` ever writes."""
    monkeypatch.setenv("GUARDIAN_WRITE_TOKEN", "write-token-value-456")
    monkeypatch.setenv("GUARDIAN_API_TOKEN", "read-token-value-123")
    read_calls = []

    async def fake_get(url, **kw):
        read_calls.append(url)
        return {"key": "k", "status": "acked"}, None

    async def fake_post(url, **kw):
        return {"key": "k", "status": "open"}, None

    monkeypatch.setattr(guardian, "get_json", fake_get)
    monkeypatch.setattr(incident, "post_json", fake_post)
    action = _action("guardian.alert_op")
    ran = await action.run(alert_key="k", op="reopen")
    assert ran.ok is True
    assert read_calls
    import json as _json
    assert _json.loads(ran.rollback_token)["prior_status"] == "acked"


@pytest.mark.parametrize("prior_status, expected_reverse", [
    ("open", "reopen"), ("acked", "ack"), ("resolved", "resolve"),
])
async def test_alert_op_rollback_restores_the_alerts_actual_prior_status(
        monkeypatch, prior_status, expected_reverse):
    monkeypatch.setenv("GUARDIAN_WRITE_TOKEN", "write-token-value-456")
    posted = []

    async def fake_post(url, **kw):
        posted.append(url)
        return {"key": "k", "status": "ok"}, None

    monkeypatch.setattr(incident, "post_json", fake_post)
    action = _action("guardian.alert_op")
    import json as _json
    token = _json.dumps({"alert_key": "k", "op": "ack", "prior_status": prior_status})
    rolled = await action.rollback(token)
    assert rolled.ok is True
    assert posted == [f"{incident.API}/alerts/k/{expected_reverse}"]


async def test_alert_op_rollback_refuses_to_restore_a_snooze(monkeypatch):
    """A prior 'snoozed' status has no restoring op -- the hub does not
    report a snooze's remaining duration, so guessing one is refused."""
    monkeypatch.setenv("GUARDIAN_WRITE_TOKEN", "write-token-value-456")
    posted = []

    async def fake_post(url, **kw):
        posted.append(url)
        return {"key": "k", "status": "ok"}, None

    monkeypatch.setattr(incident, "post_json", fake_post)
    action = _action("guardian.alert_op")
    import json as _json
    token = _json.dumps({"alert_key": "k", "op": "ack", "prior_status": "snoozed"})
    rolled = await action.rollback(token)
    assert rolled.ok is False
    assert posted == []


async def test_alert_op_rollback_refuses_when_prior_status_unknown(monkeypatch):
    monkeypatch.setenv("GUARDIAN_WRITE_TOKEN", "write-token-value-456")
    posted = []

    async def fake_post(url, **kw):
        posted.append(url)
        return {"key": "k", "status": "ok"}, None

    monkeypatch.setattr(incident, "post_json", fake_post)
    action = _action("guardian.alert_op")
    import json as _json
    token = _json.dumps({"alert_key": "k", "op": "ack", "prior_status": None})
    rolled = await action.rollback(token)
    assert rolled.ok is False
    assert posted == []


async def test_alert_op_rollback_refuses_a_bad_token(monkeypatch):
    monkeypatch.setenv("GUARDIAN_WRITE_TOKEN", "write-token-value-456")
    from atlas.modules.guardian import _alert_op_rollback
    result = await _alert_op_rollback("not json")
    assert result.ok is False


# ---------------------------------------------------------------- rescan (T2)

async def test_rescan_dry_run_says_network_is_not_sent(monkeypatch):
    monkeypatch.setenv("GUARDIAN_WRITE_TOKEN", "write-token-value-456")
    action = _action("guardian.rescan")
    result = await action.dry_run(network="office", alert_key="k")
    assert result.ok is True
    assert f"{incident.API}/scans" in result.detail
    assert "not sent" in result.detail


async def test_rescan_refuses_without_the_write_token(monkeypatch):
    monkeypatch.delenv("GUARDIAN_WRITE_TOKEN", raising=False)
    data, reason = await incident.rescan("office", "k")
    assert data is None
    assert reason == incident.NO_WRITE_TOKEN


@pytest.mark.parametrize("network, alert_key, ok", [
    ("office", "k", True),
    ("bad network!", "k", False),
    ("office", "has space", False),
    ("office", "", True),
])
async def test_rescan_validates_network_and_alert_key(monkeypatch, network, alert_key, ok):
    monkeypatch.setenv("GUARDIAN_WRITE_TOKEN", "write-token-value-456")
    why = incident.rescan_refusal(network, alert_key)
    assert (why is None) is ok


async def test_rescan_posts_with_no_body_and_no_network(monkeypatch, one_tenant):
    monkeypatch.setenv("GUARDIAN_WRITE_TOKEN", "write-token-value-456")
    posted = []

    async def fake_post(url, **kw):
        posted.append((url, kw))
        return {"id": "job-1", "status": "queued"}, None

    monkeypatch.setattr(incident, "post_json", fake_post)
    action = _action("guardian.rescan")
    ran = await action.run(network="office", alert_key="k")
    assert ran.ok is True and ran.rollback_token
    url, kw = posted[0]
    assert url == f"{incident.API}/scans"
    assert kw.get("params") is None
    assert kw.get("json_body") is None


async def test_rescan_without_a_job_id_is_a_failure(monkeypatch, one_tenant):
    async def fake_post(*a, **k):
        return {"status": "accepted"}, None

    monkeypatch.setenv("GUARDIAN_WRITE_TOKEN", "write-token-value-456")
    monkeypatch.setattr(incident, "post_json", fake_post)
    action = _action("guardian.rescan")
    ran = await action.run(network="office", alert_key="k")
    assert ran.ok is False
    assert ran.rollback_token is None


async def test_rescan_rollback_honestly_fails(monkeypatch):
    monkeypatch.setenv("GUARDIAN_WRITE_TOKEN", "write-token-value-456")
    action = _action("guardian.rescan")
    assert action.irreversible
    rolled = await action.rollback('{"job": "job-1"}')
    assert rolled.ok is False
    assert "cannot be un-run" in rolled.detail


# -------------------------------------------------------------- open incidents

async def test_open_incidents_is_unknown_without_a_token(monkeypatch):
    monkeypatch.delenv("GUARDIAN_API_TOKEN", raising=False)
    obs = await guardian.check_open_incidents()
    assert obs.health is Health.UNKNOWN
    assert obs.value is None


async def test_open_incidents_is_unknown_on_a_shape_it_did_not_expect(monkeypatch):
    monkeypatch.setenv("GUARDIAN_API_TOKEN", "read-token-value-123")

    async def fake_get(url, **kw):
        return {"what": "no items"}, None

    monkeypatch.setattr(guardian, "get_json", fake_get)
    obs = await guardian.check_open_incidents()
    assert obs.health is Health.UNKNOWN
    assert obs.value is None


async def test_open_incidents_counts_and_carries_the_plan(monkeypatch):
    monkeypatch.setenv("GUARDIAN_API_TOKEN", "read-token-value-123")
    asked: list = []

    async def fake_get(url, **kw):
        asked.append((url, kw.get("params")))
        return {"items": [{"iid": "101", "entity": "web1", "sev": "CRIT",
                           "story": "a story", "opened": _iso(T0),
                           "alert_keys": ["sec:attacker:web1:203.0.113.9"],
                           "closed": None}], "total": 1}, None

    monkeypatch.setattr(guardian, "get_json", fake_get)
    key = "sec:attacker:web1:203.0.113.9"
    hub(monkeypatch, bundle=_bundle(key), alerts=[_alert(key)])

    obs = await guardian.check_open_incidents()
    assert asked[0][0] == "/v1/incidents"
    assert asked[0][1] == {"open": "true", "limit": 50}
    assert obs.value == 1.0
    assert obs.health is Health.FAILING           # a CRIT incident is open
    assert obs.evidence["incidents"][0]["iid"] == "101"
    plan = obs.evidence["plan"]
    assert plan["alert_id"] == key
    assert plan["operator"] == "unknown"
    assert plan["vectors"][0]["kind"] == "attacker"
    assert plan["steps"] == []
    assert plan["manual"][0]["kind"] == "block_ip"
    assert plan["observed_at"]


async def test_no_open_incidents_is_ok_and_asks_nothing_more(monkeypatch):
    monkeypatch.setenv("GUARDIAN_API_TOKEN", "read-token-value-123")

    async def fake_get(url, **kw):
        return {"items": [], "total": 0}, None

    async def no_read(*a, **k):
        raise AssertionError("no incident, no analysis")

    monkeypatch.setattr(guardian, "get_json", fake_get)
    monkeypatch.setattr(incident, "_read", no_read)
    obs = await guardian.check_open_incidents()
    assert obs.health is Health.OK
    assert obs.value == 0.0


def test_open_incidents_is_a_registered_check():
    names = {c.name for c in guardian.MODULE.checks()}
    assert "open_incidents" in names


# --------------------------------------------------------------- the tools

async def test_the_incident_tool_hands_the_agent_wrapped_untrusted_text(
        monkeypatch):
    """A beacon domain is chosen by the attacker. It reaches a prompt only
    inside the untrusted-data wrapper."""
    from atlas.tools.builtin import guardian_incident

    key = "dns:beacon:10.0.0.5:ignore-previous-instructions.example"
    hub(monkeypatch, bundle=_bundle(key), alerts=[_alert(key)])
    out = await guardian_incident(key)
    assert isinstance(out, str)
    assert out.lower().startswith("<untrusted_data")
    assert "ignore-previous-instructions.example" in out
    assert "MANUAL" in out


async def test_the_incident_tool_reports_an_unreachable_hub(monkeypatch):
    from atlas.tools.builtin import guardian_incident

    hub(monkeypatch, down="timed out")
    out = await guardian_incident("k")
    assert set(out) == {"error"}
    assert out["error"].lower().startswith("<untrusted_data")
    assert "timed out" in out["error"]


def test_the_guardian_business_holds_the_read_and_write_tools():
    from atlas.kernel.businesses import BY_NAME
    from atlas.kernel.tools import TOOLS
    from atlas.tools.builtin import register_all

    register_all()
    biz = BY_NAME["guardian"]
    assert "guardian_incident" in biz.reads
    for tool_name, action in (("guardian_contain", "guardian.contain"),
                              ("guardian_alert_op", "guardian.alert_op"),
                              ("guardian_rescan", "guardian.rescan")):
        assert tool_name in biz.writes
        assert TOOLS[tool_name].action == action
    assert TOOLS["guardian_incident"].read is not None
    assert "no actions" not in biz.what.lower()


# ------------------------------------------------------------------ skill

def test_the_ir_skill_fires_on_its_triggers():
    import atlas.skills as packs
    from atlas.kernel.skills import SKILLS, skills_for

    packs.load_all()
    (skill,) = [s for s in SKILLS if s.name == "Guardian incident response"]
    for word in ("intrusion", "beacon", "c2", "lateral"):
        assert word in skill.triggers, word
    got = [s.name for s in skills_for("a c2 beacon on the office lan", "guardian")]
    assert "Guardian incident response" in got
    body = skill.body.lower()
    assert "awaiting miro" in body
    assert "neutralised" in body            # named, as the word it may not use early


def test_the_skill_names_the_real_write_actions_not_containment(monkeypatch):
    import atlas.skills as packs
    from atlas.kernel.skills import SKILLS

    packs.load_all()
    (skill,) = [s for s in SKILLS if s.name == "Guardian incident response"]
    body = skill.body
    assert "guardian_mark_untrusted" not in body     # RA-C6: suggest only
    assert "suggest" in body.lower() and "vault" in body.lower()
    assert "guardian_rescan" in body
    assert "guardian_alert_op" in body
    assert "false positive" in body.lower()


# ------------------------------------------------------------ no executor

def _imports_of(path: pathlib.Path, parts: list[str]) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level:
                base = parts[: len(parts) - (node.level - 1)]
                mod = ".".join(base + ([mod] if mod else []))
            names.add(mod)
    return names


def test_no_file_in_the_module_imports_the_executor():
    files = list(PKG.glob("*.py"))
    assert len(files) >= 2
    for f in files:
        for mod in _imports_of(f, ["atlas", "modules", "guardian"]):
            assert "executor" not in mod, f"{f.name} imports {mod}"


def test_the_ir_skill_does_not_import_the_executor():
    path = ROOT / "skills" / "guardian_ir.py"
    assert path.is_file()
    for mod in _imports_of(path, ["atlas", "skills"]):
        assert "executor" not in mod


# ------------------------------------ fix round 1: disable_account is Tier 3

DENIED = "credential revocation"


@pytest.mark.parametrize("token", [None, "write-token-value-456"])
def test_disable_account_is_refused_by_name_with_or_without_the_token(
        monkeypatch, token):
    if token:
        monkeypatch.setenv("GUARDIAN_WRITE_TOKEN", token)
    else:
        monkeypatch.delenv("GUARDIAN_WRITE_TOKEN", raising=False)
    for why in (incident.refusal("k", "backupsvc", "disable_account"),
                incident._bad_params("k", "backupsvc", "disable_account")):
        assert why is not None
        assert "disable_account" in why and DENIED in why
        assert "read token" not in why
    assert "disable_account" not in incident.KINDS


async def test_the_dry_run_refuses_disable_account_by_name_and_never_posts(
        monkeypatch):
    monkeypatch.setenv("GUARDIAN_WRITE_TOKEN", "write-token-value-456")
    posted: list = []

    async def fake_post(*a, **k):
        posted.append((a, k))
        return {"id": "c1"}, None

    monkeypatch.setattr(incident, "post_json", fake_post)
    action = _action("guardian.contain")
    result = await action.dry_run(alert_id="sec:users:web1", target="backupsvc",
                                  kind="disable_account")
    assert result.ok is False
    assert "disable_account" in result.detail and DENIED in result.detail
    assert "would POST" not in result.detail
    ran = await action.run(alert_id="sec:users:web1", target="backupsvc",
                           kind="disable_account")
    assert ran.ok is False and DENIED in ran.detail
    assert posted == []


@pytest.mark.parametrize("token", [None, "write-token-value-456"])
async def test_contain_refuses_every_kind_regardless_of_token(monkeypatch, token):
    """No endpoint flag exists to flip any more (verified permanently
    absent): every kind refuses whether or not the write token is set."""
    if token:
        monkeypatch.setenv("GUARDIAN_WRITE_TOKEN", token)
    else:
        monkeypatch.delenv("GUARDIAN_WRITE_TOKEN", raising=False)
    posted: list = []

    async def fake_post(*a, **k):
        posted.append((a, k))
        return {"id": "c1"}, None

    monkeypatch.setattr(incident, "post_json", fake_post)
    for kind, target in (("disable_account", "backupsvc"),
                         ("block_ip", "203.0.113.9"), ("isolate_host", "web1")):
        data, reason = await incident.contain("k", target, kind)
        assert data is None and reason
    assert posted == []


async def test_a_named_account_is_a_manual_entry_never_a_step(monkeypatch):
    key = "sec:users:web1"
    hub(monkeypatch, bundle=_bundle(key, evidence={"user": "backupsvc"}),
        alerts=[_alert(key)])
    plan, _ = await incident.analyse(key)
    assert plan.steps == []
    (entry,) = plan.manual
    assert entry["kind"] == "disable_account"
    assert entry["target"] == "backupsvc"
    assert entry["note"] == "Tier 3 for ATLAS: credential revocation"
    line = incident.narrate(plan)
    assert "MANUAL — disable account backupsvc" in line
    assert "Tier 3 for ATLAS: credential revocation" in line
    assert "awaiting Miro" not in line and "proposed" not in line
    vec = incident.as_dict(plan)["vectors"][0]
    assert vec["step"] is None
    assert vec["manual"]["note"] == "Tier 3 for ATLAS: credential revocation"
    assert "credential revocation" in plan.say


# ------------------------------------------- fix round 1: partial pages

async def test_open_alerts_are_read_with_the_open_filter(monkeypatch):
    key = "sec:attacker:web1:203.0.113.9"
    seen = hub(monkeypatch, bundle=_bundle(key), alerts=[_alert(key)])
    await incident.analyse(key)
    assert ("/alerts", {"status": "open", "limit": 200}) in seen


async def test_a_truncated_page_never_says_none_open(monkeypatch):
    key = "sec:attacker:web1:203.0.113.9"
    hub(monkeypatch, bundle=_bundle(key), alerts=[_alert(key)], open_total=500)
    plan, _ = await incident.analyse(key)
    assert plan.persistence_checks
    for line in plan.persistence_checks:
        assert "none open" not in line
        assert "not checked beyond the first 1 open alert(s) of 500" in line
    assert "not checked" in plan.say


async def test_a_complete_page_may_say_none_open(monkeypatch):
    key = "sec:attacker:web1:203.0.113.9"
    hub(monkeypatch, bundle=_bundle(key), alerts=[_alert(key)])
    plan, _ = await incident.analyse(key)
    assert all("none open as of" in line for line in plan.persistence_checks)


async def test_open_incidents_reports_the_hubs_total_not_the_page(monkeypatch):
    monkeypatch.setenv("GUARDIAN_API_TOKEN", "read-token-value-123")
    page = [{"iid": str(i), "entity": "web1", "sev": "WARN", "opened": _iso(T0),
             "alert_keys": [], "closed": None} for i in range(50)]

    async def fake_get(url, **kw):
        return {"items": page, "total": 73}, None

    monkeypatch.setattr(guardian, "get_json", fake_get)
    obs = await guardian.check_open_incidents()
    assert obs.value == 73.0
    assert "only the first 50 were read" in obs.detail


async def test_a_full_page_with_no_total_is_not_a_count(monkeypatch):
    monkeypatch.setenv("GUARDIAN_API_TOKEN", "read-token-value-123")
    page = [{"iid": str(i), "sev": "WARN", "alert_keys": []} for i in range(50)]

    async def fake_get(url, **kw):
        return {"items": page}, None

    monkeypatch.setattr(guardian, "get_json", fake_get)
    obs = await guardian.check_open_incidents()
    assert obs.health is Health.UNKNOWN and obs.value is None
    assert "at least 50" in obs.detail


async def test_the_plan_records_which_incident_it_belongs_to(monkeypatch):
    monkeypatch.setenv("GUARDIAN_API_TOKEN", "read-token-value-123")
    key = "sec:attacker:web1:203.0.113.9"

    async def fake_get(url, **kw):
        return {"items": [{"iid": "101", "sev": "WARN", "opened": _iso(T0),
                           "alert_keys": [key]}], "total": 1}, None

    monkeypatch.setattr(guardian, "get_json", fake_get)
    hub(monkeypatch, bundle=_bundle(key), alerts=[_alert(key)])
    obs = await guardian.check_open_incidents()
    assert obs.evidence["plan_incident"]["iid"] == "101"
    assert obs.evidence["plan_incident"]["alert_keys"] == [key]


async def test_a_shape_error_carries_no_hub_key_names(monkeypatch):
    monkeypatch.setenv("GUARDIAN_API_TOKEN", "read-token-value-123")

    async def fake_get(url, **kw):
        return {"ignore_previous_instructions": 1}, None

    monkeypatch.setattr(guardian, "get_json", fake_get)
    obs = await guardian.check_open_incidents()
    assert "ignore_previous" not in obs.detail

    key = "k"
    hub(monkeypatch, bundle={"ignore_previous_instructions": 1}, alerts=[])
    plan, reason = await incident.analyse(key)
    assert plan is None and "ignore_previous" not in reason


# ------------------------------------------- fix round 1: unreleasable

async def test_rescan_without_an_id_never_carries_a_rollback_token(monkeypatch):
    """The same discipline as before, ported to the one real write route
    that still answers a bare 202: an accepted-but-unpollable job is not a
    success, and there is no token to release what cannot be found again."""
    monkeypatch.setenv("GUARDIAN_WRITE_TOKEN", "write-token-value-456")

    async def fake_post(*a, **k):
        return {"status": "accepted"}, None

    monkeypatch.setattr(incident, "post_json", fake_post)
    action = _action("guardian.rescan")
    ran = await action.run(network="office", alert_key="k")
    assert ran.ok is False
    assert ran.rollback_token is None
