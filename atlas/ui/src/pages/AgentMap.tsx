import { useState, type JSX } from "react";
import { api, type AgentSummary } from "../api";
import { NeuralMap, type NodeModel } from "../components/NeuralMap";
import { cardName } from "../components/neuralLayout";
import { connectionsFor } from "./agentMapLayout";

/** The detail panel's links, with direction kept: who this agent hands
 *  work to (its own `handoff`, roster agents only), who hands work to it,
 *  and who shares a memory namespace with it -- each sorted. */
export interface DetailLinks { out: string[]; in: string[]; memory: string[] }

export function detailLinks(name: string, agents: AgentSummary[]): DetailLinks {
  const self = agents.find((a) => a.name === name);
  if (!self) return { out: [], in: [], memory: [] };
  const known = new Set(agents.map((a) => a.name));
  const out = [...new Set(self.handoff)].filter((h) => h !== name && known.has(h)).sort();
  const incoming = agents.filter((a) => a.name !== name && a.handoff.includes(name))
    .map((a) => a.name).sort();
  const memory = [...connectionsFor(name, agents).memory].sort();
  return { out, in: incoming, memory };
}

export { cardName };

// Floored, not rounded, matching Fleet's own `rel()`: a run 90s old must
// read "1m ago", never "2m ago".
function rel(iso: string | null): string {
  if (!iso) return "never";
  const delta = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(Math.abs(delta) / 60000);
  const text = mins < 1 ? "<1m" : mins < 60 ? `${mins}m` : `${Math.floor(mins / 60)}h`;
  return delta >= 0 ? `${text} ago` : `in ${text}`;
}

function modelLine(a: Pick<AgentSummary, "model" | "model_at">): string {
  if (a.model) return `${a.model} · ${rel(a.model_at)}`;
  if (a.model_at) return `no model answered · ${rel(a.model_at)}`;
  return "not yet run";
}

/** The detail panel for the selected card: brief, tools count, handoffs,
 * readiness reasons, last run, model, next shift, and a per-agent
 * "Run self-test" button (POST /api/agents/warm with `name` -- still
 * tool-free, still never the executor). Exported for the Agency room, which
 * shows it under the neuron picked on its own map. */
export function Detail({
  agent, links, onWarmed,
}: {
  agent: AgentSummary;
  links: DetailLinks;
  onWarmed: (() => void) | undefined;
}): JSX.Element {
  const [running, setRunning] = useState(false);
  // A peer it already hands work to is listed once, under Handoffs -- the
  // same rule the map follows (one synapse per pair, handoff first).
  const memoryOnly = links.memory.filter((n) => !links.out.includes(n) && !links.in.includes(n));
  const [error, setError] = useState<string | null>(null);

  async function runSelfTest(): Promise<void> {
    setError(null);
    setRunning(true);
    try {
      await api.warmAgents(agent.name);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setRunning(false);
      return;
    }
    onWarmed?.();
    // The warm-up itself runs in the background; one more refresh a few
    // seconds later catches most of them without polling forever.
    window.setTimeout(() => {
      setRunning(false);
      onWarmed?.();
    }, 4000);
  }

  return (
    <div className="agent-map-detail" data-testid="agent-map-detail">
      <header>
        <span className="n">{cardName(agent)}</span>
        <span className="t">{agent.title}</span>
      </header>
      {agent.persona && <p className="agent-map-detail-brief">{agent.persona.specialty}</p>}
      <p className="agent-map-detail-brief">{agent.brief}</p>
      <div className="line">{agent.tools} tool{agent.tools === 1 ? "" : "s"}</div>
      <dl className="agent-map-facts">
        <dt>Last run</dt><dd>{rel(agent.last_run_at)} — {agent.last_outcome || "—"}</dd>
        <dt>Model</dt><dd>{modelLine(agent)}</dd>
        <dt>Next shift</dt><dd>{agent.next_shift_at ? rel(agent.next_shift_at) : "off"}</dd>
        <dt>Ready</dt><dd>{agent.ready ? "yes" : "no"}</dd>
      </dl>
      {!agent.ready && (
        <ul className="agent-map-detail-reasons">
          {agent.reasons.map((r) => <li key={r}>{r}</li>)}
        </ul>
      )}
      {links.out.length > 0 && (
        <div className="agent-map-links">
          <span className="k">Hands work to</span>
          <div className="chips">
            {links.out.map((h) => <span className="chip" data-kind="handoff" key={h}>{h}</span>)}
          </div>
        </div>
      )}
      {links.in.length > 0 && (
        <div className="agent-map-links">
          <span className="k">Takes work from</span>
          <div className="chips">
            {links.in.map((h) => <span className="chip" data-kind="handoff" key={h}>{h}</span>)}
          </div>
        </div>
      )}
      {memoryOnly.length > 0 && (
        <div className="agent-map-links">
          <span className="k">Shares memory with</span>
          <div className="chips">
            {memoryOnly.map((h) => <span className="chip" data-kind="memory" key={h}>{h}</span>)}
          </div>
        </div>
      )}
      {error && <p className="errorbar">Could not start the self-test: {error}</p>}
      <button type="button" className="linklike" onClick={runSelfTest} disabled={running}>
        {running ? "Running self-test…" : "Run self-test"}
      </button>
    </div>
  );
}

export function AgentMap({
  agents, readFailed = false, onSelect, onWarmed, modelFor,
}: {
  agents: AgentSummary[];
  readFailed?: boolean;
  /** Home: each node's model and its state (`NeuralMap`). */
  modelFor?: ((name: string) => NodeModel | null) | undefined;
  /** Called with an agent's name on click or Enter/Space. */
  onSelect: (name: string) => void;
  /** Called after the detail panel's "Run self-test" starts, and again once
   *  it has likely finished, so the caller can reload the roster. */
  onWarmed?: () => void;
}): JSX.Element {
  const [selected, setSelected] = useState<string | null>(null);
  const selectedAgent = selected ? agents.find((a) => a.name === selected) ?? null : null;

  function activate(name: string): void {
    setSelected((cur) => (cur === name ? null : name));
    onSelect(name);
  }

  // The map itself is the shared neural view (the Agency tab draws the same
  // one); this page adds the selected agent's details and self-test.
  return (
    <div className="agent-map" data-has-selection={selected !== null || undefined}>
      <NeuralMap agents={agents} readFailed={readFailed} selected={selected}
                 onActivate={activate} label="Agent map" modelFor={modelFor} />
      {selectedAgent && <Detail agent={selectedAgent} links={detailLinks(selectedAgent.name, agents)}
                                     onWarmed={onWarmed} />}
      {readFailed && (
        <p className="agent-map-caption stamp">
          Could not read the roster; every neuron above is UNKNOWN, not stale.
        </p>
      )}
    </div>
  );
}
