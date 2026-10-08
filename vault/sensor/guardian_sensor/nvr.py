"""Cameras behind a recorder: the sensor-side NVR/DVR enumerator.

Why this exists: a house with ten or twelve cameras almost never has ten or
twelve cameras on the LAN. They hang off the recorder's own PoE ports, on the
recorder's own private network, and the only thing an ARP sweep of the LAN
ever sees is the recorder itself. `cameras.py` classifies what the sweep
found and will therefore report *one* camera for such a house, correctly and
uselessly. The cameras have to be asked for by name, from the one device that
can see them.

The hub cannot do the asking: the recorder lives on a private address the hub
has no route to. So the sensor does it, over the recorder's *documented* HTTP
API, and pushes the answer as an ordinary per-network device map (see
`hub/guardian_hub/recorders.py`).

**No proprietary protocols.** Reolink's port 9000 client protocol is
deliberately not implemented. On firmware where the HTTP interface is off,
this module reports `error_kind="http_disabled"` and the hub turns that into
copy naming the setting the owner has to enable. A silent zero would be the
wrong answer, and a reverse-engineered binary protocol would be a worse one.

**Vendors.** `VENDORS` maps a vendor key to a probe function with one shape:
`probe(rec, timeout) -> dict`. Reolink is implemented because it is the
hardware in front of us and its API is published. Hikvision (ISAPI
`/ISAPI/ContentMgmt/InputProxy/channels`), Dahua (`/cgi-bin/` + digest auth)
and UniFi Protect (`/proxy/protect/api/bootstrap`) each drop in as one more
entry with no change here or hub-side -- but none of them is written blind,
because an adapter nobody can test against real hardware is a liability
dressed as coverage.

**Identity.** A camera behind a recorder has no MAC the sensor can see and no
LAN address of its own, so it gets a stable synthetic identity derived from
`(recorder id, channel)` -- a locally-administered MAC (`02:…`), which can
never collide with a real vendor OUI, and which every existing surface that
keys on a MAC (device rows, presence samples, the device drawer, alert keys)
therefore accepts unchanged.

**Credentials** are never logged, never echoed into a probe result, and never
put in an alert message. `_redact` is applied to every error string that
could carry a URL.
"""

import hashlib
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

from . import util

#: Per-request timeout, seconds. A recorder is on the LAN; anything slower
#: than this is a fault, and the scan budget cannot absorb a long stall.
TIMEOUT = 8.0

#: How long a channel must have been offline before it is reported as a
#: camera that stopped answering, in seconds. Matches `cameras.GONE_AFTER_S`
#: -- a reboot or a single failed poll must never raise it.
GONE_AFTER_S = 30 * 60

#: Every `error_kind` a probe can return. The hub maps each to its own copy;
#: an unknown one falls back to the generic "could not read it" wording.
ERROR_KINDS = (
    "http_disabled",   # the HTTP port refused the connection -- the interface is off
    "api_disabled",    # HTTP answers, but the documented API path does not exist
    "auth_failed",     # the recorder rejected the username/password
    "unreachable",     # no route / unknown host
    "timeout",         # answered nothing in TIMEOUT
    "protocol",        # answered something that is not the documented API
    "out_of_scope",    # the address is not inside a network this tenant configured
    "no_credentials",  # registered, but no username/password has been stored yet
    "unsupported",     # no adapter for this vendor
)


def _redact(text):
    """Strip anything credential-shaped out of an error string before it is
    stored or shown: `user:pass@host` userinfo, and any `password=`/`token=`
    query parameter. Probe errors are surfaced in the UI verbatim, so this is
    the only place that can guarantee they carry no secret."""
    s = str(text or "")
    s = re.sub(r"//[^/@\s]+:[^/@\s]+@", "//<redacted>@", s)
    s = re.sub(r"((?:password|passwd|pwd|token|user|userName)=)[^&\s\"']+", r"\1<redacted>", s, flags=re.I)
    return s[:200]


def synthetic_mac(recorder_id, channel):
    """The stable identity of one camera behind one recorder.

    `02:` marks a locally-administered address, so this can never be mistaken
    for -- or collide with -- a real vendor OUI. Derived, not random, so the
    same channel on the same recorder is the same device row for ever.
    """
    h = hashlib.sha1(f"guardian-nvr:{recorder_id}:{channel}".encode()).hexdigest()
    return "02:" + ":".join(h[i:i + 2] for i in range(0, 10, 2))


def in_scope(host, prefixes):
    """Is `host` inside one of this tenant's own configured subnets?

    The hard rule is that Vault only ever contacts addresses inside the
    networks the tenant configured. The hub enforces it when a recorder is
    registered; this enforces it again at the moment of the request, so a
    tampered cache or a network that was narrowed afterwards cannot turn the
    sensor into a scanner of someone else's address space.
    """
    ip = (host or "").strip()
    if not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip):
        return False
    return any(p and ip.startswith(str(p) + ".") for p in (prefixes or ()))


# ---------------------------------------------------------------- transport
def _post(url, body, timeout):
    """One JSON POST. Returns the decoded body. Raises `NvrError` with an
    `error_kind` for everything the caller has to distinguish."""
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "guardian-sensor"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=util.SSL_CTX) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise NvrError("auth_failed", f"the recorder rejected the login (HTTP {e.code})")
        if e.code == 404:
            raise NvrError("api_disabled", "the recorder has no /cgi-bin/api.cgi (HTTP 404)")
        raise NvrError("protocol", f"HTTP {e.code} from the recorder")
    except urllib.error.URLError as e:
        raise NvrError(*_classify_urlerror(e))
    except socket.timeout:
        raise NvrError("timeout", "the recorder did not answer in time")
    except (ConnectionResetError, ConnectionRefusedError) as e:
        raise NvrError("http_disabled", f"the HTTP port closed the connection ({type(e).__name__})")
    except OSError as e:
        raise NvrError("unreachable", _redact(f"{type(e).__name__}: {e}"))
    try:
        return json.loads(raw or b"{}")
    except ValueError:
        # Reolink's port 9000 answers an HTTP request by resetting or by
        # returning binary: either way this is not the documented API.
        raise NvrError("protocol", "the recorder answered something that is not its HTTP API")


def _classify_urlerror(e):
    """`(error_kind, message)` for a urllib URLError.

    The distinction that matters: a *refused* or *reset* connection means the
    port is shut, which on a camera recorder means the HTTP interface has been
    turned off -- the one case that has to produce actionable copy rather than
    a shrug. A timeout or an unroutable address means the box itself is not
    answering, which is a different finding with a different fix.
    """
    reason = getattr(e, "reason", None)
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return "timeout", "the recorder did not answer in time"
    if isinstance(reason, (ConnectionRefusedError, ConnectionResetError)):
        return "http_disabled", "the HTTP port refused the connection"
    errno = getattr(reason, "errno", None)
    if errno in (61, 111):        # ECONNREFUSED on darwin / linux
        return "http_disabled", "the HTTP port refused the connection"
    if errno in (54, 104):        # ECONNRESET
        return "http_disabled", "the HTTP port closed the connection immediately"
    return "unreachable", _redact(f"{type(reason).__name__ if reason else 'URLError'}: {reason or e}")


class NvrError(Exception):
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind if kind in ERROR_KINDS else "protocol"
        self.message = _redact(message)


# ---------------------------------------------------------------- Reolink
REOLINK_PATH = "/cgi-bin/api.cgi"


def _reolink_call(base, cmd, body, timeout, token=None):
    """One Reolink api.cgi command. Returns the single response object.

    Reolink answers every command with a one-element list carrying its own
    `code`; a non-zero `code` is an application error, not an HTTP one, so it
    has to be read out of the body rather than off the status line.
    """
    q = {"cmd": cmd}
    if token:
        q["token"] = token
    url = base + REOLINK_PATH + "?" + urllib.parse.urlencode(q)
    out = _post(url, body, timeout)
    if isinstance(out, dict):
        out = [out]
    if not isinstance(out, list) or not out or not isinstance(out[0], dict):
        raise NvrError("protocol", f"unexpected answer to {cmd}")
    row = out[0]
    if int(row.get("code") or 0) != 0:
        detail = str(((row.get("error") or {}).get("detail")) or "").strip()
        rsp = ((row.get("error") or {}).get("rspCode"))
        if cmd == "Login" or rsp in (-7, -6):
            raise NvrError("auth_failed", detail or "the recorder rejected the username or password")
        raise NvrError("protocol", f"{cmd} failed: {detail or rsp or 'unknown error'}")
    return row


def reolink_probe(rec, timeout=TIMEOUT):
    """Log in, read the channel list, log out. Reolink's published HTTP API:
    `Login` -> a token, `GetChannelstatus` -> one row per channel with its
    name and whether the camera on it is online, `GetDevInfo` -> the
    recorder's own model and firmware.

    `GetDevInfo` is best-effort: the channel list is the answer this feature
    exists for, and a firmware that has renamed or restricted DevInfo must not
    cost the owner the enumeration.
    """
    base = f"{rec.get('scheme') or 'http'}://{rec['host']}:{int(rec.get('port') or 80)}"
    user = rec.get("username") or ""
    pw = rec.get("password") or ""
    login = _reolink_call(base, "Login", [{
        "cmd": "Login",
        "param": {"User": {"Version": "0", "userName": user, "password": pw}},
    }], timeout)
    token = (((login.get("value") or {}).get("Token") or {}).get("name")) or ""
    if not token:
        raise NvrError("protocol", "the recorder logged us in but returned no token")

    info = {}
    try:
        dev = _reolink_call(base, "GetDevInfo", [{"cmd": "GetDevInfo", "action": 0, "param": {}}],
                            timeout, token=token)
        info = (dev.get("value") or {}).get("DevInfo") or {}
    except NvrError:
        pass

    try:
        st = _reolink_call(base, "GetChannelstatus",
                           [{"cmd": "GetChannelstatus", "action": 0, "param": {}}], timeout, token=token)
        value = st.get("value") or {}
        rows = value.get("status") or []
        channels = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            ch = r.get("channel")
            if ch is None:
                continue
            channels.append({
                "channel": int(ch),
                "name": str(r.get("name") or "").strip(),
                "online": bool(int(r.get("online") or 0)),
                "model": str(r.get("typeInfo") or "").strip(),
            })
        channels.sort(key=lambda c: c["channel"])
        return {
            "channels": channels,
            "reported_count": int(value.get("count") or len(channels)),
            "model": str(info.get("model") or "").strip(),
            "firmware": str(info.get("firmVer") or "").strip(),
        }
    finally:
        try:
            _reolink_call(base, "Logout", [{"cmd": "Logout", "param": {}}], timeout, token=token)
        except Exception:
            pass          # a leaked lease expires on its own; never fail a probe on it


#: `vendor key -> probe(rec, timeout)`. See the module docstring on why there
#: is exactly one entry.
VENDORS = {"reolink": reolink_probe}


# ---------------------------------------------------------------- the pass
def probe(rec, prefixes=(), timeout=TIMEOUT):
    """One recorder -> one result dict, never raising.

    Shape: `{id, ok, error_kind, error, channels[], reported_count, model,
    firmware}`. `ok` false always carries an `error_kind` from `ERROR_KINDS`
    and a redacted `error`, because "nothing came back" has to be a stated
    reason the hub can write copy for, not an empty list.
    """
    out = {"id": rec.get("id") or "", "vendor": (rec.get("vendor") or "").lower(),
           "host": rec.get("host") or "", "ok": False, "error_kind": "", "error": "",
           "channels": [], "reported_count": 0, "model": "", "firmware": ""}
    fn = VENDORS.get(out["vendor"])
    if fn is None:
        out.update(error_kind="unsupported", error=f"no adapter for {out['vendor'] or 'this vendor'}")
        return out
    if not (rec.get("username") and rec.get("password")):
        out.update(error_kind="no_credentials", error="no username/password has been stored for this recorder")
        return out
    if not in_scope(out["host"], prefixes):
        out.update(error_kind="out_of_scope",
                   error="the recorder's address is not inside a network configured on this site")
        return out
    try:
        got = fn(rec, timeout)
    except NvrError as e:
        out.update(error_kind=e.kind, error=e.message)
        return out
    except Exception as e:                      # never let one recorder end the scan
        out.update(error_kind="protocol", error=_redact(f"{type(e).__name__}: {e}"))
        return out
    out.update(ok=True, **got)
    return out


def devices_for(rec, result, ts):
    """The per-network device map for one recorder's cameras.

    `{synthetic mac: {ip, name, vendor, kind, presence, ports, first_seen,
    last_seen, extra…}}` -- deliberately the same shape as a `known_macs`
    entry, so the hub files each camera as an ordinary device row on the
    recorder's network and every existing surface (Assets, Topology,
    Security, the per-network counts) works with no special case at all.

    `ip` is left empty on purpose: these cameras genuinely have no address on
    any network Vault can see, and inventing one would put a lie in the
    device drawer.
    """
    out = {}
    for ch in result.get("channels") or []:
        mac = synthetic_mac(rec.get("id") or "", ch["channel"])
        name = ch.get("name") or f"Channel {ch['channel'] + 1}"
        out[mac] = {
            "ip": "",
            "name": name,
            "vendor": (rec.get("vendor") or "").title(),
            "kind": "camera",
            "presence": "ONLINE" if ch.get("online") else "OFFLINE",
            "ports": {},
            "first_seen": ts,
            "last_seen": ts,
            "behind_recorder": rec.get("id") or "",
            "recorder_name": rec.get("name") or "",
            "channel": ch["channel"],
            "model": ch.get("model") or "",
            "synthetic_mac": True,
        }
    return out


def update_baseline(baseline, result, ts):
    """The stored per-recorder baseline: `{str(channel): {name, first_seen,
    last_online}}`.

    Its own state slice rather than something derived from the last result,
    for the same reason `cameras.update_baseline` is: "this camera used to
    answer and now does not" needs the last time it *did*, and a channel row
    keeps being returned by the recorder whether the camera on it answers or
    not.
    """
    out = dict(baseline or {})
    for ch in result.get("channels") or []:
        key = str(ch["channel"])
        row = dict(out.get(key) or {})
        row.setdefault("first_seen", ts)
        row["name"] = ch.get("name") or row.get("name") or ""
        if ch.get("online"):
            row["last_online"] = ts
        out[key] = row
    return out


def _age_s(then, now):
    import datetime as dt
    try:
        return (dt.datetime.fromisoformat(now) - dt.datetime.fromisoformat(then)).total_seconds()
    except Exception:
        return 0.0


def diff(baseline, result, ts):
    """`{"new": [...], "offline": [...], "vanished": [...]}` for one recorder.

    `baseline is None` -- this recorder has never been read -- learns silently
    and reports nothing, exactly like the LAN and camera-census baselines: a
    recorder registered in a house with twelve cameras must not open twelve
    findings on its first poll.

    - `new`: a channel the baseline has never seen (a camera was added).
    - `offline`: a channel the recorder still lists, whose camera has not
      answered for `GONE_AFTER_S` (unplugged, cut cable, dead PSU).
    - `vanished`: a channel that was in the baseline and is not in the list at
      all any more -- which is what removing a camera from the recorder looks
      like, and also what a recorder quietly losing a PoE port looks like.
    """
    if baseline is None:
        return {"new": [], "offline": [], "vanished": []}
    chans = {str(c["channel"]): c for c in (result.get("channels") or [])}
    new, offline = [], []
    for key, ch in sorted(chans.items(), key=lambda kv: int(kv[0])):
        row = baseline.get(key)
        if row is None:
            new.append(ch)
            continue
        if ch.get("online"):
            continue
        last = row.get("last_online")
        if last and _age_s(last, ts) >= GONE_AFTER_S:
            offline.append({**ch, "last_online": last})
    vanished = [{"channel": int(k), "name": (baseline.get(k) or {}).get("name") or ""}
                for k in sorted(baseline, key=lambda x: int(x) if str(x).isdigit() else 0)
                if k not in chans]
    return {"new": new, "offline": offline, "vanished": vanished}


def scan(recorders, prefixes, state, ts, timeout=TIMEOUT):
    """Probe every enabled recorder and return one result per recorder, each
    with its `devices` map and its `findings` diff attached.

    Baselines are NOT committed here -- `commit` does that, after the alert
    layer has diffed the previous one, which is what makes the first poll of a
    new recorder silent.
    """
    out = []
    for rec in recorders or []:
        if not isinstance(rec, dict) or not rec.get("host"):
            continue
        if not rec.get("enabled", True):
            continue
        r = probe(rec, prefixes, timeout)
        r["name"] = rec.get("name") or rec.get("host") or ""
        r["net"] = rec.get("net") or ""
        r["expected_channels"] = int(rec.get("expected_channels") or 0)
        r["devices"] = devices_for(rec, r, ts) if r["ok"] else {}
        base = ((state.get("recorders") or {}).get(r["id"]) or {}).get("channels")
        r["findings"] = diff(base, r, ts) if r["ok"] else {"new": [], "offline": [], "vanished": []}
        out.append(r)
    return out


def commit(results, state, ts):
    """Commit each recorder's channel baseline to `state["recorders"][id]`.

    Called after `build_alerts`, so the very first successful read of a
    recorder records its channels without raising a finding for each one. A
    failed probe leaves the baseline exactly as it was -- an unreachable
    recorder must never be read as "every camera vanished".
    """
    slot = state.setdefault("recorders", {})
    for r in results or []:
        if not r.get("ok"):
            continue
        row = dict(slot.get(r["id"]) or {})
        row["channels"] = update_baseline(row.get("channels"), r, ts)
        row["last_ok"] = ts
        slot[r["id"]] = row
    return slot
