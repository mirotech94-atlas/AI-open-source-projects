"""DNS filter tests: block matching and the DNS wire format.

Everything here is built with struct in-process — no sockets are opened, no
lists are fetched, and importing the module must not create a data directory.
"""
import os
import struct

import pytest

from guardian_sensor import dnsfilter, paths


def _query(name, qtype=1, tid=b"\xab\xcd"):
    """A minimal DNS query packet: header + one question, no EDNS."""
    q = b""
    for label in name.split("."):
        q += bytes([len(label)]) + label.encode()
    q += b"\x00"
    return tid + struct.pack("!HHHHH", 0x0100, 1, 0, 0, 0) + q + struct.pack("!HH", qtype, 1)


@pytest.fixture
def lists(monkeypatch):
    """Deterministic block/allow lists for one test."""
    monkeypatch.setitem(dnsfilter.STATE, "custom", {"example.com"})
    monkeypatch.setitem(dnsfilter.STATE, "allow", set())
    monkeypatch.setitem(dnsfilter.STATE, "blocked", {"tracker.invalid": "stevenblack"})


# ---------------- import hygiene ----------------

def test_import_does_not_create_the_data_dir(monkeypatch, tmp_path):
    """Nexus' dns.py made its dns/ dir at import; ours must not."""
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path / "nothing-here"))
    assert dnsfilter.is_blocked("plain.example.org") is None
    assert not os.path.exists(str(tmp_path / "nothing-here"))


def test_is_blocked_works_before_load_cfg_ran():
    """STATE seeds allow/custom, so is_blocked never KeyErrors on a fresh import."""
    assert isinstance(dnsfilter.STATE["allow"], set)
    assert isinstance(dnsfilter.STATE["custom"], set)


# ---------------- block matching ----------------

def test_custom_block_matches_subdomain(lists):
    assert dnsfilter.is_blocked("ads.example.com") == "custom"
    assert dnsfilter.is_blocked("example.com") == "custom"


def test_public_list_block_reports_its_list_name(lists):
    assert dnsfilter.is_blocked("beacon.tracker.invalid") == "stevenblack"


def test_allow_list_wins_over_block(monkeypatch, lists):
    monkeypatch.setitem(dnsfilter.STATE, "allow", {"ads.example.com"})
    assert dnsfilter.is_blocked("ads.example.com") is None
    # a sibling under the same blocked parent is still blocked
    assert dnsfilter.is_blocked("other.example.com") == "custom"


def test_unrelated_domain_is_not_blocked(lists):
    assert dnsfilter.is_blocked("status.example.org") is None


# ---------------- wire format ----------------

def test_parse_question_round_trip():
    pkt = _query("ads.example.com", qtype=1)
    qname, qtype, qclass, qend = dnsfilter.parse_question(pkt)
    assert qname == "ads.example.com"
    assert (qtype, qclass) == (1, 1)
    assert qend == len(pkt)


def test_parse_question_rejects_a_short_packet():
    with pytest.raises(ValueError):
        dnsfilter.parse_question(b"\x00\x01\x02")


def test_blocked_response_is_noerror_with_a_zero_a_record():
    pkt = _query("ads.example.com", qtype=1)
    _, qtype, _, qend = dnsfilter.parse_question(pkt)
    resp = dnsfilter.blocked_response(pkt, qtype, qend)
    assert resp[:2] == pkt[:2]              # same transaction id
    assert resp[2] & 0x80                    # QR: this is a response
    assert (resp[3] & 0x0F) == 0             # rcode 0 = NOERROR
    qd, an, ns, ar = struct.unpack("!HHHH", resp[4:12])
    assert (qd, an, ns, ar) == (1, 1, 0, 0)
    assert resp[12:qend] == pkt[12:qend]     # question echoed back
    ans = resp[qend:]
    assert ans[:2] == b"\xc0\x0c"            # name compression pointer to the question
    rtype, rclass, ttl, rdlen = struct.unpack("!HHIH", ans[2:12])
    assert (rtype, rclass, rdlen) == (1, 1, 4)
    assert ttl == 300
    assert ans[12:16] == b"\x00\x00\x00\x00"  # 0.0.0.0


def test_blocked_response_for_aaaa_is_all_zero_v6():
    pkt = _query("ads.example.com", qtype=28)
    _, qtype, _, qend = dnsfilter.parse_question(pkt)
    resp = dnsfilter.blocked_response(pkt, qtype, qend)
    assert (resp[3] & 0x0F) == 0
    ans = resp[qend:]
    rtype, rclass, ttl, rdlen = struct.unpack("!HHIH", ans[2:12])
    assert (rtype, rclass, rdlen) == (28, 1, 16)
    assert ans[12:28] == b"\x00" * 16


def test_blocked_response_for_other_qtypes_has_no_answer():
    pkt = _query("ads.example.com", qtype=16)  # TXT
    _, qtype, _, qend = dnsfilter.parse_question(pkt)
    resp = dnsfilter.blocked_response(pkt, qtype, qend)
    qd, an, _, _ = struct.unpack("!HHHH", resp[4:12])
    assert (qd, an) == (1, 0)
    assert (resp[3] & 0x0F) == 0


def test_min_ttl_reads_the_smallest_answer_ttl():
    pkt = _query("ads.example.com", qtype=1)
    _, qtype, _, qend = dnsfilter.parse_question(pkt)
    base = dnsfilter.blocked_response(pkt, qtype, qend)
    assert dnsfilter.min_ttl(base) == 300
    assert dnsfilter.min_ttl(b"\x00" * 4) == 0


# ---------------- resolve() takes the block path without any network ----------------

def test_resolve_blocked_query_never_touches_an_upstream(monkeypatch, lists):
    def boom(*a, **k):
        raise AssertionError("a blocked query must not reach an upstream")

    monkeypatch.setattr(dnsfilter, "upstream_doh", boom)
    monkeypatch.setattr(dnsfilter, "upstream_udp", boom)
    resp, rec = dnsfilter.resolve(_query("ads.example.com"), "192.168.1.50")
    assert rec["blocked"] is True and rec["list"] == "custom" and rec["via"] == "block"
    assert rec["name"] == "ads.example.com" and rec["type"] == "A"
    assert rec["client"] == "192.168.1.50"
    assert (resp[3] & 0x0F) == 0
    assert resp.endswith(b"\x00\x00\x00\x00")   # answer is 0.0.0.0


def test_resolve_returns_no_response_for_a_malformed_packet():
    resp, rec = dnsfilter.resolve(b"\x00\x01", "192.168.1.50")
    assert resp is None and rec["name"] is None


# ---------------- config wiring ----------------

def test_load_cfg_reads_dns_block_allow_and_gateway(monkeypatch, tmp_path):
    import json
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    with open(paths.p("config.json"), "w") as f:
        json.dump({"lan": {"expected_gateway": "10.9.8.1"},
                   "dns": {"block": ["Bad.Example.COM."], "allow": ["good.example.com"],
                           "upstreams": ["https://dns.example/dns-query"]}}, f)
    monkeypatch.setitem(dnsfilter.STATE, "custom", set())
    monkeypatch.setitem(dnsfilter.STATE, "allow", set())
    monkeypatch.setitem(dnsfilter.STATE, "cfg", {})
    monkeypatch.setitem(dnsfilter.STATE, "gw", "192.168.1.1")
    dnsfilter.load_cfg()
    assert dnsfilter.STATE["custom"] == {"bad.example.com"}   # lowercased, dots stripped
    assert dnsfilter.STATE["allow"] == {"good.example.com"}
    assert dnsfilter.STATE["gw"] == "10.9.8.1"
    assert dnsfilter.STATE["cfg"]["upstreams"] == ["https://dns.example/dns-query"]


def test_write_log_creates_the_dns_dir_under_data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    dnsfilter.write_log({"ts": "t", "client": "192.168.1.5", "name": "ads.example.com",
                         "type": "A", "blocked": True, "list": "custom", "ms": 1})
    files = os.listdir(os.path.join(str(tmp_path), "dns"))
    assert any(f.startswith("queries-") and f.endswith(".jsonl") for f in files)


def test_write_log_skips_a_record_with_no_name(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path / "empty"))
    dnsfilter.write_log({"name": None})
    assert not os.path.exists(str(tmp_path / "empty"))


def test_temporary_exception_is_exact_and_expiry_does_not_need_config_reload(lists, monkeypatch):
    import datetime as dt
    expiry=dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=1)
    item={"domain":"example.com","expires":expiry.isoformat()}
    monkeypatch.setitem(dnsfilter.STATE,"cfg",{"exceptions":[item]})
    assert dnsfilter.is_blocked("example.com") is None
    assert dnsfilter.is_blocked("ads.example.com") == "custom"
    item["expires"]=(expiry-dt.timedelta(hours=2)).isoformat()
    assert dnsfilter.is_blocked("example.com") == "custom"
