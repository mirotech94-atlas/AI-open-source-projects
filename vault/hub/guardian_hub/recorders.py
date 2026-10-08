"""Camera recorders (NVR/DVR) and the cameras behind them.

Task 73. The problem this solves, stated plainly: a household with ten or
twelve cameras has ONE camera-vendor device on its LAN -- the recorder. The
cameras sit on the recorder's own PoE network, behind it, and no sweep or
classifier will ever find them. `payloads/assets.kind_of` and the sensor's
`cameras.py` are both correct and both report one camera. The cameras have to
be asked for, from the recorder.

**Where the asking happens.** On the sensor: the recorder lives at a private
address the hub has no route to. The hub's job here is to hold the inventory
and the credential, hand both to the enrolled sensor over its authenticated
channel (`GET /api/v1/sensor/recorders`), and file the answer.

**What a camera behind a recorder IS.** A `Device` row on a `Network` of its
own, whose `expected_gateway` is the recorder. Not a child of the recorder's
device row: Assets groups by kind, Topology draws per network, Security counts
per network, `networks.counts` already counts cameras per network, and the
per-network "needs a sensor on this network" machinery already exists. Making
the recorder's cameras a network means every one of those surfaces works with
no special case -- the alternative (a `parent` pointer on the recorder's own
row) would need each of them taught a new relation, and would still leave the
cameras counted on the LAN they are not on.

That network is deliberately NOT in `Tenant.config["networks"]`: the sensor's
LAN loop is config-driven, so an entry there would make it try to ARP-sweep a
subnet it has no route to, mark it unreachable, and wipe the device rows this
module just wrote. The `Network` row exists hub-side only, which is exactly
what "a network Vault knows about but does not sweep" should look like.

**Credentials.** `Recorder.secret_enc` is one Fernet blob
(`crypto.encrypt_json`), the same at-rest treatment integration secrets get.
It is returned to exactly one caller -- the enrolled sensor -- and to no
other: not to the browser (`out()` reports `has_credentials`, never the
value), not to an alert body, not to a report, not to the audit log.
"""

from __future__ import annotations

import datetime as dt
import re

from sqlalchemy.orm import Session as DbSession

from . import crypto, models as m, networks

#: `vendor key -> display name`. Reolink only, because it is the hardware in
#: front of us and its HTTP API is published. Hikvision (ISAPI), Dahua and
#: UniFi Protect each need one entry here and one probe function in the
#: sensor's `nvr.VENDORS`; neither is written blind (see `nvr`'s docstring).
VENDORS: dict[str, str] = {"reolink": "Reolink"}

#: Vendors whose adapter is not written yet. Accepted on registration so the
#: owner can record the box, and reported honestly as `unsupported` rather
#: than silently never polled.
PLANNED_VENDORS: dict[str, str] = {
    "hikvision": "Hikvision", "dahua": "Dahua", "unifi": "UniFi Protect", "other": "Other",
}

ALL_VENDORS = {**VENDORS, **PLANNED_VENDORS}

_IP_RE = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# --------------------------------------------------------------------------
# The copy for "I could not read it"
# --------------------------------------------------------------------------
#: `error_kind -> (what happened, what only the owner can do)`. Mirrors
#: `guardian_sensor.nvr.ERROR_KINDS`. `remedy.HUMAN_REMEDIES` carries the same
#: sentences against the alert keys; this table is what the Recorders surface
#: shows on the row itself, so the answer is in front of the owner without
#: him having to find the alert first.
#:
#: The `http_disabled` wording is the one that matters most: it is the state
#: the owner's own recorder is in, and a silent zero there would be the
#: feature failing quietly. It names the setting.
ERROR_COPY: dict[str, tuple[str, str]] = {
    "http_disabled": (
        "The recorder's HTTP interface appears to be switched off. The HTTP port refused the "
        "connection, and only the recorder's proprietary client port is answering. Vault reads "
        "recorders over their documented HTTP API and will not speak an undocumented protocol.",
        "On the recorder, turn its HTTP port back on: Settings -> Network -> Advanced -> Port "
        "Settings, and enable the HTTP port (80) or HTTPS (443). In the Reolink app the same "
        "switches are under Device Settings -> Advanced -> Port Settings on newer firmware. Some "
        "firmware ships with only the proprietary port 9000 open. Once HTTP is on, tell me and I "
        "will read the recorder again. I check it on every scan.",
    ),
    "api_disabled": (
        "The recorder answers on HTTP but does not serve its documented API path, so its HTTP/ONVIF "
        "interface is disabled on this firmware.",
        "On the recorder, enable the HTTP API (and ONVIF, if your firmware lists it separately) "
        "under Settings -> Network -> Advanced, then tell me and I will read it again.",
    ),
    "auth_failed": (
        "The recorder rejected the username or password stored for it.",
        "Check the recorder's login and update it here. A view-only account with permission to see "
        "channel status is enough. Vault never needs an administrator on your recorder.",
    ),
    "unreachable": (
        "The recorder did not answer at all, so every camera behind it is unaccounted for.",
        "Check the recorder is powered on and on the network at the address recorded here; if you "
        "moved it, update the address.",
    ),
    "timeout": (
        "The recorder did not answer in time, so its cameras could not be listed.",
        "Check the recorder is powered on and reachable; if it is simply slow, tell me and I will "
        "keep watching it.",
    ),
    "protocol": (
        "The recorder answered something that is not its documented HTTP API.",
        "Confirm the address and port point at the recorder's web interface (not at its client "
        "port), then tell me and I will read it again.",
    ),
    "out_of_scope": (
        "The recorder's address is not inside any network configured on this site, so Vault did "
        "not contact it. Vault only ever talks to addresses inside your own networks.",
        "Either correct the recorder's address, or add the network it is on under Settings -> "
        "Networks.",
    ),
    "no_credentials": (
        "No username and password have been stored for this recorder yet, so there is nothing to "
        "log in with.",
        "Add the recorder's login here. A view-only account is enough.",
    ),
    "unsupported": (
        "Vault has no adapter for that make of recorder yet, so its cameras cannot be listed.",
        "Nothing you can do at the recorder: this one is on us. The cameras stay in your "
        "inventory as soon as support for that make lands.",
    ),
}

GENERIC_ERROR = (
    "Vault could not read the cameras behind this recorder.",
    "Tell me what you see on the recorder's own screen or app and I will take it from there.",
)


def error_copy(kind: str) -> tuple[str, str]:
    return ERROR_COPY.get(kind or "", GENERIC_ERROR)


# --------------------------------------------------------------------------
# Credentials
# --------------------------------------------------------------------------
def password(rec: m.Recorder) -> str:
    """The stored password, or "" when there is none (or the key rotated).
    Called by exactly two places: the sensor payload builder, and nothing
    else."""
    return (crypto.decrypt_json(rec.secret_enc or "") or {}).get("password") or ""


def set_password(rec: m.Recorder, plain: str) -> None:
    """Store (or clear) the recorder's password, encrypted at rest."""
    rec.secret_enc = crypto.encrypt_json({"password": plain}) if plain else ""


def has_credentials(rec: m.Recorder) -> bool:
    return bool(rec.username and rec.secret_enc)


# --------------------------------------------------------------------------
# Scope: only ever an address inside the tenant's own networks
# --------------------------------------------------------------------------
def lan_network_for(db: DbSession, tenant_id: str, host: str) -> m.Network | None:
    """The tenant's own network `host` sits inside, or None.

    A recorder Vault will not contact is a recorder it will not register:
    the hard rule (only addresses inside the tenant's configured networks) is
    enforced here at registration and again on the sensor at the moment of the
    request (`nvr.in_scope`), because a hub-side check alone would be undone
    by a stale credential cache or a network narrowed afterwards.
    """
    if not _IP_RE.match(host or ""):
        return None
    # Camera networks carry no `subnet_prefix`, so `for_client_ip` can only
    # ever match a real swept network -- a recorder can never be "inside"
    # another recorder's camera network.
    return networks.for_client_ip(db, tenant_id, host)


# --------------------------------------------------------------------------
# The camera network
# --------------------------------------------------------------------------
def camera_network(db: DbSession, tenant_id: str, rec: m.Recorder) -> m.Network:
    """The `Network` the cameras behind `rec` live on: created with the
    recorder, renamed with it, and never added to `Tenant.config["networks"]`
    (see the module docstring on why).

    `expected_gateway` is the recorder's own LAN address, which is the literal
    truth for these cameras -- everything they send leaves through it.
    """
    row = db.query(m.Network).filter_by(tenant_id=tenant_id, id=rec.network_id or "").first()
    name = f"{rec.name or rec.host} cameras"
    if row is None:
        row = m.Network(tenant_id=tenant_id, name=name,
                        slug=networks.unique_slug(db, tenant_id, name), kind="vlan",
                        expected_gateway=rec.host or "", colour="", is_primary=False,
                        status="pending")
        db.add(row)
        db.flush()
        rec.network_id = row.id
    else:
        row.name = name
        row.expected_gateway = rec.host or row.expected_gateway
    return row


# --------------------------------------------------------------------------
# What the sensor is given
# --------------------------------------------------------------------------
def sensor_payload(db: DbSession, tenant_id: str) -> list[dict]:
    """The recorder inventory handed to an enrolled sensor, credentials
    included. The ONLY code path that puts a recorder password on the wire."""
    rows = (db.query(m.Recorder)
            .filter_by(tenant_id=tenant_id)
            .order_by(m.Recorder.created).all())
    nets = {n.id: n for n in db.query(m.Network).filter_by(tenant_id=tenant_id).all()}
    out = []
    for r in rows:
        net = nets.get(r.network_id or "")
        out.append({
            "id": r.id, "name": r.name or r.host, "vendor": (r.vendor or "").lower(),
            "host": r.host, "port": int(r.port or 80), "scheme": r.scheme or "http",
            "username": r.username or "", "password": password(r),
            "expected_channels": int(r.expected_channels or 0),
            "enabled": bool(r.enabled), "net": net.slug if net else "",
        })
    return out


# --------------------------------------------------------------------------
# Filing what came back
# --------------------------------------------------------------------------
#: `Device` columns a camera row owns; everything else in the pushed entry
#: goes to `Device.extra`, exactly as `ingest`'s own device loop does it.
_DEV_COLS = ("ip", "name", "vendor", "kind", "presence", "ports", "first_seen", "last_seen")


def ingest(db: DbSession, tenant: m.Tenant, sensor, results: list, ts: str) -> dict:
    """File one scan's recorder results: the recorder rows, their camera
    networks, and one `Device` row per channel.

    Returns `{"recorders": n, "cameras": n}` for the ingest summary.

    Two things this deliberately does NOT do. It never deletes a device row
    for a channel that stopped being listed -- the row goes OFFLINE and the
    `nvr:cam-gone` finding explains it, because a recorder that drops a
    channel for a cycle must not silently erase the camera's history. And a
    failed probe touches no device row at all: "I could not read the recorder"
    is not "every camera left".
    """
    out = {"recorders": 0, "cameras": 0}
    for r in results or []:
        if not isinstance(r, dict) or not r.get("id"):
            continue
        rec = db.query(m.Recorder).filter_by(tenant_id=tenant.id, id=r["id"]).first()
        if rec is None:
            continue                      # a recorder deleted mid-scan
        out["recorders"] += 1
        rec.last_attempt = _now()
        net = camera_network(db, tenant.id, rec)
        if sensor is not None and not net.sensor_id:
            # The sensor that can read the recorder IS this network's sensor:
            # without it the overview says "needs a sensor on this network"
            # about a network it is actively reporting.
            net.sensor_id = sensor.id
        if not r.get("ok"):
            rec.status = "error"
            rec.error_kind = str(r.get("error_kind") or "")[:24]
            rec.error = str(r.get("error") or "")[:500]
            net.status = "unreachable"
            for device in db.query(m.Device).filter_by(tenant_id=tenant.id, network_id=net.id).all():
                if device.presence != "UNKNOWN":
                    device.extra = {**(device.extra or {}), "last_confirmed_presence":device.presence}
                device.presence = "UNKNOWN"
            continue
        rec.status = "ok"
        rec.error_kind = ""
        rec.error = ""
        rec.model = str(r.get("model") or "")[:64]
        rec.firmware = str(r.get("firmware") or "")[:64]
        rec.channels = [
            {"channel": int(c.get("channel") or 0), "name": str(c.get("name") or ""),
             "online": bool(c.get("online")), "model": str(c.get("model") or "")}
            for c in (r.get("channels") or []) if isinstance(c, dict)
        ]
        rec.last_ok = _now()
        net.status = "active"

        live: set[str] = set()
        for mac, d in (r.get("devices") or {}).items():
            if not isinstance(d, dict):
                continue
            live.add(mac)
            vals = {k: d.get(k) for k in _DEV_COLS if k in d}
            vals["extra"] = {k: v for k, v in d.items() if k not in _DEV_COLS}
            vals["kind"] = "camera"
            for k in ("ip", "name", "vendor", "presence", "first_seen", "last_seen"):
                vals[k] = vals.get(k) or ""
            vals["ports"] = vals.get("ports") or {}
            row = (db.query(m.Device)
                   .filter_by(tenant_id=tenant.id, network_id=net.id, mac=mac).first())
            if row is None:
                row = m.Device(tenant_id=tenant.id, network_id=net.id, mac=mac, **vals)
                db.add(row)
            else:
                # `first_seen` is written once, by the row's creation.
                vals.pop("first_seen", None)
                # A label the owner set in the dashboard is his, not the
                # recorder's -- the channel name never overwrites it.
                for k, v in vals.items():
                    setattr(row, k, v)
            out["cameras"] += 1

        # A channel the recorder no longer lists: OFFLINE, never deleted.
        for row in (db.query(m.Device)
                    .filter(m.Device.tenant_id == tenant.id, m.Device.network_id == net.id)
                    .all()):
            if row.mac not in live and row.presence != "OFFLINE":
                row.presence = "OFFLINE"
    db.flush()
    return out


# --------------------------------------------------------------------------
# API shape
# --------------------------------------------------------------------------
def out(db: DbSession, rec: m.Recorder, nets: dict | None = None) -> dict:
    """The `RecorderRow` a browser sees. Carries `has_credentials`, never the
    credential; carries the diagnosis and the owner's next step whenever the
    recorder could not be read, so "zero cameras" is never the whole answer."""
    nets = nets if nets is not None else {
        n.id: n for n in db.query(m.Network).filter_by(tenant_id=rec.tenant_id).all()}
    net = nets.get(rec.network_id or "")
    chans = rec.channels or []
    problem, fix = ("", "")
    if rec.status == "error":
        problem, fix = error_copy(rec.error_kind)
    elif rec.status == "pending":
        problem = "Vault has not read this recorder yet. It is read on the next sensor scan."
    return {
        "id": rec.id, "name": rec.name or rec.host, "vendor": (rec.vendor or "").lower(),
        "vendor_label": ALL_VENDORS.get((rec.vendor or "").lower(), rec.vendor or ""),
        "supported": (rec.vendor or "").lower() in VENDORS,
        "host": rec.host, "port": int(rec.port or 80), "scheme": rec.scheme or "http",
        "username": rec.username or "", "has_credentials": has_credentials(rec),
        "expected_channels": int(rec.expected_channels or 0),
        "enabled": bool(rec.enabled), "status": rec.status,
        "error_kind": rec.error_kind or "", "problem": problem, "owner_action": fix,
        "model": rec.model or "", "firmware": rec.firmware or "",
        "channel_count": len(chans),
        "channels_online": sum(1 for c in chans if c.get("online")) if rec.status == "ok" else 0,
        "channels": chans,
        "network_id": net.id if net else "",
        "network_slug": net.slug if net else "",
        "network_name": net.name if net else "",
        "last_ok": rec.last_ok.isoformat(timespec="seconds") if rec.last_ok else "",
        "last_attempt": rec.last_attempt.isoformat(timespec="seconds") if rec.last_attempt else "",
        "created": rec.created.isoformat(timespec="seconds") if rec.created else "",
    }


def listing(db: DbSession, tenant_id: str) -> list[dict]:
    nets = {n.id: n for n in db.query(m.Network).filter_by(tenant_id=tenant_id).all()}
    rows = (db.query(m.Recorder).filter_by(tenant_id=tenant_id)
            .order_by(m.Recorder.created).all())
    return [out(db, r, nets) for r in rows]
