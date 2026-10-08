import { Suspense, lazy, useCallback, useEffect, useState, type JSX } from "react";
import { api } from "../api";
import type { Turn, useVoice } from "../voice/useVoice";
import { keepSpoken, pocketStatus, pocketTurns } from "./pocketModel";
import { tapLabel } from "../voice/interrupt";

const Bust = lazy(() =>
  import("../core/Bust").then((m) => ({ default: m.Bust })));

/**
 * The pocket: ATLAS in the Mac menu bar (bootstrap/mac/menubar, "ATLAS
 * Menu"). Miro, 2026-09-29: "make it simple and fit to screen, no need to
 * scroll, just the humanoid and that's it, with chat available, and voice
 * of course."
 *
 * Three bands in one screen that never scrolls: the humanoid (tap it to
 * talk), the latest exchange, and one row with a text box and a round mic.
 * Only the answer text scrolls, inside its own box. Typing and speaking go
 * through the same doors as the dashboard (`/api/voice/ask`, the voice
 * loop, `/api/voice/speak`); this page adds no path of its own.
 */
export function Pocket({ voice }: { voice: ReturnType<typeof useVoice> }): JSX.Element {
  const [typed, setTyped] = useState<Turn[]>([]);
  const [spoken, setSpoken] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [asking, setAsking] = useState<string | null>(null);
  const [speakingTyped, setSpeakingTyped] = useState(false);

  // `wake()` empties voice.log for each new conversation; the pocket keeps
  // the spoken turns so the latest one stays on screen after it ends.
  useEffect(() => {
    setSpoken((kept) => keepSpoken(kept, voice.log));
  }, [voice.log]);

  const ask = useCallback((question: string): void => {
    const q = question.trim();
    if (!q || asking !== null) return;
    setAsking(q);
    setDraft("");
    const at = new Date();
    api.ask(q)
      .then((body) => {
        setTyped((t) => [...t, {
          said: q, at, answer: body.answer || null,
          problem: body.answer ? null : body.error ?? "I have no answer for that.",
        }]);
        if (body.answer && voice.capabilities?.speech) {
          setSpeakingTyped(true);
          void voice.speak(body.spoken || body.answer).finally(() => setSpeakingTyped(false));
        }
      })
      .catch((err: unknown) => {
        setTyped((t) => [...t, {
          said: q, at, answer: null,
          problem: err instanceof Error ? err.message : String(err),
        }]);
      })
      .finally(() => setAsking(null));
  }, [voice, asking]);

  const status = pocketStatus(voice.phase, asking !== null, speakingTyped);
  const [last] = pocketTurns(spoken, typed, 1);
  const voiceLive = voice.phase !== "dormant" && voice.phase !== "dismissing";
  const talking = voice.phase !== "dormant";

  // What the middle band shows: the exchange happening now, else the last one.
  let said: string | null = last?.said ?? null;
  let answer: string | null = last?.answer ?? null;
  let problem: string | null = last?.problem ?? null;
  let pending = false;
  if (asking !== null) {
    said = asking; answer = null; problem = null; pending = true;
  } else if (voiceLive && voice.transcript) {
    said = voice.transcript; answer = voice.typed || null; problem = null;
    pending = !voice.typed;
  }

  const micWhy = voice.problem
    ?? (voice.mic === "denied" ? "The microphone is blocked. Tap the mic to see why."
      : voice.mic === "unavailable" ? "No microphone on this device." : null);

  return (
    <div className="pocket">
      <section className="pk-stage">
        <Suspense fallback={<div className="bust" aria-hidden="true" />}>
          <Bust phase={voice.phase} bars={voice.bars}
                onWake={voice.phase === "dormant" ? voice.wake : voice.interrupt}
                gaze={null} />
        </Suspense>
        <span className="pk-name">Cabled ATLAS</span>
        <span className="pk-state" data-status={status}>
          <span className="d" aria-hidden="true" />{status}
        </span>
      </section>

      <section className="pk-chat" aria-label="Conversation" aria-live="polite">
        {/* The mic's own reason, in full: which of browser, macOS or another
            app is holding it, and where the switch is. */}
        {micWhy && <p className="pk-problem" role="alert">{micWhy}</p>}
        {voice.speechProblem && <p className="pk-note">{voice.speechProblem}</p>}
        {said === null && !micWhy && (
          <p className="pk-empty">Tap ATLAS or the mic to talk, or type below.</p>
        )}
        {said !== null && <p className="pk-you">{said}</p>}
        {said !== null && (
          <div className="pk-answer" data-kind={problem ? "problem" : pending ? "pending" : "answer"}>
            {answer ?? problem ?? (pending ? "Thinking…" : "")}
          </div>
        )}
      </section>

      <form className="pk-bar" onSubmit={(e) => { e.preventDefault(); ask(draft); }}>
        <input value={draft} onChange={(e) => setDraft(e.target.value)}
               placeholder="Type to ATLAS, Enter to send" aria-label="Type to ATLAS"
               disabled={asking !== null} autoFocus />
        <button type="button" className="pk-mic" data-phase={voice.phase} data-mic={voice.mic}
                aria-pressed={talking}
                aria-label={tapLabel(voice.phase) ?? (talking ? "Stop" : "Tap to talk")}
                title={talking ? "Tap to stop" : "Tap to talk"}
                onClick={voice.tap}>
          <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
            {talking
              ? <rect x="7" y="7" width="10" height="10" rx="1.5" fill="currentColor" />
              : <path fill="currentColor" d="M12 14a3 3 0 0 0 3-3V5a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11h-2Z" />}
          </svg>
        </button>
      </form>
    </div>
  );
}
