"""tcpdump's keeper. Spec §1, §5.

Nothing else in the sensor starts, stops or reads tcpdump. It runs with
`-s <snaplen>` (headers plus the first bytes of payload, never bodies) and
`-G 60 -w <DATA_DIR>/cap/%s.pcap` (one file per minute, epoch-named so the
newest sorts last). At most MAX_FILES are kept: the ring is what bounds disk
on a saturated network, and a minute already rolled into flows has no reason
to stay.

`error` is a sentence for the hub, not a stack trace: "tcpdump not
installed" and "no packets on eth1 for 10 minutes" become `capture:*`
alerts on the human tier -- a person installs a package or checks a switch;
no fix Vault runs reaches either.
"""
from __future__ import annotations

import os, shutil, subprocess, time
from . import paths

MAX_FILES = 10
SILENT_AFTER_S = 600
_proc: subprocess.Popen | None = None
#: Which tool the running capture was started with, for the hub's report.
_tool = ""
_started_at = 0.0
_backoff = 5.0

def _dir() -> str:
    d = paths.p("cap"); os.makedirs(d, mode=0o700, exist_ok=True); return d

def _files() -> list[str]:
    d = _dir()
    return sorted(os.path.join(d, f) for f in os.listdir(d) if f.endswith(".pcap"))

def completed_files() -> list[str]:
    """Every minute file but the newest: tcpdump is still writing that one."""
    return _files()[:-1]

def remove(path: str) -> None:
    try: os.remove(path)
    except OSError: pass

def _trim() -> None:
    for old in _files()[:-MAX_FILES]:
        remove(old)

#: Item 20: "use Wireshark/tshark where it is present".
#:
#: tcpdump first, because it is the smaller install and is already on most
#: of the boxes a sensor runs on. tshark is Wireshark's command-line capture
#: and writes the same rotating pcaps, so `flows.py` reads its files without
#: knowing which produced them -- the only difference is the flag spelling.
#:
#: Order is preference, not fallback-on-failure: whichever is present is
#: used, and if neither is, the sensor says which two it looked for rather
#: than reporting "no traffic".
_TOOLS = ("tcpdump", "tshark")


def _argv(tool: str, iface: str, snaplen: int, out_pattern: str) -> list[str]:
    """The command line for one capture tool.

    Both write one file a minute into the ring and both exclude SSH: the
    sensor reaches its own targets over SSH, and capturing that is both
    noise and a recording of Vault's own management traffic.

    `%s` in tcpdump's `-w` is strftime's epoch-seconds, which is what names
    the ring's files. tshark has no such expansion, so it gets `-b duration`
    and appends its own suffix -- hence the different pattern.
    """
    if tool == "tshark":
        # `-F pcap` is load-bearing, not tidiness. tshark writes **pcapng**
        # by default, and `flows.py` reads classic libpcap: it checks four
        # magic values and returns silently on anything else. Without this
        # flag, a machine with tshark and no tcpdump would capture happily,
        # rotate files, parse none of them, and report "Vault is
        # watching" with zero flows -- the exact silent-absence failure this
        # whole pass exists to remove. `capture.py`'s own empty-file check
        # also assumes the 24-byte classic global header.
        return ["tshark", "-i", iface, "-s", str(snaplen), "-n", "-q",
                "-F", "pcap", "-b", "duration:60", "-w", out_pattern,
                "-f", "not port 22"]
    return ["tcpdump", "-i", iface, "-s", str(snaplen), "-n", "-U", "-G", "60",
            "-w", out_pattern, "not port 22"]


def available_tool() -> str:
    """Which capture tool this machine has, or "" for neither."""
    for tool in _TOOLS:
        if shutil.which(tool):
            return tool
    return ""


def _start(iface: str, snaplen: int, tool: str = "tcpdump") -> None:
    global _proc, _started_at, _tool
    pattern = os.path.join(_dir(), "%s.pcap" if tool == "tcpdump" else "cap.pcap")
    _proc = subprocess.Popen(
        _argv(tool, iface, snaplen, pattern),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _started_at = time.time()
    _tool = tool

def tick(cfg: dict, state: dict) -> dict:
    """Called once per scan. Ensures tcpdump is running when an interface is
    configured, restarts it with backoff if it died, trims the ring, and
    reports mode/error. Router mode is decided by the caller (runner) when
    there is no interface but the router integration has per-client bytes."""
    global _proc, _backoff
    cap = cfg.get("capture") or {}
    iface = (cap.get("interface") or "").strip()
    out = {"mode": "none", "error": "", "interface": iface, "files": 0, "tool": ""}
    if not iface:
        # Not an error and not silence: nobody has asked Vault to watch
        # traffic here. The hub must be able to tell that apart from "we are
        # watching and there is nothing to report" -- see the `off` mode.
        out["mode"] = "off"
        return out
    tool = available_tool()
    if not tool:
        out["error"] = "neither tcpdump nor tshark is installed on the sensor"
        return out
    out["tool"] = tool
    if _proc is None or _proc.poll() is not None:
        # A crash-restart should grow the backoff; the very first start of
        # the process should not. Both look the same *after* `_start()` runs,
        # because `_start()` immediately overwrites `_proc` with the freshly
        # spawned Popen -- so "was there a previous process" has to be
        # decided now, before calling it, or this branch is always true and
        # the backoff only ever grows.
        restarting = _proc is not None
        if restarting and time.time() - _started_at < _backoff:
            out["error"] = "tcpdump exited; retrying"
            return out
        try:
            _start(iface, int(cap.get("snaplen") or 128), tool)
            _backoff = min(_backoff * 2, 120.0) if restarting else 5.0
        except OSError as e:
            out["error"] = f"could not start {tool}: {e}"
            return out
    elif time.time() - _started_at > SILENT_AFTER_S:
        # A run that has stayed up this long has earned back the fast retry:
        # without this, a sensor stable for weeks that then sees one flap
        # retries at whatever backoff a much older, unrelated run of crashes
        # last climbed to, up to 120s, instead of the normal 5s.
        _backoff = 5.0
    _trim()
    files = _files()
    out["files"] = len(files)
    # `-G 60` rotates a fresh file every minute regardless of traffic, so the
    # newest file's mtime is always recent and can never signal silence on
    # its own. What can: a minute file with no packets is exactly tcpdump's
    # 24-byte pcap global header (magic, version, snaplen, linktype) and
    # nothing else. So "silent" means every file rotated within the last
    # SILENT_AFTER_S window is header-only -- not just the newest one, since
    # a single stale mtime proves nothing about whether packets are flowing
    # now.
    recent = [f for f in files if time.time() - os.path.getmtime(f) <= SILENT_AFTER_S]
    if (recent and all(os.path.getsize(f) <= 24 for f in recent)
            and time.time() - _started_at > SILENT_AFTER_S):
        out["error"] = f"no packets on {iface} for {SILENT_AFTER_S // 60} minutes"
    out["mode"] = "mirror"
    out["tool"] = _tool or tool
    return out
