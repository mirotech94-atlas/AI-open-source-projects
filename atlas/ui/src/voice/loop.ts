/**
 * The conversation loop, as pure rules -- no DOM, no React, no timers.
 *
 * the owner asked to "talk to him without having to click mic on". Two things
 * make that true, and they are deliberately separate:
 *
 *   1. An on-device wake gate (`localWake.ts`, or Porcupine, or pinch) that
 *      opens the loop. Until it does, audio never leaves the tab.
 *   2. An OPEN loop: once a turn is answered, ATLAS goes back to listening
 *      instead of falling asleep, so a follow-up needs no second "atlas".
 *
 * The loop closes on three things only: quiet for FOLLOWUP_QUIET_MS, a
 * farewell ("that's all", "goodnight"), or an explicit dismiss / mute. Quiet
 * only ever CLOSES the loop -- silence is never consent (invariant 5), and
 * nothing in voice can act anyway (invariant 1).
 *
 * `handsFree` is the wake gate being armed. Closing the loop leaves it as it
 * was, so the mic stays with the on-device gate; only "mute" turns it off,
 * and muting is how the microphone is released entirely.
 */

export type Phase =
  | "dormant"
  | "waking"
  | "listening"
  | "thinking"
  | "responding"
  | "dismissing";

export type LoopEvent = "wake" | "turn_done" | "quiet" | "farewell" | "dismiss" | "mute";

export interface LoopState {
  open: boolean;
  handsFree: boolean;
  turns: number;
}

/** No speech at all for this long after an answer closes the loop. */
export const FOLLOWUP_QUIET_MS = 12000;

export const FAREWELLS =
  /\b(that'?s all|that will be all|thanks,? atlas|thank you,? atlas|goodbye|good ?night)\b/i;

export function isFarewell(transcript: string): boolean {
  return FAREWELLS.test(transcript);
}

export function next(
  state: LoopState,
  event: LoopEvent,
): { state: LoopState; go: "listening" | "dormant" | "responding" | "stay" } {
  switch (event) {
    case "wake":
      // Already talking: a second "atlas" mid-conversation is just a word.
      if (state.open) return { state, go: "stay" };
      return { state: { ...state, open: true, turns: 0 }, go: "listening" };
    case "turn_done":
      if (!state.open) return { state, go: "stay" };
      return { state: { ...state, turns: state.turns + 1 }, go: "listening" };
    case "quiet":
    case "farewell":
    case "dismiss":
      return { state: { ...state, open: false, turns: 0 }, go: "dormant" };
    case "mute":
      return { state: { open: false, handsFree: false, turns: 0 }, go: "dormant" };
  }
}

// --------------------------------------------------------------- status

/** The one status the stage shows, wherever it is drawn. Core's humanoid
 *  stage and the voice overlay both read it from here, so the two pills can
 *  never disagree about what ATLAS is doing. */
export type VoiceStatus = "IDLE" | "LISTENING" | "THINKING" | "SPEAKING";

const STATUS: Record<Phase, VoiceStatus> = {
  dormant: "IDLE",
  waking: "LISTENING",
  listening: "LISTENING",
  thinking: "THINKING",
  responding: "SPEAKING",
  dismissing: "IDLE",
};

export function statusOf(phase: Phase): VoiceStatus {
  return STATUS[phase];
}

// -------------------------------------------------------- acknowledgement

/** Said on wake, when speech is configured. A fixed list, not generated. */
export const ACKNOWLEDGEMENTS = ["At your service.", "Go ahead.", "Listening."] as const;

export function pickAcknowledgement(random: () => number = Math.random): string {
  const index = Math.floor(random() * ACKNOWLEDGEMENTS.length);
  return ACKNOWLEDGEMENTS[Math.min(index, ACKNOWLEDGEMENTS.length - 1)] ?? "Listening.";
}

/**
 * Recording starts the moment he wakes ATLAS, so the first words of a
 * one-breath question are never lost -- which means the acknowledgement may
 * be in the recording if echo cancellation misses it. Remove it, but only
 * when it is clearly separate (punctuated, or the whole transcript): an
 * unpunctuated "go ahead and book it" is his sentence, not ours.
 */
export function stripAcknowledgement(transcript: string): string {
  const text = transcript.trim();
  for (const ack of ACKNOWLEDGEMENTS) {
    const words = ack.replace(/[.!]$/, "").toLowerCase();
    const lower = text.toLowerCase();
    if (!lower.startsWith(words)) continue;
    const rest = text.slice(words.length);
    if (rest === "" || /^[.,!;:]/.test(rest)) {
      return rest.replace(/^[.,!;:]+/, "").trim();
    }
  }
  return text;
}

/**
 * The part of an answer worth reading aloud: the whole thing when short,
 * otherwise cut at the last sentence end that fits (or the last word, if
 * there is none). The full answer is always typed; this only bounds what is
 * sent to the speech provider.
 */
export function speakable(text: string, limit = 1200): string {
  const clean = text.trim();
  if (clean.length <= limit) return clean;
  const head = clean.slice(0, limit + 1);
  const sentence = Math.max(
    head.lastIndexOf(". "), head.lastIndexOf("? "), head.lastIndexOf("! "),
    head.lastIndexOf(".\n"),
  );
  if (sentence > 0) return head.slice(0, sentence + 1).trim();
  const word = head.lastIndexOf(" ");
  return (word > 0 ? head.slice(0, word) : head.slice(0, limit)).trim();
}

/**
 * A reply in sentences, for Fish Audio to voice one at a time: the first is
 * heard after its own synthesis, not the whole reply's. At most four; a
 * short one (under 24 characters) is joined to the next so Fish is never
 * asked for a single word. The same rule as ATLAS Menu's `sentences`.
 */
export function sentencesOf(text: string): string[] {
  const out: string[] = [];
  for (const cut of text.split(/(?<=[.!?])\s+/).map((c) => c.trim())) {
    if (!cut) continue;
    const last = out[out.length - 1];
    if (last !== undefined && last.length < 24) out[out.length - 1] = `${last} ${cut}`;
    else out.push(cut);
  }
  if (out.length > 4) return [...out.slice(0, 3), out.slice(3).join(" ")];
  return out;
}
