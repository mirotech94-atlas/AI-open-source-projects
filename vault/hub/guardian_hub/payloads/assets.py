"""Assets tab payloads: Websites, Servers, Devices and the device drawer.

Every card is rendering-ready: uptime percentages, latency sparklines, gauge
states, the hardening grade, the kind chips and the drawer's timeline are all
computed here, from `Site.last` / `Host.last` / `Device` rows plus `Metric`,
`Event`, `Alert`, `Fix` and `DnsQuery`.

Device classification (`kind_of` + `KIND_GROUPS`) is a port of the Nexus
classifier so the chip set is exactly the eight groups the design names.
"""

import datetime as dt
import re

from fastapi import HTTPException
from sqlalchemy.orm import Session as DbSession

from .. import models as m, maintenance, networks
from ..scans import latest_scan, latest_scan_for_sensor, latest_scan_per_sensor, parse_ts
from ..schemas_dash import (
    ContainerRow, DeviceCard, DeviceDetail, DeviceGroup, DevicesPayload, DeviceTiles,
    Gauge, HardeningCheck, HardeningGrade, HistoryChart, HostCard, HostsPayload, InboxRow,
    EventRow, KindChip, NetworkHealth as sd_NetworkHealth, PortRow, SecurityHeaders,
    Series, ServiceRow, SiteCard, SitesPayload, TimelineItem, TrendPoint,
)
from . import dnsview as dnsview_payload
from . import history as history_mod
from . import presence as presence_mod
from . import familiarity, staleness
from ._util import effective_presence, device_online, _iso, _lax_cutoff, _live_macs, _now, incidents

# --------------------------------------------------------------------------
# Device classification (port of Nexus kind_of / KIND_GROUPS)
# --------------------------------------------------------------------------

KIND_GROUPS = {
    "phone": "Phones & tablets", "tablet": "Phones & tablets",
    "laptop": "Computers", "server": "Computers",
    "tv": "TVs & media", "speaker": "TVs & media",
    "camera": "Cameras",
    "printer": "Printers & appliances", "appliance": "Printers & appliances",
    "plug": "Smart home",
    "router": "Network gear", "extender": "Network gear",
    "unknown": "Unknown",
}

# Chip order the UI draws, so the strip never reshuffles between scans.
GROUP_ORDER = ["Cameras", "Phones & tablets", "Computers", "TVs & media",
               "Smart home", "Printers & appliances", "Network gear", "Unknown"]

# The sensor's own risky-port list (`guardian_sensor.lan.RISKY_PORTS`). The hub
# cannot import the sensor package, so it is mirrored here; the names double as
# the `service` label for those ports.
RISKY_PORTS = {23: "telnet", 445: "smb", 3389: "rdp", 5900: "vnc", 1900: "upnp", 21: "ftp"}


def _risk_reasons(d, unverified: bool) -> list:
    """The device risk score, line by line. Mirrors `guardian_sensor.lan.
    risk_score` exactly (the sensor computes the number; this explains it):
    unverified +40 / unlabelled-untrusted +10, any risky service +25, +5 per
    other open port up to +20, flapping +10, quarantined = 100."""
    from ..schemas_dash import RiskReason
    out = []
    if unverified:
        out.append(RiskReason(points=40, why="not verified: no label and not trusted"))
    elif not d.trusted and not (d.label or ""):
        out.append(RiskReason(points=10, why="unlabelled and not trusted"))
    ports = d.ports or {}
    nums = []
    for p in (ports.keys() if isinstance(ports, dict) else ports):
        try:
            nums.append(int(p))
        except (TypeError, ValueError):
            pass
    risky = sorted(n for n in nums if n in RISKY_PORTS)
    other = sorted(n for n in nums if n not in RISKY_PORTS)
    if risky:
        names = ", ".join(f"{RISKY_PORTS[n]} ({n})" for n in risky)
        out.append(RiskReason(points=25, why=f"exposes a risky service on the LAN: {names}"))
    if other:
        pts = min(20, 5 * len(other))
        out.append(RiskReason(points=pts, why=f"{len(other)} other open port(s): {', '.join(map(str, other))}"))
    if (d.extra or {}).get("flapping"):
        out.append(RiskReason(points=10, why="flapping (joins and leaves repeatedly)"))
    if d.quarantined:
        out.append(RiskReason(points=100, why="quarantined -- risk is pinned to 100"))
    return out

# Camera/NVR ports, mirroring `guardian_sensor.cameras` (the hub cannot import
# the sensor package). `CAMERA_PORTS_STRONG` classify a device on their own;
# `CAMERA_PORTS_WEAK` collide with generic DVR firmware and with ordinary
# development servers, so they only ever count alongside a second signal.
CAMERA_PORTS_STRONG = (554, 8554, 37777)
CAMERA_PORTS_WEAK = (34567, 8000)
CAMERA_VENDOR_RE = re.compile(
    r"reolink|hikvision|dahua|amcrest|axis communications|vivotek|mobotix|"
    r"hanwha|uniview|lorex|swann|foscam|annke|eufy|arlo|wyze labs|"
    r"shenzhen (?:gwell|reecam)|zosi|anpviz", re.I)
CAMERA_NAME_RE = re.compile(
    r"camera|ipcam|ip-cam|\bcam\b|cam-\d|\bnvr\d*\b|\bdvr\d*\b|doorbell|"
    r"reolink|hikvision|dahua|amcrest|foscam|wyze|arlo|eufy|blink|nest ?cam|"
    r"front ?door|driveway|backyard ?cam", re.I)
CAMERA_NAME_NOT_RE = re.compile(r"webcam|camera roll|photo", re.I)

PORT_SERVICES = {
    21: "ftp", 22: "ssh", 23: "telnet", 25: "smtp", 53: "dns", 80: "http", 110: "pop3",
    143: "imap", 161: "snmp", 443: "https", 445: "smb", 515: "printer", 548: "afp",
    554: "rtsp", 631: "ipp", 993: "imaps", 1883: "mqtt", 1900: "upnp", 2049: "nfs",
    3306: "mysql", 3389: "rdp", 5000: "upnp/airplay", 5432: "postgres", 5353: "mdns",
    5900: "vnc", 6379: "redis", 7000: "airplay", 8008: "chromecast", 8080: "http-alt",
    8443: "https-alt", 8883: "mqtts", 9100: "printer", 32400: "plex", 62078: "iphone-sync",
    8554: "rtsp-alt", 34567: "dvr", 37777: "dahua",
}


def _device_text(d) -> str:
    return " ".join([(getattr(d, "label", "") or ""), (getattr(d, "name", "") or ""),
                     (getattr(d, "vendor", "") or "")]).lower()


def _is_camera(s: str, ports: set[str]) -> bool:
    """The hub's mirror of `guardian_sensor.cameras.classify`, over the same
    split port/vendor tables. Used as the fallback for a device row the sensor
    never classified -- an imported or seeded tenant, or a scan from a sensor
    that predates Task 69."""
    strong = {str(p) for p in CAMERA_PORTS_STRONG} & ports
    weak = {str(p) for p in CAMERA_PORTS_WEAK} & ports
    if CAMERA_VENDOR_RE.search(s) or strong:
        return True
    if CAMERA_NAME_RE.search(s) and not CAMERA_NAME_NOT_RE.search(s):
        return True
    # A weak port alone is not a camera; two of them, or one plus a maker with
    # a camera line, is.
    return bool(weak) and (len(weak) > 1 or bool(re.search(r"amazon|ring |wyze|xiaomi|ubiquiti|tuya|hisilicon", s)))


def kind_of(device_row) -> str:
    """The device's kind, from its label/name/vendor and open ports.

    Port of Nexus `kind_of(d)`: an explicit `Device.kind` from the sensor wins,
    otherwise the text and port heuristics decide.
    """
    explicit = (getattr(device_row, "kind", "") or "").strip().lower()
    if explicit and explicit != "unknown":
        return explicit
    s = _device_text(device_row)
    ports = {str(p) for p in (getattr(device_row, "ports", None) or {})}
    # Cameras come FIRST: a camera-only vendor or an RTSP/Dahua port settles
    # the question, and several makers of mesh gear also make cameras -- left
    # after the router test, a Reolink NVR serving a web page would be filed as
    # a router. Everything below this line is unchanged.
    if _is_camera(s, ports):
        return "camera"
    if re.search(r"router|cr1000|gateway|wnc", s):
        return "router"
    if re.search(r"extender|ce1000|arcadyan", s):
        return "extender"
    if re.search(r"printer|canon|brother|epson|hp ", s) or "9100" in ports or "631" in ports:
        return "printer"
    if re.search(r"iphone|pixel|galaxy|phone", s):
        return "phone"
    if re.search(r"ipad|tablet", s):
        return "tablet"
    if re.search(r"macbook|laptop|mac\b|windows pc|dell|lenovo|thinkpad|intel", s):
        return "laptop"
    if re.search(r"tv|fire tv|roku|chromecast|streaming|apple tv|cast", s) or "8008" in ports:
        return "tv"
    if re.search(r"echo|alexa|sonos|homepod|speaker|amazon", s):
        return "speaker"
    if re.search(r"range|oven|fridge|washer|dryer|dishwasher|thermostat|ecobee|samsung", s):
        return "appliance"
    if re.search(r"plug|bulb|hui zhou|tuya|espressif|iot|smart", s):
        return "plug"
    if re.search(r"server|nas|synology|pi\b|raspberry", s):
        return "server"
    return "unknown"


def group_of(kind: str) -> str:
    return KIND_GROUPS.get(kind, "Unknown")


def _randomized(mac: str) -> bool:
    """True when the MAC's locally-administered bit is set (a privacy MAC)."""
    try:
        return bool(int(mac.split(":")[0], 16) & 2)
    except (ValueError, IndexError, AttributeError):
        return False


def _port_rows(ports) -> list[PortRow]:
    """`Device.ports` is `{port: first-seen}`; `Host.last["ports"]` is a list."""
    rows = []
    if isinstance(ports, dict):
        items = ports.items()
    else:
        items = [(p, "") for p in (ports or [])]
    for port, since in items:
        try:
            n = int(port)
        except (TypeError, ValueError):
            continue
        rows.append(PortRow(port=n, since=str(since or ""), risky=n in RISKY_PORTS,
                            service=PORT_SERVICES.get(n, "")))
    rows.sort(key=lambda r: r.port)
    return rows


# --------------------------------------------------------------------------
# Websites
# --------------------------------------------------------------------------

LATENCY_SPARK_POINTS = 48


#: Below this many samples, two decimal places on an availability figure are
#: invented precision: across 8 checks the only reachable values are multiples
#: of 12.5, so "87.50%" reads as a measurement when it means "7 of 8".
#:
#: 20 is chosen against the sweep cadences that actually exist rather than as
#: a round number. The demo tenant writes one sample every 30 minutes (48 a
#: day) and a real sensor sweeps about every 6 minutes (240 a day), so 20 is
#: comfortably below both -- it suppresses a figure only for a site added in
#: the last couple of hours, which is exactly the case where there is nothing
#: honest to report. The plan's Metric Contract suggests 100, which assumes a
#: 60-second check; at 30-minute sampling that would blank a site for two
#: days and teach people the field is broken.
MIN_UPTIME_SAMPLES = 20


def _uptime(vals: list[bool]) -> float | None:
    """Availability over `vals`, truncated to 2dp, or None below the minimum.

    TRUNCATES, never rounds. `round()` is half-up, so a site that managed
    99.849% was being published as 99.85% -- the wrong direction for a
    number whose job is to be a floor someone can hold you to. Rounding up
    silently converts a missed target into a met one, which is the single
    cheapest way to lose an argument with a customer who kept their own
    logs.

    What this does NOT do is separate "checked, and the answer was unknown"
    from "never checked". A sweep that did not run leaves no `up` metric
    behind at all, so both cases are simply absent from `vals` and neither
    inflates nor deflates the result. That is the correct denominator by
    accident rather than by design, and the honest half of the plan's
    §2.5A: publishing an `unknown` count needs the sensor to record the
    attempt first, and manufacturing the distinction here would be the
    fabrication the rest of this function exists to avoid.
    """
    if len(vals) < MIN_UPTIME_SAMPLES:
        return None
    # int() truncates toward zero; every value here is non-negative.
    return int(10000 * sum(vals) / len(vals)) / 100


def sites(db: DbSession, tenant) -> SitesPayload:
    rows = db.query(m.Site).filter_by(tenant_id=tenant.id).order_by(m.Site.name).all()
    if not rows:
        return SitesPayload()

    now = _now()
    week = now - dt.timedelta(days=7)
    day = now - dt.timedelta(days=1)
    cutoff = staleness.stale_cutoff(db, tenant.id)
    wins = maintenance.windows(tenant)

    # One metric query for every site, then bucket in Python -- no per-site
    # round trip.
    metrics = (
        db.query(m.Metric.name, m.Metric.entity, m.Metric.ts, m.Metric.value)
        .filter(m.Metric.tenant_id == tenant.id,
                m.Metric.name.in_(("up", "latency_ms")),
                m.Metric.ts >= _lax_cutoff(week))
        .all()
    )
    ups: dict[str, list[tuple[dt.datetime, bool]]] = {}
    lats: dict[str, list[tuple[dt.datetime, float]]] = {}
    for name, entity, ts, value in metrics:
        when = parse_ts(ts)
        if when is None or when < week:
            continue
        if name == "up":
            ups.setdefault(entity, []).append((when, bool(value)))
        elif value is not None:
            lats.setdefault(entity, []).append((when, float(value)))

    out = []
    for s in rows:
        last = s.last or {}
        sec = last.get("sec") or {}
        series = sorted(ups.get(s.name, []))
        # Planned maintenance is excluded from BOTH halves of the fraction,
        # not just the numerator. Counting a window's checks as "up" would
        # reward taking a site down on purpose; counting them as down is the
        # fake outage this exists to stop. Neither is a measurement of
        # availability, so the window simply is not measured -- and the
        # count of what was skipped is published so the exclusion is
        # visible rather than something the figure quietly benefits from.
        week_vals = [ok for when, ok in series if not maintenance.covers(wins, when)]
        day_vals = [ok for when, ok in series
                    if when >= day and not maintenance.covers(wins, when)]
        week_planned = sum(1 for when, _ in series if maintenance.covers(wins, when))
        day_planned = sum(1 for when, _ in series
                          if when >= day and maintenance.covers(wins, when))
        lat_series = sorted(lats.get(s.name, []))
        latency_s = last.get("latency_s")
        out.append(SiteCard(
            name=s.name, url=s.url, ok=last.get("ok"), code=last.get("code"),
            ms=round(latency_s * 1000, 1) if latency_s is not None else None,
            uptime_24h=_uptime(day_vals),
            uptime_7d=_uptime(week_vals),
            # The denominator travels with the percentage: 100% over 21 checks
            # and 100% over 2,000 are different claims and rendered the same.
            uptime_24h_checks=len(day_vals),
            uptime_7d_checks=len(week_vals),
            uptime_24h_planned=day_planned,
            uptime_7d_planned=week_planned,
            latency_spark=[round(v, 1) for _, v in lat_series[-LATENCY_SPARK_POINTS:]],
            cert_days=last.get("cert_days_left"),
            cert_expires=last.get("cert_expires") or "",
            cert_issuer=last.get("cert_issuer") or "",
            dns_a=list(last.get("dns_a") or []),
            headers=SecurityHeaders(
                hsts=bool(sec.get("hsts")), nosniff=bool(sec.get("nosniff")),
                frame=bool(sec.get("frame")), csp=bool(sec.get("csp")),
                https_redirect=bool(sec.get("https_redirect")),
                old_tls=bool(sec.get("old_tls")), server=str(sec.get("server") or ""),
            ),
            incidents_7d=incidents(week_vals),
            public=bool(s.public),
            stale=staleness.is_stale(s.last_seen, cutoff),
            stale_note=staleness.stale_note(s.last_seen) if staleness.is_stale(s.last_seen, cutoff) else "",
            last_checked=s.last_seen or "",
        ))
    return SitesPayload(sites=out, charts=site_charts(db, tenant))


# --------------------------------------------------------------------------
# The three site trend panels (Task 77 / F1)
# --------------------------------------------------------------------------

#: The window the three site trend panels cover. The reference draws cert days
#: and availability over 7 days and latency over the History range; Vault has
#: no range picker on this tab, so all three share the reference's 7 days and
#: History remains the place to widen a window.
SITE_TREND_DAYS = 7


def site_charts(db: DbSession, tenant) -> list[HistoryChart]:
    """Latency, certificate days left and availability per site, as the same
    rendering-ready `HistoryChart` History draws.

    Every series comes from `history._metric_series`, so the bucketing,
    decimation and per-entity ordering are literally the same code as the
    History tab's -- the two tabs can never disagree about a site's latency.
    `cert_days` and `up` are written by `ingest` on every scan but are not in
    History's own chart list (a certificate is not a trend anyone watches
    hourly), which is exactly why these three panels had no data to draw.
    """
    cutoff = _now() - dt.timedelta(days=SITE_TREND_DAYS)
    charts: list[HistoryChart] = []

    latency = history_mod._metric_series(db, tenant.id, "latency_ms", cutoff)
    if latency:
        charts.append(HistoryChart(key="site_latency", label="Latency history", unit="ms",
                                   series=latency))

    cert = history_mod._metric_series(db, tenant.id, "cert_days", cutoff)
    if cert:
        charts.append(HistoryChart(key="site_cert", label="Certificate days left", unit="days",
                                   series=cert))

    # `up` is stored as 1/0 per probe, so an hourly mean scaled by 100 IS
    # availability for that hour -- the same arithmetic the cards' `uptime_7d`
    # does over the whole window, hour by hour.
    availability = history_mod._metric_series(db, tenant.id, "up", cutoff, scale=100.0)
    if availability:
        charts.append(HistoryChart(key="site_uptime", label="Availability", unit="%", max=100.0,
                                   series=availability))
    return charts


# --------------------------------------------------------------------------
# Servers
# --------------------------------------------------------------------------

# The Nexus `CK` hardening scorecard, in its original order.
HARDENING_CHECKS: list[tuple[str, str]] = [
    ("Key-only SSH", "ssh_password"),
    ("Firewall", "ufw"),
    ("fail2ban", "fail2ban"),
    ("No security updates pending", "security_updates"),
    ("Auto-updates", "unattended"),
    ("No malware signs", "malware"),
    ("No reboot pending", "reboot"),
]

DEFAULT_THRESHOLDS = {"disk_pct_warn": 90.0, "mem_pct_warn": 90.0, "load_per_core_warn": 2.0}


def _int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def hardening(last: dict) -> HardeningGrade:
    """The seven-check Nexus scorecard, scored out of 100 and graded."""
    sec = last.get("sec") or {}
    security_updates = sec.get("security_updates") or []
    miners = sec.get("miners") or []
    results = {
        "ssh_password": (not sec.get("ssh_password"),
                         "key-only" if not sec.get("ssh_password") else "password login allowed"),
        "ufw": (sec.get("ufw") == "active", str(sec.get("ufw") or "not detected")),
        "fail2ban": (last.get("fail2ban") == "active", str(last.get("fail2ban") or "not running")),
        "security_updates": (not security_updates,
                             f"{len(security_updates)} pending" if security_updates else "none pending"),
        "unattended": (bool(sec.get("unattended")),
                       "enabled" if sec.get("unattended") else "disabled"),
        "malware": (not miners and not sec.get("preload"),
                    "clean" if not miners and not sec.get("preload") else "suspicious binaries found"),
        "reboot": (not last.get("reboot_required"),
                   "up to date" if not last.get("reboot_required") else "reboot required"),
    }
    checks = [HardeningCheck(name=name, passed=results[key][0], detail=results[key][1])
              for name, key in HARDENING_CHECKS]
    passed = sum(1 for c in checks if c.passed)
    score = round(100 * passed / len(HARDENING_CHECKS))
    grade = "A" if score >= 90 else "B" if score >= 80 else "C" if score >= 70 else "D" if score >= 60 else "F"
    return HardeningGrade(score=score, grade=grade, checks=checks)


def _gauge(name: str, value, unit: str, maximum: float, warn: float) -> Gauge:
    """`warn` comes from the tenant's thresholds; crit is 10 % past it."""
    state = "ok"
    if value is not None:
        crit = min(maximum, warn * 1.1) if unit == "%" else warn * 1.5
        if float(value) >= crit:
            state = "crit"
        elif float(value) >= warn:
            state = "warn"
    return Gauge(name=name, value=None if value is None else round(float(value), 2),
                 unit=unit, max=maximum, state=state)


def hosts(db: DbSession, tenant) -> HostsPayload:
    rows = db.query(m.Host).filter_by(tenant_id=tenant.id).order_by(m.Host.name).all()
    if not rows:
        return HostsPayload()

    cutoff = staleness.stale_cutoff(db, tenant.id)
    cfg = (tenant.config or {}).get("thresholds") or {}

    def th(key: str) -> float:
        # Accept both the `*_warn` names the design uses and the bare names a
        # Nexus-imported config.json carries.
        bare = key.replace("_warn", "")
        for candidate in (key, bare):
            if cfg.get(candidate) is not None:
                try:
                    return float(cfg[candidate])
                except (TypeError, ValueError):
                    break
        return DEFAULT_THRESHOLDS[key]

    disk_warn, mem_warn, load_warn = th("disk_pct_warn"), th("mem_pct_warn"), th("load_per_core_warn")

    out = []
    for h in rows:
        last = h.last or {}
        sec = last.get("sec") or {}
        updates = last.get("updates") or {}
        docker = []
        for entry in last.get("docker") or []:
            if isinstance(entry, dict):
                docker.append(ContainerRow(name=entry.get("name", ""), status=entry.get("status", ""),
                                           image=entry.get("image", "")))
            else:
                name, _, status = str(entry).partition(":")
                docker.append(ContainerRow(name=name, status=status, image=""))
        services_raw = last.get("services") or {}
        if isinstance(services_raw, dict):
            services = [ServiceRow(name=k, state=str(v)) for k, v in services_raw.items()]
        else:
            services = [ServiceRow(name=str(x), state="") for x in services_raw]

        out.append(HostCard(
            name=h.name, address=h.address, reachable=last.get("reachable"),
            hostname=last.get("hostname") or "", uptime=str(last.get("uptime") or ""),
            load1=last.get("load1"), load_per_core=last.get("load_per_core"),
            disk_pct=last.get("disk_pct"), mem_pct=last.get("mem_pct"),
            failed_ssh_24h=_int(last.get("failed_ssh_24h")),
            banned=_int(last.get("banned")),
            fail2ban=(last.get("fail2ban") == "active") if last.get("fail2ban") is not None else None,
            reboot_required=bool(last.get("reboot_required")),
            pending_updates=_int(updates.get("count", last.get("pending_updates"))),
            security_updates=len(sec.get("security_updates") or []),
            cve_fixable=_int(last.get("cve_fixable")),
            hardening=hardening(last),
            ports=_port_rows(last.get("ports")),
            docker=docker, services=services,
            gauges=[
                _gauge("load/core", last.get("load_per_core"), "", max(2.0, load_warn * 2), load_warn),
                _gauge("disk %", last.get("disk_pct"), "%", 100.0, disk_warn),
                _gauge("memory %", last.get("mem_pct"), "%", 100.0, mem_warn),
            ],
            stale=staleness.is_stale(h.last_seen, cutoff),
            stale_note=staleness.stale_note(h.last_seen) if staleness.is_stale(h.last_seen, cutoff) else "",
            last_checked=h.last_seen or "",
        ))
    return HostsPayload(hosts=out)


# --------------------------------------------------------------------------
# Devices
# --------------------------------------------------------------------------

def _unverified_macs(raw: dict) -> set[str]:
    out = set()
    for u in ((raw.get("home") or {}).get("unverified") or []):
        mac = u.get("mac") if isinstance(u, dict) else u
        if mac:
            out.add(mac)
    return out


#: The window `DeviceCard.online_7d` covers -- the reference console's own
#: "Online 7d" column.
DEVICE_ONLINE_DAYS = 7


def device_card(d: m.Device, live: set[str], unverified: set[str],
                 netrefs: dict | None = None, online_pct: dict[str, float] | None = None) -> DeviceCard:
    """One device card. `online_pct` is the whole tenant's (or network's)
    presence percentages, computed ONCE by the caller through
    `presence.online_pct` -- never per row, and never a per-card query. Left
    out (the drawer's child cards) the field stays None, which the table reads
    as "not measured for this shape", not as 0 %.
    """
    kind = kind_of(d)
    return DeviceCard(
        network=(netrefs or {}).get(d.network_id),
        online_7d=(online_pct or {}).get(d.mac),
        mac=d.mac, ip=d.ip or "", name=d.name or "", label=d.label or "",
        vendor=d.vendor or "", kind=kind, group=group_of(kind),
        trusted=bool(d.trusted), always_on=bool(d.always_on), quarantined=bool(d.quarantined),
        parent=d.parent, risk=int(d.risk or 0), presence=effective_presence(d),
        online=device_online(d), first_seen=d.first_seen or "", last_seen=d.last_seen or "",
        ports=_port_rows(d.ports), unverified=d.mac in unverified, randomized=_randomized(d.mac),
        # Why Vault thinks this is a camera, straight from the sensor's own
        # classifier (`known_macs[mac]["camera"]`, which ingest keeps in
        # `Device.extra`). Empty for a row classified by the text fallback
        # above, which the drawer renders as "identified from its name".
        camera_reasons=list(((d.extra or {}).get("camera") or {}).get("reasons") or []),
    )


def _num(value) -> float | None:
    """A speed/latency figure as a float, or None for a missing or junk one.
    Nothing here may fall back to 0: `0 Mbps` is a claim about the link, while
    a missing sample is a claim about the sensor."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _wan_ip_of(raw: dict) -> str:
    """The public address a scan measured. The exposure probe's own reading
    wins over the plain lookup, which is the order `payloads.security` uses --
    both surfaces must never disagree about a tenant's WAN address."""
    exposure = (raw.get("home") or {}).get("exposure") or {}
    return str(exposure.get("wan_ip") or raw.get("wan_ip") or "")


def _new_device_count(raw: dict, net) -> int:
    """Devices this scan saw for the first time, scoped like every other number
    on the page.

    `raw["networks"]` is the sensor's per-network sweep list, so a selected
    network is matched on its slug and "All networks" sums the lot. A scan from
    a sensor that predates per-network sweeps carries only `raw["lan"]`, which
    IS the primary network's entry -- so it counts for the primary and for the
    unscoped read, and for no other network.
    """
    entries = [e for e in (raw.get("networks") or []) if isinstance(e, dict)]
    if entries:
        if net is not None:
            entries = [e for e in entries if e.get("slug") == net.slug]
        return sum(len(e.get("new_devices") or []) for e in entries)
    if net is not None and not net.is_primary:
        return 0
    return len(((raw.get("lan") or {}).get("new_devices") or []))


def _sensor_raw(db: DbSession, tenant, net, fallback: dict) -> dict:
    """The scan whose host-level figures (speed, DNS, WAN) apply to `net`.

    A speed test and a resolver timing are properties of the machine that ran
    them, so with a network selected they must come from THAT network's
    sensor's own most recent scan rather than from whichever sensor happened to
    push last. Falls back to the tenant's latest scan for a network with no
    sensor bound, and for the unscoped read.
    """
    if net is not None and net.sensor_id:
        scan = latest_scan_for_sensor(db, tenant.id, net.sensor_id)
        if scan is not None:
            return scan.raw or {}
    return fallback


def _wan_ips(db: DbSession, tenant, net, fallback: dict) -> list[str]:
    """Every distinct public address in the current selection, sorted.

    A network's egress is its sensor's egress, so this reads one address per
    sensor (each from that sensor's own latest scan) and de-duplicates: a
    single-sensor tenant has ONE address however many networks it watches,
    which is why `_wan_ip_of` on the tenant's latest scan is the right fallback
    and not a per-network guess.
    """
    if net is not None:
        ip = _wan_ip_of(_sensor_raw(db, tenant, net, fallback))
        return [ip] if ip else []
    ids = {n.sensor_id for n in db.query(m.Network).filter_by(tenant_id=tenant.id).all()
           if n.sensor_id}
    # One query for every sensor's newest scan, not one query per sensor: this
    # runs on a read path the Devices page re-fetches on every live `device`
    # frame, and each candidate row carries a whole scan payload.
    found = {_wan_ip_of(scan.raw or {})
             for scan in latest_scan_per_sensor(db, tenant.id, ids).values()}
    found.discard("")
    if not found:
        ip = _wan_ip_of(fallback)
        return [ip] if ip else []
    return sorted(found)


def _tiles(db: DbSession, tenant, net, raw: dict, cards: list[DeviceCard]) -> DeviceTiles:
    """The nine-tile row, from data already ingested and nothing else."""
    host = _sensor_raw(db, tenant, net, raw)
    speed = host.get("speed") or {}
    # The sensor writes `down_mbps`/`up_mbps`/`latency_ms`; the history line it
    # keeps alongside writes `down`/`up`/`lat`. Both shapes reach `Scan.raw`
    # depending on the sensor's age, so read either -- the same dual read
    # `payloads.overview` does for its internet question.
    system = (host.get("dns") or {}).get("system")
    if not isinstance(system, dict):
        system = {"ms": system} if system is not None else {}
    ips = _wan_ips(db, tenant, net, raw)
    return DeviceTiles(
        new_devices=_new_device_count(host, net),
        trusted=sum(1 for c in cards if c.trusted),
        randomized=sum(1 for c in cards if c.randomized),
        down_mbps=_num(speed.get("down_mbps") if speed.get("down_mbps") is not None else speed.get("down")),
        up_mbps=_num(speed.get("up_mbps") if speed.get("up_mbps") is not None else speed.get("up")),
        latency_ms=_num(speed.get("latency_ms") if speed.get("latency_ms") is not None else speed.get("lat")),
        dns_ms=None if _num(system.get("ms")) is None else int(_num(system.get("ms"))),
        dns_ok=None if system.get("ok") is None else bool(system.get("ok")),
        wan_ip=ips[0] if len(ips) == 1 else "",
        wan_ips=ips,
    )


def devices(db: DbSession, tenant, net=None) -> DevicesPayload:
    """`net` is the resolved `?network=` filter; `None` means every network.

    Grouping stays by device kind (the chip strip the design pins). The
    per-network split the UI draws comes from `networks`, the same health strip
    the overview carries, plus each card's own `network` ref -- so a chip and a
    card always agree on which network a device is on.
    """
    q = db.query(m.Device).filter_by(tenant_id=tenant.id)
    if net is not None:
        q = q.filter(m.Device.network_id == net.id)
    rows = q.order_by(m.Device.mac).all()
    scan = latest_scan(db, tenant.id)
    raw = scan.raw if scan else {}
    live, unverified = _live_macs(raw), _unverified_macs(raw)
    netrefs = networks.refs_by_id(db, tenant.id)

    # One presence query for the whole listing, through the same counting the
    # Screen time page uses -- see `presence.online_pct`.
    online = presence_mod.online_pct(db, tenant.id, days=DEVICE_ONLINE_DAYS, net=net)
    cards = [device_card(d, live, unverified, netrefs, online_pct=online) for d in rows]
    by_group: dict[str, list[DeviceCard]] = {}
    for c in cards:
        by_group.setdefault(c.group, []).append(c)

    groups = [DeviceGroup(group=g, devices=by_group[g],
                          network=networks.ref(net) if net is not None else None)
              for g in GROUP_ORDER if by_group.get(g)]
    # Only a group with at least one device gets a chip -- an empty tenant
    # (no devices at all) must come back with an empty chip strip, not eight
    # zero-count chips; a populated tenant simply omits whichever of the
    # eight groups it has nothing in.
    chips = [KindChip(group=g, count=len(by_group[g]), online=sum(1 for c in by_group[g] if c.online),
                      network_id=net.id if net is not None else None)
             for g in GROUP_ORDER if by_group.get(g)]
    health = networks.health(db, tenant, raw)
    return DevicesPayload(
        groups=groups, chips=chips, total=len(cards),
        online=sum(1 for c in cards if c.online),
        unverified=sum(1 for c in cards if c.unverified),
        cameras=sum(1 for c in cards if c.kind == "camera"),
        cameras_online=sum(1 for c in cards if c.kind == "camera" and c.online),
        tiles=_tiles(db, tenant, net, raw, cards),
        networks=[sd_NetworkHealth(**h) for h in health],
        charts=device_charts(db, tenant, net, cards),
    )


# --------------------------------------------------------------------------
# The three device trend panels (Task 77 / F5)
# --------------------------------------------------------------------------

#: The window the three device trend panels cover, matching the reference's
#: own "— 7 days" headings.
DEVICE_TREND_DAYS = 7


def _daily(counts: dict[str, float], days: int, end: dt.date) -> list[TrendPoint]:
    """One point per day for the whole window, oldest first -- a day with
    nothing counted is a real zero here (no device joined), not a gap."""
    return [
        TrendPoint(ts=(end - dt.timedelta(days=days - 1 - i)).isoformat(),
                   value=counts.get((end - dt.timedelta(days=days - 1 - i)).isoformat(), 0.0))
        for i in range(days)
    ]


def device_charts(db: DbSession, tenant, net, cards: list[DeviceCard]) -> list[HistoryChart]:
    """Devices online, joins and leaves, and online-vs-known -- the reference's
    three panels beside the vendor bars.

    "Devices online" is `history._devices_series`, the same presence-sample
    series History draws, so the two tabs agree. The other two are per-day
    aggregates the hub did not emit for devices at all: joins and leaves off
    `Event`, and "known" as the number of devices whose `first_seen` had already
    happened by that day -- the only honest reading of "known" available, since
    nothing snapshots the device count per day.

    Drawn as trends rather than the reference's bespoke bars: Vault has one
    chart component and one hub-built chart shape, and a count per day is as
    readable as a line as it is as a bar.
    """
    now = _now()
    cutoff = now - dt.timedelta(days=DEVICE_TREND_DAYS)
    today = now.date()
    charts: list[HistoryChart] = []

    online = history_mod._devices_series(db, tenant.id, cutoff, net)
    if online:
        charts.append(HistoryChart(key="devices_online", label="Devices online",
                                   series=online))

    # joins and leaves per day
    q = (
        db.query(m.Event.ts, m.Event.type)
        .filter(m.Event.tenant_id == tenant.id, m.Event.type.in_(("join", "leave")),
                m.Event.ts >= _lax_cutoff(cutoff))
    )
    if net is not None:
        q = q.filter(m.Event.network_id == net.id)
    per_day: dict[str, dict[str, float]] = {"join": {}, "leave": {}}
    for ts, kind in q.all():
        when = parse_ts(ts)
        if when is None or when < cutoff:
            continue
        day_key = when.date().isoformat()
        per_day[kind][day_key] = per_day[kind].get(day_key, 0.0) + 1
    if per_day["join"] or per_day["leave"]:
        charts.append(HistoryChart(key="devices_joins", label="Joins and leaves", series=[
            Series(name="joined", points=_daily(per_day["join"], DEVICE_TREND_DAYS, today)),
            Series(name="left", points=_daily(per_day["leave"], DEVICE_TREND_DAYS, today)),
        ]))

    # online vs known, per day: how many of the devices Vault knew about by
    # that day were seen that day. "Known by then" is reconstructed from the
    # record that actually exists -- devices Vault had already met before the
    # window (`first_seen` older than the cutoff) plus every mac whose first
    # sighting *inside* the window has already happened by that day. Not
    # `Device.first_seen` alone: ingest sets it when the hub first stores the
    # row, which for an imported or re-seeded tenant is far later than the
    # presence samples that prove the device was there.
    pq = (
        db.query(m.PresenceSample.ts, m.PresenceSample.mac)
        .filter(m.PresenceSample.tenant_id == tenant.id,
                m.PresenceSample.ts >= _lax_cutoff(cutoff))
    )
    if net is not None:
        pq = pq.filter(m.PresenceSample.network_id == net.id)
    seen_per_day: dict[str, set[str]] = {}
    first_in_window: dict[str, dt.date] = {}
    for ts, mac in pq.all():
        when = parse_ts(ts)
        if when is None or when < cutoff:
            continue
        day = when.date()
        seen_per_day.setdefault(day.isoformat(), set()).add(mac)
        if mac not in first_in_window or day < first_in_window[mac]:
            first_in_window[mac] = day

    known_before = 0
    for card in cards:
        when = parse_ts(card.first_seen)
        if when is not None and when < cutoff and card.mac not in first_in_window:
            known_before += 1

    if seen_per_day or known_before:
        known_counts: dict[str, float] = {}
        seen_counts: dict[str, float] = {}
        for i in range(DEVICE_TREND_DAYS):
            day = today - dt.timedelta(days=DEVICE_TREND_DAYS - 1 - i)
            key = day.isoformat()
            known_counts[key] = float(known_before + sum(1 for d in first_in_window.values() if d <= day))
            seen_counts[key] = float(len(seen_per_day.get(key, ())))
        charts.append(HistoryChart(key="devices_known", label="Online vs known", series=[
            Series(name="known", points=_daily(known_counts, DEVICE_TREND_DAYS, today)),
            Series(name="seen that day", points=_daily(seen_counts, DEVICE_TREND_DAYS, today)),
        ]))
    return charts


# --------------------------------------------------------------------------
# Device drawer
# --------------------------------------------------------------------------

DRAWER_TIMELINE_LIMIT = 60


def device_detail(db: DbSession, tenant, mac: str, net=None) -> DeviceDetail:
    # With no `?network=` a MAC present on more than one network resolves to
    # the row with the newest `last_seen` (never an arbitrary `.first()`), and
    # the card's own `network` field says which one the drawer is showing.
    d = networks.device_row(db, tenant.id, mac, net)
    if not d:
        raise HTTPException(404, "device not found")
    net = net or (db.query(m.Network).filter_by(tenant_id=tenant.id, id=d.network_id).first()
                  if d.network_id else None)

    scan = latest_scan(db, tenant.id)
    raw = scan.raw if scan else {}
    netrefs = networks.refs_by_id(db, tenant.id)
    card = device_card(d, _live_macs(raw), _unverified_macs(raw), netrefs,
                       online_pct=presence_mod.online_pct(db, tenant.id,
                                                          days=DEVICE_ONLINE_DAYS, net=net))

    events_q = db.query(m.Event).filter_by(tenant_id=tenant.id, entity=mac)
    if net is not None:
        events_q = events_q.filter(m.Event.network_id == net.id)
    events = events_q.order_by(m.Event.ts.desc()).limit(DRAWER_TIMELINE_LIMIT).all()
    alerts_q = db.query(m.Alert).filter_by(tenant_id=tenant.id)
    if net is not None:
        alerts_q = alerts_q.filter(m.Alert.network_id == net.id)
    alerts = [a for a in alerts_q.order_by(m.Alert.last_seen.desc()).limit(200).all() if mac in a.key]
    fixes = [f for f in db.query(m.Fix).filter_by(tenant_id=tenant.id)
             .order_by(m.Fix.ts.desc()).limit(200).all()
             if (f.arg or "") == mac or mac in (f.label or "")]

    merged: list[tuple[dt.datetime, TimelineItem]] = []

    def add(ts: str | None, item: TimelineItem) -> None:
        when = parse_ts(ts)
        if when is None:
            return
        item.ts = _iso(when)
        merged.append((when, item))

    for e in events:
        port = (e.payload or {}).get("port")
        if e.type.startswith("port_"):
            verb = "opened" if e.type == "port_open" else "closed"
            title, kind = f"port {port} {verb}", "port"
        else:
            title, kind = e.type, e.type if e.type in ("join", "leave") else "wan"
        add(e.ts, TimelineItem(kind=kind, title=title, entity=mac,
                               href=f"/assets/devices/{mac}"))
    for a in alerts:
        add(a.first_seen, TimelineItem(kind="alert", title=a.msg, detail=a.why or "", sev=a.sev,
                                       entity=mac, href=f"/inbox?row={a.key}"))
    for f in fixes:
        add(f.ts, TimelineItem(kind="fix", title=f.label or f.action,
                               detail="succeeded" if f.ok else "failed", entity=mac,
                               sev=None if f.ok else "WARN", href="/updates"))
    merged.sort(key=lambda pair: pair[0], reverse=True)

    live_all = _live_macs(raw)
    unverified_all = _unverified_macs(raw)
    children_q = db.query(m.Device).filter_by(tenant_id=tenant.id, parent=mac)
    if net is not None:
        children_q = children_q.filter(m.Device.network_id == net.id)
    children = [device_card(c, live_all, unverified_all, netrefs)
                for c in children_q.order_by(m.Device.mac).all()]

    return DeviceDetail(
        device=card,
        risk_reasons=_risk_reasons(d, mac in unverified_all),
        timeline=[item for _, item in merged[:DRAWER_TIMELINE_LIMIT]],
        presence=presence_mod.profile(db, tenant.id, mac, net=net),
        familiarity=familiarity.device_familiarity(db, tenant, mac, net=net),
        ports=card.ports,
        dns=dnsview_payload.device_profile(db, tenant.id, mac, hours=24, net=net),
        alerts=[InboxRow(id=a.key, view="alerts", sev=a.sev, title=a.msg, entity=mac,
                         why=a.why or "", status=a.status, first_seen=a.first_seen,
                         last_seen=a.last_seen, count=int(a.count or 1),
                         network=netrefs.get(a.network_id))
                for a in sorted(alerts, key=lambda a: a.last_seen or "", reverse=True)],
        children=children,
        events=[EventRow(ts=e.ts, type=e.type, entity=e.entity, text=e.type,
                         payload=e.payload or {}, network=netrefs.get(e.network_id))
                for e in events],
    )
