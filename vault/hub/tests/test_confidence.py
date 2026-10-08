"""The rubric's arithmetic, with no database in the way.

Weights here are chosen, not fitted -- production has 78 repairs and every
one of them succeeded, which is a single class and teaches a classifier
nothing. So the numbers are a person's judgement, and this file is where
that judgement is argued with.
"""
import pytest

from guardian_hub import confidence as c


def _f(name="history", weight=0):
    return c.Factor(name=name, question="q?", answer="a", weight=weight, why="because")


def test_a_score_starts_from_the_baseline_and_moves_with_the_factors():
    assert c.score_of([]) == c.BASELINE
    assert c.score_of([_f(weight=10)]) == c.BASELINE + 10
    assert c.score_of([_f(weight=10), _f(weight=-30)]) == c.BASELINE - 20


def test_a_score_is_clamped_to_nought_and_a_hundred():
    assert c.score_of([_f(weight=-500)]) == 0
    assert c.score_of([_f(weight=500)]) == 100


def test_the_same_factors_always_give_the_same_score():
    factors = [_f(weight=12), _f(name="verified", weight=-7)]
    assert c.score_of(factors) == c.score_of(list(reversed(factors)))


@pytest.mark.parametrize("score, expected", [
    (0, "approval"), (c.LOW_WATER - 1, "approval"),
    (c.LOW_WATER, "approval"), (c.HIGH_WATER, "auto"), (100, "auto"),
])
def test_a_tier_two_action_reads_auto_only_above_the_high_water_mark(score, expected):
    assert c.verdict_of(score, tier=2) == expected


@pytest.mark.parametrize("score", [0, 50, 100])
def test_a_tier_three_action_never_reads_auto_however_high_the_score(score):
    # The rubric cannot promote what the tier table forbids, and saying so
    # here as well as in the gate means a caller that reads the verdict
    # without the gate still cannot be misled.
    assert c.verdict_of(score, tier=3) == "approval"


def test_a_factor_carries_the_question_it_answers():
    f = c.Factor(name="host_history", question="Fixed on prod before?",
                 answer="yes, 12 times", weight=30, why="It has held every time.")
    assert f.question.endswith("?") and f.why and f.weight == 30
