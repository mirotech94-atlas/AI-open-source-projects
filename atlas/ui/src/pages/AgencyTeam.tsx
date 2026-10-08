/**
 * What the old Agents tab (Fleet) showed that the Agency room did not, moved
 * here whole when the two tabs became one (Miro, 2026-09-29: "keep only
 * agency"): the "Run all agents" warm-up, the org chart with each business's
 * deep dive (checks, last runs, memory scope), and the live feed of runs.
 * Every read keeps its own read time; nothing here writes anything except
 * the tool-free warm-up the Agents tab already offered.
 */
import { useEffect, useRef, useState, type JSX } from "react";
import { api, type AgentFeedItem, type AgentSummary } from "../api";
import { current } from "../core/core-tiles";
import { useFresh, type Fresh } from "../hooks/useFresh";
import { checkReading } from "./fleetModel";

/** How the "Run all agents" button waits for its own results to land: it
 *  polls the roster every 5s (its own read, not the world's) and gives up
 *  after this many polls rather than polling forever if a provider is down
 *  and an agent's row never lands. */
const WARM_POLL_MS = 5000;
// Three minutes: a sweep that reaches a cold local model (inbox on ollama) runs
// past one, and a button that gave up at 60 s invited a second, refused click.
const WARM_MAX_POLLS = 36;

const CONCIERGE = "concierge";
/** How many feed rows to search for a business's own runs. The Live feed
 *  panel shows the newest 40 of these -- the same read, sliced twice. */
export const FEED_SEARCH_LIMIT = 200;
const FEED_SHOWN = 40;

function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString([], {
    hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

/** "Run all agents": kick off the background warm-up, then reload the
 *  roster every 5 s until every agent's `model_at` has caught up to the
 *  click, or WARM_MAX_POLLS is reached. The click itself never blocks. */
export function RunAllAgents({
  roster, reload,
}: { roster: Fresh<{ agents: AgentSummary[] }>; reload: () => void }): JSX.Element {
  const [warming, setWarming] = useState(false);
  const [warmError, setWarmError] = useState<string | null>(null);
  const startedAt = useRef<number | null>(null);
  const polls = useRef(0);
  const agents = roster.data?.agents ?? [];

  useEffect(() => {
    if (!warming) return;
    const id = window.setInterval(() => {
      polls.current += 1;
      reload();
      if (polls.current >= WARM_MAX_POLLS) setWarming(false);
    }, WARM_POLL_MS);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [warming]);

  useEffect(() => {
    if (!warming || startedAt.current === null || agents.length === 0) return;
    const caughtUp = agents.every((a) =>
      a.model_at !== null && new Date(a.model_at).getTime() >= startedAt.current!);
    if (caughtUp) setWarming(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agents, warming]);

  async function start(): Promise<void> {
    setWarmError(null);
    try {
      await api.warmAgents();
      startedAt.current = Date.now();
    } catch (err) {
      const why = err instanceof Error ? err.message : String(err);
      // A sweep already in flight is not a failure: follow that one.
      if (!/already warming/i.test(why)) {
        setWarmError(why);
        return;
      }
      startedAt.current = startedAt.current ?? Date.now() - 5 * 60_000;
    }
    polls.current = 0;
    setWarming(true);
    reload();
  }

  const answered = agents.filter((a) => startedAt.current !== null && a.model_at !== null
    && new Date(a.model_at).getTime() >= startedAt.current).length;
  return (
    <>
      <button type="button" className="btn" onClick={() => void start()} disabled={warming}
              title="A tool-free self-test for every agent, so its last run and model are real">
        {warming ? `Running… ${answered} of ${agents.length} answered` : "Run all agents"}
      </button>
      {warmError && <p className="errorbar">Could not start the warm-up: {warmError}</p>}
    </>
  );
}

/** A business's own second-tier reports: the role agents it hands off to,
 *  minus concierge and any other business. Two levels deep, no third. */
function roleReports(
  business: AgentSummary, roster: AgentSummary[], businessNames: Set<string>,
): AgentSummary[] {
  return business.handoff
    .filter((name) => name !== CONCIERGE && !businessNames.has(name))
    .map((name) => roster.find((a) => a.name === name) ?? {
      name, title: name, display: name, brief: "", need: null, available: false,
      providers: [], tools: 0, last_run_at: null, last_outcome: null,
      runs_24h: 0, model: null, model_at: null, handoff: [], memory_scope: [],
      ready: false, reasons: [], group: "functions" as const, next_shift_at: null,
    });
}

/** The deep-dive drawer for one business: its checks (each with its own
 *  observed time), its runs from the feed already read, and its memory
 *  scope. Nothing here writes anything. */
function DeepDive({
  business, feed, onClose,
}: {
  business: AgentSummary;
  feed: Fresh<{ items: AgentFeedItem[] }>;
  onClose: () => void;
}): JSX.Element {
  const detail = useFresh(() => api.moduleDetail(business.name), [business.name]);
  // Only a read that succeeded this time: after a failed refresh the old
  // checks are not drawn under the error as if they were current.
  const detailNow = current(detail);
  const graph = useFresh(() => api.memoryGraph(), [business.name]);

  const allItems = feed.data?.items ?? [];
  const runs = allItems.filter((i) => i.agent === business.name || i.to === business.name);
  const scoped = new Set(
    (graph.data?.edges ?? [])
      .filter((e) => e.from === `agent:${business.name}`)
      .map((e) => e.to),
  );
  const scopeNodes = (graph.data?.nodes ?? []).filter((n) => scoped.has(n.id));

  return (
    <div className="panel" style={{ marginBottom: 14 }}>
      <div className="inner">
        <header style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
          <h3 className="label">{business.title} — deep dive</h3>
          <button type="button" className="linklike" onClick={onClose}>Close ×</button>
        </header>

        <h4 className="label" style={{ marginTop: 10 }}>Checks</h4>
        <p className="stamp">
          {detail.fetchedAt ? `Read ${detail.fetchedAt.toLocaleTimeString()}` : "Reading…"}
        </p>
        {detail.error && (
          <p className="errorbar">Could not read {business.name}: {detail.error}</p>
        )}
        {detailNow && !detailNow.module.enabled && (
          <p className="empty">
            Module disabled — {detailNow.module.disabled_reason ?? "no reason given"}
          </p>
        )}
        <ul className="deepdive-checks">
          {(detailNow?.checks ?? []).map((c) => {
            // The server's `current` -- its staleness rule applied -- never
            // the raw last history entry: a stale OK is UNKNOWN, not cyan.
            const { health, at, stale } = checkReading(c);
            const when = at ? new Date(at).toLocaleString([], { hour12: false }) : null;
            return (
              <li key={c.key} data-health={health}>
                <span className="n">{c.name}</span>
                <span className="h">{health.toUpperCase()}</span>
                <span className="t">
                  {when ? (stale ? `stale — last read ${when}` : when) : "never measured"}
                </span>
              </li>
            );
          })}
          {detailNow && detailNow.checks.length === 0 && (
            <li className="empty">No checks registered for this module.</li>
          )}
        </ul>

        <h4 className="label">Last runs</h4>
        <p className="stamp">
          {feed.fetchedAt ? `Read ${feed.fetchedAt.toLocaleTimeString()}` : "Reading…"}
          {` · ${runs.length} of ${allItems.length} runs searched`}
        </p>
        <ul className="fleet-feed">
          {runs.slice(0, 10).map((item, i) => (
            <li key={`${item.at}-${i}`}>
              <span className="who">
                {item.agent}
                {item.to ? ` → ${item.to}` : ""}
              </span>
              <span className="task">{item.task || "—"}</span>
              <span className="outcome" title={item.outcome ?? undefined}>
                {item.outcome || "—"}
              </span>
              <span className="t">{clock(item.at)}</span>
            </li>
          ))}
          {runs.length === 0 && <li className="empty">No runs found for {business.name}.</li>}
        </ul>

        <h4 className="label">Memory scope</h4>
        <p className="stamp">
          {graph.fetchedAt ? `Read ${graph.fetchedAt.toLocaleTimeString()}` : "Reading…"}
        </p>
        {graph.error && <p className="errorbar">Could not read the memory graph: {graph.error}</p>}
        <div className="chips">
          {scopeNodes.map((n) => (
            <span className="chip" key={n.id} title={`${n.total} stored`}>
              {n.id.replace(/^ns:/, "")} · {n.total}
            </span>
          ))}
          {graph.data && scopeNodes.length === 0 && (
            <span className="empty">This agent's memory_scope names no namespace.</span>
          )}
        </div>
      </div>
    </div>
  );
}

/** Concierge at the top, the agents it hands off to below, and each
 *  business's own role reports under it. A business opens its deep dive. */
export function OrgChart({
  roster, feed, reloadKey,
}: {
  roster: AgentSummary[];
  feed: Fresh<{ items: AgentFeedItem[] }>;
  reloadKey: number;
}): JSX.Element | null {
  const overview = useFresh(() => api.overview(), [reloadKey]);
  const [selected, setSelected] = useState<string | null>(null);
  const concierge = roster.find((a) => a.name === CONCIERGE) ?? null;
  // A business is a roster agent concierge hands off to that is ALSO a real
  // module (`/api/overview`'s own list): concierge.handoff mixes businesses
  // with role agents, and nothing in AgentSummary alone tells them apart.
  const moduleNames = new Set((overview.data?.modules ?? []).map((m) => m.module));
  const reports = concierge ? roster.filter((a) => concierge.handoff.includes(a.name)) : [];
  const selectedAgent = selected ? roster.find((a) => a.name === selected) ?? null : null;
  if (!concierge) return null;

  return (
    <>
      {overview.error && (
        <p className="errorbar">
          Could not tell businesses from role agents: {overview.error}
          {" — every org chart node below is drawn plain and non-clickable until this reads again."}
        </p>
      )}
      <div className="panel" style={{ marginBottom: 14 }}>
        <div className="inner">
          <h3 className="label">Org chart</h3>
          <p className="stamp">Click a business for its checks, last runs and memory scope.</p>
          <div className="org-chart">
            <div className="org-top">
              <span className="org-node">{concierge.title}</span>
            </div>
            {reports.length > 0 && (
              <div className="org-children"
                   style={{ gridTemplateColumns: "repeat(auto-fit, minmax(128px, 1fr))" }}>
                {reports.map((a) => {
                  const isBusiness = moduleNames.has(a.name);
                  const children = isBusiness ? roleReports(a, roster, moduleNames) : [];
                  return (
                    <div className="org-branch" key={a.name}>
                      {isBusiness ? (
                        <button type="button" className="org-node org-node-clickable"
                                aria-expanded={selected === a.name}
                                onClick={() => setSelected((cur) => (cur === a.name ? null : a.name))}>
                          {a.title}
                        </button>
                      ) : (
                        <span className="org-node">{a.title}</span>
                      )}
                      {children.length > 0 && (
                        <div className="org-grandchildren">
                          {children.map((r) => (
                            <span className="org-node org-leaf" key={r.name}>{r.title}</span>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>
      {selectedAgent && (
        // Keyed on the name so switching businesses mounts a fresh DeepDive
        // rather than showing the previous one's checks under the new title.
        <DeepDive key={selectedAgent.name} business={selectedAgent} feed={feed}
                  onClose={() => setSelected(null)} />
      )}
    </>
  );
}

/** Every agent's newest runs, from `/api/agents/feed`, with their times. */
export function LiveFeed({ feed }: { feed: Fresh<{ items: AgentFeedItem[] }> }): JSX.Element {
  return (
    <div className="panel">
      <div className="inner">
        <h3 className="label">Live feed</h3>
        <p className="stamp">
          {feed.fetchedAt ? `Read ${feed.fetchedAt.toLocaleTimeString()}` : "Reading…"}
        </p>
        {feed.error && <p className="errorbar">Feed unavailable: {feed.error}</p>}
        <ul className="fleet-feed">
          {(feed.data?.items ?? []).slice(0, FEED_SHOWN).map((item, i) => (
            <li key={`${item.at}-${i}`}>
              <span className="who">
                {item.agent}
                {item.to ? ` → ${item.to}` : ""}
              </span>
              <span className="task">{item.task || "—"}</span>
              <span className="outcome" title={item.outcome ?? undefined}>
                {item.outcome || "—"}
              </span>
              <span className="t">{clock(item.at)}</span>
            </li>
          ))}
          {feed.data && feed.data.items.length === 0 && (
            <li className="empty">No runs yet.</li>
          )}
        </ul>
      </div>
    </div>
  );
}
