import type { JSX, ReactNode } from "react";
import { Link } from "react-router-dom";
import { hhmm } from "./homeModel";

/** "read 10:42", "read failed" or "not read yet": every panel carries one. */
export function stamp(at: Date | string | null, failed = false): string {
  if (failed) return "read failed";
  if (!at) return "not read yet";
  return `read ${hhmm(at)}`;
}

/** A framed panel: corner ticks, a small-caps label, the read time. */
export function CPanel({ id, title, at, more, children, className = "" }: {
  id: string;
  title: string;
  at?: string;
  more?: { to: string; label: string };
  children: ReactNode;
  className?: string;
}): JSX.Element {
  return (
    <section className={`cmd-panel cmd-${id} ${className}`} aria-labelledby={`cmd-${id}-h`}>
      <header>
        <h2 id={`cmd-${id}-h`}>{title}</h2>
        {at && <span className="cmd-at">{at}</span>}
        {more && <Link className="cmd-more" to={more.to}>{more.label}</Link>}
      </header>
      <div className="cmd-pbody">{children}</div>
    </section>
  );
}

/** Unknown, in words: grey, dashed, never the OK treatment. */
export function Unread({ children }: { children: ReactNode }): JSX.Element {
  return (
    <p className="cmd-unread" data-health="unknown">
      <span className="cmd-word-unknown">Unknown</span> <span>{children}</span>
    </p>
  );
}
