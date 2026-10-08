import {
  Suspense, lazy, useEffect, useState, type JSX, type ReactNode,
} from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import {
  api, normaliseHealth, type Ambient, type Health, type Overview, type StatusCounters,
} from "../api";
import { MoreMenu, type MoreItem } from "../components/Shell";
import { useFresh, type Fresh } from "../hooks/useFresh";
import type { useVoice } from "../voice/useVoice";
import { statusOf } from "../voice/loop";
import { talkCaption } from "../voice/detectorCore";
import { tapAction } from "../voice/interrupt";
import { MuteButton, StopButton } from "../voice/VoiceOverlay";
import { AgentMap } from "./AgentMap";
import { mapAgents } from "./agencyModel";
import { left } from "./tasksModel";
import {
  FigurePartView, ModelsLegend, WhenVisible, useFreshlyRead,
} from "./Home";
import {
  STATUS_WORDS, flowHealth, flowRows, hhmm, marketingFigures, readOf, rel, waitingCards,
  weekDays, WEEK_HOURS,
  type Figure,
} from "./homeModel";
import { healthBars, modelBadge, modelLegend } from "./liveModel";
import { HealthBars } from "../components/Spark";
import {
  coreStates, lastAnswered, llmService, overall, providersReady, services, statusPill,
  storage, uptimeWords, type CoreStep, type Overall, type Service,
} from "./commandModel";
import type { NodeModel } from "../components/NeuralMap";

// `three` is megabytes: the figure loads only once its stage is on screen.
const Bust = lazy(() => import("../core/Bust").then((m) => ({ default: m.Bust })));

/**
 * ATLAS · Command Center -- the home page (Miro, 2026-10-06: "make the
 * ATLAS frontend tech based ... include all necessary things ... if
 * something is not needed to check all the time don't put it").
 *
 * Reads only. Every figure carries the time it was read; anything that
 * could not be read says Unknown in grey, dashed and unlit, and never
 * shares a treatment with OK (invariants 6 and 7). Nothing here can act:
 * the quick actions open a screen or start the existing voice loop.
 */

type Voice = ReturnType<typeof useVoice>;

function stamp(at: Date | string | null, failed = false): string {
  if (failed) return "read failed";
  if (!at) return "not read yet";
  return `read ${hhmm(at)}`;
}

/** A framed panel: corner ticks, a small-caps label, the read time. */
function CPanel({ id, title, at, more, children, className = "" }: {
  id: string;
  title: string;
  at?: string;
  more?: { to: string; label: string };
  children: ReactNode;
  className?: string;
}): JSX.Element {
  return (
    <section className={`cmd-panel cmd-${id} ${className}`} aria-labelledby={`cmd-${id}-h`}>
      <header>
        <h2 id={`cmd-${id}-h`}>{title}</h2>
        {at && <span className="cmd-at">{at}</span>}
        {more && <Link className="cmd-more" to={more.to}>{more.label}</Link>}
      </header>
      {children}
    </section>
  );
}

function Unread({ children }: { children: ReactNode }): JSX.Element {
  return (
    <p className="cmd-unread" data-health="unknown">
      <span className="cmd-word-unknown">Unknown</span> <span>{children}</span>
    </p>
  );
}

// -------------------------------------------------------------- header

function Clock(): JSX.Element {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return (): void => window.clearInterval(id);
  }, []);
  return (
    <span className="cmd-clock" aria-label="Local time">
      <b>{now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false })}</b>
      <span>{now.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short", year: "numeric" })}</span>
    </span>
  );
}

export function CommandMark({ size = 30 }: { size?: number }): JSX.Element {
  return (
    <svg viewBox="0 0 32 32" width={size} height={size} role="img" aria-label="ATLAS"
         className="cmd-mark">
      <rect x="1" y="1" width="30" height="30" rx="2" fill="none" />
      <g fill="none" strokeLinecap="square" strokeWidth={1.8}>
        <circle cx="16" cy="8.6" r="2.6" />
        <path d="M7 25.4L13.2 13.6M25 25.4L18.8 13.6M9.8 20.2H22.2" />
      </g>
    </svg>
  );
}

export function CommandHeader({ items, overview, waiting, feed, onSignOut, voice }: {
  items: readonly MoreItem[];
  overview: Fresh<Overview | null>;
  waiting: number | null;
  feed: "live" | "connecting" | "dropped";
  onSignOut: () => void;
  voice: Voice;
}): JSX.Element {
  const read = readOf(overview);
  const o = overall(read.error ? null : read.data?.modules ?? null);
  const pill = statusPill(o, read.error !== null || !read.data);
  return (
    <header className="cmd-top">
      <Link to="/" className="cmd-brand" aria-label="ATLAS Command Center, home">
        <CommandMark />
        <span><b>ATLAS</b><small>Command Center</small></span>
      </Link>
      <nav className="cmd-tabs" aria-label="Main">
        <NavLink to="/" end className={({ isActive }) => (isActive ? "on" : "")}>Command Center</NavLink>
        <NavLink to="/core" className={({ isActive }) => (isActive ? "on" : "")}>AI Core</NavLink>
        <button type="button" onClick={voice.tap} data-on={voice.phase !== "dormant" || undefined}>
          AI Voice
        </button>
        <MoreMenu items={items} waiting={waiting} onSignOut={onSignOut} />
      </nav>
      <Clock />
      <span className="cmd-pill" data-health={pill.health}
            title={read.data ? `${o.ok} OK, ${o.degraded} degraded, ${o.failing} failing, ${o.unknown} unknown`
              : "the checks have not been read"}>
        <i aria-hidden="true" />{pill.text}
      </span>
      <Link to="/tasks" className="cmd-needs" data-zero={waiting === 0 || undefined}
            data-health={waiting === null ? "unknown" : undefined}>
        <b>{waiting === null ? "?" : waiting}</b>
        <span>{waiting === null ? "needs you: unknown" : "need you"}</span>
      </Link>
      <span className="cmd-feed" data-state={feed}>
        <i aria-hidden="true" />{feed === "live" ? "Live" : feed === "connecting" ? "Connecting" : "Feed down"}
      </span>
    </header>
  );
}

// --------------------------------------------------------------- footer

export function CommandFooter({ voice, version, weather, network, networkOk, toggles }: {
  voice: Voice;
  version: { text: string; known: boolean };
  weather: ReactNode | null;
  network: string;
  networkOk: boolean;
  toggles: ReactNode;
}): JSX.Element {
  const phase = voice.phase;
  const listening = voice.handsFree && (voice.detector === "porcupine"
    || voice.detector === "local-whisper" || voice.detector === "speech-api");
  return (
    <footer className="cmd-foot">
      <span className="cmd-os" data-health={version.known ? undefined : "unknown"}>
        <span className="k">ATLAS OS</span> {version.text}
      </span>
      {weather && <span className="cmd-weather"><span className="k">Weather</span> {weather}</span>}
      <span className="cmd-net" data-ok={networkOk || undefined}>
        <span className="k">Network</span> {network}
      </span>
      <div className="cmd-talk">
        {tapAction(phase) === "interrupt" ? <StopButton voice={voice} /> : (
          <button type="button" className="cmd-talk-btn" data-phase={phase}
                  onClick={voice.tap} disabled={tapAction(phase) === "none"}>
            <span className="cmd-talk-wave" aria-hidden="true">
              {[0, 1, 2, 3, 4].map((i) => <i key={i} />)}
            </span>
            {tapAction(phase) === "dismiss" ? "Listening… tap to end" : "Talk to ATLAS"}
            <small>{talkCaption({ ...voice, listening })}</small>
          </button>
        )}
      </div>
      <span className="cmd-toggles">
        <MuteButton voice={voice} />
        {toggles}
      </span>
    </footer>
  );
}

// ---------------------------------------------------------- left column

function Ring({ o }: { o: Overall }): JSX.Element {
  const r = 52;
  const c = 2 * Math.PI * r;
  const p = o.percent;
  return (
    <svg className="cmd-ring" viewBox="0 0 132 132" role="img"
         aria-label={p === null ? "Overall health unknown: no module read"
           : `Overall health ${p} percent of the modules that read`}
         data-health={p === null ? "unknown" : o.failing ? "failing" : o.degraded ? "degraded" : "ok"}>
      <circle cx="66" cy="66" r={r} className="cmd-ring-bg" />
      {p !== null && (
        <circle cx="66" cy="66" r={r} className="cmd-ring-val"
                strokeDasharray={`${(c * p) / 100} ${c}`} transform="rotate(-90 66 66)" />
      )}
      {[...Array(48).keys()].map((i) => (
        <line key={i} x1="66" y1="4" x2="66" y2={i % 4 ? "8" : "11"} className="cmd-ring-tick"
              transform={`rotate(${i * 7.5} 66 66)`} />
      ))}
      <text x="66" y="68" textAnchor="middle" className="cmd-ring-n">
        {p === null ? "—" : `${p}%`}
      </text>
      <text x="66" y="86" textAnchor="middle" className="cmd-ring-l">
        {p === null ? "unknown" : "of what read"}
      </text>
    </svg>
  );
}

function SystemOverview({ overview, history, now }: {
  overview: Fresh<Overview | null>;
  history: Fresh<Awaited<ReturnType<typeof api.healthHistory>>>;
  now: number;
}): JSX.Element {
  const read = readOf(overview);
  const past = readOf(history);
  const o = overall(read.error ? null : read.data?.modules ?? null);
  return (
    <CPanel id="overview" title="System overview" at={stamp(overview.fetchedAt, read.error !== null)}
            more={{ to: "/status", label: "Status" }}>
      {read.error && <Unread>The checks could not be read: {read.error}</Unread>}
      <div className="cmd-ov">
        <Ring o={o} />
        <dl className="cmd-ov-counts">
          <div data-health="ok"><dt>OK</dt><dd>{read.data ? o.ok : "—"}</dd></div>
          <div data-health="degraded"><dt>Degraded</dt><dd>{read.data ? o.degraded : "—"}</dd></div>
          <div data-health="failing"><dt>Failing</dt><dd>{read.data ? o.failing : "—"}</dd></div>
          <div data-health="unknown"><dt>Unknown</dt><dd>{read.data ? o.unknown : "—"}</dd></div>
        </dl>
      </div>
      <p className="cmd-note">
        The percentage counts only modules that read. {o.unknown > 0
          ? `${o.unknown} could not be measured and are not in it.` : ""}
        {o.setAside > 0 ? ` ${o.setAside} not set up or paused.` : ""}
      </p>
      <h3 className="cmd-sub">Modules by state, last 7 days
        <span>{stamp(history.fetchedAt, past.error !== null)}</span></h3>
      {past.error ? <Unread>The past week could not be read: {past.error}</Unread>
        : past.data ? <HealthBars bars={healthBars(past.data.points)} from={now - 7 * 86_400_000} to={now} />
        : <p className="cmd-empty">Reading…</p>}
    </CPanel>
  );
}

function ServiceRow({ s, now }: { s: Service; now: number }): JSX.Element {
  const body = (
    <>
      <i className="cmd-sq" aria-hidden="true" />
      <span className="cmd-svc-n">{s.name}</span>
      <span className={`cmd-svc-w${s.state === "unknown" ? " cmd-word-unknown" : ""}`}>{s.word}</span>
      <span className="cmd-svc-t">{s.readAt ? rel(s.readAt, now) : "—"}</span>
    </>
  );
  const title = `${s.name}: ${s.word}${s.why ? ` (${s.why})` : ""}${s.readAt ? `, read ${new Date(s.readAt).toLocaleString()}` : ""}`;
  return (
    <li data-state={s.state} title={title}>
      {s.to ? <Link to={s.to}>{body}</Link> : <span className="cmd-svc-row">{body}</span>}
      {s.why && s.state !== "online" && <span className="cmd-svc-why">{s.why}</span>}
    </li>
  );
}

function Services({ overview, models, now }: {
  overview: Fresh<Overview | null>;
  models: Fresh<Awaited<ReturnType<typeof api.models>>>;
  now: number;
}): JSX.Element {
  const read = readOf(overview);
  const m = readOf(models);
  const list = [
    ...services(read.error ? null : read.data?.modules ?? null, read.error),
    llmService(m.data, m.error, models.fetchedAt?.toISOString() ?? null),
  ];
  const online = list.filter((s) => s.state === "online").length;
  return (
    <CPanel id="services" title="Connected services" at={`${online} of ${list.length} online`}>
      <ul className="cmd-svcs">
        {list.map((s) => <ServiceRow key={s.name} s={s} now={now} />)}
      </ul>
      <p className="cmd-note">Each from its own check; the time is when that check last read.</p>
    </CPanel>
  );
}

function Schedule({ agenda, board, now }: {
  agenda: Fresh<Awaited<ReturnType<typeof api.agenda>>>;
  board: Fresh<Awaited<ReturnType<typeof api.tasksBoard>>>;
  now: Date;
}): JSX.Element {
  const diary = readOf(agenda);
  const work = readOf(board);
  const days = weekDays({ now, agenda: diary.data, agendaError: diary.error,
                          agendaReadAt: agenda.fetchedAt, board: work.data });
  const why = diary.error ?? diary.data?.unavailable ?? null;
  return (
    <CPanel id="schedule" title="Upcoming schedule" at={stamp(agenda.fetchedAt, diary.error !== null)}
            more={{ to: "/calendar", label: "Calendar" }}>
      {why && <Unread>The diary could not be read: {why}</Unread>}
      {!why && (diary.data?.unread?.length ?? 0) > 0 && (
        <Unread>Part of the diary could not be read ({diary.data?.unread?.join(", ")}).</Unread>
      )}
      <ol className="cmd-days">
        {days.map((d) => {
          const items = d.items.filter((i) => i.at.getTime() >= now.getTime() - 3_600_000 || !d.isToday);
          return (
            <li key={d.key} data-today={d.isToday || undefined}>
              <span className="cmd-day">
                {d.isToday ? "Today" : d.date.toLocaleDateString([], { weekday: "short" })}
                <small>{d.date.toLocaleDateString([], { day: "numeric", month: "short" })}</small>
              </span>
              {items.length > 0 ? (
                <ul>
                  {items.slice(0, 4).map((it, i) => (
                    <li key={`${it.kind}-${i}`} data-kind={it.kind}>
                      <time>{hhmm(it.at)}</time> <span>{it.title}</span>
                    </li>
                  ))}
                  {items.length > 4 && <li className="cmd-more-n">+{items.length - 4} more</li>}
                </ul>
              ) : d.diary === "read" && d.unread.length === 0 ? (
                <span className="cmd-free">Nothing on</span>
              ) : (
                <span className="cmd-word-unknown">{d.diary === "beyond" ? "Not read" : "Unknown"}</span>
              )}
            </li>
          );
        })}
      </ol>
    </CPanel>
  );
}

function Alerts({ alerts, flows, now }: {
  alerts: Fresh<Awaited<ReturnType<typeof api.activity>>>;
  flows: Fresh<Awaited<ReturnType<typeof api.workflows>>>;
  now: number;
}): JSX.Element {
  const read = readOf(alerts);
  const wf = readOf(flows);
  // A workflow whose last run failed or degraded is an alert of its own:
  // nothing else on this page would show it.
  const broken = flowRows(wf.data?.graphs ?? [])
    .filter((f) => f.outcome === "failed" || f.outcome === "degraded");
  const rows = read.data?.activity ?? [];
  return (
    <CPanel id="alerts" title="Important alerts" at={stamp(alerts.fetchedAt, read.error !== null)}
            more={{ to: "/activity", label: "Activity" }}>
      {wf.error && <Unread>Workflow runs could not be read: {wf.error}</Unread>}
      {read.error ? <Unread>Alerts could not be read: {read.error}</Unread>
        : !read.data ? <p className="cmd-empty">Reading…</p>
        : rows.length === 0 && broken.length === 0 ? <p className="cmd-empty">No findings recorded.</p> : (
          <ul className="cmd-alert-list">
            {broken.slice(0, 3).map((f) => (
              <li key={`wf-${f.id}`} data-health={flowHealth(f.outcome)}>
                <i className="cmd-sq" aria-hidden="true" />
                <span className="cmd-al-s">Workflow {f.title}: last run {f.outcome}</span>
                <span className="cmd-al-m">workflows · {f.at ? rel(f.at, now) : "time not recorded"}</span>
              </li>
            ))}
            {rows.slice(0, 5).map((r) => {
              const h: Health = r.health ? normaliseHealth(r.health) : "unknown";
              return (
                <li key={`${r.id ?? r.at}-${r.subject}`} data-health={h}>
                  <i className="cmd-sq" aria-hidden="true" />
                  <span className="cmd-al-s">{r.subject}</span>
                  <span className="cmd-al-m">{r.module} · {rel(r.at, now)}</span>
                </li>
              );
            })}
          </ul>
        )}
    </CPanel>
  );
}

// --------------------------------------------------------------- centre

function Diagnostics({ models, overview, self, memory, voice, svc }: {
  models: Fresh<Awaited<ReturnType<typeof api.models>>>;
  overview: Fresh<Overview | null>;
  self: Fresh<Awaited<ReturnType<typeof api.self>>>;
  memory: Fresh<Awaited<ReturnType<typeof api.memory>>>;
  voice: Voice;
  svc: Service[];
}): JSX.Element {
  const m = readOf(models);
  const me = readOf(self);
  const mem = readOf(memory);
  const ov = readOf(overview);
  const last = lastAnswered(m.data);
  const ready = m.data ? providersReady(m.data) : null;
  // Storage is the backups module's own disk check (the data volume that
  // filled on 2026-09-29), read with its health and time -- not the API
  // process's root filesystem.
  const disk = storage(ov.error ? null : ov.data?.modules ?? null);
  const up = uptimeWords(me.data?.uptime_s);
  const svcOnline = svc.filter((s) => s.state === "online").length;
  const svcTrouble = svc.some((s) => s.state === "degraded" || s.state === "down");
  const tiles: Array<{ k: string; v: string | null; sub: string; health?: Health }> = [
    { k: "AI model active", v: last ? last.short : null,
      sub: m.error ? "read failed" : last ? `${last.agent}, ${rel(last.at)}` : "no run recorded" },
    { k: "AI providers ready", v: ready ? `${ready.ready}/${ready.total}` : null,
      sub: m.error ? "read failed" : stamp(models.fetchedAt),
      health: ready ? (ready.ready === 0 ? "failing" : ready.ready < ready.total ? "degraded" : "ok") : "unknown" },
    // The microphone as the badge reads it: open only when it is.
    { k: "Voice", v: voice.mic === "denied" ? "Mic blocked" : voice.mic === "unavailable" ? "No mic"
        : voice.mic === "streaming" ? "Streaming" : voice.mic === "listening" ? "Listening" : "Off",
      sub: "this device's microphone, now" },
    // Amber only for a service known to be in trouble; a shortfall that is
    // only Unknown is grey, not "degraded".
    { k: "Connected apps", v: `${svcOnline}/${svc.length}`, sub: "online, from their checks",
      health: svcTrouble ? "degraded" : svcOnline === svc.length ? "ok" : "unknown" },
    { k: "Memory", v: mem.data ? mem.data.total.toLocaleString("en-US") : null,
      sub: mem.error ? "read failed" : `items · ${stamp(memory.fetchedAt)}` },
    { k: "Storage", v: disk.text, health: disk.health,
      sub: ov.error ? "read failed" : disk.sub },
    { k: "Uptime", v: up, sub: me.error ? "read failed" : `ATLAS process · ${stamp(self.fetchedAt)}` },
  ];
  return (
    <ul className="cmd-diag" aria-label="System diagnostics">
      {tiles.map((t) => (
        <li key={t.k} data-health={t.v === null ? "unknown" : t.health}
            className={t.health === "unknown" && t.v !== null ? "cmd-diag-grey" : undefined}>
          <span className="k">{t.k}</span>
          {t.v === null ? <span className="v cmd-word-unknown">Unknown</span> : <span className="v">{t.v}</span>}
          <span className="s">{t.sub}</span>
        </li>
      ))}
    </ul>
  );
}

const STEP_WORD: Record<CoreStep, string> = {
  standby: "Standby", listening: "Listening", thinking: "Thinking",
  planning: "Planning", executing: "Executing",
};

function CoreCentre({ voice, gaze, models, overview, self, memory, counters, agentsRunning, svc }: {
  voice: Voice;
  gaze: { x: number; y: number } | null;
  models: Fresh<Awaited<ReturnType<typeof api.models>>>;
  overview: Fresh<Overview | null>;
  self: Fresh<Awaited<ReturnType<typeof api.self>>>;
  memory: Fresh<Awaited<ReturnType<typeof api.memory>>>;
  counters: Fresh<StatusCounters | null>;
  agentsRunning: string[] | null;
  svc: Service[];
}): JSX.Element {
  const c = readOf(counters);
  const states = coreStates({
    phase: voice.phase, agentsRunning,
    executing: c.error ? null : c.data?.executing ?? null,
  });
  const watchOnly = c.error ? null : c.data?.watch_only ?? null;
  const status = statusOf(voice.phase);
  return (
    <section className="cmd-centre" aria-labelledby="cmd-core-h">
      <h2 id="cmd-core-h" className="sr">ATLAS</h2>
      <div className="cmd-figure" data-phase={voice.phase}>
        <span className="cmd-halo" aria-hidden="true" />
        <WhenVisible fallback={<div className="bust" aria-hidden="true" />}>
          <Suspense fallback={<div className="bust" aria-hidden="true" />}>
            <Bust phase={voice.phase} bars={voice.bars}
                  onWake={voice.phase === "dormant" ? voice.wake : voice.interrupt} gaze={gaze} />
          </Suspense>
        </WhenVisible>
        <p className="cmd-fig-status" data-status={status}>
          <i aria-hidden="true" />{STATUS_WORDS[status] ?? status}
        </p>
      </div>
      <Diagnostics models={models} overview={overview} self={self} memory={memory}
                   voice={voice} svc={svc} />
      <div className="cmd-corestrip" role="list" aria-label="ATLAS AI core state">
        <span className="cmd-cs-label">ATLAS AI Core
          {watchOnly === true && <small className="cmd-watch">watch-only: reads, does not act</small>}
          {watchOnly === null && <small className="cmd-word-unknown">mode unknown</small>}
        </span>
        {states.map((s) => (
          <span key={s.step} role="listitem" className="cmd-cs" data-lit={s.lit || undefined}
                data-unknown={s.unknown || undefined} title={s.note || undefined}>
            <i aria-hidden="true" />{STEP_WORD[s.step]}
            {s.unknown && <small className="cmd-word-unknown">Unknown</small>}
            {s.lit && s.note && s.step !== "standby" && <small>{s.note}</small>}
          </span>
        ))}
      </div>
      <p className="cmd-prompt">Say “ATLAS”, or tap the figure or the bar below, to talk.</p>
    </section>
  );
}

// --------------------------------------------------------- right column

function ActiveTasks({ tasks, now }: {
  tasks: Fresh<Awaited<ReturnType<typeof api.tasks>>>;
  now: number;
}): JSX.Element {
  const read = readOf(tasks);
  const jobs = (read.data?.jobs ?? []).filter((j) => !j.finished_at);
  const research = read.data?.research ?? [];
  const rows = [
    ...jobs.map((j) => ({ key: `j${j.id}`, what: j.task, meta: `${j.repo} · ${j.status}`, at: j.at })),
    ...research.map((r) => ({ key: `r${r.id}`, what: r.question, meta: "research queued", at: r.at })),
  ];
  return (
    <CPanel id="tasks" title="Active tasks" at={stamp(tasks.fetchedAt, read.error !== null)}
            more={{ to: "/tasks", label: "Tasks" }}>
      {read.error ? <Unread>Tasks could not be read: {read.error}</Unread>
        : !read.data ? <p className="cmd-empty">Reading…</p>
        : rows.length === 0 ? <p className="cmd-empty">No task in progress.</p> : (
          <ul className="cmd-list">
            {rows.slice(0, 5).map((r) => (
              <li key={r.key}>
                <span className="cmd-l-s">{r.what}</span>
                <span className="cmd-l-m">{r.meta}{r.at ? ` · ${rel(r.at, now)}` : ""}</span>
              </li>
            ))}
          </ul>
        )}
    </CPanel>
  );
}

function Operations({ board, now }: {
  board: Fresh<Awaited<ReturnType<typeof api.tasksBoard>>>;
  now: number;
}): JSX.Element {
  const read = readOf(board);
  const rows = (read.data?.agents ?? []).flatMap((a) =>
    a.in_flight.map((r) => ({ ...r, who: a.label })));
  return (
    <CPanel id="ops" title="Current operations" at={stamp(board.fetchedAt, read.error !== null)}
            more={{ to: "/workflows", label: "Workflows" }}>
      {read.error ? <Unread>The work board could not be read: {read.error}</Unread>
        : !read.data ? <p className="cmd-empty">Reading…</p>
        : rows.length === 0 ? <p className="cmd-empty">Nothing running right now.</p> : (
          <ul className="cmd-list">
            {rows.slice(0, 5).map((r) => (
              <li key={`${r.kind}-${r.id}`}>
                <span className="cmd-l-s">{r.summary}</span>
                <span className="cmd-l-m">
                  <span className="cmd-run">Running</span> {r.who} · {r.kind}
                  {r.started ? ` · since ${rel(r.started, now).replace(" ago", "")}` : ""}
                </span>
              </li>
            ))}
          </ul>
        )}
    </CPanel>
  );
}

function NeedsYou({ board, now }: {
  board: Fresh<Awaited<ReturnType<typeof api.tasksBoard>>>;
  now: number;
}): JSX.Element {
  const read = readOf(board);
  const cards = waitingCards(read.data, now);
  return (
    <CPanel id="waiting" title="Needs you" at={stamp(board.fetchedAt, read.error !== null)}
            more={{ to: "/tasks", label: "All" }}>
      {read.error ? <Unread>What waits on you is not known: {read.error}</Unread>
        : cards === null ? (board.fetchedAt ? <Unread>The proposals could not be read.</Unread>
          : <p className="cmd-empty">Reading…</p>)
        : cards.length === 0 ? <p className="cmd-empty">Nothing needs you.</p> : (
          <ul className="cmd-list cmd-cards">
            {cards.slice(0, 4).map((c) => {
              const remaining = left(c.expires_at, now);
              return (
                <li key={c.id} data-expired={remaining === "expired" || undefined}>
                  <span className="cmd-l-s">#{c.id} {c.action.replace(/[._]/g, " ")}
                    <em>Tier {c.tier}</em></span>
                  <span className="cmd-l-m">{c.who} · {remaining === "expired" ? "expired, will not run"
                    : remaining === "—" ? "no expiry" : `expires in ${remaining}`}</span>
                </li>
              );
            })}
          </ul>
        )}
      <p className="cmd-note">Answer by text. This page cannot approve; silence expires a card.</p>
    </CPanel>
  );
}

function Recent({ activity, now }: {
  activity: Fresh<Awaited<ReturnType<typeof api.activity>>>;
  now: number;
}): JSX.Element {
  const read = readOf(activity);
  const rows = read.data?.activity ?? [];
  return (
    <CPanel id="recent" title="Recent activity" at={stamp(activity.fetchedAt, read.error !== null)}
            more={{ to: "/activity", label: "All" }}>
      {read.error ? <Unread>Activity could not be read: {read.error}</Unread>
        : !read.data ? <p className="cmd-empty">Reading…</p>
        : rows.length === 0 ? <p className="cmd-empty">Nothing recorded yet.</p> : (
          <ul className="cmd-feed-list">
            {rows.slice(0, 7).map((r) => (
              <li key={`${r.kind}-${r.id ?? r.at}-${r.subject}`}
                  data-health={r.health ? normaliseHealth(r.health) : undefined}>
                <time>{hhmm(r.at)}</time>
                <span>{r.subject}</span>
                <small>{r.module} · {rel(r.at, now)}</small>
              </li>
            ))}
          </ul>
        )}
    </CPanel>
  );
}

// ---------------------------------------------------------------- rail

function Rail({ voice, models, history, now }: {
  voice: Voice;
  models: Fresh<Awaited<ReturnType<typeof api.models>>>;
  history: Fresh<Awaited<ReturnType<typeof api.voiceHistory>>>;
  now: Date;
}): JSX.Element {
  const navigate = useNavigate();
  const m = readOf(models);
  const rows = m.data ? modelLegend(m.data, now) : [];
  const h = readOf(history);
  return (
    <aside className="cmd-rail" aria-label="Quick actions and status">
      <CPanel id="quick" title="Quick actions">
        <div className="cmd-actions">
          <button type="button" onClick={voice.tap}>Voice command</button>
          <button type="button" onClick={() => navigate("/calendar")}>Open calendar</button>
          <button type="button" onClick={() => navigate("/status")}
                  title="Opens Status. ATLAS's checks run on their own schedule; no page starts them.">
            System scan
          </button>
          <button type="button" onClick={() => navigate("/command")}>Take control</button>
        </div>
      </CPanel>
      <CPanel id="llmstat" title="LLM status" at={stamp(models.fetchedAt, m.error !== null)}>
        {m.error ? <Unread>Model chains could not be read: {m.error}</Unread>
          : !m.data ? <p className="cmd-empty">Reading…</p> : (
            <ul className="cmd-llm">
              {rows.slice(0, 8).map((r) => (
                <li key={r.key} data-dot={r.dot} title={`${r.full}: ${r.word}`}>
                  <i className="cmd-sq" aria-hidden="true" />
                  <span className="n">{r.short}</span>
                  <span className="w">{r.dot === "dropped" ? "unavailable"
                    : r.dot === "half-open" ? "half-open" : r.dot === "open" ? "open" : r.dot}</span>
                </li>
              ))}
              {rows.length > 8 && <li className="cmd-more-n">+{rows.length - 8} more below</li>}
            </ul>
          )}
        {m.data?.free_only && <p className="cmd-note">Free tiers only.</p>}
      </CPanel>
      <CPanel id="voicelog" title="Voice commands" at={stamp(history.fetchedAt, h.error !== null)}>
        {h.error ? <Unread>Voice history could not be read: {h.error}</Unread>
          : !h.data ? <p className="cmd-empty">Reading…</p>
          : h.data.turns.length === 0 ? <p className="cmd-empty">Nothing said yet.</p> : (
            <ul className="cmd-voice">
              {h.data.turns.slice(0, 5).map((t) => (
                <li key={t.id}><time>{hhmm(t.at)}</time> <span>“{t.transcript}”</span></li>
              ))}
            </ul>
          )}
      </CPanel>
      <CPanel id="stop" title="Emergency stop">
        <p className="cmd-stop-how">
          To pause ATLAS, say in Slack: <code>pause everything</code> (or <code>pause reels</code>),
          and <code>resume …</code> to undo. This page has no stop button: the dashboard cannot
          change anything, by design.
        </p>
      </CPanel>
    </aside>
  );
}

// ------------------------------------------------------------ marketing

function Kpis({ overview, now }: { overview: Fresh<Overview | null>; now: Date }): JSX.Element {
  const read = readOf(overview);
  const figures: Figure[] = marketingFigures(read.error ? null : read.data?.modules ?? null, read.error);
  const stamps = Object.fromEntries(figures.flatMap((f) => f.parts.map((p) => [p.check, p.readAtIso])));
  const fresh = useFreshlyRead(stamps);
  return (
    <CPanel id="marketing" title="Marketing" more={{ to: "/marketing", label: "Marketing" }}
            at="each number with its own read time">
      <ul className="cmd-kpis">
        {figures.map((f) => (
          <li key={f.key} className="cmd-kpi">
            <Link to={f.to} className="cmd-kpi-l">{f.label}</Link>
            {f.parts.map((p) => (
              <FigurePartView key={p.check} part={p} fresh={fresh.has(p.check)} now={now}
                              note={f.chartNote} />
            ))}
          </li>
        ))}
      </ul>
    </CPanel>
  );
}

// ---------------------------------------------------------- agents map

function AgencyMap({ roster, models, now }: {
  roster: Fresh<Awaited<ReturnType<typeof api.agents>>>;
  models: Fresh<Awaited<ReturnType<typeof api.models>>>;
  now: Date;
}): JSX.Element {
  const team = readOf(roster);
  const chains = readOf(models);
  const agents = mapAgents(null, team.data?.agents ?? null);
  const modelFor = (name: string): NodeModel => {
    const b = modelBadge(chains.data, chains.error, name, now);
    return { label: b.which === "next" ? `next: ${b.label}` : b.label, dot: b.dot, title: b.title };
  };
  return (
    <CPanel id="agentic" title="Agentic map" at={`roster ${stamp(roster.fetchedAt, team.error !== null)}`}
            more={{ to: "/agency/room", label: "Agency room" }}>
      <div className="cmd-map">
        <div className="cmd-map-canvas">
          {team.error && <Unread>The roster could not be read ({team.error}). Every agent is unknown.</Unread>}
          {(agents.length > 0 || team.error) && (
            <AgentMap agents={agents} readFailed={team.error !== null} modelFor={modelFor}
                      onSelect={() => undefined} onWarmed={() => roster.reload()} />
          )}
        </div>
        <div className="cmd-map-legend">
          <ModelsLegend models={models} now={now} />
        </div>
      </div>
    </CPanel>
  );
}

// ---------------------------------------------------------------- page

export function CommandCenter({ reloadKey, voice, overview, counters, gaze = null, agentsRunning }: {
  reloadKey: number;
  voice: Voice;
  overview: Fresh<Overview | null>;
  counters: Fresh<StatusCounters | null>;
  ambient?: Fresh<Ambient | null>;
  gaze?: { x: number; y: number } | null;
  /** Agent runs started and not finished, from the live stream; null when
   *  the stream is not live, so it is not known. */
  agentsRunning: string[] | null;
}): JSX.Element {
  const roster = useFresh(() => api.agents(), [reloadKey]);
  const board = useFresh(() => api.tasksBoard(), [reloadKey]);
  const agenda = useFresh(() => api.agenda(WEEK_HOURS), [reloadKey]);
  const models = useFresh(() => api.models(), [reloadKey]);
  const tasks = useFresh(() => api.tasks(), [reloadKey]);
  const activity = useFresh(() => api.activity({ limit: 12 }), [reloadKey]);
  const alerts = useFresh(() => api.activity({ kind: "finding", limit: 6 }), [reloadKey]);
  const self = useFresh(() => api.self(), [reloadKey]);
  const flows = useFresh(() => api.workflows(), [reloadKey]);
  // The week's health is hourly samples: read once an hour, and on mount.
  const hour = Math.floor(Date.now() / 3_600_000);
  const healthPast = useFresh(() => api.healthHistory(7), [hour]);
  const memory = useFresh(() => api.memory(), [reloadKey]);
  const history = useFresh(() => api.voiceHistory(6), [reloadKey]);
  useEffect(() => {
    const before = document.title;
    document.title = "ATLAS · Command Center";
    return (): void => { document.title = before; };
  }, []);
  const now = new Date();
  const t = now.getTime();
  const read = readOf(overview);
  const m = readOf(models);
  const svc = [
    ...services(read.error ? null : read.data?.modules ?? null, read.error),
    llmService(m.data, m.error, models.fetchedAt?.toISOString() ?? null),
  ];

  return (
    <div className="cmd-page">
      <div className="cmd-grid">
        <div className="cmd-col cmd-left">
          <SystemOverview overview={overview} history={healthPast} now={Date.now()} />
          <Services overview={overview} models={models} now={t} />
          <Schedule agenda={agenda} board={board} now={now} />
          <Alerts alerts={alerts} flows={flows} now={t} />
        </div>
        <div className="cmd-mid">
          <CoreCentre voice={voice} gaze={gaze} models={models} overview={overview} self={self}
                      memory={memory} counters={counters} agentsRunning={agentsRunning} svc={svc} />
          <Kpis overview={overview} now={now} />
        </div>
        <div className="cmd-col cmd-right">
          <NeedsYou board={board} now={t} />
          <ActiveTasks tasks={tasks} now={t} />
          <Operations board={board} now={t} />
          <Recent activity={activity} now={t} />
        </div>
        <Rail voice={voice} models={models} history={history} now={now} />
      </div>
      <AgencyMap roster={roster} models={models} now={now} />
    </div>
  );
}
