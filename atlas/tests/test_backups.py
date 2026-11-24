"""Backups and disk (the owner, 2026-09-28): a backup that never happened, or
never left the box, is FAILING; one ATLAS cannot read is UNKNOWN."""
from __future__ import annotations

import json
import pathlib
import subprocess
from datetime import UTC, datetime, timedelta

import pytest

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


GIB = 1024 ** 3


def _disk(tmp_path, monkeypatch, *, free_gib: float, total_gib: float = 100,
          age_s: float = 60, top=None, **extra):
    """A host disk.json (written by ops/watchdog/host/disk_status.sh)."""
    d = tmp_path / "backups"
    d.mkdir(exist_ok=True)
    when = datetime.now(UTC) - timedelta(seconds=age_s)
    data = {"at": when.isoformat(), "path": "/Users/x/atlas-agency/data",
            "mount": "/System/Volumes/Data", "total_bytes": int(total_gib * GIB),
            "free_bytes": int(free_gib * GIB),
            "top": top if top is not None else [
                {"path": "/Users/x/atlas-state/backups", "bytes": 5 * GIB},
                {"path": "/Users/x/atlas-agency/data/app", "bytes": 3 * GIB}]}
    data.update(extra)
    (d / "disk.json").write_text(json.dumps(data))
    monkeypatch.setattr(backups, "DISK_STATUS", str(d / "disk.json"))
    monkeypatch.setattr(backups, "DISK_FLOOR_GIB", 8.0)
    monkeypatch.setattr(backups, "DISK_WARN_GIB", 20.0)


async def test_disk_without_a_host_status_file_is_unknown_never_ok(tmp_path, monkeypatch):
    """Was DEGRADED via housekeeping. The container's own disk_usage reads the
    VM, not the Mac, so there is no fallback: no host file, no number."""
    monkeypatch.setattr(backups, "DISK_STATUS", str(tmp_path / "disk.json"))
    obs = await backups.check_disk()
    assert obs.health is Health.UNKNOWN and "disk.json" in obs.detail


async def test_disk_plenty_free_is_ok_and_carries_its_read_time(tmp_path, monkeypatch):
    _disk(tmp_path, monkeypatch, free_gib=60)
    obs = await backups.check_disk()
    assert obs.health is Health.OK
    assert "read " in obs.detail and "UTC" in obs.detail     # invariant 6


async def test_disk_at_the_absolute_floor_is_failing_and_names_folders(
        tmp_path, monkeypatch):
    """57 % free by fraction, but 8 GiB left: the GiB floor alone decides."""
    _disk(tmp_path, monkeypatch, free_gib=8, total_gib=14)
    obs = await backups.check_disk()
    assert obs.health is Health.FAILING
    assert "/Users/x/atlas-state/backups" in obs.detail
    assert "5.0 GiB" in obs.detail
    assert obs.evidence["floor_gib"] == 8.0


async def test_disk_just_above_the_floor_is_degraded_not_failing(tmp_path, monkeypatch):
    _disk(tmp_path, monkeypatch, free_gib=12, total_gib=30)
    obs = await backups.check_disk()
    assert obs.health is Health.DEGRADED and "12.0 GiB free" in obs.detail


async def test_disk_fraction_is_secondary_it_warns_but_never_fails(tmp_path, monkeypatch):
    """the owner 2026-11-15: a 96 %-full disk with ~17 GiB free reads WARN, not FAILING."""
    _disk(tmp_path, monkeypatch, free_gib=40, total_gib=1000)     # 4 % free
    monkeypatch.setattr(backups, "DISK_FLOOR_GIB", 1.0)
    monkeypatch.setattr(backups, "DISK_WARN_GIB", 2.0)
    assert (await backups.check_disk()).health is Health.DEGRADED


async def test_disk_96_percent_full_with_17_gib_free_is_warn_with_default_floors(
        tmp_path, monkeypatch):
    _disk(tmp_path, monkeypatch, free_gib=17, total_gib=425)      # 4 % free
    monkeypatch.setattr(backups, "DISK_FLOOR_GIB", backups.DEFAULT_DISK_FLOOR_GIB)
    monkeypatch.setattr(backups, "DISK_WARN_GIB", backups.DEFAULT_DISK_WARN_GIB)
    obs = await backups.check_disk()
    assert obs.health is Health.DEGRADED and "17.0 GiB free" in obs.detail


async def test_disk_default_floors_are_4_and_10_gib(tmp_path, monkeypatch):
    assert (backups.DEFAULT_DISK_FLOOR_GIB, backups.DEFAULT_DISK_WARN_GIB) == (4.0, 10.0)
    monkeypatch.setattr(backups, "DISK_FLOOR_GIB", 4.0)
    monkeypatch.setattr(backups, "DISK_WARN_GIB", 10.0)
    _disk(tmp_path, monkeypatch, free_gib=3.9, total_gib=500)
    monkeypatch.setattr(backups, "DISK_FLOOR_GIB", 4.0)
    monkeypatch.setattr(backups, "DISK_WARN_GIB", 10.0)
    assert (await backups.check_disk()).health is Health.FAILING
    _disk(tmp_path, monkeypatch, free_gib=9, total_gib=500)
    monkeypatch.setattr(backups, "DISK_FLOOR_GIB", 4.0)
    monkeypatch.setattr(backups, "DISK_WARN_GIB", 10.0)
    assert (await backups.check_disk()).health is Health.DEGRADED


async def test_disk_failing_with_no_folder_list_says_so(tmp_path, monkeypatch):
    _disk(tmp_path, monkeypatch, free_gib=2, top=[])
    obs = await backups.check_disk()
    assert obs.health is Health.FAILING and "no folder sizes" in obs.detail


async def test_disk_status_older_than_an_hour_is_unknown(tmp_path, monkeypatch):
    _disk(tmp_path, monkeypatch, free_gib=2, age_s=3601)       # even a bad number
    obs = await backups.check_disk()
    assert obs.health is Health.UNKNOWN and "stale" in obs.detail


@pytest.mark.parametrize("patch", [
    {"free_bytes": None}, {"total_bytes": 0}, {"free_bytes": -1},
    {"at": "2026-11-14T10:00:00"}, {"free_bytes": "lots"}])
async def test_disk_unreadable_fields_are_unknown(tmp_path, monkeypatch, patch):
    _disk(tmp_path, monkeypatch, free_gib=50, **patch)
    assert (await backups.check_disk()).health is Health.UNKNOWN


async def test_disk_check_never_deletes_anything(tmp_path, monkeypatch):
    """ATLAS only reports (invariant 1): the folder it names is untouched."""
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "f").write_text("x")
    _disk(tmp_path, monkeypatch, free_gib=1, top=[{"path": str(victim), "bytes": 1}])
    assert (await backups.check_disk()).health is Health.FAILING
    assert (victim / "f").exists()
    assert backups.MODULE.actions() == ()


# ---------------------------------------------- watchdog_seen (reverse heartbeat)

def _seen(tmp_path, monkeypatch, *, pulled_s=60, at_s=120, ok_s=120,
          route=True, **extra):
    """watchdog_seen.json as the host pull job writes it: the Spark's seen.json
    plus the Mac's own pulled_at."""
    d = tmp_path / "backups"
    d.mkdir(exist_ok=True)
    now = datetime.now(UTC)

    def t(secs):
        return None if secs is None else (now - timedelta(seconds=secs)).isoformat()
    data = {"pulled_at": t(pulled_s), "at": t(at_s), "last_healthz_ok_at": t(ok_s),
            "healthz_state": "OK", "route_configured": route, "label": "spark"}
    data.update(extra)
    (d / "watchdog_seen.json").write_text(json.dumps(data))
    monkeypatch.setattr(backups, "WATCHDOG_SEEN", str(d / "watchdog_seen.json"))


async def test_watchdog_seen_missing_file_is_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(backups, "WATCHDOG_SEEN", str(tmp_path / "watchdog_seen.json"))
    obs = await backups.check_watchdog_seen()
    assert obs.health is Health.UNKNOWN and "watchdog_seen.json" in obs.detail


async def test_watchdog_seen_unreadable_file_is_unknown(tmp_path, monkeypatch):
    _seen(tmp_path, monkeypatch)
    (tmp_path / "backups" / "watchdog_seen.json").write_text("{not json")
    assert (await backups.check_watchdog_seen()).health is Health.UNKNOWN


async def test_watchdog_seen_fresh_read_is_ok(tmp_path, monkeypatch):
    _seen(tmp_path, monkeypatch)
    obs = await backups.check_watchdog_seen()
    assert obs.health is Health.OK and "UTC" in obs.detail


@pytest.mark.parametrize(("secs", "health"), [(900, Health.OK), (901, Health.UNKNOWN)])
async def test_watchdog_seen_limit_is_15_minutes(tmp_path, monkeypatch, secs, health):
    """900 s old is still fresh, 901 s is UNKNOWN, on a frozen clock."""
    frozen = datetime.now(UTC)
    monkeypatch.setattr(backups, "_now", lambda: frozen)
    d = tmp_path / "backups"
    d.mkdir()
    (d / "watchdog_seen.json").write_text(json.dumps({
        "pulled_at": frozen.isoformat(), "at": frozen.isoformat(),
        "last_healthz_ok_at": (frozen - timedelta(seconds=secs)).isoformat(),
        "healthz_state": "OK", "route_configured": True}))
    monkeypatch.setattr(backups, "WATCHDOG_SEEN", str(d / "watchdog_seen.json"))
    assert (await backups.check_watchdog_seen()).health is health


async def test_watchdog_seen_last_read_old_names_the_age(tmp_path, monkeypatch):
    _seen(tmp_path, monkeypatch, ok_s=4000, at_s=100, healthz_state="FAIL")
    obs = await backups.check_watchdog_seen()
    assert obs.health is Health.UNKNOWN and "66 min" in obs.detail


async def test_watchdog_seen_pull_job_dead_is_unknown(tmp_path, monkeypatch):
    """A fresh-looking file the Mac stopped refreshing proves nothing."""
    _seen(tmp_path, monkeypatch, pulled_s=2000)
    obs = await backups.check_watchdog_seen()
    assert obs.health is Health.UNKNOWN and "pull" in obs.detail


async def test_watchdog_seen_watchdog_itself_silent_is_unknown(tmp_path, monkeypatch):
    _seen(tmp_path, monkeypatch, at_s=2000, ok_s=2000)
    assert (await backups.check_watchdog_seen()).health is Health.UNKNOWN


async def test_watchdog_seen_with_no_route_is_unknown_never_ok(tmp_path, monkeypatch):
    """The watchdog reads /healthz but cannot tell the owner: that is not seeing."""
    _seen(tmp_path, monkeypatch, route=False)
    obs = await backups.check_watchdog_seen()
    assert obs.health is Health.UNKNOWN and "no alert route" in obs.detail


@pytest.mark.parametrize("patch", [
    {"last_healthz_ok_at": None}, {"last_healthz_ok_at": "2026-11-14T10:00:00"},
    {"last_healthz_ok_at": "garbage"}, {"pulled_at": None}, {"route_configured": "yes"},
    {"last_healthz_ok_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}])
async def test_watchdog_seen_odd_fields_are_unknown(tmp_path, monkeypatch, patch):
    _seen(tmp_path, monkeypatch, **patch)
    assert (await backups.check_watchdog_seen()).health is Health.UNKNOWN


def test_the_module_registers_both_checks_as_high():
    from atlas.kernel.contracts import Severity
    got = {c.name: c for c in backups.MODULE.checks()}
    assert got["watchdog_seen"].severity_on_fail is Severity.HIGH
    assert got["disk"].severity_on_fail is Severity.HIGH


# ------------------------------------------------ the two Mac host jobs (fakes)

_HOST = pathlib.Path(__file__).resolve().parent.parent / "ops" / "watchdog" / "host"


def _fake(bindir, name, body):
    p = bindir / name
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(0o755)


def _host_run(script, tmp_path, bindir, **env):
    full = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(tmp_path),
            "ATLAS_COMPOSE_DIR": str(tmp_path / "wt"), **env}
    return subprocess.run(["/bin/bash", str(_HOST / script)], check=False, env=full,
                          capture_output=True, text=True, timeout=60)


async def test_disk_status_job_writes_the_file_the_check_reads(tmp_path, monkeypatch):
    wt = tmp_path / "wt"
    (wt / "data").mkdir(parents=True)
    roots = tmp_path / "roots"
    roots.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    # 100 GiB total, 3 GiB free (KiB blocks): below the 4 GiB floor.
    _fake(bindir, "df", 'echo "Filesystem 1024-blocks Used Available Capacity Mounted"\n'
                        'echo "/dev/x 104857600 101711872 3145728 97% /System/Volumes/Data"\n')
    _fake(bindir, "du", f'echo "5242880 {roots}/big"\necho "100 {roots}/small"\n'
                        f'echo "5243000 {roots}"\n')
    r = _host_run("disk_status.sh", tmp_path, bindir, ATLAS_DISK_ROOTS=str(roots))
    assert r.returncode == 0, r.stderr
    out = wt / "data" / "app" / "backups" / "disk.json"
    data = json.loads(out.read_text())
    assert data["free_bytes"] == 3 * 1024**3 and data["total_bytes"] == 100 * 1024**3
    assert data["top"][0]["path"].endswith("/big")
    monkeypatch.setattr(backups, "DISK_STATUS", str(out))
    obs = await backups.check_disk()
    assert obs.health is Health.FAILING and "big" in obs.detail


def test_disk_status_job_writes_nothing_when_df_is_unreadable(tmp_path):
    (tmp_path / "wt" / "data").mkdir(parents=True)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _fake(bindir, "df", "exit 1\n")
    r = _host_run("disk_status.sh", tmp_path, bindir)
    assert r.returncode == 1
    assert not (tmp_path / "wt" / "data" / "app" / "backups" / "disk.json").exists()


def _seen_job(tmp_path, remote_out, code=0):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    _fake(bindir, "ssh", f"cat <<'EOF'\n{remote_out}\nEOF\nexit {code}\n")
    return bindir


def test_pull_job_adds_pulled_at_and_keeps_only_known_fields(tmp_path):
    remote = json.dumps({"at": "2026-11-14T10:00:00+00:00",
                         "last_healthz_ok_at": "2026-11-14T10:00:00+00:00",
                         "healthz_state": "OK", "route_configured": True,
                         "label": "spark", "extra": "x"})
    r = _host_run("pull_watchdog_seen.sh", tmp_path, _seen_job(tmp_path, remote))
    assert r.returncode == 0, r.stderr
    got = json.loads((tmp_path / "wt/data/app/backups/watchdog_seen.json").read_text())
    assert got["healthz_state"] == "OK" and "pulled_at" in got and "extra" not in got


def test_pull_job_failure_leaves_the_old_record_untouched(tmp_path):
    out = tmp_path / "wt/data/app/backups"
    out.mkdir(parents=True)
    (out / "watchdog_seen.json").write_text('{"pulled_at": "old"}')
    r = _host_run("pull_watchdog_seen.sh", tmp_path, _seen_job(tmp_path, "", code=255))
    assert r.returncode == 1
    assert (out / "watchdog_seen.json").read_text() == '{"pulled_at": "old"}'


def test_pull_job_rejects_non_json(tmp_path):
    out = tmp_path / "wt/data/app/backups"
    out.mkdir(parents=True)
    (out / "watchdog_seen.json").write_text('{"pulled_at": "old"}')
    r = _host_run("pull_watchdog_seen.sh", tmp_path, _seen_job(tmp_path, "not json"))
    assert r.returncode != 0
    assert (out / "watchdog_seen.json").read_text() == '{"pulled_at": "old"}'


# ------------------------------------------------ restore drill --into refusals

_DRILL = pathlib.Path(__file__).resolve().parent.parent / "ops" / "restore_drill.sh"


@pytest.mark.parametrize("into,db,why", [
    ("atlas-agency-postgres-1", "restore_drill_x", "live ATLAS container"),
    ("atlas-postgres-1", "restore_drill_x", "live ATLAS container"),
    ("some-other-pg", "restore_drill_x", "not allowed"),
    ("atlas-test-pg-improve", "atlas", "restore_drill_"),
    ("atlas-test-pg-improve", "restore_drill_X;drop", "restore_drill_"),
])
def test_restore_drill_into_refuses_live_or_unnamed_targets(tmp_path, into, db, why):
    """--into never reaches a live container or a database it did not name; the
    refusal happens before anything is started (exit 2)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    # Any docker call only records itself: refusal must come first.
    calls = tmp_path / "docker.calls"
    _fake(bindir, "docker", f'echo "$*" >> "{calls}"\n[ "$1" = info ] && exit 0\nexit 1\n')
    r = subprocess.run(
        ["/bin/bash", str(_DRILL), "--into", into, "--db", db, "--dump", "/nonexistent"],
        check=False, capture_output=True, text=True, timeout=30,
        env={"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(tmp_path)})
    assert r.returncode == 2 and "REFUSED" in r.stdout and why in r.stdout
    assert "exec" not in (calls.read_text() if calls.exists() else "")


# ------------------------------------------------ ops/offbox host scripts (fakes)

_OFFBOX = pathlib.Path(__file__).resolve().parent.parent / "ops" / "offbox"


def _offbox_box(tmp_path, bad_sha=False):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    remote = tmp_path / "remote"
    remote.mkdir()
    # rsync SRC HOST:DIR/[NAME] -> a local copy; ssh HOST CMD -> run CMD here.
    _fake(bindir, "rsync", 'for a; do last_prev=$last; last=$a; done\n'
                           'src=$last_prev; dst=${last#*:}\ncp "$src" "$dst"\n')
    if bad_sha:
        _fake(bindir, "ssh", 'echo "deadbeef  x"\n')
    else:
        _fake(bindir, "ssh", 'for a; do cmd=$a; done\nsh -c "$cmd"\n')
    return bindir, remote


def _offbox_run(script, tmp_path, bindir, remote, **env):
    full = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(tmp_path),
            "ATLAS_COMPOSE_DIR": str(tmp_path / "wt"),
            "ATLAS_STATE_DIR": str(tmp_path / "state"),
            "ATLAS_OFFBOX_TARGET": f"box:{remote}/", **env}
    return subprocess.run(["/bin/bash", str(_OFFBOX / script)], check=False, env=full,
                          capture_output=True, text=True, timeout=60)


def test_offbox_dump_copy_needs_a_target(tmp_path):
    bindir, remote = _offbox_box(tmp_path)
    r = _offbox_run("copy_dump.sh", tmp_path, bindir, remote, ATLAS_OFFBOX_TARGET="")
    assert r.returncode == 2


def test_offbox_dump_copy_sends_only_the_encrypted_file_and_verifies(tmp_path):
    bindir, remote = _offbox_box(tmp_path)
    d = tmp_path / "state" / "backups"
    d.mkdir(parents=True)
    (d / "atlas-20261114-0300.dump").write_text("plain dump")
    (d / "atlas-20261114-0300.dump.age").write_text("ciphertext")
    r = _offbox_run("copy_dump.sh", tmp_path, bindir, remote)
    assert r.returncode == 0, r.stderr
    assert [p.name for p in remote.iterdir()] == ["atlas-20261114-0300.dump.age"]
    rec = json.loads((tmp_path / "wt/data/app/backups/offbox.json").read_text())
    assert rec["ok"] is True and rec["target_host"] == "box"


def test_offbox_dump_copy_checksum_mismatch_is_not_ok(tmp_path):
    bindir, remote = _offbox_box(tmp_path, bad_sha=True)
    d = tmp_path / "state" / "backups"
    d.mkdir(parents=True)
    (d / "atlas-20261114-0300.dump.age").write_text("ciphertext")
    r = _offbox_run("copy_dump.sh", tmp_path, bindir, remote)
    assert r.returncode == 1
    rec = json.loads((tmp_path / "wt/data/app/backups/offbox.json").read_text())
    assert rec["ok"] is False


def test_offbox_dump_copy_with_no_encrypted_dump_is_not_ok(tmp_path):
    bindir, remote = _offbox_box(tmp_path)
    (tmp_path / "state" / "backups").mkdir(parents=True)
    r = _offbox_run("copy_dump.sh", tmp_path, bindir, remote)
    assert r.returncode == 1
    rec = json.loads((tmp_path / "wt/data/app/backups/offbox.json").read_text())
    assert rec["ok"] is False


def test_offbox_branch_tip_bundle_is_sent_and_verified(tmp_path):
    bindir, remote = _offbox_box(tmp_path)
    repo = tmp_path / "wt"
    repo.mkdir()
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.test"]
    subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
    (repo / "a.txt").write_text("x")
    subprocess.run([*git, "add", "a.txt"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "one"], check=True)
    r = _offbox_run("copy_branch_tip.sh", tmp_path, bindir, remote)
    assert r.returncode == 0, r.stderr
    sent = [p.name for p in remote.iterdir()]
    assert len(sent) == 1 and sent[0].endswith(".bundle")
    rec = json.loads((repo / "data/app/backups/offbox_branches.json").read_text())
    assert rec["ok"] is True


def test_watchdog_tunnel_is_loopback_only_and_ends_in_ssh(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _fake(bindir, "ssh", f'echo "$@" > {tmp_path}/args\n')
    r = _host_run("watchdog_tunnel.sh", tmp_path, bindir)
    assert r.returncode == 0, r.stderr
    args = (tmp_path / "args").read_text()
    assert "-R 127.0.0.1:8080:127.0.0.1:18081" in args
    assert "ExitOnForwardFailure=yes" in args and args.split()[-1] == "spark"
    assert "0.0.0.0" not in args and "*:" not in args


def test_healthz_proxy_forwards_only_get_healthz():
    import http.client
    import importlib.util
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    seen = []

    class Up(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", "11")
            self.end_headers()
            self.wfile.write(b'{"ok":true} ')

        log_message = lambda *a: None  # noqa: E731

    up = HTTPServer(("127.0.0.1", 0), Up)
    spec = importlib.util.spec_from_file_location("hp", _HOST / "healthz_proxy.py")
    hp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hp)
    hp.UPSTREAM = f"http://127.0.0.1:{up.server_port}"
    px = hp.make_server(0)
    for srv in (up, px):
        threading.Thread(target=srv.serve_forever, daemon=True).start()

    def call(method, path):
        c = http.client.HTTPConnection("127.0.0.1", px.server_port, timeout=5)
        c.request(method, path)
        return c.getresponse().status

    try:
        assert call("GET", "/healthz") == 200
        for path in ("/api/status", "/", "/healthz/x", "/healthz?a=1", "/webhooks/x"):
            assert call("GET", path) == 404
        for m in ("POST", "PUT", "DELETE", "HEAD"):
            assert call(m, "/healthz") == 404
        assert seen == ["/healthz"]          # nothing else reached the api
        assert px.server_address[0] == "127.0.0.1"
    finally:
        up.shutdown()
        px.shutdown()
