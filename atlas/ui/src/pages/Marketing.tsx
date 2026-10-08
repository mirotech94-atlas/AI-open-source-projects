import type { JSX, ReactNode } from "react";
import {
  api, type IdeaFact, type MarketingChannel, type MarketingExperiment,
  type MarketingSection, type MonetizationIdea, type PlaybookLesson, type Marketing as Board,
} from "../api";
import { HealthBadge } from "../components/Health";
import { useFresh } from "../hooks/useFresh";
import {
  LANES, STATE_WORD, deltaText, lanes, num, panelView, planSummary, when, type PanelView,
} from "./marketingModel";

/**
 * The marketing board.
 *
 * Everything here is a reading of rows the collectors and the marketing loop
 * already wrote, and every figure sits beside the time it was read. Three
 * rules shape the page:
 *
 * - An unreadable `/api/marketing`, or a section the server could not read,
 *   is UNKNOWN — grey, dashed, the word visible. It is never an empty board.
 * - Running, measured and inconclusive never share a treatment. `unmeasured`
 *   borrows UNKNOWN's look because it is the one closed state in which
 *   nobody compared anything.
 * - Nothing on this screen can publish, approve or spend. Publishing waits on
 *   a Tier 2 card answered by text; money out is Tier 3 and stays there.
 */

function Unknown({ reason }: { reason: string }): JSX.Element {
  return (
    <div className="mk-unknown">
      <HealthBadge health="unknown" />
      <span>{reason}</span>
    </div>
  );
}

/** A panel body that knows the difference between "none" and "not read". */
function Body({ view, empty, rows, children }: {
  view: PanelView;
  empty: string;
  rows: number;
  children: ReactNode;
}): JSX.Element {
  if (view.kind === "reading") return <p className="empty">Reading…</p>;
  if (view.kind === "unknown") return <Unknown reason={view.reason} />;
  return rows === 0 ? <p className="empty">{empty}</p> : <>{children}</>;
}

// ------------------------------------------------------------- channels

function ChannelTile({ c }: { c: MarketingChannel }): JSX.Element {
  const platform = c.platform ?? "YouTube";
  if (c.health !== "ok") {
    return (
      <div className="mk-channel" data-state="unknown">
        <header>
          <span className="n">{c.channel} · {platform}</span>
          <HealthBadge health="unknown" />
        </header>
        <p className="why">{c.reason ?? "not read"}</p>
        <p className="stamp">
          {c.attempted_at ? `last attempt ${when(c.attempted_at)}` : "never read"}
        </p>
      </div>
    );
  }
  if (platform !== "YouTube") {
    return (
      <div className="mk-channel" data-state="ok">
        <header>
          <span className="n">{c.channel} · {platform}</span>
          <span className="stamp">read {when(c.read_at)}</span>
        </header>
        <div className="figs">
          {(c.figures ?? []).map((f) => (
            <span className="fig" key={f.label}>
              {f.value === null ? <b className="unread">not measured</b> : <b>{num(f.value)}</b>}
              <i>{f.label}</i>
            </span>
          ))}
        </div>
      </div>
    );
  }
  return (
    <div className="mk-channel" data-state="ok">
      <header>
        <span className="n">{c.channel} · YouTube</span>
        <span className="stamp">read {when(c.read_at)}</span>
      </header>
      <div className="figs">
        <span className="fig">
          <b>{c.subscribers === null ? "hidden" : num(c.subscribers)}</b>
          <i>subscribers</i>
        </span>
        <span className="fig">
          {c.views_28d === null ? (
            <b className="unread" title={c.views_28d_reason ?? undefined}>not measured</b>
          ) : (
            <b>{num(c.views_28d)}</b>
          )}
          <i>
            {c.views_28d_span_days === null
              ? "views, ~28 days"
              : `views over ${c.views_28d_span_days} days (derived from two readings)`}
          </i>
        </span>
        <span className="fig">
          <b>{num(c.views_total)}</b>
          <i>views, lifetime</i>
        </span>
      </div>
      <p className="stamp">
        {c.views_28d === null
          ? `~28-day views: ${c.views_28d_reason ?? "not measured"}`
          : `derived from two readings: ${when(c.views_28d_since)} → ${when(c.read_at)}`}
      </p>
      {c.partner && (
        <p className="mk-partner"
           data-eligible={c.partner.eligible_on_subscribers === null
             ? "unknown" : String(c.partner.eligible_on_subscribers)}>
          Partner programme: {c.partner.line} (read {when(c.read_at)})
        </p>
      )}
    </div>
  );
}

// ----------------------------------------------------------- experiments

function Figures({ e }: { e: MarketingExperiment }): JSX.Element {
  const unit = e.metric ?? "";
  if (e.state === "won" || e.state === "lost" || e.state === "flat") {
    const delta = deltaText(e.delta_pct);
    return (
      <p className="figs">
        <span>{num(e.baseline)}</span>
        <span className="arrow" aria-hidden="true">→</span>
        <b>{num(e.observed)}</b> {unit}
        {delta ? <span className="delta"> ({delta})</span> : <span className="delta"> (no % against a zero baseline)</span>}
        <span className="stamp"> · measured {when(e.measured_at)}</span>
      </p>
    );
  }
  if (e.state === "unmeasured") {
    return (
      <p className="figs">
        <span className="stamp">
          {e.never_published ? "never published" : "closed unmeasured"}
          {e.measured_at ? ` · ${when(e.measured_at)}` : ""}
        </span>
      </p>
    );
  }
  return (
    <p className="figs">
      {e.baseline === null
        ? <span className="stamp">no baseline measured</span>
        : <span>baseline {num(e.baseline)} {unit}
            <span className="stamp"> · read at filing {when(e.baseline_read_at)}</span></span>}
      {e.stage === "published" && (
        <span className="stamp">
          {" · "}{e.state === "overdue" ? "was due" : "measure due"} {when(e.measure_at)}
        </span>
      )}
    </p>
  );
}

function ExperimentCard({ e }: { e: MarketingExperiment }): JSX.Element {
  return (
    <article className="mk-card" data-state={e.state}>
      <header>
        <span className="who">{e.business} · {e.platform}</span>
        <span className="mk-outcome" data-state={e.state}>{STATE_WORD[e.state]}</span>
      </header>
      <p className="hyp">{e.hypothesis}</p>
      <p className="var">variant: {e.variant}</p>
      <Figures e={e} />
      {e.state === "unmeasured" && e.lesson && <p className="why">{e.lesson}</p>}
      {e.stage === "awaiting" && e.proposal_id !== null && (
        <p className="stamp">proposal #{e.proposal_id} — answer it by text</p>
      )}
      {!e.can_close && e.cannot_close && e.stage !== "measured" && (
        <p className="mk-cannot"><span className="mk-tag">Cannot close</span> {e.cannot_close}</p>
      )}
    </article>
  );
}

function Kanban({ data, view }: { data: Board | null; view: PanelView }): JSX.Element {
  const grouped = lanes(data?.experiments ?? []);
  return (
    <div className="mk-kanban">
      {LANES.map(({ stage, title }) => (
        <section key={stage} className="mk-lane">
          <h4>{title} <span className="count">{view.kind === "rows" ? grouped[stage].length : "?"}</span></h4>
          <Body view={view} empty="None." rows={grouped[stage].length}>
            {grouped[stage].map((e) => <ExperimentCard key={e.id} e={e} />)}
          </Body>
          {stage === "awaiting" && view.kind === "rows" && data?.unavailable.plan && (
            <p className="stamp">
              Pending proposals could not be read, so an experiment waiting on
              you may be shown under Drafts.
            </p>
          )}
        </section>
      ))}
    </div>
  );
}

// -------------------------------------------------------------- playbook

function Lesson({ l }: { l: PlaybookLesson }): JSX.Element {
  const delta = deltaText(l.delta_pct);
  const state = l.outcome === "won" || l.outcome === "lost" || l.outcome === "flat"
    ? l.outcome : "unmeasured";
  return (
    <li className="mk-lesson" data-state={state}>
      <span className="mk-outcome" data-state={state}>{STATE_WORD[state]}</span>
      <span className="txt">{l.content}</span>
      <span className="stamp">
        {delta ? `${delta} · ` : ""}{when(l.at)}
      </span>
    </li>
  );
}

// ------------------------------------------------------------ monetization

function Fact({ f }: { f: IdeaFact }): JSX.Element {
  if (f.undated) {
    return (
      <li data-state="unknown">
        <b>{f.name}</b>: no read time recorded, so not shown as a measurement
      </li>
    );
  }
  const value = typeof f.value === "number" ? num(f.value) : String(f.value ?? "—");
  return (
    <li>
      <b>{f.name}</b>: {value}{" "}
      <span className="stamp">read {when(f.observed_at)}</span>
      {f.detail && <span className="detail"> — {f.detail}</span>}
    </li>
  );
}

function Idea({ i, rank }: { i: MonetizationIdea; rank: number }): JSX.Element {
  return (
    <article className="mk-idea" data-money-out={String(i.money_out)}>
      <header>
        <span className="rank">#{rank}</span>
        <span className="n">{i.idea}</span>
        <span className="who">{i.business} · {i.mechanism}</span>
      </header>
      <p className="mk-requires">
        <span className="k">Requires</span> {i.requires || "nothing stated"}
      </p>
      {i.est_note && <p className="note">{i.est_note}</p>}
      <details>
        <summary>
          Evidence — {i.evidence.length} fact{i.evidence.length === 1 ? "" : "s"}
          <span className="stamp"> · filed {when(i.at)}</span>
        </summary>
        {i.evidence.length === 0
          ? <p className="empty">No measured facts recorded.</p>
          : <ul>{i.evidence.map((f) => <Fact key={f.name} f={f} />)}</ul>}
      </details>
    </article>
  );
}

// ------------------------------------------------------------------ page

export function Marketing({ reloadKey }: { reloadKey: number }): JSX.Element {
  const fresh = useFresh(() => api.marketing(), [reloadKey]);
  const data = fresh.data;
  const view = (section: MarketingSection): PanelView => panelView(data, fresh.error, section);
  const experiments = view("experiments");
  const plan = view("plan");
  const summary = planSummary(data?.plan ?? []);

  return (
    <>
      <h2 className="label">Marketing</h2>
      <p className="stamp" style={{ marginBottom: 12 }}>
        {fresh.fetchedAt ? `Read ${fresh.fetchedAt.toLocaleTimeString()}` : "Reading…"}
        {data?.watch_only && " · watch-only: nothing is filed or published"}
      </p>

      {fresh.error && (
        <p className="errorbar">
          Could not read the marketing board: {fresh.error}
          {data && fresh.fetchedAt
            ? ` — showing the read from ${fresh.fetchedAt.toLocaleTimeString()}`
            : ""}
        </p>
      )}

      {data?.hook_note && (
        <div className="mk-hooks">
          <span className="mk-tag">Cannot close</span>
          <span>{data.hook_note}</span>
        </div>
      )}

      <div className="panel" style={{ marginBottom: 14 }}>
        <div className="inner">
          <h3 className="label">Channels</h3>
          <Body view={view("channels")} empty="No channels configured."
                rows={data?.channels.length ?? 0}>
            <div className="mk-channels">
              {(data?.channels ?? []).map((c) => <ChannelTile key={c.channel} c={c} />)}
            </div>
          </Body>
        </div>
      </div>

      <div className="panel" style={{ marginBottom: 14 }}>
        <div className="inner">
          <h3 className="label">Experiments</h3>
          <p className="stamp" style={{ marginBottom: 8 }}>
            Each is scored at 48 hours against the trailing 4-week median. Publishing
            waits on your word, every time; nothing on this screen can approve it.
          </p>
          {experiments.kind === "unknown"
            ? <Unknown reason={experiments.reason} />
            : <Kanban data={data} view={experiments} />}
        </div>
      </div>

      <div className="mk-split">
        <div className="panel">
          <div className="inner">
            <h3 className="label">Playbook</h3>
            <Body view={view("playbook")}
                  empty="Nothing measured yet — the playbook fills as experiments close."
                  rows={data?.playbook.length ?? 0}>
              <ul className="mk-lessons">
                {(data?.playbook ?? []).map((l) => <Lesson key={l.id} l={l} />)}
              </ul>
            </Body>
          </div>
        </div>

        <div className="panel">
          <div className="inner">
            <h3 className="label">Monetization</h3>
            <p className="stamp" style={{ marginBottom: 8 }}>
              Ranked by what was measured. No revenue figure appears unless a source
              measured it; anything that costs money is yours to decide.
            </p>
            <Body view={view("ideas")}
                  empty="No ideas recorded — none rests on a measured fact yet."
                  rows={data?.ideas.length ?? 0}>
              {(data?.ideas ?? []).map((i, n) => <Idea key={i.id} i={i} rank={n + 1} />)}
            </Body>
          </div>
        </div>
      </div>

      <div className="panel" style={{ marginTop: 14 }}>
        <div className="inner">
          <h3 className="label">This week's plan</h3>
          <p className="stamp" style={{ marginBottom: 8 }}>
            {data
              ? data.filing.at
                ? `Last weekly filing ${when(data.filing.at)}: ${data.filing.filed} filed, `
                  + `${data.filing.refused} refused`
                  + (data.filing.reasons.length ? ` (${data.filing.reasons.join("; ")})` : "")
                : data.filing.note ?? "no weekly filing seen"
              : "—"}
          </p>
          {plan.kind === "unknown" ? (
            <Unknown reason={plan.reason} />
          ) : plan.kind === "reading" ? (
            <p className="empty">Reading…</p>
          ) : (
            <>
              <p className={summary.live.length ? "" : "empty"}>{summary.heading}</p>
              {summary.live.length > 0 && (
                <table className="sheet">
                  <thead>
                    <tr><th>Decision</th><th>Why</th><th>Experiment</th><th>Raised</th><th>Expires</th></tr>
                  </thead>
                  <tbody>
                    {summary.live.map((p) => (
                      <tr key={p.id}>
                        <td>{p.action}{p.channel ? ` · ${p.channel}` : ""}</td>
                        <td>{p.reason}</td>
                        <td>{p.experiment_id === null ? "—" : `#${p.experiment_id}`}</td>
                        <td>{when(p.created_at)}</td>
                        <td>{when(p.expires_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {summary.expired.length > 0 && (
                <p className="stamp" style={{ marginTop: 8 }}>
                  <span className="mk-tag">Expired</span>{" "}
                  unanswered, so they will not run:{" "}
                  {summary.expired
                    .map((p) => `#${p.id} ${p.action} (${when(p.expires_at)})`)
                    .join("; ")}
                </p>
              )}
            </>
          )}
        </div>
      </div>
    </>
  );
}
