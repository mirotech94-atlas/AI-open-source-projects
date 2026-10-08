import { useState, type JSX } from "react";
import { api, type PerplexityBudget } from "../api";
import { HealthBadge } from "../components/Health";
import { useFresh } from "../hooks/useFresh";

const TIER_WORD: Record<number, string> = {
  1: "1 · auto",
  2: "2 · ask",
  3: "3 · never",
};

/** Perplexity's month against its cap, with the time the count was read.
 *  No count is UNKNOWN — grey, dashed, the word visible — never a zero. */
function PerplexityLine({ budget }: { budget: PerplexityBudget | undefined }): JSX.Element {
  if (!budget || budget.used === null) {
    return (
      <p className="stamp" data-health="unknown" style={{ marginBottom: 12 }}>
        <HealthBadge health="unknown" /> Perplexity (research over MCP):{" "}
        {budget?.reason ?? "not reported"}
      </p>
    );
  }
  const read = budget.read_at ? new Date(budget.read_at).toLocaleTimeString() : "?";
  const atCap = budget.used >= budget.cap;
  return (
    <p className="stamp" style={{ marginBottom: 12 }}>
      <HealthBadge health={atCap ? "degraded" : "ok"} /> Perplexity (research over
      MCP): {budget.used} / {budget.cap} calls this month · read {read}
      {atCap && " · cap reached, every caller is refused"}
    </p>
  );
}

export function Tools({ reloadKey }: { reloadKey: number }): JSX.Element {
  const tools = useFresh(() => api.tools(), [reloadKey]);
  const [filter, setFilter] = useState("");

  const needle = filter.trim().toLowerCase();
  const actions = (tools.data?.actions ?? []).filter((a) =>
    !needle || a.name.toLowerCase().includes(needle)
    || a.summary.toLowerCase().includes(needle));
  const skills = (tools.data?.skills ?? []).filter((s) =>
    !needle || s.name.toLowerCase().includes(needle)
    || s.triggers.some((t) => t.includes(needle)));

  return (
    <>
      <h2 className="label">Tools &amp; skills</h2>
      <p className="stamp" style={{ marginBottom: 12 }}>
        {tools.fetchedAt ? `Read ${tools.fetchedAt.toLocaleTimeString()}` : "Reading…"}
        {tools.data && ` · ${tools.data.count} actions · ${tools.data.skills.length} skills`}
      </p>

      {tools.error && <p className="errorbar">Could not read the catalog: {tools.error}</p>}

      {tools.data && <PerplexityLine budget={tools.data.perplexity} />}

      <div className="filters">
        <input value={filter} placeholder="filter by name or trigger"
               onChange={(e) => setFilter(e.target.value)} />
      </div>

      <div className="panel" style={{ marginBottom: 14 }}>
        <div className="inner">
          <h3 className="label">Action catalog</h3>
          {/* The whole catalog, including what is refused. A screen that
              listed only the permitted actions would make Tier 3 invisible,
              and Tier 3 being visible is how it stays deliberate. */}
          <p className="stamp" style={{ marginBottom: 8 }}>
            The catalog is closed: the reasoner picks a name from this list and
            can never construct one. Tier 3 is permanently denied.
          </p>
          {tools.data && actions.length === 0 ? (
            <p className="empty">
              {needle ? `Nothing matching “${filter}”.` : "No actions registered."}
            </p>
          ) : (
            <table className="sheet">
              <thead>
                <tr>
                  <th>Action</th><th>Module</th><th>Tier</th>
                  <th>What it does</th><th>Reversible</th>
                </tr>
              </thead>
              <tbody>
                {actions.map((a) => (
                  <tr key={a.name} data-health={a.allowed ? undefined : "unknown"}>
                    <td>{a.name}</td>
                    <td>{a.module || "—"}</td>
                    <td title={a.reason}>
                      {TIER_WORD[a.tier] ?? a.tier}
                      {!a.allowed && " · denied"}
                    </td>
                    <td>
                      {/* A refused action never registered, so there is no
                          summary to show. The reason is the honest answer. */}
                      {a.summary || <span className="stamp">{a.reason}</span>}
                    </td>
                    <td>{a.allowed ? (a.reversible ? "yes" : "no") : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div className="panel">
        <div className="inner">
          <h3 className="label">Skills</h3>
          {tools.data && skills.length === 0 ? (
            <p className="empty">
              {needle ? `Nothing matching “${filter}”.` : "No skills registered."}
            </p>
          ) : (
            <table className="sheet">
              <thead>
                <tr><th>Skill</th><th>Triggers on</th><th>For</th><th>Priority</th></tr>
              </thead>
              <tbody>
                {skills.map((s) => (
                  <tr key={s.name}>
                    <td>{s.name}</td>
                    <td>{s.triggers.join(", ")}</td>
                    <td>{s.agents.length ? s.agents.join(", ") : "any agent"}</td>
                    <td>{s.priority}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </>
  );
}
