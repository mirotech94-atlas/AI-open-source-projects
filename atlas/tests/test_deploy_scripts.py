"""RA-H1: the CD path is in git, and the copy in git is the one CI reaches.

ops/deploy/ holds byte-identical copies of the host's
/opt/atlas/bootstrap/deploy/{deploy-gate,deploy}.sh (ops/deploy/README.md).
CI cannot read the host copy (its key may run only "deploy <sha>"), so these
tests pin the committed copy and the chain that links it to .gitlab-ci.yml;
ops/deploy/check-prod-sync.sh compares it with the host by hand.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "ops" / "deploy"
SERVER_DIR = "/opt/atlas/bootstrap/deploy"
HOST = "root@203.0.113.191"
SCRIPTS = ("deploy.sh", "deploy-gate.sh")


def _sums() -> dict[str, str]:
    out = {}
    for line in (DEPLOY / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split()
        out[name] = digest
    return out


def test_the_committed_copies_match_the_pinned_hashes():
    sums = _sums()
    assert set(sums) == set(SCRIPTS)
    for name in SCRIPTS:
        got = hashlib.sha256((DEPLOY / name).read_bytes()).hexdigest()
        assert got == sums[name], (
            f"{name} changed without a resync: update SHA256SUMS and follow "
            "ops/deploy/README.md 'Syncing'")


def test_both_are_executable():
    for name in SCRIPTS + ("check-prod-sync.sh",):
        assert os.access(DEPLOY / name, os.X_OK), name


@pytest.mark.skipif(shutil.which("bash") is None, reason="no bash")
def test_both_parse():
    for name in SCRIPTS + ("check-prod-sync.sh",):
        subprocess.run(["bash", "-n", str(DEPLOY / name)], check=True)


def _job(name: str) -> str:
    """One top-level job's block of .gitlab-ci.yml, as text (PyYAML is not
    a dependency of the CI image)."""
    text = (ROOT / ".gitlab-ci.yml").read_text()
    m = re.search(rf"^{re.escape(name)}:\n(.*?)(?=^\S|\Z)", text,
                  re.MULTILINE | re.DOTALL)
    assert m, f"no {name} job in .gitlab-ci.yml"
    return m.group(1)


def test_ci_sends_exactly_deploy_sha_to_the_gate_host():
    ssh = re.search(r'^\s*- ssh .*?(\S+@\S+) "([^"]*)"\s*$', _job("deploy"), re.MULTILINE)
    assert ssh, "the deploy job no longer ssh-es a single command"
    assert ssh.group(1) == HOST
    assert ssh.group(2) == "deploy $CI_COMMIT_SHA"


def test_ci_deploys_only_the_production_branch():
    rules = re.search(r"^  rules:\n((?:    .*\n)+)", _job("deploy"), re.MULTILINE)
    assert rules and rules.group(1).strip() == (
        '- if: $CI_COMMIT_BRANCH == "phases-12-14"')


def test_the_gate_execs_the_server_path_the_docs_name():
    gate = (DEPLOY / "deploy-gate.sh").read_text()
    assert f"exec {SERVER_DIR}/deploy.sh \"$sha\"" in gate
    assert f"{SERVER_DIR}/deploy-gate.sh" in gate      # its own forced command
    readme = (DEPLOY / "README.md").read_text()
    assert f"command=\"{SERVER_DIR}/deploy-gate.sh\"" in readme
    assert f"{SERVER_DIR}/deploy.sh" in readme


def test_ci_runs_these_tests_when_the_scripts_change():
    changes = re.search(r"^    - changes: \[(.*)\]$", _job("python"), re.MULTILINE)
    assert changes and '"ops/deploy/**/*"' in changes.group(1)


def _gate_with_stub(tmp_path: Path) -> Path:
    """The gate, with the server path pointed at a stub that echoes."""
    stub = tmp_path / "deploy.sh"
    stub.write_text('#!/bin/bash\necho "WOULD DEPLOY $1"\n')
    stub.chmod(0o755)
    gate = tmp_path / "gate.sh"
    gate.write_text((DEPLOY / "deploy-gate.sh").read_text()
                    .replace(f"{SERVER_DIR}/deploy.sh", str(stub)))
    return gate


def _run_gate(gate: Path, original: str | None) -> subprocess.CompletedProcess:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    if original is not None:
        env["SSH_ORIGINAL_COMMAND"] = original
    return subprocess.run(["bash", str(gate)], env=env, capture_output=True,
                          text=True, check=False)


@pytest.mark.skipif(shutil.which("bash") is None, reason="no bash")
def test_the_gate_accepts_what_ci_sends(tmp_path):
    sha = "a" * 40
    out = _run_gate(_gate_with_stub(tmp_path), f"deploy {sha}")
    assert out.returncode == 0 and out.stdout.strip() == f"WOULD DEPLOY {sha}"


@pytest.mark.skipif(shutil.which("bash") is None, reason="no bash")
@pytest.mark.parametrize("original", [
    None, "", "deploy", "deploy abc123", "deploy " + "A" * 40,
    "deploy " + "a" * 40 + " extra", "deploy " + "a" * 39,
    "deploy " + "a" * 40 + "; rm -rf /", "sh -c id", "rollback " + "a" * 40,
    "deploy $(id)",
])
def test_the_gate_refuses_everything_else(tmp_path, original):
    out = _run_gate(_gate_with_stub(tmp_path), original)
    assert out.returncode == 2 and "WOULD DEPLOY" not in out.stdout


_SECRET = re.compile(
    r"glpat-|ghp_|xox[abp]-|-----BEGIN [A-Z ]*PRIVATE KEY|AKIA[0-9A-Z]{16}"
    r"|(?:password|passwd|secret|api[_-]?key|token)\s*=\s*['\"]?[A-Za-z0-9]",
    re.IGNORECASE)


def test_no_credential_is_committed():
    for path in DEPLOY.iterdir():
        text = path.read_text()
        assert not _SECRET.search(text), f"credential-like text in {path.name}"
