"""Backups and disk: is ATLAS's memory safe, and is there room to keep working?

the owner, 2026-09-28: automate what can be. Everything ATLAS knows is one
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
from datetime import UTC, datetime, timedelta
from typing import Any

from ...kernel.contracts import Check, Health, Observation, Severity

DATA = os.getenv("ATLAS_DATA_DIR", "data")
STATUS = os.path.join(DATA, "backups", "status.json")
# Written by the Mac's launchd job ops/watchdog/host/disk_status.sh: the real
# data volume's free space and its largest folders. ATLAS runs in a container
# whose own disk_usage reads the VM, not the Mac, so it never measures itself.
DISK_STATUS = os.path.join(DATA, "backups", "disk.json")
# Written by the Mac's launchd job ops/watchdog/host/pull_watchdog_seen.sh: a
# copy of the independent watchdog's own seen.json, plus the Mac's pulled_at.
WATCHDOG_SEEN = os.path.join(DATA, "backups", "watchdog_seen.json")
# Written by ops/restore_drill.sh (weekly, atlas-restore-drill.timer) on
# every drill that ran: the latest drill's verdict only.
DRILL = os.path.join(DATA, "backups", "restore_drill.json")
# A weekly drill, with a day of slack. Older than this, or none on record,
# the backup is not proven restorable: DEGRADED, never OK (plan S05).
DRILL_STALE = timedelta(days=8)
BACKUP_STALE = timedelta(hours=30)          # a nightly job, with slack
DISK_DEGRADED, DISK_FAILING = 0.15, 0.05     # free fraction (secondary, warn only)
# Absolute GiB floors decide (the owner, 2026-11-15): a big disk that is "96 % full"
# can still hold 17 GiB, which is room for a dump, so the fraction alone must
# not fail it. Under the fail floor it FAILS; under the warn floor it WARNS.
# The old fractions stay as a secondary rule that can only raise OK to WARN
# (a 1 TB disk at 4 % free is worth a look), never to FAILING. Set per host with
# ATLAS_DISK_FLOOR_GIB / ATLAS_DISK_WARN_GIB; not tuned to one reading.
DEFAULT_DISK_FLOOR_GIB, DEFAULT_DISK_WARN_GIB = 4.0, 10.0
DISK_FLOOR_GIB = float(os.getenv("ATLAS_DISK_FLOOR_GIB", str(DEFAULT_DISK_FLOOR_GIB)))
DISK_WARN_GIB = float(os.getenv("ATLAS_DISK_WARN_GIB", str(DEFAULT_DISK_WARN_GIB)))
DISK_STATUS_STALE = timedelta(hours=1)       # the host job runs every 15 min
WATCHDOG_STALE = timedelta(seconds=900)      # the watchdog's own heartbeat limit
CLOCK_SKEW = timedelta(minutes=2)            # a time further ahead than this is wrong
GIB = 1024 ** 3


def _read(path: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None, f"{os.path.basename(path)} does not exist yet"
    except (OSError, ValueError) as exc:
        return None, f"{os.path.basename(path)} unreadable: {type(exc).__name__}"
    return (data, None) if isinstance(data, dict) else (None, "not a JSON object")


def _now() -> datetime:
    return datetime.now(UTC)


def _age(data: dict[str, Any], key: str = "at") -> timedelta | None:
    """Age of the ISO time under `key`; None when absent, unparseable or naive
    (a time with no zone is not readable as UTC)."""
    try:
        at = datetime.fromisoformat(str(data[key]))
    except (KeyError, ValueError):
        return None
    try:
        return _now() - at
    except TypeError:
        return None


def _stamp(data: dict[str, Any], key: str = "at") -> str:
    try:
        return datetime.fromisoformat(str(data[key])).astimezone(UTC).strftime(
            "%Y-%m-%d %H:%M UTC")
    except (KeyError, ValueError):
        return "an unreadable time"


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
    """Free space on the Mac's real data volume, from the host job's disk.json.

    ATLAS never measures its own disk (inside the container that is the VM)
    and never deletes anything: it names the largest folders so the owner can.
    The old housekeeping-DEGRADED rule is gone: nothing runs housekeeping on
    the Mac, so it would have pinned this check at DEGRADED for good."""
    data, why = await asyncio.to_thread(_read, DISK_STATUS)
    if data is None:
        return Observation("disk", Health.UNKNOWN,
                           detail=f"no disk status: {why} -- is the host job "
                                  f"ops/watchdog/host/disk_status.sh installed?")
    age = _age(data)
    if age is None:
        return Observation("disk", Health.UNKNOWN, detail="disk.json has no readable time")
    if age > DISK_STATUS_STALE:
        return Observation("disk", Health.UNKNOWN,
                           detail=f"disk.json is stale: read {_stamp(data)} "
                                  f"({age.total_seconds() / 3600:.1f} h ago); the host "
                                  f"job has stopped, so there is no current number")
    free, total = data.get("free_bytes"), data.get("total_bytes")
    if (isinstance(free, bool) or isinstance(total, bool)
            or not isinstance(free, int | float) or not isinstance(total, int | float)
            or free < 0 or total <= 0 or free > total):
        return Observation("disk", Health.UNKNOWN,
                           detail="disk.json has no readable free/total bytes")
    frac = free / total
    top = _top(data)
    detail = (f"{_gib(free)} free of {_gib(total)} ({frac * 100:.0f}% free) on "
              f"{data.get('mount', 'the data volume')}, read {_stamp(data)}")
    free_gib = free / GIB
    if free_gib <= DISK_FLOOR_GIB:
        health = Health.FAILING
    elif free_gib <= DISK_WARN_GIB or frac < DISK_DEGRADED:
        health = Health.DEGRADED
    else:
        health = Health.OK
    if health is not Health.OK:
        detail += (f"; largest: {top}" if top else "; no folder sizes in the status file")
        detail += f"; floor {DISK_FLOOR_GIB:g} GiB. ATLAS deletes nothing: free space by hand"
    elif top:
        detail += f"; largest: {top}"
    return Observation("disk", health, value=round(free_gib, 1), unit="GiB free",
                       detail=detail,
                       evidence={"free_bytes": int(free), "total_bytes": int(total),
                                 "floor_gib": DISK_FLOOR_GIB, "warn_gib": DISK_WARN_GIB,
                                 "read_at": data.get("at")})


def _gib(n: float) -> str:
    return f"{n / GIB:.1f} GiB"


def _top(data: dict[str, Any]) -> str:
    rows = []
    for row in data.get("top") or []:
        size = row.get("bytes") if isinstance(row, dict) else None
        if isinstance(size, int | float) and not isinstance(size, bool):
            rows.append((str(row.get("path", "?")), float(size)))
    rows.sort(key=lambda r: -r[1])
    return ", ".join(f"{p} {_gib(b)}" for p, b in rows[:5])


async def check_watchdog_seen() -> Observation:
    """Is the independent watchdog still reading /healthz?

    The watchdog (ops/watchdog/, on the Spark) writes seen.json on every run;
    the Mac host job copies it here with its own key. Nothing is shared with
    the watchdog and nothing here can speak for it. This check is OK or
    UNKNOWN only: "I cannot see the watchdog" is not "ATLAS is down", but it
    is never "fine" either (invariant 7)."""
    data, why = await asyncio.to_thread(_read, WATCHDOG_SEEN)
    if data is None:
        return Observation("watchdog_seen", Health.UNKNOWN,
                           detail=f"no watchdog record: {why} -- is the host job "
                                  f"pull_watchdog_seen.sh installed, and the watchdog "
                                  f"running on the Spark?")
    pulled = _age(data, "pulled_at")
    seen = _age(data, "at")
    last = _age(data, "last_healthz_ok_at")
    if pulled is None or seen is None:
        return Observation("watchdog_seen", Health.UNKNOWN,
                           detail="watchdog_seen.json has no readable pull or run time")
    if any(a < -CLOCK_SKEW for a in (pulled, seen, last) if a is not None):
        return Observation("watchdog_seen", Health.UNKNOWN,
                           detail="watchdog_seen.json carries a time in the future "
                                  "(clock skew between the Mac and the Spark)")
    if pulled > WATCHDOG_STALE:
        return Observation("watchdog_seen", Health.UNKNOWN,
                           detail=f"the host pull job last copied the record "
                                  f"{int(pulled.total_seconds() // 60)} min ago; the "
                                  f"watchdog may be fine but ATLAS cannot see it")
    if seen > WATCHDOG_STALE:
        return Observation("watchdog_seen", Health.UNKNOWN,
                           detail=f"the watchdog last ran {_stamp(data)} "
                                  f"({int(seen.total_seconds() // 60)} min ago): it "
                                  f"has stopped, so nothing is watching ATLAS")
    if data.get("route_configured") is not True:
        return Observation("watchdog_seen", Health.UNKNOWN,
                           detail="the watchdog runs but has no alert route "
                                  "(webhook not configured): it could not tell the owner")
    if last is None:
        return Observation("watchdog_seen", Health.UNKNOWN,
                           detail="the watchdog has never read /healthz successfully "
                                  "(no readable last_healthz_ok_at)")
    if last > WATCHDOG_STALE:
        return Observation(
            "watchdog_seen", Health.UNKNOWN,
            detail=f"the watchdog last read /healthz at "
                   f"{_stamp(data, 'last_healthz_ok_at')} "
                   f"({int(last.total_seconds() // 60)} min ago); since then its reads "
                   f"were {data.get('healthz_state', 'unknown')}")
    return Observation("watchdog_seen", Health.OK, value=round(last.total_seconds()),
                       unit="s since last read",
                       detail=f"the watchdog read /healthz at "
                              f"{_stamp(data, 'last_healthz_ok_at')} "
                              f"({int(last.total_seconds())} s ago)",
                       evidence={"last_healthz_ok_at": data.get("last_healthz_ok_at")})


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
                  "room left on the Mac's data volume (the host job's reading)",
                  baseline_sigma=None),
            Check("watchdog_seen", check_watchdog_seen, timedelta(minutes=5),
                  Severity.HIGH, "the independent watchdog is still reading /healthz",
                  baseline_sigma=None),
        )

    def actions(self):
        return ()          # the host runs the jobs; ATLAS only reports them

    async def signals(self):
        return ()

    def brief(self) -> str:
        return "Backups: last night's dump and its off-box copy; disk space; watchdog"


MODULE = BackupsModule()
