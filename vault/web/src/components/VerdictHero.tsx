import type { HTMLAttributes } from "react";
import { Ring, Sparkline } from "../design";
import type { NextAction, OverviewPayload } from "../types";

export type VerdictHeroProps = {
  payload: OverviewPayload;
  /** Fired when the primary button is pressed. The caller POSTs
   *  `next_action.path`/`body` and toasts the result — this component only
   *  renders the button and hands back which action was chosen. */
  onAction: (action: NextAction) => void;
  /** True while the action's mutation is in flight, so the button cannot be
   *  double-clicked into two writes. */
  busy?: boolean;
  className?: string;
} & Omit<HTMLAttributes<HTMLDivElement>, "children">;

/**
 * The score hero: the ring, one verdict sentence, one primary next-action
 * button, the 7-day trend beside it with its start → end annotation, and the
 * named deductions that took the score off 100.
 *
 * Pure — every pixel comes from `payload`; the deduction list and the 7-day
 * window are both built hub-side.
 */
export function VerdictHero({
  payload,
  onAction,
  busy = false,
  className = "",
  ...rest
}: VerdictHeroProps) {
  const action = payload.next_action;
  const week = payload.score_trend_7d.points
    .map((p) => p.value)
    .filter((v): v is number => v !== null);
  const first = week.at(0);
  const last = week.at(-1);
  return (
    <div className={`g-verdict-hero ${className}`.trim()} {...rest}>
      <div className="g-verdict-top">
        {/* The donut, not the flat number: Vault Portal v2 leads this panel
            with a ring whose arc IS the score, and the deductions beside it are
            what took it off 100. `flat` was the stand-in before that. */}
        <Ring value={payload.score} max={100} grade={payload.grade} size={132} label="Security score" />
        <div className="g-verdict-body">
          <p className="g-verdict-text">{payload.verdict}</p>
          {/* Why the number moved. Absent -- not "no change" -- when no scan
              is old enough to compare against, so the line never implies a
              steadiness nobody measured. */}
          {payload.score_change !== null && (
            <p
              className={`g-score-change g-tone-${
                payload.score_change.delta > 0
                  ? "good"
                  : payload.score_change.delta < 0
                    ? "alert"
                    : "plain"
              }`}
              data-testid="score-change"
            >
              {payload.score_change.summary}
            </p>
          )}
          {action !== null && (
            <button type="button" disabled={busy} onClick={() => onAction(action)}>
              {action.label}
            </button>
          )}
          {week.length >= 2 && (
            <div className="g-score-trend">
              <Sparkline points={week} height={44} tone="good" />
              <p className="hint">
                7 days: {first} → {last}
              </p>
            </div>
          )}
          <p className="hint">
            {payload.potential !== null
              ? `Grade ${payload.grade || "—"}. Following the recommendations lifts the score to ${payload.potential}.`
              : "100 minus the named deductions below."}
          </p>
          {/* What the score could not see, and which rubric produced it.
              A host that stops answering drops out of three deduction
              categories, so silence makes the number go UP -- this line is
              the only thing on the hero that tells those two cases apart.
              Shown only when something is actually unmeasured: a fully
              covered estate does not need reassuring, and a permanent
              "100% coverage" badge trains people to stop reading it. */}
          {/* Withheld, not missing. The ring falls back to "no score yet",
              which is true for a tenant whose sensor has never reported and
              misleading for one where Vault looked and could not see
              enough — those are different states and the second is the one
              that matters. A coverage block present alongside a null score
              can only mean the second, so no threshold has to be duplicated
              here to tell them apart. */}
          {payload.score === null && payload.score_coverage !== null && (
            <p className="hint" data-testid="score-withheld">
              Not scored: too little of the network could be checked. A score computed from
              the part that answered would read higher than a healthy network's, because the
              checks the silent servers feed simply stop counting.
            </p>
          )}
          {payload.score_coverage !== null && payload.score_coverage.pct < 100 && (
            <p className="hint" data-testid="score-coverage">
              Measured {payload.score_coverage.measured} of{" "}
              {payload.score_coverage.applicable} servers ({payload.score_coverage.pct}%
              coverage). The rest did not answer, so their firewall, patch and CVE
              state is unknown rather than good.
            </p>
          )}
          {payload.score_model !== "" && (
            <p className="hint" data-testid="score-model">
              Scoring rubric {payload.score_model}.
            </p>
          )}
        </div>
      </div>
      <ul className="g-deductions">
        {payload.deductions.length === 0 && <li className="hint">No deductions — full marks.</li>}
        {payload.deductions.map((d) => (
          <li key={`${d.category}|${d.why}`}>
            <span className="g-deduction-what">
              {d.category} <span className="hint">— {d.why}</span>
            </span>
            <b className="g-deduction-points">−{d.points}</b>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default VerdictHero;
