import { useEffect, useRef, useState, type JSX } from "react";
import { api } from "../api";
import { HealthBadge, HealthRail } from "../components/Health";
import { current } from "../core/core-tiles";
import { useFresh } from "../hooks/useFresh";
import type { StreamState } from "../hooks/useStream";
import {
  figure, hhmmss, liveSnapshot, readStamp, refetchDelay, sortTasks, taskWords, tileView, whyUnknown,
} from "./hudModel";
import "./hudView.css";

const POLL_MS = 10_000;
const AGENTS_MS = 30_000;

/** Unknown, drawn the one way it is drawn everywhere: grey, dashed, the word. */
function Unknown({ why }: { why: string }): JSX.Element {
  return <span className="cmd-word-unknown hatch-edge" data-health="unknown">Unknown · {why}</span>;
}

/**
 * The HUD. Read-only: it shows what ATLAS is doing and what waits on the owner.
 * There is no button that approves or runs anything; a waiting card shows its
 * id and the owner decides in Slack. It shows no request text, only a short label
 * (source, route, status, time).
 */
export function Hud({ reloadKey, stream }: { reloadKey: number; stream: StreamState }): JSX.Element {
  const read = useFresh(api.hud);
  const agents = useFresh(api.agencyActivity);
  const lastFetch = useRef(0);
  const [, setNow] = useState(0);

  // A stream burst asks for one re-read, at most once per 5 s.
  useEffect(() => {
    if (reloadKey === 0) return undefined;
    const t = window.setTimeout(() => {
      lastFetch.current = Date.now();
      read.reload();
    }, refetchDelay(lastFetch.current, Date.now()));
    return (): void => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reloadKey]);

  // Poll only while the tab is visible.
  useEffect(() => {
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") {
        lastFetch.current = Date.now();
        read.reload();
        setNow((n) => n + 1);
      }
    }, POLL_MS);
    const ag = window.setInterval(() => {
      if (document.visibilityState === "visible") agents.reload();
    }, AGENTS_MS);
    return (): void => { window.clearInterval(id); window.clearInterval(ag); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const snap = liveSnapshot(read, stream);
  const live = snap !== null;
  const why = whyUnknown(read, stream);
  const waiting = figure(snap, (s) => ({ value: s.waiting.count, read_at: s.waiting.read_at }), why);
  const cards = snap?.waiting.cards ?? null;
  const tasks = figure(snap, (s) => ({ value: s.tasks.items, read_at: s.tasks.read_at }), why);
  const jobs = snap?.jobs ?? null;
  const act = current(agents);

  return (
    <section className="hud-view" aria-label="HUD">
      <header className="hud-head">
        <h1>HUD</h1>
        <span className="stamp">{live ? readStamp(snap.read_at) : <Unknown why={why} />}</span>
      </header>

      {snap?.watch_only === true && (
        <p className="hud-banner" role="status">Watch only: nothing runs</p>
      )}

      <h2>Waiting for you</h2>
      <div className="hud-block">
        {waiting.state === "unknown" ? <Unknown why={waiting.why} /> : (
          <>
            <p className="hud-big">{waiting.value}</p>
            <span className="stamp">{readStamp(waiting.read_at)}</span>
            <ul className="hud-list">
              {(cards ?? []).map((c) => (
                <li key={c.id}>
                  card #{c.id} · {c.action} · tier {c.tier}
                  {c.expired ? " · expired, not run" : " · waiting for your approval"}
                </li>
              ))}
            </ul>
          </>
        )}
      </div>

      <h2>Tasks</h2>
      <div className="hud-block">
        {tasks.state === "unknown" ? <Unknown why={tasks.why} /> : (
          <>
            <span className="stamp">{readStamp(tasks.read_at)}</span>
            {tasks.value.length === 0 && <p>No tasks in the last 24 hours.</p>}
            <ul className="hud-list">
              {sortTasks(tasks.value).map((t) => (
                <li key={t.id} data-state={t.state}>
                  <strong>{taskWords(t)}</strong> · {t.source} · {t.route} · {t.status}
                  {t.cards.length > 0 && ` · card ${t.cards.map((c) => `#${c}`).join(", ")}`}
                </li>
              ))}
            </ul>
            {(snap?.approved_in_slack ?? []).map((a, i) => (
              <p key={i} className="stamp">{a.action}: {a.label}</p>
            ))}
          </>
        )}
      </div>

      <h2>Missions</h2>
      <div className="hud-block">
        {!live || snap.missions.items === null ? <Unknown why={live ? "unreadable" : why} /> : (
          <>
            <span className="stamp">{readStamp(snap.missions.read_at)}</span>
            {snap.missions.items.length === 0 && <p>No missions.</p>}
            <ul className="hud-list">
              {snap.missions.items.map((m) => (
                <li key={m.id}>
                  <strong>{m.agent}</strong> · {m.status} · step: {m.phase}
                </li>
              ))}
            </ul>
          </>
        )}
      </div>

      <h2>Surfaces</h2>
      <div className="hud-tiles">
        {(snap?.surfaces.items ?? read.data?.surfaces.items ?? []).map((raw) => {
          const t = tileView(raw, live);
          return (
            <div key={t.key} className={`hud-tile${t.state === "unknown" ? " hatch-edge" : ""}`}
                 data-state={t.state}>
              <HealthRail health={t.health} />
              <div>
                <h3>{t.label}</h3>
                <HealthBadge health={t.health} />
                <p className={t.state === "unknown" ? "cmd-word-unknown" : ""}>{t.words}</p>
                {t.detail && <p className="stamp">{t.detail}</p>}
                <p className="stamp">{t.read_at ? readStamp(t.read_at) : "not read"}</p>
              </div>
            </div>
          );
        })}
      </div>

      <h2>Agents and jobs</h2>
      <div className="hud-block">
        {!live || jobs === null ? <Unknown why={why || "not read"} /> : (
          <>
            <span className="stamp">{readStamp(jobs.read_at)}</span>
            {jobs.running === null
              ? <Unknown why="unreadable" />
              : <p>{jobs.running.length} job(s) running{jobs.running.some((j) => j.stalled) ? ", one stalled" : ""}</p>}
            <ul className="hud-list">
              {(jobs.running ?? []).map((j) => (
                <li key={j.name}>
                  {j.name}{j.stalled === null ? " (claim time unknown)" : j.stalled ? " (stalled)" : ""}
                </li>
              ))}
              {jobs.background.map((n) => <li key={`bg-${n}`}>background: {n}</li>)}
            </ul>
            {jobs.mac === null ? <p><Unknown why="mac jobs unreadable" /></p> : (
              <ul className="hud-list">
                {jobs.mac.map((m, i) => <li key={i}>mac: {m.op} · {m.status}</li>)}
              </ul>
            )}
            <p className="stamp">
              mac agent last seen {jobs.unread.includes("mac_agent") || !jobs.mac_last_seen
                ? "unknown" : hhmmss(jobs.mac_last_seen)}
            </p>
          </>
        )}
        {act === null ? <Unknown why="agents not read" /> : (
          <ul className="hud-list">
            {act.agents.map((a) => (
              <li key={a.name}>
                {a.label}: {a.now.headline ?? "unknown"} <span className="stamp">{readStamp(a.now.read_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
