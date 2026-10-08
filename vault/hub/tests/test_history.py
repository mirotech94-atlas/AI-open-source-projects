"""Task 57: `GET /api/v1/history`.

Runs against the 30-day demo tenant (`seed.seed_demo`) so the assertions
exercise the real hourly-mean/decimate path and a scan log with real
`Scan.raw` blobs, rather than a two-row fixture.
"""

import datetime as dt

from guardian_hub import schemas_dash as sd
from guardian_hub.db import SessionLocal
from guardian_hub.payloads import history as history_payload
from guardian_hub.seed import seed_demo
from tests.test_seed_helpers import seeded_demo, sign_in_as_demoadmin


def seeded_demo_now(client):
    """`seeded_demo`, but with the synthesized history anchored to *now*
    rather than to the fixture's own (fixed, ever-aging) timestamp -- the only
    way a window measured in hours can contain anything at all."""
    db = SessionLocal()
    try:
        seed_demo(db, anchor=dt.datetime.now(dt.timezone.utc))
    finally:
        db.close()
    # The ordinary emailed-code sign-in, not the public demo code: a demo-code
    # session is read-only (audit 2026-09-28), and these tests write.
    sign_in_as_demoadmin(client)


def test_history_payload_is_render_ready(client):
    seeded_demo(client)
    h = client.get("/api/v1/history?days=30").json()

    assert h["days"] == 30
    assert h["scan_count"] > 0
    assert h["charts"], "the demo tenant has metrics, so there must be charts"
    # Every series arrives already bucketed and decimated: the web app scales
    # and draws, it never buckets.
    for chart in h["charts"]:
        assert chart["key"] and chart["label"]
        assert chart["series"]
        for series in chart["series"]:
            assert 0 < len(series["points"]) <= history_payload.MAX_POINTS
            # oldest first, so a line chart can walk the array as drawn
            stamps = [p["ts"] for p in series["points"]]
            assert stamps == sorted(stamps)
    # rendering-ready: it validates as the declared contract, nothing extra
    assert sd.HistoryPayload.model_validate(h)


def test_history_scan_log_is_counted_hub_side_newest_first(client):
    seeded_demo(client)
    h = client.get("/api/v1/history?days=30").json()

    rows = h["scans"]
    assert rows and len(rows) <= history_payload.SCAN_LOG_LIMIT
    assert [r["ts"] for r in rows] == sorted((r["ts"] for r in rows), reverse=True)
    assert h["last_ts"] == rows[0]["ts"] and h["first_ts"] == rows[-1]["ts"]
    for r in rows:
        assert r["sites_total"] >= r["sites_ok"] >= 0
        assert r["hosts_total"] >= r["hosts_ok"] >= 0
        assert r["crit"] >= 0 and r["warn"] >= 0
    # The header reports the whole range even though the log itself is capped.
    assert h["scan_count"] >= len(rows)


def test_history_event_log_carries_kind_and_in_app_href(client):
    seeded_demo(client)
    h = client.get("/api/v1/history?days=30").json()

    events = h["events"]
    assert events and len(events) <= history_payload.EVENT_LOG_LIMIT
    assert [e["ts"] for e in events] == sorted((e["ts"] for e in events), reverse=True)
    for e in events:
        assert e["kind"]
        assert e["href"].startswith(("/assets/", "/inbox", "/history"))


def test_history_range_narrows_and_is_validated(client):
    seeded_demo(client)
    wide = client.get("/api/v1/history?days=30").json()
    narrow = client.get("/api/v1/history?days=1").json()

    assert narrow["days"] == 1
    assert narrow["scan_count"] <= wide["scan_count"]

    assert client.get("/api/v1/history?days=0").status_code == 422
    assert client.get("/api/v1/history?days=999").status_code == 422


def test_history_network_filter_resolves_or_404s(client):
    seeded_demo(client)
    nets = client.get("/api/v1/networks").json()["networks"]
    assert nets, "the demo tenant seeds two networks"

    scoped = client.get(f"/api/v1/history?days=30&network={nets[0]['slug']}").json()
    assert scoped["network"]["slug"] == nets[0]["slug"]

    # An unknown ref is never silently widened to the whole tenant.
    assert client.get("/api/v1/history?days=30&network=nope").status_code == 404


def test_history_devices_series_is_the_network_scoped_one(client):
    """`?network=` narrows the sources that carry a network. "Devices online"
    is built from presence samples, which do — so a single network's series
    can never exceed the whole tenant's at any bucket."""
    seeded_demo(client)
    nets = client.get("/api/v1/networks").json()["networks"]

    def devices(payload):
        for c in payload["charts"]:
            if c["key"] == "devices":
                return {p["ts"]: p["value"] for p in c["series"][0]["points"]}
        return {}

    whole = devices(client.get("/api/v1/history?days=30").json())
    one = devices(client.get(f"/api/v1/history?days=30&network={nets[0]['slug']}").json())
    assert whole, "the demo tenant has presence samples"
    for ts, value in one.items():
        assert value <= whole[ts]


def test_history_is_tenant_scoped_and_readable_by_a_viewer(client):
    from tests.test_auth import login, seed_user
    seeded_demo(client)
    assert client.get("/api/v1/history?days=30").json()["scan_count"] > 0

    client.post("/api/v1/auth/logout")
    seed_user(username="ann", email="a@x.com", tenant_slug="other", role="viewer")
    login(client, "ann", "a@x.com")

    # History is read-only, so a viewer reads it in full — and sees only their
    # own tenant's (empty) record.
    other = client.get("/api/v1/history?days=30")
    assert other.status_code == 200
    body = other.json()
    assert body["scan_count"] == 0 and body["scans"] == [] and body["charts"] == []


# --------------------------------------------------------------------------
# Task 77 / F9: sub-day windows (`?hours=`)
# --------------------------------------------------------------------------

def test_history_accepts_a_six_hour_window_and_echoes_it_back(client):
    """`days` has a floor of 1, so the reference console's 6h range needs its
    own parameter. The payload says which of the two it answered."""
    seeded_demo_now(client)
    h = client.get("/api/v1/history?hours=6").json()

    assert h["hours"] == 6 and h["days"] == 0
    assert h["charts"], "a 6-hour window over history anchored to now has data"
    assert sd.HistoryPayload.model_validate(h)
    for chart in h["charts"]:
        for series in chart["series"]:
            stamps = [p["ts"] for p in series["points"]]
            assert stamps == sorted(stamps)
            assert 0 < len(series["points"]) <= history_payload.MAX_POINTS


def test_history_days_window_is_unchanged_by_the_hours_parameter(client):
    """The existing contract: a `days=` read still reports `days=<n>`, still
    buckets hourly, and never reports an hours window."""
    seeded_demo_now(client)
    h = client.get("/api/v1/history?days=7").json()

    assert h["days"] == 7 and h["hours"] == 0
    assert history_payload.bucket_for(0) == history_payload.HOUR_BUCKET_MIN
    for chart in h["charts"]:
        for series in chart["series"]:
            for point in series["points"]:
                # hourly means land on the hour; 5-minute buckets would not.
                assert point["ts"][14:16] == "00"


def test_history_sub_day_window_buckets_finer_than_an_hour(client):
    """A 6-hour window of hourly means is six points. It buckets at 5 minutes
    instead, so the panel is a trend rather than a bar chart."""
    seeded_demo_now(client)
    assert history_payload.bucket_for(6) == history_payload.FINE_BUCKET_MIN
    assert history_payload.bucket_for(24) == history_payload.HOUR_BUCKET_MIN

    minutes = {
        p["ts"][14:16]
        for chart in client.get("/api/v1/history?hours=6").json()["charts"]
        for series in chart["series"]
        for p in series["points"]
    }
    # The demo seeds 5-minute scan lines, so a 6h window keeps sub-hour stamps.
    assert minutes and minutes - {"00"}, f"every point still landed on the hour: {minutes}"


def test_history_rejects_an_out_of_range_hours_window(client):
    seeded_demo(client)
    assert client.get("/api/v1/history?hours=0&days=7").json()["days"] == 7
    assert client.get("/api/v1/history?hours=-1").status_code == 422
    assert client.get(f"/api/v1/history?hours={history_payload.MAX_HOURS + 1}").status_code == 422
    assert client.get("/api/v1/history?days=0").status_code == 422


def test_the_window_a_payload_reports_is_the_window_it_was_asked_for(client):
    """`build` used to clamp `days`/`hours` inline and then report the clamped
    values on the payload. That clamping now lives in `history.window`, which
    the owner console's per-account trends also call, so that a chart labelled
    "30 days" covers the same span on both screens.

    The extraction is only safe if the payload still reports what it drew: a
    caller tells "6 hours" from "1 day" by reading `days` and `hours` back off
    the response, and getting that wrong is a mislabelled axis rather than an
    error anyone would see.
    """
    seeded_demo(client)

    six_hours = client.get("/api/v1/history?hours=6").json()
    assert (six_hours["days"], six_hours["hours"]) == (0, 6)

    # `hours` wins over `days` when both are given.
    both = client.get("/api/v1/history?days=5&hours=6").json()
    assert (both["days"], both["hours"]) == (0, 6)

    week = client.get("/api/v1/history?days=7").json()
    assert (week["days"], week["hours"]) == (7, 0)


def test_an_out_of_range_window_is_refused_at_the_route_and_clamped_below_it():
    """Two different layers, on purpose, and they answer differently.

    The HTTP route 422s rather than quietly serving a different window than
    the one asked for -- a caller that requested 999 days and silently got 365
    would draw a year of data under a label saying three.

    `window` itself clamps instead, because its other caller is not HTTP: the
    owner console's trends builder calls it directly, and a helper that raised
    would make every direct caller re-validate. Belt and braces, with the
    stricter answer at the edge.
    """
    assert history_payload.window(days=999)[0] == history_payload.MAX_DAYS
    assert history_payload.window(days=0)[0] == 1
    assert history_payload.window(hours=99999)[1] == history_payload.MAX_HOURS


def test_the_route_refuses_a_window_it_cannot_honour(client):
    seeded_demo(client)
    assert client.get("/api/v1/history?days=999").status_code == 422
    assert client.get("/api/v1/history?days=0").status_code == 422
    assert client.get("/api/v1/history?hours=99999").status_code == 422
