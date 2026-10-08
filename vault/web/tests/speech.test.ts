/* The two traps in speech.ts, as tests.
 *
 * The suspended-context test is the one that matters and the one that will
 * never reproduce by hand: a wallboard's toggle is clicked once when the
 * screen is set up and the first CRIT arrives four hours later, by which
 * time the browser may have suspended an idle AudioContext. Write it first.
 *
 * The track-release test is the other. A stopped MediaRecorder does not
 * release the microphone; the browser's recording indicator stays lit until
 * every track is stopped. A security product that appears to be listening
 * when it is not is a worse bug than one that fails to listen. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  audioReady, enableAudio, resetSpeechForTests, speak, stopSpeaking,
} from "../src/lib/speech";

class FakeSource {
  buffer: unknown = null;
  onended: (() => void) | null = null;
  started = false;
  stopped = false;
  connect() {}
  start() { this.started = true; }
  stop() { this.stopped = true; }
}

class FakeContext {
  state: "running" | "suspended" = "running";
  destination = {};
  sources: FakeSource[] = [];
  resume = vi.fn(async () => { this.state = "running"; });
  decodeAudioData = vi.fn(async () => ({ duration: 1 }));
  createBufferSource() {
    const s = new FakeSource();
    this.sources.push(s);
    return s;
  }
}

let ctx: FakeContext;

beforeEach(() => {
  resetSpeechForTests();
  ctx = new FakeContext();
  // The brief writes this as `vi.fn(() => ctx)`. Under this project's
  // vitest/tinyspy version, `new AudioContext()` then throws "is not a
  // constructor" — an arrow function has no `[[Construct]]` slot, so
  // `Reflect.construct` on it fails regardless of the `vi.fn` wrapper. The
  // module's own `try/catch` in `enableAudio` swallowed that throw, which is
  // exactly the kind of test-passes-for-the-wrong-reason trap this suite
  // exists to avoid: several assertions below read as "the module returned
  // false" when the real story was "the mock could not be constructed". A
  // plain function has no such restriction and returns the same `ctx`.
  vi.stubGlobal("AudioContext", vi.fn(function AudioContextMock() { return ctx; }));
  vi.stubGlobal("fetch", vi.fn(async () => ({
    ok: true,
    arrayBuffer: async () => new ArrayBuffer(8),
  })));
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("the audio context's lifetime", () => {
  it("plays nothing before a gesture has enabled it", async () => {
    expect(audioReady()).toBe(false);
    expect(await speak("hello")).toBe(false);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("is ready once a gesture enables it", () => {
    expect(enableAudio()).toBe(true);
    expect(audioReady()).toBe(true);
  });

  it("reuses one context across gestures rather than making a second", () => {
    enableAudio();
    enableAudio();
    expect(AudioContext).toHaveBeenCalledTimes(1);
  });

  it("refuses to pretend when the browser suspended it later", async () => {
    enableAudio();
    ctx.state = "suspended";           // hours pass on an untouched wallboard
    ctx.resume.mockImplementation(async () => {});   // a refused resume
    expect(audioReady()).toBe(false);
    expect(await speak("a new critical")).toBe(false);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("asks a suspended context to resume rather than reading state too early", () => {
    // `resume()` is async. `enableAudio` must not claim the context is
    // running on the next line — it reports that a context EXISTS, and
    // `speak` does the running check at the moment it matters.
    enableAudio();
    ctx.state = "suspended";
    ctx.resume.mockImplementation(async () => {});
    expect(enableAudio()).toBe(true);        // a context exists
    expect(ctx.resume).toHaveBeenCalled();
    expect(audioReady()).toBe(false);        // and it is still not audible
  });
});

describe("playing", () => {
  it("posts the text and starts a source", async () => {
    enableAudio();
    expect(await speak("Everything is fine.")).toBe(true);
    const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/api/v1/voice/speak");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ text: "Everything is fine." });
    expect(ctx.sources[0].started).toBe(true);
  });

  it("stops the previous voice before starting a new one", async () => {
    enableAudio();
    await speak("first");
    await speak("second");
    expect(ctx.sources[0].stopped).toBe(true);
    expect(ctx.sources[1].stopped).toBe(false);
  });

  it("stopSpeaking silences whatever is playing", async () => {
    enableAudio();
    await speak("first");
    stopSpeaking();
    expect(ctx.sources[0].stopped).toBe(true);
  });

  /* Fix round 2, minor-folded-in. `stopSpeaking()` used to only stop what was
   * already audible; a `speak()` whose fetch/decode was still in flight would
   * arrive late and play anyway. Tasks 7 and 8 both call `stopSpeaking()`
   * expecting the toggle-off to be final -- a control that says silence and
   * then keeps talking is the exact "appears to be listening when it is not"
   * lie the module's own docstring names, just on the speaker instead of the
   * microphone. */
  it("a stopSpeaking() while the request is in flight cancels it, not just what is audible", async () => {
    enableAudio();
    // Definite-assignment (`!`), not `| null`: with no `await` between this
    // declaration and its use below, TypeScript's control-flow narrowing
    // does not credit the Promise executor's (synchronous, in reality)
    // assignment, and a `T | null` variable reassigned only inside that
    // closure reads back as `never` at the call site rather than `T | null`
    // -- confirmed against this project's own tsconfig, not assumed.
    let resolveFetch!: (v: { ok: boolean; arrayBuffer: () => Promise<ArrayBuffer> }) => void;
    const pending = new Promise<{ ok: boolean; arrayBuffer: () => Promise<ArrayBuffer> }>((resolve) => {
      resolveFetch = resolve;
    });
    vi.stubGlobal("fetch", vi.fn(() => pending));

    const result = speak("a critical that arrives late");
    stopSpeaking();   // the toggle is switched off while the round trip is in the air
    resolveFetch({ ok: true, arrayBuffer: async () => new ArrayBuffer(8) });

    expect(await result).toBe(false);
    expect(ctx.sources.length).toBe(0);
  });

  it("a context suspended mid-flight plays nothing, even once the fetch succeeds", async () => {
    enableAudio();
    let resolveFetch!: (v: { ok: boolean; arrayBuffer: () => Promise<ArrayBuffer> }) => void;
    const pending = new Promise<{ ok: boolean; arrayBuffer: () => Promise<ArrayBuffer> }>((resolve) => {
      resolveFetch = resolve;
    });
    vi.stubGlobal("fetch", vi.fn(() => pending));

    const result = speak("a critical that arrives late");
    ctx.state = "suspended";   // the browser suspended it while the round trip was in flight
    resolveFetch({ ok: true, arrayBuffer: async () => new ArrayBuffer(8) });

    expect(await result).toBe(false);
    expect(ctx.sources.length).toBe(0);
  });

  it("is silent rather than loud about a failed request", async () => {
    enableAudio();
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, arrayBuffer: async () => new ArrayBuffer(0) })));
    expect(await speak("hello")).toBe(false);
  });

  it("says nothing when there is nothing to say", async () => {
    enableAudio();
    expect(await speak("   ")).toBe(false);
    expect(fetch).not.toHaveBeenCalled();
  });
});
