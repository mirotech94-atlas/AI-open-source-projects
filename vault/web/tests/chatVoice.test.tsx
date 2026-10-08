/* The microphone in the composer.
 *
 * The important assertion is the boring one: a transcript lands in the
 * textarea and is NOT sent. Voice must be exactly as powerful as the
 * keyboard, and a transcript that auto-sends is one step more — see
 * hub/tests/test_voice_authority.py. */
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { Composer } from "../src/shell/chat/Composer";

// The mock's `stop` mirrors one property of the real `useDictation.stop()`
// that the pointer-capture tests below depend on: it is idempotent. A real
// press only ever produces one transcript no matter how many times `stop()`
// fires afterwards (speech.ts: `if (!rec) { release(); return; }` once a
// press has already been torn down) — a naive mock that called `onText` on
// every `stop()` would double-append when a test exercises the real release
// order (pointerup, then the pointerleave that follows lostpointercapture),
// which is a mock bug, not a Composer bug. `armed` lives in the factory's own
// closure rather than per-hook-call, so it survives across a render (the
// `onText` call triggers `setValue`, which re-renders and calls this factory
// again) but each test still starts clean, because every test that presses
// the button always pairs a `start()` with an eventual `stop()` and leaves
// `armed` false behind it.
vi.mock("../src/lib/speech", () => {
  let armed = false;
  return {
    useDictation: ({ onText }: { onText: (t: string) => void }) => ({
      recording: false,
      error: "",
      start: () => { armed = true; },
      stop: () => {
        if (!armed) return;
        armed = false;
        onText("what is 192.168.1.5");
      },
    }),
    speak: vi.fn(async () => true),
    enableAudio: vi.fn(() => true),
    audioReady: vi.fn(() => true),
    stopSpeaking: vi.fn(),
  };
});

// jsdom 29.x implements none of setPointerCapture/releasePointerCapture/
// hasPointerCapture on Element.prototype (confirmed directly, not assumed).
// Composer's onPointerDown already tolerates that with a try/catch — the
// point of stubbing it here is the opposite of relying on the try/catch: an
// unstubbed call would throw, get silently swallowed, and a future edit that
// deleted the `setPointerCapture` call entirely would look identical to one
// that kept it. The stub turns "was it actually called" into an assertion
// instead of an assumption.
beforeEach(() => {
  Element.prototype.setPointerCapture = vi.fn();
  Element.prototype.releasePointerCapture = vi.fn();
});

afterEach(() => {
  // @ts-expect-error -- removing the stub restores jsdom's real absence of
  // the method rather than leaving a vi.fn() with accumulated call history
  // sitting on the shared prototype for whatever test runs next in this file.
  delete Element.prototype.setPointerCapture;
  // @ts-expect-error -- see above.
  delete Element.prototype.releasePointerCapture;
});

const base = { disabled: false, chips: [], placeholder: "Ask Vault" };

it("shows no microphone when the hub cannot hear", () => {
  render(<Composer {...base} voice={false} onSend={vi.fn()} />);
  expect(screen.queryByRole("button", { name: /hold to talk/i })).toBeNull();
});

it("offers a microphone when the hub can hear", () => {
  render(<Composer {...base} voice onSend={vi.fn()} />);
  expect(screen.getByRole("button", { name: /hold to talk/i })).toBeTruthy();
});

// `dictation.test.tsx` already found that `userEvent.pointer` doesn't
// reliably construct `PointerEvent`s against this project's jsdom/vitest
// pairing (the failure shows up as the handler simply never firing, not as a
// thrown error). `fireEvent.pointerDown`/`pointerUp` dispatch the same DOM
// events straight at the button's own handlers with no dependency on
// user-event's pointer gesture engine — see that file's header for the fuller
// account.
it("puts the transcript in the box and does not send it", async () => {
  const onSend = vi.fn();
  render(<Composer {...base} voice onSend={onSend} />);
  const mic = screen.getByRole("button", { name: /hold to talk/i });

  fireEvent.pointerDown(mic);
  fireEvent.pointerUp(mic);

  expect((screen.getByRole("textbox") as HTMLTextAreaElement).value)
    .toBe("what is 192.168.1.5");
  expect(onSend).not.toHaveBeenCalled();
});

it("appends to what is already typed rather than replacing it", async () => {
  render(<Composer {...base} voice onSend={vi.fn()} />);
  const box = screen.getByRole("textbox");
  await userEvent.type(box, "and ");
  const mic = screen.getByRole("button", { name: /hold to talk/i });
  fireEvent.pointerDown(mic);
  fireEvent.pointerUp(mic);
  expect((box as HTMLTextAreaElement).value).toBe("and what is 192.168.1.5");
});

// What this test can and cannot prove: jsdom has no pointer-capture
// implementation at all (verified above, not assumed), so it cannot suppress
// `pointerleave` for a "captured" pointer the way a real browser does — a
// `fireEvent.pointerLeave` here always reaches the handler no matter what
// `setPointerCapture` was called with, because jsdom doesn't know capture
// happened. That makes "a drag off the button mid-press keeps recording"
// untestable as a behavioural assertion in this environment; asserting
// `stop` was NOT called after a simulated leave would only be proving that
// our test chose not to fire the event, not that the browser would have
// suppressed it. So this test asserts the one thing that *is* real here:
// the button actually calls `setPointerCapture` with the pressing pointer's
// id on pointerdown. That call is what makes the browser behaviour above
// possible; a future edit that dropped it would ship a mic that truncates
// the moment a thumb drifts, and nothing else in this suite would notice.
it("captures the pointer on press so the browser can keep tracking it off the button", () => {
  render(<Composer {...base} voice onSend={vi.fn()} />);
  const mic = screen.getByRole("button", { name: /hold to talk/i });

  fireEvent.pointerDown(mic, { pointerId: 7 });

  expect(mic.setPointerCapture).toHaveBeenCalledWith(7);
});

it("still stops on release, and the pointerleave that follows capture loss is harmless", () => {
  const onSend = vi.fn();
  render(<Composer {...base} voice onSend={onSend} />);
  const mic = screen.getByRole("button", { name: /hold to talk/i });

  fireEvent.pointerDown(mic, { pointerId: 7 });
  fireEvent.pointerUp(mic, { pointerId: 7 });
  // The real release order is pointerup -> lostpointercapture ->
  // pointerout/pointerleave (spec order; this file does not invent it), so
  // the button's own onPointerLeave fires right after a plain release too,
  // not only when a drag actually left the element. `stop()` runs a second
  // time here on purpose, the same way it would in a browser.
  fireEvent.pointerLeave(mic);

  expect((screen.getByRole("textbox") as HTMLTextAreaElement).value)
    .toBe("what is 192.168.1.5");
  expect(onSend).not.toHaveBeenCalled();
});
