import type { JSX } from "react";
import type { Judgment as JudgmentData } from "../api";

/* Reliability curve + the one line that matters (JUDGMENT.md §8).
   A model saying 80% must be right about 80% of the time; the diagonal is
   honesty, and the distance from it is what degrades silently as a network
   takes over. Drawn inline: five bins, no library. */

const W = 220, H = 160, PAD = 24;

function x(v: number): number { return PAD + v * (W - 2 * PAD); }
function y(v: number): number { return H - PAD - v * (H - 2 * PAD); }

export function Judgment({ data, error }: {
  data: JudgmentData | null; error: string | null;
}): JSX.Element {
  if (error) return <p className="errorbar">Could not read the learning layer: {error}</p>;
  if (!data) return <div className="panel"><p className="stamp">Reading judgment…</p></div>;

  const serving = data.serving.approve;
  const full = data.bins.filter((b) => b.n > 0);
  const total = data.log.total ?? 0;
  const learning = total < data.cold_start;

  return (
    <div className="panel" style={{ marginBottom: 12 }}>
      <div style={{ display: "flex", gap: 20, alignItems: "flex-start", flexWrap: "wrap" }}>
        <svg width={W} height={H} role="img" aria-label="Reliability curve">
          <line x1={x(0)} y1={y(0)} x2={x(1)} y2={y(1)}
                stroke="var(--line-soft)" strokeDasharray="3 3" />
          <line x1={x(0)} y1={y(0)} x2={x(1)} y2={y(0)} stroke="var(--line)" />
          <line x1={x(0)} y1={y(0)} x2={x(0)} y2={y(1)} stroke="var(--line)" />
          {full.length > 1 && (
            <polyline fill="none" stroke="var(--accent, currentColor)" strokeWidth={1.5}
                      points={full.map((b) => `${x(b.predicted)},${y(b.observed)}`).join(" ")} />
          )}
          {full.map((b) => (
            <circle key={b.lo} cx={x(b.predicted)} cy={y(b.observed)}
                    r={Math.min(9, 3 + Math.sqrt(b.n))}
                    fill="var(--accent, currentColor)" opacity={0.85}>
              <title>{`said ${Math.round(b.predicted * 100)}%, yes ${Math.round(b.observed * 100)}% · ${b.n} samples`}</title>
            </circle>
          ))}
          <text x={x(0.5)} y={H - 6} textAnchor="middle" fontSize={9}
                fill="var(--ink-soft, currentColor)">I said</text>
          <text x={8} y={y(0.5)} textAnchor="middle" fontSize={9}
                transform={`rotate(-90 8 ${y(0.5)})`}
                fill="var(--ink-soft, currentColor)">you said yes</text>
        </svg>

        <div style={{ flex: 1, minWidth: 220 }}>
          <h3 className="label" style={{ margin: "0 0 6px" }}>Judgment</h3>
          <p style={{ margin: "0 0 8px", fontSize: 14 }}>{data.line}</p>
          <p className="stamp" style={{ margin: 0 }}>
            {serving
              ? `Serving: ${serving.head} since ${new Date(serving.since).toLocaleDateString()} · Brier ${serving.brier} on ${serving.n_test} held-out`
              : learning
                ? `Abstaining — ${total} of ${data.cold_start} decisions logged. Asking about everything until then.`
                : "Abstaining — no model has won a tournament yet."}
            {" · "}
            {data.log.approved} yes · {data.log.denied} no · {data.log.expired} expired
            {data.log.abstained > 0 && ` · abstained ${data.log.abstained}×`}
          </p>
          {data.tournaments.length > 0 && (
            <details style={{ marginTop: 8 }}>
              <summary className="stamp" style={{ cursor: "pointer" }}>
                Tournament log ({data.tournaments.length})
              </summary>
              <pre className="pre" style={{ marginTop: 6 }}>
                {data.tournaments.map((t) => `[${t.at.slice(0, 10)}]  ${t.line}`).join("\n")}
              </pre>
            </details>
          )}
        </div>
      </div>
    </div>
  );
}
