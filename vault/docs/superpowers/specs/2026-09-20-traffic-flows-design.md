# Traffic flows: Guardian sees who talks to whom

2026-09-20 · Phase 1 of the packet-capture work. Approach A, approved.

## The ask

> implement wireshark application as well to help the guardian bot track
> packets from each server and device and understand more about it to provide
> more actual real data

Four purposes were chosen: who talks to whom and how much; attacks on the
wire; raw captures for forensics; device fingerprinting. They share one
substrate and then diverge, so this spec is the substrate — **flows** — and
the tools that make the bot's answers real. Phases 2 (wire detections),
3 (fingerprinting) and 4 (incident `.pcap`, on the sensor, record on both
portals) each get a short spec on top of it.

## The constraint everything respects

The sensor is not in the packet path. It sits on an ordinary switch port,
finds devices by sweeping the LAN, and sees DNS only because it *is* the
resolver. On such a port it sees broadcast traffic and its own — nothing
else. So per-device traffic exists only where the traffic is brought to the
sensor:

- **mirror** — a managed switch mirrors the uplink (SPAN) to a second NIC on
  the sensor box. Full flows.
- **router** — no mirror; the router integration (`router_fios.py`, the one
  that exists) reports bytes per connected device. Coarse.
- **none** — neither. Guardian says so.

Every response from this feature carries `mode`, and no surface — page or
bot — may imply visibility it does not have. Inline placement (sensor as
gateway) was rejected: a watcher must not become a single point of failure.
ARP-spoofing tricks were rejected: they are the man-in-the-middle Guardian's
own check exists to catch.

## 1. Architecture

- **`sensor/guardian_sensor/capture.py`** owns `tcpdump`. It is a system
  package `install.py` installs (the sensor stays dependency-free Python;
  `pyproject` keeps `dependencies = []`). Started when
  `config["capture"]["interface"]` is set, with `-i <if> -s 128 -G 60 -w
  <DATA_DIR>/cap/%s.pcap`: headers plus the first bytes of payload, never
  bodies; one file per minute; at most 10 files kept. If tcpdump exits,
  `capture.py` restarts it with backoff and records `capture.error` in state
  so the hub can say "the mirror port stopped delivering" rather than "the
  network went quiet".
- **`sensor/guardian_sensor/flows.py`** parses each *completed* minute file
  with a stdlib pcap reader (link type Ethernet → IPv4/IPv6 → TCP/UDP/ICMP)
  and rolls packets into flow records. It also lifts three payload prefixes
  into `hints` now — TLS ClientHello SNI, DHCP options 12/60, mDNS names —
  so phase 3 is a hub change, not a sensor redeploy.
- **Ingest.** `flows` rides in the existing 5-minute payload beside
  `dns_queries` (`sync.py` builds it the way it builds `dns_queries`, with
  the same `_attribute_*` network attribution). `ingest.store_scan` **pops it
  out of the payload above the `m.Scan` insert**, where it already pops
  heartbeat tokens and for the same reason: `Scan.raw` is served to any
  viewer, and a minute of flows for forty devices is thousands of rows nobody
  should read there. Flows live only in their own table.
- **Read side.** `payloads/traffic.py` shapes the rows; `routers/read.py`
  serves `GET /traffic` and `GET /devices/{mac}/traffic`; the chat tools call
  the same payload functions. The Slack bot inherits them.
- **Router fallback.** `router_fios.py` gains a per-client byte read when the
  router exposes one, emitted in the same `flows` list with `source="router"`,
  `peer_ip=""`, `port=0`. Same table, same payload, coarser rows.

## 2. What a flow is

One record per **(minute, network, device MAC, peer IP, peer port, protocol,
source)**:

```
ts          minute floor, ISO, the sensor's own offset (see below)
net         network slug, attributed on the sensor as dns_queries are
mac         the device's MAC -- the key, not its IP
ip          the device's IP at that minute, for display
peer_ip     the other end
peer_port   the other end's port (0 for router rows / ICMP)
proto       tcp|udp|icmp|other
bytes_in    toward the device
bytes_out   from the device
pkts_in / pkts_out
peer_name   the DNS name that produced peer_ip, when the resolver saw it
hints       {"sni": [...], "dhcp": {...}, "mdns": [...]} -- capped, phase 3's input
source      mirror|router
```

**MAC, not IP.** A DHCP renewal must not split a device's history in two,
and on a mirror port the L2 header is the device's own. `ip` is carried for
display only. **The sensor keys its rows on the device's side of the
conversation**: a LAN-to-LAN flow appears twice, once per device, which is
what "what is the x-ray PC talking to" needs and a per-pair table does not.

**`ts` is a string in the sensor's offset**, like `DnsQuery.ts` and
`Alert.first_seen`. Any SQL `>=` on it is lexicographic; `traffic.py` uses
the lax-cutoff-then-Python-recheck pattern from `payloads/_util._lax_cutoff`,
per the `history.py` entry in `CLAUDE.md`.

**Bounded on both ends.** The sensor sends only minutes newer than its last
successful post; the hub dedups on the full key the way `store_scan` dedups
scans, so a retry after a failed post adds zero rows. Per payload the sensor
sends at most `FLOW_CAP = 5000` rows; beyond it it keeps the top rows by
bytes and reports `flows_dropped: N`. A saturated network says "I saw more
than I could send", never "quiet". `flows_dropped` is surfaced in `mode`'s
sibling field `truncated: true` on the payload.

**Not stored: anything from a packet body.** Snaplen 128 already makes that
nearly impossible; the parser additionally discards anything past the
headers it recognises and the three named prefixes. `hints.sni` is a
hostname, `hints.dhcp` is options 12 and 60 (hostname, vendor class),
`hints.mdns` is service names. Nothing else is read.

## 3. Storage and retention

- **`m.Flow`**, Alembic **`0029_flow`** (planned as `0028` here; landed as
  `0029` after a rebase past `0028_partner`). `id` autoincrement; `tenant_id`;
  `network_id` nullable, attributed at ingest from the device row as
  `DnsQuery.mac` is resolved today; the fields above. Indexes
  `(tenant_id, ts)` and `(tenant_id, mac, ts)` — the two queries every
  surface makes. Dedup key `(tenant_id, ts, mac, peer_ip, peer_port, proto,
  source)`; the hub checks it in one `IN` query per payload, never one per
  row (`tests/querycount.py` guards it).
- **Retention copies DNS exactly.** `retention.py` gains `flow_raw_days`
  (default 7) and `flow_rollup_days` (365, floored at raw + the same
  minimum extra DNS uses). **`m.FlowRollup`** per (day, network, mac,
  `peer_name` or `peer_ip`, `peer_port`, `proto`) with summed bytes and
  packets, written by the same job before raw rows age out, so "what did the
  x-ray PC talk to last month" survives the minute rows. `payloads/traffic.py`
  reads rollups for windows beyond `flow_raw_days`, as `dnsview.py` does.
- **`guardian prune`** deletes rollups past `flow_rollup_days`.
- **No trend is computed here.** `traffic.py` answers "now and recent". A
  traffic series on History, if wanted, is added to
  `payloads/history.build_charts` and nowhere else.
- **Demo tenant.** `seed-demo` gets a small, deterministic, scrubbed set of
  flows (seeded RNG, fixed timestamps, Northwind device names) so the demo
  Topology and the bot's traffic answers are not blank. `demo_trickle` does
  not invent new peers between refreshes; it repeats the same two.

## 4. What the bot and the pages get

`payloads/traffic.py` exposes:

- `overview(db, tenant, hours=1, net=None)` → `TrafficOverview`: `mode`,
  `truncated`, `window_hours`, `top_talkers` (device, bytes, top peer),
  `top_peers` (peer name/IP, bytes, device count), `new_peers` (peers no
  device on this network had contacted in the previous `flow_raw_days`),
  `external_share` (fraction of bytes leaving the LAN).
- `device(db, tenant, mac, hours=24)` → `DeviceTraffic`: `mode`, totals,
  `peers` (name, IP, port, proto, bytes each way, first/last seen in
  window), `hourly` (bytes per hour for the window — a strip, not a trend).
- `server(db, tenant, host)` — the same shape for a configured `ssh_hosts`
  entry, matched by its IP, so "is prod contacting anything odd" works
  without the customer knowing prod's MAC.

Both models are added to `schemas_dash.py` **in the same commit as their
`web/src/types.ts` mirror** — `test_contracts.py` fails otherwise, as it did
for three commits earlier today when the two were split.

Chat tools, all **tier 1 / viewer** (the dashboard shows all of this to a
viewer), registered in `chat/tools.py` with handlers in `chat/surfaces.py`
under the module's one rule — call the payload function, format the answer,
own no logic:

| Tool | Answers |
|---|---|
| `top_talkers` | "which device is eating the bandwidth", "what is busiest right now" |
| `device_traffic` | "what is the x-ray PC talking to", "has the printer contacted anything new" |
| `server_peers` | "is prod talking to anything it shouldn't", "who connects to the NAS" |

Each answer opens with the mode when it is not `mirror` — *"I can only see
bytes per device on this network (the router reports them; there is no
mirror port), so I cannot tell you what the printer is talking to"* — because
the worst answer here is a confident one built on data that is not there.

Pages: a **Traffic** section on the Topology tab (top talkers, top peers,
new peers) and a **Traffic** block on the device drawer. Both read the two
routes above. Healthy recedes: a device with nothing unusual is a quiet row.

## 5. Setup and failure handling

- **Wizard.** The network step gains "Does this network have a mirror
  port?" with a one-paragraph explanation and a "not sure — skip" that maps
  to `router` mode. Nothing is auto-detected: a NIC receiving traffic that is
  not addressed to it is the only real signal, and a wrong guess would show
  a customer their neighbour's traffic. `setup_status` (chat) reports the
  mode per network.
- **tcpdump missing** → `capture.error = "tcpdump not installed"`, mode
  `none`, alert `capture:unavailable` (INFO, `human` tier — a person installs
  a package). **Interface configured but silent for 10 minutes** → alert
  `capture:silent` (WARN, `human`): "the mirror port stopped delivering; check
  the switch". Both keys go into `remedy` and `fix.FIX_ALERT_KEY` as
  human-only, so `test_remedy` stays green.
- **Hub refuses malformed flows** per row, not per payload: a row with a bad
  MAC or negative bytes is dropped and counted in `flows_rejected`; the rest
  of the scan is stored. One bad row must not cost a scan.
- **Payload too large** for the ingest limit → the sensor halves `FLOW_CAP`
  for that post and reports `truncated`. It never drops the scan to save the
  flows.
- **Privacy.** `hints` and `peer_name` are the only strings from the wire
  that reach the hub. They appear in `Flow` rows and the payloads above —
  never in `Scan.raw`, never in an alert message verbatim, never in a report
  without the same scrubbing `demoscrub` applies.

## 6. Testing

Sensor (`sensor/tests`):
- `test_flows.py` — a checked-in 200-packet pcap fixture (synthetic, generated
  by a script in `tests/`, no real capture) parses to a known flow table;
  SNI/DHCP/mDNS hints lifted; nothing past the recognised headers retained
  (assert the parser never reads beyond its computed header end); `FLOW_CAP`
  keeps top-by-bytes and reports the drop count.
- `test_capture.py` — restart with backoff on exit; `capture.error` set when
  the binary is missing; the 10-file cap holds.

Hub (`hub/tests`):
- `test_flow_ingest.py` — rows land in `Flow`, not in `Scan.raw`; dedup on
  retry adds zero rows; one malformed row is dropped and the scan still
  stores; `network_id` attributed like `DnsQuery.mac`.
- `test_traffic_payload.py` — `mode` is `mirror|router|none` and never
  implies more than the rows support; `new_peers` is computed against
  `flow_raw_days`; the lexicographic-`ts` trap is covered with a row in a
  different offset; **statement counts** at two device counts must match
  (`querycount.py`), never a ceiling.
- `test_flow_retention.py` — rollups written before raw rows age out;
  windows past `flow_raw_days` answered from rollups.
- `test_chat_coverage.py` — the three tools are viewer/tier 1; a `router`-mode
  answer opens with the limitation sentence; `test_every_client_write_route`
  is unaffected (these are reads).
- `test_contracts.py` — unchanged, and green in every commit.
- `test_remedy.py` — the two new alert keys present on both sides.

No wall-clock assertions anywhere in this work.

## Out of scope for phase 1

Wire detections (phase 2), fingerprinting into `kind`/`name` (phase 3),
incident `.pcap` with the both-portals record (phase 4), IPv6 flow labels
beyond parsing, and any owner-console traffic view.

## Shipped

15 tasks, ~24 commits on `traffic-flows`. New test files: sensor
`test_flows.py`, `test_capture.py`, `test_flows_sync.py`,
`test_capture_alerts.py`; hub `test_flow_ingest.py`, `test_flow_retention.py`,
`test_traffic_payload.py`, plus additions to `test_chat_coverage.py`,
`test_contracts.py`, `test_seed.py` and `test_demo_trickle.py`; web
`traffic.test.tsx`, plus additions to `onboarding.test.tsx`.

Three chat tools (`top_talkers`, `device_traffic`, `server_peers`); two
routes (`GET /traffic`, `GET /devices/{mac}/traffic`); Alembic `0029_flow`.

**Limitation, stated honestly.** `router` mode has no producer yet:
`router_fios.client_bytes()` returns `[]` because the Fios page the sensor
reads exposes no per-client byte counters, and nothing builds `res["router"]`
in `run_once`. So a network whose owner chose "Not sure — skip for now" reads
as `mode: none` until a router integration supplies rows; `traffic.mode_for`
deliberately defers to the sensor's report for declared mirror OR router for
exactly this reason — the declared intent alone is not enough to claim
visibility that doesn't exist yet.

**Two design defects found in review and fixed.** Both were in this spec's
own reference description, not just in a first draft of the code:

- The tcpdump silence check originally read the mtime of the file currently
  being written. `-G 60` rotation touches that file every minute regardless
  of whether any packets arrived, so a genuinely silent mirror port would
  never be detected — the mtime always looked fresh. "Silent" now means every
  *recent, completed* minute file is exactly the 24-byte pcap global header
  and nothing else.
- `new_peers` originally compared the current window against a "seen before"
  window computed with a single SQL cutoff, which left a one-day hole at the
  boundary (a peer contacted only in that gap read as "new" when it was not,
  or vice versa depending on rounding). `_seen_before` now takes lax SQL
  bounds on both ends and re-checks the exact boundary in Python, the same
  lax-cutoff-then-recheck pattern `traffic.py` already uses for the
  lexicographic `ts` string.

**What phases 2-4 build on.** `Flow.hints` (SNI/DHCP/mDNS) is already
collected for phase 3's fingerprinting, so that phase is a hub change, not a
sensor redeploy. The flow stream itself is the input for phase 2's wire
detections. Phase 4's incident `.pcap` reuses `capture.py`'s ring of minute
files rather than adding a second capture path.
