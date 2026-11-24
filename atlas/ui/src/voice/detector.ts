/**
 * Wake-word detection — in the browser, always.
 *
 * The promise that makes an always-on microphone acceptable is that nothing
 * leaves the machine until the phrase fires: no transcription, no upload, and
 * no buffer kept longer than the detector's own rolling window. That promise
 * only holds if detection happens here, so this interface exists to make a
 * server-side detector impossible to add by accident.
 *
 * Detectors, in descending order of how well they keep that promise:
 *
 *   porcupine    On-device, nothing leaves the machine. Needs a Picovoice
 *                access key AND a keyword file trained for "atlas" in the
 *                Picovoice console. Both belong to Miro's account.
 *
 *   local-whisper  On-device speech, no cloud key. whisper-tiny.en runs in
 *                a Web Worker in this tab (`localWake.ts`), reading two-second
 *                windows of the microphone at 16 kHz with a one-second hop
 *                and answering only "was 'atlas' said". The model is served
 *                from ATLAS's own origin (`ui/public/wake/whisper/`, fetched
 *                by `ui/scripts/fetch-whisper-model.sh`) and the ONNX
 *                runtime from the bundle's `/assets/`; nothing is uploaded
 *                until the conversation loop is open. Opt-in
 *                (`capabilities.local_wake`: VOICE_LOCAL_WAKE and the files).
 *
 *   pinch        On-device hand tracking (MediaPipe HandLandmarker), nothing
 *                leaves the machine either — frames stay in a `<video>`
 *                element and nothing is uploaded. It listens for a GESTURE,
 *                not a phrase, which is why `listensForPhrase` is false and
 *                `listensForGesture` is true: a caption that said "say
 *                atlas" for this detector would be a lie. Opt-in because it
 *                asks for the camera (`capabilities.pinch_wake`). The camera
 *                and the landmarker themselves live in `useHand.ts` (Task
 *                26) — this detector only subscribes to that shared stream
 *                and reacts to its `wake` events, so a page running the
 *                gesture cursor at the same time is reading the same one
 *                hand, not opening a second camera.
 *
 *   speech-api   The browser's own SpeechRecognition, listening continuously
 *                and matching "atlas" in what comes back. Works today with no
 *                key — but in Chrome this STREAMS AUDIO TO GOOGLE, which is a
 *                different privacy posture from the paragraph above, and the
 *                opposite of what Need.PRIVATE and the local Ollama are for.
 *                It is therefore opt-in and reports itself as what it is.
 *
 *   click-to-talk  No phrase detection, no gesture detection. The honest
 *                  fallback: it starts nothing and holds nothing.
 *
 * NOTE, 2026-09-22: `ui/public/wake/` does not exist and no Picovoice key is
 * set, so `createDetector` has always returned ClickToTalk. The wake word has
 * never fired. That reads in the UI as "the wake word is off" rather than
 * "it was never built", which is why this comment is here.
 */
// The contract, the honest fallback, and the start()-failure mapping live in
// detectorCore.ts, which has no dynamic import of the browser-only packages
// below -- see that file's header for why that separation exists.
export type { Detector, DetectorKind } from "./detectorCore";
export { createClickToTalk, fallbackAfterStartFailure } from "./detectorCore";
import { createClickToTalk, withClap, type Detector } from "./detectorCore";
import { isWakePhrase } from "./wakePhrase";

/**
 * Loaded only when a key exists, and only on demand — the bundle must cost
 * nothing on pages that are being read rather than talked to.
 */
async function loadPorcupine(accessKey: string): Promise<Detector | null> {
  try {
    // Dynamic, so Vite splits it into its own chunk: the wake detector must
    // cost nothing on a page that is only being read.
    const [{ PorcupineWorker }, { WebVoiceProcessor }] = await Promise.all([
      import("@picovoice/porcupine-web"),
      import("@picovoice/web-voice-processor"),
    ]);

    let worker: Awaited<ReturnType<typeof PorcupineWorker.create>> | null = null;

    return {
      kind: "porcupine",
      listensForPhrase: true,
      listensForGesture: false,
      clap: false,
      async start(onWake: () => void): Promise<void> {
        worker = await PorcupineWorker.create(
          accessKey,
          // Trained in the Picovoice console and placed here. Two spellings,
          // because "hey atlas" and a bare "atlas" are both meant to wake him.
          [{ publicPath: "/wake/atlas.ppn", label: "atlas" }],
          () => onWake(),
          { publicPath: "/wake/porcupine_params.pv" },
        );
        await WebVoiceProcessor.subscribe(worker);
      },
      async stop(): Promise<void> {
        if (!worker) return;
        await WebVoiceProcessor.unsubscribe(worker);
        worker.release();
        worker = null;
      },
    };
  } catch (error) {
    // A missing package or a missing .ppn is a configuration problem, not a
    // reason to pretend the detector is running.
    console.warn("wake-word detector unavailable:", error);
    return null;
  }
}

/**
 * The browser's own recogniser, used only as a phrase detector.
 *
 * It runs continuously and we look for the wake word in what it returns. The
 * words it produces are thrown away: the actual question is recorded and
 * transcribed by the normal path, so this never becomes a second, quieter
 * route for speech to leave the machine.
 */
function loadSpeechApi(): Detector | null {
  const Recognition =
    (window as unknown as { SpeechRecognition?: new () => SpeechRecognitionLike })
      .SpeechRecognition ??
    (window as unknown as { webkitSpeechRecognition?: new () => SpeechRecognitionLike })
      .webkitSpeechRecognition;
  if (!Recognition) return null;

  let recogniser: SpeechRecognitionLike | null = null;
  let wanted = false;

  return {
    kind: "speech-api",
    listensForPhrase: true,
    listensForGesture: false,
    clap: false,
    async start(onWake: () => void): Promise<void> {
      wanted = true;
      const engine = new Recognition();
      engine.continuous = true;
      engine.interimResults = true;
      engine.lang = navigator.language || "en-US";

      engine.onresult = (event: SpeechRecognitionEventLike): void => {
        for (let i = event.resultIndex; i < event.results.length; i += 1) {
          const result = event.results[i];
          const heard = result?.[0]?.transcript ?? "";
          // "Atlas" and "Hey Atlas" both wake him (`wakePhrase.ts`, the same
          // rule as the on-device gate) -- but not "the atlas report", nor the
          // interim "atlas" that "atlas report" begins as.
          if (isWakePhrase(heard, result?.isFinal ?? true)) {
            onWake();
            return;
          }
        }
      };
      // Chrome ends the session on its own after a stretch of quiet. Restart
      // it, or the wake word stops working after a few minutes with no sign
      // that anything changed.
      engine.onend = (): void => {
        if (wanted) {
          try {
            engine.start();
          } catch {
            /* already starting */
          }
        }
      };
      engine.onerror = (event: { error?: string }): void => {
        // "no-speech" and "aborted" are routine; a permission error is not.
        if (event.error === "not-allowed" || event.error === "service-not-allowed") {
          wanted = false;
        }
      };
      recogniser = engine;
      engine.start();
    },
    async stop(): Promise<void> {
      wanted = false;
      recogniser?.abort();
      recogniser = null;
    },
  };
}

interface SpeechRecognitionLike {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onend: (() => void) | null;
  onerror: ((event: { error?: string }) => void) | null;
  start(): void;
  abort(): void;
}

interface SpeechRecognitionEventLike {
  resultIndex: number;
  results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal?: boolean }>;
}

/**
 * On-device hand tracking, used only as a gesture detector.
 *
 * Task 16 opened its own camera and ran its own HandLandmarker loop here.
 * Task 26 added a second consumer of the same hand -- the gesture cursor and
 * Mission Control's drag -- and two independent MediaPipe pipelines reading
 * two independent `getUserMedia` streams is exactly the kind of thing
 * CLAUDE.md's "collectors only read" spirit would flag if a second camera
 * light were a second write: one hand, one camera, one landmarker. So the
 * camera and the loop moved to `useHand.ts`'s shared, refcounted controller,
 * and this detector is now a thin subscriber to it: `start()` registers a
 * listener for `wake` events (`useHand`'s `GestureStream` already reuses
 * Task 16's exact `PinchGate` debounce for that), and `stop()` unsubscribes.
 * If nothing else on the page wants the hand, the shared controller closes
 * the camera the same way this detector always did on its own.
 */
async function loadPinch(): Promise<Detector | null> {
  try {
    // Dynamic, so a page that is only being read never pays for the vision
    // model or its wasm runtime -- this import exists only to confirm the
    // package resolves before offering "pinch" as a detector at all.
    await import("@mediapipe/tasks-vision");
  } catch (error) {
    // No camera, no model file, or the package failed to load -- a
    // configuration problem, not a reason to pretend the detector is running.
    console.warn("pinch-wake detector unavailable:", error);
    return null;
  }

  let unsubscribe: (() => void) | null = null;

  return {
    kind: "pinch",
    listensForPhrase: false,
    listensForGesture: true,
    clap: false,
    async start(onWake: () => void): Promise<void> {
      const { subscribeHand, requestHand } = await import("../hooks/useHand");
      unsubscribe = subscribeHand({
        onEvent(event) {
          if (event.kind === "wake") onWake();
        },
      });
      try {
        // Waits for the shared camera to actually come up (or reuses it if
        // another consumer already opened it) so a denied permission or a
        // missing model file surfaces here as a real rejection, the same as
        // it did before the camera moved into `useHand`'s shared controller.
        await requestHand();
      } catch (error) {
        unsubscribe();
        unsubscribe = null;
        throw error;
      }
    },
    async stop(): Promise<void> {
      unsubscribe?.();
      unsubscribe = null;
    },
  };
}

/**
 * whisper-tiny.en as a wake-word detector.
 *
 * Opens its own microphone stream (the app's `Mic` is for the waveform and
 * the recording), feeds a rolling two-second window to the worker every
 * second, and fires `onWake` when the window contains "atlas". Its `stop()`
 * stops those tracks and closes its audio context, so a muted ATLAS holds
 * no stream at all -- the badge reading "Mic off" while this held one would
 * be the lie `mic.ts` exists to prevent.
 */
async function loadLocalWhisper(): Promise<Detector | null> {
  const { loadWhisperGate, WakeWindow } = await import("./localWake");
  const gate = await loadWhisperGate("/wake/whisper/");
  if (!gate) return null;

  let stream: MediaStream | null = null;
  let context: AudioContext | null = null;
  let node: ScriptProcessorNode | null = null;
  let rolling: InstanceType<typeof WakeWindow> | null = null;
  let busy = false;
  let running = false;

  const release = (): void => {
    running = false;
    node?.disconnect();
    node = null;
    stream?.getTracks().forEach((track) => track.stop());
    stream = null;
    void context?.close();
    context = null;
    rolling?.clear();
    rolling = null;
  };

  return {
    kind: "local-whisper",
    listensForPhrase: true,
    listensForGesture: false,
    clap: false,
    async start(onWake: () => void,
                onFailure?: (err: unknown) => void): Promise<void> {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      context = new AudioContext();
      const source = context.createMediaStreamSource(stream);
      // ScriptProcessor rather than an AudioWorklet: one small node, no
      // second module file to serve, and 4096 samples (~85 ms) of latency
      // is nothing next to a one-second hop.
      node = context.createScriptProcessor(4096, 1, 1);
      const buffer = new WakeWindow(context.sampleRate);
      rolling = buffer;
      const rate = context.sampleRate;
      running = true;
      node.onaudioprocess = (event: AudioProcessingEvent): void => {
        if (!running) return;
        const chunk = buffer.push(new Float32Array(event.inputBuffer.getChannelData(0)));
        // One look at a time. A window that arrives while the last is still
        // being read is dropped, not queued: a queue would fall behind the
        // room, and the next hop covers the same second anyway.
        if (!chunk || busy) return;
        busy = true;
        void gate.push(chunk, rate).then((heard) => {
          busy = false;
          if (heard && running) {
            // Forget the window that held the word, so one "atlas" cannot
            // wake him twice on the next hop.
            buffer.clear();
            onWake();
          }
        }, (err: unknown) => {
          // The gate stopped for good (the worker died, or keeps failing).
          // Release the mic and say so -- a stopped gate that still read as
          // "heard on this machine" would be a detector that is not there.
          busy = false;
          if (!running) return;
          release();
          onFailure?.(err);
        });
      };
      source.connect(node);
      // A ScriptProcessor only runs while connected to an output. It writes
      // nothing into its output buffer, so this plays silence.
      node.connect(context.destination);
    },
    async stop(): Promise<void> {
      release();
      gate.close();
    },
  };
}

/**
 * Opens the microphone for clap-to-wake alone (Task 47 / G4) -- its own
 * stream, separate from whichever one porcupine, local-whisper or the app's
 * own `Mic` hold. `clap.ts`'s `ClapGate` reasons about a real transient
 * decaying within tens of milliseconds, which noise suppression exists to
 * flatten, so this stream asks for none of the shaping the app's voice mic
 * wants. `ClapGate.push` is cheap pure arithmetic on one small buffer, so it
 * runs directly in the audio callback -- no worker, unlike whisper's model.
 * Resolves the function that releases it, and throws (never resolves to a
 * detector that loads and never listens) if the microphone cannot be opened.
 */
async function openClapStream(onWake: () => void): Promise<() => void> {
  const { ClapGate } = await import("./clap");
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
  });
  const context = new AudioContext();
  const source = context.createMediaStreamSource(stream);
  const node = context.createScriptProcessor(1024, 1, 1);
  const gate = new ClapGate();
  node.onaudioprocess = (event: AudioProcessingEvent): void => {
    const atMs = context.currentTime * 1000;
    if (gate.push(event.inputBuffer.getChannelData(0), atMs)) onWake();
  };
  source.connect(node);
  // Same reason as local-whisper's node: a ScriptProcessor only runs while
  // connected to an output, and this one writes nothing to it.
  node.connect(context.destination);
  return (): void => {
    node.disconnect();
    node.onaudioprocess = null;
    stream.getTracks().forEach((track) => track.stop());
    void context.close();
  };
}

/** Binds `withClap` (the pure composition, in `detectorCore.ts` so it can be
 *  unit-tested with a fake stream-opener) to the real, browser-only
 *  microphone tap above. */
function withRealClap(inner: Detector): Detector {
  return withClap(inner, openClapStream);
}

export async function createDetector(
  accessKey: string | null,
  allowSpeechApi = false,
  pinchWake = false,
  localWake = false,
  clapWake = false,
): Promise<Detector> {
  // On-device first, always. It is the only one that keeps the promise in
  // the header of this file.
  if (accessKey) {
    const porcupine = await loadPorcupine(accessKey);
    if (porcupine) return clapWake ? withRealClap(porcupine) : porcupine;
  }
  // On-device speech: the same promise as Porcupine, with no account. Only
  // when the server says the model files are actually in place.
  if (localWake) {
    const whisper = await loadLocalWhisper();
    if (whisper) return clapWake ? withRealClap(whisper) : whisper;
  }
  // Opt-in: pinch asks for the camera, so it only runs when the server says
  // the model file is actually in place (see atlas/api/voice.py).
  if (pinchWake) {
    const pinch = await loadPinch();
    if (pinch) return clapWake ? withRealClap(pinch) : pinch;
  }
  // Only with an explicit opt-in, because this one sends audio to Google.
  // Clap never rides this one: it would say nothing useful about a path
  // that already leaves the machine.
  if (allowSpeechApi) {
    const speech = loadSpeechApi();
    if (speech) return speech;
  }
  return createClickToTalk();
}
