"""Is this device one of ours? -- answered from what Vault already holds.

An unnamed device with a randomized MAC on the Wi-Fi is the commonest
"should I worry?" question there is, and the only way to answer it used to be
walking round the house comparing MAC addresses on screens. Vault has
been watching that device since it first appeared: every sweep it was seen
on, every join and leave, which hours of the day it keeps, what DNS names it
asked for, whether the same name has shown up under other MACs, whether it
has been on any other watched network. Assembled, that is a stronger answer
than a MAC lookup -- a stranger's phone does not keep your household's
hours for five nights running.

Everything here is EVIDENCE with a name, never certainty: the verdict says
which pattern the record fits and lists the facts that fit it, so the owner
can disagree with any one of them.
"""
import collections
import datetime as dt

from sqlalchemy.orm import Session as DbSession

from .. import models as m, networks
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from ..scans import parse_ts
from ..schemas_dash import DeviceFamiliarity, NameSibling
from ._util import _now

#: How far back the record is read. Enough to tell a resident from a
#: visitor; long enough that a device seen every night for a month cannot
#: be mistaken for a passer-by.
LOOKBACK_DAYS = 30

#: Hours that a device is on the Wi-Fi only if it lives here. Guests and
#: passers-by are gone by then; a phone charging on a nightstand is not.
OVERNIGHT_HOURS = {0, 1, 2, 3, 4, 5}

#: DNS name fragments that identify a device's make. Conservative on
#: purpose: a hit names a platform, never a person.
_DNS_HINTS = (
    ("apple", ("apple.com", "icloud.com", "push.apple", "mzstatic")),
    ("google/android", ("google.com", "gstatic.com", "googleapis", "android")),
    ("amazon", ("amazon.com", "amazonaws", "a2z.com", "fire tv")),
    ("microsoft/windows", ("microsoft.com", "windowsupdate", "msftconnecttest", "live.com")),
    ("samsung", ("samsung", "smartthings")),
)


def _hint_from_dns(names: list[str]) -> str:
    lowered = [n.lower() for n in names]
    for label, frags in _DNS_HINTS:
        if any(any(f in n for f in frags) for n in lowered):
            return label
    return ""


def _name_siblings(db: DbSession, tenant_id: str, device: m.Device) -> list[NameSibling]:
    """Other MACs that carried this device's name.

    A phone with a private/randomized address presents a different MAC on
    each network it joins, and iOS can rotate it on the same network too --
    but its DHCP hostname ("iphone", "Reception's MacBook Air") travels with it.
    A sibling row under the same name, especially one already trusted, is
    the strongest sign that "new" MAC is a device the owner already knows.
    """
    name = (device.name or "").strip()
    if not name:
        return []
    rows = (db.query(m.Device)
            .filter(m.Device.tenant_id == tenant_id, m.Device.name == name, m.Device.mac != device.mac)
            .all())
    return [NameSibling(mac=r.mac, ip=r.ip or "", trusted=bool(r.trusted),
                        first_seen=str(r.first_seen or "")[:16], last_seen=str(r.last_seen or "")[:16])
            for r in rows]


def device_familiarity(db: DbSession, tenant, mac: str, net=None) -> DeviceFamiliarity:
    mac = (mac or "").lower()
    device = networks.device_row(db, tenant.id, mac, net)
    if device is None:
        return DeviceFamiliarity(mac=mac, verdict="unknown",
                                 summary="I have no record of this device at all.")

    try:
        zone = ZoneInfo(((tenant.settings or {}).get("network_timezones") or {}).get(device.network_id) or (tenant.settings or {}).get("timezone") or "UTC")
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("UTC")
    now = _now().astimezone(zone)
    cutoff = now - dt.timedelta(days=LOOKBACK_DAYS)
    cutoff_s = cutoff.isoformat()

    # -- presence: which days, which hours --------------------------------
    samples = (db.query(m.PresenceSample.ts, m.PresenceSample.network_id)
               .filter(m.PresenceSample.tenant_id == tenant.id, m.PresenceSample.mac == mac,
                       m.PresenceSample.ts >= cutoff_s)
               .all())
    days: set[str] = set()
    hours: collections.Counter = collections.Counter()
    nets_seen: set[str] = set()
    for ts, net_id in samples:
        when = parse_ts(ts)
        if when is None:
            continue
        when = when.astimezone(zone)
        days.add(when.date().isoformat())
        hours[when.hour] += 1
        if net_id:
            nets_seen.add(net_id)
    overnight = sum(hours[h] for h in OVERNIGHT_HOURS)
    overnight_share = round(overnight / len(samples), 2) if samples else 0.0
    # Typical hours from this device's own sightings: any hour with at least
    # half as many sightings as its busiest hour. `presence.profile` needs
    # the sweep timestamps to normalise against and comes back empty on a
    # long window, so the raw histogram is the honest source here.
    peak = max(hours.values()) if hours else 0
    typical = sorted(h for h, n in hours.items() if peak and n >= peak / 2)

    first = parse_ts(str(device.first_seen)) if device.first_seen else None
    last = parse_ts(str(device.last_seen)) if device.last_seen else None
    # Calendar days the device has been known, inclusive -- a device first
    # seen four-and-a-bit days ago has been known on five dates, and "seen
    # on 5 of the last 4 days" is the arithmetic of not counting that way.
    span_days = ((now.date() - first.date()).days + 1) if first else 0
    silent_days = (now - last).days if last else None

    # -- joins / leaves -------------------------------------------------
    ev = (db.query(m.Event.type)
          .filter(m.Event.tenant_id == tenant.id, m.Event.entity == mac, m.Event.ts >= cutoff_s)
          .all())
    joins = sum(1 for (t,) in ev if t == "join")
    leaves = sum(1 for (t,) in ev if t == "leave")

    # -- dns ------------------------------------------------------------
    dns_rows = (db.query(m.DnsQuery.name)
                .filter(m.DnsQuery.tenant_id == tenant.id, m.DnsQuery.ts >= cutoff_s,
                        m.DnsQuery.mac == mac, m.DnsQuery.network_id == device.network_id)
                .all())
    dns_names = [n for (n,) in dns_rows]
    top_dns = [n for n, _ in collections.Counter(dns_names).most_common(6)]
    platform = _hint_from_dns(dns_names)

    # -- the same name under other MACs, and other networks -----------------
    siblings = _name_siblings(db, tenant.id, device)
    other_nets = []
    for n in db.query(m.Network).filter_by(tenant_id=tenant.id).all():
        if n.id in nets_seen and (device.network_id is None or n.id != device.network_id):
            other_nets.append(n.name or n.slug)

    randomized = "randomized" in (device.vendor or "").lower() or "private" in (device.vendor or "").lower()

    # -- verdict: which pattern the record fits, and the facts that fit it --
    context = (tenant.settings or {}).get("site_context") or ("business" if ((tenant.settings or {}).get("profile") or {}).get("company") else "general")
    reasons: list[str] = []
    n_days = len(days)
    if context == "business" and n_days >= 3:
        verdict = "regular"
        work_hours = sum(hours[h] for h in range(9, 18))
        share = round(100 * work_hours / len(samples)) if samples else 0
        reasons.append(f"seen on {n_days} separate days; {share}% of sightings between 09:00 and 18:00 local time")
        if device.always_on:
            reasons.append("marked as always-on equipment; recurring presence does not establish identity")
    elif n_days >= 5 and overnight_share >= 0.25:
        verdict = "resident"
        reasons.append(f"seen on {n_days} of the {min(LOOKBACK_DAYS, max(span_days, n_days))} days it has been known")
        reasons.append(f"{int(overnight_share * 100)}% of sightings are overnight (midnight to 6am) -- recurring overnight presence, not proof of identity")
    elif n_days >= 3:
        verdict = "regular"
        reasons.append(f"seen on {n_days} separate days")
        if overnight_share > 0:
            reasons.append(f"{int(overnight_share * 100)}% of sightings overnight")
        else:
            reasons.append("never seen overnight -- comes and goes")
    elif n_days >= 1:
        verdict = "visitor"
        reasons.append(f"seen on only {n_days} day(s)")
    else:
        verdict = "unknown"
        reasons.append("no presence recorded in the last 30 days")

    if any(s.trusted for s in siblings):
        reasons.append(f'the name "{device.name}" is already trusted under another MAC -- '
                       f"possibly a rotating address; shared names alone do not establish identity")
    elif siblings:
        reasons.append(f'the name "{device.name}" has appeared under {len(siblings)} other MAC(s)')
    if other_nets:
        reasons.append("also seen on: " + ", ".join(other_nets))
    if platform:
        reasons.append(f"its DNS traffic looks like a {platform} device")
    elif not dns_names:
        reasons.append("no matching DNS observations recorded for this device")
    if randomized and not device.name:
        reasons.append("randomized MAC with no name -- a phone or laptop with privacy mode on; "
                       "the name is the thing to find")
    if silent_days is not None and silent_days >= 2:
        reasons.append(f"not seen for {silent_days} days")

    label = device.label or device.name or device.ip or mac
    head = {
        "resident": f"{label} behaves like a device that lives here" if context == "home" else f"{label} has recurring presence on this network",
        "regular": f"{label} is a regular on this network",
        "visitor": f"{label} has only been seen briefly",
        "unknown": f"{label} has no usable history yet",
    }[verdict]
    summary = head + ": " + "; ".join(reasons[:4]) + "."

    return DeviceFamiliarity(
        mac=mac, name=device.name or "", label=device.label or "", ip=device.ip or "",
        vendor=device.vendor or "", randomized_mac=randomized, trusted=bool(device.trusted),
        verdict=verdict, summary=summary, reasons=reasons,
        days_seen=n_days, lookback_days=LOOKBACK_DAYS, span_days=span_days,
        first_seen=str(device.first_seen or "")[:16], last_seen=str(device.last_seen or "")[:16],
        silent_days=silent_days, overnight_share=overnight_share, typical_hours=list(typical),
        joins=joins, leaves=leaves, dns_queries=len(dns_names), top_dns=top_dns, platform_hint=platform,
        name_siblings=siblings, other_networks=other_nets,
    )
