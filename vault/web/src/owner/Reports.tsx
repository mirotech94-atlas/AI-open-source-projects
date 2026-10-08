import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useToast } from "../design";
import { buildReport, reportsPayload } from "../lib/api";
import { shortDate, useActiveTenant } from "./net";

/** Monthly summary and audit export -- the reference's Reports, on the
 *  customer's own report data: the value strip's four figures, where the
 *  work went, and the library of what has been built. */
function Reports() {
  const tenant = useActiveTenant();
  const toast = useToast();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["reports"], queryFn: reportsPayload, enabled: tenant !== null });
  // Two documents, named for what they are. The brief is the executive read
  // -- summary, availability, compliance, recommendations -- and the full
  // report is every section, organised under its Parts. Neither is "the
  // first preset in the list", which is how a Generate button ends up
  // building the wrong thing.
  const build = useMutation({
    mutationFn: (kind: "executive" | "full") => {
      const preset = q.data!.presets.find((p) => p.key === kind) ?? q.data!.presets[0];
      const label = kind === "executive" ? "Executive brief" : "Full report";
      return buildReport(30, preset.key, preset.sections, `${tenant?.name ?? "Vault"} · ${label} · last 30 days`);
    },
    onSuccess: (card) => {
      toast.show("Report built.", "good");
      qc.setQueryData(["reports"], (old: typeof q.data) => old ? { ...old, library: [card, ...old.library] } : old);
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  if (tenant === null) return <p className="g-own-lede">Pick a customer in the bar to see their reports.</p>;
  if (q.isPending) return <p role="status">Reading reports…</p>;
  if (q.isError && !q.data) return <div role="alert">Could not read reports. <button onClick={() => void q.refetch()}>Retry</button></div>;

  const r = q.data!;
  const max = Math.max(1, ...r.value.work.map((w) => w.count));

  return (
    <div className="g-own-page">
      <div className="g-own-intro">
        <p className="g-own-lede">
          A summary of the last {r.value.days} days, built from the same record an auditor or insurer would ask for.
          {r.schedule.monthly ? " A monthly copy is generated on the first of the month." : ""}
        </p>
        <span className="g-own-intro-actions">
          <button type="button" className="g-own-btn" disabled={build.isPending || r.presets.length === 0}
                  onClick={() => build.mutate("full")}>Full report</button>
          <button type="button" className="g-own-btn g-own-btn-primary" disabled={build.isPending || r.presets.length === 0}
                  onClick={() => build.mutate("executive")}>{build.isPending ? "Building…" : "Executive brief"}</button>
        </span>
      </div>

      <section aria-label="The last month" className="g-own-tiles">
        {r.value.stats.map((s) => (
          <div key={s.key} className="g-own-tile g-own-tile-static g-own-tile-fact">
            <span className="g-own-tile-top"><span className="g-own-tile-l">{s.label}</span></span>
            <span className="g-own-tile-v">{s.value}</span>
            {s.note && <span className="g-own-tile-note">{s.note}</span>}
          </div>
        ))}
      </section>

      <div className="g-own-split g-own-split-reports">
        <section aria-label="Where the work went" className="g-own-section">
          <h2>Where the work went</h2>
          <p className="g-own-sub">Actions by category, last {r.value.days} days</p>
          {r.value.work.length === 0 ? (
            <p className="g-own-quiet">Nothing has needed doing yet.</p>
          ) : (
            <ul className="g-own-bars">
              {r.value.work.map((w) => (
                <li key={w.label}>
                  <span className="g-own-bars-row"><span>{w.label}</span><span className="g-own-mono">{w.count}</span></span>
                  <span className="g-own-bars-track"><span className="g-own-bars-fill" style={{ width: `${Math.round((w.count / max) * 100)}%` }} /></span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section aria-label="Past reports" className="g-own-section">
          <h2>Past reports <span className="g-own-meta">{r.library.length}</span></h2>
          {r.library.length === 0 ? (
            <p className="g-own-quiet">Nothing built yet. Generate one above.</p>
          ) : (
            <ul className="g-own-rows">
              {r.library.map((c) => (
                <li key={c.id} className="g-own-report">
                  <span className="g-own-report-main">
                    <span className="g-own-report-t">{c.title || `${c.period_days}-day ${c.preset}`}</span>
                    <span className="g-own-report-sub">{shortDate(c.created)}{c.score !== null ? ` · score ${c.score}` : ""}</span>
                  </span>
                  <a className="g-own-report-link" href={c.url} target="_blank" rel="noreferrer">open</a>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}

export default Reports;
