"""certbot succeeded; nginx was never reloaded. Nobody notices until it expires.

The trust plan calls this the single most common real-world certificate
outage and a genuinely differentiating check (§2.5I), and it is invisible to
every check Vault had. The renewal timer reports success, the file on
disk is valid for another ninety days, monitoring that reads the file says
everything is fine — and the server keeps presenting the old certificate
until the moment it expires and the site goes down.

The signal needs both vantage points at once, which is exactly what Vault
already has: it reads the served certificate over TLS like any visitor, and
it has SSH on the host that holds the file. If a host holds a certificate
for a hostname that expires LATER than the one actually being served, the
renewal happened and the service never picked it up.

Comparing expiry dates rather than serials on purpose. A serial mismatch is
true of any two different certificates, including a rollback or a second
unrelated cert for the same name; "the file on disk outlives what is being
served" is specifically the renewed-but-not-reloaded shape, and it is also
the shape whose remedy is a reload.
"""
import copy
import json
import pathlib

from guardian_sensor import alerts, hosts

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
    return {a["key"]: a for a in alerts.build_alerts(copy.deepcopy(last), cfg,
                                                     copy.deepcopy(state))}


# --- the host-side inventory -----------------------------------------------

def test_the_collector_parses_a_cert_inventory_off_the_wire():
    got = hosts.parse_certs("shop.example@Dec 25 10:00:00 2026 GMT;api.example@Jan  2 00:00:00 2027 GMT;")
    assert got == {"shop.example": "2026-12-25", "api.example": "2027-01-02"}


def test_an_empty_or_unparsable_inventory_is_empty_not_an_error():
    """A host without openssl, or without certbot, must not break the sweep
    or invent an entry -- it simply has nothing to say about certificates."""
    assert hosts.parse_certs("") == {}
    assert hosts.parse_certs("nonsense;;@;") == {}
    assert hosts.parse_certs(None) == {}


def test_the_latest_certificate_wins_when_a_host_holds_several_for_one_name():
    """Renewals leave the old file behind under some layouts. The question
    is what the host COULD serve, which is the newest one it has."""
    got = hosts.parse_certs("shop.example@Jan  1 00:00:00 2027 GMT;shop.example@Dec 25 10:00:00 2026 GMT;")
    assert got == {"shop.example": "2027-01-01"}


# --- the finding -----------------------------------------------------------

def _with_host_cert(last, host_of_site, served, on_disk):
    site = last["sites"][0]
    site["host"] = host_of_site
    site["cert_expires"] = served
    last["ssh"][0]["reachable"] = True
    last["ssh"][0]["certs_on_disk"] = {host_of_site: on_disk}
    return site


def test_a_renewed_certificate_that_is_not_being_served_is_reported():
    last, cfg, state = load()
    site = _with_host_cert(last, "shop.example", served="2026-10-01", on_disk="2026-12-30")
    got = keys(last, cfg, state)
    key = f"cert:stale-served:{site['name']}"
    assert key in got
    assert got[key]["sev"] == "WARN"
    assert "2026-12-30" in got[key]["msg"] and "2026-10-01" in got[key]["msg"]
    assert "reload" in got[key]["msg"].lower()


def test_a_server_already_serving_its_newest_certificate_is_silent():
    last, cfg, state = load()
    site = _with_host_cert(last, "shop.example", served="2026-12-30", on_disk="2026-12-30")
    assert f"cert:stale-served:{site['name']}" not in keys(last, cfg, state)


def test_a_host_holding_an_older_spare_copy_is_not_a_finding():
    """The old file lying around after a renewal is normal. Only a file that
    outlives what is being served means the service missed a reload."""
    last, cfg, state = load()
    site = _with_host_cert(last, "shop.example", served="2026-12-30", on_disk="2026-09-01")
    assert f"cert:stale-served:{site['name']}" not in keys(last, cfg, state)


def test_a_certificate_for_a_different_hostname_is_not_compared():
    """Hosts commonly hold certificates for names they do not serve to us.
    Matching on anything looser than the exact hostname would fire on every
    multi-site box."""
    last, cfg, state = load()
    site = last["sites"][0]
    site["host"] = "shop.example"
    site["cert_expires"] = "2026-10-01"
    last["ssh"][0]["reachable"] = True
    last["ssh"][0]["certs_on_disk"] = {"unrelated.example": "2027-01-01"}
    assert f"cert:stale-served:{site['name']}" not in keys(last, cfg, state)


def test_an_unreachable_host_makes_no_claim_about_certificates():
    """No inventory is not an empty inventory. A host Vault could not
    reach has not told it there is no newer certificate."""
    last, cfg, state = load()
    site = _with_host_cert(last, "shop.example", served="2026-10-01", on_disk="2026-12-30")
    last["ssh"][0]["reachable"] = False
    assert f"cert:stale-served:{site['name']}" not in keys(last, cfg, state)


def test_a_site_whose_own_certificate_could_not_be_read_is_not_compared():
    last, cfg, state = load()
    site = _with_host_cert(last, "shop.example", served="2026-10-01", on_disk="2026-12-30")
    site.pop("cert_expires")
    assert f"cert:stale-served:{site['name']}" not in keys(last, cfg, state)
