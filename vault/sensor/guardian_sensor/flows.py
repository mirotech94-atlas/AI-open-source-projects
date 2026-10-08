"""Flows: packets rolled into who-talked-to-whom rows. Spec §1-2.

Reads the one-minute pcaps `capture.py` has tcpdump write, decodes Ethernet →
IPv4/IPv6 → TCP/UDP/ICMP HEADERS, and rolls packets into one row per
(minute, device mac, peer ip, peer port, proto). Stdlib only: the sensor
ships with `dependencies = []` and this must not be the module that changes
that. It never reads packet bodies -- tcpdump's snaplen already truncates
them and this parser additionally stops at the header end it computed, plus
the three named prefixes `hints()` lifts (TLS SNI, DHCP 12/60, mDNS names).
"""
from __future__ import annotations

import struct
import datetime as dt
from typing import Iterable, Iterator, NamedTuple

class Packet(NamedTuple):
    ts: float
    src_mac: str
    dst_mac: str
    src_ip: str
    dst_ip: str
    proto: str        # tcp|udp|icmp|other
    src_port: int
    dst_port: int
    length: int       # on-the-wire length
    payload: bytes    # what was captured past the transport header, <= snaplen

_PROTO = {6: "tcp", 17: "udp", 1: "icmp", 58: "icmp"}

def _mac(b: bytes) -> str:
    return ":".join(f"{x:02x}" for x in b)

def read_pcap(path: str) -> Iterator[Packet]:
    """Yield decoded packets from a classic (non-pcapng) pcap. Malformed
    records end the iteration rather than raising: a file tcpdump was still
    writing when we opened it is an ordinary state, not an error."""
    with open(path, "rb") as f:
        gh = f.read(24)
        if len(gh) < 24:
            return
        magic = struct.unpack("<I", gh[:4])[0]
        # Pcap magic detection: both LE and BE files store the magic value
        # 0xA1B2C3D4, but in different byte orders. A little-endian writer puts
        # d4c3b2a1 on disk (struct.pack("<I", 0xA1B2C3D4)), which unpacking with
        # "<I" returns as 0xA1B2C3D4. A big-endian writer puts a1b2c3d4 on disk
        # (struct.pack(">I", 0xA1B2C3D4)), which unpacking with "<I" returns as
        # 0xD4C3B2A1. Nanosecond variants are similar: 0xA1B23C4D (LE on-disk:
        # 4d3cb2a1) and 0x4D3CB2A1 (BE on-disk: a1b23c4d).
        if magic == 0xA1B2C3D4:
            end, nano = "<", False
        elif magic == 0xD4C3B2A1:
            end, nano = ">", False
        elif magic == 0xA1B23C4D:
            end, nano = "<", True
        elif magic == 0x4D3CB2A1:
            end, nano = ">", True
        else:
            return
        while True:
            rh = f.read(16)
            if len(rh) < 16:
                return
            sec, frac, caplen, wirelen = struct.unpack(end + "IIII", rh)
            data = f.read(caplen)
            if len(data) < caplen:
                return
            ts = sec + frac / (1e9 if nano else 1e6)
            pkt = _decode(ts, data, wirelen)
            if pkt is not None:
                yield pkt

def _decode(ts: float, data: bytes, wirelen: int) -> Packet | None:
    if len(data) < 14:
        return None
    dst_mac, src_mac = _mac(data[0:6]), _mac(data[6:12])
    etype = struct.unpack("!H", data[12:14])[0]
    off = 14
    if etype == 0x8100 and len(data) >= 18:          # 802.1Q tag
        etype = struct.unpack("!H", data[16:18])[0]
        off = 18
    if etype == 0x0800:
        if len(data) < off + 20:
            return None
        ihl = (data[off] & 0x0F) * 4
        proto_n = data[off + 9]
        src_ip = ".".join(str(x) for x in data[off + 12:off + 16])
        dst_ip = ".".join(str(x) for x in data[off + 16:off + 20])
        l4 = off + ihl
        # Where the IP packet ENDS, from its own header: anything captured
        # past it is Ethernet padding (a 54-byte RST arrives as 60), and
        # counting padding as payload made empty probes look like data.
        # A total length below the header itself is not a real length: with
        # segmentation offload an outgoing packet is captured before the NIC
        # fills it in, and reads 0. Trust the capture then, or the TLS name
        # in that packet would be lost.
        ip_end = off + struct.unpack("!H", data[off + 2:off + 4])[0]
        if ip_end < l4:
            ip_end = len(data)
    elif etype == 0x86DD:
        if len(data) < off + 40:
            return None
        proto_n = data[off + 6]
        src_ip = _ip6(data[off + 8:off + 24])
        dst_ip = _ip6(data[off + 24:off + 40])
        l4 = off + 40
        ip_end = l4 + struct.unpack("!H", data[off + 4:off + 6])[0]
        if ip_end == l4:                     # jumbogram / offload: length 0
            ip_end = len(data)
    else:
        return None
    proto = _PROTO.get(proto_n, "other")
    sport = dport = 0
    payload = b""
    if proto == "tcp" and len(data) >= l4 + 20:
        sport, dport = struct.unpack("!HH", data[l4:l4 + 4])
        doff = (data[l4 + 12] >> 4) * 4
        payload = data[l4 + doff:ip_end]
    elif proto == "udp" and len(data) >= l4 + 8:
        sport, dport = struct.unpack("!HH", data[l4:l4 + 4])
        payload = data[l4 + 8:ip_end]
    return Packet(ts, src_mac, dst_mac, src_ip, dst_ip, proto, sport, dport, wirelen, payload)

def _ip6(b: bytes) -> str:
    return ":".join(f"{struct.unpack('!H', b[i:i+2])[0]:x}" for i in range(0, 16, 2))

FLOW_CAP = 5000

def _is_local(ip: str, prefixes: list[str]) -> bool:
    return any(ip.startswith(p + ".") for p in prefixes) or ip.startswith("fe80:") or ip.startswith("fd")

def _minute(ts: float) -> str:
    # Sensor-local offset, like every other sensor-authored `ts`; the hub
    # treats these as strings and re-parses when order matters (CLAUDE.md,
    # history.py entry).
    return dt.datetime.fromtimestamp(ts).astimezone().replace(second=0, microsecond=0).isoformat()

# The low edge of every OS's ephemeral range (Linux 32768, macOS/Windows
# 49152). A port at or above it on one end, with a port below it on the other,
# is a client's throwaway source port talking to a service.
EPHEMERAL_FROM = 32768

def _service_port(local: int, remote: int) -> int:
    """The port a row is keyed on, from one device's side.

    Normally the REMOTE port: a laptop's row for its HTTPS call is keyed 443.
    But when the device is the one being called, the remote port is the
    caller's random source port, and keying on it makes every connection a
    new row -- a printer probed 30 times showed 30 "peers". So when the remote
    end is ephemeral and the local end is not, key on the local port, the
    service that was actually reached."""
    if remote >= EPHEMERAL_FROM and 0 < local < EPHEMERAL_FROM:
        return local
    return remote

def rollup(pkts: Iterable[Packet], local_prefixes: list[str],
           own_ips: Iterable[str] = ()) -> list[dict]:
    """One row per (minute, device mac, peer ip, service port, proto), keyed on
    the DEVICE side. A LAN-to-LAN packet is two devices talking, so it is
    credited to both -- once as each one's own flow -- because "what is the
    x-ray PC talking to" needs the PC's view and the resolver's view both.

    `own_ips` are the sensor's own addresses. A TCP exchange between the
    sensor and another LAN device that carried no payload in either direction
    is the sensor's own port sweep (SYN, then SYN-ACK or RST), not traffic,
    and is dropped. Measured 2026-10-06 that sweep was 94% of a home network's
    rows and two gigabytes of the hub's database. Only the SENSOR's empty
    probes go: a sweep run by any other device stays visible, because someone
    else scanning the LAN is exactly what an owner should be able to see."""
    own = set(own_ips)
    acc: dict[tuple, dict] = {}
    for p in pkts:
        sides = []
        if _is_local(p.src_ip, local_prefixes):
            sides.append((p.src_mac, p.src_ip, p.dst_ip, _service_port(p.src_port, p.dst_port), "out"))
        if _is_local(p.dst_ip, local_prefixes):
            sides.append((p.dst_mac, p.dst_ip, p.src_ip, _service_port(p.dst_port, p.src_port), "in"))
        for mac, ip, peer_ip, peer_port, direction in sides:
            key = (_minute(p.ts), mac, peer_ip, peer_port, p.proto)
            row = acc.get(key)
            if row is None:
                row = acc[key] = {"ts": key[0], "mac": mac, "ip": ip, "peer_ip": peer_ip,
                                  "peer_port": peer_port, "proto": p.proto,
                                  "bytes_in": 0, "bytes_out": 0, "pkts_in": 0, "pkts_out": 0,
                                  "hints": {}, "source": "mirror", "payload": False}
            row["bytes_" + direction] += p.length
            row["pkts_" + direction] += 1
            if p.payload:
                row["payload"] = True
            if direction == "out":
                for k, v in hints(p).items():
                    _merge_hint(row["hints"], k, v)
    out = []
    for row in acc.values():
        carried = row.pop("payload")
        if (own and not carried and row["proto"] == "tcp"
                and (row["ip"] in own or row["peer_ip"] in own)
                and _is_local(row["ip"], local_prefixes) and _is_local(row["peer_ip"], local_prefixes)):
            continue
        out.append(row)
    return out

def _merge_hint(h: dict, k: str, v) -> None:
    if k == "mdns":
        h.setdefault("mdns", [])
        for n in v:
            if n not in h["mdns"] and len(h["mdns"]) < 5:
                h["mdns"].append(n)
    else:
        h.setdefault(k, v)

def cap(rows: list[dict], limit: int = FLOW_CAP) -> tuple[list[dict], int]:
    """Keep the `limit` biggest rows by bytes; report how many were dropped.
    A saturated network must say "I saw more than I could send", never
    "quiet" -- the caller surfaces the count as `flows_dropped`."""
    if len(rows) <= limit:
        return rows, 0
    rows = sorted(rows, key=lambda r: -(r["bytes_in"] + r["bytes_out"]))
    return rows[:limit], len(rows) - limit

def hints(p: Packet) -> dict:
    """The three payload prefixes fingerprinting (phase 3) needs, and nothing
    else. Each lifter returns {} unless the bytes are exactly that protocol's
    opening -- an HTTP request, a password, a file name all return {}."""
    if p.proto == "tcp" and p.dst_port == 443 and len(p.payload) > 43 and p.payload[0] == 0x16 and p.payload[5] == 0x01:
        sni = _sni(p.payload)
        return {"sni": sni} if sni else {}
    if p.proto == "udp" and p.dst_port == 67 and len(p.payload) > 240:
        return _dhcp(p.payload)
    if p.proto == "udp" and p.dst_port == 5353 and len(p.payload) > 12:
        names = _mdns(p.payload)
        return {"mdns": names} if names else {}
    return {}

def _sni(rec: bytes) -> str:
    try:
        i = 5 + 4 + 2 + 32                     # record hdr, handshake hdr, version, random
        i += 1 + rec[i]                        # session id
        i += 2 + struct.unpack("!H", rec[i:i+2])[0]   # cipher suites
        i += 1 + rec[i]                        # compression
        ext_len = struct.unpack("!H", rec[i:i+2])[0]; i += 2
        end = i + ext_len
        while i + 4 <= end:
            et, el = struct.unpack("!HH", rec[i:i+4]); i += 4
            if et == 0:
                n = struct.unpack("!H", rec[i+3:i+5])[0]
                return rec[i+5:i+5+n].decode("ascii", "ignore")
            i += el
    except (struct.error, IndexError):
        pass
    return ""

def _dhcp(b: bytes) -> dict:
    out = {}
    i = 240
    while i < len(b):
        opt = b[i]
        # Option 0 is Pad (RFC 2132): a bare 0x00 byte with no length field.
        if opt == 0:
            i += 1
            continue
        if opt == 255:
            break
        if i + 1 >= len(b):
            break
        ln = b[i+1]
        val = b[i+2:i+2+ln]
        if opt == 12:
            out["hostname"] = val.decode("ascii", "ignore")
        elif opt == 60:
            out["vendor"] = val.decode("ascii", "ignore")
        i += 2 + ln
    return {"dhcp": out} if out else {}

def _mdns(b: bytes) -> list[str]:
    names, i, qd = [], 12, struct.unpack("!H", b[4:6])[0]
    for _ in range(min(qd, 5)):
        parts = []
        while i < len(b) and b[i] not in (0,) and b[i] < 0xC0:
            ln = b[i]; parts.append(b[i+1:i+1+ln].decode("ascii", "ignore")); i += 1 + ln
        i += 1 + 4
        if parts:
            names.append(".".join(parts))
    return names
