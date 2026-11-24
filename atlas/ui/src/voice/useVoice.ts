import { useCallback, useEffect, useRef, useState } from "react";
import { Mic, type MicState } from "./mic";
import { createDetector, type Detector, type DetectorKind } from "./detector";
import {
  createClickToTalk,
  fallbackAfterStartFailure,
  gateStoppedProblem,
  micBadgeAction,
} from "./detectorCore";
import {
  ACKNOWLEDGEMENTS,
  FOLLOWUP_QUIET_MS,
  isFarewell,
  next,
  pickAcknowledgement,
  sentencesOf,
  speakable,
  statusOf,
  stripAcknowledgement,
  type LoopEvent,
  type LoopState,
  type Phase,
  type VoiceStatus,
} from "./loop";
import type { AskReply, SystemCheck, VoiceFocus } from "../api";
import { carryFocus, checklistDelayMs } from "./focus";
import { initialBargeIn, peakOf, stepBargeIn, type BargeInState } from "./bargeIn";
import { planInterrupt, tapAction, type InterruptCause } from "./interrupt";
import { levelOf } from "./sparks";

/**
 * The voice conversation, as a state machine.
 *
 * Three rules are structural rather than stylistic:
 *
 * 1. The mic state reported here is read from the device, never assumed. A
 *    caller cannot set it; it only ever reflects what `Mic` actually holds.
 * 2. Nothing is uploaded before the wake phrase fires. The detector reads the
 *    stream in this process; recording only starts on wake.
 * 3. Once woken, the conversation is an open loop (`loop.ts`): an answered
 *    turn goes back to listening, and the loop closes on quiet, a farewell,
 *    a dismiss or a mute -- never on a guess, and never into an action.
 * 4. He can talk over ATLAS (`bargeIn.ts`): while an answer is being
 *    spoken, a sustained voice that ATLAS's own playback cannot explain
 *    stops it and opens his next turn in the same conversation. A tap on
 *    the core ("Tap to stop") does the same while ATLAS thinks or speaks
 *    (`interrupt.ts`). A mute still wins -- it ends the conversation, and
 *    with it the answer.
 */
export type { Phase } from "./loop";

/** ATLAS's own voice: the local clone of the NotebookLM host. `health` is
 *  "ok" only when its server answered just now; otherwise "unknown" and Fish
 *  Audio speaks (`speaking`). */
export interface VoiceSource {
  voice: string;
  health: "ok" | "unknown";
  as_of: string;
  reason: string | null;
  speaking: "clone" | "fish" | "none";
  disclosure: string;
}

export interface Capabilities {
  transcription: boolean;
  answering: boolean;
  /** Some voice can speak: the local clone, or Fish Audio. */
  speech: boolean;
  voice_source?: VoiceSource;
  /** True only when BOTH a Picovoice key and the trained keyword file exist. */
  wake_word: boolean;
  /** The Picovoice key itself, or null. Browser-side by design. */
  wake_key: string | null;
  /** Opt-in fallback that streams audio to Google. Off unless enabled. */
  speech_api_wake: boolean;
  /** On-device hand tracking. True only when opted in AND the model file
   *  (fetched separately, git-ignored) is actually present on disk. */
  pinch_wake: boolean;
  /** The wider gesture set (cursor, drag, swipe, dismiss) beyond pinch-wake
   *  alone -- same opt-in flag, same model file, checked the same way. */
  gestures: boolean;
  /** On-device whisper wake word. True only when VOICE_LOCAL_WAKE is set
   *  AND the model files (fetched by a script, git-ignored) are present. */
  local_wake: boolean;
  /** A double clap, heard on this device, riding beside whichever of
   *  porcupine, local-whisper or pinch is actually live. True only when
   *  VOICE_CLAP_WAKE is set -- unlike the others, clap needs no model file,
   *  so there is no second half to check. */
  clap_wake: boolean;
  missing: string[];
}

// Where the dashboard should look after an answer -- a view command, a
// system check, or a spoken status naming a module. Defined beside the SPA's
// routing table in api.ts; re-exported here where it was first declared.
export type { VoiceFocus } from "../api";

/** One exchange of this conversation, for the transcript log. */
export interface Turn {
  said: string;
  answer: string | null;
  problem: string | null;
  at: Date;
}

export interface VoiceState {
  phase: Phase;
  /** What the stage pill says -- the same mapping everywhere it is drawn. */
  status: VoiceStatus;
  mic: MicState;
  detector: DetectorKind | null;
  /** A clap gate is riding beside `detector` (Task 47 / G4). False whenever
   *  clap is off, unavailable for the live detector (speech-api,
   *  click-to-talk), or its own microphone failed to open. */
  detectorClap: boolean;
  /** Set when the chosen detector's own start() rejected and it was
   *  replaced with click-to-talk -- e.g. "camera refused — click to talk".
   *  Cleared whenever a detector is (re)selected and has not yet failed. */
  detectorProblem: string | null;
  /** The wake detector is armed. Off only after a mute. */
  handsFree: boolean;
  transcript: string;
  answer: string;
  typed: string;
  problem: string | null;
  focus: VoiceFocus | null;
  /** Which branch answered the last turn ("system_check", "goodnight", ...). */
  kind: string | null;
  /** The system check the last answer was spoken from, to draw as said. */
  check: SystemCheck | null;
  /** Why ATLAS is typing instead of speaking, when speech is configured. */
  speechProblem: string | null;
  /** The turns of this conversation, oldest first. */
  log: Turn[];
  bars: number[];
  /** How loud ATLAS's own voice is right now, 0..1, read off the analyser on
   *  the clip that is playing. 0 whenever nothing is playing, so the speaking
   *  sparks stop in silence. Stable: safe to call from an animation frame. */
  outputLevel: () => number;
  capabilities: Capabilities | null;
  wake: () => void;
  dismiss: () => void;
  /** Stop ATLAS thinking or talking and start his next turn now. */
  interrupt: () => void;
  /** One tap on the core: wake, stop ATLAS, or dismiss (`tapAction`). */
  tap: () => void;
  /** Mute (release the mic, disarm the wake gate) or unmute. */
  toggleMic: () => void;
  /** Read text aloud through /api/voice/speak. False when it was not. */
  speak: (text: string) => Promise<boolean>;
}

const TYPE_MS = 45;           // per character, as specified
const TYPED_PAUSE_MS = 1500;  // after typing, when there is no voice to wait on
// A detector hearing ATLAS's own last words ("...ask ATLAS again") straight
// after the loop closes must not reopen it. The whisper gate remembers two
// seconds of audio, looks again every second, and answers after inference:
// a window holding words from INSIDE the conversation can come back up to
// ~window + hop + latency after the close, so the cooldown covers all three.
const WAKE_COOLDOWN_MS = 3500;
const MAX_SPEECH_MS = 90_000; // a playback whose `ended` never fires
// Fish Audio's voice not back by then: given up on, and the answer is shown
// with ATLAS's kept Fish clip "I couldn't reach my voice" (2026-10-06).
const FISH_TIMEOUT_MS = 15_000;

// Endpointing: when has he finished speaking?
//
// This was a bare 1400ms tail, and 1.4s of quiet is shorter than an ordinary
// pause for thought -- so the turn ended mid-sentence and the half he had not
// said yet was simply lost. Three constants instead of one:
// Quiet before the turn is over. 2600 felt like being ignored, and so did
// 1200 (Miro, 2026-10-06: "should answer once I stop speaking").
const SPEECH_TAIL_MS = 750;
const MIN_UTTERANCE_MS = 900; // a cough or a chair scrape must not end it
const MAX_UTTERANCE_MS = 45000; // an open mic in a noisy room must not run on
const NO_SPEECH_MS = 7000;    // nothing heard at all on the FIRST turn: say so

// The threshold, and the two ways of getting it wrong.
//
// `level` is a shaped peak: min(1, (rms * 3.2) ^ 0.7). An idle room lands
// near 0.055 and ordinary speech near 0.28. A fixed 0.06 sat directly ON the
// idle floor, so on a quiet mic speech never registered and on a loud one
// the quiet tail never elapsed -- both hang at "listening" forever.
//
// The fix for that was to measure the room for the first 500ms and set the
// floor from it. That was worse, and in the more likely direction: people
// start talking straight away. Miro says the wake word and what he wants in
// one breath, so the calibration window sampled HIS VOICE, set the floor at
// speech level, and then nothing he said could ever exceed it.
//
// So the floor is the QUIETEST thing seen so far this turn, not the loudest
// thing seen at the start. Speaking immediately cannot poison it, because a
// running minimum only falls. And the absolute floor is well clear of idle
// rather than sitting on it.
const FLOOR_MULTIPLE = 1.9;
const FLOOR_MARGIN = 0.03;
const MIN_SPEAKING_LEVEL = 0.10;

const BAR_COUNT = 38;
const silentBars = (): number[] => new Array(BAR_COUNT).fill(0);

// The three acknowledgements never change, so each is fetched from the
// speech provider once per page and replayed from memory after that: the
// second "atlas" of the day is answered instantly, not a round trip later.
const spokenAcks = new Map<string, Blob>();
interface FetchedVoice {
  blob: Blob | null;
  failed: string | null;
  aborted: boolean;
}
const isAcknowledgement = (text: string): boolean =>
  (ACKNOWLEDGEMENTS as readonly string[]).includes(text);

/** Which of the three things that all surface as "denied" actually happened.
 *  The site blocked in the browser, the whole browser blocked by the OS, and
 *  the mic held by another app need three different fixes, and one generic
 *  "blocked" sent him to a switch that was already on. */
async function micBlockedWhy(errorName: string): Promise<string> {
  if (errorName === "NotReadableError" || errorName === "AbortError") {
    return "Another app is using the microphone (a call, Zoom, dictation). Close it, then tap ATLAS.";
  }
  let site: PermissionState | "unknown" = "unknown";
  try {
    site = (await navigator.permissions.query({ name: "microphone" as PermissionName })).state;
  } catch {
    // Safari and some embedded browsers do not expose this query.
  }
  if (site === "denied") {
    return "This browser is blocking the microphone for atlas.storefront.test. Click the icon left of the "
      + "address bar → Microphone → Allow, reload, then tap ATLAS.";
  }
  // The site is allowed (or never asked), yet the request was refused: the
  // operating system is blocking the browser itself.
  return "macOS is blocking this browser from the microphone. Open System Settings → Privacy & "
    + "Security → Microphone, turn on your browser, restart it, then tap ATLAS.";
}

export function useVoice(enabled: boolean): VoiceState {
  const [phase, setPhase] = useState<Phase>("dormant");
  const [mic, setMic] = useState<MicState>("off");
  const [detectorKind, setDetectorKind] = useState<DetectorKind | null>(null);
  const [detectorClap, setDetectorClap] = useState(false);
  const [detectorProblem, setDetectorProblem] = useState<string | null>(null);
  const [handsFree, setHandsFree] = useState(true);
  // Set by a tap-to-wake while muted; consumed when that conversation ends.
  const rearmAfter = useRef(false);
  const [transcript, setTranscript] = useState("");
  const [answer, setAnswer] = useState("");
  const [typed, setTyped] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [focus, setFocus] = useState<VoiceFocus | null>(null);
  const [kind, setKind] = useState<string | null>(null);
  const [check, setCheck] = useState<SystemCheck | null>(null);
  const [speechProblem, setSpeechProblem] = useState<string | null>(null);
  const [log, setLog] = useState<Turn[]>([]);
  const [bars, setBars] = useState<number[]>(silentBars);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);

  const device = useRef<Mic | null>(null);
  const detector = useRef<Detector | null>(null);
  const frame = useRef<number>(0);
  const quietSince = useRef<number>(0);
  // When this turn began, and whether he has actually said anything yet.
  // Without the second, a silent room satisfies the quiet tail immediately
  // and the turn ends before he speaks.
  const turnStarted = useRef<number>(0);
  const spoke = useRef<boolean>(false);
  const noiseFloor = useRef<number>(0);
  const phaseRef = useRef<Phase>("dormant");
  phaseRef.current = phase;
  // The loop reads the phase between renders (inside awaits and timers), so
  // every move updates the ref at once rather than on the next render --
  // otherwise "waking" set a line earlier still reads as "dormant".
  const moveTo = useCallback((to: Phase): void => {
    phaseRef.current = to;
    setPhase(to);
  }, []);
  const capsRef = useRef<Capabilities | null>(null);
  capsRef.current = capabilities;

  const loop = useRef<LoopState>({ open: false, handsFree: true, turns: 0 });
  // Bumped whenever a conversation starts or ends. Every await in the loop
  // checks it afterwards, so a dismiss mid-answer cannot be followed by the
  // answer's own "and now listen again".
  const conversation = useRef<number>(0);
  const cooldownUntil = useRef<number>(0);
  // Why the on-device whisper gate stopped, once it has. It is not tried
  // again this page: the next detector in the order takes over, and the
  // hint says what happened.
  const gateStopped = useRef<string | null>(null);
  const [detectorEpoch, setDetectorEpoch] = useState(0);
  // The turn being answered. It joins the log once the answer has been
  // given (or the conversation closed under it), so the log never shows
  // the same exchange the stage is still typing out.
  const pendingTurn = useRef<Turn | null>(null);
  const commitTurn = useCallback((): void => {
    const turn = pendingTurn.current;
    pendingTurn.current = null;
    if (turn) setLog((turns) => [...turns, turn]);
  }, []);

  // What ATLAS is saying out loud right now, and its amplitude.
  const playing = useRef<HTMLAudioElement | null>(null);
  const speaking = useRef<boolean>(false);
  const playContext = useRef<AudioContext | null>(null);
  const speaker = useRef<AnalyserNode | null>(null);
  const speakerData = useRef<Uint8Array<ArrayBuffer>>(new Uint8Array(new ArrayBuffer(0)));

  // An answer the autoplay policy refused, waiting for the first gesture.
  const heldBack = useRef<Blob | null>(null);
  // Fish Audio could not voice the last line: noted once until it speaks again.
  const voiceDown = useRef<boolean>(false);
  // The sentences of a reply being fetched, and which reply may still speak:
  // bumped whenever ATLAS is stopped, so a later sentence never follows.
  const speechFetches = useRef<Set<AbortController>>(new Set());
  const speechRun = useRef<number>(0);

  // Barge-in. `answers` numbers each spoken answer; `speakingAnswer` is the
  // one being said right now (0 when none is), and `interruptedAnswer` the
  // one he talked over. The poll loop watches only while an answer -- not
  // the wake acknowledgement -- is actually playing.
  const answers = useRef<number>(0);
  const speakingAnswer = useRef<number>(0);
  const interruptedAnswer = useRef<number>(0);
  const bargeIn = useRef<BargeInState>(initialBargeIn());
  // The answer `respond` is handling (spoken, typed, or waiting on the
  // typing), so a tap after the voice has finished still cuts it short.
  const answering = useRef<number>(0);
  // Ends `respond`'s wait for the typing to catch up, when he interrupts.
  const cutWait = useRef<(() => void) | null>(null);
  // In-flight fetches an interrupt drops: the voice being fetched, and the
  // question being transcribed and answered.
  const speechFetch = useRef<AbortController | null>(null);
  const askFetch = useRef<AbortController | null>(null);
  // Set by a voice barge-in: the turn that follows already has speech in it.
  const floorTaken = useRef<boolean>(false);
  // Barge-in cannot work without a reading of ATLAS's own output; said once
  // per answer in the console, for the live check.
  const blindNoted = useRef<number>(0);

  // --- audio unlock --------------------------------------------------------
  // Waking by voice or clap is not a user gesture, so after a reload the
  // browser refuses sound until the page is touched. The first tap or key
  // anywhere starts the output context (sound is then allowed for the whole
  // page load) and plays whatever answer was held back.
  useEffect(() => {
    if (!enabled) return;
    const events = ["pointerdown", "keydown", "touchstart"] as const;
    const unlock = (): void => {
      for (const e of events) window.removeEventListener(e, unlock, true);
      try {
        if (!playContext.current) playContext.current = new AudioContext();
        const context = playContext.current;
        void context.resume().catch(() => undefined);
        // A silent frame, so Safari also counts the context as started.
        const source = context.createBufferSource();
        source.buffer = context.createBuffer(1, 1, 22050);
        source.connect(context.destination);
        source.start(0);
      } catch {
        // No Web Audio: the element below still gets its gesture.
      }
      const blob = heldBack.current;
      heldBack.current = null;
      if (blob) {
        const url = URL.createObjectURL(blob);
        const element = new Audio(url);
        // Tracked like any other voice, so "Tap to stop", a mute or a
        // dismiss can silence it too.
        playing.current?.pause();
        playing.current = element;
        const release = (): void => {
          URL.revokeObjectURL(url);
          if (playing.current === element) playing.current = null;
        };
        element.onended = release;
        element.onpause = release;
        void element.play().then(() => setSpeechProblem(null)).catch(release);
      }
    };
    for (const e of events) window.addEventListener(e, unlock, { capture: true, passive: true });
    return () => {
      for (const e of events) window.removeEventListener(e, unlock, true);
    };
  }, [enabled]);

  // --- capabilities ------------------------------------------------------
  useEffect(() => {
    if (!enabled) return;
    void fetch("/api/voice/capabilities", { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : null))
      .then((body) => body && setCapabilities(body as Capabilities))
      .catch(() => setCapabilities(null));
  }, [enabled]);

  // --- speech out --------------------------------------------------------
  const readSpeaker = (): number[] | null => {
    const analyser = speaker.current;
    if (!analyser || !speaking.current) return null;
    const data = speakerData.current;
    analyser.getByteTimeDomainData(data);
    const perBar = Math.max(1, Math.floor(data.length / BAR_COUNT));
    const out: number[] = [];
    for (let b = 0; b < BAR_COUNT; b += 1) {
      let sum = 0;
      for (let i = 0; i < perBar; i += 1) {
        const sample = (data[b * perBar + i] ?? 128) - 128;
        sum += sample * sample;
      }
      out.push(Math.min(1, Math.pow((Math.sqrt(sum / perBar) / 128) * 3.2, 0.7)));
    }
    return out;
  };

  /** The output level for the speaking sparks: the analyser's RMS on the
   *  playing clip, never a guess. 0 with no analyser or no clip. */
  const outputLevel = useCallback((): number => {
    const analyser = speaker.current;
    if (!analyser || !speaking.current) return 0;
    const data = speakerData.current;
    analyser.getByteTimeDomainData(data);
    return levelOf(data);
  }, []);

  const stopSpeaking = useCallback((): void => {
    // The rest of a reply's sentences are not said after this.
    speechRun.current += 1;
    // Pausing fires `pause`, which settles the promise `speak` is awaiting.
    playing.current?.pause();
  }, []);

  /** He wants the floor (`interrupt.ts`): ATLAS goes quiet now -- the voice
   *  playing, one being fetched, one held for a tap -- and his turn starts
   *  recording at once. `respond` / `finishTurn` then open the turn. */
  const takeFloor = useCallback((cause: InterruptCause): boolean => {
    const plan = planInterrupt(phaseRef.current, cause);
    if (!plan) return false;
    if (answering.current !== 0) interruptedAnswer.current = answering.current;
    if (plan.abortSpeech) {
      speechFetch.current?.abort();
      speechFetch.current = null;
      for (const pending of speechFetches.current) pending.abort();
      speechFetches.current.clear();
      speechRun.current += 1;
    }
    if (plan.dropHeld) heldBack.current = null;
    if (plan.abortAsk) {
      askFetch.current?.abort();
      askFetch.current = null;
    }
    const held = device.current?.state;
    if (plan.startRecording && held === "listening") {
      device.current?.startRecording();
      setMic(device.current?.state ?? "off");
    }
    floorTaken.current = plan.alreadySpeaking;
    if (plan.stopPlayback) stopSpeaking();
    cutWait.current?.();
    return true;
  }, [stopSpeaking]);

  /** Fish Audio could not voice this line: say so once, on screen and in
   *  the console, and play ATLAS's kept Fish clip of "I couldn't reach my
   *  voice" when there is one. Never a system voice (Miro, 2026-10-06: "the
   *  voice is Fish Audio, not the sound of Siri or anything else"). Null
   *  when nothing is kept: the answer is then text alone. */
  const voiceUnreachable = useCallback(async (why: string): Promise<Blob | null> => {
    if (!voiceDown.current) {
      voiceDown.current = true;
      console.info(`[voice] Fish Audio couldn't speak (${why}); the answer is on screen`);
    }
    setSpeechProblem(`I couldn't reach my voice (${why}), so here it is in text.`);
    try {
      const response = await fetch("/api/voice/clip/no_voice?cached_only=true",
                                   { credentials: "same-origin" });
      return response.ok ? await response.blob() : null;
    } catch {
      return null;
    }
  }, []);

  /** One sentence's voice from Fish Audio, fetched now so it is ready by
   *  the time the sentence before it has been said. Abortable: an
   *  interrupt drops every fetch still in flight. */
  const fetchVoice = useCallback((words: string): Promise<FetchedVoice> => {
    const kept = spokenAcks.get(words);
    if (kept) return Promise.resolve({ blob: kept, failed: null, aborted: false });
    const controller = new AbortController();
    speechFetches.current.add(controller);
    let timedOut = false;
    const timer = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, FISH_TIMEOUT_MS);
    const asked = performance.now();
    return (async (): Promise<FetchedVoice> => {
      try {
        const response = await fetch("/api/voice/speak", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: words }),
          credentials: "same-origin",
          signal: controller.signal,
        });
        if (!response.ok) {
          const detail = (await response.json().catch(() => ({}))) as { detail?: string };
          return { blob: null, failed: detail.detail ?? `HTTP ${response.status}`, aborted: false };
        }
        const blob = await response.blob();
        console.info(`[voice] timing: Fish audio for ${words.length} chars in `
                     + `${Math.round(performance.now() - asked)} ms`);
        return blob.size ? { blob, failed: null, aborted: false }
          : { blob: null, failed: "no audio", aborted: false };
      } catch (err) {
        if (controller.signal.aborted && !timedOut) return { blob: null, failed: null, aborted: true };
        return {
          blob: null, aborted: false,
          failed: timedOut ? "it took too long"
            : `unreachable: ${err instanceof Error ? err.message : String(err)}`,
        };
      } finally {
        window.clearTimeout(timer);
        speechFetches.current.delete(controller);
      }
    })();
  }, []);

  /** Play one Fish clip. True when it played (to the end, or until he
   *  stopped it). */
  const playVoice = useCallback(async (blob: Blob, words: string, answerVoiced: boolean): Promise<boolean> => {
    const url = URL.createObjectURL(blob);
    const element = new Audio(url);
    playing.current?.pause();
    playing.current = element;
    speaker.current = null;
    try {
      if (!playContext.current) playContext.current = new AudioContext();
      const context = playContext.current;
      if (context.state === "suspended") await context.resume().catch(() => undefined);
      // Only through a RUNNING context. An element routed into a suspended
      // one plays to the end in silence and reports success -- a voice that
      // "spoke" and was never heard. Unrouted, the element either plays or
      // is refused by the autoplay policy, and a refusal is reported below.
      if (context.state === "running") {
        const analyser = context.createAnalyser();
        analyser.fftSize = 256;
        context.createMediaElementSource(element).connect(analyser);
        analyser.connect(context.destination);
        speaker.current = analyser;
        speakerData.current = new Uint8Array(new ArrayBuffer(analyser.frequencyBinCount));
      }
    } catch {
      // No analyser: the voice still plays, the bars just stay on the mic.
      speaker.current = null;
    }
    if (!speaker.current && speakingAnswer.current !== 0
        && blindNoted.current !== speakingAnswer.current) {
      blindNoted.current = speakingAnswer.current;
      // Barge-in never fires without this reading (`bargeIn.ts`); "Tap to
      // stop" still works. Said here so a live check can see which it was.
      console.info("[voice] barge-in off for this answer: output context is",
                   playContext.current?.state ?? "missing");
    }

    speaking.current = true;
    const finished = new Promise<void>((resolve) => {
      const done = (): void => resolve();
      element.onended = done;
      element.onerror = done;
      element.onpause = done;
      window.setTimeout(done, MAX_SPEECH_MS);
    });
    let played = true;
    try {
      await element.play();
      await finished;
    } catch {
      // Autoplay policy: a page reloaded with a live session has had no
      // click yet, and the browser refuses sound until it has. Say so, and
      // let the caller fall back to the typed answer rather than waiting on
      // an `ended` that will never come.
      // The answer is kept, and the first tap or key anywhere plays it.
      played = false;
      heldBack.current = blob;
      setSpeechProblem("The browser held the voice back until the page is touched once — "
                       + "tap anywhere to hear it.");
    }
    speaking.current = false;
    speaker.current = null;
    if (playing.current === element) playing.current = null;
    URL.revokeObjectURL(url);
    if (played && answerVoiced) {
      setSpeechProblem(null);
      if (isAcknowledgement(words) && !spokenAcks.has(words)) spokenAcks.set(words, blob);
    }
    // The "couldn't reach my voice" clip is not the answer being said.
    return played && answerVoiced;
  }, []);

  /** Say a reply sentence by sentence (Miro, 2026-10-06: "should answer once
   *  I stop speaking"): every sentence goes to Fish Audio at once and the
   *  first plays as soon as its audio is back, while the rest are made.
   *  True when the reply was voiced. */
  const speak = useCallback(async (text: string): Promise<boolean> => {
    // Fish Audio is the only voice. None configured: typed, never another.
    if (!capsRef.current?.speech) return false;
    const cut = speakable(text);
    if (!cut) return false;
    // A cut answer says so, rather than trailing off -- a figure's "read at"
    // may be in the part that was not spoken.
    const words = cut.length < text.trim().length ? `${cut} The rest is on screen.` : cut;
    const run = speechRun.current;
    const parts = sentencesOf(words);
    const fetched = parts.map((part) => fetchVoice(part));
    const started = performance.now();
    let voiced = false;
    for (let i = 0; i < parts.length; i += 1) {
      if (speechRun.current !== run) break;           // he took the floor
      const got = await fetched[i]!;
      if (got.aborted || speechRun.current !== run) break;
      if (i === 0) {
        console.info(`[voice] timing: first audio ready ${Math.round(performance.now() - started)} ms `
                     + "after the answer arrived");
      }
      if (!got.blob) {
        if (i > 0) continue;                          // that sentence is on screen
        // Not even the first sentence: ATLAS's kept Fish clip says so.
        const clip = await voiceUnreachable(got.failed ?? "no audio");
        if (clip && speechRun.current === run) await playVoice(clip, parts[0]!, false);
        return false;
      }
      if (voiceDown.current) {
        voiceDown.current = false;
        console.info("[voice] Fish Audio is speaking again");
      }
      const played = await playVoice(got.blob, parts[i]!, true);
      if (!played) return voiced;
      voiced = true;
    }
    return voiced;
  }, [fetchVoice, playVoice, voiceUnreachable]);


  // --- the amplitude loop, running only while the mic is open ------------
  const poll = useCallback((): void => {
    const current = device.current;
    if (!current) return;
    const reading = current.read(BAR_COUNT);
    const out = readSpeaker();
    setBars(out ?? reading.bars);

    // Talking over an answer. `out` is null when the voice plays without an
    // analyser: then echo and Miro cannot be told apart, and it never fires.
    if (
      phaseRef.current === "responding" &&
      speaking.current &&
      speakingAnswer.current !== 0 &&
      interruptedAnswer.current !== speakingAnswer.current
    ) {
      const step = stepBargeIn(bargeIn.current, {
        now: performance.now(),
        mic: reading.level,
        speaker: peakOf(out),
        floor: noiseFloor.current,
      });
      bargeIn.current = step.state;
      if (step.interrupt) {
        // Stops the voice (settling the promise `respond` is awaiting) and
        // starts recording him now, so his first words are not lost to
        // the answer's bookkeeping.
        takeFloor("voice");
      }
    }

    if (phaseRef.current === "listening") {
      const now = Date.now();
      if (speaking.current) {
        // ATLAS is talking (the acknowledgement). His own voice is not
        // Miro's turn: the clock starts when ATLAS stops.
        turnStarted.current = now;
        quietSince.current = now;
      }
      const since = turnStarted.current ? now - turnStarted.current : 0;

      // The floor only ever falls, so a turn that opens with speech still
      // finds the room's real quiet during the first natural pause.
      noiseFloor.current = Math.min(noiseFloor.current, reading.level);
      const speakingLevel = Math.max(
        MIN_SPEAKING_LEVEL,
        noiseFloor.current * FLOOR_MULTIPLE + FLOOR_MARGIN,
      );

      // A long sentence with pauses in it is one turn, not three. End it on a
      // tail of quiet rather than a fixed timer -- but never before
      // MIN_UTTERANCE_MS, or a cough ends the turn before he has started,
      // and never after MAX_UTTERANCE_MS, or an open mic in a noisy room
      // records until the upload limit rejects it.
      if (!speaking.current && reading.level > speakingLevel) {
        quietSince.current = now;
        if (!spoke.current) spoke.current = true;
      } else if (
        spoke.current &&
        since > MIN_UTTERANCE_MS &&
        quietSince.current &&
        now - quietSince.current > SPEECH_TAIL_MS
      ) {
        quietSince.current = 0;
        void finishTurn();
      }

      if (!spoke.current) {
        if (loop.current.turns > 0 && since > FOLLOWUP_QUIET_MS) {
          // A follow-up that never came. The loop closes quietly and the
          // recording is thrown away unsent -- silence is not a question.
          quietSince.current = 0;
          endConversation("quiet");
        } else if (loop.current.turns === 0 && since > NO_SPEECH_MS) {
          // Nothing above the floor in seven seconds of the FIRST turn.
          // Either the mic is muted at the OS level, or it is picking up a
          // different device. Ending the turn produces "I didn't catch
          // anything", which is a sentence he can act on; hanging on
          // "listening" is not.
          quietSince.current = 0;
          void finishTurn();
        }
      }

      if (since > MAX_UTTERANCE_MS) {
        quietSince.current = 0;
        void finishTurn();
      }
    }
    frame.current = requestAnimationFrame(poll);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // --- opening and closing the device ------------------------------------
  const openMic = useCallback(async (): Promise<MicState> => {
    if (!device.current) device.current = new Mic();
    const state = await device.current.open();
    setMic(state);
    if (state === "listening" || state === "streaming") {
      cancelAnimationFrame(frame.current);
      frame.current = requestAnimationFrame(poll);
    }
    return state;
  }, [poll]);

  /** Release the device (tracks stopped, OS indicator dark) but leave the
   *  wake detector as it is -- pinch, for one, never needed the mic. */
  const releaseDevice = useCallback((): void => {
    cancelAnimationFrame(frame.current);
    device.current?.close();
    setMic(device.current?.state ?? "off");
    setBars(silentBars());
  }, []);

  const closeMic = useCallback((): void => {
    void detector.current?.stop();
    detector.current = null;
    releaseDevice();
  }, [releaseDevice]);

  /** The one mic a closed conversation leaves open: the stream a hands-free
   *  phrase detector reads between conversations. Anything else -- muted,
   *  click-to-talk, pinch -- lets it go. */
  const detectorKeepsDevice = (): boolean =>
    loop.current.handsFree && !!detector.current?.listensForPhrase;

  /** For an await that opened the mic and came back to find its
   *  conversation gone. `endConversation` released the device before this
   *  stream existed, so nothing else will: close it now -- unless a new
   *  conversation has already started on it, or the wake gate keeps it. */
  const releaseIfOrphaned = useCallback((): void => {
    if (loop.current.open || detectorKeepsDevice()) return;
    releaseDevice();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [releaseDevice]);

  // --- the loop ----------------------------------------------------------

  /** Close the conversation. The only way out of it, whatever closed it. */
  const endConversation = useCallback((event: LoopEvent): void => {
    const step = next(loop.current, event);
    loop.current = step.state;
    setHandsFree(step.state.handsFree);
    commitTurn();
    conversation.current += 1;
    cooldownUntil.current = Date.now() + WAKE_COOLDOWN_MS;
    speechFetch.current?.abort();
    askFetch.current?.abort();
    heldBack.current = null;
    stopSpeaking();
    // Stopped, not sent: whatever was being recorded is dropped here.
    void device.current?.stopRecording();
    setMic(device.current?.state ?? "off");
    // Hands-free with a phrase detector: the mic stays with the on-device
    // gate. Anything else -- muted, click-to-talk, pinch -- lets it go.
    if (!detectorKeepsDevice()) {
      releaseDevice();
    }
    if (rearmAfter.current && event !== "mute") {
      rearmAfter.current = false;
      loop.current = { ...loop.current, handsFree: true };
      setHandsFree(true);
    }
    if (phaseRef.current === "dormant") return;
    moveTo("dismissing");
    const closing = conversation.current;
    window.setTimeout(() => {
      if (conversation.current !== closing) return;
      moveTo("dormant");
      setTyped("");
    }, 500);
  }, [releaseDevice, stopSpeaking, moveTo, commitTurn]);

  /** Start (or restart) recording a turn. Not `wake()`: a follow-up keeps
   *  the log and the conversation it belongs to. */
  const beginListening = useCallback(async (id: number): Promise<void> => {
    quietSince.current = Date.now();
    turnStarted.current = Date.now();
    // After a barge-in he is already mid-sentence: the quiet tail may end
    // this turn, and "nothing heard" may not.
    spoke.current = floorTaken.current;
    floorTaken.current = false;
    // Starts high and is pulled down by whatever quiet actually occurs.
    noiseFloor.current = 1;

    const held = device.current?.state;
    const state = held === "listening" || held === "streaming" ? held : await openMic();
    if (conversation.current !== id) {
      releaseIfOrphaned();
      return;
    }
    if (state !== "listening" && state !== "streaming") {
      setProblem(state === "denied"
        ? await micBlockedWhy(device.current?.lastError ?? "")
        : "This device has no microphone I can use.");
      moveTo("responding");
      // Long enough to read three steps, not just the first words.
      window.setTimeout(() => {
        if (conversation.current === id) endConversation("dismiss");
      }, 15000);
      return;
    }
    if (device.current?.state !== "streaming") device.current?.startRecording();
    setMic(device.current?.state ?? "off");
    if (phaseRef.current !== "waking") moveTo("listening");
  }, [openMic, endConversation, moveTo, releaseIfOrphaned]);

  /** Show (and, when there is a voice, say) the reply, then either keep
   *  listening or close -- whichever the loop says. */
  const respond = useCallback(async (
    id: number, text: string, spoken: boolean, then: "turn_done" | LoopEvent,
    // What to say, when the server made it shorter than what is typed.
    say?: string | null,
  ): Promise<void> => {
    moveTo("responding");
    // Typed regardless, spoken when there is a voice. Listening resumes only
    // once both are over: the answer is never cut off mid-type by the next
    // turn, and ATLAS never records over his own voice -- unless he talks
    // over it, and then it stops at once and his turn starts.
    const typedBy = Date.now() + text.length * TYPE_MS;
    answers.current += 1;
    const thisAnswer = answers.current;
    answering.current = thisAnswer;
    let said = false;
    if (spoken && text) {
      // Barge-in needs the mic open while ATLAS speaks. It normally is --
      // `finishTurn` stops the recording, not the stream -- but a detector
      // restart mid-conversation can have released it. Reopened here, it is
      // released again by `endConversation` exactly as before, so the OS
      // indicator does not outlive the conversation.
      const held = device.current?.state;
      if (held !== "listening" && held !== "streaming") {
        await openMic();
        if (conversation.current !== id) {
          releaseIfOrphaned();
          return;
        }
      }
      bargeIn.current = initialBargeIn();
      speakingAnswer.current = thisAnswer;
      said = await speak(say || text);
      speakingAnswer.current = 0;
    }
    if (conversation.current !== id) {
      if (answering.current === thisAnswer) answering.current = 0;
      return;
    }
    let cut = interruptedAnswer.current === thisAnswer;
    if (!cut) {
      // Waiting for the typing to catch up -- unless he taps to stop, which
      // ends the wait at once.
      const rest = Math.max(0, typedBy - Date.now()) + (said ? 300 : TYPED_PAUSE_MS);
      await new Promise<void>((resolve) => {
        const timer = window.setTimeout(resolve, rest);
        cutWait.current = (): void => {
          window.clearTimeout(timer);
          resolve();
        };
      });
      cutWait.current = null;
      if (conversation.current !== id) {
        if (answering.current === thisAnswer) answering.current = 0;
        return;
      }
      cut = interruptedAnswer.current === thisAnswer;
    }
    if (answering.current === thisAnswer) answering.current = 0;
    if (cut) {
      // He talked over it: the whole answer is on screen and in the log,
      // and his turn starts now rather than after the typing catches up.
      setTyped(text);
    }
    commitTurn();
    // Talking over a goodbye is him not being done yet: the loop stays open
    // and listens, the same as after any other answer.
    if (then === "turn_done" || cut) {
      const step = next(loop.current, "turn_done");
      loop.current = step.state;
      if (step.go === "listening") {
        cooldownUntil.current = Date.now() + WAKE_COOLDOWN_MS;
        await beginListening(id);
      }
      return;
    }
    endConversation(then);
  }, [speak, openMic, beginListening, endConversation, moveTo, commitTurn,
      releaseIfOrphaned]);

  // --- wake --------------------------------------------------------------
  const wake = useCallback((): void => {
    if (phaseRef.current !== "dormant") return;
    // Tapping ATLAS awake while the mic is muted is him saying "listen":
    // once this conversation closes, the wake gate is re-armed so the next
    // "ATLAS" is heard without another tap. Not re-armed here -- flipping
    // hands-free restarts the detector, which would close the mic this
    // very conversation is about to record on.
    if (!loop.current.handsFree) rearmAfter.current = true;
    // Dormant is the truth: a loop still marked open here is stale, and a
    // stale "open" must not swallow the wake as a mid-conversation word.
    if (loop.current.open) loop.current = { ...loop.current, open: false, turns: 0 };
    const step = next(loop.current, "wake");
    if (step.go === "stay") return;
    loop.current = step.state;
    conversation.current += 1;
    const id = conversation.current;
    setTranscript("");
    setAnswer("");
    setTyped("");
    setProblem(null);
    setLog([]);
    // A new conversation starts with nothing drawn from the last one: no
    // stale checklist, no card for a module asked about an hour ago.
    setFocus(null);
    setKind(null);
    setCheck(null);
    pendingTurn.current = null;
    moveTo("waking");

    // Recording starts now, not after the acknowledgement: he says the wake
    // word and the question in one breath, and the first words of it must
    // not be lost to ATLAS being polite. The acknowledgement plays over the
    // start of the recording; the turn clock waits for it to finish, and
    // `stripAcknowledgement` removes it from the transcript if the echo
    // canceller let it through.
    void beginListening(id);
    if (capsRef.current?.speech) void speak(pickAcknowledgement());
    // ~700ms of ignition before the waveform takes over.
    window.setTimeout(() => {
      if (conversation.current === id && phaseRef.current === "waking") {
        moveTo("listening");
      }
    }, 700);
  }, [beginListening, speak, moveTo]);

  // --- the turn ----------------------------------------------------------
  const finishTurn = useCallback(async (): Promise<void> => {
    if (phaseRef.current !== "listening") return;
    const id = conversation.current;
    moveTo("thinking");
    setTyped("");
    stopSpeaking();
    const blob = await device.current?.stopRecording();
    setMic(device.current?.state ?? "off");
    if (conversation.current !== id) return;

    const fail = (reason: string): void => {
      setProblem(reason);
      setAnswer("");
      // Nothing usable was heard: most likely a false wake. Say why, then
      // close rather than listening again for something that is not coming.
      void respond(id, reason, false, "quiet");
    };

    if (!blob) {
      fail("I didn't catch anything — check the microphone input "
           + "device, or that this tab has permission.");
      return;
    }

    // A tap while this is being thought about drops it (`interrupt.ts`):
    // the fetches are aborted and his next turn opens instead.
    const controller = new AbortController();
    askFetch.current = controller;
    try {
      const form = new FormData();
      form.append("audio", blob, "speech.webm");
      const heard = await fetch("/api/voice/transcribe", {
        method: "POST", body: form, credentials: "same-origin",
        signal: controller.signal,
      });
      if (conversation.current !== id) return;
      if (!heard.ok) {
        const detail = (await heard.json().catch(() => ({}))) as { detail?: string };
        fail(detail.detail ?? `Transcription failed (${heard.status}).`);
        return;
      }
      const raw = String(((await heard.json()) as { transcript: string }).transcript ?? "");
      const said = stripAcknowledgement(raw);
      setTranscript(said);
      if (!said.trim()) {
        fail("I heard sound but no words. If that keeps happening, "
             + "it's usually the wrong input device.");
        return;
      }
      setProblem(null);
      setAnswer("");

      // The focus is NOT cleared here: a turn that names nothing keeps the
      // conversation's card and the navigation owed when it closes.
      setKind(null);
      setCheck(null);
      const replied = await fetch("/api/voice/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ transcript: said }),
        credentials: "same-origin",
        signal: controller.signal,
      });
      const body = (await replied.json()) as AskReply;
      if (conversation.current !== id) return;
      setFocus((previous) => carryFocus(previous, body.focus));
      setKind(body.kind ?? null);
      const drawn = body.kind === "system_check" ? body.check ?? null : null;
      setCheck(drawn);
      if (drawn) {
        // The checklist flips row by row first; the line is typed and
        // spoken once it has -- the order the check was actually made in.
        await new Promise((resolve) => window.setTimeout(resolve, checklistDelayMs(drawn)));
        if (conversation.current !== id) return;
      }
      // Tapped to stop after the reply landed but before it was said.
      if (controller.signal.aborted) throw new DOMException("interrupted", "AbortError");
      const reply = body.answer ?? null;
      const why = reply ? null : (body.error ?? "I have no answer for that.");
      if (reply) setAnswer(reply);
      else setProblem(why);
      pendingTurn.current = { said, answer: reply, problem: why, at: new Date() };
      // "Thanks, atlas" / "goodnight" is still answered -- then the loop
      // closes instead of listening again. The server's goodnight covers
      // the ways of saying it FAREWELLS does not ("I'm off to bed").
      // Said either way (2026-10-06): a turn with no answer -- no model had
      // allowance left, a check that could not run -- says why out loud,
      // in the server's own spoken line when it sent one. Never silence.
      void respond(id, reply ?? why ?? "", true,
                   isFarewell(said) || body.kind === "goodnight" ? "farewell" : "turn_done",
                   body.spoken ?? null);
    } catch (err) {
      if (conversation.current !== id) return;
      if (controller.signal.aborted) {
        // He tapped to stop while it was thinking: nothing to say, his turn.
        setAnswer("");
        setProblem(null);
        const step = next(loop.current, "turn_done");
        loop.current = step.state;
        if (step.go === "listening") await beginListening(id);
        return;
      }
      fail(err instanceof Error ? err.message : String(err));
    } finally {
      if (askFetch.current === controller) askFetch.current = null;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // --- typing the answer out --------------------------------------------
  useEffect(() => {
    if (phase !== "responding") return;
    const text = answer || problem || "";
    if (!text) return;
    setTyped("");
    let index = 0;
    const timer = window.setInterval(() => {
      index += 1;
      setTyped(text.slice(0, index));
      if (index >= text.length) window.clearInterval(timer);
    }, TYPE_MS);
    return (): void => window.clearInterval(timer);
  }, [phase, answer, problem]);

  // --- dismissal ---------------------------------------------------------
  const dismiss = useCallback((): void => {
    if (phaseRef.current === "dormant") return;
    endConversation("dismiss");
  }, [endConversation]);

  const interrupt = useCallback((): void => {
    takeFloor("tap");
  }, [takeFloor]);

  const tap = useCallback((): void => {
    switch (tapAction(phaseRef.current)) {
      case "wake":
        wake();
        return;
      case "interrupt":
        takeFloor("tap");
        return;
      case "dismiss":
        dismiss();
        return;
      case "none":
        return;
    }
  }, [wake, takeFloor, dismiss]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === "Escape") dismiss();
    };
    window.addEventListener("keydown", onKey);
    return (): void => window.removeEventListener("keydown", onKey);
  }, [dismiss]);

  // --- the always-on wake detector --------------------------------------
  useEffect(() => {
    if (!enabled || !capabilities || !handsFree) return;
    let cancelled = false;

    // A wake from the detector, as opposed to a click: ignored for a moment
    // after the loop closes, so ATLAS's own last word cannot reopen it.
    const heard = (): void => {
      if (Date.now() < cooldownUntil.current) return;
      wake();
    };

    // A detector that loaded but whose start() then rejected -- denied
    // camera or mic permission, a model fetch that 404s, a worker that
    // fails to spin up -- must not be left half-running: `detectorKind`
    // would still say "pinch" (or "porcupine") with nothing behind it,
    // which is exactly the "reads as on but is not" state this file's
    // header exists to prevent. Fall back to click-to-talk, out loud.
    const handleStartFailure = async (failed: Detector, err: unknown): Promise<void> => {
      console.warn(`${failed.kind} wake detector failed to start:`, err);
      try {
        await failed.stop();
      } catch {
        /* it already failed to start; there is nothing left to release */
      }
      if (cancelled) return;
      const fallback = fallbackAfterStartFailure(failed.kind, err);
      detector.current = createClickToTalk();
      setDetectorKind(fallback.kind);
      setDetectorClap(false);
      setDetectorProblem(fallback.problem);
      // The phrase detectors opened the app's mic for it; nothing now needs it.
      if (phaseRef.current === "dormant") releaseDevice();
    };

    // A gate that dies AFTER starting (the whisper worker crashed, or keeps
    // failing): note why, and rebuild the detector without it, so the next
    // one in the order takes over and the hint names both.
    const stoppedAfterStart = (failed: Detector, err: unknown): void => {
      if (cancelled) return;
      console.warn(`${failed.kind} wake detector stopped:`, err);
      gateStopped.current = err instanceof Error ? err.message : String(err);
      setDetectorEpoch((n) => n + 1);
    };

    void (async () => {
      // The literal string "configured" was being passed as the access key,
      // so Porcupine would have failed to initialise even with a real key
      // and a keyword file present. Picovoice keys are browser-side by
      // design; the server hands over the real one or nothing.
      const made = await createDetector(
        capabilities.wake_key ?? null,
        capabilities.speech_api_wake ?? false,
        capabilities.pinch_wake ?? false,
        (capabilities.local_wake ?? false) && gateStopped.current === null,
        capabilities.clap_wake ?? false,
      );
      if (cancelled) {
        void made.stop();
        return;
      }
      detector.current = made;
      setDetectorKind(made.kind);
      // The optimistic pre-start value: a composite reports `clap: true`
      // before it has actually tried to open its own microphone. Corrected
      // below once `start()` resolves, in case that attempt failed while
      // the detector it rides beside kept running.
      setDetectorClap(made.clap);
      setDetectorProblem(gateStopped.current === null
        ? null : gateStoppedProblem(gateStopped.current, made.kind, made.clap));

      // A clap composite over pinch forces listensForPhrase so the app's
      // Mic (and its badge) opens honestly for clap's own microphone tap --
      // but if that mic is denied, pinch itself never needed it and must
      // not be lost to a permission it never asked for. `handledByPhrase`
      // tracks whether the block below already called `made.start()`, so a
      // composite that falls through to the gesture branch is started
      // exactly once, not twice.
      let handledByPhrase = false;
      if (made.listensForPhrase) {
        // The phrase detectors are why the mic is open while dormant, so the
        // badge and the waveform show it -- whether they read the app's own
        // stream (speech-api) or hold their own (porcupine, local-whisper).
        const state = await openMic();
        if (cancelled) return;
        if (state === "listening") {
          handledByPhrase = true;
          try {
            await made.start(heard, (err) => stoppedAfterStart(made, err));
            // `start()` may have flipped `clap` to false if its own mic tap
            // was refused after the inner detector started successfully.
            if (!cancelled) setDetectorClap(made.clap);
          } catch (err) {
            await handleStartFailure(made, err);
          }
        } else if (made.clap && made.listensForGesture) {
          // The app mic is unavailable, but the inner detector (pinch)
          // never needed it -- only clap did. Drop clap rather than losing
          // pinch to a permission it never asked for; `made.start()` below
          // will see `clap` already false and skip opening a stream for it.
          made.clap = false;
          setDetectorClap(false);
        } else {
          // The composite is over porcupine or local-whisper, both of which
          // need this mic to do anything at all -- unlike pinch above,
          // there is nothing left here that could still run without it.
          // The whole detector stays inert, so clap must stop claiming to
          // ride beside it too.
          if (made.clap) {
            made.clap = false;
            setDetectorClap(false);
          }
          return;
        }
      }
      if (!handledByPhrase && made.listensForGesture) {
        // Pinch opens its own camera stream inside start() and never touches
        // the app's microphone pipeline until the gesture actually wakes
        // him -- so, unlike the phrase detectors above, it must run even
        // though it does not hold the mic.
        try {
          await made.start(heard);
          if (!cancelled) setDetectorClap(made.clap);
        } catch (err) {
          await handleStartFailure(made, err);
        }
      }
      // click-to-talk listens for neither: nothing to start, nothing held.
    })();

    return (): void => {
      cancelled = true;
      closeMic();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, capabilities, handsFree, detectorEpoch]);

  // Nothing may outlive the page: a voice mid-sentence, the playback graph.
  useEffect(() => (): void => {
    playing.current?.pause();
    void playContext.current?.close();
  }, []);

  const toggleMic = useCallback((): void => {
    // The mic badge is the MUTE control, never the wake one -- see
    // `micBadgeAction`. Waking stays with the wake word, the pinch, the
    // core and the "Talk to ATLAS" bar.
    const action = micBadgeAction({ mic, handsFree: loop.current.handsFree,
                                    detector: detectorKind });
    if (action === "mute") {
      // The loop closes, hands-free is disarmed, and the detector effect
      // stops the detector (pinch included) and every track -- at the
      // browser level, so the OS indicator goes dark too.
      endConversation("mute");
    } else if (action === "unmute") {
      // Re-arm the wake gate; the effect restarts the detector.
      loop.current = { ...loop.current, handsFree: true };
      setHandsFree(true);
    } else {
      // "Mic off" with hands-free armed: start again, and let `mic` /
      // `detectorProblem` show the real outcome (denied, unavailable).
      if (detectorKind === null || detectorKind === "click-to-talk") {
        // Nothing phrase-based to rebuild: just open the microphone.
        void openMic();
      } else {
        // A detector that did not come up: rebuild it (the effect reopens
        // the mic for the phrase detectors).
        gateStopped.current = null;
        setDetectorEpoch((n) => n + 1);
      }
    }
  }, [mic, detectorKind, endConversation, openMic]);

  return {
    phase, status: statusOf(phase), mic, detector: detectorKind, detectorClap,
    detectorProblem, handsFree, transcript, answer, typed, problem, focus, kind, check,
    speechProblem, log, bars, outputLevel,
    capabilities, wake, dismiss, interrupt, tap, toggleMic, speak,
  };
}
