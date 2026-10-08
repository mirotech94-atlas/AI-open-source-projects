"""The one way to change a value in .env.

Two writers lost a value to each other today. set-secret.sh piped a GitLab
token in; in the same minute another script did a read-modify-write of the
whole file for a webhook secret, and whichever wrote second silently
restored the other's stale copy. The paste "succeeded", the checks stayed
red, and it looked like the token was wrong.

Every writer now goes through here, under one advisory lock, with the
read and the write inside the same critical section. A writer that does
not take the lock can still race -- there is no way to stop that from
inside the file -- which is why this module is the documented entry point
and the shell one-liners are gone.

    printf 'NAME=value\\n' | python3 envlock.py          # one or more lines
    python3 envlock.py NAME                             # print, nothing else

Values arrive on stdin so they are not in a command line or a history.
A literal $ is doubled, because docker compose interpolates .env and an
unescaped $ silently truncates the value. Nothing is ever echoed back but
the name and the length.
"""
from __future__ import annotations

import fcntl
import os
import sys
import tempfile

ENV = os.environ.get("ATLAS_ENV_PATH", "/opt/atlas/.env")
LOCK = ENV + ".lock"


def _load(text: str) -> list[str]:
    return text.splitlines()


def set_values(pairs: list[tuple[str, str]]) -> None:
    with open(LOCK, "a+") as lock:
        # Exclusive: block until every other writer is done, and hold it
        # across BOTH the read and the write. A lock around only the write
        # still lets a stale read win.
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            with open(ENV) as fh:
                lines = _load(fh.read())
            index = {}
            for i, line in enumerate(lines):
                name = line.split("=", 1)[0]
                if name and not line.startswith("#"):
                    index[name] = i
            for name, value in pairs:
                row = f"{name}={value.replace('$', '$$')}"
                if name in index:
                    lines[index[name]] = row
                else:
                    lines.append(row)
                print(f"  {name:28} {len(value)} chars", file=sys.stderr)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(ENV))
            with os.fdopen(fd, "w") as fh:
                fh.write("\n".join(lines) + "\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, ENV)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def main() -> None:
    if len(sys.argv) > 1:
        # Read one value, under the shared lock so a half-written file is
        # never observed.
        with open(LOCK, "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_SH)
            with open(ENV) as fh:
                for line in fh:
                    if line.startswith(sys.argv[1] + "="):
                        sys.stdout.write(line.split("=", 1)[1])
                        return
        sys.exit(1)

    import re

    pairs = []
    for raw in sys.stdin.read().splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        name, sep, value = raw.partition("=")
        if not sep:
            sys.exit(f"not NAME=value: {raw[:20]!r}")
        name = name.strip()
        # A secret passed as the NAME once produced the line
        # `glpat-...=glpat-...` in .env. Compose ignored it, so nothing
        # noticed -- until a shell sourced the file and died on it, with the
        # token in the error. Refuse anything that is not a variable name.
        if not re.match(r"^[A-Z_][A-Z0-9_]*$", name):
            sys.exit(f"{name[:12]!r}... is not a variable name -- if that is "
                     f"a secret, the NAME goes first and the value at the "
                     f"prompt")
        pairs.append((name, value))
    if not pairs:
        sys.exit("nothing to write")
    set_values(pairs)


if __name__ == "__main__":
    main()
