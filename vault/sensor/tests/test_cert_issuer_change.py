"""A certificate that changed CA is worth one sentence to its owner.

Trust plan §2.5I lists four classic certificate bugs. This covers the fourth
— watching for an unexpected issuer change — because it is the one nobody at
this price point watches for and it costs one comparison against state
Vault already keeps.

The others are deliberately not here:

- LEAF-ONLY EXPIRY (the chain's intermediate can expire first) needs the
  served chain, and `ssl.SSLSocket.get_verified_chain()` landed in Python
  3.13. This sensor targets 3.10, so reading the chain means adding
  `cryptography` as a dependency to an agent people install with root on
  their own servers. That is a supply-chain decision, not a bug fix.
- RENEWED BUT NOT SERVED (certbot succeeded, nginx never reloaded) needs the
  served cert's serial compared against the newest on disk, so it needs SSH
  and per-host certificate paths. It is the most valuable of the four and
  the largest.

SEVERITY IS WARN, NOT CRIT, AND THAT IS THE CAREFUL PART. An issuer change
can mean interception, but the overwhelmingly common cause is a routine CA
migration or a CA renaming its own organisation field. Paging someone at 3am
because Let's Encrypt changed a string is precisely the crying-wolf the rest
of this plan is about, and a CRIT that is usually nothing teaches people to
dismiss the one that is not.
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


def fire(last, cfg, state):
    return {a["key"]: a for a in alerts.build_alerts(copy.deepcopy(last), cfg, copy.deepcopy(state))}


def test_a_changed_issuer_is_reported(): 
    last, cfg, state = load()
    site = last["sites"][0]
    site["cert_issuer"] = "Surprise CA Inc"
    state.setdefault("sites", {}).setdefault(site["name"], {})["cert_issuer"] = "Let's Encrypt"
    got = fire(last, cfg, state)
    key = f"cert:issuer:{site['name']}"
    assert key in got
    assert got[key]["sev"] == "WARN", "a routine CA migration must not page like a breach"
    assert "Let's Encrypt" in got[key]["msg"] and "Surprise CA Inc" in got[key]["msg"]


def test_an_unchanged_issuer_is_silent():
    last, cfg, state = load()
    site = last["sites"][0]
    site["cert_issuer"] = "Let's Encrypt"
    state.setdefault("sites", {}).setdefault(site["name"], {})["cert_issuer"] = "Let's Encrypt"
    assert f"cert:issuer:{site['name']}" not in fire(last, cfg, state)


def test_the_first_sighting_of_a_site_is_not_a_change():
    """Nothing to compare against must mean silence, not an alert on every
    newly added site — the day-one flood the plan warns about (§2.2)."""
    last, cfg, state = load()
    site = last["sites"][0]
    site["cert_issuer"] = "Let's Encrypt"
    state["sites"] = {}
    assert f"cert:issuer:{site['name']}" not in fire(last, cfg, state)


def test_an_unreadable_certificate_is_not_an_issuer_change():
    """A failed TLS handshake leaves no issuer. Reading that as "the issuer
    changed to nothing" would fire on every transient network blip."""
    last, cfg, state = load()
    site = last["sites"][0]
    site.pop("cert_issuer", None)
    site["cert_error"] = "timed out"
    state.setdefault("sites", {}).setdefault(site["name"], {})["cert_issuer"] = "Let's Encrypt"
    assert f"cert:issuer:{site['name']}" not in fire(last, cfg, state)


def test_an_unknown_issuer_string_is_not_treated_as_a_change():
    """`sites.check` writes "?" when the issuer field is missing from an
    otherwise valid certificate. That is absence of information, and
    comparing it against a real name would manufacture a change."""
    last, cfg, state = load()
    site = last["sites"][0]
    site["cert_issuer"] = "?"
    state.setdefault("sites", {}).setdefault(site["name"], {})["cert_issuer"] = "Let's Encrypt"
    assert f"cert:issuer:{site['name']}" not in fire(last, cfg, state)
