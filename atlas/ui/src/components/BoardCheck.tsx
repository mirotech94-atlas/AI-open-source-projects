import type { JSX } from "react";
import type { CheckSummary } from "../api";
import { checkLook } from "../pages/boardModel";

/** One check on the board: its word always visible, its treatment its own. */
export function BoardCheck({ check }: { check: CheckSummary }): JSX.Element {
  const look = checkLook(check);
  return (
    <li className="bd-check" data-look={look.kind} data-treatment={look.treatment}>
      <span className="bd-word">{look.word}</span>
      <span className="bd-name">{check.name}</span>
      {look.note && <span className="bd-note">{look.note}</span>}
    </li>
  );
}
