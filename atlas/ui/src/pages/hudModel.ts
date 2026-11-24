/**
 * What the HUD says, as pure functions of a read.
 *
 * Same rule as `missionModel.ts`: `useFresh` keeps the previous `data` after
 * a refresh fails, so `data` alone is not "a read about now". Every figure
 * goes through `current()` first, and a stream that dropped makes every figure
 * stale. A stale figure, a null figure and a figure with no `read_at` are all
 * UNKNOWN: grey, dashed, with the word, never the last number still lit as
 * live (invariants 6 and 7). The client never counts events: an event only
 * asks for a re-read.
 */
import type {
  HudSnapshot, HudSurface, HudSurfaceState, HudTask, HudTaskState,
} from "../api";
import { current, type Read } from "../core/core-tiles";
import type { StreamState } from "../hooks/useStream";

export type Figure<T> =
  | { state: "unknown"; why: string }
  | { state: "read"; value: T; read_at: string };

/** The snapshot, only when the latest read succeeded and the stream is not
 *  known to be down. */
export function liveSnapshot(read: Read<HudSnapshot>, stream: StreamState): HudSnapshot | null {
  if (stream === "dropped") return null;
  return current(read);
}

export function whyUnknown(read: Read<HudSnapshot>, stream: StreamState): string {
  if (stream === "dropped") return "live stream dropped";
  if (read.error !== null) return "read failed";
  return read.data === null ? "not read yet" : "";
}

/** One figure: its value and the time it was read, or UNKNOWN. A null value
 *  (its own read failed) and a missing `read_at` are UNKNOWN too. */
export function figure<T>(
  snap: HudSnapshot | null,
  pick: (s: HudSnapshot) => { value: T | null; read_at: string | undefined },
  why = "not read",
): Figure<T> {
  if (!snap) return { state: "unknown", why };
  const { value, read_at } = pick(snap);
  if (value === null || value === undefined) return { state: "unknown", why: "unreadable" };
  if (!read_at) return { state: "unknown", why: "no read time" };
  return { state: "read", value, read_at };
}

/** "14:03:07", the time a figure was read. */
export function hhmmss(at: string | Date | null | undefined): string {
  if (!at) return "—";
  const d = typeof at === "string" ? new Date(at) : at;
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString([], {
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
}

export function readStamp(at: string | undefined | null): string {
  return at ? `read ${hhmmss(at)}` : "not read yet";
}

export const TASK_WORDS: Readonly<Record<HudTaskState, string>> = {
  running: "running",
  waiting_for_you: "waiting for your approval",
  expired: "expired, not run",
  failed: "failed",
  declined: "declined by you",
  done: "done",
  answered: "answered, nothing ran",
  unknown: "unknown",
};

export function taskWords(task: HudTask): string {
  return TASK_WORDS[task.state] ?? TASK_WORDS.unknown;
}

export const SURFACE_WORDS: Readonly<Record<HudSurfaceState, string>> = {
  working: "working",
  idle: "idle",
  failed: "last action failed",
  watch_only: "watch only: nothing ran",
  refused: "refused",
  paused: "paused",
  rate_limited: "rate limited",
  qa_held: "held by QA",
  unknown: "unknown",
};

export interface TileView {
  key: string;
  label: string;
  /** UNKNOWN when the read is stale, the tile cannot be read or it is not
   *  instrumented. */
  state: HudSurfaceState;
  words: string;
  health: string;
  detail: string;
  read_at: string | null;
}

/** A surface tile as drawn. `live` false (a failed refetch or a dropped
 *  stream) turns every tile UNKNOWN. */
export function tileView(tile: HudSurface, live: boolean): TileView {
  const known = live && tile.state !== "unknown" && tile.unread.length === 0;
  const state: HudSurfaceState = known ? tile.state : "unknown";
  const why = !live ? "stale" : tile.why ?? (tile.unread.length ? "unreadable" : "");
  const bits: string[] = [];
  if (known && tile.last_action) bits.push(`last: ${tile.last_action}`);
  if (!known && why) bits.push(why);
  if (tile.tool_seen) bits.push(`seen: ${tile.tool_seen.tool} at ${hhmmss(tile.tool_seen.at)}`);
  return {
    key: tile.key, label: tile.label, state,
    words: SURFACE_WORDS[state], health: live ? tile.health : "unknown",
    detail: bits.join(" · "), read_at: live ? tile.read_at : null,
  };
}

/** Which tasks wait on him: shown first. */
export function sortTasks(tasks: HudTask[]): HudTask[] {
  const rank = (t: HudTask): number =>
    t.state === "waiting_for_you" ? 0 : t.state === "running" ? 1 : 2;
  return [...tasks].sort((a, b) => rank(a) - rank(b) || b.id - a.id);
}

/** Minimum gap between stream-triggered refetches. */
export const REFETCH_GAP_MS = 5000;

/** When a stream event may trigger a refetch: now if the gap has passed,
 *  else after the rest of it. */
export function refetchDelay(lastFetchAt: number, now: number): number {
  return Math.max(0, lastFetchAt + REFETCH_GAP_MS - now);
}
