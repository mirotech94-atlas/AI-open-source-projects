"""Whether the machine Vault runs on is actually defending itself.

Vault has no agent on a customer's phones and TVs and cannot scan them
for viruses. What it CAN do is look at the machines it is actually on -- the
sensor's own host, and the servers it already reaches over SSH -- and say
whether the protections those machines ship with are switched on.

That is a deliberately smaller promise than "antivirus", and a more useful
one. Every one of these is off by accident far more often than a machine is
infected, each is a real weakening of the box, and each has a one-line fix
the owner can perform. None of it needs a signature database, a kernel
extension, or root.

The parsers are separated from the commands so they can be tested without a
Mac, a Linux box, or malware.
"""
from __future__ import annotations

import os
import subprocess

from . import util


def _run(cmd: list[str], timeout: float = 6.0) -> str:
    """Best effort. A tool that is missing, slow or refuses is not an
    incident -- it is an unknown, and `posture` reports unknowns as None
    rather than guessing that the protection is off."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "") + (r.stderr or "")
    except Exception:  # noqa: BLE001
        return ""


# --------------------------------------------------------------------------
# Parsers. Pure: text in, tri-state out (True on, False off, None unknown).
# --------------------------------------------------------------------------

def parse_sip(out: str) -> bool | None:
    o = out.lower()
    if "status: enabled" in o:
        return True
    if "status: disabled" in o:
        return False
    return None


def parse_gatekeeper(out: str) -> bool | None:
    o = out.lower()
    if "assessments enabled" in o:
        return True
    if "assessments disabled" in o:
        return False
    return None


def parse_filevault(out: str) -> bool | None:
    o = out.lower()
    if "filevault is on" in o:
        return True
    if "filevault is off" in o:
        return False
    return None


def parse_alf(out: str) -> bool | None:
    """macOS application firewall: `globalstate` is 0 off, 1 on, 2 on for
    essential services only."""
    s = (out or "").strip()
    if s in ("1", "2"):
        return True
    if s == "0":
        return False
    return None


def parse_ufw(out: str) -> bool | None:
    o = (out or "").lower()
    if "status: active" in o:
        return True
    if "status: inactive" in o:
        return False
    return None


def parse_clamav_version(out: str) -> str:
    """`ClamAV 1.0.1/27000/...` -> the version, or "" when absent."""
    s = (out or "").strip()
    return s.split("/")[0].strip() if s.lower().startswith("clamav") else ""


#: Directories a legitimate startup item essentially never lives in. A
#: launch item pointing into one of these is the single most common way
#: commodity macOS malware survives a reboot, and unlike "is this binary
#: signed" it is cheap to check and hard to argue with.
SUSPECT_PATH_HINTS = ("/tmp/", "/var/tmp/", "/private/tmp/", "/users/shared/",
                      "/.hidden", "/library/caches/")


def suspicious_launch_items(programs: dict) -> list:
    """Launch items whose EXECUTABLE lives in a hiding place.

    Takes {plist path: program path} -- the thing the item actually runs,
    not the file's text. That distinction is the whole check: the first
    version searched the raw plist for "/tmp" and immediately accused two of
    the owner's own agents, because a launch item routinely writes its
    StandardOutPath and StandardErrorPath to /tmp while running a perfectly
    ordinary binary. Flagging that is worse than not looking: it teaches the
    owner that this alert means nothing.
    """
    hits = []
    for item, program in (programs or {}).items():
        low = (program or "").lower()
        if not low:
            continue
        for hint in SUSPECT_PATH_HINTS:
            if low.startswith(hint) or f"/{hint.strip('/')}/" in low:
                hits.append({"item": item, "program": program,
                             "why": f"its executable lives in {hint.strip('/')}"})
                break
    return hits


def _launch_item_programs(limit: int = 400) -> dict:
    """{plist path: the program it runs}, read with plistlib so the answer
    is the executable rather than any string that happens to be in the
    file. Unreadable or malformed items are skipped, not guessed at."""
    import plistlib
    out = {}
    dirs = [os.path.expanduser("~/Library/LaunchAgents"),
            "/Library/LaunchAgents", "/Library/LaunchDaemons"]
    for d in dirs:
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for n in names:
            if not n.endswith(".plist") or len(out) >= limit:
                continue
            path = os.path.join(d, n)
            try:
                with open(path, "rb") as fh:
                    pl = plistlib.load(fh)
            except Exception:  # noqa: BLE001 -- binary, malformed or unreadable
                continue
            prog = pl.get("Program")
            if not prog:
                args = pl.get("ProgramArguments") or []
                prog = args[0] if args else ""
            if isinstance(prog, str) and prog:
                out[path] = prog
    return out


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------

def posture() -> dict:
    """What this machine's own defences look like right now.

    Every value is True (on), False (off) or None (could not tell). None is
    never reported as a problem: a check Vault could not run is not
    evidence that the protection is missing, and saying otherwise would
    teach the owner to ignore the panel.
    """
    rep: dict = {"platform": "mac" if util.IS_MAC else "linux", "checks": {}}
    c = rep["checks"]
    if util.IS_MAC:
        c["sip"] = parse_sip(_run(["csrutil", "status"]))
        c["gatekeeper"] = parse_gatekeeper(_run(["spctl", "--status"]))
        c["filevault"] = parse_filevault(_run(["fdesetup", "status"]))
        c["firewall"] = parse_alf(_run(
            ["defaults", "read", "/Library/Preferences/com.apple.alf", "globalstate"]))
        rep["suspicious_launch_items"] = suspicious_launch_items(_launch_item_programs())
    else:
        c["firewall"] = parse_ufw(_run(["ufw", "status"]))
        rep["clamav"] = parse_clamav_version(_run(["clamscan", "--version"]))
        c["malware_scanner"] = bool(rep["clamav"]) or None
    return rep


#: Human wording for each check, and what turning it back on actually buys.
#: Kept here rather than in `alerts` so the sensor and any future host agent
#: describe the same thing the same way.
LABELS = {
    "sip": ("System Integrity Protection", "stops even an admin process rewriting the system"),
    "gatekeeper": ("Gatekeeper", "refuses to launch unsigned or unnotarised apps"),
    "filevault": ("FileVault", "makes the disk unreadable if the machine is stolen"),
    "firewall": ("the firewall", "refuses unexpected inbound connections"),
    "malware_scanner": ("a malware scanner", "gives this host something to scan files with"),
}
