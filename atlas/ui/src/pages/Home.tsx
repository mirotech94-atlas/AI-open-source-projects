import {
  Suspense, lazy, useEffect, useRef, useState, type JSX, type ReactNode,
} from "react";
import { Link } from "react-router-dom";
import {
  api, type HealthHistory, type Models, type Overview, type StatusCounters,
} from "../api";
import type { NodeModel } from "../components/NeuralMap";
import { HealthBars, ReadingsChart } from "../components/Spark";
import { useFresh, type Fresh } from "../hooks/useFresh";
import { AgentMap } from "./AgentMap";
import { mapAgents } from "./agencyModel";
import { left } from "./tasksModel";
import type { useVoice } from "../voice/useVoice";
import { statusOf, type Phase } from "../voice/loop";
import { talkCaption } from "../voice/detectorCore";
import { tapAction } from "../voice/interrupt";
import { MuteButton, StopButton, VoiceHints } from "../voice/VoiceOverlay";
import {
  STATUS_WORDS, busySeats, flowHealth, flowRows, healthLine, healthSummary, hhmm, jobsDue,
  marketingFigures, readLine, readOf, rel, waitingCards, weekDays,
  WEEK_HOURS, type Figure, type FigurePart, type WeekDay,
} from "./homeModel";
import {
  changeLabel, changeSince, freshlyRead, healthBars, modelBadge, modelLegend, shapeSeries,
} from "./liveModel";

// Lazy for the same reason as on the AI Core: `three` is megabytes, and the
// home page is read far more often than it is talked to. The figure loads
// only once its stage scrolls into view.
const Bust = lazy(() => import("../core/Bust").then((m) => ({ default: m.Bust })));

/**
 * Home: the one page Miro asked for (2026-10-06) -- what needs him, the
 * week, ATLAS itself, who is working, health, workflows and marketing --
 * each a short, plain section with a "More" link to its full page.
 *
 * Reads only. Every figure carries the time it was read, and anything that
 * could not be read says UNKNOWN in grey, dashed and unlit (invariants 6
 * and 7). Overview and the counters come from the shell, which already
 * fetches them; this page fetches the roster, the work board, the diary and
 * the workflows once each per refresh.
 */

export function Unknown({ children }: { children?: ReactNode }): JSX.Element {
  return (
    <p className="ac-unknown" data-health="unknown">
      <span className="ac-unknown-word hatch-edge">Unknown</span>
      {children && <span>{children}</span>}
    </p>
  );
}

function Section({
  id, title, more, stamp, children, className = "",
}: {
  id: string;
  title: string;
  more: string;
  stamp: ReactNode;
  children: ReactNode;
  className?: string;
}): JSX.Element {
  return (
    <section className={`ac-card ac-${id} ${className}`} aria-labelledby={`ac-${id}-h`}>
      <header className="ac-card-head">
        <h2 id={`ac-${id}-h`}>{title}</h2>
        <span className="ac-stamp">{stamp}</span>
        <Link className="ac-more-link" to={more}>
          More<span className="sr"> about {title.toLowerCase()}</span>
        </Link>
      </header>
      {children}
    </section>
  );
}

// ------------------------------------------------------------ needs you

function NeedsYou({ board, now }: {
  board: Fresh<Awaited<ReturnType<typeof api.tasksBoard>>>; now: number;
}): JSX.Element {
  const read = readOf(board);
  const cards = waitingCards(read.data, now);
  const SHOW = 5;
  return (
    <Section id="needs" title="Needs you" more="/tasks"
             stamp={read.error ? "read failed" : readLine(board.fetchedAt)}>
      {read.error ? (
        <Unknown>The work board could not be read ({read.error}), so what waits on you is not known.</Unknown>
      ) : cards === null ? (
        board.fetchedAt ? <Unknown>The proposals could not be read.</Unknown>
          : <p className="ac-empty">Reading…</p>
      ) : cards.length === 0 ? (
        <p className="ac-empty">Nothing needs you. Every decision is answered.</p>
      ) : (
        <>
          <p className="ac-needs-count">
            <b>{cards.length}</b> decision{cards.length === 1 ? "" : "s"} waiting.
            {" "}Answer by text: this page can't approve anything, and an unanswered card expires instead of running.
          </p>
          <ul className="ac-cards">
            {cards.slice(0, SHOW).map((c) => {
              const remaining = left(c.expires_at, now);
              return (
                <li key={c.id} className="ac-decision" data-expired={remaining === "expired" || undefined}>
                  <span className="ac-decision-what">
                    <b>#{c.id} {c.action.replace(/[._]/g, " ")}</b>
                    <span className="ac-tier">Tier {c.tier}</span>
                  </span>
                  <span className="ac-decision-why">{c.reason}</span>
                  <span className="ac-decision-meta">
                    {c.who} · {remaining === "expired" ? "expired, will not run"
                      : remaining === "—" ? "no expiry recorded" : `expires in ${remaining}`}
                  </span>
                </li>
              );
            })}
          </ul>
          {cards.length > SHOW && (
            <p className="ac-note"><Link to="/tasks">{cards.length - SHOW} more on the Tasks page</Link></p>
          )}
        </>
      )}
    </Section>
  );
}

// ----------------------------------------------------------------- week

const KIND_WORD: Record<string, string> = {
  event: "Event", deadline: "Due", job: "Scheduled", card: "Card expires",
};

export function Day({ day }: { day: WeekDay }): JSX.Element {
  const name = day.isToday ? "Today"
    : day.date.toLocaleDateString([], { weekday: "short" });
  return (
    <li className="ac-day" data-today={day.isToday || undefined} data-diary={day.diary}
        data-empty={day.items.length === 0 || undefined}>
      <h3>
        <span className="ac-day-name">{name}</span>
        <span className="ac-day-date">
          {day.date.toLocaleDateString([], { day: "numeric", month: "short" })}
        </span>
      </h3>
      {day.items.length > 0 && (
        <ul>
          {day.items.map((it, i) => (
            <li key={`${it.kind}-${it.at.getTime()}-${i}`} className="ac-item" data-kind={it.kind}
                title={`${KIND_WORD[it.kind] ?? it.kind}${it.detail ? `: ${it.detail}` : ""}${it.source ? ` (${it.source})` : ""}`}>
              <time dateTime={it.at.toISOString()}>{hhmm(it.at)}</time>
              <span>{it.title}</span>
            </li>
          ))}
        </ul>
      )}
      {day.diary === "beyond" && (
        <p className="ac-diary hatch-edge" data-health="unknown">Diary not read this far ahead</p>
      )}
      {day.diary === "partial" && day.until && (
        <p className="ac-diary hatch-edge" data-health="unknown">Diary read until {hhmm(day.until)}</p>
      )}
      {day.diary === "unknown" && (
        <p className="ac-diary hatch-edge" data-health="unknown">Diary unknown</p>
      )}
      {day.diary !== "beyond" && day.diary !== "unknown" && day.unread.length > 0 && (
        <p className="ac-diary hatch-edge" data-health="unknown"
           title={`Not read: ${day.unread.join(", ")}`}>Diary partly unknown</p>
      )}
      {day.diary === "read" && day.items.length === 0 && day.unread.length === 0 && (
        <p className="ac-day-free">Nothing on</p>
      )}
    </li>
  );
}

function Week({ agenda, board, now }: {
  agenda: Fresh<Awaited<ReturnType<typeof api.agenda>>>;
  board: Fresh<Awaited<ReturnType<typeof api.tasksBoard>>>;
  now: Date;
}): JSX.Element {
  const diary = readOf(agenda);
  const work = readOf(board);
  const days = weekDays({
    now, agenda: diary.data, agendaError: diary.error, agendaReadAt: agenda.fetchedAt,
    board: work.data,
  });
  const diaryWhy = diary.error ?? diary.data?.unavailable ?? null;
  return (
    <Section id="week" title="This week" more="/calendar"
             stamp={`diary ${diary.error ? "read failed" : readLine(agenda.fetchedAt)}`
               + ` · tasks ${work.error ? "read failed" : readLine(board.fetchedAt)}`}>
      <ol className="ac-days">
        {days.map((d) => <Day key={d.key} day={d} />)}
      </ol>
      <p className="ac-legend">
        <span data-kind="event">Events</span>
        <span data-kind="deadline">Deadlines</span>
        <span data-kind="card">Cards expiring</span>
        <span data-kind="job">Scheduled jobs</span>
        {diary.data && !diaryWhy && (
          <span className="ac-legend-note">The diary is read {diary.data.horizon_hours} hours ahead.</span>
        )}
      </p>
      {diaryWhy && <Unknown>The diary could not be read: {diaryWhy}</Unknown>}
      {!diaryWhy && (diary.data?.unread?.length ?? 0) > 0 && (
        <Unknown>
          Part of the diary could not be read ({diary.data?.unread?.join(", ")}), so an
          empty day may not be free.
        </Unknown>
      )}
      {work.error && <Unknown>Cards and jobs are missing: the work board could not be read ({work.error}).</Unknown>}
    </Section>
  );
}

// ---------------------------------------------------------------- ATLAS

/** Mounts its children once the box is near the viewport, then keeps them. */
export function WhenVisible({ children, fallback }: { children: ReactNode; fallback: ReactNode }): JSX.Element {
  const ref = useRef<HTMLDivElement>(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || seen) return;
    if (typeof IntersectionObserver === "undefined") { setSeen(true); return; }
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) { setSeen(true); io.disconnect(); }
    }, { rootMargin: "200px" });
    io.observe(el);
    return (): void => io.disconnect();
  }, [seen]);
  return <div ref={ref} className="ac-stage">{seen ? children : fallback}</div>;
}

function Atlas({ voice, gaze }: {
  voice: ReturnType<typeof useVoice>;
  gaze: { x: number; y: number } | null;
}): JSX.Element {
  const phase: Phase = voice.phase;
  const status = statusOf(phase);
  const listening = voice.handsFree && (voice.detector === "porcupine"
    || voice.detector === "local-whisper" || voice.detector === "speech-api");
  // The conversation itself -- what was said, the answer, the log, a
  // check's checklist -- is the voice overlay's, which veils whatever part
  // of this page is on screen (a wake word heard while scrolled to
  // Marketing must still be answered where he is looking). The card is the
  // figure, its state and the controls.
  return (
    <section className="ac-card ac-atlas" aria-labelledby="ac-atlas-h">
      <header className="ac-card-head">
        <h2 id="ac-atlas-h">ATLAS</h2>
        <span className="ac-stamp">live device state</span>
        <Link className="ac-more-link" to="/core">More<span className="sr"> about ATLAS</span></Link>
      </header>
      <WhenVisible fallback={<div className="bust" aria-hidden="true" />}>
        <Suspense fallback={<div className="bust" aria-hidden="true" />}>
          <Bust phase={phase} bars={voice.bars}
                onWake={phase === "dormant" ? voice.wake : voice.interrupt} gaze={gaze} />
        </Suspense>
      </WhenVisible>
      <p className="ac-atlas-status" data-status={status} aria-live="polite">
        <span className="dot" aria-hidden="true" />
        {STATUS_WORDS[status] ?? status}
      </p>
      <div className="ac-atlas-actions">
        {/* One tap, announced: wake, stop the answer, or end the turn --
            `tapAction` decides, the same rule as every other talk control. */}
        {tapAction(phase) === "interrupt" ? <StopButton voice={voice} /> : (
          <button type="button" className="ac-btn ac-btn-accent" data-phase={phase}
                  onClick={voice.tap} disabled={tapAction(phase) === "none"}>
            {tapAction(phase) === "dismiss" ? "Listening… tap to end" : "Talk to ATLAS"}
          </button>
        )}
        <MuteButton voice={voice} />
      </div>
      <p className="ac-caption">{talkCaption({ ...voice, listening })}</p>
      <VoiceHints voice={voice} />
    </section>
  );
}

// --------------------------------------------------------------- agents

export function ModelsLegend({ models, now }: { models: Fresh<Models>; now: Date }): JSX.Element {
  const read = readOf(models);
  const rows = read.data ? modelLegend(read.data, now) : [];
  return (
    <details className="ac-models" open>
      <summary>
        Models <span>{read.error ? "read failed" : readLine(models.fetchedAt)}
          {read.data?.free_only ? " · free tiers only" : ""}</span>
      </summary>
      {read.error ? (
        <Unknown>The model chains could not be read ({read.error}), so every model is unknown.</Unknown>
      ) : !read.data ? (
        <p className="ac-empty">Reading…</p>
      ) : rows.length === 0 ? (
        <Unknown>No model chain is configured.</Unknown>
      ) : (
        <ul>
          {rows.map((r) => (
            <li key={r.key} data-dot={r.dot} title={r.full}>
              <i className={`ac-mdot${r.dot === "dropped" || r.dot === "unknown" ? " hatch-edge" : ""}`}
                 data-dot={r.dot} aria-hidden="true" />
              <span className="ac-model-name">
                <b>{r.short}</b> <span>{r.provider}</span>
              </span>
              <span className={`ac-model-state${r.dot === "dropped" || r.dot === "unknown"
                ? " ac-unknown-word hatch-edge" : ""}`}>
                {r.dot === "dropped" ? `Unknown / unavailable` : r.word}
              </span>
              <span className="ac-model-needs">
                {r.dot === "dropped" ? r.word : r.needs.length ? r.needs.join(", ") : "no Need"}
              </span>
            </li>
          ))}
        </ul>
      )}
      <p className="ac-note">
        A node shows the model that answered that agent's last recorded run, or,
        when it has none, the one it would ask first. The dot is that model's
        breaker now: red open, amber half-open, grey dashed unavailable.
        Breakers are read every 30 seconds.
      </p>
    </details>
  );
}

function Agents({ roster, board, models, now }: {
  roster: Fresh<Awaited<ReturnType<typeof api.agents>>>;
  board: Fresh<Awaited<ReturnType<typeof api.tasksBoard>>>;
  models: Fresh<Models>;
  now: number;
}): JSX.Element {
  const team = readOf(roster);
  const work = readOf(board);
  const chains = readOf(models);
  const agents = mapAgents(null, team.data?.agents ?? null);
  const busy = busySeats(work.data);
  const ranRecently = (team.data?.agents ?? [])
    .filter((a) => a.last_run_at && now - Date.parse(a.last_run_at) < 3_600_000).length;
  const at = new Date(now);
  const modelFor = (name: string): NodeModel => {
    const b = modelBadge(chains.data, chains.error, name, at);
    return { label: b.which === "next" ? `next: ${b.label}` : b.label, dot: b.dot, title: b.title };
  };
  return (
    <Section id="agents" title="Agents working" more="/agency/room"
             stamp={`roster ${team.error ? "read failed" : readLine(roster.fetchedAt)}`}>
      <div className="ac-agents-body">
        <div className="ac-map">
          {team.error && <Unknown>The roster could not be read ({team.error}). Every agent below is unknown.</Unknown>}
          {(agents.length > 0 || team.error) && (
            <AgentMap agents={agents} readFailed={team.error !== null} modelFor={modelFor}
                      onSelect={() => undefined} onWarmed={() => roster.reload()} />
          )}
          <ModelsLegend models={models} now={at} />
          {!team.error && roster.fetchedAt && agents.length === 0 && (
            <p className="ac-empty">No agents are registered.</p>
          )}
        </div>
        <div className="ac-onit">
          <h3>On it now</h3>
          {work.error ? (
            <Unknown>What each agent has in hand is not known: {work.error}</Unknown>
          ) : !work.data ? (
            <p className="ac-empty">Reading…</p>
          ) : busy.length === 0 ? (
            <p className="ac-empty">No agent has work in hand right now.</p>
          ) : (
            <ul className="ac-busy">
              {busy.map((s) => (
                <li key={s.name}>
                  <span className="ac-busy-who">
                    <b>{s.label}</b> <span>{s.title}</span>
                  </span>
                  <span className="ac-busy-what">{s.headline}</span>
                  <span className="ac-busy-n">
                    {s.inFlight === null
                      ? <span className="ac-unknown-word hatch-edge" data-health="unknown">Unknown</span>
                      : `${s.inFlight} in hand`}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {work.data && (
            <p className="ac-note">
              {work.data.totals.agents_busy} of {work.data.agents.length} seats busy
              {!team.error && team.data ? ` · ${ranRecently} ran in the last hour` : ""}
              {` · ${readLine(board.fetchedAt)}`}
            </p>
          )}
        </div>
      </div>
    </Section>
  );
}

// --------------------------------------------------------------- health

function HealthStrip({ overview, history, now }: {
  overview: Fresh<Overview | null>;
  history: Fresh<HealthHistory>;
  now: Date;
}): JSX.Element {
  const read = readOf(overview);
  const summary = healthSummary(read.error ? null : read.data?.modules ?? null);
  const watched = summary.rows.filter((r) => !r.setAside);
  const past = readOf(history);
  const to = now.getTime();
  return (
    <Section id="health" title="Health" more="/status"
             stamp={read.error ? "read failed" : readLine(overview.fetchedAt)}>
      {read.error && <Unknown>The checks could not be read: {read.error}</Unknown>}
      {summary.known && (
        <>
          <div className="ac-strip" role="img" aria-label={healthLine(summary)}>
            {watched.map((r) => (
              <span key={r.module} data-health={r.health}
                    className={r.health === "unknown" ? "hatch" : undefined}
                    title={`${r.title}: ${r.health.toUpperCase()}`} />
            ))}
          </div>
          <p className="ac-health-line">{healthLine(summary)}</p>
          <h3 className="ac-sub">Last 7 days <span>{past.error ? "read failed"
            : readLine(history.fetchedAt)}</span></h3>
          {past.error ? (
            <Unknown>The past week could not be read: {past.error}</Unknown>
          ) : past.data ? (
            <HealthBars bars={healthBars(past.data.points)} from={to - 7 * DAY_MS} to={to} />
          ) : (
            <p className="ac-empty">Reading…</p>
          )}
          <details className="ac-health-list">
            <summary>Every module</summary>
            <ul>
              {summary.rows.map((r) => (
                <li key={r.module} data-health={r.setAside ? "unknown" : r.health}>
                  <Link to={`/status?focus=${encodeURIComponent(r.module)}`}>{r.title}</Link>
                  <span className={`ac-word${r.health === "unknown" || r.setAside ? " hatch-edge" : ""}`}>
                    {r.setAside ?? (r.health === "ok" ? "OK" : r.health[0]?.toUpperCase() + r.health.slice(1))}
                  </span>
                  <span className="ac-mod-meta">
                    {r.finding ?? "no findings"} · checked {rel(r.lastAt)}
                  </span>
                </li>
              ))}
            </ul>
          </details>
        </>
      )}
      {!summary.known && !read.error && <p className="ac-empty">Reading…</p>}
    </Section>
  );
}

// ------------------------------------------------------------ workflows

function Workflows({ flows, board, now }: {
  flows: Fresh<Awaited<ReturnType<typeof api.workflows>>>;
  board: Fresh<Awaited<ReturnType<typeof api.tasksBoard>>>;
  now: number;
}): JSX.Element {
  const read = readOf(flows);
  const work = readOf(board);
  const rows = flowRows(read.data?.graphs ?? []);
  const next = jobsDue(work.data?.agents ?? [])
    .filter((j) => Date.parse(j.next_due ?? "") >= now - 60_000).slice(0, 4);
  return (
    <Section id="flows" title="Workflows" more="/workflows"
             stamp={read.error ? "read failed" : readLine(flows.fetchedAt)}>
      <h3>Last runs</h3>
      {read.error ? (
        <Unknown>The workflows could not be read: {read.error}</Unknown>
      ) : !read.data ? (
        <p className="ac-empty">Reading…</p>
      ) : rows.length === 0 ? (
        <p className="ac-empty">No workflows are defined.</p>
      ) : (
        <ul className="ac-flowlist">
          {rows.slice(0, 5).map((f) => {
            const h = flowHealth(f.outcome);
            return (
              <li key={f.id} data-health={h}>
                <span className={`ac-dot${h === "unknown" ? " hatch" : ""}`} aria-hidden="true" />
                <span className="ac-flow-t">{f.title}</span>
                <span className="ac-flow-o">
                  {f.outcome === "never" ? "never run" : `${f.outcome}${f.at ? ` · ${rel(f.at, now)}` : ""}`}
                </span>
              </li>
            );
          })}
        </ul>
      )}
      <h3>Next up</h3>
      {work.error ? (
        <Unknown>Next runs are not known: the work board could not be read.</Unknown>
      ) : next.length === 0 ? (
        <p className="ac-empty">{work.data ? "Nothing scheduled with a recorded next run." : "Reading…"}</p>
      ) : (
        <ul className="ac-nextlist">
          {next.map((j) => (
            <li key={j.name}>
              <time dateTime={j.next_due ?? undefined}>
                {new Date(j.next_due ?? "").toLocaleString([], {
                  weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false,
                })}
              </time>
              <span>{j.name.replace(/_/g, " ")}</span>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

// ------------------------------------------------------------ marketing

const HISTORY_DAYS = 7;
const DAY_MS = 86_400_000;

/** One number: the check's value with its unit and read time, the change
 *  since the last reading that counted, and its last 7 days. */
export function FigurePartView({ part, fresh = false, now = new Date(), note = null }: {
  part: FigurePart;
  fresh?: boolean;
  now?: Date;
  note?: string | null;
}): JSX.Element {
  // Read again only when the check has read again: a new read time is the
  // only thing that can add a point.
  const history = useFresh(
    () => api.checkHistory(part.module, part.name, HISTORY_DAYS),
    [part.check, part.readAtIso]);
  const hist = readOf(history);
  const change = hist.data && part.state !== "unknown"
    ? changeSince({ value: part.value, unit: part.unit, health: part.health,
                    readAt: part.readAt }, hist.data.points)
    : null;
  const marker = change ? changeLabel(change, now) : null;
  const to = now.getTime();
  return (
    <div className="ac-part" data-state={part.state} data-fresh={fresh || undefined}
         data-health={part.state === "unknown" ? "unknown" : part.state === "ok" ? "ok" : "degraded"}>
      <span className="ac-part-label">{part.label}</span>
      <span className="ac-part-num">
        {part.state === "unknown" ? (
          <span className="ac-fig-value ac-unknown-word hatch-edge">Unknown</span>
        ) : part.display ? (
          <>
            <span className="ac-fig-value">{part.display.text}</span>
            {part.display.unit && <span className="ac-fig-unit">{part.display.unit}</span>}
          </>
        ) : null}
        {marker && change && (
          <span className="ac-change" data-dir={change.dir}
                title={`${change.dir === "up" ? "Up" : "Down"} ${marker.amount} ${marker.since}`}>
            <span aria-hidden="true">{marker.mark}</span>
            <span className="sr">{change.dir === "up" ? "up" : "down"}</span> {marker.amount}{" "}
            <small>{marker.since}</small>
          </span>
        )}
      </span>
      {part.text && <span className="ac-fig-text">{part.text}</span>}
      {part.state === "warn" && <span className="ac-word">{part.word}</span>}
      <span className="ac-fig-read">
        {part.readAt ? `${part.state === "unknown" ? "last read" : "read"} ${part.readAt.toLocaleString([], {
          day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false,
        })}` : "no reading"}
      </span>
      {hist.error ? (
        <p className="ac-spark-unknown hatch-edge" data-health="unknown">
          History unknown: {hist.error}
        </p>
      ) : hist.data ? (
        <ReadingsChart series={shapeSeries(hist.data.points, hist.data.stale_after_s)}
                   from={to - HISTORY_DAYS * DAY_MS} to={to}
                   label={`${part.label}, last ${HISTORY_DAYS} days`} note={note} />
      ) : null}
    </div>
  );
}

export function FigureTile({ f, fresh = new Set<string>(), now = new Date() }: {
  f: Figure;
  fresh?: Set<string>;
  now?: Date;
}): JSX.Element {
  const states = f.parts.map((p) => p.state);
  const tileState = states.every((s) => s === "unknown") ? "unknown"
    : states.includes("warn") ? "warn" : states.includes("unknown") ? "mixed" : "ok";
  return (
    // A tile whose numbers disagree (one read, one UNKNOWN) takes no colour
    // of its own: each number carries its own treatment.
    <li className="ac-fig" data-state={tileState}
        data-health={tileState === "unknown" ? "unknown" : tileState === "warn" ? "degraded"
          : tileState === "mixed" ? undefined : "ok"}>
      <Link to={f.to} className="ac-fig-label">{f.label}</Link>
      {f.parts.map((p) => (
        <FigurePartView key={p.check} part={p} fresh={fresh.has(p.check)} now={now}
                        note={f.chartNote} />
      ))}
      {f.lines.length > 0 && (
        <ul className="ac-fig-lines">{f.lines.map((l) => <li key={l}>{l}</li>)}</ul>
      )}
    </li>
  );
}

/** The checks whose read time moved while the page was open, held for a few
 *  seconds so each changed figure can be highlighted once. */
export function useFreshlyRead(stamps: Record<string, string | null>, holdMs = 4000): Set<string> {
  const seen = useRef(new Map<string, string | null>());
  const timers = useRef<number[]>([]);
  const [fresh, setFresh] = useState<Set<string>>(() => new Set());
  const signature = JSON.stringify(stamps);
  useEffect(() => {
    const keys = freshlyRead(seen.current, stamps);
    for (const [k, v] of Object.entries(stamps)) if (v !== null) seen.current.set(k, v);
    if (keys.length === 0) return;
    setFresh((prev) => new Set([...prev, ...keys]));
    timers.current.push(window.setTimeout(() => setFresh((prev) => {
      const next = new Set(prev);
      for (const k of keys) next.delete(k);
      return next;
    }), holdMs));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature]);
  useEffect(() => (): void => { for (const t of timers.current) window.clearTimeout(t); }, []);
  return fresh;
}

function Marketing({ overview, now }: { overview: Fresh<Overview | null>; now: Date }): JSX.Element {
  const read = readOf(overview);
  const figures = marketingFigures(read.error ? null : read.data?.modules ?? null, read.error);
  const stamps = Object.fromEntries(
    figures.flatMap((f) => f.parts.map((p) => [p.check, p.readAtIso])));
  const fresh = useFreshlyRead(stamps);
  return (
    <Section id="marketing" title="Marketing" more="/marketing"
             stamp="each number shows when its check read it">
      <ul className="ac-figs">
        {figures.map((f) => <FigureTile key={f.key} f={f} fresh={fresh} now={now} />)}
      </ul>
      <p className="ac-note">
        Each number is one check's own reading. ▲/▼ is the change since the check's
        last reading that counted, at the time shown. Charts are the stored readings
        of the last 7 days: a break is a stretch with no reading, and a grey hollow
        mark is a reading that could not be taken.
      </p>
    </Section>
  );
}

// ----------------------------------------------------------------- page

export function Home({ reloadKey, voice, overview, counters, gaze = null }: {
  reloadKey: number;
  voice: ReturnType<typeof useVoice>;
  /** The shell's own reads, passed down rather than fetched twice. */
  overview: Fresh<Overview | null>;
  counters: Fresh<StatusCounters | null>;
  gaze?: { x: number; y: number } | null;
}): JSX.Element {
  const roster = useFresh(() => api.agents(), [reloadKey]);
  const board = useFresh(() => api.tasksBoard(), [reloadKey]);
  const agenda = useFresh(() => api.agenda(WEEK_HOURS), [reloadKey]);
  const flows = useFresh(() => api.workflows(), [reloadKey]);
  // Breakers publish nothing on the stream, so the chains are read with the
  // shell's refresh: every event, and every 30 seconds behind the stream.
  const models = useFresh(() => api.models(), [reloadKey]);
  const now = new Date();
  // The week's health is hourly samples: read once an hour, and on reload.
  const hour = Math.floor(now.getTime() / 3_600_000);
  const healthPast = useFresh(() => api.healthHistory(7), [hour]);
  const c = readOf(counters).data;

  return (
    <div className="ac-home ac-skin">
      <div className="ac-hello">
        <h1>{greeting(now)}, Miro.</h1>
        <p>
          {now.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" })}
          {c && c.watch_only && " · watch-only: ATLAS reads, it does not act"}
        </p>
      </div>
      <div className="ac-grid">
        <NeedsYou board={board} now={now.getTime()} />
        <Week agenda={agenda} board={board} now={now} />
        <Atlas voice={voice} gaze={gaze} />
        <HealthStrip overview={overview} history={healthPast} now={now} />
        <Agents roster={roster} board={board} models={models} now={now.getTime()} />
        <Workflows flows={flows} board={board} now={now.getTime()} />
        <Marketing overview={overview} now={now} />
      </div>
    </div>
  );
}

function greeting(now: Date): string {
  const h = now.getHours();
  return h < 5 ? "Good night" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}
