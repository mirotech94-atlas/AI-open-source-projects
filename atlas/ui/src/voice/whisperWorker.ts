/**
 * The wake model's own thread. Loads whisper-tiny.en from ATLAS's origin
 * and turns two-second windows into text, which the page only ever tests
 * for the word "atlas".
 *
 * Everything is local: `allowRemoteModels` is off, so a missing file is an
 * error rather than a quiet download from the Hugging Face hub, and the ONNX
 * runtime's wasm is pinned to the copy Vite bundles into `/assets/`, not the
 * CDN that transformers.js defaults to. The dashboard has no external origin, and the
 * wake detector least of all.
 */
import ortWasmUrl from "onnxruntime-web/ort-wasm-simd-threaded.asyncify.wasm?url";

type ToWorker =
  | { type: "load"; modelPath: string }
  | { type: "hear"; id: number; pcm: Float32Array };

type Transcriber = (pcm: Float32Array) => Promise<string>;

let transcribe: Transcriber | null = null;

async function load(modelPath: string): Promise<"webgpu" | "wasm"> {
  const { pipeline, env } = await import("@huggingface/transformers");
  env.allowRemoteModels = false;
  env.allowLocalModels = true;
  env.localModelPath = "/";
  const base = `/${modelPath.replace(/^\/+|\/+$/g, "")}`;
  // transformers.js points the ONNX runtime at cdn.jsdelivr.net whenever
  // nothing else is set. Set it, explicitly, to the copy Vite emitted into
  // /assets/ (the `?url` import above) -- an explicit local URL rather than
  // deleting the library's default, so no upgrade that sets the default
  // later can quietly bring the CDN back. The factory glue is embedded in
  // the bundled runtime, so the wasm binary is the only file it fetches.
  const onnx = env.backends.onnx as { wasm?: { wasmPaths?: unknown } };
  if (onnx.wasm) onnx.wasm.wasmPaths = { wasm: new URL(ortWasmUrl, self.location.href).href };
  const model = base.slice(1);

  // WebGPU when the browser has it, the full-precision encoder there; the
  // 8-bit build on wasm everywhere else. Both file sets are fetched by the
  // script, and a WebGPU failure falls back rather than giving up.
  const candidates: Array<{ device: "webgpu" | "wasm"; dtype: unknown }> = [];
  if ("gpu" in navigator) {
    candidates.push({
      device: "webgpu",
      dtype: { encoder_model: "fp32", decoder_model_merged: "q8" },
    });
  }
  candidates.push({ device: "wasm", dtype: "q8" });

  let lastError: unknown = null;
  for (const candidate of candidates) {
    try {
      // The options object is typed per-task in transformers.js; the fields
      // used here are the documented ones.
      const recogniser = (await pipeline("automatic-speech-recognition", model, {
        device: candidate.device,
        dtype: candidate.dtype,
      } as never)) as unknown as (
        audio: Float32Array,
      ) => Promise<{ text: string } | Array<{ text: string }>>;
      transcribe = async (pcm): Promise<string> => {
        const out = await recogniser(pcm);
        return Array.isArray(out) ? out.map((o) => o.text).join(" ") : out.text;
      };
      return candidate.device;
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError instanceof Error ? lastError : new Error(String(lastError));
}

self.onmessage = async (event: MessageEvent<ToWorker>): Promise<void> => {
  const message = event.data;
  if (message.type === "load") {
    try {
      const device = await load(message.modelPath);
      self.postMessage({ type: "ready", device });
    } catch (error) {
      self.postMessage({
        type: "error",
        reason: error instanceof Error ? error.message : String(error),
      });
    }
    return;
  }
  if (!transcribe) {
    self.postMessage({ type: "failed", id: message.id, reason: "model not loaded" });
    return;
  }
  try {
    self.postMessage({ type: "heard", id: message.id, text: await transcribe(message.pcm) });
  } catch (error) {
    self.postMessage({
      type: "failed",
      id: message.id,
      reason: error instanceof Error ? error.message : String(error),
    });
  }
};
