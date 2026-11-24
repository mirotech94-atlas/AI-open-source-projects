"""The LinkedIn state file, now written on the server.

This is the same `state.json` the Mac-side agent kept for nineteen runs:
post banks, follower ledgers, sent invites, queued messages, standing
instructions, every named deviation with its reasoning. It moved here whole.
ATLAS is now its only writer.

Writes are atomic (a .tmp then a rename) so a crash mid-write leaves the
previous good file, and every write also appends a line to the run log.
"""
from __future__ import annotations

import re

import asyncio
import contextlib
import fcntl
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RUNS = Path(os.getenv("LINKEDIN_RUNS_DIR", "/app/data/linkedin-agent/runs"))


def path() -> Path:
    """One knob for where the file lives: the package's STATE."""
    from . import STATE
    return STATE


def load() -> dict[str, Any]:
    with path().open() as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise TypeError(f"{path()} is not a JSON object")
    return data


def save(state: dict[str, Any]) -> None:
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False, default=str))
    os.replace(tmp, target)


def now_local() -> datetime:
    return datetime.now(UTC).astimezone()


def stamp() -> str:
    return now_local().isoformat(timespec="minutes")


def today() -> str:
    return now_local().strftime("%Y-%m-%d")


def log_run(lines: list[str], *, label: str = "sweep") -> Path | None:
    """One markdown file per run, like the Mac agent kept."""
    try:
        RUNS.mkdir(parents=True, exist_ok=True)
        path = RUNS / f"{now_local():%Y-%m-%d-%H%M}-{label}.md"
        path.write_text(f"# LinkedIn {label} — {stamp()}\n\n"
                        + "\n".join(f"- {line}" for line in lines) + "\n")
        return path
    except OSError:
        return None


def bank(state: dict[str, Any], page: str) -> tuple[list[dict], dict, dict]:
    """(post_bank, posting, holder) for a page. The two pages keep their
    banks in different places; guessing one shape for both reads an empty
    bank and reports infinite runway."""
    from . import KNOWN_PAGES
    spec = KNOWN_PAGES[page]
    holder = state.get(spec["bank_key"]) or {}
    return (list(holder.get("post_bank") or []),
            dict(holder.get("posting") or {}), holder)


def page_obj(state: dict[str, Any], page: str) -> dict:
    from . import KNOWN_PAGES
    return state.setdefault(KNOWN_PAGES[page]["page_key"], {})


@contextlib.asynccontextmanager
async def file_lock(timeout_s: float = 900.0):
    """One writer of the state file at a time, across processes.

    The in-process asyncio lock is not enough: a sweep started by hand with
    `docker compose exec` and the scheduler's own run overlapped, each
    loaded the file, and the later save threw away the earlier one -- three
    freshly drafted posts vanished and their approval cards then failed
    with "no entry". Every load-modify-save runs inside this.
    """
    lock_path = path().with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = lock_path.open("a+")
    try:
        deadline = asyncio.get_running_loop().time() + timeout_s
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if asyncio.get_running_loop().time() > deadline:
                    raise TimeoutError("another LinkedIn run holds the state "
                                       "lock") from None
                await asyncio.sleep(2)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


# Links a post may never carry: the lab hosts (any other example.com name) and
# the demo/guarder apps. The one public host, agency.example.com, is the agency
# and Academy home and is the only example.com name a post may link to.
BANNED_LINKS = ("example.com", "pulsedemo", "my.vault.example", "admin.vault.example")
PUBLIC_HOST = "agency.example.com"


_HOST = re.compile(r"(?:[a-z0-9-]+\.)+[a-z]{2,}")


def has_banned_link(text: str) -> bool:
    """True when a post names a banned app or any example.com host other than the
    one public host. Host names are read out whole and compared exactly, so
    `evil.agency.example.com` and `agency.example.com.evil.io` are not the public host."""
    low = text.lower()
    for host in _HOST.findall(low):
        if host != PUBLIC_HOST and "example.com" in host:
            return True
    rest = low.replace(PUBLIC_HOST, " ")
    return any(b in rest for b in BANNED_LINKS if b != "example.com")
