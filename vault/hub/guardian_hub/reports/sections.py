"""Rendering primitives for the report engine -- a one-for-one port of the
Nexus helpers (`~/.claude/nexus-agent/ui/server.py:305-334`), renamed to
Vault's own names:

    Nexus        Vault
    ---------    -----------
    _H           esc
    _pct         pct
    _svg_line    svg_line
    _svg_bars    svg_bars
    _hb          heartbeat_row
    _kpi         kpis
    _tbl         table
    _pill        pill
    _ago_h       ago_hours

Also carries the section catalogue: `SECTION_KEYS` (the canonical, printed
order of all 19 sections) and `SECTION_TITLES` (key -> heading text), ported
from Nexus `REPORT_SECTIONS` + `DEVICE_SECTIONS` (server.py:298-303).
"""

import math
import datetime as dt

from ..payloads.assets import kind_of
from ..scans import parse_ts

# --------------------------------------------------------------------------
# Device-group sections (dev_* keys), ported from Nexus DEVICE_SECTIONS.
# `group_spec` is a "|"-joined set of the KIND_GROUPS labels
# (guardian_hub.payloads.assets.KIND_GROUPS) that key selects.
# --------------------------------------------------------------------------

DEVICE_SECTIONS = [
    ("dev_cameras", "Devices — cameras", "Cameras"),
    ("dev_people", "Devices — phones, tablets & computers", "Phones & tablets|Computers"),
    ("dev_media", "Devices — TVs & media", "TVs & media"),
    ("dev_smart", "Devices — smart home & appliances", "Smart home|Printers & appliances"),
    ("dev_net", "Devices — network gear", "Network gear"),
    ("dev_unknown", "Devices — unknown", "Unknown"),
]

_CORE_SECTIONS = [
    ("summary", "Executive summary"),
    ("availability", "Website availability"),
    ("servers", "Servers & security posture"),
    ("updates", "Patching & vulnerabilities"),
    ("home", "Home network & Wi-Fi"),
    ("devices", "Device inventory"),
    ("presence", "Estimated network presence"),
    ("alerts", "Alerts & incidents"),
    ("fixes", "Automatic remediation"),
    ("internet", "Internet line"),
    ("email", "Domains & email protection"),
    ("compliance", "Compliance checklist"),
    ("evidence_pack", "Compliance evidence pack"),
    ("recommendations", "Recommendations"),
]

SECTION_KEYS: list[str] = [k for k, _ in _CORE_SECTIONS] + [k for k, _, _ in DEVICE_SECTIONS]
SECTION_TITLES: dict[str, str] = {**dict(_CORE_SECTIONS), **{k: t for k, t, _ in DEVICE_SECTIONS}}

# The Parts a full report reads under. Sections keep their canonical order
# and numbering (the contract every preset and test relies on); the Parts
# are printed between them, once each, by `builder.build_report.section`.
# Each Part is a question the reader has: how is it going, what is running,
# what is on the network, what happened, can it be evidenced, what next --
# and the device groups are reference material, so they are an appendix.
PARTS: dict[str, tuple[str, str]] = {
    "summary": ("Part I", "Summary"),
    "infra": ("Part II", "Infrastructure"),
    "network": ("Part III", "Network"),
    "activity": ("Part IV", "Activity"),
    "assurance": ("Part V", "Assurance"),
    "decisions": ("Part VI", "Recommendations"),
    "appendix": ("Appendix", "Device groups"),
}
PART_OF: dict[str, str] = {
    "summary": "summary",
    "availability": "infra", "servers": "infra", "updates": "infra",
    "home": "network", "devices": "network", "presence": "network",
    "alerts": "activity", "fixes": "activity", "internet": "activity",
    "email": "assurance", "compliance": "assurance", "evidence_pack": "assurance",
    "recommendations": "decisions",
    **{k: "appendix" for k, _, _ in DEVICE_SECTIONS},
}


def esc(s) -> str:
    """HTML-escape (port of Nexus `_H`)."""
    return str("" if s is None else s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def pct(a, b) -> float | None:
    """Port of Nexus `_pct`."""
    return round(100.0 * a / b, 2) if b else None


def _ticks(top: float, want: int = 5) -> list[float]:
    """Gridline values a person would choose: 0, 25, 50, 75 rather than
    0, 33.3333, 66.6667, 100.

    The axis used to be cut into equal thirds and the raw value printed, so a
    score chart topping out at 100 was labelled 33.3333 and 66.6667. Nobody
    writes an axis that way. The step is rounded up to the nearest 1, 2 or 5
    times a power of ten, which is the usual rule and always lands on figures
    that read cleanly.
    """
    if top <= 0:
        return [0.0]
    raw = top / max(1, want - 1)
    mag = 10 ** math.floor(math.log10(raw))
    for mult in (1, 2, 2.5, 5, 10):
        step = mult * mag
        if step >= raw:
            break
    out, v = [], 0.0
    while v <= top + step * 0.001:
        out.append(round(v, 6))
        v += step
    return out


def svg_line(series, w=640, h=140, fmt=lambda v: f"{v:g}", ymax=None) -> str:
    """Inline SVG line chart (port of Nexus `_svg_line`).

    `series` is a list of `{"name": str, "pts": [(x, y), ...], "color": str?}`
    dicts; `x` is a unix timestamp (float/int), `y` a numeric value or None.
    """
    pts = [p for s in series for p in s["pts"] if p[1] is not None]
    if len(pts) < 2:
        return '<div class="muted">not enough data</div>'
    x0 = min(p[0] for p in pts)
    x1 = max(p[0] for p in pts) or x0 + 1
    y1 = ymax if ymax is not None else (max(p[1] for p in pts) * 1.1 or 1)
    pl, pr, pt, pb = 44, 10, 8, 20
    X = lambda x: pl + (x - x0) / ((x1 - x0) or 1) * (w - pl - pr)
    Y = lambda y: pt + (1 - y / y1) * (h - pt - pb)
    g = "".join(
        f'<line x1="{pl}" x2="{w-pr}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="#e5e9f0"/>'
        f'<text x="{pl-6}" y="{Y(v)+4:.1f}" text-anchor="end" font-size="10" fill="#6b7280">{fmt(v)}</text>'
        for v in _ticks(y1)
    )
    f = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%b %d %H:%M")
    g += (f'<text x="{pl}" y="{h-4}" font-size="10" fill="#6b7280">{f(x0)}</text>'
          f'<text x="{w-pr}" y="{h-4}" font-size="10" fill="#6b7280" text-anchor="end">{f(x1)}</text>')
    cols = ["#0b3d91", "#1d6fe0", "#5aa9ff", "#0ea5c9", "#7c9cc9", "#1e4fa8"]
    for i, s in enumerate(series):
        p = [q for q in s["pts"] if q[1] is not None]
        if not p:
            continue
        d = " ".join(("M" if j == 0 else "L") + f"{X(q[0]):.1f} {Y(q[1]):.1f}" for j, q in enumerate(p))
        g += f'<path d="{d}" fill="none" stroke="{s.get("color") or cols[i % 6]}" stroke-width="1.8"/>'
    leg = "".join(
        f'<span style="display:inline-block;margin-right:12px;font-size:11px;color:#374151">'
        f'<i style="display:inline-block;width:12px;height:3px;background:{s.get("color") or cols[i%6]};'
        f'vertical-align:middle;margin-right:5px"></i>{esc(s["name"])}</span>'
        for i, s in enumerate(series)
    )
    return f'<div>{leg}</div><svg viewBox="0 0 {w} {h}" style="width:100%;height:{h}px;display:block">{g}</svg>'


def svg_bars(items, w=640, h=150, color="#0b3d91") -> str:
    """Inline SVG bar chart (port of Nexus `_svg_bars`). `items` is `[(label, value), ...]`."""
    if not items:
        return '<div class="muted">no data</div>'
    mx = max(v for _, v in items) or 1
    n = len(items)
    bw = (w - 50) / n
    g = ""
    for i, (lab, v) in enumerate(items):
        x = 40 + i * bw
        bh = (h - 34) * v / mx
        g += (f'<rect x="{x+2:.1f}" y="{h-24-bh:.1f}" width="{max(2,bw-4):.1f}" height="{bh:.1f}" '
              f'rx="3" fill="{color}"/>'
              f'<text x="{x+bw/2:.1f}" y="{h-28-bh:.1f}" font-size="10" text-anchor="middle" fill="#374151">{v}</text>')
        if n <= 16 or i % max(1, n // 12) == 0:
            g += f'<text x="{x+bw/2:.1f}" y="{h-8}" font-size="9.5" text-anchor="middle" fill="#6b7280">{esc(lab)}</text>'
    return f'<svg viewBox="0 0 {w} {h}" style="width:100%;height:{h}px;display:block">{g}</svg>'


def heartbeat_row(vals) -> str:
    """Port of Nexus `_hb`: a strip of pass/fail/no-data bars."""
    return '<div class="hbr">' + "".join(
        f'<i style="background:{"#16a34a" if v else ("#e02424" if v is False else "#e5e9f0")}"></i>'
        for v in vals
    ) + '</div>'


def kpis(items) -> str:
    """Port of Nexus `_kpi`. `items` is `[(label, value, css_class, subtitle), ...]`."""
    return '<div class="kpis">' + "".join(
        f'<div class="kpi {c}"><div class="k">{esc(k)}</div><div class="v">{v}</div>'
        f'{f"<div class=s>{esc(s)}</div>" if s else ""}</div>'
        for k, v, c, s in items
    ) + '</div>'


def table(head, rows, cls="") -> str:
    """Port of Nexus `_tbl`."""
    if not rows:
        return '<div class="muted">nothing in this period</div>'
    return (f'<table class="{cls}"><thead><tr>' + "".join(f"<th>{esc(h)}</th>" for h in head)
            + "</tr></thead><tbody>"
            + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
            + "</tbody></table>")


def pill(ok, txt, bad="bad") -> str:
    """Port of Nexus `_pill`."""
    return f'<span class="pill {"ok" if ok else bad}">{esc(txt)}</span>'


def ago_hours(ts) -> float | None:
    """Port of Nexus `_ago_h`: hours between now and an ISO timestamp."""
    when = parse_ts(ts) if isinstance(ts, str) else None
    if when is None:
        return None
    return (dt.datetime.now(dt.timezone.utc) - when).total_seconds() / 3600


# --------------------------------------------------------------------------
# Per-network report blocks (Task 54).
#
# A tenant with 0 or 1 networks renders the legacy, un-prefixed body
# (`network=None`) -- exactly the markup these sections produced before
# networks existed. With 2+ networks, `builder.build_report` calls these
# once per network (primary first, then by name -- `builder.networks_of`)
# and concatenates the blocks, so "Device inventory" (etc.) still appears
# exactly once as an `<h2>` but gains one `<h3>` sub-block per network.
# --------------------------------------------------------------------------

_NO_DEVICES_MSG = "no devices seen on this network in this period."


def _subnet_caption(network) -> str:
    return f"{network.subnet_prefix}.0/24" if network.subnet_prefix else "no subnet configured"


def net_heading(network, title: str) -> str:
    """`"<Network name> — <title>"` sub-heading plus the network's subnet
    in the sub-caption."""
    return (f'<h3>{esc(network.name)} — {esc(title)}</h3>'
            f'<div class="muted net-cap">{esc(_subnet_caption(network))}</div>')


def _netblock(html: str) -> str:
    """Wraps one network's block so print CSS (`.netblock{page-break-
    inside:avoid}`) keeps it from splitting mid-table across a page break,
    even when the whole section (all networks together) is too long to fit
    on one page itself."""
    return f'<div class="netblock">{html}</div>'


def devices(dev_rows, unv_macs, live, network=None) -> str:
    """Device-inventory block: a per-device table plus the by-manufacturer
    histogram, for one network's `dev_rows` (or, with `network=None`, the
    legacy whole-tenant rendering)."""
    heading = ""
    if network is not None:
        heading = net_heading(network, "Device inventory")
        if not dev_rows:
            return _netblock(heading + f'<p class="muted">{_NO_DEVICES_MSG}</p>')
    drows = [[
        f"<b>{esc(d.label or d.name or '—')}</b>", esc(d.ip), esc((d.vendor or '')[:28]),
        pill(d.mac in live, "online" if d.mac in live else (d.presence or "offline").lower(), "muted"),
        pill(d.trusted, "trusted" if d.trusted else ("unverified" if d.mac in unv_macs else "baseline"),
             "warn" if d.mac in unv_macs else "muted"),
        f"<b>{d.risk}</b>",
        ", ".join(sorted((d.ports or {}).keys(), key=lambda x: int(x) if str(x).isdigit() else 0)) or "—",
        esc((d.first_seen or "")[:10]),
    ] for d in dev_rows]
    body = table(["Device", "IP", "Vendor", "State", "Trust", "Risk", "Services", "First seen"], drows, "small")
    # Legacy whole-tenant rendering (network is None) always emits the
    # "By manufacturer" heading + chart, even at zero devices -- matching
    # the pre-networks behaviour it is ported from. Only the per-network
    # early return above (line ~210) skips this for an empty network.
    vend: dict[str, int] = {}
    for d in dev_rows:
        v = d.vendor or "unknown"
        v = "Phones/laptops (private MAC)" if v.startswith("private") else v.split(" ")[0]
        vend[v] = vend.get(v, 0) + 1
    tail = heading + body + "<h3>By manufacturer</h3>" + svg_bars(sorted(vend.items(), key=lambda x: -x[1])[:10])
    return _netblock(tail) if network is not None else tail


def dev_group(title, dev_rows, unv_macs, live, presence_pct, network=None) -> str:
    """One `dev_*` group's KPIs + table, for one network's `dev_rows` (or,
    with `network=None`, the legacy whole-tenant rendering, which always
    renders -- even at zero devices -- exactly as it did before networks
    existed). `presence_pct(mac)` returns that device's presence percentage
    for whatever population `dev_rows` was drawn from."""
    heading = ""
    if network is not None:
        heading = net_heading(network, title)
        if not dev_rows:
            return _netblock(heading + f'<p class="muted">{_NO_DEVICES_MSG}</p>')
    rows_g, on_now, att = [], 0, 0
    for d in dev_rows:
        isunv = d.mac in unv_macs
        online = d.mac in live
        on_now += 1 if online else 0
        att += 1 if (isunv or (d.risk or 0) >= 40) else 0
        pres = presence_pct(d.mac)
        rows_g.append([
            f"<b>{esc(d.label or d.name or '—')}</b><div class='muted'>{esc(kind_of(d))}</div>",
            esc(d.ip), esc((d.vendor or '')[:28]),
            pill(online, "online" if online else (d.presence or "offline").lower(), "muted"),
            pill(d.trusted, "trusted" if d.trusted else ("unverified" if isunv else "baseline"),
                 "warn" if isunv else "muted"),
            f"<b>{d.risk}</b>",
            ", ".join(sorted((d.ports or {}).keys(), key=lambda x: int(x) if str(x).isdigit() else 0)) or "—",
            esc((d.first_seen or "")[:10]), f"{pres}%",
        ])
    tail = (heading
            + kpis([("Devices", str(len(rows_g)), "", ""), ("Online now", str(on_now), "ok", ""),
                    ("Need attention", str(att), "warn" if att else "ok", "unverified or risk ≥ 40")])
            + table(["Device", "IP", "Vendor", "State", "Trust", "Risk", "Services", "First seen", "Presence"],
                    rows_g, "small"))
    return _netblock(tail) if network is not None else tail


def presence(network, screen_rows, hb_lines=None) -> str:
    """One network's screen-time block. `screen_rows` is
    `payloads.presence.screen_time(db, tenant.id, days=days, net=network)
    .devices` -- already the Task 51 per-network semantics (deduplicated
    `(ts, mac)` samples), sorted by `-total_h`. `r.pct` here is that
    screen_time-derived percentage (samples for this network / distinct
    scan-ts for this network), not the legacy whole-tenant history-line
    fraction the single-network path uses -- the two agree for a
    single-network tenant but can diverge for a multi-homed device.
    `hb_lines` is `builder.network_presence_lines(...)` for this network
    (already filtered to lines with at least one live mac), used to render
    the "Last 60 scans" heartbeat column per device -- dropped from the
    per-network table until this fix, unlike the legacy single-network one."""
    heading = net_heading(network, "Screen time")
    if not screen_rows:
        return _netblock(heading + f'<p class="muted">{_NO_DEVICES_MSG}</p>')
    hb = (hb_lines or [])[-60:]
    prows = [[f"<b>{esc(r.name)}</b>", esc(r.ip), f"{r.total_h} h", f"{r.pct}%",
              heartbeat_row([r.mac in (h.get("live_macs") or []) for h in hb])] for r in screen_rows[:25]]
    return _netblock(heading + table(["Device", "IP", "Time online", "Presence", "Last 60 scans"], prows))
