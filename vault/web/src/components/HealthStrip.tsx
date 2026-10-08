import type { HTMLAttributes } from "react";
import type { HealthStrip as HealthStripRow } from "../types";
import { stateTone } from "../lib/format";

export type HealthStripProps = {
  rows: HealthStripRow[];
  className?: string;
} & Omit<HTMLAttributes<HTMLDivElement>, "children">;

/** One block's state, tallied for the row's single summarising label. */
function summarize(blocks: HealthStripRow["blocks"]): string {
  const counts = new Map<string, number>();
  for (const block of blocks) {
    counts.set(block.state, (counts.get(block.state) ?? 0) + 1);
  }
  return Array.from(counts.entries())
    .map(([state, n]) => `${n} ${state}`)
    .join(", ");
}

/** One row per site/host, 24 hour-blocks each. The row itself is the one tab
 *  stop and carries one summarising `aria-label` ("status.example.org, last 24
 *  hours: 22 ok, 2 warn") — a screen reader gets the whole row in one
 *  announcement rather than 24 individually-focusable stops. The blocks are
 *  `aria-hidden`; sighted/mouse users still get the per-hour detail through
 *  `title` on hover. */
export function HealthStrip({ rows, className = "", ...rest }: HealthStripProps) {
  if (rows.length === 0) {
    return <p className="g-health-empty">No sites or hosts are being watched yet.</p>;
  }
  return (
    <div className={`g-health-strip ${className}`.trim()} {...rest}>
      {rows.map((row) => (
        <div key={`${row.kind}:${row.name}`} className="g-health-row">
          <span className="g-health-name">{row.name}</span>
          <div
            className="g-health-blocks"
            role="group"
            tabIndex={0}
            aria-label={`${row.name}, last 24 hours: ${summarize(row.blocks)}`}
          >
            {row.blocks.map((block) => {
              const readout = `${block.hour}:00 — ${block.state}`;
              return (
                <span
                  key={block.hour}
                  data-testid="hour-block"
                  className={`g-health-block g-tone-${stateTone(block.state)}`}
                  style={{ background: "var(--tone)" }}
                  title={readout}
                  aria-hidden="true"
                />
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

export default HealthStrip;
