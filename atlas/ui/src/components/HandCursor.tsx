import type { JSX } from "react";
import type { HandState } from "../hooks/useHand";

/**
 * The on-screen pointer a raised palm drives. It only appears once cursor
 * mode is actually engaged -- never while the camera is merely `watching` --
 * so its presence on screen means a hand is genuinely steering something
 * right now, not that a camera happens to be open.
 */
export function HandCursor({
  state, cursor,
}: {
  state: HandState;
  cursor: { x: number; y: number } | null;
}): JSX.Element | null {
  if ((state !== "cursor" && state !== "grab") || !cursor) return null;
  return (
    <div
      className="hand-cursor"
      data-hand={state}
      style={{
        left: `${cursor.x * 100}%`,
        top: `${cursor.y * 100}%`,
      }}
      aria-hidden="true"
    />
  );
}
