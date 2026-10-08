"""The flow parser reads only headers and the named prefixes. See spec §2."""
import os, struct, tempfile
import datetime
from guardian_sensor import flows

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "flows_sample.pcap")

def test_rollup_keys_on_the_device_side_and_splits_direction():
    pkts = list(flows.read_pcap(FIX))
    rows = flows.rollup(pkts, local_prefixes=["192.168.1"])
    row = next(r for r in rows if r["peer_ip"] == "142.250.1.1")
    assert row["mac"] == "aa:bb:cc:00:00:01" and row["ip"] == "192.168.1.20"
    assert (row["peer_port"], row["proto"]) == (443, "tcp")
    assert row["pkts_in"] == 10 and row["pkts_out"] == 3
    assert row["bytes_in"] == 10 * (14 + 20 + 20 + 1400) and row["bytes_out"] == 3 * (14 + 20 + 20 + 60)
    # Verify minute flooring: parse timestamp and assert seconds are zeroed
    ts_obj = datetime.datetime.fromisoformat(row["ts"])
    assert row["source"] == "mirror" and ts_obj.second == 0 and ts_obj.microsecond == 0

def test_minute_flooring_at_boundary():
    # Two packets within the same minute floor to the same row;
    # a third across the minute boundary gets its own row.
    t0 = 1758326400.0  # arbitrary epoch baseline
    pkt1 = flows.Packet(t0 + 15, "aa:bb:cc:00:00:01", "aa:bb:cc:00:00:ff",
                        "192.168.1.20", "8.8.8.8", "tcp", 50000, 443, 100, b"")
    pkt2 = flows.Packet(t0 + 45, "aa:bb:cc:00:00:01", "aa:bb:cc:00:00:ff",
                        "192.168.1.20", "8.8.8.8", "tcp", 50000, 443, 100, b"")
    pkt3 = flows.Packet(t0 + 60, "aa:bb:cc:00:00:01", "aa:bb:cc:00:00:ff",
                        "192.168.1.20", "8.8.8.8", "tcp", 50000, 443, 100, b"")
    rows = flows.rollup([pkt1, pkt2, pkt3], local_prefixes=["192.168.1"])
    # Should have 2 rows (first two in one minute, third in next)
    ts_values = sorted({r["ts"] for r in rows})
    assert len(ts_values) == 2, f"Expected 2 unique timestamps, got {len(ts_values)}: {ts_values}"
    # The row for the earlier minute (first two packets) should have pkts_out == 2
    row_earlier = next(r for r in rows if r["ts"] == ts_values[0])
    assert row_earlier["pkts_out"] == 2
    # The row for the later minute (third packet) should have pkts_out == 1
    row_later = next(r for r in rows if r["ts"] == ts_values[1])
    assert row_later["pkts_out"] == 1

def test_lan_to_lan_flow_appears_once_per_device():
    # PC -> resolver on the same subnet: both are "devices", so both get a row
    pkts = list(flows.read_pcap(FIX))
    rows = flows.rollup(pkts, local_prefixes=["192.168.1"])
    macs = sorted(r["mac"] for r in rows if r["peer_port"] == 53 or r["ip"] == "192.168.1.9")
    assert macs == ["aa:bb:cc:00:00:01", "aa:bb:cc:00:00:ff"]

def test_cap_keeps_top_by_bytes_and_counts_the_drop():
    rows = [{"bytes_in": i, "bytes_out": 0} for i in range(10)]
    kept, dropped = flows.cap(rows, limit=3)
    assert [r["bytes_in"] for r in kept] == [9, 8, 7] and dropped == 7

def test_hints_lift_sni_and_nothing_else():
    # A minimal TLS ClientHello carrying SNI "printer.example"
    name = b"printer.example"
    ext = b"\x00\x00" + struct.pack("!H", len(name) + 5) + struct.pack("!H", len(name) + 3) + b"\x00" + struct.pack("!H", len(name)) + name
    body = b"\x03\x03" + b"\x00" * 32 + b"\x00" + b"\x00\x02\x13\x01" + b"\x01\x00" + struct.pack("!H", len(ext)) + ext
    hello = b"\x01" + struct.pack("!I", len(body))[1:] + body
    rec = b"\x16\x03\x01" + struct.pack("!H", len(hello)) + hello
    pkt = flows.Packet(0, "a", "b", "192.168.1.20", "1.1.1.1", "tcp", 50000, 443, 0, rec)
    assert flows.hints(pkt) == {"sni": "printer.example"}
    assert flows.hints(pkt._replace(payload=b"GET /secret HTTP/1.1\r\nHost: x")) == {}

def test_dhcp_hostname_and_vendor_survive_pad():
    # DHCP payload: 240-byte fixed header + options.
    # Option layout: [option_code][length][value...][option_code][length][value...]...
    # Option 0 is Pad (no length field). Option 255 is End.
    payload = b"\x00" * 240  # Fixed DHCP header
    # Add: pad byte (option 0), then option 12 with "reception-pc", option 60 with "MSFT 5.0", end
    hostname = b"reception-pc"
    vendor = b"MSFT 5.0"
    payload += b"\x00"  # Option 0 (Pad) — no length byte
    payload += b"\x0c" + bytes([len(hostname)]) + hostname  # Option 12
    payload += b"\x3c" + bytes([len(vendor)]) + vendor  # Option 60
    payload += b"\xff"  # Option 255 (End)
    pkt = flows.Packet(0, "a", "b", "192.168.1.20", "192.168.1.1", "udp", 68, 67, 0, payload)
    assert flows.hints(pkt) == {"dhcp": {"hostname": "reception-pc", "vendor": "MSFT 5.0"}}

def test_dhcp_boundary_too_short():
    # DHCP payload of exactly 240 bytes (header only, no options) returns {}
    payload = b"\x00" * 240
    pkt = flows.Packet(0, "a", "b", "192.168.1.20", "192.168.1.1", "udp", 68, 67, 0, payload)
    assert flows.hints(pkt) == {}

def test_mdns_query_extracts_names():
    # mDNS packet: 12-byte header with qdcount=1 at offset 4-6, then labels.
    # Header: 2B ID + 2B flags + 2B qdcount + 2B ancount + 2B nscount + 2B arcount
    # Labels: dot-separated parts, each prefixed with length byte, terminated by 0x00.
    # Then 4 bytes of type/class per question.
    payload = b"\x00\x00"  # ID
    payload += b"\x00\x00"  # Flags
    payload += b"\x00\x01"  # qdcount = 1
    payload += b"\x00\x00"  # ancount = 0
    payload += b"\x00\x00"  # nscount = 0
    payload += b"\x00\x00"  # arcount = 0
    # Add a single question: _ipp._tcp.local
    labels = b"\x04_ipp\x04_tcp\x05local\x00"
    payload += labels
    payload += b"\x00\x0c\x00\x01"  # Type PTR (0x000c), Class IN (0x0001)
    pkt = flows.Packet(0, "a", "b", "192.168.1.20", "192.168.1.1", "udp", 5353, 5353, 0, payload)
    assert flows.hints(pkt) == {"mdns": ["_ipp._tcp.local"]}

def test_mdns_boundary_too_short():
    # mDNS payload of exactly 12 bytes (header only, no questions) returns {}
    payload = b"\x00" * 12
    pkt = flows.Packet(0, "a", "b", "192.168.1.20", "192.168.1.1", "udp", 5353, 5353, 0, payload)
    assert flows.hints(pkt) == {}

def test_read_pcap_yields_one_packet_per_record_with_headers_decoded():
    pkts = list(flows.read_pcap(FIX))
    assert len(pkts) == 16
    p = pkts[0]
    assert (p.src_mac, p.dst_mac) == ("aa:bb:cc:00:00:ff", "aa:bb:cc:00:00:01")
    assert (p.src_ip, p.dst_ip, p.proto, p.src_port, p.dst_port) == ("142.250.1.1", "192.168.1.20", "tcp", 443, 50000)
    # `length` is the ON-THE-WIRE length, not the captured bytes: bytes
    # counts must reflect what crossed the network, and snaplen 128 caps
    # what we KEEP, not what we COUNT.
    assert p.length == 14 + 20 + 20 + 1400

def test_payload_never_exceeds_what_tcpdump_kept():
    for p in flows.read_pcap(FIX):
        assert len(p.payload) <= 128

def test_udp_dns_packet_decoded():
    """Fixture packet 13 (DNS) must decode UDP proto and ports correctly."""
    pkts = list(flows.read_pcap(FIX))
    dns = pkts[13]
    assert dns.proto == "udp"
    assert (dns.src_ip, dns.dst_ip) == ("192.168.1.20", "192.168.1.9")
    assert (dns.src_port, dns.dst_port) == (51000, 53)

def test_vlan_tagged_frame_decoded():
    """Fixture packet 14 (VLAN-tagged) decodes payload with correct IPs/ports."""
    pkts = list(flows.read_pcap(FIX))
    vlan = pkts[14]
    assert vlan.proto == "udp"
    assert (vlan.src_ip, vlan.dst_ip) == ("10.0.1.5", "192.168.1.20")
    assert (vlan.src_port, vlan.dst_port) == (5353, 5353)

def test_ipv6_tcp_frame_decoded():
    """Fixture packet 15 (IPv6) decodes proto and colon-hex addresses."""
    pkts = list(flows.read_pcap(FIX))
    ipv6 = pkts[15]
    assert ipv6.proto == "tcp"
    # IPv6 addresses should be in colon-separated hex format
    assert ":" in ipv6.src_ip and ":" in ipv6.dst_ip
    assert ipv6.src_port == 443
    assert ipv6.dst_port == 50001

def test_big_endian_pcap_yields_same_packets():
    """Big-endian pcap (0xD4C3B2A1 magic) decodes identically to little-endian."""
    # Read little-endian fixture
    le_pkts = list(flows.read_pcap(FIX))

    # Create big-endian version in temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pcap") as f:
        be_path = f.name
        # Write big-endian pcap header: pack with ">" byte order
        f.write(struct.pack(">IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 128, 1))

        # Read fixture and convert records to big-endian
        with open(FIX, "rb") as src:
            src.read(24)  # skip header
            for _ in range(len(le_pkts)):
                rh = src.read(16)
                if len(rh) < 16:
                    break
                sec, frac, caplen, wirelen = struct.unpack("<IIII", rh)
                data = src.read(caplen)
                if len(data) < caplen:
                    break
                # Write in big-endian
                f.write(struct.pack(">IIII", sec, frac, caplen, wirelen))
                f.write(data)

    try:
        # Decode big-endian and compare field-by-field
        be_pkts = list(flows.read_pcap(be_path))
        assert len(be_pkts) == len(le_pkts)
        for le, be in zip(le_pkts, be_pkts):
            assert le.ts == be.ts
            assert (le.src_mac, le.dst_mac, le.src_ip, le.dst_ip, le.proto, le.src_port, le.dst_port, le.length) == \
                   (be.src_mac, be.dst_mac, be.src_ip, be.dst_ip, be.proto, be.src_port, be.dst_port, be.length)
    finally:
        os.unlink(be_path)

def test_truncated_pcap_file_stops_gracefully():
    """A pcap file truncated mid-record yields all complete packets, no exception."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pcap") as f:
        trunc_path = f.name
        # Read a few records, truncate mid-way through the second one
        with open(FIX, "rb") as src:
            # Copy header + first 2 complete records, then cut off mid-third
            header = src.read(24)
            f.write(header)

            # Copy 1st record (header 16 + data up to 128)
            rh1 = src.read(16)
            caplen1 = struct.unpack("<I", rh1[8:12])[0]
            f.write(rh1)
            f.write(src.read(caplen1))

            # Copy 2nd record
            rh2 = src.read(16)
            caplen2 = struct.unpack("<I", rh2[8:12])[0]
            f.write(rh2)
            f.write(src.read(caplen2))

            # Start 3rd record header but truncate mid-way
            rh3 = src.read(16)
            f.write(rh3[:10])  # Only write 10 bytes of the 16-byte header

    try:
        pkts = list(flows.read_pcap(trunc_path))
        # Should have 2 complete packets, no exception
        assert len(pkts) == 2
    finally:
        os.unlink(trunc_path)

def test_nanosecond_pcaps_decode_in_both_byte_orders():
    """Nanosecond pcaps with both byte orders decode correctly."""
    sec = 1758326400
    frac_ns = 500_000_000  # 0.5 seconds in nanoseconds
    
    for byte_order, byte_order_str in [("<", "LE"), (">", "BE")]:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pcap") as f:
            temp_path = f.name
            # Both LE and BE files store the SAME nanosecond magic VALUE (0xA1B23C4D),
            # just in different byte orders. This gives different magic values when
            # read with <I: LE gives 0xA1B23C4D, BE gives 0x4D3CB2A1.
            f.write(struct.pack(byte_order + "IHHiIII", 0xA1B23C4D, 2, 4, 0, 0, 128, 1))
            # Write one simple record with nanosecond fraction
            payload = b"test"
            eth_len = 14 + 20 + 8 + len(payload)  # eth + IPv4 + UDP + payload
            f.write(struct.pack(byte_order + "IIII", sec, frac_ns, min(eth_len, 128), eth_len))
            # Write a minimal Ethernet/IPv4/UDP frame
            f.write(b"\xaa\xbb\xcc\x00\x00\x01" + b"\xaa\xbb\xcc\x00\x00\xff" + b"\x08\x00")  # eth
            f.write(struct.pack("!BBHHHBBH4s4s", 0x45, 0, 28, 1, 0, 64, 17, 0,
                                bytes([192, 168, 1, 20]), bytes([8, 8, 8, 8])))
            f.write(struct.pack("!HHHH", 5353, 53, 12, 0) + payload)  # UDP
        
        try:
            pkts = list(flows.read_pcap(temp_path))
            assert len(pkts) == 1, f"{byte_order_str} nanosecond pcap should have 1 packet"
            p = pkts[0]
            # Verify nanosecond precision: ts should be sec + 0.5
            expected_ts = sec + (frac_ns / 1e9)
            assert abs(p.ts - expected_ts) < 1e-6, f"{byte_order_str} nanosecond ts {p.ts} != expected {expected_ts}"
        finally:
            os.unlink(temp_path)


# --- the sensor's own probes, and ephemeral client ports -------------------
#
# Measured on the home network 2026-10-06: 94% of one minute's 825 rows were
# under 2 KB, and 407 distinct "peer ports" appeared in that minute. They were
# the sensor's own port sweep (SYN, SYN-ACK/RST, nothing carried) and each
# reply keyed on the prober's random source port, so every probe was a row.

SENSOR, PRINTER, LAPTOP = "192.168.1.170", "192.168.1.202", "192.168.1.30"

def _p(t, src, dst, sport, dport, payload=b"", proto="tcp"):
    return flows.Packet(1758326400.0 + t, "aa:00:00:00:00:" + src[-2:], "aa:00:00:00:00:" + dst[-2:],
                        src, dst, proto, sport, dport, 60 + len(payload), payload)

def test_the_sensors_own_empty_probes_are_not_traffic():
    pkts = []
    for i, port in enumerate((9100, 5900, 8443, 62078)):
        pkts.append(_p(i, SENSOR, PRINTER, 57000 + i, port))      # SYN
        pkts.append(_p(i, PRINTER, SENSOR, port, 57000 + i))      # SYN-ACK / RST
    assert flows.rollup(pkts, ["192.168.1"], own_ips={SENSOR}) == []

def test_someone_elses_sweep_still_shows_one_row_per_service():
    pkts = []
    for i in range(30):                                       # 30 probes of one port
        pkts.append(_p(i, LAPTOP, PRINTER, 50000 + i, 9100))
        pkts.append(_p(i, PRINTER, LAPTOP, 9100, 50000 + i))
    rows = flows.rollup(pkts, ["192.168.1"], own_ips={SENSOR})
    printer = [r for r in rows if r["ip"] == PRINTER]
    assert len(printer) == 1 and printer[0]["peer_port"] == 9100
    assert printer[0]["pkts_in"] == 30 and printer[0]["pkts_out"] == 30

def test_the_sensor_hosts_real_lan_conversation_is_kept():
    pkts = [_p(0, SENSOR, PRINTER, 57001, 445, b"x" * 200), _p(0, PRINTER, SENSOR, 445, 57001, b"y" * 900)]
    rows = flows.rollup(pkts, ["192.168.1"], own_ips={SENSOR})
    assert {(r["ip"], r["peer_port"]) for r in rows} == {(SENSOR, 445), (PRINTER, 445)}
    assert all("payload" not in r for r in rows)

def test_ethernet_padding_is_not_payload():
    # A bare RST is 54 bytes on the wire and arrives padded to Ethernet's
    # 60-byte minimum. Reading those six zero bytes as payload made every
    # probe reply look like data, so the sweep above was never dropped.
    eth = bytes(6) + bytes.fromhex("aa0000000002") + b"\x08\x00"
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 40, 0, 0, 64, 6, 0,
                     bytes([192, 168, 1, 202]), bytes([192, 168, 1, 170]))
    tcp = struct.pack("!HHIIBBHHH", 9100, 57001, 0, 0, 5 << 4, 0x14, 0, 0, 0)
    pkt = flows._decode(0.0, eth + ip + tcp + bytes(6), 60)
    assert pkt is not None and pkt.payload == b"" and pkt.dst_port == 57001

def test_an_offloaded_packet_with_no_ip_length_keeps_its_payload():
    eth = bytes(6) + bytes.fromhex("aa0000000002") + b"\x08\x00"
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 0, 0, 0, 64, 6, 0,
                     bytes([192, 168, 1, 170]), bytes([93, 184, 216, 34]))
    tcp = struct.pack("!HHIIBBHHH", 57001, 443, 0, 0, 5 << 4, 0x18, 0, 0, 0)
    pkt = flows._decode(0.0, eth + ip + tcp + b"\x16\x03\x01hello", 200)
    assert pkt.payload == b"\x16\x03\x01hello"
