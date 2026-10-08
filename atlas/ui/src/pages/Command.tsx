import type { JSX } from "react";
import { Link } from "react-router-dom";
import { api, normaliseHealth } from "../api";
import { useFresh } from "../hooks/useFresh";
import { Panel, Stat } from "../components/Panel";
import { Dial } from "../components/Dial";
import { HealthBadge } from "../components/Health";

function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString([], {
    hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

function rel(iso: string | null): string {
  if (!iso) return "never";
  const delta = Date.now() - new Date(iso).getTime();
  const mins = Math.round(Math.abs(delta) / 60000);
  const text = mins < 1 ? "<1m" : mins < 60 ? `${mins}m` : `${Math.round(mins / 60)}h`;
  return delta >= 0 ? `${text} ago` : `in ${text}`;
}

function bytes(n: number | null): string {
  if (n === null) return "—";
  const gb = n / 1024 ** 3;
  return gb >= 1 ? `${gb.toFixed(1)}G` : `${Math.round(n / 1024 ** 2)}M`;
}

/** The core visual. Concentric rings, drawn once, animated only by CSS.
 *
 * Exported because the Command Center shows the same globe under a different
 * caption. Two hand-drawn globes would drift; one with two captions cannot.
 * The defaults are this page's own, so `/core` renders exactly as before.
 */
export function Core({ health, hurt, blind, name = "ATLAS",
                       sub = "personal operations", caption }: {
  health: string; hurt: string[]; blind: number;
  name?: string; sub?: string; caption?: string;
}): JSX.Element {
  // "FAILING" under the wordmark read as "ATLAS is failing". It never meant
  // that: ATLAS was fine and something it watches was not. The word is the
  // estate's, so say whose -- the same rule as the pill in the top bar.
  const word =
    health === "ok" ? "all clear"
      : health === "unknown"
        ? `${blind} unmeasured`
        : `${hurt.length} need${hurt.length === 1 ? "s" : ""} attention`;
  return (
    <div className="core-vis" data-health={health}
         title={hurt.length ? `${hurt.join(", ")} — ATLAS itself is fine` : undefined}>
      <svg viewBox="0 0 320 320" role="img" aria-label={`ATLAS core: ${health}`}>
        {[150, 122, 96, 70].map((r, i) => (
          <circle key={r} cx="160" cy="160" r={r} fill="none"
                  stroke="var(--state)" strokeOpacity={0.14 + i * 0.07}
                  strokeWidth={i === 1 ? 1.4 : 0.8}
                  strokeDasharray={i % 2 ? "2 7" : undefined}
                  className={`cv r${i}`} />
        ))}
        {/* Latitude lines: a globe, not a logo. */}
        {[-56, -28, 0, 28, 56].map((offset) => (
          <ellipse key={offset} cx="160" cy="160" rx="96"
                   ry={Math.max(6, 96 * Math.cos((offset * Math.PI) / 180) * 0.42)}
                   fill="none" stroke="var(--state)" strokeOpacity=".16"
                   strokeWidth=".7" transform={`translate(0 ${offset * 0.92})`} />
        ))}
        {[0, 36, 72, 108, 144].map((angle) => (
          <ellipse key={angle} cx="160" cy="160" rx="34" ry="96" fill="none"
                   stroke="var(--state)" strokeOpacity=".13" strokeWidth=".7"
                   transform={`rotate(${angle} 160 160)`} />
        ))}
        <circle cx="160" cy="160" r="96" fill="none" stroke="var(--state)"
                strokeOpacity=".3" strokeWidth="1" />
      </svg>
      <div className="core-text">
        <span className="ct-name">{name}</span>
        <span className="ct-sub">{sub}</span>
        <span className="ct-state">{(caption ?? word).toUpperCase()}</span>
      </div>
    </div>
  );
}

export function Command({ reloadKey }: { reloadKey: number }): JSX.Element {
  const overview = useFresh(() => api.overview(), [reloadKey]);
  const counters = useFresh(() => api.status(), [reloadKey]);
  const feed = useFresh(() => api.activity({ limit: 40 }), [reloadKey]);
  const sys = useFresh(() => api.system(), [reloadKey]);
  const agents = useFresh(() => api.agents(), [reloadKey]);
  const llm = useFresh(() => api.llm(), [reloadKey]);
  const mem = useFresh(() => api.memory(), [reloadKey]);

  const modules = overview.data?.modules ?? [];
  const watched = modules.filter((m) => m.enabled && !m.setup);
  const worst = watched.reduce<string>((acc, m) => {
    const rank: Record<string, number> = { ok: 0, unknown: 1, degraded: 2, failing: 3 };
    const h = normaliseHealth(m.health);
    return (rank[h] ?? 1) > (rank[acc] ?? 0) ? h : acc;
  }, watched.length ? "ok" : "unknown");

  const hurt = watched
    .filter((m) => ["failing", "degraded"].includes(normaliseHealth(m.health)))
    .map((m) => m.title || m.module);
  const blind = watched.filter((m) => normaliseHealth(m.health) === "unknown").length;

  const upcoming = watched
    .filter((m) => m.next_check_at)
    .sort((a, b) => (a.next_check_at ?? "").localeCompare(b.next_check_at ?? ""))
    .slice(0, 6);

  return (
    <div className="cc">
      {/* --- left column ------------------------------------------------ */}
      <div className="cc-left">
        <Panel title="Core overview">
          <ul className="corelist">
            <li>
              <span className="k">Modules</span>
              <span className="v">{watched.length} watched</span>
              <HealthBadge health={worst} />
            </li>
            <li>
              <span className="k">Not watched</span>
              <span className="v">{modules.length - watched.length}</span>
              <span className="note">missing credentials</span>
            </li>
            <li>
              <span className="k">Memory</span>
              <span className="v">{mem.data?.total ?? "—"}</span>
              <span className="note">{mem.data ? "stored" : "unreadable"}</span>
            </li>
            <li>
              <span className="k">Agents</span>
              <span className="v">
                {agents.data
                  ? `${agents.data.agents.filter((a) => a.available).length}/${agents.data.agents.length}`
                  : "—"}
              </span>
              <span className="note">can run</span>
            </li>
            <li>
              <span className="k">Providers</span>
              <span className="v">{llm.data?.vendors.length ?? "—"}</span>
              <span className="note">{llm.data?.vendors.join(" · ") || "none wired"}</span>
            </li>
            <li>
              <span className="k">Mode</span>
              <span className="v" style={{ color: "var(--amber)" }}>
                {counters.data?.watch_only ? "watch-only" : "armed"}
              </span>
              <span className="note">
                {counters.data?.watch_only ? "cannot act" : "Tier 1 acts"}
              </span>
            </li>
          </ul>
        </Panel>

        <Panel title="System monitor"
               unavailable={sys.error ? `Could not read the host: ${sys.error}` : null}>
          <div className="dials">
            <Dial percent={sys.data?.cpu_percent ?? null} label="CPU" />
            <Dial percent={sys.data?.memory.percent ?? null} label="RAM"
                  detail={`${bytes(sys.data?.memory.used ?? null)} / ${bytes(sys.data?.memory.total ?? null)}`} />
            <Dial percent={sys.data?.disk.percent ?? null} label="Disk"
                  detail={`${bytes(sys.data?.disk.used ?? null)} / ${bytes(sys.data?.disk.total ?? null)}`} />
          </div>
        </Panel>
      </div>

      {/* --- centre ------------------------------------------------------ */}
      <div className="cc-mid">
        <Core health={worst} hurt={hurt} blind={blind} />

        <Panel title="Mission timeline"
               right={<Link to="/activity">Activity ›</Link>}>
          {upcoming.length === 0 ? (
            <p className="empty">
              Nothing scheduled — no module is being watched.
            </p>
          ) : (
            <ul className="timeline">
              {upcoming.map((m) => (
                <li key={m.module}>
                  <span className="t">{m.next_check_at ? clock(m.next_check_at) : "—"}</span>
                  <span className="bar" aria-hidden="true" />
                  <span className="what">
                    <Link to={`/activity?module=${m.module}`}>{m.module}</Link>
                    <span className="note"> · {rel(m.next_check_at)}</span>
                  </span>
                  <HealthBadge health={m.health} />
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel title="Active agents"
               right={agents.data ? `${agents.data.agents.length}` : ""}>
          <div className="agents">
            {(agents.data?.agents ?? []).map((a) => (
              <div className="agent" key={a.name} data-available={String(a.available)}>
                <span className="dot" aria-hidden="true" />
                <div className="meta">
                  <span className="n">{a.name}</span>
                  {/* Not "running": agents are invoked per request. What
                      matters is whether the tier they need resolves. */}
                  <span className="s">
                    {a.available ? `ready · ${a.providers[0]}` : "no provider"}
                  </span>
                </div>
                <span className="tier">{a.need ?? ""}</span>
              </div>
            ))}
          </div>
        </Panel>
      </div>

      {/* --- right column ------------------------------------------------ */}
      <div className="cc-right">
        <Panel title="Live intelligence"
               right={<Link to="/activity">All ›</Link>}
               unavailable={feed.error ? `Feed unavailable: ${feed.error}` : null}>
          <ul className="intel">
            {(feed.data?.activity ?? []).slice(0, 12).map((row) => (
              <li key={`${row.kind}-${row.id ?? row.at}-${row.subject}`}
                  data-kind={row.kind}
                  data-health={row.health ? normaliseHealth(row.health) : undefined}>
                <span className="tag">{row.kind}</span>
                <span className="body">
                  <span className="s">{row.subject}</span>
                  <span className="d">{row.detail}</span>
                </span>
                <span className="t">{clock(row.at)}</span>
              </li>
            ))}
            {feed.data && feed.data.activity.length === 0 && (
              <li className="empty">Nothing yet.</li>
            )}
          </ul>
        </Panel>

        <Panel title="LLM status"
               right={llm.data ? `${llm.data.vendors.length} wired` : ""}>
          <div className="llms">
            {Object.entries(llm.data?.needs ?? {}).map(([need, info]) => (
              <div className="llm" key={need} data-connected={String(info.connected)}>
                <span className="dot" aria-hidden="true" />
                <span className="n">{need}</span>
                <span className="p">
                  {info.providers.join(" → ") || "not connected"}
                </span>
                {info.cross_provider === false && (
                  <span className="warn" title="Two models from one vendor is not a second opinion">
                    single vendor
                  </span>
                )}
              </div>
            ))}
          </div>
        </Panel>

        <Panel title="Memory">
          <div className="memgrid">
            {Object.entries(mem.data?.kinds ?? {}).map(([kind, n]) => (
              <Stat key={kind} value={n} label={kind} />
            ))}
            <Stat value={mem.data?.voice_turns ?? null} label="voice turns" />
            <Stat value={mem.data?.tool_calls ?? null} label="tool calls" />
          </div>
        </Panel>
      </div>
    </div>
  );
}
