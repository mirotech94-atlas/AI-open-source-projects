import { Badge } from "../design";
import type { DeviceCard, HostCard, SiteCard } from "../types";
import "../styles/inventory.css";

/**
 * The Assets page as Vault Portal v2 draws it: five tiles, then one table
 * of everything Vault watches, with a posture word per row.
 *
 * Every posture is derived from a field the row already carries — nothing
 * here asks the hub a second question — and each word is chosen so it cannot
 * be read as more than the data supports: a device that is simply asleep is
 * not "clean", it is "unknown".
 */

export type InventoryRow = {
  id: string;
  name: string;
  sub: string;
  kind: string;
  address: string;
  platform: string;
  seen: string;
  posture: { label: string; tone: "good" | "warn" | "alert" | "plain" };
  /** Which chip this row answers to. */
  bucket: "servers" | "devices" | "unknown";
  mac?: string;
};

function serverPosture(h: HostCard): InventoryRow["posture"] {
  if (h.reachable === false) return { label: "unreachable", tone: "alert" };
  if (h.reachable === null || h.stale) return { label: "unknown", tone: "plain" };
  const findings = h.security_updates + h.cve_fixable + (h.reboot_required ? 1 : 0);
  if (findings > 0) return { label: `${findings} finding${findings === 1 ? "" : "s"}`, tone: "warn" };
  return { label: "clean", tone: "good" };
}

function devicePosture(d: DeviceCard): InventoryRow["posture"] {
  if (d.quarantined) return { label: "quarantined", tone: "alert" };
  if (d.unverified) return { label: "unverified", tone: "warn" };
  if (d.ports.some((p) => p.risky)) return { label: "open port", tone: "warn" };
  // Offline with nothing against it is not a clean bill: Vault has not
  // looked at it recently enough to say.
  if (!d.online) return { label: "unknown", tone: "plain" };
  return { label: "clean", tone: "good" };
}

function humanKind(d: DeviceCard): string {
  const k = (d.kind || d.group || "").replace(/[-_]/g, " ").trim();
  return k ? k[0].toUpperCase() + k.slice(1) : "Device";
}

export function inventoryRows(hosts: HostCard[], devices: DeviceCard[]): InventoryRow[] {
  const servers: InventoryRow[] = hosts.map((h) => ({
    id: `host:${h.name}`,
    name: h.name,
    sub: h.hostname && h.hostname !== h.name ? h.hostname : "Server",
    kind: "Server",
    address: h.address,
    // `platform` for a host is the best identity Vault actually reads. It
    // does not read an OS name, and inventing "Ubuntu" would be a lie.
    platform: h.hostname || "—",
    seen: h.last_checked,
    posture: serverPosture(h),
    bucket: "servers",
  }));
  const rest: InventoryRow[] = devices.map((d) => ({
    id: `dev:${d.mac || d.ip || d.name}`,
    name: d.label || d.name || d.vendor || d.mac,
    sub: d.network?.name ?? (d.vendor && d.vendor !== (d.label || d.name) ? d.vendor : ""),
    kind: humanKind(d),
    address: d.ip || "—",
    platform: d.vendor || "—",
    seen: d.last_seen,
    posture: devicePosture(d),
    bucket: d.unverified ? "unknown" : "devices",
    mac: d.mac,
  }));
  // Worst posture first, then servers, then name: the row that needs a look
  // is the one the eye should land on.
  const rank = { alert: 0, warn: 1, plain: 2, good: 3 };
  return [...servers, ...rest].sort(
    (a, b) => rank[a.posture.tone] - rank[b.posture.tone] || a.kind.localeCompare(b.kind) || a.name.localeCompare(b.name),
  );
}

export function AssetTiles({
  devices, hosts, sites, unverified, certWarnDays,
}: {
  devices: { total: number; online: number };
  hosts: HostCard[];
  sites: SiteCard[];
  unverified: number;
  certWarnDays: number;
}) {
  const reporting = hosts.filter((h) => h.reachable === true).length;
  const sitesUp = sites.filter((s) => s.ok === true).length;
  const certsRead = sites.filter((s) => s.cert_days !== null);
  const certsSoon = certsRead.filter((s) => (s.cert_days ?? 0) <= certWarnDays).length;

  const tiles = [
    { key: "devices", label: "Devices", value: devices.online, note: `of ${devices.total} online` },
    { key: "servers", label: "Servers", value: hosts.length,
      note: hosts.length === 0 ? "none configured" : reporting === hosts.length ? "all reporting" : `${reporting} reporting` },
    { key: "websites", label: "Websites", value: sites.length,
      note: sites.length === 0 ? "none configured" : sitesUp === sites.length ? "all up" : `${sitesUp} up` },
    // The same framing as the Overview's Coverage row, in the same words:
    // certificates READ, of the sites that have one. Showing only the read
    // count hid an unread site -- "2" with no denominator reads as fine while
    // Coverage beside it reads 2 / 3. Both numbers come from each site's own
    // last check, so the two cards cannot disagree.
    { key: "certificates", label: "Certificates", value: certsRead.length,
      note: sites.length === 0 ? "no sites watched"
          : certsRead.length === 0 ? `none of ${sites.length} read yet`
          : certsRead.length < sites.length ? `${certsRead.length} of ${sites.length} read · ${certsSoon} inside ${certWarnDays} d`
          : `all ${sites.length} read · ${certsSoon} inside ${certWarnDays} d`,
      warn: certsSoon > 0 || (sites.length > 0 && certsRead.length < sites.length) },
    { key: "unknown", label: "Unknown", value: unverified, note: unverified === 0 ? "everything named" : "need naming",
      warn: unverified > 0 },
  ];
  return (
    <ul className="g-inv-tiles" data-testid="asset-tiles">
      {tiles.map((t) => (
        <li key={t.key} className="g-inv-tile" data-warn={t.warn ? "1" : undefined}>
          <span className="g-inv-tile-label">{t.label}</span>
          <span className="g-inv-tile-value">{t.value}</span>
          <span className="g-inv-tile-note">{t.note}</span>
        </li>
      ))}
    </ul>
  );
}

const CHIPS: { key: "all" | InventoryRow["bucket"]; label: string }[] = [
  { key: "all", label: "All" },
  { key: "servers", label: "Servers" },
  { key: "devices", label: "Devices" },
  { key: "unknown", label: "Unknown" },
];

function ago(iso: string): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return iso;
  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (s < 60) return "now";
  if (s < 3600) return `${Math.round(s / 60)} min`;
  if (s < 86400) return `${Math.round(s / 3600)} h`;
  return `${Math.round(s / 86400)} d`;
}

export function AssetInventoryTable({
  rows, filter, onFilter, onOpen,
}: {
  rows: InventoryRow[];
  filter: "all" | InventoryRow["bucket"];
  onFilter: (f: "all" | InventoryRow["bucket"]) => void;
  onOpen?: (row: InventoryRow) => void;
}) {
  const counts = { all: rows.length, servers: 0, devices: 0, unknown: 0 } as Record<string, number>;
  for (const r of rows) counts[r.bucket] += 1;
  const shown = filter === "all" ? rows : rows.filter((r) => r.bucket === filter);

  return (
    <section className="g-inv" data-testid="asset-inventory">
      <header className="g-inv-head">
        <h3>Devices</h3>
        <div className="g-inv-chips" role="group" aria-label="Filter">
          {CHIPS.map((c) => (
            <button key={c.key} type="button" className="g-inv-chip" aria-pressed={filter === c.key}
                    onClick={() => onFilter(c.key)}>
              {c.label} · {counts[c.key]}
            </button>
          ))}
        </div>
      </header>
      {shown.length === 0 ? (
        <p className="g-inv-empty">Nothing in this group.</p>
      ) : (
        <table className="g-inv-table">
          <thead>
            <tr><th>Device</th><th>Kind</th><th>Address</th><th>Platform</th><th className="num">Seen</th><th className="num">Posture</th></tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.id} className={onOpen ? "g-inv-row-click" : undefined}
                  onClick={onOpen ? () => onOpen(r) : undefined}>
                <td>
                  <span className={`g-inv-dot g-tone-${r.posture.tone}`} aria-hidden="true" />
                  <span className="g-inv-name">{r.name}</span>
                  {r.sub && <span className="g-inv-sub">{r.sub}</span>}
                </td>
                <td>{r.kind}</td>
                <td className="mono">{r.address}</td>
                <td>{r.platform}</td>
                <td className="num mono">{ago(r.seen)}</td>
                <td className="num"><Badge tone={r.posture.tone}>{r.posture.label}</Badge></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
