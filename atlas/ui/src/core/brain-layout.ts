/**
 * Where the brain's regions sit, and what each one is allowed to claim.
 *
 * Pure arithmetic: no React, no SVG, no clock beyond the one the label needs.
 * Two rules are structural rather than cosmetic:
 *
 * 1. A region's place is a property of its NAME, not of its position in the
 *    response. A brain whose regions moved because the server sorted its rows
 *    differently would be decoration, not a map of anything.
 * 2. The namespace → region map lives on the server (`atlas/api/dashboard.py`).
 *    This file never second-guesses it — a region name it has never seen still
 *    gets drawn, in a place derived from the name, rather than dropped. Two
 *    copies of that map would be two maps.
 */

/** One region as `/api/brain` reports it. */
export interface BrainRegion {
  region: string;
  namespaces: string[];
  memories: number;
  /** ISO stamp of the last recall that touched the region, or null. */
  last_read_at: string | null;
  reads_1h: number;
  /** This region's reads in the last hour over the busiest region's, 0..1. */
  firing: number;
}

export interface BrainNode {
  region: string;
  namespaces: string[];
  x: number;
  y: number;
  r: number;
  firing: number;
  label: string;
  memories: number;
  reads_1h: number;
  /** Where this node's edge runs to, as a fraction of the box: the bust's
   *  head, off to the left of the brain panel. */
  anchor: { x: number; y: number };
}

/** The regions `/api/brain` can name, in the order they read top to bottom.
 *  OTHER is last on purpose: it is the bucket, and a bucket that sorted
 *  itself among the named regions would look like one of them. */
export const REGION_ORDER = [
  "PREFRONTAL",
  "ASSOCIATION",
  "SENSORY",
  "CONCEPT",
  "HIPPOCAMPUS",
  "CREATIVE",
  "MOTOR",
  "OTHER",
] as const;

/** Roughly anatomical, and fixed. Fractions of the box, so the panel can be
 *  any size without the brain rearranging itself. */
const PLACE: Record<string, [number, number]> = {
  PREFRONTAL: [0.18, 0.28],
  ASSOCIATION: [0.40, 0.16],
  SENSORY: [0.62, 0.24],
  CONCEPT: [0.83, 0.40],
  HIPPOCAMPUS: [0.46, 0.50],
  CREATIVE: [0.20, 0.62],
  MOTOR: [0.68, 0.66],
  OTHER: [0.42, 0.86],
};

export const BRAIN_ANCHOR = { x: 0, y: 0.34 } as const;

const MIN_R = 9;
const MAX_R = 26;

/** A name this file has never seen still has to land somewhere, and somewhere
 *  it will land again tomorrow. Hashed onto an ellipse inside the box. */
function placeUnknown(name: string): [number, number] {
  let hash = 2166136261;
  for (let i = 0; i < name.length; i += 1) {
    hash ^= name.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  const angle = ((hash >>> 0) / 4294967296) * Math.PI * 2;
  return [0.5 + 0.30 * Math.cos(angle), 0.5 + 0.30 * Math.sin(angle)];
}

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

/** `firing` comes off the wire. NaN would silently poison an opacity, and a
 *  figure above 1 would make one region brighter than "brightest". */
function safeFiring(value: number): number {
  return Number.isFinite(value) ? clamp(value, 0, 1) : 0;
}

function clock(iso: string): string | null {
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return null;
  return when.toLocaleTimeString([], {
    hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

/**
 * `<N> memories · fired HH:MM`, or `· quiet` when nothing has read it.
 *
 * "Quiet" is not "0 reads" dressed up: a region no agent has recalled from is
 * a real and visible fact about what ATLAS is using, and the label says it in
 * a word rather than leaving a blank where a time should be.
 */
export function regionLabel(region: BrainRegion): string {
  const when = region.last_read_at ? clock(region.last_read_at) : null;
  return `${region.memories} memories · ${when ? `fired ${when}` : "quiet"}`;
}

/**
 * Regions in, drawable nodes out. `width` and `height` are the SVG box.
 *
 * Size is sqrt of the memory count against the biggest region, for the same
 * reason the memory map uses it: area already squares the difference, so a
 * linear radius makes one busy region swallow the picture.
 */
export function brainLayout(
  regions: BrainRegion[], width: number, height: number,
): BrainNode[] {
  if (regions.length === 0) return [];
  const biggest = regions.reduce((m, r) => Math.max(m, r.memories), 0);
  const ceiling = Math.max(MIN_R, Math.min(MAX_R, width / 2, height / 2));

  return regions.map((region) => {
    const [nx, ny] = PLACE[region.region] ?? placeUnknown(region.region);
    const r = biggest > 0
      ? MIN_R + (ceiling - MIN_R) * Math.sqrt(Math.max(0, region.memories) / biggest)
      : MIN_R;
    const radius = Math.min(r, ceiling);
    return {
      region: region.region,
      namespaces: region.namespaces,
      x: clamp(nx * width, radius, width - radius),
      y: clamp(ny * height, radius, height - radius),
      r: radius,
      firing: safeFiring(region.firing),
      label: regionLabel(region),
      memories: region.memories,
      reads_1h: region.reads_1h,
      anchor: BRAIN_ANCHOR,
    };
  });
}
