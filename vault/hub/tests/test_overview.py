"""Task 18: the Overview payload and `GET /live`.

Everything here runs against the 30-day demo tenant (`seed.seed_demo`), so
the assertions exercise the real bucketing/decimation paths rather than a
two-row fixture.
"""

import threading
import time

from guardian_hub import schemas_dash as sd
from tests.test_read_write import seeded
from tests.test_seed_helpers import seeded_demo


def _seeded_with_sites(client):
    """A REAL (non-demo) tenant with two configured site targets.

    The `/live`-probe-mechanics tests below (deadline handling, the dead-
    site path, the cache generation counter) are about `_probe`/
    `_cached_probe` itself, not about the demo tenant specifically -- they
    just used `seeded_demo` as a convenient tenant that already had sites
    configured. Since F4 (`routers.live._synthetic_sites`) made the demo
    tenant's `/live` skip that probing engine entirely and answer from
    `Site.last` instead, those tests have to run against an ordinary
    tenant to keep exercising the real probe path they're named for.
    """
    tid, H = seeded(client)
    r = client.post("/api/v1/settings/targets", json={
        "sites": [{"name": "Site A", "url": "https://a.example.invalid/health"},
                  {"name": "Site B", "url": "https://b.example.invalid/health"}],
    })
    assert r.status_code == 200, r.text
    return tid, H


def test_overview_payload_is_render_ready(client):
    seeded_demo(client)
    o = client.get("/api/v1/overview").json()
    assert o["score"] and o["grade"] and o["age_s"] is not None
    assert len(o["strip"]) >= 4 and all(len(s["blocks"]) == 24 for s in o["strip"])
    assert {b["state"] for s in o["strip"] for b in s["blocks"]} <= {"up", "down", "partial", "nodata"}
    assert all([b["hour"] for b in s["blocks"]] == list(range(24)) for s in o["strip"])
    assert 2 <= len(o["score_trend"]["points"]) <= 240
    assert len(o["questions"]) == 5 and all(q["state"] in ("good", "warn", "bad") for q in o["questions"])
    assert o["timeline"] and o["timeline"][0]["ts"] >= o["timeline"][-1]["ts"]
    assert o["sensor"]["status"] == "active"
    # rendering-ready: it validates as the declared contract, nothing extra
    assert sd.OverviewPayload.model_validate(o)


def test_overview_verdict_and_next_action_carry_method_and_tier(client):
    seeded_demo(client)
    o = client.get("/api/v1/overview").json()
    assert o["verdict"]
    na = o["next_action"]
    if na is not None:
        assert na["method"] == "POST" and na["path"].startswith("/api/v1/")
        assert na["body"].get("tier") in (1, 2, 3)


def test_overview_timeline_kinds_and_hrefs(client):
    seeded_demo(client)
    o = client.get("/api/v1/overview").json()
    kinds = {t["kind"] for t in o["timeline"]}
    assert kinds and kinds <= {"join", "leave", "port", "wan", "fix", "alert", "scan"}
    for t in o["timeline"]:
        if t["href"]:
            assert t["href"].startswith(("/assets/devices/", "/inbox?row=", "/updates"))


def test_overview_is_tenant_scoped(client):
    """Another tenant's member sees its own (empty) overview, not the demo's."""
    from tests.test_auth import login, seed_user
    seeded_demo(client)
    demo = client.get("/api/v1/overview").json()
    assert demo["devices"]["total"] >= 20
    client.post("/api/v1/auth/logout")
    seed_user(username="ann", email="a@x.com", tenant_slug="other", role="viewer")
    login(client, "ann", "a@x.com")
    other = client.get("/api/v1/overview").json()
    assert other["devices"]["total"] == 0 and other["strip"] == [] and other["timeline"] == []


def test_live_probes_and_is_rate_limited(client, monkeypatch):
    _seeded_with_sites(client)
    import guardian_hub.routers.live as live
    monkeypatch.setattr(live, "_probe", lambda s: (s["name"], {"ms": 12, "code": 200, "ok": True}))
    body = client.get("/api/v1/live").json()
    # LivePayload.sites is a list[LiveSite] (schemas_dash / web types.ts).
    assert body["sites"] and all(v["ok"] for v in body["sites"])
    assert body["scanning"] is False and body["sensor_age_s"] is not None
    assert sd.LivePayload.model_validate(body)
    # Exhaust the per-tenant budget (see live.py `_RATE_LIMIT`) rather than a
    # hardcoded count, so this doesn't silently stop testing the limiter the
    # next time that number is retuned. One call already went through above.
    for _ in range(live._RATE_LIMIT - 1):
        client.get("/api/v1/live")
    assert client.get("/api/v1/live").status_code == 429


def test_live_survives_a_dead_site(client, monkeypatch):
    """A probe that fails must come back ok=False, not raise or hang."""
    _seeded_with_sites(client)
    import guardian_hub.routers.live as live
    monkeypatch.setattr(live, "_HTTP_TIMEOUT", 0.01)
    monkeypatch.setattr(live, "_open", lambda url, method: (_ for _ in ()).throw(OSError("unreachable")))
    body = client.get("/api/v1/live").json()
    assert body["sites"] and all(s["ok"] is False and s["code"] is None for s in body["sites"])


def test_live_returns_at_the_deadline_not_when_probes_finish(client, monkeypatch):
    """`/live` must come back at `_GATHER_DEADLINE`, not whenever the slowest
    probe finishes -- a `with ThreadPoolExecutor(...)` block around the
    fan-out used to block on shutdown until every started probe (already
    running, so uncancellable) completed. A slow probe should be reported
    as timed out (ok=None) in the immediate response, then the background
    result should show up in the next call once it lands."""
    _seeded_with_sites(client)
    import guardian_hub.routers.live as live

    live.cache_clear()
    monkeypatch.setattr(live, "_GATHER_DEADLINE", 0.2)

    release = threading.Event()

    def slow_probe(s):
        release.wait(timeout=5)
        return s["name"], {"ms": 1, "code": 200, "ok": True}

    monkeypatch.setattr(live, "_probe", slow_probe)

    t0 = time.monotonic()
    body = client.get("/api/v1/live").json()
    elapsed = time.monotonic() - t0
    assert elapsed < 2.0, f"took {elapsed}s -- blocked on the slow probe instead of the deadline"
    assert body["sites"] and all(s["ok"] is None for s in body["sites"])

    # Let the background probe finish, then wait for its done-callback to
    # refresh the cache before asking again. Poll sparsely: `/live` is
    # rate-limited to 12 calls/min/tenant, and this test has already spent
    # one on the initial call above.
    release.set()
    for _ in range(9):
        time.sleep(0.3)
        again = client.get("/api/v1/live").json()
        if all(s["ok"] for s in again["sites"]):
            break
    else:
        raise AssertionError("background probe result never reached the cache")


def test_live_cache_refresh_never_overwrites_a_newer_generation(client, monkeypatch):
    """A slow probe from an OLD cycle that finishes after a NEWER cycle has
    already been cached must not clobber the newer cycle's results (or
    reset the cache's TTL clock) -- regression test for the generation
    counter in `_watch_pending`/`_refresh` (fix round 2)."""
    _seeded_with_sites(client)
    import guardian_hub.routers.live as live

    live.cache_clear()
    monkeypatch.setattr(live, "_GATHER_DEADLINE", 0.2)

    release = threading.Event()

    def slow_probe(s):
        release.wait(timeout=5)
        return s["name"], {"ms": 1, "code": 200, "ok": False}

    # Cycle 1: every probe blocks on `release`, so `/live` returns at the
    # deadline with ok=None and leaves the slow futures running in the
    # background (same mechanics as the deadline test above).
    monkeypatch.setattr(live, "_probe", slow_probe)
    body = client.get("/api/v1/live").json()
    assert body["sites"] and all(s["ok"] is None for s in body["sites"])

    # Cycle 2: force the TTL to expire immediately so the very next `/live`
    # call starts a fresh probe cycle instead of reusing cycle 1's cache
    # entry, then answer fast with a distinguishable result.
    monkeypatch.setattr(live, "_CACHE_TTL", 0.0)

    def fast_probe(s):
        return s["name"], {"ms": 5, "code": 200, "ok": True}

    monkeypatch.setattr(live, "_probe", fast_probe)
    body2 = client.get("/api/v1/live").json()
    assert body2["sites"] and all(s["ok"] is True and s["ms"] == 5 for s in body2["sites"])

    # Read the cache the same way `_cached_probe` does: after cycle 2 there
    # is exactly one entry (this test's tenant), holding cycle 2's results.
    with live._cache_lock:
        [(cached_ts_before, cached_gen_before, _)] = list(live._cache.values())

    # Now let cycle 1's slow probe finish. Its done-callback (`_refresh`)
    # must see that a newer generation is already cached and bail out
    # without overwriting it or touching the cached timestamp.
    release.set()
    for _ in range(9):
        time.sleep(0.3)
        with live._cache_lock:
            [(ts_now, gen_now, sites_now)] = list(live._cache.values())
        if any(s.ok is False for s in sites_now) or gen_now != cached_gen_before:
            raise AssertionError("stale cycle-1 result overwrote the fresher cycle-2 cache entry")

    assert gen_now == cached_gen_before, "generation must not change"
    assert ts_now == cached_ts_before, "TTL clock must not be reset by a stale refresh"
    assert all(s.ok is True and s.ms == 5 for s in sites_now), \
        "cache must still show cycle 2's result, not cycle 1's stale probe"

    # And a fresh GET must reflect the same thing, not the stale data.
    again = client.get("/api/v1/live").json()
    assert all(s["ok"] is True and s["ms"] == 5 for s in again["sites"])


def test_overview_answers_quickly_on_the_demo_tenant(client):
    """Catches N+1 queries: the 30-day demo tenant must render fast."""
    seeded_demo(client)
    t0 = time.monotonic()
    assert client.get("/api/v1/overview").status_code == 200
    assert time.monotonic() - t0 < 2.0






# --------------------------------------------------------------------------
# Task 74: the five hero cards' figures, and the panels under them
# --------------------------------------------------------------------------

def test_overview_hero_card_figures(client):
    """Every one of the five cards has a hub-side figure behind it, so the web
    app never has to count rows itself."""
    seeded_demo(client)
    o = client.get("/api/v1/overview").json()

    # 1. Are you watching? -- freshness, plus what the sensor said about its run.
    assert o["health"]["sensor_state"] in ("online", "silent")
    assert o["health"]["scans_last_hour"] is not None
    assert o["health"]["run_seconds"] is not None

    # 2. What do I have?
    assets = o["assets"]
    assert assets["total"] > 0
    assert assets["servers"] > 0 and assets["sites"] > 0
    assert o["devices"]["online"] >= 0

    # 3. What changed? -- alerts opened/resolved plus fixes, in 24 h.
    assert o["changed_24h"] >= 1

    # 4. What is dangerous?
    inc = o["incidents"]
    assert inc["open"] == inc["crit"] + inc["warn"] or inc["open"] >= inc["crit"]
    assert len(o["incident_briefs"]) <= 3
    for b in o["incident_briefs"]:
        assert b["title"] and b["sev"] in ("CRIT", "WARN", "INFO")
        # Task 83/A2: the brief now opens the incident's own page, not the
        # inbox filter -- an incident has its own reloadable URL.
        assert b["href"].startswith(f"/incidents/{b['id']}")

    # 5. What should I do? -- and the count agrees with the sidebar badge's.
    recs = o["recs"]
    assert recs["open"] > 0 and recs["top"]
    assert recs["open"] == client.get("/api/v1/inbox?view=recs").json()["counts"]["recs"]

    assert sd.OverviewPayload.model_validate(o)


def test_overview_health_panel_reports_every_feed_and_channel(client):
    seeded_demo(client)
    h = client.get("/api/v1/overview").json()["health"]
    names = {f["name"] for f in h["feeds"]}
    assert {"sites", "servers", "lan", "dns", "speed", "path", "heartbeats"} <= names
    assert h["sensors_total"] >= 1 and h["sensors_active"] >= 1
    assert h["last_event_ts"] and h["last_event_age_s"] is not None
    assert h["dns_filter_state"] in ("on", "down", "off")
    assert h["dns_blocked_24h"] > 0            # the demo DNS filter really blocks
    assert h["notify_channel"] == "slack"       # the enabled destination, not the retired implicit topic
    assert h["autofix_24h"] >= 0


def test_overview_path_health_bands(client):
    seeded_demo(client)
    paths = client.get("/api/v1/overview").json()["paths"]
    assert paths, "the demo scan measures three WAN anchors"
    for p in paths:
        assert p["name"] and p["ip"]
        assert p["state"] in ("good", "warn", "bad")
        # A measured anchor with no loss and a sane latency is good.
        if p["avg_ms"] is not None and p["loss_pct"] == 0 and p["avg_ms"] <= 120:
            assert p["state"] == "good"


def test_overview_score_trend_7d_is_a_window_of_the_30_day_series(client):
    seeded_demo(client)
    o = client.get("/api/v1/overview").json()
    week, month = o["score_trend_7d"]["points"], o["score_trend"]["points"]
    assert week and month
    assert len(week) <= len(month)
    assert week[0]["ts"] >= month[0]["ts"]
    # The 7-day window is undecimated (168 hours < 240 points) while the 30-day
    # one groups three hours per point and labels each group by its FIRST hour,
    # so the week's newest point is at or ahead of the month's.
    assert week[-1]["ts"] >= month[-1]["ts"]


def test_overview_hero_figures_are_zero_on_an_empty_tenant(client):
    """A tenant with no scan gets zeros and nulls, never a fabricated figure."""
    from tests.test_auth import login, seed_user
    seed_user(username="bo", email="b@x.com", tenant_slug="empty", role="viewer")
    login(client, "bo", "b@x.com")
    o = client.get("/api/v1/overview").json()
    assert o["changed_24h"] == 0
    assert o["assets"] == {"total": 0, "servers": 0, "sites": 0}
    assert o["incidents"]["open"] == 0 and o["incident_briefs"] == []
    assert o["recs"] == {"open": 0, "critical": 0, "high": 0, "top": ""}
    assert o["paths"] == []
    h = o["health"]
    assert h["sensor_state"] == "unknown"
    assert h["run_seconds"] is None and h["scans_last_hour"] is None
    assert h["feeds"] == [] and h["last_event_ts"] == "" and h["last_event_age_s"] is None
    assert sd.OverviewPayload.model_validate(o)
