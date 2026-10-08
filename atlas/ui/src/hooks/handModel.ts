/**
 * Where the on-device hand model lives, and the one function that loads it.
 *
 * Shared by `useHand.ts` (pinch-to-wake, the gesture cursor) and Air Glass
 * (`airglass/camera.ts`), so both load the same files from the same origin:
 * `ui/scripts/fetch-hand-model.sh` puts them in `ui/public/wake/`, Vite copies
 * them into `dist/wake/`, and `atlas/api/main.py` serves `/wake`. Nothing is
 * fetched from a CDN, which is what the dashboard's CSP (`script-src 'self'
 * 'wasm-unsafe-eval'; connect-src 'self'`) requires.
 */
export const HAND_WASM_PATH = "/wake/wasm";
export const HAND_MODEL_PATH = "/wake/hand_landmarker.task";

type HandLandmarkerCtor = typeof import("@mediapipe/tasks-vision").HandLandmarker;
export type Landmarker = Awaited<ReturnType<HandLandmarkerCtor["createFromOptions"]>>;

export interface HandModelOptions {
  numHands: number;
  /** Omitted: MediaPipe's own default, which is what pinch-to-wake always used. */
  delegate?: "GPU" | "CPU";
  minHandDetectionConfidence?: number;
  minHandPresenceConfidence?: number;
  minTrackingConfidence?: number;
}

/** A VIDEO-mode HandLandmarker from the same-origin files above. */
export async function createHandLandmarker(options: HandModelOptions): Promise<Landmarker> {
  const { FilesetResolver, HandLandmarker } = await import("@mediapipe/tasks-vision");
  const fileset = await FilesetResolver.forVisionTasks(HAND_WASM_PATH);
  const { delegate, numHands, ...confidence } = options;
  return HandLandmarker.createFromOptions(fileset, {
    baseOptions: delegate
      ? { modelAssetPath: HAND_MODEL_PATH, delegate }
      : { modelAssetPath: HAND_MODEL_PATH },
    runningMode: "VIDEO",
    numHands,
    ...confidence,
  });
}
