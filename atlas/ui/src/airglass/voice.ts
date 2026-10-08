// ATLAS's voice on the Air Glass page, and the page's own ears.
//
// Speaking: ATLAS's Fish Audio voice, through the same /api/voice/speak the
// dashboard uses. Never the browser's speechSynthesis. The newest line wins
// (an older one still loading is dropped), and while a line plays -- and for
// ECHO_S after -- what the microphone hears is thrown away, so ATLAS never
// answers its own "give it to ATLAS".
//
// Listening (on while the page is open; the Mic button mutes it):
//   on-device  the browser's SpeechRecognition, only when it promises to
//              recognise on this device (Chromium's `processLocally`).
//              Nothing leaves the Mac until he has stopped speaking and the
//              words are about what he is doing.
//   server     otherwise: the microphone is read here, and only a stretch of
//              speech (onset to ~0.7 s of quiet) is recorded and sent to
//              /api/voice/transcribe -- the same transcriber the dashboard's
//              microphone uses. The mic badge says which mode is live.
// Either way the turn ends on ~0.7 s of silence, so the reply starts as soon
// as he stops talking.
import { Mic } from "../voice/mic";
import type { OfflinePack } from "./offlinePack";
import type { Utterance } from "./talk";

export const SILENCE_S = 0.7;
export const ECHO_S = 0.5;

// ------------------------------------------------------------- endpointing

export type EndpointEvent = { type: "onset"; t: number } | { type: "end"; start: number; end: number }
  | { type: "drop"; why: string };

/**
 * Speech start and end from the microphone level, for the server mode. The
 * noise floor is tracked (it falls fast, rises slowly), speech is a level
 * well above it for `onsetS`, and the turn ends after `silenceS` below it.
 * Pure, so it is tested with recorded-looking levels.
 */
export class Endpointer {
  floor = 0.02;
  private speaking = false;
  private above = 0;
  private start = 0;
  private lastLoud = 0;
  constructor(private readonly silenceS = SILENCE_S, private readonly onsetS = 0.08,
              private readonly minS = 0.3, private readonly maxS = 15) {}
  get active(): boolean { return this.speaking; }
  threshold(): number { return Math.max(0.08, this.floor * 3); }
  reset(): void { this.speaking = false; this.above = 0; }
  feed(level: number, t: number, dt: number): EndpointEvent | null {
    if (!this.speaking) {
      this.floor = level < this.floor ? this.floor + (level - this.floor) * 0.3
        : this.floor + (level - this.floor) * Math.min(1, dt * 0.25);
    }
    const loud = level > this.threshold();
    if (!this.speaking) {
      this.above = loud ? this.above + dt : 0;
      if (this.above >= this.onsetS) {
        this.speaking = true;
        this.start = t - this.above;
        this.lastLoud = t;
        return { type: "onset", t: this.start };
      }
      return null;
    }
    if (loud) this.lastLoud = t;
    if (t - this.lastLoud >= this.silenceS || t - this.start >= this.maxS) {
      this.speaking = false;
      this.above = 0;
      const end = this.lastLoud;
      if (end - this.start < this.minS) return { type: "drop", why: "too short to be words" };
      return { type: "end", start: this.start, end };
    }
    return null;
  }
}

/**
 * On-device interim results to an utterance with word times: each word is
 * stamped the first time an interim result contains it, and the turn ends
 * when nothing new has arrived for `silenceS` -- without waiting for the
 * recogniser's own (slower) final result.
 */
export class InterimTracker {
  private words: Array<{ word: string; t: number }> = [];
  private text = "";
  private first = 0;
  private last = 0;
  private through = -1;    // recognition results up to here are spent
  private top = -1;        // the highest result index in the current turn
  private ready: Utterance | null = null;
  constructor(private readonly silenceS = SILENCE_S) {}
  get pending(): boolean { return this.text.length > 0; }
  /** Results already turned into an utterance: their late finals (the
   * recogniser's own, slower verdict on the same words) are ignored. */
  get spentThrough(): number { return this.through; }

  /**
   * One recognition event: the text of the results not yet spent, the
   * highest result index it covers, and whether all of them are final. A
   * final ends the turn at once, whichever comes first, it or the silence.
   */
  update(transcript: string, t: number, index = this.top + 1, final = false): void {
    if (index <= this.through) return;
    const text = transcript.trim();
    this.top = Math.max(this.top, index);
    if (text && text !== this.text) {
      if (!this.text) this.first = t;
      const ws = text.split(/\s+/);
      this.words = ws.map((w, i) => {
        const had = this.words[i];
        return had && had.word === w ? had : { word: w, t };
      });
      this.text = text;
      this.last = t;
    }
    if (final && this.text) this.ready = this.take();
  }

  private take(): Utterance {
    const u: Utterance = {
      text: this.text, start: Math.max(0, this.first - 0.35), end: this.last, words: this.words,
    };
    this.through = this.top;
    this.text = "";
    this.words = [];
    return u;
  }

  /** The finished utterance once he has been quiet for silenceS (or the
   * recogniser has said it is final), else null. Each turn comes out once. */
  poll(t: number): Utterance | null {
    if (this.ready) { const u = this.ready; this.ready = null; return u; }
    if (!this.text || t - this.last < this.silenceS - 1e-6) return null;
    return this.take();
  }
  /** Drop the turn in progress (ATLAS was talking); its results stay spent. */
  clear(): void { this.text = ""; this.words = []; this.ready = null; this.through = this.top; }
  /** A new recognition session: result indices start again from 0. */
  restart(): void { this.text = ""; this.words = []; this.ready = null; this.through = -1; this.top = -1; }
}

// ---------------------------------------------------------------- speaking

export type SpeakResult = { ok: true } | { ok: false; problem: string };

/** ATLAS's Fish Audio voice. Not the browser's: there is no fallback to
 * speechSynthesis, so a failure is shown, not covered by another voice.
 * When ATLAS cannot be reached, a line kept in the offline pack (the same
 * words, rendered by Fish in ATLAS's voice) plays instead; any other line
 * stays text. */
export class FishSpeaker {
  enabled = true;
  /** performance.now()/1000 until which the microphone should not trust
   * what it hears (ATLAS's own voice, and its echo). */
  quietUntil = 0;
  onChange: ((speaking: boolean) => void) | null = null;
  /** ATLAS's offline lines, kept in this browser (offlinePack.ts). */
  pack: OfflinePack | null = null;
  private fetching: AbortController | null = null;
  private playing: HTMLAudioElement | null = null;
  private url: string | null = null;
  private n = 0;

  get speaking(): boolean { return this.playing !== null; }

  async say(text: string): Promise<SpeakResult> {
    const words = text.trim().slice(0, 1400);
    if (!this.enabled || !words) return { ok: false, problem: "ATLAS speaks is off" };
    const n = ++this.n;
    this.stop();
    const ctl = new AbortController();
    this.fetching = ctl;
    let blob: Blob;
    try {
      const r = await fetch("/api/voice/speak", {
        method: "POST", headers: { "Content-Type": "application/json" },
        credentials: "same-origin", body: JSON.stringify({ text: words }), signal: ctl.signal,
      });
      if (!r.ok) {
        const d = (await r.json().catch(() => ({}))) as { detail?: string };
        return await this.kept(words, n, d.detail ?? `Speech failed (${r.status}).`);
      }
      blob = await r.blob();
    } catch (e) {
      if (ctl.signal.aborted) return { ok: false, problem: "interrupted" };
      return await this.kept(words, n, `Speech unreachable: ${String(e)}`);
    } finally {
      if (this.fetching === ctl) this.fetching = null;
    }
    return this.play(blob, n);
  }

  /** One of the offline pack's lines by id, from this browser only (no
   * network): what Air Glass says when ATLAS itself is out of reach. With no
   * kept clip it says nothing and the caller's text stands. */
  async sayKept(id: string): Promise<SpeakResult> {
    if (!this.enabled) return { ok: false, problem: "ATLAS speaks is off" };
    const n = ++this.n;
    this.stop();
    const blob = (await this.pack?.clip(id)) ?? null;
    if (!blob) return { ok: false, problem: "no kept clip of that line" };
    return this.play(blob, n);
  }

  /** `words` from the offline pack when Fish could not be reached for them. */
  private async kept(words: string, n: number, problem: string): Promise<SpeakResult> {
    const blob = (await this.pack?.clipFor(words)) ?? null;
    return blob ? this.play(blob, n) : { ok: false, problem };
  }

  private async play(blob: Blob, n: number): Promise<SpeakResult> {
    if (n !== this.n || !this.enabled) return { ok: false, problem: "interrupted" };
    const url = URL.createObjectURL(blob);
    const el = new Audio(url);
    this.url = url;
    this.playing = el;
    this.quietUntil = Infinity;
    this.onChange?.(true);
    const done = new Promise<void>((resolve) => {
      el.onended = (): void => resolve();
      el.onerror = (): void => resolve();
      el.onpause = (): void => resolve();
    });
    try {
      await el.play();
    } catch (e) {
      this.finish(el);
      return { ok: false, problem: `The browser would not play the voice (${(e as Error)?.name ?? e}).` };
    }
    await done;
    this.finish(el);
    return { ok: true };
  }

  private finish(el: HTMLAudioElement): void {
    if (this.playing !== el) return;
    this.playing = null;
    if (this.url) URL.revokeObjectURL(this.url);
    this.url = null;
    this.quietUntil = performance.now() / 1000 + ECHO_S;
    this.onChange?.(false);
  }

  stop(): void {
    this.fetching?.abort();
    this.fetching = null;
    const el = this.playing;
    if (el) { el.pause(); this.finish(el); }
  }

  destroy(): void { this.n += 1; this.stop(); this.onChange = null; }
}

// --------------------------------------------------------------- listening

interface RecognitionResultLike { readonly length: number; [i: number]: { transcript: string } | undefined; isFinal: boolean }
interface RecognitionEventLike { resultIndex: number; results: { length: number; [i: number]: RecognitionResultLike | undefined } }
interface RecognitionLike {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  processLocally?: boolean;
  onresult: ((e: RecognitionEventLike) => void) | null;
  onerror: ((e: { error?: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}
interface RecognitionCtor {
  new (): RecognitionLike;
  available?: (o: { langs: string[]; processLocally: boolean }) => Promise<string>;
  install?: (o: { langs: string[]; processLocally: boolean }) => Promise<boolean>;
}

export type CommandSupport = "on-device" | "downloadable" | "unsupported";

function recognitionCtor(): RecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

/** Can this browser recognise speech on this device? Never guesses yes. */
export async function commandSupport(lang = "en-US"): Promise<CommandSupport> {
  const Ctor = recognitionCtor();
  if (!Ctor?.available) return "unsupported";
  try {
    const a = await Ctor.available({ langs: [lang], processLocally: true });
    if (a === "available") return "on-device";
    if (a === "downloadable" || a === "downloading") return "downloadable";
    return "unsupported";
  } catch {
    return "unsupported";
  }
}

export type ListenMode = "off" | "starting" | "on-device" | "server" | "muted" | "unavailable";

const now = (): number => performance.now() / 1000;

/**
 * The page's ears. start() picks on-device recognition when the browser can
 * promise it, else the server mode; either hands finished utterances to
 * `onUtterance`. `quietUntil()` is when ATLAS last stopped talking (Infinity
 * while it talks): what is heard before then, and on-device words that began
 * within a second of it, are its own voice and are dropped.
 */
export class Listener {
  mode: ListenMode = "off";
  detail = "";
  /** 0..1, the live microphone level (server mode), for the badge. */
  level = 0;
  private rec: RecognitionLike | null = null;
  private readonly interim = new InterimTracker();
  private mic: Mic | null = null;
  private readonly ends = new Endpointer();
  private timer: number | null = null;
  private lastTick = 0;
  private recording = false;
  private wanted = false;

  constructor(private readonly onUtterance: (u: Utterance) => void,
              private readonly onState: (mode: ListenMode, detail: string) => void,
              private readonly quietUntil: () => number) {}

  private quiet(): boolean { return now() < this.quietUntil(); }

  private set(mode: ListenMode, detail: string): void {
    this.mode = mode; this.detail = detail;
    this.onState(mode, detail);
  }

  async start(): Promise<ListenMode> {
    this.wanted = true;
    this.set("starting", "opening the microphone");
    if (await this.startOnDevice()) return this.mode;
    if (!this.wanted) return this.mode;
    return this.startServer();
  }

  private async startOnDevice(): Promise<boolean> {
    const Ctor = recognitionCtor();
    let support = await commandSupport();
    if (support === "downloadable" && Ctor?.install) {
      this.set("starting", "downloading the on-device speech model");
      try {
        if (await Ctor.install({ langs: ["en-US"], processLocally: true })) support = "on-device";
      } catch { /* stays downloadable */ }
    }
    if (support !== "on-device" || !Ctor || !this.wanted) return false;
    const rec = new Ctor();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = "en-US";
    rec.processLocally = true;
    if (rec.processLocally !== true) return false;   // the promise did not stick
    rec.onresult = (e): void => {
      // Only results not yet turned into an utterance: the recogniser's late
      // final for words already acted on must not act a second time.
      let text = "", top = -1, final = true;
      for (let i = Math.max(0, this.interim.spentThrough + 1); i < e.results.length; i++) {
        const r = e.results[i];
        if (!r) continue;
        text += ` ${r[0]?.transcript ?? ""}`;
        top = i;
        final &&= r.isFinal;
      }
      if (top < 0) return;
      if (this.quiet()) { this.interim.update("", now(), top); this.interim.clear(); return; }
      this.interim.update(text, now(), top, final);
    };
    rec.onerror = (e): void => { this.detail = `on-device: ${e.error ?? "error"}`; };
    rec.onend = (): void => {
      // Continuous recognition still ends after a silence; carry on while wanted.
      if (this.wanted && this.rec === rec && this.mode === "on-device") {
        this.interim.restart();   // a new session numbers its results from 0
        try { rec.start(); } catch { this.set("unavailable", "on-device recognition stopped"); }
      }
    };
    try { rec.start(); } catch { return false; }
    this.rec = rec;
    this.tick();
    this.set("on-device", "listening on this Mac");
    return true;
  }

  private async startServer(): Promise<ListenMode> {
    const mic = new Mic();
    const state = await mic.open();
    if (!this.wanted) { mic.close(); return this.mode; }
    if (state !== "listening") {
      this.set("unavailable", state === "denied" ? "the microphone was refused" : "no microphone");
      return this.mode;
    }
    this.mic = mic;
    this.ends.reset();
    this.tick();
    this.set("server", "listening; what you say is transcribed by ATLAS");
    return this.mode;
  }

  private tick(): void {
    if (this.timer !== null) return;
    this.lastTick = now();
    this.timer = window.setInterval(() => void this.step(), 50);
  }

  private async step(): Promise<void> {
    const t = now(), dt = t - this.lastTick;
    this.lastTick = t;
    if (this.rec) {
      const u = this.interim.poll(t);
      // Words that began while ATLAS spoke, or within a second after, are
      // its own voice coming back.
      if (u && u.start + 0.35 >= this.quietUntil() + 1) this.onUtterance(u);
      return;
    }
    const mic = this.mic;
    if (!mic) return;
    this.level = mic.read(8).level;
    if (this.quiet()) {
      // ATLAS is talking: whatever is being recorded is its own voice.
      if (this.recording) { this.recording = false; void mic.stopRecording(); }
      this.ends.reset();
      return;
    }
    const ev = this.ends.feed(this.level, t, dt);
    if (ev?.type === "onset") {
      this.recording = mic.startRecording();
    } else if ((ev?.type === "end" || ev?.type === "drop") && this.recording) {
      this.recording = false;
      const blob = await mic.stopRecording();
      if (ev.type === "end" && blob && blob.size > 0) void this.transcribe(blob, ev.start, ev.end);
    }
  }

  private async transcribe(blob: Blob, start: number, end: number): Promise<void> {
    const form = new FormData();
    form.append("audio", blob, "airglass.webm");
    try {
      const r = await fetch("/api/voice/transcribe", { method: "POST", body: form, credentials: "same-origin" });
      if (!r.ok) {
        const d = (await r.json().catch(() => ({}))) as { detail?: string };
        this.detail = d.detail ?? `transcription failed (${r.status})`;
        this.onState(this.mode, this.detail);
        return;
      }
      const body = (await r.json()) as { transcript?: string };
      const text = (body.transcript ?? "").trim();
      if (text && this.wanted && this.mode === "server") this.onUtterance({ text, start, end });
    } catch (e) {
      this.detail = `transcription unreachable: ${String(e)}`;
      this.onState(this.mode, this.detail);
    }
  }

  /** Mute: let go of the microphone entirely (the OS indicator goes dark). */
  stop(mode: ListenMode = "muted"): void {
    this.wanted = false;
    if (this.timer !== null) window.clearInterval(this.timer);
    this.timer = null;
    const rec = this.rec;
    this.rec = null;
    try { rec?.abort(); } catch { /* already stopped */ }
    this.interim.clear();
    this.mic?.close();
    this.mic = null;
    this.recording = false;
    this.level = 0;
    this.set(mode, mode === "muted" ? "muted: the microphone is closed" : "");
  }
}
