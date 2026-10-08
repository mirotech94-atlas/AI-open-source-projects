import type { JSX } from "react";
import { normaliseHealth, type Health } from "../api";

const WORD: Record<Health, string> = {
  ok: "OK",
  degraded: "Degraded",
  failing: "Failing",
  unknown: "Unknown",
};

/**
 * A radial gauge per business.
 *
 * The arc is the share of this module's checks that returned a real
 * measurement and were healthy. The colour is its worst state. Both matter:
 * a module can be "not failing" purely because nothing could be measured, and
 * a full green ring on an unmeasured module would be a lie.
 *
 * UNKNOWN draws a dashed, unlit ring at zero — there is no arc to fill,
 * because nothing was measured.
 */
export function Gauge({
  name,
  health,
  ok,
  total,
}: {
  name: string;
  health: string;
  ok: number;
  total: number;
}): JSX.Element {
  const state = normaliseHealth(health);
  const size = 66;
  const stroke = 4;
  const r = (size - stroke) / 2;
  const circumference = 2 * Math.PI * r;
  const fraction = total > 0 ? Math.max(0, Math.min(1, ok / total)) : 0;
  const unlit = state === "unknown";

  return (
    <div className="gauge" data-health={state}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img"
           aria-label={`${name}: ${WORD[state]}, ${ok} of ${total} checks healthy`}>
        <circle
          cx={size / 2} cy={size / 2} r={r}
          fill="none" stroke="var(--line)" strokeWidth={stroke}
          strokeDasharray={unlit ? "3 5" : undefined}
        />
        {!unlit && fraction > 0 && (
          <circle
            cx={size / 2} cy={size / 2} r={r}
            fill="none" stroke="var(--state)" strokeWidth={stroke}
            strokeDasharray={`${circumference * fraction} ${circumference}`}
            strokeLinecap="butt"
            transform={`rotate(-90 ${size / 2} ${size / 2})`}
            style={{ filter: "drop-shadow(0 0 5px var(--state-glow))" }}
          />
        )}
        <text
          x="50%" y="50%" dominantBaseline="central" textAnchor="middle"
          fill="var(--state)" fontSize="15" fontWeight="700"
          fontFamily="var(--mono)"
        >
          {total > 0 ? `${ok}/${total}` : "—"}
        </text>
      </svg>
      <div className="name" title={name}>{name}</div>
      {/* The word is always present. Colour never carries the meaning alone. */}
      <div className="word">{WORD[state]}</div>
      <div className="sub">
        {total === 0 ? "no checks" : `${total} check${total === 1 ? "" : "s"}`}
      </div>
    </div>
  );
}
