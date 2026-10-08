"""Presence payloads: one device's presence profile and the whole tenant's
screen-time view.

The port of Nexus `presence_stats` / `/api/presence`, reading `PresenceSample`
(one row per scan line per live mac, written by `ingest.store_scan`) instead
of `history.jsonl`.

A scan line represents one 5-minute sweep, so `samples * 5 / 60` is hours
online -- the same conversion Nexus used. The number of distinct sample
timestamps in the window is the number of scans, which is what every
percentage divides by.
"""

import datetime as dt

from sqlalchemy.orm import Session as DbSession

from .. import models as m, networks
from ..scans import parse_ts
from ..schemas_dash import (
    DayHours, HeatmapCell, HourBlock, PresenceProfile, ScreenTimePayload, ScreenTimeRow,
)
from ._util import _lax_cutoff, _now

MINUTES_PER_SCAN = 5
TYPICAL_HOUR_SHARE = 0.15       # an hour is "typical" at >= 15 % of the device's samples
STRIP_HOURS = 24


def _samples(db: DbSession, tenant_id: str, cutoff: dt.datetime,
             mac: str | None = None, net=None) -> list[tuple[str, str, dt.datetime, str | None]]:
    """`(ts, mac, parsed ts, network_id)` for every presence sample after
    `cutoff`, optionally narrowed to one mac and/or one network."""
    q = db.query(m.PresenceSample.ts, m.PresenceSample.mac, m.PresenceSample.network_id).filter(
        m.PresenceSample.tenant_id == tenant_id,
        m.PresenceSample.ts >= _lax_cutoff(cutoff),
    )
    if mac is not None:
        q = q.filter(m.PresenceSample.mac == mac)
    if net is not None:
        q = q.filter(m.PresenceSample.network_id == net.id)
    out = []
    for ts, row_mac, nid in q.all():
        when = parse_ts(ts)
        if when is not None and when >= cutoff:
            out.append((ts, row_mac, when, nid))
    return out


def _scan_timestamps(db: DbSession, tenant_id: str, cutoff: dt.datetime, net=None) -> list[dt.datetime]:
    """Every distinct scan timestamp in the window (the denominator)."""
    q = (
        db.query(m.PresenceSample.ts)
        .filter(m.PresenceSample.tenant_id == tenant_id,
                m.PresenceSample.ts >= _lax_cutoff(cutoff))
    )
    if net is not None:
        q = q.filter(m.PresenceSample.network_id == net.id)
    rows = q.distinct().all()
    out = []
    for (ts,) in rows:
        when = parse_ts(ts)
        if when is not None and when >= cutoff:
            out.append(when)
    return out


def _ha_home_fraction_by_hour(db: DbSession, tenant_id: str, mac: str, cutoff: dt.datetime) -> dict[int, float]:
    """Home Assistant's own `device_tracker` reads (`homeassistant.
    HomeAssistantIntegration.watchdog` -> `Device.extra["ha_presence"]`,
    one `{ts, home}` sample per hourly tick) bucketed the same way as the
    scan-derived `seen_by_hour`/`scans_by_hour` pair below -- fraction of
    samples in that hour-of-day where `home` was true, within `days`.

    Router-integration presence is generally tighter than Wi-Fi-scan
    presence (a phone that's asleep but still associated reads as "seen"
    to a scan; HA's own tracker reflects whatever policy the user's HA
    config already applies), so an hour-of-day bucket with any HA samples
    overrides the scan-derived fraction for that bucket entirely rather
    than being blended with it."""
    device = db.query(m.Device).filter_by(tenant_id=tenant_id, mac=mac).first()
    samples = ((device.extra or {}).get("ha_presence") or []) if device else []
    by_hour: dict[int, list[bool]] = {}
    for s in samples:
        when = parse_ts(s.get("ts"))
        if when is None or when < cutoff:
            continue
        by_hour.setdefault(when.hour, []).append(bool(s.get("home")))
    return {h: round(sum(v) / len(v), 3) for h, v in by_hour.items() if v}


def profile(db: DbSession, tenant_id: str, mac: str, days: int = 7, net=None) -> PresenceProfile:
    """One device's presence: 24 h/7 d online percentages, an hour-of-day
    profile, per-day rows and a 24-block strip for the drawer."""
    now = _now()
    cutoff = now - dt.timedelta(days=days)
    day_cutoff = now - dt.timedelta(days=1)

    scans = _scan_timestamps(db, tenant_id, cutoff, net=net)
    seen = [when for _, _, when, _nid in _samples(db, tenant_id, cutoff, mac=mac, net=net)]
    ha_by_hour = _ha_home_fraction_by_hour(db, tenant_id, mac, cutoff)
    if not scans and not ha_by_hour:
        return PresenceProfile(hours=[0.0] * 24,
                               strip=[HourBlock(hour=i, state="nodata") for i in range(STRIP_HOURS)])

    scans_24h = [w for w in scans if w >= day_cutoff]
    seen_24h = [w for w in seen if w >= day_cutoff]

    # hour-of-day: fraction of the scans taken in that hour that saw the device
    scans_by_hour: dict[int, int] = {}
    for w in scans:
        scans_by_hour[w.hour] = scans_by_hour.get(w.hour, 0) + 1
    seen_by_hour: dict[int, int] = {}
    for w in seen:
        seen_by_hour[w.hour] = seen_by_hour.get(w.hour, 0) + 1
    hours = [
        ha_by_hour[h] if h in ha_by_hour
        else (round(seen_by_hour.get(h, 0) / scans_by_hour[h], 3) if scans_by_hour.get(h) else 0.0)
        for h in range(24)
    ]

    threshold = max(1, len(seen) * TYPICAL_HOUR_SHARE)
    typical = [h for h in range(24)
               if (ha_by_hour[h] >= TYPICAL_HOUR_SHARE if h in ha_by_hour else seen_by_hour.get(h, 0) >= threshold)]

    # per-day rows, oldest first
    scans_by_day_hour: dict[tuple[str, int], int] = {}
    for w in scans:
        scans_by_day_hour[(w.date().isoformat(), w.hour)] = \
            scans_by_day_hour.get((w.date().isoformat(), w.hour), 0) + 1
    seen_by_day_hour: dict[tuple[str, int], int] = {}
    for w in seen:
        seen_by_day_hour[(w.date().isoformat(), w.hour)] = \
            seen_by_day_hour.get((w.date().isoformat(), w.hour), 0) + 1
    day_rows = []
    for day in sorted({w.date().isoformat() for w in scans}):
        day_rows.append(DayHours(day=day, hours=[
            round(seen_by_day_hour.get((day, h), 0) / scans_by_day_hour[(day, h)], 3)
            if scans_by_day_hour.get((day, h)) else 0.0
            for h in range(24)
        ]))

    # 24-block strip: up = seen this hour, down = scans ran but the device was
    # absent, nodata = no scan in that hour at all.
    now_hour = now.replace(minute=0, second=0, microsecond=0)
    scan_hours = {w.replace(minute=0, second=0, microsecond=0) for w in scans}
    seen_hours = {w.replace(minute=0, second=0, microsecond=0) for w in seen}
    strip = []
    for idx in range(STRIP_HOURS):
        hour = now_hour - dt.timedelta(hours=STRIP_HOURS - 1 - idx)
        if hour in seen_hours:
            state = "up"
        elif hour in scan_hours:
            state = "down"
        else:
            state = "nodata"
        strip.append(HourBlock(hour=idx, state=state))

    return PresenceProfile(
        online_pct_24h=round(100 * len(seen_24h) / len(scans_24h), 1) if scans_24h else 0.0,
        online_pct_7d=round(100 * len(seen) / len(scans), 1) if scans else 0.0,
        hours=hours, typical_hours=typical, days=day_rows, strip=strip,
    )


def online_pct(db: DbSession, tenant_id: str, days: int = 7, net=None) -> dict[str, float]:
    """`mac -> percentage of the window's scans that saw it`, over `days`.

    Deliberately the same counting `screen_time` does -- distinct `(ts, mac)`
    over the count of distinct scan timestamps -- rather than a fresh
    aggregate, so a device card's figure and that device's row on the Screen
    time page can never disagree for a multi-homed device (the same laptop
    live on the Wi-Fi and in the lab at one `ts` is two `PresenceSample` rows
    and one sample of "this device was online").

    An empty dict means no scans in the window: no mac gets a 0 % it did not
    earn.
    """
    cutoff = _now() - dt.timedelta(days=days)
    rows = _samples(db, tenant_id, cutoff, net=net)
    scans = len({ts for ts, _, _, _ in rows})
    if not scans:
        return {}
    seen: set[tuple[str, str]] = set()
    counts: dict[str, int] = {}
    for ts, mac, _when, _nid in rows:
        if (ts, mac) in seen:
            continue
        seen.add((ts, mac))
        counts[mac] = counts.get(mac, 0) + 1
    return {mac: round(100 * count / scans, 1) for mac, count in counts.items()}


def screen_time(db: DbSession, tenant_id: str, days: int = 7, net=None) -> ScreenTimePayload:
    """Every device's time online over `days`, plus the hour heatmap, in two
    queries (samples + device names) regardless of device count.

    Presence is per NETWORK, not per tenant (Task 51): the same laptop live on
    the Wi-Fi and in the lab at the same `ts` is two `PresenceSample` rows, so
    that a `?network=` filter can split screen time per network. The per-tenant
    totals this function returns when `net is None` are therefore computed over
    `count(distinct (ts, mac))` -- deduplicating those two rows back into one
    5-minute sample -- so an unfiltered read never double-counts a multi-homed
    device, and `sum(per-network hours) >= unfiltered hours` by exactly the
    multi-homed overlap.
    """
    now = _now()
    cutoff = now - dt.timedelta(days=days)
    rows = _samples(db, tenant_id, cutoff, net=net)
    scans = len({ts for ts, _, _, _ in rows})
    if not rows:
        return ScreenTimePayload(days=days, scans=0)

    netrefs = networks.refs_by_id(db, tenant_id)
    per: dict[str, dict] = {}
    seen_keys: set[tuple[str, str]] = set()
    for ts, mac, when, nid in rows:
        if (ts, mac) in seen_keys:
            # distinct (ts, mac): the same mac on two networks at one ts is one
            # 5-minute sample of "this device was online", not two.
            continue
        seen_keys.add((ts, mac))
        e = per.setdefault(mac, {"samples": 0, "hours": [0.0] * 24, "net": nid})
        e["samples"] += 1
        e["hours"][when.hour] += 1

    names = {d.mac: d for d in db.query(m.Device).filter_by(tenant_id=tenant_id).all()}

    out_rows = []
    for mac, e in per.items():
        d = names.get(mac)
        label = (getattr(d, "label", "") or getattr(d, "name", "") or getattr(d, "vendor", "") or mac) if d else mac
        total_h = round(e["samples"] * MINUTES_PER_SCAN / 60, 1)
        out_rows.append(ScreenTimeRow(
            mac=mac, name=label, ip=getattr(d, "ip", "") or "" if d else "",
            trusted=bool(getattr(d, "trusted", False)) if d else False,
            total_h=total_h,
            pct=round(100 * e["samples"] / scans, 1) if scans else 0.0,
            hours_per_day=round(total_h / days, 1) if days else 0.0,
            hours=list(e["hours"]),
            network=netrefs.get(net.id if net is not None else e["net"]),
        ))
    out_rows.sort(key=lambda r: -r.total_h)

    peak = max((max(e["hours"]) for e in per.values()), default=0.0)
    heatmap = []
    for row in out_rows:
        for hour, count in enumerate(row.hours):
            if count:
                heatmap.append(HeatmapCell(row=row.mac, col=hour,
                                           value=round(100 * count / peak, 1) if peak else 0.0))
    return ScreenTimePayload(days=days, scans=scans, devices=out_rows, heatmap=heatmap)
