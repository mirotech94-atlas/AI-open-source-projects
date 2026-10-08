import { Badge } from "../design";
import { EmptyState } from "./EmptyState";
import { NetworkBadge } from "./NetworkBadge";
import { ringTone } from "../lib/format";
import type { Deduction, NetworkHealth, Scorecard } from "../types";

export type ScorecardGridProps = {
  scorecards: Scorecard[];
  /** Fired when "Fix now" is pressed for a card. The caller owns the write
   *  (`requestFix(scorecard.target, scorecard.fix)`) and the queue poll —
   *  this component only renders the button and reports which card. */
  onFix: (scorecard: Scorecard) => void;
  /** Opens the resolve-or-leave-it drawer for one deduction (owner request:
   *  "same thing for security posture as well"). Only a deduction whose
   *  `key` names a real `SecurityCheck` opens one — `""` means this
   *  deduction scores the card but has no row of its own to drill into
   *  (see `Deduction.key`'s doc). Omitted, the grid renders exactly as
   *  before. */
  onOpenDeduction?: (deduction: Deduction, card: Scorecard) => void;
  /** Targets with a fix currently queued: their button shows "Fixing…" and
   *  disables rather than double-submitting. */
  pending?: Set<string>;
  /** Disables every "Fix now" button with a tooltip naming the role that can
   *  press it (viewers can look, never write). */
  canWrite?: boolean;
  writeTooltip?: string;
  /** The network a LAN/home scorecard belongs to, badged on those cards only.
   *  Scorecards are NOT network-scoped rows on the hub (task 51 §11.6) — they
   *  are derived from the scan's tenant-level site/host/home blocks — so this
   *  is the caller telling the grid which network the LAN card describes, and
   *  no other card kind gets a badge. */
  lanNetwork?: NetworkHealth | null;
};

/** Item 22: the hub calls a machine a "host" and a website a "site", and
 *  "Roots host" beside "Roots site" is the same word twice to a client who
 *  has a server and an app of that name. There is no such thing as a host
 *  AND a server here -- they are one thing, and the word is "server". */
const KIND_WORD: Record<string, string> = {
  host: "server",
  site: "website",
  lan: "network",
  home: "network",
};

/** One target's posture card: grade + score, its top 3 deductions, and a
 *  "Fix now" button when the hub attached a `fix` kind to this scorecard. */
export function ScorecardGrid({
  scorecards,
  onFix,
  onOpenDeduction,
  pending = new Set(),
  canWrite = true,
  writeTooltip = "operator role required",
  lanNetwork = null,
}: ScorecardGridProps) {
  if (scorecards.length === 0) {
    return (
      <EmptyState
        title="No targets scored yet."
        body="Add a website or a server to watch in Settings, and its posture card lands here after the next scan."
        action={{ label: "Add a target", href: "/settings/targets" }}
      />
    );
  }

  return (
    <div className="g-sec-scorecards">
      {scorecards.map((card) => {
        const tone = ringTone(card.score, 100);
        const isPending = pending.has(card.target);
        return (
          <article key={card.target} className={`g-sec-scorecard g-tone-${tone}`} data-testid="scorecard">
            <header className="g-sec-scorecard-head">
              <div>
                <p className="g-sec-scorecard-target">{card.target}</p>
                <p className="g-sec-scorecard-kind">
                  {KIND_WORD[card.kind] ?? card.kind}
                  {(card.kind === "lan" || card.kind === "home") && lanNetwork !== null && (
                    <NetworkBadge
                      network={{
                        id: lanNetwork.id,
                        slug: lanNetwork.slug,
                        name: lanNetwork.name,
                        colour: lanNetwork.colour,
                      }}
                    />
                  )}
                </p>
              </div>
              <div className="g-sec-scorecard-grade">
                <span className="g-sec-scorecard-letter">{card.grade}</span>
                <span className="g-sec-scorecard-score">{card.score}</span>
              </div>
            </header>

            {card.deductions.length > 0 && (
              <ul className="g-sec-deductions">
                {card.deductions.slice(0, 3).map((d) => {
                  const text = (
                    <span className="g-sec-deduction-text">
                      <strong>{d.category}</strong> — {d.why}
                    </span>
                  );
                  const openHandler = d.key ? onOpenDeduction : undefined;
                  // Every deduction is styled as the same fact a failing
                  // check row is (`.g-sec-check-row`'s own box), so a score
                  // and the check it came from read as one thing rather than
                  // two lists that happen to agree. A deduction with no
                  // matching check (`d.key` is "") is not force-clickable —
                  // faking a drill-down for a fact with no route behind it
                  // would be worse than admitting there is nothing to open —
                  // so it keeps the row look but swaps the pass/fail glyph
                  // for a small "computed" mark with its own explanation.
                  return (
                    <li key={`${d.category}-${d.why}`} className="g-sec-deduction-row">
                      <span
                        className="g-sec-passfail g-tone-alert"
                        aria-hidden="true"
                      >
                        {openHandler ? "✗" : "Σ"}
                      </span>
                      {openHandler ? (
                        <button
                          type="button"
                          className="g-sec-check-row-btn g-sec-deduction-btn"
                          onClick={() => openHandler(d, card)}
                        >
                          {text}
                        </button>
                      ) : (
                        <span
                          className="g-sec-deduction-static"
                          title="Scored from configuration, not a single check — there is no detail to open."
                        >
                          {text}
                        </span>
                      )}
                      <Badge tone="warn">−{d.points}</Badge>
                    </li>
                  );
                })}
              </ul>
            )}

            {card.fix !== "" && (
              <button
                type="button"
                disabled={!canWrite || isPending}
                title={canWrite ? undefined : writeTooltip}
                onClick={() => onFix(card)}
              >
                {isPending ? "Fixing…" : "Fix now"}
              </button>
            )}
          </article>
        );
      })}
    </div>
  );
}

export default ScorecardGrid;
