import type { JSX } from "react";
import type { ActivityRow } from "../api";
import { normaliseHealth } from "../api";

function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString([], {
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
}

/**
 * The live activity rail: what ATLAS checked, what he found, what he did.
 *
 * Terse on purpose — this is read at a glance from across the room, not
 * studied. Rows that arrived since the last render fade in; nothing else moves.
 */
export function Feed({
  rows,
  freshBefore,
}: {
  rows: ActivityRow[];
  freshBefore: number;
}): JSX.Element {
  if (rows.length === 0) {
    return (
      <p className="empty" style={{ padding: "10px 12px", fontSize: 11.5 }}>
        Nothing yet. Activity appears as checks run.
      </p>
    );
  }
  return (
    <ul className="feedlist">
      {rows.map((row) => {
        const fresh = new Date(row.at).getTime() > freshBefore;
        return (
          <li
            key={`${row.kind}-${row.id ?? row.at}-${row.subject}`}
            className={fresh ? "fresh" : ""}
            data-health={row.health ? normaliseHealth(row.health) : undefined}
          >
            <span className="t">{clock(row.at)}</span>
            <span className="what">
              <span className="k" data-kind={row.kind}>{row.kind}</span>{" "}
              <span>{row.subject}</span>
              <div className="sub">{row.detail}</div>
            </span>
          </li>
        );
      })}
    </ul>
  );
}
