import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Drawer, Page, PageHeader } from "../design";
import { DataNotice } from "../components/DataNotice";
import { trafficConnections } from "../lib/api";
import { clock } from "../lib/format";
import { useLiveFeed } from "../lib/live";
import { machineClass } from "../lib/machine";
import type { ConnStreamRow, ConnectionDest, ConnectionDevice } from "../types";
import "../styles/connections.css";

/** How many raw rows the expert feed keeps. The grouped view above it is the
 *  reading; this is the evidence, and nobody scrolls past a thousand. */
const BUFFER_CAP = 1000;
const WINDOW_SIZE = 300;
/** A device's bar shows its biggest destinations as their own segments and
 *  folds the rest into one, so the bar stays legible on a busy laptop. */
const SEGMENTS = 4;

function fmtBytes(n: number): string {
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)} GB`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)} MB`;
  if (n >= 1_000) return `${Math.round(n / 1000)} KB`;
  return `${n} B`;
}

function toRow(data: Record<string, unknown>): ConnStreamRow {
  const str = (k: string) => (typeof data[k] === "string" ? (data[k] as string) : "");
  const num = (k: string) => (typeof data[k] === "number" ? (data[k] as number) : 0);
  return {
    ts: str("ts"), mac: str("mac"), ip: str("ip"), device: str("device"),
    peer: str("peer"), peer_ip: str("peer_ip"), peer_port: num("peer_port"), proto: str("proto"),
    bytes_in: num("bytes_in"), bytes_out: num("bytes_out"),
  };
}

const total = (x: { bytes_in: number; bytes_out: number }) => x.bytes_in + x.bytes_out;

/** One destination seen from the destination side: which devices used it. */
type DestGroup = {
  key: string;
  dest: ConnectionDest;
  bytes: number;
  devices: { name: string; bytes: number }[];
};

function byDestination(devices: ConnectionDevice[]): DestGroup[] {
  const groups = new Map<string, DestGroup>();
  for (const dev of devices) {
    for (const d of dev.destinations) {
      const key = `${d.name}|${d.port_meaning}`;
      const g = groups.get(key) ?? { key, dest: d, bytes: 0, devices: [] };
      g.bytes += total(d);
      g.devices.push({ name: dev.name, bytes: total(d) });
      // A destination is new if it was new for anyone.
      if (d.is_new && !g.dest.is_new) g.dest = { ...g.dest, is_new: true };
      groups.set(key, g);
    }
  }
  return [...groups.values()].sort((a, b) => b.bytes - a.bytes);
}

/** "Anthropic" and, when Vault knows it, what that is. An unrecognised name
 *  is shown as the hostname or address itself, never dressed up. */
function DestName({ d }: { d: ConnectionDest }) {
  const named = d.sure || d.local;
  return (
    <span className="g-cx-dest-name">
      <span className={named ? "g-cx-name" : `g-cx-name g-cx-raw ${machineClass(d.name)}`}>{d.name}</span>
      {d.is_new && <span className="g-cx-new" title="Nothing on this network had connected here before">New</span>}
    </span>
  );
}

/** The sentence the drawer leads with: the whole connection, in words. */
function sentence(device: string, d: ConnectionDest): string {
  const who = d.what ? `${d.name} (${d.what})` : d.name;
  const kind = d.port_meaning.toLowerCase();
  const moved = `${fmtBytes(d.bytes_in)} came back and ${fmtBytes(d.bytes_out)} went out`;
  return `${device} connected to ${who} using ${kind}. ${moved}.`;
}

/** Share-of-traffic bar: the device's biggest destinations as segments. */
function ShareBar({ device }: { device: ConnectionDevice }) {
  const all = total(device) || 1;
  const top = device.destinations.slice(0, SEGMENTS);
  const rest = device.destinations.slice(SEGMENTS).reduce((n, d) => n + total(d), 0);
  return (
    <span className="g-cx-share" role="img"
          aria-label={`Most of ${device.name}'s traffic: ${top.map((d) => d.name).join(", ")}`}>
      {top.map((d, i) => (
        <span key={`${d.name}|${d.port}|${i}`} className="g-cx-seg" data-rank={i}
              style={{ width: `${Math.max(2, (total(d) / all) * 100)}%` }}
              title={`${d.name}: ${fmtBytes(total(d))}`} />
      ))}
      {rest > 0 && (
        <span className="g-cx-seg" data-rank="rest" style={{ width: `${(rest / all) * 100}%` }}
              title={`Everything else: ${fmtBytes(rest)}`} />
      )}
    </span>
  );
}

/** What this network's capture can see, said before the data rather than
 *  implied by an empty list. Same four modes as the Traffic panel. */
function ModeNotice({ mode, why }: { mode: string; why: string }) {
  if (mode === "mirror") return null;
  if (mode === "host") {
    return (
      <p className="g-cx-note" data-testid="conn-mode">
        You are seeing the sensor machine's own connections only. It is not on a mirror port,
        so other devices on this network do not appear here.
      </p>
    );
  }
  if (mode === "router") {
    return (
      <p className="g-cx-note" data-testid="conn-mode">
        This network reports only how busy each device is, not who it connects to, so there
        are no connections to show.
      </p>
    );
  }
  return (
    <p className="g-cx-note" data-testid="conn-mode">
      {why || "Vault is not watching traffic on this network."}{" "}
      <Link to="/settings/networks">Turn it on for a network you own</Link>.
    </p>
  );
}

type Opened = { device: string; dest: ConnectionDest } | null;

/** Column names, once per list. The page's readers asked what they were
 *  looking at; four named columns answer that before any row is read. */
function DestHead() {
  return (
    <li className="g-cx-head" aria-hidden="true">
      <span>Connected to</span><span>What it is</span><span>Kind of traffic</span><span>Data</span>
    </li>
  );
}

/** Live connections: what each device on the network is talking to, now.
 *
 *  Grouped by device (or by destination), each destination named in words
 *  and marked New when nothing here had contacted it before. The raw feed
 *  it is built from sits underneath, collapsed, for whoever wants the rows.
 *  Connections, never packets: Vault keeps nothing from a packet body. */
export function Connections() {
  const qc = useQueryClient();
  const live = useLiveFeed({ subscribe: ["flow"] });
  const [minutes, setMinutes] = useState(15);
  const [view, setView] = useState<"device" | "dest">("device");
  const [opened, setOpened] = useState<Opened>(null);
  const [buffer, setBuffer] = useState<ConnStreamRow[]>([]);
  const [paused, setPaused] = useState(false);
  const [frozen, setFrozen] = useState<ConnStreamRow[] | null>(null);
  const [filter, setFilter] = useState("");

  const grouped = useQuery({
    queryKey: ["connections", minutes],
    queryFn: () => trafficConnections(minutes),
    refetchInterval: 30_000,
  });

  // A new flow means the grouped view is out of date, but refetching on every
  // frame would be a request per connection. At most one refresh every 10 s.
  const lastRefresh = useRef(0);
  useEffect(() => {
    return live.on("flow", (event) => {
      setBuffer((prev) => {
        const next = [...prev, toRow(event.data)];
        return next.length > BUFFER_CAP ? next.slice(next.length - BUFFER_CAP) : next;
      });
      const now = Date.now();
      if (now - lastRefresh.current > 10_000) {
        lastRefresh.current = now;
        void qc.invalidateQueries({ queryKey: ["connections", minutes] });
      }
    });
  }, [live, qc, minutes]);

  const data = grouped.data;
  const devices = useMemo(() => data?.devices ?? [], [data]);
  const dests = useMemo(() => byDestination(devices), [devices]);
  const mode = data?.mode ?? "none";

  function togglePause() {
    setFrozen(paused ? null : buffer);
    setPaused(!paused);
  }
  const visible = paused ? frozen ?? buffer : buffer;
  const needle = filter.trim().toLowerCase();
  const rawRows = (needle === "" ? visible : visible.filter((r) =>
    [r.device, r.ip, r.mac, r.peer, r.peer_ip, String(r.peer_port), r.proto]
      .some((v) => v.toLowerCase().includes(needle)))).slice(-WINDOW_SIZE).slice().reverse();

  const summary = devices.length === 0
    ? null
    : `${devices.length} ${devices.length === 1 ? "device" : "devices"} connected to ${data?.destinations_total ?? 0} ${
        data?.destinations_total === 1 ? "place" : "places"} in the last ${minutes === 60 ? "hour" : `${minutes} minutes`}.`;

  return (
    <Page>
      <PageHeader
        eyebrow="Network · live"
        title="Live connections"
        sub="What the devices on your network are connecting to on the internet, as it happens."
      />
      <DataNotice query={grouped} />

      <section className="g-cx" aria-label="Connections">
        <header className="g-cx-bar">
          <p className="g-cx-summary" data-testid="conn-summary">
            <span className="g-cx-live" data-state={live.status} aria-hidden="true" />
            {summary ?? (grouped.isLoading ? "Loading…" : "No connections in this window.")}
            {data && data.new_total > 0 && (
              <> <span className="g-cx-summary-new">{data.new_total} {data.new_total === 1 ? "is" : "are"} new.</span></>
            )}
          </p>
          <div className="g-cx-controls">
            <div className="g-cx-seg-ctl" role="group" aria-label="Group by">
              <button type="button" aria-pressed={view === "device"} onClick={() => setView("device")}>By device</button>
              <button type="button" aria-pressed={view === "dest"} onClick={() => setView("dest")}>By destination</button>
            </div>
            <div className="g-cx-seg-ctl" role="group" aria-label="Time window">
              <button type="button" aria-pressed={minutes === 15} onClick={() => setMinutes(15)}>15 min</button>
              <button type="button" aria-pressed={minutes === 60} onClick={() => setMinutes(60)}>1 hour</button>
            </div>
          </div>
        </header>

        <ModeNotice mode={mode} why={data?.why_none ?? ""} />
        {devices.length > 0 && view === "device" && (
          <p className="g-cx-note">
            Each device's bar shows where its traffic went, biggest first. Each row is one place
            it connected to; select a row for the full story.
          </p>
        )}

        {view === "device" && devices.map((dev) => (
          <article key={dev.mac} className="g-cx-device" data-testid="conn-device">
            <header className="g-cx-device-head">
              <h2 className="g-cx-device-name">{dev.name}</h2>
              <span className="g-cx-device-meta mono">{dev.ip}</span>
              <span className="g-cx-device-total">{fmtBytes(total(dev))}</span>
              <ShareBar device={dev} />
            </header>
            <ul className="g-cx-dests">
              <DestHead />
              {dev.destinations.map((d, i) => (
                <li key={`${d.name}|${d.port}|${d.proto}|${i}`}>
                  <button type="button" className="g-cx-dest" data-testid="conn-row"
                          onClick={() => setOpened({ device: dev.name, dest: d })}>
                    <DestName d={d} />
                    <span className="g-cx-what">{d.what}</span>
                    <span className="g-cx-kind">{d.port_meaning}</span>
                    <span className="g-cx-bytes">{fmtBytes(total(d))}</span>
                  </button>
                </li>
              ))}
            </ul>
          </article>
        ))}

        {view === "dest" && (
          <ul className="g-cx-dests g-cx-by-dest">
            <DestHead />
            {dests.map((g) => (
              <li key={g.key}>
                <button type="button" className="g-cx-dest" data-testid="conn-row"
                        onClick={() => setOpened({ device: g.devices.map((x) => x.name).join(", "), dest: g.dest })}>
                  <span className="g-cx-dest-cell">
                    <DestName d={g.dest} />
                    <span className="g-cx-who">from {g.devices.map((x) => x.name).join(", ")}</span>
                  </span>
                  <span className="g-cx-what">{g.dest.what}</span>
                  <span className="g-cx-kind">{g.dest.port_meaning}</span>
                  <span className="g-cx-bytes">{fmtBytes(g.bytes)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}

        {/* The evidence under the reading: every connection as the sensor
            reported it. Collapsed, because the grouped view above answers the
            question most people came with. */}
        <details className="g-cx-raw">
          <summary>Every connection as it arrives</summary>
          <div className="g-dns-stream">
            <div className="g-dns-stream-controls">
              <label className="g-dns-search">
                <span>Search</span>
                <input type="search" aria-label="Search connections" value={filter}
                       onChange={(e) => setFilter(e.target.value)} placeholder="device, destination or port…" />
              </label>
              <button type="button" aria-pressed={paused} onClick={togglePause}>{paused ? "Resume" : "Pause"}</button>
            </div>
            {rawRows.length === 0 ? (
              <p className="g-asset-empty">No connections seen yet.</p>
            ) : (
              <ul className="g-dns-stream-rows">
                {rawRows.map((r) => (
                  <li key={`${r.ts}|${r.mac}|${r.peer_ip}|${r.peer_port}|${r.proto}`}
                      className="g-dns-stream-row" data-testid="conn-raw-row">
                    <span className="g-dns-stream-time mono">{clock(r.ts)}</span>
                    <span className="g-dns-client-badge">
                      {(() => { const v = r.device || r.ip || r.mac;
                                return <span className={machineClass(v)}>{v}</span>; })()}
                    </span>
                    <span className="g-dns-stream-name mono" title={r.peer_ip}>→ {r.peer || r.peer_ip}</span>
                    <span className="g-dns-stream-qtype">{r.proto}/{r.peer_port}</span>
                    <span className="g-dns-stream-ms mono">{fmtBytes(r.bytes_in)} in · {fmtBytes(r.bytes_out)} out</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </details>
      </section>

      <Drawer open={opened !== null} onClose={() => setOpened(null)}
              title={opened?.dest.name ?? ""} subtitle={opened?.dest.what || opened?.dest.port_meaning}>
        {opened && (
          <div className="g-cx-drawer">
            <p className="g-cx-sentence" data-testid="conn-sentence">{sentence(opened.device, opened.dest)}</p>
            {opened.dest.is_new && (
              <p className="g-cx-note">
                Nothing on this network had connected here before this window. That is often a new
                app or an update. If you do not recognise it, ask Vault about it.
              </p>
            )}
            {!opened.dest.sure && !opened.dest.local && (
              <p className="g-cx-note">
                Vault cannot tell whose this is from the address alone, so it shows the address
                rather than guess.
              </p>
            )}
            <dl className="g-dns-why">
              {opened.dest.host && (<><dt>Name the device asked for</dt><dd className="mono">{opened.dest.host}</dd></>)}
              <dt>Address</dt><dd className="mono">{opened.dest.peer_ip}</dd>
              <dt>Kind of traffic</dt><dd>{opened.dest.port_meaning} <span className="mono">({opened.dest.proto}/{opened.dest.port})</span></dd>
              <dt>Data</dt><dd>{fmtBytes(opened.dest.bytes_in)} received · {fmtBytes(opened.dest.bytes_out)} sent</dd>
              <dt>Seen</dt><dd>{clock(opened.dest.first_seen)}{opened.dest.last_seen !== opened.dest.first_seen ? ` to ${clock(opened.dest.last_seen)}` : ""}</dd>
            </dl>
          </div>
        )}
      </Drawer>
    </Page>
  );
}

export default Connections;
