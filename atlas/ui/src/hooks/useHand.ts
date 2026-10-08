import { useEffect, useRef, useState } from "react";
import { classify, GestureStream, type GestureEvent } from "../voice/gestures";
import { createHandLandmarker, type Landmarker } from "./handModel";

/**
 * The camera, opened once. Shared by pinch-to-wake and the gesture stream so
 * there is ONE camera stream and ONE landmarker, whatever combination of
 * `useHand()` calls and `detector.ts`'s pinch detector happen to be active
 * at once. Nothing here is drawn to a canvas, appended to the document, or
 * sent anywhere -- the same "frames never leave the tab" rule pinch-to-wake
 * (Task 16) already followed on its own.
 *
 * `off` / `watching` / `cursor` / `grab` describes the DEVICE, never an
 * intention: `watching` means the camera is genuinely open and a frame is
 * genuinely being read, which is also the camera-on indicator Task 16's
 * review asked for -- the hand badge that reports this state is it.
 */
export type HandState = "off" | "watching" | "cursor" | "grab";

export interface HandListener {
  onState?: (state: HandState) => void;
  onCursor?: (cursor: { x: number; y: number } | null) => void;
  onEvent?: (event: GestureEvent) => void;
}

// At most 15 detections a second -- the same ceiling pinch-to-wake used, for
// the same reason: a hand cannot move fast enough to need more, and the
// model is not free to run on a laptop that is otherwise idle.
const HAND_MAX_FPS = 15;
const HAND_MIN_FRAME_MS = 1000 / HAND_MAX_FPS;

// The middle-finger MCP joint -- roughly the centre of the palm, and stable
// whether the hand is open, fisted, or anywhere between, unlike a fingertip.
const PALM_CENTER = 9;

const listeners = new Set<HandListener>();
let stream: MediaStream | null = null;
let video: HTMLVideoElement | null = null;
let landmarker: Landmarker | null = null;
let gestures = new GestureStream();
let raf = 0;
let lastFrame = 0;
let starting: Promise<void> | null = null;
let manuallyStopped = false;
let currentState: HandState = "off";
let currentCursor: { x: number; y: number } | null = null;

// The camera lent to a page that needs it whole (Air Glass: two hands, a
// visible picture, a higher resolution). Separate from `manuallyStopped` on
// purpose: lending is the page's business and must never overwrite his own
// off switch. While lent, starting WAITS rather than failing -- the pinch
// detector's `start()` rejecting would make `useVoice` fall back to
// click-to-talk for good, and pinch-to-wake would not come back when the
// page closes. `generation` lets a start that was already in flight notice
// it was overtaken and let go of whatever it opened.
let lentTo: string | null = null;
let generation = 0;
const waitingForReturn: Array<{ resolve: () => void; reject: (e: unknown) => void }> = [];
const lendListeners = new Set<(owner: string | null) => void>();
const LENT = new Error("camera lent");

function waitForReturn(): Promise<void> {
  return new Promise((resolve, reject) => waitingForReturn.push({ resolve, reject }));
}

function setState(state: HandState): void {
  currentState = state;
  listeners.forEach((l) => l.onState?.(state));
}

function setCursor(cursor: { x: number; y: number } | null): void {
  currentCursor = cursor;
  listeners.forEach((l) => l.onCursor?.(cursor));
}

function emit(event: GestureEvent): void {
  listeners.forEach((l) => l.onEvent?.(event));
}

function teardown(): void {
  cancelAnimationFrame(raf);
  raf = 0;
  stream?.getTracks().forEach((track) => track.stop());
  stream = null;
  if (video) video.srcObject = null;
  video = null;
  landmarker?.close();
  landmarker = null;
  gestures = new GestureStream();
  setCursor(null);
  setState("off");
}

/** Prefers the Insta360 Link when it is plugged in, falls back to whatever
 * the browser considers the default camera otherwise. `enumerateDevices`
 * only returns real labels once permission has already been granted once in
 * this origin, which is fine -- the first-ever open just gets the default. */
async function preferredDeviceId(): Promise<string | undefined> {
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    const link = devices.find(
      (d) => d.kind === "videoinput" && /insta360\s*link/i.test(d.label),
    );
    return link?.deviceId;
  } catch {
    return undefined;
  }
}

async function ensureStarted(): Promise<void> {
  // A deliberate click on the hand badge means off, full stop -- including
  // for the pinch detector's own `requestHand`, which must see this as a
  // real failure to start rather than a silent no-op that would otherwise
  // leave it reporting "pinch" as the working wake detector.
  if (manuallyStopped) throw new Error("hand tracking turned off");
  // Lent to a page that needs the camera whole: wait for it to come back.
  if (lentTo) return waitForReturn();
  if (stream) return;
  if (starting) return starting;

  const gen = generation;
  const overtaken = (): boolean => gen !== generation;
  const attempt = (async () => {
    try {
      const made = await createHandLandmarker({ numHands: 1 });
      if (overtaken()) {
        made.close();
        throw LENT;
      }
      landmarker = made;

      const deviceId = await preferredDeviceId();
      const opened = await navigator.mediaDevices.getUserMedia({
        video: deviceId
          ? { deviceId: { exact: deviceId }, width: 320, height: 240 }
          : { width: 320, height: 240 },
      });
      // Lent while the permission prompt was up: let go of the tracks now,
      // or the camera light stays on for a stream nothing reads.
      if (overtaken()) {
        opened.getTracks().forEach((track) => track.stop());
        throw LENT;
      }
      stream = opened;
      video = document.createElement("video");
      video.muted = true;
      video.playsInline = true;
      video.srcObject = stream;
      await video.play();
      if (overtaken()) throw LENT;

      gestures = new GestureStream();
      setState("watching");

      const loop = (now: number): void => {
        raf = requestAnimationFrame(loop);
        if (!landmarker || !video || now - lastFrame < HAND_MIN_FRAME_MS) return;
        lastFrame = now;
        const result = landmarker.detectForVideo(video, now);
        const hand = result.landmarks[0];
        if (!hand) return;

        const gesture = classify(hand);
        const palm = hand[PALM_CENTER] ?? hand[0];
        if (!palm) return;
        // getUserMedia hands back the RAW frame, not the mirrored one a
        // person sees of themselves in a normal video call -- this
        // `<video>` is never appended to the page, so nothing ever mirrors
        // it for us. Left un-mirrored, moving a hand to the physical right
        // would move the on-screen cursor left, a fist would grab the tile
        // under the mirror image of where the hand actually is, and a
        // rightward swipe would report as "left". Flipping x here, once,
        // before it reaches classify's caller-agnostic x/y, is what makes
        // the cursor, the drag, and the swipe direction all agree with the
        // hand a person is actually watching move.
        const mirroredX = 1 - palm.x;
        const event = gestures.push(gesture, mirroredX, palm.y, now);
        if (!event) return;

        emit(event);
        if (event.kind === "grab_start") setState("grab");
        else if (event.kind === "grab_end") setState("cursor");
        else if (event.kind === "cursor") {
          setState(currentState === "grab" ? "grab" : "cursor");
          setCursor({ x: event.x, y: event.y });
        } else if (event.kind === "dismiss") {
          setState("watching");
          setCursor(null);
        }
      };
      raf = requestAnimationFrame(loop);
    } catch (error) {
      if (error === LENT) {
        // Whatever this attempt still holds belongs to an overtaken start.
        if (gen !== generation && (stream || landmarker)) teardown();
        throw LENT;
      }
      // No camera, no model file, or the package failed to load -- a
      // configuration problem, not a reason to pretend the hand is tracked.
      // Re-thrown (not just logged) so `requestHand` below can report a
      // genuine failure to start rather than reading as "on" forever.
      console.warn("hand tracking unavailable:", error);
      teardown();
      throw error;
    }
  })();
  starting = attempt
    .finally(() => {
      if (starting === current) starting = null;
    })
    .catch((error: unknown) => {
      // Overtaken by a lend: wait for the camera to come back (or, if it
      // already has, start again) instead of failing the caller.
      if (error !== LENT) throw error;
      return lentTo ? waitForReturn() : ensureStarted();
    });
  const current = starting;
  return current;
}

/**
 * Hands the camera to a page that needs it whole, and returns the function
 * that gives it back. Taking it stops the shared stream outright (the
 * camera light belongs to the page now); every subscriber stays registered
 * and simply sees `off`, so pinch-to-wake, the cursor and the swipes pause
 * rather than fail. Giving it back restarts the shared stream only if
 * someone still wants it and he has not switched hand tracking off himself.
 */
export function lendCamera(owner: string): () => void {
  lentTo = owner;
  generation += 1;
  teardown();
  lendListeners.forEach((l) => l(owner));
  let returned = false;
  return () => {
    if (returned || lentTo !== owner) return;
    returned = true;
    lentTo = null;
    lendListeners.forEach((l) => l(null));
    const pending = waitingForReturn.splice(0);
    if (manuallyStopped) {
      pending.forEach((w) => w.reject(new Error("hand tracking turned off")));
      return;
    }
    if (listeners.size === 0 && pending.length === 0) return;
    const restart = ensureStarted();
    restart.catch(() => { /* reflected in `state`, not thrown here */ });
    pending.forEach((w) => restart.then(w.resolve, w.reject));
  };
}

/** Who has the camera on loan, if anyone ("airglass"), for the hand badge. */
export function cameraLentTo(): string | null {
  return lentTo;
}

/** Called with the borrower's name when the camera is lent, null on return. */
export function onCameraLent(listener: (owner: string | null) => void): () => void {
  lendListeners.add(listener);
  return () => {
    lendListeners.delete(listener);
  };
}

/**
 * Registers a consumer with the shared camera, starting it on the first one
 * and stopping it when the last one leaves. Both `useHand` (React) and
 * `detector.ts`'s pinch detector (plain JS, invoked from `useVoice`'s
 * effect) subscribe through this -- neither owns a camera of its own.
 *
 * A failure to open is not thrown here: a passive subscriber (the cursor
 * overlay, a page's `useDraggableTiles`) never awaits this, so a rejection
 * would go unhandled. It learns of the failure the same way the mic badge
 * does -- `state` simply stays "off". A caller that needs to know whether
 * the camera actually came up (the pinch detector's `start()`, which must
 * report a real failure rather than silently read as "on") uses
 * `requestHand` instead.
 */
export function subscribeHand(listener: HandListener): () => void {
  listeners.add(listener);
  listener.onState?.(currentState);
  listener.onCursor?.(currentCursor);
  ensureStarted().catch(() => { /* reflected in `state`, not thrown here */ });
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) teardown();
  };
}

/**
 * Opens the shared camera and waits for it, rejecting if it never comes up.
 * Used by `detector.ts`'s pinch detector so a denied camera or a missing
 * model file still falls back to click-to-talk with an honest message, the
 * same as it did before the camera moved into this shared controller.
 */
export async function requestHand(): Promise<void> {
  await ensureStarted();
}

/** The hand badge's click handler: off means off, the same rule `Mic`
 * follows -- tracks stopped outright, not merely ignored, and it stays off
 * until clicked again even if a page that wants gestures is still mounted. */
export function toggleHandTracking(): void {
  if (currentState === "off") {
    manuallyStopped = false;
    // A denied camera or a missing model file on re-enable must not become
    // an unhandled rejection -- `ensureStarted` already reflects the
    // failure in `state` (via `teardown`, back to "off"), the same way a
    // passive `subscribeHand` caller learns of it.
    ensureStarted().catch(() => { /* reflected in `state`, not thrown here */ });
  } else {
    manuallyStopped = true;
    teardown();
  }
}

export interface UseHandResult {
  state: HandState;
  cursor: { x: number; y: number } | null;
}

/**
 * The React side of the shared camera above. Any number of components may
 * call this at once -- the sidebar's hand badge, the cursor overlay, a
 * page's `useDraggableTiles` -- and they all watch the same hand, because
 * `subscribeHand` is the only thing that ever opens one.
 */
export function useHand(enabled: boolean, onEvent?: (event: GestureEvent) => void): UseHandResult {
  const [state, setLocalState] = useState<HandState>(currentState);
  const [cursor, setLocalCursor] = useState<{ x: number; y: number } | null>(currentCursor);
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    if (!enabled) {
      setLocalState("off");
      setLocalCursor(null);
      return;
    }
    const unsubscribe = subscribeHand({
      onState: setLocalState,
      onCursor: setLocalCursor,
      onEvent: (event) => onEventRef.current?.(event),
    });
    return unsubscribe;
  }, [enabled]);

  return { state, cursor };
}
