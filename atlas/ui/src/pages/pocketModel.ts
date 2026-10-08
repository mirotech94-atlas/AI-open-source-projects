import type { Phase } from "../voice/loop";
import type { Turn } from "../voice/useVoice";

/**
 * The pocket (/pocket, the ATLAS Menu popover in the Mac menu bar): the
 * humanoid and the latest exchange, in a 380x560 window. The pure half lives here so it
 * can be tested without a microphone.
 */

/** How many turns the model keeps by default; the popover shows only the latest. */
export const POCKET_TURNS = 30;

export type PocketStatus = "Idle" | "Listening" | "Thinking" | "Speaking";

/**
 * The status line. `voice.phase` alone is not enough: a typed question is
 * sent outside the voice loop (the phase stays dormant while it is being
 * answered), and `voice.speak` reads an answer aloud without moving the
 * phase either. Without the two local flags a typed turn would read "Idle"
 * the whole time ATLAS was working on it or saying it.
 */
export function pocketStatus(phase: Phase, asking: boolean, speakingTyped: boolean): PocketStatus {
  if (phase === "waking" || phase === "listening") return "Listening";
  if (phase === "thinking") return "Thinking";
  if (phase === "responding") return "Speaking";
  if (asking) return "Thinking";
  if (speakingTyped) return "Speaking";
  return "Idle";
}

const key = (t: Turn): string => `${t.at.getTime()}\u0000${t.said}`;

/**
 * Spoken turns kept past the conversation they belong to. `voice.wake()`
 * empties `voice.log` for every new conversation; the pocket is one running
 * thread, so each turn the log has ever shown is kept here. A turn already
 * kept is not kept twice.
 */
export function keepSpoken(kept: Turn[], log: Turn[]): Turn[] {
  const seen = new Set(kept.map(key));
  const fresh = log.filter((t) => !seen.has(key(t)));
  return fresh.length === 0 ? kept : [...kept, ...fresh];
}

/** Spoken and typed turns in one thread, oldest first, the latest `limit`. */
export function pocketTurns(spoken: Turn[], typed: Turn[], limit = POCKET_TURNS): Turn[] {
  const seen = new Set<string>();
  const all = [...spoken, ...typed].filter((t) => {
    const k = key(t);
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
  all.sort((a, b) => a.at.getTime() - b.at.getTime());
  return limit > 0 ? all.slice(-limit) : [];
}
