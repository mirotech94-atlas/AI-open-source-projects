import { useEffect, useRef, type JSX } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, normaliseHealth } from "../api";
import { useFresh } from "../hooks/useFresh";
import { Gauge } from "../components/Gauge";
import { Feed } from "../components/Feed";
import { HealthBadge, HealthRail } from "../components/Health";
import { BoardCheck } from "../components/BoardCheck";
import { selfTestLine } from "./boardModel";

function rel(iso: string | null): string {
  if (!iso) return "never";
  const delta = Date.now() - new Date(iso).getTime();
  const mins = Math.round(Math.abs(delta) / 60000);
  const text = mins < 1 ? "<1m" : mins < 60 ? `${mins}m` : `${Math.round(mins / 60)}h`;
  return delta >= 0 ? `${text} ago` : `in ${text}`;
}

/** A check is treated as running when its next run is due and it has not
 *  reported since. Only then does the tile sweep. */
function scanning(next: string | null, last: string | null): boolean {
  if (!next) return false;
  const due = new Date(next).getTime();
  if (Date.now() < due) return false;
  return !last || new Date(last).getTime() < due;
}

export function Status({ reloadKey }: { reloadKey: number }): JSX.Element {
  const overview = useFresh(() => api.overview(), [reloadKey]);
  const counters = useFresh(() => api.status(), [reloadKey]);
  const feed = useFresh(() => api.activity({ limit: 60 }), [reloadKey]);

  // Rows newer than the previous render are the ones that fade in.
  const previous = useRef<number>(Date.now());
  const freshBefore = previous.current;
  useEffect(() => {
    previous.current = Date.now();
  }, [feed.data]);

  const modules = overview.data?.modules ?? [];
  const watched = modules.filter((m) => m.enabled && !m.setup);
  const c = counters.data;
  // Parked and retired checks, listed in words: neither reads as OK nor as
  // UNKNOWN. (UNKNOWN stays on its tile; it is counted in the self-test line.)
  const oddChecks = modules.flatMap((m) =>
    m.checks
      .filter((k) => k.state?.kind === "parked" || k.state?.kind === "retired")
      .map((check) => ({ module: m.module, check })));

  // `?focus=flycheap`: the module a spoken status just named. Its tile is
  // marked and scrolled to -- the same focus the voice card showed.
  const [params] = useSearchParams();
  const focused = params.get("focus");
  // Once per focused value: the tile is found on the first render that has
  // it, and later refreshes do not drag the page back to it.
  const scrolledTo = useRef<string | null>(null);
  useEffect(() => {
    if (!focused || scrolledTo.current === focused) return;
    const tile = document.querySelector(`[data-module="${CSS.escape(focused)}"]`);
    if (!tile) return;
    scrolledTo.current = focused;
    tile.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [focused, overview.data]);

  return (
    <>
      <h2 className="label">System status</h2>

      {overview.error && (
        <p className="errorbar">Could not read status: {overview.error}</p>
      )}

      {watched.length > 0 && (
        <div className="gauges">
          {watched.map((m) => (
            <Gauge
              key={m.module}
              name={m.module}
              health={m.health}
              ok={m.checks.filter((k) => normaliseHealth(k.health) === "ok").length}
              total={m.checks.length}
            />
          ))}
        </div>
      )}

      <div className="bd-lines" data-testid="self-test">
        {selfTestLine(overview.data?.self_test).map((line) => (
          <span key={line}>{line}</span>
        ))}
      </div>
      {oddChecks.length > 0 && (
        <ul className="bd-checks" aria-label="Checks that are not plainly OK">
          {oddChecks.map(({ module, check }) => (
            <BoardCheck key={`${module}.${check.name}`}
                        check={{ ...check, name: `${module}.${check.name}` }} />
          ))}
        </ul>
      )}

      <div className="status-grid">
        <section>
          <h2 className="label">Modules</h2>
          <div className="tiles">
            {modules.map((m) => {
              const isScanning =
                m.enabled && scanning(m.next_check_at, m.last_check_at);
              return (
                <div className="tile" key={m.module}
                     data-module={m.module}
                     data-focused={String(focused === m.module)}
                     data-health={normaliseHealth(m.health)}
                     data-setup={m.setup ? "true" : undefined}
                     data-scanning={String(isScanning)}>
                  <HealthRail health={m.health} />
                  <div className="body">
                    <div className="head">
                      <span className="name">
                        <Link to={`/activity?module=${m.module}`}>{m.module}</Link>
                      </span>
                      {m.setup
                        ? <span className="setup-badge" data-setup="true">{m.setup.toUpperCase()}</span>
                        : <HealthBadge health={m.health} />}
                    </div>
                    <div className="meta">
                      {m.enabled ? (
                        <>
                          <span>last {rel(m.last_check_at)}</span>
                          <span>next {rel(m.next_check_at)}</span>
                          {isScanning && <span style={{ color: "var(--cyan)" }}>scanning</span>}
                        </>
                      ) : (
                        <span>not watched</span>
                      )}
                    </div>
                    <div className={`finding${m.latest_finding ? "" : " none"}`}>
                      {m.setup
                        ? (m.checks.find((k) => k.state)?.detail || m.disabled_reason || m.setup)
                        : m.disabled_reason
                        ? m.disabled_reason
                        : m.latest_finding
                          ? m.latest_finding.title
                          : "no findings recorded"}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        <aside className="rail-panel">
          <h2 className="label">Activity</h2>
          <Feed rows={feed.data?.activity ?? []} freshBefore={freshBefore} />
        </aside>
      </div>

      <div style={{ height: 14 }} />

      <div className="bottombar" style={{ border: "1px solid var(--line)" }}>
        <div className="cell">
          <span className="n" data-tone="cyan">{c?.scans_today ?? "—"}</span>
          <span className="k">scans today</span>
        </div>
        <div className="cell">
          <span className="n" data-tone={c && c.issues_found > 0 ? "amber" : undefined}>
            {c?.issues_found ?? "—"}
          </span>
          <span className="k">issues found</span>
        </div>
        <div className="cell">
          {/* Kept separate from issues on purpose: a check that could not run
              is not a problem ATLAS found, it is an answer he does not have. */}
          <span className="n" data-tone="grey">{c?.unknown_today ?? "—"}</span>
          <span className="k">unmeasured</span>
        </div>
        <div className="cell">
          <span className="n" data-tone="cyan">{c?.auto_fixed ?? "—"}</span>
          <span className="k">auto-fixed</span>
        </div>
        <div className="cell">
          <span className="n" data-tone={c && c.waiting_on_you > 0 ? "amber" : undefined}>
            {c?.waiting_on_you ?? "—"}
          </span>
          <span className="k">waiting on you · by text</span>
        </div>
      </div>
    </>
  );
}
