import { useId } from "react";

export type RouteMatrixProps = {
  routes: Record<string, boolean>;
  onChange: (severity: string, checked: boolean) => void;
  disabled?: boolean;
  disabledTitle?: string;
};

// Fixed order and fixed set — the hub's `routes` object on an `Integration`
// row is always exactly these three keys (see `schemas_dash.py` /
// `IntegrationCard.routes`), so this never derives the list from
// `Object.keys(routes)` (which would drop a key the card omits, or render
// one in the wrong order if the hub ever changed its dict literal order).
const SEVERITIES = ["CRIT", "WARN", "INFO"] as const;

/** Three severity toggles (CRIT / WARN / INFO) deciding which alerts this
 *  integration delivers. Each is a real checkbox whose accessible name is
 *  the bare severity word — the label text is the only content, so a
 *  screen reader (and `getByRole("checkbox", { name: "INFO" })` in tests)
 *  sees exactly that. */
export function RouteMatrix({ routes, onChange, disabled = false, disabledTitle }: RouteMatrixProps) {
  const legendId = useId();

  return (
    <fieldset className="g-integrations-routes" aria-describedby={legendId}>
      <legend id={legendId}>Delivers</legend>
      {SEVERITIES.map((sev) => (
        <label key={sev} className={`g-integrations-route g-integrations-route-${sev.toLowerCase()}`}>
          <input
            type="checkbox"
            checked={routes[sev] ?? false}
            disabled={disabled}
            title={disabled ? disabledTitle : undefined}
            onChange={(e) => onChange(sev, e.target.checked)}
          />
          {sev}
        </label>
      ))}
    </fieldset>
  );
}

export default RouteMatrix;
