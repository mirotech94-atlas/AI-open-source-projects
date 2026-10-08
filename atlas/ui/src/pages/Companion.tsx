import { Suspense, lazy, useCallback, useEffect, useState, type JSX } from "react";
import { Link } from "react-router-dom";
import { api, type AgentSummary } from "../api";
import { useFresh } from "../hooks/useFresh";
import { Mark } from "../components/Mark";
import { Tile } from "../components/Tile";
import type { Turn, useVoice } from "../voice/useVoice";
import { statusOf, type Phase } from "../voice/loop";
import { AnswerExtras, MuteButton, StopButton, VoiceHints } from "../voice/VoiceOverlay";
import { tapLabel } from "../voice/interrupt";
import { AgentMap, cardName } from "./AgentMap";
import { companionTiles, ladderWords } from "./companionModel";

const Bust = lazy(() =>
  import("../core/Bust").then((m) => ({ default: m.Bust })));

/**
 * The companion: ATLAS in its own window (/Applications/ATLAS.app opens
 * this page as a Chrome app). Miro, 2026-09-28: "a downloadable application
 * that shows the humanoid talking to me and a few data and agent modes on
 * the side".
 *
 * The figure in the middle, the conversation on the right, the agents on
 * the left, and three figures under the figure -- each one a check's own
 * reading with the time it was read. Everything here reads; a Mac action
 * spoken here runs through the same voice door as the dashboard, nothing
 * else.
 */

function Clock(): JSX.Element {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = window.setInterval(() => setNow(new Date()), 1000);
    return (): void => window.clearInterval(t);
  }, []);
  return (
    <span className="cp-clock">
      {now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
      <i>{now.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" })}</i>
    </span>
  );
}

function AgentRow({ agent, on, onPick }: {
  agent: AgentSummary; on: boolean; onPick: (name: string) => void;
}): JSX.Element {
  const state = !agent.ready ? "blocked" : agent.runs_24h > 0 ? "working" : "idle";
  return (
    <li>
      <button className="cp-agent" data-on={String(on)} data-state={state}
              onClick={() => onPick(agent.name)}>
        <span className="dot" aria-hidden="true" />
        <span className="who">
          <b>{agent.persona?.name ?? cardName(agent)}</b>
          <i>{agent.title}</i>
        </span>
        <span className="st">{state}</span>
      </button>
      {on && (
        <div className="cp-agent-detail">
          <p>{agent.brief}</p>
          <p className="ladder">{ladderWords(agent.ladder)}</p>
          {!agent.ready && agent.reasons.length > 0 && (
            <p className="why">Blocked: {agent.reasons.join("; ")}</p>
          )}
          <p className="stamp">
            {agent.last_run_at
              ? `last ran ${new Date(agent.last_run_at).toLocaleString()} · ${agent.last_outcome ?? "no outcome"}`
              : "has not run yet"}
            {agent.next_shift_at
              && ` · next shift ${new Date(agent.next_shift_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`}
          </p>
        </div>
      )}
    </li>
  );
}

export function Companion({ voice, reloadKey }: {
  voice: ReturnType<typeof useVoice>;
  reloadKey: number;
}): JSX.Element {
  const overview = useFresh(() => api.overview(), [reloadKey]);
  const counters = useFresh(() => api.status(), [reloadKey]);
  const roster = useFresh(() => api.agents(), [reloadKey]);
  const [view, setView] = useState<"atlas" | "map">("atlas");
  const [picked, setPicked] = useState<string | null>(null);

  // Typed turns, kept beside the spoken ones: typing and speaking end in the
  // same conversation.
  const [typed, setTyped] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [asking, setAsking] = useState(false);

  const ask = useCallback((question: string): void => {
    const q = question.trim();
    if (!q) return;
    setAsking(true);
    setDraft("");
    const at = new Date();
    api.ask(q)
      .then((body) => {
        setTyped((t) => [...t, {
          said: q, at, answer: body.answer || null,
          problem: body.answer ? null : body.error ?? "I have no answer for that.",
        }]);
        if (body.answer && voice.capabilities?.speech) void voice.speak(body.spoken || body.answer);
      })
      .catch((err: unknown) => {
        setTyped((t) => [...t, {
          said: q, at, answer: null,
          problem: err instanceof Error ? err.message : String(err),
        }]);
      })
      .finally(() => setAsking(false));
  }, [voice]);

  const phase: Phase = asking ? "thinking" : voice.phase;
  const status = statusOf(phase);
  const turns = [...voice.log, ...typed].sort((a, b) => a.at.getTime() - b.at.getTime());
  const voiceLive = voice.phase === "thinking" || voice.phase === "responding";
  const tiles = companionTiles(overview.error ? null : overview.data?.modules ?? null);
  const waiting = counters.error ? null : counters.data?.waiting_on_you ?? null;
  const agents = roster.data?.agents ?? [];

  return (
    <div className="companion">
      <header className="cp-top">
        <span className="cp-brand"><Mark size={20} /> ATLAS</span>
        <span className="cp-status" data-status={status}>
          <span className="d" aria-hidden="true" />{status}
        </span>
        <Clock />
        <Link className="cp-full" to="/">Full dashboard ›</Link>
      </header>

      <aside className="cp-left" aria-label="Agents">
        <div className="cp-modes" role="tablist">
          <button role="tab" aria-selected={view === "atlas"} onClick={() => setView("atlas")}>
            ATLAS
          </button>
          <button role="tab" aria-selected={view === "map"} onClick={() => setView("map")}>
            Agents at work
          </button>
        </div>
        {roster.error && <p className="cp-unknown">Agents unreadable: {roster.error}</p>}
        <ul className="cp-agents">
          {agents.map((a) => (
            <AgentRow key={a.name} agent={a} on={picked === a.name}
                      onPick={(n) => setPicked((cur) => (cur === n ? null : n))} />
          ))}
        </ul>
        {roster.fetchedAt && (
          <p className="stamp">read {roster.fetchedAt.toLocaleTimeString([], { hour12: false })}</p>
        )}
      </aside>

      <main className="cp-stage">
        {view === "map" ? (
          <div className="cp-map">
            <AgentMap agents={agents} readFailed={Boolean(roster.error)}
                      onSelect={(n) => setPicked(n)} />
          </div>
        ) : (
          <Suspense fallback={<div className="bust" aria-hidden="true" />}>
            <Bust phase={phase} bars={voice.bars}
                  onWake={voice.phase === "dormant" ? voice.wake : voice.interrupt} />
          </Suspense>
        )}
        {voiceLive && voice.transcript && <p className="core-said">“{voice.transcript}”</p>}
        {voiceLive && voice.typed && <p className="core-answer">{voice.typed}</p>}
        {voice.phase !== "dormant" && <AnswerExtras voice={voice} />}
        <VoiceHints voice={voice} always />

        <div className="cp-tiles">
          <Tile glyph="☑" name="Waiting on you"
                value={waiting === null ? "unknown" : String(waiting)}
                state={waiting === null ? "unknown" : waiting > 0 ? "warn" : "live"}
                readAt={counters.error ? null : counters.fetchedAt} to="/tasks" />
          {tiles.map((t) => (
            <Tile key={t.key} glyph={t.glyph} name={t.name} value={t.value}
                  state={t.state} readAt={t.readAt} to={t.to} />
          ))}
        </div>
      </main>

      <aside className="cp-right" aria-label="Conversation">
        <ol className="cp-log">
          {turns.length === 0 && <li className="cp-empty">Say “ATLAS”, or type below.</li>}
          {turns.map((t, i) => (
            <li key={i}>
              <p className="you">{t.said}</p>
              {t.answer && <p className="atlas">{t.answer}</p>}
              {t.problem && <p className="atlas problem">{t.problem}</p>}
              <span className="stamp">{t.at.toLocaleTimeString([], { hour12: false })}</span>
            </li>
          ))}
        </ol>
        <form className="cp-input" onSubmit={(e) => { e.preventDefault(); ask(draft); }}>
          <button type="button" className="cp-mic" data-phase={voice.phase}
                  onClick={voice.tap}
                  title={tapLabel(voice.phase) ?? (voice.phase === "dormant" ? "Talk" : "Stop")}>◍</button>
          <input value={draft} onChange={(e) => setDraft(e.target.value)}
                 placeholder="You're talking to ATLAS…" aria-label="Type to ATLAS"
                 disabled={asking} />
          <StopButton voice={voice} />
          <MuteButton voice={voice} />
        </form>
      </aside>
    </div>
  );
}
