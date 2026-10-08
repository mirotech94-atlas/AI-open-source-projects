"""`GET /api/v1/public/feed` -- the live panel on the marketing site.

The sales page claims Vault notices things. This endpoint is how it proves
it: the demo tenant's own most recent notices, unauthenticated, so a visitor
sees real rows moving before they ever ask for access. It is the only read
route on the hub with no session behind it, so every line below is written for
an internet-facing, anonymous caller.

**Scope.** The demo tenant and nothing else. The slug is a constant here and
the caller passes *nothing*: no tenant, no slug, no limit, no offset, no
cursor. There is no parameter to point this at another tenant, so there is no
parameter to get wrong. A hub with no demo tenant answers 200 with an empty
list -- an unseeded box is not an error the public should see.

**Nothing identifying leaves.** Every string in the response is composed here
from an allow-list, never copied out of a text column. That matters because
the columns it reads are full of exactly what must not ship: `Event.entity` is
a MAC, `DnsQuery.client` is an RFC1918 address, `Alert.msg`/`Alert.key` embed
MACs, hostnames and public IPs, `Fix.out` is command output, and `Device.label`
in the demo data carries people's first names ("Reception's MacBook Air"). So a
device becomes its *class* ("A laptop") through `_device_class`, a fix becomes
its action's plain-English name, a site becomes the short half of its display
name, and a blocked lookup keeps only the domain that was blocked. `tests/
test_public_feed.py` sweeps the whole serialised body for MAC and private-IP
patterns and for the tenant's id and slug, so a future field that forgets this
fails there rather than on the live page.

**Load.** A marketing page that polls must never become database load. The
payload is built at most once per `_TTL_S` for the whole process and served
from memory in between, so a thousand simultaneous visitors cost one query
set regardless of how many are polling. On top of that the limiter runs per
source address and hub-wide, sized in `FEED_PER_IP` / `FEED_GLOBAL` for a page
left open all day behind one NAT.

The client (`site/app.js`) polls every `REFRESH_MS` = 4000ms -- the middle of
the 3-5s band the panel is meant to feel "live" in. `_TTL_S` is set to match
that cadence (4.0s) so the cache never serves data staler than one client
tick, and `Cache-Control: max-age` is set to half of `_TTL_S` (2s) so an
intermediate proxy or the browser's own HTTP cache can never hold a response
longer than the endpoint's own freshness window.

The per-IP budget is sized off that same interval: one tab left open and
foregrounded for a full hour polls at most 3600s / 4s = 900 times. `FEED_PER_IP`
gives that 2x headroom (1800/hour) for jitter, a couple of tabs behind one
NAT, and the client's own retry-after-failure behaviour never quite hitting
the ideal cadence. `FEED_GLOBAL` keeps the same 25x per-IP-to-global ratio
this route has always used (`1800 * 25 = 45000`/hour, ~12.5 req/s hub-wide
average) -- generous for legitimate traffic because the cache absorbs it for
free, tight enough that a scripted flood still trips it well before the
database ever sees load.

**CORS.** One header, `Access-Control-Allow-Origin: *`, on this route only. It
is safe here precisely because the route reads no cookie, no `Authorization`
header and no caller-supplied scope -- there is no ambient authority for a
foreign origin to borrow, and the body is the same for everyone. In production
the site fetches a same-origin relative path anyway (nginx proxies this exact
location); the header is what lets the page be served from somewhere else --
a local preview, a staging copy -- without a proxy in front.
"""
import datetime as dt
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session as DbSession

from .. import models as m, ratelimit, schemas as sc
from ..db import get_db
from ..payloads.assets import PORT_SERVICES
from ..scans import latest_scan, parse_ts
from .leads import _client_ip

router = APIRouter(prefix="/api/v1", tags=["public"])

#: The one tenant this route may ever read. Matches `guardian seed-demo`'s
#: default slug (cli.py). Not a setting on purpose: a typo in an env var must
#: not be able to publish a real customer's network.
DEMO_SLUG = "demo"

#: How many rows the panel shows. Fixed, so the panel's height never changes
#: as it refreshes, and small, so the queries behind it stay trivial.
ROWS = 8

#: Per-source caps applied *before* the merge, so one chatty source (blocked
#: DNS fires every few minutes; a device joins once a day) cannot crowd the
#: others out of a panel that is meant to show the range of what Vault
#: notices.
_CAP_DNS, _CAP_FIX, _CAP_EVENT = 2, 3, 4

#: Sized for the client's 4s poll (see module docstring): a tab left open and
#: foregrounded for a full hour makes at most 3600/4 = 900 requests; this
#: gives 2x headroom (1800) for jitter and a few tabs behind one NAT. The
#: cache below is what actually protects the database, so this limit only has
#: to stop somebody scripting a flood.
FEED_PER_IP = (1800, 3600)
#: Hub-wide ceiling for the same reason `leads.py` has one -- a botnet does
#: not share an address. Kept at the same 25x ratio to FEED_PER_IP this route
#: has always used. Both windows stay <= ratelimit._STALE_AFTER_S.
FEED_GLOBAL = (45000, 3600)

#: Matches the client's 4s poll interval (site/app.js REFRESH_MS) so the
#: cache is never more than one client tick behind.
_TTL_S = 4.0
_lock = threading.Lock()
_cache: tuple[float, sc.PublicFeedOut] | None = None

#: `demo_trickle`'s cadence note explains why the demo tenant's rows can
#: momentarily vanish: `scheduler.demo_refresh_job` deletes every ingest-
#: derived row for the tenant and then re-seeds it, and those two steps are
#: separate commits (`seed.reset_tenant` commits on its own; `seed_demo`'s
#: inserts land in several commits after it). A cache miss that lands in
#: that gap would otherwise hand a visitor an empty panel every ~10 minutes
#: -- exactly the "visibly resets" failure mode the public feed must not
#: have. `_last_good` is the last payload that actually had rows, kept
#: alongside the TTL cache so a transient empty build (tenant exists, but
#: nothing to show *yet*) can serve it instead of the empty one. Bounded by
#: `_LAST_GOOD_MAX_AGE_S` so a demo tenant that is genuinely gone (or a
#: refresh that is genuinely stuck) does not look "live" forever.
_last_good: sc.PublicFeedOut | None = None
_last_good_at: float = 0.0
#: The gap this bridges is a full `reset_tenant` + `seed_demo` re-ingest
#: (~3000 DNS rows plus ~1440 history rows through `store_scan`) on the same
#: small VPS whose SQLite database has already seen lock contention under
#: far lighter load -- long enough that a tight bound here would let a slow
#: refresh empty the panel anyway, which is exactly the failure this exists
#: to prevent. 90s is generous against that without coming close to reading
#: as "live" for a demo tenant that is genuinely stuck or gone.
_LAST_GOOD_MAX_AGE_S = 90.0

_now = lambda: dt.datetime.now(dt.timezone.utc)


def reset_cache() -> None:
    """Test-only, mirroring `ratelimit.reset()`: the cache (and the
    `_last_good` fallback below) is process-global, so without this a test
    that seeds rows would be served a payload an earlier test cached or
    left as the last-known-good one, and read as an endpoint bug."""
    global _cache, _last_good, _last_good_at
    with _lock:
        _cache = None
        _last_good = None
        _last_good_at = 0.0


# --------------------------------------------------------------------------
# allow-lists: every user-visible string in the response comes from one of
# these, or from an integer/date this module formatted itself
# --------------------------------------------------------------------------

#: (substring to look for in a device's label/name, what to call it). Ordered:
#: the first hit wins, so the specific entries come before the loose ones
#: ("macbook" before "mac", "fire tv" before "tv").
_DEVICE_CLASSES: tuple[tuple[str, str], ...] = (
    ("iphone", "A phone"), ("ipad", "A tablet"), ("android", "A phone"),
    ("macbook", "A laptop"), ("laptop", "A laptop"),
    ("windows pc", "A desktop PC"), ("desktop", "A desktop PC"),
    ("printer", "A printer"), ("camera", "A camera"),
    ("echo", "A smart speaker"), ("speaker", "A smart speaker"),
    ("fire tv", "A streaming box"), ("streaming", "A streaming box"),
    ("smart tv", "A TV"), ("tv", "A TV"),
    ("router", "A router"), ("extender", "A Wi-Fi extender"),
    ("switch", "A network switch"), ("console", "A games console"),
    ("range", "A kitchen appliance"), ("oven", "A kitchen appliance"),
    ("thermostat", "A thermostat"), ("watch", "A smart watch"),
    ("phone", "A phone"), ("mac", "A laptop"), ("amazon", "A smart-home device"),
    ("samsung", "A smart-home device"),
)

#: `Fix.action` -> (headline, sentence). Keys are the sensor's real action
#: names (sensor/guardian_sensor/fix.py FIX_ACTIONS). Headlines are kept
#: short deliberately: the panel gives them a narrow column, and a headline
#: that wraps to three lines makes a fixed-height row impossible.
_FIX_TITLES: dict[str, tuple[str, str]] = {
    "restart_service": ("Restarted a service", "It had died; Vault brought it back."),
    "full_upgrade": ("Installed updates", "The pending security updates are on."),
    "reboot": ("Rebooted a server", "A kernel update had been waiting for it."),
    "ban_long": ("Blocked an attacker", "An address that kept guessing passwords."),
    "clear_logs": ("Cleared a full disk", "A log directory had filled it up."),
    "renew_cert": ("Renewed a certificate", "Days before it would have lapsed."),
}


def _device_class(label: str, name: str) -> str:
    """A device's *kind*, never its identity.

    Runs over the label and the hostname only to choose one of the fixed
    strings above; nothing from either is ever returned. The demo tenant's
    labels include "Reception's MacBook Air", "Miro Mac" and "Fire TV (Waiting)", so
    passing a label through would put someone's first name on a public page.
    """
    haystack = f"{label} {name}".lower()
    for needle, pretty in _DEVICE_CLASSES:
        if needle in haystack:
            return pretty
    return "A device"


def _port_label(port: int) -> str:
    service = PORT_SERVICES.get(port)
    return f"{service} (port {port})" if service else f"port {port}"


# --------------------------------------------------------------------------
# row builders -- one per source table
# --------------------------------------------------------------------------

def _row(sev: str, kind: str, title: str, target: str, detail: str, when: dt.datetime) -> tuple:
    """(sort key, row). The key is a real datetime so the merge is correct
    across three tables whose `ts` columns are strings written in whatever
    offset the sensor was in."""
    return when, sc.PublicFeedRow(
        sev=sev, kind=kind, title=title, target=target, detail=detail,
        ts=when.astimezone(dt.timezone.utc).isoformat(timespec="seconds"),
    )


def _device_rows(db: DbSession, tenant_id: str) -> list[tuple]:
    """Joins, departures and ports, from `Event`.

    The MAC in `Event.entity` is used as a lookup key and then thrown away --
    only the matched device's *class* is carried into the row.
    """
    events = (
        db.query(m.Event)
        .filter(m.Event.tenant_id == tenant_id,
                m.Event.type.in_(("join", "leave", "port_open", "port_closed")))
        .order_by(m.Event.ts.desc())
        .limit(_CAP_EVENT * 4)      # over-read: some will have no parseable ts
        .all()
    )
    macs = {e.entity for e in events if e.entity}
    devices = {
        d.mac: d for d in
        db.query(m.Device).filter(m.Device.tenant_id == tenant_id, m.Device.mac.in_(macs or {""})).all()
    } if macs else {}

    out: list[tuple] = []
    for e in events:
        when = parse_ts(e.ts)
        if when is None:
            continue
        d = devices.get(e.entity)
        what = _device_class(d.label if d else "", d.name if d else "")
        port = (e.payload or {}).get("port")
        if e.type == "join":
            out.append(_row("ok", "device", "A new device joined", what,
                            "First time this one has been seen here.", when))
        elif e.type == "leave":
            out.append(_row("info", "device", "A device went quiet", what,
                            "Stopped answering on the network.", when))
        elif e.type == "port_open" and isinstance(port, int):
            out.append(_row("warn", "port", "A port opened", what,
                            f"{_port_label(port)} is now reachable on the LAN.", when))
        elif e.type == "port_closed" and isinstance(port, int):
            out.append(_row("ok", "port", "A port closed", what,
                            f"{_port_label(port)} is no longer reachable.", when))
        if len(out) >= _CAP_EVENT:
            break
    return out


def _fix_rows(db: DbSession, tenant_id: str) -> list[tuple]:
    """Repairs Vault carried out itself.

    `Fix.host`, `Fix.arg`, `Fix.out` and `Fix.by` are all read past: a host
    name identifies the owner's infrastructure and `out` is raw command
    output. Only the action, mapped through `_FIX_TITLES`, survives -- and an
    action with no mapping is skipped rather than printed raw.
    """
    out: list[tuple] = []
    for f in (db.query(m.Fix).filter_by(tenant_id=tenant_id)
              .order_by(m.Fix.ts.desc()).limit(_CAP_FIX * 4).all()):
        when = parse_ts(f.ts)
        mapped = _FIX_TITLES.get(f.action)
        if when is None or mapped is None:
            continue
        title, detail = mapped
        out.append(_row("ok" if f.ok else "crit", "fix",
                        title if f.ok else f"{title} -- failed",
                        "A monitored server",
                        detail if f.by == "auto" else f"{detail} (asked for)", when))
        if len(out) >= _CAP_FIX:
            break
    return out


def _dns_rows(db: DbSession, tenant_id: str) -> list[tuple]:
    """Lookups the filter refused.

    `DnsQuery.client` is an address on the demo network and never leaves; the
    domain that was blocked does, because that is the whole content of the
    row and it names a destination, not a person.
    """
    out: list[tuple] = []
    seen: set[str] = set()
    for q in (db.query(m.DnsQuery).filter_by(tenant_id=tenant_id, blocked=True)
              .order_by(m.DnsQuery.ts.desc()).limit(_CAP_DNS * 12).all()):
        when = parse_ts(q.ts)
        name = (q.name or "").strip().lower()[:80]
        if when is None or not name or name in seen:
            continue
        seen.add(name)
        out.append(_row("info", "dns", "A lookup was blocked", "A device on the network",
                        f"Asked for {name}; the filter refused it.", when))
        if len(out) >= _CAP_DNS:
            break
    return out


#: A certificate this far out is worth a row on the panel; anything further
#: away is not news. Matches the spirit of "weeks out, not a Sunday outage".
CERT_WARN_DAYS = 45


def _site_rows(db: DbSession, tenant_id: str, scan_at: dt.datetime | None) -> list[tuple]:
    """Websites: the ones that failed their last check, and the certificates
    running out. Built from the newest scan, so they all carry the scan's
    timestamp and sort to the top of a freshly-polled feed.

    A site's display name is cut at its first " (" -- the demo's names are
    "Roots prod (status.example.org)" and the half in brackets is a hostname.
    """
    if scan_at is None:
        return []
    out: list[tuple] = []
    for s in db.query(m.Site).filter_by(tenant_id=tenant_id).order_by(m.Site.name).all():
        last = s.last or {}
        label = (s.name or "").split(" (")[0].strip()[:60] or "A website"
        if not last.get("ok"):
            out.append(_row("crit", "site", "A site went down", label,
                            "The last check did not get a response.", scan_at))
            continue
        days = last.get("cert_days_left")
        if isinstance(days, int) and days <= CERT_WARN_DAYS:
            sev = "crit" if days <= 7 else "warn"
            out.append(_row(sev, "cert", "A certificate expires soon", label,
                            f"{days} day{'' if days == 1 else 's'} left before it expires.", scan_at))
    return out


# --------------------------------------------------------------------------
# the payload
# --------------------------------------------------------------------------

def build_feed(db: DbSession) -> sc.PublicFeedOut:
    """The whole payload, uncached. Public because the tests build it
    directly rather than going through the cache."""
    now = _now()
    tenant = db.query(m.Tenant).filter_by(slug=DEMO_SLUG).first()
    if tenant is None:
        # No demo tenant on this hub (a fresh box, a hub that never ran
        # `guardian seed-demo`). The panel's job is then to say so quietly,
        # which it does by rendering its own fallback line for zero rows.
        return sc.PublicFeedOut(rows=[], last_scan=None, generated=now.isoformat(timespec="seconds"))

    scan = latest_scan(db, tenant.id)
    scan_at = parse_ts(scan.ts) if scan else None

    keyed = (_site_rows(db, tenant.id, scan_at) + _dns_rows(db, tenant.id)
             + _fix_rows(db, tenant.id) + _device_rows(db, tenant.id))
    keyed.sort(key=lambda kr: kr[0], reverse=True)

    return sc.PublicFeedOut(
        rows=[row for _, row in keyed[:ROWS]],
        last_scan=scan_at.astimezone(dt.timezone.utc).isoformat(timespec="seconds") if scan_at else None,
        generated=now.isoformat(timespec="seconds"),
    )


@router.get("/public/feed", response_model=sc.PublicFeedOut)
def public_feed(request: Request, response: Response, db: DbSession = Depends(get_db)):
    global _cache, _last_good, _last_good_at

    if not ratelimit.allow(f"feed:ip:{_client_ip(request)}", *FEED_PER_IP) or \
       not ratelimit.allow("feed:global", *FEED_GLOBAL):
        # The page treats this exactly like an unreachable hub and shows its
        # static line, so a throttled visitor sees an explanation rather than
        # an empty box.
        raise HTTPException(429, "too many requests -- try again shortly")

    response.headers["Access-Control-Allow-Origin"] = "*"
    # Half of _TTL_S: a proxy or browser may hold it, but never longer than
    # the panel's own refresh, so the stamp on the page stays honest.
    response.headers["Cache-Control"] = "public, max-age=2"

    now = time.monotonic()
    with _lock:
        if _cache is not None and _cache[0] > now:
            return _cache[1]

    payload = build_feed(db)
    if payload.rows:
        with _lock:
            _last_good, _last_good_at = payload, now
    elif db.query(m.Tenant.id).filter_by(slug=DEMO_SLUG).first() is not None:
        # The tenant exists but `build_feed` found nothing -- the mid-refresh
        # gap `_last_good`'s docstring describes, not "no demo tenant on this
        # hub" (that case never has a `_last_good` to fall back to anyway,
        # since `build_feed` returns empty for it every time). Bridge it with
        # the last payload that did have rows, rather than showing the panel
        # as reset.
        with _lock:
            stale_for = now - _last_good_at
            if _last_good is not None and stale_for < _LAST_GOOD_MAX_AGE_S:
                payload = _last_good

    with _lock:
        _cache = (time.monotonic() + _TTL_S, payload)
    return payload
