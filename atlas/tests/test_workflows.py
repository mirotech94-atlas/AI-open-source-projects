"""The workflow catalog: shape, and reading real evidence from the tables.

`atlas/kernel/workflows.py` never imports the executor (invariant 1) --
checked here the same way `test_judgment.py` checks it for the judgment
package, because a file that only ever reads must never gain a path to the
one thing that acts.
"""
from __future__ import annotations

import ast
import pathlib
from datetime import UTC, datetime

import pytest

from atlas.kernel import workflows as wf
from atlas.kernel.workflows import (
    NEVER,
    OK,
    NodeStatus,
    WEdge,
    WNode,
    Workflow,
)

FILE = pathlib.Path(__file__).resolve().parent.parent / "atlas" / "kernel" / "workflows.py"


# ------------------------------------------------------------- invariant 1

def test_workflows_never_imports_the_executor():
    tree = ast.parse(FILE.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level:
                mod = f"atlas.kernel.{mod}" if mod else "atlas.kernel"
            names.add(mod)
            names.update(f"{mod}.{a.name}" for a in node.names)
    for name in names:
        assert not name.startswith("atlas.kernel.executor"), \
            f"workflows.py reaches the executor via {name}"


# ------------------------------------------------------------ catalog shape

def test_at_least_eight_workflows_are_catalogued():
    assert len(wf.WORKFLOWS) >= 8


@pytest.mark.parametrize("wanted", [
    "scheduler", "brief", "reels", "mail", "phone", "linkedin",
    "nightshift", "fixes", "lifecycle",
])
def test_every_named_workflow_is_present(wanted):
    assert any(w.id == wanted for w in wf.WORKFLOWS)


def test_every_workflow_has_at_least_five_nodes_not_a_short_one():
    for w in wf.WORKFLOWS:
        assert len(w.nodes) >= 5, f"{w.id} has only {len(w.nodes)} nodes"


def test_every_node_kind_is_one_of_the_seven():
    for w in wf.WORKFLOWS:
        for n in w.nodes:
            assert n.kind in wf.NODE_KINDS, f"{w.id}.{n.id}: {n.kind!r}"


def test_every_trigger_is_one_of_the_four():
    for w in wf.WORKFLOWS:
        assert w.trigger in wf.TRIGGER_KINDS


def test_every_workflow_has_a_trigger_node():
    for w in wf.WORKFLOWS:
        assert any(n.kind == "trigger" for n in w.nodes), w.id


def test_every_edge_references_a_real_node():
    for w in wf.WORKFLOWS:
        ids = {n.id for n in w.nodes}
        for e in w.edges:
            assert e.frm in ids and e.to in ids


def test_node_ids_are_namespaced_by_workflow_and_globally_unique():
    seen: set[str] = set()
    for w in wf.WORKFLOWS:
        for n in w.nodes:
            assert n.id.startswith(f"{w.id}.")
            assert n.id not in seen
            seen.add(n.id)


def test_a_workflow_with_no_trigger_node_is_refused():
    with pytest.raises(ValueError, match="no trigger"):
        Workflow("x", "X", "cron", "", (WNode("a", "check", "A", ""),), ())


def test_duplicate_node_ids_are_refused():
    with pytest.raises(ValueError, match="duplicate"):
        Workflow("x", "X", "cron", "", (
            WNode("t", "trigger", "T", ""), WNode("t", "check", "C", "")), ())


def test_an_edge_to_an_unknown_node_is_refused():
    with pytest.raises(ValueError, match="not in this workflow"):
        Workflow("x", "X", "cron", "", (WNode("t", "trigger", "T", ""),),
                 (WEdge("t", "ghost"),))


def test_a_cycle_is_refused():
    with pytest.raises(ValueError, match="cycle"):
        Workflow("x", "X", "cron", "", (
            WNode("t", "trigger", "T", ""),
            WNode("a", "check", "A", ""),
            WNode("b", "check", "B", ""),
        ), (WEdge("t", "a"), WEdge("a", "b"), WEdge("b", "a")))


def test_an_unknown_trigger_kind_is_refused():
    with pytest.raises(ValueError, match="trigger"):
        Workflow("x", "X", "carrier-pigeon", "", (
            WNode("t", "trigger", "T", ""),), ())


def test_an_unknown_node_kind_is_refused():
    with pytest.raises(ValueError):
        WNode("a", "spaceship", "A", "")


# --------------------------------------------------------------- resolution

async def test_a_check_node_reads_ok_degraded_failing_and_unknown(monkeypatch):
    at = datetime(2026, 9, 26, 8, 0, tzinfo=UTC)
    rows = [
        {"module": "mail", "check": "followups", "health": "ok",
         "detail": "3 open", "at": at},
        {"module": "linkedin", "check": "sweep", "health": "degraded",
         "detail": "1 surface unreadable", "at": at},
        {"module": "linkedin", "check": "agent_run", "health": "failing",
         "detail": "stale 30h", "at": at},
        {"module": "nightshift", "check": "jobs", "health": "unknown",
         "detail": "timed out", "at": at},
    ]

    async def fake_fetch_all(sql, *args):
        assert "FROM checks" in sql
        return rows

    async def fake_fetch_one(sql, *args):
        raise AssertionError("no fetch_one expected for check-only nodes")

    monkeypatch.setattr(wf, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(wf, "fetch_one", fake_fetch_one)

    catalog = (wf._wf("x", "X", "cron", "", (
        WNode("t", "trigger", "T", ""),
        WNode("a", "check", "A", "", wf.Evidence("check", "mail.followups")),
        WNode("b", "check", "B", "", wf.Evidence("check", "linkedin.sweep")),
        WNode("c", "check", "C", "", wf.Evidence("check", "linkedin.agent_run")),
        WNode("d", "check", "D", "", wf.Evidence("check", "nightshift.jobs")),
        WNode("e", "check", "E", "", wf.Evidence("check", "never.seen")),
    ), ()),)

    statuses = await wf.resolve_all(catalog)
    assert statuses["x.a"] == NodeStatus(at, OK, "3 open")
    assert statuses["x.b"].outcome == wf.DEGRADED
    assert statuses["x.c"].outcome == wf.FAILED
    assert statuses["x.d"].outcome == wf.UNKNOWN
    assert statuses["x.e"] == NodeStatus(None, NEVER, "no observation recorded yet")


async def test_an_action_node_tells_finish_from_watch_only_from_error(monkeypatch):
    at = datetime(2026, 9, 26, 9, 0, tzinfo=UTC)
    rows = [
        {"subject": "content.publish_reel", "event": "execute.finish",
         "payload": '{"ok": true, "detail": "posted"}', "at": at},
        {"subject": "nightshift.run", "event": "execute.watch_only",
         "payload": "{}", "at": at},
        {"subject": "mail.draft_reply", "event": "execute.error",
         "payload": '{"error": "HTTP 500 from gmail"}', "at": at},
        {"subject": "linkedin.publish_post", "event": "execute.finish",
         "payload": '{"ok": false, "detail": "no approved entry"}', "at": at},
    ]

    async def fake_fetch_all(sql, *args):
        assert "FROM actions_log" in sql
        return rows

    monkeypatch.setattr(wf, "fetch_all", fake_fetch_all)

    catalog = (wf._wf("x", "X", "cron", "", (
        WNode("t", "trigger", "T", ""),
        WNode("ok", "action", "OK", "", wf.Evidence("action", "content.publish_reel")),
        WNode("wo", "action", "WO", "", wf.Evidence("action", "nightshift.run")),
        WNode("err", "action", "ERR", "", wf.Evidence("action", "mail.draft_reply")),
        WNode("bad", "action", "BAD", "", wf.Evidence("action", "linkedin.publish_post")),
        WNode("never", "action", "N", "", wf.Evidence("action", "flycheap.never_run")),
    ), ()),)

    statuses = await wf.resolve_all(catalog)
    assert statuses["x.ok"] == NodeStatus(at, OK, "posted")
    assert statuses["x.wo"].outcome == wf.DEGRADED
    assert statuses["x.err"] == NodeStatus(at, wf.FAILED, "HTTP 500 from gmail")
    assert statuses["x.bad"].outcome == wf.FAILED
    assert statuses["x.never"] == NodeStatus(None, NEVER, "never run")


async def test_a_proposal_gate_maps_every_status(monkeypatch):
    created = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)
    decided = datetime(2026, 9, 26, 10, 5, tzinfo=UTC)
    rows = [
        {"action": "linkedin.publish_post", "status": "executed",
         "decided_at": decided, "created_at": created, "decided_via": "slack"},
        {"action": "nightshift.merge", "status": "declined",
         "decided_at": decided, "created_at": created, "decided_via": "slack"},
        {"action": "content.publish_reel", "status": "pending",
         "decided_at": None, "created_at": created, "decided_via": None},
        {"action": "mail.draft_nudge", "status": "expired",
         "decided_at": None, "created_at": created, "decided_via": None},
    ]

    async def fake_fetch_all(sql, *args):
        assert "FROM proposals" in sql
        return rows

    monkeypatch.setattr(wf, "fetch_all", fake_fetch_all)

    catalog = (wf._wf("x", "X", "cron", "", (
        WNode("t", "trigger", "T", ""),
        WNode("a", "gate", "A", "", wf.Evidence("proposal", "linkedin.publish_post")),
        WNode("b", "gate", "B", "", wf.Evidence("proposal", "nightshift.merge")),
        WNode("c", "gate", "C", "", wf.Evidence("proposal", "content.publish_reel")),
        WNode("d", "gate", "D", "", wf.Evidence("proposal", "mail.draft_nudge")),
        WNode("e", "gate", "E", "", wf.Evidence("proposal", "never.asked")),
    ), ()),)

    statuses = await wf.resolve_all(catalog)
    assert statuses["x.a"] == NodeStatus(decided, OK, "status: executed (via slack)")
    assert statuses["x.b"].outcome == wf.DEGRADED
    assert statuses["x.c"].at == created and statuses["x.c"].outcome == wf.DEGRADED
    assert statuses["x.d"].outcome == wf.DEGRADED
    assert statuses["x.e"] == NodeStatus(None, NEVER, "no card raised yet")


async def test_an_agent_node_tells_answered_from_silent(monkeypatch):
    at = datetime(2026, 9, 26, 11, 0, tzinfo=UTC)
    rows = [
        {"agent": "ops", "model": "anthropic:claude-sonnet-4-5",
         "provider": "anthropic", "outcome": "the mesh is fine", "at": at},
        {"agent": "inbox", "model": None, "provider": "",
         "outcome": "local model down", "at": at},
    ]

    async def fake_fetch_all(sql, *args):
        assert "FROM agent_runs" in sql
        return rows

    monkeypatch.setattr(wf, "fetch_all", fake_fetch_all)

    catalog = (wf._wf("x", "X", "cron", "", (
        WNode("t", "trigger", "T", ""),
        WNode("a", "agent", "A", "", wf.Evidence("agent", "ops")),
        WNode("b", "agent", "B", "", wf.Evidence("agent", "inbox")),
        WNode("c", "agent", "C", "", wf.Evidence("agent", "sentinel")),
    ), ()),)

    statuses = await wf.resolve_all(catalog)
    assert statuses["x.a"].outcome == OK
    assert statuses["x.b"].outcome == wf.DEGRADED
    assert statuses["x.c"] == NodeStatus(None, NEVER, "never run")


async def test_a_report_node_tells_delivered_from_stored_only(monkeypatch):
    at = datetime(2026, 9, 26, 7, 0, tzinfo=UTC)
    rows = [
        {"kind": "brief", "at": at, "delivered_via": "slack"},
        {"kind": "instagram_reels", "at": at, "delivered_via": None},
    ]

    async def fake_fetch_all(sql, *args):
        assert "FROM reports" in sql
        return rows

    monkeypatch.setattr(wf, "fetch_all", fake_fetch_all)

    catalog = (wf._wf("x", "X", "cron", "", (
        WNode("t", "trigger", "T", ""),
        WNode("a", "store", "A", "", wf.Evidence("report", "brief")),
        WNode("b", "store", "B", "", wf.Evidence("report", "instagram_reels")),
        WNode("c", "store", "C", "", wf.Evidence("report", "weekly")),
    ), ()),)

    statuses = await wf.resolve_all(catalog)
    assert statuses["x.a"].outcome == OK
    assert statuses["x.b"].outcome == wf.DEGRADED
    assert statuses["x.c"] == NodeStatus(None, NEVER, "never sent")


async def test_a_nightshift_job_node_maps_every_status(monkeypatch):
    at = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)

    async def fake_fetch_one(sql, *args):
        assert "FROM nightshift_jobs" in sql
        return {"status": "done", "at": at, "finished_at": at}

    monkeypatch.setattr(wf, "fetch_one", fake_fetch_one)

    catalog = (wf._wf("x", "X", "cron", "", (
        WNode("t", "trigger", "T", ""),
        WNode("a", "store", "A", "", wf.Evidence("job", "")),
    ), ()),)

    statuses = await wf.resolve_all(catalog)
    assert statuses["x.a"] == NodeStatus(at, OK, "status: done")


async def test_a_failed_read_is_unknown_not_ok_or_a_500(monkeypatch):
    async def boom(sql, *args):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(wf, "fetch_all", boom)
    monkeypatch.setattr(wf, "fetch_one", boom)

    catalog = (wf._wf("x", "X", "cron", "", (
        WNode("t", "trigger", "T", ""),
        WNode("a", "check", "A", "", wf.Evidence("check", "mail.followups")),
        WNode("b", "action", "B", "", wf.Evidence("action", "mail.draft_reply")),
        WNode("c", "gate", "C", "", wf.Evidence("lifecycle", "execute")),
    ), ()),)

    statuses = await wf.resolve_all(catalog)
    assert statuses["x.a"] == NodeStatus(None, wf.UNKNOWN, "could not be read")
    assert statuses["x.b"] == NodeStatus(None, wf.UNKNOWN, "could not be read")
    assert statuses["x.c"] == NodeStatus(None, wf.UNKNOWN, "could not be read")


async def test_lifecycle_row_exists_probe_says_ok_once_seen(monkeypatch):
    at = datetime(2026, 9, 26, 13, 0, tzinfo=UTC)

    async def fake_fetch_one(sql, *args):
        assert "proposal.created" in sql
        return {"at": at}

    monkeypatch.setattr(wf, "fetch_one", fake_fetch_one)
    status = await wf._lifecycle_status("propose")
    assert status == NodeStatus(at, OK, "row exists")


async def test_lifecycle_probe_with_no_row_ever_is_never(monkeypatch):
    async def fake_fetch_one(sql, *args):
        return {"at": None}

    monkeypatch.setattr(wf, "fetch_one", fake_fetch_one)
    status = await wf._lifecycle_status("execute")
    assert status == NodeStatus(None, NEVER, "no record yet")


async def test_a_node_with_no_evidence_is_left_out_of_resolution():
    catalog = (wf._wf("x", "X", "cron", "", (
        WNode("t", "trigger", "T", ""),
        WNode("a", "agent", "A", ""),
    ), ()),)
    statuses = await wf.resolve_all(catalog)
    assert "x.a" not in statuses


# ------------------------------------------------------------- serialise

async def test_serialise_reports_the_most_recently_run_node_as_last_run():
    old = datetime(2026, 9, 25, 8, 0, tzinfo=UTC)
    new = datetime(2026, 9, 26, 8, 0, tzinfo=UTC)
    w = wf._wf("x", "X", "cron", "static detail", (
        WNode("t", "trigger", "T", ""),
        WNode("a", "check", "A", "detail a"),
        WNode("b", "action", "B", "detail b"),
    ), (WEdge("t", "a"), WEdge("a", "b")))
    statuses = {"x.a": NodeStatus(old, OK, "older"),
                "x.b": NodeStatus(new, wf.FAILED, "newer, and it failed")}
    body = wf.serialise(w, statuses)
    assert body["last_run"] == {"at": new.isoformat(), "outcome": wf.FAILED}
    assert body["trigger_detail"] == "static detail"
    by_id = {n["id"]: n for n in body["nodes"]}
    assert by_id["x.t"]["last_at"] is None
    assert by_id["x.t"]["last_outcome"] == NEVER
    assert by_id["x.b"]["last_outcome"] == wf.FAILED
    assert len(body["edges"]) == 2
    assert body["edges"][0] == {"from": "x.t", "to": "x.a", "label": ""}


async def test_serialise_never_when_nothing_in_the_workflow_has_run():
    w = Workflow("x", "X", "cron", "", (WNode("t", "trigger", "T", ""),), ())
    body = wf.serialise(w, {})
    assert body["last_run"] == {"at": None, "outcome": NEVER}


def test_a_callable_trigger_detail_is_read_at_serialise_time():
    calls = []

    def detail():
        calls.append(1)
        return "computed"

    w = Workflow("x", "X", "cron", detail, (WNode("t", "trigger", "T", ""),), ())
    body = wf.serialise(w, {})
    assert body["trigger_detail"] == "computed"
    assert calls == [1]


def test_a_callable_trigger_detail_that_raises_is_unknown_not_a_500():
    def boom():
        raise RuntimeError("no env")

    w = Workflow("x", "X", "cron", boom, (WNode("t", "trigger", "T", ""),), ())
    body = wf.serialise(w, {})
    assert body["trigger_detail"] == "(could not be read)"


# ------------------------------------------------------ the catalog itself

async def test_the_real_catalog_resolves_cleanly_against_an_empty_backend(
        memory_backend):
    """Every workflow in WORKFLOWS, against the real MemoryBackend with
    nothing stubbed: every query either raises (caught, unknown) or is
    answered as empty (never). Nothing should raise out of resolve_all."""
    statuses = await wf.resolve_all(wf.WORKFLOWS)
    bodies = [wf.serialise(w, statuses) for w in wf.WORKFLOWS]
    assert len(bodies) == len(wf.WORKFLOWS)
    for body in bodies:
        for node in body["nodes"]:
            assert node["last_outcome"] in wf.OUTCOMES


def test_an_action_reads_its_result_inside_the_audit_envelope():
    """The executor writes {"event", "subject", "data": {"ok", ...}}; read at
    the top level every successful run looked FAILED."""
    import json
    from datetime import UTC, datetime

    from atlas.kernel import workflows as wf
    now = datetime.now(UTC)
    ok = {"event": "execute.finish", "at": now, "payload": json.dumps(
        {"event": "execute.finish", "subject": "content.render_calendar_job",
         "data": {"ok": True, "detail": "0 clip(s) banked"}})}
    bad = {**ok, "payload": {"event": "execute.finish",
                             "data": {"ok": False, "detail": "Veo said no"}}}
    assert wf._action_status(ok).outcome == wf.OK
    assert wf._action_status(bad).outcome == wf.FAILED
    assert "Veo said no" in wf._action_status(bad).note


async def test_church_nodes_count_posts_at_their_stage(monkeypatch):
    """The ECC channel: a stage reads the count of church_posts there now;
    zero is never, an unreadable table is unknown -- never ok."""
    from datetime import UTC, datetime
    at = datetime(2026, 10, 15, 12, tzinfo=UTC)
    rows = [{"platform": "youtube", "status": "scheduled", "n": 7, "at": at},
            {"platform": "facebook", "status": "scheduled", "n": 30, "at": at},
            {"platform": "facebook", "status": "awaiting_review", "n": 3, "at": at}]
    yt = wf._church_status("youtube:scheduled", rows, None)
    assert yt.outcome == wf.OK and "7 post" in yt.note
    both = wf._church_status("awaiting_review|rendered", rows, None)
    assert both.outcome == wf.OK and "3 post" in both.note
    assert wf._church_status("published", rows, None).outcome == wf.NEVER
    assert wf._church_status("planned", None, None).outcome == wf.UNKNOWN
    assert wf._church_status("metrics", rows, {"n": 0, "at": None}).outcome == wf.NEVER
    assert wf._church_status("metrics", rows, "__unreadable__").outcome == wf.UNKNOWN
    church = next(w for w in wf.WORKFLOWS if w.id == "church")
    assert [n.label for n in church.nodes][:2] == ["Photos", "Script (EN / AR)"]
