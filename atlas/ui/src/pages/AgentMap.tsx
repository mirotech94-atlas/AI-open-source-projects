import { useState, type JSX } from "react";
import { api, type AgentSummary } from "../api";
import { NeuralMap, type NodeBadge, type NodeModel } from "../components/NeuralMap";
import { cardName } from "../components/neuralLayout";
import { connectionsFor, dotFor, radialLayout, type CardDot } from "./agentMapLayout";
import { rosterRow } from "./opsFloorModel";
import { outcomeLine } from "./outcomeText";

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
        <dt>Last run</dt><dd>{rel(agent.last_run_at)} — {outcomeLine(agent.last_outcome) ?? "—"}</dd>
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
  agents, readFailed = false, onSelect, onWarmed, modelFor, badgeFor, fill = false,
}: {
  /** Fill the parent's width and height (the Agency tab). */
  fill?: boolean;
  agents: AgentSummary[];
  readFailed?: boolean;
  /** The Agency tab: a count under a node's name (`NeuralMap`). */
  badgeFor?: ((name: string) => NodeBadge | null) | undefined;
  /** Home: each node's model and its state (`NeuralMap`). */
  modelFor?: ((name: string) => NodeModel | null) | undefined;
  /** Called with an agent's name on click or Enter/Space. */
  onSelect: (name: string) => void;
  /** Called after the detail panel's "Run self-test" starts, and again once
   *  it has likely finished, so the caller can reload the roster. */
  onWarmed?: (() => void) | undefined;
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
    <div className="agent-map" data-fill={fill || undefined}
         data-has-selection={selected !== null || undefined}>
      <NeuralMap agents={agents} readFailed={readFailed} selected={selected}
                 onActivate={activate} label="Agent map" modelFor={modelFor}
                 badgeFor={badgeFor} fill={fill} />
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

// ------------------------------------------------------------ radial (v2)

/** A node's state on the radial map. A failed roster read, or an agent whose
 *  own status is unknown, is UNKNOWN: grey, dashed, the word. Never ready. */
export type RadialState = CardDot | "failing";

export function radialState(a: AgentSummary, readFailed: boolean, now: number): RadialState {
  if (readFailed) return "unknown";
  const row = rosterRow({ status: a.status ?? null, shift_state: a.shift_state ?? null });
  if (row.state === "unknown") return "unknown";
  if (row.state === "failing") return "failing";
  return dotFor(a, false, now);
}

const STATE_WORD: Record<RadialState, string> = {
  ready: "Ready", working: "Working now", "not-ready": "Not ready",
  unknown: "Unknown", failing: "Failing",
};

const clip = (s: string, n: number): string => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

/**
 * The Agency tab's map: ATLAS in the middle, one node per roster agent on a
 * ring, curved links. Light moves along a link only while the roster read is
 * fresh (`fresh`) and that agent is not UNKNOWN; the motion itself lives in
 * `desk.css` inside prefers-reduced-motion: no-preference. Selection is held
 * by the parent so the agent's card can open with it.
 */
export function RadialMap({
  agents, readFailed = false, fresh = false, selected, onSelect, onWarmed, now = Date.now(),
}: {
  agents: AgentSummary[];
  readFailed?: boolean;
  fresh?: boolean;
  selected: string | null;
  onSelect: (name: string | null) => void;
  onWarmed?: (() => void) | undefined;
  now?: number;
}): JSX.Element {
  const [hover, setHover] = useState<string | null>(null);
  const layout = radialLayout(agents);
  const { frame } = layout;
  const byName = new Map(agents.map((a) => [a.name, a]));
  const focus = hover && byName.has(hover) ? hover : selected;
  const lit = connectionsFor(focus, agents);
  const states = new Map(agents.map((a) => [a.name, radialState(a, readFailed, now)]));
  const flowing = fresh && !readFailed;
  const selectedAgent = selected ? byName.get(selected) ?? null : null;

  const activate = (name: string): void => onSelect(selected === name ? null : name);

  return (
    <div className="agent-map rm" data-radial=""
         data-fresh={flowing || undefined} data-read-failed={readFailed || undefined}
         data-has-selection={selected !== null || undefined}>
      <div className="rm-scroll">
        <svg className="rm-web" viewBox={`0 0 ${frame.w} ${frame.h}`} role="group"
             aria-label="Agent map">
          <g className="rm-links" aria-hidden="true">
            {layout.links.map((l) => {
              if (l.kind === "memory" && !(focus !== null && (l.from === focus || l.to === focus))) {
                return null;
              }
              const touches = focus !== null && (l.from === focus || l.to === focus);
              const live = l.kind === "spoke" && flowing && states.get(l.to) !== "unknown";
              return (
                <path key={l.id} className="rm-link" d={l.d} data-kind={l.kind}
                      data-from={l.from} data-to={l.to}
                      data-lit={touches || undefined} data-flow={live || undefined} />
              );
            })}
          </g>
          <g className="rm-core" transform={`translate(${frame.cx} ${frame.cy})`} aria-hidden="true">
            <circle className="rm-core-disc" r={30} />
            <text className="rm-core-label" y={4} textAnchor="middle">ATLAS</text>
          </g>
          {layout.nodes.map((p) => {
            const a = byName.get(p.name);
            if (!a) return null;
            const st = states.get(p.name) ?? "unknown";
            const role = a.role ?? a.title;
            const linked = lit.handoff.has(p.name) || lit.memory.has(p.name);
            return (
              <g key={p.name} className="rm-node" data-agent={p.name} data-state={st}
                 data-selected={selected === p.name || undefined}
                 data-linked={linked || undefined}
                 transform={`translate(${p.x} ${p.y})`} tabIndex={0} role="button"
                 aria-pressed={selected === p.name}
                 aria-label={`${cardName(a)}: ${role}. ${STATE_WORD[st]}${readFailed ? " (roster read failed)" : ""}.`}
                 onClick={() => activate(p.name)}
                 onKeyDown={(ev) => {
                   if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); activate(p.name); }
                 }}
                 onMouseEnter={() => setHover(p.name)} onMouseLeave={() => setHover(null)}
                 onFocus={() => setHover(p.name)} onBlur={() => setHover(null)}>
                <circle className="rm-disc" r={22} />
                <text className="rm-n" y={42} textAnchor="middle">{cardName(a)}</text>
                <text className="rm-r" y={57} textAnchor="middle">{clip(role, 28)}</text>
                <text className="rm-w" y={71} textAnchor="middle"
                      data-unknown={st === "unknown" || undefined}>{STATE_WORD[st]}</text>
              </g>
            );
          })}
        </svg>
      </div>
      {agents.length === 0 && (
        <p className="rm-empty">
          {readFailed ? "Unknown: the roster could not be read." : "No agents on the roster."}
        </p>
      )}
      <div className="rm-legend">
        <span><i className="k-sq" data-k="ready" />Ready</span>
        <span><i className="k-sq" data-k="working" />Ran in the last 2 min</span>
        <span><i className="k-sq" data-k="not-ready" />Not ready</span>
        <span><i className="k-sq" data-k="failing" />Failing</span>
        <span><i className="k-sq" data-k="unknown" />Unknown</span>
      </div>
      {selectedAgent && (
        <Detail agent={selectedAgent} links={detailLinks(selectedAgent.name, agents)}
                onWarmed={onWarmed} />
      )}
      {readFailed && (
        <p className="agent-map-caption stamp">
          Could not read the roster; every node above is UNKNOWN, not stale.
        </p>
      )}
    </div>
  );
}
