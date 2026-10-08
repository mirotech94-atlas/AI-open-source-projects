// Deterministic layered layout for the topology map (task: "an actual
// topology map with links and everything, but readable"). No simulation,
// no randomness -- the same `TopologyPayload` (plus the same set of
// expanded bundle ids) always produces the same geometry.
//
// The backbone is `TopoNode.ring` (0 internet, 1 gateway, 2 infra, 3
// leaves), exactly as `hub/guardian_hub/payloads/topology.py` documents it.
// One deliberate departure: a `server`/`site` node carries `ring=3` in the
// payload (that module's own comment: they land in the generic "leaves"
// bucket) but its only link is straight to the internet -- architecturally
// it sits at the WAN edge, one hop out, same as the gateway. Drawing it at
// the leaf row would force its link to bypass every row in between with a
// long diagonal skating past unrelated nodes. Instead it is drawn in its
// own "edge" lane beside the gateway, at the gateway's row -- a rendering
// choice, not a change to what `ring` means or to the payload itself.
//
// A hairball is avoided with structure, not by hiding data:
//  - every real network (`TopoCluster` with a `network_id`) is one
//    contiguous "boundary" segment of leaf tiles, in a single row (no
//    internal wrap) so a link never has to cross a sibling row to reach a
//    tile -- the map scrolls/zooms instead of stacking rows.
//  - within a boundary, a run of four or more devices that are all
//    quiet (online/sleeping, trusted, unquarantined, risk < 40) and share
//    the same immediate parent and device group collapses into one
//    expandable bundle chip. Anything failing, offline, untrusted or
//    quarantined is always drawn on its own -- see `isAlwaysIndividual`.
import type { NetworkHealth, RecorderGap, TopoLink, TopoNode, TopologyPayload } from "../types";

export const BUNDLE_THRESHOLD = 4;

export type LeafItem =
  | { kind: "node"; id: string; node: TopoNode }
  | {
      kind: "bundle";
      id: string;
      label: string;
      group: string;
      icon: string;
      count: number;
      members: TopoNode[];
      /** Worst-case tone across members -- always "good" by construction,
       *  since anything not quiet is excluded from a bundle. Kept explicit
       *  rather than assumed, so a future relaxation of the quiet rule
       *  can't silently mis-tone a bundle. */
      allOnline: boolean;
    }
  | { kind: "ghost"; id: string; gap: RecorderGap; index: number; count: number; networkId: string };

export type Row = "internet" | "gateway" | "edge" | "infra" | "leaf";

export type PositionedItem = {
  item: LeafItem;
  x: number;
  y: number;
  w: number;
  h: number;
  row: Row;
  /** id of the node/gateway this item's own link (if any) originates from --
   *  null only for the internet node itself. */
  parentId: string | null;
  linkKind: string | null;
  linkPulse: boolean;
  /** Which group square this tile sits inside, "" for rows that are not
   *  grouped (internet, gateway, edge, infra). */
  groupId?: string;
  /** true when this item's own state (or, for a bundle, any member's state)
   *  means the link leading to it should read as "leads to trouble". */
  targetTroubled: boolean;
  targetTone: LinkTone;
};

export type PositionedLink = {
  id: string;
  path: string;
  pulse: boolean;
  kind: string;
  /** the link reads differently when what it leads to is not healthy --
   *  a dashed stroke, never colour alone. */
  troubled: boolean;
  /** green = all good; yellow = something needs a look but is not an
   *  emergency (a WARN alert, risk >= 40, quiet/flapping, untrusted);
   *  red = an emergency (a CRIT alert, quarantined, offline). */
  tone: LinkTone;
  /** the node this link leads to -- what a click on a coloured link opens */
  leafId: string;
};

export type LinkTone = "good" | "warn" | "alert";

/** The tone of the node a link leads to. CRIT beats everything. */
export function nodeTone(n: TopoNode): LinkTone {
  if (n.quarantined || n.alert_sev === "CRIT" || n.state === "offline") return "alert";
  const untrustedDevice = n.mac !== null && !n.trusted;
  if (n.alert_sev === "WARN" || n.risk >= 40 || n.state === "sleeping" || n.state === "flapping" || untrustedDevice) return "warn";
  return "good";
}

export type GroupBox = {
  /** Stable within a boundary: `${networkId}::${group}`. */
  id: string;
  /** What the devices in it ARE — "Phones", "Cameras", "Computers". */
  label: string;
  count: number;
  x: number;
  y: number;
  w: number;
  h: number;
  /** Worst state inside, so a square with something wrong in it reads as
   *  such without the reader opening it. */
  tone: "good" | "warn" | "alert";
};

export type BoundaryBox = {
  id: string;
  label: string;
  networkId: string | null;
  x: number;
  y: number;
  w: number;
  h: number;
  health: NetworkHealth | null;
};

export type TopologyLayout = {
  items: PositionedItem[];
  /** Miro's ask: devices squared up by what they ARE, rather than one long
   *  row of separate tiles you cannot read at a glance. */
  groups: GroupBox[];
  links: PositionedLink[];
  boundaries: BoundaryBox[];
  width: number;
  height: number;
};

const ROW_Y: Record<Row, number> = { internet: 36, edge: 150, gateway: 150, infra: 264, leaf: 378 };
const TILE_H = 64;
const TILE_GAP = 12;
const TILE_MIN_W = 60;
const TILE_MAX_W = 104;
const SEGMENT_GAP = 40;
//: Item 17. A segment used to be ONE row however many devices it held, and
//: the result was a strip: the demo's 19-device Office Wi-Fi measured 2210
//: by 422 units, so fitting it to a 734x520 box scaled every tile down to a
//: few pixels. A map you have to pan across one row at a time is not a map
//: of a network, it is a list drawn sideways.
//:
//: Leaves now wrap at this width. It is a fixed number of LAYOUT units, not
//: a function of the viewport: the same payload has to produce the same
//: geometry on every screen, or the picture a customer describes on the
//: phone is not the one the person helping them is looking at.
//:
//: ~1120 is eight typical tiles. Every leaf in a segment shares one parent,
//: so the extra rows make a fan from a single point rather than the crossing
//: hairball the single-row rule was protecting against -- and links are
//: drawn beneath the tiles, which are opaque.
const SEGMENT_MAX_W = 1120;

//: A group square's own geometry. `GROUP_MAX_COLS` caps how wide one square
//: gets before it grows downwards instead — without it, a network that is
//: mostly phones rebuilds the long strip this exists to replace.
const GROUP_MAX_COLS = 4;
const GROUP_HEADER_H = 22;
const GROUP_PAD = 10;
const GROUP_GAP = 14;
const SEGMENT_PAD = 16;
const SEGMENT_HEADER_H = 30;
const EDGE_LANE_GAP = 56;
const CHAR_W = 6.1;

/** A deterministic, environment-independent width estimate -- no canvas
 *  `measureText` (unavailable/inconsistent under jsdom, and the point of a
 *  "same data, same picture" layout is to not depend on font-rendering
 *  specifics of whatever machine draws it). */
function estimateWidth(label: string): number {
  return Math.round(label.length * CHAR_W + 34);
}

function tileWidth(label: string): number {
  return Math.min(TILE_MAX_W, Math.max(TILE_MIN_W, estimateWidth(label)));
}

/** Shortens a label to fit `maxChars` (derived from `TILE_MAX_W`), keeping
 *  the full value reachable via the caller's `<title>`/aria-label rather
 *  than dropping it. */
export function truncateLabel(label: string): { text: string; truncated: boolean } {
  const maxChars = Math.floor((TILE_MAX_W - 34) / CHAR_W);
  if (label.length <= maxChars) return { text: label, truncated: false };
  return { text: `${label.slice(0, Math.max(1, maxChars - 1))}…`, truncated: true };
}

function bundleLabel(group: string, count: number): string {
  return `${count} ${group}`;
}

function isQuiet(n: TopoNode): boolean {
  return (
    (n.state === "online" || n.state === "sleeping") &&
    n.trusted &&
    !n.quarantined &&
    n.risk < 40 &&
    n.mac !== null
  );
}

/** Anything failing, offline, untrusted or quarantined is never folded into
 *  a bundle -- it stays a first-class tile so it can't be scrolled past. */
function isAlwaysIndividual(n: TopoNode): boolean {
  return !isQuiet(n) || n.state === "archived" || nodeTone(n) !== "good";
}

function troubledNode(n: TopoNode): boolean {
  return n.state === "offline" || n.state === "flapping" || n.state === "archived" || n.quarantined || n.risk >= 40;
}

function gapCount(gap: RecorderGap): number {
  return Math.max(0, gap.expected - gap.known);
}

type Internal = {
  parentLink: Map<string, TopoLink>;
};

function index(topology: TopologyPayload): Internal {
  const parentLink = new Map<string, TopoLink>();
  for (const l of topology.links) {
    if (!parentLink.has(l.source)) parentLink.set(l.source, l);
  }
  return { parentLink };
}

export function computeLayout(
  topology: TopologyPayload,
  cameraGaps: RecorderGap[],
  expandedBundles: ReadonlySet<string>,
): TopologyLayout {
  const { parentLink } = index(topology);
  const internet = topology.nodes.find((n) => n.kind === "internet") ?? null;

  const gateways = topology.nodes.filter((n) => n.kind === "router" && parentLink.get(n.id)?.target === internet?.id);
  const gatewayIds = new Set(gateways.map((n) => n.id));

  const edgeNodes = topology.nodes.filter(
    (n) => n.id !== internet?.id && !gatewayIds.has(n.id) && parentLink.get(n.id)?.target === internet?.id,
  );
  const edgeIds = new Set(edgeNodes.map((n) => n.id));

  // Every other node hangs somewhere off a gateway, directly or via an
  // infra node -- `ring` says which row; `parentLink` says which parent.
  const treeNodes = topology.nodes.filter(
    (n) => n.id !== internet?.id && !gatewayIds.has(n.id) && !edgeIds.has(n.id),
  );
  const infraNodes = treeNodes.filter((n) => n.ring <= 2);
  const leafNodes = treeNodes.filter((n) => n.ring >= 3);

  const childrenOf = new Map<string, TopoNode[]>();
  for (const n of [...infraNodes, ...leafNodes]) {
    const parentId = parentLink.get(n.id)?.target;
    if (!parentId) continue;
    const list = childrenOf.get(parentId) ?? [];
    list.push(n);
    childrenOf.set(parentId, list);
  }

  const networkOrder = topology.networks.map((h) => h.id);
  const networkOf = (n: TopoNode) => n.network?.id ?? null;

  // ---- group leaves into per-network boundary segments ------------------
  const segmentIds = Array.from(
    new Set([...leafNodes.map((n) => networkOf(n)), ...cameraGaps.filter((g) => g.network_id).map((g) => g.network_id)]),
  ).filter((id): id is string => id !== null);
  segmentIds.sort((a, b) => {
    const ai = networkOrder.indexOf(a);
    const bi = networkOrder.indexOf(b);
    return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi);
  });
  const noNetworkLeaves = leafNodes.filter((n) => networkOf(n) === null);

  type Segment = { networkId: string | null; label: string; health: NetworkHealth | null; leaves: TopoNode[]; gaps: RecorderGap[] };
  const healthById = new Map(topology.networks.map((h) => [h.id, h]));
  const clusterLabel = new Map(topology.clusters.map((c) => [c.network_id, c.label]));
  const segments: Segment[] = segmentIds.map((id) => ({
    networkId: id,
    label: clusterLabel.get(id) ?? healthById.get(id)?.name ?? id,
    health: healthById.get(id) ?? null,
    leaves: leafNodes.filter((n) => networkOf(n) === id),
    gaps: cameraGaps.filter((g) => g.network_id === id),
  }));
  if (noNetworkLeaves.length > 0) {
    segments.push({ networkId: null, label: "Other", health: null, leaves: noNetworkLeaves, gaps: [] });
  }

  // ---- bundle each segment's leaves --------------------------------------
  function buildLeafItems(seg: Segment): LeafItem[] {
    const byBucket = new Map<string, TopoNode[]>();
    const order: string[] = [];
    for (const n of seg.leaves) {
      const parentId = parentLink.get(n.id)?.target ?? "?";
      const key = `${parentId}::${n.group}`;
      if (!byBucket.has(key)) {
        byBucket.set(key, []);
        order.push(key);
      }
      byBucket.get(key)!.push(n);
    }
    const items: LeafItem[] = [];
    for (const key of order) {
      const nodes = byBucket.get(key)!.sort((a, b) => a.label.localeCompare(b.label, undefined, { numeric: true }));
      const individual = nodes.filter(isAlwaysIndividual);
      const bundleable = nodes.filter((n) => !isAlwaysIndividual(n));
      for (const n of individual) items.push({ kind: "node", id: n.id, node: n });
      if (bundleable.length === 0) continue;
      const bundleId = `bundle:${seg.networkId ?? "none"}:${key}`;
      if (bundleable.length >= BUNDLE_THRESHOLD && !expandedBundles.has(bundleId)) {
        items.push({
          kind: "bundle",
          id: bundleId,
          label: bundleLabel(bundleable[0].group, bundleable.length),
          group: bundleable[0].group,
          icon: bundleable[0].icon,
          count: bundleable.length,
          members: bundleable,
          allOnline: bundleable.every((n) => n.state === "online"),
        });
      } else {
        for (const n of bundleable) items.push({ kind: "node", id: n.id, node: n });
      }
    }
    items.sort((a, b) => leafItemLabel(a).localeCompare(leafItemLabel(b), undefined, { numeric: true }));
    for (const [gi, gap] of seg.gaps.entries()) {
      const count = gapCount(gap);
      for (let si = 0; si < count; si++) {
        items.push({ kind: "ghost", id: `ghost:${gap.network_id}:${gi}:${si}`, gap, index: si, count, networkId: gap.network_id });
      }
    }
    return items;
  }

  function leafItemGroup(item: LeafItem): string {
    // The payload's own `group` ("Phones", "Cameras", "Computers"), which
    // is the same word the Assets page groups by — so the map and the list
    // cannot disagree about what a device IS.
    if (item.kind === "node") return item.node.group || "Other";
    if (item.kind === "bundle") return item.group || "Other";
    return "Cameras";   // a ghost slot is a camera a recorder claims
  }

  function leafItemLabel(item: LeafItem): string {
    if (item.kind === "node") return item.node.label;
    if (item.kind === "bundle") return item.label;
    return "zzz-not-listed"; // ghosts sort after real devices, before nothing
  }

  function leafItemParent(item: LeafItem): string | null {
    if (item.kind === "node") return parentLink.get(item.node.id)?.target ?? null;
    if (item.kind === "bundle") return parentLink.get(item.members[0].id)?.target ?? null;
    return null; // ghost: resolved to the gateway/segment root by the caller
  }

  function leafItemTroubled(item: LeafItem): boolean {
    if (item.kind === "node") return troubledNode(item.node);
    if (item.kind === "bundle") return false; // a troubled member is never bundled
    return true; // a ghost slot is, by definition, an open question
  }

  function leafItemTone(item: LeafItem): LinkTone {
    if (item.kind === "node") return nodeTone(item.node);
    if (item.kind === "bundle") return "good"; // only quiet, healthy devices get bundled
    return "warn"; // a ghost slot: an open question, not an emergency
  }

  // ---- x placement --------------------------------------------------------
  let cursorX = 0;
  const items: PositionedItem[] = [];
  const boundaries: BoundaryBox[] = [];
  const groups: GroupBox[] = [];
  const childXs = new Map<string, number[]>(); // parentId -> child centre-x's, for infra placement
  const leafCenterXs: number[] = []; // for centring the gateway -- leaves only, so a childless infra tile parked off to the side never skews it

  for (const seg of segments) {
    const leafItems = buildLeafItems(seg);
    const segStartX = cursorX;
    const rowStartX = cursorX + SEGMENT_PAD;

    // Miro's ask: "groups of devices in squares, not every device to itself
    // — square them up so you can tell which are wemo devices or servers".
    //
    // A flat row of 26 separate tiles is a list you have to read one label
    // at a time. Grouped, the same 26 become five squares you take in at a
    // glance, and the one with something wrong in it is obvious before you
    // read a word.
    const byGroup = new Map<string, LeafItem[]>();
    const groupOrder: string[] = [];
    for (const li of leafItems) {
      const g = leafItemGroup(li);
      if (!byGroup.has(g)) { byGroup.set(g, []); groupOrder.push(g); }
      byGroup.get(g)!.push(li);
    }
    // Biggest first: the eye should land on what this network is mostly
    // MADE of, and a two-device group sitting first pushes everything else
    // out of the first screen for no reason.
    groupOrder.sort((a, b) => (byGroup.get(b)!.length - byGroup.get(a)!.length) || a.localeCompare(b));

    let gx = rowStartX;
    let gy = ROW_Y.leaf;
    let segRightX = rowStartX;
    let bandBottom = gy;

    for (const g of groupOrder) {
      const members = byGroup.get(g)!;
      // A square, as squarely as the members allow: columns are the
      // ceiling of the square root, so ten devices lay out 4x3 rather than
      // as another long strip.
      const cols = Math.max(1, Math.min(GROUP_MAX_COLS, Math.ceil(Math.sqrt(members.length))));
      const cellW = Math.max(...members.map((li) => tileWidth(
        leafItemLabel(li) === "zzz-not-listed" ? "Not listed" : leafItemLabel(li))));
      const gRows = Math.ceil(members.length / cols);
      const boxW = GROUP_PAD * 2 + cols * cellW + (cols - 1) * TILE_GAP;
      const boxH = GROUP_HEADER_H + GROUP_PAD + gRows * TILE_H + (gRows - 1) * TILE_GAP + GROUP_PAD;

      // Wrap whole squares, never a square's insides.
      if (gx > rowStartX && gx + boxW - rowStartX > SEGMENT_MAX_W) {
        gx = rowStartX;
        gy = bandBottom + GROUP_GAP;
      }

      let worst: "good" | "warn" | "alert" = "good";
      members.forEach((li, i) => {
        const col = i % cols;
        const rowN = Math.floor(i / cols);
        const x = gx + GROUP_PAD + col * (cellW + TILE_GAP);
        const y = gy + GROUP_HEADER_H + GROUP_PAD + rowN * (TILE_H + TILE_GAP);
        const parentId = li.kind === "ghost" ? (gateways[0]?.id ?? null) : leafItemParent(li);
        if (parentId) {
          const list = childXs.get(parentId) ?? [];
          list.push(x + cellW / 2);
          childXs.set(parentId, list);
        }
        leafCenterXs.push(x + cellW / 2);
        const troubled = leafItemTroubled(li);
        const tone = leafItemTone(li);
        if (troubled) worst = "alert";
        else if (tone === "warn" && worst === "good") worst = "warn";
        items.push({
          item: li,
          x, y, w: cellW, h: TILE_H,
          row: "leaf",
          parentId,
          linkKind: li.kind === "node" ? parentLink.get(li.node.id)?.kind ?? null : li.kind === "bundle" ? parentLink.get(li.members[0].id)?.kind ?? null : "gap",
          linkPulse: li.kind === "node" ? li.node.pulse : li.kind === "bundle" ? li.members.some((m) => m.pulse) : false,
          targetTroubled: troubled, targetTone: tone,
          groupId: `${seg.networkId ?? "other"}::${g}`,
        });
      });

      groups.push({
        id: `${seg.networkId ?? "other"}::${g}`,
        label: g,
        // Counts DEVICES, not tiles: a bundle chip stands for several, and
        // a square headed "4" that holds twelve phones is a wrong number.
        count: members.reduce((n, li) => n + (li.kind === "bundle" ? li.count : 1), 0),
        x: gx, y: gy, w: boxW, h: boxH, tone: worst,
      });

      gx += boxW + GROUP_GAP;
      segRightX = Math.max(segRightX, gx - GROUP_GAP);
      bandBottom = Math.max(bandBottom, gy + boxH);
    }

    const segEndX = leafItems.length > 0 ? segRightX + SEGMENT_PAD : segStartX + 120;
    boundaries.push({
      id: seg.networkId ?? "other",
      label: seg.label,
      networkId: seg.networkId,
      x: segStartX,
      y: ROW_Y.leaf - SEGMENT_HEADER_H,
      w: Math.max(segEndX - segStartX, 120),
      // The box has to cover every wrapped row, not just the first.
      // Measured from the squares it holds, not from a tile-row count: the
      // boundary is a box around group boxes now.
      h: (bandBottom - ROW_Y.leaf) + SEGMENT_HEADER_H + SEGMENT_PAD,
      health: seg.health,
    });
    cursorX = segEndX + SEGMENT_GAP;
  }
  const treeRightEdge = cursorX - SEGMENT_GAP;

  // ---- infra row: centred over children when it has any, otherwise ------
  // parked together *past the right edge of the leaf rows* -- never within
  // that span, or a gateway-to-leaf link that skips the infra row (see
  // `routePath`) could run its vertical drop straight through a childless
  // extender that happens to share its x.
  const childless: TopoNode[] = [];
  const infraPositioned: { node: TopoNode; x: number }[] = [];
  const sortedInfra = [...infraNodes].sort((a, b) => {
    const an = networkOrder.indexOf(networkOf(a) ?? "");
    const bn = networkOrder.indexOf(networkOf(b) ?? "");
    return (an === -1 ? 999 : an) - (bn === -1 ? 999 : bn) || a.label.localeCompare(b.label, undefined, { numeric: true });
  });
  for (const n of sortedInfra) {
    const xs = childXs.get(n.id);
    if (xs && xs.length > 0) {
      infraPositioned.push({ node: n, x: xs.reduce((a, b) => a + b, 0) / xs.length });
    } else {
      childless.push(n);
    }
  }
  // Spaced by each tile's *real* width, not an assumed minimum -- a long
  // label (e.g. "Wi-Fi extender (kitchen)") would otherwise claim more
  // room than its neighbour left for it and the two would overlap.
  const childlessWidths = childless.map((n) => tileWidth(n.label));
  let parkX = treeRightEdge + SEGMENT_GAP + (childlessWidths[0] ?? 0) / 2;
  for (const [i, n] of childless.entries()) {
    infraPositioned.push({ node: n, x: parkX });
    parkX += childlessWidths[i] / 2 + TILE_GAP + (childlessWidths[i + 1] ?? 0) / 2;
  }
  for (const { node, x } of infraPositioned) {
    const w = tileWidth(node.label);
    const parentId = parentLink.get(node.id)?.target ?? null;
    if (parentId) {
      const list = childXs.get(parentId) ?? [];
      list.push(x);
      childXs.set(parentId, list);
    }
    items.push({
      item: { kind: "node", id: node.id, node },
      x: x - w / 2,
      y: ROW_Y.infra,
      w,
      h: TILE_H,
      row: "infra",
      parentId,
      linkKind: parentLink.get(node.id)?.kind ?? null,
      linkPulse: node.pulse,
      targetTroubled: troubledNode(node), targetTone: nodeTone(node),
    });
  }

  // ---- gateway row: centred over the leaves (not the parked childless
  // infra tiles, which sit outside the leaf span on purpose) -----------
  const treeCenter = leafCenterXs.length > 0
    ? leafCenterXs.reduce((a, b) => a + b, 0) / leafCenterXs.length
    : treeRightEdge / 2;

  const gatewayXs: number[] = [];
  for (const [gi, gw] of gateways.entries()) {
    const w = tileWidth(gw.label);
    const gx = treeCenter + (gi - (gateways.length - 1) / 2) * (w + TILE_GAP * 3);
    gatewayXs.push(gx);
    items.push({
      item: { kind: "node", id: gw.id, node: gw },
      x: gx - w / 2,
      y: ROW_Y.gateway,
      w,
      h: TILE_H,
      row: "gateway",
      parentId: internet?.id ?? null,
      linkKind: parentLink.get(gw.id)?.kind ?? null,
      linkPulse: gw.pulse,
      targetTroubled: troubledNode(gw), targetTone: nodeTone(gw),
    });
  }
  const leftmostGatewayX = gatewayXs.length > 0 ? Math.min(...gatewayXs) : treeCenter;
  const leftmostGatewayW = gateways.length > 0 ? tileWidth(gateways[0].label) : 0;

  // ---- edge lane: immediately beside the gateway (not the far left edge
  // of the whole -- possibly very wide -- tree), own column each. A
  // server/site's only link is to the internet, same one hop out as the
  // gateway itself (see this file's header); keeping it physically close
  // keeps that short link short. --------------------------------------
  const edgeSorted = [...edgeNodes].sort((a, b) => a.label.localeCompare(b.label, undefined, { numeric: true }));
  const edgeWidths = edgeSorted.map((n) => tileWidth(n.label));
  const edgeTotalW = edgeWidths.reduce((a, b) => a + b + TILE_GAP, 0);
  let edgeX = leftmostGatewayX - leftmostGatewayW / 2 - EDGE_LANE_GAP - edgeTotalW + TILE_GAP;
  for (const [i, n] of edgeSorted.entries()) {
    const w = edgeWidths[i];
    items.push({
      item: { kind: "node", id: n.id, node: n },
      x: edgeX,
      y: ROW_Y.edge,
      w,
      h: TILE_H,
      row: "edge",
      parentId: internet?.id ?? null,
      linkKind: parentLink.get(n.id)?.kind ?? null,
      linkPulse: n.pulse,
      targetTroubled: troubledNode(n), targetTone: nodeTone(n),
    });
    edgeX += w + TILE_GAP;
  }

  // ---- internet, centred over the gateway(s) -----------------------------
  if (internet) {
    const w = tileWidth(internet.label);
    items.unshift({
      item: { kind: "node", id: internet.id, node: internet },
      x: treeCenter - w / 2,
      y: ROW_Y.internet,
      w,
      h: TILE_H,
      row: "internet",
      parentId: null,
      linkKind: null,
      linkPulse: internet.pulse,
      targetTroubled: false, targetTone: "good",
    });
  }

  // ---- shift everything so the leftmost item sits at x=0 -----------------
  const minX = Math.min(0, ...items.map((it) => it.x));
  const shift = -minX;
  for (const it of items) it.x += shift;
  for (const b of boundaries) b.x += shift;
  for (const g of groups) g.x += shift;
  const width = Math.max(...items.map((it) => it.x + it.w), 400) + SEGMENT_PAD;
  // Measured, not assumed: leaves wrap, so the last row is no longer at a
  // height this file can name in advance.
  const height = Math.max(
    ...items.map((it) => it.y + it.h),
    ...boundaries.map((b) => b.y + b.h),
    ...groups.map((g) => g.y + g.h),
    ROW_Y.leaf + TILE_H,
  ) + SEGMENT_PAD * 2;

  // ---- links --------------------------------------------------------------
  const byItemId = new Map<string, PositionedItem>();
  for (const it of items) {
    const id = it.item.kind === "ghost" ? it.item.id : it.item.kind === "bundle" ? it.item.id : it.item.id;
    byItemId.set(id, it);
  }

  const links: PositionedLink[] = [];
  for (const it of items) {
    if (!it.parentId) continue;
    const parent = byItemId.get(it.parentId);
    if (!parent) continue;
    const id = it.item.kind === "ghost" ? it.item.id : it.item.id;
    links.push({
      id: `link:${id}`,
      path: routePath(parent, it),
      pulse: it.linkPulse,
      kind: it.linkKind ?? "lan",
      troubled: it.targetTroubled,
      tone: it.targetTone,
      leafId: id,
    });
  }

  return { items, links, boundaries, groups, width, height };
}

function routePath(from: PositionedItem, to: PositionedItem): string {
  const x1 = from.x + from.w / 2;
  const y1 = from.y + from.h;
  const x2 = to.x + to.w / 2;
  const y2 = to.y;
  const rowGap = to.row === "leaf" || to.row === "infra" ? Math.abs(y2 - y1) : 0;
  const adjacentRow =
    (from.row === "internet" && (to.row === "gateway")) ||
    (from.row === "gateway" && to.row === "infra") ||
    (from.row === "infra" && to.row === "leaf") ||
    (from.row === "gateway" && to.row === "leaf" && Math.abs(x1 - x2) < 1);
  if (from.row === "internet" && to.row === "edge") {
    // Bypass curve: internet sits over the gateway, the edge lane sits off
    // to the side -- a straight elbow would cut across the gateway's own
    // column, so this one gently curves instead (spec explicitly allows
    // curves for exactly this case).
    const midY = y1 + (y2 - y1) * 0.5;
    return `M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${y1 + 12}, ${x2} ${y2}`;
  }
  if (adjacentRow || rowGap === 0) {
    const midY = y1 + (y2 - y1) / 2;
    return `M ${x1} ${y1} L ${x1} ${midY} L ${x2} ${midY} L ${x2} ${y2}`;
  }
  // A gateway-to-leaf link that skips the infra row entirely: turn early
  // (right below the source) so the long vertical run happens at the
  // child's own x, clear of the gateway's column, before any infra tile.
  const turnY = y1 + 18;
  return `M ${x1} ${y1} L ${x1} ${turnY} L ${x2} ${turnY} L ${x2} ${y2}`;
}
