import {
  useEffect, useRef, useState, type JSX, type KeyboardEvent, type PointerEvent,
} from "react";
import { api, type ScheduledStep, type WorkflowGraph, type WorkflowNode,
         type WorkflowOutcome } from "../api";
import { useFresh } from "../hooks/useFresh";
import {
  CARD_H, CARD_W, dotOutcome, KIND_GLYPH, layout, nextZoom,
  type LayoutNode,
} from "./workflowsModel";

const WEEKDAY = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                 "Saturday", "Sunday"] as const;

function when(step: ScheduledStep): string {
  if (step.interval_s !== null) {
    const mins = Math.round(step.interval_s / 60);
    return mins >= 1 ? `every ${mins}m` : `every ${step.interval_s}s`;
  }
  const hour = step.hour === null ? "—" : `${String(step.hour).padStart(2, "0")}:00`;
  if (step.weekday !== null) {
    return `${WEEKDAY[step.weekday] ?? `day ${step.weekday}`} from ${hour}`;
  }
  return `daily from ${hour}`;
}

function stamp(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString([], { hour12: false }) : "—";
}

// Floored, not rounded, same reasoning Fleet's own `rel()` gives: a node
// that ran 90s ago must read "1m ago", never round up into disagreeing
// with a health window measured in minutes.
function rel(iso: string | null): string {
  if (!iso) return "never";
  const delta = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(Math.abs(delta) / 60000);
  const text = mins < 1 ? "<1m"
    : mins < 60 ? `${mins}m`
    : mins < 1440 ? `${Math.floor(mins / 60)}h`
    : `${Math.floor(mins / 1440)}d`;
  return delta >= 0 ? `${text} ago` : `in ${text}`;
}

/** The node's `<g>` reuses this HUD's shared `[data-health]` vocabulary
 *  (`_tokens.css`), which is `ok | degraded | failing | unknown` — one word
 *  short of, and one spelling apart from, this page's own `WorkflowOutcome`
 *  (`failed`, `never`). Translated once, here, rather than teaching the
 *  shared tokens a second spelling. */
function healthAttr(outcome: WorkflowOutcome): "ok" | "degraded" | "failing" | "unknown" {
  if (outcome === "failed") return "failing";
  if (outcome === "never") return "unknown";
  return outcome;
}

function diamondPoints(w: number, h: number): string {
  return `${w / 2},0 ${w},${h / 2} ${w / 2},${h} 0,${h / 2}`;
}

function NodeDetail({ node, onClose }: {
  node: WorkflowNode; onClose: () => void;
}): JSX.Element {
  return (
    <div className="panel wf-detail">
      <div className="inner">
        <header>
          <span className="label">{node.label}</span>
          <button type="button" className="linklike" onClick={onClose}>Close ×</button>
        </header>
        <p>{node.detail}</p>
        <p className="stamp">
          {node.last_at ? `${stamp(node.last_at)} (${rel(node.last_at)})` : "never run"}
          {" — "}{node.last_outcome}{node.note ? ` · ${node.note}` : ""}
        </p>
      </div>
    </div>
  );
}

function WorkflowCanvas({ graph }: { graph: WorkflowGraph }): JSX.Element {
  const laidOut = layout(graph);
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const [selected, setSelected] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const [dragging, setDragging] = useState(false);
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const dragStart = useRef<
    { x: number; y: number; left: number; top: number } | null>(null);

  // Wheel zoom, native: React's onWheel is passive, so preventDefault would
  // not stop the page scrolling underneath the canvas.
  useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    const onWheel = (ev: WheelEvent): void => {
      ev.preventDefault();
      setZoom((z) => nextZoom(z, ev.deltaY));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return (): void => el.removeEventListener("wheel", onWheel);
  }, []);

  const onPointerDown = (ev: PointerEvent<HTMLDivElement>): void => {
    // A touch drag is the phone's own horizontal scroll (the container is
    // `overflow: auto` either way); hijacking it here would fight the OS
    // gesture instead of using it.
    if (ev.pointerType === "touch") return;
    const el = canvasRef.current;
    if (!el) return;
    dragStart.current = {
      x: ev.clientX, y: ev.clientY, left: el.scrollLeft, top: el.scrollTop,
    };
    setDragging(true);
    el.setPointerCapture(ev.pointerId);
  };
  const onPointerMove = (ev: PointerEvent<HTMLDivElement>): void => {
    const start = dragStart.current;
    const el = canvasRef.current;
    if (!start || !el) return;
    el.scrollLeft = start.left - (ev.clientX - start.x);
    el.scrollTop = start.top - (ev.clientY - start.y);
  };
  const endDrag = (ev: PointerEvent<HTMLDivElement>): void => {
    dragStart.current = null;
    setDragging(false);
    canvasRef.current?.releasePointerCapture(ev.pointerId);
  };

  const activate = (id: string) => (): void => setSelected(id);
  const onKey = (id: string) => (ev: KeyboardEvent): void => {
    if (ev.key === "Enter" || ev.key === " ") {
      ev.preventDefault();
      setSelected(id);
    }
  };

  const posById = new Map(laidOut.nodes.map((n) => [n.id, n]));
  const selectedNode = selected ? byId.get(selected) ?? null : null;
  const last = graph.last_run;

  return (
    <div className="panel wf-canvas-wrap">
      <div className="inner">
        <div className="wf-header">
          <h3 className="label">{graph.title}</h3>
          <span className="stamp">
            [{graph.trigger.toUpperCase()}] {graph.trigger_detail} · last run{" "}
            {last.at ? `${rel(last.at)} — ${last.outcome}` : "never"}
          </span>
        </div>

        <div
          className="wf-canvas"
          ref={canvasRef}
          data-dragging={dragging || undefined}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={endDrag}
          onPointerLeave={endDrag}
        >
          <svg
            className="wf-svg"
            width={laidOut.width * zoom}
            height={laidOut.height * zoom}
            viewBox={`0 0 ${laidOut.width} ${laidOut.height}`}
            role="img"
            aria-label={`${graph.title}: ${laidOut.nodes.length} steps, `
              + `${laidOut.edges.length} connections`}
          >
            <defs>
              <marker id={`wf-arrow-${graph.id}`} viewBox="0 0 10 10" refX="8" refY="5"
                      markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M0,0 L10,5 L0,10 z" className="wf-arrowhead" />
              </marker>
            </defs>

            {laidOut.edges.map((e, i) => {
              const a = posById.get(e.from);
              const b = posById.get(e.to);
              const midX = a && b ? (a.x + CARD_W + b.x) / 2 : 0;
              const midY = a && b ? (a.y + b.y) / 2 + CARD_H / 2 - 4 : 0;
              return (
                <g key={`${e.from}-${e.to}-${i}`}>
                  <path className="wf-edge" d={e.d}
                        markerEnd={`url(#wf-arrow-${graph.id})`} />
                  {e.label && (
                    <text className="wf-edge-label" x={midX} y={midY}>{e.label}</text>
                  )}
                </g>
              );
            })}

            {laidOut.nodes.map((n: LayoutNode) => {
              const src = byId.get(n.id);
              if (!src) return null;
              return (
                <g
                  key={n.id}
                  className="wf-node"
                  data-kind={n.kind}
                  data-health={healthAttr(src.last_outcome)}
                  data-selected={selected === n.id || undefined}
                  transform={`translate(${n.x} ${n.y})`}
                  tabIndex={0}
                  role="button"
                  aria-label={`${src.label}: ${src.detail}`}
                  onClick={activate(n.id)}
                  onKeyDown={onKey(n.id)}
                >
                  <title>{src.detail}</title>
                  {n.shape === "diamond" ? (
                    <polygon className="wf-shape" points={diamondPoints(CARD_W, CARD_H)} />
                  ) : (
                    <rect className="wf-shape" width={CARD_W} height={CARD_H} rx={8} />
                  )}
                  {n.shape === "card" && (
                    <rect className="wf-bar" x={0} y={0} width={4} height={CARD_H} />
                  )}
                  <text className="wf-glyph" x={n.shape === "diamond" ? CARD_W / 2 - 6 : 10}
                        y={n.shape === "diamond" ? 20 : 22}>
                    {KIND_GLYPH[n.kind]}
                  </text>
                  {(() => {
                    // "Morning brief — 07:00 daily" is a name and a when: the
                    // name on the first line, the when under it, so neither
                    // is cut to fit a card that holds twenty characters.
                    const [name, when = ""] = src.label.split(/\s+[—–-]{1,2}\s+/, 2);
                    const fit = (t: string, max: number): string =>
                      t.length > max ? `${t.slice(0, max - 1)}…` : t;
                    const diamond = n.shape === "diamond";
                    return (
                      <>
                        <text className="wf-label" x={diamond ? CARD_W / 2 - 6 : 26}
                              y={diamond ? 30 : 21} textAnchor={diamond ? "middle" : "start"}>
                          {fit(name ?? src.label, diamond ? 16 : 19)}
                        </text>
                        {when && (
                          <text className="wf-when" x={diamond ? CARD_W / 2 - 6 : 26}
                                y={diamond ? 42 : 36} textAnchor={diamond ? "middle" : "start"}>
                            {fit(when, diamond ? 18 : 22)}
                          </text>
                        )}
                      </>
                    );
                  })()}
                  <circle className="wf-dot" data-outcome={dotOutcome(src.last_outcome)}
                          cx={CARD_W - 14} cy={13} r={4} />
                  <text className="wf-time"
                        x={n.shape === "diamond" ? CARD_W / 2 - 6 : 26} y={CARD_H - 9}
                        textAnchor={n.shape === "diamond" ? "middle" : "start"}>
                    {rel(src.last_at)}
                  </text>
                </g>
              );
            })}
          </svg>
        </div>
      </div>

      {selectedNode && <NodeDetail node={selectedNode} onClose={() => setSelected(null)} />}
    </div>
  );
}

export function Workflows({ reloadKey }: { reloadKey: number }): JSX.Element {
  const workflows = useFresh(() => api.workflows(), [reloadKey]);
  const data = workflows.data;

  return (
    <>
      <h2 className="label">Workflows</h2>
      <p className="stamp" style={{ marginBottom: 12 }}>
        {workflows.fetchedAt ? `Read ${workflows.fetchedAt.toLocaleTimeString()}` : "Reading…"}
        {data && ` · ${data.graphs.length} workflows`
          + ` · ${data.rules.length} standing rule${data.rules.length === 1 ? "" : "s"}`
          + ` · ${data.schedule.length} timed steps`}
      </p>

      {workflows.error && (
        <p className="errorbar">Could not read workflows: {workflows.error}</p>
      )}

      {(data?.graphs ?? []).map((g) => <WorkflowCanvas key={g.id} graph={g} />)}

      <div className="panel" style={{ marginBottom: 14 }}>
        <div className="inner">
          <h3 className="label">On a clock</h3>
          <p className="stamp" style={{ marginBottom: 8 }}>
            Local time on the box ATLAS runs on, from the scheduler&rsquo;s own
            reading of each variable — not a second copy that could disagree.
          </p>
          <table className="sheet">
            <thead>
              <tr><th>Step</th><th>Runs</th><th>Set by</th><th>What it does</th></tr>
            </thead>
            <tbody>
              {(data?.schedule ?? []).map((s) => (
                <tr key={s.name}>
                  <td>{s.name}</td>
                  <td>{when(s)}</td>
                  <td>{s.env}</td>
                  <td>{s.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="panel">
        <div className="inner">
          <h3 className="label">Standing rules</h3>
          {/* Said once and meant permanently — and still not a tier override.
              The rule records the answer; the gate stays where it is. */}
          <p className="stamp" style={{ marginBottom: 8 }}>
            A standing rule is the answer given in advance, not the gate
            removed. The next occurrence is still proposed.
          </p>
          {data && data.rules.length === 0 ? (
            <p className="empty">
              No standing rules were returned at {stamp(data.as_of)}.
            </p>
          ) : (
            <table className="sheet">
              <thead>
                <tr><th>He said</th><th>Via</th><th>Recorded</th><th>Briefed</th></tr>
              </thead>
              <tbody>
                {(data?.rules ?? []).map((r) => (
                  <tr key={r.id}>
                    <td>{r.said}</td>
                    <td>{r.via ?? "—"}</td>
                    <td>{stamp(r.at)}</td>
                    <td>{stamp(r.briefed_at)}</td>
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
