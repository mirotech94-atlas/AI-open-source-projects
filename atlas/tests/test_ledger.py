"""The mistake ledger (kernel/ledger.py): the same failure recognised under
different parameters and across nights, said once when it becomes a
pattern and once when it comes back. A report, never a rule."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from atlas.kernel import ledger

NOW = datetime(2026, 10, 6, 3, 0, tzinfo=UTC)


def _row(action, lesson, times=1, days_ago=1.0):
    at = NOW - timedelta(days=days_ago)
    return {"action": action, "lesson": lesson, "times": times, "at": at, "last_at": at}


def test_what_varies_is_removed_so_the_same_fault_reads_the_same():
    a = "content.publish_reel failed: HTTP 429 for https://api.x.test/v1/post/8812 after 3 tries"
    b = "content.publish_reel failed: HTTP 429 for https://api.x.test/v1/post/9040 after 5 tries"
    assert ledger.normalise(a) == ledger.normalise(b) == "http <n> for <url> after <n> tries"
    assert ledger.fingerprint("content.publish_reel", a) == ledger.fingerprint(
        "content.publish_reel", b)
    assert ledger.fingerprint("other.action", a) != ledger.fingerprint("content.publish_reel", a)
    assert ledger.normalise("x failed: no file 'reel-22.mp4' in job 4f9a7c2d11") == \
        "no file <value> in job <id>"


def test_one_fault_under_two_parameter_sets_is_one_recurring_mistake():
    """What `lessons` cannot see: each row is times = 1, a different key."""
    rows = [_row("agency.verify", "agency.verify failed: timeout after 30 s for site 12"),
            _row("agency.verify", "agency.verify failed: timeout after 30 s for site 44", days_ago=2)]
    [m] = ledger.group(rows)
    assert (m.occurrences, m.variants) == (2, 2) and ledger.is_recurring(m)
    assert m.first_seen < m.last_seen


def test_a_single_failure_is_new_and_is_not_announced():
    [m] = ledger.group([_row("mail.draft", "mail.draft failed: boom")])
    steps, close = ledger.plan([m], {}, NOW)
    assert [(s.state, s.tell) for s in steps] == [("new", False)] and close == []
    assert ledger.digest(steps) == ""


def test_a_pattern_is_said_once_not_every_night():
    [m] = ledger.group([_row("a.b", "a.b failed: nope", times=3)])
    [first], _ = ledger.plan([m], {}, NOW)
    assert (first.state, first.tell) == ("recurring", True)
    known = {m.fingerprint: {"state": "recurring", "told_at": NOW, "closed_at": None,
                             "last_seen": m.last_seen}}
    [again], _ = ledger.plan([m], known, NOW + timedelta(days=1))
    assert (again.state, again.tell) == ("recurring", False)


def test_silent_for_two_weeks_closes_and_coming_back_reopens_and_is_said():
    old_fp = "f" * 16
    known = {old_fp: {"state": "recurring", "told_at": NOW, "closed_at": None,
                      "last_seen": NOW - timedelta(days=ledger.CLOSE_DAYS + 1)}}
    steps, close = ledger.plan([], known, NOW)
    assert steps == [] and close == [old_fp]

    [m] = ledger.group([_row("a.b", "a.b failed: nope")])
    closed = {m.fingerprint: {"state": "closed", "told_at": NOW - timedelta(days=30),
                              "closed_at": NOW - timedelta(days=5),
                              "last_seen": NOW - timedelta(days=20)}}
    [step], _ = ledger.plan([m], closed, NOW)
    assert (step.state, step.tell, step.reopened) == ("reopened", True, True)
    assert "Came back after it had stopped" in ledger.digest([step])


def test_the_digest_carries_the_evidence_and_the_summary_counts():
    ms = ledger.group([_row("a.b", "a.b failed: timeout 3", times=4),
                       _row("c.d", "c.d failed: once")])
    steps, _ = ledger.plan(ms, {}, NOW)
    text = ledger.digest(steps)
    assert "Keeps failing: `a.b`" in text and "4 time(s)" in text and "Example: a.b failed" in text
    assert "c.d" not in text
    assert ledger.summary_line(steps, 2) == "Ledger: 1 new, 1 recurring, 0 reopened, 2 closed"


async def test_nightly_writes_the_rows_and_posts_one_digest(memory_backend, monkeypatch):
    posted: list = []

    async def notify(msg):
        posted.append(msg)
        return ["slack"]
    monkeypatch.setattr("atlas.kernel.comms.notify", notify)
    memory_backend.responses["SELECT action, lesson, times, at, last_at FROM lessons"] = [
        _row("a.b", "a.b failed: timeout 3", times=3), _row("c.d", "c.d failed: once")]
    memory_backend.responses["SELECT fingerprint, state, told_at"] = []
    line = await ledger.nightly(NOW)
    assert line == "Ledger: 1 new, 1 recurring, 0 reopened, 0 closed"
    writes = [a for s, a in memory_backend.sql_log if "INSERT INTO ledger_mistakes" in s]
    assert len(writes) == 2
    [msg] = posted
    assert msg.title == line and msg.topic == "brief" and "`a.b`" in msg.body


async def test_nightly_changes_nothing_when_it_cannot_read(monkeypatch):
    async def broken(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr("atlas.kernel.state.fetch_all", broken)
    assert await ledger.nightly(NOW) is None


def test_the_ledger_is_a_report_and_reads_nothing_that_decides():
    """It never imports the executor, the permission layer or approvals."""
    import inspect
    src = inspect.getsource(ledger)
    for forbidden in ("executor", "permissions", "approvals", "propose(", "REGISTRY"):
        assert forbidden not in src.split('"""', 2)[2], forbidden
