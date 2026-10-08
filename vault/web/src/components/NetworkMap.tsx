// The network map ("make topology look like an actual topology map with
// links and everything, but it's visually readable and understandable"):
// a layered graph -- internet, gateway, infrastructure, leaves, exactly
// `TopoNode.ring`'s own backbone -- with every link the hub sends actually
// drawn, routed by `../lib/topologyLayout.ts` so nothing crosses a node it
// has no business touching. See that module's header for the layout
// algorithm and the reasoning behind each shortcut it takes.
//
// A hairball is the one failure mode this exists to avoid. With 35 devices
// on the demo tenant the fix is structural, not cosmetic: every real
// network is one contiguous, single-row "boundary" segment (scroll/zoom
// instead of wrapping into more rows a link would have to cross), and a
// run of four or more identical *quiet* devices under the same parent
// collapses into one expandable bundle chip. Anything failing, offline,
// untrusted or quarantined is never folded in -- see
// `topologyLayout.isAlwaysIndividual`.
//
// The single thing carried over unchanged from the retired grid layout:
// a camera a recorder claims but Vault has never confirmed is drawn as
// a dashed, unfilled slot -- absence with a shape, not a footnote (see
// `GhostTile` below, sourced from `RecorderGap`, never invented).
//
// Two markups render side by side, exactly as before -- the SVG map
// (`.g-map-grid`, >= 699px) and the ordered list (`.g-map-list`, < 699px)
// -- and CSS alone decides which one shows. The list is untouched by this
// pass: same boundaries, same urgency-first ordering, same data, so a
// person switching between a wide and a narrow screen never sees a
// different picture, just a different shape for it.
import {
  Fragment,
  useCallback,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
  type ReactNode,
  type WheelEvent,
} from "react";
import { machineClass } from "../lib/machine";
import { DeviceIcon } from "./DeviceIcon";
import { needsSensor } from "../lib/network";
import {
  computeLayout,
  truncateLabel,
  type BoundaryBox,
  type LeafItem,
  type PositionedItem,
  type PositionedLink,
  type TopologyLayout,
} from "../lib/topologyLayout";
import type { NetworkHealth, RecorderGap, TopoCluster, TopoNode, TopologyPayload } from "../types";

export type GapSlotKey = string;

/** Widest graph, in LAYOUT units, that still reads at a glance when the
 *  whole of it is shown. A tile is about 120 units; below this the tiles
 *  land somewhere near 40px on a normal laptop, which is the width at
 *  which a label is still a word rather than a smudge. */
const READABLE_FIT_W = 2200;

/** Builds the stable key one dashed camera slot is selected/keyed by --
 *  exported so `Topology.tsx` can recognize which gap a `Drawer` is open for
 *  without re-deriving the shape here and there. */
export function gapSlotKey(networkId: string, gapIndex: number, slotIndex: number): GapSlotKey {
  return `${networkId}:${gapIndex}:${slotIndex}`;
}

export type NetworkMapProps = {
  topology: TopologyPayload;
  /** Overview's camera census gaps (Task 83) -- never computed here, only
   *  placed: `network_id` says which boundary a recorder's unconfirmed
   *  channels belong behind. A gap with no `network_id` (a recorder never
   *  successfully read) has nowhere honest to be drawn and is skipped. */
  cameraGaps: RecorderGap[];
  selectedNodeId: string | null;
  selectedGapKey: GapSlotKey | null;
  onSelectNode: (node: TopoNode) => void;
  /** A yellow/red link was clicked. Falls back to `onSelectNode`. */
  onSelectLink?: (node: TopoNode) => void;
  onSelectGap: (gap: RecorderGap, key: GapSlotKey) => void;
};

/** Status ring tone, off the design system's own `.g-tone-*` hooks
 *  (dashed border = warn, plain border = good/offline) rather than a
 *  bespoke colour -- the map never leans on colour alone to say "quiet"
 *  vs "not". */
function nodeTone(state: string): "good" | "warn" | "plain" {
  if (state === "online") return "good";
  if (state === "sleeping" || state === "flapping") return "warn";
  return "plain";
}

function nodeLabel(node: TopoNode): string {
  const risk = node.risk >= 40 ? `, risk ${node.risk}` : "";
  const untrusted = node.kind === "camera" && !node.trusted ? ", untrusted" : "";
  return `${node.label}, ${node.state}${risk}${untrusted}`;
}

const MIN_SCALE = 0.35;
const MAX_SCALE = 2.5;

type ViewBox = { x: number; y: number; w: number; h: number };

/** One real device/host/site/router tile. Square-ish icon-over-label card,
 *  positioned by the layout rather than a physics step; a truncated label
 *  keeps its full value in a native `<title>` (keyboard/screen-reader
 *  reachable) and in the existing aria-label. */
function NodeTile({ pos, selected, onSelect }: { pos: PositionedItem; selected: boolean; onSelect: (node: TopoNode) => void }) {
  if (pos.item.kind !== "node") return null;
  const node = pos.item.node;
  const isUntrustedCamera = node.kind === "camera" && !node.trusted;
  const showHalo = node.pulse && node.mac !== null;
  const { text, truncated } = truncateLabel(node.label);
  const label = nodeLabel(node);
  return (
    <g
      data-testid="map-node"
      data-kind={node.kind}
      data-mac={node.mac ?? ""}
      data-id={node.id}
      className={[
        "g-map-tile",
        `g-tone-${nodeTone(node.state)}`,
        isUntrustedCamera ? "g-map-tile-untrusted" : "",
        showHalo ? "g-map-tile-pulse" : "",
        selected ? "g-map-tile-selected" : "",
      ]
        .filter(Boolean)
        .join(" ")}
      transform={`translate(${pos.x}, ${pos.y})`}
      tabIndex={0}
      role="button"
      aria-pressed={selected}
      aria-label={label}
      onClick={() => onSelect(node)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect(node);
        }
      }}
    >
      {truncated && <title>{node.label}</title>}
      <rect className="g-map-tile-rect" width={pos.w} height={pos.h} rx={8} />
      <g transform={`translate(${pos.w / 2 - 9}, 8)`}>
        <DeviceIcon icon={node.icon} size={18} />
      </g>
      {node.risk >= 40 && (
        <g transform={`translate(${pos.w - 10}, 2)`}>
          <circle r={8} className="g-map-tile-risk-dot" />
          <text textAnchor="middle" dy="3" className="g-map-tile-risk-text">
            {node.risk}
          </text>
        </g>
      )}
      {/* The device's name when it has one, its MAC when it does not -- the
          same fallback the grid and the list row carry. An SVG label takes the
          class the same way, and `mono` resolves to the same family here as it
          does in HTML. */}
      <text x={pos.w / 2} y={pos.h - 8} textAnchor="middle"
            className={`g-map-tile-label ${machineClass(text)}`.trim()}>
        {text}
      </text>
    </g>
  );
}

/** One expandable bundle chip standing in for four-or-more quiet, identical
 *  devices under the same parent -- a count that hides detail, never a
 *  problem (see `topologyLayout.isAlwaysIndividual`). Clicking it expands
 *  the bundle back into its members in place; it never opens the device
 *  drawer, since it is not one device. */
function BundleTile({
  pos,
  expanded,
  onToggle,
}: {
  pos: PositionedItem;
  expanded: boolean;
  onToggle: () => void;
}) {
  if (pos.item.kind !== "bundle") return null;
  const b = pos.item;
  const { text, truncated } = truncateLabel(b.label);
  return (
    <g
      data-testid="map-bundle"
      data-id={b.id}
      className="g-map-tile g-map-tile-bundle"
      transform={`translate(${pos.x}, ${pos.y})`}
      tabIndex={0}
      role="button"
      aria-pressed={expanded}
      aria-label={`${b.label}, all quiet -- expand to see each device`}
      onClick={onToggle}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onToggle();
        }
      }}
    >
      {truncated && <title>{b.label}</title>}
      <rect className="g-map-tile-rect" width={pos.w} height={pos.h} rx={8} />
      <g transform={`translate(${pos.w / 2 - 9}, 6)`}>
        <DeviceIcon icon={b.icon} size={16} />
        <DeviceIcon icon={b.icon} size={16} x={4} y={-3} className="g-map-tile-bundle-ghost" />
      </g>
      <text x={pos.w / 2} y={pos.h - 8} textAnchor="middle" className="g-map-tile-label">
        {text}
      </text>
    </g>
  );
}

/** One dashed, unfilled slot for a camera a recorder claims but Vault has
 *  never listed -- unchanged in meaning from the retired grid layout, only
 *  repositioned onto the graph. */
function GhostTile({ pos, selected, onSelect }: { pos: PositionedItem; selected: boolean; onSelect: () => void }) {
  if (pos.item.kind !== "ghost") return null;
  const g = pos.item;
  return (
    <g
      data-testid="map-ghost"
      className={["g-map-tile", "g-tone-warn", "g-map-tile-ghost", selected ? "g-map-tile-selected" : ""].join(" ")}
      transform={`translate(${pos.x}, ${pos.y})`}
      tabIndex={0}
      role="button"
      aria-pressed={selected}
      aria-label={`Camera the recorder claims but Vault has never listed, ${g.index + 1} of ${g.count}, behind ${g.gap.name}`}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect();
        }
      }}
    >
      <rect className="g-map-tile-rect" width={pos.w} height={pos.h} rx={8} />
      <g transform={`translate(${pos.w / 2 - 9}, 8)`}>
        <DeviceIcon icon="camera" size={18} />
        <text x={13} y={16} className="g-map-tile-ghost-mark">
          ?
        </text>
      </g>
      <text x={pos.w / 2} y={pos.h - 8} textAnchor="middle" className="g-map-tile-label">
        Not listed
      </text>
    </g>
  );
}

function LinkPath({ link, onOpen }: { link: PositionedLink; onOpen?: () => void }) {
  const clickable = link.tone !== "good" && onOpen !== undefined;
  const why = link.tone === "alert" ? "needs attention now" : "needs a look";
  return (
    <g
      data-testid="map-link"
      data-kind={link.kind}
      data-tone={link.tone}
      data-leaf={link.leafId}
      className={clickable ? "g-map-link-clickable" : undefined}
      role={clickable ? "button" : undefined}
      tabIndex={clickable ? 0 : undefined}
      aria-label={clickable ? `Connection ${why} — open details` : undefined}
      onClick={clickable ? (e) => { e.stopPropagation(); onOpen?.(); } : undefined}
      onPointerDown={clickable ? (e) => e.stopPropagation() : undefined}
      onKeyDown={clickable ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); onOpen?.(); } } : undefined}
    >
      {/* a wide, invisible stroke so a thin coloured line is easy to click */}
      {clickable && <path className="g-map-link-hit" d={link.path} />}
      <path
        className={["g-map-link", `g-map-link-${link.tone}`, link.troubled ? "g-map-link-troubled" : "", link.pulse ? "g-map-link-pulse" : ""]
          .filter(Boolean)
          .join(" ")}
        d={link.path}
      />
    </g>
  );
}

/** The boundary's own translucent backdrop plus its floating title/health
 *  strip -- drawn above the box, never overlapping the tiles or the links
 *  dropping into them. */
function BoundaryGroup({ box, children }: { box: BoundaryBox; children: ReactNode }) {
  return (
    <g data-testid="map-boundary" data-network={box.networkId ?? ""} aria-label={box.label}>
      <rect className="g-map-boundary-rect" x={box.x} y={box.y} width={box.w} height={box.h} rx={10} />
      <text x={box.x + 10} y={box.y - 8} className="g-map-boundary-label">
        {box.label}
      </text>
      {box.health && (
        <text x={box.x + box.w - 10} y={box.y - 8} textAnchor="end" className="g-map-boundary-meta">
          {boundaryMeta(box.health)}
        </text>
      )}
      {children}
    </g>
  );
}

function boundaryMeta(h: NetworkHealth): string {
  const parts = [`${h.online}/${h.devices} last reported online`];
  if (h.cameras > 0) parts.push(`${h.cameras_online}/${h.cameras} cameras`);
  if (h.unverified > 0) parts.push(`${h.unverified} unverified`);
  if (needsSensor(h.status)) parts.push("needs a sensor on this network");
  return parts.join(" · ");
}

// ---------------------------------------------------------------------------
// The mobile/narrow list -- unchanged behaviour, grouped by the same
// clusters the map draws, urgency-first within each. Kept as its own
// section below so the SVG map above it can be read on its own.
// ---------------------------------------------------------------------------

type Boundary = {
  cluster: TopoCluster;
  health: NetworkHealth | null;
  cameras: TopoNode[];
  others: TopoNode[];
  gaps: RecorderGap[];
};

function buildBoundaries(topology: TopologyPayload, cameraGaps: RecorderGap[]): Boundary[] {
  const byId = new Map(topology.nodes.map((n) => [n.id, n]));
  const healthById = new Map(topology.networks.map((n) => [n.id, n]));
  const order = topology.networks.map((n) => n.id);

  return topology.clusters
    .filter((c) => c.id !== "Shared")
    .map((cluster) => {
      const members = cluster.members.map((id) => byId.get(id)).filter((n): n is TopoNode => n !== undefined);
      return {
        cluster,
        health: cluster.network_id ? healthById.get(cluster.network_id) ?? null : null,
        cameras: members.filter((n) => n.kind === "camera"),
        others: members.filter((n) => n.kind !== "camera"),
        gaps: cameraGaps.filter((g) => g.network_id !== "" && g.network_id === cluster.network_id),
      };
    })
    .sort((a, b) => {
      const ai = a.cluster.network_id ? order.indexOf(a.cluster.network_id) : -1;
      const bi = b.cluster.network_id ? order.indexOf(b.cluster.network_id) : -1;
      return ai - bi;
    });
}

function buildEdge(topology: TopologyPayload): TopoNode[] {
  const shared = topology.clusters.find((c) => c.id === "Shared");
  if (!shared) return [];
  const byId = new Map(topology.nodes.map((n) => [n.id, n]));
  return shared.members
    .filter((id) => id !== "internet")
    .map((id) => byId.get(id))
    .filter((n): n is TopoNode => n !== undefined);
}

function gapCount(gap: RecorderGap): number {
  return Math.max(0, gap.expected - gap.known);
}

export function NetworkMap({ topology, cameraGaps, selectedNodeId, selectedGapKey, onSelectNode, onSelectLink, onSelectGap }: NetworkMapProps) {
  const headingId = useId();
  const [expandedBundles, setExpandedBundles] = useState<ReadonlySet<string>>(new Set());
  const layout = useMemo(() => computeLayout(topology, cameraGaps, expandedBundles), [topology, cameraGaps, expandedBundles]);

  const svgRef = useRef<SVGSVGElement | null>(null);

  // Item 17. The map was unreadable and this is why: a viewBox is fitted to
  // the element by `preserveAspectRatio`, so a 900-unit-wide box inside a
  // 310px-wide element letterboxes -- the whole graph scales down to fit
  // the WIDTH and leaves dead space below it. Every node ended up a few
  // pixels across. Nothing was wrong with the layout; the view was the
  // wrong shape for the box it was being poured into.
  //
  // So the view now takes its aspect from the element it is drawn in, and
  // is re-measured when that element resizes. 1.7 is a sane first guess for
  // the frame between mount and the first measurement.
  const [aspect, setAspect] = useState(1.7);
  const measure = useCallback((node: SVGSVGElement | null) => {
    svgRef.current = node;
    if (!node || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      const r = node.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) setAspect(r.width / r.height);
    });
    ro.observe(node);
  }, []);

  // The graph is usually much wider than it is tall (one row per network,
  // side by side) -- fitting the *whole* thing on load would shrink every
  // label past reading size. So the opening view is a normal, legible
  // zoom level centred over the gateway (where a look at the map starts),
  // tall enough to show internet/gateway/infra plus the top of the first
  // boundary; "Fit to screen" is the explicit, one-click way to zoom out
  // and see the entire graph when that is what's wanted.
  const defaultView = useCallback((l: TopologyLayout): ViewBox => {
    // Item 17: now that leaves wrap, a single network usually fits WHOLE at
    // a readable scale -- and a map you can see all of beats one you have
    // to pan. READABLE_FIT_W is in layout units, not pixels, so the same
    // payload opens the same way on every screen: below it, show
    // everything; above it, fall back to a legible zoom over the gateway,
    // which is where a look at a map starts.
    const whole = l.width + 40;
    if (whole <= READABLE_FIT_W) {
      return { x: -20, y: 0, w: whole, h: Math.max(l.height, whole / aspect) };
    }
    const gatewayXs = l.items.filter((it) => it.row === "gateway").map((it) => it.x + it.w / 2);
    const centerX = gatewayXs.length > 0 ? gatewayXs.reduce((a, b) => a + b, 0) / gatewayXs.length : l.width / 2;
    const w = 900;
    // Height follows the box's own shape, so nothing is letterboxed away.
    return { x: centerX - w / 2, y: 0, w, h: Math.min(l.height, w / aspect) };
  }, [aspect]);
  const fitView = useCallback((l = layout) => {
    // Fitting means the WHOLE graph visible: take whichever dimension needs
    // the wider view once the box's shape is accounted for.
    const w = Math.max(l.width + 40, l.height * aspect);
    return setView({ x: -20 - (w - l.width - 40) / 2, y: 0, w, h: w / aspect });
  }, [layout, aspect]);
  const [view, setView] = useState<ViewBox>(() => defaultView(layout));
  // A new topology (device joined/left, a bundle toggled) always resets to
  // the default view rather than keeping a stale viewBox sized for the old
  // graph.
  // The aspect is in the key so the opening view re-fits when the box
  // changes shape — but only to ONE decimal. At two, every pixel of a drag
  // resize was a new key, which reset a pan the person was in the middle
  // of. A fit that fights the user is worse than one that is slightly
  // stale.
  const widthKey = `${layout.width}:${layout.height}:${aspect.toFixed(1)}`;
  const lastWidthKey = useRef(widthKey);
  if (lastWidthKey.current !== widthKey) {
    lastWidthKey.current = widthKey;
    setView(defaultView(layout));
  }

  function zoomBy(factor: number) {
    setView((v) => {
      const cx = v.x + v.w / 2;
      const cy = v.y + v.h / 2;
      const scale = clampScale((layout.width / v.w) * factor) / (layout.width / v.w);
      const w = v.w / scale;
      const h = v.h / scale;
      return { x: cx - w / 2, y: cy - h / 2, w, h };
    });
  }
  function clampScale(s: number): number {
    return Math.min(MAX_SCALE, Math.max(MIN_SCALE, s));
  }

  function onWheel(e: WheelEvent<SVGSVGElement>) {
    e.preventDefault();
    zoomBy(e.deltaY < 0 ? 1.12 : 1 / 1.12);
  }

  const drag = useRef<{ x: number; y: number; view: ViewBox } | null>(null);
  function onPointerDown(e: PointerEvent<SVGSVGElement>) {
    // jsdom (the test environment) has no pointer-capture implementation;
    // panning itself is exercised via the toolbar/keyboard paths in tests,
    // so this is a real-browser-only enhancement, guarded rather than
    // assumed.
    (e.target as Element).setPointerCapture?.(e.pointerId);
    drag.current = { x: e.clientX, y: e.clientY, view };
  }
  function onPointerMove(e: PointerEvent<SVGSVGElement>) {
    if (!drag.current || !svgRef.current) return;
    const rect = svgRef.current.getBoundingClientRect();
    const scaleX = drag.current.view.w / rect.width;
    const scaleY = drag.current.view.h / rect.height;
    const dx = (e.clientX - drag.current.x) * scaleX;
    const dy = (e.clientY - drag.current.y) * scaleY;
    setView({ ...drag.current.view, x: drag.current.view.x - dx, y: drag.current.view.y - dy });
  }
  function onPointerUp() {
    drag.current = null;
  }

  function onKeyPan(e: KeyboardEvent<SVGSVGElement>) {
    // Each axis paces off its own extent -- a graph this much wider than it
    // is tall would otherwise make vertical panning either a single huge
    // jump (stepping by width) or imperceptible (stepping by height on the
    // horizontal axis).
    const stepX = view.w * 0.08;
    const stepY = view.h * 0.08;
    switch (e.key) {
      case "ArrowLeft":
        setView((v) => ({ ...v, x: v.x - stepX }));
        break;
      case "ArrowRight":
        setView((v) => ({ ...v, x: v.x + stepX }));
        break;
      case "ArrowUp":
        setView((v) => ({ ...v, y: v.y - stepY }));
        break;
      case "ArrowDown":
        setView((v) => ({ ...v, y: v.y + stepY }));
        break;
      case "+":
      case "=":
        zoomBy(1.15);
        break;
      case "-":
      case "_":
        zoomBy(1 / 1.15);
        break;
      case "0":
        fitView();
        break;
      default:
        return;
    }
    e.preventDefault();
  }

  function toggleBundle(id: string) {
    setExpandedBundles((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  // A leaf tile that happens to be a member of a currently-collapsed bundle
  // is never individually selected (it isn't drawn); nothing special to do
  // here beyond letting the bundle chip itself be a normal tab stop.

  const itemsByRow = layout.items;
  const boundaries = layout.boundaries;
  const itemsInBoundary = (box: BoundaryBox) =>
    itemsByRow.filter((it) => it.row === "leaf" && x1Within(it, box));
  function x1Within(it: PositionedItem, box: BoundaryBox): boolean {
    const cx = it.x + it.w / 2;
    return cx >= box.x && cx <= box.x + box.w;
  }
  const nonLeafItems = itemsByRow.filter((it) => it.row !== "leaf");

  // ---- the narrow-viewport list (unchanged data, unchanged behaviour) ----
  const internetNode = topology.nodes.find((n) => n.id === "internet") ?? null;
  const edge = useMemo(() => buildEdge(topology), [topology]);
  const boundaryList = useMemo(() => buildBoundaries(topology, cameraGaps), [topology, cameraGaps]);
  const macBoundaries = useMemo(() => buildMacBoundaries(boundaryList), [boundaryList]);

  function otherBoundariesFor(node: TopoNode, ownLabel: string): string[] {
    if (node.mac === null) return [];
    return (macBoundaries.get(node.mac) ?? []).filter((label) => label !== ownLabel);
  }

  return (
    <div className="g-map-wrap">
      {/* ---------------- spatial view (>= 699px) ---------------- */}
      <div className="g-map-grid" aria-labelledby={headingId}>
        <h2 id={headingId} className="g-map-sr-heading">
          Network map
        </h2>

        <div className="g-map-toolbar" role="toolbar" aria-label="Map view controls">
          <button type="button" onClick={() => zoomBy(1.25)} aria-label="Zoom in">
            +
          </button>
          <button type="button" onClick={() => zoomBy(1 / 1.25)} aria-label="Zoom out">
            −
          </button>
          <button type="button" onClick={() => fitView()} aria-label="Fit whole map to screen">
            Fit to screen
          </button>
          <button type="button" onClick={() => setView(defaultView(layout))}>Readable size</button>
          <label>Focus network <select aria-label="Focus network" defaultValue="" onChange={e => {
            const box = layout.boundaries.find(b => b.id === e.target.value);
            if (box) setView({x:box.x-20,y:Math.max(0,box.y-40),w:Math.min(box.w+40,900),h:box.h+100});
          }}><option value="">Choose…</option>{layout.boundaries.map(b => <option key={b.id} value={b.id}>{b.label}</option>)}</select></label>
          <span className="g-map-toolbar-hint">Arrow keys pan · +/− zoom · 0 fits</span>
        </div>

        <svg
          ref={measure}
          className="g-map-svg"
          viewBox={`${view.x} ${view.y} ${view.w} ${view.h}`}
          role="application"
          aria-label="Network topology graph. Use the toolbar or arrow keys to pan and zoom; tab through devices."
          tabIndex={0}
          onWheel={onWheel}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerLeave={onPointerUp}
          onKeyDown={onKeyPan}
        >
          {/* Item 17: the one light source for every mark on the board.
              Defined once here rather than per-tile — 43 gradients is 43
              paint sources for the compositor, and they would all be
              identical. */}
          <defs>
            <linearGradient id="g-map-face" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--color-surface)" />
              <stop offset="100%" stopColor="var(--color-surface-sunken)" />
            </linearGradient>
          </defs>
          <g className="g-map-links">
            {layout.links.map((l) => (
              <LinkPath key={l.id} link={l}
                onOpen={() => {
                  const n = topology.nodes.find((x) => x.id === l.leafId);
                  if (n) (onSelectLink ?? onSelectNode)(n);
                }} />
            ))}
          </g>

          {boundaries.map((box) => (
            <BoundaryGroup key={box.id} box={box}>
              {/* Miro's ask: devices squared up by what they ARE. Drawn
                  BEFORE the tiles so a square is a backdrop its devices sit
                  on, not a frame drawn over them. */}
              {layout.groups
                .filter((g) => g.id.startsWith(`${box.id}::`))
                .map((g) => (
                  <g key={g.id} className="g-map-group" data-tone={g.tone}>
                    <rect className="g-map-group-rect" x={g.x} y={g.y}
                          width={g.w} height={g.h} rx={10} />
                    <text className="g-map-group-label" x={g.x + 10} y={g.y + 15}>
                      {g.label}
                    </text>
                    <text className="g-map-group-count" x={g.x + g.w - 10} y={g.y + 15}
                          textAnchor="end">
                      {g.count}
                    </text>
                  </g>
                ))}
              {itemsInBoundary(box).map((it) =>
                renderItem(it, selectedNodeId, selectedGapKey, onSelectNode, onSelectGap, expandedBundles, toggleBundle),
              )}
            </BoundaryGroup>
          ))}

          <g className="g-map-loose">
            {nonLeafItems.map((it) =>
              renderItem(it, selectedNodeId, selectedGapKey, onSelectNode, onSelectGap, expandedBundles, toggleBundle),
            )}
          </g>
        </svg>

        <MapLegend />
      </div>

      {/* ---------------- ordered list (< 699px) ---------------- */}
      <div className="g-map-list" role="list" aria-label="Network map, as a list">
        <details className="g-map-list-section" open>
          <summary>
            <span>The internet</span>
            <span className="g-map-list-count num">{internetNode ? "reachable" : "unreachable"}</span>
          </summary>
          <div className="g-map-list-body">
            {internetNode && (
              <ListRow
                label={internetNode.label}
                note={internetNode.detail}
                selected={selectedNodeId === internetNode.id}
                onClick={() => onSelectNode(internetNode)}
              />
            )}
          </div>
        </details>

        {edge.length > 0 && (
          <details className="g-map-list-section" open>
            <summary>
              <span>Your servers and sites</span>
              <span className="g-map-list-count num">{edge.length}</span>
            </summary>
            <div className="g-map-list-body">
              {edge.map((node) => (
                <ListRow
                  key={node.id}
                  label={node.label}
                  note={node.state}
                  selected={selectedNodeId === node.id}
                  onClick={() => onSelectNode(node)}
                />
              ))}
            </div>
          </details>
        )}

        {boundaryList.map((boundary) => {
          const members = [...boundary.others, ...boundary.cameras].sort((a, b) => {
            const aUrgent = a.risk >= 40 ? 0 : a.state !== "online" ? 1 : 2;
            const bUrgent = b.risk >= 40 ? 0 : b.state !== "online" ? 1 : 2;
            if (aUrgent !== bUrgent) return aUrgent - bUrgent;
            return a.label.localeCompare(b.label, undefined, { numeric: true });
          });
          const missing = boundary.gaps.reduce((sum, g) => sum + gapCount(g), 0);
          const needsAttention = missing > 0 || members.some((n) => n.risk >= 40 || n.state !== "online");
          return (
            <details className="g-map-list-section" key={boundary.cluster.id} open={needsAttention}>
              <summary>
                <span>{boundary.cluster.label}</span>
                <span className="g-map-list-count num">
                  {boundary.health ? `${boundary.health.online}/${boundary.health.devices}` : ""}
                </span>
              </summary>
              <div className="g-map-list-body">
                {members.map((node) => {
                  const others = otherBoundariesFor(node, boundary.cluster.label);
                  const note =
                    node.risk >= 40
                      ? `${node.state}, risk ${node.risk}`
                      : others.length > 0
                        ? `${node.state} · also on ${others.join(", ")}`
                        : node.state;
                  return (
                    <ListRow
                      key={node.id}
                      label={node.label}
                      note={note}
                      selected={selectedNodeId === node.id}
                      onClick={() => onSelectNode(node)}
                    />
                  );
                })}
                {boundary.gaps.map((gap, gi) => (
                  <Fragment key={`${gap.network_id}:${gi}`}>
                    {gapCount(gap) > 0 && (
                      <ListRow
                        inferred
                        label={`${gapCount(gap)} camera${gapCount(gap) === 1 ? "" : "s"}, not listed`}
                        note="recorder claims, never confirmed"
                        selected={selectedGapKey === gapSlotKey(gap.network_id, gi, 0)}
                        onClick={() => onSelectGap(gap, gapSlotKey(gap.network_id, gi, 0))}
                      />
                    )}
                  </Fragment>
                ))}
              </div>
            </details>
          );
        })}
      </div>
    </div>
  );
}

function renderItem(
  it: PositionedItem,
  selectedNodeId: string | null,
  selectedGapKey: GapSlotKey | null,
  onSelectNode: (node: TopoNode) => void,
  onSelectGap: (gap: RecorderGap, key: GapSlotKey) => void,
  expandedBundles: ReadonlySet<string>,
  toggleBundle: (id: string) => void,
) {
  const item: LeafItem = it.item;
  if (item.kind === "node") {
    return <NodeTile key={item.id} pos={it} selected={selectedNodeId === item.node.id} onSelect={onSelectNode} />;
  }
  if (item.kind === "bundle") {
    return <BundleTile key={item.id} pos={it} expanded={expandedBundles.has(item.id)} onToggle={() => toggleBundle(item.id)} />;
  }
  const key = gapSlotKey(item.networkId, item.index, item.index);
  return <GhostTile key={item.id} pos={it} selected={selectedGapKey === key} onSelect={() => onSelectGap(item.gap, key)} />;
}

/** Every boundary a given MAC is actually drawn in, by boundary label --
 *  a multi-homed device (seen on two networks) gets one `TopoNode` per
 *  boundary it sits behind, so this is the one place that reassembles
 *  "also on X" from those separate rows rather than merging them back into
 *  a single node. Feeds the narrow-list annotation only; the map itself has
 *  no room to say it without a footnote, so a multi-homed device there is
 *  still honestly two tiles, just without the extra label. */
function buildMacBoundaries(boundaries: Boundary[]): Map<string, string[]> {
  const byMac = new Map<string, string[]>();
  for (const boundary of boundaries) {
    for (const node of [...boundary.others, ...boundary.cameras]) {
      if (node.mac === null) continue;
      const labels = byMac.get(node.mac) ?? [];
      labels.push(boundary.cluster.label);
      byMac.set(node.mac, labels);
    }
  }
  return byMac;
}

function MapLegend() {
  return (
    <div className="g-map-legend" aria-label="Map legend">
      <div className="g-map-legend-group">
        <strong>Shapes</strong>
        <p>Icon = device type. Solid ring = online. Dashed ring = quiet (sleeping) or flapping. Red outline = untrusted/quarantined.</p>
      </div>
      <div className="g-map-legend-group">
        <strong>Links</strong>
        <p>
          <span className="g-map-legend-swatch g-map-legend-line g-map-legend-line-good" /> green: all good ·{" "}
          <span className="g-map-legend-swatch g-map-legend-line g-map-legend-line-warn" /> yellow: needs a look, not an emergency (a warning, risk 40+, quiet or untrusted) ·{" "}
          <span className="g-map-legend-swatch g-map-legend-line g-map-legend-line-alert" /> red: an emergency (critical alert, quarantined, offline) · yellow and red lines are clickable and open that device · a moving dash marks live traffic right now.
        </p>
      </div>
      <div className="g-map-legend-group">
        <strong>Other marks</strong>
        <p>A dashed, empty camera slot with a "?" is one a recorder claims but Vault has never confirmed. A chip like "12 phones" is a group of quiet, healthy devices — click it to see each one.</p>
      </div>
    </div>
  );
}

function ListRow({
  label,
  note,
  selected,
  inferred = false,
  onClick,
}: {
  label: string;
  note: string;
  selected: boolean;
  inferred?: boolean;
  onClick: () => void;
}) {
  return (
    <div
      className={["g-map-list-row", inferred ? "g-map-list-row-inferred" : "", selected ? "g-map-list-row-selected" : ""]
        .filter(Boolean)
        .join(" ")}
      role="listitem"
    >
      <button type="button" className="g-map-list-row-btn" onClick={onClick}>
        {/* The device's name when it has one, its MAC when it does not --
            the same fallback the device grid has, decided per value. */}
        <span className={`g-map-list-row-name ${machineClass(label)}`.trim()}>{label}</span>
        <span className="g-map-list-row-note num">{note}</span>
      </button>
    </div>
  );
}

export default NetworkMap;
