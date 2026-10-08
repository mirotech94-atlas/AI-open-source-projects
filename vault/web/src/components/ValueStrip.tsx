import type { ValueStrip as Data } from "../types";
import "../styles/value.css";

/**
 * What Vault did for you, over a window.
 *
 * Every stat carries its note, and the notes are load-bearing rather than
 * decorative: "Median repair time" is how long a repair took once it
 * started, NOT how long a problem lasted — Vault records no such thing —
 * and there is deliberately no minutes-saved figure anywhere, because
 * nothing here can see the owner's clock.
 */
export function ValueStrip({ data }: { data: Data }) {
  const total = data.work.reduce((n, w) => n + w.count, 0);

  return (
    <div className="g-value" data-testid="value-strip">
      <ul className="g-value-stats">
        {data.stats.map((s) => (
          <li key={s.key}>
            <span className="g-value-n">{s.value}</span>
            <span className="g-value-label">{s.label}</span>
            <span className="g-value-note">{s.note}</span>
          </li>
        ))}
      </ul>

      {total > 0 && (
        <div className="g-value-work">
          <h4>Where the work went</h4>
          <ul>
            {data.work.map((w) => (
              <li key={w.label}>
                <span className="g-value-work-label">{w.label}</span>
                <span className="g-value-work-bar" aria-hidden="true">
                  <span style={{ width: `${Math.max(2, Math.round((w.count / total) * 100))}%` }} />
                </span>
                <span className="g-value-work-n">{w.count}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export default ValueStrip;
