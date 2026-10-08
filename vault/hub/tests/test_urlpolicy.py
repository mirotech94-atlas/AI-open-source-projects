"""Security re-review, fix round 2, finding 1: loopback bypass on the plain-
http branch of `urlpolicy.py`.

`host_is_private` treated any single-label host as private and `_parsed_ip`
only parsed dotted/colon literals, so an integer, hex, octal-dotted, or
IPv4-mapped-IPv6 loopback literal passed `check_url(..., allow_plain_for_private=True)`
and `check_host` (the MQTT variant) while still reaching 127.0.0.1. Each of
these must be rejected in both entry points.
"""

from guardian_hub.integrations import urlpolicy


LOOPBACK_LITERALS = [
    "http://2130706433/",              # decimal integer form of 127.0.0.1
    "http://0x7f000001/",              # hex form of 127.0.0.1
    "http://0177.0.0.1/",              # octal-looking dotted quad (ambiguous -> block outright)
    "http://[::ffff:127.0.0.1]:8123/", # IPv4-mapped IPv6
    "http://0.0.0.0/",                 # unspecified address
]


def test_check_url_rejects_loopback_literals():
    for url in LOOPBACK_LITERALS:
        err = urlpolicy.check_url(url, "Home Assistant base URL", allow_plain_for_private=True)
        assert err, f"expected rejection for {url!r}"


def test_check_url_rejects_loopback_literals_without_plain_allowance():
    # https-required fields (Discord webhook, Jira/ServiceNow base_url) hit
    # the scheme check first, but a caller must never see these pass as a
    # "private, plain-http OK" host either.
    for url in LOOPBACK_LITERALS:
        err = urlpolicy.check_url(url, "field", allow_plain_for_private=False)
        assert err, f"expected rejection for {url!r}"


def test_check_host_rejects_loopback_literals():
    hosts = [
        "2130706433",
        "2130706433:1883",
        "0x7f000001",
        "0x7f000001:1883",
        "0177.0.0.1",
        "0177.0.0.1:1883",
        "[::ffff:127.0.0.1]:8123",
        "0.0.0.0",
        "0.0.0.0:1883",
    ]
    for host in hosts:
        err = urlpolicy.check_host(host, "MQTT host")
        assert err, f"expected rejection for {host!r}"


def test_host_is_blocked_directly_for_each_literal_form():
    for host in ("2130706433", "0x7f000001", "0177.0.0.1", "::ffff:127.0.0.1", "0.0.0.0"):
        assert urlpolicy.host_is_blocked(host), f"expected blocked: {host!r}"


def test_host_is_private_is_false_for_loopback_literal_forms():
    for host in ("2130706433", "0x7f000001", "0177.0.0.1", "::ffff:127.0.0.1", "0.0.0.0"):
        assert not urlpolicy.host_is_private(host), f"expected not-private: {host!r}"


def test_genuinely_private_hosts_still_pass():
    # Guard against the fix over-tightening the existing, intended behavior.
    assert urlpolicy.host_is_private("192.168.1.10")
    assert urlpolicy.host_is_private("homeassistant.local")
    assert urlpolicy.host_is_private("nas")
    assert not urlpolicy.host_is_blocked("192.168.1.10")
    assert urlpolicy.check_url("http://homeassistant.local:8123/", "x",
                                allow_plain_for_private=True) == ""
    assert urlpolicy.check_host("192.168.1.10:1883", "MQTT host") == ""
