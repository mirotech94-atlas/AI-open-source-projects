"""bootstrap/mac/spark-vllm-tunnel.sh with ssh, curl, nc and pgrep stubbed on PATH.

Fake ssh processes are real `sleep`s listed in a process table file, so the
script's `kill` hits real pids and the test can see exactly which ones died.
"""
from __future__ import annotations

import os
import pathlib
import re
import signal
import subprocess

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "bootstrap/mac/spark-vllm-tunnel.sh"
FWD = "-L 18000:127.0.0.1:8000"
OTHER = [
    "ssh -N -L 9050:127.0.0.1:9050 spark",  # com.owner.spark-tunnel style
    "ssh -N -D 1080 spark",  # spark-socks
    "ssh -N -L 18000:127.0.0.1:80001 spark",  # near miss
    "ssh -N -L 18000:127x0x0x1:8000 spark",  # an unescaped dot would match this
    "vim notes -L 18000:127.0.0.1:8000",  # not an ssh at all
]

STUBS = {
    # curl: exit 0 when HEALTH file says ok, 22 otherwise; logs argv.
    "curl": """#!/bin/bash
echo "curl $*" >> "$STUB_DIR/calls"
[[ "$(cat "$STUB_DIR/health" 2>/dev/null)" == ok ]]
""",
    "ssh": """#!/bin/bash
echo "ssh $*" >> "$STUB_DIR/calls"
[[ -e "$STUB_DIR/ssh_fails" ]] && exit 255
bash -c 'sleep 300 </dev/null >/dev/null 2>&1 & echo $! >> "$STUB_DIR/dialed"; echo "$! ssh -N '"$*"'" >> "$STUB_DIR/procs"'
""",
    # nc: bound when any listed pid carrying the :18000 forward is alive.
    "nc": """#!/bin/bash
python3 - <<'PY'
import os, re, sys
d = os.environ["STUB_DIR"]
try:
    lines = open(d + "/procs").read().splitlines()
except FileNotFoundError:
    sys.exit(1)
for ln in lines:
    pid, cmd = ln.split(" ", 1)
    if "-L 18000:127.0.0.1:8000" not in cmd:
        continue
    try:
        os.kill(int(pid), 0)
    except OSError:
        continue
    sys.exit(0)
sys.exit(1)
PY
""",
    "pgrep": """#!/bin/bash
echo "pgrep $*" >> "$STUB_DIR/calls"
pat=""
while (($#)); do
  case "$1" in -f) shift ;; --) shift; pat="$1"; shift ;; *) shift ;; esac
done
PAT="$pat" python3 - <<'PY'
import os, re
d = os.environ["STUB_DIR"]
for ln in open(d + "/procs").read().splitlines():
    pid, cmd = ln.split(" ", 1)
    if re.search(os.environ["PAT"], cmd):
        print(pid)
PY
""",
}


@pytest.fixture
def env(tmp_path):
    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in STUBS.items():
        p = bindir / name
        p.write_text(body)
        p.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    (stub_dir / "procs").write_text("")
    spawned: list[int] = []
    e = {
        "HOME": str(home),
        "STUB_DIR": str(stub_dir),
        "PATH": f"{bindir}:/usr/bin:/bin",
    }
    e["_spawned"] = spawned  # type: ignore[assignment]
    yield e
    for pid in spawned:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    dialed = stub_dir / "dialed"
    if dialed.exists():
        for pid in dialed.read_text().split():
            try:
                os.kill(int(pid), signal.SIGKILL)
            except OSError:
                pass


def spawn(env, cmdline: str) -> int:
    """A real, detached sleep recorded in the fake process table."""
    out = subprocess.run(
        ["bash", "-c", "sleep 300 >/dev/null 2>&1 & echo $!"],
        capture_output=True, text=True, check=True,
    )
    pid = int(out.stdout.strip())
    env["_spawned"].append(pid)
    with open(pathlib.Path(env["STUB_DIR"]) / "procs", "a") as f:
        f.write(f"{pid} {cmdline}\n")
    return pid


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def tick(env, health: str) -> subprocess.CompletedProcess:
    pathlib.Path(env["STUB_DIR"], "health").write_text(health)
    clean = {k: v for k, v in env.items() if not k.startswith("_")}
    return subprocess.run(
        ["/bin/bash", str(SCRIPT)], env=clean, capture_output=True, text=True,
        timeout=60, check=False,
    )


def calls(env) -> list[str]:
    p = pathlib.Path(env["STUB_DIR"], "calls")
    return p.read_text().splitlines() if p.exists() else []


def counter(env) -> str:
    p = pathlib.Path(env["HOME"], "Library/Caches/atlas/spark-tunnel-fails")
    return p.read_text().strip() if p.exists() else ""


def test_dials_when_unbound_with_connect_timeout(env):
    r = tick(env, "ok")
    assert r.returncode == 0
    ssh = [c for c in calls(env) if c.startswith("ssh ")]
    assert len(ssh) == 1
    assert "-o ConnectTimeout=10" in ssh[0]
    assert "-o ExitOnForwardFailure=yes" in ssh[0]
    assert FWD in ssh[0]
    assert "18001" not in ssh[0]
    assert ssh[0].endswith(" spark")


def test_healthy_bound_does_nothing(env):
    spawn(env, f"ssh -N {FWD} spark")
    r = tick(env, "ok")
    assert r.returncode == 0
    assert not [c for c in calls(env) if c.startswith("ssh ")]
    assert counter(env) == "0"


def test_one_or_two_failed_ticks_never_kill(env):
    fwd = spawn(env, f"ssh -N {FWD} spark")
    assert tick(env, "bad").returncode == 2
    assert counter(env) == "1"
    assert tick(env, "bad").returncode == 2
    assert counter(env) == "2"
    assert alive(fwd)
    assert not [c for c in calls(env) if c.startswith(("ssh ", "pgrep"))]


def test_slow_tick_between_failures_resets_the_count(env):
    fwd = spawn(env, f"ssh -N {FWD} spark")
    tick(env, "bad")
    tick(env, "bad")
    tick(env, "ok")
    assert counter(env) == "0"
    tick(env, "bad")
    tick(env, "bad")
    assert alive(fwd)
    assert counter(env) == "2"


def test_third_failed_tick_kills_only_the_18000_forward_and_redials(env):
    fwd = spawn(env, f"ssh -N -o BatchMode=yes {FWD} spark")
    others = [spawn(env, c) for c in OTHER]
    tick(env, "bad")
    tick(env, "bad")
    r = tick(env, "bad")
    # redial happened (new ssh), old forward is gone, every other ssh survives
    ssh = [c for c in calls(env) if c.startswith("ssh ")]
    assert len(ssh) == 1 and FWD in ssh[0]
    assert not any(c for c in calls(env) if c.startswith("pgrep") and "18001" in c)
    pg = [c for c in calls(env) if c.startswith("pgrep")]
    assert len(pg) == 1 and "^(/usr/bin/)?ssh " in pg[0]
    assert r"-L 18000:127\.0\.0\.1:8000" in pg[0]
    for _ in range(20):
        if not alive(fwd):
            break
        subprocess.run(["sleep", "0.1"], check=False)
    assert not alive(fwd)
    assert all(alive(p) for p in others)
    assert counter(env) == "0"
    assert r.returncode in (0, 2)


def test_ssh_failure_reports_and_leaves_no_key_in_output(env):
    pathlib.Path(env["STUB_DIR"], "ssh_fails").write_text("")
    r = tick(env, "bad")
    assert r.returncode == 2
    out = r.stdout + r.stderr + "\n".join(calls(env))
    assert not re.search(r"(?i)bearer|spark-vllm-key|-i\s|IdentityFile|security find", out)


def test_script_shape():
    text = SCRIPT.read_text()
    assert "\nset -u\n" in text
    assert "security find-generic-password" not in text
    assert "pkill" not in text and "killall" not in text
    assert subprocess.run(["bash", "-n", str(SCRIPT)], check=False).returncode == 0
