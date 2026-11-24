import { useState, type JSX } from "react";
import type { Board, BoardAgent, BoardRow } from "../api";
import { api } from "../api";
import { useFresh } from "../hooks/useFresh";
import { LayerChip } from "./Brain";
import {
  cardLine, countText, figureText, filterAgents, jobLine, nightshiftRows, orderAgents,
  progressPct, progressText, recentLine, rowKind, rowMeta, startsOpen, totalsLine, when,
} from "./tasksModel";

/**
 * Tasks: what every agent is doing, from `GET /api/tasks/board`
 * (kernel/workboard.py) -- the same read the spoken answer uses.
 *
 * A strip for the whole business (what waits on you, what is in flight),
 * then one collapsible section per seat: what it has in hand (with a
 * progress bar when its own tables count done/total), the cards waiting on
 * you, its department's scheduled jobs, the window's results, each
 * module's own figures, and any pause touching it. Seats with work in hand
 * come first.
 *
 * Read-only on purpose. Decisions are answered by text, through the one
 * path that goes via the executor -- nothing on this screen can approve,
 * deny or run. A count that could not be read is UNKNOWN in grey, with the
 * table named, never a 0 (invariant 7); every figure carries its read time.
 */

function Unknown({ children }: { children: string }): JSX.Element {
  return <span className="bn-pct wb-unknown" data-unknown="true">{children}</span>;
}

function Count({ n }: { n: number | null }): JSX.Element {
  return n === null ? <Unknown>UNKNOWN</Unknown>
    : <span className="bn-num bn-small" data-some={n > 0 || undefined}>{n}</span>;
}

function Strip({ board }: { board: Board }): JSX.Element {
  const t = board.totals;
  const errors = Object.keys(board.errors);
  return (
    <div className="bn-strip wb-strip" role="group" aria-label="Whole business">
      <a className="bn-cell bn-waiting" href="#wb-waiting"
         data-unknown={t.waiting_on_you === null || undefined}
         data-some={(t.waiting_on_you ?? 0) > 0 || undefined}>
        <span className="k">Waiting on you</span>
        <span className="bn-num">{t.waiting_on_you === null ? "UNKNOWN" : t.waiting_on_you}</span>
        <span className="stamp">the cards, below →</span>
      </a>
      <div className="bn-cell" data-unknown={t.in_flight === null || undefined}>
        <span className="k">In flight</span>
        <span className="bn-num">{t.in_flight === null ? "UNKNOWN" : t.in_flight}</span>
        <span className="stamp">missions, outbox, nightshift, research</span>
      </div>
      <div className="bn-cell">
        <span className="k">Seats busy</span>
        <span className="bn-num">{t.agents_busy}<span className="wb-of">/{board.agents.length}</span></span>
        <span className="stamp">with something in hand</span>
      </div>
      <div className="bn-cell" data-unknown={t.paused === null || undefined}>
        <span className="k">Paused</span>
        <span className="bn-num">{t.paused === null ? "UNKNOWN" : t.paused}</span>
        <span className="stamp">{t.paused === null ? "the pause state could not be read"
          : t.paused === 0 ? "nothing is paused" : "scopes, named in their sections"}</span>
      </div>
      {errors.length > 0 && (
        <p className="bn-note">Could not read: {errors.map((e) => `${e} (${board.errors[e]})`).join("; ")}.
          The seats these feed say UNKNOWN.</p>
      )}
    </div>
  );
}

function Bar({ row }: { row: BoardRow }): JSX.Element | null {
  const p = row.progress;
  if (!p) return null;
  const pct = progressPct(p);
  return (
    <span className="wb-progress" data-unknown={pct === null || undefined}>
      <span className="wb-bar" role="progressbar" aria-valuemin={0} aria-valuemax={p.total}
            aria-valuenow={p.done ?? undefined} aria-label={progressText(p)}>
        <i style={{ width: `${pct ?? 0}%` }} />
      </span>
      <span className="wb-bar-t">{progressText(p)}{pct === null ? " · UNKNOWN" : ""}</span>
    </span>
  );
}

function InFlight({ a }: { a: BoardAgent }): JSX.Element {
  const rows = a.in_flight.filter((r) => r.kind !== "nightshift");
  const nightshift = nightshiftRows(a);
  return (
    <div className="wb-block">
      <h4 className="k">In flight <Count n={a.counts.in_flight} /></h4>
      {a.counts.in_flight === null && (
        <p className="stamp">could not read: {a.unread.join(", ")}</p>
      )}
      {rows.length === 0 && a.counts.in_flight !== null && nightshift.length === 0 && (
        <p className="empty">Nothing in hand.</p>
      )}
      {rows.length > 0 && (
        <ul className="wb-rows">
          {rows.map((r) => (
            <li key={`${r.kind}-${r.id}`} className="wb-row" data-kind={r.kind}>
              <span className="wb-row-k">{rowKind(r)}</span>
              <span className="wb-row-s">{r.summary}</span>
              <Bar row={r} />
              <span className="stamp">{rowMeta(r)}</span>
            </li>
          ))}
        </ul>
      )}
      {a.modules.includes("nightshift") && (
        <div className="wb-nightshift">
          <h5 className="k">Nightshift</h5>
          {nightshift.length === 0 ? <p className="empty">Nothing queued or running.</p> : (
            <table className="sheet wb-sheet">
              <thead>
                <tr><th>Task</th><th className="wb-wide">Repo</th><th className="wb-wide">Branch</th>
                  <th>Status</th><th>Started</th></tr>
              </thead>
              <tbody>
                {nightshift.map((j) => (
                  <tr key={j.id}>
                    <td>{j.summary}</td>
                    <td className="wb-wide">{j.repo}</td>
                    <td className="wb-wide">{j.branch}</td>
                    <td>{j.status}</td>
                    <td title={j.finished ?? undefined}>{when(j.started)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}

function Waiting({ a, now }: { a: BoardAgent; now: number }): JSX.Element {
  return (
    <div className="wb-block">
      <h4 className="k">Waiting on the owner <Count n={a.counts.waiting} /></h4>
      {a.waiting === null ? <p className="stamp">could not read: proposals</p>
        : a.waiting.length === 0 ? <p className="empty">Nothing waiting.</p> : (
          <ul className="wb-rows">
            {a.waiting.map((c) => (
              <li key={c.id} className="wb-row" data-kind="card">
                <span className="wb-row-k wb-card">{cardLine(c, now)}</span>
                <span className="wb-row-s">{c.reason}</span>
              </li>
            ))}
          </ul>
        )}
    </div>
  );
}

function Jobs({ a }: { a: BoardAgent }): JSX.Element | null {
  if (a.jobs.length === 0) return null;
  return (
    <div className="wb-block">
      <h4 className="k">Scheduled jobs · {a.department_name}</h4>
      <ul className="wb-rows">
        {a.jobs.map((j) => (
          <li key={j.name} className="wb-row wb-job"
              data-unknown={(j.recorded && (!j.last_run_known)) || undefined}>
            <span className="wb-row-k">{j.name.replace(/_/g, " ")} <LayerChip layer={j.layer} /></span>
            <span className="wb-row-s">{j.when}</span>
            <span className="stamp">{jobLine(j)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Progress({ a }: { a: BoardAgent }): JSX.Element | null {
  if (a.progress.length === 0) return null;
  return (
    <div className="wb-block">
      <h4 className="k">Progress, from its own tables</h4>
      <ul className="wb-figs">
        {a.progress.map((p) => (
          <li key={p.module} data-error={p.error !== null || undefined}>
            <span className="wb-row-k">{p.module}{p.oversight ? ` · as head of ${a.department_name}` : ""}</span>
            {p.error !== null ? (
              <Unknown>{`UNKNOWN — ${p.error}`}</Unknown>
            ) : p.figures.length === 0 ? (
              <span className="stamp">nothing to count</span>
            ) : (
              <span className="wb-fig-list">
                {p.figures.map((f) => (
                  <span key={f.label} className="wb-fig" data-unknown={f.value === null ? "true" : undefined}
                        title={`${f.note ? f.note + " · " : ""}read ${when(f.read_at)}`}>
                    {figureText(f)}
                  </span>
                ))}
              </span>
            )}
            <span className="stamp">read {when(p.read_at)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Seat({ a, board, now }: { a: BoardAgent; board: Board; now: number }): JSX.Element {
  const unknown = a.counts.in_flight === null || a.counts.waiting === null;
  return (
    <details className="wb-agent" data-agent={a.name} open={startsOpen(a)}
             data-unknown={unknown || undefined}
             data-busy={(a.counts.in_flight ?? 0) > 0 || undefined}>
      <summary>
        <span className="wb-who">
          <span className="wb-name">{a.label}</span>
          <span className="wb-title">{a.title}</span>
          <span className="stamp wb-dept">
            {a.department_name}{a.head ? ` · head of ${a.department_name}` : ` · head ${a.head_label}`}
          </span>
        </span>
        <span className="wb-counts">
          {unknown ? <Unknown>UNKNOWN</Unknown> : (
            <>
              <span data-some={(a.counts.in_flight ?? 0) > 0 || undefined}>{countText(a.counts.in_flight)} in flight</span>
              <span data-some={(a.counts.waiting ?? 0) > 0 || undefined}>{countText(a.counts.waiting)} waiting</span>
            </>
          )}
        </span>
        <span className="wb-headline">{a.headline}</span>
      </summary>
      <div className="wb-body">
        {a.paused !== null && a.paused.length > 0 && (
          <p className="bn-paused">{a.paused.map((p) => p.says).join(" · ")}</p>
        )}
        {a.paused === null && <p className="stamp">paused: UNKNOWN (the pause state could not be read)</p>}
        <InFlight a={a} />
        <Waiting a={a} now={now} />
        <Jobs a={a} />
        <div className="wb-block">
          <h4 className="k">Last {board.window_days} days</h4>
          <p className="wb-recent">{recentLine(a, board.window_days)}</p>
        </div>
        <Progress a={a} />
        <p className="stamp wb-mods">
          modules: {a.modules.length ? a.modules.join(", ") : "none"}
          {a.oversees.length > 0 && ` · oversees: ${a.oversees.join(", ")}`}
        </p>
      </div>
    </details>
  );
}

/** The whole page from one board: pure, so the test can render it. */
export function TasksView({ board, filter, now }: {
  board: Board; filter: string; now: number;
}): JSX.Element {
  const ordered = orderAgents(board.agents);
  const shown = filterAgents(ordered, filter);
  const cards = ordered.flatMap((a) => (a.waiting ?? []).map((c) => ({ ...c, who: a.label })))
    .sort((a, b) => {
      const priority = (iso: string | null): [number, number] => {
        const expiry = iso ? Date.parse(iso) : NaN;
        if (Number.isNaN(expiry)) return [1, 0];
        return expiry > now ? [0, expiry] : [2, expiry];
      };
      const first = priority(a.expires_at);
      const second = priority(b.expires_at);
      return first[0] - second[0] || first[1] - second[1] || a.id - b.id;
    });
  return (
    <div className="wb">
      <Strip board={board} />
      <p className="stamp">{totalsLine(board)}</p>

      <div className="panel wb-waiting" id="wb-waiting">
        <div className="inner">
          <h3 className="label">Waiting on you</h3>
          {/* Read-only on purpose. Decisions are answered by text, through the
              one path that goes via the executor — not by a button here. */}
          <p className="stamp">
            Answer these by text. Nothing on this screen can approve or deny,
            and an unanswered proposal expires rather than running.
          </p>
          {board.totals.waiting_on_you === null ? (
            <p className="stamp">UNKNOWN — the proposals could not be read.</p>
          ) : cards.length === 0 ? (
            <p className="empty">No decisions pending.</p>
          ) : (
            <table className="sheet wb-sheet">
              <thead>
                <tr><th>Card</th><th>Why</th><th className="wb-wide">Seat</th><th>Expires</th></tr>
              </thead>
              <tbody>
                {cards.map((c) => (
                  <tr key={c.id}>
                    <td>#{c.id} {c.action} <span className="bn-tier" data-tier={c.tier}>T{c.tier}</span></td>
                    <td>{c.reason}</td>
                    <td className="wb-wide">{c.who}</td>
                    <td title={c.expires_at ?? undefined}>{cardLine(c, now).split(" · ").pop()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {shown.length === 0 ? (
        <p className="empty">No seat matches “{filter}”.</p>
      ) : (
        <div className="wb-agents">
          {shown.map((a) => <Seat key={a.name} a={a} board={board} now={now} />)}
        </div>
      )}
    </div>
  );
}

/** A failed refresh invalidates cached figures until the next successful read. */
export function TasksContent({ board, error, filter, now }: {
  board: Board | null; error: string | null; filter: string; now: number;
}): JSX.Element | null {
  if (error) return <p className="errorbar" data-unknown="true">
    Could not read the work board: {error}. Current tasks: UNKNOWN.
  </p>;
  return board ? <TasksView board={board} filter={filter} now={now} /> : null;
}

export function Tasks({ reloadKey }: { reloadKey: number }): JSX.Element {
  const fresh = useFresh(() => api.tasksBoard(), [reloadKey]);
  const [filter, setFilter] = useState("");
  const data = fresh.data;

  return (
    <>
      <div className="wb-head">
        <h2 className="label">Tasks</h2>
        <label className="wb-filter">
          <span className="k">Filter</span>
          <input type="search" value={filter} placeholder="agent, department or module"
                 onChange={(e) => setFilter(e.target.value)} />
        </label>
      </div>
      <p className="stamp" style={{ marginBottom: 12 }}>
        {fresh.error ? "Latest read failed" : fresh.fetchedAt
          ? `Read ${fresh.fetchedAt.toLocaleTimeString()}` : "Reading…"}
        {data && !fresh.error
          && ` · window ${data.window_days}d · ${countText(data.totals.waiting_on_you)} waiting on you`}
      </p>
      <TasksContent board={data} error={fresh.error} filter={filter} now={Date.now()} />
    </>
  );
}
