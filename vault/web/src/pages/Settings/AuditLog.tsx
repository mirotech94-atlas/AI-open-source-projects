import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Table } from "../../design";
import type { Column } from "../../design";
import { auditPayload } from "../../lib/api";
import type { Role } from "../../lib/auth";
import type { AuditRow } from "../../types";
import { RoleNotice } from "./shared";

export type AuditLogProps = {
  role: Role | null;
  /** `GET /api/v1/audit` is admin-only on the hub — a viewer/operator gets
   *  a 403, so the query is never even attempted for them; the section
   *  shows the same role explanation as every other gated control instead
   *  of surfacing a fetch error. */
  canRead: boolean;
};

function withinRange(ts: string, from: string, to: string): boolean {
  if (from && ts < from) return false;
  if (to && ts > `${to}T23:59:59`) return false;
  return true;
}

/** Audit log: a filterable table (action, user, date range) of `AuditRow`s,
 *  each expandable to its raw `body`. */
export function AuditLog({ role, canRead }: AuditLogProps) {
  const [action, setAction] = useState("");
  const [user, setUser] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const auditQuery = useQuery({
    queryKey: ["audit", action, user],
    queryFn: () => auditPayload(200, action, user),
    enabled: canRead,
  });

  const rows = useMemo(() => {
    const all = auditQuery.data?.rows ?? [];
    if (!from && !to) return all;
    return all.filter((r) => withinRange(r.ts, from, to));
  }, [auditQuery.data, from, to]);

  if (!canRead) {
    return <RoleNotice role={role} required="admin" />;
  }

  function toggle(i: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
  }

  const columns: Column<AuditRow>[] = [
    { key: "ts", header: "When" },
    { key: "user", header: "User" },
    { key: "role", header: "Role" },
    { key: "action", header: "Action" },
    { key: "target", header: "Target" },
  ];

  return (
    <div className="g-settings-audit">
      <div className="g-settings-audit-filters">
        <label htmlFor="audit-filter-action">Action</label>
        <input id="audit-filter-action" value={action} onChange={(e) => setAction(e.target.value)} placeholder="e.g. thresholds.update" />
        <label htmlFor="audit-filter-user">User</label>
        <input id="audit-filter-user" value={user} onChange={(e) => setUser(e.target.value)} />
        <label htmlFor="audit-filter-from">From</label>
        <input id="audit-filter-from" type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
        <label htmlFor="audit-filter-to">To</label>
        <input id="audit-filter-to" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
      </div>

      <Table
        columns={columns}
        rows={rows}
        rowKey={(r) => `${r.ts}-${r.user}-${r.action}-${r.target}`}
        onRowClick={(r) => toggle(rows.indexOf(r))}
        empty="No audit entries match."
      />

      {rows.map(
        (r, i) =>
          expanded.has(i) && (
            <pre key={`${r.ts}-${i}`} className="g-settings-audit-body">
              {JSON.stringify(r.body, null, 2)}
            </pre>
          ),
      )}
    </div>
  );
}

export default AuditLog;
