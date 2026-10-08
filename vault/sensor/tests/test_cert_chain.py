"""The certificate that expires first is not always the one on the website.

A served chain is leaf + intermediates, and the site stops working when ANY
of them lapses. Vault read the leaf's `notAfter` and reported that, so an
intermediate expiring next week behind a leaf good for seventy days showed
as "74 days left" — a confident number pointing at the wrong certificate
(trust plan §2.5I, the first of its four).

NO NEW DEPENDENCY. `ssl.SSLSocket.get_verified_chain()` is Python 3.13 and
this sensor targets 3.10, which left "add `cryptography` to an agent people
install as root" as the apparent alternative — a supply-chain decision for
the owner rather than a bug fix. It turns out not to be needed: `openssl
s_client -showcerts` prints the whole chain, and the sensor already shells
out to openssl for the host-side certificate inventory. So this costs a
subprocess, not a dependency.

FAILING TO READ THE CHAIN REPORTS NOTHING, never "fine". A host without
openssl, a timeout, a server that sends no intermediates — all of those mean
Vault did not measure the chain, which is different from measuring it and
finding it healthy. The leaf's own expiry still stands on its own.
"""
import datetime as dt

from guardian_sensor import sites

LEAF = "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----"
MID = "-----BEGIN CERTIFICATE-----\nBBBB\n-----END CERTIFICATE-----"


def _fmt(days):
    """`days` whole days out, plus an hour.

    The hour is not decoration. `days_left` truncates -- the same convention
    the leaf check has always used -- so a certificate formatted to the exact
    second `days` from now lands a hair under and reports `days - 1`. The
    buffer makes the expected value unambiguous instead of encoding a
    rounding accident into the test.
    """
    when = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days, hours=1)
    return when.strftime("%b %d %H:%M:%S %Y GMT")


def test_the_chain_reports_the_earliest_expiry_not_the_leafs(monkeypatch):
    """The whole point: an intermediate lapsing first is what takes the site
    down, and it is invisible to a leaf-only check."""
    monkeypatch.setattr(sites, "_chain_pems", lambda host, port=443: [LEAF, MID])
    monkeypatch.setattr(sites, "_cert_enddate",
                        lambda pem: _fmt(70) if pem == LEAF else _fmt(9))
    got = sites.chain_expiry("shop.example")
    assert got is not None
    assert got["days_left"] == 9
    assert got["certs"] == 2
    assert got["weakest"] != "leaf", "the finding is about the intermediate"


def test_a_healthy_chain_agrees_with_the_leaf(monkeypatch):
    monkeypatch.setattr(sites, "_chain_pems", lambda host, port=443: [LEAF, MID])
    monkeypatch.setattr(sites, "_cert_enddate",
                        lambda pem: _fmt(70) if pem == LEAF else _fmt(300))
    got = sites.chain_expiry("shop.example")
    assert got["days_left"] == 70 and got["weakest"] == "leaf"


def test_a_chain_that_cannot_be_read_reports_nothing_rather_than_fine(monkeypatch):
    """Unmeasured is not healthy — the same rule as the score coverage gate."""
    monkeypatch.setattr(sites, "_chain_pems", lambda host, port=443: [])
    assert sites.chain_expiry("shop.example") is None


def test_an_unparsable_certificate_does_not_poison_the_answer(monkeypatch):
    """One cert openssl cannot read must not silently become the weakest
    link, nor discard the ones it could read."""
    monkeypatch.setattr(sites, "_chain_pems", lambda host, port=443: [LEAF, MID])
    monkeypatch.setattr(sites, "_cert_enddate",
                        lambda pem: _fmt(40) if pem == LEAF else None)
    got = sites.chain_expiry("shop.example")
    assert got["days_left"] == 40 and got["certs"] == 1


def test_openssl_being_absent_is_not_an_error(monkeypatch):
    """A sensor on a host without openssl simply has nothing to say."""
    def boom(host, port=443):
        raise FileNotFoundError("openssl")
    monkeypatch.setattr(sites, "_chain_pems", boom)
    assert sites.chain_expiry("shop.example") is None


def test_the_pem_splitter_finds_every_certificate_in_the_output():
    out = ("depth=2 O = Test\n" + LEAF + "\n more noise\n" + MID + "\nEND\n")
    assert sites._split_pems(out) == [LEAF, MID]


def test_the_pem_splitter_is_empty_on_noise():
    assert sites._split_pems("connect: Connection refused") == []


# --- the alert has to fire on the weakest link ----------------------------

import copy
import json
import pathlib

from guardian_sensor import alerts

FX = pathlib.Path(__file__).parent / "fixtures"


def _load():
    with open(FX / "last.json") as f:
        last = json.load(f)
    with open(FX / "config.json") as f:
        cfg = json.load(f)
    with open(FX / "state.json") as f:
        state = json.load(f)
    return last, cfg, state


def _cert_alert(last, cfg, state):
    site = last["sites"][0]
    got = {a["key"]: a for a in alerts.build_alerts(copy.deepcopy(last), cfg,
                                                    copy.deepcopy(state))}
    return got.get(f"cert:{site['name']}")


def test_an_expiring_intermediate_raises_the_alert_the_leaf_would_not():
    """The regression this whole check exists for: a leaf good for seventy
    days hiding an intermediate with nine."""
    last, cfg, state = _load()
    site = last["sites"][0]
    site["cert_days_left"] = 70
    site["cert_chain_days_left"] = 5
    site["cert_chain_weakest"] = "intermediate 1"
    a = _cert_alert(last, cfg, state)
    assert a is not None and a["sev"] == "CRIT"
    assert "5 days" in a["msg"]
    assert "intermediate 1" in a["msg"], "say which certificate, or it looks wrong"


def test_a_healthy_chain_leaves_the_leafs_own_warning_alone():
    last, cfg, state = _load()
    site = last["sites"][0]
    site["cert_days_left"] = 5
    site["cert_expires"] = "2026-10-01"
    site["cert_chain_days_left"] = 300
    site["cert_chain_weakest"] = "intermediate 1"
    a = _cert_alert(last, cfg, state)
    assert a is not None and "5 days" in a["msg"] and "2026-10-01" in a["msg"]
    assert "intermediate" not in a["msg"], "the leaf is the problem; do not misattribute it"


def test_a_chain_that_could_not_be_read_leaves_the_leaf_check_untouched():
    """Unmeasured is not a reason to say less than Vault already knew."""
    last, cfg, state = _load()
    site = last["sites"][0]
    site["cert_days_left"] = 3
    site["cert_expires"] = "2026-09-25"
    site.pop("cert_chain_days_left", None)
    a = _cert_alert(last, cfg, state)
    assert a is not None and a["sev"] == "CRIT" and "3 days" in a["msg"]


def test_a_healthy_certificate_still_raises_nothing():
    last, cfg, state = _load()
    site = last["sites"][0]
    site["cert_days_left"] = 200
    site["cert_chain_days_left"] = 180
    assert _cert_alert(last, cfg, state) is None
