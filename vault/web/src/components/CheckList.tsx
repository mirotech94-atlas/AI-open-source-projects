import { ago } from "../lib/format";
import type { SecurityCheck } from "../types";

export type CheckListProps = {
  checks: SecurityCheck[];
  /** Opens the resolve-or-leave-it drawer for one failing check (owner
   *  request: clicking a failing check should open detail). Only a row with
   *  `passed === false` is clickable — `null`/`true` carry no drill-down
   *  today, and offering one on a fact Vault has not actually verified
   *  would be the same false confidence `_secondary_guard`'s `passed: null`
   *  rows already avoid. Omitted, every row renders exactly as before. */
  onOpen?: (check: SecurityCheck) => void;
};

function groupByArea(checks: SecurityCheck[]): Map<string, SecurityCheck[]> {
  const groups = new Map<string, SecurityCheck[]>();
  for (const check of checks) {
    const list = groups.get(check.area);
    if (list) list.push(check);
    else groups.set(check.area, [check]);
  }
  return groups;
}

function autofixChip(autofix: string): { label: string; tone: "good" | "plain" } {
  const norm = autofix.trim().toLowerCase();
  if (norm === "auto") return { label: "Auto", tone: "good" };
  if (norm === "manual") return { label: "Manual", tone: "plain" };
  return { label: "—", tone: "plain" };
}

/** One full-detail row: pass/fail glyph, title (+ "Left as-is" badge), the raw
 *  evidence line, when it was last verified, and the autofix chip. A check a
 *  person left alone keeps its glyph neutral rather than alarming red — it is
 *  a decision on record, not a failure nobody has looked at, and the drawer's
 *  own "Undo — reopen" is what makes that decision reversible. */
function FullRow({ check, onOpen }: { check: SecurityCheck; onOpen?: (check: SecurityCheck) => void }) {
  const chip = autofixChip(check.autofix);
  const left = check.decision?.status === "left";
  // A left-as-is check is still `passed: false` — it was never fixed, only
  // decided about — so its own tone stays out of alert red once it has an
  // owner's decision attached, reserving red for a failure nobody has
  // actually weighed in on yet.
  const tone = check.passed === true ? "good" : check.passed === false ? (left ? "plain" : "alert") : "plain";
  const openHandler = check.passed === false ? onOpen : undefined;
  const body = (
    <span className="g-sec-check-body">
      <span className="g-sec-check-title">
        {check.title}
        {left && <span className="g-sec-check-left-badge">Left as-is · reversible</span>}
      </span>
      <span className="g-sec-check-evidence mono">{check.evidence}</span>
      <span className="g-sec-check-meta">Verified {ago(check.last_verified)}</span>
    </span>
  );
  return (
    <li className="g-sec-check-row" data-testid="check-row">
      <span className={`g-sec-passfail g-tone-${tone}`} aria-hidden="true">
        {check.passed === true ? "✓" : check.passed === false ? (left ? "•" : "✗") : "?"}
      </span>
      {openHandler ? (
        <button type="button" className="g-sec-check-row-btn" onClick={() => openHandler(check)}>
          {body}
        </button>
      ) : (
        body
      )}
      <span className={`g-sec-autofix-chip g-tone-${chip.tone}`}>{chip.label}</span>
    </li>
  );
}

/** A passing check is proof, not content: one compact chip (glyph + title),
 *  no evidence line, no "verified" timestamp taking up the same vertical
 *  space as an open failure. */
function ProofChip({ check, onOpen }: { check: SecurityCheck; onOpen?: (c: SecurityCheck) => void }) {
  // Item 25: a passing check opens too. It used to be inert — which meant
  // that the moment a finding was actually FIXED it became unclickable, and
  // the proof that it was fixed became unreachable at exactly the point
  // somebody would want to see it. "Was this dealt with, or did it just
  // stop showing up?" is a question about a green row.
  // Without a handler it stays the plain line it always was, rather than a
  // button that looks live and does nothing.
  if (!onOpen) {
    return (
      <li className="g-sec-check-proof" title={`${check.title} — verified ${ago(check.last_verified)}`}>
        <span className="g-sec-passfail g-tone-good" aria-hidden="true">✓</span>
        {check.title}
      </li>
    );
  }
  return (
    <li className="g-sec-check-proof">
      <button
        type="button"
        onClick={() => onOpen(check)}
        title={`${check.title} — verified ${ago(check.last_verified)}. Open to see what was done about it.`}
      >
        <span className="g-sec-passfail g-tone-good" aria-hidden="true">
          ✓
        </span>
        {check.title}
      </button>
    </li>
  );
}

/** Every check as proof, not just a verdict — grouped by `area` so "Hosts"
 *  and "Wi-Fi" checks don't blur together, and inside each area a failure
 *  comes first: what needs a look is the content of this list, and a pass is
 *  a compact line of proof underneath it rather than another row the same
 *  size as the thing that actually needs attention. */
export function CheckList({ checks, onOpen }: CheckListProps) {
  if (checks.length === 0) {
    return <p className="g-asset-empty">No checks have run yet.</p>;
  }

  const groups = groupByArea(checks);

  return (
    <div className="g-sec-checks">
      {[...groups.entries()].map(([area, rows]) => {
        const failing = rows.filter((c) => c.passed === false);
        const unverified = rows.filter((c) => c.passed === null);
        const passing = rows.filter((c) => c.passed === true);
        return (
          <section key={area} className="g-sec-check-group">
            <h3 className="g-sec-check-area">{area}</h3>
            {(failing.length > 0 || unverified.length > 0) && (
              <ul className="g-sec-check-rows">
                {failing.map((check) => (
                  <FullRow key={check.key} check={check} onOpen={onOpen} />
                ))}
                {unverified.map((check) => (
                  <FullRow key={check.key} check={check} onOpen={onOpen} />
                ))}
              </ul>
            )}
            {passing.length > 0 && (
              <ul className="g-sec-check-proofs" aria-label={`${area} — passing checks`}>
                {passing.map((check) => (
                  <ProofChip key={check.key} check={check} onOpen={onOpen} />
                ))}
              </ul>
            )}
          </section>
        );
      })}
    </div>
  );
}

export default CheckList;
