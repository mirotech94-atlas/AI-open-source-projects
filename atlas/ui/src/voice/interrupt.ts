/**
 * Taking the floor back from ATLAS, as pure rules -- no DOM, no timers.
 *
 * "I should be able to interrupt while it's speaking, at anything." Two
 * causes, one outcome: he talks over an answer (`bargeIn.ts` decided it was
 * him and not ATLAS's echo), or he taps the core / "Tap to stop". Either
 * way ATLAS goes quiet at once -- the playing voice, a voice still being
 * fetched, and one the autoplay policy is holding for the next tap -- and
 * his next turn starts recording now, in the same conversation, rather
 * than after the typing has caught up.
 *
 * The in-flight reply is dropped on the client only. Whatever the server
 * already did with the request stays as it was: at most a card waiting for
 * his yes (nothing in voice acts), so there is nothing to undo.
 */
import type { Phase } from "./loop";

export type InterruptCause = "voice" | "tap";

export interface InterruptPlan {
  /** Pause the element that is playing. */
  stopPlayback: boolean;
  /** Abort the /api/voice/speak fetch, so a late voice never plays. */
  abortSpeech: boolean;
  /** Forget an answer the autoplay policy held back for the next tap. */
  dropHeld: boolean;
  /** Abort the /api/voice/ask fetch and drop its reply. */
  abortAsk: boolean;
  /** Start recording his turn now, not after the answer's bookkeeping. */
  startRecording: boolean;
  /** He is already talking: the turn has speech in it from the start, so
   *  the quiet tail can end it and the no-speech timeout cannot. */
  alreadySpeaking: boolean;
}

/**
 * What an interrupt does in this phase, or null when there is nothing to
 * interrupt. A voice barge-in only ever happens while an answer is being
 * said; a tap also cuts a question still being thought about.
 */
export function planInterrupt(phase: Phase, cause: InterruptCause): InterruptPlan | null {
  if (phase === "responding") {
    return {
      stopPlayback: true, abortSpeech: true, dropHeld: true, abortAsk: false,
      startRecording: true, alreadySpeaking: cause === "voice",
    };
  }
  if (phase === "thinking" && cause === "tap") {
    return {
      stopPlayback: true, abortSpeech: true, dropHeld: true, abortAsk: true,
      startRecording: true, alreadySpeaking: false,
    };
  }
  return null;
}

export type TapAction = "wake" | "interrupt" | "dismiss" | "none";

/**
 * What one tap on the core (or the voice panel's button) means now. While
 * ATLAS is thinking or speaking it is "stop, I want to talk"; the mute
 * button and Escape still end the conversation outright.
 */
export function tapAction(phase: Phase): TapAction {
  switch (phase) {
    case "dormant":
      return "wake";
    case "thinking":
    case "responding":
      return "interrupt";
    case "waking":
    case "listening":
      return "dismiss";
    case "dismissing":
      return "none";
  }
}

/** The words on the control, so a tap never does something unannounced. */
export function tapLabel(phase: Phase): string | null {
  const action = tapAction(phase);
  if (action === "interrupt") return "Tap to stop";
  return null;
}
