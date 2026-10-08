/**
 * What the AI-core tiles around the bust say, as pure functions of a read.
 *
 * `useFresh` keeps the previous `data` when a refresh fails and sets `error`
 * beside it, so `data` alone is not "a read about now". Every tile here goes
 * through `current()` first: a stale figure behind a failed refresh is
 * UNKNOWN — grey, dashed, "unreadable" — never the old number still lit as
 * live (invariants 6 and 7).
 */
import type { TileState } from "../components/Tile";
import type { BrainRegion } from "./brain-layout";

/** The part of `Fresh<T>` a tile needs. */
export interface Read<T> {
  data: T | null;
  error: string | null;
}

/** The data, only when it is the result of the latest read succeeding. */
export function current<T>(read: Read<T>): T | null {
  return read.data !== null && read.error === null ? read.data : null;
}

export interface TileWords {
  state: TileState;
  value: string;
  note: string;
}

/** Why there is no figure: still loading, or the read failed. */
function unread(read: Read<unknown>, what: string): TileWords {
  return {
    state: "unknown",
    value: "unreadable",
    note: read.error ? `${what} could not be read: ${read.error}` : "not read yet",
  };
}

export function llmTile(read: Read<{
  vendors: string[];
  needs: Record<string, { connected: boolean }>;
}>): TileWords {
  const llm = current(read);
  if (!llm) return unread(read, "the model routes");
  const connected = Object.values(llm.needs).some((n) => n.connected);
  return {
    state: connected ? "live" : "unknown",
    value: connected ? `${llm.vendors.length} connected` : "no route",
    note: llm.vendors.join(" · ") || "no vendor is wired",
  };
}

export function memoryTile(read: Read<{ total: number }>): TileWords {
  const mem = current(read);
  if (!mem) return unread(read, "memory");
  return { state: "live", value: `${mem.total} stored`, note: "rows in memories" };
}

export function regionsTile(read: Read<{ regions: BrainRegion[] }>): TileWords {
  const brain = current(read);
  if (!brain) return unread(read, "the recall log");
  const firing = brain.regions.filter((r) => r.reads_1h > 0).length;
  const recalls = brain.regions.reduce((n, r) => n + r.reads_1h, 0);
  return {
    state: "live",
    value: `${firing}/${brain.regions.length} firing`,
    note: `${recalls} recalls in the last hour`,
  };
}
