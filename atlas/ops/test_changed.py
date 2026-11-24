#!/usr/bin/env python3
"""Pick the test files a change can affect (the changed-files lane).

    ops/test_changed.py [--base BRANCH] [--list] [files...]

With no files it reads what differs from BASE (default feat/atlas-improve, or
$TEST_BASE): committed since the merge-base, staged, modified and untracked.
Prints one test file per line. ops/test_changed.sh runs them.

A test file is selected when it
  * is itself in the change;
  * imports a changed `atlas/` module (dotted name, `from pkg import mod`,
    or the dotted name inside a patch string), or mentions a changed file's
    path, or is named test_<module>*.py;
  * imports a changed non-test helper in tests/ (e.g. scheduler_support.py);
  * is named by a rule in ops/test_map.txt for a changed path.

It is a direct-import map, one hop: a test that only reaches the module through
another module is not selected. That is why the full suite still runs before
merge (docs/test-loop.md).
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
MAP_FILE = ROOT / "ops" / "test_map.txt"


def _git(*args: str) -> list[str]:
    out = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True,
                         text=True, check=False).stdout
    return [line for line in out.splitlines() if line]


def changed_files(base: str) -> list[str]:
    names: set[str] = set()
    merge_base = _git("merge-base", "HEAD", base)
    if merge_base:
        names.update(_git("diff", "--name-only", merge_base[0], "HEAD"))
    names.update(_git("diff", "--name-only", "HEAD"))
    names.update(_git("ls-files", "--others", "--exclude-standard"))
    return sorted(n for n in names if (ROOT / n).exists())


def _dotted(path: str) -> str:
    parts = pathlib.PurePosixPath(path).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imports(text: str, dotted: str) -> bool:
    if re.search(rf"(?<![\w.]){re.escape(dotted)}(?![\w])", text):
        return True
    if "." not in dotted:
        return False
    parent, leaf = dotted.rsplit(".", 1)
    pat = (rf"from\s+{re.escape(parent)}\s+import\s+"
           rf"(?:\([^)]*\b{re.escape(leaf)}\b|[^\n(]*\b{re.escape(leaf)}\b)")
    return re.search(pat, text) is not None


def _map_rules() -> list[tuple[str, list[str]]]:
    rules: list[tuple[str, list[str]]] = []
    if not MAP_FILE.exists():
        return rules
    for raw in MAP_FILE.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        left, right = line.split(":", 1)
        rules.append((left.strip(), right.split()))
    return rules


def select(files: list[str]) -> list[str]:
    tests = {p.name: p.read_text(errors="replace")
             for p in sorted(TESTS.glob("*.py"))}
    chosen: set[str] = set()
    all_tests = [n for n in tests if n.startswith("test_")]
    for path in files:
        p = pathlib.PurePosixPath(path)
        if p.parts[0] == "tests" and len(p.parts) == 2:
            if p.name.startswith("test_"):
                chosen.add(p.name)
            else:                      # a helper or conftest: who imports it
                if p.name == "conftest.py":
                    chosen.update(all_tests)
                    continue
                stem = p.stem
                for name, text in tests.items():
                    if name.startswith("test_") and re.search(
                            rf"from\s+tests(\.{stem}|\s+import\s+[^\n]*\b{stem}\b)"
                            rf"|import\s+tests\.{stem}", text):
                        chosen.add(name)
            continue
        if path.endswith(".py"):
            dotted = _dotted(path)
            leaf = dotted.rsplit(".", 1)[-1]
            for name, text in tests.items():
                if not name.startswith("test_"):
                    continue
                if _imports(text, dotted) or path in text \
                        or fnmatch.fnmatch(name, f"test_{leaf}*.py"):
                    chosen.add(name)
        else:
            for name, text in tests.items():
                if name.startswith("test_") and path in text:
                    chosen.add(name)
        for left, globs in _map_rules():
            if fnmatch.fnmatch(path, left) or path.startswith(left.rstrip("*")):
                for g in globs:
                    chosen.update(n for n in all_tests
                                  if fnmatch.fnmatch(f"tests/{n}", g))
    return [f"tests/{n}" for n in sorted(chosen)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("TEST_BASE", "feat/atlas-improve"))
    ap.add_argument("files", nargs="*")
    args = ap.parse_args()
    files = args.files or changed_files(args.base)
    picked = select(files)
    print(f"changed: {len(files)} file(s), selected: {len(picked)} test file(s)",
          file=sys.stderr)
    print("\n".join(picked))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
