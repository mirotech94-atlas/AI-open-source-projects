"""The morning brief. It runs every day, so it is the thing most likely to get
muted — which means it must never claim something it did not check."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from atlas.kernel import brief as brief_mod
from atlas.kernel.brief import Section, build
from atlas.kernel.contracts import AgendaItem, InboxItem, Severity

NOW = datetime.now(UTC)
UNREADABLE_MAIL = "inbox for mail unreadable — what is waiting there is not known"


class FakeModule:
    def __init__(self, name, *, agenda=(), inbox=(), line="", explode=False):
        self.name = name
        self.title = name
        self._agenda = agenda
        self._inbox = inbox
        self._line = line
        self._explode = explode

    async def agenda(self, within):
        if self._explode:
            raise RuntimeError("calendar API down")
        return self._agenda

    async def inbox(self, limit=10):
        if self._explode:
            raise RuntimeError("mailbox unreachable")
        return self._inbox

    def brief(self):
        return self._line


@pytest.fixture
def empty_db(memory_backend, monkeypatch):
    for fragment in ("FROM incidents", "FROM actions_log", "FROM proposals",
                     "FROM brief_queue"):
        memory_backend.responses[fragment] = []
    memory_backend.responses["WHERE event = 'execute.watch_only'"] = [{"n": 0}]
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "true")
    return memory_backend


@pytest.fixture
def modules(monkeypatch):
    registry = {}
    monkeypatch.setattr(brief_mod.REGISTRY, "modules", registry)
    monkeypatch.setattr(brief_mod.REGISTRY, "disabled", {})
    return registry


def section(brief, title) -> Section:
    return next(s for s in brief.sections if s.title == title)


def local_today_at(hour: int) -> datetime:
    """An aware datetime at `hour` o'clock on the local calendar day."""
    return datetime.now().astimezone().replace(
        hour=hour, minute=0, second=0, microsecond=0)


# ------------------------------------------------------ never claim silence

async def test_a_failing_section_says_so_instead_of_reading_as_empty(
        empty_db, modules):
    """'I could not look' must not render as 'there is nothing'."""
    modules["mail"] = FakeModule("mail", explode=True)
    result = await build()
    needs_reply = section(result, "Needs a reply")
    # The module failing is named, never folded into an empty section...
    assert needs_reply.lines == [UNREADABLE_MAIL]
    # ...and a section-level failure renders as a warning, not as "Nothing".
    broken = Section("X", unavailable="boom")
    assert "could not check" in broken.render()
    assert "Nothing" not in broken.render()


async def test_an_agenda_failure_marks_both_time_sections_unavailable(
        empty_db, modules, monkeypatch):
    async def explode(_now):
        raise RuntimeError("no calendar")
    monkeypatch.setattr(brief_mod, "_agenda", explode)
    result = await build()
    assert "no calendar" in section(result, "Today").render()
    assert "no calendar" in section(result, "Due inside 72 hours").render()
    assert "Nothing scheduled" not in section(result, "Today").render()


async def test_an_unreadable_inbox_is_named_never_nothing_waiting(
        empty_db, modules):
    """A module whose inbox read failed is not an empty inbox: the section
    names it, and never says "Nothing waiting on you" over it."""
    modules["mail"] = FakeModule("mail", explode=True)
    modules["messages"] = FakeModule("messages")
    brief = await build()
    lines = section(brief, "Needs a reply").lines
    assert lines == [UNREADABLE_MAIL]
    # A section with lines never falls back to its `empty` sentence.
    assert section(brief, "Needs a reply").lines


async def test_an_unreadable_inbox_is_listed_above_what_was_read(empty_db, modules):
    item = InboxItem(at=NOW - timedelta(hours=2), sender="Ann", subject="hi",
                     urgency=Severity.HIGH, why="reply today", reference="1")
    modules["mail"] = FakeModule("mail", inbox=(item,))
    modules["agency"] = FakeModule("agency", explode=True)
    lines = section(await build(), "Needs a reply").lines
    assert lines[0].startswith("inbox for agency unreadable")
    assert any("Ann: hi" in line for line in lines[1:])


async def test_an_empty_brief_is_explicit_about_being_empty(empty_db, modules):
    result = await build()
    rendered = result.render()
    assert "No incidents" in rendered
    assert "Nothing waiting on you" in rendered
    assert "No decisions pending" in rendered


async def test_a_pause_waiting_on_him_is_in_the_brief(empty_db, modules):
    """R09: a pause set by a refused login waits for his resume, so the
    brief says it -- in the scoped form for one account."""
    empty_db.responses["FROM pauses WHERE resumed_at IS NULL ORDER BY paused_at"] = [
        {"scope": "instagram:clips", "paused_at": datetime.now(UTC),
         "paused_by": "atlas:auth-refused",
         "reason": "instagram.publish's login was refused (HTTP 401)"}]
    waiting = section(await build(), "Waiting on you")
    assert any(line.startswith("Paused: the instagram module's clips account "
                               "is paused") for line in waiting.lines)


# ------------------------------------------------------------- conflicts

async def test_overlapping_events_are_flagged(empty_db, modules):
    # "Today" is the LOCAL calendar day — that is what a morning brief means.
    # Building these in UTC makes the test ambiguous near local midnight.
    start = local_today_at(9)
    modules["calendar"] = FakeModule("calendar", agenda=(
        AgendaItem(when=start, title="Standup", minutes=60),
        AgendaItem(when=start + timedelta(minutes=30), title="Client call",
                   minutes=30),
        AgendaItem(when=start + timedelta(hours=5), title="Gym", minutes=60),
    ))
    today = section(await build(), "Today")
    clashing = [line for line in today.lines if "CLASHES" in line]
    assert len(clashing) == 2
    assert any("Standup" in line for line in clashing)
    assert any("Client call" in line for line in clashing)
    assert not any("Gym" in line for line in clashing)


async def test_conflicts_are_detected_across_different_modules(empty_db, modules):
    start = local_today_at(11)
    modules["calendar"] = FakeModule("calendar", agenda=(
        AgendaItem(when=start, title="Interview", minutes=60),))
    modules["fiverr"] = FakeModule("fiverr", agenda=(
        AgendaItem(when=start + timedelta(minutes=15), title="Order delivery",
                   minutes=30),))
    today = section(await build(), "Today")
    assert sum("CLASHES" in line for line in today.lines) == 2


# -------------------------------------------------------------- deadlines

async def test_deadlines_inside_72_hours_appear_and_later_ones_do_not(
        empty_db, modules):
    now = datetime.now(UTC)
    modules["gitlab"] = FakeModule("gitlab", agenda=(
        AgendaItem(when=now + timedelta(hours=30), title="Coursework due",
                   kind="deadline"),
        AgendaItem(when=now + timedelta(days=9), title="Far away",
                   kind="deadline"),
    ))
    due = section(await build(), "Due inside 72 hours")
    assert any("Coursework due" in line for line in due.lines)
    assert not any("Far away" in line for line in due.lines)


async def test_an_overdue_deadline_is_marked(empty_db, modules):
    modules["x"] = FakeModule("x", agenda=(
        AgendaItem(when=datetime.now(UTC) - timedelta(hours=3),
                   title="Invoice", kind="deadline"),))
    due = section(await build(), "Due inside 72 hours")
    assert any("OVERDUE" in line for line in due.lines)


# ------------------------------------------------------------ inbox order

async def test_replies_are_ranked_by_urgency(empty_db, modules):
    now = datetime.now(UTC)
    modules["mail"] = FakeModule("mail", inbox=(
        InboxItem(at=now - timedelta(hours=2), sender="Ann", subject="hi",
                  urgency=Severity.INFO),
        InboxItem(at=now - timedelta(hours=90), sender="Bob", subject="invoice",
                  urgency=Severity.HIGH, why="unanswered for 90h"),
    ))
    lines = section(await build(), "Needs a reply").lines
    assert "Bob" in lines[0] and "HIGH" in lines[0]
    assert "Ann" in lines[1]


# ---------------------------------------------------- disabled visibility

async def test_a_disabled_module_is_named_as_not_watched(
        empty_db, modules, monkeypatch):
    monkeypatch.setattr(brief_mod.REGISTRY, "disabled",
                        {"flycheap": "missing env: FLYCHEAP_API_TOKEN"})
    stand = section(await build(), "Where things stand")
    assert any("flycheap: not watched" in line for line in stand.lines)


# ----------------------------------------------------------- watch-only

async def test_watch_only_is_stated_in_the_brief(empty_db, modules):
    assert "watch-only" in (await build()).render()


async def test_withheld_actions_are_reported(memory_backend, modules, monkeypatch):
    for fragment in ("FROM incidents", "FROM proposals", "FROM brief_queue"):
        memory_backend.responses[fragment] = []
    memory_backend.responses["event = 'execute.finish'"] = []
    memory_backend.responses["WHERE event = 'execute.watch_only'"] = [{"n": 4}]
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "true")
    overnight = section(await build(), "Overnight")
    assert any("withheld" in line and "4" in line for line in overnight.lines)


# ------------------------------------------- the brief must survive failures

async def test_the_brief_still_runs_when_checks_and_reasoning_fail(monkeypatch):
    """One failing step must not stop the others.

    The scheduler previously ran everything in a single try block, so a finding
    that could not be reasoned about aborted proposal expiry, retention and the
    morning brief for that iteration — and every iteration after it.
    """
    from atlas.kernel import scheduler

    ran: list[str] = []

    async def boom():
        raise RuntimeError("no LLM provider configured")

    async def note(name):
        ran.append(name)

    monkeypatch.setattr(scheduler, "_run_checks", boom)
    monkeypatch.setattr(scheduler, "expire_stale", lambda: note("approvals"))
    monkeypatch.setattr(scheduler, "_maybe_signals", lambda: note("signals"))
    monkeypatch.setattr(scheduler, "_maybe_brief", lambda: note("brief"))
    monkeypatch.setattr(scheduler, "_maybe_weekly", lambda: note("weekly"))
    monkeypatch.setattr(scheduler, "_maybe_marketing", lambda: note("marketing"))
    monkeypatch.setattr(scheduler, "_maybe_review", lambda: note("review"))
    monkeypatch.setattr(scheduler, "_maybe_rhythm", lambda: note("rhythm"))
    monkeypatch.setattr(scheduler, "_maybe_purge", lambda: note("retention"))
    monkeypatch.setattr(scheduler, "_maybe_tournament", lambda: note("judgment"))
    # Detached, same as _maybe_fixes just above it in the tuple (also real
    # and unstubbed here) -- both catch their own failures internally and
    # log rather than raise, so leaving them real does not break this test.
    # Only the new step needs a stub: `agents.ROSTER` is whatever earlier
    # tests left it, and a real shift run is a model call this test must
    # not make.
    monkeypatch.setattr(scheduler, "_maybe_agent_shifts", lambda: note("agent_shifts"))
    # Same for the Agency meeting: a real one reads agent_runs and asks a
    # model to reword it.
    monkeypatch.setattr(scheduler, "_maybe_agency_meeting",
                        lambda: note("agency_meeting"))
    # The lead board reads the agency tables; the lead replies draft with
    # a model. Neither belongs in this test.
    monkeypatch.setattr(scheduler, "_maybe_lead_board",
                        lambda: note("agency_lead_board"))
    monkeypatch.setattr(scheduler, "_maybe_lead_replies",
                        lambda: note("lead_replies"))
    # The LinkedIn page replies draft with a model and drive a browser.
    monkeypatch.setattr(scheduler, "_maybe_linkedin_replies",
                        lambda: note("linkedin_replies"))

    async def stop(_seconds):
        raise KeyboardInterrupt
    monkeypatch.setattr(scheduler.asyncio, "sleep", stop)

    scheduler._tasks.clear()
    with pytest.raises(KeyboardInterrupt):
        await scheduler.loop(interval=0)

    # The inline steps ran past the failing one, in order.
    assert ran == ["approvals", "agency_lead_board", "marketing", "lead_replies",
                   "linkedin_replies", "review", "rhythm"]
    # The expensive ones no longer run inline — a reply draft runs a model per
    # message, and while that held the cycle nothing was checked at all. They
    # are dispatched as their own tasks, and they still all run.
    await asyncio.gather(*scheduler._tasks.values())
    scheduler._tasks.clear()
    assert sorted(ran) == ["agency_lead_board", "agency_meeting", "agent_shifts",
                           "approvals", "brief", "judgment", "lead_replies",
                           "linkedin_replies", "marketing", "retention", "review", "rhythm",
                           "signals", "weekly"]


# ---------------------------------------------------------- weekly summary

from atlas.kernel import weekly as weekly_mod


async def test_a_quiet_week_says_so_plainly(monkeypatch, memory_backend):
    async def rows(sql, *a):
        if "DISTINCT ON" in sql:
            return [{"health": "ok", "detail": "", "check_name": "api",
                     "property": "flycheap"}]
        return []

    monkeypatch.setattr("atlas.kernel.state.fetch_all", rows)
    # These are about the health checks; "Checked my work" has its own
    # tests (test_automation_reports.py), including how it moves the headline.
    async def nothing_to_say(*a):
        return None

    monkeypatch.setattr(weekly_mod, "checked_my_work", nothing_to_say)
    out = await weekly_mod.build()
    assert "Quiet week" in out.headline


async def test_an_unknown_check_is_not_reported_as_fine(monkeypatch, memory_backend):
    """"Nothing broke" and "I couldn't look" are different weeks."""
    async def rows(sql, *a):
        if "DISTINCT ON" in sql:
            return [{"health": "unknown", "detail": "no token",
                     "check_name": "peers", "property": "netbird"}]
        return []

    monkeypatch.setattr("atlas.kernel.state.fetch_all", rows)
    # These are about the health checks; "Checked my work" has its own
    # tests (test_automation_reports.py), including how it moves the headline.
    async def nothing_to_say(*a):
        return None

    monkeypatch.setattr(weekly_mod, "checked_my_work", nothing_to_say)
    out = await weekly_mod.build()
    assert "Quiet week" not in out.headline
    assert "unknown rather than fine" in out.headline
    assert any("Still blind" in p for p in out.paragraphs)


async def test_a_long_running_failure_says_how_long(monkeypatch, memory_backend):
    """"It has been like that all week" is the sentence that makes someone
    act; "failing" on its own is one they have already scrolled past."""
    from datetime import UTC, datetime, timedelta

    async def rows(sql, *a):
        if "DISTINCT ON" in sql:
            return [{"health": "failing",
                     "detail": "no snapshots and no backups exist",
                     "check_name": "protection", "property": "vpsie"}]
        if "min(o.at)" in sql:
            return [{"since": datetime.now(UTC) - timedelta(days=7)}]
        return []

    monkeypatch.setattr("atlas.kernel.state.fetch_all", rows)
    out = await weekly_mod.build()
    body = "\n".join(out.paragraphs)
    assert "no snapshots and no backups exist" in body
    assert "all week" in body


async def test_an_unreadable_database_is_not_a_quiet_week(monkeypatch, memory_backend):
    """The dangerous failure: reporting silence when the truth is that
    nothing could be read."""
    async def boom(*a, **k):
        raise RuntimeError("database is down")

    monkeypatch.setattr("atlas.kernel.state.fetch_all", boom)
    out = await weekly_mod.build()
    assert "can't tell you" in out.headline
    assert "missing rather than empty" in "\n".join(out.paragraphs)


async def test_the_summary_never_asks_a_model_for_a_number():
    """A weekly summary that quietly invented a figure would take weeks to
    notice. Every number here comes from the same tables the tiles read."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(weekly_mod))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = " ".join(a.name for a in node.names)
            where = getattr(node, "module", "") or ""
            assert "llm" not in f"{where} {names}", "the weekly summary must not use a model"


async def test_a_restart_does_not_send_the_brief_again(monkeypatch):
    """Every api restart after BRIEF_HOUR re-sent the brief — three on
    2026-09-24. The archive is the durable guard: a brief stored since
    midnight means today's already went out."""
    from atlas.kernel import brief

    async def already(*a, **k):
        return {"x": 1}
    monkeypatch.setattr("atlas.kernel.state.fetch_one", already)

    async def boom():
        raise AssertionError("built a second brief on the same day")
    monkeypatch.setattr(brief, "build", boom)
    assert await brief.send() is None


async def test_a_brief_asked_for_by_hand_still_goes_out(monkeypatch):
    from atlas.kernel import brief

    async def already(*a, **k):
        raise AssertionError("force must not consult the once-a-day guard")
    monkeypatch.setattr(brief, "already_sent_today", already)
    built = []

    async def fake_build():
        built.append(1)
        raise RuntimeError("stop here")        # proves we got past the guard
    monkeypatch.setattr(brief, "build", fake_build)
    import pytest
    with pytest.raises(RuntimeError, match="stop here"):
        await brief.send(force=True)
    assert built == [1]


async def test_the_weekly_is_kept_so_a_restart_does_not_send_it_again(
        monkeypatch, memory_backend):
    """It used to be kept through brief.store, which reads `.sections`; a
    Weekly has none, every store raised, and each restart that week sent
    "This week" again (six times on 2026-09-27)."""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    weekly = weekly_mod.Weekly(at=now, since=now - timedelta(days=7),
                               headline="Quiet week.")
    sent: list = []

    async def fake_build(days: int = 7, money=None):
        return weekly

    async def fake_notify(msg):
        sent.append(msg)
        return ["slack"]

    kept: list = []

    async def fake_fetch_one(sql, *args):
        if "INSERT INTO reports" in sql:
            kept.append(args)
            return {"id": 1}
        return None if not kept else {"x": 1}

    monkeypatch.setattr(weekly_mod, "build", fake_build)
    monkeypatch.setattr("atlas.kernel.comms.notify", fake_notify)
    monkeypatch.setattr("atlas.kernel.state.fetch_one", fake_fetch_one)

    await weekly_mod.send()
    assert len(sent) == 1 and sent[0].topic == "brief"
    assert kept and kept[0][1] == "weekly"
    # The archive row now exists, so the next call is a no-op.
    await weekly_mod.send()
    assert len(sent) == 1


async def test_the_brief_and_the_expiry_notice_have_their_own_channels():
    """Without a topic both went to the default channel with everything else."""
    import inspect

    from atlas.kernel import approvals, brief
    assert 'topic="brief"' in inspect.getsource(brief.send)
    assert 'topic="decisions"' in inspect.getsource(approvals._expiry_notice)


def test_the_weekly_names_its_exact_window_and_what_the_day_score_means():
    from datetime import UTC, datetime, timedelta
    at = datetime(2026, 9, 27, 23, 0, tzinfo=UTC)
    w = weekly_mod.Weekly(at=at, since=at - timedelta(days=7), headline="x")
    window = w.window()
    assert window.startswith("the 7 days from ") and " to " in window
    import inspect
    src = inspect.getsource(weekly_mod.build)
    assert "Average day score" not in src and "of the items" in src
