/**
 * The n8n-style canvas's layout, as a pure function (Task 61).
 *
 * Deterministic on purpose: no d3, no random jitter, no layout engine that
 * settles differently on a reload. A node's column is the longest path from
 * any node with no incoming edge to it -- the same "longest-path layering"
 * a real flow-chart tool uses, so a node never sits to the LEFT of something
 * that must run before it even when a second, shorter path also reaches it.
 * Within a column, nodes keep the catalog's own order -- never sorted by id,
 * never by degree -- so the picture does not reshuffle when an unrelated
 * evidence read changes a node's outcome.
 */
import type { WorkflowEdge, WorkflowNode, WorkflowNodeKind } from "../api";

export const CARD_W = 176;
export const CARD_H = 64;
export const COL_GAP = 96;
export const ROW_GAP = 28;
export const MARGIN = 24;

/** One glyph per node kind, reusing symbols the nav already uses elsewhere
 *  in this HUD (`App.tsx`'s route icons) rather than inventing a second
 *  icon vocabulary. */
export const KIND_GLYPH: Record<WorkflowNodeKind, string> = {
  trigger: "▷", check: "◎", agent: "⁂", action: "▣",
  gate: "◇", channel: "⇢", store: "▤",
};

export const MIN_ZOOM = 0.4;
export const MAX_ZOOM = 2;

/** Zoom after one wheel tick, clamped -- pure, so the wheel handler itself
 *  stays a thin adapter around this. A negative `deltaY` (scroll up / pinch
 *  out) zooms in. */
export function nextZoom(zoom: number, deltaY: number): number {
  const factor = Math.exp(-deltaY * 0.0015);
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom * factor));
}

export type Shape = "card" | "diamond";

export interface LayoutNode {
  id: string;
  kind: WorkflowNodeKind;
  shape: Shape;
  layer: number;
  x: number;
  y: number;
}

export interface LayoutEdge {
  from: string;
  to: string;
  label: string;
  d: string;
}

export interface Layout {
  nodes: LayoutNode[];
  edges: LayoutEdge[];
  width: number;
  height: number;
}

export interface GraphLike {
  nodes: Pick<WorkflowNode, "id" | "kind">[];
  edges: Pick<WorkflowEdge, "from" | "to" | "label">[];
}

/** Every node's column: 0 for a node with no incoming edge, otherwise one
 *  past the longest of its predecessors'. A back-edge that slipped past the
 *  Python catalog's own acyclic check (it should never — see
 *  `atlas/kernel/workflows.py`'s `_assert_acyclic`) breaks here rather than
 *  recursing forever: a node already being resolved is treated as column 0
 *  for that one lookup, so the layout still terminates and simply draws the
 *  cycle badly instead of hanging the page. */
export function assignLayers(graph: GraphLike): Map<string, number> {
  const preds = new Map<string, string[]>();
  for (const n of graph.nodes) preds.set(n.id, []);
  for (const e of graph.edges) {
    if (preds.has(e.to) && preds.has(e.from)) preds.get(e.to)!.push(e.from);
  }

  const layer = new Map<string, number>();
  const resolving = new Set<string>();

  function resolve(id: string): number {
    const cached = layer.get(id);
    if (cached !== undefined) return cached;
    if (resolving.has(id)) return 0;
    resolving.add(id);
    const ps = preds.get(id) ?? [];
    const value = ps.length === 0 ? 0 : Math.max(...ps.map(resolve)) + 1;
    resolving.delete(id);
    layer.set(id, value);
    return value;
  }

  for (const n of graph.nodes) resolve(n.id);
  return layer;
}

function shapeOf(kind: WorkflowNodeKind): Shape {
  return kind === "gate" ? "diamond" : "card";
}

/** A smooth cubic bezier from the source card's right edge to the target
 *  card's left edge, both at mid-height -- the n8n look. The horizontal
 *  control-point offset grows with the gap so a same-column or backward
 *  edge (there should be none; see `assignLayers`'s note) still curves
 *  instead of drawing a straight line through other cards. */
function bezier(x1: number, y1: number, x2: number, y2: number): string {
  const dx = Math.max(24, Math.abs(x2 - x1) / 2);
  return `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
}

export function layout(graph: GraphLike): Layout {
  const layers = assignLayers(graph);
  const byLayer = new Map<number, string[]>();
  for (const n of graph.nodes) {
    const l = layers.get(n.id) ?? 0;
    const bucket = byLayer.get(l);
    if (bucket) bucket.push(n.id);
    else byLayer.set(l, [n.id]);
  }

  const pos = new Map<string, { x: number; y: number }>();
  for (const [l, ids] of byLayer) {
    ids.forEach((id, i) => {
      pos.set(id, {
        x: MARGIN + l * (CARD_W + COL_GAP),
        y: MARGIN + i * (CARD_H + ROW_GAP),
      });
    });
  }

  const nodes: LayoutNode[] = graph.nodes.map((n) => {
    const p = pos.get(n.id) ?? { x: MARGIN, y: MARGIN };
    return {
      id: n.id, kind: n.kind, shape: shapeOf(n.kind),
      layer: layers.get(n.id) ?? 0, x: p.x, y: p.y,
    };
  });
  const byId = new Map(nodes.map((n) => [n.id, n]));

  const edges: LayoutEdge[] = graph.edges.map((e) => {
    const a = byId.get(e.from);
    const b = byId.get(e.to);
    if (!a || !b) return { from: e.from, to: e.to, label: e.label, d: "" };
    const x1 = a.x + CARD_W;
    const y1 = a.y + CARD_H / 2;
    const x2 = b.x;
    const y2 = b.y + CARD_H / 2;
    return { from: e.from, to: e.to, label: e.label, d: bezier(x1, y1, x2, y2) };
  });

  const maxLayer = byLayer.size ? Math.max(...byLayer.keys()) : 0;
  const maxRows = byLayer.size
    ? Math.max(...Array.from(byLayer.values(), (v) => v.length))
    : 1;
  const width = MARGIN * 2 + (maxLayer + 1) * CARD_W + maxLayer * COL_GAP;
  const height = MARGIN * 2 + maxRows * CARD_H + Math.max(0, maxRows - 1) * ROW_GAP;

  return { nodes, edges, width, height };
}

/** The dot's colour bucket. Four reach the canvas -- `unknown` (a check
 *  that ran but could not measure, or a read that itself failed) draws
 *  exactly like `never`: grey, dashed, unlit -- invariant 7's "never shares
 *  a treatment with OK", kept even for a fifth word the brief did not ask
 *  for by name. The real word stays on the node for its tooltip and the
 *  detail panel; only the dot's colour collapses the two. */
export function dotOutcome(
  outcome: WorkflowNode["last_outcome"],
): "ok" | "degraded" | "failed" | "never" {
  return outcome === "unknown" ? "never" : outcome;
}
