import { Fragment, useState, type JSX } from "react";
import { useSearchParams } from "react-router-dom";
import { api, normaliseHealth } from "../api";
import { useFresh } from "../hooks/useFresh";
import { HealthBadge } from "../components/Health";
import { Judgment } from "../components/Judgment";

export function Activity({ reloadKey }: { reloadKey: number }): JSX.Element {
  const [params, setParams] = useSearchParams();
  const moduleFilter = params.get("module") ?? "";
  const kindFilter = params.get("kind") ?? "";
  const dayFilter = params.get("day") ?? "";
  const [open, setOpen] = useState<string | null>(null);

  const rows = useFresh(
    () => api.activity({
      ...(moduleFilter ? { module: moduleFilter } : {}),
      ...(kindFilter ? { kind: kindFilter } : {}),
      ...(dayFilter ? { day: dayFilter } : {}),
      limit: 300,
    }),
    [reloadKey, moduleFilter, kindFilter, dayFilter],
  );
  const chain = useFresh(() => api.verifyChain(), [reloadKey]);
  const overview = useFresh(() => api.overview(), [reloadKey]);
  const judgment = useFresh(() => api.judgment(), [reloadKey]);

  const setFilter = (key: string, value: string): void => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next);
  };

  return (
    <>
      <h2 className="label">Activity</h2>
      <p className="stamp" style={{ marginBottom: 12 }}>
        {rows.fetchedAt ? `Read ${rows.fetchedAt.toLocaleTimeString()}` : "Reading…"}
        {rows.data && ` · ${rows.data.activity.length} entries`}
      </p>

      {/* The integrity readout. A tampered record must be impossible to miss. */}
      <div className="chain" data-ok={String(chain.data?.ok ?? true)}>
        <span>{chain.data?.ok === false ? "◆" : "◇"}</span>
        <span>
          {chain.loading
            ? "Verifying the audit chain…"
            : chain.error
              ? `Could not verify the chain: ${chain.error}`
              : chain.data?.ok
                ? `Audit chain intact — ${chain.data.detail}`
                : `AUDIT CHAIN BROKEN — ${chain.data?.detail}. Every entry after
                   the break is unverifiable. Do not trust this log until you
                   know why.`}
        </span>
        <span className="spacer" />
        <span className="stamp">
          {chain.fetchedAt ? `checked ${chain.fetchedAt.toLocaleTimeString()}` : ""}
        </span>
      </div>

      {/* The learning layer: what it says, how honest it has been, who is
          serving. Read-only, like everything else on this screen. */}
      <Judgment data={judgment.data ?? null} error={judgment.error ?? null} />

      <div className="filters">
        <select value={moduleFilter} onChange={(e) => setFilter("module", e.target.value)}>
          <option value="">All modules</option>
          {(overview.data?.modules ?? []).map((m) => (
            <option key={m.module} value={m.module}>{m.module}</option>
          ))}
        </select>
        <select value={kindFilter} onChange={(e) => setFilter("kind", e.target.value)}>
          <option value="">Scans, findings and actions</option>
          <option value="scan">Scans only</option>
          <option value="finding">Findings only</option>
          <option value="action">Actions only</option>
        </select>
        <input type="date" value={dayFilter} aria-label="Day"
               onChange={(e) => setFilter("day", e.target.value)} />
      </div>

      {rows.error && <p className="errorbar">Could not load activity: {rows.error}</p>}

      {rows.data && rows.data.activity.length === 0 && (
        <p className="empty">
          Nothing matches. That is not the same as nothing happening — check the
          filters, and the Status screen for modules that are not being watched.
        </p>
      )}

      {rows.data && rows.data.activity.length > 0 && (
        <div className="panel">
          <table className="sheet">
            <thead>
              <tr>
                <th>When</th><th>Kind</th><th>Subject</th><th>State</th><th>What</th>
              </tr>
            </thead>
            <tbody>
              {rows.data.activity.map((row) => {
                const key = `${row.kind}-${row.id ?? row.at}-${row.subject}`;
                const expandable = row.kind !== "scan";
                return (
                  <Fragment key={key}>
                    <tr className={expandable ? "expandable" : ""}
                        onClick={() => expandable && setOpen(open === key ? null : key)}>
                      <td style={{ whiteSpace: "nowrap" }}>
                        {new Date(row.at).toLocaleString()}
                      </td>
                      <td>
                        <span className="k" data-kind={row.kind}
                              style={{ fontSize: 9.5, letterSpacing: ".14em",
                                       textTransform: "uppercase" }}>
                          {row.kind}
                        </span>
                      </td>
                      <td>{row.subject}</td>
                      <td>
                        {row.health
                          ? <HealthBadge health={normaliseHealth(row.health)} />
                          : <span className="empty">—</span>}
                      </td>
                      <td>{row.detail}</td>
                    </tr>
                    {open === key && (
                      <tr className="detail">
                        <td colSpan={5}>
                          {row.body
                            ? <pre className="pre">{row.body}</pre>
                            : <span className="empty">Nothing further recorded.</span>}
                          {row.kind === "finding" && row.id !== null && (
                            <div style={{ marginTop: 8 }}>
                              <a href={`/f/${row.id}`}>Permalink</a>
                            </div>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
