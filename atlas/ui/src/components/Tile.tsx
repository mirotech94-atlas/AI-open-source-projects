import type { CSSProperties, JSX } from "react";
import { Link } from "react-router-dom";

/**
 * One row of the AI CORE OVERVIEW column: a glyph, a name, and the one thing
 * worth knowing about it right now.
 *
 * `state` is the whole point. "unknown" borrows the same vocabulary UNKNOWN
 * carries everywhere else in this interface — grey, unlit, dashed, with the
 * word visible — because a subsystem that could not be measured must never
 * share a treatment with one that was measured and is fine.
 *
 * `readAt` is not optional by accident. Every figure on this screen carries
 * the time it was read; a tile with a number and no time is the bug this
 * whole system is built to avoid.
 */
export type TileState = "live" | "warn" | "unknown";

export function Tile({
  glyph,
  name,
  value,
  state,
  readAt,
  to,
  note,
  tileId,
  style,
}: {
  glyph: string;
  name: string;
  value: string;
  state: TileState;
  readAt: Date | null;
  to?: string;
  note?: string;
  /** Opts this tile into `useDraggableTiles`' fist-grab dragging -- the id a
   *  page's positions are keyed by. Omitted, the tile is fixed, same as
   *  before this existed. */
  tileId?: string | undefined;
  /** `useDraggableTiles`' `styleFor(tileId)` -- a `translate()`, or
   *  `undefined` at the default position. */
  style?: CSSProperties | undefined;
}): JSX.Element {
  const stamp = readAt ? `read ${readAt.toLocaleTimeString([], { hour12: false })}` : "not read yet";
  const body = (
    <>
      <span className="g" aria-hidden="true">{glyph}</span>
      <span className="m">
        <span className="n">{name}</span>
        <span className="v">{value}</span>
      </span>
      <span className="s">{stamp}</span>
    </>
  );
  const title = note ? `${note} · ${stamp}` : stamp;

  return to ? (
    <Link className="mc-tile" data-state={state} data-tile={tileId} to={to}
          title={title} style={style}>{body}</Link>
  ) : (
    <div className="mc-tile" data-state={state} data-tile={tileId} title={title}
         style={style}>{body}</div>
  );
}
