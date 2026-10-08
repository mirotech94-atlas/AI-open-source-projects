"""The five invariants. Each is its own named test so a failure says which
promise broke, not merely that one did.
"""
import pytest

from guardian_hub import confidence as c


def _conf(score):
    return c.Confidence(score=score, verdict="auto" if score >= c.HIGH_WATER else "approval",
                        factors=[])


def _gate(kind="approval", score=95, **kw):
    args = dict(action="security_updates", tier=2, verified_successes=5,
                recurred_since=False, ever_undone=False, sensor_fresh=True,
                auto_heal=True, caller_may_run=True)
    args.update(kw)
    return c.gate(kind, _conf(score), **args)


def test_a_tier_three_action_is_never_promoted_at_any_score():
    for score in (0, 50, 85, 100):
        kind, _ = _gate(score=score, action="reboot", tier=3, verified_successes=99)
        assert kind == "approval", score


def test_an_action_never_run_on_this_host_is_never_promoted():
    kind, reason = _gate(verified_successes=0)
    assert kind == "approval"
    assert "never" in reason.lower() or "history" in reason.lower()


def test_the_kill_switch_beats_any_score():
    kind, reason = _gate(auto_heal=False)
    assert kind == "approval"
    assert "switched off" in reason.lower() or "auto_heal" in reason.lower()


def test_a_recurring_alert_is_never_promoted_however_often_it_was_fixed():
    kind, _ = _gate(verified_successes=50, recurred_since=True)
    assert kind == "approval"


def test_a_repair_a_person_undid_is_never_promoted():
    kind, _ = _gate(ever_undone=True)
    assert kind == "approval"


def test_a_low_score_demotes_an_auto_to_an_approval():
    kind, reason = _gate(kind="auto", score=c.LOW_WATER - 1)
    assert kind == "approval" and reason


def test_a_high_score_with_every_receipt_promotes():
    kind, reason = _gate(kind="approval", score=c.HIGH_WATER)
    assert kind == "auto" and reason


def test_a_human_decision_is_never_touched():
    # `human` means nothing Vault runs can fix it -- a dead sensor is the
    # archetype. No score makes that actionable.
    for score in (0, 100):
        kind, _ = _gate(kind="human", score=score)
        assert kind == "human"


def test_a_high_score_alone_does_not_promote_without_the_history():
    kind, _ = _gate(score=100, verified_successes=c.PROMOTE_MIN_SUCCESSES - 1)
    assert kind == "approval"


def test_a_caller_who_may_not_run_it_is_never_promoted():
    """The sixth invariant, and the one whose absence was a live defect.

    `remedy.resolve_action` reaches `kind="approval"` from two unrelated
    causes: a tier-3 action anyone may run once a person confirms it, and an
    action this caller has no right to run at all (`role != "admin"`, which
    `apply_fix`'s `min_role` enforces everywhere a repair is actually
    executed). `gate` used to see only `kind` and `tier`, so it could not
    tell them apart -- and because an admin below tier 3 arrives here already
    `auto` and an admin at tier 3 is refused by the tier guard, the promotion
    branch was reachable ONLY for the caller the spec forbids promoting for.
    Every receipt below is present and the score is the maximum, precisely so
    that nothing but `caller_may_run` can be what holds the promotion back.
    """
    kind, _ = _gate(score=100, verified_successes=99, caller_may_run=False)
    assert kind == "approval"


def test_a_caller_who_may_not_run_it_can_still_be_demoted():
    """Caution stays available to everyone; only promotion is gated.

    The companion to the test above, and the reason `caller_may_run` is
    checked after the demotion branch rather than at the top of `gate`: the
    spec's asymmetry is "demotion is always available", so a `caller_may_run`
    guard placed first would have quietly bought the fix by breaking the half
    of the function that was never in question.
    """
    kind, reason = _gate(kind="auto", score=c.LOW_WATER - 1, caller_may_run=False)
    assert kind == "approval" and reason
