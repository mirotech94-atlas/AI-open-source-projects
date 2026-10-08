"""Backups and disk: is ATLAS's memory safe, and is there room to keep working?

Miro, 2026-09-28: automate what can be. Everything ATLAS knows is one
Postgres database on one host, and until today nothing copied it anywhere.
The host now dumps it nightly and copies each dump to the GitLab VM
(`bootstrap/backup/atlas-backup.sh`), and a weekly job keeps the disk from
filling (`atlas-housekeeping.sh`). Both write a small status file into the
data volume; these checks read them. The API never runs the jobs -- they are
the host's -- it only says whether they happened.

Invariant 7 throughout: a status file that is missing or unreadable is
UNKNOWN, never OK; a night with no dump, or a dump that never left the box,
is FAILING -- a backup that only exists on the machine it protects is not a
backup.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from datetime import UTC, datetime, timedelta
from typing import Any

from ...kernel.contracts import Check, Health, Observation, Severity

DATA = os.getenv("ATLAS_DATA_DIR", "data")
STATUS = os.path.join(DATA, "backups", "status.json")
HOUSEKEEPING = os.path.join(DATA, "backups", "housekeeping.json")
# Written by ops/restore_drill.sh (weekly, atlas-restore-drill.timer) on
# every drill that ran: the latest drill's verdict only.
DRILL = os.path.join(DATA, "backups", "restore_drill.json")
# A weekly drill, with a day of slack. Older than this, or none on record,
# the backup is not proven restorable: DEGRADED, never OK (plan S05).
DRILL_STALE = timedelta(days=8)
BACKUP_STALE = timedelta(hours=30)          # a nightly job, with slack
HOUSEKEEPING_STALE = timedelta(days=8)       # a weekly job, with slack
DISK_DEGRADED, DISK_FAILING = 0.15, 0.05     # free fraction


def _read(path: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None, f"{os.path.basename(path)} does not exist yet"
    except (OSError, ValueError) as exc:
        return None, f"{os.path.basename(path)} unreadable: {type(exc).__name__}"
    return (data, None) if isinstance(data, dict) else (None, "not a JSON object")


def _age(data: dict[str, Any]) -> timedelta | None:
    try:
        at = datetime.fromisoformat(str(data["at"]))
    except (KeyError, ValueError):
        return None
    try:
        return datetime.now(UTC) - at
    except TypeError:                   # a naive time: not readable as UTC
        return None


async def check_backup() -> Observation:
    data, why = await asyncio.to_thread(_read, STATUS)
    if data is None:
        return Observation("backup", Health.UNKNOWN,
                           detail=f"no backup status: {why} -- is atlas-backup.timer "
                                  f"installed on the host?")
    age = _age(data)
    if age is None:
        return Observation("backup", Health.UNKNOWN, detail="status has no readable time")
    hours = age.total_seconds() / 3600
    size = int(data.get("bytes") or 0)
    detail = (f"last run {hours:.0f} h ago: {data.get('detail', '')}; "
              f"{data.get('file', '?')} ({size / 1e6:.1f} MB)")
    evidence = {"hours": round(hours, 1), "dump_ok": data.get("dump_ok"),
                "offsite_ok": data.get("offsite_ok"), "bytes": size}
    if age > BACKUP_STALE:
        return Observation("backup", Health.FAILING, value=hours, unit="h",
                           detail=f"no backup for {hours:.0f} h -- {detail}",
                           evidence=evidence)
    if not data.get("dump_ok") or size == 0:
        return Observation("backup", Health.FAILING, value=hours, unit="h",
                           detail=detail, evidence=evidence)
    if not data.get("offsite_ok"):
        return Observation("backup", Health.FAILING, value=hours, unit="h",
                           detail=f"{detail} -- a copy that never left the box "
                                  f"is not a backup", evidence=evidence)
    # Plan S05: a backup that exists but cannot be restored is not ready. The
    # latest restore drill's verdict outranks a good night: a dump that
    # failed its drill stays FAILING until a drill passes (the fault is as
    # likely in how dumps are made as in that one file). No drill on record
    # is not a failure, but it is not proven either: DEGRADED, as is a
    # passing drill older than DRILL_STALE (docs/acceptance.md, S05).
    drill, drill_why = await asyncio.to_thread(_read, DRILL)
    if drill is None:
        if drill_why and "does not exist" not in drill_why:
            return Observation("backup", Health.UNKNOWN, value=hours, unit="h",
                               detail=f"{detail}; restore drill result {drill_why}",
                               evidence=evidence)
        evidence["restore_drill_ok"] = None
        return Observation(
            "backup", Health.DEGRADED, value=hours, unit="h",
            detail=(f"{detail} -- no restore drill on record, so this backup "
                    f"is not proven restorable; run ops/restore_drill.sh"),
            evidence=evidence)
    evidence["restore_drill_ok"] = drill.get("ok")
    if drill.get("ok") is not True:
        return Observation(
            "backup", Health.FAILING, value=hours, unit="h",
            detail=(f"{detail} -- the latest restore drill FAILED "
                    f"({drill.get('dump', '?')}, {drill.get('at', '?')}): a "
                    f"backup that cannot be restored is not a backup; fix it "
                    f"and run ops/restore_drill.sh again"), evidence=evidence)
    drill_age = _age(drill)
    if drill_age is None or drill_age > DRILL_STALE:
        when = ("at an unreadable time" if drill_age is None
                else f"{drill_age.days} days ago")
        evidence["restore_drill_days"] = (None if drill_age is None
                                          else drill_age.days)
        return Observation(
            "backup", Health.DEGRADED, value=hours, unit="h",
            detail=(f"{detail} -- the last restore drill passed {when}; the "
                    f"weekly drill has not run since, so restores are not "
                    f"proven now"), evidence=evidence)
    return Observation("backup", Health.OK, value=hours, unit="h", detail=detail,
                       evidence=evidence)


async def check_disk() -> Observation:
    try:
        usage = await asyncio.to_thread(shutil.disk_usage, DATA)
    except OSError as exc:
        return Observation("disk", Health.UNKNOWN, detail=f"disk unreadable: {exc}")
    free = usage.free / usage.total
    house, _ = await asyncio.to_thread(_read, HOUSEKEEPING)
    age = _age(house) if house else None
    note = ("housekeeping never ran" if house is None else
            f"housekeeping {age.days} d ago freed "
            f"{int(house.get('freed_bytes') or 0) / 1e9:.1f} GB" if age else
            "housekeeping time unreadable")
    detail = f"{usage.free / 1e9:.1f} GB free of {usage.total / 1e9:.0f} GB; {note}"
    health = (Health.FAILING if free < DISK_FAILING else
              Health.DEGRADED if free < DISK_DEGRADED else Health.OK)
    if health is Health.OK and (house is None or age is None or age > HOUSEKEEPING_STALE):
        health = Health.DEGRADED
    return Observation("disk", health, value=round(free * 100, 1), unit="% free",
                       detail=detail, evidence={"free_bytes": usage.free})


class BackupsModule:
    name = "backups"
    title = "Backups and disk"
    requires_env = ()
    optional_env = ()

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        return (
            Check("backup", check_backup, timedelta(hours=1), Severity.HIGH,
                  "last night's database backup, copied off the box",
                  baseline_sigma=None),
            Check("disk", check_disk, timedelta(minutes=30), Severity.HIGH,
                  "room left on the host's disk", baseline_sigma=None),
        )

    def actions(self):
        return ()          # the host runs the jobs; ATLAS only reports them

    async def signals(self):
        return ()

    def brief(self) -> str:
        return "Backups: last night's dump and its off-box copy; disk space"


MODULE = BackupsModule()
