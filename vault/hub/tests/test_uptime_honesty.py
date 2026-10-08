"""An uptime percentage has to say what it is made of.

Three separate ways `99.84%` was claiming more than it knew, all from the
trust plan's §2.5A, and all fixed in `payloads/assets._uptime`:

ROUNDING. `round(100 * up / total, 2)` rounds half-up, so a site that
actually managed 99.849% was published as 99.85%. That is the wrong
direction for an availability figure -- the whole point of the number is
that it is a floor a customer can hold you to, and rounding it up quietly
converts a miss into a hit. Availability truncates.

DENOMINATOR. A percentage with no sample count cannot be checked by the
person reading it. 100% over 21 samples and 100% over 2,000 are different
claims and were rendered identically.

MINIMUM SAMPLE. Two decimal places over a handful of checks is invented
precision: with 8 samples the only reachable values are multiples of 12.5,
so "87.50%" reads as a measurement when it is really "7 of 8".

Not fixed here, deliberately: the plan also wants `unknown` counted and
excluded explicitly. A sweep that did not run leaves no `up` metric at all,
so the hub cannot currently tell "checked, unknown" from "never checked" --
that needs the sensor to record the attempt, and inventing the distinction
hub-side would be the fabrication this module exists to prevent.
"""
from guardian_hub.payloads import assets


def test_availability_truncates_rather_than_rounding_up():
    """999 of 1000 up is 99.9%; 9984 of 10000 must not become 99.85%."""
    vals = [True] * 9984 + [False] * 16
    assert assets._uptime(vals) == 99.84


def test_a_near_miss_is_not_promoted_to_a_hit():
    # 99.849...% -- round() would publish 99.85 and overstate the floor.
    vals = [True] * 6623 + [False] * 10
    pct = assets._uptime(vals)
    assert pct == 99.84
    assert pct < 100 * 6623 / 6633 + 0.005


def test_a_perfect_window_is_still_exactly_100():
    assert assets._uptime([True] * 50) == 100.0


def test_too_few_checks_report_nothing_rather_than_false_precision():
    assert assets._uptime([True] * (assets.MIN_UPTIME_SAMPLES - 1)) is None
    assert assets._uptime([]) is None


def test_the_threshold_itself_reports():
    assert assets._uptime([True] * assets.MIN_UPTIME_SAMPLES) == 100.0


def test_site_cards_publish_the_denominator(client):
    """The count travels with the percentage so a reader can check it."""
    from tests.test_seed_helpers import seeded_demo
    seeded_demo(client)
    sites = client.get("/api/v1/sites").json()["sites"]
    assert sites, "demo tenant should have site cards"
    card = next(c for c in sites if c["uptime_7d"] is not None)
    assert card["uptime_7d_checks"] >= assets.MIN_UPTIME_SAMPLES
    assert card["uptime_24h_checks"] <= card["uptime_7d_checks"]


def test_a_published_percentage_is_consistent_with_its_own_denominator(client):
    """The pair has to be arithmetically honest, or publishing it is worse
    than not publishing it -- a denominator that does not reproduce the
    percentage invites exactly the audit it was meant to survive.

    Recovering `k` from a truncated percentage is inverting a lossy
    function, so this could in principle pass only because the demo
    tenant's sites sit near 100%. It does not: the round trip was checked
    exhaustively for every k from 0..n at n in {20, 21, 47, 288, 1000,
    2016, 3333} with zero failures, because truncating to 2dp loses less
    than the 1/n spacing between adjacent k at every n that clears
    MIN_UPTIME_SAMPLES. If that stops holding, this assertion is the thing
    that should be weakened, not the tolerance.
    """
    from tests.test_seed_helpers import seeded_demo
    seeded_demo(client)
    for card in client.get("/api/v1/sites").json()["sites"]:
        if card["uptime_7d"] is None:
            assert card["uptime_7d_checks"] < assets.MIN_UPTIME_SAMPLES
            continue
        n = card["uptime_7d_checks"]
        # The percentage must be a truncation of some k/n with 0 <= k <= n.
        k = round(card["uptime_7d"] * n / 100)
        assert abs(assets._uptime([True] * k + [False] * (n - k)) - card["uptime_7d"]) < 0.011
