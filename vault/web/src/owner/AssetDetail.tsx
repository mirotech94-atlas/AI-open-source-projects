import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { deviceDetail, hostsPayload, sitesPayload } from "../lib/api";
import type { InboxRow, PortRow, TimelineItem } from "../types";
import { ago, clock, sevLabel, sevTone, shortDate, useActiveTenant } from "./net";
import { fromDevice, fromHost, fromSite, type AssetKind, type AssetRow } from "./assetRows";

/**
 * One asset -- the reference's Asset detail. Identity on the left, what is
 * open on it and what has been done to it on the right. The three kinds are
 * one page because the reader's question is the same for all three; each
 * fills in the sections it has evidence for and omits the rest, rather than
 * showing an empty "Open ports" for a website.
 */

function Kv({ rows }: { rows: [string, string][] }) {
  return (
    <dl className="g-own-kv">
      {rows.map(([k, v]) => (
        <div key={k}><dt>{k}</dt><dd>{v || "—"}</dd></div>
      ))}
    </dl>
  );
}

function Ports({ ports }: { ports: PortRow[] }) {
  if (ports.length === 0) return <p className="g-own-quiet">No open ports seen.</p>;
  return (
    <div className="g-own-ports">
      {ports.map((p) => (
        <div key={p.port} className={`g-own-port${p.risky ? " is-risky" : ""}`}>
          <span className="g-own-port-n">{p.port}</span>
          <span className="g-own-port-s">{p.service || "—"}</span>
          <span className="g-own-port-note">{p.risky ? "Unexpected · risky service" : `since ${shortDate(p.since)}`}</span>
        </div>
      ))}
    </div>
  );
}

function Findings({ alerts }: { alerts: InboxRow[] }) {
  const open = alerts.filter((a) => a.status !== "resolved" && a.status !== "closed");
  if (open.length === 0) return <p className="g-own-quiet">Nothing open on this asset.</p>;
  return (
    <ul className="g-own-rows">
      {open.map((a) => (
        <li key={a.id} className="g-own-finding">
          <span className={`g-own-pill g-own-pill-${sevTone(a.sev)}`}>{sevLabel(a.sev)}</span>
          <span className="g-own-finding-main">
            <span className="g-own-finding-t">{a.title}</span>
            {a.remedy?.summary && <span className="g-own-finding-cmd">{a.remedy.summary}</span>}
          </span>
          <span className={`g-own-finding-mode ${a.remedy?.kind === "auto" ? "is-auto" : ""}`}>
            {a.remedy ? (a.remedy.kind === "auto" ? "allow-listed" : a.remedy.kind === "approval" ? "needs your yes" : "manual") : "manual"}
          </span>
        </li>
      ))}
    </ul>
  );
}

function Recent({ items }: { items: TimelineItem[] }) {
  if (items.length === 0) return <p className="g-own-quiet">Nothing recorded yet.</p>;
  return (
    <ul className="g-own-rows">
      {items.slice(0, 10).map((t, i) => (
        <li key={`${t.ts}-${i}`} className="g-own-act">
          <span className="g-own-act-time">{shortDate(t.ts)} {clock(t.ts)}</span>
          <span className="g-own-act-text">{t.title}{t.detail && <span className="g-own-act-detail"> — {t.detail}</span>}</span>
          <span className={`g-own-pill g-own-pill-${t.kind === "fix" ? "good" : "plain"}`}>{t.kind === "fix" ? "auto" : t.kind}</span>
        </li>
      ))}
    </ul>
  );
}

function AssetDetail() {
  const tenant = useActiveTenant();
  const { kind = "device", id = "" } = useParams<{ kind: AssetKind; id: string }>();
  const on = tenant !== null && id !== "";
  const dev = useQuery({ queryKey: ["device", id], queryFn: () => deviceDetail(id, null), enabled: on && kind === "device" });
  const hosts = useQuery({ queryKey: ["hosts"], queryFn: hostsPayload, enabled: on && kind === "server" });
  const sites = useQuery({ queryKey: ["sites"], queryFn: sitesPayload, enabled: on && kind === "site" });

  if (tenant === null) return <p className="g-own-lede">Pick a customer in the bar first.</p>;

  let row: AssetRow | null = null;
  let identity: [string, string][] = [];
  let ports: PortRow[] | null = null;
  let alerts: InboxRow[] = [];
  let recent: TimelineItem[] = [];
  let extra: [string, string][] = [];

  if (kind === "device" && dev.data) {
    const d = dev.data.device;
    row = fromDevice(d);
    identity = [["Address", d.ip], ["MAC", d.mac], ["Kind", d.kind], ["Vendor", d.vendor], ["Network", d.network?.name ?? ""],
      ["First seen", shortDate(d.first_seen)], ["Trusted", d.trusted ? "yes" : "no"]];
    ports = dev.data.ports; alerts = dev.data.alerts; recent = dev.data.timeline;
    extra = [["Online, 7 days", d.online_7d === null ? "" : `${d.online_7d}%`], ["Presence", d.presence]];
  } else if (kind === "server" && hosts.data) {
    const h = hosts.data.hosts.find((x) => x.name === id);
    if (h) {
      row = fromHost(h);
      identity = [["Address", h.address], ["Hostname", h.hostname], ["Uptime", h.uptime], ["Hardening", h.hardening?.grade ?? ""],
        ["Pending updates", String(h.pending_updates)], ["Security updates", String(h.security_updates)], ["Reboot", h.reboot_required ? "required" : "not needed"]];
      ports = h.ports;
      extra = [["Disk", h.disk_pct === null ? "" : `${Math.round(h.disk_pct)}%`], ["Memory", h.mem_pct === null ? "" : `${Math.round(h.mem_pct)}%`],
        ["Load / core", h.load_per_core === null ? "" : h.load_per_core.toFixed(2)], ["Failed SSH, 24 h", String(h.failed_ssh_24h)]];
    }
  } else if (kind === "site" && sites.data) {
    const s = sites.data.sites.find((x) => x.name === id);
    if (s) {
      row = fromSite(s);
      identity = [["URL", s.url], ["DNS", s.dns_a.join(", ")], ["Certificate", s.cert_issuer], ["Expires", s.cert_expires ? `${shortDate(s.cert_expires)} · ${s.cert_days ?? "?"} d` : ""],
        ["Last check", `${s.code ?? "—"} · ${s.ms ?? "—"} ms`], ["Public", s.public ? "yes" : "no"]];
      extra = [["Uptime, 24 h", s.uptime_24h === null ? "" : `${s.uptime_24h}%`], ["Uptime, 7 d", s.uptime_7d === null ? "" : `${s.uptime_7d}%`],
        ["Incidents, 7 d", String(s.incidents_7d)]];
    }
  }

  const loading = (kind === "device" && dev.isPending) || (kind === "server" && hosts.isPending) || (kind === "site" && sites.isPending);
  if (loading) return <p role="status">Reading the asset…</p>;
  if (!row) {
    return (
      <div className="g-own-page">
        <Link to="/owner/assets" className="g-own-back">← Assets</Link>
        <p className="g-own-lede">No {kind} called <span className="g-own-mono">{id}</span> on {tenant.name}'s network right now.</p>
      </div>
    );
  }

  return (
    <div className="g-own-page">
      <Link to="/owner/assets" className="g-own-back">← Assets</Link>

      <header className="g-own-section g-own-detailhead">
        <div className="g-own-detailhead-l">
          <span className={`g-own-dot g-own-dot-lg g-tone-${row.seen ? "good" : row.posture.tone === "crit" ? "alert" : "plain"}`} aria-hidden="true" />
          <h2 className="g-own-detailname">{row.name}</h2>
          <span className={`g-own-pill g-own-pill-${row.posture.tone}`}>{row.posture.label}</span>
        </div>
        <p className="g-own-detailsub">
          {row.type} · <span className="g-own-mono">{row.address}</span>{row.sub ? ` · ${row.sub}` : ""} · seen {ago(row.lastSeen)}
        </p>
      </header>

      <div className="g-own-split g-own-split-detail">
        <div className="g-own-col">
          <section aria-label="Identity" className="g-own-section">
            <h2 className="g-own-h2-mono">Identity</h2>
            <Kv rows={identity} />
          </section>
          {extra.length > 0 && (
            <section aria-label="Readings" className="g-own-section">
              <h2 className="g-own-h2-mono">Readings</h2>
              <Kv rows={extra} />
            </section>
          )}
        </div>
        <div className="g-own-col">
          {(kind !== "site") && (
            <section aria-label="Open findings" className="g-own-section">
              <h2>Open findings <span className="g-own-meta">{alerts.filter((a) => a.status !== "resolved").length} open</span></h2>
              <Findings alerts={alerts} />
            </section>
          )}
          {ports !== null && (
            <section aria-label="Open ports" className="g-own-section">
              <h2>Open ports <span className="g-own-meta">seen {ago(row.lastSeen)}</span></h2>
              <div><Ports ports={ports} /></div>
            </section>
          )}
          {kind === "device" && (
            <section aria-label="Recent" className="g-own-section">
              <h2>Recent actions on this asset</h2>
              <Recent items={recent} />
            </section>
          )}
        </div>
      </div>
    </div>
  );
}

export default AssetDetail;
