"""`kernel.grounding.check`: a model's rewording is kept only when every
number, time, date, name and outcome in it is already in its facts.

Accept cases first (a real rewording must pass, or the polish is useless),
then one reject case per rule -- each a way a model could slip a fact in.
"""
from __future__ import annotations

import pytest

from atlas.kernel.grounding import check

ORIGINAL = ("3 runs in the last 24 hours (1 shift). Last: All four servers "
            "answered. Next shift in 4 hours.")
FACTS = ["Marcus", "ATLAS"]


def ok(candidate: str, original: str = ORIGINAL, facts=FACTS) -> bool:
    return check(candidate, facts, original=original).ok


# ------------------------------------------------------------------ accepted

@pytest.mark.parametrize("candidate", [
    ("I did 3 runs in the last 24 hours, 1 of them a shift. Last: all four "
     "servers answered. My next shift is in 4 hours."),
    ("Over the last 24 hours I ran 3 times (1 shift); all four servers "
     "answered. Next shift in 4 hours."),
])
def test_a_faithful_rewording_passes(candidate):
    assert ok(candidate), check(candidate, FACTS, original=ORIGINAL).problems


def test_names_from_the_facts_and_sentence_openers_pass():
    original = "Jasper, your last run failed: no model answered. What's next?"
    assert ok("Jasper, your last run failed because no model answered. "
              "What comes next?", original, ["Jasper", "ATLAS"])


def test_a_clock_time_that_is_in_the_record_passes():
    original = "My last run, at 06:12, said: Orders are up to date."
    assert ok("At 06:12 my last run said orders are up to date.", original, [])


# ------------------------------------------------------------------ refused

def test_an_invented_number_is_refused():
    v = check("I did 5 runs in the last 24 hours (1 shift). All four servers "
              "answered. Next shift in 4 hours.", FACTS, original=ORIGINAL)
    assert not v.ok
    assert any("'5'" in p for p in v.problems)


def test_a_dropped_number_is_refused():
    v = check("Several runs today; all four servers answered. Next shift soon.",
              FACTS, original=ORIGINAL)
    assert not v.ok
    assert any("dropped" in p for p in v.problems)


def test_numbers_cannot_be_reassigned_to_another_recorded_unit():
    original = "3 runs in the last 24 hours."
    swapped = "3 hours in the last 24 runs."
    verdict = check(swapped, [], original=original)
    assert not verdict.ok
    assert any("hours" in problem for problem in verdict.problems)


def test_a_number_in_words_is_refused_unless_the_record_says_it():
    # "four" is in the record ("All four servers"); "a dozen" is not.
    v = check("3 runs in the last 24 hours, about a dozen checks (1 shift). "
              "Next shift in 4 hours.", FACTS, original=ORIGINAL)
    assert not v.ok and any("dozen" in p for p in v.problems)


def test_a_new_date_or_time_is_refused():
    for candidate in (
            "3 runs yesterday (1 shift); all four servers answered. Next shift in 4 hours.",
            ("3 runs in the last 24 hours (1 shift); all four servers answered. "
             "Next shift at 11:00, in 4 hours."),
            ("3 runs in the last 24 hours (1 shift); all four servers answered on "
             "Monday. Next shift in 4 hours.")):
        assert not ok(candidate), candidate


def test_a_new_proper_name_is_refused():
    v = check("3 runs in the last 24 hours (1 shift); Hetzner says all four "
              "servers answered. Next shift in 4 hours.", FACTS, original=ORIGINAL)
    assert not v.ok and any("Hetzner" in p for p in v.problems)


def test_a_new_outcome_is_refused():
    v = check("3 runs in the last 24 hours (1 shift); all four servers answered "
              "and I fixed the disk. Next shift in 4 hours.", FACTS, original=ORIGINAL)
    assert not v.ok and any("fixed" in p for p in v.problems)


def test_a_dropped_outcome_is_refused():
    original = "Jasper, your last run failed: no model answered. What's next?"
    v = check("Jasper, how did your last run go? What's next?", ["Jasper"],
              original=original)
    assert not v.ok and any("fail" in p for p in v.problems)


def test_money_and_links_are_refused():
    assert not ok("3 runs in the last 24 hours (1 shift), worth $3. All four "
                  "servers answered. Next shift in 4 hours.")
    assert not ok("3 runs in the last 24 hours (1 shift); see https://x.io. "
                  "All four servers answered. Next shift in 4 hours.")


def test_a_rewording_that_runs_long_is_refused():
    padded = ORIGINAL + " " + "All four servers answered. " * 4
    v = check(padded, FACTS, original=ORIGINAL)
    assert not v.ok and any("characters" in p for p in v.problems)


def test_an_empty_rewording_is_refused():
    assert not ok("   ")
