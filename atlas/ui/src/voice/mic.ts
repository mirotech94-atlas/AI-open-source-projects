/**
 * The microphone, and the truth about it.
 *
 * The indicator can never be wrong, which means it is derived from the actual
 * MediaStream rather than from what the app intended. There are exactly four
 * states and each one corresponds to something real:
 *
 *   unavailable — no device, or the browser refuses getUserMedia
 *   denied      — permission was refused
 *   off         — we hold no stream at all; the OS indicator is dark
 *   listening   — a stream is open, and the wake detector is reading it here
 *   streaming   — audio is being recorded to send to the server
 *
 * "off" means the tracks are stopped, not muted. A muted track still holds the
 * device and still lights the OS indicator, which would make the UI a liar.
 */
export type MicState =
  | "unavailable"
  | "denied"
  | "off"
  | "listening"
  | "streaming";

export interface MicReading {
  /** 0..1, from the live stream. Used to draw the waveform. */
  level: number;
  bars: number[];
}

export class Mic {
  private stream: MediaStream | null = null;
  private context: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  // Backed by a plain ArrayBuffer: getByteTimeDomainData will not accept a
  // view that might be over a SharedArrayBuffer.
  private data: Uint8Array<ArrayBuffer> = new Uint8Array(new ArrayBuffer(0));
  private recorder: MediaRecorder | null = null;
  private chunks: Blob[] = [];

  state: MicState = "off";
  /** The browser's own name for the last refusal (NotAllowedError,
   *  NotReadableError, ...), so the page can say WHICH block it hit. */
  lastError = "";
  /** The processing the browser reports it applied to the open track --
   *  null for a setting it does not report. Empty until opened. */
  processing: Record<"echoCancellation" | "noiseSuppression" | "autoGainControl",
    boolean | null> | null = null;

  static supported(): boolean {
    return (
      typeof navigator !== "undefined" &&
      !!navigator.mediaDevices?.getUserMedia &&
      typeof AudioContext !== "undefined"
    );
  }

  /** Open the device. Resolves to the state so the caller cannot assume. */
  async open(): Promise<MicState> {
    // Already open: hand back what is held. Opening again would overwrite
    // `stream` without stopping it, and the orphaned tracks would keep the
    // OS indicator lit after "mute" -- the badge saying off while a stream
    // is live, which is the one thing this class exists to prevent.
    if (this.stream) return this.state;
    if (!Mic.supported()) {
      this.state = "unavailable";
      return this.state;
    }
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
    } catch (err) {
      // NotAllowedError is a refusal; NotFoundError means there is no device.
      const name = err instanceof Error ? err.name : "";
      this.lastError = name;
      this.state = name === "NotFoundError" || name === "OverconstrainedError"
        ? "unavailable"
        : "denied";
      return this.state;
    }

    // What the browser actually applied, which is not always what was asked:
    // barge-in (`bargeIn.ts`) leans on the echo canceller, and another
    // stream on the same device opened without it (the clap tap) may change
    // what this one gets. Said once per open, for the live check.
    const track = this.stream.getAudioTracks()[0];
    const settings = track?.getSettings?.() ?? {};
    this.processing = {
      echoCancellation: settings.echoCancellation ?? null,
      noiseSuppression: settings.noiseSuppression ?? null,
      autoGainControl: settings.autoGainControl ?? null,
    };
    console.info("[voice] mic processing applied", this.processing);

    this.context = new AudioContext();
    const source = this.context.createMediaStreamSource(this.stream);
    this.analyser = this.context.createAnalyser();
    this.analyser.fftSize = 256;
    this.analyser.smoothingTimeConstant = 0.7;
    source.connect(this.analyser);
    this.data = new Uint8Array(new ArrayBuffer(this.analyser.frequencyBinCount));

    this.state = "listening";
    return this.state;
  }

  /** Release the device completely. Stops tracks — does not merely mute. */
  close(): void {
    this.recorder?.state === "recording" && this.recorder.stop();
    this.recorder = null;
    this.chunks = [];
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
    void this.context?.close();
    this.context = null;
    this.analyser = null;
    this.state = "off";
  }

  /** Live amplitude, for a waveform that actually responds to a voice. */
  read(bars = 38): MicReading {
    if (!this.analyser) return { level: 0, bars: new Array(bars).fill(0) };
    this.analyser.getByteTimeDomainData(this.data);

    const perBar = Math.max(1, Math.floor(this.data.length / bars));
    const out: number[] = [];
    let peak = 0;
    for (let b = 0; b < bars; b += 1) {
      let sum = 0;
      for (let i = 0; i < perBar; i += 1) {
        const sample = (this.data[b * perBar + i] ?? 128) - 128;
        sum += sample * sample;
      }
      // RMS, then a gentle curve so ordinary speech fills the bar usefully.
      const rms = Math.sqrt(sum / perBar) / 128;
      const shaped = Math.min(1, Math.pow(rms * 3.2, 0.7));
      out.push(shaped);
      peak = Math.max(peak, shaped);
    }
    return { level: peak, bars: out };
  }

  startRecording(): boolean {
    if (!this.stream) return false;
    const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find(
      (candidate) => MediaRecorder.isTypeSupported(candidate),
    );
    this.chunks = [];
    this.recorder = new MediaRecorder(
      this.stream,
      mime ? { mimeType: mime } : undefined,
    );
    this.recorder.ondataavailable = (event): void => {
      if (event.data.size > 0) this.chunks.push(event.data);
    };
    this.recorder.start();
    this.state = "streaming";
    return true;
  }

  async stopRecording(): Promise<Blob | null> {
    const recorder = this.recorder;
    if (!recorder || recorder.state !== "recording") return null;
    const done = new Promise<void>((resolve) => {
      recorder.onstop = (): void => resolve();
    });
    recorder.stop();
    await done;
    this.recorder = null;
    if (this.stream) this.state = "listening";
    const type = this.chunks[0]?.type ?? "audio/webm";
    return this.chunks.length ? new Blob(this.chunks, { type }) : null;
  }
}
