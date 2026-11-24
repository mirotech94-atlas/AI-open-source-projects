import { useEffect, type JSX, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import {
  api, type AgentSummary, type Lead, type MoneyRead, type Overview, type PipelineRead, type SpendRead,
} from "../api";
import { useFresh, type Fresh } from "../hooks/useFresh";
import { hhmm, readOf, type FigurePart, type Read } from "./homeModel";
import { overall } from "./commandModel";
import { healthSegments } from "./healthSegments";
import { CPanel, Unread, stamp } from "./cmdParts";
import {
  alarmLines, healthLines, moneyFigures, searchLines, siteLines, todayLines,
} from "./commandSections";
import { safeText } from "./retired";
import {
  alertPanel, rosterRow, shiftOutcome, summaryStrip, type RosterAgent,
} from "./opsFloorModel";
import { LeadsGrid, PipelineChart, ShiftsChart } from "./CommandCharts";
import { BootRing } from "../components/BootRing";
import { splitFlanks, type FlankUnit } from "./flankLayout";
import "../cmdSections.css";

/**
 * The Command Center, short (Miro, 2026-10-29: "don't make the Command
 * Center so long; keep the page short with only the most important
 * sections"). One screen: the summary strip, Roster, Needs you and one
 * compact Health & money panel. Everything else that used to sit here
 * (Pipeline, Money & costs with the free-tier pools, Alerts, Today, the full
 * Money and Health lists, Search & Sites, Coming up) is `AgencyMore`, on the
 * Agency tab: moved, not deleted. Every figure carries its read time;
 * Unknown is the grey dashed word.
 */

/** Site rows Search & Sites shows before "+n more" (Miro 2026-10-29). */
const SITES_SHOWN = 6;

const U = (): JSX.Element => <span className="cmd-word-unknown">Unknown</span>;

/** A number, or Unknown: never 0 for a figure that was not read. */
function Num({ v, suffix = "" }: { v: number | null; suffix?: string }): JSX.Element {
  return v === null ? <U /> : <b>{v.toLocaleString("en-US")}{suffix}</b>;
}

function Today({ reloadKey }: { reloadKey: number }): JSX.Element {
  const activity = useFresh(() => api.agencyActivity(), [reloadKey]);
  const read = readOf(activity);
  const lines = todayLines(read.data);
  return (
    <section id="activity" className="cmd-anchor">
      <CPanel id="s-today" title="Today" at={stamp(activity.fetchedAt, read.error !== null)}>
        {read.error && <Unread>Agent activity: {safeText(read.error)}</Unread>}
        <ul className="cs-list">
          {lines.map((l) => (
            <li key={l.name} data-health={l.health}>
              <i className="cmd-sq" aria-hidden="true" />
              <span className="cs-k">{l.label}</span>
              <span className="cs-v">{l.text === null ? <U /> : safeText(l.text)}</span>
              <span className="cs-t">{l.readAt ? `read ${hhmm(l.readAt)}` : "not read"}</span>
            </li>
          ))}
        </ul>
      </CPanel>
    </section>
  );
}

function Figure({ p }: { p: FigurePart }): JSX.Element {
  return (
    <li data-health={p.health}>
      <i className="cmd-sq" aria-hidden="true" />
      <span className="cs-k">{p.label}</span>
      <span className="cs-v">
        {p.state === "unknown" ? <><U /> <small>{safeText(p.text)}</small></>
          : p.display ? <b>{p.display.unit === "usd" ? `$${p.display.text}` : p.display.text}
              {p.display.unit && p.display.unit !== "usd" ? ` ${p.display.unit}` : ""}</b>
          : <small>{safeText(p.text)}</small>}
      </span>
      <span className="cs-t">{p.readAt ? `read ${hhmm(p.readAt)}` : "not read"}</span>
    </li>
  );
}

function Money({ overview }: { overview: Fresh<Overview | null> }): JSX.Element {
  const read = readOf(overview);
  const f = moneyFigures(read.error ? null : read.data?.modules ?? null, read.error);
  return (
    <CPanel id="s-money" title="Money" at={stamp(overview.fetchedAt, read.error !== null)}>
      <h3 className="cs-sub">In</h3>
      <ul className="cs-list">{f.in.map((p) => <Figure key={p.check} p={p} />)}</ul>
      <h3 className="cs-sub">Out</h3>
      <ul className="cs-list">{f.out.map((p) => <Figure key={p.check} p={p} />)}</ul>
    </CPanel>
  );
}

function HealthSection({ overview }: { overview: Fresh<Overview | null> }): JSX.Element {
  const read = readOf(overview);
  const modules = read.error ? null : read.data?.modules ?? null;
  const o = overall(modules);
  const lines = healthLines(modules);
  return (
    <CPanel id="s-health" title="Health" at={stamp(overview.fetchedAt, read.error !== null)}>
      {read.error && <Unread>Checks: {safeText(read.error)}</Unread>}
      <dl className="cs-counts">
        {(["ok", "degraded", "failing", "unknown"] as const).map((h) => (
          <div key={h} data-health={h}>
            <dt>{h === "ok" ? "OK" : h}</dt><dd>{modules ? o[h] : <U />}</dd>
          </div>
        ))}
      </dl>
      <p className="cs-counts-t">{modules && !read.error && overview.fetchedAt
        ? `read ${hhmm(overview.fetchedAt)}` : "not read"}</p>
      {modules && lines.length === 0 && <p className="cmd-empty">All checks OK.</p>}
      <ul className="cs-list">
        {lines.slice(0, 8).map((l) => (
          <li key={l.module} data-health={l.health}>
            <i className="cmd-sq" aria-hidden="true" />
            <span className="cs-k">{l.title}</span>
            <span className="cs-v">
              {l.health === "unknown" && <><U />{" "}</>}{safeText(l.why)}
            </span>
            <span className="cs-t">{l.readAt ? `read ${hhmm(l.readAt)}` : "not read"}</span>
          </li>
        ))}
        {lines.length > 8 && <li className="cmd-more-n">+{lines.length - 8} more</li>}
      </ul>
    </CPanel>
  );
}

function SearchSites({ reloadKey, now }: { reloadKey: number; now: Date }): JSX.Element {
  const seo = useFresh(() => api.agencySeo(), [reloadKey]);
  const sites = useFresh(() => api.sites(), [reloadKey]);
  const s = readOf(seo);
  const w = readOf(sites);
  const search = searchLines(s.data);
  const live = siteLines(w.data, now);
  return (
    <CPanel id="s-search" title="Search & Sites" at={stamp(seo.fetchedAt, s.error !== null)}>
      {s.error && <Unread>Search Console / PageSpeed: {safeText(s.error)}</Unread>}
      {s.data && search.length === 0 && <p className="cmd-empty">No site tracked in Search Console.</p>}
      {search.length > 0 && (
        <ul className="cs-list cs-search">
          {search.map((l) => (
            <li key={l.domain}>
              <span className="cs-k">{l.domain}</span>
              <span className="cs-v">
                <Num v={l.clicks} /> clicks · pos <Num v={l.position === null ? null
                  : Math.round(l.position * 10) / 10} /> · PageSpeed <Num v={l.mobile} />
                /<Num v={l.desktop} />
              </span>
              <span className="cs-t">{l.searchAt || l.vitalsAt
                ? `read ${hhmm((l.searchAt ?? l.vitalsAt) as string)}` : "not read"}</span>
            </li>
          ))}
        </ul>
      )}
      <h3 className="cs-sub">Sites <span>{stamp(sites.fetchedAt, w.error !== null)}</span></h3>
      {w.error && <Unread>Sites: {safeText(w.error)}</Unread>}
      {w.data && live.length === 0 && <p className="cmd-empty">No client sites.</p>}
      <ul className="cs-list">
        {live.slice(0, SITES_SHOWN).map((l) => (
          <li key={l.slug} data-health={l.health}>
            <i className="cmd-sq" aria-hidden="true" />
            <span className="cs-k">{l.host}</span>
            <span className="cs-v">
              {l.health === "unknown" ? <U /> : <span className="cs-word">{l.health}</span>}
              {l.docs === null ? <> · docs <U /></> : l.docs.map((d) => (
                <span key={d.id}> · {d.url
                  ? <a href={d.url} target="_blank" rel="noreferrer noopener">{safeText(d.title)}</a>
                  : <>{safeText(d.title)} <U /></>}</span>
              ))}
            </span>
            <span className="cs-t">{l.readAt ? `read ${hhmm(l.readAt)}` : "not read"}</span>
          </li>
        ))}
        {live.length > SITES_SHOWN && (
          <li className="cmd-more-n">+{live.length - SITES_SHOWN} more</li>
        )}
      </ul>
      <p className="cmd-note"><Link to={{ pathname: "/agency", hash: "#clients" }}>All sites</Link></p>
    </CPanel>
  );
}

// ------------------------------------------------------------ ops floor

function toRoster(a: AgentSummary): RosterAgent {
  return {
    name: a.name, role: a.role ?? a.title, status: a.status ?? null,
    shift_state: a.shift_state ?? null, last_shift_at: a.last_shift_at ?? null,
    last_shift_outcome: a.last_shift_outcome ?? null,
    // The detail is of the same last run only when the roster line is set.
    last_shift_outcome_detail: a.last_shift_outcome ? a.last_outcome_detail ?? null : null,
    next_shift_at: a.next_shift_at,
    attempts_today: a.attempts_today ?? null, today_output: a.today_output ?? null,
    alert_unrouted: a.alert_unrouted ?? false, read_at: a.read_at ?? null,
  };
}

const readTime = (at: string | null): string => (at ? `read ${hhmm(at)}` : "not read");

/** Strip figures whose detail lives on the Agency tab: the number links there. */
const STRIP_LINKS: Readonly<Record<string, string>> = { pipeline: "/agency#pipeline" };

function Strip({ items, side = null }: {
  items: ReturnType<typeof summaryStrip>; side?: "left" | "right" | null;
}): JSX.Element {
  return (
    <dl className="cmd-strip" aria-label={side ? `Summary, ${side} side` : "Summary"}
        data-side={side ?? undefined}>
      {items.map((i) => (
        <div key={i.key} className={i.value === null ? "cmd-unknown" : undefined}
             data-key={i.key}>
          <dt>{i.label}</dt>
          <dd>{i.value === null ? <U />
            : STRIP_LINKS[i.key] ? <Link to={STRIP_LINKS[i.key] as string}>{i.text}</Link>
            : i.text}</dd>
          <small>{readTime(i.readAt)}</small>
        </div>
      ))}
    </dl>
  );
}

/** The health distribution bar: segments are counts of the checks already read,
 *  never tiles (no data-key). Unread is one hatched UNKNOWN segment. */
function HealthBar({ overview }: { overview: Read<Overview | null> }): JSX.Element {
  const r = readOf(overview);
  const failed = r.error !== null || !r.data;
  const segs = healthSegments(failed ? null : r.data?.modules ?? null, failed);
  const unread = segs.length === 1 && segs[0]?.health === "unknown" && segs[0].count === 0;
  const allUnknown = segs.every((s) => s.health === "unknown");
  return (
    <div className="cmd-healthbar" data-health={allUnknown ? "unknown" : undefined}
         role="group" aria-label="Health of the checks">
      <div className="cmd-hb-bar">
        {segs.map((s) => (
          <span key={s.health} className={`cmd-hb-seg${s.health === "unknown" ? " hatch" : ""}`}
                data-health={s.health}
                style={unread ? undefined : { flexGrow: s.count }} title={s.label} />
        ))}
      </div>
      <ul className="cmd-hb-legend">
        {segs.map((s) => (
          <li key={s.health} data-health={s.health}>
            <i className="cmd-sq" aria-hidden="true" />
            {s.health === "unknown" ? <span className="cmd-word-unknown">{s.label}</span> : s.label}
          </li>
        ))}
        <li className="cmd-hb-at">
          {r.error !== null ? "read failed" : !r.data ? "reading…" : stamp(overview.fetchedAt)}
        </li>
      </ul>
    </div>
  );
}

function Roster({ agents, error, at }: {
  agents: RosterAgent[] | null; error: string | null; at: Date | null;
}): JSX.Element {
  return (
    <CPanel id="roster" title="Roster" at={stamp(at, error !== null)}>
      {error && <Unread>Agents: {safeText(error)}</Unread>}
      {!agents && !error && <p className="cmd-empty">Reading…</p>}
      {agents && (
        <table className="cmd-roster-t">
          <thead>
            <tr>
              <th>Agent</th><th>Role</th><th>Status</th><th>Last shift</th>
              <th>Next shift</th><th>Tries</th><th>Today</th><th>Read</th>
            </tr>
          </thead>
          <tbody>
            {agents.map((a) => {
              const v = rosterRow(a);
              const o = shiftOutcome(a);
              return (
                <tr key={a.name} data-state={v.state}>
                  <td className="cmd-roster-name">{a.name}</td>
                  <td>{safeText(a.role)}</td>
                  <td><span className={`cmd-state ${v.className}`}>{v.word}</span></td>
                  <td className="cmd-shift">{a.last_shift_at ? <>{hhmm(a.last_shift_at)}
                    {o && (o.detail ? (
                      <details className="cmd-outcome">
                        <summary><small>{o.line}</small></summary>
                        <p>{o.detail}</p>
                      </details>
                    ) : <small> {o.line}</small>)}</>
                    : <span className="cmd-dim">none yet</span>}</td>
                  <td>{a.next_shift_at ? hhmm(a.next_shift_at)
                    : <span className="cmd-dim">{a.shift_state === "off" ? "off" : "—"}</span>}</td>
                  <td>{a.attempts_today === null ? <U /> : a.attempts_today}</td>
                  <td>{a.today_output === null ? <U /> : a.today_output}</td>
                  <td className="cmd-dim">{readTime(a.read_at)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </CPanel>
  );
}

function Pipeline({ pipe }: {
  pipe: Fresh<Awaited<ReturnType<typeof api.pipeline>>>;
}): JSX.Element {
  const read = readOf(pipe);
  const d = read.error ? null : read.data;
  return (
    <CPanel id="pipeline" title="Pipeline" at={stamp(pipe.fetchedAt, read.error !== null)}>
      {read.error && <Unread>Pipeline: {safeText(read.error)}</Unread>}
      {d?.sites.state === "unknown" && <Unread>{safeText(d.sites.reason)}</Unread>}
      {d?.leads.state === "unknown" && <Unread>{safeText(d.leads.reason)}</Unread>}
      {d && d.rows.length === 0 && <p className="cmd-empty">No client or lead yet.</p>}
      {d && d.rows.length > 0 && (
        <ul className="cmd-rows">
          {d.rows.slice(0, 12).map((r, i) => (
            <li key={`${r.kind}-${r.client}-${i}`}>
              <span className="cmd-rows-k">{safeText(r.client)} <small>{r.kind}</small></span>
              <span>{r.stage ? safeText(r.stage) : <U />}</span>
              <span>{r.next_step
                ? <>{r.health === "unknown" && <><U />{" "}</>}{safeText(r.next_step)}</>
                : <><U /> <small>{safeText(r.reason)}</small></>}</span>
              <span className="cmd-dim">{readTime(r.read_at)}</span>
            </li>
          ))}
          {d.rows.length > 12 && <li className="cmd-more-n">+{d.rows.length - 12} more</li>}
        </ul>
      )}
    </CPanel>
  );
}

function Spend({ label, s, unit }: { label: string; s: SpendRead; unit: string }): JSX.Element {
  return (
    <li className={s.used === null ? "cmd-unknown" : undefined}>
      <span className="cmd-rows-k">{label}</span>
      <span>{s.used === null ? <><U /> <small>{safeText(s.reason ?? "not read")}</small></>
        : <b>{s.used.toLocaleString("en-US")}</b>}
        {s.cap !== null && <small> of {s.cap.toLocaleString("en-US")} {unit}</small>}</span>
      <span className="cmd-dim">{readTime(s.read_at)}</span>
    </li>
  );
}

function MoneyCosts({ money }: {
  money: Fresh<Awaited<ReturnType<typeof api.money>>>;
}): JSX.Element {
  const read = readOf(money);
  const d = read.error ? null : read.data;
  const pools = d ? Object.entries(d.costs.free_quota.pools) : [];
  return (
    <CPanel id="costs" title="Money & costs" at={stamp(money.fetchedAt, read.error !== null)}>
      {read.error && <Unread>Money: {safeText(read.error)}</Unread>}
      {d && (
        <>
          <ul className="cmd-rows">
            <li className={d.revenue.value === null ? "cmd-unknown" : undefined}>
              <span className="cmd-rows-k">Revenue</span>
              <span>{d.revenue.value === null
                ? <><U /> <small>{safeText(d.revenue.reason)}</small></>
                : <b>${d.revenue.value.toLocaleString("en-US")}</b>}</span>
              <span className="cmd-dim">{readTime(d.revenue.read_at)}</span>
            </li>
            <Spend label="Perplexity calls" s={d.costs.perplexity} unit="this month" />
            <Spend label="Places calls" s={d.costs.places} unit="this month" />
          </ul>
          <h3 className="cmd-sub">Free-tier pools <span>{readTime(d.costs.free_quota.read_at)}</span></h3>
          {pools.length === 0 ? (
            <p className="cmd-empty"><U /> no free-tier call made since the API started</p>
          ) : (
            <ul className="cmd-rows">
              {pools.slice(0, 8).map(([name, p]) => (
                <li key={name}>
                  <span className="cmd-rows-k">{name}</span>
                  <span>{p.requests_left === null ? <U /> : <b>{p.requests_left}</b>}
                    {p.requests_limit !== null && <small> of {p.requests_limit} requests left</small>}
                    {p.blocked_s > 0 && <small className="cmd-bad"> · blocked {p.blocked_s}s</small>}</span>
                  <span className="cmd-dim">{p.tokens_used_24h === null ? "tokens 24h ?"
                    : `${p.tokens_used_24h.toLocaleString("en-US")} tok 24h`}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </CPanel>
  );
}

function Alerts({ agents, error, at }: {
  agents: RosterAgent[] | null; error: string | null; at: Date | null;
}): JSX.Element {
  const lines = alertPanel(error ? null : agents);
  return (
    <CPanel id="alerts" title="Alerts" at={stamp(at, error !== null)}>
      {!agents && !error ? <p className="cmd-empty">Reading…</p>
        : lines.length === 0 ? <p className="cmd-empty">No agent is failing.</p> : (
          <ul className="cmd-rows">
            {lines.map((l) => (
              <li key={l.key} className={l.health === "failing" ? "cmd-failing" : "cmd-unknown"}>
                <span className="cmd-rows-k">
                  {l.health === "failing" ? "FAILING" : <U />}
                </span>
                <span>{safeText(l.text)}</span>
                <span className="cmd-dim">{stamp(at, error !== null)}</span>
              </li>
            ))}
          </ul>
        )}
    </CPanel>
  );
}

/** FAILING / DEGRADED lines the compact panel shows before "+n more". */
const ALARMS_SHOWN = 3;

/** Health & money, compact: the check counts, only FAILING and DEGRADED
 *  lines, UNKNOWN as a grey dashed chip with its count, then revenue and the
 *  two metered spends. The full lists are on the Agency tab. */
function Vitals({ overview, money, roster }: {
  overview: Read<Overview | null>;
  money: Read<MoneyRead>;
  roster: RosterAgent[] | null;
}): JSX.Element {
  const ov = readOf(overview);
  const modules = ov.error ? null : ov.data?.modules ?? null;
  const o = overall(modules);
  const lines = alarmLines(modules);
  const m = readOf(money);
  const d = m.error ? null : m.data;
  const unrouted = roster?.some((a) => a.alert_unrouted) ?? false;
  const spend = (label: string, s: SpendRead): JSX.Element => (
    <li data-health={s.used === null ? "unknown" : undefined}>
      <i className="cmd-sq" aria-hidden="true" />
      <span className="cs-k">{label}</span>
      <span className="cs-v">{s.used === null ? <U /> : <b>{s.used.toLocaleString("en-US")}</b>}
        {s.cap !== null && <small> of {s.cap.toLocaleString("en-US")}</small>}</span>
      <span className="cs-t">{readTime(s.read_at)}</span>
    </li>
  );
  return (
    <CPanel id="vitals" title="Health & money" at={stamp(overview.fetchedAt, ov.error !== null)}>
      <div className="cs-vitals">
        <div>
          {ov.error && <Unread>Checks: {safeText(ov.error)}</Unread>}
          <div className="cs-vitals-top">
            <dl className="cs-counts cs-counts-3">
              {(["ok", "degraded", "failing"] as const).map((h) => (
                <div key={h} data-health={h}>
                  <dt>{h === "ok" ? "OK" : h}</dt><dd>{modules ? o[h] : <U />}</dd>
                </div>
              ))}
            </dl>
            <p className="cs-counts-t">
              {modules && o.unknown > 0 && (
                <span className="cs-chip-unknown" data-health="unknown">{`${o.unknown} Unknown`}</span>
              )}
              {modules && overview.fetchedAt ? ` read ${hhmm(overview.fetchedAt)}` : " not read"}
            </p>
          </div>
          {modules && lines.length === 0 && !unrouted && (
            <p className="cmd-empty">Nothing failing or degraded.</p>
          )}
          <ul className="cs-list">
            {lines.slice(0, ALARMS_SHOWN).map((l) => (
              <li key={l.module} data-health={l.health}>
                <i className="cmd-sq" aria-hidden="true" />
                <span className="cs-k">{l.title}</span>
                <span className="cs-v">{l.health === "failing" ? "FAILING" : "DEGRADED"}
                  {" "}<small>{safeText(l.why)}</small></span>
                <span className="cs-t">{readTime(l.readAt)}</span>
              </li>
            ))}
            {unrouted && (
              <li data-health="unknown">
                <i className="cmd-sq" aria-hidden="true" />
                <span className="cs-k">alert channel</span>
                <span className="cs-v"><U /> <small>FAILING alerts recorded, not delivered</small></span>
                <span className="cs-t">{readTime(roster?.[0]?.read_at ?? null)}</span>
              </li>
            )}
            {lines.length > ALARMS_SHOWN && (
              <li className="cmd-more-n">
                <Link to="/agency#health">+{lines.length - ALARMS_SHOWN} more</Link>
              </li>
            )}
          </ul>
        </div>
        <div>
          {m.error && <Unread>Money: {safeText(m.error)}</Unread>}
          {d && (
            <ul className="cs-list">
              <li data-health={d.revenue.value === null ? "unknown" : undefined}>
                <i className="cmd-sq" aria-hidden="true" />
                <span className="cs-k">Revenue</span>
                <span className="cs-v">{d.revenue.value === null ? <U />
                  : <b>${d.revenue.value.toLocaleString("en-US")}</b>}</span>
                <span className="cs-t">{readTime(d.revenue.read_at)}</span>
              </li>
              {spend("Perplexity", d.costs.perplexity)}
              {spend("Places", d.costs.places)}
            </ul>
          )}
          {!d && !m.error && <p className="cmd-empty">Reading…</p>}
          <p className="cmd-note"><Link to="/agency#more">Pipeline, costs and more</Link></p>
        </div>
      </div>
    </CPanel>
  );
}

type AgentsRead = Awaited<ReturnType<typeof api.agents>>;

export interface FloorProps {
  agents: Read<AgentsRead>;
  pipe: Read<PipelineRead>;
  money: Read<MoneyRead>;
  overview: Read<Overview | null>;
  pending: { count: number; readAt: string | null } | null;
  needsYou: ReactNode;
  /** The latest leads (GET /api/leads); the chart band is left off without it. */
  leads?: Read<{ leads: Lead[]; total: number }>;
  /** Coming up as one short line, or null to leave it off. */
  nextUp?: ReactNode;
  /** From the session read; null when it has not been read. */
  watchOnly?: boolean | null;
}

/** The whole short page from its reads: pure, so it can be tested. */
export function CommandFloorBody({ agents, pipe, money, overview, pending, needsYou,
  leads, nextUp = null, watchOnly = null }: FloorProps): JSX.Element {
  const a = readOf(agents);
  const roster = a.data && !a.error ? a.data.agents.map(toRoster) : null;
  const p = readOf(pipe);
  const m = readOf(money);
  const items = summaryStrip({
    roster: roster ? { agents: roster, readAt: roster[0]?.read_at ?? a.data?.as_of ?? null }
      : null,
    pending,
    pipeline: p.data && !p.error && p.data.sites.state === "ok" && p.data.leads.state === "ok"
      ? { count: p.data.rows.length, readAt: p.data.read_at } : null,
    places: m.data && !m.error
      ? { used: m.data.costs.places.used, readAt: m.data.costs.places.read_at } : null,
  });
  // Every section sits left or right of the ring (Miro 2026-11-12). The
  // split is pure (flankLayout.ts); each unit renders exactly what it did.
  const parts: Record<string, JSX.Element> = {};
  const units: FlankUnit[] = [];
  const add = (id: string, weight: number, order: number, node: JSX.Element,
               cluster?: string): void => {
    parts[id] = node;
    units.push({ id, weight, order, cluster });
  };
  items.forEach((i) => units.push({ id: `tile-${i.key}`, weight: 1, order: 0 }));
  if (nextUp) add("next", 1, 0, <div className="cmd-flank-next">{nextUp}</div>, "attention");
  add("needs", 6, 1, <div className="cmd-floor-needs">{needsYou}</div>, "attention");
  add("roster", 6, 1, <div className="cmd-floor-roster">
    <Roster agents={roster} error={a.error} at={agents.fetchedAt} /></div>, "agents");
  add("vitals", 4, 2, <Vitals overview={overview} money={money} roster={roster} />);
  if (leads) {
    add("leads", 4, 4, <div className="cmd-floor-leads"><LeadsGrid leads={leads} /></div>, "pipeline");
    add("pipeline", 3, 5, <div className="cmd-flank-chart"><PipelineChart pipe={pipe} /></div>, "pipeline");
    add("shifts", 3, 3, <div className="cmd-flank-chart">
      <ShiftsChart agents={roster} error={a.error} at={agents.fetchedAt} /></div>, "agents");
  }
  const { left, right } = splitFlanks(units);
  let barPlaced = false;
  const column = (side: "left" | "right", col: FlankUnit[]): JSX.Element => {
    const tiles = col.filter((x) => x.id.startsWith("tile-"));
    const rest = col.filter((x) => !x.id.startsWith("tile-"));
    return (
      <div className="cmd-flank" data-side={side} role="group"
           aria-label={`Command Center, ${side} of the ATLAS ring`}>
        {tiles.length > 0 && (
          <div className="cmd-flank-unit cmd-snap" data-unit="snapshot">
            <Strip side={side} items={tiles.map((t) => items.find((i) => `tile-${i.key}` === t.id) as
              ReturnType<typeof summaryStrip>[number])} />
            {!barPlaced && (barPlaced = true) && <HealthBar overview={overview} />}
          </div>
        )}
        {rest.map((x) => (
          <div key={x.id} className="cmd-flank-unit" data-unit={x.id} data-weight={x.weight}>
            {parts[x.id]}
          </div>
        ))}
      </div>
    );
  };
  return (
    <div className="cmd-floor">
      <div className="cmd-triad">
        <div className="cmd-ring-col" role="group" aria-label="ATLAS status ring">
          <BootRing overview={overview} watchOnly={watchOnly} />
        </div>
        {column("left", left)}
        {column("right", right)}
      </div>
    </div>
  );
}

export function CommandSections({ reloadKey, overview, needsYou, nextUp = null,
  pending = null, watchOnly = null }: {
  watchOnly?: boolean | null;
  reloadKey: number;
  overview: Fresh<Overview | null>;
  needsYou: ReactNode;
  nextUp?: ReactNode;
  pending?: { count: number; readAt: string | null } | null;
}): JSX.Element {
  const agents = useFresh(() => api.agents(), [reloadKey]);
  const pipe = useFresh(() => api.pipeline(), [reloadKey]);
  const money = useFresh(() => api.money(), [reloadKey]);
  const leads = useFresh(() => api.leads({ limit: 6 }), [reloadKey]);
  return (
    <CommandFloorBody agents={agents} pipe={pipe} money={money} overview={overview}
                      leads={leads} pending={pending} needsYou={needsYou} nextUp={nextUp}
                      watchOnly={watchOnly} />
  );
}

/** What left the Command Center (2026-10-29), on the Agency tab: Pipeline,
 *  Money & costs (free-tier pools), Alerts, the full Health and Money lists,
 *  Today, Search & Sites and Coming up. Each is an anchor (`/agency#id`). */
export function AgencyMore({ reloadKey, overview, comingUp = null }: {
  reloadKey: number;
  overview: Fresh<Overview | null>;
  comingUp?: ReactNode;
}): JSX.Element {
  const agentsRead = useFresh(() => api.agents(), [reloadKey]);
  const pipe = useFresh(() => api.pipeline(), [reloadKey]);
  const money = useFresh(() => api.money(), [reloadKey]);
  const a = readOf(agentsRead);
  const roster = a.data && !a.error ? a.data.agents.map(toRoster) : null;
  const { hash } = useLocation();
  // The panels draw once their reads land; scroll to an anchor again then.
  useEffect(() => {
    if (hash) document.getElementById(hash.slice(1))?.scrollIntoView();
  }, [hash, pipe.fetchedAt === null, money.fetchedAt === null]);
  return (
    <section id="more" className="cs-more" aria-label="More from the Command Center">
      <div className="cs-grid">
        <section id="pipeline" className="cmd-anchor cs-span2"><Pipeline pipe={pipe} /></section>
        <section id="alerts" className="cmd-anchor">
          <Alerts agents={roster} error={a.error} at={agentsRead.fetchedAt} />
        </section>
        <section id="costs" className="cmd-anchor"><MoneyCosts money={money} /></section>
        <section id="health" className="cmd-anchor"><HealthSection overview={overview} /></section>
        <Money overview={overview} />
        <Today reloadKey={reloadKey} />
        <SearchSites reloadKey={reloadKey} now={new Date()} />
        {comingUp}
      </div>
    </section>
  );
}
