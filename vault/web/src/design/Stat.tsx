import type { HTMLAttributes, ReactNode } from "react";
import type { Tone } from "../lib/format";

export type StatProps = {
  label: ReactNode;
  value: ReactNode;
  unit?: ReactNode;
  sub?: ReactNode;
  tone?: Tone;
  /** Renders the value in IBM Plex Mono with tabular numerals, so a column of
   *  stats does not jitter as the numbers change. */
  mono?: boolean;
  className?: string;
} & Omit<HTMLAttributes<HTMLDivElement>, "children">;

export function Stat({
  label,
  value,
  unit,
  sub,
  tone = "plain",
  mono = false,
  className = "",
  ...rest
}: StatProps) {
  return (
    <div className={`g-stat g-tone-${tone} ${className}`.trim()} {...rest}>
      <span className="g-stat-label">{label}</span>
      <span className="g-stat-value-row">
        <span className={mono ? "g-stat-value g-stat-mono" : "g-stat-value"}>{value}</span>
        {unit !== undefined && <span className="g-stat-unit">{unit}</span>}
      </span>
      {sub !== undefined && <span className="g-stat-sub">{sub}</span>}
    </div>
  );
}

export default Stat;
