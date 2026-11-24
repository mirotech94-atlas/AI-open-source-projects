import { createContext, useContext, useEffect, type JSX } from "react";
import type { Overview } from "../api";
import { overall, statusPill } from "../pages/commandModel";
import { readTime, whyList, type WhyRow } from "../pages/healthWhy";
import type { LiveRead } from "../hooks/useLiveRead";
import "./whyPanel.css";

/** Opens the "Why" list. The header pill and the status ring both call it. */
export const WhyContext = createContext<() => void>(() => undefined);
export function useWhy(): () => void {
  return useContext(WhyContext);
}

function Row({ r }: { r: WhyRow }): JSX.Element {
  return (
    <li className="why-row" data-health={r.health}>
      <span className="why-mod">{r.module}</span>
      <span className="why-check">{r.check}</span>
      <span className="why-word">{r.health === "unknown" ? "UNKNOWN" : r.health.toUpperCase()}</span>
      <span className="why-detail">{r.detail}</span>
      <span className="why-at">{r.stale ? "stale, " : ""}{readTime(r.readAt)}</span>
    </li>
  );
}

/** Every failing, degraded and unknown check behind the pill and the ring,
 *  failing first, unknown grouped by module. Reads nothing itself: it shows
 *  the overview the pill already read, with that read's time. */
export function WhyPanel({ overview, onClose }: {
  overview: LiveRead<Overview | null>;
  onClose: () => void;
}): JSX.Element {
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return (): void => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const failed = overview.error !== null || !overview.data;
  const modules = failed ? null : overview.data?.modules ?? null;
  const pill = statusPill(overall(modules), failed);
  const list = whyList(modules);
  const at = failed
    ? `read failed ${readTime(overview.attemptedAt ?? null).replace("read ", "")}`
    : readTime(overview.fetchedAt);

  return (
    <div className="why-back" onClick={onClose} role="presentation">
      <section className="why" role="dialog" aria-label="Why ATLAS reads this"
               data-health={pill.health} onClick={(e) => e.stopPropagation()}>
        <header className="why-head">
          <h2>{pill.text}</h2>
          <span className="why-stamp">{at}</span>
          <button type="button" className="why-close" onClick={onClose} aria-label="Close">×</button>
        </header>
        {failed ? (
          <p className="why-none" data-health="unknown">
            UNKNOWN: checks unreachable{overview.error ? ` (${overview.error})` : ""}.
            Nothing below can be trusted until the next read succeeds.
          </p>
        ) : (
          <>
            {list.attention.length === 0 && list.unknown.length === 0 && (
              <p className="why-none">Every watched check read OK.</p>
            )}
            {list.attention.length > 0 && (
              <>
                <h3>Failing and degraded ({list.attention.length})</h3>
                <ul className="why-list">{list.attention.map((r) => <Row key={`${r.module}.${r.check}`} r={r} />)}</ul>
              </>
            )}
            {list.unknown.length > 0 && (
              <>
                <h3 data-health="unknown">Unknown ({list.unknownCount})</h3>
                {list.unknown.map((g) => (
                  <div key={g.module} className="why-group" data-health="unknown">
                    <h4>{g.module} <small>{g.rows.length}</small></h4>
                    <ul className="why-list">{g.rows.map((r) => <Row key={`${r.module}.${r.check}`} r={r} />)}</ul>
                  </div>
                ))}
              </>
            )}
            {list.setAside > 0 && (
              <p className="why-aside">{list.setAside} check(s) not set up or paused are left out of the count.</p>
            )}
          </>
        )}
      </section>
    </div>
  );
}
