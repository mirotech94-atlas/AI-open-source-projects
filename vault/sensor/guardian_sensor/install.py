"""Install the sensor (and optionally the DNS filter) as a system service.

macOS -> per-user LaunchAgents com.guardian.sensor / com.guardian.dns.
Linux -> systemd units guardian-sensor.service / guardian-dns.service.

Both run the `guardian-sensor` console script that sits next to the
interpreter this module is running under, so a venv install needs no PATH
setup, and both keep the process alive across crashes and reboots.
"""
import os
import plistlib
import shutil
import subprocess
import sys

from . import config, paths

SENSOR_LABEL = "com.guardian.sensor"
DNS_LABEL = "com.guardian.dns"
SENSOR_UNIT = "guardian-sensor.service"
DNS_UNIT = "guardian-dns.service"
SYSTEMD_DIR = "/etc/systemd/system"


def env_file():
    """The unit's EnvironmentFile: the `.env` inside DATA_DIR, wherever that
    resolved to (GUARDIAN_DATA, /var/lib/guardian, ~/.guardian). That is the
    file `util.load_env()` actually reads, so the unit must not hardcode a path
    that may not be it."""
    return paths.p(".env")


def script_path():
    """The installed `guardian-sensor` console script (next to sys.executable)."""
    return os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "guardian-sensor")


def logs_dir():
    d = paths.p("logs")
    os.makedirs(d, exist_ok=True)
    return d


def _dns_enabled():
    try:
        return bool((config.load().get("dns") or {}).get("enabled"))
    except Exception:
        return False


def _run(cmd):
    """Run a service-manager command; return (ok, output)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return r.returncode == 0, ((r.stdout or "") + (r.stderr or "")).strip()
    except Exception as e:
        return False, str(e)[:200]


# ---------------- macOS ----------------
def _plist(label, subcommand, log_name):
    prog = script_path()
    plist = {
        "Label": label,
        "ProgramArguments": [prog, subcommand],
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": os.path.join(logs_dir(), log_name),
        "StandardErrorPath": os.path.join(logs_dir(), log_name),
        "WorkingDirectory": paths.DATA_DIR,
        "ProcessType": "Background",
        # A LaunchAgent gets 256 open files by default; the DNS resolver ran
        # out of them on 2026-09-29 and took the Mac's lookups down with it.
        "SoftResourceLimits": {"NumberOfFiles": 4096},
        "HardResourceLimits": {"NumberOfFiles": 8192},
    }
    env = {}
    if os.environ.get("GUARDIAN_DATA"):
        env["GUARDIAN_DATA"] = os.environ["GUARDIAN_DATA"]
    if env:
        plist["EnvironmentVariables"] = env
    return plist


def launchd():
    """Write and load the LaunchAgents. Returns a process exit code."""
    paths.ensure()
    agents = os.path.expanduser("~/Library/LaunchAgents")
    os.makedirs(agents, exist_ok=True)
    jobs = [(SENSOR_LABEL, "run", "sensor.log")]
    if _dns_enabled():
        jobs.append((DNS_LABEL, "dns", "dns.log"))
    prog = script_path()
    if not os.path.exists(prog):
        print(f"guardian-sensor: {prog} not found — install the package first (pip install .)")
        return 1
    rc = 0
    for label, sub, logname in jobs:
        target = os.path.join(agents, f"{label}.plist")
        with open(target, "wb") as f:
            plistlib.dump(_plist(label, sub, logname), f)
        print(f"wrote {target}")
        _run(["launchctl", "unload", target])          # ignore "not loaded"
        ok, out = _run(["launchctl", "load", "-w", target])
        if ok:
            print(f"loaded {label} (guardian-sensor {sub}, logs {os.path.join(logs_dir(), logname)})")
        else:
            rc = 1
            print(f"failed to load {label}: {out[:200]}")
    if DNS_LABEL not in [j[0] for j in jobs]:
        print("dns.enabled is false in config.json — skipped the DNS filter agent")
    return rc


# ---------------- Linux ----------------
def _unit(desc, subcommand):
    return f"""[Unit]
Description={desc}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
ExecStart={script_path()} {subcommand}
Restart=always
RestartSec=10
EnvironmentFile=-{env_file()}
WorkingDirectory={paths.DATA_DIR}
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""


def systemd():
    """Write and enable the systemd units. Returns a process exit code."""
    # tcpdump is the one system package the sensor needs beyond Python: flows
    # (spec 2026-09-20-traffic-flows) capture with it when a mirror port is
    # configured. Absent, capture reports mode "none" -- it never breaks install.
    if shutil.which("apt-get") and not shutil.which("tcpdump"):
        _run(["apt-get", "install", "-y", "tcpdump"])
    paths.ensure()
    units = [(SENSOR_UNIT, "Vault sensor (network and server collector)", "run")]
    if _dns_enabled():
        units.append((DNS_UNIT, "Vault DNS filter", "dns"))
    prog = script_path()
    if not os.path.exists(prog):
        print(f"guardian-sensor: {prog} not found — install the package first (pip install .)")
        return 1
    if not os.access(SYSTEMD_DIR, os.W_OK):
        print(f"guardian-sensor: {SYSTEMD_DIR} is not writable — run this with sudo")
        return 1
    rc = 0
    for name, desc, sub in units:
        target = os.path.join(SYSTEMD_DIR, name)
        with open(target, "w") as f:
            f.write(_unit(desc, sub))
        print(f"wrote {target}")
    ok, out = _run(["systemctl", "daemon-reload"])
    if not ok:
        print(f"systemctl daemon-reload failed: {out[:200]}")
        rc = 1
    for name, _desc, _sub in units:
        ok, out = _run(["systemctl", "enable", "--now", name])
        if ok:
            print(f"enabled and started {name}")
        else:
            rc = 1
            print(f"failed to enable {name}: {out[:200]}")
    if DNS_UNIT not in [u[0] for u in units]:
        print("dns.enabled is false in config.json — skipped the DNS filter unit")
    return rc
