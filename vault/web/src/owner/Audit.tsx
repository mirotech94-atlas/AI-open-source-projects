import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { auditPayload, platformAudit } from "../lib/api";
import { downloadCsv, useActiveTenant } from "./net";

/**
 * Append-only record of every action -- the reference's Audit log.
 *
 * Two scopes, one table. "This customer" is the selected network's own trail,
 * the same rows their dashboard shows; "Every customer" is the platform's,
 * which is the other direction: something was changed and you do not yet
 * know where. Both label a platform act as such rather than leaving a blank
 * column that reads like missing information.
 *
 * Neither shows the audit `body`. That is the free-form detail of an act --
 * where a changed setting's old and new values live -- and "what happened
 * lately" is a different question from "what exactly changed in that one
 * act".
 */

type Row = { ts: string; user: string; role: string; action: string; target: string; tenant: string; tenant_id: string; platform: boolean };

function when(iso: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString(undefined, { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

/** An act by the product rather than by a person. The trail records the
 *  actor's name and role as free text, so this is a naming convention rather
 *  than a flag: the hub writes its own acts as `guardian`, and a sensor's as
 *  the `sensor` role. A miss here only costs a colour. */
function isMachine(user: string, role: string): boolean {
  const u = user.toLowerCase();
  const r = role.toLowerCase();
  return u === "guardian" || u === "system" || r === "system" || r === "sensor";
}

function Audit() {
  const tenant = useActiveTenant();
  const [scope, setScope] = useState<"tenant" | "platform">(tenant ? "tenant" : "platform");
  const [q_, setQ] = useState("");

  const mine = useQuery({ queryKey: ["audit", 200], queryFn: () => auditPayload(200), refetchInterval: 60_000, enabled: scope === "tenant" && tenant !== null });
  const every = useQuery({ queryKey: ["platform-audit"], queryFn: platformAudit, refetchInterval: 60_000, enabled: scope === "platform" });
  const q = scope === "tenant" ? mine : every;

  const rows: Row[] = useMemo(() => {
    if (scope === "tenant") {
      return (mine.data?.rows ?? []).map((r) => ({ ...r, tenant: tenant?.name ?? "", tenant_id: tenant?.id ?? "", platform: false }));
    }
    return every.data?.rows ?? [];
  }, [scope, mine.data, every.data, tenant]);

  const shown = useMemo(() => {
    const needle = q_.trim().toLowerCase();
    if (!needle) return rows;
    // Matched against everything visible, so what you filter on is what you
    // can see -- a filter that also searched hidden fields would return rows
    // with no apparent reason for being there.
    return rows.filter((r) => [r.user, r.action, r.target, r.tenant, r.role].some((f) => f.toLowerCase().includes(needle)));
  }, [rows, q_]);

  if (q.isPending) return <p role="status">Loading the trail…</p>;
  if (q.isError && !q.data) {
    return <div role="alert">Could not read the trail. <button onClick={() => void q.refetch()}>Retry</button></div>;
  }

  return (
    <div className="g-own-page">
      <div className="g-own-intro">
        <p className="g-own-lede">
          Every action Vault takes is written here first. Append-only, exportable, and it names who authorised the change.
        </p>
        <button type="button" className="g-own-btn" disabled={shown.length === 0}
                onClick={() => downloadCsv(
                  `vault-audit-${scope === "tenant" ? tenant?.slug ?? "customer" : "platform"}.csv`,
                  ["time", "actor", "role", "action", "target", "customer"],
                  shown.map((r) => [r.ts, r.user, r.role, r.action, r.target, r.platform ? "platform" : r.tenant]),
                )}>
          Export CSV
        </button>
      </div>

      <section aria-label="The trail" className="g-own-section">
        <h2>The trail <span className="g-own-meta">{q_ ? `${shown.length} of ${rows.length}` : `${rows.length} acts`}</span></h2>
        <div className="g-own-toolbar">
          <button type="button" className="g-own-fchip" aria-pressed={scope === "tenant"} disabled={tenant === null}
                  onClick={() => setScope("tenant")}>{tenant ? tenant.name : "This customer"}</button>
          <button type="button" className="g-own-fchip" aria-pressed={scope === "platform"} onClick={() => setScope("platform")}>Every customer</button>
          <label htmlFor="audit-filter" className="g-own-filter-l">Filter</label>
          <input id="audit-filter" type="search" className="g-own-filter-in" value={q_} onChange={(e) => setQ(e.target.value)}
                 placeholder="a person, an action, a target" />
        </div>
        <div className="g-own-tablewrap">
          <table className="g-own-table g-own-table-audit">
            <thead>
              <tr>
                <th scope="col">Time</th>
                <th scope="col">Actor</th>
                <th scope="col">Action</th>
                <th scope="col">Target</th>
                <th scope="col">{scope === "platform" ? "Customer" : "Role"}</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((r, i) => (
                <tr key={`${r.ts}-${i}`}>
                  <td className="g-own-mono g-own-dim">{when(r.ts)}</td>
                  <td><span className={`g-own-actor${isMachine(r.user, r.role) ? " is-machine" : ""}`}>{r.user}</span></td>
                  <td className="g-own-action">{r.action}</td>
                  <td className="g-own-mono g-own-dim">{r.target || "—"}</td>
                  <td>
                    {scope === "platform" ? (
                      r.platform ? <span className="g-own-pill">platform</span>
                        : r.tenant_id ? <Link to={`/owner/accounts/${r.tenant_id}`}>{r.tenant || r.tenant_id}</Link>
                        : <span className="g-own-quiet">—</span>
                    ) : (
                      <span className="g-own-mono g-own-dim">{r.role || "—"}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {shown.length === 0 && <p className="g-own-quiet">{q_ ? `Nothing matches “${q_}”.` : "Nothing recorded yet."}</p>}
      </section>
    </div>
  );
}

export default Audit;
