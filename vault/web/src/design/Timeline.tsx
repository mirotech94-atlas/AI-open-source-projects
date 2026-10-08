import type { HTMLAttributes, ReactNode } from "react";
import type { TimelineItem } from "../types";
import { machineClass } from "../lib/machine";
import { ago, sevTone } from "../lib/format";

export type TimelineProps = {
  items: TimelineItem[];
  /** Called on click/Enter. A row with an `href` and no handler still shows the
   *  link, so "open the device" works either way. */
  onSelect?: (item: TimelineItem) => void;
  empty?: ReactNode;
  className?: string;
} & Omit<HTMLAttributes<HTMLUListElement>, "children">;

/** The "what changed" list: severity stripe, title, relative time, optional
 *  link. Items arrive already ordered by the hub — nothing is re-sorted here. */
export function Timeline({
  items,
  onSelect,
  empty = "Nothing has changed yet.",
  className = "",
  ...rest
}: TimelineProps) {
  if (items.length === 0) {
    return <p className="g-timeline-empty">{empty}</p>;
  }
  return (
    <ul className={`g-timeline ${className}`.trim()} {...rest}>
      {items.map((item, i) => {
        const tone = sevTone(item.sev);
        const body = (
          <>
            <span className={`g-timeline-stripe g-tone-${tone}`} aria-hidden="true" />
            <span className="g-timeline-body">
              <span className="g-timeline-title">{item.title}</span>
              {/* A timeline detail is usually a phrase, but for device events
                  it is the MAC that identifies the thing that moved. Decided
                  per value rather than per timeline, since one list holds
                  both. */}
              {item.detail !== "" && (
                <span
                  className={`g-timeline-detail ${machineClass(item.detail)}`.trim()}
                  title={item.detail}
                >
                  {item.detail}
                </span>
              )}
            </span>
            <span className="g-timeline-meta">
              <span className="g-timeline-ago">{ago(item.ts)}</span>
              {/* The thing the event was about: a device name when it has
                  one, its MAC when it does not -- the same fallback the detail
                  above carries. Marking one and not the other is exactly how a
                  MAC survived here after the detail was fixed. */}
              {item.entity !== "" && (
                <span className={machineClass(item.entity)}>{item.entity}</span>
              )}
            </span>
          </>
        );
        // `ts + kind + entity` is stable for a given event; the index only
        // breaks a tie between two events of the same kind in the same second.
        const key = `${item.ts}|${item.kind}|${item.entity}|${i}`;
        return (
          <li key={key} className={`g-timeline-row g-tone-${tone}`}>
            {onSelect ? (
              <button
                type="button"
                className="g-timeline-item"
                onClick={() => onSelect(item)}
              >
                {body}
              </button>
            ) : item.href !== null ? (
              <a className="g-timeline-item" href={item.href}>
                {body}
              </a>
            ) : (
              <span className="g-timeline-item">{body}</span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

export default Timeline;
