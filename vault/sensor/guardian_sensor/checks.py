"""Config-defined extra probes (tcp/icmp/dns) + heartbeat dead-man checks.

Ported from the Nexus collector (~/.claude/nexus-agent/checks.py) — logic
copied faithfully, never imported from that directory.
"""
import concurrent.futures
import datetime as dt
import re
import socket
import subprocess
import time

from . import util


def check_one(c):
    """One config-defined probe: tcp (host:port), icmp (ping), dns (name -> expected A).
    Module-level so the runner can re-run a single failed check on its own."""
    t0 = time.time(); ok = False; detail = ""
    if str(c.get("target") or "").startswith("-"):
        # A target starting with "-" would be read as a ping/dig option.
        return {"name": c["name"], "type": c.get("type"), "target": c.get("target"), "port": c.get("port"), "ok": False, "latency_ms": 0, "detail": "refused: target is not a plain host"}
    if True:
        try:
            if c.get("type") == "tcp":
                with socket.create_connection((c["target"], int(c.get("port", 80))), timeout=float(c.get("timeout", 5))): ok = True
            elif c.get("type") == "icmp":
                r = subprocess.run(["ping", "-c", "2"] + util.PING_W(1500) + [c["target"]], capture_output=True, text=True, timeout=10); ok = r.returncode == 0
                m = re.search(r"= [\d.]+/([\d.]+)/", r.stdout); detail = f"{m.group(1)} ms" if m else ""
            elif c.get("type") == "dns":
                r = subprocess.run(["dig", "+short", "+time=3", "+tries=1", c["target"], c.get("record", "A")], capture_output=True, text=True, timeout=10); ans = r.stdout.split()
                ok = bool(ans) and (not c.get("expect") or c["expect"] in ans); detail = ",".join(ans[:3])
            else: detail = "unknown type"
        except Exception as e: detail = str(e)[:80]
        return {"name": c["name"], "type": c.get("type"), "target": c.get("target"), "port": c.get("port"), "ok": ok, "latency_ms": int((time.time() - t0) * 1000), "detail": detail}


def extra_checks(cfg):
    """All config-defined probes, in parallel."""
    with concurrent.futures.ThreadPoolExecutor(8) as ex: return list(ex.map(check_one, cfg.get("checks") or []))


def heartbeats_check(cfg, state):
    """Dead-man checks: jobs that must ping /hb/<name>/<token>; missing ping => alert."""
    hb = cfg.get("heartbeats") or {}; st = state.setdefault("heartbeats", {}); now = dt.datetime.now().astimezone(); out = {}
    import secrets as _s
    for name, c in hb.items():
        e = st.setdefault(name, {})
        if not e.get("token"): e["token"] = _s.token_urlsafe(10)
        every = int(c.get("every_min", 60)); last = e.get("last")
        late = not last or (now - dt.datetime.fromisoformat(last)).total_seconds() > every * 60 * 1.5
        out[name] = {"every_min": every, "last": last, "late": late, "token": e["token"], "note": c.get("note", "")}
    return out
