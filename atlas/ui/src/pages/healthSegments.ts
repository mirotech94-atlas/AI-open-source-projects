/**
 * The health bar's segments: pure, from the modules already read. A read that
 * failed or covered nothing is one UNKNOWN segment, never OK and never a
 * count of zero passed off as healthy.
 */
import type { Health, ModuleTile } from "../api";
import { overall } from "./commandModel";

export interface HealthSegment {
  health: Health;
  count: number;
  label: string;
}

const UNREAD: HealthSegment = { health: "unknown", count: 0, label: "Unknown: checks not read" };

export function healthSegments(
  modules: ModuleTile[] | null,
  readFailed: boolean,
): HealthSegment[] {
  if (readFailed || modules === null) return [{ ...UNREAD }];
  const o = overall(modules);
  const all: HealthSegment[] = [
    { health: "ok", count: o.ok, label: `OK: ${o.ok}` },
    { health: "degraded", count: o.degraded, label: `Degraded: ${o.degraded}` },
    { health: "failing", count: o.failing, label: `Failing: ${o.failing}` },
    { health: "unknown", count: o.unknown, label: `Unknown: ${o.unknown}` },
  ];
  const kept = all.filter((s) => s.count > 0);
  return kept.length > 0 ? kept : [{ ...UNREAD }];
}
