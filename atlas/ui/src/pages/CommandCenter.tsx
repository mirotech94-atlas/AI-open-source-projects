import { useEffect, useRef, useState, type JSX, type ReactNode } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import { api, ApiError, type Ambient, type Overview, type StatusCounters } from "../api";
import { TABS } from "../nav";
import { useFresh, type Fresh } from "../hooks/useFresh";
import type { useVoice } from "../voice/useVoice";
import { talkCaption } from "../voice/detectorCore";
import { tapAction } from "../voice/interrupt";
import { MuteButton, StopButton } from "../voice/VoiceOverlay";
import { hhmm, readOf, weekDays, WEEK_HOURS } from "./homeModel";
import { Wordmark } from "../components/Wordmark";
import { overall, statusPill } from "./commandModel";
import { crumbOf, healthMini } from "./shellModel";
import { CPanel, Unread, stamp } from "./cmdParts";
import { pendingDecisions } from "./alertsModel";
import { decisionButtons, decisionNote, type DecisionNote } from "./opsFloorModel";
import { academyLabel, isRetiredText, isHiddenModule, safeText } from "./retired";
import { CommandSections } from "./CommandSections";
import { nextUp } from "./commandSections";
import { autopilotLine } from "./autopilotModel";
import { WORDMARK } from "./deskModel";
import { useWhy } from "../components/WhyPanel";
import { readTime } from "./healthWhy";
import type { LiveRead } from "../hooks/useLiveRead";

export { CPanel, Unread, stamp };

/**
 * ATLAS · Command Center -- the home page (Miro, 2026-10-06: "make the
 * ATLAS frontend tech based ... include all necessary things ... if
 * something is not needed to check all the time don't put it").
 *
 * Reads only. Every figure carries the time it was read; anything that
 * could not be read says Unknown in grey, dashed and unlit, and never
 * shares a treatment with OK (invariants 6 and 7). Nothing here acts on
 * the world: the one write is Decline/Approve on a pending Tier 2 card
 * (Miro 2026-10-29: GUI decisions), which goes through `decide` exactly as
 * Slack does and is refused for approve while watch-only.
 */

type Voice = ReturnType<typeof useVoice>;

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

/** The ATLAS Academy mark, the storefront's own tile (`ui/public/favicon.svg`
 *  is a copy of atlas/modules/academy/assets/icon.svg). Decorative: the link
 *  around it carries the name. */
export function CommandMark({ size = 30 }: { size?: number }): JSX.Element {
  return <img src="/favicon.svg" width={size} height={size} alt="" className="cmd-mark" />;
}

/** The header: the two tabs, the clock, health and what needs Miro. No
 *  voice tab and no sign-out (Miro, 2026-10-28): the voice loop answers the
 *  footer mic and the wake word, and an expired session lands on the login
 *  gate by itself. */
export function CommandHeader({ overview, waiting, feed }: {
  overview: LiveRead<Overview | null>;
  waiting: number | null;
  feed: "live" | "connecting" | "dropped";
}): JSX.Element {
  const read = readOf(overview);
  const o = overall(read.error ? null : read.data?.modules ?? null);
  const pill = statusPill(o, read.error !== null || !read.data);
  const why = useWhy();
  // The pill's own read time: when it last read, or when the read last failed.
  const pillAt = read.error !== null
    ? `read failed ${readTime(overview.attemptedAt ?? null).replace("read ", "")}`
    : readTime(overview.fetchedAt);
  const mini = healthMini(o, read.error !== null || !read.data);
  const { pathname } = useLocation();
  return (
    <>
      <aside className="cmd-side">
        <Link to="/" className="cmd-brand" aria-label={`${WORDMARK}, home`}>
          <CommandMark size={28} />
          <Wordmark />
        </Link>
        <span className="v2-eyebrow cmd-side-cap">Platform admin</span>
        <span className="v2-eyebrow cmd-side-label">Workspace</span>
        <nav className="cmd-tabs" aria-label="Main">
          {TABS.map((t) => (
            <NavLink key={t.to} to={t.to} end={t.to === "/"}
                     className={({ isActive }) => (isActive ? "on" : "")}>{t.label}</NavLink>
          ))}
        </nav>
        <button type="button" className={`cmd-mini${mini.failed ? " hatch-edge" : ""}`}
                data-health={mini.failed ? "unknown" : undefined} onClick={why}
                aria-haspopup="dialog" aria-label="Checks summary: open the checks behind it">
          {mini.failed ? (
            <>
              <span className="cmd-word-unknown">Unknown</span>
              <span className="cmd-mini-at">{pillAt}</span>
            </>
          ) : (
            <>
              {mini.lines.map((l) => (
                <span key={l.health} className="cmd-mini-line" data-health={l.health}>
                  <i className="cmd-sq" aria-hidden="true" />
                  <b>{l.count}</b><span>{l.word}</span>
                </span>
              ))}
              <span className="cmd-mini-at">{pillAt}</span>
            </>
          )}
        </button>
        <span className="cmd-side-role">Platform owner</span>
      </aside>
      <header className="cmd-top">
        <span className="cmd-crumb">Atlas <span aria-hidden="true">/</span> <b>{crumbOf(pathname)}</b></span>
        <Clock />
        <button type="button" className="cmd-pill" data-health={pill.health} onClick={why}
                aria-haspopup="dialog"
                title={(read.data ? `${o.ok} OK, ${o.degraded} degraded, ${o.failing} failing, ${o.unknown} unknown`
                  : "the checks have not been read") + ": click for the checks behind it"}>
          <i aria-hidden="true" />{pill.text}<small>{pillAt}</small>
        </button>
        <Link to={{ pathname: "/", hash: "#decisions" }} className="cmd-needs" data-zero={waiting === 0 || undefined}
              data-health={waiting === null ? "unknown" : undefined}>
          <b>{waiting === null ? "?" : waiting}</b>
          <span>{waiting === null ? "needs you: unknown" : "need you"}</span>
        </Link>
        <span className="cmd-feed" data-state={feed}>
          <i aria-hidden="true" />{feed === "live" ? "Live" : feed === "connecting" ? "Connecting" : "Feed down"}
        </span>
      </header>
    </>
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

// ------------------------------------------------------------ sections

export function Schedule({ agenda, board, now }: {
  agenda: Fresh<Awaited<ReturnType<typeof api.agenda>>>;
  board: Fresh<Awaited<ReturnType<typeof api.tasksBoard>>>;
  now: Date;
}): JSX.Element {
  const diary = readOf(agenda);
  const work = readOf(board);
  // Board jobs can be named after a retired lane: those are dropped before
  // they reach a day (the diary's own entries are kept).
  const jobs = work.data ? {
    ...work.data,
    agents: work.data.agents.map((a) => ({
      ...a, jobs: a.jobs.filter((j) => !isRetiredText(j.name) && !isHiddenModule(a.name)),
    })),
  } : null;
  const days = weekDays({ now, agenda: diary.data, agendaError: diary.error,
                          agendaReadAt: agenda.fetchedAt, board: jobs });
  const why = diary.error ?? diary.data?.unavailable ?? null;
  return (
    <CPanel id="schedule" title="Coming up" at={stamp(agenda.fetchedAt, diary.error !== null)}>
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

type SessionRead = Awaited<ReturnType<typeof api.session>>;

/** Cards Needs you lists before "+n more": the page stays one screen. */
const DECISIONS_SHOWN = 4;

/** Coming up as one short line on the Command Center: the next three items
 *  with the diary's read time. The week itself is on the Agency tab. */
export function NextUp({ agenda, board, now }: {
  agenda: Fresh<Awaited<ReturnType<typeof api.agenda>>>;
  board: Fresh<Awaited<ReturnType<typeof api.tasksBoard>>>;
  now: Date;
}): JSX.Element {
  const diary = readOf(agenda);
  const work = readOf(board);
  const jobs = work.data ? {
    ...work.data,
    agents: work.data.agents.map((a) => ({
      ...a, jobs: a.jobs.filter((j) => !isRetiredText(j.name) && !isHiddenModule(a.name)),
    })),
  } : null;
  const days = weekDays({ now, agenda: diary.data, agendaError: diary.error,
                          agendaReadAt: agenda.fetchedAt, board: jobs });
  const why = diary.error ?? diary.data?.unavailable ?? null;
  const items = nextUp(days, now, 3);
  const day = (d: Date): string => (d.toDateString() === now.toDateString() ? ""
    : `${d.toLocaleDateString([], { weekday: "short" })} `);
  return (
    <p className="cmd-next" data-health={why ? "unknown" : undefined}>
      <Link to="/agency#schedule" className="cmd-next-k">Coming up</Link>
      {why ? <span className="cmd-word-unknown">Unknown</span>
        : items.length === 0 ? <span className="cmd-dim">nothing in the next week</span>
        : items.map((it, i) => (
          <span key={`${it.kind}-${i}`}><time>{day(it.at)}{hhmm(it.at)}</time> {safeText(it.title)}</span>
        ))}
      <span className="cmd-dim">{stamp(agenda.fetchedAt, diary.error !== null)}</span>
    </p>
  );
}

/** One Autopilot line (last cycle, fixed, waiting on Miro) with its read time.
 *  A null figure or a failed read is Unknown, never 0. */
export function AutopilotLine({ autopilot }: {
  autopilot: Fresh<Awaited<ReturnType<typeof api.autopilot>>>;
}): JSX.Element {
  const rd = readOf(autopilot);
  if (rd.error || !rd.data) {
    return (
      <p className="cmd-next" data-health="unknown">
        <span className="cmd-next-k">Autopilot</span>
        <span className="cmd-word-unknown">Unknown</span>
        <span className="cmd-dim">{stamp(autopilot.fetchedAt, rd.error !== null)}</span>
      </p>
    );
  }
  const l = autopilotLine(rd.data, hhmm);
  const bad = l.unknown.length > 0;
  return (
    <div>
      <p className="cmd-next" data-health={bad ? "unknown" : undefined}>
        <span>{l.text.split(/(Unknown|no cycle yet)/).map((part, i) =>
          part === "Unknown" || part === "no cycle yet"
            ? <span key={i} className="cmd-word-unknown">{part}</span> : part)}</span>
        <span className="cmd-dim">{stamp(autopilot.fetchedAt)}</span>
      </p>
      {l.items.length > 0 && (
        <ul className="cmd-dim cmd-ap-items">
          {l.items.map((it, i) => (
            <li key={i}>{safeText(it.subject)}: {safeText(it.cause)} → {safeText(it.fix)}
              {" "}({safeText(it.outcome)})</li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Needs you: the pending cards, with Decline and Approve on a pending
 *  Tier 2 card (Miro 2026-10-29: GUI decisions). The server re-checks
 *  everything (session, Origin, CSRF, tier from code, watch-only, a
 *  per-session rate); this page says its answer in plain words above the
 *  list, where it stays after a declined card leaves (a click never fails
 *  silently). Under watch-only Approve is labelled as having no effect, and
 *  the card stays pending. One request at a time: a ref, not state, guards
 *  it, so two clicks in one frame cannot both send. */
export function Decisions({ approvals, now, session = null }: {
  approvals: Fresh<Awaited<ReturnType<typeof api.approvals>>>;
  now: number;
  session?: Fresh<SessionRead | null> | null;
}): JSX.Element {
  const read = readOf(approvals);
  const pending = read.data ? pendingDecisions(read.data.proposals, now) : null;
  const csrf = session?.data?.csrf ?? null;
  // Fail safe: unless a read says otherwise, Approve is labelled as no effect.
  const watchOnly = session?.data?.watch_only ?? read.data?.watch_only ?? true;
  const [busy, setBusy] = useState<number | null>(null);
  const [note, setNote] = useState<DecisionNote | null>(null);
  const sending = useRef(false);
  const answer = (pid: number, verdict: "approve" | "decline"): void => {
    if (!csrf || sending.current) return;
    sending.current = true;
    setBusy(pid);
    api.decideProposal(pid, verdict, csrf)
      .then((r) => setNote(decisionNote(pid, verdict, {
        kind: "verdict", outcome: r.verdict.outcome, detail: r.verdict.detail,
      }, r.watch_only ?? watchOnly)))
      .catch((e: unknown) => setNote(decisionNote(pid, verdict, e instanceof ApiError
        ? { kind: "error", status: e.status, detail: e.message }
        : { kind: "error", status: null, detail: "" }, watchOnly)))
      .finally(() => { sending.current = false; setBusy(null); approvals.reload(); });
  };
  return (
    <section id="decisions" className="cmd-anchor cmd-w-decisions">
      <CPanel id="agency-decisions" title="Needs you"
              at={stamp(approvals.fetchedAt, read.error !== null)}>
        <p className="cmd-verdict cmd-verdict-last" role="status" aria-live="polite"
           data-tone={note?.tone} hidden={!note}>{note?.text}</p>
        {read.error ? <Unread>Pending cards: {safeText(read.error)}</Unread>
          : !pending ? <p className="cmd-empty">Reading…</p>
          : pending.rows.length === 0 ? <p className="cmd-empty">Nothing needs you.</p> : (
            <ul className="cs-list cs-cards">
              {pending.rows.slice(0, DECISIONS_SHOWN).map((d) => {
                const card = read.data?.proposals.find((p) => p.id === d.id);
                const buttons = card ? decisionButtons(card, watchOnly) : [];
                return (
                  <li key={d.id} data-expired={d.expiresIn === "expired" || undefined}>
                    <span className="cs-k">#{d.id} {d.kind}</span>
                    <span className="cs-v">{academyLabel(d.action.replace(/[._]/g, " "))}
                      {d.tag && <small> · {safeText(d.tag)}</small>} <small>· Tier {d.tier}</small></span>
                    <span className="cs-t">{d.expiresIn === "expired" ? "expired, will not run"
                      : d.expiresIn === "—" ? "no expiry" : `expires in ${d.expiresIn}`}</span>
                    {buttons.length > 0 && (
                      <span className="cmd-decide">
                        {buttons.map((b) => (
                          <button key={b.verdict} type="button" data-verdict={b.verdict}
                                  className="cmd-decide-btn"
                                  disabled={!csrf || busy !== null}
                                  onClick={() => answer(d.id, b.verdict)}>
                            {busy === d.id ? "Sending…" : b.label}
                          </button>
                        ))}
                      </span>
                    )}
                  </li>
                );
              })}
              {pending.rows.length > DECISIONS_SHOWN && (
                <li className="cmd-more-n">+{pending.rows.length - DECISIONS_SHOWN} more
                  {" "}<small>(oldest first; answer these, or in Slack)</small></li>
              )}
            </ul>
          )}
        <p className="cmd-note">
          Decline or approve a Tier 2 card here or in Slack (#agency-decisions); silence
          expires a card.{watchOnly ? " Watch-only: an approve changes nothing." : ""}
        </p>
        {!csrf && pending && pending.rows.length > 0 && (
          <p className="cmd-note">Session not read: the buttons stay off until it is.</p>
        )}
        {pending && pending.hidden > 0 && (
          <p className="cmd-note">{pending.hidden} from retired lanes not shown.</p>
        )}
      </CPanel>
    </section>
  );
}

// ---------------------------------------------------------------- page

export function CommandCenter({ reloadKey, overview }: {
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
  // `voice`, `counters`, `gaze` and `agentsRunning` are still passed by the
  // shell; the voice loop lives in the footer.
  const board = useFresh(() => api.tasksBoard(), [reloadKey]);
  const agenda = useFresh(() => api.agenda(WEEK_HOURS), [reloadKey]);
  const approvals = useFresh(() => api.approvals(), [reloadKey]);
  const session = useFresh(() => api.session(), [reloadKey]);
  const autopilot = useFresh(() => api.autopilot(), [reloadKey]);
  useEffect(() => {
    const before = document.title;
    document.title = `${WORDMARK} · Command Center`;
    return (): void => { document.title = before; };
  }, []);
  const { hash } = useLocation();
  useEffect(() => {
    if (hash) document.getElementById(hash.slice(1))?.scrollIntoView();
  }, [hash]);
  const now = new Date();
  const ap = readOf(approvals);
  const pending = ap.data && !ap.error
    ? { count: pendingDecisions(ap.data.proposals, now.getTime()).rows.length,
        readAt: approvals.fetchedAt?.toISOString() ?? null }
    : null;

  return (
    <div className="cmd-page">
      <CommandSections reloadKey={reloadKey} overview={overview} pending={pending}
                       watchOnly={session.data?.watch_only ?? ap.data?.watch_only ?? null}
                       needsYou={<Decisions approvals={approvals} now={now.getTime()}
                                            session={session} />}
                       nextUp={<><NextUp agenda={agenda} board={board} now={now} />
                                 <AutopilotLine autopilot={autopilot} /></>} />
      {/* Everything else is on the Agency tab (Miro 2026-10-29: keep it short). */}
    </div>
  );
}
