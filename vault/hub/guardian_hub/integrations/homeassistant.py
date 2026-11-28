"""Home Assistant: MQTT-discovery entities outbound, REST automations
inbound, and a presence *read* the other direction — HA's own
`device_tracker` state feeds Vault's own presence accuracy.

MQTT is spoken with a tiny stdlib client (`_mqtt_connect_packet`/
`_mqtt_publish_packet`/`_mqtt_publish_all`) — CONNECT + PUBLISH (QoS 0,
retained) + a graceful DISCONNECT, nothing else, no `paho-mqtt` dependency.
That is enough for HA discovery: no subscribe, no PUBACK/QoS1 handshake, no
reconnect logic — the hourly watchdog tick just opens a short-lived
connection, publishes every entity's config+state topic, and closes.

Two credentials on one row, and fix round 1 (finding 1 / ruling a) puts
BOTH of them under encryption: the MQTT password is `fields[0]` (backed by
`Integration.secret`) and the HA long-lived access token is now a
`kind="secret"` field stored in the encrypted `Integration.secrets_enc`
blob, read here as `ctx.secrets["ha_token"]`. It used to be declared
`kind="text"`, which put it in the plaintext `config` column AND echoed it
back to the client in the integration card — a long-lived HA token is full
control of the user's home, so that was the one hard constraint this module
broke. `0006_integration_secrets` moves any already-stored plaintext copy
into the blob on upgrade.

URL policy (finding 4 / ruling e): `ha_base_url` may be plain http only for
a private host (a home install genuinely is
`http://homeassistant.local:8123`), the MQTT host must be a private address
because this client speaks plain TCP, and loopback/link-local are rejected
either way — see `urlpolicy.py`.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import socket
import urllib.request

from .. import audit
from .. import models as m
from ..live import bus
from ..routers.write import apply_alert_op
from . import urlpolicy
from .base import Integration, IntegrationField, InboundResult, Message, SendContext, SendResult
from .registry import register

MQTT_TIMEOUT_S = 5
HA_HTTP_TIMEOUT_S = 10
_PRESENCE_SAMPLE_CAP = 500


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", (s or "guardian").lower()).strip("_") or "guardian"


# --------------------------------------------------------------------------
# Minimal stdlib MQTT 3.1.1 client: CONNECT + PUBLISH(QoS0) + DISCONNECT.
# --------------------------------------------------------------------------

def _mqtt_remaining_length(n: int) -> bytes:
    out = bytearray()
    while True:
        byte, n = n % 128, n // 128
        if n > 0:
            byte |= 0x80
        out.append(byte)
        if n <= 0:
            return bytes(out)


def _mqtt_connect_packet(client_id: str, user: str | None, password: str | None, keepalive: int = 30) -> bytes:
    variable_header = b"\x00\x04MQTT\x04"
    flags = 0x02   # clean session
    if user:
        flags |= 0x80
    if password:
        flags |= 0x40
    variable_header += bytes([flags]) + keepalive.to_bytes(2, "big")
    payload = len(client_id).to_bytes(2, "big") + client_id.encode()
    if user:
        payload += len(user).to_bytes(2, "big") + user.encode()
    if password:
        pw = password.encode()
        payload += len(pw).to_bytes(2, "big") + pw
    body = variable_header + payload
    return bytes([0x10]) + _mqtt_remaining_length(len(body)) + body


def _mqtt_publish_packet(topic: str, payload: str, retain: bool) -> bytes:
    topic_b = topic.encode()
    body = len(topic_b).to_bytes(2, "big") + topic_b + payload.encode()
    flags = 0x01 if retain else 0x00   # QoS 0 -> no packet id
    return bytes([0x30 | flags]) + _mqtt_remaining_length(len(body)) + body


MQTT_TLS_PORT = 8883


def _mqtt_uses_tls(port: int, ca_pem: str | None) -> bool:
    """TLS whenever the broker is on 8883 or a CA certificate is supplied."""
    return int(port) == MQTT_TLS_PORT or bool((ca_pem or "").strip())


def _mqtt_publish_all(host: str, port: int, user: str | None, password: str | None,
                       client_id: str, messages: list[tuple[str, str, bool]],
                       ca_pem: str | None = None) -> SendResult:
    """The one network call this module makes — monkeypatched wholesale in
    tests, same role as `_post` in the webhook-shaped providers.

    Plain TCP on a private host, or TLS (port 8883 / a CA given) anywhere:
    that is what lets a broker sit on a public address -- the owner's Home
    Assistant on a CloudHost VM -- without the MQTT password crossing the
    internet in the clear. The broker's certificate is verified against the
    CA the owner pasted (a self-signed CA is the normal case for a private
    broker), or the system store when none is given.
    """
    try:
        raw = socket.create_connection((host, port), timeout=MQTT_TIMEOUT_S)
        if _mqtt_uses_tls(port, ca_pem):
            import ssl
            ctx = ssl.create_default_context()
            if (ca_pem or "").strip():
                ctx.load_verify_locations(cadata=ca_pem.strip())
            # The certificate is issued to the IP/host we dialled; verify it.
            try:
                import ipaddress
                ipaddress.ip_address(host)
                ctx.check_hostname = False   # IP SAN is checked by verify_mode below via match_hostname on cert
            except ValueError:
                pass
            sock = ctx.wrap_socket(raw, server_hostname=host)
            if not ctx.check_hostname:
                # check_hostname off only skips the DNS-name match; the chain
                # is still verified (CERT_REQUIRED). Enforce the IP SAN by hand.
                cert = sock.getpeercert()
                sans = {v for k, v in cert.get("subjectAltName", ()) if k == "IP Address"}
                if host not in sans:
                    sock.close()
                    return SendResult(False, f"broker certificate is not issued for {host}")
        else:
            sock = raw
        with sock:
            sock.sendall(_mqtt_connect_packet(client_id, user, password))
            connack = sock.recv(4)
            if len(connack) < 4 or connack[0] != 0x20 or connack[3] != 0x00:
                return SendResult(False, "MQTT broker refused the connection")
            for topic, payload, retain in messages:
                sock.sendall(_mqtt_publish_packet(topic, payload, retain))
            sock.sendall(bytes([0xE0, 0x00]))   # DISCONNECT
        return SendResult(True, "")
    except Exception as e:
        return SendResult(False, str(e)[:200])


# --------------------------------------------------------------------------
# HA REST: presence read only (no HA write path needed for discovery).
# --------------------------------------------------------------------------

def _fetch_ha_states(base_url: str, token: str) -> list[dict]:
    req = urllib.request.Request(f"{base_url.rstrip('/')}/api/states",
                                  headers={"Authorization": f"Bearer {token}", "User-Agent": "guardian/1.0"})
    with urllib.request.urlopen(req, timeout=HA_HTTP_TIMEOUT_S) as resp:
        return json.loads(resp.read() or b"[]")


def _sync_ha_presence(ctx: SendContext, devices: list["m.Device"]) -> None:
    """Reads HA `device_tracker.*` states and writes a `{ts, home}` sample
    into each matched `Device.extra["ha_presence"]`. Matched by the
    tracker's `mac` attribute (most HA device trackers set one for a
    router-integration-backed entity) — a tracker with no `mac` attribute
    cannot be matched to a Vault `Device` and is skipped. Best-effort:
    any failure here must never break the MQTT discovery publish it runs
    alongside."""
    cfg = ctx.config or {}
    base_url = cfg.get("ha_base_url")
    if not base_url:
        return
    # Second line of defence behind `validate()`: a row saved before the
    # policy landed (or one whose config was hand-edited) must not get an
    # `Authorization: Bearer <long-lived HA token>` header sent to a
    # loopback/metadata address.
    if urlpolicy.check_url(base_url, "Home Assistant base URL", allow_plain_for_private=True):
        return
    try:
        states = _fetch_ha_states(base_url, ctx.secrets.get("ha_token", ""))
    except Exception:
        return

    home_by_mac: dict[str, bool] = {}
    for st in states:
        if not str(st.get("entity_id", "")).startswith("device_tracker."):
            continue
        mac = (st.get("attributes") or {}).get("mac")
        if not mac:
            continue
        mac_norm = str(mac).upper()
        home_by_mac[mac_norm] = home_by_mac.get(mac_norm, False) or (st.get("state") == "home")

    if not home_by_mac:
        return
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    for d in devices:
        mac_norm = d.mac.upper()
        if mac_norm not in home_by_mac:
            continue
        extra = dict(d.extra or {})
        samples = list(extra.get("ha_presence") or [])
        samples.append({"ts": now_iso, "home": home_by_mac[mac_norm]})
        extra["ha_presence"] = samples[-_PRESENCE_SAMPLE_CAP:]
        d.extra = extra   # reassignment (not in-place mutation) so the ORM marks it dirty


# --------------------------------------------------------------------------
# Discovery entity set (spec §10.1's Home Assistant list)
# --------------------------------------------------------------------------

def _discovery_messages(ctx: SendContext, devices: list["m.Device"]) -> list[tuple[str, str, bool]]:
    cfg = ctx.config or {}
    prefix = (cfg.get("discovery_prefix") or "homeassistant").rstrip("/")
    node = _slug(ctx.tenant.slug)
    device_info = {"identifiers": [f"guardian_{node}"], "name": f"Vault ({ctx.tenant.name})",
                   "manufacturer": "Vault"}

    db = ctx.db
    open_alerts = db.query(m.Alert).filter_by(tenant_id=ctx.tenant.id, status="open").all()
    crit = sum(1 for a in open_alerts if a.sev == "CRIT")
    warn = sum(1 for a in open_alerts if a.sev == "WARN")
    unverified = sum(1 for d in devices if not d.trusted and not d.label)
    sensor_row = (db.query(m.Sensor).filter_by(tenant_id=ctx.tenant.id)
                  .order_by(m.Sensor.last_seen.desc()).first())
    sensor_online = bool(sensor_row and sensor_row.status == "active")
    scan = db.query(m.Scan).filter_by(tenant_id=ctx.tenant.id).order_by(m.Scan.ts.desc()).first()
    score = scan.score if scan else None
    grade = ((scan.raw or {}).get("score") or {}).get("grade") if scan else ""
    day_ago = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
    dns_blocked = (db.query(m.DnsQuery)
                   .filter(m.DnsQuery.tenant_id == ctx.tenant.id, m.DnsQuery.blocked.is_(True),
                           m.DnsQuery.ts >= day_ago).count())

    messages: list[tuple[str, str, bool]] = []

    def entity(component: str, obj_id: str, name: str, state, device_class: str | None = None) -> None:
        base_topic = f"{prefix}/{component}/{node}/{obj_id}"
        cfg_payload = {"name": f"Vault {name}", "unique_id": f"guardian_{node}_{obj_id}",
                       "state_topic": f"{base_topic}/state", "device": device_info}
        if device_class:
            cfg_payload["device_class"] = device_class
        messages.append((f"{base_topic}/config", json.dumps(cfg_payload), True))
        messages.append((f"{base_topic}/state", str(state), True))

    entity("sensor", "security_score", "security score", score if score is not None else "")
    entity("sensor", "grade", "grade", grade or "")
    entity("sensor", "alerts_crit", "open critical alerts", crit)
    entity("sensor", "alerts_warn", "open warning alerts", warn)
    entity("sensor", "unverified_devices", "unverified devices", unverified)
    entity("binary_sensor", "sensor_online", "sensor online", "ON" if sensor_online else "OFF",
           device_class="connectivity")
    entity("sensor", "dns_blocked_24h", "DNS queries blocked (24h)", dns_blocked)

    for d in devices:
        obj_id = "device_" + re.sub(r"[^a-z0-9]+", "", d.mac.lower())
        label = d.label or d.name or d.mac
        entity("binary_sensor", obj_id, f"{label} online", "ON" if d.presence == "ONLINE" else "OFF",
               device_class="presence")

    return messages


@register
class HomeAssistantIntegration(Integration):
    kind = "homeassistant"
    title = "Home Assistant"
    help = "MQTT-discovery entities for score/alerts/devices, plus REST automations and device_tracker presence."
    direction = "out+in"
    fields = [
        IntegrationField(name="mqtt_password", label="MQTT password", kind="secret"),
        IntegrationField(name="mqtt_host", label="MQTT host", kind="text", required=True),
        IntegrationField(name="mqtt_port", label="MQTT port", kind="text"),
        IntegrationField(name="mqtt_ca", label="Broker CA certificate (PEM) — required for a public broker", kind="text"),
        IntegrationField(name="mqtt_user", label="MQTT username", kind="text"),
        IntegrationField(name="discovery_prefix", label="Discovery prefix", kind="text"),
        IntegrationField(name="ha_base_url", label="Home Assistant base URL", kind="url"),
        IntegrationField(name="ha_token", label="HA long-lived access token", kind="secret"),
    ]

    def validate(self, fields: dict) -> str:
        f = fields or {}
        tls = _mqtt_uses_tls(int(f.get("mqtt_port") or 1883), f.get("mqtt_ca"))
        return ((urlpolicy.check_host(f.get("mqtt_host") or "", "MQTT host") if not tls
                 else urlpolicy.check_public_host(f.get("mqtt_host") or "", "MQTT host"))
                or urlpolicy.check_url(f.get("ha_base_url") or "", "Home Assistant base URL",
                                        allow_plain_for_private=True))

    def send(self, ctx: SendContext, msg: Message) -> SendResult:
        cfg = ctx.config or {}
        host = cfg.get("mqtt_host")
        if not host:
            return SendResult(False, "no MQTT host configured")
        node = _slug(ctx.tenant.slug)
        prefix = (cfg.get("discovery_prefix") or "homeassistant").rstrip("/")
        topic = f"{prefix}/sensor/{node}/guardian_test/state"
        return _mqtt_publish_all(host, int(cfg.get("mqtt_port") or 1883), cfg.get("mqtt_user"), ctx.secret,
                                  f"guardian-{node}-test", [(topic, msg.body, False)], ca_pem=cfg.get("mqtt_ca"))

    def watchdog(self, ctx: SendContext) -> "SendResult | None":
        cfg = ctx.config or {}
        devices = ctx.db.query(m.Device).filter_by(tenant_id=ctx.tenant.id).all()

        # Presence read is independent of MQTT being configured — a tenant
        # may want HA-fed presence accuracy without wiring up discovery.
        try:
            _sync_ha_presence(ctx, devices)
        except Exception:
            pass

        host = cfg.get("mqtt_host")
        if not host:
            return None
        node = _slug(ctx.tenant.slug)
        messages = _discovery_messages(ctx, devices)
        result = _mqtt_publish_all(host, int(cfg.get("mqtt_port") or 1883), cfg.get("mqtt_user"), ctx.secret,
                                    f"guardian-{node}", messages, ca_pem=cfg.get("mqtt_ca"))
        # Finding 9: RETURN the failure instead of raising it. A raise here
        # skipped `watchdog_job`'s `else:` branch, so an unreachable broker
        # also threw away whatever `_sync_ha_presence` had just recorded in
        # `ctx.config` on the same tick. `watchdog_job` now writes the config
        # back in a `finally` either way, and records this error itself.
        if not result.ok:
            return SendResult(False, result.error or "MQTT publish failed")
        return None

    def inbound(self, ctx: SendContext, request_body: dict, headers: dict) -> InboundResult:
        db, tenant = ctx.db, ctx.tenant
        who = f"ha:{tenant.slug}"
        action = (request_body or {}).get("action", "")

        if action == "quarantine_device":
            mac = (request_body or {}).get("mac", "")
            d = db.query(m.Device).filter_by(tenant_id=tenant.id, mac=mac).first()
            if not d:
                return InboundResult(ok=False, body={"error": "device not found"}, status=404)
            d.quarantined = bool(request_body.get("on", True))
            db.add(m.Edit(tenant_id=tenant.id, who=who, path="/api/device/quarantine",
                           body={"mac": mac, "on": d.quarantined}))
            audit.log(db, tenant.id, who, "system", "device.update", target=mac, ip=ctx.ip,
                       body={"quarantined": d.quarantined})
            bus.publish(tenant.id, "device", {"mac": mac, "event": "update", "ip": d.ip, "label": d.label})
            return InboundResult(ok=True, body={"ok": True}, status=200)

        if action == "run_scan":
            from ..routers.write import queue_and_audit
            reply = queue_and_audit(db, tenant, who, "system", "/api/run", "run", body={}, ip=ctx.ip)
            return InboundResult(ok=True, body=reply, status=200)

        if action == "ack_alert":
            key = (request_body or {}).get("key", "")
            a = apply_alert_op(db, tenant, key, "ack", who, "system", ip=ctx.ip)
            if a is None:
                return InboundResult(ok=False, body={"error": "alert not found"}, status=404)
            return InboundResult(ok=True, body={"ok": True}, status=200)
        if action == "notify":
            # Home Assistant -> Vault -> every channel the tenant routes to.
            # This is the leg the owner's own mobile app rides on: an HA
            # automation POSTs {"action":"notify","title":..,"message":..,
            # "severity":"CRIT|WARN|INFO"} and Vault fans it out exactly
            # like one of its own alerts -- no Companion app, no HA exposed
            # to the internet, no domain.
            from .registry import Message, deliver_background
            title = str((request_body or {}).get("title") or "Home Assistant")[:120]
            body = str((request_body or {}).get("message") or (request_body or {}).get("body") or "")[:1000]
            sev = str((request_body or {}).get("severity") or "INFO").upper()
            if sev not in ("CRIT", "WARN", "INFO"):
                sev = "INFO"
            if not body:
                return InboundResult(ok=False, body={"error": "message required"}, status=422)
            audit.log(db, tenant.id, who, "system", "ha.notify", target=title, ip=ctx.ip, body={"severity": sev})
            db.commit()
            deliver_background(tenant.id, Message(title=title, body=body, severity=sev, kind="report",
                                                  dedup_key=f"ha:{title}"))
            return InboundResult(ok=True, body={"ok": True, "severity": sev}, status=200)

        return InboundResult(ok=False, body={"error": "unknown action"}, status=400)
