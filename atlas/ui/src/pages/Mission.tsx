import { Suspense, lazy, useEffect, useState, type JSX } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  api, normaliseHealth, type ActivityRow, type AgendaEntry,
} from "../api";
import { useDraggableTiles } from "../hooks/useDraggableTiles";
import { useFresh } from "../hooks/useFresh";
import type { useVoice } from "../voice/useVoice";
import { Panel } from "../components/Panel";
import { Dial } from "../components/Dial";
import { Tile } from "../components/Tile";
import { Sparkline } from "../components/Sparkline";
import { Core } from "./Command";
import { current } from "../core/core-tiles";
import { statusOf } from "../voice/loop";

// Lazy for the same reason /core loads it lazily: `three` is megabytes, and
// the figure is the only thing on this page that needs it.
const Bust = lazy(() =>
  import("../core/Bust").then((m) => ({ default: m.Bust })));

/** What the figure is doing, said the way he would say it. */
const FIGURE_WORDS = {
  IDLE: "Say “ATLAS” or tap him to talk",
  LISTENING: "Listening",
  THINKING: "Thinking",
  SPEAKING: "Speaking",
} as const;
import {
  agentsWords, coreConnected as isCoreConnected, estate, llmsWords, memoryWords,
  systemWords,
} from "./missionModel";

// The dashboard's own version, shown under the wordmark when the server does
// not publish a build sha. Kept in step with ui/package.json by hand — there
// is one of each and they are two lines apart in a review.
const APP_VERSION = "1.0.0";

const HOUR_MS = 3600_000;
const BUCKETS = 24;

// The providers `atlas/kernel/llm.py` knows how to build a chain from. Listed
// here so the panel can say "Not linked" about one that is absent — the API
// reports what IS wired and has no way to name what is not.
const KNOWN_VENDORS: readonly string[] =
  ["anthropic", "openai", "local", "openrouter"];

function clock(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString([], {
    hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

/** Minute-granular on purpose: "in 42m" is the phrase on the timeline, and
 *  rounding it to the hour would make the screen say "in 1h" for something
 *  starting in three minutes. */
function until(iso: string): string {
  const delta = new Date(iso).getTime() - Date.now();
  if (delta <= 0) return "Done";
  const mins = Math.floor(delta / 60000);
  if (mins < 60) return `in ${Math.max(1, mins)}m`;
  const hours = Math.floor(mins / 60);
  return hours < 24 ? `in ${hours}h ${mins % 60}m` : `in ${Math.floor(hours / 24)}d`;
}

function readStamp(at: Date | null): string {
  return at ? `read ${at.toLocaleTimeString([], { hour12: false })}` : "not read yet";
}

/** INFO / WARN / TIP / LIVE, from what the row actually is. A finding ATLAS
 *  rated high is a warning; a low-severity one is a suggestion; an action is
 *  something happening now; a scan is the background hum. */
function tagFor(row: ActivityRow): "INFO" | "WARN" | "TIP" | "LIVE" {
  if (row.kind === "action") return "LIVE";
  if (row.kind === "finding") return (row.severity ?? 1) >= 2 ? "WARN" : "TIP";
  return normaliseHealth(row.health) === "ok" ? "INFO" : "WARN";
}

export function Mission({ reloadKey, voice, search }: {
  reloadKey: number;
  voice: ReturnType<typeof useVoice>;
  search: string;
}): JSX.Element {
  const navigate = useNavigate();
  // Fist-grab drags a tile or a panel, and the layout is remembered per
  // browser -- the same hook Fleet's cards demonstrated first. The six AI
  // Core Overview tiles and the six row-2/row-3 panels opt in; the centre
  // Core widget and the live intelligence feed do not, so the eye always has
  // a fixed anchor to come back to.
  const { styleFor } = useDraggableTiles("mission");

  const overview = useFresh(() => api.overview(), [reloadKey]);
  const feed = useFresh(() => api.activity({ limit: 60 }), [reloadKey]);
  const sys = useFresh(() => api.system(), [reloadKey]);
  const agents = useFresh(() => api.agents(), [reloadKey]);
  const agentFeed = useFresh(() => api.agentsFeed(200), [reloadKey]);
  const llm = useFresh(() => api.llm(), [reloadKey]);
  const mem = useFresh(() => api.memory(), [reloadKey]);
  const graph = useFresh(() => api.memoryGraph(), [reloadKey]);
  const agenda = useFresh(() => api.agenda(), [reloadKey]);
  const tasks = useFresh(() => api.tasks(), [reloadKey]);

  // One tick a minute, so "in 42m" and "expiring in <1h" stay true without a
  // refetch. Nothing here re-reads the server; the numbers are unchanged, only
  // the distance to them.
  const [, setTick] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => setTick((n) => n + 1), 30_000);
    return (): void => window.clearInterval(timer);
  }, []);

  // --- the estate reduction, the same one the top bar and /core use -------
  // Every figure below is read through `current()` (missionModel.ts): after
  // a failed refresh `useFresh` keeps the old data, and a tile lit from it
  // would be an old number drawn as live. UNKNOWN instead -- grey, dashed,
  // the word visible -- as on /core.
  const { worst, hurt, blind } = estate(overview);

  // --- left column: the six subsystem tiles -------------------------------
  const coreConnected = isCoreConnected(llm);
  const memTile = memoryWords(mem);
  const agentsTile = agentsWords(agents);
  const llmsTile = llmsWords(llm);
  const llmNow = current(llm);

  // "Online" is a claim about two capabilities, so it is only made when both
  // hold — and when it does not, the tile names which one is missing rather
  // than falling back to a vague amber.
  const canHear = voice.mic !== "unavailable" && voice.mic !== "denied";
  const canAnswer = coreConnected;
  const voiceWord = canHear && canAnswer
    ? "Online"
    : !canHear && !canAnswer ? "No mic, no model"
      : !canHear ? (voice.mic === "denied" ? "Mic blocked" : "No microphone")
        : "No model connected";

  // Optimal is a conjunction, and a missing reading is never one of the
  // conjuncts: if CPU could not be read, the box is not known to be optimal.
  const { state: systemState, value: systemWord } = systemWords(sys);
  const sysNow = current(sys);
  const cpu = sysNow?.cpu_percent ?? null;
  const ram = sysNow?.memory.percent ?? null;
  const disk = sysNow?.disk.percent ?? null;

  // --- centre: the version under the wordmark ------------------------------
  const version = sys.data?.version ?? APP_VERSION;

  // --- right: the live intelligence feed -----------------------------------
  const needle = search.trim().toLowerCase();
  // The search filters everything in this panel, overdue rows included. A box
  // that quietly skipped the rows pinned to the top would be worse than one
  // that skipped nothing.
  // Pinned into the feed panel, which hides only on the FEED's error: read
  // the proposals through current() so a failed refresh drops them.
  const soon = (current(tasks)?.proposals ?? []).filter((p) => {
    if (!p.expires_at) return false;
    const left = new Date(p.expires_at).getTime() - Date.now();
    if (left <= 0 || left >= HOUR_MS) return false;
    return !needle
      || p.action.toLowerCase().includes(needle)
      || p.reason.toLowerCase().includes(needle)
      || (p.finding ?? "").toLowerCase().includes(needle);
  });
  const rows = (feed.data?.activity ?? []).filter((r) =>
    !needle
    || r.subject.toLowerCase().includes(needle)
    || r.detail.toLowerCase().includes(needle)
    || r.module.toLowerCase().includes(needle));

  // --- active agents: runs per hour, bucketed from what actually came back --
  const items = agentFeed.data?.items ?? [];
  const oldest = items.length ? items[items.length - 1]?.at ?? null : null;
  const now = Date.now();
  const series = new Map<string, number[]>();
  for (const item of items) {
    const age = now - new Date(item.at).getTime();
    const bucket = BUCKETS - 1 - Math.floor(age / HOUR_MS);
    if (bucket < 0 || bucket >= BUCKETS) continue;
    const line = series.get(item.agent) ?? Array.from({ length: BUCKETS }, () => 0);
    line[bucket] = (line[bucket] ?? 0) + 1;
    series.set(item.agent, line);
  }
  // The feed is capped server-side, so the honest window for the SPARKLINE is
  // the span of what came back — claiming 24h when 200 rows only reached back
  // three would be a number with the wrong time attached to it. The count
  // beside it is the server's own 24h figure and keeps its own label; the two
  // windows are different and must not borrow each other's words.
  const sparkWindow = items.length >= 200 && oldest
    ? `since ${clock(oldest)}`
    : "last 24h";

  // `/api/llm` lists only the vendors that appear in a routing chain, so a
  // panel built from it alone can never say anything but "Connected" — it has
  // no way to name what is missing. The known set is what makes "Not linked"
  // sayable. Anything the server reports that is not in the set is still shown
  // (and connected), so a provider added to llm.py cannot vanish from here.
  const vendorRoster = (() => {
    if (!llmNow) return [];
    const connected = new Set(llmNow.vendors);
    const extra = llmNow.vendors.filter((v) => !KNOWN_VENDORS.includes(v));
    return [...KNOWN_VENDORS, ...extra].map((name) => ({
      name, connected: connected.has(name),
    }));
  })();

  const agendaItems: AgendaEntry[] = (agenda.data?.items ?? []).slice(0, 6);
  const nodes = graph.data?.nodes ?? [];
  const biggest = nodes.reduce((m, n) => Math.max(m, n.total), 0);

  return (
    <div className="mc">
      {/* --- row 1 -------------------------------------------------------- */}
      <div className="mc-r1">
        <div className="mc-col">
          <Panel title="AI core overview">
            <div className="mc-tiles">
              <Tile glyph="◈" name="AI Core" state={coreConnected ? "live" : "unknown"}
                    value={coreConnected ? "Active" : "Unavailable"}
                    readAt={llm.fetchedAt} to="/core"
                    note={coreConnected ? "a model route resolves" : "no route resolves"}
                    tileId="tile-core" style={styleFor("tile-core")} />
              <Tile glyph="⊚" name="Memory" state={memTile.state}
                    value={memTile.value}
                    readAt={mem.fetchedAt} to="/memory"
                    tileId="tile-memory" style={styleFor("tile-memory")} />
              <Tile glyph="◍" name="Voice" state={canHear && canAnswer ? "live" : "unknown"}
                    value={voiceWord} readAt={llm.fetchedAt}
                    note="the device and the model behind it, not the intention"
                    tileId="tile-voice" style={styleFor("tile-voice")} />
              <Tile glyph="⁂" name="Agents" state={agentsTile.state}
                    value={agentsTile.value}
                    readAt={agents.fetchedAt} to="/agency"
                    note="available means the tier it needs resolves to a provider"
                    tileId="tile-agents" style={styleFor("tile-agents")} />
              <Tile glyph="◇" name="LLMs"
                    state={llmsTile.state}
                    value={llmsTile.value}
                    readAt={llm.fetchedAt}
                    tileId="tile-llms" style={styleFor("tile-llms")} />
              <Tile glyph="▤" name="System" state={systemState} value={systemWord}
                    readAt={sys.fetchedAt}
                    note="optimal only when CPU under 80%, RAM and disk under 85%"
                    tileId="tile-system" style={styleFor("tile-system")} />
            </div>
          </Panel>
        </div>

        {/* The presence: ATLAS's figure beside the core it runs on. The
            figure is the voice loop made visible -- it listens, thinks and
            speaks with the real phase and amplitude -- and the orb is the
            estate's health. Tap the figure to talk. */}
        <div className="mc-core mc-presence" data-phase={voice.phase}>
          <figure className="mc-figure">
            <Suspense fallback={<div className="bust" aria-hidden="true" />}>
              <Bust phase={voice.phase} bars={voice.bars}
                    onWake={voice.phase === "dormant" ? voice.wake : voice.interrupt} />
            </Suspense>
            <span className="mc-figname">Cabled ATLAS</span>
            <figcaption className="mc-figcap">
              <span className="dot" aria-hidden="true" />
              {FIGURE_WORDS[statusOf(voice.phase)]}
            </figcaption>
          </figure>
          <Core health={worst} hurt={hurt} blind={blind}
                sub="AI Core" caption={`v${version}`} />
        </div>

        <div className="mc-col">
          <Panel title="Live intelligence feed"
                 right={<Link to="/activity">All ›</Link>}
                 unavailable={feed.error ? `Feed unavailable: ${feed.error}` : null}>
            <ul className="intel mc-intel">
              {soon.map((p) => (
                <li key={`soon-${p.id}`} data-tag="WARN">
                  <span className="tag">WARN</span>
                  <span className="body">
                    <span className="s">{p.action}</span>
                    <span className="d">
                      expires {p.expires_at ? until(p.expires_at) : "—"} · silence is not consent
                    </span>
                  </span>
                  <span className="t">{clock(p.expires_at)}</span>
                </li>
              ))}
              {rows.slice(0, 14).map((row) => (
                <li key={`${row.kind}-${row.id ?? row.at}-${row.subject}`}
                    data-tag={tagFor(row)}
                    data-health={row.health ? normaliseHealth(row.health) : undefined}>
                  <span className="tag">{tagFor(row)}</span>
                  <span className="body">
                    <span className="s">{row.subject}</span>
                    <span className="d">{row.detail}</span>
                  </span>
                  <span className="t">{clock(row.at)}</span>
                </li>
              ))}
              {feed.data && rows.length === 0 && soon.length === 0 && (
                <li className="empty">
                  {needle ? `Nothing matching “${search}”.` : "Nothing yet."}
                </li>
              )}
            </ul>
            <p className="stamp mc-stamp">{readStamp(feed.fetchedAt)}</p>
          </Panel>
        </div>
      </div>

      {/* --- row 2 -------------------------------------------------------- */}
      <div className="mc-r2">
        <Panel title="Active agents" right={<Link to="/agency">View all ›</Link>}
               unavailable={agents.error ? `Roster unavailable: ${agents.error}` : null}
               tileId="panel-agents" style={styleFor("panel-agents")}>
          <div className="mc-agents">
            {(agents.data?.agents ?? []).slice(0, 6).map((a) => {
              const ranRecently = a.last_run_at !== null
                && now - new Date(a.last_run_at).getTime() < HOUR_MS;
              // A tick is a claim that this agent both can run and did. An
              // agent with no provider never gets one, however recently it
              // happened to run before the provider went away.
              const ticked = a.available && ranRecently;
              return (
                <div className="mc-agent" key={a.name}
                     data-state={a.available ? (ranRecently ? "working" : "available") : "unknown"}>
                  <span className="hd">
                    <span className="n">{a.title || a.name}</span>
                    {ticked && <span className="tick" aria-label="ran within the hour">✓</span>}
                  </span>
                  <span className="st">
                    {a.available ? (ranRecently ? "Active" : "Standby") : "Unavailable"}
                  </span>
                  <Sparkline values={series.get(a.name) ?? []}
                             label={`${a.name} runs per hour, ${sparkWindow}`} />
                  <span className="sub" title={`sparkline covers ${sparkWindow}`}>
                    {a.runs_24h} run{a.runs_24h === 1 ? "" : "s"} · last 24h
                  </span>
                </div>
              );
            })}
            {agents.data && agents.data.agents.length === 0 && (
              <p className="empty">No agents registered.</p>
            )}
          </div>
          <p className="stamp mc-stamp">{readStamp(agentFeed.fetchedAt)}</p>
        </Panel>

        <Panel title="Mission timeline"
               right={<Link to="/calendar">Full schedule ›</Link>}
               unavailable={agenda.data?.unavailable
                 ?? (agenda.error ? `Agenda unavailable: ${agenda.error}` : null)}
               tileId="panel-timeline" style={styleFor("panel-timeline")}>
          {agendaItems.length === 0 ? (
            <p className="empty">Nothing scheduled inside 72 hours.</p>
          ) : (
            <ul className="timeline">
              {agendaItems.map((item) => (
                <li key={`${item.when}-${item.title}`}>
                  <span className="t">{clock(item.when)}</span>
                  <span className="bar" aria-hidden="true" />
                  <span className="what" title={item.detail || undefined}>
                    {item.title}
                    <span className="note"> · {item.source}</span>
                  </span>
                  <span className="when"
                        title={until(item.when) === "Done"
                          ? "its time has passed — not a claim that it was completed"
                          : undefined}>
                    {until(item.when)}
                  </span>
                </li>
              ))}
            </ul>
          )}
          <p className="stamp mc-stamp">{readStamp(agenda.fetchedAt)}</p>
        </Panel>

        {/* Four buttons, and every one of them only moves the screen. There is
            no write endpoint on this dashboard beyond approvals, and a command
            palette that could start something would be the first one. */}
        <Panel title="Quick commands" tileId="panel-quick" style={styleFor("panel-quick")}>
          <div className="mc-quick">
            <button className="btn" onClick={() => navigate("/tasks")}>
              <span className="g" aria-hidden="true">＋</span> New task
            </button>
            <button className="btn" onClick={() => navigate("/calendar")}>
              <span className="g" aria-hidden="true">▦</span> Open calendar
            </button>
            <button className="btn" onClick={voice.wake}>
              <span className="g" aria-hidden="true">◍</span> Start voice chat
            </button>
            <button className="btn" onClick={() => navigate("/workflows")}>
              <span className="g" aria-hidden="true">▷</span> Run workflow
            </button>
          </div>
          <p className="stamp mc-stamp">
            These open a screen. Nothing here sends, buys or changes anything.
          </p>
        </Panel>
      </div>

      {/* --- row 3 -------------------------------------------------------- */}
      <div className="mc-r3">
        <Panel title="System monitor"
               unavailable={sys.error ? `Could not read the host: ${sys.error}` : null}
               tileId="panel-sysmon" style={styleFor("panel-sysmon")}>
          <div className="dials">
            <Dial percent={cpu} label="CPU" detail={readStamp(sys.fetchedAt)} />
            <Dial percent={ram} label="RAM" detail={readStamp(sys.fetchedAt)} />
            <Dial percent={disk} label="Disk" detail={readStamp(sys.fetchedAt)} />
          </div>
        </Panel>

        <Panel title="Memory insights" right={<Link to="/memory">View memory map ›</Link>}
               unavailable={graph.error ? `Graph unavailable: ${graph.error}` : null}
               tileId="panel-meminsights" style={styleFor("panel-meminsights")}>
          <div className="mc-constellation">
            <svg viewBox="0 0 220 96" role="img"
                 aria-label={`Memory: ${nodes.length} namespaces`}>
              {nodes.slice(0, 14).map((n, i, all) => {
                const x = 12 + (i * 196) / Math.max(1, all.length - 1);
                const y = 20 + ((i * 37) % 56);
                const r = biggest > 0 ? 2 + 4 * Math.sqrt(n.total / biggest) : 2;
                const next = all[i + 1];
                const nx = 12 + ((i + 1) * 196) / Math.max(1, all.length - 1);
                const ny = 20 + (((i + 1) * 37) % 56);
                return (
                  <g key={n.id}>
                    {next && <line x1={x} y1={y} x2={nx} y2={ny}
                                   stroke="var(--line)" strokeWidth="1" />}
                    <circle cx={x} cy={y} r={r}
                            fill={n.total > 0 ? "var(--cyan)" : "transparent"}
                            stroke={n.total > 0 ? "none" : "var(--grey)"}
                            strokeDasharray={n.total > 0 ? undefined : "2 2"}>
                      <title>{`${n.id.replace(/^ns:/, "")} — ${n.total} stored`}</title>
                    </circle>
                  </g>
                );
              })}
            </svg>
            <div className="mc-counts">
              {/* This panel hides on the GRAPH's error; the counts come from
                  a different read, so a failed one shows a dash, not the old
                  number. */}
              <span><b>{current(mem)?.total ?? "—"}</b> memories</span>
              <span><b>{current(mem)?.voice_turns ?? "—"}</b> voice turns</span>
              <span><b>{current(mem)?.tool_calls ?? "—"}</b> tool calls</span>
            </div>
          </div>
          <p className="stamp mc-stamp">{readStamp(mem.fetchedAt)}</p>
        </Panel>

        <Panel title="LLM status"
               right={llmNow ? `${llmNow.vendors.length} connected` : ""}
               unavailable={llm.error ? `Routing unreadable: ${llm.error}` : null}
               tileId="panel-llm" style={styleFor("panel-llm")}>
          <div className="mc-vendors">
            {vendorRoster.map(({ name, connected }) => (
              <span className="mc-vendor" key={name}
                    data-connected={String(connected)}>
                <span className="dot" aria-hidden="true" />{name}
                <i>{connected ? "Connected" : "Not linked"}</i>
              </span>
            ))}
            {llmNow && vendorRoster.length === 0 && (
              <span className="mc-vendor" data-connected="false">
                <span className="dot" aria-hidden="true" />no vendor
                <i>Not linked</i>
              </span>
            )}
          </div>
          <p className="stamp mc-stamp">{readStamp(llm.fetchedAt)}</p>
        </Panel>
      </div>
    </div>
  );
}
