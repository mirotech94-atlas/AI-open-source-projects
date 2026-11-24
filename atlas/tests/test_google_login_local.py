"""bootstrap/google_login.py's local mode (ATLAS_SSH_HOST=local).

The server atlas.example.com is gone; ATLAS runs from this checkout on the Mac.
Every ssh path (read a value, write the GOOGLE_*/MAIL_*/YOUTUBE_* values,
restart the api, run the checks) gets a local twin, the same way
bootstrap/set-secret.sh has one. Local is the default when /opt/atlas is
absent and the repo has docker-compose.local.yml. Secrets travel on stdin,
never in argv, and are never printed.

subprocess.run is monkeypatched: nothing here reaches ssh or docker.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FAKE = "fake-not-a-secret-91c2"


def _load(monkeypatch, **env):
    for k in ("ATLAS_GOOGLE_MAILBOX", "ATLAS_GOOGLE_YOUTUBE", "ATLAS_SSH_HOST",
              "ATLAS_ENV_PATH", "ATLAS_COMPOSE_DIR"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location("google_login_local_t",
                                                  ROOT / "bootstrap" / "google_login.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Recorder:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.calls: list[dict] = []
        self.stdout, self.returncode = stdout, returncode

    def __call__(self, cmd, **kw):
        self.calls.append({"cmd": cmd, **kw})
        return subprocess.CompletedProcess(cmd, self.returncode, self.stdout, "")


def test_local_is_the_default_without_opt_atlas_and_with_the_local_compose(monkeypatch,
                                                                         tmp_path):
    gl = _load(monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docker-compose.local.yml").write_text("services: {}\n")
    assert gl.default_host(repo=repo, opt=tmp_path / "absent") == "local"
    opt = tmp_path / "opt"
    opt.mkdir()
    assert gl.default_host(repo=repo, opt=opt) == "root@atlas.example.com"
    (repo / "docker-compose.local.yml").unlink()
    assert gl.default_host(repo=repo, opt=tmp_path / "absent") == "root@atlas.example.com"
    # An explicit ATLAS_SSH_HOST always wins.
    monkeypatch.setenv("ATLAS_SSH_HOST", "root@elsewhere")
    assert gl.default_host(repo=repo, opt=tmp_path / "absent") == "root@elsewhere"


def test_local_read_goes_through_envlock_on_the_repo_env(monkeypatch, tmp_path):
    gl = _load(monkeypatch, ATLAS_ENV_PATH=str(tmp_path / ".env"))
    rec = Recorder(stdout="abc.apps.googleusercontent.com\n")
    monkeypatch.setattr(gl.subprocess, "run", rec)
    assert gl._server_value("local", "GOOGLE_CLIENT_ID") == "abc.apps.googleusercontent.com"
    (call,) = rec.calls
    assert "ssh" not in call["cmd"]
    assert call["cmd"][-2:] == [str(ROOT / "bootstrap" / "envlock.py"), "GOOGLE_CLIENT_ID"]
    assert call["env"]["ATLAS_ENV_PATH"] == str(tmp_path / ".env")


def test_local_read_defaults_to_the_checkout_env(monkeypatch):
    gl = _load(monkeypatch)
    rec = Recorder(returncode=1)
    monkeypatch.setattr(gl.subprocess, "run", rec)
    assert gl._server_value("local", "MAIL_ACCOUNTS") == ""
    assert rec.calls[0]["env"]["ATLAS_ENV_PATH"] == str(ROOT / ".env")


def test_local_write_pipes_values_to_envlock_then_restarts_the_api(monkeypatch, tmp_path,
                                                                    capsys):
    gl = _load(monkeypatch, ATLAS_ENV_PATH=str(tmp_path / ".env"),
               ATLAS_COMPOSE_DIR=str(tmp_path))
    rec = Recorder()
    monkeypatch.setattr(gl.subprocess, "run", rec)
    lines = ["GOOGLE_CLIENT_ID=x.apps.googleusercontent.com",
             f"GOOGLE_CLIENT_SECRET={FAKE}", f"GOOGLE_REFRESH_TOKEN={FAKE}r"]
    gl.write_values("local", lines)
    write, restart = rec.calls
    assert write["cmd"][-1] == str(ROOT / "bootstrap" / "envlock.py")
    assert write["input"] == "\n".join(lines) + "\n"
    assert write["env"]["ATLAS_ENV_PATH"] == str(tmp_path / ".env")
    assert write["check"] is True
    assert restart["cmd"] == ["docker", "compose", "-f", "docker-compose.yml",
                              "-f", "docker-compose.local.yml", "up", "-d", "api"]
    assert restart["cwd"] == str(tmp_path)
    for call in rec.calls:                     # never on a command line
        assert not any(FAKE in str(part) for part in call["cmd"])
    out = capsys.readouterr()
    assert FAKE not in out.out and FAKE not in out.err


def test_local_check_runs_connect_in_the_local_compose(monkeypatch):
    gl = _load(monkeypatch)
    rec = Recorder()
    monkeypatch.setattr(gl.subprocess, "run", rec)
    gl.run_check("local", "mail")
    (call,) = rec.calls
    assert call["cmd"] == ["docker", "compose", "-f", "docker-compose.yml",
                           "-f", "docker-compose.local.yml", "exec", "-T", "api",
                           "python", "-m", "atlas.cli", "connect", "mail"]
    assert call["cwd"] == str(ROOT)
    assert call["check"] is False


def test_remote_paths_are_unchanged(monkeypatch):
    gl = _load(monkeypatch)
    rec = Recorder(stdout="v\n")
    monkeypatch.setattr(gl.subprocess, "run", rec)
    assert gl._server_value("root@h", "GOOGLE_CLIENT_ID") == "v"
    gl.write_values("root@h", [f"GOOGLE_CLIENT_SECRET={FAKE}"])
    gl.run_check("root@h", "calendar")
    read, write, check = rec.calls
    assert read["cmd"][:2] == ["ssh", "root@h"]
    assert "envlock.py GOOGLE_CLIENT_ID" in read["cmd"][2]
    assert write["cmd"][:2] == ["ssh", "root@h"] and "docker compose up -d api" in write["cmd"][2]
    assert write["input"] == f"GOOGLE_CLIENT_SECRET={FAKE}\n"
    assert not any(FAKE in part for part in write["cmd"])
    assert check["cmd"][:2] == ["ssh", "root@h"] and "connect calendar" in check["cmd"][2]


def test_mailbox_mode_reads_the_shared_client_locally(monkeypatch, tmp_path):
    import sys
    gl = _load(monkeypatch, ATLAS_GOOGLE_MAILBOX="CLIPS", ATLAS_SSH_HOST="local")
    seen = []

    def server_value(host, name):
        seen.append((host, name))
        return ""
    monkeypatch.setattr(gl, "_server_value", server_value)
    monkeypatch.setattr(sys, "argv", ["google_login.py"])
    with pytest.raises(SystemExit):
        gl.main()
    assert seen == [("local", "GOOGLE_CLIENT_ID"), ("local", "GOOGLE_CLIENT_SECRET")]


def test_local_mode_refuses_before_consent_when_the_env_is_missing(monkeypatch, tmp_path):
    import sys
    gl = _load(monkeypatch, ATLAS_SSH_HOST="local", ATLAS_ENV_PATH=str(tmp_path / ".env"))
    monkeypatch.setattr(sys, "argv", ["google_login.py", "a.apps.googleusercontent.com",
                                      FAKE])
    opened = []
    monkeypatch.setattr(gl.webbrowser, "open", opened.append)
    with pytest.raises(SystemExit, match="does not exist"):
        gl.main()
    assert opened == []


@pytest.mark.parametrize("venv", [True, False])
def test_the_wrapper_falls_back_to_python3_without_a_venv(tmp_path, venv):
    """google-login.sh needs only stdlib: no .venv means python3, not an exit."""
    repo = tmp_path / "repo"
    (repo / "bootstrap").mkdir(parents=True)
    sh = repo / "bootstrap" / "google-login.sh"
    sh.write_text((ROOT / "bootstrap" / "google-login.sh").read_text())
    (repo / "bootstrap" / "google_login.py").write_text(
        "import sys\nprint('ran', sys.executable, sys.argv[1:])\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    py3 = bindir / "python3"
    py3.write_text('#!/bin/sh\necho "python3-shim $*"\n')
    py3.chmod(0o755)
    if venv:
        vpy = repo / ".venv" / "bin" / "python"
        vpy.parent.mkdir(parents=True)
        vpy.write_text('#!/bin/sh\necho "venv-python $*"\n')
        vpy.chmod(0o755)
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(tmp_path)}
    out = subprocess.run(["bash", str(sh), "abc.apps.googleusercontent.com", FAKE],
                         env=env, capture_output=True, text=True, timeout=20, check=False)
    assert out.returncode == 0, out.stderr
    assert out.stdout.startswith("venv-python" if venv else "python3-shim")
    assert os.fspath(repo / "bootstrap" / "google_login.py") in out.stdout
