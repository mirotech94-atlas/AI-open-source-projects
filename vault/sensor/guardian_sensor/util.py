"""Small stdlib-only utilities shared across sensor collectors.

Ported from the Nexus collector (~/.claude/nexus-agent/checks.py) — logic
copied faithfully, never imported from that directory.
"""
import datetime as dt
import os
import ssl
import subprocess
import sys
import threading

from . import paths

CAFILE = next(
    (p for p in ["/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"] if os.path.exists(p)),
    None,
)
SSL_CTX = ssl.create_default_context(cafile=CAFILE)

IS_MAC = sys.platform == "darwin"


def now_iso():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def PING_W(ms):  # macOS -W is milliseconds, Linux -W is seconds
    return ["-W", str(ms)] if IS_MAC else ["-W", str(max(1, int(round(ms / 1000))))]


def load_env():
    env = {}
    p = paths.p(".env")
    if os.path.exists(p):
        for line in open(p):
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                env[k] = v
    return env


def ssh_run(h, cmd, timeout=120, on_line=None):
    """Run a shell command on a configured host (root, key auth). Returns (rc, output).

    `on_line(line)`, when given, is called for every stdout/stderr line as it
    arrives (streamed, not buffered) -- used by `fix.apply_fix`'s upgrade
    progress reporting. Without it this is exactly the old batch behaviour
    (one `subprocess.run`, output captured at the end)."""
    target = str(h.get("target") or "")
    # A target starting with "-" would be read as an ssh option
    # (-oProxyCommand=...), which runs commands on the sensor itself.
    if not target or target.startswith("-"):
        return -1, "refused: the SSH target is not a plain user@host"
    c = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=accept-new"]
    if h.get("key"):
        c += ["-i", os.path.expanduser(h["key"])]
    if on_line is None:
        try:
            r = subprocess.run(c + ["--", target, cmd], capture_output=True, text=True, timeout=timeout)
            return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()[-1500:]
        except subprocess.TimeoutExpired:
            return -1, "timeout"

    proc = subprocess.Popen(c + ["--", target, cmd], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1)
    # `for line in proc.stdout` blocks on read() -- a stalled child (a dpkg
    # prompt with nothing left to print) would otherwise hang forever no
    # matter what `timeout` says, since the loop only checks the clock
    # AFTER a line arrives. A real wall-clock kill via `threading.Timer`
    # covers the "no output at all" case too.
    timed_out = [False]

    def _kill():
        timed_out[0] = True
        proc.kill()

    timer = threading.Timer(timeout, _kill)
    timer.start()
    lines = []
    try:
        for line in proc.stdout:
            lines.append(line.rstrip("\n"))
            try:
                on_line(lines[-1])
            except Exception:
                pass
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
    finally:
        timer.cancel()
    if timed_out[0]:
        return -1, "timeout"
    rc = proc.returncode if proc.returncode is not None else -1
    return rc, "\n".join(lines).strip()[-1500:]


def run_for(cmd, secs):
    """Run a never-ending tool for a few seconds and return what it printed."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=secs)
        return r.stdout
    except subprocess.TimeoutExpired as e:
        o = e.stdout or b""
        return o.decode("utf-8", "ignore") if isinstance(o, bytes) else o
    except Exception:
        return ""


def cfg_host(cfg, name):
    """Look up an SSH host config by name. Returns the host dict or empty dict if not found."""
    return next((h for h in cfg["ssh_hosts"] if h["name"] == name), {})
