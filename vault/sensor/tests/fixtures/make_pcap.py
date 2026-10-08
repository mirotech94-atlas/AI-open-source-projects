"""Writes a small synthetic pcap. Run once; the output is checked in.
Ethernet/IPv4/{TCP,UDP}, IPv6/TCP, 802.1Q VLAN; 128-byte snaplen like production."""
import struct, sys, socket

def _mac(s): return bytes.fromhex(s.replace(":", ""))
def _ip(s): return bytes(int(x) for x in s.split("."))
def _ip6(s): return socket.inet_pton(socket.AF_INET6, s)

def ipv4(src, dst, proto, payload):
    hdr = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(payload), 1, 0, 64, proto, 0, _ip(src), _ip(dst))
    return hdr + payload

def ipv6(src, dst, proto, payload):
    # IPv6 header: version/traffic/flow (4B) + payload_len (2B) + next_hdr (1B) + hop_limit (1B) + src (16B) + dst (16B)
    hdr = struct.pack("!I", 0x60000000)  # version 6, traffic class 0, flow label 0
    hdr += struct.pack("!H", len(payload) + 20)  # payload length
    hdr += struct.pack("!BB", proto, 64)  # next header (protocol), hop limit
    hdr += _ip6(src) + _ip6(dst)
    return hdr + payload

def tcp(sport, dport, data=b""):
    return struct.pack("!HHIIBBHHH", sport, dport, 1, 1, 0x50, 0x18, 65535, 0, 0) + data

def udp(sport, dport, data=b""):
    return struct.pack("!HHHH", sport, dport, 8 + len(data), 0) + data

def frame(src_mac, dst_mac, ip_bytes):
    return _mac(dst_mac) + _mac(src_mac) + b"\x08\x00" + ip_bytes

def frame_vlan(src_mac, dst_mac, vlan_id, ip_bytes):
    # 802.1Q tag: 0x8100 (tag protocol) + 2-byte TPID (vlan_id in top 12 bits)
    return _mac(dst_mac) + _mac(src_mac) + b"\x81\x00" + struct.pack("!H", vlan_id) + b"\x08\x00" + ip_bytes

def frame_ipv6(src_mac, dst_mac, ip_bytes):
    return _mac(dst_mac) + _mac(src_mac) + b"\x86\xdd" + ip_bytes

PC, GW = "aa:bb:cc:00:00:01", "aa:bb:cc:00:00:ff"
records = []
t = 1758326400.0
# 10 x 1400-byte "downloads" from 142.250.1.1:443 -> PC, 3 x small uploads back
for i in range(10):
    records.append((t + i * 0.1, frame(GW, PC, ipv4("142.250.1.1", "192.168.1.20", 6, tcp(443, 50000, b"x" * 1400)))))
for i in range(3):
    records.append((t + 1 + i * 0.1, frame(PC, GW, ipv4("192.168.1.20", "142.250.1.1", 6, tcp(50000, 443, b"y" * 60)))))
# one DNS query PC -> 192.168.1.9:53
records.append((t + 2, frame(PC, GW, ipv4("192.168.1.20", "192.168.1.9", 17, udp(51000, 53, b"\x00" * 30)))))
# one VLAN-tagged IPv4/UDP frame
records.append((t + 3, frame_vlan(GW, PC, 100, ipv4("10.0.1.5", "192.168.1.20", 17, udp(5353, 5353, b"\x00" * 20)))))
# one IPv6/TCP frame
records.append((t + 4, frame_ipv6(GW, PC, ipv6("2001:0db8:85a3::8a2e", "2001:0db8:85a3::1", 6, tcp(443, 50001, b"z" * 30)))))

def write(path):
    with open(path, "wb") as f:
        f.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 128, 1))
        for ts, data in records:
            cap = data[:128]
            f.write(struct.pack("<IIII", int(ts), int((ts % 1) * 1e6), len(cap), len(data)))
            f.write(cap)

if __name__ == "__main__":
    write(sys.argv[1] if len(sys.argv) > 1 else "sensor/tests/fixtures/flows_sample.pcap")
