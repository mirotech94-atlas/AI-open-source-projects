import { useState, type JSX, type KeyboardEvent } from "react";
import { api } from "../api";
import { useFresh } from "../hooks/useFresh";

/** The contract is four kinds. A namespace's `kind_counts` only carries the
 *  ones that actually exist there -- a missing key means zero, not "unknown
 *  kind", so every lookup below defaults to 0 rather than skipping it. */
const KINDS = ["episodic", "semantic", "procedural", "working"] as const;
const KIND_COLOR: Record<(typeof KINDS)[number], string> = {
  episodic: "var(--cyan)",
  semantic: "var(--amber)",
  procedural: "#a78bfa",
  working: "#fb7185",
};

const MIN_R = 8;
const MAX_R = 40;

/** sqrt scale: a namespace with 4x the memories of another should look
 *  noticeably bigger, not four times the area -- area already does that. */
function radiusFor(total: number, maxTotal: number): number {
  if (maxTotal <= 0) return MIN_R;
  return MIN_R + (MAX_R - MIN_R) * (Math.sqrt(total) / Math.sqrt(maxTotal));
}

interface Segment {
  kind: string;
  color: string;
  dasharray: string;
  dashoffset: number;
}

/** A thin ring of arcs around each namespace circle, one per kind present,
 *  proportional to its share of that namespace's memories. Hover shows the
 *  exact counts; this ring is what makes the legend below mean something. */
function segmentsFor(counts: Record<string, number>, total: number, r: number): Segment[] {
  if (total <= 0) return [];
  const ringR = r + 4;
  const circumference = 2 * Math.PI * ringR;
  let cumulative = 0;
  const segments: Segment[] = [];
  for (const kind of KINDS) {
    const n = counts[kind] ?? 0;
    if (n <= 0) continue;
    const frac = n / total;
    segments.push({
      kind,
      color: KIND_COLOR[kind],
      dasharray: `${frac * circumference} ${circumference - frac * circumference}`,
      dashoffset: -cumulative * circumference,
    });
    cumulative += frac;
  }
  return segments;
}

const AGENT_X = 40;
// Wide enough for the longest agent title ("Inbox & Correspondence") plus
// its square and gap, so the namespace circle never has to guess how much
// room the left column needs.
const AGENT_COL_W = 170;
const RING = 4;
// Reserves room for a namespace label reading outward from the circle in
// either direction. Namespace names run up to ~14 characters; generous on
// purpose since drift here means a clipped label, not a bug that shows up
// in any test.
const LABEL_PAD = 100;
const GAP = 20;
const MARGIN = 12;

/** The roster's `memory_scope` alone is ~20 namespaces before anything has
 *  even been written yet, so the circle has to grow with the node count
 *  rather than assume the handful that fits a demo. Solved from the chord
 *  length between adjacent nodes: it must clear two node footprints (worst
 *  case, both at MAX_R) plus a margin, or neighbouring circles overlap. */
function circleRadiusFor(n: number): number {
  if (n <= 1) return 130;
  const halfFootprint = MAX_R + RING + 10;
  return Math.max(130, halfFootprint / Math.sin(Math.PI / n));
}

/** Where a namespace's label sits relative to its circle, chosen from which
 *  quadrant it falls in -- above/below near the poles, to the side
 *  elsewhere -- so labels read outward and never collide with the ones next
 *  to them the way a fixed "always below" placement would near the top of
 *  the circle. */
function labelOffset(cosA: number, sinA: number, r: number):
    { dx: number; dy: number; anchor: "start" | "middle" | "end" } {
  if (sinA < -0.35) return { dx: 0, dy: -(r + 10), anchor: "middle" };
  if (sinA > 0.35) return { dx: 0, dy: r + 16, anchor: "middle" };
  return cosA >= 0
    ? { dx: r + 8, dy: 4, anchor: "start" }
    : { dx: -(r + 8), dy: 4, anchor: "end" };
}

export function Memory({ reloadKey }: { reloadKey: number }): JSX.Element {
  const graph = useFresh(() => api.memoryGraph(), [reloadKey]);
  const stats = useFresh(() => api.memory(), [reloadKey]);

  const nodes = graph.data?.nodes ?? [];
  const edges = graph.data?.edges ?? [];
  const agentList = graph.data?.agents ?? [];
  const maxTotal = nodes.reduce((m, n) => Math.max(m, n.total), 0);

  // The circle's own half-extent (radius plus the biggest possible node and
  // its label) sets the canvas size; the agent column sits to its left with
  // its own reserved width, so neither can ever encroach on the other.
  const circleR = circleRadiusFor(nodes.length);
  const halfExtent = circleR + MAX_R + RING + LABEL_PAD;
  const circleCx = AGENT_COL_W + GAP + halfExtent;
  const circleCy = Math.max(210, halfExtent) + MARGIN;
  const W = circleCx + halfExtent + MARGIN;
  const H = circleCy * 2;

  // Agents in a left column; namespaces on a circle to the right. No layout
  // library -- both are placed by plain trigonometry so the graph never
  // depends on anything that could silently fail to load.
  const agentPos = new Map<string, { x: number; y: number }>();
  agentList.forEach((a, i) => {
    const y = agentList.length > 1
      ? 36 + (i * (H - 72)) / (agentList.length - 1)
      : H / 2;
    agentPos.set(a.id, { x: AGENT_X, y });
  });

  const nsLayout = nodes.map((n, i) => {
    const angle = (i / Math.max(1, nodes.length)) * 2 * Math.PI - Math.PI / 2;
    const cosA = Math.cos(angle);
    const sinA = Math.sin(angle);
    return {
      node: n,
      x: circleCx + circleR * cosA,
      y: circleCy + circleR * sinA,
      cosA,
      sinA,
    };
  });
  const nsPos = new Map(nsLayout.map((l) => [l.node.id, { x: l.x, y: l.y }]));

  // What is picked: an agent or a namespace. Hover previews it, a click or
  // Enter pins it. Everything not wired to it steps back, so the one agent's
  // memory reads out of what is otherwise every line at once.
  const [pinned, setPinned] = useState<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const focus = hovered ?? pinned;
  const agentKey = (id: string): string => `agent:${id}`;
  const fromKey = (from: string): string => (from.startsWith("agent:") ? from : agentKey(from));
  const linked = new Set<string>();
  if (focus) {
    linked.add(focus);
    for (const e of edges) {
      if (fromKey(e.from) === focus) linked.add(e.to);
      if (e.to === focus) linked.add(fromKey(e.from));
    }
  }
  const lit = (id: string): boolean | undefined => (focus ? linked.has(id) : undefined);
  const pick = (id: string) => ({
    tabIndex: 0,
    role: "button",
    "aria-pressed": pinned === id,
    onClick: () => setPinned((cur) => (cur === id ? null : id)),
    onKeyDown: (ev: KeyboardEvent) => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        setPinned((cur) => (cur === id ? null : id));
      }
    },
    onMouseEnter: () => setHovered(id),
    onMouseLeave: () => setHovered(null),
    onFocus: () => setHovered(id),
    onBlur: () => setHovered(null),
  });
  const kindTotals = KINDS.map((k) => ({ k, n: stats.data?.kinds?.[k] ?? 0 }));
  const kindSum = kindTotals.reduce((a, b) => a + b.n, 0);
  const focusNode = focus && !focus.startsWith("agent:") ? nodes.find((n) => n.id === focus) ?? null : null;
  const focusAgent = focus?.startsWith("agent:")
    ? agentList.find((a) => agentKey(a.id) === focus) ?? null : null;

  return (
    <>
      <h2 className="label">Memory</h2>
      <p className="stamp" style={{ marginBottom: 12 }}>
        {graph.fetchedAt ? `Read ${graph.fetchedAt.toLocaleTimeString()}` : "Reading…"}
        {graph.data && ` · ${nodes.length} namespaces · ${agentList.length} agents`}
      </p>

      {graph.error && (
        <p className="errorbar">Could not read the memory graph: {graph.error}</p>
      )}
      {stats.error && (
        <p className="errorbar">Could not read memory counts: {stats.error}</p>
      )}

      {/* What ATLAS remembers, by kind: one proportional bar, then the
          numbers. Replaces the old counts table at the foot of the page. */}
      <section className="mem-summary" aria-label="Memories by kind">
        <div className="mem-total">
          <span className="v">{stats.data?.total ?? "—"}</span>
          <span className="k">memories</span>
        </div>
        <div className="mem-kinds">
          <div className="mem-bar" aria-hidden="true">
            {kindSum > 0 && kindTotals.map(({ k, n }) => n > 0 && (
              <span key={k} style={{ flexGrow: n, background: KIND_COLOR[k] }} />
            ))}
          </div>
          <ul>
            {kindTotals.map(({ k, n }) => (
              <li key={k}>
                <span className="sw" style={{ background: KIND_COLOR[k] }} aria-hidden="true" />
                <span className="k">{k}</span>
                <span className="v">{n}</span>
              </li>
            ))}
          </ul>
        </div>
        <dl className="mem-side">
          <dt>Voice turns</dt><dd>{stats.data?.voice_turns ?? "—"}</dd>
          <dt>Tool calls</dt><dd>{stats.data?.tool_calls ?? "—"}</dd>
        </dl>
      </section>

      <div className="panel mem-panel" data-focus={focus ? "true" : undefined}>
        <div className="inner mem-inner">
          <svg
            className="mem-graph"
            viewBox={`0 0 ${W} ${H}`}
            role="img"
            aria-label="Memory graph: agents and the namespaces they read and write"
          >
            {edges.map((e, i) => {
              const a = agentPos.get(e.from.replace(/^agent:/, ""));
              const n = nsPos.get(e.to);
              if (!a || !n) return null;
              return (
                <path key={i} className="mem-edge"
                      data-lit={focus && (fromKey(e.from) === focus || e.to === focus) ? true : undefined}
                      d={`M${a.x},${a.y} C${(a.x + n.x) / 2},${a.y} ${(a.x + n.x) / 2},${n.y} ${n.x},${n.y}`} />
              );
            })}

            {agentList.map((a) => {
              const p = agentPos.get(a.id);
              if (!p) return null;
              return (
                <g key={a.id} className="mem-pick" data-lit={lit(agentKey(a.id))}
                   transform={`translate(${p.x - 6} ${p.y - 6})`} {...pick(agentKey(a.id))}>
                  <title>{a.title}</title>
                  <rect className="mem-agent" width="12" height="12" />
                  <text className="mem-label" x="18" y="10">{a.title}</text>
                </g>
              );
            })}

            {nsLayout.map(({ node: n, x, y, cosA, sinA }) => {
              const r = radiusFor(n.total, maxTotal);
              const label = n.id.replace(/^ns:/, "");
              const counts = Object.entries(n.kind_counts);
              const tip = `${label} — ${n.total} total` +
                (counts.length ? ` (${counts.map(([k, v]) => `${k}: ${v}`).join(", ")})`
                  : " (nothing stored yet)");
              const lbl = labelOffset(cosA, sinA, r);
              return (
                <g key={n.id} className="mem-pick" data-lit={lit(n.id)}
                   transform={`translate(${x} ${y})`} {...pick(n.id)}>
                  <title>{tip}</title>
                  {segmentsFor(n.kind_counts, n.total, r).map((seg) => (
                    <circle
                      key={seg.kind}
                      r={r + RING}
                      className="mem-ring"
                      stroke={seg.color}
                      strokeDasharray={seg.dasharray}
                      strokeDashoffset={seg.dashoffset}
                      transform="rotate(-90)"
                    />
                  ))}
                  <circle r={r} className="mem-ns" />
                  <text className="mem-label" x={lbl.dx} y={lbl.dy} textAnchor={lbl.anchor}>
                    {label}
                  </text>
                </g>
              );
            })}
          </svg>

          <aside className="mem-detail" aria-live="polite">
            {focusNode ? (
              <>
                <h3>{focusNode.id.replace(/^ns:/, "")}</h3>
                <p className="stamp">{focusNode.total} stored</p>
                <ul className="mem-detail-kinds">
                  {KINDS.map((k) => (
                    <li key={k}>
                      <span className="sw" style={{ background: KIND_COLOR[k] }} aria-hidden="true" />
                      {k}<b>{focusNode.kind_counts[k] ?? 0}</b>
                    </li>
                  ))}
                </ul>
                <p className="stamp">
                  Used by {agentList.filter((ag) => linked.has(agentKey(ag.id))).map((ag) => ag.title).join(", ") || "no agent"}
                </p>
              </>
            ) : focusAgent ? (
              <>
                <h3>{focusAgent.title}</h3>
                <p className="stamp">Reads and writes</p>
                <ul className="mem-detail-list">
                  {nodes.filter((n) => linked.has(n.id)).map((n) => (
                    <li key={n.id}>{n.id.replace(/^ns:/, "")}<b>{n.total}</b></li>
                  ))}
                </ul>
              </>
            ) : (
              <>
                <h3>Pick an agent or a memory</h3>
                <p className="stamp">
                  Hover or click a name to see what it remembers and who shares it.
                  Ring colours show the kinds stored in each.
                </p>
                <ul className="mem-detail-kinds">
                  {KINDS.map((k) => (
                    <li key={k}>
                      <span className="sw" style={{ background: KIND_COLOR[k] }} aria-hidden="true" />{k}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </aside>
        </div>
      </div>
    </>
  );
}
