import type { DeviceTraffic } from "../types";

export type TrafficBlockProps = {
  data: DeviceTraffic;
};

/** KB below 1 MB, one decimal MB above. Deliberately not shared with
 *  `TrafficSection`'s copy of the same formula -- two components, no
 *  dependency, no barrel just to save four lines. */
function fmtBytes(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)} MB`;
  return `${Math.round(n / 1000)} KB`;
}

/** The drawer's own-traffic panel: a totals line, the busiest peers (mirror
 *  mode only -- router/none never populate `peers`, since a router counter
 *  cannot say who the other end was), and an hourly strip. The strip is
 *  exactly `data.hourly.length` (== `window_hours`) plain `<span>`s scaled to
 *  the busiest hour in the window -- a glance at shape, not a chart the
 *  History tab already owns. */
export function TrafficBlock({ data }: TrafficBlockProps) {
  if (data.mode === "none") {
    return (
      <section aria-label="Device traffic" className="g-traffic-block">
        <h2>Traffic</h2>
        <p className="hint">
          Vault cannot see traffic on this network: no mirror port is configured and the
          router does not report per-device counts.
        </p>
      </section>
    );
  }

  const max = Math.max(1, ...data.hourly);
  const totalsQuiet = data.bytes_in + data.bytes_out === 0;

  return (
    <section aria-label="Device traffic" className="g-traffic-block">
      <h2>Traffic</h2>
      <p className={totalsQuiet ? "g-traffic-totals g-traffic-quiet" : "g-traffic-totals"}>
        {fmtBytes(data.bytes_in)} in / {fmtBytes(data.bytes_out)} out, last {data.window_hours}h
      </p>

      <div className="g-traffic-strip" role="img" aria-label={`Hourly bytes, last ${data.window_hours} hours`}>
        {data.hourly.map((bytes, i) => (
          <span
            key={i}
            className="g-traffic-strip-bar"
            style={{ height: `${Math.max(2, Math.round((bytes / max) * 100))}%` }}
          />
        ))}
      </div>

      {data.mode === "host" && (
        <p className="hint" data-testid="traffic-host-only">
          Vault sees only the sensor machine's own traffic on this network, so for any other
          device these figures are just what it exchanged with the sensor.
        </p>
      )}
      {data.mode === "router" ? (
        <p className="hint">
          Vault can see only bytes per device on this network — the router reports them;
          there is no mirror port — so it can say who is busiest but not what they talk to.
        </p>
      ) : data.peers.length > 0 ? (
        <ul className="g-traffic-peers">
          {data.peers.slice(0, 8).map((p) => (
            <li key={`${p.peer}:${p.peer_port}`}>
              <span className="g-traffic-name">{p.peer || p.peer_ip}</span>
              <span className="hint">
                {p.proto}/{p.peer_port} · {fmtBytes(p.bytes_in + p.bytes_out)}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="hint g-traffic-quiet">No peers recorded this window.</p>
      )}
    </section>
  );
}

export default TrafficBlock;
