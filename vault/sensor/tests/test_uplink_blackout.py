"""When Vault's own uplink drops, that is one event, not N outages.

The trust plan calls fake outages the #1 credibility killer (§2.5A). The
worst source of them is structural: the sensor is a single vantage point,
so anything that breaks its own internet makes every monitored site look
down at the same instant. The customer gets five CRIT pages saying their
business is offline, discovers it was the sensor's ISP, and from then on
reads every Vault alert as probably-nothing. That is unrecoverable in a
way a missed alert is not.

Two independent signals have to agree before the alerts are collapsed:

1. EVERY configured site failed in the same sweep, with at least two
   configured. One site down is just that site. Two sites failing together
   is suspicious but not proof -- they might share a host.
2. The sensor is on the LAN it expects and still could not establish its own
   WAN address, which means two external address services were unreachable.
   This signal does not come from the sites at all, which is the point: the
   sites cannot be used as evidence about themselves.

Requiring both is what keeps the failure mode acceptable. A real outage that
takes down every site at once still pages as an outage, because the sensor
can still reach the internet. The collapse only happens when the vantage
point is provably blind, and then it says so instead of guessing.
"""
import copy
import json
import pathlib

from guardian_sensor import alerts

FX = pathlib.Path(__file__).parent / "fixtures"


def load():
    with open(FX / "last.json") as f:
        last = json.load(f)
    with open(FX / "config.json") as f:
        cfg = json.load(f)
    with open(FX / "state.json") as f:
        state = json.load(f)
    return last, cfg, state


def keys(last, cfg, state):
    return {a["key"] for a in alerts.build_alerts(copy.deepcopy(last), cfg, copy.deepcopy(state))}


def all_sites_down(last):
    for s in last["sites"]:
        s["ok"] = False
        s["code"] = 0
        s["error"] = "connection refused"
    return last


def test_the_normal_case_is_untouched_one_site_down_is_that_site_down():
    last, cfg, state = load()
    assert len(last["sites"]) >= 2
    last["sites"][0]["ok"] = False
    got = keys(last, cfg, state)
    assert f"site:{last['sites'][0]['name']}:down" in got
    assert "sensor:uplink" not in got


def test_every_site_down_with_no_internet_collapses_to_one_event():
    last, cfg, state = load()
    all_sites_down(last)
    last["on_home_lan"], last["wan_ip"] = True, None
    got = keys(last, cfg, state)
    assert "sensor:uplink" in got
    assert not [k for k in got if k.startswith("site:") and k.endswith(":down")], (
        "individual site outages must not also fire — that is the page storm"
    )


def test_every_site_down_while_the_sensor_still_has_internet_is_a_real_outage():
    """The case that must NOT be collapsed. If Vault can reach the
    internet and every site is unreachable, the sites really are down and
    silencing that would be the most expensive bug in the product."""
    last, cfg, state = load()
    all_sites_down(last)
    last["on_home_lan"], last["wan_ip"] = True, "203.0.113.9"
    got = keys(last, cfg, state)
    assert "sensor:uplink" not in got
    for s in last["sites"]:
        assert f"site:{s['name']}:down" in got


def test_a_sensor_off_its_expected_lan_does_not_claim_an_uplink_failure():
    """`wan_ip` is only probed when the sensor is on the LAN it expects, so
    None off-LAN means "not measured", not "no internet". Treating the two
    the same would collapse real outages for any sensor whose gateway
    changed."""
    last, cfg, state = load()
    all_sites_down(last)
    last["on_home_lan"], last["wan_ip"] = False, None
    got = keys(last, cfg, state)
    assert "sensor:uplink" not in got
    assert [k for k in got if k.startswith("site:") and k.endswith(":down")]


def test_one_configured_site_is_never_enough_to_infer_a_blackout():
    last, cfg, state = load()
    last["sites"] = [dict(last["sites"][0], ok=False, code=0, error="timeout")]
    last["on_home_lan"], last["wan_ip"] = True, None
    got = keys(last, cfg, state)
    assert "sensor:uplink" not in got
    assert f"site:{last['sites'][0]['name']}:down" in got


def test_a_blackout_does_not_charge_the_score_for_unmeasured_sites():
    """Sites are UNKNOWN during a blackout, not down, so they must not
    deduct Availability. Vault's own ISP having a bad minute is not a
    finding about the customer's security posture.

    The score does still move, and that is correct rather than a leftover:
    the one `sensor:uplink` CRIT counts as an active threat, because a blind
    watchdog IS worth flagging. The difference is that the movement is now
    attributable to a single named alert the owner can read, instead of N
    phantom outages. So the assertion is comparative -- a blackout must cost
    far less than the same sweep would if the sites really were down.
    """
    last, cfg, state = load()
    healthy = alerts.security_score(copy.deepcopy(last), cfg, copy.deepcopy(state))["total"]

    real = copy.deepcopy(last)
    all_sites_down(real)
    real["on_home_lan"], real["wan_ip"] = True, "203.0.113.9"
    real["alerts"] = alerts.build_alerts(copy.deepcopy(real), cfg, copy.deepcopy(state))
    real_total = alerts.security_score(real, cfg, copy.deepcopy(state))["total"]

    blind = copy.deepcopy(last)
    all_sites_down(blind)
    blind["on_home_lan"], blind["wan_ip"] = True, None
    blind["alerts"] = alerts.build_alerts(copy.deepcopy(blind), cfg, copy.deepcopy(state))
    blind_total = alerts.security_score(blind, cfg, copy.deepcopy(state))["total"]

    assert blind_total > real_total, "an unmeasured sweep must cost less than a measured outage"
    assert blind_total < healthy, "going blind is still worth flagging, not free"
    # The whole remaining gap is the single uplink CRIT (Active threats,
    # 15 per critical). If Availability were still charging for the sites
    # this would be 20 points worse.
    assert healthy - blind_total == 15


def test_a_real_all_site_outage_still_costs_the_score():
    last, cfg, state = load()
    healthy = alerts.security_score(copy.deepcopy(last), cfg, copy.deepcopy(state))["total"]
    all_sites_down(last)
    last["on_home_lan"], last["wan_ip"] = True, "203.0.113.9"
    blacked = alerts.security_score(copy.deepcopy(last), cfg, copy.deepcopy(state))["total"]
    assert blacked < healthy
