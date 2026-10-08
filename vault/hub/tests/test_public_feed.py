"""`GET /api/v1/public/feed` -- the unauthenticated panel on guarder.io.

The first test here is the important one. The route reads tables full of MACs,
RFC1918 addresses, hostnames and people's first names, and composes every
output string by hand instead. `test_response_leaks_nothing_identifying`
sweeps the *whole serialised body* for those patterns rather than checking
field by field, so a field added later that forgets the rule fails here rather
than on a public web page.
"""
import datetime as dt
import re

from tests.test_seed_helpers import seeded_demo
from guardian_hub.db import SessionLocal
from guardian_hub import demo_trickle, demoscrub, models as m, ratelimit
from guardian_hub.routers import public_feed as pf

FEED = "/api/v1/public/feed"

_MAC = re.compile(r"\b[0-9a-f]{2}(?::[0-9a-f]{2}){5}\b", re.I)
_PRIVATE_IP = re.compile(
    r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|127\.\d{1,3}\.\d{1,3}\.\d{1,3})\b"
)


def _demo(client):
    seeded_demo(client)
    pf.reset_cache()
    db = SessionLocal()
    return db, db.query(m.Tenant).filter_by(slug="demo").one()


# --------------------------------------------------------------------------
# the hard requirement
# --------------------------------------------------------------------------

def test_response_leaks_nothing_identifying(client):
    db, tenant = _demo(client)
    body = client.get(FEED).text
    assert body

    assert not _MAC.search(body), f"a MAC reached the public feed: {_MAC.search(body).group(0)}"
    assert not _PRIVATE_IP.search(body), f"an internal address reached the public feed: {_PRIVATE_IP.search(body).group(0)}"
    assert tenant.id not in body
    assert '"demo"' not in body and demoscrub.BRAND not in body

    # The demo labels carry real first names; the route must map them to a
    # device *class* instead. These are the ones in the fixture.
    lowered = body.lower()
    for personal in ("emma", "miro", "canary-person", "canary-device"):
        assert personal not in lowered, f"{personal!r} reached the public feed"

    # Nor any of the free-text columns the row builders deliberately read past.
    fix = db.query(m.Fix).filter_by(tenant_id=tenant.id).first()
    assert fix is not None and fix.host and fix.host not in body
    assert "auto-fix" not in lowered          # Fix.label
    for alert_word in ("debsecan", "failed ssh logins", demoscrub.DOMAIN2):
        assert alert_word not in lowered      # nothing is built from Alert.msg


def test_rows_are_well_formed_and_newest_first(client):
    _demo(client)
    payload = client.get(FEED).json()
    rows = payload["rows"]
    assert 0 < len(rows) <= pf.ROWS
    assert payload["last_scan"] and payload["generated"]

    stamps = []
    for r in rows:
        assert r["sev"] in ("crit", "warn", "ok", "info")
        assert r["kind"] in ("site", "cert", "dns", "fix", "device", "port")
        assert r["title"] and r["target"] and r["detail"]
        when = dt.datetime.fromisoformat(r["ts"])
        assert when.tzinfo is not None and when.utcoffset() == dt.timedelta(0), "ts must be UTC"
        stamps.append(when)
    assert stamps == sorted(stamps, reverse=True)


def test_trickled_rows_make_the_panels_newest_row_seconds_old(client):
    """The actual claim in play: not that a table has a fresh row (that's
    `test_demo_trickle.py`'s job), but that the payload this route hands a
    browser reflects it. This is the one place a string-sort mismatch
    between the trickled rows' timestamp offset and the seeded rows' own
    offset would show up -- the row builders take their "most recent" slice
    with a plain `ORDER BY ts DESC LIMIT n`, so a trickled row stamped in
    the wrong offset could parse as fresh yet never survive that slice."""
    db, tenant = _demo(client)
    for _ in range(4):
        demo_trickle.trickle_demo(db)
    pf.reset_cache()

    rows = client.get(FEED).json()["rows"]
    assert rows
    newest = max(dt.datetime.fromisoformat(r["ts"]) for r in rows)
    assert dt.datetime.now(dt.timezone.utc) - newest < dt.timedelta(seconds=30), \
        f"panel's newest row is {dt.datetime.now(dt.timezone.utc) - newest} old"


def test_panel_shows_more_than_one_kind_of_notice(client):
    """The point of the panel is breadth -- the per-source caps exist so one
    chatty table cannot fill all eight rows with the same kind of line."""
    _demo(client)
    kinds = {r["kind"] for r in client.get(FEED).json()["rows"]}
    assert len(kinds) >= 3, kinds


# --------------------------------------------------------------------------
# scope: the demo tenant and nothing else
# --------------------------------------------------------------------------

def test_only_the_demo_tenant_is_read(client):
    db, demo = _demo(client)
    other = m.Tenant(slug="acme", name="Acme Ltd")
    db.add(other); db.flush()
    db.add(m.Fix(tenant_id=other.id, ts=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                 host="acme-db", action="restart_service", label="acme secret", ok=True, by="auto"))
    db.add(m.DnsQuery(tenant_id=other.id, ts=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                      client="10.9.9.9", name="acme-internal.example", qtype="A", blocked=True, list="", ms=0))
    db.commit()
    pf.reset_cache()

    body = client.get(FEED).text
    assert "acme" not in body.lower()
    # And there is no parameter that could widen it.
    assert client.get(FEED, params={"slug": "acme"}).text == body
    assert client.get(FEED, params={"tenant_id": other.id, "limit": 500}).text == body
    assert demo.id not in body


def test_no_demo_tenant_is_an_empty_payload_not_an_error(client):
    pf.reset_cache()
    r = client.get(FEED)
    assert r.status_code == 200
    payload = r.json()
    assert payload["rows"] == [] and payload["last_scan"] is None and payload["generated"]


# --------------------------------------------------------------------------
# the ten-minute refresh's reset/reseed gap must not visibly empty the panel
# --------------------------------------------------------------------------

def test_refresh_reset_window_does_not_empty_the_panel(client):
    """`scheduler.demo_refresh_job` deletes every ingest-derived row for the
    tenant and then re-seeds it in several later commits (`seed.reset_tenant`
    -> `seed.seed_demo`). A cache miss landing in that gap must be bridged by
    `_last_good`, not served as an empty "panel just reset" payload -- the
    tenant is not gone, it is mid-refresh.
    """
    import guardian_hub.seed as seed_mod

    db, tenant = _demo(client)
    good = client.get(FEED).json()
    assert good["rows"]

    # Force the next fetch to actually rebuild (not the TTL cache), but keep
    # `_last_good` -- unlike `pf.reset_cache()`, which would also wipe the
    # very fallback this test is exercising.
    pf._cache = None

    seed_mod.reset_tenant(db, tenant.id)   # the delete half of a refresh, alone
    mid_refresh = client.get(FEED).json()
    assert mid_refresh["rows"], "the panel went empty during the reset/reseed gap"
    assert mid_refresh["rows"] == good["rows"]

    # Finish the refresh and confirm a genuinely fresh (non-fallback) payload
    # follows once new rows exist again.
    seed_mod.seed_demo(db, anchor=dt.datetime.now(dt.timezone.utc))
    pf._cache = None
    fresh = client.get(FEED).json()
    assert fresh["rows"] and fresh["last_scan"]


def test_last_good_fallback_expires_rather_than_looking_alive_forever(client, monkeypatch):
    db, tenant = _demo(client)
    assert client.get(FEED).json()["rows"]

    monkeypatch.setattr(pf, "_LAST_GOOD_MAX_AGE_S", 0.0)
    import guardian_hub.seed as seed_mod
    pf._cache = None
    seed_mod.reset_tenant(db, tenant.id)
    payload = client.get(FEED).json()
    assert payload["rows"] == [] and payload["last_scan"] is None


# --------------------------------------------------------------------------
# abuse defences
# --------------------------------------------------------------------------

def test_cached_so_a_burst_of_visitors_is_one_query_set(client, monkeypatch):
    _demo(client)
    calls = {"n": 0}
    real = pf.build_feed

    def counting(db):
        calls["n"] += 1
        return real(db)

    monkeypatch.setattr(pf, "build_feed", counting)
    pf.reset_cache()
    for _ in range(25):
        assert client.get(FEED).status_code == 200
    assert calls["n"] == 1


def test_rate_limited_per_address(client, monkeypatch):
    """The real limit is 240/hour, so the limit itself is lowered rather than
    reached: hammering it 241 times through TestClient would be slow and would
    leave 240 hits in a process-global bucket for whatever runs next.
    `ratelimit.allow` is proven in test_ratelimit.py; what matters here is that
    this route consults it and turns a refusal into a 429."""
    _demo(client)
    monkeypatch.setattr(pf, "FEED_PER_IP", (2, 3600))
    ratelimit.reset()
    assert client.get(FEED).status_code == 200
    assert client.get(FEED).status_code == 200
    assert client.get(FEED).status_code == 429


def test_rate_limited_hub_wide_too(client, monkeypatch):
    """The per-address bucket is no defence against a botnet, so there is a
    second, hub-wide one -- checked here with the per-address limit left
    wide open, so only the global ceiling can be what refuses."""
    _demo(client)
    monkeypatch.setattr(pf, "FEED_GLOBAL", (1, 3600))
    ratelimit.reset()
    assert client.get(FEED).status_code == 200
    assert client.get(FEED).status_code == 429


def test_cors_header_is_present_and_no_session_is_read(client):
    _demo(client)
    r = client.get(FEED, headers={"Cookie": "session=whatever", "Authorization": "Bearer nope"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "*"
    assert "set-cookie" not in {k.lower() for k in r.headers}


# --------------------------------------------------------------------------
# the composers, directly
# --------------------------------------------------------------------------

def test_device_class_never_returns_its_input():
    assert pf._device_class("Reception's MacBook Air", "") == "A laptop"
    assert pf._device_class("Fire TV (Waiting)", "waiting's 6th Fire TV") == "A streaming box"
    assert pf._device_class("Phone (staff-phone)", "staff-phone") == "A phone"
    assert pf._device_class("", "") == "A device"
    # An unrecognised label falls back rather than leaking.
    assert pf._device_class("Dave's thing at 10.0.0.4", "dave-laptop-serial-991") == "A laptop"
    assert pf._device_class("wholly unknown gizmo", "hostname-1234") == "A device"


def test_unmapped_fix_action_is_skipped_rather_than_printed(client):
    db, tenant = _demo(client)
    db.query(m.Fix).filter_by(tenant_id=tenant.id).delete()
    db.add(m.Fix(tenant_id=tenant.id, ts=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                 host="h", action="rm_rf_everything", label="x", ok=True, by="auto"))
    db.commit()
    pf.reset_cache()
    body = client.get(FEED).text
    assert "rm_rf_everything" not in body


def test_expiring_certificate_becomes_a_row(client):
    db, tenant = _demo(client)
    site = db.query(m.Site).filter_by(tenant_id=tenant.id).first()
    site.last = dict(site.last or {}, ok=True, cert_days_left=5)
    db.commit()
    pf.reset_cache()
    rows = client.get(FEED).json()["rows"]
    cert = [r for r in rows if r["kind"] == "cert"]
    assert cert and cert[0]["sev"] == "crit" and "5 days left" in cert[0]["detail"]


def test_a_site_that_failed_its_check_becomes_a_crit_row(client):
    db, tenant = _demo(client)
    site = db.query(m.Site).filter_by(tenant_id=tenant.id).first()
    site.last = dict(site.last or {}, ok=False)
    db.commit()
    pf.reset_cache()
    rows = client.get(FEED).json()["rows"]
    down = [r for r in rows if r["kind"] == "site"]
    assert down and down[0]["sev"] == "crit"
    # The bracketed half of the demo's site names is a hostname; it is cut.
    assert demoscrub.map_host("status.example.org") not in client.get(FEED).text
