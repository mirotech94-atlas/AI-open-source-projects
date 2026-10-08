import { useState, type FormEvent, type JSX } from "react";
import { BRAIN_ANCHOR, brainLayout } from "./brain-layout";
import type { BrainRead, BrainRegion } from "../api";

/**
 * ATLAS's memory, drawn as regions.
 *
 * Nothing here is a model of a brain. Each cluster is a group of real memory
 * namespaces; its size is how many memories are stored in them, and its
 * brightness is how many times something has actually recalled from them in
 * the last hour — `memory_reads` rows, written by `memory.recall()` itself.
 * A region nobody has read is drawn dim and labelled "quiet", never dark and
 * unexplained, for the same reason UNKNOWN never borrows OK's treatment.
 *
 * `regions` and `recent` are `null` when there is no fresh read behind them —
 * still loading, or the read failed. That is drawn as UNKNOWN (grey, dashed,
 * the word visible), never as an empty brain or "nothing recalled": an empty
 * list is a measurement, and a failed read is not one.
 */

const W = 340;
const H = 250;
const PAD = 30;

/** Keeps a label inside the box when its cluster sits near an edge. */
function labelShift(x: number, halfWidth: number): number {
  return Math.min(W - halfWidth, Math.max(halfWidth, x)) - x;
}

function clockOf(iso: string | null): string {
  if (!iso) return "—";
  const when = new Date(iso);
  return Number.isNaN(when.getTime())
    ? "—"
    : when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

export function Brain({ regions, recent, error, onAsk, asking }: {
  /** `null`: not read, or unreadable. Never shown as zero. */
  regions: BrainRegion[] | null;
  recent: BrainRead[] | null;
  error: string | null;
  /** Sends the typed question down the same path a spoken one takes. */
  onAsk: (question: string) => void;
  asking: boolean;
}): JSX.Element {
  const [draft, setDraft] = useState("");
  // A failed refresh does not fall back to the previous read: a number from
  // earlier is not a number about now.
  const unread = error !== null || regions === null;
  const nodes = unread ? [] : brainLayout(regions, W - PAD * 2, H - PAD * 2);
  const anchor = {
    x: BRAIN_ANCHOR.x * (W - PAD * 2) + PAD,
    y: BRAIN_ANCHOR.y * (H - PAD * 2) + PAD,
  };

  const submit = (event: FormEvent): void => {
    event.preventDefault();
    const question = draft.trim();
    if (!question || asking) return;
    setDraft("");
    onAsk(question);
  };

  return (
    <div className="brain">
      {unread ? (
        <p className="brain-unknown" data-state="unknown">
          <b>UNKNOWN</b>{" "}
          {error
            ? `— the brain could not be read: ${error}`
            : "— loading…"}
        </p>
      ) : (
        <svg className="brain-svg" viewBox={`0 0 ${W} ${H}`} role="img"
             aria-label={`Memory regions: ${nodes.length}`}>
          <defs>
            <radialGradient id="brain-glow">
              <stop offset="0%" stopColor="var(--cyan)" stopOpacity="0.55" />
              <stop offset="100%" stopColor="var(--cyan)" stopOpacity="0" />
            </radialGradient>
          </defs>

          {/* Every region runs a line back to the bust's head, off to the
              left. One anchor, so the edges read as one nervous system. */}
          {nodes.map((n) => (
            <line key={`e-${n.region}`} className="brain-edge"
                  x1={anchor.x} y1={anchor.y} x2={n.x + PAD} y2={n.y + PAD}
                  strokeOpacity={0.10 + n.firing * 0.5} />
          ))}

          {nodes.map((n) => {
            const cx = n.x + PAD;
            const cy = n.y + PAD;
            // Quiet is a floor, not zero: a region must still be legible when
            // nothing has read it this hour.
            const lit = 0.22 + n.firing * 0.78;
            return (
              <g key={n.region} transform={`translate(${cx} ${cy})`}
                 className="brain-node" data-firing={n.firing > 0 ? "yes" : "no"}>
                <title>
                  {`${n.region} — ${n.namespaces.join(", ") || "no namespaces"}`
                    + ` · ${n.reads_1h} recalls in the last hour`}
                </title>
                <circle r={n.r * 1.9} fill="url(#brain-glow)" opacity={n.firing * 0.8} />
                <circle className="brain-core" r={n.r} fillOpacity={lit * 0.34}
                        strokeOpacity={0.3 + n.firing * 0.7} />
                <text className="brain-name" y={-n.r - 5}
                      x={labelShift(cx, 44)} textAnchor="middle">{n.region}</text>
                <text className="brain-meta" y={n.r + 11}
                      x={labelShift(cx, 52)} textAnchor="middle">{n.label}</text>
              </g>
            );
          })}
        </svg>
      )}

      <form className="brain-feed" onSubmit={submit}>
        <input
          className="brain-input"
          placeholder="Feed the brain…"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          aria-label="Ask ATLAS a question, typed"
          disabled={asking}
        />
        <button className="btn" type="submit" disabled={asking || !draft.trim()}>
          {asking ? "Thinking…" : "Ask"}
        </button>
      </form>
      {/* The same endpoint the microphone reaches. Typing is not a second
          path to the world: anything that would act still becomes a proposal
          he has to approve. */}
      <p className="stamp">
        Typed questions take the same route as spoken ones. Nothing here acts.
      </p>

      <div className="brain-log">
        <h3 className="label">Last recalls</h3>
        {unread || recent === null ? (
          <p className="brain-unknown" data-state="unknown">
            <b>UNKNOWN</b>{" "}
            {error ? "— the recall log could not be read" : "— loading…"}
          </p>
        ) : recent.length === 0 ? (
          <p className="empty">Nothing has been recalled yet.</p>
        ) : (
          <ul>
            {recent.slice(0, 7).map((row, i) => (
              <li key={`${row.at ?? i}-${row.namespace}-${i}`}>
                <span className="t">{clockOf(row.at)}</span>
                <span className="ns">{row.namespace}</span>
                <span className="h">{row.hits} hit{row.hits === 1 ? "" : "s"}</span>
                <span className="who">{row.agent ?? "unattributed"}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
