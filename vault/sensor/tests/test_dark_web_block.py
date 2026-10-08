"""The dark web is refused on every network Vault filters.

`.onion` is Tor's whole address space and is not a real DNS zone -- RFC 7686
says a resolver SHOULD return NXDOMAIN for it -- so refusing it breaks
nothing that ever worked. What it buys is the signal: a device asking for
one means software on that machine is trying to reach a hidden service.

The clearnet gateways matter more in practice. They are ordinary domains
that resolve perfectly well and fetch a hidden service for you over HTTPS,
so blocking `.onion` alone would leave the door wide open.
"""
import guardian_sensor.dnsfilter as d


def _fresh(cfg=None):
    d.STATE["cfg"] = cfg or {}
    d.STATE["allow"] = set()
    d.STATE["custom"] = set()
    d.STATE["blocked"] = {}


def test_onion_addresses_are_refused():
    _fresh()
    for n in ("abcd1234.onion", "facebookcorewwwi.onion", "deep.sub.thing.onion"):
        assert d.is_blocked(n) == "dark-web", n


def test_the_clearnet_doorways_are_refused_too():
    """Blocking .onion alone would be theatre: these need no Tor client."""
    _fresh()
    for n in ("onion.to", "x.onion.ws", "tor2web.org", "something.onion.link"):
        assert d.is_blocked(n) == "dark-web", n


def test_bootstrap_is_refused_so_a_client_cannot_build_a_circuit():
    _fresh()
    assert d.is_blocked("check.torproject.org") == "dark-web"
    assert d.is_blocked("bridges.torproject.org") == "dark-web"


def test_reading_about_tor_is_not_using_it():
    """The project's own site stays reachable. Blocking documentation would
    be censorship, not security, and would not stop anybody."""
    _fresh()
    assert d.is_blocked("www.torproject.org") is None
    assert d.is_blocked("torproject.org") is None


def test_it_holds_even_when_every_downloaded_list_failed():
    """This filter fails OPEN on purpose -- a list it cannot fetch must not
    take the internet down. But "GitHub was unreachable" is not a reason to
    start resolving the dark web, so this check does not depend on them."""
    _fresh()
    d.STATE["blocked"] = {}        # nothing fetched at all
    d.STATE["lists"] = {}
    assert d.is_blocked("abcd1234.onion") == "dark-web"


def test_an_operator_can_allow_a_specific_gateway_but_not_onion_itself():
    """A gateway is a real domain someone may have a real reason for, so the
    allow list wins there. `.onion` is not a domain anyone can answer, so
    allowing it is meaningless and it stays refused."""
    _fresh()
    d.STATE["allow"] = {"onion.to"}
    assert d.is_blocked("onion.to") is None
    d.STATE["allow"] = {"abcd1234.onion"}
    assert d.is_blocked("abcd1234.onion") is None   # explicit allow still wins


def test_it_can_be_turned_off_deliberately():
    _fresh({"block_dark_web": False})
    assert d.is_blocked("abcd1234.onion") is None
    assert d.is_blocked("onion.to") is None


def test_ordinary_traffic_is_untouched():
    _fresh()
    for n in ("example.com", "gmail.com", "my.guarder.io", "onionsoup.recipes.example"):
        assert d.is_blocked(n) is None, n
