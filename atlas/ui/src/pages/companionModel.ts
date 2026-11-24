import { normaliseHealth, type AgentLadder, type ModuleTile } from "../api";
import type { TileState } from "../components/Tile";

/**
 * The companion window's few figures: what the owner glances at while talking to
 * ATLAS. Each is one real check's own reading, with that reading's time --
 * never a figure the page works out for itself.
 */
export interface CompanionTile {
  key: string;
  glyph: string;
  name: string;
  value: string;
  state: TileState;
  readAt: Date | null;
  to: string;
}

/** module.check pairs, in the order they are shown. */
export const COMPANION_CHECKS = [
  { key: "content.reel_legs", glyph: "▶", name: "Reels", to: "/marketing" },
  { key: "shop.sales_7d", glyph: "$", name: "Sales, 7 days", to: "/marketing" },
  { key: "markets.basket", glyph: "↗", name: "Markets (watch only)", to: "/status" },
] as const;

/** The first clause of a check's detail: the headline, not the paragraph. */
export function headline(detail: string, max = 70): string {
  const first = detail.split(/[;\n]/)[0]?.trim() ?? "";
  return first.length > max ? `${first.slice(0, max - 1)}…` : first;
}

export function companionTiles(modules: ModuleTile[] | null): CompanionTile[] {
  return COMPANION_CHECKS.map((spec) => {
    const [mod, name] = spec.key.split(".");
    const check = modules
      ?.find((m) => m.module === mod)
      ?.checks.find((c) => c.name === name);
    const base = { key: spec.key, glyph: spec.glyph, name: spec.name, to: spec.to };
    if (!check) {
      return { ...base, value: modules ? "not running" : "not read", state: "unknown",
               readAt: null };
    }
    const health = check.stale ? "unknown" : normaliseHealth(check.health);
    const state: TileState = health === "ok" ? "live"
      : health === "unknown" ? "unknown" : "warn";
    const value = health === "unknown" && !check.detail
      ? "unknown" : headline(check.detail) || health;
    return {
      ...base, value, state,
      readAt: check.observed_at ? new Date(check.observed_at) : null,
    };
  });
}

/** An agent's autonomy ladder in words: what it watches, does by itself,
 *  asks first, and is never allowed. Unresolved tools are named, never
 *  folded into "does by itself". */
export function ladderWords(l: AgentLadder | null | undefined): string {
  if (!l) return "autonomy unknown";
  const parts = [`watches ${l.watches}`, `does ${l.acts_alone} by itself`,
                 `asks you before ${l.asks_first}`];
  if (l.never) parts.push(`never allowed ${l.never}`);
  if (l.unknown) parts.push(`${l.unknown} unresolved`);
  return parts.join(" · ");
}
