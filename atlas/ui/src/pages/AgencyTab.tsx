import { useEffect, useState, type JSX } from "react";
import { api, type AgentSummary, type Approvals, type Overview } from "../api";
import { useFresh } from "../hooks/useFresh";
import { AgentMap, RadialMap } from "./AgentMap";
import { agencyGlance, type GlanceFigure } from "./agencyGlance";
import { AgentActivityPanel } from "./AgentActivity";
import { PreviewsPanel } from "./PreviewsPanel";
import { PREVIEW_AGENT, previewBadge } from "./agencyMapModel";
import { stamp } from "./cmdParts";
import { readOf, type Read } from "./homeModel";
import { WORDMARK } from "./deskModel";
import { isRetiredAgent } from "./roster";

/**
 * Agency: the agency map and nothing else (the owner, 2026-10-28: "the agency
 * tab should have only the agency map containing all the agents that are
 * present"). Every agent on the roster (`GET /api/agents`) is a neuron with
 * its live state; a roster that cannot be read draws every neuron UNKNOWN,
 * grey and dashed. The `sites` neuron carries the previews waiting on his
 * card. Under the map, the agents' activity panel (`AgentActivity.tsx`):
 * now, yesterday and issues per agent; then the SEO panel (`SeoPanel.tsx`):
 * Search Console and PageSpeed per site. What used to sit below the map is
 * the Command Center's Agency desk.
 * 2026-10-30 (the owner: "keep only the agent map and all agent cards and what
 * they are doing"): the tab is the map and the agents' activity cards, nothing
 * else. AgencyMore, SeoPanel and the Agency desk are no longer mounted here;
 * their components stay in the tree.
 * Reads only, plus the map's own per-agent self-test.
 */

export interface AgencyMapProps {
  roster: Read<{ agents: AgentSummary[] }>;
  approvals: Read<Approvals>;
  now: number;
  onWarmed?: (() => void) | undefined;
  /** Design v2: the radial map, with selection held by the tab. */
  radial?: boolean;
  selected?: string | null;
  onSelect?: ((name: string | null) => void) | undefined;
  /** The roster read is recent: only then does light move along a link. */
  fresh?: boolean;
}

/** A roster read counts as fresh for two minutes (the shell re-reads every 30 s). */
export const ROSTER_FRESH_MS = 120_000;

/** The whole tab from its reads: pure, so it can be tested. */
export function AgencyMapBody({
  roster, approvals, now, onWarmed, radial = false, selected = null, onSelect, fresh = false,
}: AgencyMapProps): JSX.Element {
  const r = readOf(roster);
  // The map draws the roster the server returns (eight agents); a stale row
  // carrying a retired name is never drawn as a live agent.
  // Radial map: after a failed read the names the last good read held are
  // still drawn, every one UNKNOWN (no figure or state is carried over).
  const source = radial ? (roster.data?.agents ?? []) : (r.data?.agents ?? []);
  const agents = source.filter((a) => !isRetiredAgent(a.name));
  const badge = previewBadge(approvals, now);
  if (radial) {
    const failedRead = r.error !== null;
    return (
      <div className="desk desk-map">
        <header className="desk-map-head">
          <h2>Agent network</h2>
          <span className="desk-map-sub">Who is on the roster and how they hand work on.</span>
          <span className="cmd-at">
            {failedRead ? <span className="cmd-word-unknown">Unknown</span>
              : r.data ? `${agents.length} agents` : "reading…"}{" "}
            · {stamp(roster.fetchedAt, failedRead)}
          </span>
        </header>
        <RadialMap agents={agents} readFailed={failedRead} fresh={fresh} now={now}
                   selected={selected} onSelect={onSelect ?? (() => {})} onWarmed={onWarmed} />
      </div>
    );
  }
  return (
    <div className="desk desk-map">
      <header className="desk-map-head">
        <h2>Global neural network topology</h2>
        <span className="cmd-at">
          {r.error !== null ? <span className="cmd-word-unknown">Unknown</span>
            : r.data ? `${agents.length} agents` : "reading…"}{" "}
          · {stamp(roster.fetchedAt, r.error !== null)}
        </span>
      </header>
      <AgentMap fill agents={agents} readFailed={r.error !== null} onSelect={() => {}}
                onWarmed={onWarmed}
                badgeFor={(name) => (name === PREVIEW_AGENT ? badge : null)} />
    </div>
  );
}

const GLANCE_CELLS = [
  { cell: "registered", label: "Agents registered", from: "agents" },
  { cell: "failing", label: "Agents failing", from: "agents" },
  { cell: "waiting", label: "Waiting on you", from: "approvals" },
  { cell: "checks", label: "Checks degraded", from: "overview" },
] as const;

/** Four figures above the map, each from a read the tab already holds and
 *  each stamped. A figure whose read failed (or has not come back) is
 *  UNKNOWN, dashed grey, with the word: never 0. */
export function AgencyGlancePanel({ agents, approvals, overview }: {
  agents: Read<{ as_of: string; agents: AgentSummary[] }>;
  approvals: Read<Approvals>;
  overview: Read<Overview>;
}): JSX.Element {
  const g = agencyGlance({ agents, approvals, overview });
  const figs: Record<(typeof GLANCE_CELLS)[number]["cell"], GlanceFigure> = {
    registered: g.registered, failing: g.failing, waiting: g.waiting, checks: g.degradedChecks,
  };
  const src = { agents, approvals, overview };
  return (
    <section className="desk-sec agency-glance" aria-label="Agency at a glance">
      {GLANCE_CELLS.map((c) => {
        const f = figs[c.cell];
        const read = src[c.from];
        if (f.value === null) {
          const reading = read.error === null && read.data === null;
          return (
            <div className="ag-cell" data-cell={c.cell} data-health="unknown" key={c.cell}>
              <span className="v2-eyebrow">{c.label}</span>
              <span className="cmd-word-unknown">Unknown</span>
              <span className="stamp">{reading ? "reading…" : "read failed"}</span>
            </div>
          );
        }
        const bad = c.cell === "failing" && f.value > 0;
        return (
          <div className="ag-cell" data-cell={c.cell}
               data-health={bad ? "failing" : undefined} key={c.cell}>
            <span className="v2-eyebrow">
              {bad && <i className="ag-flag" aria-hidden="true" />}{c.label}
            </span>
            <span className="v2-fig">{f.value}</span>
            <span className="stamp">{stamp(f.readAt)}</span>
          </div>
        );
      })}
    </section>
  );
}

export function AgencyTab({ reloadKey, overview }: {
  reloadKey: number;
  /** The shell's own overview read; no extra call is made here. */
  overview?: Read<Overview> | undefined;
}): JSX.Element {
  const roster = useFresh(() => api.agents(), [reloadKey]);
  const approvals = useFresh(() => api.approvals(), [reloadKey]);
  const [selected, setSelected] = useState<string | null>(null);
  useEffect(() => {
    const before = document.title;
    document.title = `${WORDMARK} · Agency`;
    return (): void => { document.title = before; };
  }, []);
  const now = Date.now();
  const fresh = roster.error === null && roster.fetchedAt !== null
    && now - roster.fetchedAt.getTime() < ROSTER_FRESH_MS;
  const noOverview: Read<Overview> = { data: null, error: "not read", fetchedAt: null };
  return (
    <div className="agency-tab">
      <AgencyGlancePanel agents={roster} approvals={approvals} overview={overview ?? noOverview} />
      <AgencyMapBody roster={roster} approvals={approvals} now={now} onWarmed={roster.reload}
                     radial selected={selected} onSelect={setSelected} fresh={fresh} />
      <AgentActivityPanel reloadKey={reloadKey} roster={roster} selected={selected}
                          onSelect={setSelected} />
      {/* the owner 2026-10-30: the built demo sites, viewable in the portal. */}
      <div className="desk desk-prev-host"><PreviewsPanel reloadKey={reloadKey} /></div>
    </div>
  );
}
