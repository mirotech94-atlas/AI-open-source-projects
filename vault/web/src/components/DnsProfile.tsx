import { useQuery } from "@tanstack/react-query";
import { Badge } from "../design";
import { deviceDetail } from "../lib/api";

export type DnsProfileProps = {
  mac: string;
};

/** The per-device DNS profile — queries/blocked counts and top-domain lists
 *  for one device. Not its own route: `DeviceDetail.dns` (`DeviceDnsProfile`)
 *  already carries exactly this, so this reuses `deviceDetail()` and shares
 *  its query cache with the Assets device drawer. */
export function DnsProfile({ mac }: DnsProfileProps) {
  const query = useQuery({ queryKey: ["deviceDetail", mac], queryFn: () => deviceDetail(mac) });
  const dns = query.data?.dns;

  if (!dns) {
    return <p className="hint">Loading…</p>;
  }

  return (
    <div className="g-dns-profile">
      <dl className="g-facts">
        <dt>Queries, 24h</dt>
        <dd>{dns.queries_24h}</dd>
        <dt>Blocked, 24h</dt>
        <dd>{dns.blocked_24h}</dd>
      </dl>
      <div className="g-dns-profile-lists">
        <div>
          <h3>Top domains</h3>
          {dns.top.length === 0 ? (
            <p className="g-asset-empty">No queries recorded yet.</p>
          ) : (
            <ul>
              {dns.top.map((n) => (
                <li key={n.name}>
                  <span className="mono">{n.name}</span> · {n.count}
                </li>
              ))}
            </ul>
          )}
        </div>
        <div>
          <h3>Top blocked</h3>
          {dns.blocked_top.length === 0 ? (
            <p className="g-asset-empty">Nothing blocked yet.</p>
          ) : (
            <ul>
              {dns.blocked_top.map((n) => (
                <li key={n.name}>
                  <Badge tone="warn">{n.name}</Badge> · {n.count}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

export default DnsProfile;
