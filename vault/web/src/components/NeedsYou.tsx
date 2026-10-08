import { Badge } from "../design";
import type { FixedWithoutYou, NeedsYouItem } from "../types";
import "../styles/needsyou.css";

const SEV: Record<string, "alert" | "warn" | "plain"> = {
  CRIT: "alert", WARN: "warn", INFO: "plain",
};

/**
 * The first thing on the page, and the only two-button queue in the product.
 *
 * Every row here is a repair Vault CAN run and is choosing not to run
 * unasked (`AlertRemedy.kind === "approval"`). A repair it already made is
 * not a decision, and an errand it cannot perform is not one either — both
 * would make Approve a button that lies about what it does.
 *
 * When the queue is empty the component does not disappear and it does not
 * say "you are protected". It reports what Vault handled alone, which is
 * a claim that can be checked, unlike safety.
 */

/** Fourteen days as a grid of day cells, not a stretched bar chart.
 *
 *  Bars sized by `flex: 1` grew to whatever width the container had, which
 *  on a full-width panel turned a fortnight into a row of squat blocks with
 *  no shape to read. Fixed cells keep the same footprint wherever the panel
 *  sits, and the split stays legible: the cell is tinted by the day's total
 *  and carries a bar along the bottom for the share you approved.
 */
function FixGrid({ fixed }: { fixed: FixedWithoutYou }) {
  const peak = Math.max(1, ...fixed.daily.map((d) => d.automatic + d.approved));
  return (
    <div className="g-fixgrid">
      <div className="g-fixgrid-head">
        <h4>Fixed without you</h4>
        <span>Last {fixed.days} days</span>
      </div>
      <ol className="g-fixgrid-cells">
        {fixed.daily.map((d) => {
          const total = d.automatic + d.approved;
          return (
            <li
              key={d.day}
              className="g-fixcell"
              data-empty={total === 0 ? "1" : undefined}
              title={`${d.day}: ${d.automatic} automatic, ${d.approved} approved`}
            >
              {/* Opacity, not hue: one colour with a readable range beats
                  five colours nobody has a key for. */}
              <span className="g-fixcell-fill" style={{ opacity: total === 0 ? 0 : 0.28 + 0.72 * (total / peak) }} />
              {d.approved > 0 && (
                <span className="g-fixcell-approved" style={{ height: `${(d.approved / total) * 100}%` }} />
              )}
              <span className="g-fixcell-n">{total > 0 ? total : ""}</span>
            </li>
          );
        })}
      </ol>
      <p className="g-fixgrid-key">
        <span className="g-fixkey g-fixkey-auto" /> Automatic {fixed.automatic.toLocaleString()}
        {fixed.approved > 0 && (
          <>
            {"  "}
            <span className="g-fixkey g-fixkey-approved" /> You approved {fixed.approved.toLocaleString()}
          </>
        )}
      </p>
    </div>
  );
}

export function NeedsYou({
  items, fixed, onApprove, onDismiss, pendingKey = null,
}: {
  items: NeedsYouItem[];
  fixed: FixedWithoutYou;
  onApprove: (item: NeedsYouItem) => void;
  onDismiss: (item: NeedsYouItem) => void;
  /** The row whose first press came back needing a tier-3 confirmation. Its
   *  button becomes an explicit second yes rather than repeating "Approve",
   *  so nobody confirms by reflex. */
  pendingKey?: string | null;
}) {
  const handled = fixed.automatic + fixed.approved;

  if (items.length === 0) {
    return (
      <div className="g-needsyou g-needsyou-clear" data-testid="needs-you">
        <p className="g-needsyou-clearline">
          <strong>Nothing needs you.</strong>{" "}
          {handled > 0
            ? `Vault handled ${fixed.automatic.toLocaleString()} thing${fixed.automatic === 1 ? "" : "s"} on its own in the last ${fixed.days} days` +
              (fixed.approved > 0 ? `, and ${fixed.approved.toLocaleString()} with your approval.` : ".")
            : `Nothing has needed fixing in the last ${fixed.days} days.`}
        </p>
        {fixed.daily.length > 0 && handled > 0 && <FixGrid fixed={fixed} />}
      </div>
    );
  }

  return (
    <div className="g-needsyou" data-testid="needs-you">
      <header className="g-needsyou-head">
        <h2>Needs you</h2>
        <Badge tone="warn">
          {items.length} waiting
        </Badge>
      </header>

      <ul className="g-needsyou-list">
        {items.map((it) => (
          <li key={it.key} className="g-needsyou-row" data-sev={it.severity}>
            <div className="g-needsyou-what">
              <span className="g-needsyou-title">
                <Badge tone={SEV[it.severity] ?? "plain"}>{it.severity}</Badge>
                {it.title}
              </span>
              {it.detail && <span className="g-needsyou-detail">{it.detail}</span>}
              {/* Never hidden behind a disclosure: what it costs to say yes is
                  the half of the decision people get wrong. */}
              {it.cost && <span className="g-needsyou-cost">{it.cost}</span>}
            </div>
            <div className="g-needsyou-act">
              <button type="button" className="g-btn-quiet" onClick={() => onDismiss(it)}>
                Not now
              </button>
              <button
                type="button"
                className="g-btn-primary"
                data-confirming={pendingKey === it.key ? "1" : undefined}
                onClick={() => onApprove(it)}
              >
                {pendingKey === it.key ? "Yes, do it" : "Approve"}
              </button>
            </div>
          </li>
        ))}
      </ul>

      {handled > 0 && fixed.daily.length > 0 && <FixGrid fixed={fixed} />}
    </div>
  );
}

export default NeedsYou;
