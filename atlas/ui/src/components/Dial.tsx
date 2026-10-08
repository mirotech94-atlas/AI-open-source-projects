import type { JSX } from "react";

/**
 * A radial dial for a percentage.
 *
 * A null reading draws an unlit dashed ring and a dash — not a zero. Zero and
 * "not measured" are different facts, and a 0% CPU dial that actually means
 * "could not read /proc" is exactly the lie this interface must not tell.
 */
export function Dial({
  percent,
  label,
  detail,
}: {
  percent: number | null;
  label: string;
  detail?: string;
}): JSX.Element {
  const size = 76;
  const stroke = 5;
  const r = (size - stroke) / 2;
  const circumference = 2 * Math.PI * r;
  const known = percent !== null && Number.isFinite(percent);
  const fraction = known ? Math.max(0, Math.min(1, percent / 100)) : 0;
  const tone = !known ? "grey" : percent >= 90 ? "red" : percent >= 70 ? "amber" : "cyan";

  return (
    <div className="dial" data-tone={tone}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img"
           aria-label={`${label}: ${known ? `${percent}%` : "not measured"}`}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none"
                stroke="var(--line)" strokeWidth={stroke}
                strokeDasharray={known ? undefined : "3 5"} />
        {known && fraction > 0 && (
          <circle cx={size / 2} cy={size / 2} r={r} fill="none"
                  stroke="var(--tone)" strokeWidth={stroke}
                  strokeDasharray={`${circumference * fraction} ${circumference}`}
                  transform={`rotate(-90 ${size / 2} ${size / 2})`}
                  style={{ filter: "drop-shadow(0 0 5px var(--tone-glow))" }} />
        )}
        <text x="50%" y="50%" dominantBaseline="central" textAnchor="middle"
              fill="var(--tone)" fontSize="15" fontWeight="700"
              fontFamily="var(--mono)">
          {known ? `${Math.round(percent)}%` : "—"}
        </text>
      </svg>
      <div className="dl">{label}</div>
      <div className="dd">{known ? (detail ?? "") : "not measured"}</div>
    </div>
  );
}
