/* `useDictation` releases the microphone on BOTH paths. See speech.test.ts's
 * header for why the error path is the one that regresses. */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { resetSpeechForTests, useDictation } from "../src/lib/speech";

let tracks: { stop: ReturnType<typeof vi.fn> }[];
let recorder: {
  start: ReturnType<typeof vi.fn>;
  stop: ReturnType<typeof vi.fn>;
  ondataavailable: ((e: { data: Blob }) => void) | null;
  onstop: (() => void) | null;
  state: string;
  mimeType: string;
};

function Harness({ onText }: { onText: (t: string) => void }) {
  const dict = useDictation({ onText });
  return (
    <>
      <button onPointerDown={dict.start} onPointerUp={dict.stop}>talk</button>
      <span data-testid="rec">{dict.recording ? "on" : "off"}</span>
      <span data-testid="err">{dict.error}</span>
    </>
  );
}

beforeEach(() => {
  resetSpeechForTests();
  // Fix round 4. The brief's track stub is a bare `vi.fn()`, which makes
  // stopping a track a no-op that no other object can observe. A real one is
  // not inert: per mediacapture-record, ending the last live track makes the
  // stream inactive, and a `MediaRecorder` still in "recording" when that
  // happens fires its own `dataavailable` and `stop` — nobody has to call
  // `rec.stop()` for `onstop` to run. Without that side effect here the suite
  // is structurally blind to anything `release()` sets off, which is how the
  // unmount-mid-press upload below survived three rounds of review.
  //
  // One deliberate divergence from that real behaviour, called out because
  // fidelity is this file's whole point: the stub fires only `stop`, never
  // the `dataavailable` a real recorder would flush alongside it. The tests
  // that care about a clip's CONTENTS push their own `ondataavailable`
  // (search this file for it), and adding a flush here would double the
  // chunks they set up. A test that needs a clip captured on the
  // release-path — rather than on an explicit `rec.stop()` — has to push
  // that blob itself.
  tracks = [{
    stop: vi.fn(() => {
      if (recorder.state === "recording") {
        // State first, then the handler: `onstop`'s `finally` calls
        // `release()`, which stops these tracks again, and a stub that
        // fired before flipping the state would recurse without end.
        recorder.state = "inactive";
        recorder.onstop?.();
      }
    }),
  }];
  recorder = {
    start: vi.fn(),
    // The brief's stub never mutates `state`, so `useDictation`'s `stop()`
    // always took the `rec.stop()` branch and the `else release()` branch
    // (reached when the browser already ended the recording itself) went
    // unexercised by any test. A real `MediaRecorder.stop()` transitions to
    // "inactive"; matching that here is what let round 2's "already
    // stopped itself" test actually exercise the branch it claims to.
    stop: vi.fn(() => { recorder.state = "inactive"; }),
    ondataavailable: null, onstop: null, state: "recording", mimeType: "audio/webm",
  };
  // The brief's version attaches `isTypeSupported` by re-reading
  // `globalThis.MediaRecorder` after `vi.stubGlobal`; that round-trip is
  // unnecessary — `Rec` below is the same function object either way — but
  // harmless, so it is kept close to the original for anyone diffing against
  // the brief.
  //
  // `vi.fn(() => recorder)` (the brief's arrow form) makes `new
  // MediaRecorder(...)` throw "is not a constructor" under this project's
  // vitest/tinyspy — see speech.test.ts's matching note for `AudioContext`.
  // `useDictation` does not wrap that constructor call in try/catch (only
  // `getUserMedia` is guarded), so the throw became an unhandled rejection
  // and `onstop`/`ondataavailable` were never attached — silently emptying
  // every assertion downstream of it rather than failing loudly at the
  // construction site. A plain function sidesteps the same restriction.
  vi.stubGlobal("MediaRecorder", vi.fn(function MediaRecorderMock() { return recorder; }));
  (globalThis as Record<string, unknown>).MediaRecorder =
    Object.assign((globalThis as Record<string, unknown>).MediaRecorder as object,
                  { isTypeSupported: () => true });
  vi.stubGlobal("navigator", {
    ...navigator,
    mediaDevices: { getUserMedia: vi.fn(async () => ({ getTracks: () => tracks })) },
  });
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ text: "hello there" }) })));
});

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("hands the transcript back and releases the microphone", async () => {
  const onText = vi.fn();
  render(<Harness onText={onText} />);
  const btn = screen.getByText("talk");

  // The brief drives this with `userEvent.pointer({ keys: "[MouseLeft>]" })`.
  // user-event's pointer API depends on jsdom's `PointerEvent` construction
  // matching what it expects internally, which this project's jsdom/vitest
  // pairing does not do reliably — the symptom was `TypeError` from inside
  // user-event's own event factory, not from this module. `Harness` wires
  // its handlers straight to `onPointerDown`/`onPointerUp`, so dispatching
  // those DOM events directly through `fireEvent` exercises the exact same
  // handler with no less fidelity and no dependency on user-event's pointer
  // gesture engine.
  await act(async () => { fireEvent.pointerDown(btn); });
  expect(screen.getByTestId("rec").textContent).toBe("on");

  await act(async () => { fireEvent.pointerUp(btn); });
  await act(async () => {
    recorder.ondataavailable?.({ data: new Blob([new Uint8Array([1, 2])], { type: "audio/webm" }) });
    recorder.onstop?.();
  });

  expect(onText).toHaveBeenCalledWith("hello there");
  expect(tracks[0].stop).toHaveBeenCalled();
  expect(screen.getByTestId("rec").textContent).toBe("off");
});

it("releases the microphone when the upload fails too", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("offline"); }));
  const onText = vi.fn();
  render(<Harness onText={onText} />);
  const btn = screen.getByText("talk");

  await act(async () => { fireEvent.pointerDown(btn); });
  await act(async () => { fireEvent.pointerUp(btn); });
  await act(async () => {
    recorder.ondataavailable?.({ data: new Blob([new Uint8Array([1])], { type: "audio/webm" }) });
    recorder.onstop?.();
  });

  expect(onText).not.toHaveBeenCalled();
  expect(tracks[0].stop).toHaveBeenCalled();
  expect(screen.getByTestId("err").textContent).not.toBe("");
});

it("reports a denied microphone instead of failing silently", async () => {
  vi.stubGlobal("navigator", {
    ...navigator,
    mediaDevices: { getUserMedia: vi.fn(async () => { throw new Error("NotAllowedError"); }) },
  });
  render(<Harness onText={vi.fn()} />);
  await act(async () => { fireEvent.pointerDown(screen.getByText("talk")); });
  await act(async () => {});
  expect(screen.getByTestId("err").textContent).toMatch(/microphone/i);
  expect(screen.getByTestId("rec").textContent).toBe("off");
});

/* Fix round 1: the pending-start race. This is not an edge case — it is the
 * ordinary first-use sequence. The very first press on an origin triggers the
 * native permission prompt, which blocks `getUserMedia`'s promise for as long
 * as it takes the user to move the mouse and click "Allow." A press-and-
 * release shorter than that (a tap, or a finger lifted while still deciding)
 * calls `stop()` while `recRef.current` is still null, so the existing
 * release-on-stop path no-ops — there is nothing to release yet. Without a
 * session check, the permission grant then arrives *after* the button is up,
 * and the async continuation opens the microphone anyway: `recording` flips
 * back to `true` with no press behind it and no further UI action guaranteed
 * to close it. That is trap two — "a stopped MediaRecorder does not release
 * the microphone" — wearing a different hat: here nothing was ever stopped,
 * because nothing had started yet, and the mic still ends up open. */
it("releases the microphone when the button comes up before permission resolves", async () => {
  let resolvePermission: ((stream: { getTracks: () => typeof tracks }) => void) | null = null;
  const pending = new Promise<{ getTracks: () => typeof tracks }>((resolve) => {
    resolvePermission = resolve;
  });
  vi.stubGlobal("navigator", {
    ...navigator,
    mediaDevices: { getUserMedia: vi.fn(() => pending) },
  });
  render(<Harness onText={vi.fn()} />);
  const btn = screen.getByText("talk");

  // The finger goes down and comes back up entirely before the permission
  // prompt has been answered — `getUserMedia`'s promise is still pending.
  await act(async () => { fireEvent.pointerDown(btn); });
  await act(async () => { fireEvent.pointerUp(btn); });
  expect(screen.getByTestId("rec").textContent).toBe("off");

  // The prompt resolves late, after the press is long over.
  await act(async () => {
    resolvePermission?.({ getTracks: () => tracks });
    await pending;
  });

  expect(tracks[0].stop).toHaveBeenCalled();
  expect(screen.getByTestId("rec").textContent).toBe("off");
  expect(recorder.start).not.toHaveBeenCalled();
});

it("releases the microphone when the recorder can't be constructed", async () => {
  const throwing = vi.fn(function MediaRecorderMock(): MediaRecorder {
    throw new Error("no supported codec");
  });
  Object.assign(throwing, { isTypeSupported: () => true });
  vi.stubGlobal("MediaRecorder", throwing);

  render(<Harness onText={vi.fn()} />);
  const btn = screen.getByText("talk");

  await act(async () => { fireEvent.pointerDown(btn); });
  await act(async () => { fireEvent.pointerUp(btn); });

  expect(tracks[0].stop).toHaveBeenCalled();
  expect(screen.getByTestId("err").textContent).not.toBe("");
  expect(screen.getByTestId("rec").textContent).toBe("off");
});

/* Fix round 2, Important 1. `new MediaRecorder(...)` got a try/catch in round
 * 1; `rec.start()`, one line below it, did not. By the time it runs, the
 * stream and recorder are already ours and `setRecording(true)` has not run
 * yet — the review's probe found `recording: "off"`, `error: ""`, and the
 * stream still held, i.e. the module's OWN "reports a denied microphone
 * instead of failing silently" promise broken by the very same module, one
 * call further down. Not exotic: a USB mic unplugged between `getUserMedia`
 * and `start`, or a codec `isTypeSupported` accepted and the recorder then
 * refuses. */
it("releases the microphone when starting the recorder throws", async () => {
  recorder.start = vi.fn(() => { throw new Error("device disappeared"); });

  render(<Harness onText={vi.fn()} />);
  const btn = screen.getByText("talk");

  await act(async () => { fireEvent.pointerDown(btn); });

  expect(tracks[0].stop).toHaveBeenCalled();
  expect(screen.getByTestId("err").textContent).not.toBe("");
  expect(screen.getByTestId("rec").textContent).toBe("off");
});

/* Fix round 2, Important 2. There was no cleanup effect at all: a component
 * that unmounts while `recording` is true — the chat panel closing under a
 * held button (Task 6), the wallboard route changing (Task 8) — leaves the
 * stream referenced only by refs nobody reads again. No user action and no
 * code path is left to call `stop()`; the browser's recording indicator
 * stays lit for the life of the tab. */
it("releases the microphone when the component unmounts mid-recording", async () => {
  const { unmount } = render(<Harness onText={vi.fn()} />);
  const btn = screen.getByText("talk");

  await act(async () => { fireEvent.pointerDown(btn); });
  expect(screen.getByTestId("rec").textContent).toBe("on");

  await act(async () => { unmount(); });

  expect(tracks[0].stop).toHaveBeenCalled();
});

/* Fix round 3. Round 2's unmount fix covered "unmount while recording" --
 * this is the other unmount: press, the permission prompt blocks the await,
 * the component unmounts before it resolves. `release()` alone finds nothing
 * to release (`streamRef.current` is still null) and does not touch
 * `sessionRef`, so when the prompt finally resolves the session check still
 * matches and the continuation adopts the stream into a dead component's
 * refs -- exactly the pending-start race round 1 closed for the button-up
 * case, reopened here because unmount has no "finger" to bump the session
 * for it. */
it("releases the microphone when the component unmounts while permission is still pending", async () => {
  let resolvePermission!: (stream: { getTracks: () => typeof tracks }) => void;
  const pending = new Promise<{ getTracks: () => typeof tracks }>((resolve) => {
    resolvePermission = resolve;
  });
  vi.stubGlobal("navigator", {
    ...navigator,
    mediaDevices: { getUserMedia: vi.fn(() => pending) },
  });
  const { unmount } = render(<Harness onText={vi.fn()} />);
  const btn = screen.getByText("talk");

  // The prompt is still up when the component goes away -- no button-up,
  // no user action of any kind, just the panel closing under a held press.
  await act(async () => { fireEvent.pointerDown(btn); });
  await act(async () => { unmount(); });

  // The prompt resolves after the component is already gone.
  await act(async () => {
    resolvePermission({ getTracks: () => tracks });
    await pending;
  });

  expect(tracks[0].stop).toHaveBeenCalled();
  expect(recorder.start).not.toHaveBeenCalled();
});

it("releases directly when the recorder already stopped itself", async () => {
  render(<Harness onText={vi.fn()} />);
  const btn = screen.getByText("talk");

  await act(async () => { fireEvent.pointerDown(btn); });
  expect(screen.getByTestId("rec").textContent).toBe("on");

  // The browser can end a MediaRecorder on its own -- a track ending, the
  // tab losing the device -- before the user releases the button. `stop()`
  // must not call `.stop()` again on an already-inactive recorder; it
  // should release directly.
  recorder.state = "inactive";

  await act(async () => { fireEvent.pointerUp(btn); });

  expect(recorder.stop).not.toHaveBeenCalled();
  expect(tracks[0].stop).toHaveBeenCalled();
  expect(screen.getByTestId("rec").textContent).toBe("off");
});

/* Fix round 4. Round 2's unmount test is not this one: it never pushes a
 * chunk, so `onstop`'s `if (!blob.size) return` swallows the upload and the
 * leak stays invisible. Here the clip already has bytes in it when the panel
 * closes under a held button.
 *
 * The mechanism is the whole point. Nobody calls `stop()` on this path.
 * `release()` stops the tracks, an inactive stream makes a still-recording
 * `MediaRecorder` fire its own `stop`, and `release()` nulls `recRef.current`
 * without ever detaching `rec.onstop` — so the handler runs, with a full
 * clip, from a component that is already gone. Uploading it would send a
 * customer's audio out of the browser after the panel closed, which is the
 * opposite of what push-to-talk promises: the microphone indicator being lit
 * for exactly as long as the button is down is the only honest signal a
 * customer can check, and an upload after the fact is not covered by it. */
it("sends nothing when the component unmounts with a clip already recorded", async () => {
  const onText = vi.fn();
  const { unmount } = render(<Harness onText={onText} />);

  await act(async () => { fireEvent.pointerDown(screen.getByText("talk")); });
  await act(async () => {
    recorder.ondataavailable?.({ data: new Blob([new Uint8Array([1, 2, 3])], { type: "audio/webm" }) });
  });

  // The panel closes mid-press. No button-up, no `stop()`.
  await act(async () => { unmount(); });
  // `onstop`'s body is an async IIFE; let any scheduled continuation run.
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });

  const transcribes = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls
    .filter(([url]) => String(url).includes("/voice/transcribe"));
  expect(transcribes).toHaveLength(0);
  expect(onText).not.toHaveBeenCalled();
  expect(tracks.every((t) => t.stop.mock.calls.length > 0)).toBe(true);
});
