"""Backups and disk (Miro, 2026-09-28): a backup that never happened, or
never left the box, is FAILING; one ATLAS cannot read is UNKNOWN."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from atlas.kernel.contracts import Health
from atlas.modules import backups


def _write(tmp_path, monkeypatch, *, drill_hours: float | None = 24, **fields):
    """status.json as given, and a passing restore drill `drill_hours` ago
    (None: no drill on record)."""
    d = tmp_path / "backups"
    d.mkdir(exist_ok=True)
    (d / "status.json").write_text(json.dumps(fields))
    if drill_hours is not None:
        (d / "restore_drill.json").write_text(json.dumps({
            "at": _at(drill_hours), "ok": True, "dump": "atlas-x.dump", "exit": 0}))
    monkeypatch.setattr(backups, "STATUS", str(d / "status.json"))
    monkeypatch.setattr(backups, "DRILL", str(d / "restore_drill.json"))


def _at(hours):
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat()


async def test_no_status_is_unknown_never_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(backups, "STATUS", str(tmp_path / "nope.json"))
    assert (await backups.check_backup()).health is Health.UNKNOWN


async def test_a_fresh_dump_copied_off_the_box_is_ok(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, at=_at(3), dump_ok=True, offsite_ok=True,
           bytes=9_000_000, file="atlas-x.dump", detail="dumped and copied")
    assert (await backups.check_backup()).health is Health.OK


async def test_a_good_dump_with_no_drill_on_record_is_degraded(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, drill_hours=None, at=_at(3), dump_ok=True,
           offsite_ok=True, bytes=9_000_000, file="atlas-x.dump", detail="ok")
    obs = await backups.check_backup()
    assert obs.health is Health.DEGRADED and "no restore drill" in obs.detail


async def test_a_drill_older_than_a_week_and_a_day_is_degraded(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, drill_hours=9 * 24, at=_at(3), dump_ok=True,
           offsite_ok=True, bytes=9_000_000, file="atlas-x.dump", detail="ok")
    obs = await backups.check_backup()
    assert obs.health is Health.DEGRADED and "9 days ago" in obs.detail


async def test_a_drill_with_a_naive_time_is_not_green(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, drill_hours=None, at=_at(3), dump_ok=True,
           offsite_ok=True, bytes=9_000_000, file="atlas-x.dump", detail="ok")
    (tmp_path / "backups" / "restore_drill.json").write_text(json.dumps(
        {"at": "2026-09-28T20:00:00", "ok": True, "dump": "x", "exit": 0}))
    assert (await backups.check_backup()).health is Health.DEGRADED


async def test_a_drill_seven_days_old_is_still_ok(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, drill_hours=7 * 24, at=_at(3), dump_ok=True,
           offsite_ok=True, bytes=9_000_000, file="atlas-x.dump", detail="ok")
    assert (await backups.check_backup()).health is Health.OK


async def test_a_dump_that_never_left_the_box_is_failing(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, at=_at(3), dump_ok=True, offsite_ok=False,
           bytes=9_000_000, file="atlas-x.dump", detail="copy failed")
    obs = await backups.check_backup()
    assert obs.health is Health.FAILING and "not a backup" in obs.detail


async def test_no_backup_for_a_day_and_a_half_is_failing(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, at=_at(40), dump_ok=True, offsite_ok=True,
           bytes=9_000_000, file="atlas-x.dump", detail="ok")
    assert (await backups.check_backup()).health is Health.FAILING


async def test_disk_without_housekeeping_is_degraded(tmp_path, monkeypatch):
    monkeypatch.setattr(backups, "DATA", str(tmp_path))
    monkeypatch.setattr(backups, "HOUSEKEEPING", str(tmp_path / "none.json"))
    obs = await backups.check_disk()
    assert obs.health in (Health.DEGRADED, Health.FAILING)
    assert "housekeeping never ran" in obs.detail
