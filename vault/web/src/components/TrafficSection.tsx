import type { TrafficOverview } from "../types";
import { Link } from "react-router-dom";

export type TrafficSectionProps = {
  data: TrafficOverview;
};

/** KB below 1 MB, one decimal MB above -- no dependency, this is the only
 *  place on Topology that turns a byte count into words. `DeviceTraffic`'s
 *  own block in the drawer formats independently (same shape, different
 *  file) rather than sharing an import across two components that would
 *  otherwise need a barrel just for this. */
function fmtBytes(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)} MB`;
  return `${Math.round(n / 1000)} KB`;
}

/** The sensor caps flows at 5000 per payload (Task 9's ingest side) and sets
 *  `truncated` precisely so a saturated network never reads as a quiet one --
 *  a busy hour that silently drops to "top 5000" would otherwise look like
 *  ordinary top-N ranking. Shown above the lists, in every mode: even
 *  `router`'s talker ranking is drawn from the same capped flow set, and
 *  `none` still says nothing further is coming this call either. */
function TruncatedNotice({ truncated }: { truncated: boolean }) {
  if (!truncated) return null;
  return (
    <p className="hint">
      The sensor saw more flows than it could send, so these are the biggest, not all of them.
    </p>
  );
}

/** Topology's "who is talking to whom" panel. What it can show depends on
 *  what the sensor could see, and Vault says so rather than drawing empty
 *  destination/new-peer columns the two lesser modes can never fill:
 *  - `mirror` -- a SPAN/mirror port saw full flows, so destinations and new
 *    peers are real.
 *  - `router` -- only the router's own per-device byte counters exist, so
 *    Vault can rank talkers but has no idea what they talked to.
 *  - `none` -- neither exists; there is nothing to draw at all.
 *  Getting this wrong reads as a bug ("why is the destinations list always
 *  empty?") instead of a hardware limit, which is why the copy is explicit
 *  and the columns are omitted rather than rendered blank. */
export function TrafficSection({ data }: TrafficSectionProps) {
  if (data.mode === "none") {
    return (
      <section aria-label="Traffic" className="g-traffic-section">
        <h2>Traffic</h2>
        <TruncatedNotice truncated={data.truncated} />
        {/* Item 20: WHY there is nothing, in the customer's words. It used
            to say "Vault cannot see traffic" whatever the reason — which
            reads as a hardware limit even when the real answer is that
            nobody has switched it on. Watching a network's traffic is
            opt-in per network, so "off" is a normal state and the page has
            to be able to say so. */}
        <p className="hint" data-testid="traffic-why">
          {data.why_none
            || "Vault cannot see traffic on this network: no mirror port is configured and the router does not report per-device counts."}
        </p>
        {data.why_none && (
          <p className="hint">
            <Link to="/settings/networks">Turn it on for a network you own</Link> — Vault
            reads packet headers on the interface you name, never message bodies, and only
            on the networks you ask it to watch.
          </p>
        )}
      </section>
    );
  }

  if (data.mode === "router") {
    return (
      <section aria-label="Traffic" className="g-traffic-section">
        <h2>Traffic</h2>
        <TruncatedNotice truncated={data.truncated} />
        <p className="hint">
          Vault can see only bytes per device on this network — the router reports them;
          there is no mirror port — so it can say who is busiest but not what they talk to.
        </p>
        {data.top_talkers.length > 0 && (
          <ul className="g-traffic-talkers">
            {data.top_talkers.map((t) => (
              <li key={t.mac} className={t.bytes_in + t.bytes_out > 0 ? "" : "g-traffic-quiet"}>
                <span className="g-traffic-name">{t.name}</span>
                <span className="g-traffic-bytes">
                  {fmtBytes(t.bytes_in)} in / {fmtBytes(t.bytes_out)} out
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    );
  }

  // mirror: the full picture -- talkers, the destinations everyone actually
  // reached, and any peer nobody on this network has ever reached before.
  // host: the same rows, but only the sensor machine's own -- a sensor on
  // Wi-Fi, or on a port nobody mirrored. Drawn the same way because the rows
  // are real, and said up front because one machine's destinations under a
  // plain "Top destinations" heading read as the whole network's.
  const hostOnly = data.mode === "host";
  return (
    <section aria-label="Traffic" className="g-traffic-section">
      <h2>Traffic</h2>
      <TruncatedNotice truncated={data.truncated} />
      {hostOnly && (
        <p className="hint" data-testid="traffic-host-only">
          This is the sensor machine's own traffic only. It is not on a mirror port, so Vault
          cannot see what other devices on this network are talking to.
        </p>
      )}
      {data.top_talkers.length === 0 ? (
        <p className="hint g-traffic-quiet">
          {/* Only reachable when Vault IS watching, so "quiet" is a true
              answer here rather than a stand-in for "never asked". */}
          Nothing unusual — no notable traffic this hour.
        </p>
      ) : (
        <div className="g-traffic-talkers-wrap">
          <h3>Top talkers</h3>
          <ul className="g-traffic-talkers">
            {data.top_talkers.map((t) => (
              <li key={t.mac}>
                <span className="g-traffic-name">{t.name}</span>
                <span className="g-traffic-bytes">
                  {fmtBytes(t.bytes_in)} in / {fmtBytes(t.bytes_out)} out
                </span>
                {t.top_peer && <span className="g-traffic-peer hint">→ {t.top_peer}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {data.top_peers.length > 0 && (
        <div className="g-traffic-destinations">
          <h3>{hostOnly ? "Where the sensor connected" : "Top destinations"}</h3>
          <ul className="g-traffic-peers">
            {data.top_peers.map((p) => (
              <li key={`${p.peer}:${p.peer_port}`}>
                <span className="g-traffic-name">{p.peer || p.peer_ip}</span>
                <span className="hint">
                  {p.proto}/{p.peer_port} · {fmtBytes(p.bytes_in + p.bytes_out)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {data.new_peers.length > 0 && (
        <div className="g-traffic-new">
          <h3>New peers</h3>
          <ul>
            {data.new_peers.map((p) => (
              <li key={`${p.peer}:${p.peer_port}`}>
                <span className="g-traffic-name">{p.peer || p.peer_ip}</span>
                <span className="hint">
                  {p.proto}/{p.peer_port} · {fmtBytes(p.bytes_in + p.bytes_out)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

export default TrafficSection;
