"""The QA hard gate (kernel/qa_gate.py): a gated action needs a standing QA
pass for its exact content, at propose() and at execute(), every tier.
Fail closed."""
from __future__ import annotations

from pathlib import Path

import pytest

from atlas.kernel import approvals, executor, qa_gate
from atlas.kernel.contracts import Action, ActionResult, FailureClass, Tier
from atlas.kernel.executor import ExecutionRequest, execute
from atlas.kernel.permissions import classify
from atlas.kernel.registry import REGISTRY

CLEAN_EMAIL = ("Hi Joe,\n\nI looked at your site and the booking link is broken.\n\n"
               "ATLAS, assistant to Miro at Mapfront\n1 Main St, Newark NJ\n"
               "Reply STOP to opt out.")


@pytest.fixture(autouse=True)
def clean_registry(memory_backend, monkeypatch):
    saved_a, saved_d = dict(REGISTRY.actions), dict(REGISTRY.decisions)
    REGISTRY.actions.clear()
    REGISTRY.decisions.clear()
    executor._recent.clear()
    monkeypatch.setenv("AGENCY_POSTAL_ADDRESS", "1 Main St, Newark NJ")
    yield
    REGISTRY.actions.clear()
    REGISTRY.decisions.clear()
    REGISTRY.actions.update(saved_a)
    REGISTRY.decisions.update(saved_d)
    executor._recent.clear()


def install(name: str, tier: Tier, ran: list) -> Action:
    async def run(**kw):
        ran.append(("run", kw))
        return ActionResult(True, "done", rollback_token="tok")

    async def dry(**kw):
        ran.append(("dry", kw))
        return ActionResult(True, "preview")

    async def undo(token):
        return ActionResult(True, "undone")

    action = Action(name=name, tier=tier, summary="s", run=run, dry_run=dry,
                    rollback=undo, params={"slug": "site"}, max_per_hour=5)
    REGISTRY.actions[name] = action
    REGISTRY.decisions[name] = classify(action)
    return action


def site_resolver(sha: str = "a" * 64):
    async def resolve(params):
        return qa_gate.Subject(f"site:{params['slug']}", sha)
    return resolve


def text_resolver(text: str, kind: str = "email"):
    async def resolve(params):
        return qa_gate._text_subject(f"agency_outreach:{params['slug']}", kind, text)
    return resolve


def no_board_task(memory_backend):
    memory_backend.responses["FROM project_tasks WHERE kind = 'remediation'"] = None
    memory_backend.responses["INSERT INTO project_tasks"] = {"id": 41}


def writes(fake, table):
    return [sql for sql, _ in fake.sql_log if f"INSERT INTO {table}" in sql]


async def test_propose_without_a_pass_raises_and_leaves_no_card(memory_backend,
                                                                monkeypatch):
    install("agency.deploy_site", Tier.ASK, [])
    monkeypatch.setitem(qa_gate.RESOLVERS, "agency.deploy_site", site_resolver())
    memory_backend.responses["FROM qa_results WHERE subject_ref"] = None
    no_board_task(memory_backend)
    with pytest.raises(qa_gate.QaRequired) as err:
        await approvals.propose("agency.deploy_site", {"slug": "joes"}, "deploy it")
    assert "no passing qa.check_site" in err.value.spoken
    assert not writes(memory_backend, "proposals")
    assert len(writes(memory_backend, "project_tasks")) == 1
    assert any(r.event == "proposal.qa_held" for r in memory_backend.audit_rows)


async def test_an_open_task_is_not_raised_twice(memory_backend, monkeypatch):
    install("agency.deploy_site", Tier.ASK, [])
    monkeypatch.setitem(qa_gate.RESOLVERS, "agency.deploy_site", site_resolver())
    memory_backend.responses["FROM qa_results WHERE subject_ref"] = None
    memory_backend.responses["FROM project_tasks WHERE kind = 'remediation'"] = {"id": 9}
    with pytest.raises(qa_gate.QaRequired):
        await qa_gate.require("agency.deploy_site", {"slug": "joes"})
    assert not writes(memory_backend, "project_tasks")


async def test_clean_text_is_checked_recorded_and_passes(memory_backend, monkeypatch):
    install("agency.send_outreach", Tier.ASK, [])
    monkeypatch.setitem(qa_gate.RESOLVERS, "agency.send_outreach",
                        text_resolver(CLEAN_EMAIL))
    memory_backend.responses["FROM qa_results WHERE subject_ref"] = None
    memory_backend.responses["INSERT INTO qa_results"] = {"id": 5}
    assert await qa_gate.require("agency.send_outreach", {"slug": "1"}) == 5
    (args,) = [a for s, a in memory_backend.sql_log if "INSERT INTO qa_results" in s]
    assert args[0] == "draft" and args[3] is True
    # The text itself is never stored.
    assert "booking link" not in str(args)


async def test_dirty_text_is_held_with_its_failures(memory_backend, monkeypatch):
    install("agency.send_outreach", Tier.ASK, [])
    monkeypatch.setitem(qa_gate.RESOLVERS, "agency.send_outreach",
                        text_resolver(CLEAN_EMAIL + "\nA site for just $100, guaranteed."))
    memory_backend.responses["FROM qa_results WHERE subject_ref"] = None
    memory_backend.responses["INSERT INTO qa_results"] = {"id": 6}
    no_board_task(memory_backend)
    with pytest.raises(qa_gate.QaRequired) as err:
        await qa_gate.require("agency.send_outreach", {"slug": "1"})
    assert any("$100" in f for f in err.value.failures)
    assert any("guaranteed" in f for f in err.value.failures)


async def test_auto_deploy_without_a_pass_is_quality_and_not_dispatched(
        memory_backend, monkeypatch):
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    ran: list = []
    install("agency.deploy_site", Tier.AUTO, ran)
    monkeypatch.setitem(qa_gate.RESOLVERS, "agency.deploy_site", site_resolver())
    memory_backend.responses["FROM qa_results WHERE subject_ref"] = None
    no_board_task(memory_backend)
    result = await execute(ExecutionRequest("agency.deploy_site", {"slug": "joes"},
                                            reason="test"))
    assert not result.ok and result.failure_class is FailureClass.QUALITY
    assert result.dispatched is False and ran == []
    assert any(r.event == "execute.qa_held" for r in memory_backend.audit_rows)


async def test_a_pass_for_another_sha_does_not_count(memory_backend, monkeypatch):
    seen: list = []

    async def latest(ref, sha):
        seen.append(sha)
        return {"id": 1, "ok": True, "failures": []} if sha == "b" * 64 else None

    install("agency.deploy_site", Tier.ASK, [])
    monkeypatch.setitem(qa_gate.RESOLVERS, "agency.deploy_site", site_resolver("c" * 64))
    from atlas.modules.qa import store
    monkeypatch.setattr(store, "latest", latest)
    no_board_task(memory_backend)
    with pytest.raises(qa_gate.QaRequired):
        await qa_gate.require("agency.deploy_site", {"slug": "joes"})
    assert seen == ["c" * 64]


async def test_a_standing_pass_lets_it_run(memory_backend, monkeypatch):
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    ran: list = []
    install("agency.deploy_site", Tier.AUTO, ran)
    monkeypatch.setitem(qa_gate.RESOLVERS, "agency.deploy_site", site_resolver())
    memory_backend.responses["FROM qa_results WHERE subject_ref"] = {
        "id": 3, "kind": "site", "ok": True, "failures": "[]", "checked_at": None}
    memory_backend.responses["INSERT INTO actions_log"] = None
    try:
        await execute(ExecutionRequest("agency.deploy_site", {"slug": "joes"},
                                       reason="test"))
    except NotImplementedError:
        pass        # past the gate: what follows is the executor's own bookkeeping
    assert ("dry", {"slug": "joes"}) in ran


async def test_an_unreadable_subject_is_held_without_a_task(memory_backend,
                                                            monkeypatch):
    async def broken(params):
        raise OSError("disk gone")

    install("agency.deploy_site", Tier.ASK, [])
    monkeypatch.setitem(qa_gate.RESOLVERS, "agency.deploy_site", broken)
    with pytest.raises(qa_gate.QaRequired) as err:
        await qa_gate.require("agency.deploy_site", {"slug": "joes"})
    assert "could not read" in err.value.spoken
    assert not writes(memory_backend, "project_tasks")


async def test_a_gate_error_at_propose_fails_closed(memory_backend, monkeypatch):
    install("agency.deploy_site", Tier.ASK, [])

    async def boom(action, params):
        raise RuntimeError("db down")

    monkeypatch.setattr(qa_gate, "require", boom)
    with pytest.raises(qa_gate.QaRequired) as err:
        await approvals.propose("agency.deploy_site", {"slug": "joes"}, "deploy it")
    assert "QA could not run" in err.value.spoken
    assert not writes(memory_backend, "proposals")


async def test_a_non_gated_action_never_reaches_the_gate(memory_backend, monkeypatch):
    called: list = []

    async def spy(action, params):
        called.append(action)

    monkeypatch.setattr(qa_gate, "require", spy)
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    install("mod.other", Tier.AUTO, [])
    assert not qa_gate.gated("mod.other")
    try:
        await execute(ExecutionRequest("mod.other", {"slug": "x"}, reason="test"))
    except NotImplementedError:
        pass
    assert called == []


async def test_watch_only_still_refuses_before_the_gate(memory_backend, monkeypatch):
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "true")
    ran: list = []
    install("agency.deploy_site", Tier.AUTO, ran)
    result = await execute(ExecutionRequest("agency.deploy_site", {"slug": "joes"},
                                            reason="test"))
    assert not result.ok and ran == []
    assert "watch" in result.detail.lower()


def test_lead_followup_treats_a_qa_hold_as_an_answer():
    from atlas.kernel import lead_followup
    held = ActionResult(False, "QA: held", failure_class=FailureClass.QUALITY,
                        dispatched=False)
    assert lead_followup._held(ActionResult(False, "paused", dispatched=False))
    assert not lead_followup._held(held)


def test_every_registered_gated_name_has_a_resolver():
    import importlib
    import pkgutil

    import atlas.modules
    names = set()
    for info in pkgutil.iter_modules(atlas.modules.__path__):
        if info.name.startswith("_"):
            continue
        module = getattr(importlib.import_module(f"atlas.modules.{info.name}"),
                         "MODULE", None)
        if module is not None:
            names |= {a.name for a in module.actions()}
    gated = qa_gate.GATED_NAMES & names
    assert gated >= {"agency.send_outreach", "agency.deploy_site"}
    assert sorted(gated - set(qa_gate.RESOLVERS)) == []


async def test_tutorial_ready_refuses_a_notebooklm_render_qa_fails(memory_backend,
                                                                  monkeypatch):
    from atlas.modules.academy import notebooklm
    from atlas.modules.academy import tutorials as tut
    series = next(s for s in tut.SERIES if s.episodes)
    ep = series.episodes[0]
    m = tut.Manifest(series=series.key, episode=ep.number, audio=notebooklm.AUDIO,
                     seconds=301.0)
    monkeypatch.setattr(tut, "resolve", lambda s, e: (series, ep))
    monkeypatch.setattr(tut, "episode_dir", lambda s, e: Path("/nowhere"))
    monkeypatch.setattr(tut, "publishable_render", lambda v: None)
    monkeypatch.setattr(tut, "read_manifest", lambda d: m)

    async def no_refusal(s, e, d, man, video):
        return "A lesson with no disclosure. https://agency.storefront.test/x", None

    monkeypatch.setattr(tut, "notebooklm_refusal", no_refusal)
    got, why = await tut.ready(series.key, ep.number)
    assert got is None and why.startswith(tut.QA_PREFIX)
    assert "AI voice" in why
    assert memory_backend.sql_log == []
