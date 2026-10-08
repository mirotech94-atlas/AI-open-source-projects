/**
 * What Mission Control's tiles and core say, as pure functions of a read.
 *
 * `useFresh` keeps the previous `data` after a refresh fails and sets
 * `error` beside it, so `data` alone is not "a read about now". Every figure
 * here goes through `current()` first -- the rule `/core`'s tiles already
 * follow (core-tiles.ts): a stale figure behind a failed refresh is UNKNOWN,
 * grey and dashed with the word visible, never the old number still lit as
 * live (invariants 6 and 7).
 */
import { normaliseHealth, type ModuleTile } from "../api";
import { current, type Read } from "../core/core-tiles";
import type { TileState } from "../components/Tile";

export interface Estate {
  worst: string;
  hurt: string[];
  blind: number;
}

const RANK: Record<string, number> = { ok: 0, unknown: 1, degraded: 2, failing: 3 };

/** The estate reduction the top bar and /core use. An overview that could
 *  not be read is unknown -- not the last worst-health still drawn. */
export function estate(read: Read<{ modules: ModuleTile[] }>): Estate {
  const overview = current(read);
  if (!overview) return { worst: "unknown", hurt: [], blind: 0 };
  const watched = overview.modules.filter((m) => m.enabled);
  const worst = watched.reduce<string>((acc, m) => {
    const h = normaliseHealth(m.health);
    return (RANK[h] ?? 1) > (RANK[acc] ?? 0) ? h : acc;
  }, watched.length ? "ok" : "unknown");
  return {
    worst,
    hurt: watched
      .filter((m) => ["failing", "degraded"].includes(normaliseHealth(m.health)))
      .map((m) => m.title || m.module),
    blind: watched.filter((m) => normaliseHealth(m.health) === "unknown").length,
  };
}

/** A model route resolves, per the latest successful read -- else false. */
export function coreConnected(
  read: Read<{ needs: Record<string, { connected: boolean }> }>,
): boolean {
  const llm = current(read);
  return llm !== null && Object.values(llm.needs).some((n) => n.connected);
}

export interface Words { state: TileState; value: string }

const UNREAD: Words = { state: "unknown", value: "unreadable" };

export function memoryWords(read: Read<{ total: number }>): Words {
  const mem = current(read);
  return mem ? { state: "live", value: `${mem.total} stored` } : UNREAD;
}

export function agentsWords(read: Read<{ agents: Array<{ available: boolean }> }>): Words {
  const agents = current(read);
  if (!agents) return UNREAD;
  return { state: "live",
           value: `${agents.agents.filter((a) => a.available).length} available` };
}

export function llmsWords(read: Read<{ vendors: string[] }>): Words {
  const llm = current(read);
  if (!llm) return UNREAD;
  return { state: llm.vendors.length > 0 ? "live" : "unknown",
           value: `${llm.vendors.length} connected` };
}

interface SystemRead {
  cpu_percent: number | null;
  memory: { percent: number | null };
  disk: { percent: number | null };
}

/** Optimal is a conjunction, and a missing reading is never one of the
 *  conjuncts -- nor is a reading from before a refresh that failed. */
export function systemWords(read: Read<SystemRead>): Words {
  const sys = current(read);
  if (!sys) return UNREAD;
  const figures: Array<[string, number | null, number]> = [
    ["CPU", sys.cpu_percent, 80], ["RAM", sys.memory.percent, 85],
    ["Disk", sys.disk.percent, 85],
  ];
  const unread = figures.filter(([, v]) => v === null).map(([k]) => k);
  const over = figures.filter(([, v, limit]) => v !== null && v >= limit);
  if (unread.length) return { state: "unknown", value: `${unread.join("/")} unread` };
  if (over.length === 0) return { state: "live", value: "Optimal" };
  return { state: "warn",
           value: `${over[0]?.[0]} ${Math.round(over[0]?.[1] ?? 0)}%` };
}
