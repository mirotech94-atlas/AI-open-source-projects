"""Camera identification and the per-network camera census.

Why this exists: a home with a dozen cameras wants them INVENTORIED — every
one found, counted per network, and noticed when one appears or stops
answering. Vault already had a `camera` device kind that only ever came
from the hub's text heuristics (`hub/guardian_hub/payloads/assets.kind_of`);
nothing on the sensor ever set `kind` at all, so a camera was only ever a
camera if its vendor string happened to say so.

This module is the sensor-side classifier. It runs over the facts the sweep
already produced — vendor (OUI lookup), DHCP/Bonjour/rDNS name, the user's
label, and the open TCP ports — and never probes anything of its own, so it
adds no scan time and stays inside the tenant's own networks.

Confidence, and why the port table is split:

- `STRONG_PORTS` are camera/NVR control ports that essentially nothing else
  serves: RTSP (554, 8554) and Dahua's own protocol (37777). One of these
  open is enough on its own.
- `WEAK_PORTS` are ports the camera world uses but shares with other
  software: 34567 is the Xiongmai/generic-DVR family (also seen on no-name
  IoT firmware) and 8000 collides with every development server on earth.
  One of these classifies a device ONLY together with a second signal
  (another camera port, a camera vendor, or a camera-ish name).

Vendors are likewise split. `VENDOR_RE` is camera-only makers — a Reolink or
Hikvision OUI is a camera, full stop. `VENDOR_HINT_RE` is makers with a
camera line inside a much broader catalogue (Amazon/Ring, Wyze, Xiaomi,
Ubiquiti, TP-Link/Tapo): those need a name or port signal too, or every Echo
and every mesh node in the house would be filed as a camera.

The hub mirrors the same tables in `payloads/assets.py` (the hub may not
import the sensor package) as the fallback for rows this classifier never
saw — an imported or seeded tenant.
"""
import re

#: port -> label. Camera/NVR ports worth sweeping for. Merged into
#: `lan.LAN_PORTS`, so every LAN port scan already answers this question.
CAMERA_PORTS = {
    554: "rtsp", 8554: "rtsp-alt", 37777: "dahua", 34567: "dvr", 8000: "camera-http",
}

#: On its own, enough to call a device a camera.
STRONG_PORTS = (554, 8554, 37777)
#: Only ever a camera signal alongside a second one (see the module docstring).
WEAK_PORTS = (34567, 8000)

#: Camera-only vendors: the OUI alone settles it.
VENDOR_RE = re.compile(
    r"reolink|hikvision|dahua|amcrest|axis communications|vivotek|mobotix|"
    r"hanwha|uniview|lorex|swann|foscam|annke|eufy|arlo|wyze labs|"
    r"shenzhen (?:gwell|reecam)|zosi|anpviz",
    re.I,
)
#: Vendors with a camera line inside a much wider catalogue.
VENDOR_HINT_RE = re.compile(r"amazon|ring |wyze|xiaomi|ubiquiti|tp-link|tplink|tuya|espressif|hisilicon", re.I)

#: Name/label patterns. `\bcam\b` and `cam-` deliberately do NOT match
#: "webcam" alone (a laptop's built-in webcam is not a network camera) —
#: "webcam" is excluded below.
NAME_RE = re.compile(
    r"camera|ipcam|ip-cam|\bcam\b|cam-\d|\bnvr\d*\b|\bdvr\d*\b|doorbell|"
    r"reolink|hikvision|dahua|amcrest|foscam|wyze|arlo|eufy|blink|nest ?cam|"
    r"front ?door|driveway|backyard ?cam",
    re.I,
)
NAME_NOT_RE = re.compile(r"webcam|camera roll|photo", re.I)

#: A camera that has not answered for this long counts as "stopped
#: answering" — six five-minute scans, so a single missed sweep or a brief
#: reboot never raises it.
GONE_AFTER_S = 30 * 60


def _text(d):
    return " ".join(str(d.get(k) or "") for k in ("label", "name", "vendor"))


def classify(d):
    """Is this `known_macs[mac]` entry a camera? Returns
    `(is_camera, confidence, reasons)` — `confidence` is "high"/"medium",
    `reasons` are the human-readable signals that decided it, which is what
    the alert copy and the device drawer show instead of a bare verdict."""
    text = _text(d)
    label = str(d.get("label") or "")
    ports = {int(p) for p in (d.get("ports") or {}) if str(p).isdigit()}

    reasons = []
    strong = sorted(ports & set(STRONG_PORTS))
    weak = sorted(ports & set(WEAK_PORTS))
    vendor_hit = bool(VENDOR_RE.search(text))
    hint_hit = bool(VENDOR_HINT_RE.search(text))
    name_hit = bool(NAME_RE.search(text)) and not NAME_NOT_RE.search(text)

    # An explicit kind — set by a previous run of this classifier, or by the
    # dashboard — wins, so a device the owner filed by hand stays filed.
    if str(d.get("kind") or "").lower() == "camera":
        reasons.append("already filed as a camera")
    if vendor_hit:
        reasons.append(f"camera vendor ({d.get('vendor') or label})")
    if strong:
        reasons.append("answers on " + ", ".join(f"{p} ({CAMERA_PORTS[p]})" for p in strong))
    if name_hit:
        reasons.append(f"name/label says camera ({label or d.get('name')})")
    if weak:
        reasons.append("answers on " + ", ".join(f"{p} ({CAMERA_PORTS[p]})" for p in weak))

    if str(d.get("kind") or "").lower() == "camera" or vendor_hit or strong:
        return True, "high", reasons
    if name_hit and (weak or hint_hit or not ports):
        return True, "high" if weak else "medium", reasons
    if name_hit:
        return True, "medium", reasons
    # A weak port alone is not a camera; a weak port plus a broad-catalogue
    # camera vendor is.
    if weak and (hint_hit or len(weak) > 1):
        return True, "medium", reasons
    return False, "", []


def census(known, live_macs):
    """The camera inventory for ONE network.

    `known` is that network's `known_macs`; `live_macs` the MACs seen live in
    this sweep. Mutates each camera's entry with `kind: "camera"` and a
    `camera` block (confidence + reasons) so the hub's ingest files the device
    row as a camera without having to re-run any heuristics, then returns the
    census the alert layer diffs.
    """
    macs, online, detail = [], [], {}
    for mac, d in sorted(known.items()):
        is_cam, conf, reasons = classify(d)
        if not is_cam:
            continue
        d["kind"] = "camera"
        d["camera"] = {"confidence": conf, "reasons": reasons}
        macs.append(mac)
        if mac in live_macs:
            online.append(mac)
        detail[mac] = {"ip": d.get("ip"), "label": d.get("label"), "name": d.get("name"),
                       "vendor": d.get("vendor"), "confidence": conf, "reasons": reasons,
                       "online": mac in live_macs}
    return {"count": len(macs), "macs": macs, "online": online,
            "offline": [m for m in macs if m not in online], "detail": detail}


def update_baseline(baseline, cens, ts):
    """The stored camera baseline: `{mac: {"first_seen", "last_online"}}`.

    Kept as its own state slice rather than derived from `known_macs` every
    run, because "this camera used to answer and now does not" needs the last
    time it DID answer, and `known_macs[mac]["last_seen"]` moves for any
    reason at all (an ARP-table entry with no reply keeps a device "seen").
    """
    out = dict(baseline or {})
    for mac in cens.get("macs") or []:
        row = dict(out.get(mac) or {})
        row.setdefault("first_seen", ts)
        if mac in (cens.get("online") or []):
            row["last_online"] = ts
        out[mac] = row
    return out


def _age_s(then, now):
    import datetime as dt
    try:
        return (dt.datetime.fromisoformat(now) - dt.datetime.fromisoformat(then)).total_seconds()
    except Exception:
        return 0.0


def diff(baseline, cens, ts):
    """`(new_macs, gone)` for one network.

    `baseline is None` means this network has never had a camera census — the
    caller learns it silently and raises nothing, exactly like the LAN device
    baseline, so enrolling a sensor in a house full of cameras does not open a
    dozen alerts on the first scan.

    `gone` carries the last time each camera answered, so the copy can say how
    long it has been quiet rather than just "missing".
    """
    if baseline is None:
        return [], []
    macs = cens.get("macs") or []
    online = set(cens.get("online") or [])
    new = [m for m in macs if m not in baseline]
    gone = []
    for mac in macs:
        if mac in online or mac not in baseline:
            continue
        last = (baseline.get(mac) or {}).get("last_online")
        if last and _age_s(last, ts) >= GONE_AFTER_S:
            gone.append({"mac": mac, "last_online": last,
                          **{k: v for k, v in (cens.get("detail") or {}).get(mac, {}).items()
                             if k in ("ip", "label", "name", "vendor")}})
    return new, gone
