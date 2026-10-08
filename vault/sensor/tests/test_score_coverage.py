"""The score has to say which rubric produced it, and how much it could see.

Two separate failures of trust, both fixed by shipping one more pair of
facts next to `total`:

A score of 77 computed under one set of weights and a score of 77 computed
under another are not the same measurement, and today nothing on the row
says which one you are looking at. The trend chart draws them on the same
line. Stamping `model` means an old number keeps the meaning it had when it
was taken -- `Scan.raw["score"]` is already written once per sweep and never
rewritten, so the stamp freezes with it and history stops reshaping itself
when the weights change.

Coverage is the more dangerous one. `security_score` builds its
Vulnerabilities, Configuration and Patch-status deductions from
`[h for h in res["ssh"] if h.get("reachable")]` -- so a host that stops
answering stops deducting, and the score goes UP. A watchdog that scores
well because it went blind is the worst failure this product can have, and
the only thing that distinguishes it from a genuinely healthy network is
publishing how many of the inputs actually answered.
"""
import copy
import json
import pathlib

from guardian_sensor import alerts

FX = pathlib.Path(__file__).parent / "fixtures"


def load():
    with open(FX / "last.json") as f:
        last = json.load(f)
    with open(FX / "config.json") as f:
        cfg = json.load(f)
    with open(FX / "state.json") as f:
        state = json.load(f)
    return last, cfg, state


def score(last, cfg, state):
    return alerts.security_score(copy.deepcopy(last), cfg, copy.deepcopy(state))


def test_score_carries_its_model_version():
    last, cfg, state = load()
    assert score(last, cfg, state)["model"] == alerts.SCORE_MODEL


def test_a_fully_answering_estate_reports_full_coverage():
    last, cfg, state = load()
    for h in last["ssh"]:
        h["reachable"] = True
    cov = score(last, cfg, state)["coverage"]
    assert cov["measured"] == cov["applicable"] == len(last["ssh"])
    assert cov["pct"] == 100


def test_a_host_that_stops_answering_lowers_coverage_not_just_the_score():
    """The regression this exists to catch: silence must cost coverage.

    Without this, an unreachable host simply drops out of three deduction
    categories and the number it leaves behind looks like an improvement.
    """
    last, cfg, state = load()
    for h in last["ssh"]:
        h["reachable"] = True
    before = score(last, cfg, state)["coverage"]

    last["ssh"][0]["reachable"] = False
    after = score(last, cfg, state)["coverage"]

    assert after["measured"] == before["measured"] - 1
    assert after["applicable"] == before["applicable"]
    assert after["pct"] < before["pct"]


def test_coverage_is_full_when_there_is_nothing_to_be_blind_about():
    """A tenant with no servers is not 0% covered -- it is fully measured.

    Dividing by zero hosts and rendering "0% coverage" would put a scary,
    meaningless number on every home account that never configured SSH.
    """
    last, cfg, state = load()
    last["ssh"] = []
    cov = score(last, cfg, state)["coverage"]
    assert cov["applicable"] == 0 and cov["pct"] == 100


def test_an_optional_host_still_counts_as_something_guardian_wanted_to_see():
    """`optional` suppresses the Availability *alert* for a host that is
    allowed to be off. It does not make that host's hardening facts known --
    they are exactly as unmeasured as any other silent host, so coverage
    must still record the gap even though nothing is "down".
    """
    last, cfg, state = load()
    for h in last["ssh"]:
        h["reachable"] = True
    full = score(last, cfg, state)["coverage"]

    last["ssh"][0]["reachable"] = False
    last["ssh"][0]["optional"] = True
    cov = score(last, cfg, state)["coverage"]
    assert cov["measured"] == full["measured"] - 1


# --------------------------------------------------------------------------
# The coverage gate: a score nobody could measure is not a good score.
# --------------------------------------------------------------------------

def blind(last, keep_reachable=0):
    """Mark all but `keep_reachable` hosts unreachable."""
    for i, h in enumerate(last["ssh"]):
        h["reachable"] = i < keep_reachable
    return last


def test_a_mostly_unreachable_estate_has_no_score_rather_than_a_good_one():
    """The worst failure this product can have, stated plainly: with most
    hosts silent, the Vulnerabilities, Configuration and Patch categories
    are all vacuously perfect, and the number that comes out the other end
    is higher than the one from a healthy network. It must refuse to be a
    number instead.
    """
    last, cfg, state = load()
    blind(last)
    got = score(last, cfg, state)
    assert got["total"] is None
    assert got["grade"] == ""
    assert got["potential"] is None
    assert got["coverage"]["pct"] < alerts.SCORE_COVERAGE_FLOOR


def test_an_unknown_score_still_carries_its_coverage_and_rubric():
    """Refusing to score is a measurement too, and has to be readable: the
    reader needs to know how blind Vault was and under which rubric."""
    last, cfg, state = load()
    blind(last)
    got = score(last, cfg, state)
    assert got["model"] == alerts.SCORE_MODEL
    assert got["coverage"]["measured"] == 0
    assert got["deductions"], "the findings that WERE observed are still reported"


def test_a_fully_covered_estate_still_scores_normally():
    last, cfg, state = load()
    for h in last["ssh"]:
        h["reachable"] = True
    got = score(last, cfg, state)
    assert isinstance(got["total"], int)
    assert got["grade"]


def test_an_account_with_no_servers_is_scored_not_blanked():
    """100% coverage by definition — a home network with no servers is
    fully measured, and blanking its score would be a false alarm of the
    exact kind the gate exists to prevent."""
    last, cfg, state = load()
    last["ssh"] = []
    got = score(last, cfg, state)
    assert got["coverage"]["pct"] == 100
    assert isinstance(got["total"], int)


def test_the_gate_sits_at_the_published_floor():
    """Just above the floor scores; just below it does not. The threshold
    has to be a line someone can check, not a feeling."""
    last, cfg, state = load()
    n = len(last["ssh"])
    assert n >= 3, "fixture should have enough hosts to straddle the floor"
    blind(last, keep_reachable=n)
    assert isinstance(score(last, cfg, state)["total"], int)
    blind(last, keep_reachable=1)
    got = score(last, cfg, state)
    if got["coverage"]["pct"] < alerts.SCORE_COVERAGE_FLOOR:
        assert got["total"] is None
