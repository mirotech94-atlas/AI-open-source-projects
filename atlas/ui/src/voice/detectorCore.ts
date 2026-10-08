/**
 * The detector contract, the honest fallback, and what to do when a detector
 * fails to start -- kept apart from `detector.ts` on purpose.
 *
 * `detector.ts` dynamically imports the actual wake-word packages
 * (Picovoice, MediaPipe), which are browser-only: neither ships a
 * `main`/`exports` entry Node can resolve, only a bundler-facing `module`
 * field. That is fine for the real app, which never runs this code outside
 * a browser, but it means any test file that merely imports `detector.ts` --
 * even to reach code that never touches those packages -- drags Vite's
 * SSR module resolution through them and fails before a single test runs.
 * This module has no such import, so it can be tested directly.
 */

import type { Phase } from "./loop";
import type { MicState } from "./mic";

export type DetectorKind =
  | "porcupine"
  | "local-whisper"
  | "pinch"
  | "speech-api"
  | "click-to-talk";

export interface Detector {
  readonly kind: DetectorKind;
  /** True when this detector actually listens for a spoken phrase. */
  readonly listensForPhrase: boolean;
  /** True when this detector actually listens for a hand gesture. */
  readonly listensForGesture: boolean;
  /** True when a clap gate is riding beside this detector on its own
   *  on-device mic tap (Task 47 / G4). Not `readonly`: a composite may
   *  learn, only after `start()` runs, that its own clap half failed to
   *  open a microphone while the detector it rides beside is still live --
   *  the field is corrected in place rather than left claiming a gate that
   *  never actually started (invariant 7's spirit: UNKNOWN, not a false OK). */
  clap: boolean;
  /** `onFailure` is for a detector that dies AFTER starting (a crashed
   *  worker): it must say so rather than keep reading as live. */
  start(onWake: () => void, onFailure?: (err: unknown) => void): Promise<void>;
  stop(): Promise<void>;
}

class ClickToTalk implements Detector {
  readonly kind = "click-to-talk" as const;
  readonly listensForPhrase = false;
  readonly listensForGesture = false;
  clap = false;
  async start(): Promise<void> {}
  async stop(): Promise<void> {}
}

/**
 * The fallback everything else falls back to. Exported as a factory, not the
 * class itself, so a failed detector can be replaced with a fresh one that
 * has never been started -- reusing a detector instance across a failure
 * would let whatever broke it (a half-open stream, a released worker) leak
 * into the thing meant to be the safe floor.
 */
export function createClickToTalk(): Detector {
  return new ClickToTalk();
}

/**
 * What to fall back to, and what to say, when a detector's own `start()`
 * rejects -- a denied camera or microphone permission, a model or wasm
 * fetch that 404s, a worker that fails to spin up. `createDetector` already
 * returns `null` (and `detector.ts` logs a warning) for a detector that
 * fails to *load*; this covers the other half, a detector that loaded fine
 * and then failed to *start*. Both must land on click-to-talk, because a
 * detector that is neither running nor reported as click-to-talk is the
 * "reads as on but is not" trap this file exists to avoid.
 *
 * A pure function on purpose: the failure handling itself lives in
 * `useVoice`'s effect, which also has to call `stop()` on the failed
 * detector and touch React state -- neither of which belongs in a unit test.
 * This is the part of that decision that can be pinned with one.
 */
export function fallbackAfterStartFailure(
  kind: DetectorKind,
  err: unknown,
): { kind: "click-to-talk"; problem: string } {
  const reason = err instanceof Error ? err.message : String(err);
  const cause =
    kind === "pinch"
      ? err instanceof DOMException && err.name === "NotAllowedError"
        ? "camera refused"
        : err instanceof DOMException &&
            (err.name === "NotFoundError" || err.name === "OverconstrainedError")
          ? "no camera available"
          : `pinch wake unavailable (${reason})`
      : kind === "local-whisper"
        ? err instanceof DOMException && err.name === "NotAllowedError"
          ? "microphone refused"
          : `local wake unavailable (${reason})`
      : kind === "porcupine"
        ? `wake word unavailable (${reason})`
        : kind === "speech-api"
          ? `speech recognition unavailable (${reason})`
          : `wake detector unavailable (${reason})`;
  return { kind: "click-to-talk", problem: `${cause} — click to talk` };
}

/**
 * Which detector is live, in words -- the hint under the core. Each says
 * what it listens for AND where the listening happens, because the five
 * differ most in the second: two keep everything on this machine, one sends
 * audio to Google, and one listens for nothing at all.
 *
 * `clap` names the composite clap gate (Task 47 / G4) that can run beside
 * porcupine, local-whisper or pinch -- never speech-api (it would say
 * nothing useful about a path that already leaves the machine, and clap
 * never rides it) and never click-to-talk or "choosing…" (there is nothing
 * running to ride beside).
 */
export function describeDetector(kind: DetectorKind | null, clap = false): string {
  let base: string;
  switch (kind) {
    case "porcupine":
      base = "say “atlas” — Porcupine wake word, on this device";
      break;
    case "local-whisper":
      base = "say “atlas” — heard on this machine, nothing sent";
      break;
    case "pinch":
      base = "Pinch or tap to speak — hand tracking, on this device";
      break;
    case "speech-api":
      base = "say “atlas” — browser recogniser, sends audio to Google";
      break;
    case "click-to-talk":
      base = "Click to talk — no wake detector running";
      break;
    case null:
      base = "choosing a wake detector…";
      break;
  }
  return clapRides(kind, clap) ? `${base}, or a double clap` : base;
}

/**
 * Whether a clap gate is really riding beside `kind`. `createDetector` only
 * composites clap onto porcupine, local-whisper and pinch; beside speech-api
 * or click-to-talk it is inert, so no surface may name it there. The one
 * rule every caption below and `describeDetector` above share.
 */
export function clapRides(kind: DetectorKind | null, clap: boolean): boolean {
  return clap && (kind === "porcupine" || kind === "local-whisper" || kind === "pinch");
}

/** The dormant core button's accessible name: what a screen reader says
 *  wakes ATLAS, including the double clap wherever one is armed. */
export function coreLabel(kind: DetectorKind | null, clap = false): string {
  const base = kind === "pinch"
    ? "Pinch or tap to speak"
    : kind === "click-to-talk"
      ? "Click to talk"
      : "Say atlas, or click to talk";
  return clapRides(kind, clap) ? `${base}, or double clap` : base;
}

/**
 * The "Talk to ATLAS" bar's caption. The live phase first, then a detector
 * problem, then a mute, then what wakes it. `listening` is the caller's own
 * "a phrase detector is genuinely listening" -- click-to-talk never is.
 */
export function talkCaption(state: {
  phase: Phase;
  detector: DetectorKind | null;
  detectorProblem: string | null;
  handsFree: boolean;
  detectorClap?: boolean;
  listening: boolean;
}): string {
  // While ATLAS thinks or speaks, a tap on the bar stops it (`interrupt.ts`).
  if (state.phase === "thinking" || state.phase === "responding") return "Tap to stop";
  if (state.phase !== "dormant") return state.phase;
  if (state.detectorProblem) return state.detectorProblem;
  if (!state.handsFree) return "Muted — tap to speak";
  const clap = clapRides(state.detector, state.detectorClap ?? false);
  if (state.detector === "pinch") {
    return clap ? "Pinch, double clap or tap to speak" : "Pinch or tap to speak";
  }
  if (state.listening) return clap ? "I am listening… or double clap" : "I am listening…";
  return "Tap to speak";
}

/**
 * The line under the core while ATLAS is dormant -- the same words in the
 * overlay and on the AI Core stage. Null mid-conversation: the pill speaks
 * then. A detector problem comes first, then a mute, then the detector.
 */
export function voiceHint(state: {
  phase: Phase;
  detector: DetectorKind | null;
  detectorProblem: string | null;
  handsFree: boolean;
  /** Optional so every existing caller keeps compiling and passing tests
   *  unchanged; absent is treated as "no clap gate running". */
  detectorClap?: boolean;
}): string | null {
  if (state.phase !== "dormant") return null;
  if (state.detectorProblem) return state.detectorProblem;
  if (!state.handsFree) return "muted — tap the mic badge to listen again";
  return describeDetector(state.detector, state.detectorClap ?? false);
}

/**
 * What a tap on the mic badge does. The badge is the MUTE control, never
 * the wake one: waking belongs to the wake word, the pinch, the core and the
 * "Talk to ATLAS" bar. It mutes an open mic, and a dormant pinch too (whose
 * mic is off but whose camera is still watching for the gesture); it
 * unmutes a mute; and with nothing armed it does nothing.
 */
export function micBadgeAction(state: {
  mic: MicState;
  handsFree: boolean;
  detector: DetectorKind | null;
}): "mute" | "unmute" | null {
  if (state.mic === "listening" || state.mic === "streaming") return "mute";
  // Pinch never needs the microphone, whether it is off, unavailable, or
  // (Task 47 / G4: a clap composite forces the app mic open for clap's own
  // tap) denied outright -- a denied APP mic says nothing about pinch's own
  // camera permission, which it never asked for in the first place.
  if (state.handsFree && state.detector === "pinch") return "mute";
  if (state.mic === "denied" || state.mic === "unavailable") return null;
  if (!state.handsFree) return "unmute";
  return null;
}

/** The hand badge's tooltip. Pinch-to-wake shares this camera, and a mute
 *  on the mic badge disarms it too -- both are said here, where the hand
 *  is, rather than discovered by pinching at nothing. */
export function handBadgeTitle(cameraOn: boolean, pinch: "live" | "muted" | "none"): string {
  const muted = "Pinch-to-wake is muted — tap the mic badge to re-arm it.";
  if (!cameraOn) {
    return pinch === "muted"
      ? `Click to turn on the camera for hand gestures. ${muted}`
      : "Click to turn on the camera for hand gestures";
  }
  if (pinch === "live") {
    return "Click to stop the camera. Turning the hand off also turns off pinch-to-wake, "
      + "and so does Tap to mute on the mic badge.";
  }
  if (pinch === "muted") return `Click to stop the camera. ${muted}`;
  return "Click to stop the camera";
}

/** A wake gate that died after starting (the whisper worker crashed, the GPU
 *  was lost), and the detector that took over. */
export function gateStoppedProblem(
  reason: string, next: DetectorKind, nextClap = false,
): string {
  return `wake gate stopped (${reason}) — now: ${describeDetector(next, nextClap)}`;
}

/**
 * Runs a clap gate beside whatever detector is actually live -- the ruling
 * for Task 47 / G4. `kind` and `listensForGesture` stay the inner
 * detector's own (no new `DetectorKind` for a pairing that can happen with
 * three different detectors); `listensForPhrase` is forced true because
 * clap always taps a live microphone of its own, and the mic badge must say
 * so even when the inner detector is pinch, which never otherwise touches
 * the mic.
 *
 * `openStream` is the actual microphone tap -- `openClapStream` in
 * `detector.ts`, which this file cannot import (see this file's own header)
 * -- injected so it can be driven exactly in a test: made to hang, reject,
 * or never be called at all.
 *
 * Three failure shapes matter, and all three are pinned by
 * `detectorCore.test.ts`:
 *   - the inner detector's own `start()` rejects: `openStream` is never
 *     even attempted, and the rejection propagates so the caller falls the
 *     whole thing back to click-to-talk exactly as it would without clap.
 *   - `openStream` rejects *after* the inner detector already started: only
 *     `clap` is unset, in place -- the inner detector keeps running. A
 *     caller must re-read `.clap` after `start()` resolves to see this.
 *   - `stop()` lands while `start()` is still awaiting `inner.start()` or
 *     `openStream()`: no stream is left running. Either it is never opened
 *     (the first case) or, if it finishes opening after `stop()` already
 *     ran, it is closed immediately (the second) -- a live microphone the
 *     mic badge has already reported as "off" is exactly the lie this
 *     whole file exists to prevent.
 */
export function withClap(
  inner: Detector,
  openStream: (onWake: () => void) => Promise<() => void>,
): Detector {
  let closeClap: (() => void) | null = null;
  let stopped = false;
  const composite: Detector = {
    kind: inner.kind,
    listensForPhrase: true,
    listensForGesture: inner.listensForGesture,
    clap: true,
    async start(onWake: () => void, onFailure?: (err: unknown) => void): Promise<void> {
      stopped = false;
      await inner.start(onWake, onFailure);
      // stop() may have landed while the line above was still pending (a
      // camera prompt, whisper's own getUserMedia) -- there is nothing left
      // to run clap beside.
      if (stopped) return;
      // A caller (useVoice.ts, when the app mic is denied but the inner
      // detector never needed it) may already have decided there is
      // nothing for clap to ride. Honour that rather than opening a stream
      // anyway.
      if (!composite.clap) return;
      try {
        const close = await openStream(onWake);
        if (stopped) {
          // stop() landed while openStream() itself was pending.
          close();
          return;
        }
        closeClap = close;
      } catch {
        // The inner detector is live; losing the clap half must not take
        // it down too. It just isn't running, and `clap` says so.
        composite.clap = false;
      }
    },
    async stop(): Promise<void> {
      stopped = true;
      closeClap?.();
      closeClap = null;
      await inner.stop();
    },
  };
  return composite;
}
