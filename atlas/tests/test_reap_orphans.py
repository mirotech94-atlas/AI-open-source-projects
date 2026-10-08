"""An approval a killed process left at 'approved' is closed as failed with a
receipt at the next API start, never re-run (2026-09-28, #189)."""
from __future__ import annotations

from atlas.kernel import approvals, state


async def test_an_orphaned_approval_is_failed_with_a_receipt(monkeypatch):
    sql, receipts, audits = [], [], []

    async def fetch_all(q, *a):
        return [{"id": 189, "action": "linkedin.update_page_identity",
                 "params": '{"page": "guardian"}'},
                {"id": 190, "action": "x.y", "params": {}}]

    async def fetch_one(q, *a):
        sql.append((q, a))
        return {"id": a[0]}

    async def receipt(pid, action, params, result):
        receipts.append((pid, params, result.ok, result.detail))

    async def audit(event, subject, data):
        audits.append((event, data["id"]))

    monkeypatch.setattr(state, "fetch_all", fetch_all)
    monkeypatch.setattr(approvals, "fetch_one", fetch_one)
    monkeypatch.setattr(approvals, "_receipts", receipt)
    monkeypatch.setattr(approvals, "audit", audit)
    monkeypatch.setattr(approvals, "RUNNING", {190: object()})   # 190 is live

    assert await approvals.reap_orphans() == [189]
    assert "status = 'failed'" in sql[0][0] and "status = 'approved'" in sql[0][0]
    [(pid, params, ok, detail)] = receipts
    assert pid == 189 and params == {"page": "guardian"} and not ok
    assert "cut short by a restart" in detail
    assert audits == [("proposal.reaped", 189)]


def test_boot_never_reaps():
    """The CLI and one-off scripts run boot(); only the API lifespan reaps."""
    import inspect

    from atlas import boot
    assert "reap_orphans" not in inspect.getsource(boot)
    assert "reap_abandoned_runs" not in inspect.getsource(boot)
    assert "reconcile" not in inspect.getsource(boot)


async def test_a_run_cut_off_mid_way_is_marked_abandoned_and_told_once(monkeypatch):
    """RA-B2: execute.start with no finish -- outcome unknown, never re-run."""
    from datetime import UTC, datetime
    audits, told = [], []

    async def fetch_all(q, *a):
        assert "execute.abandoned" in q      # an already-marked run is skipped
        return [{"id": 7, "at": datetime(2026, 9, 28, 0, 14, tzinfo=UTC),
                 "subject": "linkedin.publish_post"}]

    async def audit(event, subject, data):
        audits.append((event, subject, data["start_id"]))

    async def notify(msg):
        told.append(msg)

    monkeypatch.setattr(state, "fetch_all", fetch_all)
    monkeypatch.setattr(approvals, "audit", audit)
    monkeypatch.setattr("atlas.kernel.comms.notify", notify)
    assert await approvals.reap_abandoned_runs() == ["linkedin.publish_post"]
    assert audits == [("execute.abandoned", "linkedin.publish_post", 7)]
    [msg] = told
    assert "unknown" in msg.body and "nothing was retried" in msg.body


# ---------------------------------------------------- RA-D1: reconcile on reap

async def _settle() -> None:
    import asyncio

    from atlas.kernel import background
    pending = [t for t in list(background._TASKS) if not t.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def _abandoned(monkeypatch, subjects):
    """Rows as the reap SELECT returns them, and the audit/notify it writes."""
    import json
    from datetime import UTC, datetime
    audits, told = [], []
    rows = [{"id": 7 + i, "at": datetime(2026, 9, 28, 0, 14, tzinfo=UTC),
             "subject": s,
             "payload": json.dumps({"event": "execute.start", "subject": s,
                                    "data": {"params": {"n": i}}})}
            for i, s in enumerate(subjects)]

    async def fetch_all(q, *a):
        assert "s.payload" in q
        return rows

    async def audit(event, subject, data):
        audits.append((event, subject, data))

    async def notify(msg):
        told.append(msg)

    monkeypatch.setattr(state, "fetch_all", fetch_all)
    monkeypatch.setattr(approvals, "audit", audit)
    monkeypatch.setattr("atlas.kernel.comms.notify", notify)
    return audits, told


async def test_reconcile_is_off_by_default(monkeypatch):
    from atlas.kernel import verify
    monkeypatch.delenv("ATLAS_RECONCILE", raising=False)
    called = []

    async def spy(*a):
        called.append(a)
        return verify.verified(id=1)

    monkeypatch.setattr(verify, "reconcile_unknown", spy)
    audits, _ = _abandoned(monkeypatch, ["gitlab.open_issue"])
    assert await approvals.reap_abandoned_runs() == ["gitlab.open_issue"]
    await _settle()
    assert called == []
    assert [e for e, _s, _d in audits] == ["execute.abandoned"]


async def test_reconcile_records_each_outcome_after_marking_abandoned(monkeypatch):
    from atlas.kernel import verify
    monkeypatch.setenv("ATLAS_RECONCILE", "on")
    answers = {"a.done": verify.verified(id=5),
               "b.gone": verify.contradicted("not there", looked="yes"),
               "c.huh": verify.unknown("cannot tell")}
    seen_params = []

    async def rec(action, params, row):
        seen_params.append(verify.started_params(row))
        return answers[action]

    monkeypatch.setattr(verify, "reconcile_unknown", rec)
    audits, told = _abandoned(monkeypatch, list(answers))
    await approvals.reap_abandoned_runs()
    await _settle()
    events = [(e, s) for e, s, _d in audits]
    # Every run is marked abandoned first; the look-up comes after.
    assert events[:3] == [("execute.abandoned", s) for s in answers]
    assert events[3:] == [("reconcile.done", "a.done"), ("reconcile.failed", "b.gone"),
                          ("reconcile.needs_human", "c.huh")]
    failed = next(d for e, _s, d in audits if e == "reconcile.failed")
    assert failed["evidence"] == {"looked": "yes"} and failed["start_id"] == 8
    assert failed["reason"] == "not there"
    assert seen_params == [{"n": 0}, {"n": 1}, {"n": 2}]
    assert len(told) == 2 and "nothing was retried" in told[1].body


async def test_reconcile_never_reexecutes(monkeypatch):
    """The real look-up path, with an action whose every callable raises."""
    from atlas.kernel import verify
    from atlas.kernel.contracts import Action, Tier
    from atlas.kernel.registry import REGISTRY
    monkeypatch.setenv("ATLAS_RECONCILE", "on")

    async def forbidden(*_a, **_kw):
        raise AssertionError("reconcile reached the action")

    monkeypatch.setitem(REGISTRY.actions, "zrec.act", Action(
        name="zrec.act", tier=Tier.ASK, summary="s", run=forbidden,
        dry_run=forbidden, rollback=forbidden))
    looked = []

    async def found(params, started):
        looked.append(params)
        return verify.verified(id=1)

    monkeypatch.setitem(verify._RECONCILERS, "zrec.act", found)
    audits, _ = _abandoned(monkeypatch, ["zrec.act"])
    await approvals.reap_abandoned_runs()
    await _settle()
    assert [e for e, _s, _d in audits] == ["execute.abandoned", "reconcile.done"]
    assert looked == [{"n": 0}]


async def test_a_reconcile_that_raises_leaves_the_run_abandoned(monkeypatch):
    from atlas.kernel import verify
    monkeypatch.setenv("ATLAS_RECONCILE", "on")

    async def boom(*_a):
        raise RuntimeError("kernel bug")

    monkeypatch.setattr(verify, "reconcile_unknown", boom)
    audits, told = _abandoned(monkeypatch, ["x.y"])
    assert await approvals.reap_abandoned_runs() == ["x.y"]
    await _settle()
    assert [e for e, _s, _d in audits] == ["execute.abandoned"]
    assert len(told) == 1


async def test_reconcile_reads_an_asyncpg_record_row():
    """Production rows are asyncpg Records: keyed, not a Mapping, no
    attributes. The start time and params must still be read off them."""
    import json
    from datetime import UTC, datetime

    from atlas.kernel import verify

    class Record:
        def __init__(self, **kw):
            self._kw = kw

        def __getitem__(self, key):
            return self._kw[key]

    at = datetime(2026, 9, 28, 0, 14, tzinfo=UTC)
    row = Record(id=7, at=at, subject="z.act",
                 payload=json.dumps({"data": {"params": {"n": 1}}}))
    assert verify.started_at(row) == at
    assert verify.started_params(row) == {"n": 1}
