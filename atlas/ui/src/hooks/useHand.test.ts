import { describe, expect, it, vi } from "vitest";
import { cameraLentTo, lendCamera, onCameraLent, requestHand, toggleHandTracking } from "./useHand";

// Air Glass borrows the camera. While it is lent, a start (pinch-to-wake's
// `requestHand`) must WAIT, not fail: a rejection would make useVoice fall
// back to click-to-talk for good. Giving it back lets the wait go on to a
// real start (which, with no camera in this environment, then fails -- the
// point is that it failed only after the camera came back).
describe("lendCamera", () => {
  it("makes a start wait while lent, and resumes it on return", async () => {
    vi.stubGlobal("cancelAnimationFrame", () => undefined);
    const seen: Array<string | null> = [];
    const off = onCameraLent((owner) => seen.push(owner));
    const giveBack = lendCamera("airglass");
    expect(cameraLentTo()).toBe("airglass");

    let settled: "pending" | "resolved" | "rejected" = "pending";
    requestHand().then(() => { settled = "resolved"; }, () => { settled = "rejected"; });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(settled).toBe("pending");

    giveBack();
    giveBack();          // a second call is a no-op
    expect(cameraLentTo()).toBeNull();
    // The real start now runs (it loads MediaPipe, then fails for want of a
    // camera here): wait for it to settle rather than guess how long.
    await vi.waitFor(() => expect(settled).not.toBe("pending"), { timeout: 5000 });
    expect(seen).toEqual(["airglass", null]);
    off();
    vi.unstubAllGlobals();
  });
});

// `toggleHandTracking`'s off -> on branch used to be `void ensureStarted();`
// -- a denied camera or a missing model file on re-enable rejected with no
// catch, which is an unhandled rejection, not a message a user ever sees.
// This test environment has no `navigator`, no `getUserMedia`, no
// `cancelAnimationFrame` -- `ensureStarted` fails here for a different
// reason than a real denied-camera would, but it fails the same way: a
// rejected promise, with nothing awaiting it. Vitest fails the whole run
// (non-zero exit, an "Unhandled Rejection" reported separately from the
// test list) the moment ANY promise rejects with no handler anywhere in the
// process, whether or not the test that caused it makes an assertion about
// it -- which is exactly the failure mode this guards against, and why this
// test needs no mock of the camera or the model to prove the fix.
describe("toggleHandTracking", () => {
  it("does not leave an unhandled rejection when re-enabling fails", async () => {
    toggleHandTracking(); // off -> on: nothing in this environment can open a camera.
    // `ensureStarted`'s rejection resolves on a microtask; give it a real
    // task-queue turn so a missing `.catch` would have already surfaced.
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(true).toBe(true);
  });
});
