import { useEffect, useLayoutEffect, useRef, useState, type JSX, type KeyboardEvent } from "react";
import type { Brain } from "../api";
import { LAYER_LABEL, pctText, ranState, ranText } from "../pages/brainModel";
import { brainLayout, variantFor, type BrainNode } from "./brainLayout";
import type { Variant } from "./neuralLayout";

/**
 * The Brain as a constellation, drawn in the agency map's own vocabulary
 * (the `nn-*` classes of NeuralMap): an SVG of spokes and links under one
 * focusable HTML element per node. Departments circle the Knowledge Core;
 * each carries its seats (agents and jobs); YOU is wired to every
 * department that holds a Tier 2 action. Nothing moves: a seat is filled
 * when it ran in the window, hollow when it did not, and grey and dashed
 * when there is no per-run record to say (UNKNOWN never looks like idle).
 */

const useIsoLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

function useStageWidth(): [React.RefObject<HTMLDivElement>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useIsoLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    setWidth(el.getBoundingClientRect().width);
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width;
      if (w !== undefined) setWidth(w);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width];
}

export interface BrainMapProps {
  brain: Brain;
  selected?: string | null;
  onActivate?: (id: string) => void;
  /** Force a frame (tests); otherwise it follows the stage's width. */
  variant?: Variant;
}

export function BrainMap({ brain, selected = null, onActivate, variant: fixed }: BrainMapProps): JSX.Element {
  const [stage, width] = useStageWidth();
  const [hover, setHover] = useState<string | null>(null);
  const variant = fixed ?? variantFor(width);
  const layout = brainLayout(brain, variant);
  const { frame } = layout;
  const days = brain.window_days;
  const byKey = new Map(brain.departments.map((d) => [d.key, d]));
  const focusDept = (() => {
    const id = hover ?? selected;
    if (!id) return null;
    const n = layout.byId.get(id);
    return n?.dept ?? null;
  })();

  const activate = (id: string): void => onActivate?.(id);

  const el = (n: BrainNode): JSX.Element => {
    const d = n.dept ? byKey.get(n.dept) ?? null : null;
    let name = "";
    let sub = "";
    let aria = "";
    let state = "ran";
    if (n.kind === "you") {
      name = "YOU";
      sub = "sign-off";
      aria = `You: every department wired to you has actions that need your sign-off.`;
    } else if ((n.kind === "dept" || n.kind === "core") && d) {
      name = d.name;
      const runs = pctText(d.runs_without_you);
      sub = `${d.head.label} · ${runs}`;
      state = runs === "unknown" ? "unknown" : "ran";
      aria = `${d.name}, headed by ${d.head.label}. Runs without you: ${runs}. `
        + `Waiting on you: ${d.waiting_on_you ?? "unknown"}.`;
    } else if (n.seat) {
      name = n.seat.label;
      sub = LAYER_LABEL[n.seat.layer];
      state = ranState(n.seat);
      aria = `${n.seat.label} (${n.seat.kind}, ${LAYER_LABEL[n.seat.layer]}): ${ranText(n.seat, days)}. Open its SOP.`;
    }
    const key = (ev: KeyboardEvent) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        activate(n.id);
      }
    };
    const inFocus = focusDept !== null && n.dept === focusDept;
    return (
      <div
        key={n.id}
        className="nn-node bn-node"
        data-kind={n.kind}
        data-dept={n.dept ?? undefined}
        data-ran={n.kind === "seat" || n.kind === "dept" || n.kind === "core" ? state : undefined}
        data-layer={n.seat?.layer}
        data-side={n.side}
        data-selected={selected === n.id || undefined}
        data-active={inFocus || hover === n.id || undefined}
        data-waiting={d && n.kind !== "seat" && (d.waiting_on_you ?? 0) > 0 ? true : undefined}
        style={{ left: `${n.px}%`, top: `${n.py}%`, ["--size" as string]: `${n.size}px` }}
        tabIndex={0}
        role="button"
        aria-pressed={selected === n.id}
        aria-label={aria}
        title={aria}
        onClick={() => activate(n.id)}
        onKeyDown={key}
        onMouseEnter={() => setHover(n.id)}
        onMouseLeave={() => setHover(null)}
        onFocus={() => setHover(n.id)}
        onBlur={() => setHover(null)}
      >
        <span className="nn-soma" aria-hidden="true" />
        <span className="nn-label">
          <span className="n">{name}</span>
          {sub && <span className="s">{sub}</span>}
        </span>
      </div>
    );
  };

  return (
    <div className="nn bn" data-variant={variant} data-focus={focusDept !== null || undefined}>
      <div ref={stage} className="nn-stage bn-stage" role="group" aria-label="The Brain"
           style={{ aspectRatio: `${frame.w} / ${frame.h}` }}>
        <svg className="nn-web" viewBox={`0 0 ${frame.w} ${frame.h}`}
             preserveAspectRatio="xMidYMid meet" aria-hidden="true">
          {variant === "wide" && (
            <ellipse className="nn-ring" cx={frame.cx} cy={frame.cy} rx={frame.rx} ry={frame.ry} />
          )}
          <g className="nn-spokes">
            {layout.links.filter((l) => l.kind === "spoke").map((l) => (
              <path key={l.id} d={l.d} data-lit={focusDept === l.dept || undefined} />
            ))}
          </g>
          <g className="bn-seat-links">
            {layout.links.filter((l) => l.kind === "seat").map((l) => (
              <path key={l.id} d={l.d} data-lit={focusDept === l.dept || undefined} />
            ))}
          </g>
          <g className="bn-signoff">
            {layout.links.filter((l) => l.kind === "signoff").map((l) => (
              <path key={l.id} d={l.d} data-dept={l.dept} data-lit={focusDept === l.dept || undefined} />
            ))}
          </g>
        </svg>
        {el(layout.you)}
        {el(layout.core)}
        {layout.depts.map(el)}
        {layout.seats.map(el)}
      </div>
      <div className="nn-legend bn-legend">
        <span><i className="k-soma" data-k="ready" />Ran in {days} days</span>
        <span><i className="k-soma" data-k="not-ready" />No run in {days} days</span>
        <span><i className="k-soma" data-k="unknown" />Not recorded per run</span>
        <span><i className="k-line bn-k-signoff" />Needs your sign-off (Tier 2)</span>
        <span className="k-note">A department's % is its runs without you.</span>
        <span className="k-note">Pick a department or a seat; a seat opens its SOP.</span>
      </div>
    </div>
  );
}
