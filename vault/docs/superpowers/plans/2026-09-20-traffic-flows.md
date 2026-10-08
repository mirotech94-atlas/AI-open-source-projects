# Traffic Flows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Guardian's sensor rolls mirrored packets into per-device flows; the hub stores, retains and shapes them; the customer and the bot can ask "who is this device talking to, and how much" and always be told which mode the answer rests on.

**Architecture:** `tcpdump` (system package) writes one-minute header-only pcaps on the sensor; a stdlib parser (`flows.py`) rolls them into flow rows that ride the existing 5-minute payload beside `dns_queries`; `ingest.store_scan` pops them out before the `m.Scan` insert into their own `flow` table; `payloads/traffic.py` shapes them for two read routes, three chat tools, a Topology section and the device drawer. Retention copies the DNS raw/rollup pattern exactly.

**Tech Stack:** Python 3.10 stdlib on the sensor (no new Python deps; `tcpdump` via `install.py`), FastAPI + SQLAlchemy + Alembic on the hub, React + TanStack Query + vitest on the web.

**Spec:** `docs/superpowers/specs/2026-09-20-traffic-flows-design.md`

## Global Constraints

- Sensor `pyproject.toml` keeps `dependencies = []`. tcpdump is a system package, installed by `install.py`, checked with `shutil.which`.
- `Scan.raw` must never contain `flows`: pop above the `m.Scan` insert in `ingest.store_scan`, beside the heartbeat-token pop.
- Every `schemas_dash.py` model lands **in the same commit** as its `web/src/types.ts` mirror (`export type <Name> = { ... }`, same field names) or `tests/test_contracts.py` fails.
- Alembic revision is **`0029_flow`**, `down_revision = "0028_partner"`. Keep `tests/test_alembic_single_head.py` green.
- Alert keys `capture:unavailable` and `capture:silent` are human-only: entries in `remedy.HUMAN_REMEDIES`, **not** in `FIX_ALERT_KEY` on either side.
- `ts` columns are sensor-offset ISO strings: SQL filters use `payloads/_util._lax_cutoff`, then re-check in Python with `scans.parse_ts`.
- No wall-clock assertions. Work that scales with rows is guarded by `tests/querycount.py` with **equal** counts at two sizes.
- `label` and `trusted` are never written by anything here.
- Every response carries `mode: "mirror" | "router" | "none"`; a `router`/`none` chat answer opens with the limitation sentence.
- Commit subjects: a present-tense sentence about the user-visible change. Stage by explicit path (peers share this checkout). Run hub tests from `hub/` with `.venv/bin/pytest`, sensor tests from `sensor/` with `.venv/bin/pytest`, web with `cd web && npm test`.

## File structure

Sensor (`sensor/guardian_sensor/`):
- Create `flows.py` — pcap reader + flow rollup + hints. Pure functions; no I/O except `read_pcap(path)`.
- Create `capture.py` — tcpdump lifecycle (start/stop/restart, file cap, `capture.error`).
- Modify `config.py` — `DEFAULTS["capture"]`.
- Modify `install.py` — install tcpdump on Linux.
- Modify `runner.py:751 run_once` — call `capture.tick` and `flows.collect`; write `flows-YYYY-MM-DD.jsonl`.
- Modify `sync.py:push` — chunk/offset `flows` like `dns_queries`.
- Modify `alerts.py` — two `capture:` keys.
- Modify `router_fios.py` — per-client bytes when the router exposes them.
- Tests `sensor/tests/test_flows.py`, `test_capture.py`, `tests/fixtures/make_pcap.py`.

Hub (`hub/guardian_hub/`):
- Modify `models.py` — `Flow`, `FlowRollup`, `Network.traffic_mode`.
- Create `alembic/versions/0029_flow.py`.
- Modify `ingest.py` — pop + store flows.
- Modify `retention.py` — `flow_raw_days`, `flow_rollup_days`, `rollup_flows`, prune.
- Create `payloads/traffic.py`.
- Modify `schemas_dash.py`, `routers/read.py`, `routers/networks.py`, `remedy.py`, `chat/tools.py`, `chat/surfaces.py`, `seed.py`, `demo_trickle.py`.
- Tests `hub/tests/test_flow_ingest.py`, `test_traffic_payload.py`, `test_flow_retention.py`, additions to `test_chat_coverage.py`, `test_remedy.py` unchanged but must pass.

Web (`web/src/`):
- Modify `types.ts`, `lib/api.ts`, `pages/Topology.tsx`, `components/DeviceDrawer.tsx`, `pages/Onboarding.tsx`.
- Create `components/TrafficSection.tsx`, `components/TrafficBlock.tsx`.
- Tests `web/tests/traffic.test.tsx`, additions to `onboarding.test.tsx`.

---

### Task 1: Sensor pcap reader

**Files:**
- Create: `sensor/guardian_sensor/flows.py`
- Create: `sensor/tests/fixtures/make_pcap.py`
- Test: `sensor/tests/test_flows.py`

**Interfaces:**
- Produces: `flows.read_pcap(path: str) -> Iterator[Packet]` where `Packet` is a `NamedTuple(ts: float, src_mac: str, dst_mac: str, src_ip: str, dst_ip: str, proto: str, src_port: int, dst_port: int, length: int, payload: bytes)`. `payload` is at most the bytes tcpdump kept (snaplen 128) minus headers, and is only used by Task 2's hint lifters.

- [ ] **Step 1: Write the fixture generator** — a script that writes a synthetic pcap (no real capture ever enters the repo).

```python
# sensor/tests/fixtures/make_pcap.py
"""Writes a small synthetic pcap. Run once; the output is checked in.
Ethernet/IPv4/{TCP,UDP} only, 128-byte snaplen like production."""
import struct, sys

def _mac(s): return bytes.fromhex(s.replace(":", ""))
def _ip(s): return bytes(int(x) for x in s.split("."))

def ipv4(src, dst, proto, payload):
    hdr = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(payload), 1, 0, 64, proto, 0, _ip(src), _ip(dst))
    return hdr + payload

def tcp(sport, dport, data=b""):
    return struct.pack("!HHIIBBHHH", sport, dport, 1, 1, 0x50, 0x18, 65535, 0, 0) + data

def udp(sport, dport, data=b""):
    return struct.pack("!HHHH", sport, dport, 8 + len(data), 0) + data

def frame(src_mac, dst_mac, ip_bytes):
    return _mac(dst_mac) + _mac(src_mac) + b"\x08\x00" + ip_bytes

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

def write(path):
    with open(path, "wb") as f:
        f.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 128, 1))
        for ts, data in records:
            cap = data[:128]
            f.write(struct.pack("<IIII", int(ts), int((ts % 1) * 1e6), len(cap), len(data)))
            f.write(cap)

if __name__ == "__main__":
    write(sys.argv[1] if len(sys.argv) > 1 else "sensor/tests/fixtures/flows_sample.pcap")
```

- [ ] **Step 2: Generate the fixture**

Run: `cd sensor && .venv/bin/python tests/fixtures/make_pcap.py tests/fixtures/flows_sample.pcap && ls -la tests/fixtures/flows_sample.pcap`
Expected: a file of a few KB.

- [ ] **Step 3: Write the failing test**

```python
# sensor/tests/test_flows.py
"""The flow parser reads only headers and the named prefixes. See spec §2."""
import os
from guardian_sensor import flows

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "flows_sample.pcap")

def test_read_pcap_yields_one_packet_per_record_with_headers_decoded():
    pkts = list(flows.read_pcap(FIX))
    assert len(pkts) == 14
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
```

- [ ] **Step 4: Run to verify it fails**

Run: `cd sensor && .venv/bin/pytest tests/test_flows.py -q`
Expected: FAIL — `ModuleNotFoundError: guardian_sensor.flows`.

- [ ] **Step 5: Implement the reader**

```python
# sensor/guardian_sensor/flows.py
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
from typing import Iterator, NamedTuple

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
        if magic == 0xA1B2C3D4:
            end, nano = "<", False
        elif magic == 0xD4C3B2A1:
            end, nano = ">", False
        elif magic == 0xA1B23C4D:
            end, nano = "<", True
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
    elif etype == 0x86DD:
        if len(data) < off + 40:
            return None
        proto_n = data[off + 6]
        src_ip = _ip6(data[off + 8:off + 24])
        dst_ip = _ip6(data[off + 24:off + 40])
        l4 = off + 40
    else:
        return None
    proto = _PROTO.get(proto_n, "other")
    sport = dport = 0
    payload = b""
    if proto == "tcp" and len(data) >= l4 + 20:
        sport, dport = struct.unpack("!HH", data[l4:l4 + 4])
        doff = (data[l4 + 12] >> 4) * 4
        payload = data[l4 + doff:]
    elif proto == "udp" and len(data) >= l4 + 8:
        sport, dport = struct.unpack("!HH", data[l4:l4 + 4])
        payload = data[l4 + 8:]
    return Packet(ts, src_mac, dst_mac, src_ip, dst_ip, proto, sport, dport, wirelen, payload)

def _ip6(b: bytes) -> str:
    return ":".join(f"{struct.unpack('!H', b[i:i+2])[0]:x}" for i in range(0, 16, 2))
```

- [ ] **Step 6: Run to verify it passes**

Run: `cd sensor && .venv/bin/pytest tests/test_flows.py -q`
Expected: `2 passed`.

- [ ] **Step 7: Commit**

```bash
git add sensor/guardian_sensor/flows.py sensor/tests/test_flows.py sensor/tests/fixtures/make_pcap.py sensor/tests/fixtures/flows_sample.pcap
git commit -m "The sensor can read a packet capture down to its headers, and no further"
```

---

### Task 2: Flow rollup, hints and the cap

**Files:**
- Modify: `sensor/guardian_sensor/flows.py`
- Test: `sensor/tests/test_flows.py`

**Interfaces:**
- Produces: `flows.rollup(pkts: Iterable[Packet], local_prefixes: list[str]) -> list[dict]` — rows shaped exactly as spec §2 (`ts, mac, ip, peer_ip, peer_port, proto, bytes_in, bytes_out, pkts_in, pkts_out, hints, source="mirror"`; `net` and `peer_name` are added later by `sync.py`/ingest). `flows.cap(rows, limit=FLOW_CAP) -> tuple[list[dict], int]` keeps the top rows by total bytes and returns the drop count. `FLOW_CAP = 5000`. `flows.hints(pkt) -> dict` lifts `{"sni": str}`, `{"dhcp": {"hostname":..., "vendor":...}}`, `{"mdns": [names]}` or `{}`.

- [ ] **Step 1: Write the failing tests**

```python
def test_rollup_keys_on_the_device_side_and_splits_direction():
    pkts = list(flows.read_pcap(FIX))
    rows = flows.rollup(pkts, local_prefixes=["192.168.1"])
    row = next(r for r in rows if r["peer_ip"] == "142.250.1.1")
    assert row["mac"] == "aa:bb:cc:00:00:01" and row["ip"] == "192.168.1.20"
    assert (row["peer_port"], row["proto"]) == (443, "tcp")
    assert row["pkts_in"] == 10 and row["pkts_out"] == 3
    assert row["bytes_in"] == 10 * (14 + 20 + 20 + 1400) and row["bytes_out"] == 3 * (14 + 20 + 20 + 60)
    assert row["source"] == "mirror" and row["ts"].endswith(":00")   # minute floor

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
    from tests.fixtures.make_pcap import tcp
    name = b"printer.example"
    ext = b"\x00\x00" + struct.pack("!H", len(name) + 5) + struct.pack("!H", len(name) + 3) + b"\x00" + struct.pack("!H", len(name)) + name
    body = b"\x03\x03" + b"\x00" * 32 + b"\x00" + b"\x00\x02\x13\x01" + b"\x01\x00" + struct.pack("!H", len(ext)) + ext
    hello = b"\x01" + struct.pack("!I", len(body))[1:] + body
    rec = b"\x16\x03\x01" + struct.pack("!H", len(hello)) + hello
    pkt = flows.Packet(0, "a", "b", "192.168.1.20", "1.1.1.1", "tcp", 50000, 443, 0, rec)
    assert flows.hints(pkt) == {"sni": "printer.example"}
    assert flows.hints(pkt._replace(payload=b"GET /secret HTTP/1.1\r\nHost: x")) == {}
```

(add `import struct` at the top of the test file.)

- [ ] **Step 2: Run to verify they fail**

Run: `cd sensor && .venv/bin/pytest tests/test_flows.py -q`
Expected: 4 FAIL with `AttributeError: module has no attribute 'rollup'` etc.

- [ ] **Step 3: Implement rollup, cap and hints**

```python
import datetime as dt

FLOW_CAP = 5000

def _is_local(ip: str, prefixes: list[str]) -> bool:
    return any(ip.startswith(p + ".") for p in prefixes) or ip.startswith("fe80:") or ip.startswith("fd")

def _minute(ts: float) -> str:
    # Sensor-local offset, like every other sensor-authored `ts`; the hub
    # treats these as strings and re-parses when order matters (CLAUDE.md,
    # history.py entry).
    return dt.datetime.fromtimestamp(ts).astimezone().replace(second=0, microsecond=0).isoformat()

def rollup(pkts, local_prefixes: list[str]) -> list[dict]:
    """One row per (minute, device mac, peer ip, peer port, proto), keyed on
    the DEVICE side. A LAN-to-LAN packet is two devices talking, so it is
    credited to both -- once as each one's own flow -- because "what is the
    x-ray PC talking to" needs the PC's view and the resolver's view both."""
    acc: dict[tuple, dict] = {}
    for p in pkts:
        sides = []
        if _is_local(p.src_ip, local_prefixes):
            sides.append((p.src_mac, p.src_ip, p.dst_ip, p.dst_port, "out"))
        if _is_local(p.dst_ip, local_prefixes):
            sides.append((p.dst_mac, p.dst_ip, p.src_ip, p.src_port, "in"))
        for mac, ip, peer_ip, peer_port, direction in sides:
            key = (_minute(p.ts), mac, peer_ip, peer_port, p.proto)
            row = acc.get(key)
            if row is None:
                row = acc[key] = {"ts": key[0], "mac": mac, "ip": ip, "peer_ip": peer_ip,
                                  "peer_port": peer_port, "proto": p.proto,
                                  "bytes_in": 0, "bytes_out": 0, "pkts_in": 0, "pkts_out": 0,
                                  "hints": {}, "source": "mirror"}
            row["bytes_" + direction] += p.length
            row["pkts_" + direction] += 1
            if direction == "out":
                for k, v in hints(p).items():
                    _merge_hint(row["hints"], k, v)
    return list(acc.values())

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
    while i + 2 <= len(b):
        opt, ln = b[i], b[i+1]
        if opt == 255:
            break
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
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd sensor && .venv/bin/pytest tests/test_flows.py -q`
Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add sensor/guardian_sensor/flows.py sensor/tests/test_flows.py
git commit -m "Packets roll up into who-talked-to-whom rows, biggest first when there are too many"
```

---

### Task 3: tcpdump lifecycle and install

**Files:**
- Create: `sensor/guardian_sensor/capture.py`
- Modify: `sensor/guardian_sensor/config.py:36` (`DEFAULTS`)
- Modify: `sensor/guardian_sensor/install.py`
- Test: `sensor/tests/test_capture.py`

**Interfaces:**
- Consumes: `config.DEFAULTS`, `paths.p(name)`.
- Produces: `capture.tick(cfg, state) -> dict` returning `{"mode": "mirror"|"router"|"none", "error": str, "interface": str, "files": int}`; it starts/restarts tcpdump as needed and trims the file directory to `MAX_FILES = 10`. `capture.completed_files() -> list[str]` — minute files no longer being written (all but the newest). `capture.remove(path)`.

- [ ] **Step 1: Add config defaults**

In `config.py` `DEFAULTS`, add:
```python
    "capture": {
        # "" = no mirror port; flows fall back to router counters when the
        # router integration exposes them, else mode "none". Never guessed.
        "interface": "",
        "snaplen": 128,
    },
```

- [ ] **Step 2: Write the failing tests**

```python
# sensor/tests/test_capture.py
from guardian_sensor import capture

def test_no_interface_means_mode_none_without_touching_tcpdump(monkeypatch, tmp_path):
    monkeypatch.setattr(capture.paths, "p", lambda n: str(tmp_path / n))
    monkeypatch.setattr(capture.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not start")))
    out = capture.tick({"capture": {"interface": ""}}, {})
    assert out["mode"] == "none" and out["error"] == ""

def test_missing_binary_is_reported_not_raised(monkeypatch, tmp_path):
    monkeypatch.setattr(capture.paths, "p", lambda n: str(tmp_path / n))
    monkeypatch.setattr(capture.shutil, "which", lambda _: None)
    out = capture.tick({"capture": {"interface": "eth1"}}, {})
    assert out["mode"] == "none" and "tcpdump" in out["error"]

def test_file_cap_holds(monkeypatch, tmp_path):
    d = tmp_path / "cap"; d.mkdir()
    for i in range(14):
        (d / f"{1700000000 + i * 60}.pcap").write_bytes(b"")
    monkeypatch.setattr(capture.paths, "p", lambda n: str(tmp_path / n))
    capture._trim()
    assert len(list(d.iterdir())) == capture.MAX_FILES

def test_completed_files_excludes_the_newest(monkeypatch, tmp_path):
    d = tmp_path / "cap"; d.mkdir()
    for i in range(3):
        (d / f"{1700000000 + i * 60}.pcap").write_bytes(b"")
    monkeypatch.setattr(capture.paths, "p", lambda n: str(tmp_path / n))
    assert [p.split("/")[-1] for p in capture.completed_files()] == ["1700000000.pcap", "1700000060.pcap"]
```

- [ ] **Step 3: Run to verify they fail**

Run: `cd sensor && .venv/bin/pytest tests/test_capture.py -q` — Expected: `ModuleNotFoundError`.

- [ ] **Step 4: Implement**

```python
# sensor/guardian_sensor/capture.py
"""tcpdump's keeper. Spec §1, §5.

Nothing else in the sensor starts, stops or reads tcpdump. It runs with
`-s <snaplen>` (headers plus the first bytes of payload, never bodies) and
`-G 60 -w <DATA_DIR>/cap/%s.pcap` (one file per minute, epoch-named so the
newest sorts last). At most MAX_FILES are kept: the ring is what bounds disk
on a saturated network, and a minute already rolled into flows has no reason
to stay.

`error` is a sentence for the hub, not a stack trace: "tcpdump not
installed" and "no packets on eth1 for 10 minutes" become `capture:*`
alerts on the human tier -- a person installs a package or checks a switch;
no fix Guardian runs reaches either.
"""
from __future__ import annotations

import os, shutil, subprocess, time
from . import paths

MAX_FILES = 10
SILENT_AFTER_S = 600
_proc: subprocess.Popen | None = None
_started_at = 0.0
_backoff = 5.0

def _dir() -> str:
    d = paths.p("cap"); os.makedirs(d, mode=0o700, exist_ok=True); return d

def _files() -> list[str]:
    d = _dir()
    return sorted(os.path.join(d, f) for f in os.listdir(d) if f.endswith(".pcap"))

def completed_files() -> list[str]:
    """Every minute file but the newest: tcpdump is still writing that one."""
    return _files()[:-1]

def remove(path: str) -> None:
    try: os.remove(path)
    except OSError: pass

def _trim() -> None:
    for old in _files()[:-MAX_FILES]:
        remove(old)

def _start(iface: str, snaplen: int) -> None:
    global _proc, _started_at
    _proc = subprocess.Popen(
        ["tcpdump", "-i", iface, "-s", str(snaplen), "-n", "-U", "-G", "60",
         "-w", os.path.join(_dir(), "%s.pcap"), "not port 22"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _started_at = time.time()

def tick(cfg: dict, state: dict) -> dict:
    """Called once per scan. Ensures tcpdump is running when an interface is
    configured, restarts it with backoff if it died, trims the ring, and
    reports mode/error. Router mode is decided by the caller (runner) when
    there is no interface but the router integration has per-client bytes."""
    global _proc, _backoff
    cap = cfg.get("capture") or {}
    iface = (cap.get("interface") or "").strip()
    out = {"mode": "none", "error": "", "interface": iface, "files": 0}
    if not iface:
        return out
    if not shutil.which("tcpdump"):
        out["error"] = "tcpdump not installed on the sensor"
        return out
    if _proc is None or _proc.poll() is not None:
        if _proc is not None and time.time() - _started_at < _backoff:
            out["error"] = "tcpdump exited; retrying"
            return out
        try:
            _start(iface, int(cap.get("snaplen") or 128))
            _backoff = min(_backoff * 2, 120.0) if _proc is not None else 5.0
        except OSError as e:
            out["error"] = f"could not start tcpdump: {e}"
            return out
    _trim()
    files = _files()
    out["files"] = len(files)
    newest = os.path.getmtime(files[-1]) if files else _started_at
    if time.time() - newest > SILENT_AFTER_S and time.time() - _started_at > SILENT_AFTER_S:
        out["error"] = f"no packets on {iface} for {SILENT_AFTER_S // 60} minutes"
    out["mode"] = "mirror"
    return out
```

In `install.py`, inside the Linux branch of `systemd()` before writing units, add:
```python
    # tcpdump is the one system package the sensor needs beyond Python: flows
    # (spec 2026-09-20-traffic-flows) capture with it when a mirror port is
    # configured. Absent, capture reports mode "none" -- it never breaks install.
    if shutil.which("apt-get") and not shutil.which("tcpdump"):
        _run(["apt-get", "install", "-y", "tcpdump"])
```
(add `import shutil` at the top.)

- [ ] **Step 5: Run to verify they pass** — `cd sensor && .venv/bin/pytest tests/test_capture.py tests/test_config.py -q` → all pass.

- [ ] **Step 6: Commit**

```bash
git add sensor/guardian_sensor/capture.py sensor/guardian_sensor/config.py sensor/guardian_sensor/install.py sensor/tests/test_capture.py
git commit -m "A sensor with a mirror port keeps tcpdump running and says so when it cannot"
```

---

### Task 4: Sensor wiring — flows into the payload

**Files:**
- Modify: `sensor/guardian_sensor/runner.py:751` (`run_once`, near line 823 where `res["dns_filter"]` is set)
- Modify: `sensor/guardian_sensor/sync.py:26,180-215` (`DNS_MAX_PER_PUSH` block and `push`)
- Modify: `sensor/guardian_sensor/router_fios.py`
- Test: `sensor/tests/test_flows_sync.py`

**Interfaces:**
- Consumes: `capture.tick`, `capture.completed_files`, `capture.remove`, `flows.read_pcap`, `flows.rollup`, `flows.cap`, `sync._parse_lines`, `sync._attribute_dns`-style attribution.
- Produces: in `res` (the scan payload's `last`): `res["capture"] = {"mode", "error", "interface", "flows_dropped": int}`; a per-day file `<DATA_DIR>/flows/flows-YYYY-MM-DD.jsonl` of flow rows; in the push body: `body["flows"] = [rows]` each stamped with `net` via `_attribute_flows(rows, nets)` (matches on `ip`), and `body["capture"]` untouched (it is inside `last`). `FLOW_MAX_PER_PUSH = 5000`.

- [ ] **Step 1: Write the failing test**

```python
# sensor/tests/test_flows_sync.py
"""Flows leave the sensor the way dns_queries do: per-day jsonl, offset in
state["hub_sent"], advanced only after a successful push."""
import json, os, datetime as dt
from guardian_sensor import sync

def _client(monkeypatch, tmp_path, responses):
    monkeypatch.setattr(sync.paths, "p", lambda n: str(tmp_path / n))
    (tmp_path / "flows").mkdir(); (tmp_path / "dns").mkdir()
    sent = []
    def fake_try(self, method, path, body=None):
        sent.append(body)
        r = responses.pop(0)
        if isinstance(r, Exception): raise r
        return r
    monkeypatch.setattr(sync.HubClient, "_try", fake_try)
    return sync.HubClient("http://hub", "tok"), sent

def _write_flows(tmp_path, n):
    name = "flows-" + dt.date.today().isoformat() + ".jsonl"
    with open(tmp_path / "flows" / name, "w") as f:
        for i in range(n):
            f.write(json.dumps({"ts": "2026-09-20T10:00:00+00:00", "mac": f"aa:bb:cc:00:00:{i:02x}",
                                "ip": f"192.168.1.{i+10}", "peer_ip": "1.1.1.1", "peer_port": 443,
                                "proto": "tcp", "bytes_in": 1, "bytes_out": 1, "pkts_in": 1, "pkts_out": 1,
                                "hints": {}, "source": "mirror"}) + "\n")

def test_flows_ride_the_push_and_the_offset_advances_only_on_success(monkeypatch, tmp_path):
    _write_flows(tmp_path, 3)
    client, sent = _client(monkeypatch, tmp_path, [RuntimeError("down"), {"ok": True}])
    state = {}
    payload = {"version": "t", "last": {"lan": {}, "networks": []}, "state": state}
    assert client.push(payload) is None
    assert "flows" not in (state.get("hub_sent") or {})          # failed: nothing advanced
    assert client.push(payload)["ok"] and len(sent[1]["flows"]) == 3
    assert state["hub_sent"]["flows"]["off"] == 3                  # succeeded: advanced

def test_flows_are_attributed_to_a_network_by_ip(monkeypatch, tmp_path):
    _write_flows(tmp_path, 1)
    client, sent = _client(monkeypatch, tmp_path, [{"ok": True}])
    payload = {"version": "t", "state": {}, "last": {"lan": {},
               "networks": [{"slug": "main", "subnet_prefix": "192.168.1", "primary": True}]}}
    client.push(payload)
    assert sent[0]["flows"][0]["net"] == "main"
```

- [ ] **Step 2: Run to verify it fails** — `cd sensor && .venv/bin/pytest tests/test_flows_sync.py -q` → FAIL (`KeyError: 'flows'`).

- [ ] **Step 3: Implement in `sync.py`**

Below `DNS_MAX_PER_PUSH` add `FLOW_MAX_PER_PUSH = 5000`. In `push`, after the `pending_dns` line and before the `try`:
```python
        # Flows: same per-day file + offset dance as dns_queries, for the same
        # reason -- a retry after a failed post must add zero rows, and the
        # offset may only advance once the hub has actually stored them.
        fl_prev = sent.get("flows") if isinstance(sent.get("flows"), dict) else {}
        fl_name = "flows-" + dt.date.today().isoformat() + ".jsonl"
        fl_lines = _read_lines(os.path.join(paths.p("flows"), fl_name))
        fl_off = _int(fl_prev.get("off"), 0) if fl_prev.get("file") == fl_name else 0
        if len(fl_lines) < fl_off:
            fl_off = 0
        fl_chunk = fl_lines[fl_off:fl_off + FLOW_MAX_PER_PUSH]
        body["flows"] = _attribute_flows(_parse_lines(fl_chunk), nets)
        pending_flows = {"file": fl_name, "off": fl_off + len(fl_chunk)}
```
and after `sent["dns"] = pending_dns` add `sent["flows"] = pending_flows`.

The cap has to give way before the scan does (spec §5): in `push`, wrap the
`self._try("POST", "/ingest", body)` so that an `urllib.error.HTTPError` with
`code == 413` halves the flow cap for the next attempt and never drops the
scan to save the flows:
```python
        try:
            resp = self._try("POST", "/ingest", body)
        except urllib.error.HTTPError as e:
            if e.code == 413 and body.get("flows"):
                # The hub refused the size. Flows are the only part of the
                # payload that scales with the network, so they are the part
                # that yields: halve the cap (floor 500), retry this push with
                # the smaller chunk, and let the offset stay put so nothing is
                # skipped. The scan itself is never sacrificed for the flows.
                self._flow_cap = max(500, getattr(self, "_flow_cap", FLOW_MAX_PER_PUSH) // 2)
                log(f"ingest 413: flow cap now {self._flow_cap}")
                body["flows"] = body["flows"][: self._flow_cap]
                pending_flows = {"file": fl_name, "off": fl_off + len(body["flows"])}
                try:
                    resp = self._try("POST", "/ingest", body)
                except Exception as e2:
                    log(f"ingest failed: {str(e2)[:140]}"); return None
            else:
                log(f"ingest failed: {str(e)[:140]}"); return None
        except Exception as e:
            log(f"ingest failed: {str(e)[:140]}")
            return None
```
and use `getattr(self, "_flow_cap", FLOW_MAX_PER_PUSH)` in place of the bare
constant when slicing `fl_chunk`. Add a test to `test_flows_sync.py`: a
first `_try` raising `urllib.error.HTTPError(url, 413, "too big", {}, None)`
followed by `{"ok": True}` results in a second body with fewer flows and an
offset equal to the number actually sent.

Add beside `_attribute_dns`:
```python
def _attribute_flows(rows, nets):
    """`_attribute_dns` for flow rows: the device side is `ip`, not `client`."""
    if not nets:
        return rows
    prim = next((n["slug"] for n in nets if n.get("primary")), nets[0].get("slug"))
    pres = sorted(((n.get("subnet_prefix") or "", n.get("slug")) for n in nets if n.get("subnet_prefix")),
                  key=lambda x: -len(x[0]))
    for r in rows:
        if isinstance(r, dict) and not r.get("net"):
            ip = r.get("ip") or ""
            r["net"] = next((slug for pre, slug in pres if ip.startswith(pre + ".")), prim)
    return rows
```

- [ ] **Step 4: Implement in `runner.py`** — in `run_once`, right after `res["dns_filter"] = dnsstats.dns_analyse(cfg, state)`:
```python
        # Flows (spec 2026-09-20-traffic-flows): roll every COMPLETED minute
        # file into rows, append them to today's flows jsonl for sync.push to
        # chunk, then delete the pcap -- a minute already rolled up has no
        # reason to stay on disk. Router mode is chosen here, not in
        # capture.tick, because only the runner knows whether the router
        # integration produced per-client bytes this scan.
        from . import capture, flows
        # Roll up BEFORE tick(): tick() trims the ring to MAX_FILES, so any
        # completed minute must be read first or a slow scan could lose it.
        prefixes = [n.get("subnet_prefix") for n in (cfg.get("networks") or []) if n.get("subnet_prefix")] \
                   or [cfg.get("lan", {}).get("subnet_prefix", "192.168.1")]
        rows = []
        for path in capture.completed_files():
            rows.extend(flows.rollup(flows.read_pcap(path), prefixes))
            capture.remove(path)
        rows, dropped = flows.cap(rows)
        capinfo = capture.tick(cfg, state)
        if capinfo["mode"] == "mirror" or rows:
            _append_flows(rows)
        elif router_rows := (res.get("router") or {}).get("client_bytes"):
            capinfo["mode"] = "router"
            _append_flows([{"ts": res["ts"][:16] + ":00" + res["ts"][19:], "mac": r["mac"], "ip": r.get("ip", ""),
                            "peer_ip": "", "peer_port": 0, "proto": "other",
                            "bytes_in": int(r.get("rx") or 0), "bytes_out": int(r.get("tx") or 0),
                            "pkts_in": 0, "pkts_out": 0, "hints": {}, "source": "router"} for r in router_rows])
        res["capture"] = {**capinfo, "flows_dropped": dropped}
```
and a module-level helper:
```python
def _append_flows(rows):
    d = paths.p("flows"); os.makedirs(d, mode=0o700, exist_ok=True)
    with open(os.path.join(d, "flows-" + dt.date.today().isoformat() + ".jsonl"), "a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
```
In `router_fios.py` add `def client_bytes(base_url, password, **kw) -> list[dict]` returning `[{"mac","ip","rx","tx"}]` from the router's connected-devices page when it exposes byte counters, `[]` otherwise; wire its result into `res["router"]["client_bytes"]` where `res["router"]` is built. If the Fios firmware in use exposes no counters, return `[]` and leave a docstring saying so — mode falls to `none` honestly.

- [ ] **Step 5: Run** — `cd sensor && .venv/bin/pytest tests/test_flows_sync.py tests/test_collectors.py -q` → pass.

- [ ] **Step 6: Commit**

```bash
git add sensor/guardian_sensor/runner.py sensor/guardian_sensor/sync.py sensor/guardian_sensor/router_fios.py sensor/tests/test_flows_sync.py
git commit -m "Flows travel to the hub with every scan, and a failed post never sends them twice"
```

---

### Task 5: The two capture alerts, on the human tier, both sides

**Files:**
- Modify: `sensor/guardian_sensor/alerts.py` (near line 559 `sensor:uplink`)
- Modify: `hub/guardian_hub/remedy.py:285` (`HUMAN_REMEDIES`)
- Test: `sensor/tests/test_capture_alerts.py`; `hub/tests/test_remedy.py` must stay green.

- [ ] **Step 1: Failing sensor test**
```python
from guardian_sensor import alerts
def test_capture_error_becomes_a_human_tier_alert():
    res = {"capture": {"mode": "none", "error": "tcpdump not installed on the sensor", "interface": "eth1"}}
    keys = {a["key"]: a for a in alerts.evaluate_capture(res)}
    assert keys["capture:unavailable"]["sev"] == "INFO"
def test_silent_mirror_port_warns():
    res = {"capture": {"mode": "mirror", "error": "no packets on eth1 for 10 minutes", "interface": "eth1"}}
    keys = {a["key"]: a for a in alerts.evaluate_capture(res)}
    assert keys["capture:silent"]["sev"] == "WARN" and "switch" in keys["capture:silent"]["msg"]
def test_no_interface_raises_nothing():
    assert alerts.evaluate_capture({"capture": {"mode": "none", "error": "", "interface": ""}}) == []
```
- [ ] **Step 2: Run to fail** — `AttributeError: evaluate_capture`.
- [ ] **Step 3: Implement** in `alerts.py`:
```python
def evaluate_capture(res):
    """Capture problems are things a PERSON fixes -- install a package, check
    a switch -- so both keys live in remedy.HUMAN_REMEDIES, not FIX_ALERT_KEY."""
    cap = res.get("capture") or {}
    out = []
    if not cap.get("interface"):
        return out
    err = cap.get("error") or ""
    if "not installed" in err or "could not start" in err:
        out.append({"sev": "INFO", "key": "capture:unavailable", "msg": f"Traffic capture is configured on {cap['interface']} but cannot run: {err}"})
    elif err.startswith("no packets"):
        out.append({"sev": "WARN", "key": "capture:silent", "msg": f"The mirror port {cap['interface']} has gone quiet ({err}); check the switch's mirror/SPAN setting"})
    return out
```
and call it where `A` is assembled: `A.extend(evaluate_capture(res))`. In `remedy.HUMAN_REMEDIES` add:
```python
    ("capture:unavailable",
     "traffic capture needs the tcpdump package on the sensor box, which I cannot install from here",
     "run `sudo apt-get install -y tcpdump` on the sensor, then tell me and I will re-check"),
    ("capture:silent",
     "the mirror port has stopped delivering packets, which is a switch setting I have no login for",
     "check the switch's mirror/SPAN configuration still points the uplink at the sensor's port, then tell me and I will re-check"),
```
- [ ] **Step 4: Run** — `cd sensor && .venv/bin/pytest tests/test_capture_alerts.py -q` and `cd hub && .venv/bin/pytest tests/test_remedy.py -q` → pass.
- [ ] **Step 5: Commit**
```bash
git add sensor/guardian_sensor/alerts.py sensor/tests/test_capture_alerts.py hub/guardian_hub/remedy.py
git commit -m "A broken or silent mirror port is reported as something a person must fix"
```

---

### Task 6: Hub tables — Flow, FlowRollup, Network.traffic_mode

**Files:**
- Modify: `hub/guardian_hub/models.py` (after `DnsRollup`, ~line 475; `Network` ~line 173)
- Create: `hub/alembic/versions/0029_flow.py`
- Test: `hub/tests/test_alembic_single_head.py` (exists), `hub/tests/test_flow_ingest.py` (created in Task 7 — this task only needs the head test and a cold import).

- [ ] **Step 1: Models**
```python
class Flow(Base):
    """One minute of one device talking to one peer on one port (spec §2).
    Keyed on the device's MAC, not its IP, so a DHCP renewal does not split a
    device's history. `ts` is the sensor's own ISO string -- lexicographic in
    SQL, see history.py's entry in CLAUDE.md. `hints` holds only the three
    fingerprinting prefixes; nothing from a packet body is ever stored."""
    __tablename__ = "flow"
    __table_args__ = (
        UniqueConstraint("tenant_id", "ts", "mac", "peer_ip", "peer_port", "proto", "source", name="uq_flow"),
        Index("ix_flow_ts", "tenant_id", "ts"),
        Index("ix_flow_mac", "tenant_id", "mac", "ts"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(32))
    network_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    ts: Mapped[str] = mapped_column(String(32))
    mac: Mapped[str] = mapped_column(String(17))
    ip: Mapped[str] = mapped_column(String(45), default="")
    peer_ip: Mapped[str] = mapped_column(String(45), default="")
    peer_port: Mapped[int] = mapped_column(Integer, default=0)
    proto: Mapped[str] = mapped_column(String(8), default="other")
    bytes_in: Mapped[int] = mapped_column(Integer, default=0)
    bytes_out: Mapped[int] = mapped_column(Integer, default=0)
    pkts_in: Mapped[int] = mapped_column(Integer, default=0)
    pkts_out: Mapped[int] = mapped_column(Integer, default=0)
    peer_name: Mapped[str] = mapped_column(String(255), default="")
    hints: Mapped[dict] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(8), default="mirror")   # mirror|router

class FlowRollup(Base):
    """Per-day flow totals that outlive the minute rows (spec §3), the
    `DnsRollup` pattern applied to traffic. `peer` is `peer_name` when the
    resolver saw one, else `peer_ip`."""
    __tablename__ = "flow_rollup"
    __table_args__ = (
        UniqueConstraint("tenant_id", "day", "network_id", "mac", "peer", "peer_port", "proto"),
        Index("ix_flowroll", "tenant_id", "day"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(32), index=True)
    day: Mapped[str] = mapped_column(String(10))
    network_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    mac: Mapped[str] = mapped_column(String(17))
    peer: Mapped[str] = mapped_column(String(255))
    peer_port: Mapped[int] = mapped_column(Integer, default=0)
    proto: Mapped[str] = mapped_column(String(8), default="other")
    bytes_in: Mapped[int] = mapped_column(Integer, default=0)
    bytes_out: Mapped[int] = mapped_column(Integer, default=0)
    pkts: Mapped[int] = mapped_column(Integer, default=0)
```
On `Network` add: `traffic_mode: Mapped[str] = mapped_column(String(8), default="none")  # mirror|router|none -- set by a person in the wizard/Settings, never guessed (spec §5)`.

- [ ] **Step 2: Alembic**
```python
"""Flows: who talked to whom, per minute, and their daily rollups; networks say how traffic reaches the sensor."""
from alembic import op
import sqlalchemy as sa

revision = "0029_flow"
down_revision = "0028_partner"
branch_labels = depends_on = None

def upgrade():
    op.create_table("flow",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("network_id", sa.String(32), nullable=True),
        sa.Column("ts", sa.String(32), nullable=False),
        sa.Column("mac", sa.String(17), nullable=False),
        sa.Column("ip", sa.String(45), nullable=False, server_default=""),
        sa.Column("peer_ip", sa.String(45), nullable=False, server_default=""),
        sa.Column("peer_port", sa.Integer, nullable=False, server_default="0"),
        sa.Column("proto", sa.String(8), nullable=False, server_default="other"),
        sa.Column("bytes_in", sa.Integer, nullable=False, server_default="0"),
        sa.Column("bytes_out", sa.Integer, nullable=False, server_default="0"),
        sa.Column("pkts_in", sa.Integer, nullable=False, server_default="0"),
        sa.Column("pkts_out", sa.Integer, nullable=False, server_default="0"),
        sa.Column("peer_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("hints", sa.JSON, nullable=False),
        sa.Column("source", sa.String(8), nullable=False, server_default="mirror"),
        sa.UniqueConstraint("tenant_id", "ts", "mac", "peer_ip", "peer_port", "proto", "source", name="uq_flow"),
    )
    op.create_index("ix_flow_ts", "flow", ["tenant_id", "ts"])
    op.create_index("ix_flow_mac", "flow", ["tenant_id", "mac", "ts"])
    op.create_index("ix_flow_network_id", "flow", ["network_id"])
    op.create_table("flow_rollup",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("network_id", sa.String(32), nullable=True),
        sa.Column("mac", sa.String(17), nullable=False),
        sa.Column("peer", sa.String(255), nullable=False),
        sa.Column("peer_port", sa.Integer, nullable=False, server_default="0"),
        sa.Column("proto", sa.String(8), nullable=False, server_default="other"),
        sa.Column("bytes_in", sa.Integer, nullable=False, server_default="0"),
        sa.Column("bytes_out", sa.Integer, nullable=False, server_default="0"),
        sa.Column("pkts", sa.Integer, nullable=False, server_default="0"),
        sa.UniqueConstraint("tenant_id", "day", "network_id", "mac", "peer", "peer_port", "proto"),
    )
    op.create_index("ix_flowroll", "flow_rollup", ["tenant_id", "day"])
    op.create_index("ix_flow_rollup_tenant_id", "flow_rollup", ["tenant_id"])
    with op.batch_alter_table("network") as b:
        b.add_column(sa.Column("traffic_mode", sa.String(8), nullable=False, server_default="none"))

def downgrade():
    with op.batch_alter_table("network") as b:
        b.drop_column("traffic_mode")
    op.drop_table("flow_rollup")
    op.drop_table("flow")
```
- [ ] **Step 3: Verify** — `cd hub && .venv/bin/pytest tests/test_alembic_single_head.py -q && .venv/bin/python -c "import guardian_hub.main"`; then `.venv/bin/alembic upgrade head` against a scratch copy of the dev DB → no error.
- [ ] **Step 4: Commit**
```bash
git add hub/guardian_hub/models.py hub/alembic/versions/0029_flow.py
git commit -m "The hub has a table for who talked to whom, and networks record how traffic reaches the sensor"
```

---

### Task 7: Ingest — flows out of `raw`, into `flow`

**Files:**
- Modify: `hub/guardian_hub/ingest.py:203-240` (out counters, pop) and after the DNS block (~line 880)
- Test: `hub/tests/test_flow_ingest.py`

**Interfaces:**
- Consumes: `_net_for`, `ip2mac`, `_ts_bounds`, `existing_*` dedup pattern, `networks.by_slug`.
- Produces: `out["flows"]`, `out["flows_rejected"]`; `m.Flow` rows; `Scan.raw` without `flows`.

- [ ] **Step 1: Failing tests**
```python
# hub/tests/test_flow_ingest.py
from guardian_hub.db import SessionLocal
from guardian_hub import models as m
from tests.test_seed_helpers import seeded_demo

def _row(**kw):
    base = {"ts": "2026-09-20T10:00:00+00:00", "net": None, "mac": "aa:bb:cc:00:00:01", "ip": "192.168.1.20",
            "peer_ip": "142.250.1.1", "peer_port": 443, "proto": "tcp", "bytes_in": 1000, "bytes_out": 100,
            "pkts_in": 10, "pkts_out": 3, "hints": {"sni": "www.google.com"}, "source": "mirror"}
    base.update(kw); return base

def _push(client, rows, ts="2026-09-20T10:05:00+00:00"):
    from tests.test_seed_helpers import sensor_headers, minimal_payload   # existing helpers
    body = minimal_payload(ts); body["flows"] = rows
    r = client.post("/api/v1/ingest", json=body, headers=sensor_headers(client))
    assert r.status_code == 200, r.text
    return r.json()

def test_flows_land_in_their_table_and_never_in_scan_raw(client):
    seeded_demo(client)
    out = _push(client, [_row()])
    assert out["flows"] == 1
    db = SessionLocal()
    assert db.query(m.Flow).filter_by(mac="aa:bb:cc:00:00:01").count() == 1
    latest = db.query(m.Scan).order_by(m.Scan.ts.desc()).first()
    assert "flows" not in (latest.raw or {})

def test_a_retry_adds_zero_rows(client):
    seeded_demo(client)
    _push(client, [_row()]); out = _push(client, [_row()], ts="2026-09-20T10:10:00+00:00")
    assert out["flows"] == 0 and SessionLocal().query(m.Flow).count() == 1

def test_one_bad_row_is_dropped_and_the_scan_still_stores(client):
    seeded_demo(client)
    out = _push(client, [_row(), _row(mac="not-a-mac"), _row(peer_port=80, bytes_in=-5)])
    assert out["flows"] == 1 and out["flows_rejected"] == 2 and out["scan"] == 1

def test_network_is_attributed_like_dns(client):
    seeded_demo(client)
    _push(client, [_row(net=None)])
    row = SessionLocal().query(m.Flow).one()
    assert row.network_id is not None
```
If `minimal_payload`/`sensor_headers` do not exist under those names in `tests/test_seed_helpers.py`, use whatever helper the existing `test_dns_ingest*.py` uses to post an ingest — copy its exact call.

- [ ] **Step 2: Run to fail** — `KeyError: 'flows'` on `out`.
- [ ] **Step 3: Implement** — in the `out = {...}` dict add `"flows": 0, "flows_rejected": 0`. Directly under the heartbeat-token pop (before `# --- Scan`):
```python
    # Flows come out of the payload here too, and for the same reason as the
    # tokens: `Scan.raw` is served to any viewer, and a minute of flows for
    # forty devices is thousands of rows that belong in their own table, not
    # in a blob every dashboard poll can read. Popped ABOVE the insert so what
    # is persisted never had them.
    flow_rows = last.pop("flows", None) or p.get("flows") or []
```
(`p` is the whole POST body; `last` is `p["last"]`. `sync.push` puts `flows` at the top level of the body, so `p.get("flows")` is the real source; the `last.pop` is belt-and-braces for a sensor that nests it.) After the DNS block:
```python
    # --- Flows: dedup on the full key, one IN query per payload, per-row
    #     validation so one bad row costs one row and never the scan. -------
    _MAC = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
    fl_bounds = _ts_bounds([(f.get("ts") or ts) if isinstance(f, dict) else None for f in flow_rows])
    existing_fl: set[tuple] = set()
    if fl_bounds:
        lo, hi = fl_bounds
        existing_fl = set(db.query(m.Flow.ts, m.Flow.mac, m.Flow.peer_ip, m.Flow.peer_port, m.Flow.proto, m.Flow.source)
                          .filter(m.Flow.tenant_id == tenant.id, m.Flow.ts >= lo, m.Flow.ts <= hi).all())
    for f in flow_rows:
        try:
            mac = str(f.get("mac") or "").lower()
            if not _MAC.match(mac): raise ValueError("mac")
            vals = {k: int(f.get(k) or 0) for k in ("bytes_in", "bytes_out", "pkts_in", "pkts_out", "peer_port")}
            if any(v < 0 for v in vals.values()): raise ValueError("negative")
            proto = str(f.get("proto") or "other")[:8]
            source = "router" if f.get("source") == "router" else "mirror"
            f_ts, f_ip, peer_ip = str(f.get("ts") or ts), str(f.get("ip") or "")[:45], str(f.get("peer_ip") or "")[:45]
        except (ValueError, TypeError, AttributeError):
            out["flows_rejected"] += 1
            continue
        key = (f_ts, mac, peer_ip, vals["peer_port"], proto, source)
        if key in existing_fl:
            continue
        existing_fl.add(key)
        hints = f.get("hints") if isinstance(f.get("hints"), dict) else {}
        hints = {k: hints[k] for k in ("sni", "dhcp", "mdns") if k in hints}
        db.add(m.Flow(tenant_id=tenant.id, network_id=_net_for(f.get("net"), f_ip).id, ts=f_ts, mac=mac, ip=f_ip,
                      peer_ip=peer_ip, peer_port=vals["peer_port"], proto=proto,
                      bytes_in=vals["bytes_in"], bytes_out=vals["bytes_out"], pkts_in=vals["pkts_in"], pkts_out=vals["pkts_out"],
                      peer_name=str(f.get("peer_name") or "")[:255], hints=hints, source=source))
        out["flows"] += 1
```
(`import re` at top if absent.) Also, where `capture` info arrives in `last["capture"]`, nothing is stored beyond `raw` — the payload reads it from the latest scan.

- [ ] **Step 4: Run** — `cd hub && .venv/bin/pytest tests/test_flow_ingest.py -q` → 4 passed. Then `tests/test_ingest*.py -q` for regressions.
- [ ] **Step 5: Commit**
```bash
git add hub/guardian_hub/ingest.py hub/tests/test_flow_ingest.py
git commit -m "Flows the sensor sends are stored once each, in their own table, never in the scan blob"
```

---

### Task 8: Retention — raw days, rollups, prune

**Files:**
- Modify: `hub/guardian_hub/retention.py` (`_POLICY_DEFAULTS`, `policy`, new `_build_flow_rollup_rows`, `rollup_flows`, `prune_tenant` steps)
- Test: `hub/tests/test_flow_retention.py`

**Interfaces:**
- Produces: `retention.rollup_flows(db, tenant, before_day: str) -> int`; policy keys `flow_raw_days` (7), `flow_rollup_days` (365, floored at raw + `DNS_ROLLUP_MIN_EXTRA_DAYS`); `prune_tenant` counts `flow`, `flow_rollup`, `flows_rolled_up`.

- [ ] **Step 1: Failing test**
```python
# hub/tests/test_flow_retention.py
import datetime as dt
from guardian_hub.db import SessionLocal
from guardian_hub import models as m, retention
from tests.test_seed_helpers import seeded_demo

def _old_flow(db, tenant_id, days_ago, peer="1.1.1.1", name=""):
    ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)).isoformat(timespec="seconds")
    db.add(m.Flow(tenant_id=tenant_id, ts=ts, mac="aa:bb:cc:00:00:01", ip="192.168.1.20", peer_ip=peer,
                  peer_port=443, proto="tcp", bytes_in=500, bytes_out=50, pkts_in=5, pkts_out=1, peer_name=name, hints={}))

def test_rollup_runs_before_raw_rows_age_out_and_answers_by_name(client):
    seeded_demo(client)
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    _old_flow(db, t.id, 10, name="www.google.com"); _old_flow(db, t.id, 10, name="www.google.com"); db.commit()
    counts = retention.prune_tenant(db, t)
    assert counts["flows_rolled_up"] == 1 and counts["flow"] == 2
    roll = db.query(m.FlowRollup).filter_by(tenant_id=t.id).one()
    assert roll.peer == "www.google.com" and roll.bytes_in == 1000 and roll.pkts == 12
    assert db.query(m.Flow).filter_by(tenant_id=t.id).count() == 0

def test_fresh_rows_are_left_alone(client):
    seeded_demo(client)
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    _old_flow(db, t.id, 1); db.commit()
    retention.prune_tenant(db, t)
    assert db.query(m.Flow).filter_by(tenant_id=t.id).count() == 1
```
- [ ] **Step 2: Run to fail** — `KeyError: 'flows_rolled_up'`.
- [ ] **Step 3: Implement** — add `"flow_raw_days": 7, "flow_rollup_days": 365` to `_POLICY_DEFAULTS`; in `policy()` floor `flow_rollup_days` at `flow_raw_days + DNS_ROLLUP_MIN_EXTRA_DAYS` with the same comment DNS carries. Add:
```python
def _build_flow_rollup_rows(db, tenant_id: str, before_day: str) -> list["m.FlowRollup"]:
    """Group raw flows older than `before_day` by (day, network, mac, peer,
    port, proto). `peer` is the DNS name when the sensor saw one, else the
    IP -- a month later "142.250.1.1" means nothing and "googleapis.com"
    means everything. Idempotent per day like `_build_rollup_rows`."""
    from sqlalchemy import func
    q = (db.query(func.substr(m.Flow.ts, 1, 10).label("day"), m.Flow.network_id, m.Flow.mac,
                  func.coalesce(func.nullif(m.Flow.peer_name, ""), m.Flow.peer_ip).label("peer"),
                  m.Flow.peer_port, m.Flow.proto,
                  func.sum(m.Flow.bytes_in), func.sum(m.Flow.bytes_out), func.sum(m.Flow.pkts_in + m.Flow.pkts_out))
           .filter(m.Flow.tenant_id == tenant_id, m.Flow.ts < before_day)
           .group_by("day", m.Flow.network_id, m.Flow.mac, "peer", m.Flow.peer_port, m.Flow.proto))
    return [m.FlowRollup(tenant_id=tenant_id, day=d, network_id=n, mac=mac, peer=p, peer_port=pp, proto=pr,
                         bytes_in=int(bi or 0), bytes_out=int(bo or 0), pkts=int(pk or 0))
            for d, n, mac, p, pp, pr, bi, bo, pk in q.all()]

def rollup_flows(db, tenant, before_day: str) -> int:
    rows = _build_flow_rollup_rows(db, tenant.id, before_day)
    days = {r.day for r in rows}
    if days:
        db.query(m.FlowRollup).filter(m.FlowRollup.tenant_id == tenant.id, m.FlowRollup.day.in_(days)).delete(synchronize_session=False)
    db.add_all(rows); db.flush()
    return len(rows)
```
In `prune_tenant`, mirror the three DNS steps: compute `flow_raw_cutoff_day`/`flow_rollup_cutoff_day`; `counts["flows_rolled_up"] = rollup_flows(...)` (or the `len(_build_flow_rollup_rows(...))` in dry-run); `counts["flow"] = _batched_delete_by_id(db, m.Flow, (m.Flow.tenant_id == tenant.id) & (m.Flow.ts < flow_raw_cutoff_day))`; `counts["flow_rollup"] = _batched_delete_by_id(db, m.FlowRollup, (m.FlowRollup.tenant_id == tenant.id) & (m.FlowRollup.day < flow_rollup_cutoff_day))`.
- [ ] **Step 4: Run** — `cd hub && .venv/bin/pytest tests/test_flow_retention.py tests/test_retention*.py -q` → pass.
- [ ] **Step 5: Commit**
```bash
git add hub/guardian_hub/retention.py hub/tests/test_flow_retention.py
git commit -m "Traffic history outlives its minute rows: a week raw, a year by the day"
```

---

### Task 9: `payloads/traffic.py` and its schemas (with the `types.ts` mirror)

**Files:**
- Create: `hub/guardian_hub/payloads/traffic.py`
- Modify: `hub/guardian_hub/schemas_dash.py` (append after `DnsRollup`-related models), `web/src/types.ts` (append after `DnsStatus`)
- Test: `hub/tests/test_traffic_payload.py`; `hub/tests/test_contracts.py` stays green.

**Interfaces:**
- Produces (schemas, field-for-field mirrored in `types.ts`):
```python
class TrafficPeer(_Model):
    peer: str = ""          # DNS name when known, else IP
    peer_ip: str = ""
    peer_port: int = 0
    proto: str = ""
    bytes_in: int = 0
    bytes_out: int = 0
    first_seen: str = ""
    last_seen: str = ""
class TrafficTalker(_Model):
    mac: str = ""
    name: str = ""          # label or name or vendor or mac -- display only
    ip: str = ""
    bytes_in: int = 0
    bytes_out: int = 0
    top_peer: str = ""
class TrafficOverview(_Model):
    mode: str = "none"      # mirror|router|none
    truncated: bool = False
    window_hours: int = 1
    top_talkers: list[TrafficTalker] = Field(default_factory=list)
    top_peers: list[TrafficPeer] = Field(default_factory=list)
    new_peers: list[TrafficPeer] = Field(default_factory=list)
    external_share: float = 0.0
class DeviceTraffic(_Model):
    mode: str = "none"
    mac: str = ""
    window_hours: int = 24
    bytes_in: int = 0
    bytes_out: int = 0
    peers: list[TrafficPeer] = Field(default_factory=list)
    hourly: list[int] = Field(default_factory=list)   # bytes per hour, oldest first, len == window_hours
```
- Functions: `traffic.mode_for(db, tenant, net) -> tuple[str, bool]` (mode, truncated — from the latest scan's `raw["capture"]` and the network's `traffic_mode`); `traffic.overview(db, tenant, hours=1, net=None) -> TrafficOverview`; `traffic.device(db, tenant, mac, hours=24, net=None) -> DeviceTraffic`; `traffic.server(db, tenant, host: str) -> DeviceTraffic | None` (resolves the `ssh_hosts` entry's IP to the device row's MAC via `networks.device_row` / `m.Device.ip`).

- [ ] **Step 1: Failing tests**
```python
# hub/tests/test_traffic_payload.py
import datetime as dt
from guardian_hub.db import SessionLocal
from guardian_hub import models as m
from guardian_hub.payloads import traffic
from tests.test_seed_helpers import seeded_demo
from tests.querycount import queries_for

def _flow(db, t, mac, peer_ip, bi, bo, minutes_ago=5, name="", offset="+00:00"):
    ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes_ago)).replace(microsecond=0)
    if offset != "+00:00":   # same instant, written in another zone -- the lexicographic trap
        ts = ts.astimezone(dt.timezone(dt.timedelta(hours=int(offset[:3]))))
    db.add(m.Flow(tenant_id=t.id, ts=ts.isoformat(), mac=mac, ip="192.168.1.20", peer_ip=peer_ip, peer_port=443,
                  proto="tcp", bytes_in=bi, bytes_out=bo, pkts_in=1, pkts_out=1, peer_name=name, hints={}))

def _tenant(): db = SessionLocal(); return db, db.query(m.Tenant).filter_by(slug="demo").one()

def test_mode_is_none_when_no_network_declares_a_mirror(client):
    seeded_demo(client); db, t = _tenant()
    assert traffic.overview(db, t).mode == "none"

def test_top_talkers_and_peers_by_bytes_with_names_over_ips(client):
    seeded_demo(client); db, t = _tenant()
    net = db.query(m.Network).filter_by(tenant_id=t.id, is_primary=True).one(); net.traffic_mode = "mirror"
    dev = db.query(m.Device).filter_by(tenant_id=t.id).first()
    _flow(db, t, dev.mac, "142.250.1.1", 5000, 100, name="www.google.com"); _flow(db, t, "aa:bb:cc:00:00:02", "1.1.1.1", 10, 10); db.commit()
    o = traffic.overview(db, t, hours=1)
    assert o.mode == "mirror" and o.top_talkers[0].mac == dev.mac and o.top_peers[0].peer == "www.google.com"

def test_a_row_in_another_offset_is_still_inside_the_window(client):
    seeded_demo(client); db, t = _tenant()
    _flow(db, t, "aa:bb:cc:00:00:03", "9.9.9.9", 1, 1, minutes_ago=5, offset="+05:00"); db.commit()
    d = traffic.device(db, t, "aa:bb:cc:00:00:03", hours=1)
    assert d.bytes_in == 1

def test_new_peers_are_those_unseen_in_the_previous_raw_window(client):
    seeded_demo(client); db, t = _tenant()
    _flow(db, t, "aa:bb:cc:00:00:04", "8.8.8.8", 1, 1, minutes_ago=3 * 24 * 60); _flow(db, t, "aa:bb:cc:00:00:04", "8.8.8.8", 1, 1)
    _flow(db, t, "aa:bb:cc:00:00:04", "5.5.5.5", 1, 1); db.commit()
    assert [p.peer for p in traffic.overview(db, t, hours=1).new_peers] == ["5.5.5.5"]

def test_statement_count_does_not_grow_with_devices(client):
    seeded_demo(client); db, t = _tenant()
    for i in range(3): _flow(db, t, f"aa:bb:cc:00:01:{i:02x}", "1.1.1.1", 1, 1)
    db.commit(); n3 = queries_for(client, "/api/v1/traffic?hours=1")
    for i in range(30): _flow(db, t, f"aa:bb:cc:00:02:{i:02x}", "1.1.1.1", 1, 1)
    db.commit(); n33 = queries_for(client, "/api/v1/traffic?hours=1")
    assert n3 == n33
```
(the last test needs Task 10's route; write it now, expect it to fail until Task 10, and note that in the commit.)

- [ ] **Step 2: Run to fail** — `ModuleNotFoundError: payloads.traffic`.
- [ ] **Step 3: Implement** `payloads/traffic.py`:
```python
"""Who talked to whom, shaped for the page and the bot. Spec §4.

Answers "now and recent" only. A trend over time belongs in
`payloads/history.build_charts` -- the one place a trend is computed -- and
nowhere here. Every response carries `mode`, and `mode` is read from a
person's declaration (`Network.traffic_mode`) crossed with what the sensor
actually reported (`raw["capture"]`), never inferred from the rows: rows
from a router look like rows from a mirror, and only the mode says which
questions they can answer.
"""
from __future__ import annotations
import datetime as dt
from sqlalchemy import func
from sqlalchemy.orm import Session as DbSession
from .. import models as m, networks
from ..scans import latest_scan, parse_ts
from ..schemas_dash import DeviceTraffic, TrafficOverview, TrafficPeer, TrafficTalker
from ._util import _lax_cutoff
from ..retention import policy

TOP_N = 10

def mode_for(db: DbSession, tenant, net: "m.Network | None") -> tuple[str, bool]:
    scan = latest_scan(db, tenant.id)
    cap = ((scan.raw or {}).get("capture") or {}) if scan else {}
    declared = net.traffic_mode if net is not None else max(
        (n.traffic_mode for n in db.query(m.Network).filter_by(tenant_id=tenant.id).all()),
        key=lambda s: {"mirror": 2, "router": 1}.get(s, 0), default="none")
    reported = cap.get("mode") or "none"
    # The sensor's report wins whenever a person declared mirror OR router: a
    # declared mirror the sensor has not delivered (no tcpdump, silent port)
    # is not a mirror, and a declared router with no integration feeding rows
    # is not a router -- the bot would otherwise claim "bytes per device"
    # about nothing. Only a declared "none" is taken at its word.
    mode = reported if declared in ("mirror", "router") else "none"
    return mode, bool(cap.get("flows_dropped"))

def _window(hours: int) -> tuple[dt.datetime, str]:
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    return cutoff, _lax_cutoff(cutoff)

def _rows(db, tenant, cutoff, lax, net, mac=None):
    q = db.query(m.Flow).filter(m.Flow.tenant_id == tenant.id, m.Flow.ts >= lax)
    if net is not None: q = q.filter(m.Flow.network_id == net.id)
    if mac: q = q.filter(m.Flow.mac == mac.lower())
    # Lax SQL cutoff, exact Python re-check: `ts` is a string in the sensor's
    # own offset, so `>=` in SQL is lexicographic.
    return [r for r in q.all() if (parse_ts(r.ts) or cutoff) >= cutoff]

def _peer_label(r) -> str: return r.peer_name or r.peer_ip

def overview(db: DbSession, tenant, hours: int = 1, net=None) -> TrafficOverview:
    mode, truncated = mode_for(db, tenant, net)
    cutoff, lax = _window(hours)
    rows = _rows(db, tenant, cutoff, lax, net)
    out = TrafficOverview(mode=mode, truncated=truncated, window_hours=hours)
    if not rows: return out
    by_mac: dict[str, dict] = {}; by_peer: dict[str, dict] = {}
    ext_bytes = total = 0
    for r in rows:
        d = by_mac.setdefault(r.mac, {"bi": 0, "bo": 0, "ip": r.ip, "peers": {}})
        d["bi"] += r.bytes_in; d["bo"] += r.bytes_out
        d["peers"][_peer_label(r)] = d["peers"].get(_peer_label(r), 0) + r.bytes_in + r.bytes_out
        p = by_peer.setdefault(_peer_label(r), {"ip": r.peer_ip, "port": r.peer_port, "proto": r.proto, "bi": 0, "bo": 0, "first": r.ts, "last": r.ts})
        p["bi"] += r.bytes_in; p["bo"] += r.bytes_out; p["first"] = min(p["first"], r.ts); p["last"] = max(p["last"], r.ts)
        total += r.bytes_in + r.bytes_out
        if r.peer_ip and not networks.is_private_ip(r.peer_ip): ext_bytes += r.bytes_in + r.bytes_out
    # One query for every device name at once, never one per talker.
    devs = {d.mac: d for d in db.query(m.Device).filter(m.Device.tenant_id == tenant.id, m.Device.mac.in_(list(by_mac))).all()}
    out.top_talkers = [TrafficTalker(mac=mac, ip=d["ip"], bytes_in=d["bi"], bytes_out=d["bo"],
                                     name=(lambda dv: (dv.label or dv.name or dv.vendor or mac) if dv else mac)(devs.get(mac)),
                                     top_peer=max(d["peers"], key=d["peers"].get) if d["peers"] else "")
                       for mac, d in sorted(by_mac.items(), key=lambda kv: -(kv[1]["bi"] + kv[1]["bo"]))[:TOP_N]]
    out.top_peers = [TrafficPeer(peer=k, peer_ip=p["ip"], peer_port=p["port"], proto=p["proto"], bytes_in=p["bi"], bytes_out=p["bo"], first_seen=p["first"], last_seen=p["last"])
                     for k, p in sorted(by_peer.items(), key=lambda kv: -(kv[1]["bi"] + kv[1]["bo"]))[:TOP_N]]
    out.external_share = round(ext_bytes / total, 3) if total else 0.0
    # New peers: EVERY peer nobody on this network contacted in the previous
    # flow_raw_days -- not only the top talkers, because the peer a security
    # product must surface is the one small new outbound connection. Sorted
    # biggest first and capped at TOP_N for display only.
    raw_days = policy(tenant)["flow_raw_days"]
    prev_cut = cutoff - dt.timedelta(days=raw_days)
    seen_before = _seen_before(db, tenant, net, prev_cut, cutoff)
    new = [(k, p) for k, p in by_peer.items() if k not in seen_before]
    new.sort(key=lambda kv: -(kv[1]["bi"] + kv[1]["bo"]))
    out.new_peers = [TrafficPeer(peer=k, peer_ip=p["ip"], peer_port=p["port"], proto=p["proto"], bytes_in=p["bi"], bytes_out=p["bo"], first_seen=p["first"], last_seen=p["last"])
                     for k, p in new[:TOP_N]]
    return out

def _seen_before(db, tenant, net, prev_cut, cutoff) -> set[str]:
    """Peers any device on this network contacted in [prev_cut, cutoff).
    Same lax-then-exact dance as _rows(): `ts` is a sensor-offset string, so
    BOTH SQL bounds are a superset and Python decides membership. The first
    draft used the current window's lax lower bound as the upper bound here,
    which left the day before the window uncounted and reported peers seen
    twelve hours ago as new."""
    q = db.query(m.Flow.ts, m.Flow.peer_ip, m.Flow.peer_name).filter(
        m.Flow.tenant_id == tenant.id,
        m.Flow.ts >= _lax_cutoff(prev_cut),
        m.Flow.ts < (cutoff + dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S"))
    if net is not None:
        q = q.filter(m.Flow.network_id == net.id)
    out: set[str] = set()
    for ts, ip, name in q.all():
        when = parse_ts(ts)
        if when is not None and prev_cut <= when < cutoff:
            out.add(name or ip)
    return out

def device(db: DbSession, tenant, mac: str, hours: int = 24, net=None) -> DeviceTraffic:
    mode, _ = mode_for(db, tenant, net)
    cutoff, lax = _window(hours)
    rows = _rows(db, tenant, cutoff, lax, net, mac=mac)
    out = DeviceTraffic(mode=mode, mac=mac.lower(), window_hours=hours, hourly=[0] * hours)
    peers: dict[tuple, dict] = {}
    for r in rows:
        out.bytes_in += r.bytes_in; out.bytes_out += r.bytes_out
        k = (_peer_label(r), r.peer_port, r.proto)
        p = peers.setdefault(k, {"ip": r.peer_ip, "bi": 0, "bo": 0, "first": r.ts, "last": r.ts})
        p["bi"] += r.bytes_in; p["bo"] += r.bytes_out; p["first"] = min(p["first"], r.ts); p["last"] = max(p["last"], r.ts)
        when = parse_ts(r.ts) or cutoff
        idx = min(hours - 1, max(0, int((when - cutoff).total_seconds() // 3600)))
        out.hourly[idx] += r.bytes_in + r.bytes_out
    out.peers = [TrafficPeer(peer=k[0], peer_ip=p["ip"], peer_port=k[1], proto=k[2], bytes_in=p["bi"], bytes_out=p["bo"], first_seen=p["first"], last_seen=p["last"])
                 for k, p in sorted(peers.items(), key=lambda kv: -(kv[1]["bi"] + kv[1]["bo"]))[:50]]
    return out

def server(db: DbSession, tenant, host: str) -> DeviceTraffic | None:
    """A configured server by its `ssh_hosts` name: the customer says "prod",
    never a MAC. Resolved to the device row whose IP the entry targets."""
    target = next((h.get("target", "") for h in (tenant.config or {}).get("ssh_hosts") or [] if h.get("name") == host), "")
    ip = target.split("@")[-1] if target else ""
    dev = db.query(m.Device).filter_by(tenant_id=tenant.id, ip=ip).first() if ip else None
    return device(db, tenant, dev.mac) if dev else None
```
If `networks.is_private_ip` does not exist, add it to `networks.py` as a two-line wrapper around `ipaddress.ip_address(ip).is_private` (True on parse failure).

- [ ] **Step 4: Mirror in `types.ts`** — append `export type TrafficPeer = {...}`, `TrafficTalker`, `TrafficOverview`, `DeviceTraffic` with identical field names and TS types (`number`, `string`, `boolean`, `TrafficPeer[]`, `number[]`).
- [ ] **Step 5: Run** — `cd hub && .venv/bin/pytest tests/test_traffic_payload.py tests/test_contracts.py -q` → all pass except `test_statement_count_does_not_grow_with_devices` (needs Task 10's route; expected 404 → fails). Proceed only if the other four pass and contracts is green.
- [ ] **Step 6: Commit**
```bash
git add hub/guardian_hub/payloads/traffic.py hub/guardian_hub/schemas_dash.py hub/guardian_hub/networks.py web/src/types.ts hub/tests/test_traffic_payload.py
git commit -m "The hub can say which devices talk the most, to whom, and what is new (route follows)"
```

---

### Task 10: Read routes and the network's declared mode

**Files:**
- Modify: `hub/guardian_hub/routers/read.py` (after `/presence`, line 122), `hub/guardian_hub/routers/networks.py:42-60` (`NetworkIn`, `NetworkPatch`), `hub/guardian_hub/schemas_dash.py` (`Network` model gets `traffic_mode: str = "none"`), `web/src/types.ts` (`Network` gets `traffic_mode: string`)
- Test: `hub/tests/test_traffic_payload.py` (the statement-count test now passes), `hub/tests/test_networks*.py` (existing) plus one new case.

- [ ] **Step 1: Routes**
```python
@router.get("/traffic", response_model=sd.TrafficOverview)
def traffic_overview(hours: int = 1, network: str = "", ctx=VIEWER, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    if not 1 <= hours <= 168:
        raise HTTPException(422, "hours must be between 1 and 168")
    return traffic_payload.overview(db, tenant, hours=hours, net=networks.resolve(db, tenant.id, network))

@router.get("/devices/{mac}/traffic", response_model=sd.DeviceTraffic)
def device_traffic(mac: str, hours: int = 24, network: str = "", ctx=VIEWER, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    if not 1 <= hours <= 168:
        raise HTTPException(422, "hours must be between 1 and 168")
    return traffic_payload.device(db, tenant, mac, hours=hours, net=networks.resolve(db, tenant.id, network))
```
(add `traffic as traffic_payload` to the `from ..payloads import (...)` block.)
- [ ] **Step 2: `traffic_mode` on networks** — `NetworkIn.traffic_mode: str = "none"`, `NetworkPatch.traffic_mode: str | None = None`; validate `in ("mirror", "router", "none")` else 422 "traffic_mode must be mirror, router or none"; write it in create/patch where `kind` is written; include it in the `sd.Network` response. Add to `sd.Network` and `types.ts` `Network`.
- [ ] **Step 3: Test** — a case in `hub/tests/test_traffic_payload.py`:
```python
def test_patching_a_network_to_mirror_changes_the_reported_mode(client):
    seeded_demo(client)
    db, t = _tenant(); net = db.query(m.Network).filter_by(tenant_id=t.id, is_primary=True).one()
    r = client.patch(f"/api/v1/networks/{net.id}", json={"traffic_mode": "mirror"}); assert r.status_code == 200
    assert client.get("/api/v1/traffic").json()["mode"] in ("mirror", "none")   # none until the sensor reports capture
    r = client.patch(f"/api/v1/networks/{net.id}", json={"traffic_mode": "sideways"}); assert r.status_code == 422
```
(the `client` fixture is signed in as demo admin — check `tests/conftest.py`; if it is not, use the sign-in helper the other network tests use.)
- [ ] **Step 4: Run** — `cd hub && .venv/bin/pytest tests/test_traffic_payload.py tests/test_networks*.py tests/test_contracts.py tests/test_chat_coverage.py -q` → all pass (the coverage guard is unaffected: these are GETs and `PATCH /networks/{net_id}` already has `rename_network`).
- [ ] **Step 5: Commit**
```bash
git add hub/guardian_hub/routers/read.py hub/guardian_hub/routers/networks.py hub/guardian_hub/schemas_dash.py web/src/types.ts hub/tests/test_traffic_payload.py
git commit -m "The dashboard can ask who is talking to whom, and each network says how Guardian sees its traffic"
```

---

### Task 11: Three chat tools

**Files:**
- Modify: `hub/guardian_hub/chat/surfaces.py` (append), `hub/guardian_hub/chat/tools.py` (`TOOLS`)
- Test: `hub/tests/test_chat_coverage.py` (append)

- [ ] **Step 1: Failing tests**
```python
def test_traffic_tools_are_viewer_tier_1(client):
    for name in ("top_talkers", "device_traffic", "server_peers"):
        assert registry.TOOLS[name].tier == 1 and registry.TOOLS[name].min_role == "viewer", name

def test_router_or_none_mode_answer_opens_with_the_limitation(client):
    seeded_demo(client)
    res = registry.dispatch(ctx_for(role="viewer"), "top_talkers", {"hours": 1})
    assert res.ok and res.text.lower().startswith("i can")   # "I can only see..." / "I cannot see traffic..."

def test_device_traffic_asks_which_device_when_ambiguous(client):
    seeded_demo(client)
    res = registry.dispatch(ctx_for(role="viewer"), "device_traffic", {"target": "zz:zz:not-here"})
    assert res.ok is False and "no device" in res.text.lower()
```
- [ ] **Step 2: Run to fail** — `KeyError: 'top_talkers'`.
- [ ] **Step 3: Handlers** (append to `surfaces.py`):
```python
def _mode_preamble(mode: str) -> str:
    """The sentence every non-mirror answer opens with. The worst answer in
    this feature is a confident one built on rows that cannot support it."""
    if mode == "router":
        return ("I can only see bytes per device on this network -- the router reports them; there is no "
                "mirror port -- so I can say who is busiest but not what they are talking to. ")
    if mode == "none":
        return ("I cannot see traffic on this network: no mirror port is configured and the router does not "
                "report per-device counts. Ask me to help set up a mirror port if you want this. ")
    return ""

def _mb(n: int) -> str: return f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{n / 1e3:.0f} KB"

def h_top_talkers(ctx, hours: int = 1, network: str = ""):
    from .tools import ToolResult, _resolve_network
    from ..payloads import traffic
    net = None
    if network:
        net, problem = _resolve_network(ctx, network)
        if net is None: return ToolResult(ok=False, text=problem)
    try: hours = max(1, min(int(hours), 168))
    except (TypeError, ValueError): hours = 1
    o = traffic.overview(ctx.db, ctx.tenant, hours=hours, net=net)
    lines = [_mode_preamble(o.mode).strip()] if o.mode != "mirror" else []
    if o.mode == "none" or not o.top_talkers:
        lines.append(f"No traffic rows in the last {_plural(hours, 'hour')}.")
        return ToolResult(ok=True, data=o.model_dump(), text="\n".join(lines))
    lines.append(f"Busiest in the last {_plural(hours, 'hour')}:")
    for t in o.top_talkers[:6]:
        lines.append(f"- {t.name}: {_mb(t.bytes_in)} in, {_mb(t.bytes_out)} out" + (f", mostly {t.top_peer}" if t.top_peer and o.mode == "mirror" else ""))
    if o.mode == "mirror":
        if o.top_peers: lines.append("Top destinations: " + ", ".join(f"{p.peer} ({_mb(p.bytes_in + p.bytes_out)})" for p in o.top_peers[:5]))
        if o.new_peers: lines.append("New this window (nobody here had contacted them before): " + ", ".join(p.peer for p in o.new_peers[:5]))
        lines.append(f"{round(o.external_share * 100)}% of bytes left the LAN.")
    if o.truncated: lines.append("The sensor saw more flows than it could send, so these are the biggest, not all of them.")
    return ToolResult(ok=True, data=o.model_dump(), text="\n".join(lines))

def h_device_traffic(ctx, target: str, hours: int = 24):
    from .tools import ToolResult, _resolve_device
    from ..payloads import traffic
    dev, problem = _resolve_device(ctx, target)
    if dev is None: return ToolResult(ok=False, text=problem)
    try: hours = max(1, min(int(hours), 168))
    except (TypeError, ValueError): hours = 24
    d = traffic.device(ctx.db, ctx.tenant, dev.mac, hours=hours)
    shown = dev.label or dev.name or dev.ip or dev.mac
    pre = _mode_preamble(d.mode)
    if d.mode != "mirror":
        return ToolResult(ok=True, data=d.model_dump(), text=pre + f"{shown} moved {_mb(d.bytes_in)} in and {_mb(d.bytes_out)} out in the last {_plural(hours, 'hour')}.")
    lines = [f"{shown} in the last {_plural(hours, 'hour')}: {_mb(d.bytes_in)} in, {_mb(d.bytes_out)} out, {_plural(len(d.peers), 'peer')}."]
    for p in d.peers[:8]:
        lines.append(f"- {p.peer}:{p.peer_port}/{p.proto} -- {_mb(p.bytes_in + p.bytes_out)}")
    if len(d.peers) > 8: lines.append(f"...and {len(d.peers) - 8} more on the device's page.")
    return ToolResult(ok=True, data=d.model_dump(), text="\n".join(lines))

def h_server_peers(ctx, host: str, hours: int = 24):
    from .tools import ToolResult
    from ..payloads import traffic
    d = traffic.server(ctx.db, ctx.tenant, host)
    if d is None:
        return ToolResult(ok=False, text=f"I have no device row for the server called {host!r}; is it on a network the sensor watches?")
    return h_device_traffic(ctx, d.mac, hours=hours)
```
- [ ] **Step 4: Register** in `TOOLS` (all `tier=1, min_role="viewer"`, `route=""`, `effect="reads traffic rows already stored; changes nothing"`, `reversal="read-only"`): `top_talkers` (schema `hours` integer, `network` string), `device_traffic` (`target` required, `hours`), `server_peers` (`host` required, `hours`). Descriptions: name the questions each answers, per spec §4.
- [ ] **Step 5: Run** — `cd hub && .venv/bin/pytest tests/test_chat_coverage.py -q` → all pass.
- [ ] **Step 6: Commit**
```bash
git add hub/guardian_hub/chat/surfaces.py hub/guardian_hub/chat/tools.py hub/tests/test_chat_coverage.py
git commit -m "Ask Guardian can say who is talking to whom, and says plainly when it cannot see"
```

---

### Task 12: Web — Traffic on Topology and in the device drawer

**Files:**
- Modify: `web/src/lib/api.ts` (after `topologyPayload`), `web/src/pages/Topology.tsx` (after the `g-network-summaries` section), `web/src/components/DeviceDrawer.tsx`
- Create: `web/src/components/TrafficSection.tsx`, `web/src/components/TrafficBlock.tsx`
- Test: `web/tests/traffic.test.tsx`

- [ ] **Step 1: API helpers**
```ts
export function trafficOverview(hours = 1, network?: string | null): Promise<TrafficOverview> {
  return get(`/api/v1/traffic${query({ hours, network: network ?? undefined })}`);
}
export function deviceTraffic(mac: string, hours = 24, network?: string | null): Promise<DeviceTraffic> {
  return get(`/api/v1/devices/${encodeURIComponent(mac)}/traffic${query({ hours, network: network ?? undefined })}`);
}
```
- [ ] **Step 2: Failing test**
```tsx
// web/tests/traffic.test.tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TrafficSection } from "../src/components/TrafficSection";
import type { TrafficOverview } from "../src/types";

const base: TrafficOverview = { mode: "mirror", truncated: false, window_hours: 1, top_talkers: [], top_peers: [], new_peers: [], external_share: 0 };

describe("TrafficSection", () => {
  it("says what it cannot see in router mode instead of drawing empty columns", () => {
    render(<TrafficSection data={{ ...base, mode: "router" }} />);
    expect(screen.getByText(/only bytes per device/i)).toBeInTheDocument();
    expect(screen.queryByText(/top destinations/i)).toBeNull();
  });
  it("lists talkers biggest first and flags new peers", () => {
    render(<TrafficSection data={{ ...base,
      top_talkers: [{ mac: "a", name: "X-ray PC", ip: "1", bytes_in: 5e6, bytes_out: 1e5, top_peer: "vendor.example" }],
      new_peers: [{ peer: "odd.example", peer_ip: "9.9.9.9", peer_port: 443, proto: "tcp", bytes_in: 1, bytes_out: 1, first_seen: "", last_seen: "" }] }} />);
    expect(screen.getByText("X-ray PC")).toBeInTheDocument();
    expect(screen.getByText(/new/i).closest("section, div")).toHaveTextContent("odd.example");
  });
  it("renders nothing loud when there is nothing to say", () => {
    render(<TrafficSection data={{ ...base, mode: "none" }} />);
    expect(screen.getByText(/cannot see traffic/i)).toBeInTheDocument();
  });
});
```
- [ ] **Step 3: Run to fail** — `cd web && npm test -- traffic` → module not found.
- [ ] **Step 4: Components** — `TrafficSection` is presentational (`{ data: TrafficOverview }`), three states by `mode`; healthy recedes: the section header is `<h2>` "Traffic" and quiet rows use the existing `g-quiet`/muted token classes rather than new colour. `TrafficBlock` (`{ data: DeviceTraffic }`) for the drawer: totals line, up to 8 peers, the hourly strip drawn as 24 `<span>`s with height proportional to bytes (no chart library — the History tab draws with SVG; this is a strip, not a trend). In `Topology.tsx` add `const trafficQuery = useQuery({ queryKey: ["traffic", network], queryFn: () => trafficOverview(1, network) })` and render `<TrafficSection data={trafficQuery.data} />` under the summaries when data exists. In `DeviceDrawer.tsx` add `useQuery({ queryKey: ["deviceTraffic", mac, network], queryFn: () => deviceTraffic(mac, 24, network), enabled: !!mac })` and render `<TrafficBlock>`.
- [ ] **Step 5: Run** — `cd web && npm test && npm run lint && npm run build` → green. Update `topology.test.tsx`'s `vi.mock("../src/lib/api")` to also stub `trafficOverview: vi.fn()` returning `base` so existing tests keep passing.
- [ ] **Step 6: Commit**
```bash
git add web/src/lib/api.ts web/src/components/TrafficSection.tsx web/src/components/TrafficBlock.tsx web/src/pages/Topology.tsx web/src/components/DeviceDrawer.tsx web/tests/traffic.test.tsx web/tests/topology.test.tsx
git commit -m "Topology shows who is talking to whom, and a device's drawer shows its own traffic"
```

---

### Task 13: The wizard asks about the mirror port

**Files:**
- Modify: `web/src/pages/Onboarding.tsx:29-34,246-262,382-420`
- Test: `web/tests/onboarding.test.tsx`

- [ ] **Step 1: Failing test** — in `onboarding.test.tsx`, on the networks step: a select labelled "How does Guardian see this network's traffic?" defaults to "Not sure — skip for now"; choosing "A mirror/SPAN port feeds the sensor" makes `createNetwork` receive `traffic_mode: "mirror"`; skipping sends `"router"`.
- [ ] **Step 2: Run to fail.**
- [ ] **Step 3: Implement** — `NetworkDraft` gains `traffic_mode: "mirror" | "router"` (default `"router"`); each row gets:
```tsx
<label>
  <span>How does Guardian see this network's traffic?</span>
  <select value={row.traffic_mode} onChange={(e) => editDraft(i, { traffic_mode: e.target.value as "mirror" | "router" })}>
    <option value="router">Not sure — skip for now</option>
    <option value="mirror">A mirror/SPAN port on the switch feeds the sensor</option>
  </select>
</label>
<p className="hint">
  With a mirror port Guardian can say what each device talks to. Without one it can only see how much
  each device moves, if the router reports it. You can change this later in Settings → Networks.
</p>
```
and the mutation passes `traffic_mode: row.traffic_mode`. Nothing is auto-detected (spec §5).
- [ ] **Step 4: Run** — `cd web && npm test -- onboarding && npm run lint` → green.
- [ ] **Step 5: Commit**
```bash
git add web/src/pages/Onboarding.tsx web/tests/onboarding.test.tsx
git commit -m "Setup asks whether a mirror port feeds the sensor, and never guesses"
```

---

### Task 14: Demo tenant flows, deterministic and bounded

**Files:**
- Modify: `hub/guardian_hub/seed.py` (beside `_gen_dns`, line 351; and the payload dict at ~652), `hub/guardian_hub/demo_trickle.py`
- Test: `hub/tests/test_seed_demo*.py` (existing idempotence test must still add zero rows on a second run); add one case asserting `Flow` rows exist for the demo tenant and the primary network is `traffic_mode="mirror"`.

- [ ] **Step 1: `_gen_flows(last, rng, n=600)`** — same shape as `_gen_dns`: anchor on `last["ts"]`, minute-floored timestamps stepping back over 24 h, device IPs/MACs from `last["lan"]["devices"]`, peers drawn from a fixed list `DEMO_PEERS = [("www.google.com","142.250.1.1",443,"tcp"), ("update.microsoft.com","20.1.1.1",443,"tcp"), ("dentrix.example","203.0.113.10",443,"tcp"), ("pool.ntp.org","162.159.200.1",123,"udp")]`, bytes from `rng.randint`, `hints={}`, `source="mirror"`. Set the demo primary network's `traffic_mode = "mirror"` in `seed_demo`, and put `{"mode": "mirror", "error": "", "interface": "eth1", "flows_dropped": 0}` into `last["capture"]`. Add `"flows": flows` to the pushed payload.
- [ ] **Step 2: `demo_trickle`** — when it trickles a scan, append four flow rows for the same two demo peers (`www.google.com`, `dentrix.example`) at the trickled minute, in the tenant's offset (`_tenant_offset`). It never adds a new peer.
- [ ] **Step 3: Run** — `cd hub && .venv/bin/pytest tests/test_seed_demo*.py tests/test_demo_trickle*.py -q` → pass, including the second-run-adds-zero-rows assertion.
- [ ] **Step 4: Commit**
```bash
git add hub/guardian_hub/seed.py hub/guardian_hub/demo_trickle.py hub/tests/test_seed_demo*.py
git commit -m "The demo practice has traffic to look at, the same traffic every time it is rebuilt"
```

---

### Task 15: Docs

**Files:**
- Modify: `CLAUDE.md` (Where things live), `docs/superpowers/specs/2026-09-20-traffic-flows-design.md` (add a "Shipped" section)

- [ ] **Step 1:** Add to `CLAUDE.md` under Where things live:
```
- `hub/guardian_hub/payloads/traffic.py` — who talks to whom, from `Flow`
  rows the sensor's `flows.py` rolls up out of tcpdump minute files. Every
  response carries `mode` (`mirror|router|none`), read from the network's
  declared `traffic_mode` crossed with what the sensor reported in
  `raw["capture"]` — never inferred from the rows, because router rows and
  mirror rows look alike and only the mode says which questions they can
  answer. `flows` is popped out of the payload above the `m.Scan` insert
  (`Scan.raw` is served to any viewer). Retention copies DNS: raw a week,
  `FlowRollup` a year. Nothing from a packet body is ever stored; `hints`
  holds only TLS SNI, DHCP 12/60 and mDNS names.
```
- [ ] **Step 2:** Spec "Shipped" section: tool count, test files, what phase 2–4 build on.
- [ ] **Step 3: Commit**
```bash
git add CLAUDE.md docs/superpowers/specs/2026-09-20-traffic-flows-design.md
git commit -m "Docs: where traffic flows live and the rule about mode"
```

---

## Self-review

**Spec coverage.** §1 architecture → Tasks 1–4, 6–7, 9–10. §2 flow shape, MAC keying, LAN-to-LAN twice, `ts` string trap, FLOW_CAP/`flows_dropped`/`truncated`, no bodies → Tasks 1, 2, 4, 7, 9. §3 tables, indexes, dedup IN-query, retention, prune, no trend, demo → Tasks 6, 7, 8, 14. §4 payload functions, schemas + mirror, three tools with the limitation preamble, Topology section, drawer block → Tasks 9, 11, 12. §5 wizard question with skip, `capture:unavailable`/`capture:silent` human tier, per-row rejection, payload halving on 413 → Tasks 13, 5, 7, and the 413 halving is Task 4 Step 3. §5 privacy (`hints`/`peer_name` only, never in `Scan.raw`/alerts/reports) → Tasks 2, 7; alert messages in Task 5 carry only the interface name. §6 tests → each task carries its own; the router-mode chat test is Task 11; statement-count parity is Task 9/10.

**Placeholders.** None: every step has code or an exact command. Task 4's `router_fios.client_bytes` returns `[]` when the firmware exposes nothing and says so — that is the honest behaviour, not a stub.

**Type consistency.** `flows.rollup` rows → `sync._attribute_flows` (`ip` key) → ingest keys (`ts, mac, peer_ip, peer_port, proto, source`) → `m.Flow` columns → `traffic.py` readers → `TrafficPeer/TrafficTalker/TrafficOverview/DeviceTraffic` → `types.ts` — same names throughout. `capture.tick` returns `mode/error/interface/files`; runner adds `flows_dropped`; `traffic.mode_for` reads `raw["capture"]["mode"]` and `["flows_dropped"]`. `Network.traffic_mode` is written by `routers/networks.py` and read by `traffic.mode_for`.
