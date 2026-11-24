import { Suspense, lazy, useCallback, useState, type JSX } from "react";
import { api } from "../api";
import { useFresh } from "../hooks/useFresh";
import { Panel } from "../components/Panel";
import { Dial } from "../components/Dial";
import { Tile } from "../components/Tile";
import { Brain } from "../core/Brain";
import { current, llmTile, memoryTile, regionsTile } from "../core/core-tiles";
import type { useVoice } from "../voice/useVoice";
import { statusOf, type Phase } from "../voice/loop";
import { SpeakingSparks } from "../voice/SpeakingSparks";
import {
  AnswerExtras, MuteButton, StopButton, TranscriptLog, VoiceHints,
} from "../voice/VoiceOverlay";

// Lazy, for the same reason the wake detector is: `three` is over three
// megabytes, and it must cost nothing on the nine screens that are only being
// read. The stage is the one page that needs it.
const Bust = lazy(() =>
  import("../core/Bust").then((m) => ({ default: m.Bust })));

/**
 * The AI core: the humanoid stage, and the brain behind it.
 *
 * The figure is not an avatar with a mood. It has four states and each one is
 * something that is actually true of the system at that moment — IDLE is a
 * closed microphone, LISTENING is an open one, THINKING is a request in
 * flight, SPEAKING is an answer being read out with the amplitude the device
 * is measuring. Nothing on this screen animates to look busy.
 *
 * The brain beside it is the memory graph of what ATLAS remembers and what it
 * has actually recalled, not a neural network diagram. Every figure on it
 * carries the time it was read.
 */

// The phase-to-status mapping lives in voice/loop.ts, shared with the voice
// overlay's pill: one conversation loop, one status, however many places
// draw it.

const EQ_BARS = 24;

function readStamp(at: Date | null): string {
  return at ? `read ${at.toLocaleTimeString([], { hour12: false })}` : "not read yet";
}

export function Core({ reloadKey, voice, gaze = null }: {
  reloadKey: number;
  voice: ReturnType<typeof useVoice>;
  /** The cursor a tracked hand is steering, normalised 0..1, or null when
   *  no hand is currently in control of it. The figure's eyes lean toward
   *  it — a real position when there is one, dead ahead otherwise. */
  gaze?: { x: number; y: number } | null;
}): JSX.Element {
  const brain = useFresh(() => api.brain(), [reloadKey]);
  const mem = useFresh(() => api.memory(), [reloadKey]);
  const llm = useFresh(() => api.llm(), [reloadKey]);
  const sys = useFresh(() => api.system(), [reloadKey]);

  // The typed question's answer. Kept here rather than inside the brain panel
  // because it belongs on the stage's own line: typing and speaking end in
  // the same place, which is the point of the input existing at all.
  const [asking, setAsking] = useState(false);
  const [said, setSaid] = useState<string | null>(null);
  const [answer, setAnswer] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const ask = useCallback((question: string): void => {
    setAsking(true);
    setSaid(question);
    setAnswer(null);
    setProblem(null);
    api.ask(question)
      .then((body) => {
        if (body.answer) {
          setAnswer(body.answer);
          // Typed in, but heard back: the same voice as a spoken turn, when
          // there is one. He asked to hear him, not only read him.
          if (voice.capabilities?.speech) void voice.speak(body.spoken || body.answer);
        } else setProblem(body.error ?? "I have no answer for that.");
      })
      .catch((err: unknown) => {
        setProblem(err instanceof Error ? err.message : String(err));
      })
      .finally(() => setAsking(false));
  }, [voice]);

  // A typed question in flight is THINKING as surely as a spoken one: the
  // stage reports the request, not which device it came from.
  const phase: Phase = asking ? "thinking" : voice.phase;
  const status = statusOf(phase);
  // Only a fresh, successful read counts. On a failed refresh useFresh keeps
  // the previous data, and a figure from then is not a figure about now —
  // every tile below goes through `current()` for that reason.
  const brainRead = current(brain);
  const llmWords = llmTile(llm);
  const memWords = memoryTile(mem);
  const regionWords = regionsTile(brain);

  // The voice's own line wins while a conversation is happening — what he
  // just said out loud is more current than what he typed a minute ago.
  //
  // Only the exchange in progress, though: once a spoken turn is answered it
  // moves into the conversation log under the stage, and the loop goes back
  // to listening with the line clear for the next one.
  const voiceLive = voice.phase === "thinking" || voice.phase === "responding";
  const hudLine = (voiceLive ? voice.typed : "")
    || (asking ? `asking — “${said ?? ""}”` : null)
    || answer
    || problem
    || null;

  return (
    <div className="corepage">
      {/* --- the gauges, as in the reference: around the figure ---------- */}
      <div className="core-side">
        <Panel title="AI core">
          <div className="mc-tiles">
            <Tile glyph="◈" name="LLM" {...llmWords} readAt={llm.fetchedAt} />
            <Tile glyph="⊚" name="Memory" {...memWords} readAt={mem.fetchedAt}
                  to="/memory" />
            <Tile glyph="◍" name="Voice" state={voice.mic === "denied"
                    || voice.mic === "unavailable" ? "unknown" : "live"}
                  value={status}
                  // Not fetched: the phase is read from the voice machine on
                  // this render, so the render is when it was read.
                  readAt={new Date()}
                  note="live device state, not a fetched figure" />
            <Tile glyph="⁂" name="Regions" {...regionWords} readAt={brain.fetchedAt} />
          </div>
        </Panel>

        <Panel title="Memory counts"
               unavailable={mem.error ? `Counts unreadable: ${mem.error}` : null}>
          <div className="memgrid">
            {Object.entries(mem.data?.kinds ?? {}).map(([kind, n]) => (
              <span className="corecount" key={kind}><b>{n}</b>{kind}</span>
            ))}
            <span className="corecount">
              <b>{mem.data?.voice_turns ?? "—"}</b>voice turns
            </span>
          </div>
          <p className="stamp mc-stamp">{readStamp(mem.fetchedAt)}</p>
        </Panel>

        <Panel title="System"
               unavailable={sys.error ? `Could not read the host: ${sys.error}` : null}>
          <div className="dials">
            <Dial percent={sys.data?.cpu_percent ?? null} label="CPU" />
            <Dial percent={sys.data?.memory.percent ?? null} label="RAM" />
            <Dial percent={sys.data?.disk.percent ?? null} label="Disk" />
          </div>
          <p className="stamp mc-stamp">{readStamp(sys.fetchedAt)}</p>
        </Panel>
      </div>

      {/* --- the stage --------------------------------------------------- */}
      <div className="core-stage">
        <div className="core-figure">
          <Suspense fallback={<div className="bust" aria-hidden="true" />}>
            <Bust phase={phase} bars={voice.bars}
                  onWake={voice.phase === "dormant" ? voice.wake : voice.interrupt}
                  gaze={gaze} />
          </Suspense>
          {/* Sparks over the figure while he speaks, as loud as the real clip. */}
          <SpeakingSparks voice={voice} origin={{ x: 0.5, y: 0.36, r: 0.13 }} />
        </div>

        <div className="core-hud">
          <span className="hud-status" data-status={status}>
            <span className="dot" aria-hidden="true" />
            STATUS: {status}
          </span>
          <span className="hud-eq" data-flat={String(status === "THINKING")}
                aria-hidden="true">
            {Array.from({ length: EQ_BARS }, (_, i) => (
              <span key={i} className="b" style={{
                ["--h" as string]: `${Math.round((voice.bars[i] ?? 0) * 100)}%`,
                ["--i" as string]: String(i),
              }} />
            ))}
          </span>
          <StopButton voice={voice} />
          <MuteButton voice={voice} />
          <span className="hud-id">CABLED ATLAS</span>
        </div>

        {voiceLive && voice.transcript && (
          <p className="core-said">“{voice.transcript}”</p>
        )}
        {hudLine && (
          <p className={`core-answer${problem && !answer ? " problem" : ""}`}>
            {hudLine}
          </p>
        )}
        {/* The system check's checklist, or the named module's card --
            the overlay draws them elsewhere, and draws nothing here. */}
        {voice.phase !== "dormant" && <AnswerExtras voice={voice} />}
        {/* The overlay draws nothing on this page, so the lines it would
            draw under its core -- which detector is live, muted, the
            detector's problem, what is not configured -- are drawn here,
            by the same component. */}
        <VoiceHints voice={voice} always />
        <TranscriptLog turns={voice.log} />
        {voice.speechProblem && <p className="stamp">{voice.speechProblem}</p>}
      </div>

      {/* --- the brain ---------------------------------------------------- */}
      <div className="core-side">
        <Panel title="Brain"
               right={brainRead ? `${brainRead.regions.length} regions` : ""}>
          <Brain regions={brainRead?.regions ?? null}
                 recent={brainRead?.recent ?? null}
                 error={brain.error} onAsk={ask} asking={asking} />
          <p className="stamp mc-stamp">{readStamp(brain.fetchedAt)}</p>
        </Panel>
      </div>
    </div>
  );
}
