import { TABS } from "../nav";
import type { Overall } from "./commandModel";

/** The sidebar health mini: one line per state, from `overall()`. When the
 *  checks were not read there are no counts at all (invariants 6 and 7):
 *  the caller shows the word Unknown and "read failed", never a zero. */
export interface MiniLine { health: "ok" | "degraded" | "failing" | "unknown"; count: number; word: string }

export function healthMini(o: Overall, readFailed: boolean): { failed: boolean; lines: MiniLine[] } {
  if (readFailed || (o.ok + o.degraded + o.failing + o.unknown === 0)) {
    return { failed: true, lines: [] };
  }
  return {
    failed: false,
    lines: [
      { health: "ok", count: o.ok, word: "OK" },
      { health: "degraded", count: o.degraded, word: "Degraded" },
      { health: "failing", count: o.failing, word: "Failing" },
      { health: "unknown", count: o.unknown, word: "Unknown" },
    ],
  };
}

/** The topbar crumb: the label of the tab the path belongs to. */
export function crumbOf(pathname: string): string {
  // The HUD has no tab; it opens when ATLAS is called and is still named.
  if (pathname === "/hud" || pathname.startsWith("/hud/")) return "HUD";
  const hit = TABS.find((t) => t.to !== "/" && (pathname === t.to || pathname.startsWith(`${t.to}/`)));
  return (hit ?? TABS[0]).label;
}
