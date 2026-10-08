"""One locked writer for .env, because two unlocked ones lost a value.

set-secret.sh piped a GitLab token in; in the same minute another script did
a read-modify-write of the whole file for a webhook secret, and whichever
wrote second silently restored the other's stale copy. The paste
"succeeded", the checks stayed red, and it looked like the token was wrong.
"""
from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "bootstrap" / "envlock.py"


def _run(env: Path, stdin: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT)], input=stdin, text=True,
                          capture_output=True,
                          env={"ATLAS_ENV_PATH": str(env), "PATH": "/usr/bin:/bin"})


def test_concurrent_writers_lose_nothing(tmp_path):
    """Twenty writers, each setting its own key, all at once. Without the
    lock some keys vanish -- the read-modify-write of one writer overwrites
    another's just-landed line."""
    env = tmp_path / ".env"
    env.write_text("EXISTING=keep\n")
    threads = [threading.Thread(target=_run, args=(env, f"KEY_{i}=v{i}\n"))
               for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    text = env.read_text()
    assert "EXISTING=keep" in text
    missing = [i for i in range(20) if f"KEY_{i}=v{i}" not in text]
    assert not missing, f"lost updates for KEY_{missing}"


def test_a_dollar_is_escaped_once(tmp_path):
    """docker compose interpolates $; an unescaped one silently truncates.
    Escaped twice, it arrives doubled. Exactly once."""
    env = tmp_path / ".env"
    env.write_text("")
    _run(env, "H=$argon2id$v=19$x\n")
    assert env.read_text().strip() == "H=$$argon2id$$v=19$$x"


def test_reading_returns_the_stored_form(tmp_path):
    env = tmp_path / ".env"
    env.write_text("TOKEN=abc\n")
    out = subprocess.run([sys.executable, str(SCRIPT), "TOKEN"], text=True,
                         capture_output=True,
                         env={"ATLAS_ENV_PATH": str(env), "PATH": "/usr/bin:/bin"})
    assert out.stdout.strip() == "abc"


def test_the_value_is_never_echoed(tmp_path):
    env = tmp_path / ".env"
    env.write_text("")
    out = _run(env, "SECRET=hunter2-never-print\n")
    assert "hunter2" not in out.stdout + out.stderr
    assert "SECRET" in out.stderr


def test_set_secret_refuses_a_secret_as_the_name():
    """The mistake that actually happened: the token passed as the argument.
    It went into shell history and nothing went into .env."""
    script = SCRIPT.parent / "set-secret.sh"
    out = subprocess.run(["bash", str(script), "glpat-EXAMPLE"], text=True,
                         capture_output=True)
    assert out.returncode == 2
    assert "looks like a SECRET" in out.stderr
    assert "history" in out.stderr


def test_a_secret_as_the_name_is_refused_at_the_writer(tmp_path):
    """set-secret.sh already refuses this, but a direct pipe into envlock
    did not, and one got through: `glpat-...=glpat-...` sat in .env until a
    shell sourced the file and printed the token in its error."""
    env = tmp_path / ".env"
    env.write_text("")
    out = _run(env, "glpat-abc.01.xyz=glpat-abc.01.xyz\n")
    assert out.returncode != 0
    assert "not a variable name" in out.stderr
    assert env.read_text() == ""
