"""The LinkedIn module's readings of its own state file.

The load-bearing property: **a missing state file is UNKNOWN, never OK.**
The write path (publish, approve) and the sweep are covered in
test_linkedin_server.py.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from atlas.kernel.contracts import Health, Severity
from atlas.modules import linkedin


@pytest.fixture
def state(tmp_path, monkeypatch):
    """Writes a state file and points the module at it."""
    def write(doc: dict) -> None:
        path = tmp_path / "state.json"
        path.write_text(json.dumps(doc))
        monkeypatch.setattr(linkedin, "STATE", path)
    return write


def _now(offset_hours: float = 0.0) -> str:
    return (datetime.now(UTC) - timedelta(hours=offset_hours)).isoformat()


def bank(*statuses: str, enabled: bool = True, per_day: int = 1) -> dict:
    return {
        "post_bank": [{"id": f"B{i}", "status": s}
                      for i, s in enumerate(statuses, 1)],
        "posting": {"enabled": enabled, "max_per_day": per_day},
    }


# ------------------------------------------------ missing state is UNKNOWN

async def test_every_check_is_unknown_without_a_state_file(tmp_path, monkeypatch):
    monkeypatch.setattr(linkedin, "STATE", tmp_path / "absent.json")
    for check in linkedin.MODULE.checks():
        obs = await check.run()
        assert obs.health is Health.UNKNOWN, check.name
        assert "absent.json" in obs.detail, "say which path was tried"


async def test_corrupt_state_is_unknown_not_ok(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_text("{ truncated")
    monkeypatch.setattr(linkedin, "STATE", path)
    obs = await linkedin.agent_run()
    assert obs.health is Health.UNKNOWN


async def test_missing_state_emits_no_signal(tmp_path, monkeypatch):
    """The checks already report it. A signal as well notifies twice."""
    monkeypatch.setattr(linkedin, "STATE", tmp_path / "absent.json")
    assert await linkedin.MODULE.signals() == ()


# ----------------------------------------------------------- post runway

@pytest.mark.parametrize("statuses, days, health", [
    (("published", "published"), 0, Health.FAILING),
    (("approved",), 1, Health.DEGRADED),
    (("approved", "approved"), 2, Health.DEGRADED),
    (("approved",) * 5, 5, Health.OK),
])
async def test_runway_counts_only_approved_entries(state, statuses, days, health):
    """A draft is never published, so it is not runway. Counting the whole
    bank reports days of cover that do not exist."""
    state({"improver": bank(*statuses), "page": {}})
    obs = await linkedin.post_runway("flycheap")
    assert obs.value == days
    assert obs.health is health


async def test_drafts_are_reported_but_not_counted(state):
    state({"improver": bank("approved", "draft", "needs_rework_2026_09_20")})
    obs = await linkedin.post_runway("flycheap")
    assert obs.value == 1
    assert obs.evidence["waiting_on_approval"] == 2
    assert "approval card" in obs.detail


async def test_the_two_pages_read_different_locations(state):
    """flycheap's bank lives under `improver`, Guardian's under its own page
    object. Assuming one shape for both silently reads an empty bank and
    reports infinite runway — the most dangerous wrong answer available."""
    state({"improver": bank("approved"),
           "guardian_page": bank("approved", "approved", "approved", "approved")})
    assert (await linkedin.post_runway("flycheap")).value == 1
    assert (await linkedin.post_runway("guardian")).value == 4


async def test_an_absent_bank_is_unknown_not_zero(state):
    """"No bank found" and "the bank is empty" are different facts. Reporting
    the first as FAILING would page Miro about a path that changed."""
    state({"improver": {}})
    obs = await linkedin.post_runway("flycheap")
    assert obs.health is Health.UNKNOWN


async def test_disabled_posting_is_not_a_failure(state):
    state({"improver": bank("approved", "draft", enabled=False)})
    obs = await linkedin.post_runway("flycheap")
    assert obs.health is Health.OK
    assert "disabled" in obs.detail


# ------------------------------------------------------- follower ledger

async def test_a_count_that_outruns_the_list_is_degraded(state):
    """The real bug this catches: Guardian held followers=8 against three
    recorded names, so five followers were invisible to every diff and no
    follow-up could ever fire for them."""
    state({"guardian_page": {"followers": 8, "followers_seen": ["a", "b", "c"]}})
    obs = await linkedin.follower_ledger("guardian")
    assert obs.health is Health.DEGRADED
    assert obs.evidence["gap"] == 5
    assert "truncated" in obs.detail
    assert "not an arrival" in obs.detail


async def test_a_matching_ledger_is_ok(state):
    state({"page": {"followers_count": 2, "followers_seen": ["a", "b"]}})
    assert (await linkedin.follower_ledger("flycheap")).health is Health.OK


async def test_an_uncomparable_ledger_is_unknown(state):
    state({"page": {"followers_count": None, "followers_seen": None}})
    assert (await linkedin.follower_ledger("flycheap")).health is Health.UNKNOWN


# --------------------------------------------------------------- staleness

@pytest.mark.parametrize("hours, health", [
    (2, Health.OK), (14, Health.DEGRADED), (30, Health.FAILING)])
async def test_a_stalled_agent_is_visible(state, hours, health):
    """A stalled agent looks exactly like a calm week: nothing posts, nothing
    is reported, and the silence reads as health."""
    state({"last_run": _now(hours), "runs": 19})
    assert (await linkedin.agent_run()).health is health


async def test_a_timestamp_without_an_offset_is_unknown(state):
    """Naive timestamps are ambiguous. Assuming UTC on a local-time string
    shifts staleness by four hours and can hide a missed run."""
    state({"last_run": "2026-09-22T09:45:00"})
    assert (await linkedin.agent_run()).health is Health.UNKNOWN


# ------------------------------------------------------------ alert prose

NEGATED = (
    "BROWSER UNAVAILABLE - run cut short. Browser pane: signed OUT of "
    "LinkedIn, lands on the Sign in page. NOT a LinkedIn restriction/CAPTCHA/"
    "limit warning - no LinkedIn-side block was seen.",
    "Browser pane: signed OUT of LinkedIn. NO LinkedIn-side restriction, "
    "CAPTCHA or limit warning was seen - this is a tooling failure.",
    "Not a LinkedIn restriction/CAPTCHA/limit warning - LinkedIn was never "
    "reached at all this run.",
)


@pytest.mark.parametrize("text", NEGATED)
async def test_a_denial_of_a_restriction_is_not_a_restriction(state, text):
    """Every hard-stop keyword hit in the real state file is of this shape.
    A bare keyword scan fires on the sentence written to say the opposite,
    and would have escalated a signed-out browser as a restricted account."""
    state({"alerts": [{"when": _now(1), "what": text}]})
    obs = await linkedin.account_alerts()
    assert obs.health is Health.OK, f"false positive on: {text[:60]}"


async def test_a_real_restriction_is_surfaced_verbatim(state):
    state({"alerts": [{"when": _now(1),
                       "what": "LinkedIn has restricted the account after "
                               "unusual activity. All actions stopped."}]})
    obs = await linkedin.account_alerts()
    assert obs.health is Health.DEGRADED
    assert "restricted the account" in obs.detail
    assert "cannot confirm" in obs.detail, "do not claim certainty from prose"


async def test_a_restriction_never_reports_failing(state):
    """The check reads prose heuristically. FAILING at Severity.CRITICAL is
    what rings the phone outside quiet hours, and a keyword match has not
    earned that."""
    state({"alerts": [{"when": _now(1), "what": "account restricted, captcha"}]})
    assert (await linkedin.account_alerts()).health is not Health.FAILING
    check, = [c for c in linkedin.MODULE.checks() if c.name == "account_alerts"]
    assert check.severity_on_fail is not Severity.CRITICAL


async def test_self_labelled_tooling_alerts_are_ignored(state):
    state({"alerts": [{"when": _now(1),
                       "what": "Not a LinkedIn alert. The permission "
                               "classifier blocked typing; captcha restricted"}]})
    assert (await linkedin.account_alerts()).health is Health.OK


async def test_old_alerts_do_not_page_forever(state):
    """Alerts accumulate in this file and are never pruned. An unresolved
    restriction from three weeks ago is history, not news."""
    state({"alerts": [{"when": _now(24 * 30),
                       "what": "LinkedIn restricted the account"}]})
    assert (await linkedin.account_alerts()).health is Health.OK


# ---------------------------------------------------------------- signals

async def test_runway_does_not_also_raise_a_signal(state):
    """The check reports runway as DEGRADED/FAILING at Severity.HIGH, which
    is actionable and routes on its own. A signal too is two notifications
    for one fact — and the second one is what makes Miro mute the first."""
    state({"improver": bank("approved")})
    assert [s for s in await linkedin.MODULE.signals() if "runs out" in s.title] == []


async def test_the_check_names_the_drafts_waiting_on_approval(state):
    """That list was the signal's only unique contribution, so it has to
    survive in the check or dropping the signal loses information."""
    state({"improver": bank("approved", "draft", "needs_rework_2026_09_20")})
    detail = (await linkedin.post_runway("flycheap")).detail
    assert "B2, B3" in detail
    assert "they need a yes" in detail


async def test_open_items_are_surfaced_with_their_blocker(state):
    """The key is date-suffixed, so the name is not fixed."""
    state({"open_items_2026_09_21": [
        {"what": "delete and repost B11 text-only, or leave it?",
         "blocked_by": "Myron's decision"}]})
    signal, = [s for s in await linkedin.MODULE.signals() if "waiting on you" in s.title]
    assert "Myron's decision" in signal.body


async def test_agenda_names_the_day_each_page_goes_quiet(state):
    state({"improver": bank("approved", "approved")})
    item, = await linkedin.MODULE.agenda(timedelta(days=7))
    assert item.kind == "deadline"
    assert (item.when - datetime.now(UTC)).days == 1     # 2 posts, 1/day


async def test_agenda_respects_the_horizon(state):
    state({"improver": bank(*(("approved",) * 40))})
    assert await linkedin.MODULE.agenda(timedelta(days=7)) == ()


# ------------------------------------------------------------ stale mirror
