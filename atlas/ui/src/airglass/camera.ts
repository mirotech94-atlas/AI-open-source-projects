// The camera and the hand model for Air Glass.
//
// The model and its wasm are the same same-origin files pinch-to-wake uses
// (`hooks/handModel.ts`), so the page runs under the dashboard's CSP with no
// CDN. The camera is borrowed from the shared hand controller
// (`useHand.ts::lendCamera`) for as long as the page is open: one camera
// light, one reader. Nothing here draws a frame anywhere but the page's own
// <video>, and no frame or landmark is sent anywhere.
import { createHandLandmarker, HAND_MODEL_PATH, type Landmarker } from "../hooks/handModel";
import { lendCamera } from "../hooks/useHand";

export type ModelStatus = "loading" | "ready" | "missing" | "failed";
export type CameraStatus = "off" | "requesting" | "on" | "unavailable";

export class Camera {
  landmarker: Landmarker | null = null;
  delegate: "GPU" | "CPU" | null = null;
  model: ModelStatus = "loading";
  camera: CameraStatus = "off";
  cameraError: string | null = null;
  private stream: MediaStream | null = null;
  private giveBack: (() => void) | null = null;
  private closed = false;
  private modelPromise: Promise<void> | null = null;

  constructor(private readonly video: HTMLVideoElement,
              private readonly onChange: () => void) {
    // The loan is taken as soon as the page opens, whatever it then does
    // (camera, demo, buttons only): pinch-to-wake's stream is stopped for as
    // long as the page is open, which is what the page and the hand badge
    // say. close() gives it back.
    this.giveBack = lendCamera("airglass");
  }

  /** Load the hand model. Independent of the camera, so a page with no
   * camera (or the demo) still proves the model loads under the CSP. */
  loadModel(): Promise<void> {
    this.modelPromise ??= (async () => {
      this.model = "loading";
      this.onChange();
      try {
        // A missing file is a build without `fetch-hand-model.sh`, not a
        // broken camera: say which.
        const head = await fetch(HAND_MODEL_PATH, { method: "HEAD", cache: "no-store" });
        // A dev server answers an unknown path with index.html and a 200.
        if (!head.ok || (head.headers.get("content-type") ?? "").includes("text/html")) {
          this.model = "missing";
          this.onChange();
          return;
        }
        const opts = { numHands: 2, minHandDetectionConfidence: 0.6,
          minHandPresenceConfidence: 0.5, minTrackingConfidence: 0.5 };
        let made: Landmarker;
        try {
          made = await createHandLandmarker({ ...opts, delegate: "GPU" });
          this.delegate = "GPU";
        } catch {
          made = await createHandLandmarker({ ...opts, delegate: "CPU" });
          this.delegate = "CPU";
        }
        if (this.closed) { made.close(); return; }
        this.landmarker = made;
        this.model = "ready";
      } catch (e) {
        console.warn("air glass: hand model unavailable:", e);
        this.model = "failed";
      }
      this.onChange();
    })();
    return this.modelPromise;
  }

  /** Take the camera from the shared controller and open it. */
  async open(): Promise<boolean> {
    if (this.closed) return false;
    this.stopTracks();
    this.camera = "requesting";
    this.cameraError = null;
    this.onChange();
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error("no mediaDevices");
      const s = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "user", width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      });
      // Closed while the permission prompt was up (or StrictMode's first
      // mount went away): let go now, or the camera light stays on.
      if (this.closed) { s.getTracks().forEach((t) => t.stop()); return false; }
      this.stream = s;
      this.video.srcObject = s;
      await this.video.play().catch(() => undefined);
      this.camera = "on";
      this.onChange();
      return true;
    } catch (e) {
      this.camera = "unavailable";
      this.cameraError = (e as { name?: string })?.name || String(e);
      this.onChange();
      return false;
    }
  }

  /** Stop the camera but keep the loan (demo mode while the page is open). */
  stopTracks(): void {
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.video.srcObject = null;
    if (this.camera === "on") this.camera = "off";
  }

  get live(): boolean { return this.stream !== null; }

  /** Everything off, and the camera handed back to pinch-to-wake. */
  close(): void {
    this.closed = true;
    this.stopTracks();
    this.camera = "off";
    try { this.landmarker?.close(); } catch { /* already closed */ }
    this.landmarker = null;
    this.giveBack?.();
    this.giveBack = null;
  }
}
