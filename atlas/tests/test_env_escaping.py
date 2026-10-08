"""Escaping a secret for docker compose's .env interpolation.

This bug has now been hit twice, and both times it presented as a silently
wrong value rather than an error:

  * an argon2 password hash containing '$' arrived in the container as "",
    so every correct password was rejected with nothing logged
  * three LLM API keys came back "API key is invalid" because a sed
    expression appended '$$' to each one -- which sends you to the provider's
    dashboard rather than to the file

The second is the one this file guards, because the wrong version looks
right: `sed "s/\\$/\\$\\$/g"` reads as "replace $ with $$", but sed's pattern
'$' means end-of-line, so it appends instead of replacing.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# setenv.py is now a shim over envlock.py, which holds the lock AND the
# escaping. The round-trip tests still drive setenv.py, because that is the
# entry point callers use; the source-level assertion looks at the real one.
SCRIPT = Path(__file__).resolve().parent.parent / "bootstrap" / "setenv.py"
IMPL = Path(__file__).resolve().parent.parent / "bootstrap" / "envlock.py"

# Real shapes: an argon2 hash is full of '$', and API keys are not.
VALUES = [
    "$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHQ$aGFzaA",
    "sk-ant-api03-abcDEF123_-xyz",
    "sk-proj-UsrRvsV1o7VKoZLghQtv",
    "plain-value-no-dollars",
    "$leading",
    "trailing$",
    "$$already$$doubled$$",
]


def _write(tmp_path, pairs: list[tuple[str, str]]) -> str:
    env = tmp_path / ".env"
    env.write_text("EXISTING=keep-me\n")
    stdin = "".join(f"{n}={v}\n" for n, v in pairs)
    subprocess.run([sys.executable, str(SCRIPT)], input=stdin, text=True,
                   check=True, capture_output=True,
                   env={"ATLAS_ENV_PATH": str(env), "PATH": "/usr/bin:/bin"})
    return env.read_text()


def _compose_reads(env_text: str, name: str) -> str:
    """What docker compose hands the container: $$ collapses to $, and a
    single $ before a name would interpolate (which is the whole hazard)."""
    for line in env_text.splitlines():
        key, _, raw = line.partition("=")
        if key == name:
            return raw.replace("$$", "$")
    raise AssertionError(f"{name} not written")


@pytest.mark.parametrize("value", VALUES)
def test_a_value_survives_compose_interpolation(tmp_path, value):
    text = _write(tmp_path, [("SECRET", value)])
    assert _compose_reads(text, "SECRET") == value


def test_the_escaping_is_a_literal_replacement():
    """`sed "s/$/$$/g"` appends rather than replaces, because '$' in a sed
    pattern means end-of-line. str.replace has no such reading, and the
    escaping must not be shelled out to anything that does.

    (The file mentions sed in its comments on purpose -- that is the
    explanation of the bug, not a use of it.)"""
    import ast

    tree = ast.parse(IMPL.read_text())
    assert "replace('$', '$$')" in IMPL.read_text()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            assert name not in ("system", "popen", "run", "check_output"), (
                f"escaping must not shell out ({name} at line {node.lineno})")


def test_nothing_else_in_the_file_is_touched(tmp_path):
    text = _write(tmp_path, [("SECRET", "x")])
    assert "EXISTING=keep-me" in text


def test_an_existing_name_is_replaced_not_duplicated(tmp_path):
    env = tmp_path / ".env"
    env.write_text("TOKEN=old\nOTHER=1\n")
    subprocess.run([sys.executable, str(SCRIPT)], input="TOKEN=new\n",
                   text=True, check=True, capture_output=True,
                   env={"ATLAS_ENV_PATH": str(env), "PATH": "/usr/bin:/bin"})
    lines = env.read_text().splitlines()
    assert [ln for ln in lines if ln.startswith("TOKEN=")] == ["TOKEN=new"]
    assert "OTHER=1" in lines


def test_the_value_is_never_echoed(tmp_path):
    env = tmp_path / ".env"
    env.write_text("")
    result = subprocess.run([sys.executable, str(SCRIPT)],
                            input="SECRET=hunter2-do-not-print\n", text=True,
                            check=True, capture_output=True,
                            env={"ATLAS_ENV_PATH": str(env), "PATH": "/usr/bin:/bin"})
    assert "hunter2" not in result.stdout
    assert "hunter2" not in result.stderr
    assert "SECRET" in result.stderr        # the name and length are fine
