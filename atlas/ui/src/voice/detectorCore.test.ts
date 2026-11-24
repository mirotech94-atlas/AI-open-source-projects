import { describe, expect, it, vi } from "vitest";
// Imported from detectorCore, not detector.ts: detector.ts dynamically
// imports browser-only packages with no Node-resolvable entry point, which
// would fail Vite's SSR module resolution the moment this test file merely
// imported it -- see detectorCore.ts's header. withClap lives here, not in
// detector.ts, for exactly that reason: it needs to be testable with a fake
// stream-opener, and importing detector.ts at all is off-limits here.
import {
  coreLabel,
  createClickToTalk,
  describeDetector,
  fallbackAfterStartFailure,
  gateStoppedProblem,
  handBadgeTitle,
  micBadgeAction,
  talkCaption,
  voiceHint,
  withClap,
  type Detector,
} from "./detectorCore";

/** A controllable promise: resolve/reject it from outside, on the test's own
 *  schedule, so a race between start() and stop() can be driven exactly. */
function deferred<T>(): {
  promise: Promise<T>; resolve: (v: T) => void; reject: (e: unknown) => void;
} {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function fakeDetector(over: Partial<Detector> = {}): Detector {
  return {
    kind: "pinch",
    listensForPhrase: false,
    listensForGesture: true,
    clap: false,
    start: async () => {},
    stop: async () => {},
    ...over,
  };
}

describe("createClickToTalk", () => {
  it("listens for neither a phrase nor a gesture, and starts/stops as a no-op", async () => {
    const detector = createClickToTalk();
    expect(detector.kind).toBe("click-to-talk");
    expect(detector.listensForPhrase).toBe(false);
    expect(detector.listensForGesture).toBe(false);
    await expect(detector.start(() => {})).resolves.toBeUndefined();
    await expect(detector.stop()).resolves.toBeUndefined();
  });

  it("hands back a fresh instance each time, so a failed detector is never reused", () => {
    expect(createClickToTalk()).not.toBe(createClickToTalk());
  });
});

describe("fallbackAfterStartFailure", () => {
  it("always falls back to click-to-talk, whatever failed", () => {
    expect(fallbackAfterStartFailure("pinch", new Error("x")).kind)
      .toBe("click-to-talk");
    expect(fallbackAfterStartFailure("porcupine", new Error("x")).kind)
      .toBe("click-to-talk");
    expect(fallbackAfterStartFailure("speech-api", new Error("x")).kind)
      .toBe("click-to-talk");
  });

  it("names a refused camera specifically, for pinch", () => {
    const err = new DOMException("Permission denied", "NotAllowedError");
    const { problem } = fallbackAfterStartFailure("pinch", err);
    expect(problem).toBe("camera refused — click to talk");
  });

  it("names a missing camera specifically, for pinch", () => {
    const notFound = new DOMException("no device", "NotFoundError");
    expect(fallbackAfterStartFailure("pinch", notFound).problem)
      .toBe("no camera available — click to talk");

    const overconstrained = new DOMException("no match", "OverconstrainedError");
    expect(fallbackAfterStartFailure("pinch", overconstrained).problem)
      .toBe("no camera available — click to talk");
  });

  it("falls back to the error's own message for any other pinch failure", () => {
    const { problem } = fallbackAfterStartFailure("pinch", new Error("wasm 404"));
    expect(problem).toBe("pinch wake unavailable (wasm 404) — click to talk");
  });

  it("names porcupine and speech-api failures without pretending it was a camera", () => {
    expect(fallbackAfterStartFailure("porcupine", new Error("worker crashed")).problem)
      .toBe("wake word unavailable (worker crashed) — click to talk");
    expect(fallbackAfterStartFailure("speech-api", new Error("service down")).problem)
      .toBe("speech recognition unavailable (service down) — click to talk");
  });

  it("stringifies a non-Error rejection rather than throwing on it", () => {
    const { problem } = fallbackAfterStartFailure("pinch", "camera is busy");
    expect(problem).toBe("pinch wake unavailable (camera is busy) — click to talk");
  });

  it("names a refused microphone for the on-device whisper gate", () => {
    const err = new DOMException("Permission denied", "NotAllowedError");
    expect(fallbackAfterStartFailure("local-whisper", err).problem)
      .toBe("microphone refused — click to talk");
    expect(fallbackAfterStartFailure("local-whisper", new Error("model 404")).problem)
      .toBe("local wake unavailable (model 404) — click to talk");
  });
});

describe("describeDetector", () => {
  it("says which of the five detectors is live, in words", () => {
    const kinds = ["porcupine", "local-whisper", "pinch", "speech-api", "click-to-talk"] as const;
    const words = kinds.map((k) => describeDetector(k));
    expect(new Set(words).size).toBe(5);
    expect(describeDetector("local-whisper")).toContain("atlas");
    expect(describeDetector("local-whisper")).toContain("this machine");
    expect(describeDetector("speech-api")).toContain("Google");
    expect(describeDetector("pinch")).toContain("Pinch");
    expect(describeDetector("click-to-talk")).toContain("Click");
  });

  it("says nothing is chosen yet rather than guessing", () => {
    expect(describeDetector(null)).toBe("choosing a wake detector…");
  });

  it("names the clap gate riding beside porcupine, local-whisper or pinch", () => {
    for (const kind of ["porcupine", "local-whisper", "pinch"] as const) {
      expect(describeDetector(kind, true)).toContain(", or a double clap");
      expect(describeDetector(kind, false)).not.toContain("clap");
    }
  });

  it("never claims a clap gate beside speech-api, click-to-talk or nothing chosen -- "
    + "there is nothing on-device for it to ride beside", () => {
    expect(describeDetector("speech-api", true)).not.toContain("clap");
    expect(describeDetector("click-to-talk", true)).not.toContain("clap");
    expect(describeDetector(null, true)).not.toContain("clap");
  });
});

describe("voiceHint", () => {
  const base = { phase: "dormant" as const, detector: "local-whisper" as const,
                 detectorProblem: null, handsFree: true };

  it("names the live detector while dormant", () => {
    expect(voiceHint(base)).toBe(describeDetector("local-whisper"));
    expect(voiceHint({ ...base, detector: "speech-api" })).toContain("Google");
  });

  it("says muted when hands-free is off, whatever the detector", () => {
    expect(voiceHint({ ...base, handsFree: false })).toMatch(/^muted/);
  });

  it("puts a detector problem first", () => {
    expect(voiceHint({ ...base, detectorProblem: "camera refused — click to talk" }))
      .toBe("camera refused — click to talk");
  });

  it("is silent mid-conversation -- the pill speaks then", () => {
    expect(voiceHint({ ...base, phase: "listening" })).toBeNull();
  });

  it("names the clap gate when one is riding beside the live detector", () => {
    expect(voiceHint({ ...base, detectorClap: true }))
      .toBe(describeDetector("local-whisper", true));
  });

  it("defaults to no clap gate when the field is absent, so every existing "
    + "caller keeps behaving exactly as it did before Task 47", () => {
    expect(voiceHint(base)).toBe(describeDetector("local-whisper", false));
  });
});

describe("micBadgeAction", () => {
  it("mutes an open mic", () => {
    expect(micBadgeAction({ mic: "listening", handsFree: true, detector: "local-whisper" }))
      .toBe("mute");
    expect(micBadgeAction({ mic: "streaming", handsFree: true, detector: "pinch" }))
      .toBe("mute");
  });

  it("mutes a dormant pinch even though the mic is off", () => {
    expect(micBadgeAction({ mic: "off", handsFree: true, detector: "pinch" })).toBe("mute");
  });

  it("mutes a clap-composited pinch even though the APP mic was denied -- "
    + "a denied app mic (clap's own tap) says nothing about pinch's own "
    + "camera permission, which pinch never needed in the first place "
    + "(Task 47 / G4)", () => {
    expect(micBadgeAction({ mic: "denied", handsFree: true, detector: "pinch" }))
      .toBe("mute");
  });

  it("unmutes when muted", () => {
    expect(micBadgeAction({ mic: "off", handsFree: false, detector: "pinch" })).toBe("unmute");
  });

  it("restarts the mic when it reads \"Mic off\" with hands-free armed and "
    + "no pinch behind it -- the badge must never be a dead button", () => {
    for (const detector of ["click-to-talk", "porcupine", "local-whisper", "speech-api", null] as const) {
      expect(micBadgeAction({ mic: "off", handsFree: true, detector })).toBe("restart");
    }
  });

  it("retries a denied or unavailable mic so the real reason is shown, "
    + "except behind a pinch, which mutes", () => {
    expect(micBadgeAction({ mic: "denied", handsFree: true, detector: "porcupine" }))
      .toBe("restart");
    expect(micBadgeAction({ mic: "unavailable", handsFree: true, detector: "click-to-talk" }))
      .toBe("restart");
    expect(micBadgeAction({ mic: "unavailable", handsFree: true, detector: "pinch" }))
      .toBe("mute");
  });

  it("unmutes a denied mic that was muted", () => {
    expect(micBadgeAction({ mic: "denied", handsFree: false, detector: null })).toBe("unmute");
  });
});

describe("handBadgeTitle", () => {
  it("says that mute also stops pinch-to-wake while pinch is live", () => {
    const title = handBadgeTitle(true, "live");
    expect(title).toContain("also turns off pinch-to-wake");
    expect(title).toContain("mute");
  });

  it("says pinch-to-wake is muted, and how to re-arm it", () => {
    expect(handBadgeTitle(true, "muted")).toContain("Pinch-to-wake is muted");
    expect(handBadgeTitle(false, "muted")).toContain("Pinch-to-wake is muted");
  });

  it("keeps the plain camera wording when pinch is not the wake detector", () => {
    expect(handBadgeTitle(false, "none")).toBe("Click to turn on the camera for hand gestures");
    expect(handBadgeTitle(true, "none")).toBe("Click to stop the camera");
  });
});

describe("gateStoppedProblem", () => {
  it("says the gate stopped and names what took over", () => {
    expect(gateStoppedProblem("worker crashed", "pinch"))
      .toBe(`wake gate stopped (worker crashed) — now: ${describeDetector("pinch")}`);
  });

  it("names the clap gate on the detector that took over, when it has one", () => {
    expect(gateStoppedProblem("worker crashed", "pinch", true))
      .toBe(`wake gate stopped (worker crashed) — now: ${describeDetector("pinch", true)}`);
  });
});

describe("withClap", () => {
  it("mirrors the inner detector's kind and listensForGesture, forces "
    + "listensForPhrase, and reports clap: true", () => {
    const inner = fakeDetector({ kind: "porcupine", listensForPhrase: true,
                                  listensForGesture: false });
    const composite = withClap(inner, async () => (() => {}));
    expect(composite.kind).toBe("porcupine");
    expect(composite.listensForPhrase).toBe(true);
    expect(composite.listensForGesture).toBe(false);
    expect(composite.clap).toBe(true);
  });

  it("never attempts to open a clap stream when the inner detector's own "
    + "start() rejects", async () => {
    const inner = fakeDetector({
      start: async () => { throw new Error("camera refused"); },
    });
    const openStream = vi.fn(async () => (() => {}));
    const composite = withClap(inner, openStream);

    await expect(composite.start(() => {})).rejects.toThrow("camera refused");
    expect(openStream).not.toHaveBeenCalled();
  });

  it("opens its own stream only after the inner detector has actually "
    + "started", async () => {
    const order: string[] = [];
    const inner = fakeDetector({
      start: async () => { order.push("inner"); },
    });
    const openStream = async (): Promise<() => void> => {
      order.push("clap");
      return () => {};
    };
    const composite = withClap(inner, openStream);
    await composite.start(() => {});
    expect(order).toEqual(["inner", "clap"]);
  });

  it("skips opening a stream at all when clap was already disabled before "
    + "start() ran -- the mic-denied-but-pinch-still-works path", async () => {
    const inner = fakeDetector({ listensForGesture: true });
    const openStream = vi.fn(async () => (() => {}));
    const composite = withClap(inner, openStream);
    composite.clap = false; // as useVoice.ts does when the app mic is denied
    await composite.start(() => {});
    expect(openStream).not.toHaveBeenCalled();
    expect(composite.clap).toBe(false);
  });

  it("disables clap in place, without touching the inner detector, when "
    + "clap's own stream fails to open after the inner detector already "
    + "started", async () => {
    const inner = fakeDetector();
    const openStream = async (): Promise<() => void> => {
      throw new Error("microphone refused");
    };
    const composite = withClap(inner, openStream);
    await expect(composite.start(() => {})).resolves.toBeUndefined();
    expect(composite.clap).toBe(false);
  });

  it("does not leak a clap stream that finishes opening after stop() was "
    + "already called while the inner detector's own start() was still "
    + "pending", async () => {
    const innerStart = deferred<void>();
    const inner = fakeDetector({ start: () => innerStart.promise });
    const openStream = vi.fn(async () => (() => {}));
    const composite = withClap(inner, openStream);

    const started = composite.start(() => {});
    const stopped = composite.stop();       // lands before inner.start() resolves
    innerStart.resolve();
    await Promise.all([started, stopped]);

    // The whole point: a stream must never be opened onto a detector that
    // has already been told to shut down.
    expect(openStream).not.toHaveBeenCalled();
  });

  it("closes a clap stream immediately if it finishes opening after stop() "
    + "was called while the stream itself was still opening", async () => {
    const openClose = vi.fn();
    const openStream = deferred<() => void>();
    const inner = fakeDetector();
    const composite = withClap(inner, () => openStream.promise);

    const started = composite.start(() => {});
    // Let start() actually run past inner.start() and reach the point
    // where it is awaiting openStream() itself -- otherwise stop()'s
    // synchronous flag-set would land before start() ever gets there,
    // which is the other race, covered by the previous test.
    for (let i = 0; i < 4; i += 1) await Promise.resolve();
    const stopped = composite.stop();       // lands while openStream() is pending
    openStream.resolve(openClose);
    await Promise.all([started, stopped]);

    expect(openClose).toHaveBeenCalledTimes(1);
  });

  it("stops both halves on a clean stop()", async () => {
    const innerStop = vi.fn(async () => {});
    const clapClose = vi.fn();
    const inner = fakeDetector({ stop: innerStop });
    const composite = withClap(inner, async () => clapClose);

    await composite.start(() => {});
    await composite.stop();

    expect(clapClose).toHaveBeenCalledTimes(1);
    expect(innerStop).toHaveBeenCalledTimes(1);
  });
});

// Task 47 put the clap into the visible hint; the core button's accessible
// name and the "Talk to ATLAS" caption still said "Pinch or tap to speak", so
// a screen reader never heard that a double clap wakes him. One rule now
// names the clap on every surface: the same one describeDetector uses.
describe("coreLabel", () => {
  it("keeps the words it had without a clap gate", () => {
    expect(coreLabel("pinch")).toBe("Pinch or tap to speak");
    expect(coreLabel("click-to-talk")).toBe("Click to talk");
    expect(coreLabel("local-whisper")).toBe("Say atlas, or click to talk");
    expect(coreLabel(null)).toBe("Say atlas, or click to talk");
  });

  it("names the double clap wherever it rides", () => {
    for (const kind of ["porcupine", "local-whisper", "pinch"] as const) {
      expect(coreLabel(kind, true)).toContain("double clap");
    }
  });

  it("never names a clap beside speech-api, click-to-talk or nothing chosen", () => {
    for (const kind of ["speech-api", "click-to-talk", null] as const) {
      expect(coreLabel(kind, true)).not.toContain("clap");
    }
  });
});

describe("talkCaption", () => {
  const base = { phase: "dormant" as const, detector: "pinch" as const,
                 detectorProblem: null, handsFree: true, detectorClap: false,
                 listening: false };

  it("keeps the words it had without a clap gate", () => {
    expect(talkCaption(base)).toBe("Pinch or tap to speak");
    expect(talkCaption({ ...base, detector: "local-whisper", listening: true }))
      .toBe("I am listening…");
    expect(talkCaption({ ...base, detector: "click-to-talk" })).toBe("Tap to speak");
    expect(talkCaption({ ...base, handsFree: false })).toBe("Muted — tap to speak");
  });

  it("names the double clap beside a pinch or a listening wake gate", () => {
    expect(talkCaption({ ...base, detectorClap: true })).toContain("double clap");
    expect(talkCaption({ ...base, detector: "porcupine", listening: true,
                         detectorClap: true })).toContain("double clap");
  });

  it("never names a clap it has no gate for", () => {
    expect(talkCaption({ ...base, detector: "speech-api", listening: true,
                         detectorClap: true })).not.toContain("clap");
    expect(talkCaption({ ...base, detector: "click-to-talk", detectorClap: true }))
      .not.toContain("clap");
  });

  it("says a problem, a mute or the live phase before any clap", () => {
    const clap = { ...base, detectorClap: true };
    expect(talkCaption({ ...clap, detectorProblem: "mic blocked" })).toBe("mic blocked");
    expect(talkCaption({ ...clap, handsFree: false })).toBe("Muted — tap to speak");
    expect(talkCaption({ ...clap, phase: "listening" })).toBe("listening");
  });

  it("says a tap stops ATLAS while it thinks or speaks", () => {
    expect(talkCaption({ ...base, phase: "thinking" })).toBe("Tap to stop");
    expect(talkCaption({ ...base, phase: "responding" })).toBe("Tap to stop");
  });
});
