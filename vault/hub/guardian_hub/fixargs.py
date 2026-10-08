"""What a fix's `arg` and a host's SSH target may look like.

Both end up in a root shell on a customer host (the sensor builds
`systemctl restart <arg>`, `ssh <target> ...`), so the hub refuses anything
that isn't a plain value before it is queued or saved. The sensor checks the
same shapes again right before it runs (audit 2026-09-28).
"""
from __future__ import annotations

import ipaddress
import re

_UNIT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@:-]{0,127}")
_CONTAINER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_PACKAGE = re.compile(r"[a-z0-9][a-z0-9.+:~_-]{0,127}")
_PROCESS = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_TARGET = re.compile(r"(?:[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}@)?[A-Za-z0-9\[][A-Za-z0-9.:\]-]{0,252}")


def _is_ip(v: str) -> bool:
    try:
        ipaddress.ip_address(v)
        return True
    except ValueError:
        return False


def _is_net(v: str) -> bool:
    try:
        ipaddress.ip_network(v, strict=False)
        return True
    except ValueError:
        return False


def _words(pattern: re.Pattern, v: str, *, required: bool) -> bool:
    words = v.split()
    return (bool(words) or not required) and all(pattern.fullmatch(w) for w in words)


def arg_problem(action: str, arg: str | None) -> str | None:
    """Why `arg` is not acceptable for `action`, or None when it is."""
    a = "" if arg is None else str(arg)
    if action in ("block_ip", "ban_long"):
        return None if _is_ip(a) else f"{action} needs an IP address"
    if action == "block_subnet":
        return None if _is_net(a) else "block_subnet needs a network like 203.0.113.0/24"
    if action == "restart_service":
        return None if _UNIT.fullmatch(a) else "restart_service needs a plain unit name"
    if action == "restart_container":
        return None if _CONTAINER.fullmatch(a) else "restart_container needs a plain container name"
    if action == "kill_miner":
        return None if _words(_PROCESS, a, required=True) else "kill_miner needs plain process names"
    if action == "security_updates":
        return None if _words(_PACKAGE, a, required=False) else "security_updates takes plain package names"
    return None


def target_problem(target: str) -> str | None:
    """Why an SSH target is not a plain `user@host`, or None when it is."""
    t = (target or "").strip()
    if not t or t.startswith("-") or not _TARGET.fullmatch(t):
        return "the SSH target must be a plain user@host"
    return None
