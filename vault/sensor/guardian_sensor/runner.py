"""The collector loop: one scan cycle, then the forever loop around it.

`run_once` is a faithful port of the Nexus collector's `main()` body
(~/.claude/nexus-agent/checks.py, from the `hub_pull` call through the final
writes) with the collectors mapped onto this package's modules, `HERE`
replaced by `paths.p`, the hub sync replaced by `sync.HubClient`, and every
user-facing string rebranded. Logic copied faithfully, never imported from
that directory.

File shapes under DATA_DIR are exactly the Nexus ones — `last.json`,
`state.json`, `history.jsonl`, `events.jsonl`, `remediation.jsonl` — because
the hub's ingest was built on them.

---- the queue's fast path (Task: sensor-queue-latency) ----

A dashboard-queued job used to be picked up only by `run_once`'s own
`client.pull()`, and executed only near the end of that same scan -- so a
job could wait for the next scan to even START, then wait again for the
whole scan to FINISH. Fixed by giving the queue its own short-cadence path,
entirely separate from the scan:

- `sync.HubClient.poll_queue()` fetches ONLY `GET /queue` (split out of
  `pull()`, which no longer touches it at all).
- `poll_queue_once()` fetches one batch and runs it through the existing
  `run_queue()` against a throwaway `cfg`/`res` it builds itself.
- `queue_poll_loop()` calls that on a repeating ~10s timer, in a background
  thread, for the life of the process.

Why a background THREAD, specifically, and why it is safe:

`run_once` is one long synchronous call -- collectors, a LAN sweep, maybe an
SSH-driven apt upgrade -- that can run for minutes. Nothing in a single
thread gets a chance to check the queue while that call is in flight, and
there is no way to interrupt it mid-collector without touching collector
code this task must not touch. A second thread is the only way to keep
polling the hub *while* a scan is running, which is exactly requirement #4
("a long scan must not block queued work").

What could that thread race with the scan on?

- `state` / `cfg` / `res`: NOT shared. `main()` already reloads `cfg` and
  `state` from disk fresh every scan iteration (see the bottom of this
  file), and `poll_queue_once()` does the same (`config.load()`) plus a
  throwaway `res = {"ts": ...}` of its own. The two threads never hold a
  reference to the same dict, so there is no Python-level mutable state to
  corrupt -- nothing to lock there.
- The on-disk `state.json` / `config.json`: the poll thread never writes
  either file (queue actions only ever append to `remediation.jsonl`, via
  `fix.apply_fix`), so there is no write/write race with `run_once`'s own
  `_dump("state.json", ...)`.
- The single-instance `.run.lock`: still acquired exactly once, for the
  process's whole life, in `main()` below -- both threads run inside that
  one already-locked process, so `fix.apply_fix` keeps calling itself with
  `lock=False` (unchanged) and never re-enters `flock`.
- The remote host and `remediation.jsonl`, when a queued fix and the scan's
  own auto-remediation (`fix.remediate`) could otherwise run a command
  against the SAME host at the same moment: serialised per-host through
  `fix._host_lock`, which covers only the actual command execution (never
  the long `flock` wait, which nothing here still uses with `lock=True`) --
  deliberately per-host rather than one global lock, so a job on a
  different host is never stuck behind a 25-minute `full_upgrade` -- see
  fix.py.
- Overlapping ITSELF: `queue_poll_loop` is one `while` loop that always
  finishes one tick (fetch + run every item in that batch + report each
  result) before it waits for the next one. There is no timer callback that
  could fire again while a previous tick is still busy, so "the poll comes
  round again while the first is still running" (requirement #4's second
  half) cannot happen by construction.

This makes the overlap requirement (#5) a non-issue by design rather than
something patched over with a lock on `state`/`cfg`/`res` themselves --
those objects are simply never shared.
"""
import concurrent.futures
import datetime as dt
import json
import os
import random
import re
import secrets
import sys
import threading
import time

from . import probe, VERSION, alerts, backups, cameras, checks, config, dnsstats, domains, fix, home, hosts, lan, nvr
from . import notify as notifier
from . import endpoint, paths, sites, sync, util

SLEEP_SECONDS = 300          # unless --once, a cycle every 5 minutes, forever
DEFAULT_PUBLIC_URL = ""      # notification links fall back to the hub's own URL

# ---- queue poll cadence ----
# 10s base interval: comfortably inside the 5-15s "starts within seconds"
# target even after network round-trip and the sensor's own dispatch, while
# staying far above "tight loop." The request itself is one indexed SQLite
# lookup on the hub (`WHERE tenant_id=? AND status='queued'`), sub-millisecond
# regardless of tenant count; the cost that matters is HTTP request volume,
# not query time. At 1000 enrolled sensors that is ~100 req/s of the cheapest
# possible read against a single FastAPI/SQLite process -- a lot in absolute
# terms, but each one is a no-op most of the time (empty queue -> nothing
# else happens), and it is what buys 5-15s job latency instead of the
# multi-minute wait this task exists to fix. QUEUE_POLL_JITTER staggers many
# sensors so they do not all land on the hub in the same instant.
QUEUE_POLL_SECONDS = 10
QUEUE_POLL_JITTER = 3
# Backoff while the hub is unreachable (requirement #6): double the interval
# on every failed poll, capped at 2 minutes, and reset to the base cadence
# the moment a poll succeeds again -- the same "retry, then back off" shape
# `sync.HubClient._try` already uses for a single request, just carried
# across ticks instead of within one.
QUEUE_POLL_MAX_BACKOFF = 120

# alerts that page immediately; everything else waits for the daily digest
IMMEDIATE = ("lan:new:", "reboot:", "sec:login:", "sec:attacker:", "lan:unverified", "lan:port:",
             "lan:offline:", "hb:", "domain:", "sec:drift:", "wan:path",
             # Task 69: a camera that went quiet, a device that is not on this
             # network's address range, and something answering like a second
             # router all page now rather than waiting for the daily digest.
             "lan:camera-gone:", "lan:offsubnet:", "lan:second-router:")


def log(msg):
    # stderr, so `guardian-sensor run --once` keeps stdout a clean JSON document
    print(f"{util.now_iso()} runner: {msg}", file=sys.stderr, flush=True)


def _flag(args, name, default=False):
    return bool(getattr(args, name, default)) if args is not None else default


def _site_host(s):
    return s.get("host") or re.sub(r"^https?://([^/]+).*", r"\1", s["url"])


def _append_jsonl(name, rows):
    with open(paths.p(name), "a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _append_flows(rows):
    d = paths.p("flows"); os.makedirs(d, mode=0o700, exist_ok=True)
    with open(os.path.join(d, "flows-" + dt.date.today().isoformat() + ".jsonl"), "a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _dump(name, obj):
    with open(paths.p(name), "w") as f:
        json.dump(obj, f, indent=2)


def load_state():
    p = paths.p("state.json")
    if not os.path.exists(p):
        return {}
    try:
        with open(p) as f:
            return json.load(f)
    except Exception as e:
        log(f"state.json unreadable ({str(e)[:80]}) — starting from an empty state")
        return {}


# ---------------- hub work queue ----------------
def _in_window(now, start, end):
    """True when `now` is inside an ISO [start, end] reboot window."""
    try:
        a = dt.datetime.fromisoformat(start)
        b = dt.datetime.fromisoformat(end)
    except Exception:
        return False
    if a.tzinfo is None:
        a = a.replace(tzinfo=now.tzinfo)
    if b.tzinfo is None:
        b = b.replace(tzinfo=now.tzinfo)
    return a <= now <= b


def _wifi_stations_if_needed(res, client):
    """The gateway's per-station Wi-Fi table, but only when it would change
    what Vault says.

    Returns [] when nothing is flapping, when there is no router credential,
    or when the read fails -- all of which mean "no extra evidence", never
    "the device is fine". `alerts.build_alerts` treats an empty list as
    unknown and falls back to the old, vaguer wording.
    """
    try:
        flapping = any(d.get("flapping")
                       for net in (res.get("lan") or {}).get("networks", {}).values()
                       for d in (net.get("known_macs") or {}).values())
        if not flapping:
            flapping = any(d.get("flapping")
                           for d in ((res.get("lan") or {}).get("known_macs") or {}).values())
        if not flapping or client is None:
            return []
        rj = client._try("GET", "/router") or {}
        rc = rj.get("router") if isinstance(rj, dict) else None
        if not rc or not rc.get("base_url") or not rc.get("password"):
            return []
        if (rc.get("vendor") or "fios").lower() != "fios":
            return []
        from . import router_fios
        si = router_fios.station_info(rc["base_url"], rc["password"],
                                      username=rc.get("username") or "")
        return si.get("stations") or []
    except Exception:  # noqa: BLE001 -- evidence is optional; a scan is not
        return []


def run_queue(queued, cfg, res, client):
    """Execute the items the dashboard queued and report each one back.

    The hub flips every row it hands over to "running", so each item gets
    exactly one `.result()` — refusals included — or the row is stuck forever.
    Returns True when a speed test was requested (the history line needs it).
    """
    speed_requested = False
    for qd in queued or []:
        b = qd.get("body") or {}
        p = qd.get("path")
        qid = qd.get("id")
        ok, out, detail = False, "", None
        try:
            if p == "/api/fix":
                action = b.get("action")
                if action not in fix.FIX_ACTIONS:
                    out = f"refused: {action!r} is not a known fix action"
                else:
                    # Report live progress for the long-running apt actions
                    # so the dashboard's upgrade progress bar has something
                    # to show -- client.progress() throttles itself to one
                    # POST every 3s, so this is safe to call per-line.
                    fix_kwargs = {}
                    if client is not None and qid is not None and action in ("security_updates", "full_upgrade"):
                        def _prog_cb(done, total, line, _qid=qid):
                            client.progress(_qid, done, total, line)
                        fix_kwargs["progress"] = _prog_cb
                    # lock=False: this process already holds the run lock.
                    r = fix.apply_fix(cfg, b.get("host"), action, b.get("arg"),
                                      by=f"hub:{qd.get('who')}", lock=False, **fix_kwargs)
                    res.setdefault("fixes", []).append(r)
                    ok, out, detail = bool(r.get("ok")), r.get("out") or "", r
            elif p == "/api/speedtest":
                sp = home.speed_test()
                res["speed"] = sp
                speed_requested = True
                ok, out, detail = not sp.get("error"), sp.get("error") or "ok", sp
            elif p == "/api/run":
                # Executed by the fast queue poller, not inside a scan (see
                # this file's module docstring), so this cannot honestly say
                # "scan ran" any more -- it wakes `main()`'s sleep early
                # (`poll_queue_once`'s `run_now_event`) so a real scan starts
                # within seconds, but that scan is a separate event from
                # this acknowledgement.
                ok, out = True, "scan requested"
            elif p == "/api/reboot-plan":
                now = dt.datetime.now().astimezone()
                if _in_window(now, b.get("window_start"), b.get("window_end")):
                    r = fix.apply_fix(cfg, b.get("host"), "reboot", by=f"hub:{qd.get('who')}", lock=False)
                    res.setdefault("fixes", []).append(r)
                    ok, out, detail = bool(r.get("ok")), r.get("out") or "", r
                else:
                    ok, out = False, "outside window"
            elif p in ("/api/vuln-scan", "/api/deep-dive"):
                # The manual, Nessus-shaped scan (vulnscan.py). deep-dive is
                # the same thing pointed at one device on the quick profile --
                # the chat tool has queued it for weeks and this branch is
                # what finally answers it instead of refusing.
                from . import vulnscan
                vb = dict(b)
                if p == "/api/deep-dive":
                    vb.setdefault("profile", "quick")
                r = vulnscan.run(cfg, vb)
                n = r.get("summary") or {}
                ok = "error" not in r
                out = (f"{r.get('kind')} {r.get('target')}: {len(r.get('findings') or [])} finding(s) — "
                       f"{n.get('critical', 0)} critical, {n.get('high', 0)} high, {n.get('medium', 0)} medium, "
                       f"{n.get('low', 0)} low; score {r.get('score')}")
                detail = r
            elif p == "/api/router/test":
                # The site gateway credential Test: fetch the router login from
                # the hub (the ONLY place it lives, delivered over the sensor's
                # authenticated channel) and log in + straight back out. No
                # mutation, and the credential never leaves this branch -- only
                # a plain ok/why message is reported back.
                from . import router_fios
                rj = client._try("GET", "/router") if client is not None else {}
                rc = (rj or {}).get("router") if isinstance(rj, dict) else None
                if not rc or not rc.get("base_url") or not rc.get("password"):
                    ok, out = False, "no router credential is configured"
                elif (rc.get("vendor") or "fios").lower() != "fios":
                    ok, out = False, f"router vendor {rc.get('vendor')!r} is not supported"
                else:
                    tr = router_fios.test_credential(
                        rc["base_url"], rc["password"],
                        username=rc.get("username") or "")
                    ok = bool(tr.get("ok"))
                    out = "credential accepted (logged in and out, nothing changed)" if ok \
                          else (tr.get("error") or "login failed")
            elif p == "/api/router/disable-remote-admin":
                # The confirmed, mutating router action: log in and turn remote
                # administration off. Gated hub-side behind a tier-3, single-use
                # confirmation (only an admin who just clicked through the
                # summary reaches here). Credential comes from GET /router, same
                # as the Test; the password never leaves this branch.
                from . import router_fios
                rj = client._try("GET", "/router") if client is not None else {}
                rc = (rj or {}).get("router") if isinstance(rj, dict) else None
                if not rc or not rc.get("base_url") or not rc.get("password"):
                    ok, out = False, "no router credential is configured"
                elif (rc.get("vendor") or "fios").lower() != "fios":
                    ok, out = False, f"router vendor {rc.get('vendor')!r} is not supported"
                else:
                    dr = router_fios.disable_remote_admin(
                        rc["base_url"], rc["password"],
                        username=rc.get("username") or "")
                    ok = bool(dr.get("ok"))
                    out = dr.get("out") if ok else (dr.get("error") or "failed to apply")
                    detail = dr
            elif p in ("/api/router/read-dns", "/api/router/set-dns", "/api/router/restore-dns"):
                # Miro's call: Vault points the router's DNS at itself, so
                # nobody has to open 192.168.1.1 and find the right box.
                #
                # `read-dns` is READ-ONLY and answers the one question that
                # decides whether DNS filtering works at all -- Vault can
                # only filter a query that reaches it, and until now the page
                # asked the customer to go and look.
                #
                # The two writes are gated hub-side exactly like
                # disable-remote-admin: a Test that has already passed, then a
                # tier-3 single-use confirmation. Same credential path; the
                # password never leaves this branch.
                from . import router_fios
                rj = client._try("GET", "/router") if client is not None else {}
                rc = (rj or {}).get("router") if isinstance(rj, dict) else None
                if not rc or not rc.get("base_url") or not rc.get("password"):
                    ok, out = False, "no router credential is configured"
                elif (rc.get("vendor") or "fios").lower() != "fios":
                    ok, out = False, f"router vendor {rc.get('vendor')!r} is not supported"
                elif p == "/api/router/read-dns":
                    dr = router_fios.read_dns(rc["base_url"], rc["password"],
                                              username=rc.get("username") or "")
                    ok = bool(dr.get("ok"))
                    out = (", ".join(dr.get("servers") or []) or "none set") if ok \
                          else (dr.get("error") or "could not read")
                    detail = dr
                elif p == "/api/router/set-dns":
                    dr = router_fios.set_dns(
                        rc["base_url"], rc["password"], str(b.get("server") or ""),
                        username=rc.get("username") or "",
                        both_boxes=bool(b.get("both_boxes")))
                    ok = bool(dr.get("ok"))
                    out = dr.get("out") if ok else (dr.get("error") or "failed to apply")
                    detail = dr
                else:
                    dr = router_fios.restore_dns(
                        rc["base_url"], rc["password"], dict(b.get("previous") or {}),
                        username=rc.get("username") or "")
                    ok = bool(dr.get("ok"))
                    out = dr.get("out") if ok else (dr.get("error") or "failed to restore")
                    detail = dr
            elif p == "/api/router/station":
                # READ-ONLY: ask the gateway what it knows about one station's
                # Wi-Fi. This is the only source of signal strength and
                # association uptime -- Vault's own presence detection is a
                # ping sweep plus ARP, and a phone in power save ignores pings
                # and looks offline while still associated. Same credential
                # path as the Test; nothing on the router is changed.
                from . import router_fios
                rj = client._try("GET", "/router") if client is not None else {}
                rc = (rj or {}).get("router") if isinstance(rj, dict) else None
                want_mac = (b or {}).get("mac") or ""
                if not rc or not rc.get("base_url") or not rc.get("password"):
                    ok, out = False, "no router credential is configured"
                elif (rc.get("vendor") or "fios").lower() != "fios":
                    ok, out = False, f"router vendor {rc.get('vendor')!r} is not supported"
                else:
                    si = router_fios.station_info(
                        rc["base_url"], rc["password"],
                        username=rc.get("username") or "")
                    ok = bool(si.get("ok"))
                    sig = (router_fios.read_signal(si.get("stations") or [], want_mac)
                           if want_mac else {})
                    if not ok:
                        out = si.get("error") or "could not read the station table"
                    elif want_mac:
                        # The verdict first, because it is the answer to the
                        # question that prompted the probe.
                        out = router_fios.explain_presence(sig)
                    else:
                        out = f"{len(si.get('stations') or [])} stations on the gateway"
                    detail = {"station": sig, "stations": si.get("stations"),
                              "nodes": si.get("nodes"), "tables": si.get("tables"),
                              "error": si.get("error")}
            else:
                out = f"refused: unsupported queue path {p!r}"
        except Exception as e:
            ok, out = False, str(e)[:300]
        if client is not None and qid is not None:
            client.result(qid, ok, out, detail if isinstance(detail, dict) else None)
    return speed_requested


def poll_queue_once(client=None, cfg=None, run_now_event=None):
    """Fetch and run ONE batch of dashboard-queued work, independent of any
    scan -- the fast path that fixes the "queued 3.5 minutes ago, nothing
    happened yet" bug. Called on a ~10s timer by `queue_poll_loop`, and once
    more from `main()` right after a `--once` cycle so the manual/debug run
    still drains the queue the way it always has.

    Builds its own `cfg` (a fresh `config.load()`) and a throwaway `res`
    dict -- never the scan's -- so nothing here is shared, mutable state
    with a concurrently-running `run_once` (see this file's module
    docstring). Returns how many items it ran (0 if none were queued, or no
    hub is enrolled).
    """
    client = client or sync.client_from_disk()
    if client is None:
        return 0
    queued = client.poll_queue()
    if not queued:
        return 0
    if run_now_event is not None and any(qd.get("path") == "/api/run" for qd in queued):
        # Wake `main()`'s sleep early so "run now" actually starts a scan
        # soon, rather than just posting a truthful-sounding acknowledgement
        # (see the "/api/run" branch above) with no scan behind it.
        run_now_event.set()
    cfg = cfg if cfg is not None else config.load()
    res = {"ts": util.now_iso()}
    run_queue(queued, cfg, res, client)
    return len(queued)


def queue_poll_loop(stop_event, run_now_event):
    """Background thread body: `poll_queue_once()` on a repeating cadence
    for the life of the process. See this file's module docstring for why a
    thread is safe here and what it does and does not share with the scan.

    Never lets an exception kill the thread -- a poller that silently stops
    taking jobs after one bad response is worse than one that logs and
    keeps going. A failure backs the interval off exponentially
    (QUEUE_POLL_SECONDS -> ... -> QUEUE_POLL_MAX_BACKOFF), matching the
    "retry, then back off" shape `sync.HubClient._try` already logs for a
    single request; a success resets it immediately.
    """
    interval = QUEUE_POLL_SECONDS
    while not stop_event.is_set():
        try:
            poll_queue_once(run_now_event=run_now_event)
            interval = QUEUE_POLL_SECONDS
        except Exception as e:
            interval = min(interval * 2, QUEUE_POLL_MAX_BACKOFF)
            log(f"queue poll failed: {type(e).__name__}: {str(e)[:120]} — backing off to {interval:.0f}s")
        if stop_event.wait(interval + random.uniform(0, QUEUE_POLL_JITTER)):
            return


# ---------------- networks ----------------
#: The per-network keys of `state["lan"]["networks"][<slug>]`.
NET_STATE_KEYS = ("known_macs", "last_live_macs", "last_live_count", "miss_count",
                  "transitions", "gw_mac", "home_ssid", "twin_channels", "presence",
                  "cameras", "wan_ip")
#: The subset the legacy flat `state["lan"]` keys alias, BY REFERENCE, at the
#: primary network's slice — so `state.json` still shows the pre-Task-52 shape
#: for a hub on older code and for Task 16's ingest device loop.
ALIASED_KEYS = ("known_macs", "last_live_macs", "miss_count", "transitions")


def _migrate_lan_state(state, primary_slug):
    """Move the flat `state["lan"]` baseline into
    `state["lan"]["networks"][<primary slug>]` and alias the legacy keys back
    at it by reference. Idempotent; recorded as `state["lan"]["schema"] = 2`."""
    ls = state.setdefault("lan", {})
    nets = ls.setdefault("networks", {})
    pn = nets.setdefault(primary_slug, {})
    if ls.get("schema") != 2:
        for k in NET_STATE_KEYS:
            if k in ls and k not in pn:
                pn[k] = ls[k]
        for k in ("gw_mac", "home_ssid", "twin_channels"):   # these live at the top level
            if k in state and k not in pn:
                pn[k] = state[k]
        ls["schema"] = 2
    for k in ALIASED_KEYS:
        if k in pn:
            ls[k] = pn[k]
        elif k in ls:
            pn[k] = ls[k]
    return pn


def net_state(state, net):
    """This network's slice of `state["lan"]["networks"]`, created on demand."""
    return state.setdefault("lan", {}).setdefault("networks", {}).setdefault(net["slug"], {})


def _prune_stale_networks(state, cfg):
    """Drop `state["lan"]["networks"][<slug>]` slices whose slug no longer
    appears in the current config (e.g. a network was renamed, which slugs
    to something new, or removed outright) — otherwise a stale slice sits in
    state.json forever."""
    nets = state.get("lan", {}).get("networks")
    if not isinstance(nets, dict):
        return
    live = {n["slug"] for n in config.networks_of(cfg)}
    for slug in list(nets):
        if slug not in live:
            nets.pop(slug, None)


def _set_net(state, net, key, value):
    """Write one per-network state value, keeping the primary's legacy alias in
    step — every rebind must go through here or `state["lan"]["known_macs"]`
    silently drifts away from `networks[<primary>]["known_macs"]`."""
    ns = net_state(state, net)
    ns[key] = value
    if net.get("primary") and key in ALIASED_KEYS:
        state["lan"][key] = value
    return value


def scan_networks(cfg, state, no_lan):
    """One sweep per enabled configured network, sequentially.

    Each entry is `lan.lan_sweep(net)` plus the network's identity and
    `status: "active"`, or — when this sensor holds no address on that subnet —
    an entry with `status: "unreachable"` and a `reason`, and NO `devices`
    map. Reporting an empty sweep instead would read as "every device left".
    """
    if no_lan:
        return []
    nets = [n for n in config.networks_of(cfg) if n.get("enabled", True)]
    if not nets:
        return []
    subs = {}
    try:
        subs = lan.local_subnets()
    except Exception as e:
        log(f"local_subnets failed: {type(e).__name__}: {str(e)[:100]}")
    known_prefixes = sorted({n.get("subnet_prefix") for n in nets if n.get("subnet_prefix")} | set(subs or {}))
    out = []
    for net in nets:
        meta = {"name": net["name"], "slug": net["slug"], "kind": net["kind"],
                "subnet_prefix": net["subnet_prefix"], "expected_gateway": net["expected_gateway"],
                "expected_wan_ip": net.get("expected_wan_ip") or "",
                "primary": net["primary"], "watch_ips_labels": dict(net.get("watch_ips") or {})}
        if not lan.reachable(net, subs):
            out.append({**meta, "status": "unreachable", "reason": lan.unreachable_reason(net)})
            continue
        try:
            # Bonjour only answers on the sensor's own segment and costs ~12s:
            # only the primary sweep pays for it (`mdns`), which is how N
            # sequential sweeps stay inside the old single-network budget.
            # `prefixes`: every subnet this sensor knows about (configured or
            # held on an interface), so `lan.offsubnet` can tell an address
            # that belongs to no network of ours from one that simply belongs
            # to a different one of ours.
            sweep = lan.lan_sweep({**net, "mdns": net["primary"], "prefixes": known_prefixes})
        except Exception as e:
            out.append({**meta, "status": "unreachable", "reason": f"sweep failed: {type(e).__name__}: {str(e)[:80]}"})
            continue
        # Roaming guard: the same subnet_prefix can exist on an unrelated
        # network (e.g. another 192.168.1.0/24). Once this network has a
        # learned gateway MAC, a sweep whose gateway MAC does NOT match it
        # is a DIFFERENT physical network wearing the same address range —
        # treat it as unreachable rather than adopting it as a
        # gateway-MAC-changed CRIT plus a `lan:new` flood plus permanent
        # baseline pollution. A network with no stored gw_mac yet learns it
        # on its first successful sweep (nothing to compare against). The
        # guard fires ONLY when the sweep actually reports a gateway MAC
        # that differs from the stored one — a sweep where the real gateway
        # simply missed one ARP round (`gw_mac` is None) is a transient
        # ARP miss, not a roam: the network stays active for that cycle and
        # the stored gw_mac is left untouched (never overwritten with None).
        stored_gw = net_state(state, net).get("gw_mac")
        swept_gw = sweep.get("gw_mac")
        if stored_gw and swept_gw and swept_gw != stored_gw:
            out.append({**meta, "status": "unreachable", "reason": "gateway MAC does not match this network"})
            continue
        out.append({**sweep, **meta, "status": "active"})
    return out


def _device_pass(res, net, state):
    """The LAN port scan, presence state machine, flapping and risk block for
    ONE network, against that network's own state slice."""
    nstate = net_state(state, net)
    if not nstate.get("known_macs"):
        return
    known = nstate["known_macs"]
    lanr = net
    pev = []
    try:
        live_ips = [ip for ip, m in lanr["devices"].items() if m]
        scan = lan.lan_ports(live_ips)
        for ip, ports in scan.items():
            m = lanr["devices"].get(ip)
            if not m or m not in known:
                continue
            d = known[m]
            had = d.get("ports") or {}
            for pt in ports:
                if str(pt) not in had:
                    had[str(pt)] = res["ts"]
                    pev.append({"ts": res["ts"], "type": "port_open", "mac": m, "ip": ip, "port": pt,
                                "label": d.get("label") or d.get("name") or d.get("vendor"), "trusted": bool(d.get("trusted"))})
            for pt in list(had):
                if int(pt) not in ports:
                    had.pop(pt)
                    pev.append({"ts": res["ts"], "type": "port_closed", "mac": m, "ip": ip, "port": int(pt),
                                "label": d.get("label") or d.get("name") or d.get("vendor"), "trusted": bool(d.get("trusted"))})
            d["ports"] = had
    except Exception as e:
        lanr["port_scan_error"] = str(e)[:120]
    lanr["port_events"] = pev
    now_live = {m for m in lanr["devices"].values() if m}
    trans = nstate.setdefault("transitions", {})
    prev_live = set(nstate.get("last_live_macs") or [])
    cutoff = (dt.datetime.now().astimezone() - dt.timedelta(hours=1)).isoformat(timespec="seconds")
    for m in (now_live ^ prev_live):
        if m in known:
            trans[m] = [x for x in trans.get(m, []) if x >= cutoff] + [res["ts"]]
    for m in list(trans):
        trans[m] = [x for x in trans[m] if x >= cutoff]
        if not trans[m]:
            trans.pop(m)
    unv = {d["mac"] for d in (res.get("home") or {}).get("unverified") or []}
    base_fs = min((d.get("first_seen") or "9" for d in known.values()), default=None)
    # the machine running the sensor is obviously the owner's: trust it so
    # its own SMB / screen-sharing ports never alert
    try:
        my_ip = lan.my_lan_ip()
        my_mac = lanr["devices"].get(my_ip)
        if my_mac and my_mac in known and not known[my_mac].get("trusted"):
            known[my_mac].update(trusted=True, label=known[my_mac].get("label") or ("This Mac (Vault)" if util.IS_MAC else "Vault sensor"))
    except Exception:
        pass
    for m, d in known.items():
        flapping = len(trans.get(m, [])) >= 4
        d["flapping"] = flapping
        d["presence"] = lan.presence_state(d, m in now_live, res["ts"], flapping, base_fs)
        d["risk"] = lan.risk_score(d, m in unv)
    lanr["presence"] = {s: sum(1 for d in known.values() if d.get("presence") == s)
                        for s in ("NEW", "ONLINE", "OFFLINE", "SLEEPING", "FLAPPING", "ARCHIVED")}
    # The camera inventory for this network. Runs after the port pass, because
    # RTSP / Dahua / DVR ports are half the evidence, and it stamps
    # `kind: "camera"` onto each camera's `known_macs` entry so the hub files
    # the device row as a camera without re-deriving anything.
    try:
        lanr["cameras"] = cameras.census(known, now_live)
    except Exception as e:
        lanr["camera_error"] = f"{type(e).__name__}: {str(e)[:100]}"


def _events_pass(res, net, state):
    """Join/leave (+ port) events for ONE network. Every line carries
    `"net": <slug>` so the hub can attribute it without guessing from the ip."""
    nstate = net_state(state, net)
    events = []
    prev_live = set(nstate.get("last_live_macs") or [])
    now_live = {m for m in net["devices"].values() if m}
    known = nstate.get("known_macs") or {}
    if nstate.get("known_macs") is not None:
        for m in sorted(now_live - prev_live):
            d = known.get(m, {})
            events.append({"ts": res["ts"], "type": "join", "mac": m,
                           "ip": next((ip for ip, mm in net["devices"].items() if mm == m), None),
                           "label": d.get("label") or d.get("vendor")})
        # leave = missed twice in a row (debounce flaky sweeps)
        miss = nstate.get("miss_count") or {}
        for m in sorted(prev_live - now_live):
            miss[m] = miss.get(m, 0) + 1
            if miss[m] == 2:
                d = known.get(m, {})
                events.append({"ts": res["ts"], "type": "leave", "mac": m, "ip": d.get("ip"),
                               "label": d.get("label") or d.get("vendor")})
        for m in now_live:
            miss.pop(m, None)
        _set_net(state, net, "miss_count", miss)
        prev_live = prev_live | {m for m, c in miss.items() if c < 2}  # still present until confirmed gone
    flap = {m for m, d in known.items() if d.get("flapping")}
    events = [e for e in events if e["mac"] not in flap]  # flapping devices: one "unstable" alert, not a join/leave storm
    events += net.get("port_events") or []
    for e in events:
        e["net"] = net["slug"]
    net["events"] = events
    return events


def _commit_net_state(res, net, state):
    """Commit ONE network's baseline facts (live macs, known_macs, vendors,
    gateway MAC) to its slice of `state["lan"]["networks"]`, so the next run
    can diff it. The primary's `known_macs` / `last_live_macs` / `miss_count`
    stay aliased at the legacy flat keys (see `_migrate_lan_state`)."""
    nstate = net_state(state, net)
    _now = {m for m in net["devices"].values() if m}
    _pending = {m for m, c in (nstate.get("miss_count") or {}).items() if c < 2}
    _set_net(state, net, "last_live_macs", sorted(_now | _pending))
    known = nstate.get("known_macs")
    if known is None:
        known = {}
        for ip, mac in net["devices"].items():
            if mac:
                known[mac] = {"ip": ip, "first_seen": res["ts"], "last_seen": res["ts"], "vendor": None, "name": None}
    else:
        for d in net.get("new_devices", []):
            known[d["mac"]] = {"ip": d["ip"], "first_seen": d["first_seen"], "last_seen": res["ts"], "vendor": d["vendor"],
                               "name": d["name"], "parent": d.get("parent"), "trusted": d.get("trusted"), "label": d.get("label")}
        for ip, mac in net["devices"].items():
            if mac in known:
                known[mac]["last_seen"] = res["ts"]
                known[mac]["ip"] = ip
        nm = net.get("names") or {}
        for ip, mac in net["devices"].items():
            if mac in known and nm.get(ip):
                known[mac]["name"] = nm[ip]
        # backfill missing vendors a few at a time (rate-limited APIs)
        todo = [m for m, d in known.items() if not d.get("vendor") or d["vendor"] in ("?", "unknown vendor") and not (int(m[:2], 16) & 2)][:5]
        for m in todo:
            v = lan.mac_vendor(m)
            if v and v != "?":
                known[m]["vendor"] = v
    _set_net(state, net, "known_macs", known)
    if net.get("cameras") is not None:
        # Written AFTER `build_alerts` has diffed the previous baseline, which
        # is what makes the very first census silent.
        nstate["cameras"] = cameras.update_baseline(nstate.get("cameras"), net["cameras"], res["ts"])
    if res.get("wan_ip") and net.get("primary"):
        nstate["wan_ip"] = res["wan_ip"]
    nstate["last_live_count"] = net["live_count"]
    if net.get("presence"):
        nstate["presence"] = net["presence"]
    if net.get("gw_mac"):
        nstate["gw_mac"] = net["gw_mac"]
        if net.get("primary"):
            state["gw_mac"] = net["gw_mac"]


def _hist_live_macs(res, cfg):
    """The history line's live-mac field: a flat sorted list while there is
    exactly one active network AND the config carries no `networks` block
    at all (the pure pre-Task-52 shape, which `alerts.unusual_hours` and the
    hub's ingest both read); `{slug: [macs]}` whenever `config.networks` is
    actually configured, even down to one entry, and whenever there is more
    than one active network."""
    active = [n for n in res.get("networks") or [] if n.get("status") == "active"]
    if not active:
        return None
    if len(active) == 1 and not cfg.get("_networks_configured"):
        return sorted({m for m in active[0].get("devices", {}).values() if m})
    return {n["slug"]: sorted({m for m in n.get("devices", {}).values() if m}) for n in active}


# ---------------- one scan ----------------
def settle_dashboard_edits(state):
    """Finish applying the dashboard edits this run replayed at its start.

    `sync.pull()` applies every hub edit (trust, label, forget, ack) to the
    live `state` before the sweep; nothing else writes `state.json` while a
    run is going (the queue thread never does -- see the threading note at
    the top of this file). The only thing left to settle is a forget: the
    sweep re-discovered the device after the edit popped it, so it has to be
    popped again now, once, and the list cleared.

    This used to reload the PRE-RUN `state.json` and copy its `label` /
    `trusted` / `note` back over the live map "so dashboard clicks are never
    lost" -- a hold-over from when the dashboard wrote that file directly.
    With edits now arriving through `pull()`, the reload could only ever
    revert them: a device trusted at the start of the run was un-trusted
    again at the end (the on-disk `trusted: None` won), the edit offset had
    already advanced, and the "not verified" alert came straight back on the
    next sweep. Every trust-from-chat and trust-from-inbox click was lost
    that way.
    """
    forgotten = list(state.get("forgotten") or [])
    if forgotten:
        lan = state.get("lan") or {}
        maps = [lan.get("known_macs") or {}]
        maps += [(v or {}).get("known_macs") or {} for v in (lan.get("networks") or {}).values()]
        for km in maps:
            for mac in forgotten:
                km.pop(mac, None)
    state["forgotten"] = []




def email_advisory_domains(cfg):
    """Domains Vault CHECKS but must never raise anything about.

    `email.exclude` used to mean "do not look at all", so a domain somebody
    else owns simply had no answer. Miro's ruling changed the meaning, not
    the key: check everything, because a spoofable domain in his estate is
    worth knowing about whoever owns the DNS — but a domain he cannot fix
    must never produce an alert, a deduction or a nudge. status.example.org is
    the case this exists for, and being told about records only somebody
    else can set is exactly the nagging he asked me to stop.
    """
    return {d.strip().lower() for d in ((cfg.get("email") or {}).get("exclude") or []) if d.strip()}


def email_domains_of(cfg):
    """Every registrable domain whose email records Vault checks: one per
    monitored site. Nothing is dropped here any more — see
    `email_advisory_domains` for what "excluded" now means."""
    doms = {".".join(_site_host(s).split(".")[-2:]).lower() for s in cfg.get("sites") or []}
    return sorted(d for d in doms if d)


def run_once(cfg, state, args=None):
    no_lan = _flag(args, "no_lan")
    paths.ensure()
    client = sync.client_from_disk()
    hub = None
    if client is not None:
        # replay dashboard edits made while this sensor was busy or asleep,
        # and pick up whatever work was queued for it
        hub = client.pull(state)
        if hub.get("config"):
            cfg = config.load()   # re-merge: the pull may have rewritten config.json
    th = cfg["thresholds"]
    res = {"ts": util.now_iso()}
    _t_start = time.time()

    with concurrent.futures.ThreadPoolExecutor(8) as ex:
        # One failed request must not be a CRIT "DOWN" push: confirm a bad
        # site/host reading with a retry before it can raise anything.
        _slow_s = th.get("http_latency_warn_s", 5)
        site_list = list(ex.map(lambda s: probe.confirm(
            lambda: sites.check_site(s, th),
            lambda r: not r.get("ok") or (r.get("latency_s") or 0) > _slow_s), cfg["sites"]))
        sshs = list(ex.map(lambda h: probe.confirm(
            lambda: hosts.check_ssh(h, th), lambda r: not r.get("reachable")), cfg["ssh_hosts"]))
    res["sites"] = site_list
    res["ssh"] = sshs
    on_home_lan = lan.default_gateway() == cfg["lan"]["expected_gateway"]
    res["on_home_lan"] = on_home_lan
    _migrate_lan_state(state, config.primary_slug(cfg))
    _prune_stale_networks(state, cfg)
    # no global `lan.enabled` gate: `scan_networks` filters per network entry,
    # and a legacy `lan.enabled: false` is mirrored onto the primary entry by
    # `config.normalise_networks`, so that config disables exactly what it did
    # before while a disabled primary no longer suppresses a reachable secondary.
    res["networks"] = scan_networks(cfg, state, no_lan)
    _prim = next((n for n in res["networks"] if n.get("primary")), None)
    # `res["lan"]` IS the primary entry (by reference), so last.json keeps its
    # existing `lan` block — and stays None when that network was not swept,
    # because every legacy reader indexes ["devices"] straight away.
    res["lan"] = _prim if (_prim and _prim.get("status") == "active") else None
    res["tailscale"] = home.tailscale_status() if cfg.get("tailscale", {}).get("enabled") else None
    res["wan_ip"] = home.wan_ip() if on_home_lan else None
    res["dns"] = probe.confirm(home.dns_check, lambda d: any(not v.get("ok") for v in d.values())
                                                       or (d.get("system") or {}).get("ms", 0) > 150)
    try:
        res["wan_path"] = home.wan_path(cfg, slow_ms=th.get("path_ms_warn", 120)) if on_home_lan and not no_lan else None
        res["domains"] = domains.domain_expiry([_site_host(s) for s in cfg["sites"]], state)
        res["heartbeats"] = checks.heartbeats_check(cfg, state)
        res["backups"] = backups.check_backups(cfg, state)
        envp = paths.p(".env")
        st_env = os.stat(envp).st_mode & 0o077 if os.path.exists(envp) else 0
        res["self"] = {"env_perms_bad": bool(st_env), "version": VERSION,
                       "env_age_days": int((time.time() - os.stat(envp).st_mtime) / 86400) if os.path.exists(envp) else 0,
                       # The sensor host's own load, for the dashboard's
                       # sub-header. Either key can be None on a platform with
                       # no load average; the hub stores None and the UI falls
                       # back to the sensor-freshness pill rather than drawing
                       # a zero.
                       **home.host_load()}
        # Whether the machine Vault runs on is defending itself. Cheap,
        # read-only, no root: see endpoint.py for why this is a smaller and
        # more useful promise than "antivirus".
        try:
            res["endpoint"] = endpoint.posture()
        except Exception as e:  # noqa: BLE001 -- a posture read must never fail a scan
            res["endpoint"] = {"error": str(e)[:120]}
        _chk = checks.extra_checks(cfg)
        _bycfg = {c.get("name"): c for c in (cfg.get("checks") or [])}
        for i, r in enumerate(_chk):   # retry only what failed; one blip is not a CRIT
            if not r.get("ok") and r.get("name") in _bycfg:
                time.sleep(2); _chk[i] = checks.check_one(_bycfg[r["name"]])
        res["checks"] = _chk
        res["dns_filter"] = dnsstats.dns_analyse(cfg, state)
        # Flows (spec 2026-09-20-traffic-flows): roll every COMPLETED minute
        # file into rows, append them to today's flows jsonl for sync.push to
        # chunk, then delete the pcap -- a minute already rolled up has no
        # reason to stay on disk. Router mode is chosen here, not in
        # capture.tick, because only the runner knows whether the router
        # integration produced per-client bytes this scan.
        from . import capture, flows
        # Roll up BEFORE tick(): tick() trims the ring to MAX_FILES, so any
        # completed minute must be read first or a slow scan could lose it.
        prefixes = [n.get("subnet_prefix") for n in (cfg.get("networks") or []) if n.get("subnet_prefix")] \
                   or [cfg.get("lan", {}).get("subnet_prefix", "192.168.1")]
        rows = []
        own = lan.own_ips()
        for path in capture.completed_files():
            rows.extend(flows.rollup(flows.read_pcap(path), prefixes, own))
            capture.remove(path)
        rows, dropped = flows.cap(rows)
        capinfo = capture.tick(cfg, state)
        if capinfo["mode"] == "mirror" or rows:
            _append_flows(rows)
        elif router_rows := (res.get("router") or {}).get("client_bytes"):
            capinfo["mode"] = "router"
            _append_flows([{"ts": res["ts"][:16] + ":00" + res["ts"][19:], "mac": r["mac"], "ip": r.get("ip", ""),
                            "peer_ip": "", "peer_port": 0, "proto": "other",
                            "bytes_in": int(r.get("rx") or 0), "bytes_out": int(r.get("tx") or 0),
                            "pkts_in": 0, "pkts_out": 0, "hints": {}, "source": "router"} for r in router_rows])
        res["capture"] = {**capinfo, "flows_dropped": dropped}
        res["admin_probe"] = sites.admin_probe(cfg["sites"], state)
        state["admin_probe"] = res["admin_probe"]
        res["email_sec"] = domains.email_security(
            email_domains_of(cfg), state, advisory=email_advisory_domains(cfg))
        state["email_sec"] = res["email_sec"]
        res["external_sites"] = sites.external_sites(cfg, site_list, state)
        state["external_sites"] = res["external_sites"]
        wifi_now = home.wifi_info() if not no_lan else None
        res["rogue"] = home.rogue_checks(cfg, wifi_now, on_home_lan, state) if wifi_now else None
        # Per-network Wi-Fi guard: only the network this sensor's own Wi-Fi
        # interface sits on can produce evil-twin / rogue-DHCP facts, so the
        # others are absent here and the hub reports them "not verified on
        # this network" rather than clean.
        res["rogues"] = {}
        if res["rogue"] is not None and _prim is not None:
            _ns = net_state(state, _prim)
            for _k in ("home_ssid", "twin_channels"):
                if state.get(_k) is not None:
                    _ns[_k] = state[_k]
            res["rogues"][_prim["slug"]] = res["rogue"]
            _prim["wifi"] = wifi_now
            _prim["rogue"] = res["rogue"]
        # Every OTHER configured network — and the primary too when its own
        # Wi-Fi check produced nothing this cycle (no Wi-Fi interface, not on
        # the home LAN, etc.) — gets an explicit placeholder rather than
        # silently missing from `res["rogues"]`: this sensor's own Wi-Fi
        # interface is not on that network, so evil-twin / rogue-DHCP facts
        # cannot be produced for it.
        for _n in res.get("networks") or []:
            if _n.get("slug") not in res["rogues"]:
                res["rogues"][_n["slug"]] = {"verified": False, "reason": "no Wi-Fi interface on this network"}
    except Exception as e:
        res["collect_error"] = str(e)[:200]
    mu = state.get("mac_updates") or {}
    mu_due = not mu.get("ts") or (dt.datetime.now().astimezone() - dt.datetime.fromisoformat(mu["ts"])).total_seconds() > 6 * 3600
    if mu_due and not no_lan:
        mu = hosts.mac_updates()
        state["mac_updates"] = mu
    res["mac_updates"] = mu
    try:
        res["home"] = home.home_security(cfg, res, state) if on_home_lan and not no_lan else (state.get("home_last") if not on_home_lan else None)
    except Exception as e:
        res["home"] = {"error": str(e)[:160]}
    # speed test hourly (~20s); keep the last result visible in between
    prev_speed = state.get("speed") or {}
    due = not prev_speed.get("ts") or (dt.datetime.now().astimezone() - dt.datetime.fromisoformat(prev_speed["ts"])).total_seconds() > 3300
    if on_home_lan and due and not no_lan:
        _sp = home.speed_test()
        _base = (state.get("speed_baseline") or {}).get("down_mbps")
        # A single low run is noise (a download in the house, a busy hour):
        # re-measure before it can become "well below usual".
        if _sp and not _sp.get("error") and _base and (_sp.get("down_mbps") or _base) < _base * 0.4:
            _sp = home.speed_test()
        res["speed"] = _sp
    else:
        res["speed"] = prev_speed or None

    # per-device services (LAN port scan) -> port events; presence state machine; flapping; risk
    for _net in res["networks"]:
        if _net.get("status") == "active":
            _device_pass(res, _net, state)

    # Cameras behind a recorder (Task 73): the NVR's own PoE network is not
    # sweepable from here, so each registered recorder is ASKED over its
    # documented HTTP API. Runs before `build_alerts` (which raises the
    # findings) and is committed after it (which is what makes a new
    # recorder's first read silent). `subnet_prefix`es come from the CONFIG,
    # not from `res["networks"]`, so a network that happens to be unreachable
    # this cycle does not put its own recorder out of scope.
    try:
        res["recorders"] = nvr.scan(
            (hub or {}).get("recorders") or sync.load_recorders(),
            [n.get("subnet_prefix") for n in config.networks_of(cfg) if n.get("subnet_prefix")],
            state, res["ts"])
    except Exception as e:
        res["recorders"] = []
        res["recorder_error"] = f"{type(e).__name__}: {str(e)[:120]}"

    # Before deciding WHY a device looks like it is leaving the Wi-Fi, ask the
    # thing that actually knows. Presence here is a ping sweep plus ARP, and a
    # device in power save ignores pings: the gaps look like flapping even
    # when the radio never left. Signal strength and association uptime live
    # on the router.
    #
    # Only when there is something to explain, and only once per scan: this
    # costs a router login, so it is skipped entirely unless a device is
    # actually flapping and a credential exists.
    res["wifi_stations"] = _wifi_stations_if_needed(res, client)
    res["alerts"] = alerts.build_alerts(res, cfg, state)
    try:
        nvr.commit(res.get("recorders"), state, res["ts"])
    except Exception as e:
        log(f"recorder baseline commit failed: {type(e).__name__}: {str(e)[:120]}")
    try:
        for _row in alerts.unusual_hours(res, state):
            m = _row["mac"] if isinstance(_row, dict) else _row
            _net = _row.get("net") if isinstance(_row, dict) else None
            d = (alerts.net_state(state, _net or {}).get("known_macs") or {}).get(m, {})
            res["alerts"].append({"sev": "INFO", "key": alerts.lan_key("unusual-hour", _net or {}, mac=m),
                                  "msg": f"{d.get('label') or d.get('name') or d.get('vendor') or m} is online at {res['ts'][11:16]}, an hour it has never been online this week",
                                  "confidence": 45, "net": (_net or {}).get("slug")})
    except Exception:
        pass

    try:
        res["fixes"] = fix.remediate(res, cfg, state)
    except Exception as e:
        res["fixes"] = [{"ts": res["ts"], "host": "-", "action": "remediate", "ok": False, "out": str(e)[:200]}]
    # Dashboard-queued fixes / speed tests / reboot plans / "run now" are
    # deliberately NOT fetched or executed here any more -- see this file's
    # module docstring addendum. They run on their own ~10s background poll
    # (`queue_poll_loop`), which starts them within seconds of being queued
    # instead of waiting for this scan to reach this line.

    if res["fixes"]:
        okf = [f for f in res["fixes"] if f["ok"]]
        bad = [f for f in res["fixes"] if not f["ok"]]
        msg = "; ".join(f"{f['host']}: {f.get('label') or f.get('action')}"
                        + (f" ({f['arg'][:30]})" if f.get("arg") and f["action"] in ("restart_container", "restart_service", "block_ip", "kill_miner") else "")
                        for f in okf[:4])
        if bad:
            msg += (" | FAILED: " if msg else "FAILED: ") + "; ".join(f"{f['host']}: {f.get('label') or f.get('action')}" for f in bad[:3])
        res["fix_notified"] = notifier.notify("Vault auto-fix", msg[:400], "default")
        for f in res["fixes"]:  # mark the alert that triggered each fix
            k = fix.FIX_ALERT_KEY.get(f["action"], "-")
            key = k.format(h=f["host"], a=f.get("arg") or "")
            for al in res["alerts"]:
                if al["key"] == key or (f["action"] == "kill_miner" and al["key"] == f"sec:miner:{f['host']}"):
                    al["fix"] = f"{'auto-fixed' if f['ok'] else 'auto-fix FAILED'}: {f.get('label') or f['action']}"

    # ---- out-of-band notifications (independent of any dashboard being open) ----
    acked = state.get("acked") or {}
    known0 = (state.get("lan") or {}).get("known_macs") or {}
    notified = state.get("notified") or {}          # key -> ts of last push
    cur_keys = {al["key"] for al in res["alerts"]}
    now_dt = dt.datetime.now().astimezone()
    now_s = res["ts"]
    # ---- blueprint layer: score, recommendations, incidents, sensor health ----
    try:
        res["score"] = alerts.security_score(res, cfg, state)
        res["recommendations"] = alerts.recommendations(res, cfg, state)
    except Exception as e:
        res["score"] = {"total": None, "error": str(e)[:100]}
        res["recommendations"] = []
    # ---- unified alert lifecycle: one object per dedup key with first/last seen, count, state, resolution ----
    adb = state.setdefault("alerts_db", {})
    fixed_keys = {}
    for f in res.get("fixes", []):
        kf = fix.FIX_ALERT_KEY.get(f["action"], "-").format(h=f["host"], a=f.get("arg") or "")
        if f["ok"]:
            fixed_keys[kf] = f.get("label") or f["action"]
    for al in res["alerts"]:
        k = al["key"]
        e = adb.get(k)
        if e and e.get("state") != "resolved":
            if {"INFO":0,"WARN":1,"CRIT":2}.get(al["sev"],0) > {"INFO":0,"WARN":1,"CRIT":2}.get(e.get("sev"),0):
                e.pop("snooze_until", None)
                acked.pop(k, None)
                e["state"] = "open"
            e.update(last_seen=now_s, count=e.get("count", 0) + 1, sev=al["sev"], msg=al["msg"])
            if al.get("net") is not None:
                e["net"] = al["net"]
        else:
            adb[k] = e = {"key": k, "sev": al["sev"], "msg": al["msg"], "first_seen": now_s, "last_seen": now_s,
                          "count": 1, "state": "open", "fires": (e or {}).get("fires", 0) + 1, "net": al.get("net")}
        if e.get("snooze_until") and e["snooze_until"] > now_s:
            e["state"] = "snoozed"
        elif k in acked:
            e["state"] = "acked"
        elif e["state"] == "snoozed":
            e["state"] = "open"
        if al.get("fix"):
            e["fix"] = al["fix"]
        al.update(first_seen=e["first_seen"], count=e["count"], state=e["state"], fires=e["fires"])
    for k, e in adb.items():
        if k not in cur_keys and e.get("state") != "resolved":
            e.update(state="resolved", resolved_at=now_s,
                     resolved_by=("fix: " + fixed_keys[k]) if k in fixed_keys else "auto-cleared")
            acked.pop(k, None)
    try:
        res["incidents"] = alerts.correlate_incidents(res, state)
    except Exception as e:
        res["incidents"] = []
        res["incident_error"] = str(e)[:100]
    keep_after = (now_dt - dt.timedelta(days=30)).isoformat(timespec="seconds")
    state["alerts_db"] = {k: e for k, e in adb.items()
                          if e.get("state") != "resolved" or (e.get("resolved_at") or "") >= keep_after}
    state["acked"] = acked

    # ---- hygiene: consecutive-failure gate, resend interval, maintenance window, snooze/ack, immediate vs digest ----
    th2 = cfg["thresholds"]
    fire_after = int(th2.get("fire_after_runs", 2))
    resend_s = float(th2.get("resend_hours", 6)) * 3600
    maint = state.get("maintenance_until")
    in_maint = bool(maint and maint > now_s)

    def pushable(al):
        k = al["key"]
        e = adb[k]
        if e["state"] in ("acked", "snoozed"):
            return False
        if (k.startswith(("site:", "ssh:")) and k.endswith(":down") or k.startswith("check:")) and e["count"] < fire_after:
            return False
        if in_maint and al["sev"] != "CRIT":
            return False
        ck = alerts.canonical_lan_key(k)
        immediate = al["sev"] == "CRIT" or ck.startswith(IMMEDIATE) or (k.startswith("ports:") and ":open:" in k) or k in ("dns:system", "dns:all")
        if not immediate:
            return False
        if ck.startswith("lan:new:") and known0.get(ck.split(":", 2)[2], {}).get("trusted"):
            return False
        last = notified.get(k)
        if last:
            if al["sev"] != "CRIT":
                return False
            if (now_dt - dt.datetime.fromisoformat(last)).total_seconds() < resend_s:
                return False
        return True

    pushes = [al for al in res["alerts"] if pushable(al)]
    # The Trust / Ignore-24h buttons call back into /act/<token>/… , which only
    # a Vault web front end serves — the hub's own base URL does not. So they
    # are attached only when `public_url` is explicitly configured; otherwise the
    # push goes out with no actions (and no token is minted for nobody to use).
    act_base = (cfg.get("public_url") or "").rstrip("/")
    pub = (act_base or (client.url if client else "") or DEFAULT_PUBLIC_URL).rstrip("/")
    click = (pub + "/") if pub else None
    pend = state.setdefault("pending_actions", {})
    for tok in [tk for tk, v in pend.items() if v.get("expires", "") < now_s]:
        pend.pop(tok)
    dev_pushes = [al for al in pushes if alerts.canonical_lan_key(al["key"]).startswith("lan:new:")][:3]
    other = [al for al in pushes if al not in dev_pushes]
    res["notified_via"] = []
    for al in dev_pushes:  # one push per new device, with Trust / Ignore-24h buttons when a dashboard can serve them
        mac = alerts.canonical_lan_key(al["key"]).split(":", 2)[2]
        acts = None
        if act_base:
            tok = secrets.token_urlsafe(12)
            pend[tok] = {"mac": mac, "expires": (now_dt + dt.timedelta(hours=24)).isoformat(timespec="seconds")}
            acts = [f"http, Trust (mine), {act_base}/act/{tok}/trust, method=GET, clear=true",
                    f"http, Ignore 24h, {act_base}/act/{tok}/snooze, method=GET, clear=true",
                    f"view, Open Vault, {act_base}/"]
        res["notified_via"] += notifier.notify("Vault: new device on Wi-Fi", al["msg"], "high", actions=acts, click=click)
        notified[al["key"]] = now_s
    if other:
        crit = [p for p in other if p["sev"] == "CRIT"]
        title = "Vault CRITICAL" if crit else "Vault alert"
        msg = " | ".join(p["msg"] for p in other[:4]) + (f" (+{len(other)-4} more)" if len(other) > 4 else "")
        res["notified_via"] += notifier.notify(title, msg, "high" if crit else "default", click=click)
        for p in other:
            notified[p["key"]] = now_s
    recovered = [k for k in list(notified) if k not in cur_keys and not alerts.canonical_lan_key(k).startswith(
        ("lan:new:", "sec:login:", "sec:authkeys:", "sec:users:", "sec:cron:", "reboot:", "lan:unverified", "lan:rotated:", "sec:drift:"))]
    if recovered and not in_maint:
        notifier.notify("Vault recovered", "Cleared: " + ", ".join((adb.get(k) or {}).get("msg", k)[:60] for k in recovered[:4]), "low")
    for k in recovered:
        notified.pop(k, None)
    for k in [k for k in notified if alerts.canonical_lan_key(k).startswith(("lan:new:", "sec:drift:")) and k not in cur_keys]:
        notified.pop(k, None)
    state["notified"] = notified
    # ---- escalation: a CRIT that stays open and unacknowledged for 30 min gets one URGENT push ----
    for al in res["alerts"]:
        e = adb[al["key"]]
        if (al["sev"] == "CRIT" and e["state"] == "open" and not e.get("escalated")
                and (now_dt - dt.datetime.fromisoformat(e["first_seen"])).total_seconds() > 1800 and not in_maint):
            e["escalated"] = now_s
            res["notified_via"] += notifier.notify("Vault URGENT — still critical after 30 min", al["msg"], "urgent", click=click)
    # ---- daily digest (LOW/INFO never page; they arrive once a day) ----
    dh = int((cfg.get("auto_fix") or {}).get("digest_hour", 9))
    today = now_dt.date().isoformat()
    if now_dt.hour >= dh and state.get("digest_date") != today:
        opn = [e for e in state["alerts_db"].values() if e["state"] != "resolved"]
        day_ago = (now_dt - dt.timedelta(days=1)).isoformat(timespec="seconds")
        fx = _read_jsonl("remediation.jsonl")
        fx = [f for f in fx if f.get("ts", "") >= day_ago]
        evs = _read_jsonl("events.jsonl")[-400:]
        evs = [e for e in evs if e.get("ts", "") >= day_ago]
        msg = (f"Open: {sum(1 for e in opn if e['sev']=='CRIT')} crit, {sum(1 for e in opn if e['sev']=='WARN')} warn, {sum(1 for e in opn if e['sev']=='INFO')} info. "
               f"Auto-fixes 24h: {sum(1 for f in fx if f.get('ok'))} ok, {sum(1 for f in fx if not f.get('ok'))} failed. "
               f"Wi-Fi: {sum(1 for e in evs if e.get('type')=='join')} joins, {sum(1 for e in evs if e.get('type')=='leave')} leaves, {sum(1 for e in evs if e.get('type')=='port_open')} new services. "
               f"Unverified devices: {len((res.get('home') or {}).get('unverified') or [])}. Sites up {sum(1 for s in site_list if s['ok'])}/{len(site_list)}.")
        res["digest_via"] = notifier.notify("Vault daily digest", msg, "low", click=click)
        state["digest_date"] = today
        if now_dt.weekday() == 0:  # Monday: weekly summary
            wk = (now_dt - dt.timedelta(days=7)).isoformat(timespec="seconds")
            pts = [p for p in _read_jsonl("history.jsonl") if (p.get("ts") or "") >= wk and not p.get("speed_only")]
            upt = {n: round(100 * sum(1 for p in pts if p["sites"].get(n, {}).get("ok")) / max(1, sum(1 for p in pts if n in p["sites"])), 2)
                   for n in {n for p in pts for n in p["sites"]}}
            fxw = [f for f in _read_jsonl("remediation.jsonl") if f.get("ts", "") >= wk]
            resolved = [e for e in state["alerts_db"].values() if (e.get("resolved_at") or "") >= wk]
            notifier.notify("Vault weekly summary",
                            "Uptime: " + ", ".join(f"{n.split(' (')[0]} {u}%" for n, u in upt.items())
                            + f". {len(pts)} scans. {len(fxw)} auto-fixes. {len(resolved)} alerts resolved. "
                              f"Devices known: {len((state.get('lan') or {}).get('known_macs') or {})}.", "low", click=click)
    if res["speed"] and not res["speed"].get("error"):
        state["speed"] = res["speed"]
        b = state.get("speed_baseline") or {}
        n = b.get("n", 0)
        state["speed_baseline"] = {"down_mbps": round((b.get("down_mbps", 0) * n + res["speed"]["down_mbps"]) / (n + 1), 1),
                                   "n": min(n + 1, 48)}

    # Per-device DNS-volume baseline, same running-average shape as
    # `speed_baseline` above (`alerts.build_alerts` reads it as "the prior
    # baseline" the *next* run, since it runs earlier in this function against
    # the state loaded from disk). Capped at 30 samples so roughly a month of
    # ordinary use, not the device's entire lifetime, decides "usual".
    dns_totals = (res.get("dns_filter") or {}).get("client_totals_by_mac") or {}
    if dns_totals:
        vb = dict(state.get("dns_volume_baseline") or {})
        for mac, total in dns_totals.items():
            prev = vb.get(mac) or {}; n = prev.get("n", 0)
            vb[mac] = {"avg": round((prev.get("avg", 0) * n + total) / (n + 1), 1), "n": min(n + 1, 30)}
        state["dns_volume_baseline"] = vb

    # LAN join/leave events (devices seen live last run vs now), per network
    events = []
    for _net in res["networks"]:
        if _net.get("status") == "active":
            events += _events_pass(res, _net, state)
    if events:
        _append_jsonl("events.jsonl", events)

    hp0 = paths.p("history.jsonl")
    hour_ago = (dt.datetime.now().astimezone() - dt.timedelta(hours=1)).isoformat(timespec="seconds")
    res["health"] = {"sensor": "online", "run_seconds": int(time.time() - _t_start),
                     "scans_last_hour": _count_lines_since(hp0, hour_ago) + 1,
                     "feeds": {"sites": bool(res["sites"]), "servers": any(h.get("reachable") for h in res["ssh"]),
                               "lan": bool(res.get("lan")), "dns": bool(res.get("dns")), "speed": bool(res.get("speed")),
                               "path": bool(res.get("wan_path")), "heartbeats": bool(res.get("heartbeats"))},
                     "assets": sum(len(v.get("known_macs") or {}) for v in
                                   ((state.get("lan") or {}).get("networks") or {}).values()) + len(res["ssh"]) + len(res["sites"])}
    _dump("last.json", res)

    # history time-series (one compact line per run)
    hist = {"ts": res["ts"],
            "sites": {s["name"]: {"ok": s["ok"], "lat": s.get("latency_s"), "cert": s.get("cert_days_left")} for s in site_list},
            "hosts": {h["name"]: ({"disk": h.get("disk_pct"), "mem": h.get("mem_pct"), "load": h.get("load_per_core"), "failed": h.get("failed_ssh_24h")} if h["reachable"] else None) for h in sshs},
            "lan": (res["lan"] or {}).get("live_count"),
            "live_macs": _hist_live_macs(res, cfg),
            "networks": {n["slug"]: {"status": n.get("status"), "lan": n.get("live_count"),
                                     "cameras": (n.get("cameras") or {}).get("count")}
                         for n in res["networks"]} or None,
            "dns_ms": {k: (v["ms"] if v["ok"] else None) for k, v in (res.get("dns") or {}).items()},
            "speed": ({"down": res["speed"].get("down_mbps"), "up": res["speed"].get("up_mbps"), "lat": res["speed"].get("latency_ms")}
                      if (due and on_home_lan and not no_lan and res.get("speed") and not res["speed"].get("error")) else None),
            "alerts": {k: sum(1 for a in res["alerts"] if a["sev"] == k) for k in ("CRIT", "WARN", "INFO")},
            "wan_path": {k: {"avg": v.get("avg_ms"), "loss": v.get("loss_pct")} for k, v in (res.get("wan_path") or {}).items()} or None,
            "presence": (res.get("lan") or {}).get("presence"),
            "presence_by_net": ({n["slug"]: n.get("presence") for n in res["networks"] if n.get("presence")} or None
                                if len(res["networks"]) > 1 else None),
            "checks": {c["name"]: c["ok"] for c in (res.get("checks") or [])} or None,
            "score": (res.get("score") or {}).get("total"), "incidents": len(res.get("incidents") or [])}
    _append_jsonl("history.jsonl", [hist])

    # commit baseline facts to state so the next run can diff (never stores secrets)
    state["last_run"] = res["ts"]
    if (res.get("rogue") or {}).get("twin_channels") is not None and res["rogue"].get("on_home_ssid"):
        state["twin_channels"] = (sorted(set(state.get("twin_channels") or []) | set(res["rogue"]["twin_channels"]))
                                  if not any(al["key"] == "home:evil-twin" for al in res["alerts"]) else state.get("twin_channels"))
        if _prim is not None:
            net_state(state, _prim)["twin_channels"] = state.get("twin_channels")
    if res.get("domains"):
        state["domains"] = res["domains"]
    if res["wan_ip"]:
        state["wan_ip"] = res["wan_ip"]
    # `cert_issuer` is carried across sweeps so the next one can notice a
    # change of certificate authority (see `alerts.build_alerts`). Stored
    # only when the handshake actually produced one: writing None on a
    # failed probe would erase the baseline and make the following sweep
    # read the recovery as a change.
    state["sites"] = {s["name"]: {"dns_a": s.get("dns_a"), "ok": s["ok"],
                                  "cert_days_left": s.get("cert_days_left"),
                                  "cert_issuer": s.get("cert_issuer") or
                                  (state.get("sites") or {}).get(s["name"], {}).get("cert_issuer")}
                      for s in site_list}
    state["ssh"] = {h["name"]: {"uptime": h.get("uptime"), "reachable": h["reachable"], "disk_pct": h.get("disk_pct"), "ports": h.get("ports")} for h in sshs}
    for _net in res["networks"]:
        if _net.get("status") == "active":
            _commit_net_state(res, _net, state)
    fixed_hosts = {f["host"] for f in res.get("fixes", []) if f.get("ok")}
    old_sec = state.get("sec") or {}
    state["sec"] = {h["name"]: {**{k: h["sec"].get(k) for k in ("authorized_keys", "login_users", "cron_net", "login_sources", "lan_login_macs")},
                                "drift": (None if h["name"] in fixed_hosts else
                                          (h.get("drift") if (not (old_sec.get(h["name"]) or {}).get("drift")
                                                              or not any(a["key"].startswith(f"sec:drift:{h['name']}:") for a in res["alerts"]))
                                           else old_sec[h["name"]]["drift"]))}
                    for h in sshs if h["reachable"] and h.get("sec")}
    if res.get("home") and not res["home"].get("error"):
        state["home"] = {"exposure": res["home"].get("exposure")}
        state["home_last"] = {k: v for k, v in res["home"].items() if k != "unverified"} | {"unverified": res["home"].get("unverified"), "stale": True}
    state.setdefault("alert_history", [])
    state["alert_history"] = (state["alert_history"] + [{"ts": res["ts"], "keys": [a["key"] for a in res["alerts"]]}])[-96:]
    settle_dashboard_edits(state)

    # retention runs BEFORE the push and shifts the hub offsets by exactly the
    # number of lines it dropped, so the push still sends only the new lines.
    try:
        apply_retention(state)
    except Exception as e:
        log(f"retention failed: {type(e).__name__}: {str(e)[:140]}")

    # The push must never cost this cycle its state write: hub_sent offsets, the
    # alert lifecycle and every baseline diff live in state.json, so it is
    # persisted in a finally regardless of how the push went.
    try:
        if client is not None:
            if hub and hub.get("errors"):
                res["hub_pull_errors"] = hub["errors"]
            resp = client.push({"version": VERSION, "last": res, "state": state})
            res["hub_sync"] = "ok" if (resp or {}).get("ok") else "failed"
    except Exception as e:
        res["hub_sync"] = "failed"
        res["hub_error"] = f"{type(e).__name__}: {str(e)[:200]}"
        log(f"hub push raised: {type(e).__name__}: {str(e)[:200]}")
    finally:
        _dump("state.json", state)
        _dump("last.json", res)
    return res


# ---------------- retention ----------------
HISTORY_DAYS = 30            # history.jsonl keeps 30 days
EVENTS_MAX_LINES = 5000      # events.jsonl keeps its last 5000 lines


def _nonempty(lines):
    return sum(1 for l in lines if l.strip())


def _trim_jsonl(path, keeper):
    """Rewrite one append-only log in place, dropping lines from its head.

    `keeper(lines)` returns the suffix to keep. Returns how many NON-EMPTY lines
    were dropped — the unit sync's offsets count in (`sync._read_lines` skips
    blank lines), so a stray blank line cannot skew the shift below.
    """
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    keep = keeper(lines)
    if len(keep) >= len(lines):
        return 0
    with open(path, "w") as f:
        f.writelines(keep)
    return max(0, _nonempty(lines) - _nonempty(keep))


def apply_retention(state):
    """Age out history/events lines, then shift `state["hub_sent"]` down by as
    many lines as were dropped. Returns {key: lines_removed}.

    Must run before the hub push and before state.json is written. The offsets
    are line counts from the head of each file, so front-trimming invalidates
    them: at steady state retention drops roughly one history line per cycle
    while the cycle appends one, the file length barely moves, and an unshifted
    offset makes `lines[off:]` empty — history would silently stop reaching the
    hub forever. events.jsonl, pinned at 5000 lines, fails the other way: the
    offset goes stale-high, trips push's truncation guard, resets to 0 and
    resends all 5000 rows on every cycle.
    """
    cutoff = (dt.datetime.now().astimezone() - dt.timedelta(days=HISTORY_DAYS)).isoformat(timespec="seconds")
    removed = {
        # key names are sync.LOG_FILES keys: the offsets they index
        "history": _trim_jsonl(paths.p("history.jsonl"),
                               lambda ls: [l for l in ls if l[8:33] >= cutoff or '"ts": "' not in l[:12]]),
        "events": _trim_jsonl(paths.p("events.jsonl"),
                              lambda ls: ls[-EVENTS_MAX_LINES:] if len(ls) > EVENTS_MAX_LINES else ls),
    }
    # remediation.jsonl is append-only and never trimmed, so "fixes" needs no shift
    if any(removed.values()):
        sent = state.get("hub_sent")
        if not isinstance(sent, dict):
            sent = {}
        for key, n in removed.items():
            if not n:
                continue
            try:
                off = int(sent.get(key) or 0)
            except (TypeError, ValueError):
                off = 0
            sent[key] = max(0, off - n)
        state["hub_sent"] = sent
    return removed


def _count_lines_since(path, since):
    """How many history lines carry a ts at or after `since` (0 if no file)."""
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8", errors="ignore") as f:
        return sum(1 for l in f if l[8:33] >= since)


def _read_jsonl(name):
    p = paths.p(name)
    if not os.path.exists(p):
        return []
    out = []
    with open(p, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict):
                out.append(obj)
    return out


# ---------------- the loop ----------------
def main(args=None):
    """Single-instance collector loop. --once runs one cycle and exits."""
    import fcntl
    paths.ensure()
    # single-instance lock: the service manager, a manual run and a hub
    # "run now" can all fire at once. Held for the life of the process.
    lockf = open(paths.p(".run.lock"), "w")
    try:
        try:
            fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(json.dumps({"skipped": "another collector run is in progress"}))
            return 0
        once = _flag(args, "once")
        # `run_now_event` lets the fast queue poller (below) wake this loop's
        # sleep early when a dashboard "run now" is queued, instead of that
        # request just sitting there for up to SLEEP_SECONDS. threading.Event
        # is the whole synchronisation story: `.wait(timeout)` returns True
        # immediately if the event is ALREADY set (so a "run now" queued
        # while a scan is in flight is not lost -- the next `.wait()` call
        # sees it set and returns at once), and `.set()`/`.clear()` are
        # atomic, so no separate lock is needed around it.
        stop_event = threading.Event()
        run_now_event = threading.Event()
        poller = None
        if not once:
            poller = threading.Thread(target=queue_poll_loop, args=(stop_event, run_now_event),
                                      name="guardian-queue-poll", daemon=True)
            poller.start()
        try:
            while True:
                try:
                    cfg = config.load()
                    state = load_state()
                    res = run_once(cfg, state, args)
                    if once:
                        # No background poller in --once mode (see above) --
                        # drain the queue with a single synchronous tick so
                        # `guardian-sensor run --once` still does what it
                        # always did: leave nothing queued behind it.
                        try:
                            n = poll_queue_once(run_now_event=run_now_event)
                            if n:
                                log(f"queue: ran {n} item(s) queued for --once")
                        except Exception as e:
                            log(f"queue poll (--once) failed: {type(e).__name__}: {str(e)[:200]}")
                        print(json.dumps(res, indent=2))
                    else:
                        log(f"scan {res['ts']}: {len(res.get('alerts') or [])} alerts, "
                            f"score {(res.get('score') or {}).get('total')}, hub {res.get('hub_sync', 'not enrolled')}")
                except Exception as e:
                    log(f"cycle failed: {type(e).__name__}: {str(e)[:300]}")
                if once:
                    return 0
                if run_now_event.wait(SLEEP_SECONDS):
                    run_now_event.clear()
        finally:
            stop_event.set()
    finally:
        lockf.close()
