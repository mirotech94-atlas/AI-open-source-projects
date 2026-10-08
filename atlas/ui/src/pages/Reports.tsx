import { useState, type JSX } from "react";
import { api } from "../api";
import { useFresh } from "../hooks/useFresh";

export function Reports({ reloadKey }: { reloadKey: number }): JSX.Element {
  const [kind, setKind] = useState("");
  const [since, setSince] = useState("");
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState<number | null>(null);

  const list = useFresh(
    () => api.reports({
      ...(kind ? { kind } : {}),
      ...(since ? { since } : {}),
      ...(search ? { module: search } : {}),
    }),
    [reloadKey, kind, since, search],
  );
  const detail = useFresh(
    () => (open === null ? Promise.resolve(null) : api.report(open)),
    [open],
  );

  return (
    <>
      <h2 className="label">Reports</h2>
      <p className="stamp" style={{ marginBottom: 12 }}>
        {list.fetchedAt ? `Read ${list.fetchedAt.toLocaleTimeString()}` : "Reading…"}
        {list.data && ` · ${list.data.reports.length} archived`}
      </p>

      <div className="filters">
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">All kinds</option>
          <option value="brief">Morning briefs</option>
          <option value="incident">Incident reports</option>
          <option value="weekly">Weekly summaries</option>
        </select>
        <input type="date" value={since} onChange={(e) => setSince(e.target.value)}
               aria-label="On or after" />
        <input type="search" placeholder="search text / module" value={search}
               onChange={(e) => setSearch(e.target.value)} />
      </div>

      {list.error && <p className="errorbar">Could not load reports: {list.error}</p>}

      {list.data && list.data.reports.length === 0 && (
        <p className="empty">
          No reports yet. The first one is written when ATLAS sends the 07:00
          brief.
        </p>
      )}

      {list.data && list.data.reports.length > 0 && (
        <div className="panel">
          <table className="sheet">
            <thead>
              <tr>
                <th>Sent</th><th>Kind</th><th>Title</th><th>Delivered via</th>
              </tr>
            </thead>
            <tbody>
              {list.data.reports.map((r) => (
                <tr key={r.id} className="expandable"
                    onClick={() => setOpen(open === r.id ? null : r.id)}>
                  <td>{new Date(r.at).toLocaleString()}</td>
                  <td>{r.kind}</td>
                  <td>
                    {r.title}
                    <div style={{ color: "var(--text-faint)", fontSize: 11 }}>
                      {r.excerpt.split("\n").slice(0, 2).join(" · ")}
                    </div>
                  </td>
                  <td style={{ color: r.delivered_via === "undelivered"
                    ? "var(--amber)" : "var(--text-dim)" }}>
                    {r.delivered_via ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {open !== null && (
        <div style={{ marginTop: 16 }}>
          <h2 className="label">Report #{open}</h2>
          {detail.error && <p className="errorbar">{detail.error}</p>}
          {detail.data?.report && (
            <>
              <p className="stamp" style={{ marginBottom: 8 }}>
                <a href={`/reports/${open}`}>Open as a page</a> — this is the link
                that gets texted
              </p>
              <pre className="pre" style={{ maxHeight: "none" }}>
                {detail.data.report.body}
              </pre>
            </>
          )}
        </div>
      )}
    </>
  );
}
