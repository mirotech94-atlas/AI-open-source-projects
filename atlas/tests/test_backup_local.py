"""T11: the Mac's nightly backup (bootstrap/mac/atlas-backup-local.sh).

The script writes data/app/backups/status.json in the shape
atlas/modules/backups reads. These tests run it with fake `docker`, `age`
and `rsync` on PATH, then feed its status file to the real check:
an offsite copy that is not configured, or failed, reads FAILING, never OK;
a missing public key fails closed and never derives one from the private
key.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess

import pytest

from atlas.kernel.contracts import Health
from atlas.modules import backups

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "bootstrap" / "mac" / "atlas-backup-local.sh"


def _tool(bindir: pathlib.Path, name: str, body: str) -> None:
    path = bindir / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


@pytest.fixture
def box(tmp_path):
    """A fake worktree, state dir and toolchain."""
    wt = tmp_path / "wt"
    (wt / "data").mkdir(parents=True)
    (wt / "data" / "backup-age.pub").write_text("age1fakepublickeyfortests\n")
    (wt / ".env").write_text("ATLAS_WATCH_ONLY=true\n")
    state = tmp_path / "state"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "calls.log"
    # `docker compose ... exec -T postgres sh -c 'pg_dump ...'` prints a dump.
    _tool(bindir, "docker", f'echo "docker $*" >> "{calls}"\nprintf "PGDMP-fake-dump"\n')
    # `age -R pub -o out in` copies in to out.
    _tool(bindir, "age", f'echo "age $*" >> "{calls}"\n'
                         'while [ $# -gt 0 ]; do case "$1" in -o) out=$2; shift;; '
                         '-R) shift;; *) in=$1;; esac; shift; done\ncp "$in" "$out"\n')
    _tool(bindir, "age-keygen", f'echo "age-keygen $*" >> "{calls}"\nexit 1\n')
    _tool(bindir, "rsync", f'echo "rsync $*" >> "{calls}"\nexit 0\n')
    return {"wt": wt, "state": state, "bin": bindir, "calls": calls}


def _run(box, **extra: str) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": f"{box['bin']}:/usr/bin:/bin",
        "HOME": str(box["wt"].parent),
        "ATLAS_COMPOSE_DIR": str(box["wt"]),
        "ATLAS_STATE_DIR": str(box["state"]),
        **extra,
    }
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True,
                          text=True, timeout=60, check=False)


def _status(box) -> dict:
    path = box["wt"] / "data" / "app" / "backups" / "status.json"
    return json.loads(path.read_text())


async def _check(box, monkeypatch) -> Health:
    monkeypatch.setattr(backups, "STATUS",
                        str(box["wt"] / "data" / "app" / "backups" / "status.json"))
    monkeypatch.setattr(backups, "DRILL", str(box["wt"] / "no-drill.json"))
    return (await backups.check_backup()).health


def test_status_shape_matches_what_the_module_reads(box):
    _run(box)
    data = _status(box)
    for key in ("at", "dump_ok", "offsite_ok", "ok", "file", "bytes", "detail"):
        assert key in data, key
    assert backups._age(data) is not None          # a readable, aware UTC time
    assert data["dump_ok"] is True
    assert data["bytes"] > 0
    assert data["file"].startswith("atlas-") and data["file"].endswith(".dump")
    dumps = list((box["state"] / "backups").glob("atlas-*.dump.age"))
    assert len(dumps) == 1


async def test_offsite_not_configured_is_failing(box, monkeypatch):
    out = _run(box)
    assert out.returncode != 0
    data = _status(box)
    assert data["ok"] is False and data["offsite_ok"] is False
    assert "offsite not configured" in data["detail"]
    assert "rsync" not in box["calls"].read_text()
    assert await _check(box, monkeypatch) is Health.FAILING


async def test_offsite_failure_is_failing(box, monkeypatch):
    _tool(box["bin"], "rsync", "exit 12\n")
    out = _run(box, ATLAS_BACKUP_OFFSITE="atlasbak@backup.invalid:")
    assert out.returncode != 0
    data = _status(box)
    assert data["ok"] is False and data["offsite_ok"] is False
    assert "off-box copy failed" in data["detail"]
    assert await _check(box, monkeypatch) is Health.FAILING


async def test_offsite_read_from_env_file_and_copied(box, monkeypatch):
    env = box["wt"] / ".env"
    env.write_text(env.read_text() + "ATLAS_BACKUP_OFFSITE=atlasbak@backup.invalid:\n")
    out = _run(box)
    assert out.returncode == 0, out.stderr
    data = _status(box)
    assert data["ok"] is True and data["offsite_ok"] is True
    rsync = [c for c in box["calls"].read_text().splitlines() if c.startswith("rsync")]
    assert len(rsync) == 2 and ".dump.age" in rsync[0]   # copy, then verify
    assert "atlasbak@backup.invalid:" in rsync[0]
    # No restore drill on record: proven copied, not proven restorable.
    assert await _check(box, monkeypatch) is Health.DEGRADED


async def test_copy_is_verified_by_checksum_on_the_offsite_host(box, monkeypatch):
    out = _run(box, ATLAS_BACKUP_OFFSITE="atlasbak@backup.invalid:")
    assert out.returncode == 0, out.stderr
    rsync = [c for c in box["calls"].read_text().splitlines() if c.startswith("rsync")]
    assert len(rsync) == 2
    verify = rsync[1].split()
    assert "-n" in verify and "-c" in verify and "-i" in verify
    assert _status(box)["offsite_ok"] is True
    assert "verified" in _status(box)["detail"]


async def test_offsite_copy_that_differs_is_failing(box, monkeypatch):
    # The copy "succeeds" but a checksum dry run says the remote file differs.
    _tool(box["bin"], "rsync", f'echo "rsync $*" >> "{box["calls"]}"\n'
                               'case " $* " in *" -n "*) echo ">fc...... x.dump.age";; '
                               'esac\nexit 0\n')
    out = _run(box, ATLAS_BACKUP_OFFSITE="atlasbak@backup.invalid:")
    assert out.returncode != 0
    data = _status(box)
    assert data["ok"] is False and data["offsite_ok"] is False
    assert "does not match" in data["detail"]
    assert await _check(box, monkeypatch) is Health.FAILING


def test_offsite_without_colon_is_never_a_local_path(box):
    out = _run(box, ATLAS_BACKUP_OFFSITE="atlasbak@backup.invalid")
    assert out.returncode == 0, out.stderr
    rsync = [c for c in box["calls"].read_text().splitlines() if c.startswith("rsync")]
    assert len(rsync) == 2
    assert all(c.endswith(" atlasbak@backup.invalid:") for c in rsync)


async def test_missing_public_key_fails_closed(box, monkeypatch):
    (box["wt"] / "data" / "backup-age.pub").unlink()
    out = _run(box, ATLAS_BACKUP_OFFSITE="atlasbak@backup.invalid:")
    assert out.returncode != 0
    assert "backup-age.pub" in out.stderr
    calls = box["calls"].read_text()
    assert "age-keygen" not in calls and "rsync" not in calls
    assert not list((box["state"] / "backups").glob("*.age"))
    data = _status(box)
    assert data["ok"] is False and data["offsite_ok"] is False
    assert "backup-age.pub" in data["detail"]
    assert await _check(box, monkeypatch) is Health.FAILING


async def test_failed_dump_is_failing(box, monkeypatch):
    _tool(box["bin"], "docker", "exit 1\n")
    out = _run(box)
    assert out.returncode != 0
    data = _status(box)
    assert data["dump_ok"] is False and data["ok"] is False
    assert await _check(box, monkeypatch) is Health.FAILING


def test_keeps_fourteen_local_copies(box):
    backups_dir = box["state"] / "backups"
    backups_dir.mkdir(parents=True)
    for i in range(20):
        for suffix in (".dump", ".dump.age"):
            path = backups_dir / f"atlas-20260101-{i:04d}{suffix}"
            path.write_text("old")
            os.utime(path, (1_700_000_000 + i, 1_700_000_000 + i))
    _run(box)
    assert len(list(backups_dir.glob("atlas-*.dump"))) == 14
    assert len(list(backups_dir.glob("atlas-*.dump.age"))) == 14
