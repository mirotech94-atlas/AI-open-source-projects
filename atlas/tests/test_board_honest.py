"""Phase 4, an honest board: stale, parked, retired and self-test."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from atlas.kernel import board_state
from atlas.kernel.board_state import Parked

# the overview fixtures live in test_api.py
from tests import test_api

client = test_api.client
signed_in = test_api.signed_in


def _row(prop, name, *, age=timedelta(0), health="ok", interval=300):
    now = datetime.now(UTC)
    return {"property": prop, "check_name": name, "last_run_at": now - age,
            "next_run_at": now, "interval_s": interval, "health": health,
            "detail": "", "observed_at": now - age}


def _serve(memory_backend, rows):
    async def fetch(sql, *args):
        return rows if "FROM checks c" in " ".join(sql.split()) else []

    async def fetchrow(sql, *args):
        return {"n": 0}

    memory_backend.fetch = fetch
    memory_backend.fetchrow = fetchrow


def _tile(client, module):
    body = client.get("/api/overview").json()
    return body, next(m for m in body["modules"] if m["module"] == module)


def _live(monkeypatch, *names):
    from atlas.kernel.registry import REGISTRY
    monkeypatch.setitem(REGISTRY.modules, "cloudco", object())
    for n in names:
        monkeypatch.setitem(REGISTRY.checks, f"cloudco.{n}", object())


# ------------------------------------------------------------- pure rules

def test_committed_parked_list_is_sound():
    assert board_state.validate() == []


def test_validate_catches_a_missing_reason_and_a_duplicate():
    bad = (Parked("a.b", " ", date(2030, 1, 1)), Parked("a.b", "x", date(2030, 1, 1)))
    assert any("no reason" in p for p in board_state.validate(bad))
    assert any("twice" in p for p in board_state.validate(bad))


def test_parked_until_its_date_then_judged_again():
    entry = Parked("a.b", "waiting on a vendor", date(2026, 12, 1))
    assert board_state.parked_entry("a.b", date(2026, 11, 30), (entry,)) is entry
    assert board_state.parked_entry("a.b", date(2026, 12, 1), (entry,)) is None
    assert board_state.parked_entry("a.b", date(2027, 1, 1), (entry,)) is None
    assert board_state.parked_entry("x.y", date(2026, 11, 30), (entry,)) is None


def test_retired_wins_over_parked_and_empty_registry_retires_nothing():
    entry = Parked("a.b", "r", date(2030, 1, 1))
    today = date(2026, 11, 14)
    got = board_state.override_state("a.b", registered=False, today=today,
                                     parked=(entry,))
    assert got["kind"] == "retired"
    got = board_state.override_state("a.b", registered=True, today=today,
                                     parked=(entry,))
    assert got["kind"] == "parked" and "r" in got["label"]
    assert board_state.override_state("a.b", registered=False, today=today,
                                      registry_loaded=False, parked=()) is None


def test_a_stopped_collector_reads_unknown_within_three_intervals():
    from atlas.kernel.freshness import effective_health
    interval = timedelta(minutes=5)
    limit = board_state.reads_unknown_within(interval)
    now = datetime.now(UTC)
    just_before = now - (limit - timedelta(seconds=30))
    after = now - (limit + timedelta(seconds=30))
    assert effective_health("ok", just_before, interval, now) == ("ok", False)
    assert effective_health("ok", after, interval, now) == ("unknown", True)


def test_self_test_counts_and_carries_the_read_time():
    now = datetime.now(UTC)
    fresh = now.isoformat()
    rows = [
        {"health": "ok", "state": None, "observed_at": fresh, "stale": False},
        {"health": "unknown", "state": None, "observed_at": None, "stale": False},
        {"health": "unknown", "state": None, "observed_at": fresh, "stale": True},
        {"health": "unknown", "state": {"kind": "parked"}, "observed_at": fresh,
         "stale": False},
        {"health": "ok", "state": {"kind": "retired"}, "observed_at": fresh,
         "stale": False},
    ]
    st = board_state.self_test(rows, now)
    assert st["checks_run"]["n"] == 2 and st["checks_run"]["of"] == 4
    assert st["unknown"]["n"] == 2
    assert st["parked"]["n"] == 1 and st["retired"]["n"] == 1
    for key in ("checks_run", "unknown", "parked"):
        assert st[key]["read_at"] == now.isoformat()


# --------------------------------------------------------------- registry

def test_voice_is_switched_off_by_the_committed_list(monkeypatch):
    from atlas.kernel.registry import Registry
    monkeypatch.delenv("ATLAS_DISABLED_MODULES", raising=False)
    reg = Registry()
    reg.load_all()
    assert "voice" not in reg.modules
    assert "switched off" in reg.disabled["voice"]
    assert not any(k.startswith("voice.") for k in reg.checks)


# --------------------------------------------------------------- overview

def test_a_retired_row_is_listed_as_retired_never_ok(signed_in, memory_backend,
                                                     monkeypatch):
    _serve(memory_backend, [_row("cloudco", "protection"), _row("cloudco", "snapshots")])
    _live(monkeypatch, "protection")
    body, tile = _tile(signed_in, "cloudco")
    old = next(c for c in tile["checks"] if c["name"] == "snapshots")
    assert old["state"]["kind"] == "retired"
    assert tile["health"] == "ok"
    assert body["self_test"]["retired"]["n"] >= 1


def test_a_gone_module_shows_retired(signed_in, memory_backend, monkeypatch):
    _serve(memory_backend, [_row("a2p", "registration"), _row("cloudco", "protection")])
    _live(monkeypatch, "protection")
    body = signed_in.get("/api/overview").json()
    gone = [m for m in body["modules"] if m["module"] == "a2p"]
    assert gone, "a module the code no longer has must still be shown"
    assert all(c["state"]["kind"] == "retired" for c in gone[0]["checks"])


def test_a_stale_reading_is_unknown_with_last_read_at(signed_in, memory_backend,
                                                      monkeypatch):
    age = board_state.reads_unknown_within(timedelta(seconds=300)) \
        + timedelta(minutes=1)
    _serve(memory_backend, [_row("cloudco", "protection", age=age)])
    _live(monkeypatch, "protection")
    _, tile = _tile(signed_in, "cloudco")
    c = tile["checks"][0]
    assert c["health"] == "unknown"
    assert c["last_read_at"]


def test_a_fresh_reading_stays_ok(signed_in, memory_backend, monkeypatch):
    _serve(memory_backend, [_row("cloudco", "protection")])
    _live(monkeypatch, "protection")
    _, tile = _tile(signed_in, "cloudco")
    assert tile["checks"][0]["health"] == "ok"


def test_a_parked_check_has_its_own_state_and_does_not_colour_the_tile(
        signed_in, memory_backend, monkeypatch):
    entry = Parked("cloudco.protection", "waiting on a vendor", date(2099, 1, 1))
    monkeypatch.setattr(board_state, "PARKED", (entry,))
    _serve(memory_backend, [_row("cloudco", "protection", health="failing"),
                            _row("cloudco", "other")])
    _live(monkeypatch, "protection", "other")
    body, tile = _tile(signed_in, "cloudco")
    p = next(c for c in tile["checks"] if c["name"] == "protection")
    assert p["state"]["kind"] == "parked"
    assert "waiting on a vendor" in p["state"]["label"]
    assert tile["health"] == "ok"
    assert body["self_test"]["parked"]["n"] == 1


def test_a_parking_whose_date_has_come_is_judged_on_its_reading(
        signed_in, memory_backend, monkeypatch):
    entry = Parked("cloudco.protection", "old", date(2020, 1, 1))
    monkeypatch.setattr(board_state, "PARKED", (entry,))
    _serve(memory_backend, [_row("cloudco", "protection", health="failing")])
    _live(monkeypatch, "protection")
    _, tile = _tile(signed_in, "cloudco")
    assert tile["checks"][0]["health"] == "failing"
    assert (tile["checks"][0].get("state") or {}).get("kind") != "parked"


def test_a_registered_check_never_read_is_unknown(signed_in, memory_backend,
                                                  monkeypatch):
    _serve(memory_backend, [_row("cloudco", "protection")])
    _live(monkeypatch, "protection", "newcheck")
    body, tile = _tile(signed_in, "cloudco")
    new = next(c for c in tile["checks"] if c["name"] == "newcheck")
    assert new["health"] == "unknown"
    assert tile["health"] == "unknown"
    st = body["self_test"]
    assert st["checks_run"]["of"] > st["checks_run"]["n"]
    assert st["unknown"]["n"] >= 1
