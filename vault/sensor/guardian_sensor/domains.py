"""Domain-expiry (whois) and email-security (SPF/DMARC/DKIM) collectors.

Ported from the Nexus collector (~/.claude/nexus-agent/checks.py) — logic
copied faithfully, never imported from that directory.
"""
import datetime as dt
import re
import subprocess

from . import util


def domain_expiry(hosts, state):
    """Registrable-domain expiry via whois, once a day per domain."""
    out = dict((state.get("domains") or {}))
    now = dt.datetime.now().astimezone()
    for h in hosts:
        dom = ".".join(h.split(".")[-2:])
        cur = out.get(dom)
        if cur and cur.get("ts") and (now - dt.datetime.fromisoformat(cur["ts"])).total_seconds() < 86400: continue
        try:
            w = subprocess.run(["whois", dom], capture_output=True, text=True, timeout=20).stdout
            m = re.search(r"(?:Registry Expiry Date|Registrar Registration Expiration Date|Expiration Date|Expiry Date|expires?)\s*:\s*(\d{4}-\d{2}-\d{2})", w, re.I)
            exp = m.group(1) if m else None
            out[dom] = {"expires": exp, "days_left": (dt.date.fromisoformat(exp) - now.date()).days if exp else None, "ts": util.now_iso()}
        except Exception as e:
            out[dom] = {"expires": None, "days_left": None, "error": str(e)[:60], "ts": util.now_iso()}
    return out


def email_security(domains, state, advisory=frozenset()):
    """SPF / DMARC / DKIM presence per registrable domain, daily.

    Only the domains asked for come back: the daily cache in `state` is
    trimmed to `domains`, so a domain that has stopped being monitored
    drops out of the result -- and so out of the alerts -- on the very next
    cycle instead of riding the cache forever.

    `advisory` names domains that are CHECKED but must never raise
    anything: each result carries `advisory: True` and the hub reports it
    without alerting, deducting or nudging. A domain somebody else owns is
    still worth knowing about -- a spoofable one in the estate is a real
    risk -- but being told to fix records only its owner can set is nagging,
    and nagging is what gets a security product ignored.
    """
    wanted = set(domains)
    out = {d: v for d, v in (state.get("email_sec") or {}).items() if d in wanted}
    now = dt.datetime.now().astimezone()
    def txt(name):
        try:
            r = subprocess.run(["dig", "+short", "+time=3", "+tries=1", name, "TXT"], capture_output=True, text=True, timeout=10).stdout
            return [l.strip().strip('"').replace('" "', "") for l in r.splitlines() if l.strip()]
        except Exception: return []
    for dom in domains:
        cur = out.get(dom)
        if cur and cur.get("ts") and (now - dt.datetime.fromisoformat(cur["ts"])).total_seconds() < 86400: continue
        spf = next((x for x in txt(dom) if x.lower().startswith("v=spf1")), None)
        dm = next((x for x in txt("_dmarc." + dom) if x.lower().startswith("v=dmarc1")), None)
        pol = (re.search(r"p=(\w+)", dm or "") or [None, None])[1]
        dkim = next((sel for sel in ("default", "google", "selector1", "selector2", "k1", "mail", "dkim", "s1") if any("v=dkim1" in x.lower() or "k=rsa" in x.lower() for x in txt(f"{sel}._domainkey.{dom}"))), None)
        mx = bool(subprocess.run(["dig", "+short", "+time=3", dom, "MX"], capture_output=True, text=True, timeout=10).stdout.strip())
        out[dom] = {"spf": spf, "dmarc": dm, "dmarc_policy": pol, "dkim_selector": dkim, "has_mx": mx, "ts": util.now_iso()}
    # Stamped on every pass, not only on a fresh lookup: a domain moved in
    # or out of `advisory` has to change behaviour on the next scan, not
    # whenever its 24h cache happens to expire.
    for dom in out:
        out[dom]["advisory"] = dom in advisory
    return out
