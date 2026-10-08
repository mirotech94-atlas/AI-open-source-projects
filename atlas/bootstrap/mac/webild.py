#!/usr/bin/env python3
"""The Mac side of ATLAS's Webild queue, for the Webild agent.

    webild.py claim                             -> EMPTY, or your task as JSON
    webild.py submitted <lead_id> <url>         -> record the published URL
    webild.py report <lead_id> <status> <file>  -> status: needs_miro | failed

Installed as ~/.config/atlas/webild.py (see bootstrap/mac/webild-agent.md).
The bearer token is read from ~/.config/atlas/handoff-token and handed to
curl on stdin as curl config, so it never shows in a process list, a log or
the agent's transcript. Standard library only; runs under /usr/bin/python3
(3.9): no syntax newer than that.
"""
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

BASE = os.environ.get("ATLAS_BASE", "https://atlas.example.com")
TOKEN_FILE = pathlib.Path.home() / ".config/atlas/handoff-token"
STATUSES = ("needs_miro", "failed")


def call(method, path, body=None, seconds=30):
    """(HTTP status, body text). 0 when curl itself failed or timed out."""
    token = TOKEN_FILE.read_text().strip()
    config = (f'header = "Authorization: Bearer {token}"\n'
              'header = "Content-Type: application/json"\n')
    argv = ["curl", "-sS", "-m", str(seconds), "-K", "-", "-X", method,
            "-w", "\n%{http_code}", BASE + path]
    name = None
    if body is not None:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(body, f)
            name = f.name
        argv[6:6] = ["--data-binary", "@" + name]
    try:
        out = subprocess.run(argv, input=config, capture_output=True, text=True,
                             check=False)
    finally:
        if name:
            os.unlink(name)
    if out.returncode:
        return 0, out.stderr.strip()[:300]
    text, _, code = out.stdout.rpartition("\n")
    return int(code or 0), text


def lead(value):
    if not re.match(r"^[1-9][0-9]{0,11}$", value or ""):
        sys.exit("lead_id must be a positive number")
    return int(value)


def main():
    args = sys.argv[1:]
    if args == ["claim"]:
        code, text = call("POST", "/webild/claim", {"agent": "webild-agent"})
        if code == 204:
            print("EMPTY")
        elif code == 200:
            print(json.dumps(json.loads(text)["task"], indent=2))
        else:
            sys.exit(f"claim failed: HTTP {code} {text[:300]}")
    elif len(args) == 3 and args[0] == "submitted":
        # ATLAS reads the live site before it answers: allow it the time.
        code, text = call("POST", f"/webild/{lead(args[1])}/submitted",
                          {"url": args[2]}, seconds=240)
        print(f"HTTP {code} {text[:2000]}")
        if code != 200:
            sys.exit(1)
    elif len(args) == 4 and args[0] == "report" and args[2] in STATUSES:
        summary = pathlib.Path(args[3]).read_text()[:4000]
        code, text = call("POST", f"/webild/{lead(args[1])}/report",
                          {"status": args[2], "summary": summary})
        print(f"HTTP {code} {text[:300]}")
        if code != 200:
            sys.exit(1)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
