import { Link } from "react-router-dom";
import { Badge } from "../design";
import { ago, sevTone } from "../lib/format";
import type { IncidentBrief, IncidentCounts } from "../types";

export type IncidentPanelProps = {
  briefs: IncidentBrief[];
  counts: IncidentCounts;
  /** How many assets Vault is watching, named in the empty state so "no
   *  incidents" reads as coverage rather than as silence. */
  assets: number;
  className?: string;
};

/** Open incidents — correlated stories, not raw alerts. Says plainly when
 *  there are none, and how much is being watched. Pure. */
export function IncidentPanel({ briefs, counts, assets, className = "" }: IncidentPanelProps) {
  if (briefs.length === 0) {
    return (
      <p className={`hint ${className}`.trim()}>
        No open incidents. Vault is watching {assets} {assets === 1 ? "asset" : "assets"}.
      </p>
    );
  }
  return (
    <ul className={`g-incident-list ${className}`.trim()}>
      {briefs.map((b) => (
        <li key={b.id} className={`g-incident g-tone-${sevTone(b.sev)}`}>
          <Link to={b.href}>
            <span className="g-incident-title">
              <Badge tone={sevTone(b.sev)}>{b.sev}</Badge> {b.title}
            </span>
            <span className="hint">
              {b.entity} · {b.alerts} {b.alerts === 1 ? "alert" : "alerts"} · since {ago(b.opened)}
            </span>
          </Link>
        </li>
      ))}
      {counts.open > briefs.length && (
        <li className="hint">
          <Link to="/inbox?filter=incidents">{counts.open - briefs.length} more in the Inbox</Link>
        </li>
      )}
    </ul>
  );
}

export default IncidentPanel;
