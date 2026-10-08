import { StatStrip, Verdict } from "../design";
import { Fragment, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { accountPayload, platformPayload } from "../lib/api";
import type { AccountSensor, PlatformTenantRow } from "../types";
import { TrendCell } from "./TrendCell";

/** The sensors themselves, under the customer's row: name, platform,
 *  version, last seen. Read on open, not for every row -- the roster is a
 *  glance and this is the look. */
function SensorRows({ tid }: { tid: string }) {
  const q = useQuery({ queryKey: ["account", tid], queryFn: () => accountPayload(tid) });
  if (q.isPending) return <p className="g-own-quiet">Reading the sensors…</p>;
  const rows = q.data?.sensors ?? [];
  if (rows.length === 0) return <p className="g-own-quiet">Nothing enrolled. Every figure Vault could show this customer depends on a sensor.</p>;
  const tone = (s: AccountSensor) => {
    if (s.status !== "active") return s.status === "revoked" ? "crit" : "warn";
    const ms = s.last_seen ? Date.now() - new Date(s.last_seen).getTime() : Infinity;
    return ms > 3 * 3600_000 ? "crit" : ms > 900_000 ? "warn" : "good";
  };
  return (
    <ul className="g-own-rows g-own-subrows">
      {rows.map((s) => (
        <li key={s.id} className="g-own-held">
          <span className="g-own-target">{s.name}</span>
          <span className="g-own-dim">{s.platform || "—"}</span>
          <span className="g-own-mono g-own-dim">{s.version || "—"}</span>
          <span className={`g-own-pill g-own-pill-${tone(s)}`}>{s.status || "unknown"}</span>
          <span className="g-own-mono g-own-dim" style={{ marginLeft: "auto" }}>{ago(s.last_seen ?? "")}</span>
        </li>
      ))}
      <li className="g-own-held g-own-subfoot">
        <Link to={`/owner/accounts/${tid}`}>Everything set up on this account</Link>
      </li>
    </ul>
  );
}

/**
 * Every sensor the platform depends on, in one list.
 *
 * The Ledger answers "how is this customer", one row at a time. This answers
 * the question that cuts across them: which sensors have gone quiet, oldest
 * first. Those two are not the same screen, because a sensor that stopped
 * reporting is the platform's problem before it is the customer's -- every
 * figure Vault shows that customer is downstream of it, and a quiet sensor
 * silently turns the whole dashboard into stale history.
 *
 * Sorted by how long it has been quiet rather than alphabetically, because the
 * order IS the information: the top of this list is what to chase.
 */

function ago(iso: string): string {
  if (!iso) return "never";
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms)) return "never";
  const m = Math.round(ms / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

/** Milliseconds since the last scan, or Infinity when there has never been
 *  one. Infinity sorts to the top on purpose: a customer whose sensor has
 *  never reported is worse off than one whose sensor stopped yesterday, and
 *  sorting "never" as zero would bury them at the bottom. */
function quietFor(r: PlatformTenantRow): number {
  if (!r.last_scan) return Number.POSITIVE_INFINITY;
  const ms = Date.now() - new Date(r.last_scan).getTime();
  return Number.isNaN(ms) ? Number.POSITIVE_INFINITY : ms;
}

function state(r: PlatformTenantRow): { label: string; tone: "good" | "warn" | "crit" } {
  if (r.sensors_total === 0) return { label: "no sensor enrolled", tone: "crit" };
  if (!r.ingesting) return { label: "not reporting", tone: "crit" };
  if (r.sensors_online < r.sensors_total) {
    return { label: `${r.sensors_online} of ${r.sensors_total} reporting`, tone: "warn" };
  }
  return { label: r.sensors_total === 1 ? "reporting" : `all ${r.sensors_total} reporting`, tone: "good" };
}

function Fleet() {
  const q = useQuery({ queryKey: ["platform"], queryFn: platformPayload, refetchInterval: 60_000 });
  const p = q.data;
  const [open, setOpen] = useState<string | null>(null);

  const rows = useMemo(() => {
    const all = [...(p?.tenants ?? [])];
    // Unhealthy first, then longest-quiet first inside each group.
    all.sort((a, b) => {
      const rank = (r: PlatformTenantRow) => (state(r).tone === "crit" ? 0 : state(r).tone === "warn" ? 1 : 2);
      return rank(a) - rank(b) || quietFor(b) - quietFor(a);
    });
    return all;
  }, [p]);

  if (q.isPending) return <p role="status">Loading the fleet…</p>;
  if (q.isError && !p) {
    return (
      <div role="alert">
        Could not read the fleet. <button onClick={() => void q.refetch()}>Retry</button>
      </div>
    );
  }

  const t = p!.totals;
  const quiet = Math.max(0, t.sensors_total - t.sensors_online);

  const customers = p!.tenants.length;
  return (
    <div className="g-own-page">
      {/* Zero sensors is not "every sensor is reporting" -- that reads as
          reassurance while the table underneath says no sensor installed.
          Nothing deployed is its own state, and it is work, not calm. */}
      <Verdict tone={t.sensors_total === 0 ? "warn" : quiet > 0 ? (quiet >= Math.max(2, t.sensors_total / 2) ? "alert" : "warn") : "good"}
               headline={t.sensors_total === 0
                 ? "No sensors deployed yet"
                 : quiet === 0 ? "Every sensor is reporting" : `${quiet} of ${t.sensors_total} sensors quiet`}
               body={t.sensors_total === 0
                 ? `${customers} customer${customers === 1 ? "" : "s"} on the books and nothing watching them yet. Vault has nothing to report until a sensor checks in.`
                 : quiet === 0
                   ? `${t.sensors_total} sensors across ${customers} customer${customers === 1 ? "" : "s"}.`
                   : "Everything Vault shows those customers is as old as their last scan."} />
      <StatStrip items={[
        { label: "Reporting", value: `${t.sensors_online}/${t.sensors_total}`, deltaTone: quiet === 0 ? "good" : "warn" },
        { label: "Customers", value: String(customers) },
        { label: "Open critical", value: String(t.open_crit), deltaTone: t.open_crit > 0 ? "alert" : undefined },
        { label: "Open warnings", value: String(t.open_warn), deltaTone: t.open_warn > 0 ? "warn" : undefined },
      ]} />
      <header className="g-own-head">
        <p className="g-own-lede">
          {quiet === 0
            ? `All ${t.sensors_total} sensors are reporting.`
            : `${quiet} of ${t.sensors_total} sensors ${quiet === 1 ? "is" : "are"} quiet. Everything Vault shows those customers is as old as their last scan.`}
        </p>
      </header>

      <section aria-label="Sensors" className="g-own-section">
      <h2>Sensors <span className="g-own-meta">{t.sensors_online}/{t.sensors_total} reporting</span></h2>
      <div className="g-own-tablewrap">
        <table className="g-own-table">
          <thead>
            <tr>
              <th scope="col">Customer</th>
              <th scope="col">Sensors</th>
              <th scope="col">Last scan</th>
              <th scope="col" className="g-own-num">Devices</th>
              <th scope="col">Open</th>
              {/* Same column the Ledger carries, for the same reason: a sensor
                  that is reporting tells you nothing about whether what it
                  reports has been getting worse. */}
              <th scope="col">30 days</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const s = state(r);
              const isOpen = open === r.id;
              return (
                <Fragment key={r.id}>
                <tr className={`g-own-tr-${s.tone} g-own-rowlink${isOpen ? " is-open" : ""}`} tabIndex={0}
                    aria-expanded={isOpen}
                    onClick={() => setOpen(isOpen ? null : r.id)}
                    onKeyDown={(e) => { if (e.key === "Enter") setOpen(isOpen ? null : r.id); }}>
                  <th scope="row">
                    <span className="g-own-name"><span className="g-own-caret" aria-hidden="true">{isOpen ? "▾" : "▸"}</span>{r.name}</span>
                    <span className="g-own-slug">{r.slug}</span>
                  </th>
                  <td><span className={`g-own-pill g-own-pill-${s.tone}`}>{s.label}</span></td>
                  <td className="g-own-mono">{ago(r.last_scan)}</td>
                  <td className="g-own-num g-own-mono">{r.devices}</td>
                  <td>
                    {r.open_crit > 0 && <span className="g-own-crit">{r.open_crit} crit</span>}
                    {r.open_crit > 0 && r.open_warn > 0 && " · "}
                    {r.open_warn > 0 && <span className="g-own-warn">{r.open_warn} warn</span>}
                    {r.open_crit === 0 && r.open_warn === 0 && <span className="g-own-quiet">—</span>}
                  </td>
                  <td><TrendCell tenantId={r.id} /></td>
                </tr>
                {isOpen && (
                  <tr className="g-own-expand">
                    <td colSpan={6}><SensorRows tid={r.id} /></td>
                  </tr>
                )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      </section>
    </div>
  );
}

export default Fleet;
