import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { devicesPayload, hostsPayload, sitesPayload } from "../lib/api";
import { ago, useActiveTenant } from "./net";
import { fromDevice, fromHost, fromSite, type AssetRow } from "./assetRows";

/** Devices, servers, sites and certificates -- the reference's Assets, as one
 *  table over the customer's three inventories. */

type Filter = "all" | "server" | "device" | "site" | "unmanaged";

function Assets() {
  const tenant = useActiveTenant();
  const nav = useNavigate();
  const on = tenant !== null;
  const devices = useQuery({ queryKey: ["devices", null], queryFn: () => devicesPayload(null), refetchInterval: 60_000, enabled: on });
  const hosts = useQuery({ queryKey: ["hosts"], queryFn: hostsPayload, refetchInterval: 60_000, enabled: on });
  const sites = useQuery({ queryKey: ["sites"], queryFn: sitesPayload, refetchInterval: 60_000, enabled: on });
  const [filter, setFilter] = useState<Filter>("all");

  const rows: AssetRow[] = useMemo(() => [
    ...(hosts.data?.hosts ?? []).map(fromHost),
    ...(devices.data?.groups ?? []).flatMap((g) => g.devices).map(fromDevice),
    ...(sites.data?.sites ?? []).map(fromSite),
  ], [devices.data, hosts.data, sites.data]);

  const counts = useMemo(() => ({
    all: rows.length,
    server: rows.filter((r) => r.kind === "server").length,
    device: rows.filter((r) => r.kind === "device").length,
    site: rows.filter((r) => r.kind === "site").length,
    unmanaged: rows.filter((r) => r.unmanaged).length,
  }), [rows]);

  const shown = useMemo(() => {
    const pick = rows.filter((r) => filter === "all" ? true : filter === "unmanaged" ? r.unmanaged : r.kind === filter);
    // Worst first, then by name -- the reference sorts its list for reading
    // top-down, and a page of assets is opened to find the one that is wrong.
    const rank = (t: string) => (t === "crit" ? 0 : t === "warn" ? 1 : t === "plain" ? 2 : 3);
    return pick.sort((a, b) => rank(a.posture.tone) - rank(b.posture.tone) || a.name.localeCompare(b.name));
  }, [rows, filter]);

  if (tenant === null) return <p className="g-own-lede">Pick a customer in the bar to see their assets.</p>;
  if (devices.isPending && hosts.isPending && sites.isPending) return <p role="status">Reading the inventory…</p>;

  const chips: { key: Filter; label: string; warn?: boolean }[] = [
    { key: "all", label: `All · ${counts.all}` },
    { key: "server", label: `Servers · ${counts.server}` },
    { key: "device", label: `Devices · ${counts.device}` },
    { key: "site", label: `Websites · ${counts.site}` },
    { key: "unmanaged", label: `Unverified · ${counts.unmanaged}`, warn: counts.unmanaged > 0 },
  ];

  return (
    <div className="g-own-page">
      <div className="g-own-toolbar g-own-toolbar-bare">
        {chips.map((c) => (
          <button key={c.key} type="button" className={`g-own-fchip${c.warn ? " is-warn" : ""}`}
                  aria-pressed={filter === c.key} onClick={() => setFilter(c.key)}>{c.label}</button>
        ))}
        <span className="g-own-toolbar-note g-own-mono">{counts.all} discovered · {counts.unmanaged} unverified</span>
      </div>

      <section aria-label="Assets" className="g-own-section">
        <div className="g-own-tablewrap">
          <table className="g-own-table g-own-table-assets">
            <thead>
              <tr>
                <th scope="col">Asset</th>
                <th scope="col">Type</th>
                <th scope="col">Address</th>
                <th scope="col">Platform</th>
                <th scope="col">Last seen</th>
                <th scope="col" className="g-own-num">Posture</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((r) => (
                <tr key={`${r.kind}-${r.id}`} className="g-own-rowlink" tabIndex={0}
                    onClick={() => nav(`/owner/assets/${r.kind}/${encodeURIComponent(r.id)}`)}
                    onKeyDown={(e) => { if (e.key === "Enter") nav(`/owner/assets/${r.kind}/${encodeURIComponent(r.id)}`); }}>
                  <td>
                    <span className="g-own-asset">
                      <span className={`g-own-dot g-tone-${r.seen ? "good" : r.posture.tone === "crit" ? "alert" : "plain"}`} aria-hidden="true" />
                      <span className="g-own-asset-txt">
                        <span className="g-own-asset-name">{r.name}</span>
                        {r.sub && <span className="g-own-asset-sub">{r.sub}</span>}
                      </span>
                    </span>
                  </td>
                  <td className="g-own-dim">{r.type}</td>
                  <td className="g-own-mono">{r.address}</td>
                  <td className="g-own-dim">{r.platform}</td>
                  <td className="g-own-mono g-own-dim">{ago(r.lastSeen)}</td>
                  <td className="g-own-num"><span className={`g-own-pill g-own-pill-${r.posture.tone}`}>{r.posture.label}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {shown.length === 0 && <p className="g-own-quiet">Nothing in this group.</p>}
      </section>
    </div>
  );
}

export default Assets;
