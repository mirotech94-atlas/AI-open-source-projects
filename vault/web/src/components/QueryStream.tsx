import { machineClass } from "../lib/machine";
import { useState } from "react";
import { Badge } from "../design";
import { clock, ms as fmtMs } from "../lib/format";
import type { DnsStreamRow } from "../types";

export type QueryStreamProps = {
  /** The page's ever-growing, 1 000-row-capped buffer — it keeps accumulating
   *  regardless of `paused`. This component decides what to *show*. */
  rows: DnsStreamRow[];
  paused: boolean;
  onPause: (paused: boolean) => void;
  filter: string;
  onFilter: (filter: string) => void;
  /** Fired when a client badge is clicked, for the per-device DNS profile
   *  drawer. Rows with no `mac` (the hub could not resolve one) render the
   *  badge but it does nothing. */
  onSelectClient?: (row: DnsStreamRow) => void;
  /** Item 36: the stream said THAT a name was blocked and never why. */
  onSelectName?: (row: DnsStreamRow) => void;
};

const WINDOW_SIZE = 300;

/** The live DNS query stream: time, a client badge (device label, falling
 *  back to the IP), domain, qtype, a blocked pill naming the list, and
 *  latency. Windowed to the newest 300 rows. Pausing freezes the *view* on
 *  whatever `rows` looked like the instant pause was pressed — new frames
 *  keep landing in the caller's buffer underneath, so resuming picks up
 *  wherever the buffer has gotten to, not where the view was frozen. */
export function QueryStream({ rows, paused, onPause, filter, onFilter, onSelectClient, onSelectName }: QueryStreamProps) {
  const [frozen, setFrozen] = useState<DnsStreamRow[] | null>(null);
  const [lastPaused, setLastPaused] = useState(paused);

  // Snapshot exactly on the false -> true transition, and clear exactly on
  // true -> false — adjusted during render (React's documented pattern for
  // deriving state from a prop change) rather than in an effect, so pausing
  // takes effect in the same render instead of one render later.
  if (paused !== lastPaused) {
    setLastPaused(paused);
    setFrozen(paused ? rows : null);
  }

  const visible = paused ? frozen ?? rows : rows;

  const needle = filter.trim().toLowerCase();
  const filtered =
    needle === ""
      ? visible
      : visible.filter(
          (r) =>
            r.name.toLowerCase().includes(needle) ||
            r.client.toLowerCase().includes(needle) ||
            r.device.toLowerCase().includes(needle),
        );

  // Newest 300, newest first — a live feed reads top-down.
  const windowed = filtered.slice(-WINDOW_SIZE).slice().reverse();

  return (
    <div className="g-dns-stream">
      <div className="g-dns-stream-controls">
        <label className="g-dns-search">
          <span>Search</span>
          <input
            type="search"
            aria-label="Search the query stream"
            value={filter}
            onChange={(e) => onFilter(e.target.value)}
            placeholder="domain, client or device…"
          />
        </label>
        <button type="button" aria-pressed={paused} onClick={() => onPause(!paused)}>
          {paused ? "Resume" : "Pause"}
        </button>
      </div>

      {windowed.length === 0 ? (
        <p className="g-asset-empty">No queries seen yet.</p>
      ) : (
        <ul className="g-dns-stream-rows">
          {windowed.map((row) => (
            // Keyed on the row's own identity (device + name + qtype + ms),
            // never on array position: a new query arrives at index 0 and
            // pushes every older row down one slot, so an index-based key
            // would read as "every row changed" on every single frame and
            // force React to tear down and rebuild the whole list — the
            // exact whole-list repaint (and the scroll-stealing that comes
            // with it) this stream must not do. With a stable key only the
            // one truly new `<li>` mounts; the rest are left untouched.
            <li
              key={`${row.ts}|${row.mac ?? row.client}|${row.name}|${row.qtype}|${row.ms}`}
              className="g-dns-stream-row"
              data-testid="dns-row"
            >
              <span className="g-dns-stream-time mono">{clock(row.ts)}</span>
              <button
                type="button"
                className="g-dns-client-badge"
                disabled={!onSelectClient || !row.mac}
                title={row.mac ? undefined : "No device resolved for this client"}
                onClick={onSelectClient ? () => onSelectClient(row) : undefined}
              >
                {/* The device's name when Vault resolved one, the raw
                    client address when it did not — and an address is read
                    character by character. */}
                {(() => { const v = row.device || row.client;
                          return <span className={machineClass(v)}>{v}</span>; })()}
              </button>
              {/* A blocked lookup names a domain, and a domain is an
                  untrusted string off the network: it can be arbitrarily
                  long or odd-looking. It renders as plain text (React
                  escapes it) inside a monospace, ellipsis-truncated cell so
                  it can never stretch the row or be mistaken for a UI
                  label, and `title` keeps the full value one hover away. */}
              <button
                type="button"
                className="g-dns-stream-name mono"
                title={onSelectName ? `Why was ${row.name} ${row.blocked ? "blocked" : "allowed"}?` : row.name}
                disabled={!onSelectName}
                onClick={onSelectName ? () => onSelectName(row) : undefined}
              >
                {row.name}
              </button>
              <span className="g-dns-stream-qtype">{row.qtype}</span>
              {row.blocked && <Badge tone="alert">{row.list ? `blocked · ${row.list}` : "blocked"}</Badge>}
              <span className="g-dns-stream-ms mono">{fmtMs(row.ms)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default QueryStream;
