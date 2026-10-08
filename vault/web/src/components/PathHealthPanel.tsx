import { ms, pct, stateTone } from "../lib/format";
import type { PathHealth } from "../types";

export type PathHealthPanelProps = {
  paths: PathHealth[];
  className?: string;
};

/** The internet line: one row per anchor, a bar as wide as the latency, loss
 *  and jitter under it, the latency on the right. Pure — the good/warn/bad
 *  band is decided hub-side. */
export function PathHealthPanel({ paths, className = "" }: PathHealthPanelProps) {
  if (paths.length === 0) {
    return <p className={`hint ${className}`.trim()}>No path measured yet — the sensor pings each anchor every scan.</p>;
  }
  return (
    <ul className={`g-paths ${className}`.trim()}>
      {paths.map((p) => {
        // 200 ms is full width, matching the reference's own scale; an
        // unmeasured anchor draws the full bar in its bad tone rather than an
        // empty one that would read as "fast".
        const width = Math.min(100, ((p.avg_ms ?? 200) / 200) * 100);
        return (
          <li key={p.name} className={`g-path g-tone-${stateTone(p.state === "bad" ? "crit" : p.state)}`}>
            <span className="g-path-name">
              {/* The name is ours ("Cloudflare", "Gateway"); the address is
                  the machine's, and is read digit by digit when somebody is
                  checking which resolver they are actually on. */}
              {p.name} <span className="hint mono">{p.ip}</span>
            </span>
            <span className="g-path-bar">
              <i style={{ width: `${width}%` }} />
            </span>
            <span className="g-path-value">{ms(p.avg_ms)}</span>
            <span className="g-path-detail hint">
              loss {pct(p.loss_pct)} · jitter {ms(p.jitter_ms)}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

export default PathHealthPanel;
