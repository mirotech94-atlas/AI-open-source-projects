"""QA autopilot (atlas/modules/qa/autopilot.py): diagnose, fix through the
executor only when a Tier 1 action exists, ask once otherwise, never loop."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from atlas.kernel.contracts import Action, ActionResult, FailureClass, Tier
from atlas.modules.qa import autopilot as ap

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def finding(subject="research.scan_freshness", health="degraded", detail="",
            stale=False, kind="check", module=None):
    return ap.Finding(kind, subject, health, detail, {}, stale,
                      module or subject.split(".")[0])


class IO:
    """Records every side effect; each test sets only what it needs."""

    def __init__(self, findings, *, disabled=None, history=None, tiers=None,
                 fix_result=None, reread="ok", fixes=None):
        self._findings, self.disabled = findings, disabled or {}
        self._history = history or {}
        self.tiers = tiers if tiers is not None else {"research.daily_scan": Tier.AUTO}
        self.fix_result = fix_result or ActionResult(True, "ran", dispatched=True)
        self.reread_to, self.fixes = reread, fixes
        self.ran, self.tasks, self.cards, self.recorded = [], [], [], []

    async def findings(self):
        return self._findings

    def disabled_reason(self, module):
        return self.disabled.get(module)

    async def history(self, cause_key):
        return self._history.get(cause_key, ap.History(0, None))

    def tier_of(self, action):
        return self.tiers.get(action)

    async def run_fix(self, action, params, reason):
        self.ran.append((action, params))
        return self.fix_result

    async def reread(self, subject):
        return self.reread_to

    async def raise_task(self, owner, title, evidence, fix, ref):
        self.tasks.append((owner, title, ref, fix, evidence))
        return 1

    async def raise_card(self, action, params, reason, dedup_key):
        self.cards.append((action, dedup_key))
        return 7

    async def record(self, at, rows, report):
        self.recorded.append((rows, report))
        return 1


# --------------------------------------------------------------- classify

def test_missing_env_names_the_exact_variables():
    d = ap.classify(finding("fiverr.inbox", "unknown"), "missing env: FIVERR_KEY, FIVERR_SECRET")
    assert d.cause_class == "missing_credential" and d.who == "miro"
    assert d.env_missing == ("FIVERR_KEY", "FIVERR_SECRET")


def test_unset_variable_in_detail_is_a_missing_config():
    d = ap.classify(finding("qa.gate_health", "unknown",
                            "qa-browser off (QA_BROWSER_URL unset: screenshots UNKNOWN)"), None)
    assert d.cause_class == "missing_credential" and d.env_missing == ("QA_BROWSER_URL",)


def test_expired_token_stale_disk_and_real_fault():
    assert ap.classify(finding(detail="HTTP 401 from api"), None).cause_class == "expired_token"
    assert ap.classify(finding(detail="invalid_grant"), None).who == "miro"
    assert ap.classify(finding(detail="no space left on device"), None
                       ).cause_class == "resource"
    assert ap.classify(finding(stale=True), None).cause_class == "stale_data"
    d = ap.classify(finding("sites.build", "failing", "build exited 2"), None)
    assert d.cause_class == "real_fault" and d.who == "agent"


def test_unused_module_is_only_what_miro_declared(monkeypatch):
    monkeypatch.setenv("AUTOPILOT_UNUSED_MODULES", "fiverr, nexus")
    d = ap.classify(finding("fiverr.inbox", "unknown"), "missing env: FIVERR_KEY")
    assert d.cause_class == "unused_module" and d.who == "nobody"


def test_agent_failed_shift_is_classified_from_its_outcome():
    f = ap.Finding("agent", "agent:qa", "failing", "The local model is down", {}, False, "qa")
    assert ap.classify(f, None).cause_class == "real_fault"
    f = ap.Finding("agent", "agent:qa", "failing", "shift failed: ReservedForMiro", {},
                   False, "qa")
    assert ap.classify(f, None).cause_class == "resource"


def test_fix_actions_never_touch_the_forbidden_areas():
    for action, _p in ap.FIX_ACTIONS.values():
        assert not ap.forbidden(action)
    for name in ("bizops.send_invoice", "dns.update_record", "files.delete_old",
                 "vault.revoke_token", "atlas.modify_self", "stripe.refund"):
        assert ap.forbidden(name), name


# ------------------------------------------------------------------ cycle

@pytest.mark.asyncio
async def test_tier1_fix_runs_and_is_proved_by_a_reread():
    io = IO([finding(stale=True)])
    rep = await ap.run_cycle(io, NOW)
    assert io.ran == [("research.daily_scan", {})]
    assert rep.fixed == 1 and rep.waiting_on_miro == 0
    assert io.recorded[0][0][0]["outcome"] == "fixed"


@pytest.mark.asyncio
async def test_unknown_after_the_fix_is_not_fixed():
    io = IO([finding(stale=True)], reread="unknown")
    rep = await ap.run_cycle(io, NOW)
    assert rep.fixed == 0 and io.recorded[0][0][0]["outcome"] == "fix_failed"


@pytest.mark.asyncio
async def test_watch_only_hold_is_held_not_an_attempt():
    held = ActionResult(False, "watch-only", evidence={"executor_hold": "watch_only"},
                        failure_class=FailureClass.DISABLED, dispatched=False)
    io = IO([finding(stale=True)], fix_result=held)
    rep = await ap.run_cycle(io, NOW)
    assert rep.held == 1 and rep.fixed == 0 and rep.waiting_on_miro == 0
    assert io.tasks == []


@pytest.mark.asyncio
async def test_same_cause_twice_after_a_fix_escalates_once_and_does_not_retry():
    key = "research.scan_freshness:stale_data"
    io = IO([finding(stale=True)], history={key: ap.History(1, "fixed")})
    await ap.run_cycle(io, NOW)
    assert io.ran == [] and len(io.tasks) == 1
    assert io.recorded[0][0][0]["outcome"] == "escalated"
    # next cycle: already escalated, no retry, still one task only
    io2 = IO([finding(stale=True)], history={key: ap.History(1, "escalated")})
    await ap.run_cycle(io2, NOW)
    assert io2.ran == [] and io2.tasks == []


@pytest.mark.asyncio
async def test_daily_cap_escalates_instead_of_retrying():
    key = "research.scan_freshness:stale_data"
    io = IO([finding(stale=True)],
            history={key: ap.History(ap.MAX_ATTEMPTS_PER_DAY, "fix_failed")})
    await ap.run_cycle(io, NOW)
    assert io.ran == [] and len(io.tasks) == 1


@pytest.mark.asyncio
async def test_all_credential_causes_make_one_task_never_ok():
    io = IO([finding("fiverr.inbox", "unknown"), finding("qa.gate_health", "unknown",
                                                        "QA_BROWSER_URL unset")],
            disabled={"fiverr": "missing env: FIVERR_KEY"})
    rep = await ap.run_cycle(io, NOW)
    assert len(io.tasks) == 1 and io.ran == [] and io.cards == []
    owner, _title, ref, fix, _ev = io.tasks[0]
    assert owner == "Miro" and ref == "credentials"
    assert "FIVERR_KEY" in fix and "QA_BROWSER_URL" in fix and "fiverr.inbox" in fix
    assert rep.waiting_on_miro == 2
    assert all(r["outcome"] == "waiting_miro" for r in io.recorded[0][0])


@pytest.mark.asyncio
async def test_tier2_fix_is_one_card_deduped_per_cause():
    io = IO([finding(stale=True)], tiers={"research.daily_scan": Tier.ASK})
    rep = await ap.run_cycle(io, NOW)
    assert io.ran == [] and io.cards == [("research.daily_scan",
                                          "autopilot:research.scan_freshness:stale_data")]
    assert rep.waiting_on_miro == 1


@pytest.mark.asyncio
async def test_a_fix_action_that_is_not_registered_tier1_is_never_run():
    io = IO([finding(stale=True)], tiers={})
    await ap.run_cycle(io, NOW)
    assert io.ran == [] and len(io.tasks) == 1


@pytest.mark.asyncio
async def test_real_fault_without_a_fix_is_a_task_for_the_agent():
    io = IO([finding("sites.build", "failing", "build exited 2")])
    rep = await ap.run_cycle(io, NOW)
    assert io.ran == [] and io.tasks[0][2] == "sites.build:real_fault"
    assert rep.waiting_on_miro == 0 and rep.tasks == 1


@pytest.mark.asyncio
async def test_unused_module_is_idle_and_raises_nothing(monkeypatch):
    monkeypatch.setenv("AUTOPILOT_UNUSED_MODULES", "fiverr")
    io = IO([finding("fiverr.inbox", "unknown")], disabled={"fiverr": "missing env: K"})
    rep = await ap.run_cycle(io, NOW)
    assert io.tasks == [] and rep.waiting_on_miro == 0
    assert io.recorded[0][0][0]["outcome"] == "idle"


# ------------------------------------------------------------- the action

def test_the_action_is_tier1_with_dry_run_and_rollback():
    from atlas.modules import qa
    acts = {a.name: a for a in qa.MODULE.actions()}
    a = acts["qa.autopilot_cycle"]
    assert isinstance(a, Action) and a.tier is Tier.AUTO and a.dry_run and a.rollback


def test_summary_figures_carry_their_read_time_and_unknown_when_unread():
    s = ap.summary(None, NOW)
    assert s["fixed"] is None and s["waiting_on_miro"] is None
    assert s["read_at"] == NOW.isoformat() and s["last_cycle_at"] is None
    s = ap.summary({"at": NOW, "fixed": 2, "waiting": 3, "held": 0, "tasks": 1,
                    "id": 4}, NOW)
    assert s["fixed"] == 2 and s["waiting_on_miro"] == 3 and s["last_cycle_at"]


async def test_summary_endpoint_reads_the_latest_cycle_and_names_a_failed_read(
        monkeypatch, memory_backend):
    memory_backend.responses["FROM autopilot_cycles"] = {
        "id": 3, "at": NOW, "fixed": 2, "waiting": 1, "held": 0, "tasks": 1}
    memory_backend.responses["FROM autopilot_log"] = [
        {"subject": "fiverr.inbox", "cause": "FIVERR_KEY not set", "fix": "set it",
         "outcome": "waiting_miro"}]
    out = await ap.read_summary()
    assert out["fixed"] == 2 and out["waiting_on_miro"] == 1 and out["items"]
    assert out["last_cycle_at"] and out["read_at"] and out["unread"] == []
    memory_backend.responses.clear()

    async def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr("atlas.kernel.state.fetch_one", boom)
    out = await ap.read_summary()
    assert out["fixed"] is None and out["waiting_on_miro"] is None and out["unread"]
