"""Hub sync: enrolment, config/edit/queue pull, ingest push, queue results.

Ported from the Nexus collector's sensor-side hub sync
(~/.claude/nexus-agent/checks.py `_hub_req`/`hub_pull`/`hub_push`) and the
replayable edit log (~/.claude/nexus-agent/hubsync.py `apply_edit`) — logic
copied faithfully, never imported from that directory. The routes are the
Vault hub's `/api/v1/sensor/*` API with bearer-token auth, so the request
plumbing is new; the offset bookkeeping and edit replay are the ported parts.

Offsets are LINE COUNTS already sent (Nexus used byte offsets). They are
truncation-safe: if a log file now holds fewer lines than the offset says
were sent, the file was rotated, so the offset resets to 0 and everything is
re-sent (the hub's ingest is idempotent per row).
"""
import datetime as dt
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

from . import VERSION, paths, util

API_PREFIX = "/api/v1/sensor"
RETRY_DELAY = 3.0          # seconds; every request is retried once on a network error
DNS_MAX_PER_PUSH = 5000    # cap on dns query lines sent in a single ingest
FLOW_MAX_PER_PUSH = 5000   # cap on flow rows sent in a single ingest
LOG_FILES = {"events": "events.jsonl", "history": "history.jsonl", "fixes": "remediation.jsonl"}
UA = f"guardian-sensor/{VERSION}"


def log(msg):
    # stderr, so `guardian-sensor run --once` keeps stdout a clean JSON document
    print(f"{util.now_iso()} sync: {msg}", file=sys.stderr, flush=True)


# ---------------- transport ----------------
def _request(url, method="GET", token=None, body=None, timeout=30):
    """One HTTP request against the hub. Returns the decoded JSON body ({} for
    an empty response). Raises on transport/HTTP errors."""
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"Content-Type": "application/json", "User-Agent": UA}
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=timeout, context=util.SSL_CTX) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


class HubClient:
    """Talks to one Vault hub as one enrolled sensor."""

    def __init__(self, url, token, timeout=30):
        self.url = (url or "").rstrip("/")
        self.token = token
        self.timeout = timeout
        self._progress_last: dict = {}

    # -- low level --
    def _req(self, method, path, body=None):
        return _request(self.url + API_PREFIX + path, method=method, token=self.token,
                        body=body, timeout=self.timeout)

    def _try(self, method, path, body=None):
        """_req with one retry after RETRY_DELAY seconds — the hub may be
        restarting or the sensor's uplink may have blipped."""
        try:
            return self._req(method, path, body)
        except Exception as first:
            log(f"{method} {path}: {str(first)[:120]} — retrying in {RETRY_DELAY:.0f}s")
            time.sleep(RETRY_DELAY)
            return self._req(method, path, body)

    # -- pull --
    def pull(self, state):
        """Fetch tenant config, replay dashboard edits onto state/config, and
        collect the work queue. Never raises: a failed leg is logged, recorded
        in the returned "errors" list, and the scan continues."""
        out = {"config": None, "edits": [], "errors": []}
        cfg_path = paths.p("config.json")
        raw_cfg = {}
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path) as f:
                    raw_cfg = json.load(f)
            except Exception as e:
                out["errors"].append(f"config.json unreadable: {str(e)[:80]}")

        # GET /config — the hub's copy only bootstraps a sensor that has no
        # local config.json; once a local file exists, local wins.
        try:
            hub_cfg = self._try("GET", "/config")
            if isinstance(hub_cfg, dict) and hub_cfg and not os.path.exists(cfg_path):
                paths.ensure()
                _write_json(cfg_path, hub_cfg)
                raw_cfg = hub_cfg
        except Exception as e:
            out["errors"].append(f"config: {str(e)[:120]}")
            log(f"config pull failed: {str(e)[:120]}")

        # GET /edits?after=<last applied id> — replay every dashboard write
        # made while this sensor was busy or asleep, in id order.
        after = _int(state.get("hub_edits_offset"), 0)
        try:
            j = self._try("GET", f"/edits?after={after}")
            edits = j.get("edits") or []
            cfg_changed = False
            for e in edits:
                try:
                    if apply_edit(e, state, raw_cfg) == "config":
                        cfg_changed = True
                except Exception as ex:
                    log(f"edit {e.get('id')} {e.get('path')}: {str(ex)[:100]}")
            state["hub_edits_offset"] = max(after, _int(j.get("last_id"), after))
            if cfg_changed:
                paths.ensure()
                _write_json(cfg_path, raw_cfg)
            out["edits"] = edits
        except Exception as e:
            out["errors"].append(f"edits: {str(e)[:120]}")
            log(f"edits pull failed: {str(e)[:120]}")

        # The work queue (fixes / speed tests / reboot plans requested on the
        # dashboard) is deliberately NOT fetched here any more. `pull()` runs
        # once per multi-minute scan, so a queue leg here waits for the next
        # scan to start and then rides along with it -- exactly the latency
        # bug this addendum fixes. See `poll_queue()` below and
        # `runner.queue_poll_loop`, which fetch and execute it on their own
        # ~10s cadence, independent of the scan.

        # GET /recorders — the NVR/DVR inventory for this site, WITH the
        # credentials needed to read it (see `recorders_file`'s docstring on
        # why this is its own 0600 file and not an /api/config edit).
        try:
            out["recorders"] = self.recorders()
        except Exception as e:
            out["errors"].append(f"recorders: {str(e)[:120]}")
            log(f"recorders pull failed: {str(e)[:120]}")
            out["recorders"] = load_recorders()

        out["config"] = raw_cfg
        return out

    def recorders(self):
        """The tenant's camera recorders, cached to disk so a hub blip does not
        blind the camera enumeration for a cycle.

        The hub returns each recorder's username and password in the clear over
        the same TLS bearer channel that already carries every other secret this
        sensor holds. Nothing about them is logged: a failure here reports the
        transport error, never the body.
        """
        j = self._try("GET", "/recorders")
        rows = j.get("recorders") if isinstance(j, dict) else None
        if not isinstance(rows, list):
            return load_recorders()
        paths.ensure()
        _write_json(recorders_file(), {"recorders": rows}, mode=0o600)
        return rows

    # -- work queue (fast path) --
    def poll_queue(self):
        """GET /queue on its own: fixes / speed tests / reboot plans / "run
        now" requested on the dashboard. The hub marks each returned row
        "running" as it hands it over, so every item this returns must be
        reported back with `.result()` -- exactly the same contract `pull()`
        used to carry, just split into its own cheap, frequent call so
        `runner.queue_poll_loop` can hit ONLY this leg every ~10s instead of
        re-fetching config/edits/recorders on every tick too."""
        j = self._try("GET", "/queue")
        return j.get("queued") or []

    # -- push --
    def push(self, payload):
        """POST /ingest with this run plus only the log lines the hub has not
        seen. Offsets in state["hub_sent"] advance only on a successful push,
        so a failed push is retried in full on the next cycle."""
        state = payload.get("state")
        if state is None:
            state = {}
            payload = dict(payload, state=state)
        sent = state.get("hub_sent") or {}
        body = dict(payload)
        pending = {}

        for key, fname in LOG_FILES.items():
            lines = _read_lines(paths.p(fname))
            off = _int(sent.get(key), 0)
            if len(lines) < off:      # rotated/truncated since the last push
                off = 0
            body[key] = _parse_lines(lines[off:])
            pending[key] = len(lines)

        dns_prev = sent.get("dns") if isinstance(sent.get("dns"), dict) else {}
        dns_name = "queries-" + dt.date.today().isoformat() + ".jsonl"
        dns_lines = _read_lines(os.path.join(paths.p("dns"), dns_name))
        dns_off = _int(dns_prev.get("off"), 0) if dns_prev.get("file") == dns_name else 0
        if len(dns_lines) < dns_off:
            dns_off = 0
        chunk = dns_lines[dns_off:dns_off + DNS_MAX_PER_PUSH]
        nets = _push_networks(payload)
        body["networks"] = nets
        body["dns_queries"] = _attribute_dns(_parse_lines(chunk), nets)
        pending_dns = {"file": dns_name, "off": dns_off + len(chunk)}

        # Flows: same per-day file + offset dance as dns_queries, for the same
        # reason -- a retry after a failed post must add zero rows, and the
        # offset may only advance once the hub has actually stored them.
        fl_prev = sent.get("flows") if isinstance(sent.get("flows"), dict) else {}
        fl_name = "flows-" + dt.date.today().isoformat() + ".jsonl"
        fl_lines = _read_lines(os.path.join(paths.p("flows"), fl_name))
        fl_off = _int(fl_prev.get("off"), 0) if fl_prev.get("file") == fl_name else 0
        if len(fl_lines) < fl_off:
            fl_off = 0
        fl_chunk = fl_lines[fl_off:fl_off + getattr(self, "_flow_cap", FLOW_MAX_PER_PUSH)]
        body["flows"] = _attribute_flows(_parse_lines(fl_chunk), nets)
        pending_flows = {"file": fl_name, "off": fl_off + len(fl_chunk)}

        try:
            resp = self._try("POST", "/ingest", body)
        except urllib.error.HTTPError as e:
            if e.code == 413 and body.get("flows"):
                # The hub refused the size. Flows are the only part of the
                # payload that scales with the network, so they are the part
                # that yields: halve the cap (floor 500), retry this push with
                # the smaller chunk, and let the offset stay put so nothing is
                # skipped. The scan itself is never sacrificed for the flows.
                self._flow_cap = max(500, getattr(self, "_flow_cap", FLOW_MAX_PER_PUSH) // 2)
                log(f"ingest 413: flow cap now {self._flow_cap}")
                body["flows"] = body["flows"][: self._flow_cap]
                pending_flows = {"file": fl_name, "off": fl_off + len(body["flows"])}
                try:
                    resp = self._try("POST", "/ingest", body)
                except Exception as e2:
                    log(f"ingest failed: {str(e2)[:140]}"); return None
            else:
                log(f"ingest failed: {str(e)[:140]}"); return None
        except Exception as e:
            log(f"ingest failed: {str(e)[:140]}")
            return None
        sent.update(pending)
        sent["dns"] = pending_dns
        sent["flows"] = pending_flows
        state["hub_sent"] = sent
        return resp

    # -- queue results --
    def result(self, qid, ok, out="", result=None):
        """Report one queued item's outcome. Every item handed over by
        GET /queue needs exactly one of these — including refusals — or the
        hub leaves the row stuck in "running"."""
        body = {"ok": bool(ok), "out": str(out or "")[:4000], "result": result}
        try:
            return self._try("POST", f"/queue/{qid}/result", body)
        except Exception as e:
            log(f"queue result {qid} failed: {str(e)[:120]}")
            return None

    # -- queue progress --
    def progress(self, qid, done, total, line):
        """Report one in-flight `security_updates`/`full_upgrade` progress
        line. Throttled to one POST every 3s PER QUEUE ITEM -- an apt run
        can stream hundreds of lines a second faster than that, and the
        hub/browser only need a heartbeat, not every line. A single retry
        on a network blip (via `_try`) is skipped here on purpose: a missed
        progress post is harmless (the next one lands in <=3s) and must
        never block the actual upgrade command's own timeout budget."""
        last = self._progress_last.get(qid, 0.0)
        now = time.monotonic()
        if now - last < 3.0:
            return None
        self._progress_last[qid] = now
        body = {"done": int(done), "total": int(total), "line": str(line or "")[:300]}
        try:
            return self._req("POST", f"/queue/{qid}/progress", body)
        except Exception as e:
            log(f"queue progress {qid} failed: {str(e)[:120]}")
            return None


# ---------------- per-network push shape ----------------
#: dropped from a `networks[]` entry before it is pushed: the hub reads the
#: device maps out of `state["lan"]["networks"][<slug>]["known_macs"]` and the
#: rows out of `last`, so repeating them here would only bloat the body.
_BULKY = ("devices", "names", "new_devices", "events", "port_events")


def _push_networks(payload):
    """The `networks` block of an ingest body: one slim entry per network,
    carrying its identity and `status` (`active` / `unreachable` + `reason`).
    An `unreachable` entry never carries a device map — the hub marks the
    network unreachable and touches no device row."""
    last = payload.get("last") or {}
    out = []
    for n in last.get("networks") or []:
        if not isinstance(n, dict) or not n.get("slug"):
            continue
        e = {k: v for k, v in n.items() if k not in _BULKY}
        e.pop("watch_ips_labels", None)
        out.append(e)
    return out


def _attribute_dns(rows, nets):
    """Stamp each pushed dns query line with the `net` slug its client ip sits
    on (longest matching `subnet_prefix`; unmatched falls back to the primary).
    The hub attributes by client ip too, so this is a hint, not a dependency."""
    if not nets:
        return rows
    prim = next((n["slug"] for n in nets if n.get("primary")), nets[0].get("slug"))
    pres = sorted(((n.get("subnet_prefix") or "", n.get("slug")) for n in nets if n.get("subnet_prefix")),
                  key=lambda x: -len(x[0]))
    for r in rows:
        if not isinstance(r, dict) or r.get("net"):
            continue
        ip = r.get("client") or ""
        r["net"] = next((slug for pre, slug in pres if ip.startswith(pre + ".")), prim)
    return rows


def _attribute_flows(rows, nets):
    """`_attribute_dns` for flow rows: the device side is `ip`, not `client`."""
    if not nets:
        return rows
    prim = next((n["slug"] for n in nets if n.get("primary")), nets[0].get("slug"))
    pres = sorted(((n.get("subnet_prefix") or "", n.get("slug")) for n in nets if n.get("subnet_prefix")),
                  key=lambda x: -len(x[0]))
    for r in rows:
        if isinstance(r, dict) and not r.get("net"):
            ip = r.get("ip") or ""
            r["net"] = next((slug for pre, slug in pres if ip.startswith(pre + ".")), prim)
    return rows


# ---------------- helpers ----------------
def _int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _read_lines(path):
    """Non-empty lines of an append-only log, or [] if it does not exist."""
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return [l for l in (line.strip() for line in f) if l]
    except OSError:
        return []


def _parse_lines(lines):
    """Decode jsonl lines into the objects the hub's ingest expects. A line
    that will not parse is dropped but still counted in the offset, so one
    corrupt write can never wedge the sync forever."""
    out = []
    for l in lines:
        try:
            obj = json.loads(l)
        except Exception:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _write_json(path, obj, mode=None):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)
    if mode is not None:
        try:
            os.chmod(path, mode)
        except OSError:
            pass


# ---------------- edit replay ----------------
EDIT_PATHS = {"/api/device", "/api/device/forget", "/api/device/merge", "/api/device/quarantine",
              "/api/ack", "/api/alert", "/api/config", "/api/rec", "/api/attacker",
              "/api/maintenance", "/api/profile", "/api/reset-lan-baseline", "/api/dns/rules"}


def _primary_slug(st):
    """The primary network's slug as recorded in state: the explicit
    `state["lan"]["primary"]`, else the slice whose `known_macs` IS the legacy
    flat map (they are aliased by `runner._migrate_lan_state`), else the first."""
    ls = st.get("lan") or {}
    if ls.get("primary"):
        return ls["primary"]
    nets = ls.get("networks") or {}
    flat = ls.get("known_macs")
    for slug, v in nets.items():
        if flat is not None and (v or {}).get("known_macs") is flat:
            return slug
    return next(iter(nets), None)


def _known(st, net=None):
    """The `known_macs` map an edit applies to: the named network's when it is
    given and known, else the primary's (which is the legacy flat map, so a
    pre-addendum edit body with no `net` replays exactly as it used to)."""
    ls = st.setdefault("lan", {})
    nets = ls.get("networks")
    if isinstance(nets, dict) and nets:
        key = net if net and net in nets else None
        if key is None:
            # The hub names the primary network by ITS slug ("primary", from
            # the 0007 migration); this sensor's config may call it something
            # else ("home-network"). An edit for a slug we do not have is an
            # edit for the primary -- which is the only network a hub-side
            # "primary" can mean. It used to fall through to the legacy flat
            # map below, which is no longer the same object as the primary's
            # slice, so every such edit was applied to a map the next scan
            # threw away: 56 "trust this device" edits in a row silently did
            # nothing, and the hub's rows were then overwritten from the
            # sensor's unchanged state.
            key = next((k for k, v in nets.items() if isinstance(v, dict) and v.get("primary")), None) \
                or next(iter(nets))
        slice_ = nets[key] or {}
        nets[key] = slice_
        # `known_macs is None` means "no baseline yet, record one on the next
        # scan" — which is what `alerts._lan_alerts` emits `lan:baseline` for.
        # An edit arriving between a baseline reset and that scan therefore
        # applies to a throwaway map, never promoting None to {} (the
        # pre-Task-52 `setdefault(...) or {}` behaved the same way).
        return slice_.setdefault("known_macs", {}) or {}
    return ls.setdefault("known_macs", {}) or {}


#: Where `apply_edit` remembers the last config the hub sent, in the sensor's
#: persisted state. See `_merge_from_hub`.
HUB_CONFIG_SEEN_KEY = "hub_config_seen"


def _merge_from_hub(local: dict, new: dict, seen: dict) -> None:
    """Fold the hub's config into the sensor's, three ways.

    The hub always sends a WHOLE section (its entire `dns`, its entire
    `auto_fix`), so there are three kinds of key and each needs a different
    answer:

    - in `new`: the hub is speaking for it now -- take its value (recursing
      into dicts, so a section's sensor-only keys beside it survive);
    - in `seen` but not in `new`: the hub used to send it and has stopped --
      that is how the dashboard says "removed" (a per-host auto-fix override
      someone deleted), so delete it here too;
    - in neither: the hub has never spoken for it (`dns.enabled` set on the
      sensor, `dns.upstreams`, `capture`) -- leave it alone.

    The second rule depends on every `/api/config` edit carrying the hub's
    WHOLE `tenant.config`, which is true of all three emitters today
    (`routers/settings._write_config`, `routers/networks._commit_write`,
    `routers/integrations`). A hub path that sent only part of the config
    would make every key it left out look deleted -- so if one is ever added,
    it must send the whole thing too.

    A plain deep merge gets the third right and the second wrong: nothing the
    hub ever sent could be removed again. The old top-level `update` got the
    second right and the third wrong one level down, which is what wiped a
    locally enabled DNS filter's flag. Lists and scalars are values, not
    sections -- the hub's copy replaces them whole.
    """
    for key, value in new.items():
        if isinstance(value, dict) and isinstance(local.get(key), dict):
            prior = seen.get(key) if isinstance(seen.get(key), dict) else {}
            _merge_from_hub(local[key], value, prior)
        else:
            local[key] = json.loads(json.dumps(value))
    for key in seen:
        if key not in new:
            local.pop(key, None)


def apply_edit(e, state, config):
    """Replay one hub edit onto this sensor's state/config dicts.

    Returns "state", "config" or None (nothing matched). Both dicts are
    mutated in place; the caller persists whichever was touched.
    """
    p = e.get("path")
    b = e.get("body") or {}
    st = state
    cfg = config
    # Lazy -- only device-related branches touch `known_macs`, and it must
    # not be created as a side effect of unrelated edits (a caller replaying
    # e.g. "/api/dns/rules" onto a fresh `state={}` expects `state` to stay
    # untouched).
    DEVICE_PATHS = ("/api/device", "/api/device/forget", "/api/device/quarantine", "/api/device/merge")
    if p in DEVICE_PATHS:
        known = _known(st, b.get("net"))
    else:
        known = {}
    touched = None
    if p == "/api/device":
        m = b.get("mac")
        if m in known:
            for k in ("label", "note", "trusted", "always_on"):
                if k in b:
                    known[m][k] = b[k]
            touched = "state"
    elif p == "/api/device/forget":
        known.pop(b.get("mac"), None)
        st.setdefault("forgotten", []).append(b.get("mac"))
        touched = "state"
    elif p == "/api/device/quarantine":
        m = b.get("mac")
        if m in known:
            on = bool(b.get("on", True))
            known[m]["quarantined"] = on
            known[m]["trusted"] = False if on else known[m].get("trusted")
            touched = "state"
    elif p == "/api/device/merge":
        m = b.get("mac")
        par = b.get("parent")
        if m in known:
            if par and par in known:
                known[m]["parent"] = par
                known[m]["label"] = known[par].get("label")
                known[m]["trusted"] = known[par].get("trusted")
            elif not par:
                known[m].pop("parent", None)
            touched = "state"
    elif p == "/api/ack":
        acked = st.setdefault("acked", {})
        k = b.get("key")
        if b.get("clear"):
            acked.pop(k, None)
        else:
            acked[k] = {"ts": e.get("ts") or util.now_iso(), "note": b.get("note", "")}
        touched = "state"
    elif p == "/api/alert":
        adb = st.setdefault("alerts_db", {})
        acked = st.setdefault("acked", {})
        k = b.get("key")
        a = b.get("action")
        x = adb.get(k)
        if x:
            if a == "ack":
                x["state"] = "acked"
                acked[k] = {"ts": e.get("ts") or util.now_iso(), "note": b.get("note", "")}
            elif a == "snooze":
                x["state"] = "snoozed"
                x["snooze_until"] = (dt.datetime.now().astimezone() + dt.timedelta(hours=float(b.get("hours", 24)))).isoformat(timespec="seconds")
                acked.pop(k, None)
            elif a == "resolve":
                x.update(state="resolved", resolved_at=e.get("ts") or util.now_iso(), resolved_by="human")
                acked.pop(k, None)
            elif a == "reopen":
                x["state"] = "open"
                x.pop("snooze_until", None)
                acked.pop(k, None)
            touched = "state"
    elif p == "/api/maintenance":
        hrs = float(b.get("hours", 0) or 0)
        st["maintenance_until"] = (dt.datetime.now().astimezone() + dt.timedelta(hours=hrs)).isoformat(timespec="seconds") if hrs > 0 else None
        touched = "state"
    elif p == "/api/reset-lan-baseline":
        net = b.get("net")
        ls = st.setdefault("lan", {})
        nets = ls.get("networks") or {}
        if net and net in nets and net != _primary_slug(st):
            nets[net]["known_macs"] = None      # one secondary network only
        else:
            ls["known_macs"] = None             # the primary: legacy key + slice
            prim = _primary_slug(st)
            if prim and prim in nets:
                nets[prim]["known_macs"] = None
        touched = "state"
    elif p == "/api/rec":
        # a recommendation Myron answered on the dashboard: "yes" is consent
        # for the collector to run its concrete action on the next scan.
        recs = st.setdefault("rec_decisions", {})
        k = b.get("key")
        d = b.get("decision")
        if d == "clear":
            recs.pop(k, None)
        else:
            recs[k] = {"decision": d, "by": e.get("who") or "hub",
                       "ts": e.get("ts") or util.now_iso(), "status": ""}
        touched = "state"
    elif p == "/api/attacker":
        cfg.setdefault("auto_fix", {})["attacker_method"] = b.get("method") or "auto"
        touched = "config"
    elif p == "/api/profile":
        if "owner" in b:
            cfg["owner"] = b["owner"]
        if "profile" in b:
            cfg["profile"] = b["profile"]
        touched = "config"
    elif p == "/api/config":
        if isinstance(b.get("config"), dict):
            # MERGE, never replace. `cfg.clear()` here destroyed the sensor's
            # own configuration every time the dashboard saved anything:
            # the hub's `tenant.config` holds only what the dashboard owns
            # (`auto_fix`, `dns`), because sites and ssh hosts live in their
            # own tables -- so replaying one autofix edit wiped `sites`,
            # `ssh_hosts`, `lan`, `networks` and `thresholds` from
            # config.json. The sensor then scanned with nothing to scan:
            # `sites: []`, `ssh: []`, every target going stale, and the
            # Security posture reporting "1 of 7 scored" on a live hub.
            #
            # A deliberate removal still works: the dashboard's own targets
            # editor sends the whole `sites` list, so the key is PRESENT in
            # the payload and overwrites. What merging preserves is exactly
            # the keys the hub was never speaking for.
            #
            # And that has to hold INSIDE a section too, which a top-level
            # `update` did not: the hub's `dns` section never carries
            # `enabled`, so every dashboard save anywhere replaced the
            # sensor's whole `dns` block and switched a locally enabled
            # filter's flag off (2026-09-27, twice). See `_merge_from_hub`.
            prev = state.get(HUB_CONFIG_SEEN_KEY)
            _merge_from_hub(cfg, b["config"], prev if isinstance(prev, dict) else {})
            state[HUB_CONFIG_SEEN_KEY] = json.loads(json.dumps(b["config"]))
            touched = "config"
    elif p == "/api/dns/rules":
        # No reload code needed here -- dnsfilter.housekeeping() already
        # re-reads config.json every 60s (dnsfilter.py:345), so writing the
        # allow/block lists is enough.
        dns_cfg = cfg.setdefault("dns", {})
        if "allow" in b:
            dns_cfg["allow"] = b["allow"]
        if "block" in b:
            dns_cfg["block"] = b["block"]
        touched = "config"
    return touched


# ---------------- enrolment / credentials ----------------
def hub_file():
    return paths.p("hub.json")


def recorders_file():
    """The camera-recorder credential cache.

    Its OWN 0600 file, next to `hub.json`, and deliberately NOT `config.json`
    and NOT `state.json`:

    - `config.json` is world-readable, is what an `/api/config` edit rewrites
      wholesale, and is the file a support conversation asks the owner to
      paste. A recorder password replayed into it via the edit log would also
      sit in the hub's `edit` table in plaintext for ever, and be re-sent to
      every sensor on the site on every whole-config write.
    - `state.json` is the diagnostic dump.

    Being honest about the consequence: an NVR password does land on the
    sensor's disk. It has to -- the sensor is the only thing that can reach the
    recorder, and the recorder speaks no token or capability scheme. What this
    file buys is that the secret is in exactly one place, readable only by the
    account that runs the sensor, out of the config that gets pasted around,
    out of the state that gets shipped, and out of the hub's replayable edit
    log. The mitigation the owner should also take is a recorder account that
    can view channel status and nothing else.
    """
    return paths.p("recorders.json")


def load_recorders():
    """The cached recorder inventory, or [] when the hub has never answered."""
    try:
        with open(recorders_file()) as f:
            rows = (json.load(f) or {}).get("recorders")
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


def load_hub():
    """Return the saved enrolment dict, or {} when this sensor is not enrolled."""
    try:
        with open(hub_file()) as f:
            return json.load(f)
    except Exception:
        return {}


def client_from_disk():
    """A HubClient built from hub.json, or None when not enrolled."""
    h = load_hub()
    if h.get("hub_url") and h.get("token"):
        return HubClient(h["hub_url"], h["token"])
    return None


def enrol(code, hub_url):
    """Trade an enrolment code for a sensor token and save it to hub.json.
    Returns a process exit code (0 ok, 1 failed)."""
    url = (hub_url or "").rstrip("/")
    if not url:
        print("guardian-sensor: --hub is required")
        return 1
    body = {"code": (code or "").strip().upper(), "name": socket.gethostname(),
            "platform": sys.platform, "version": VERSION}
    try:
        j = _request(url + API_PREFIX + "/enrol", method="POST", body=body, timeout=30)
    except Exception as e:
        print(f"guardian-sensor: enrolment failed: {str(e)[:200]}")
        return 1
    token = (j or {}).get("token")
    if not token:
        print("guardian-sensor: enrolment failed: the hub returned no token")
        return 1
    tenant = j.get("tenant") or {}
    paths.ensure()
    _write_json(hub_file(), {"hub_url": url, "token": token, "tenant": tenant,
                             "sensor_id": j.get("sensor_id")}, mode=0o600)
    name = tenant.get("name") or tenant.get("slug") or "your hub"
    print(f"Enrolled with {name}. Credentials saved to {hub_file()}")
    return 0
