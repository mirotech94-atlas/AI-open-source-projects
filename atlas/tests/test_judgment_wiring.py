"""Where the learning layer touches the rest of ATLAS: proposals write the
log, answers resolve it, expiry is its own class, a hushed proposal never
becomes pending, and the number always travels with its reasons."""
from __future__ import annotations

import pytest

from atlas.kernel.contracts import Tier
from atlas.kernel.judgment import Advice, Prediction
from atlas.kernel.judgment import policy as jpolicy
from atlas.kernel.registry import REGISTRY


@pytest.fixture
def proposing(monkeypatch, memory_backend):
    """A fake proposals table (the memory backend refuses arbitrary SQL) and
    a record of what the learning layer was told."""
    from atlas.kernel import approvals, state

    class A:
        summary = "File an issue"
        params = {"title": "the title"}

    class D:
        tier = Tier.ASK
        allowed = True
        reason = ""

    monkeypatch.setitem(REGISTRY.actions, "gitlab.open_issue", A())
    monkeypatch.setitem(REGISTRY.decisions, "gitlab.open_issue", D())

    inserted: list[dict] = []
    logged: list[tuple] = []
    outcomes: list[tuple] = []
    sent: list = []

    async def fake_fetch_one(sql, *args):
        if "INSERT INTO proposals" in sql:
            inserted.append({"status": args[10], "predicted": args[11]})
            return {"id": 100 + len(inserted)}
        if "UPDATE proposals SET status='declined'" in sql:
            return {"action": "gitlab.open_issue"}
        return None

    async def fake_record(pid, pred, tier):
        logged.append((pid, pred.p, pred.head, tuple(pred.reasons)))

    async def fake_outcome(pid, outcome, via=""):
        outcomes.append((pid, outcome, via))

    async def fake_notify(msg):
        sent.append(msg)
        return ["slack"]

    monkeypatch.setattr(approvals, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(state, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(approvals, "notify", fake_notify)
    monkeypatch.setattr(jpolicy, "record", fake_record)
    monkeypatch.setattr(jpolicy, "record_outcome", fake_outcome)
    from atlas.kernel import judgment
    monkeypatch.setattr(judgment, "record", fake_record)
    monkeypatch.setattr(judgment, "record_outcome", fake_outcome)
    return {"inserted": inserted, "logged": logged, "outcomes": outcomes,
            "sent": sent}


def _pred(p, advice_seen=25, abstained=False, reasons=("you always approve this",
                                                       "it's reversible", "it's daytime")):
    return Prediction(p, "logistic", reasons, abstained, "", advice_seen, True)


async def test_a_proposal_is_logged_the_moment_it_exists(proposing, monkeypatch):
    from atlas.kernel.approvals import propose

    async def fake_predict(action, severity, *, at=None):
        return _pred(0.85)
    monkeypatch.setattr(jpolicy, "predict", fake_predict)
    from atlas.kernel import judgment
    monkeypatch.setattr(judgment, "predict", fake_predict)

    pid = await propose("gitlab.open_issue", {"title": "a"}, reason="because")
    assert proposing["logged"] == [(pid, 0.85, "logistic",
                                    ("you always approve this", "it's reversible",
                                     "it's daytime"))]
    assert proposing["inserted"][0]["status"] == "pending"
    assert proposing["inserted"][0]["predicted"] == 0.85


async def test_the_card_carries_the_number_and_its_reasons(proposing, monkeypatch):
    from atlas.kernel import judgment
    from atlas.kernel.approvals import propose

    async def fake_predict(action, severity, *, at=None):
        return _pred(0.85)
    monkeypatch.setattr(judgment, "predict", fake_predict)

    await propose("gitlab.open_issue", {"title": "a"}, reason="because")
    body = proposing["sent"][0].body
    assert "85%" in body and "you usually say yes" in body
    assert "you always approve this" in body and "it's reversible" in body


async def test_abstaining_puts_no_number_on_the_card(proposing, monkeypatch):
    from atlas.kernel import judgment
    from atlas.kernel.approvals import propose

    async def fake_predict(action, severity, *, at=None):
        return Prediction(None, "abstain", (), True, "only 12 decisions", 0, True)
    monkeypatch.setattr(judgment, "predict", fake_predict)

    await propose("gitlab.open_issue", {"title": "a"}, reason="because")
    body = proposing["sent"][0].body
    assert "%" not in body
    assert proposing["inserted"][0]["status"] == "pending"


async def test_a_hushed_proposal_is_recorded_but_never_pending(proposing, monkeypatch):
    """p < 0.30 with twenty answers behind it: not raised, not sent, and
    stored as 'suppressed' so nothing can approve it by accident."""
    from atlas.kernel import judgment
    from atlas.kernel.approvals import propose

    async def fake_predict(action, severity, *, at=None):
        return _pred(0.12)
    monkeypatch.setattr(judgment, "predict", fake_predict)

    pid = await propose("gitlab.open_issue", {"title": "a"}, reason="because")
    assert proposing["sent"] == []
    assert proposing["inserted"][0]["status"] == "suppressed"
    assert proposing["logged"][0][0] == pid


async def test_standing_rule_is_drafted_not_granted(proposing, monkeypatch):
    from atlas.kernel import judgment
    from atlas.kernel.approvals import propose

    async def fake_predict(action, severity, *, at=None):
        return _pred(0.97)
    monkeypatch.setattr(judgment, "predict", fake_predict)

    await propose("gitlab.open_issue", {"title": "a"}, reason="because")
    body = proposing["sent"][0].body
    assert "Standing rule?" in body and "from now on" in body
    assert "records it" in body
    # Still Tier 2, still asks: the buttons are the same four.
    labels = [b.label for b in proposing["sent"][0].buttons]
    assert labels == ["Approve", "Approve + suggest always", "Decline", "Details"]
    assert REGISTRY.decisions["gitlab.open_issue"].tier is Tier.ASK


async def test_decline_is_denied_and_expiry_is_expired(proposing, monkeypatch):
    from atlas.kernel import approvals

    v = await approvals.decide(7, "decline", "slack:U1")
    assert v.outcome == "declined"
    assert proposing["outcomes"] == [(7, "denied", "slack:U1")]

    async def fake_fetch_all(sql, *args):
        return [{"id": 8, "action": "gitlab.open_issue"}]
    monkeypatch.setattr(approvals, "fetch_all", fake_fetch_all)
    n = await approvals.expire_stale()
    assert n == 1
    assert proposing["outcomes"][-1] == (8, "expired", "expiry")


def test_advice_is_the_only_thing_the_number_may_do():
    assert set(Advice) == {Advice.STANDING_RULE, Advice.CONFIDENT, Advice.ASK,
                           Advice.SUPPRESS, Advice.ABSTAIN}
    # No advice value names an execution, a tier, or a promotion.
    for a in Advice:
        assert "execute" not in a.value and "tier" not in a.value
        assert "promote" not in a.value


def test_the_scheduler_runs_the_tournament_nightly_and_the_brief_reads_it():
    import inspect

    from atlas.kernel import brief, scheduler
    assert '("judgment", _maybe_tournament)' in inspect.getsource(scheduler)
    assert "run_all" in inspect.getsource(scheduler._maybe_tournament)
    src = inspect.getsource(brief)
    assert '"Judgment"' in src and "take_lines" in src and "suppressed" in src
