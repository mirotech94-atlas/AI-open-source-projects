"""T6: `ATLAS_SSH_HOST=local bootstrap/set-secret.sh NAME` on the Mac.

The value arrives on stdin (the `read -rs` prompt), goes through
bootstrap/envlock.py into ATLAS_ENV_PATH, and never appears on stdout or
stderr. A literal `$` is stored doubled (compose interpolates .env), and only
the first line of input is ever read. The API restart runs the two-file
compose from ATLAS_COMPOSE_DIR unless ATLAS_NO_RESTART=1.

The value used here is a made-up marker, not a credential.
"""
from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "bootstrap" / "set-secret.sh"
FAKE = "fake-not-a-secret-7f3a9c"


@pytest.fixture
def env_file(tmp_path):
    path = tmp_path / ".env"
    path.write_text("ATLAS_WATCH_ONLY=true\nSLACK_APP_TOKEN=\n")
    path.chmod(0o600)
    return path


@pytest.fixture
def fake_docker(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "docker.log"
    docker = bindir / "docker"
    docker.write_text(f'#!/bin/sh\necho "$PWD $*" >> "{log}"\nexit 0\n')
    docker.chmod(0o755)
    return bindir, log


def _run(name: str, stdin: str, env_file: pathlib.Path, bindir: pathlib.Path,
         compose_dir: pathlib.Path, *, restart: bool = False
         ) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": f"{bindir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "HOME": str(compose_dir),
        "ATLAS_SSH_HOST": "local",
        "ATLAS_ENV_PATH": str(env_file),
        "ATLAS_COMPOSE_DIR": str(compose_dir),
        "ATLAS_RESTART_WAIT": "0",
    }
    if not restart:
        env["ATLAS_NO_RESTART"] = "1"
    return subprocess.run(["bash", str(SCRIPT), name], input=stdin, env=env,
                          capture_output=True, text=True, timeout=30,
                          check=False)


def test_local_mode_writes_through_envlock_and_never_echoes(env_file, fake_docker,
                                                            tmp_path):
    bindir, log = fake_docker
    out = _run("SLACK_APP_TOKEN", FAKE + "\n", env_file, bindir, tmp_path)
    assert out.returncode == 0, out.stderr
    assert f"SLACK_APP_TOKEN={FAKE}\n" in env_file.read_text()
    assert FAKE not in out.stdout and FAKE not in out.stderr
    assert "SLACK_APP_TOKEN" in out.stderr            # envlock names it, by length
    assert oct(env_file.stat().st_mode & 0o777) == "0o600"
    assert not log.exists()                           # ATLAS_NO_RESTART=1


def test_a_dollar_is_stored_doubled_and_never_echoed(env_file, fake_docker, tmp_path):
    bindir, _ = fake_docker
    value = FAKE + "$tail"
    out = _run("SESSION_SECRET", value + "\n", env_file, bindir, tmp_path)
    assert out.returncode == 0, out.stderr
    assert f"SESSION_SECRET={FAKE}$$tail\n" in env_file.read_text()
    for stream in (out.stdout, out.stderr):
        assert FAKE not in stream and "$tail" not in stream


def test_only_the_first_line_is_read(env_file, fake_docker, tmp_path):
    bindir, _ = fake_docker
    out = _run("GROQ_API_KEY", f"{FAKE}\nsecond-line-marker\n", env_file, bindir,
               tmp_path)
    assert out.returncode == 0, out.stderr
    text = env_file.read_text()
    assert f"GROQ_API_KEY={FAKE}\n" in text
    assert "second-line-marker" not in text
    assert "second-line-marker" not in out.stdout + out.stderr


def test_empty_value_writes_nothing(env_file, fake_docker, tmp_path):
    bindir, _ = fake_docker
    before = env_file.read_text()
    out = _run("GROQ_API_KEY", "\n", env_file, bindir, tmp_path)
    assert out.returncode != 0
    assert env_file.read_text() == before


def test_a_secret_as_the_name_is_refused(env_file, fake_docker, tmp_path):
    bindir, _ = fake_docker
    before = env_file.read_text()
    out = _run("xoxb-" + FAKE, FAKE + "\n", env_file, bindir, tmp_path)
    assert out.returncode == 2
    assert env_file.read_text() == before


def test_missing_env_file_fails_clearly(tmp_path, fake_docker):
    bindir, _ = fake_docker
    out = _run("GROQ_API_KEY", FAKE + "\n", tmp_path / "nope.env", bindir, tmp_path)
    assert out.returncode != 0
    assert "does not exist" in out.stderr
    assert FAKE not in out.stdout + out.stderr
    assert not (tmp_path / "nope.env").exists()


def test_restart_uses_both_compose_files_from_the_compose_dir(env_file, fake_docker,
                                                              tmp_path):
    bindir, log = fake_docker
    compose_dir = tmp_path / "wt"
    compose_dir.mkdir()
    out = _run("GROQ_API_KEY", FAKE + "\n", env_file, bindir, compose_dir,
               restart=True)
    assert out.returncode == 0, out.stderr
    calls = log.read_text().splitlines()
    up = [c for c in calls if " up -d api" in c]
    assert len(up) == 1
    assert up[0].startswith(str(compose_dir) + " ")
    assert "-f docker-compose.yml -f docker-compose.local.yml" in up[0]
    assert all("ssh" not in c for c in calls)
    assert FAKE not in log.read_text()
    assert FAKE not in out.stdout + out.stderr
