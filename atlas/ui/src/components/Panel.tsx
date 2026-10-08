import type { CSSProperties, JSX, ReactNode } from "react";

/**
 * A bracketed panel. The chrome of this interface.
 *
 * `unavailable` is the important prop: a panel that could not be filled says
 * so, unlit, rather than rendering as an empty-but-fine box. In a glowing HUD
 * an empty panel reads as "nothing to report", which is a different claim from
 * "I could not look".
 */
export function Panel({
  title,
  right,
  unavailable,
  children,
  className = "",
  tileId,
  style,
}: {
  title: string;
  right?: ReactNode;
  unavailable?: string | null;
  children: ReactNode;
  className?: string;
  /** Opts this whole panel into `useDraggableTiles`' fist-grab dragging --
   *  the id a page's positions are keyed by. Omitted, the panel is fixed,
   *  same as before this existed. */
  tileId?: string | undefined;
  /** `useDraggableTiles`' `styleFor(tileId)` -- a `translate()`, or
   *  `undefined` at the default position. */
  style?: CSSProperties | undefined;
}): JSX.Element {
  return (
    <section className={`jpanel ${className}`} data-unavailable={String(!!unavailable)}
             data-tile={tileId} style={style}>
      <header>
        <h2 className="label">{title}</h2>
        {right && <span className="right">{right}</span>}
      </header>
      <div className="jbody">
        {unavailable ? <p className="unavail">{unavailable}</p> : children}
      </div>
    </section>
  );
}

/** A labelled number with the unit kept small. Null renders as a dash, never 0. */
export function Stat({
  value,
  unit,
  label,
  tone = "cyan",
}: {
  value: number | string | null | undefined;
  unit?: string;
  label: string;
  tone?: "cyan" | "amber" | "red" | "grey";
}): JSX.Element {
  const missing = value === null || value === undefined;
  return (
    <div className="stat" data-tone={missing ? "grey" : tone}>
      <span className="v">
        {missing ? "—" : value}
        {!missing && unit && <span className="u">{unit}</span>}
      </span>
      <span className="l">{label}</span>
    </div>
  );
}
