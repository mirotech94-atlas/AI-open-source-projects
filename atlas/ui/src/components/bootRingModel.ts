/**
 * ATLAS boot ring, as pure functions: ATLAS health -> how the ring looks.
 * Single source of truth for the state is `statusPill` (the header pill), so
 * the ring and the pill can never disagree. UNKNOWN is grey, dashed, unlit and
 * still, with the word visible; it shares no look field with OK (invariant 7).
 */
import { statusPill, type Overall } from "../pages/commandModel";

export type RingState = "ok" | "degraded" | "failing" | "unknown";

export function ringState(o: Overall, readFailed: boolean): RingState {
  return statusPill(o, readFailed).health;
}

export interface RingLook {
  state: RingState;
  /** Ring colour token: brand blue, or grey for unknown. */
  tone: "brand" | "grey";
  /** An extra arc segment in this colour on top of the ring. */
  segment: "none" | "amber" | "red";
  word: string;
  dashed: boolean;
  lit: boolean;
  spinning: boolean;
  pulsing: boolean;
}

export function ringLook(state: RingState, reducedMotion: boolean): RingLook {
  const unknown = state === "unknown";
  return {
    state,
    tone: unknown ? "grey" : "brand",
    segment: state === "degraded" ? "amber" : state === "failing" ? "red" : "none",
    word: state === "ok" ? "ONLINE" : state === "degraded" ? "DEGRADED"
      : state === "failing" ? "FAILING" : "UNKNOWN",
    dashed: unknown,
    lit: !unknown,
    spinning: !unknown && !reducedMotion,
    pulsing: !unknown && !reducedMotion,
  };
}

const f = (n: number): string => n.toFixed(2);

/** One path of radial tick marks around (c, c): `count` ticks of length `len`
 *  ending at radius `r`; every `major`th tick is twice as long. */
export function tickPath(c: number, r: number, len: number, count: number, major = 6): string {
  const parts: string[] = [];
  for (let i = 0; i < count; i += 1) {
    const a = (2 * Math.PI * i) / count - Math.PI / 2;
    const l = i % major === 0 ? len * 2 : len;
    parts.push(`M${f(c + (r - l) * Math.cos(a))} ${f(c + (r - l) * Math.sin(a))}`
      + `L${f(c + r * Math.cos(a))} ${f(c + r * Math.sin(a))}`);
  }
  return parts.join("");
}
