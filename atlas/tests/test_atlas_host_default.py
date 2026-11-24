"""The server (atlas.example.com) is deleted: no helper may default to it.

Plain `bootstrap/set-secret.sh NAME` on the Mac (no /opt/atlas, this
checkout has docker-compose.local.yml) writes the local .env and restarts
the api with both compose files, exactly as `google_login.default_host()`
decides. Helpers that only know the server refuse in local mode before
they touch anything. ssh is faked and logs every call: in local mode it
must never be called.

The value used here is a made-up marker, not a credential.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import re
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
BOOT = ROOT / "bootstrap"
FAKE = "fake-not-a-secret-51c0"


@pytest.fixture
def sandbox(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    for tool in ("ssh", "rsync", "docker"):
        exe = bindir / tool
        exe.write_text(f'#!/bin/sh\necho "{tool} $PWD $*" >> "{log}"\nexit 0\n')
        exe.chmod(0o755)
    env_file = tmp_path / ".env"
    env_file.write_text("ATLAS_WATCH_ONLY=true\n")
    env_file.chmod(0o600)
    env = {
        "PATH": f"{bindir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "HOME": str(tmp_path),
        "ATLAS_OPT_DIR": str(tmp_path / "no-opt-atlas"),
        "ATLAS_ENV_PATH": str(env_file),
        "ATLAS_COMPOSE_DIR": str(tmp_path),
        "ATLAS_RESTART_WAIT": "0",
    }
    return env, env_file, log, tmp_path


def _calls(log: pathlib.Path) -> str:
    return log.read_text() if log.exists() else ""


def test_host_helper_says_local_without_opt_atlas(sandbox):
    env, _, _, _ = sandbox
    out = subprocess.run(["bash", "-c", f'. "{BOOT}/atlas-host.sh"; atlas_host'],
                         env=env, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "local"


def test_host_helper_keeps_the_server_where_opt_atlas_exists(sandbox):
    env, _, _, tmp = sandbox
    (tmp / "opt").mkdir()
    env = {**env, "ATLAS_OPT_DIR": str(tmp / "opt")}
    out = subprocess.run(["bash", "-c", f'. "{BOOT}/atlas-host.sh"; atlas_host'],
                         env=env, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "root@atlas.example.com"


def test_explicit_ssh_host_still_wins(sandbox):
    env, _, _, _ = sandbox
    env = {**env, "ATLAS_SSH_HOST": "root@example.test"}
    out = subprocess.run(["bash", "-c", f'. "{BOOT}/atlas-host.sh"; atlas_host'],
                         env=env, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "root@example.test"


def test_plain_set_secret_writes_the_local_env_and_restarts_locally(sandbox):
    env, env_file, log, tmp = sandbox
    out = subprocess.run(["bash", str(BOOT / "set-secret.sh"), "SOME_TOKEN"],
                         input=FAKE + "\n", env=env, capture_output=True,
                         text=True, timeout=30, check=False)
    assert out.returncode == 0, out.stderr
    assert f"SOME_TOKEN={FAKE}" in env_file.read_text()
    assert FAKE not in out.stdout + out.stderr
    calls = _calls(log)
    assert "ssh" not in calls.split()
    assert f"docker {tmp} compose -f docker-compose.yml " \
           "-f docker-compose.local.yml up -d api" in calls


@pytest.mark.parametrize("script", ["linkedin-login.sh", "academy-files.sh"])
def test_server_only_helpers_refuse_locally_before_doing_anything(sandbox, script):
    env, _, log, _ = sandbox
    out = subprocess.run(["bash", str(BOOT / script)], env=env, input="",
                         capture_output=True, text=True, timeout=30, check=False)
    assert out.returncode == 2, out.stderr
    assert "server was deleted" in out.stderr
    assert _calls(log) == ""


def test_no_shell_helper_defaults_to_the_deleted_server():
    offenders = [p.name for p in BOOT.glob("*.sh")
                 if re.search(r":-\s*root@atlas\.18dn\.com", p.read_text())]
    assert offenders == []


def _load(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_getlayers_connect_defaults_to_local(monkeypatch, tmp_path):
    monkeypatch.delenv("ATLAS_SSH_HOST", raising=False)
    monkeypatch.setenv("ATLAS_OPT_DIR", str(tmp_path / "missing"))
    mod = _load(BOOT / "getlayers-connect.py", "getlayers_connect_t")
    assert mod.default_host() == "local"


def test_getlayers_local_push_goes_through_envlock_not_ssh(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("A=1\n")
    env_file.chmod(0o600)
    monkeypatch.setenv("ATLAS_ENV_PATH", str(env_file))
    mod = _load(BOOT / "getlayers-connect.py", "getlayers_connect_t2")
    seen: list[list[str]] = []
    real_run = subprocess.run

    def fake_run(cmd, *a, **kw):
        seen.append(list(cmd))
        if cmd and cmd[0] in ("ssh", "docker"):
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    mod.push_to_server(f"GETLAYERS_OAUTH_CLIENT_ID={FAKE}\n", "local")
    assert f"GETLAYERS_OAUTH_CLIENT_ID={FAKE}" in env_file.read_text()
    assert not any(c[0] == "ssh" for c in seen)
    assert any(c[0] == "docker" and "docker-compose.local.yml" in c for c in seen)


def test_cli_matrix_setup_host_defaults_to_local(monkeypatch, tmp_path):
    from atlas import cli
    monkeypatch.delenv("ATLAS_SSH_HOST", raising=False)
    monkeypatch.setenv("ATLAS_OPT_DIR", str(tmp_path / "missing"))
    assert cli._atlas_host() == "local"
