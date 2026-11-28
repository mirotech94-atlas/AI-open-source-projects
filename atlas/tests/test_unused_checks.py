"""`ATLAS_UNUSED_CHECKS=module.check,...`: checks Miro does not use. The runner
never runs them, the board lists them as "switched off" (never OK, never
counted FAILING / DEGRADED / UNKNOWN), autopilot says module_off and closes
their open tasks (invariant 7: nothing here reads as healthy)."""
from __future__ import annotations

import sys
import types
from datetime import UTC, date, datetime, timedelta

import pytest

from atlas.kernel import board_state
from atlas.modules.qa import autopilot as ap
from tests import test_api
from tests.test_autopilot import IO, NOW, finding

client = test_api.client
signed_in = test_api.signed_in


# ------------------------------------------------------------- the list

def test_the_list_is_read_from_the_env_and_normalised(monkeypatch):
    monkeypatch.setenv("ATLAS_UNUSED_CHECKS",
                       " Calendar.Focus_Invites ,youtube.amsm_stats,,nodot, a.b.c ")
    assert board_state.unused_checks() == frozenset(
        {"calendar.focus_invites", "youtube.amsm_stats"})
    assert board_state.is_unused("CALENDAR.focus_invites")
    assert not board_state.is_unused("calendar.other")


def test_unset_means_nothing_is_unused(monkeypatch):
    monkeypatch.delenv("ATLAS_UNUSED_CHECKS", raising=False)
    assert board_state.unused_checks() == frozenset()


def test_switched_off_state_is_its_own_kind_and_wins_over_parked():
    parked = (board_state.Parked("a.b", "r", date(2030, 1, 1)),)
    today = date(2026, 11, 24)
    got = board_state.override_state("a.b", registered=False, today=today,
                                     parked=parked, unused=True)
    assert got["kind"] == "switched_off" and "switched off" in got["label"]
    # without the flag the old rules are unchanged
    assert board_state.override_state("a.b", registered=True, today=today,
                                      parked=parked)["kind"] == "parked"


def test_self_test_counts_switched_off_apart_from_unknown():
    now = datetime.now(UTC)
    fresh = now.isoformat()
    rows = [
        {"health": "ok", "state": None, "observed_at": fresh, "stale": False},
        {"health": "unknown", "state": {"kind": "switched_off"},
         "observed_at": None, "stale": False},
        {"health": "failing", "state": {"kind": "switched_off"},
         "observed_at": fresh, "stale": False},
    ]
    st = board_state.self_test(rows, now)
    assert st["checks_run"]["n"] == 1 and st["checks_run"]["of"] == 1
    assert st["unknown"]["n"] == 0
    assert st["switched_off"]["n"] == 2 and st["switched_off"]["read_at"] == fresh


# ------------------------------------------------------------- registry

def _stub_module(monkeypatch):
    class Chk:
        def __init__(self, name):
            self.name = name
            self.interval = timedelta(minutes=5)

    class Mod:
        name = "calendar"
        title = "calendar"
        requires_env: tuple = ()

        def checks(self):
            return [Chk("focus_invites"), Chk("sync")]

        def actions(self):
            return []

    fake = types.ModuleType("fake_unused_calendar")
    fake.MODULE = Mod()
    monkeypatch.setitem(sys.modules, "fake_unused_calendar", fake)


def test_an_unused_check_is_never_registered_so_never_run(monkeypatch):
    from atlas.kernel.registry import Registry
    monkeypatch.setenv("ATLAS_UNUSED_CHECKS", "calendar.focus_invites")
    _stub_module(monkeypatch)
    reg = Registry()
    reg.load("fake_unused_calendar", "calendar")
    assert "calendar.focus_invites" not in reg.checks
    assert "calendar.sync" in reg.checks
    assert reg.unused["calendar.focus_invites"].startswith("switched off")
    assert "calendar" in reg.modules          # the module itself stays on


@pytest.mark.asyncio
async def test_the_runner_skips_an_unused_check_even_if_one_slips_in(monkeypatch):
    from atlas.kernel import scheduler
    from atlas.kernel.registry import REGISTRY
    monkeypatch.setenv("ATLAS_UNUSED_CHECKS", "calendar.focus_invites")
    runs: list[str] = []

    class Chk:
        interval = timedelta(minutes=5)
        timeout = timedelta(seconds=5)

        async def run(self):
            runs.append("ran")

    monkeypatch.setattr(REGISTRY, "checks", {"calendar.focus_invites": Chk()})
    monkeypatch.setattr(scheduler, "_next_run", {})
    assert await scheduler.run_once() == []
    assert runs == []


# ------------------------------------------------------------- the board

def _row(prop, name, *, health="ok"):
    now = datetime.now(UTC)
    return {"property": prop, "check_name": name, "last_run_at": now,
            "next_run_at": now, "interval_s": 300, "health": health,
            "detail": "", "observed_at": now}


def _serve(memory_backend, rows):
    async def fetch(sql, *args):
        return rows if "FROM checks c" in " ".join(sql.split()) else []

    async def fetchrow(sql, *args):
        return {"n": 0}

    memory_backend.fetch = fetch
    memory_backend.fetchrow = fetchrow


def test_the_board_lists_an_unused_check_as_switched_off_never_ok(
        signed_in, memory_backend, monkeypatch):
    from atlas.kernel.registry import REGISTRY
    monkeypatch.setenv("ATLAS_UNUSED_CHECKS", "calendar.focus_invites,calendar.ghost")
    monkeypatch.setitem(REGISTRY.modules, "calendar", object())
    monkeypatch.setitem(REGISTRY.checks, "calendar.sync", object())
    monkeypatch.setitem(REGISTRY.unused, "calendar.focus_invites", "switched off")
    monkeypatch.setitem(REGISTRY.unused, "calendar.ghost", "switched off")
    # one has an old failing row, one has no row at all
    _serve(memory_backend, [_row("calendar", "sync"),
                            _row("calendar", "focus_invites", health="failing")])
    body = signed_in.get("/api/overview").json()
    tile = next(m for m in body["modules"] if m["module"] == "calendar")
    by = {c["name"]: c for c in tile["checks"]}
    for name in ("focus_invites", "ghost"):
        c = by[name]
        assert c["state"]["kind"] == "switched_off"
        assert "switched off" in c["state"]["label"]
        assert c["health"] == "unknown"           # never ok, never failing
    assert tile["health"] == "ok"                 # decided by `sync` alone
    st = body["self_test"]
    assert st["switched_off"]["n"] == 2
    every = [c for m in body["modules"] for c in m["checks"]]
    # only the unrelated, really-unread checks are UNKNOWN; ours are not
    assert st["unknown"]["n"] == sum(
        1 for c in every if c["health"] == "unknown" and c["state"] is None)
    assert st["checks_run"]["of"] == len(every) - 2


def test_a_module_whose_every_check_is_unused_reads_switched_off(
        signed_in, memory_backend, monkeypatch):
    from atlas.kernel.registry import REGISTRY
    monkeypatch.setenv("ATLAS_UNUSED_CHECKS", "calendar.focus_invites")
    monkeypatch.setitem(REGISTRY.modules, "calendar", object())
    monkeypatch.setitem(REGISTRY.unused, "calendar.focus_invites", "switched off")
    _serve(memory_backend, [_row("calendar", "focus_invites")])
    body = signed_in.get("/api/overview").json()
    tile = next(m for m in body["modules"] if m["module"] == "calendar")
    assert "switched off" in (tile["setup"] or "")
    assert tile["health"] == "unknown"


# ------------------------------------------------------------- autopilot

class UnusedIO(IO):
    def __init__(self, findings, unused, **kw):
        super().__init__(findings, **kw)
        self.unused = set(unused)

    def unused_checks(self):
        return self.unused


@pytest.mark.asyncio
async def test_autopilot_marks_unused_checks_module_off_with_no_task():
    io = UnusedIO([finding("calendar.focus_invites", "unknown", stale=True),
                   finding("youtube.amsm_stats", "failing",
                           detail="token expired 401"),
                   finding("research.scan_freshness", "degraded", stale=True)],
                  {"calendar.focus_invites", "youtube.amsm_stats"})
    rep = await ap.run_cycle(io, NOW)
    assert rep.off == 2
    assert not any("amsm" in str(t) or "focus" in str(t) for t in io.tasks)
    rows = io.recorded[0][0]
    off = [r for r in rows if r["outcome"] == "module_off"]
    assert len(off) == 1 and off[0]["cause_class"] == "module_off"
    assert "calendar.focus_invites" in off[0]["detail"]
    assert "youtube.amsm_stats" in off[0]["detail"]
    assert "ATLAS_UNUSED_CHECKS" in off[0]["cause"]


@pytest.mark.asyncio
async def test_autopilot_closes_open_tasks_of_unused_checks_even_with_no_finding():
    io = UnusedIO([], {"content.shorts_lane"})
    io.open = [(1, "content.shorts_lane:real_fault"),
               (2, "content.other:real_fault"),
               (3, "agent:qa:real_fault")]
    await ap.run_cycle(io, NOW)
    assert io.closed == [1]
