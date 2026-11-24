/**
 * The on-device wake gate: whisper-tiny.en, running in a Web Worker in this
 * tab, listening for the word "atlas".
 *
 * The promise is the one `detector.ts` opens with: nothing leaves the
 * machine until the wake word fires. Here that is literal -- the model
 * files are served from `/wake/whisper/` on ATLAS's own origin (fetched once
 * by `ui/scripts/fetch-whisper-model.sh`), the ONNX runtime from the
 * bundle's own `/assets/`, remote model loading is switched off inside the
 * worker, and the audio is a two-second window of samples handed to that
 * worker and dropped. No transcript is kept; only the yes/no of "was
 * 'atlas' in it" comes back out.
 *
 * This file holds the pure parts (the word match, the resampler, the rolling
 * window) so they can be tested in Node, and `loadWhisperGate`, which is the
 * only thing here that touches a Worker. `@huggingface/transformers` is
 * imported only inside the worker (`whisperWorker.ts`), never from this
 * module, so importing it from a test costs nothing.
 */

import { isWakePhrase } from "./wakePhrase";

/** Whisper's native rate. */
export const TARGET_RATE = 16000;
/** Each look at the audio covers this much of it... */
export const WINDOW_SECONDS = 2;
/** ...and a new look starts this often, so a word on a boundary is seen whole. */
export const HOP_SECONDS = 1;

export interface WakeGate {
  /** True when "atlas" was heard in this window (~2 s of audio). Rejects
   *  once the gate has stopped (the worker died or keeps failing). */
  push(pcm: Float32Array, sampleRate: number): Promise<boolean>;
  /** Which backend the model is running on. */
  readonly device: "webgpu" | "wasm";
  /** Release the worker and the model with it. */
  close(): void;
}

/**
 * "Atlas" or "Hey Atlas", any case, any punctuation around it (the rule is
 * `wakePhrase.ts`, shared with the browser recogniser: the bare word wakes
 * him when addressed to him, not inside "the atlas report"). Whisper
 * writes "Atlas," and "ATLAS." and "hey atlas" -- all of those wake him.
 * It also writes "At last," for a phrase that genuinely is "at last", and
 * that must NOT wake him; nor must "atlases" or "Atlassian".
 */
export function containsWake(text: string): boolean {
  return isWakePhrase(text);
}

/**
 * Bring audio to 16 kHz. Downsampling averages every input sample that
 * falls inside each output sample's span (a box filter -- crude, but it
 * keeps energy above 8 kHz from folding back into the band whisper reads);
 * upsampling, which a real microphone should never need, interpolates.
 */
export function resample(input: Float32Array, fromRate: number,
                         toRate = TARGET_RATE): Float32Array {
  if (fromRate === toRate) return input;
  const length = Math.round((input.length * toRate) / fromRate);
  const out = new Float32Array(length);
  const ratio = fromRate / toRate;
  if (ratio > 1) {
    for (let i = 0; i < length; i += 1) {
      const start = Math.floor(i * ratio);
      const end = Math.min(input.length, Math.max(start + 1, Math.floor((i + 1) * ratio)));
      let sum = 0;
      for (let j = start; j < end; j += 1) sum += input[j] ?? 0;
      out[i] = sum / (end - start);
    }
  } else {
    for (let i = 0; i < length; i += 1) {
      const at = i * ratio;
      const left = Math.floor(at);
      const frac = at - left;
      const a = input[left] ?? 0;
      const b = input[left + 1] ?? a;
      out[i] = a + (b - a) * frac;
    }
  }
  return out;
}

/**
 * The rolling window: holds at most WINDOW_SECONDS of audio and hands back a
 * copy of it every HOP_SECONDS. Nothing older than one window is ever kept,
 * which is the whole of this detector's memory.
 */
export class WakeWindow {
  private readonly size: number;
  private readonly hop: number;
  private readonly buffer: Float32Array;
  private filled = 0;
  private sinceEmit = 0;
  private emitted = false;

  constructor(readonly sampleRate: number) {
    this.size = Math.round(sampleRate * WINDOW_SECONDS);
    this.hop = Math.round(sampleRate * HOP_SECONDS);
    this.buffer = new Float32Array(this.size);
  }

  /** Samples currently held. Never more than one window. */
  get held(): number {
    return this.filled;
  }

  push(samples: Float32Array): Float32Array | null {
    const n = samples.length;
    if (n >= this.size) {
      this.buffer.set(samples.subarray(n - this.size));
      this.filled = this.size;
    } else {
      const keep = Math.min(this.filled, this.size - n);
      // Slide the newest `keep` samples down, then append.
      this.buffer.copyWithin(0, this.filled - keep, this.filled);
      this.buffer.set(samples, keep);
      this.filled = keep + n;
    }
    this.sinceEmit += n;
    if (this.filled < this.size) return null;
    if (this.emitted && this.sinceEmit < this.hop) return null;
    this.emitted = true;
    this.sinceEmit = 0;
    return this.buffer.slice(0, this.size);
  }

  clear(): void {
    this.filled = 0;
    this.sinceEmit = 0;
    this.emitted = false;
  }
}

// ------------------------------------------------------------ gate health

/** Consecutive windows the worker may fail to read before the gate is
 *  declared stopped. One bad window is noise; three in a row is a broken
 *  runtime, and a gate that keeps "listening" through that is a detector
 *  that reads as live and is not. */
export const MAX_FAILED_WINDOWS = 3;
/** A window not answered in this long means the worker is hung. */
export const HEAR_TIMEOUT_MS = 15_000;

export class GateHealth {
  private failures = 0;
  private stoppedWith: string | null = null;

  /** Record one window. Returns the reason once the gate has stopped. */
  record(ok: boolean, reason = "the model could not read the audio"): string | null {
    if (this.stoppedWith) return this.stoppedWith;
    this.failures = ok ? 0 : this.failures + 1;
    if (this.failures >= MAX_FAILED_WINDOWS) this.stoppedWith = reason;
    return this.stoppedWith;
  }

  /** Stop outright -- the worker itself died. */
  stop(reason: string): string {
    this.stoppedWith ??= reason;
    return this.stoppedWith;
  }

  get stopped(): string | null {
    return this.stoppedWith;
  }
}

// ------------------------------------------------------------ the worker

type FromWorker =
  | { type: "ready"; device: "webgpu" | "wasm" }
  | { type: "error"; reason: string }
  | { type: "heard"; id: number; text: string }
  | { type: "failed"; id: number; reason: string };

const LOAD_TIMEOUT_MS = 60_000;

/**
 * Start the worker and load whisper-tiny.en in it. Resolves to null -- never
 * to a gate that loads and does not listen -- when there is no Worker, the
 * model files are missing, or the runtime cannot start.
 */
export async function loadWhisperGate(modelPath: string): Promise<WakeGate | null> {
  if (typeof Worker === "undefined") return null;
  let worker: Worker;
  try {
    worker = new Worker(new URL("./whisperWorker.ts", import.meta.url), {
      type: "module",
    });
  } catch (error) {
    console.warn("local wake: could not start the worker:", error);
    return null;
  }

  // Each window's settle function: text, null for one failed read, or an
  // Error once the gate has stopped for good.
  const pending = new Map<number, (text: string | null | Error) => void>();
  let loaded: ((m: FromWorker) => void) | null = null;
  worker.onmessage = (event: MessageEvent<FromWorker>): void => {
    const message = event.data;
    if (message.type === "heard") {
      pending.get(message.id)?.(message.text);
      pending.delete(message.id);
    } else if (message.type === "failed") {
      pending.get(message.id)?.(null);
      pending.delete(message.id);
    } else {
      loaded?.(message);
    }
  };

  const ready = await new Promise<FromWorker>((resolve) => {
    const timer = window.setTimeout(
      () => resolve({ type: "error", reason: "timed out loading the model" }),
      LOAD_TIMEOUT_MS,
    );
    loaded = (message): void => {
      window.clearTimeout(timer);
      resolve(message);
    };
    worker.onerror = (event): void => {
      window.clearTimeout(timer);
      resolve({ type: "error", reason: event.message || "the worker failed" });
    };
    worker.postMessage({ type: "load", modelPath });
  });
  loaded = null;

  if (ready.type !== "ready") {
    console.warn("local wake unavailable:",
      ready.type === "error" ? ready.reason : "unexpected reply");
    worker.terminate();
    return null;
  }

  // After load, a worker that dies (out of memory in wasm, a lost WebGPU
  // device) must not leave every pending window waiting forever -- the
  // detector would stay "busy", never push again, and still read as live.
  const health = new GateHealth();
  const stopAll = (reason: string): void => {
    const why = health.stop(reason);
    for (const settle of pending.values()) settle(new Error(why));
    pending.clear();
  };
  worker.onerror = (event): void => {
    event.preventDefault();
    stopAll(event.message || "the worker crashed");
  };
  worker.onmessageerror = (): void => stopAll("the worker's reply was unreadable");

  let nextId = 0;
  return {
    device: ready.device,
    async push(pcm: Float32Array, sampleRate: number): Promise<boolean> {
      if (health.stopped) throw new Error(health.stopped);
      const samples = resample(pcm, sampleRate);
      const id = (nextId += 1);
      const text = await new Promise<string | null | Error>((resolve) => {
        const timer = window.setTimeout(
          () => stopAll("the model stopped answering"), HEAR_TIMEOUT_MS);
        pending.set(id, (value) => {
          window.clearTimeout(timer);
          resolve(value);
        });
        // Transferred, not copied: the window leaves this thread entirely.
        worker.postMessage({ type: "hear", id, pcm: samples }, [samples.buffer]);
      });
      if (text instanceof Error) throw text;
      const stopped = health.record(text !== null);
      if (stopped) throw new Error(stopped);
      return text !== null && containsWake(text);
    },
    close(): void {
      health.stop("closed");
      for (const settle of pending.values()) settle(null);
      pending.clear();
      worker.terminate();
    },
  };
}
