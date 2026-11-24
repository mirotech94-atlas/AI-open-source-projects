import { useState, type JSX } from "react";
import { api, type AgencyActivity, type AgentActivity, type AgentSummary } from "../api";
import { HealthBadge } from "../components/Health";
import { useFresh } from "../hooks/useFresh";
import { stamp } from "./cmdParts";
import { readOf, type Read } from "./homeModel";
import {
  agentHealth, agentMeter, issueLines, nowLines, yesterdayLines, type Line,
} from "./agentActivityModel";
import { rosterRow } from "./opsFloorModel";
import { isRetiredAgent } from "./roster";

/**
 * Beside the Agency map: one card per roster agent -- what it is doing now,
 * what it did yesterday, its issues -- each section with its own read time.
 * A read that failed is UNKNOWN (grey, dashed, the word), never a zero.
 * Reads only (`GET /api/agency/activity`), re-read every 30 s.
 */

function Lines({ lines }: { lines: Line[] }): JSX.Element {
  return (
    <ul className="act-lines">
      {lines.map((l, i) => (
        <li key={i} data-health={l.health === "plain" ? undefined : l.health}>
          {l.health === "unknown" && <span className="cmd-word-unknown">Unknown</span>}{" "}
          {l.text}
        </li>
      ))}
    </ul>
  );
}

/** The card's progress bar: yesterday's real ok/failed action split. UNKNOWN
 *  is a grey dashed track with the word, never an empty or full bar. */
function Meter({ a }: { a: AgentActivity }): JSX.Element {
  const m = agentMeter(a);
  if (m.state === "unknown") {
    return (
      <div className="act-meter" data-meter="unknown">
        <span className="act-meter-k">Actions ok</span>
        <span className="act-meter-track" aria-hidden="true" />
        <span className="cmd-word-unknown">Unknown</span>
      </div>
    );
  }
  return (
    <div className="act-meter" data-meter="read">
      <span className="act-meter-k">Actions ok</span>
      <span className="act-meter-track" role="img"
            aria-label={`${m.ok} of ${m.total} actions ok yesterday`}>
        <i className="ok" style={{ width: `${m.okPct}%` }} />
        <i className="no" style={{ width: `${m.failedPct}%` }} />
      </span>
      <span className="act-meter-v">{m.total === 0 ? "no action" : `${m.okPct}%`}</span>
    </div>
  );
}

/** A card's state. A failed roster read, or a roster status the server did not
 *  give, is "unknown"; "idle" is its own word and is never drawn as OK. */
type CardHealth = ReturnType<typeof agentHealth> | "idle";

function cardHealth(a: AgentActivity, sum: AgentSummary | null, rosterFailed: boolean,
                    hasRoster: boolean): CardHealth {
  if (!hasRoster) return agentHealth(a);
  if (rosterFailed || !sum) return "unknown";
  const row = rosterRow({ status: sum.status ?? null, shift_state: sum.shift_state ?? null });
  if (row.state === "unknown") return "unknown";
  if (row.state === "failing") return "failing";
  if (row.state === "idle") return "idle";
  return agentHealth(a);
}

function RosterBadge({ health }: { health: CardHealth }): JSX.Element {
  if (health === "idle") return <span className="badge" data-health="idle">Idle</span>;
  return <HealthBadge health={health} />;
}

function AgentCard({ a, sum, hasRoster, rosterFailed, expanded, onToggle }: {
  a: AgentActivity;
  sum: AgentSummary | null;
  hasRoster: boolean;
  rosterFailed: boolean;
  expanded: boolean;
  onToggle: () => void;
}): JSX.Element {
  const health = cardHealth(a, sum, rosterFailed, hasRoster);
  const detailId = `act-d-${a.name}`;
  const work = rosterFailed ? null : (sum?.last_outcome_detail || a.now.headline);
  return (
    <article className="act-card" data-agent={a.name} data-health={health}
             data-expanded={expanded || undefined}>
      <header>
        <b>{a.label}</b> <span className="act-title">{sum?.role ?? a.title}</span>
        {hasRoster
          ? <RosterBadge health={health} />
          : health === "unknown" && <span className="cmd-word-unknown">Unknown</span>}
      </header>
      {hasRoster && sum?.brief && !rosterFailed && <p className="act-brief">{sum.brief}</p>}
      {hasRoster && (
        <p className="act-work">
          <span className="v2-eyebrow">Current work</span>
          {work === null || work === undefined
            ? <span className="cmd-word-unknown">Unknown</span>
            : <span className="act-work-t">{work}</span>}
        </p>
      )}
      <Meter a={a} />
      {hasRoster && (
        <footer className="act-foot">
          <span className="stamp">{stamp(rosterFailed ? null : a.now.read_at, rosterFailed)}</span>
          <button type="button" className="act-toggle" aria-expanded={expanded}
                  aria-controls={detailId} onClick={onToggle}>
            {expanded ? "Hide details" : "Show details"}
          </button>
        </footer>
      )}
      <div className="act-detail" id={detailId} hidden={hasRoster && !expanded}>
        <section>
          <h4>Now <small>{stamp(a.now.read_at)}</small></h4>
          <Lines lines={nowLines(a)} />
        </section>
        <section>
          <h4>Yesterday <small>{a.yesterday.day} · {stamp(a.yesterday.read_at)}</small></h4>
          <Lines lines={yesterdayLines(a)} />
        </section>
        <section>
          <h4>Issues <small>{stamp(a.issues.read_at)}</small></h4>
          <Lines lines={issueLines(a)} />
        </section>
      </div>
    </article>
  );
}

/** The panel from its read: pure, so it can be tested. */
export function AgentActivityBody({ activity, roster, selected = null, onSelect }: {
  activity: Read<AgencyActivity>;
  /** Design v2: the roster read the cards take role, brief and status from. */
  roster?: Read<{ agents: AgentSummary[] }> | undefined;
  /** The agent whose card is open (a map node chosen, or its toggle). */
  selected?: string | null;
  onSelect?: ((name: string | null) => void) | undefined;
}): JSX.Element {
  const r = readOf(activity);
  const rr = roster ? readOf(roster) : null;
  const byName = new Map((rr?.data?.agents ?? []).map((x) => [x.name, x]));
  const agents = (r.data?.agents ?? []).filter((a) => !isRetiredAgent(a.name));
  return (
    <section className="act-panel" aria-labelledby="act-h">
      <header className="desk-map-head">
        <h2 id="act-h">Agent status &amp; metrics</h2>
        <span className="cmd-at">{stamp(activity.fetchedAt, r.error !== null)} · every 30 s</span>
      </header>
      {r.error !== null ? (
        <p className="cmd-unread" data-health="unknown">
          <span className="cmd-word-unknown">Unknown</span>{" "}
          The agents' activity could not be read: {r.error}
        </p>
      ) : !r.data ? <p className="cmd-empty">Reading…</p> : (
        <div className="act-grid">
          {agents.map((a) => (
            <AgentCard key={a.name} a={a} sum={byName.get(a.name) ?? null}
                       hasRoster={roster !== undefined} rosterFailed={rr !== null && rr.error !== null}
                       expanded={selected === a.name}
                       onToggle={() => onSelect?.(selected === a.name ? null : a.name)} />
          ))}
        </div>
      )}
    </section>
  );
}

/** `reloadKey` is the shell's: its poll bumps it every ACTIVITY_REFRESH_MS
 *  (30 s, App.tsx) and a burst of stream events bumps it sooner. */
export function AgentActivityPanel({ reloadKey, roster, selected, onSelect }: {
  reloadKey: number;
  roster?: Read<{ agents: AgentSummary[] }> | undefined;
  selected?: string | null;
  onSelect?: ((name: string | null) => void) | undefined;
}): JSX.Element {
  const activity = useFresh(() => api.agencyActivity(), [reloadKey]);
  // Used on its own (no tab holding the selection) a card still opens.
  const [own, setOwn] = useState<string | null>(null);
  const held = onSelect !== undefined;
  return <AgentActivityBody activity={activity} roster={roster}
                            selected={held ? selected ?? null : own}
                            onSelect={held ? onSelect : setOwn} />;
}
