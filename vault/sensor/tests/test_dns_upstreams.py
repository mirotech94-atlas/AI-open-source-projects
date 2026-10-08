"""Vault's upstream must itself filter malware, not just resolve.

Cloudflare runs three resolvers: 1.1.1.1 plain, 1.1.1.2 malware-filtering,
1.1.1.3 malware + adult. Vault asked for the plain one, throwing away
free protection on top of its own finite blocklists.
"""
from guardian_sensor import dnsfilter as d


def test_the_default_upstream_filters_malware_and_phishing():
    doh, plain = d.upstreams_for({})
    assert doh[0] == "https://security.cloudflare-dns.com/dns-query"
    assert plain == ("1.1.1.2", "1.0.0.2"), "1.1.1.1 resolves everything and filters nothing"


def test_the_plain_cloudflare_resolver_is_never_a_default():
    # The whole point: a fallback that silently stops filtering is worse
    # than the outage it covers for.
    for cfg in ({}, {"family_filter": True}):
        _doh, plain = d.upstreams_for(cfg)
        assert "1.1.1.1" not in plain and "1.0.0.1" not in plain


def test_every_default_upstream_is_a_filtering_one():
    for cfg in ({}, {"family_filter": True}):
        doh, _plain = d.upstreams_for(cfg)
        assert all("cloudflare-dns.com" in u or "quad9" in u for u in doh)
        assert "https://cloudflare-dns.com/dns-query" not in doh, "that is the unfiltered endpoint"


def test_family_filter_moves_the_upstream_too_not_just_the_blocklist():
    doh, plain = d.upstreams_for({"family_filter": True})
    assert doh[0] == "https://family.cloudflare-dns.com/dns-query"
    assert plain == ("1.1.1.3", "1.0.0.3")


def test_an_operator_who_named_their_own_resolver_still_gets_it():
    doh, _plain = d.upstreams_for({"upstreams": ["https://my.dns/dns-query"]})
    assert doh == ["https://my.dns/dns-query"]
