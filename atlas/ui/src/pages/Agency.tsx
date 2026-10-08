import { useEffect, useRef, useState, type JSX } from "react";
import {
  api, type AgentSummary, type Meeting, type MeetingExchange, type MeetingTurn,
  type Mission,
} from "../api";
import { NeuralMap } from "../components/NeuralMap";
import { useFresh } from "../hooks/useFresh";
import { Detail, detailLinks } from "./AgentMap";
import { BrainPanel } from "./Brain";
import { FEED_SEARCH_LIMIT, LiveFeed, OrgChart, RunAllAgents } from "./AgencyTeam";
import {
  CLOSING, OPENING, mapAgents, meetingScript, nextShiftLine, playScript, provenance,
  speakOverHttp, threadsOf, type PlayResult,
} from "./agencyModel";
import { ladderWords } from "./companionModel";
import { modelState } from "./fleetModel";

/** How often the live room is rebuilt while the page is open. */
export const LIVE_REFRESH_MS = 60_000;

// Floored, like Fleet's and the map's own: a run 90s old reads "1m ago".
function rel(iso: string | null): string {
  if (!iso) return "never";
  const delta = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(Math.abs(delta) / 60000);
  const text = mins < 1 ? "<1m" : mins < 60 ? `${mins}m` : `${Math.floor(mins / 60)}h`;
  return delta >= 0 ? `${text} ago` : `in ${text}`;
}

function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour12: false });
}

const STATUS_WORD: Record<MeetingTurn["status"], string> = {
  worked: "WORKED", idle: "IDLE", blocked: "BLOCKED",
};

/** A small mark on a line a model reworded (it passed the grounding
 *  check); hovering shows the record's own words. */
function Polished({ plain }: { plain: string }): JSX.Element {
  return (
    <span className="agency-polished" title={`Reworded; the record's own words: ${plain}`}>
      {" "}reworded
    </span>
  );
}

/** The model line the old Agents cards carried: the model that answered the
 *  LAST run, never a configured pin as if it had run. */
function modelText(a: AgentSummary): string {
  switch (modelState(a)) {
    case "answered": return `${a.model} · ${rel(a.model_at)}`;
    case "silent": return `no model answered · ${rel(a.model_at)}`;
    default: return "not yet run";
  }
}

/** One agent's seat at the table: persona name, title and specialty, the
 *  day's question and its answer, then the four items with the time each
 *  figure came from -- and, from the roster, its model and autonomy. */
function Seat({
  turn, speaking, highlighted, agent,
}: {
  turn: MeetingTurn; speaking: boolean; highlighted: boolean;
  agent: AgentSummary | null;
}): JSX.Element {
  return (
    <article
      className="agency-seat"
      data-agent={turn.agent}
      data-status={turn.status}
      data-voice={turn.voice}
      data-speaking={speaking || undefined}
      data-highlighted={highlighted || undefined}
      aria-current={speaking ? "true" : undefined}
      tabIndex={-1}
    >
      <header>
        <span className="agency-avatar" aria-hidden="true" />
        <span className="agency-who">
          <span className="n">{turn.persona.name}</span>
          <span className="t">{turn.title} · {turn.voice} voice</span>
        </span>
        <span className="agency-status">{speaking ? "SPEAKING" : STATUS_WORD[turn.status]}</span>
      </header>
      <p className="agency-specialty">{turn.persona.specialty}</p>
      {turn.question && turn.answer && (
        <div className="agency-qa">
          <p className="q">{turn.question}</p>
          <p className="a" data-polished={turn.answer.polished || undefined}>
            {turn.answer.text}
            {turn.answer.polished && <Polished plain={turn.answer.plain} />}
          </p>
        </div>
      )}
      <dl className="agency-items">
        <dt>Did</dt>
        <dd>
          {turn.did.text}
          {turn.did.last_at && (
            <span className="stamp"> · last run {rel(turn.did.last_at)}</span>
          )}
        </dd>
        <dt>Issues</dt>
        <dd data-none={turn.issues.text === "none" || undefined}>
          {turn.issues.text}
          {turn.issues.last_failure_at && (
            <span className="stamp"> · last failure {rel(turn.issues.last_failure_at)}</span>
          )}
        </dd>
        <dt>Improve</dt>
        <dd>{turn.improve.text}</dd>
        <dt>Next</dt>
        <dd>{nextShiftLine(turn.next)}</dd>
        {agent && (
          <>
            <dt>Model</dt>
            <dd>{modelText(agent)}</dd>
            <dt>Autonomy</dt>
            <dd>{ladderWords(agent.ladder)} · {agent.tools} tool{agent.tools === 1 ? "" : "s"}</dd>
          </>
        )}
      </dl>
    </article>
  );
}

/** One line of the conversation: who speaks, to whom, what, when. */
function Line({ x, speaking }: { x: MeetingExchange; speaking: boolean }): JSX.Element {
  return (
    <li
      className="agency-say"
      data-kind={x.kind}
      data-speaker={x.speaker}
      data-reply-to={x.reply_to ?? undefined}
      data-speaking={speaking || undefined}
      aria-current={speaking ? "true" : undefined}
    >
      <span className="who">
        <span className="n">{x.speaker_name}</span>
        <span className="to"> → {x.to_name}</span>
      </span>
      <span className="text">
        {x.text}
        {x.polished && <Polished plain={x.plain} />}
      </span>
      <span className="stamp">read {clock(x.read_at)}</span>
    </li>
  );
}

const KIND_WORD: Record<MeetingExchange["kind"], string> = {
  follow_up: "ATLAS follows up",
  dependency: "Depends on",
  shared: "Shares a module",
};

/** The meeting's conversation as threads: each question, and under it the
 *  reply that answers it. */
export function Conversation({
  exchanges, speaking,
}: { exchanges: MeetingExchange[] | undefined; speaking: string | null }): JSX.Element {
  const threads = threadsOf(exchanges);
  return (
    <section className="agency-talk" aria-label="Conversation">
      <h3 className="label">Conversation</h3>
      {threads.length === 0 ? (
        <p className="empty">No exchanges: nothing on the record called for one.</p>
      ) : (
        <ol className="agency-threads">
          {threads.map((thread) => (
            <li className="agency-thread" key={thread[0]?.thread}
                data-kind={thread[0]?.kind}>
              <span className="agency-thread-kind">
                {thread[0] ? KIND_WORD[thread[0].kind] : ""}
              </span>
              <ol className="agency-lines">
                {thread.map((x) => <Line key={x.id} x={x} speaking={speaking === x.id} />)}
              </ol>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

/** The room itself, pure: what the page draws for a meeting, who is
 *  speaking (a script key, or null), and nothing that fetches or plays --
 *  so it renders to static markup in `Agency.test.ts`. */
export function AgencyRoom({
  meeting, speaking, highlighted = null, roster = null,
}: {
  meeting: Meeting;
  speaking: string | null;
  /** The seat picked on the agency map, if any. */
  highlighted?: string | null;
  /** `/api/agents`, for each seat's model and autonomy; null when unread. */
  roster?: AgentSummary[] | null;
}): JSX.Element {
  const c = meeting.closing.counts;
  const byName = new Map((roster ?? []).map((a) => [a.name, a]));
  return (
    <div className="agency-room" data-playing={speaking !== null || undefined}>
      <section
        className="agency-head"
        data-speaking={speaking === OPENING || undefined}
        aria-current={speaking === OPENING ? "true" : undefined}
      >
        <header>
          <span className="agency-avatar agency-avatar-atlas" aria-hidden="true" />
          <span className="agency-who">
            <span className="n">ATLAS</span>
            <span className="t">Chair · head of the table</span>
          </span>
          {speaking === OPENING && <span className="agency-status">SPEAKING</span>}
        </header>
        {meeting.theme && (
          <p className="agency-theme" data-theme={meeting.theme.key}>
            <span className="k">Today's theme</span> {meeting.theme.title}
          </p>
        )}
        <p className="agency-line">
          {meeting.opening.text}
          {meeting.opening.polished && <Polished plain={meeting.opening.plain ?? ""} />}
        </p>
        <p className="stamp">
          {provenance(meeting)} · runs from the last {meeting.window_hours} hours
        </p>
      </section>

      {meeting.turns.length > 0 ? (
        <div className="agency-table">
          {meeting.turns.map((t) => (
            <Seat key={t.agent} turn={t} speaking={speaking === t.agent}
                  highlighted={highlighted === t.agent} agent={byName.get(t.agent) ?? null} />
          ))}
        </div>
      ) : (
        <p className="empty agency-empty">
          No agents are registered, so there is no one at the table.
        </p>
      )}

      {meeting.exchanges !== undefined && (
        <Conversation exchanges={meeting.exchanges} speaking={speaking} />
      )}

      <section
        className="agency-foot"
        data-speaking={speaking === CLOSING || undefined}
        aria-current={speaking === CLOSING ? "true" : undefined}
      >
        <header>
          <span className="agency-avatar agency-avatar-atlas" aria-hidden="true" />
          <span className="agency-who">
            <span className="n">ATLAS</span>
            <span className="t">Summary</span>
          </span>
          {speaking === CLOSING && <span className="agency-status">SPEAKING</span>}
        </header>
        <p className="agency-line">{meeting.closing.text}</p>
        <div className="agency-counts">
          <span data-k="ready">{c.ready} ready</span>
          <span data-k="worked">{c.worked} worked</span>
          <span data-k="idle">{c.idle} idle</span>
          <span data-k="blocked">{c.blocked} blocked</span>
        </div>
        <p className="stamp">Read {clock(meeting.closing.read_at)}</p>
      </section>
    </div>
  );
}

/** What the error bar says when the meeting could not be read: the reason,
 *  and -- when an earlier read is still on screen -- that it is old. */
export function readNotice(error: string, previous: Meeting | null): string {
  return `Could not read the meeting: ${error}`
    + (previous ? ` — the room below is the read from ${clock(previous.read_at)}, not now.`
                : " — nothing is shown rather than a room of guesses.");
}

/** The jobs handed to agents: who took it, what he asked, where it stands.
 *  Read-only; a failed read says so rather than showing "no missions". */
export function Missions({ items, error, readAt }: {
  items: Mission[] | null; error: string | null; readAt: Date | null;
}): JSX.Element | null {
  if (error) return <p className="errorbar">Could not read the missions: {error}</p>;
  if (!items || items.length === 0) return null;
  return (
    <div className="panel agency-missions">
      <div className="inner">
        <h3 className="label">Missions</h3>
        <ul className="agency-mission-list">
          {items.map((m) => (
            <li key={m.id}>
              <strong>#{m.id} {m.persona}</strong>{" "}
              <span className="stamp">{m.status}{m.phase ? ` · ${m.phase}` : ""}
                {` · ${m.steps} step(s)`}
                {m.cards.length > 0 ? ` · card(s) ${m.cards.map((c) => `#${c}`).join(", ")}` : ""}
              </span>
              <div>“{m.request}”</div>
              {m.waiting && <div className="stamp">Waiting: {m.waiting}</div>}
              {m.report && <pre className="agency-mission-report">{m.report}</pre>}
            </li>
          ))}
        </ul>
        {readAt && <p className="stamp">Read {readAt.toLocaleTimeString()}</p>}
      </div>
    </div>
  );
}

export type RoomView = "live" | "stored";

export function Agency({ reloadKey }: { reloadKey: number }): JSX.Element {
  // Live by default, rebuilt from the records every minute while open;
  // "This morning's" is the meeting the daily step stored (and posted).
  const [view, setView] = useState<RoomView>("live");
  const meeting = useFresh(() => api.agencyMeeting(view === "live"), [reloadKey, view]);
  // The roster: the map's synapses (handoffs and memory scopes are not in
  // the meeting), each seat's model and autonomy, and the org chart.
  const roster = useFresh(() => api.agents(), [reloadKey]);
  const feed = useFresh(() => api.agentsFeed(FEED_SEARCH_LIMIT), [reloadKey]);
  const missions = useFresh(() => api.missions(), [reloadKey]);
  // The Brain: departments, their last 7 days and every seat's SOP.
  const brain = useFresh(() => api.agencyBrain(), [reloadKey]);
  const [speaking, setSpeaking] = useState<string | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [playNote, setPlayNote] = useState<string | null>(null);
  // Non-fatal: a persona voice the server does not have yet (X-Voice said
  // ATLAS spoke instead). The meeting keeps playing; the page says so.
  const [voiceNotes, setVoiceNotes] = useState<string[]>([]);
  const abort = useRef<AbortController | null>(null);

  // Leaving the page stops the meeting: abort the fetch, pause the audio.
  useEffect(() => () => abort.current?.abort(), []);

  // Live updates: rebuilt every minute while the page is open -- never
  // while the meeting is playing, so the room does not change mid-sentence.
  const reloadMeeting = meeting.reload;
  useEffect(() => {
    if (view !== "live" || playing) return;
    const id = window.setInterval(reloadMeeting, LIVE_REFRESH_MS);
    return () => window.clearInterval(id);
  }, [view, playing, reloadMeeting]);

  async function play(): Promise<void> {
    const m = meeting.data;
    if (!m) return;
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;
    setPlayNote(null);
    setVoiceNotes([]);
    setPlaying(true);
    const result: PlayResult = await playScript(
      meetingScript(m), speakOverHttp, setSpeaking, controller.signal,
      (note) => setVoiceNotes((cur) => (cur.includes(note) ? cur : [...cur, note])));
    if (abort.current === controller) abort.current = null;
    setPlaying(false);
    if (result.kind === "failed") {
      const who = result.at === OPENING || result.at === CLOSING ? "ATLAS"
        : m.turns.find((t) => t.agent === result.at)?.persona.name
          ?? m.exchanges?.find((x) => x.id === result.at)?.speaker_name ?? result.at;
      setPlayNote(`The meeting stopped at ${who}: ${result.reason}`);
    }
  }

  // A neuron picked on the map: hold it, show its details under the map,
  // and bring that agent's seat into view -- instantly under reduced motion.
  function pick(name: string): void {
    const next = picked === name ? null : name;
    setPicked(next);
    if (!next) return;
    const seat = document.querySelector<HTMLElement>(
      `.agency-seat[data-agent="${CSS.escape(next)}"]`);
    if (!seat) return;
    const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    seat.scrollIntoView({ behavior: still ? "auto" : "smooth", block: "center" });
    seat.focus({ preventScroll: true });
  }

  function stop(): void {
    abort.current?.abort();
    abort.current = null;
  }

  const data = meeting.data;
  const agents = roster.data?.agents ?? null;
  const rosterFailed = roster.error !== null;
  const mapped = mapAgents(data, agents);
  const pickedAgent = picked && agents ? agents.find((a) => a.name === picked) ?? null : null;
  const agentSpeaking = speaking && data?.turns.some((t) => t.agent === speaking)
    ? speaking : null;
  const lineSpeaking = speaking ? data?.exchanges?.find((x) => x.id === speaking) : undefined;
  const coreSpeaking = speaking === OPENING || speaking === CLOSING
    || lineSpeaking?.speaker === "atlas";
  const speakerName = speaking === OPENING || speaking === CLOSING ? "ATLAS"
    : lineSpeaking?.speaker_name
      ?? data?.turns.find((t) => t.agent === agentSpeaking)?.persona.name ?? null;
  const neuronSpeaking = agentSpeaking
    ?? (lineSpeaking && lineSpeaking.speaker !== "atlas" ? lineSpeaking.speaker : null);
  return (
    <>
      <h2 className="label">Agency</h2>
      <p className="stamp" style={{ marginBottom: 12 }}>
        {meeting.fetchedAt ? `Read ${meeting.fetchedAt.toLocaleTimeString()}` : "Reading…"}
        {data && ` · ${data.turns.length} agents meet with ATLAS`}
        {view === "live" && " · refreshes every minute"}
      </p>

      <BrainPanel brain={brain.data} error={brain.error} fetchedAt={brain.fetchedAt} />

      {meeting.error && <p className="errorbar">{readNotice(meeting.error, data)}</p>}
      {playNote && <p className="errorbar">{playNote}</p>}
      {voiceNotes.map((n) => <p className="stamp agency-voice-note" key={n}>{n}</p>)}

      <div className="agency-controls">
        <div className="agency-view" role="group" aria-label="Which meeting">
          <button type="button" className="btn" aria-pressed={view === "live"}
                  onClick={() => setView("live")} disabled={playing}>
            Live
          </button>
          <button type="button" className="btn" aria-pressed={view === "stored"}
                  onClick={() => setView("stored")} disabled={playing}>
            This morning's
          </button>
        </div>
        {playing ? (
          <button type="button" className="btn" onClick={stop}>■ Stop</button>
        ) : (
          <button type="button" className="btn" onClick={() => void play()}
                  disabled={!data || data.turns.length === 0}>
            ▶ Play meeting
          </button>
        )}
        <button type="button" className="btn" onClick={() => meeting.reload()}
                disabled={playing || meeting.loading}>
          Reload
        </button>
        <RunAllAgents roster={roster} reload={roster.reload} />
        <span className="stamp">
          ATLAS speaks in his own voice; each agent in its own female or male voice.
        </span>
      </div>

      {(mapped.length > 0 || roster.data) && (
        <div className="panel agency-map">
          <div className="inner">
            <header className="agency-map-head">
              <h3 className="label">Agency map</h3>
              <span className="stamp" aria-live="polite">
                {speakerName ? `${speakerName} is speaking` : "Pick an agent to see its connections, its details and its turn"}
              </span>
            </header>
            {roster.error && (
              <p className="errorbar">
                Could not read the roster: {roster.error}
                {" — every neuron is drawn UNKNOWN and no connections are shown."}
              </p>
            )}
            <NeuralMap agents={mapped} readFailed={rosterFailed} selected={picked}
                       onActivate={pick} speaking={neuronSpeaking}
                       coreSpeaking={coreSpeaking} speakingKey label="Agency map" />
            {pickedAgent && agents && (
              <Detail agent={pickedAgent} links={detailLinks(pickedAgent.name, agents)}
                      onWarmed={() => roster.reload()} />
            )}
            {roster.fetchedAt && (
              <p className="stamp">Roster read {roster.fetchedAt.toLocaleTimeString()}</p>
            )}
          </div>
        </div>
      )}

      <Missions items={missions.data?.missions ?? null} error={missions.error}
                readAt={missions.fetchedAt} />

      {data ? (
        <AgencyRoom meeting={data} speaking={speaking} highlighted={picked}
                    roster={rosterFailed ? null : agents} />
      ) : (
        !meeting.error && <p className="empty">Gathering the team…</p>
      )}

      <div style={{ marginTop: 14 }}>
        {agents && !rosterFailed && (
          <OrgChart roster={agents} feed={feed} reloadKey={reloadKey} />
        )}
        <LiveFeed feed={feed} />
      </div>
    </>
  );
}
