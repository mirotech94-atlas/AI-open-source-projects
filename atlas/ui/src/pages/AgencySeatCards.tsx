import type { JSX } from "react";
import type {
  ActivityRow, AgentSummary, Approvals, Health, Overview, api,
} from "../api";
import { Unread, stamp } from "./cmdParts";
import { hhmm, readOf, rel, type Read } from "./homeModel";
import { DECISION_KINDS, pendingDecisions, type DecisionRow } from "./alertsModel";
import {
  SEATS, doneSoFar, seatChecks, seatHealth, seatLastAction, seatRemediation, seatRoster,
  seatWaiting, type Seat,
} from "./agencySeats";
import type { RemediationRead } from "./remediationModel";
import { academyLabel, safeText, visible } from "./retired";
import { youtubeChannel } from "./youtubeModel";

/**
 * The eight seat cards (moved out of the Agency tab, which is now the map
 * alone; they sit in the Command Center's Agency desk under Team): eight seats (Client Websites, Personal Ops, Content, Sales,
 * Business Automation, SEO, Research, QA), each with its role, brief, its own checks, what it has finished and what waits on an
 * answer. Reads only: decisions are answered in Slack. Every figure carries
 * the time it was read; a seat that cannot be read says Unknown.
 */

type Leads = Awaited<ReturnType<typeof api.leads>>;

export interface AgencyBodyProps {
  overview: Read<Overview>;
  roster: Read<{ agents: AgentSummary[] }>;
  leads: Read<Leads>;
  approvals: Read<Approvals>;
  actions: Read<{ activity: ActivityRow[] }>;
  /** GET /api/remediation: open tasks per seat. */
  remediation: Read<Pick<RemediationRead, "tasks">>;
  now: Date;
}

const HEALTH_WORD: Record<Health, string> = {
  ok: "OK", degraded: "Degraded", failing: "Failing", unknown: "Unknown",
};

function Word({ health }: { health: Health }): JSX.Element {
  return health === "unknown"
    ? <span className="cmd-word-unknown">Unknown</span>
    : <span className="cmd-seat-w" data-health={health}>{HEALTH_WORD[health]}</span>;
}

function SeatCard({ seat, p, decisions, actions, roster }: {
  seat: Seat;
  p: AgencyBodyProps;
  decisions: DecisionRow[] | null;
  actions: ActivityRow[] | null;
  roster: AgentSummary[] | null;
}): JSX.Element {
  const ov = readOf(p.overview);
  const modules = ov.error ? null : ov.data?.modules ?? null;
  const health = seatHealth(seat, modules);
  const checks = seatChecks(seat, modules);
  const rosterLine = seatRoster(seat, roster);
  const done = actions ? doneSoFar(seat, actions) : null;
  const waiting = decisions ? seatWaiting(seat, decisions) : null;
  const ownRoster = readOf(p.roster);
  const ownActions = readOf(p.actions);
  const ownApprovals = readOf(p.approvals);
  const t = p.now.getTime();
  const yt = seat.key === "academy" ? youtubeChannel(modules, "clips") : null;
  const leads = seat.key === "websites" ? readOf(p.leads) : null;
  const last = actions ? seatLastAction(seat, actions) : null;
  const rem = readOf(p.remediation);
  const openRem = rem.error ? null : seatRemediation(seat, rem.data?.tasks ?? null);
  return (
    <section className="cmd-panel cmd-seat" id={`seat-${seat.key}`} data-seat={seat.key}
             data-health={health} aria-labelledby={`h-${seat.key}`}>
      <header>
        <h2 id={`h-${seat.key}`}>{seat.title}</h2>
        <Word health={health} />
        <span className="cmd-at">{stamp(p.overview.fetchedAt, ov.error !== null)}</span>
      </header>
      <p className="cmd-seat-role">{seat.role}</p>
      <p className="cmd-seat-brief">{seat.brief}</p>
      <dl className="cmd-seat-facts">
        <dt>Last action</dt>
        <dd>{ownActions.error ? <span className="cmd-word-unknown">Unknown</span>
          : actions === null ? "Reading…"
          : last === null ? "No action finished among the newest 500 logged." : (
            <><span>{safeText(last.subject)}</span>{" "}
              <time>{hhmm(last.at)}</time> <small>{rel(last.at, t)}</small></>
          )}{" "}<small>{stamp(p.actions.fetchedAt, ownActions.error !== null)}</small></dd>
        <dt>Open remediation</dt>
        <dd>{rem.error ? (
          <><span className="cmd-word-unknown">Unknown</span>{" "}
            <small>remediation could not be read: {safeText(rem.error)}</small></>
        ) : openRem === null ? "Reading…" : <b>{openRem}</b>}{" "}
          <small>{stamp(p.remediation.fetchedAt, rem.error !== null)}</small></dd>
      </dl>

      <h3 className="cmd-sub">Checks</h3>
      {ov.error ? <Unread>The checks could not be read: {safeText(ov.error)}</Unread>
        : !ov.data ? <p className="cmd-empty">Reading…</p>
        : checks.length === 0 ? <Unread>No check of this seat is reporting.</Unread> : (
          <ul className="cmd-seat-checks">
            {checks.map((c) => (
              <li key={c.id} data-health={c.health}>
                <i className="cmd-sq" aria-hidden="true" />
                <span className="cmd-al-s">{c.label}</span>
                <Word health={c.health} />
                <time>{c.readAt ? `read ${hhmm(c.readAt)}` : "no read time"}</time>
                {c.detail && <small>{c.detail}</small>}
              </li>
            ))}
          </ul>
        )}

      <h3 className="cmd-sub">Roster</h3>
      {rosterLine === null ? (
        <p className="cmd-empty">Scheduler steps, no roster agent.</p>
      ) : rosterLine === "unknown" ? (
        <Unread>{ownRoster.error ? `The roster could not be read: ${safeText(ownRoster.error)}`
          : "This seat's agent is not on the roster."}</Unread>
      ) : (
        <p className="cmd-empty">
          {rosterLine.runs24h} run{rosterLine.runs24h === 1 ? "" : "s"} in 24h · last run{" "}
          {rosterLine.lastRunAt ? rel(rosterLine.lastRunAt, t) : "never"}
          {rosterLine.ready ? "" : " · not ready"}{" "}
          <small>{stamp(p.roster.fetchedAt)}</small>
        </p>
      )}

      {leads && (
        <>
          <h3 className="cmd-sub">Leads <span>{stamp(p.leads.fetchedAt, leads.error !== null)}</span></h3>
          {leads.error ? <Unread>Leads could not be read: {safeText(leads.error)}</Unread>
            : !leads.data ? <p className="cmd-empty">Reading…</p> : (
              <p className="cmd-empty">
                {leads.data.total} in total
                {Object.entries(leads.data.counts).sort(([a], [b]) => a.localeCompare(b))
                  .map(([k, n]) => ` · ${safeText(k)} ${n}`).join("")}
              </p>
            )}
        </>
      )}

      {yt && (
        <>
          <h3 className="cmd-sub">Atlas Academy channel</h3>
          {yt.state === "unknown" ? <Unread>{yt.why}</Unread>
            : yt.state === "ok-nonumbers" ? (
              <p className="cmd-empty">{yt.text} <small>read {hhmm(yt.readAt)}</small></p>
            ) : (
              <p className="cmd-empty">
                Subscribers {yt.subscribers === null
                  ? <span className="cmd-word-unknown">unreported</span> : yt.subscribers.toLocaleString("en-US")}
                {" · "}Views {yt.views === null
                  ? <span className="cmd-word-unknown">unreported</span> : yt.views.toLocaleString("en-US")}
                {" "}<small>read {hhmm(yt.readAt)}</small>
              </p>
            )}
          <p className="cmd-note">
            Daily tutorial tick: 17:00 UTC (code default;{" "}
            <span className="cmd-word-unknown">not read from the server</span>).
          </p>
        </>
      )}

      <h3 className="cmd-sub">Done so far
        <span>{stamp(p.actions.fetchedAt, ownActions.error !== null)}</span></h3>
      {ownActions.error ? <Unread>Actions could not be read: {safeText(ownActions.error)}</Unread>
        : done === null ? <p className="cmd-empty">Reading…</p>
        : done.length === 0 ? <p className="cmd-empty">None recorded.</p> : (
          <ul className="cmd-feed-list">
            {done.map((r, i) => (
              <li key={`${r.at}-${r.subject}-${i}`}>
                <time>{hhmm(r.at)}</time>
                <span>{safeText(r.subject)}</span>
                <small>{rel(r.at, t)}</small>
              </li>
            ))}
          </ul>
        )}
      <p className="cmd-note">Finished actions among the newest 500 logged; a quiet day shows none.</p>

      <h3 className="cmd-sub">Waiting in {seat.slack}
        <span>{stamp(p.approvals.fetchedAt, ownApprovals.error !== null)}</span></h3>
      {ownApprovals.error ? <Unread>Pending cards could not be read: {safeText(ownApprovals.error)}</Unread>
        : waiting === null ? <p className="cmd-empty">Reading…</p>
        : waiting.length === 0 ? <p className="cmd-empty">Nothing waiting.</p> : (
          <ul className="cmd-list cmd-cards">
            {waiting.slice(0, 4).map((d) => (
              <li key={d.id} data-expired={d.expiresIn === "expired" || undefined}>
                <span className="cmd-l-s">#{d.id} {academyLabel(d.action.replace(/[._]/g, " "))}
                  <em>Tier {d.tier}</em></span>
                <span className="cmd-l-m">{d.expiresIn === "expired" ? "expired, will not run"
                  : d.expiresIn === "—" ? "no expiry" : `expires in ${d.expiresIn}`}</span>
              </li>
            ))}
            {waiting.length > 4 && <li className="cmd-more-n">+{waiting.length - 4} more</li>}
          </ul>
        )}
    </section>
  );
}

/** The whole tab from the reads it is given: pure, so it can be tested. */
export function AgencyBody(p: AgencyBodyProps): JSX.Element {
  const actionsRead = readOf(p.actions);
  const approvalsRead = readOf(p.approvals);
  const rosterRead = readOf(p.roster);
  const shownActions = actionsRead.data
    ? visible(actionsRead.data.activity, (r) => ({ module: r.module, text: [r.subject] })) : null;
  const decisions = approvalsRead.data
    ? pendingDecisions(approvalsRead.data.proposals, p.now.getTime()) : null;
  const hidden = (shownActions?.hidden ?? 0) + (decisions?.hidden ?? 0);
  const counts = decisions
    ? DECISION_KINDS.map((k) => `${k} ${decisions.rows.filter((d) => d.kind === k).length}`).join(" · ")
    : null;
  return (
    <div className="cmd-page cmd-agency">
      <div className="cmd-seat-core">
        <b>ATLAS</b>
        <span>Agency · 8 seats</span>
        <small>Answer in Slack (#agency-decisions). This page cannot approve; silence expires a card.</small>
      </div>
      <div className="cmd-seats">
        {SEATS.map((seat) => (
          <SeatCard key={seat.key} seat={seat} p={p} decisions={decisions?.rows ?? null}
                    actions={shownActions?.rows ?? null} roster={rosterRead.data?.agents ?? null} />
        ))}
      </div>
      {counts && <p className="cmd-note">Pending cards by kind: {counts}.</p>}
      {hidden > 0 && <p className="cmd-note">{hidden} from retired lanes not shown.</p>}
    </div>
  );
}
