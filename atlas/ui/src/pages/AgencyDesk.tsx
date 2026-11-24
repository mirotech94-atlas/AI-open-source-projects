import { useEffect, useRef, useState, type JSX, type ReactNode } from "react";
import type {
  ActivityRow, AgentSummary, Approvals, Health, Overview, ReportSummary,
} from "../api";
import { Unread, stamp } from "./cmdParts";
import { PreviewsPanel } from "./PreviewsPanel";
import { hhmm, readOf, rel, type Read } from "./homeModel";
import { pendingDecisions } from "./alertsModel";
import { safeText } from "./retired";
import type { SitesOverview } from "./sitesModel";
import {
  EMPTY, SECRET_WORD, SECTIONS, STAGES, WORDMARK, boardIsEmpty, buildBoard,
  calendarGroups, connectionRows, healthTreatment, measured, paletteMatches, salesReading,
  salesSummary,
  secretRows, teamRows, weeklyReports, type AcademyCalendar, type LeadLike, type MeasuresRead,
  type Section, type SectionId,
} from "./deskModel";
import {
  projectRows, remediationRows, type ProjectsRead, type RemediationRead,
} from "./remediationModel";

/**
 * ATLAS Agency: the Command Center's Agency desk sections, each an anchor
 * (`/#<id>`). Reads only. Every section carries its read time; a read
 * that failed says UNKNOWN with the reason; a read that found nothing says
 * what is missing and draws no placeholder row.
 */

export interface DeskProps {
  overview: Read<Overview>;
  leads: Read<{ leads: LeadLike[]; total: number }>;
  sites: Read<SitesOverview>;
  approvals: Read<Approvals>;
  calendar: Read<AcademyCalendar>;
  activity: Read<{ activity: ActivityRow[] }>;
  reports: Read<{ reports: ReportSummary[] }>;
  roster: Read<{ agents: AgentSummary[] }>;
  secrets: Read<{ secrets: Record<string, unknown> }>;
  /** GET /api/projects and GET /api/remediation (the project board). */
  projects: Read<ProjectsRead>;
  remediation: Read<RemediationRead>;
  /** SEO and Research figures. Absent: nothing measures them yet, which is
   *  "not measured", never UNKNOWN. */
  seo?: Read<MeasuresRead>;
  research?: Read<MeasuresRead>;
  now: Date;
  /** The eight seat cards, drawn inside Team. */
  seats?: ReactNode;
  /** The week's schedule panel, drawn with the calendar. */
  schedule?: ReactNode;
}

/** Rows a board column or a calendar series shows before "+n more"
 *  (the owner 2026-10-29: each section scannable at a glance). */
const COL_SHOWN = 8;
const POSTED_SHOWN = 3;

export function HealthWord({ health }: { health: Health }): JSX.Element {
  const t = healthTreatment(health);
  return <span className={t.className} data-health={health}>{t.word}</span>;
}

function dayTime(iso: string | null): string {
  if (!iso) return "no time recorded";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "no time recorded";
  return d.toLocaleString([], {
    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

function Skeleton(): JSX.Element {
  return (
    <div className="desk-skel" aria-busy="true" aria-label="Reading">
      <i /><i /><i />
    </div>
  );
}

function Sec({ id, at, failed, children }: {
  id: SectionId; at: Date | null; failed: boolean; children: ReactNode;
}): JSX.Element {
  const s = SECTIONS.find((x) => x.id === id) as Section;
  return (
    <section id={id} className="desk-sec" aria-labelledby={`desk-${id}-h`}>
      <header>
        <h2 id={`desk-${id}-h`}>{s.label}</h2>
        <span className="desk-at">{stamp(at, failed)}</span>
      </header>
      {children}
    </section>
  );
}

/** error -> UNKNOWN with the reason; not read yet -> skeleton; else body. */
function body<T>(read: Read<T>, what: string, draw: (data: T) => JSX.Element): JSX.Element {
  const r = readOf(read);
  if (r.error) return <Unread>{what} could not be read: {safeText(r.error)}</Unread>;
  if (r.data === null) return <Skeleton />;
  return draw(r.data);
}

function Empty({ id }: { id: SectionId }): JSX.Element {
  return <p className="desk-empty" data-empty={id}>{EMPTY[id]}</p>;
}

// ----------------------------------------------------------- sections

function Clients({ p }: { p: DeskProps }): JSX.Element {
  const [showClosed, setShowClosed] = useState(false);
  const sites = readOf(p.sites);
  const cards = readOf(p.approvals);
  return (
    <Sec id="clients" at={p.leads.fetchedAt} failed={p.leads.error !== null}>
      {body(p.leads, "The leads", (data) => {
        const board = buildBoard(data.leads, sites.data?.sites ?? null,
                                 cards.data?.proposals ?? null, p.now.getTime());
        if (boardIsEmpty(board)) return <Empty id="clients" />;
        return (
          <>
            <div className="desk-board">
              {STAGES.map((s) => (
                <div key={s.key} className="desk-col" data-stage={s.key}>
                  <h3>{s.label} <b>{board.columns[s.key].length}</b></h3>
                  {board.columns[s.key].length === 0 ? <p className="desk-none">None.</p> : (
                    <ul>
                      {board.columns[s.key].slice(0, COL_SHOWN).map((c) => (
                        <li key={c.id} data-lead={c.id}>
                          <span>{safeText(c.name)}</span>
                          <small>{safeText(c.category)}
                            {c.stage === "live" && c.deployedAt ? ` · live since ${dayTime(c.deployedAt)}` : ""}
                            {c.cardId !== null ? ` · card #${c.cardId}` : ""}</small>
                        </li>
                      ))}
                      {board.columns[s.key].length > COL_SHOWN && (
                        <li className="desk-more">
                          +{board.columns[s.key].length - COL_SHOWN} more
                        </li>
                      )}
                    </ul>
                  )}
                </div>
              ))}
            </div>
            <div className="desk-chips">
              {board.closed.length > 0 && (
                <button type="button" className="desk-chip" aria-expanded={showClosed}
                        onClick={() => setShowClosed((v) => !v)}>
                  closed ({board.closed.length})
                </button>
              )}
              {board.unsorted > 0 && (
                <span className="desk-note">
                  <HealthWord health="unknown" /> {board.unsorted} with a status the board does not know
                </span>
              )}
            </div>
            {showClosed && (
              <ul className="desk-list" data-closed="">
                {board.closed.map((c) => <li key={c.id}>{safeText(c.name)}</li>)}
              </ul>
            )}
            <PreviewsPanel reloadKey={p.leads.fetchedAt?.getTime() ?? 0} />
            {sites.error && (
              <Unread>The client sites could not be read, so Live is not shown: {safeText(sites.error)}</Unread>
            )}
            {data.leads.length < data.total && (
              <p className="desk-note">Showing the first {data.leads.length} of {data.total} leads.</p>
            )}
          </>
        );
      })}
    </Sec>
  );
}

function Projects({ p }: { p: DeskProps }): JSX.Element {
  return (
    <Sec id="projects" at={p.projects.fetchedAt} failed={p.projects.error !== null}>
      {body(p.projects, "The project board", (data) => {
        const clients = projectRows(data.projects);
        if (clients.length === 0) return <Empty id="projects" />;
        return (
          <ul className="desk-list desk-projects">
            {clients.map((c) => (
              <li key={c.client} data-client={c.client}>
                <span>{c.client}</span>
                <ul>
                  {c.projects.map((pr) => (
                    <li key={pr.id} data-status={pr.status}>
                      {pr.name} <small>{pr.status} · {pr.openTasks} open
                        {" "}· read {hhmm(data.served_at)}</small>
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        );
      })}
    </Sec>
  );
}

function NotMeasured({ children }: { children: ReactNode }): JSX.Element {
  return <p className="desk-nm" data-measured="no">Not measured: {children}</p>;
}

function SalesFact({ label, p, check }: {
  label: string; p: DeskProps; check: string;
}): JSX.Element {
  const r = salesReading(p.overview.data?.modules ?? null, check);
  return (
    <div data-check={`sales.${check}`}><dt>{label}</dt><dd>
      {r.health === null ? <span className="desk-nm">not measured</span>
        : r.value === null ? <HealthWord health={r.health === "ok" ? "unknown" : r.health} />
        : <b>{r.value}</b>}{" "}
      <small>{r.health === null ? "the sales module is not reporting"
        : r.readAt ? `read ${hhmm(r.readAt)}` : "no read time"}</small></dd></div>
  );
}

function Sales({ p }: { p: DeskProps }): JSX.Element {
  const cards = readOf(p.approvals);
  return (
    <Sec id="sales" at={p.leads.fetchedAt} failed={p.leads.error !== null}>
      {body(p.leads, "The leads", (data) => {
        const decisions = cards.data ? pendingDecisions(cards.data.proposals, p.now.getTime()).rows
          : null;
        const s = salesSummary(data.leads, decisions);
        if (data.leads.length === 0 && s.openCards === 0) return <Empty id="sales" />;
        return (
          <>
            <dl className="desk-facts">
              {s.stages.map((st) => (
                <div key={st.key}><dt>{st.label}</dt><dd><b>{st.count}</b>{" "}
                  <small>{stamp(p.leads.fetchedAt)}</small></dd></div>
              ))}
              <div><dt>Open sales cards</dt><dd>{cards.error
                ? <HealthWord health="unknown" /> : s.openCards === null ? "Reading…"
                : <b>{s.openCards}</b>}{" "}
                <small>{stamp(p.approvals.fetchedAt, cards.error !== null)}</small></dd></div>
              <SalesFact label="Follow-ups due" p={p} check="followups_due" />
              <SalesFact label="Outreach cards left today" p={p} check="cap_left" />
            </dl>
            {data.leads.length < data.total && (
              <p className="desk-note">Stages from the first {data.leads.length} of {data.total} leads.</p>
            )}
          </>
        );
      })}
    </Sec>
  );
}

function Measures({ id, read, what, none }: {
  id: "seo" | "research"; read: Read<MeasuresRead> | undefined; what: string; none: string;
}): JSX.Element {
  if (read === undefined) {
    return (
      <Sec id={id} at={null} failed={false}>
        <NotMeasured>{none}</NotMeasured>
      </Sec>
    );
  }
  return (
    <Sec id={id} at={read.fetchedAt} failed={read.error !== null}>
      {body(read, what, (data) => {
        const rows = measured(data.rows);
        if (rows.length === 0) return <Empty id={id} />;
        return (
          <ul className="desk-list desk-measures">
            {rows.map((r, i) => (
              <li key={`${r.label}-${i}`} data-measured={r.state === "measured" ? "yes" : "no"}>
                <span>{r.label}</span>{" "}
                {r.state === "measured"
                  ? <><b>{r.value.toLocaleString("en-US")}{r.unit}</b>{" "}<small>read {hhmm(r.readAt)}</small></>
                  : <span className="desk-nm">not measured</span>}
              </li>
            ))}
          </ul>
        );
      })}
    </Sec>
  );
}

function Remediation({ p }: { p: DeskProps }): JSX.Element {
  const t = p.now.getTime();
  return (
    <Sec id="remediation" at={p.remediation.fetchedAt} failed={p.remediation.error !== null}>
      {body(p.remediation, "The remediation tasks", (data) => {
        const { open, closed } = remediationRows(data.tasks);
        if (open.length === 0 && closed.length === 0) return <Empty id="remediation" />;
        return (
          <>
            <p className="desk-note"><b>{open.length}</b> open · read {hhmm(data.served_at)}</p>
            {open.length > 0 && (
              <ul className="desk-list desk-rem">
                {open.map((r) => (
                  <li key={r.id} data-task={r.id} data-status={r.status}>
                    <span>#{r.id} {r.title}</span>
                    <small>{r.owner} · {r.status}{r.createdAt ? ` · raised ${rel(r.createdAt, t)}` : ""}
                      {r.source ? ` · from ${r.source}` : ""}</small>
                    {r.fix && <small className="desk-fix">Fix: {r.fix}</small>}
                  </li>
                ))}
              </ul>
            )}
            {closed.length > 0 && (
              <p className="desk-note">{closed.length} recently closed (done or dropped).</p>
            )}
          </>
        );
      })}
    </Sec>
  );
}

function Calendar({ p }: { p: DeskProps }): JSX.Element {
  return (
    <Sec id="calendar" at={p.calendar.fetchedAt} failed={p.calendar.error !== null}>
      {body(p.calendar, "The content calendar", (data) => {
        const groups = calendarGroups(data);
        if (groups.length === 0) return <Empty id="calendar" />;
        return (
          <>
            <div className="desk-cal">
              {groups.map((g) => (
                <div key={g.key} className="desk-series" data-series={g.key}>
                  <h3>{g.name}</h3>
                  {g.nextUp.length > 0 && (
                    <ul className="desk-next">
                      {g.nextUp.map((n) => (
                        <li key={`n-${n.episode}`}>
                          <em>next up</em> #{n.episode} {n.title}
                        </li>
                      ))}
                    </ul>
                  )}
                  {g.posted.length > 0 && (
                    <ul className="desk-list">
                      {g.posted.slice(0, POSTED_SHOWN).map((r) => (
                        <li key={`p-${r.episode}`}>
                          <span>#{r.episode} {r.title}</span>
                          <small>{r.public ? "public" : "uploaded, not public"} · {dayTime(r.at)}</small>
                        </li>
                      ))}
                      {g.posted.length > POSTED_SHOWN && (
                        <li className="desk-more">+{g.posted.length - POSTED_SHOWN} more</li>
                      )}
                    </ul>
                  )}
                </div>
              ))}
            </div>
            <p className="desk-note">Next up is today&apos;s order; no date is set for it.</p>
            {data.notes.map((n, i) => <p key={i} className="desk-note">{safeText(n)}</p>)}
          </>
        );
      })}
    </Sec>
  );
}

function Reports({ p }: { p: DeskProps }): JSX.Element {
  return (
    <Sec id="reports" at={p.reports.fetchedAt} failed={p.reports.error !== null}>
      {body(p.reports, "The reports", (data) => {
        const { rows } = weeklyReports(data.reports);
        if (rows.length === 0) return <Empty id="reports" />;
        return (
          <ul className="desk-list">
            {rows.slice(0, 6).map((r) => (
              <li key={r.id}>
                <span>{safeText(r.title)}</span>
                <small>written {dayTime(r.at)}</small>
                {r.excerpt && <p>{safeText(r.excerpt)}</p>}
              </li>
            ))}
          </ul>
        );
      })}
    </Sec>
  );
}

function Team({ p }: { p: DeskProps }): JSX.Element {
  const t = p.now.getTime();
  const act = readOf(p.activity);
  return (
    <Sec id="team" at={p.roster.fetchedAt} failed={p.roster.error !== null}>
      {body(p.roster, "The roster", (data) => {
        const { rows } = teamRows(data.agents, act.data?.activity ?? null);
        if (rows.length === 0) return <Empty id="team" />;
        return (
          <ul className="desk-team">
            {rows.map((r) => (
              <li key={r.key} data-agent={r.key}>
                <span className="desk-team-name">{safeText(r.title)}</span>
                <small className="desk-team-role" title={safeText(r.role)}>{safeText(r.role)}</small>
                {r.status === "idle"
                  ? <span className="desk-h desk-h-idle" data-health="idle">IDLE</span>
                  : <HealthWord health={r.status} />}
                <small className="desk-team-when">{r.lastRunAt ? rel(r.lastRunAt, t)
                  : act.error && r.key === "academy" ? "unread" : "never"}</small>
                {r.line && r.detail && r.detail !== r.line ? (
                  <details className="desk-team-out">
                    <summary>{r.line}</summary>
                    <span>{r.detail}</span>
                  </details>
                ) : <small className="desk-team-out">{r.line ?? "—"}</small>}
                <small className="desk-team-next">{r.nextShiftAt
                  ? `next ${dayTime(r.nextShiftAt)}` : "no shift"}</small>
              </li>
            ))}
          </ul>
        );
      })}
      {p.seats}
    </Sec>
  );
}

function Settings({ p }: { p: DeskProps }): JSX.Element {
  return (
    <Sec id="settings" at={p.overview.fetchedAt} failed={p.overview.error !== null}>
      <h3 className="desk-sub">Connections</h3>
      {body(p.overview, "The connection checks", (data) => (
        <ul className="desk-conn">
          {connectionRows(data.modules).map((c) => (
            <li key={c.module} data-module={c.module}>
              <span>{c.module}</span> <HealthWord health={c.health} />
              <ul>
                {c.checks.map((k) => (
                  <li key={k.name}>
                    {k.name} <HealthWord health={k.health} />{" "}
                    <small>{k.readAt ? `read ${hhmm(k.readAt)}` : "no read time"}
                      {k.detail ? ` · ${k.detail}` : ""}</small>
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      ))}
      <h3 className="desk-sub">Secrets <span className="desk-at">
        {stamp(p.secrets.fetchedAt, p.secrets.error !== null)}</span></h3>
      {body(p.secrets, "The secrets list", (data) => {
        const rows = secretRows(data.secrets);
        if (rows.length === 0) return <p className="desk-empty">No secret name is listed.</p>;
        return (
          <ul className="desk-secrets">
            {rows.map((r) => (
              <li key={r.name} data-state={r.state}>
                <code>{r.name}</code>{" "}
                {r.state === "unknown" ? <HealthWord health="unknown" />
                  : <span className={`desk-secret-${r.state}`}>{SECRET_WORD[r.state]}</span>}
              </li>
            ))}
          </ul>
        );
      })}
      <p className="desk-note">Names only: a value never leaves the server. Enter one with
        bootstrap/set-secret.sh.</p>
    </Sec>
  );
}

// ------------------------------------------------------------ palette

export function Palette({ open, query, onQuery, onPick, onClose }: {
  open: boolean;
  query: string;
  onQuery: (q: string) => void;
  onPick: (id: SectionId) => void;
  onClose: () => void;
}): JSX.Element | null {
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { if (open) input.current?.focus(); }, [open]);
  if (!open) return null;
  const items = paletteMatches(query);
  return (
    <div className="desk-pal-back" onClick={onClose}>
      <div className="desk-pal" role="dialog" aria-label="Go to section"
           onClick={(e) => e.stopPropagation()}>
        <input ref={input} value={query} placeholder="Go to…" aria-label="Section"
               onChange={(e) => onQuery(e.target.value)}
               onKeyDown={(e) => {
                 if (e.key === "Escape") onClose();
                 if (e.key === "Enter" && items[0]) onPick(items[0].id);
               }} />
        <ul>
          {items.map((s) => (
            <li key={s.id} data-section={s.id}>
              <button type="button" onClick={() => onPick(s.id)}>{s.label}</button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export function DeskTop({ onPalette }: { onPalette: () => void }): JSX.Element {
  return (
    <header className="desk-top">
      <span className="desk-wordmark">{WORDMARK}</span>
      <nav aria-label="Sections">
        {SECTIONS.map((s) => <a key={s.id} href={`#${s.id}`}>{s.label}</a>)}
      </nav>
      <button type="button" className="desk-k" onClick={onPalette} aria-label="Go to section (Command K)">
        ⌘K
      </button>
    </header>
  );
}

/** Every section from the reads it is given: pure, so it can be tested. */
export function AgencyDeskBody(p: DeskProps): JSX.Element {
  return (
    <div className="desk-sections">
      {/* Decisions and activity are the top grid's panels, not repeated here. */}
      <Team p={p} />
      <Sales p={p} />
      <Measures id="seo" read={p.seo} what="The SEO figures"
                none="the SEO agent has not recorded an audit or a rank yet." />
      <Measures id="research" read={p.research} what="The research figures"
                none="the Research agent has not recorded a brief or a competitor reading yet." />
      <Remediation p={p} />
      {p.schedule}
      <Calendar p={p} />
      <Reports p={p} />
      <Clients p={p} />
      <Projects p={p} />
      <Settings p={p} />
    </div>
  );
}
