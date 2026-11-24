/**
 * Command Center flanks (Miro 2026-11-12: "every section ... on the left
 * and right side of the ATLAS animation so that he is in between of
 * everything"). A pure, deterministic split of the page's sections into a
 * left and a right column around the ring, balanced by height weight.
 * Sections of one `cluster` (related, e.g. Roster + Shifts) stay together.
 * It only places things; every section keeps its own data and read time.
 */

export interface FlankUnit {
  id: string;
  /** Rough height of the unit, in tile heights. */
  weight: number;
  /** Position within its column, top first. */
  order: number;
  /** Units of one cluster land on the same side. */
  cluster?: string | undefined;
}

export interface Flanks {
  left: FlankUnit[];
  right: FlankUnit[];
  leftWeight: number;
  rightWeight: number;
}

/** Heaviest cluster first (ties: first appearance), each to the lighter
 *  side (ties: left). Input order does not change the answer. */
export function splitFlanks(units: readonly FlankUnit[]): Flanks {
  const clusters = new Map<string, FlankUnit[]>();
  for (const x of units) {
    const key = x.cluster ?? `\u0000${x.id}`;
    clusters.set(key, [...(clusters.get(key) ?? []), x]);
  }
  const groups = [...clusters.entries()]
    .map(([key, members]) => ({
      key, members, weight: members.reduce((s, m) => s + m.weight, 0),
      first: members.map((m) => m.id).sort()[0] as string,
    }))
    // `first` is the lowest id: a stable tie-break that ignores input order.
    .sort((a, b) => b.weight - a.weight || (a.first < b.first ? -1 : a.first > b.first ? 1 : 0));
  const left: FlankUnit[] = [];
  const right: FlankUnit[] = [];
  let lw = 0;
  let rw = 0;
  for (const g of groups) {
    if (lw <= rw) { left.push(...g.members); lw += g.weight; }
    else { right.push(...g.members); rw += g.weight; }
  }
  const byOrder = (a: FlankUnit, b: FlankUnit): number =>
    a.order - b.order || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
  return { left: left.sort(byOrder), right: right.sort(byOrder), leftWeight: lw, rightWeight: rw };
}
