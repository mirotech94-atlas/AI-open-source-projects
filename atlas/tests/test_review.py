"""The nightly review. Same discipline as the weekly summary: every line
comes from a table this process already writes to, never from a model."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from atlas.kernel import review as review_mod
from atlas.kernel.review import Review, build, render

TODAY = date(2026, 9, 24)


def _fake_fetch_all(rows_by_marker: dict[str, list[dict]]):
    """A `fetch_all` stand-in that routes on a unique substring of the SQL,
    the same style `tests/test_brief.py` uses for `weekly.build()`."""

    async def fetch_all(sql, *args):
        flat = " ".join(sql.split())
        for marker, rows in rows_by_marker.items():
            if marker in flat:
                return rows
        return []

    return fetch_all


# ------------------------------------------------------------------ build

async def test_two_recoveries_against_one_failure_scores_two_thirds(monkeypatch):
    monkeypatch.setattr("atlas.kernel.state.fetch_all", _fake_fetch_all({
        "bad.health IN": [
            {"property": "flycheap", "check_name": "affiliate_links"},
            {"property": "cloudco", "check_name": "protection"},
        ],
        "execute.error": [{"subject": "guardian.reply", "n": 1}],
    }))

    result = await build(TODAY)

    assert result.score == pytest.approx(2 / 3)
    assert len(result.went_well) == 2
    assert any("flycheap.affiliate_links recovered" in x for x in result.went_well)
    assert any("cloudco.protection recovered" in x for x in result.went_well)
    assert len(result.went_wrong) == 1
    assert "guardian.reply failed, 1 time" in result.went_wrong[0]


async def test_a_quiet_day_has_no_score(monkeypatch):
    monkeypatch.setattr("atlas.kernel.state.fetch_all", _fake_fetch_all({}))
    result = await build(TODAY)
    assert result.went_well == []
    assert result.went_wrong == []
    assert result.decisions == []
    assert result.score is None


async def test_approved_and_executed_proposals_count_as_went_well(monkeypatch):
    monkeypatch.setattr("atlas.kernel.state.fetch_all", _fake_fetch_all({
        "SELECT action, decided_at FROM proposals": [
            {"action": "flycheap.pause_listing", "decided_at": datetime.now(UTC)},
        ],
    }))
    result = await build(TODAY)
    assert any("flycheap.pause_listing — approved and done" in x
               for x in result.went_well)
    assert result.score == 1.0


async def test_expired_proposals_and_open_findings_count_as_went_wrong(monkeypatch):
    monkeypatch.setattr("atlas.kernel.state.fetch_all", _fake_fetch_all({
        "status = 'expired'": [{"action": "guardian.escalate"}],
        "DISTINCT ON (f.check_key)": [
            {"check_key": "flycheap.api", "detail": "still 500ing",
             "health": "failing"},
            {"check_key": "cloudco.protection", "detail": "recovered now",
             "health": "ok"},
        ],
    }))
    result = await build(TODAY)
    assert "guardian.escalate — expired unanswered" in result.went_wrong
    assert "flycheap.api — still failing" in result.went_wrong
    # The detail stays out: every went_wrong line becomes a lesson in each
    # agent's system prompt, and a detail quotes what the collector saw.
    assert not any("500ing" in x for x in result.went_wrong)
    assert not any("cloudco.protection" in x for x in result.went_wrong)
    assert result.score == 0.0


async def test_unattended_tier1_successes_are_labelled_ran_on_its_own(monkeypatch):
    """`approved_by` is null exactly when nothing had to approve it -- only a
    Tier 1 action ever reaches the executor unattended."""
    monkeypatch.setattr("atlas.kernel.state.fetch_all", _fake_fetch_all({
        "approved_by') IS NULL": [
            {"subject": "flycheap.repost_listing", "n": 2},
        ],
    }))
    result = await build(TODAY)
    assert any("flycheap.repost_listing ran on its own, 2 times" in x
               for x in result.went_well)


async def test_a_live_phone_approval_is_labelled_on_your_word(monkeypatch):
    """`intent.py` can approve and execute a Tier 2 action in the same
    breath, over the phone, without ever writing a `proposals` row -- that
    must not be mislabelled with the Tier 1 "ran on its own" wording."""
    monkeypatch.setattr("atlas.kernel.state.fetch_all", _fake_fetch_all({
        "approved_by') IS NOT NULL": [
            {"subject": "guardian.refund", "approved_by": "voice:owner"},
        ],
    }))
    result = await build(TODAY)
    assert "guardian.refund — on your word from voice" in result.went_well
    assert not any("ran on its own" in x for x in result.went_well)


async def test_a_proposal_backed_execution_is_not_also_counted_as_live(
        monkeypatch):
    """An approved-and-executed proposal shows `approved_by` on its finish
    event too (`approvals.py` passes the channel through) -- it must be
    counted once, under "approved and done", not a second time here."""
    monkeypatch.setattr("atlas.kernel.state.fetch_all", _fake_fetch_all({
        "approved_by') IS NOT NULL": [],   # excluded by the NOT IN proposals
        "SELECT action, decided_at FROM proposals": [
            {"action": "flycheap.pause_listing", "decided_at": datetime.now(UTC)},
        ],
    }))
    result = await build(TODAY)
    assert result.went_well == ["flycheap.pause_listing — approved and done"]


async def test_build_queries_local_midnight_bounds_not_a_date_cast(monkeypatch):
    """A bare `at::date = $1` truncates in the database session's timezone,
    not the caller's local one -- on a UTC session that misfiles a slice of
    every local day. Every query must instead pass an explicit half-open
    range of local-time bounds."""
    captured: list[tuple[str, tuple]] = []

    async def fetch_all(sql, *args):
        captured.append((sql, args))
        return []

    monkeypatch.setattr("atlas.kernel.state.fetch_all", fetch_all)
    await build(TODAY)

    assert captured, "build() must run at least one query"
    for sql, args in captured:
        assert "::date" not in sql, f"bare date cast left in: {sql[:80]}"
        assert len(args) == 2, f"expected (start, end) bounds: {sql[:80]}"
        start, end = args
        assert start.tzinfo is not None and end.tzinfo is not None
        assert end - start == timedelta(days=1)
        assert start.astimezone().date() == TODAY


async def test_decisions_come_from_the_decision_log_verbatim(monkeypatch):
    monkeypatch.setattr("atlas.kernel.state.fetch_all", _fake_fetch_all({
        "FROM decision_log": [
            {"module": "flycheap", "action": "pause_listing",
             "outcome": "approved"},
        ],
    }))
    result = await build(TODAY)
    assert result.decisions == ["flycheap.pause_listing: approved"]


async def test_a_broken_query_does_not_crash_the_review(monkeypatch):
    async def boom(sql, *a):
        raise RuntimeError("database is down")
    monkeypatch.setattr("atlas.kernel.state.fetch_all", boom)
    result = await build(TODAY)
    assert result.went_well == []
    assert result.went_wrong == []
    assert result.score is None


# ----------------------------------------------------------------- render

def test_render_names_the_day_and_lists_all_three():
    r = Review(day=TODAY, went_well=["a recovered"], went_wrong=["b failed"],
               decisions=["m.a: approved"], score=0.5)
    text = render(r)
    assert "*Tonight*" in text
    assert "a recovered" in text
    assert "b failed" in text
    assert "m.a: approved" in text
    assert "Score: 0.50" in text


def test_render_of_a_zero_day_says_so_plainly():
    r = Review(day=TODAY, went_well=[], went_wrong=[], decisions=[], score=None)
    text = render(r)
    assert text == "*Tonight* — 24 September\nQuiet day: nothing broke and nothing was decided."


# ------------------------------------------------------------------- send

async def test_send_stores_a_review_row_and_a_report_row(monkeypatch):
    calls = []

    async def fake_notify(msg):
        calls.append(("notify", msg.title, msg.severity))
        return ["slack"]

    inserted = {}

    async def fake_fetch_one(sql, *args):
        flat = " ".join(sql.split())
        if "INSERT INTO reviews" in flat:
            inserted["review"] = args
            return {"id": 1}
        if "INSERT INTO reports" in flat:
            inserted["report"] = args
            return {"id": 2}
        return None

    async def fake_execute_sql(sql, *args):
        inserted["linked"] = args
        return "OK"

    monkeypatch.setattr("atlas.kernel.state.fetch_all", _fake_fetch_all({}))
    monkeypatch.setattr("atlas.kernel.comms.notify", fake_notify)
    monkeypatch.setattr("atlas.kernel.state.fetch_one", fake_fetch_one)
    monkeypatch.setattr("atlas.kernel.state.execute_sql", fake_execute_sql)

    result = await review_mod.send(TODAY)

    assert result.day == TODAY
    assert calls and calls[0][0] == "notify"
    assert "review" in inserted and "report" in inserted
    assert inserted["linked"] == (1, 2)


# --------------------------------------------------- nothing here guesses

def test_the_review_never_asks_a_model_for_a_number():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(review_mod))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = " ".join(a.name for a in node.names)
            where = getattr(node, "module", "") or ""
            assert "llm" not in f"{where} {names}", \
                "the daily review must not use a model"
            assert "executor" not in f"{where} {names}", \
                "the daily review must not import the executor"


# --------------------------------------------------------------- scheduler

def _claims(monkeypatch):
    """kernel.occurrence against an in-memory table (the claim, and the
    history the missed-run policy reads)."""
    from atlas.kernel import missed, occurrence
    table: dict[tuple[str, str], str] = {}
    missed.forget()

    async def claim(job, window, scheduled_for=None):
        if table.get((job, window)) in ("running", "done"):
            return False
        table[(job, window)] = "running"
        return True

    async def done(job, window):
        table[(job, window)] = "done"

    async def release(job, window):
        table[(job, window)] = "failed"

    async def history(job, windows):
        rows = {w: {"status": st, "claimed_at": None}
                for (j, w), st in table.items() if j == job and w in windows}
        return rows, any(j == job for j, _ in table)

    async def skip(job, window, scheduled_for, reason):
        table[(job, window)] = "skipped"
        return True

    monkeypatch.setattr(occurrence, "claim", claim)
    monkeypatch.setattr(occurrence, "done", done)
    monkeypatch.setattr(occurrence, "release", release)
    monkeypatch.setattr(occurrence, "history", history)
    monkeypatch.setattr(occurrence, "skip", skip)
    return table


async def test_a_restart_inside_the_window_does_not_resend(monkeypatch):
    """2026-09-27: ten reviews, one per restart after 23:00. The process
    forgets the date; the claimed window does not."""
    from atlas.kernel import scheduler
    monkeypatch.setenv("REVIEW_HOUR", "21")
    sent = []

    async def fake_send(day=None):
        sent.append(1)
    monkeypatch.setattr(review_mod, "send", fake_send)
    _claims(monkeypatch)
    monkeypatch.setattr(scheduler, "_now",
                        lambda: datetime(2026, 9, 27, 23, 5).astimezone())
    for _restart in range(10):
        scheduler._last_review_date = None          # a fresh process
        await scheduler._maybe_review()
    assert sent == [1]


async def test_a_failed_send_is_retried_later_in_the_window(monkeypatch):
    from atlas.kernel import scheduler
    monkeypatch.setenv("REVIEW_HOUR", "21")
    calls = []

    async def flaky(day=None):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("slack down")
    monkeypatch.setattr(review_mod, "send", flaky)
    table = _claims(monkeypatch)
    monkeypatch.setattr(scheduler, "_now",
                        lambda: datetime(2026, 9, 27, 23, 5).astimezone())
    scheduler._last_review_date = None
    await scheduler._maybe_review()
    await scheduler._maybe_review()
    assert len(calls) == 2 and table[("review", "2026-09-27")] == "done"


async def test_maybe_review_sends_once_per_local_date(monkeypatch):
    from atlas.kernel import scheduler

    monkeypatch.setenv("REVIEW_HOUR", "21")
    scheduler._last_review_date = None
    sent = []

    async def fake_send(day=None):
        sent.append(1)
    monkeypatch.setattr(review_mod, "send", fake_send)
    _claims(monkeypatch)

    before_hour = datetime(2026, 9, 24, 20, 0).astimezone()
    monkeypatch.setattr(scheduler, "_now", lambda: before_hour)
    await scheduler._maybe_review()
    assert sent == []

    at_hour = datetime(2026, 9, 24, 21, 5).astimezone()
    monkeypatch.setattr(scheduler, "_now", lambda: at_hour)
    await scheduler._maybe_review()
    assert sent == [1]

    later_same_day = datetime(2026, 9, 24, 23, 0).astimezone()
    monkeypatch.setattr(scheduler, "_now", lambda: later_same_day)
    await scheduler._maybe_review()
    assert sent == [1]                       # not sent twice

    next_day = datetime(2026, 9, 25, 21, 30).astimezone()
    monkeypatch.setattr(scheduler, "_now", lambda: next_day)
    await scheduler._maybe_review()
    assert sent == [1, 1]


async def test_the_scheduler_step_list_runs_the_review(monkeypatch):
    from atlas.kernel import scheduler

    ran: list[str] = []

    async def note(name):
        ran.append(name)

    monkeypatch.setattr(scheduler, "_run_checks", lambda: note("checks"))
    monkeypatch.setattr(scheduler, "expire_stale", lambda: note("approvals"))
    monkeypatch.setattr(scheduler, "_maybe_signals", lambda: note("signals"))
    monkeypatch.setattr(scheduler, "_maybe_brief", lambda: note("brief"))
    monkeypatch.setattr(scheduler, "_maybe_weekly", lambda: note("weekly"))
    monkeypatch.setattr(scheduler, "_maybe_review", lambda: note("review"))
    monkeypatch.setattr(scheduler, "_maybe_purge", lambda: note("retention"))
    monkeypatch.setattr(scheduler, "_maybe_tournament", lambda: note("judgment"))

    async def stop(_seconds):
        raise KeyboardInterrupt
    monkeypatch.setattr(scheduler.asyncio, "sleep", stop)

    with pytest.raises(KeyboardInterrupt):
        await scheduler.loop(interval=0)

    assert "review" in ran
