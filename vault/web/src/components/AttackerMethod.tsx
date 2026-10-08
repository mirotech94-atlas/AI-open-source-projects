import { useId } from "react";

export type AttackerMethodProps = {
  value: string;
  methods: string[];
  onChange: (next: string) => void;
  /** Only an admin may change the org-wide attacker-handling policy. */
  canWrite?: boolean;
  writeTooltip?: string;
};

/** The org-wide "what do we do about an attacker" policy select. Disabled
 *  (not hidden) for anyone below admin, with a tooltip naming the role that
 *  can change it — same pattern as every other write control in the app. */
export function AttackerMethod({
  value,
  methods,
  onChange,
  canWrite = true,
  writeTooltip = "admin role required",
}: AttackerMethodProps) {
  const id = useId();

  return (
    <div className="g-sec-attacker-method">
      <label htmlFor={id}>Attacker method</label>
      <select
        id={id}
        value={value}
        disabled={!canWrite}
        title={canWrite ? undefined : writeTooltip}
        onChange={(e) => onChange(e.target.value)}
      >
        {methods.map((m) => (
          <option key={m} value={m}>
            {m}
          </option>
        ))}
      </select>
      {!canWrite && <p className="hint">{writeTooltip}</p>}
    </div>
  );
}

export default AttackerMethod;
