import {
  useEffect, useLayoutEffect, useRef, useState, type JSX, type KeyboardEvent,
} from "react";
import type { AgentSummary } from "../api";
import { connectionsFor, dotFor, type CardDot } from "../pages/agentMapLayout";
import {
  PULSE_WINDOW_MS, cardName, isPulsing, neuralLayout, shortName, variantFor,
  type Neuron, type Variant,
} from "./neuralLayout";

/**
 * The agency map: every roster agent as a neuron around ATLAS's core,
 * wired only by what the roster declares (see `neuralLayout.ts`).
 *
 * Two layers on one stage of fixed aspect ratio. Underneath, an SVG in the
 * frame's own units: the ring guides, the spokes to the core, the synapses
 * and the core itself. On top, one HTML element per neuron, placed by
 * percentage -- a real focusable button-role element with its label as
 * text, so labels stay legible at any width and a screen reader gets a
 * list of agents, not a picture.
 *
 * Light follows the HUD's rule: cyan only for a measured, live thing.
 * Motion exists in exactly two cases, both real: a signal down an agent's
 * spoke while its last run is under ten minutes old, and down the
 * speaker's spoke while the Agency meeting plays. Nothing else moves, and
 * under prefers-reduced-motion the signal is a steady brighter spoke.
 */

// Re-evaluate "ran in the last ten minutes" twice a minute, so a signal
// stops once its run ages out even if nothing else re-renders the map.
const TICK_MS = 30_000;

function rel(iso: string | null, now: number): string {
  if (!iso) return "never";
  const delta = now - new Date(iso).getTime();
  const mins = Math.floor(Math.abs(delta) / 60000);
  const text = mins < 1 ? "<1m" : mins < 60 ? `${mins}m` : `${Math.floor(mins / 60)}h`;
  return delta >= 0 ? `${text} ago` : `in ${text}`;
}

const STATE_WORD: Record<CardDot, string> = {
  ready: "ready", working: "working now", "not-ready": "not ready", unknown: "unknown",
};

const useIsoLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

function useStageSize(): [React.RefObject<HTMLDivElement>, { w: number; h: number }] {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  useIsoLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const set = (w: number, h: number): void =>
      setSize((cur) => (Math.round(cur.w) === Math.round(w) && Math.round(cur.h) === Math.round(h)
        ? cur : { w, h }));
    const r = el.getBoundingClientRect();
    set(r.width, r.height);
    const ro = new ResizeObserver((entries) => {
      const c = entries[0]?.contentRect;
      if (c) set(c.width, c.height);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, size];
}

function useNow(fixed: number | undefined): number {
  const [now, setNow] = useState(() => fixed ?? Date.now());
  useEffect(() => {
    if (fixed !== undefined) return;
    const id = window.setInterval(() => setNow(Date.now()), TICK_MS);
    return () => window.clearInterval(id);
  }, [fixed]);
  return fixed ?? now;
}

/** The model an agent runs on, as the node shows it (Home's agency map):
 *  a short name and a dot for that deployment's state. */
export interface NodeModel {
  label: string;
  /** closed | open | half-open | dropped | pin | off-chain | unknown */
  dot: string;
  title: string;
}

/** A count the node carries under its name (the Agency tab: the previews
 *  waiting on Miro's card, on the `sites` neuron). `unknown` is a count that
 *  could not be read: grey, dashed, with the word, never a zero. */
export interface NodeBadge {
  label: string;
  title: string;
  unknown?: boolean;
}

function NeuronNode({
  neuron, compact, state, now, selected, connected, active, speaking, pulsing, onActivate, onHover,
  model, badge = null, fill = false,
}: {
  fill?: boolean;
  model: NodeModel | null;
  badge?: NodeBadge | null;
  neuron: Neuron;
  compact: boolean;
  state: CardDot;
  now: number;
  selected: boolean;
  connected: "handoff" | "memory" | null;
  active: boolean;
  speaking: boolean;
  pulsing: boolean;
  onActivate: (name: string) => void;
  onHover: (name: string | null) => void;
}): JSX.Element {
  const a = neuron.agent;
  const key = (ev: KeyboardEvent) => {
    if (ev.key === "Enter" || ev.key === " ") {
      ev.preventDefault();
      onActivate(a.name);
    }
  };
  const ran = a.last_run_at ? `ran ${rel(a.last_run_at, now)}` : "not run yet";
  const detail = `${STATE_WORD[state]}, ${a.runs_24h} run${a.runs_24h === 1 ? "" : "s"} in 24h, ${ran}`;
  const notReady = !a.ready && a.reasons.length > 0;
  return (
    <div
      className="nn-node agent-map-card"
      data-agent={a.name}
      data-state={state}
      data-lane={neuron.lane}
      data-side={neuron.side}
      data-selected={selected || undefined}
      data-connected={connected ?? undefined}
      data-active={active || undefined}
      data-speaking={speaking || undefined}
      data-pulsing={pulsing || undefined}
      style={{
        left: `${neuron.px}%`, top: `${neuron.py}%`,
        ["--size" as string]: `${neuron.size}px`,
        ...(fill && (neuron.side === "left" || neuron.side === "right")
          ? { ["--label-max" as string]: `${neuron.labelMax}px` } : {}),
      }}
      tabIndex={0}
      role="button"
      aria-pressed={selected}
      aria-label={`${cardName(a)}: ${a.brief}. ${detail}${notReady ? `: ${a.reasons[0]}` : ""}.${model ? ` Model: ${model.title}.` : ""}${badge ? ` ${badge.title}.` : ""}`}
      title={`${notReady ? `${detail}: ${a.reasons[0]}` : detail}${model ? `\nModel: ${model.title}` : ""}${badge ? `\n${badge.title}` : ""}`}
      onClick={() => onActivate(a.name)}
      onKeyDown={key}
      onMouseEnter={() => onHover(a.name)}
      onMouseLeave={() => onHover(null)}
      onFocus={() => onHover(a.name)}
      onBlur={() => onHover(null)}
    >
      <span className="nn-soma" aria-hidden="true" />
      <span className="nn-label">
        <span className="n">{compact ? shortName(a) : cardName(a)}</span>
        {notReady ? (
          <span className="s agent-map-reasons">{a.reasons[0]}</span>
        ) : (
          a.persona && <span className="s">{a.persona.specialty}</span>
        )}
        {model && (
          <span className="nn-model" data-dot={model.dot} title={model.title}>
            <i aria-hidden="true" />
            {!compact && <span>{model.label}</span>}
          </span>
        )}
        {badge && (
          <span className="nn-badge" data-unknown={badge.unknown || undefined}
                title={badge.title}>
            {badge.label}
          </span>
        )}
      </span>
    </div>
  );
}

export interface NeuralMapProps {
  agents: AgentSummary[];
  /** The roster read failed: every neuron is UNKNOWN, nothing glows. */
  readFailed?: boolean;
  selected?: string | null;
  onActivate?: (name: string) => void;
  /** The agent whose turn it is in the Agency meeting, if any. */
  speaking?: string | null;
  /** ATLAS itself is speaking (the meeting's opening or closing). */
  coreSpeaking?: boolean;
  /** Draw the "speaking" key in the legend (only the Agency room speaks). */
  speakingKey?: boolean;
  /** Fixed clock for tests; the live map ticks on its own. */
  now?: number;
  /** Force a frame (tests); otherwise it follows the stage's width. */
  variant?: Variant;
  label?: string;
  /** Home's agency map: the model each agent runs on, by name. Optional,
   *  so the Agency room's map is unchanged. */
  modelFor?: ((name: string) => NodeModel | null) | undefined;
  /** The Agency tab: a count under a node's name, by agent name. */
  badgeFor?: ((name: string) => NodeBadge | null) | undefined;
  /** Fill the parent's width AND height instead of keeping the frame's
   *  aspect ratio: the layout is computed for the measured stage (1 frame
   *  unit = 1 CSS px), so nothing is letterboxed. Wide variant only; the
   *  phone frame keeps its own shape. */
  fill?: boolean;
}

export function NeuralMap({
  agents, readFailed = false, selected = null, onActivate, speaking = null,
  coreSpeaking = false, speakingKey = false, now: fixedNow, variant: fixedVariant,
  label = "Agency map", modelFor, badgeFor, fill = false,
}: NeuralMapProps): JSX.Element {
  const [stage, size] = useStageSize();
  const width = size.w;
  const now = useNow(fixedNow);
  const [hover, setHover] = useState<string | null>(null);
  const variant = fixedVariant ?? variantFor(width);
  const filling = fill && variant === "wide";
  const layout = neuralLayout(agents, variant, filling && size.h > 0 ? size : null);
  const { frame } = layout;

  // Hover or focus previews a neuron's synapses; selection holds them.
  const focus = hover && layout.byName.has(hover) ? hover : selected;
  const lit = connectionsFor(focus, agents);
  const selectedLinks = connectionsFor(selected, agents);

  const state = (a: AgentSummary): CardDot => dotFor(a, readFailed, now);
  const pulsing = new Set(
    agents.filter((a) => isPulsing(a, readFailed, now)).map((a) => a.name));
  const recent = pulsing.size;
  const blocked = readFailed ? [] : agents.filter((a) => !a.ready && a.reasons.length > 0);

  const activate = (name: string): void => onActivate?.(name);

  const node = (x: Neuron): JSX.Element => (
    <NeuronNode
      key={x.agent.name}
      neuron={x}
      compact={variant === "compact"}
      state={state(x.agent)}
      now={now}
      selected={selected === x.agent.name}
      connected={
        selectedLinks.handoff.has(x.agent.name) ? "handoff"
        : selectedLinks.memory.has(x.agent.name) ? "memory"
        : null
      }
      active={focus !== null && (focus === x.agent.name
        || lit.handoff.has(x.agent.name) || lit.memory.has(x.agent.name))}
      speaking={speaking === x.agent.name}
      pulsing={pulsing.has(x.agent.name)}
      onActivate={activate}
      onHover={setHover}
      model={modelFor ? modelFor(x.agent.name) : null}
      badge={badgeFor ? badgeFor(x.agent.name) : null}
      fill={filling}
    />
  );

  const touches = (a: string, b: string): boolean => focus !== null && (a === focus || b === focus);

  return (
    <div
      className="nn"
      data-variant={variant}
      data-fill={filling || undefined}
      data-focus={focus !== null || undefined}
      data-speaking={speaking !== null || coreSpeaking || undefined}
      data-read-failed={readFailed || undefined}
    >
      <div
        ref={stage}
        className="nn-stage"
        role="group"
        aria-label={label}
        style={filling ? undefined : { aspectRatio: `${frame.w} / ${frame.h}` }}
      >
        <svg className="nn-web" viewBox={`0 0 ${frame.w} ${frame.h}`}
             preserveAspectRatio="xMidYMid meet" aria-hidden="true">
          {variant === "wide" && (
            <>
              <ellipse className="nn-ring" cx={frame.cx} cy={frame.cy}
                       rx={frame.inner.rx} ry={frame.inner.ry} />
              <ellipse className="nn-ring" cx={frame.cx} cy={frame.cy}
                       rx={frame.outer.rx} ry={frame.outer.ry} />
            </>
          )}
          <g className="nn-spokes">
            {layout.spokes.map((s) => (
              <path key={s.name} d={s.d} data-from={s.name}
                    data-lit={focus === s.name || speaking === s.name || undefined} />
            ))}
          </g>
          <g className="nn-synapses">
            {layout.synapses.map((s) => {
              // Shared memory is a quieter fact than a handoff: drawn only
              // for the neuron in focus, never as standing clutter.
              if (s.kind === "memory" && !touches(s.a, s.b)) return null;
              return (
                <path key={s.id} d={s.d} data-kind={s.kind} data-from={s.a} data-to={s.b}
                      data-lit={touches(s.a, s.b) || undefined} />
              );
            })}
          </g>
          <g className="nn-signals">
            {layout.spokes.map((s) => {
              const talking = speaking === s.name;
              if (!talking && !pulsing.has(s.name)) return null;
              return (
                <path key={s.name} d={s.d} pathLength={1} data-from={s.name}
                      data-kind={talking ? "speaking" : "recent"} />
              );
            })}
          </g>
          <g className="nn-core" data-speaking={coreSpeaking || undefined}
             transform={`translate(${frame.cx} ${frame.cy})`}>
            <rect className="nn-core-frame" x={-17} y={-17} width={34} height={34} />
            <rect className="nn-core-soma" x={-7} y={-7} width={14} height={14} />
            <text className="nn-core-label" y={34} textAnchor="middle">ATLAS</text>
          </g>
        </svg>

        {layout.lead && <div className="agent-map-lead nn-lead">{node(layout.lead)}</div>}
        <section className="agent-map-lane nn-lane" data-lane="businesses">
          <h4 className="agent-map-lane-title"
              style={layout.bands ? { top: `${layout.bands.businesses}%` } : undefined}>
            <span className="nn-ring-key" data-ring="inner" aria-hidden="true" />
            Businesses
            {layout.businesses.length > 0 && <span className="c">{layout.businesses.length}</span>}
          </h4>
          {layout.businesses.map(node)}
          {layout.businesses.length === 0 && (
            <p className="empty" style={layout.bands ? { top: `${layout.bands.businesses}%`, marginTop: 16 } : undefined}>
              {readFailed ? "Unknown: the roster could not be read." : "No agents in this lane."}
            </p>
          )}
        </section>
        <section className="agent-map-lane nn-lane" data-lane="functions">
          <h4 className="agent-map-lane-title"
              style={layout.bands ? { top: `${layout.bands.functions}%` } : undefined}>
            <span className="nn-ring-key" data-ring="outer" aria-hidden="true" />
            Functions
            {layout.functions.length > 0 && <span className="c">{layout.functions.length}</span>}
          </h4>
          {layout.functions.map(node)}
          {layout.functions.length === 0 && (
            <p className="empty" style={layout.bands ? { top: `${layout.bands.functions}%`, marginTop: 16 } : undefined}>
              {readFailed ? "Unknown: the roster could not be read." : "No agents in this lane."}
            </p>
          )}
        </section>
      </div>

      {variant === "compact" && blocked.length > 0 && (
        // At phone width a neuron carries only a first name, so the reason
        // an agent is not ready is listed here rather than lost.
        <ul className="nn-blocked">
          {blocked.map((a) => (
            <li key={a.name}>
              <span className="n">{shortName(a)}</span> not ready:{" "}
              <span className="agent-map-reasons">{a.reasons[0]}</span>
            </li>
          ))}
        </ul>
      )}

      <div className="nn-legend">
        <span><i className="k-soma" data-k="ready" />Ready</span>
        <span><i className="k-soma" data-k="working" />Ran in the last 2 min</span>
        <span><i className="k-soma" data-k="not-ready" />Not ready</span>
        <span><i className="k-soma" data-k="unknown" />Unknown</span>
        {speakingKey && <span><i className="k-soma" data-k="speaking" />Speaking</span>}
        <span><i className="k-line" data-k="handoff" />Handoff</span>
        <span><i className="k-line" data-k="memory" />Shares memory (on focus)</span>
        <span className="k-note">Size is runs in the last 24 hours.</span>
        <span className="k-note" data-recent={recent || undefined}>
          {readFailed
            ? "No signals: the roster could not be read."
            : recent === 0
              ? `No agent ran in the last ${PULSE_WINDOW_MS / 60000} minutes, so nothing moves.`
              : `A signal runs to ATLAS from the ${recent === 1 ? "agent" : `${recent} agents`} that ran in the last ${PULSE_WINDOW_MS / 60000} minutes.`}
        </span>
      </div>
    </div>
  );
}
