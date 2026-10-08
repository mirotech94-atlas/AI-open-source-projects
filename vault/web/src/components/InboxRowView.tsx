import type { HTMLAttributes } from "react";
import { Link } from "react-router-dom";
import { Badge } from "../design";
import { NetworkBadge } from "./NetworkBadge";
import { gate, roleForTier } from "../lib/auth";
import type { Role } from "../lib/auth";
import { ago, sevTone } from "../lib/format";
import type { AlertRemedy, ConfidenceFactor, InboxRow, RowAction } from "../types";

export type RecDecisionStatus = "idle" | "queued" | "done" | "failed";

export type InboxRowViewProps = {
  row: InboxRow;
  /** The viewer's role, for gating `row.actions` the same way `TopBar`'s
   *  header controls gate (render disabled, name the role needed) — see
   *  `payloads/inbox.py`'s `_alert_actions` for why the hub sends every
   *  action regardless of role rather than omitting the ones this caller
   *  cannot use. `null` while `me()` is still loading. */
  role: Role | null;
  isCursor: boolean;
  isSelected: boolean;
  isExpanded: boolean;
  pending: boolean;
  /** Only meaningful for a `view: "rec"` row — the state of its Yes/No
   *  decision and, once queued, the fix's queue status. `undefined` before
   *  any decision is made. */
  recStatus?: RecDecisionStatus;
  onToggleExpand: () => void;
  onToggleSelect: () => void;
  onRunAction: (action: RowAction) => void;
  onRecDecision: (accept: boolean) => void;
  /** Opens the chat hand-off for this row — rendered only when the row's
   *  remedy needs the owner (an approval, a role they lack, or something only
   *  a person can do). Omitted for a row that has no such remedy. */
  onAskGuardian?: () => void;
  /** 0 for a top-level row, 1 for an incident's child alert. */
  depth?: number;
  className?: string;
  // Same reason `InboxList.tsx`'s own `role` prop excludes it: `HTMLAttributes`
  // already has an ARIA "role", and without excluding it here this prop's
  // `Role | null` silently intersects down to a non-nullable type.
} & Omit<HTMLAttributes<HTMLLIElement>, "children" | "role">;

/** How sure Vault is that this finding is real, as the predecessor's
 *  "Sure" column showed it. The sensor scores every alert 0-100 (`CONF` in
 *  `sensor/guardian_sensor/alerts.py`, defaulting to 70) and an incident
 *  averages its members', so a low number is a hedge worth seeing next to a
 *  CRIT — it is the difference between "your gateway MAC changed" and "this
 *  domain looks algorithmically generated".
 *
 *  Deliberately not a third alarm colour: high confidence is affirmative,
 *  middling is neutral, and only a genuinely weak signal is toned warn. */
function confidenceTone(pct: number): "good" | "warn" | "plain" {
  if (pct >= 85) return "good";
  if (pct < 60) return "warn";
  return "plain";
}

/** The remedy this row actually renders, or `null`.
 *
 *  `kind: "none"` is what the hub sends for a row that is not an alert (a
 *  recommendation, an incident header with nothing to repair), so it is not a
 *  remedy at all and nothing about it may be drawn. One expression, because
 *  every reader of a remedy below has to apply the same guard and a reader
 *  that forgets it paints an empty offer. */
function remedyOf(row: InboxRow): AlertRemedy | null {
  return row.remedy && row.remedy.kind !== "none" ? row.remedy : null;
}

/** The confidence rubric's factors for this row, `[]` when there are none.
 *
 *  Derived through `remedyOf` rather than reaching into `row.remedy` directly:
 *  the render path below and `rowCanExpand` must agree exactly on what counts
 *  as "there is working to show", and a second spelling of it is free to drift
 *  by a `kind` guard or a missing `??`. The `??` is not decoration either — a
 *  payload without the field (an older hub, a hand-built fixture) would throw
 *  inside a hotkey handler rather than degrade to "nothing to expand". */
function factorsOf(row: InboxRow): ConfidenceFactor[] {
  return remedyOf(row)?.confidence_factors ?? [];
}

/** Whether this row has anything to reveal when it is expanded.
 *
 *  Exported because `Inbox.tsx`'s `Enter` hotkey has to ask the same question
 *  the row's own controls do. It used to ask `row.children.length > 0` inline,
 *  which was the only answer there was; now a plain alert with no children can
 *  also expand, to show the working behind its confidence score. Two copies of
 *  this predicate would mean the keyboard and the mouse disagreed about which
 *  rows open, and the one nobody tests is the one that drifts — the hazard
 *  CLAUDE.md names for `remedy`'s fix tables and for `payloads/history.py`. */
export function rowCanExpand(row: InboxRow): boolean {
  return row.children.length > 0 || factorsOf(row).length > 0;
}

/** The weight column, as a signed number a reader can add up themselves.
 *
 *  `+30` / `-25` / `0` — signed so the arithmetic of the score is visible
 *  (`score_of` is BASELINE plus every weight, deliberately addition rather
 *  than multiplication so a person can check it), but a zero is printed bare
 *  rather than as `+0`: it contributed nothing and a sign would imply it did. */
function signedWeight(weight: number): string {
  return weight > 0 ? `+${weight}` : `${weight}`;
}

/** One inbox row: severity stripe, title, entity chip, why/what-changed
 *  lines, actions rendered from `row.actions` (never hard-coded), a
 *  ticket-key badge when the hub set one, and — for an incident — an expand
 *  toggle over its `children`. */
export function InboxRowView({
  row,
  role,
  isCursor,
  isSelected,
  isExpanded,
  pending,
  recStatus,
  onToggleExpand,
  onToggleSelect,
  onRunAction,
  onRecDecision,
  onAskGuardian,
  depth = 0,
  className = "",
  ...rest
}: InboxRowViewProps) {
  const hasChildren = row.children.length > 0;
  const isRec = row.view === "rec";
  // What Vault can do about this row, as the hub resolved it for this
  // viewer's role. "auto" also carries a `fix` action in `row.actions`, so the
  // line below is the offer and the button is the act; "approval"/"human" have
  // no button of their own — they hand off to chat, which is where a
  // confirmation or an instruction belongs.
  const remedy = remedyOf(row);
  const needsOwner = remedy !== null && remedy.kind !== "auto";
  const tone = sevTone(row.sev);
  // The rubric's working (`hub/guardian_hub/confidence.py`). Shown only in the
  // expanded region, never on the collapsed row, and the reason is the badge
  // twelve lines down: `row.confidence` is the SENSOR's confidence that the
  // finding is real, printed as "High / Moderate / Limited evidence". This is
  // a different quantity that happens to share the word — how sure Vault is
  // that it should do the repair itself. Two unlabelled percentages on one row
  // would be read as one number disagreeing with itself, so the score appears
  // only where it can carry its own heading and its six factors.
  //
  // An empty list is a real answer (no scored remedy, or hub-side scoring fell
  // open — see `schemas_dash.AlertRemedy.confidence_factors`), and it renders
  // as nothing at all rather than an empty table, which would say Vault
  // considered nothing.
  const factors = factorsOf(row);
  const detailId = `g-inbox-score-${row.id}`;

  const classes = [
    "g-inbox-row",
    `g-tone-${tone}`,
    isCursor ? "g-inbox-row-cursor" : "",
    isSelected ? "g-inbox-row-selected" : "",
    depth > 0 ? "g-inbox-row-child" : "",
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <li className={classes} aria-current={isCursor ? "true" : undefined} {...rest}>
      <span className="g-inbox-stripe" style={{ background: "var(--tone)" }} aria-hidden="true" />

      {row.view !== "rec" && (
        <label className="g-inbox-select">
          <input
            type="checkbox"
            checked={isSelected}
            onChange={onToggleSelect}
            aria-label={`Select ${row.title}`}
          />
        </label>
      )}

      <div className="g-inbox-body">
        <div className="g-inbox-title-row">
          {hasChildren ? (
            <button type="button" className="g-inbox-title-button" onClick={onToggleExpand} aria-expanded={isExpanded}>
              {row.title}
            </button>
          ) : (
            <span className="g-inbox-title">{row.title}</span>
          )}
          {row.view === "incidents" && (
            <Link to={`/incidents/${row.id}`} className="g-inbox-open-incident">
              Open
            </Link>
          )}
          {row.entity !== "" && <span className="g-inbox-entity">{row.entity}</span>}
          <NetworkBadge network={row.network} />
          {row.ticket_key !== "" && <Badge tone="plain">{row.ticket_key}</Badge>}
          {row.count > 1 && <Badge tone="plain">×{row.count}</Badge>}
          {row.confidence !== null && (
            <Badge
              tone={confidenceTone(row.confidence)}
              title="Evidence strength is a rule-based assessment, not a probability"
              data-testid="inbox-confidence"
            >
              {row.confidence >= 80 ? "High" : row.confidence >= 60 ? "Moderate" : "Limited"} evidence
            </Badge>
          )}
        </div>
        {row.why !== "" && <p className="g-inbox-why">{row.why}</p>}
        {row.what_changed !== "" && <p className="g-inbox-changed">{row.what_changed}</p>}
        {row.drift !== null && (
          <div className="g-inbox-drift" data-testid="inbox-drift">
            <p>
              <strong>{row.drift.file}</strong> on {row.drift.host} changed {ago(row.drift.changed_at)}
              {row.drift.fingerprint !== "" && <span className="hint"> (fingerprint {row.drift.fingerprint})</span>}.
            </p>
            {/* Item 26: the file name alone is a log line. What a change to
                THIS file lets an attacker do is the warning. */}
            {row.drift.why !== "" && <p className="g-inbox-drift-why">{row.drift.why}</p>}
            <p className="hint">{row.drift.note}</p>
          </div>
        )}
        {remedy !== null && (
          <p className={`g-inbox-remedy${needsOwner ? " g-inbox-remedy-needs-you" : ""}`} data-testid="inbox-remedy">
            {remedy.summary}
            {remedy.need !== "" && <span className="g-inbox-remedy-need"> {remedy.need}</span>}
            {/* What it costs, on its own line and labelled.
                Vault is asking to change a running network, and this is the
                sentence the owner is actually weighing -- inlined after `need`
                it would read as more of the same explanation and be skimmed,
                which is the one outcome that makes printing it pointless.
                Absent rather than hedged when the action has no entry: a
                missing cost line must never read as "no cost". */}
            {remedy.impact !== "" && (
              <span className="g-inbox-remedy-cost" data-testid="inbox-remedy-cost">
                <span className="g-inbox-remedy-cost-label">What it costs</span>
                {remedy.impact}
              </span>
            )}
            {/* How sure Vault is, as the sentence rather than the number.
                Last in the block and below the cost, which its own comment
                above says an owner reads and then skips the rest of: this
                qualifies the offer, so it must not push the thing being
                weighed further down the row. Unlabelled, because it is
                Vault speaking in the same voice as `summary` — the label
                and the arithmetic live in the drawer, where there is room to
                say what kind of number it is. */}
            {remedy.confidence_why !== "" && (
              <span className="g-inbox-remedy-why" data-testid="inbox-confidence-why">
                {remedy.confidence_why}
              </span>
            )}
          </p>
        )}

        {/* The working behind that sentence, on demand. Its own control rather
            than the title button: the title only becomes a button for a row
            with children (an incident), and turning every alert's title into
            one would offer no clue what clicking it reveals. This says so, and
            `aria-expanded`/`aria-controls` tie it to the region it opens.

            It drives the SAME `isExpanded` the incident expansion uses, so an
            incident row that carries a score opens both at once — one expanded
            state per row, one region, whichever control was used. */}
        {/* ONE button per row, and only where the row has no other way to
            open. `expanded` is a single flag per row id, and both expanded
            regions read it: `InboxList` unfurls an incident's child alerts on
            it, this file unfurls the rubric on it. Two regions sharing one
            flag is fine -- expanding a row shows everything that row has.
            Two BUTTONS for it is not. An incidents-view header carries a
            remedy of its own (`payloads/inbox.py` sets `remedy=lead_remedy`),
            so it has children AND six factors, and it used to render both the
            title toggle and this one. A sighted reader sees two affordances
            that turn out to do the same thing; a screen-reader user hears two
            independent disclosures, each claiming through `aria-controls` to
            own a different region, and operating either one opens both -- so
            what is announced is not what happens.

            So the control appears only on a row whose title is NOT already a
            toggle. Where the title is one, the score keeps its heading but as
            plain text inside the region it heads, which is what it is. */}
        {factors.length > 0 && remedy !== null && !hasChildren && (
          <button
            type="button"
            className="g-inbox-score-head g-inbox-score-button"
            aria-expanded={isExpanded}
            aria-controls={detailId}
            onClick={onToggleExpand}
            data-testid="inbox-score-toggle"
          >
            <span className="g-inbox-score-label">How sure Vault is</span>
            {/* The number is part of the control once the control is open, so
                the heading and the score are one line -- the shape the owner
                approved. Rendered only when expanded, which is the whole
                point: collapsed, this row must carry the sentence and no
                second percentage beside the evidence badge. */}
            {isExpanded && (
              <span className="g-inbox-score-value" data-testid="inbox-score-value">
                {remedy.confidence} / 100
              </span>
            )}
          </button>
        )}
        {isExpanded && factors.length > 0 && remedy !== null && hasChildren && (
          <p className="g-inbox-score-head">
            <span className="g-inbox-score-label">How sure Vault is</span>
            <span className="g-inbox-score-value" data-testid="inbox-score-value">
              {remedy.confidence} / 100
            </span>
          </p>
        )}
        {isExpanded && factors.length > 0 && remedy !== null && (
          <div className="g-inbox-score" id={detailId} data-testid="inbox-score">
            {/* The spec's own requirement, and it is not decoration: the
                weights were chosen by a person and argued over, not fitted to
                data, and a bare 0-100 beside six factors looks exactly like
                something that was. Saying so here is the difference between a
                number an owner can push back on and one they are expected to
                defer to. */}
            <p className="g-inbox-score-note">
              A rubric with weights Vault&apos;s authors chose, not a prediction — the six
              answers below add up to it.
            </p>
            {/* A list, not a `<table>`: there is no header row to give a table
                its columns, and read aloud each item is already a sentence in
                the right order — the question, what the rows said, what it was
                worth. The grid that makes it read as a table is CSS only. */}
            <ul className="g-inbox-score-factors">
              {factors.map((f) => (
                <li key={f.name} className="g-inbox-score-factor" data-testid="inbox-score-factor">
                  <span className="g-inbox-score-question">{f.question}</span>
                  <span className="g-inbox-score-answer">{f.answer}</span>
                  {/* `is-zero` recedes and nothing else is emphasised by
                      colour. A factor that found nothing is informative --
                      that is why all six are here -- but it must not be the
                      loudest mark in the table, and a negative weight is a
                      reason for caution rather than an error, so it gets
                      weight and not the console's one alarm colour. */}
                  <span
                    className={`g-inbox-score-weight${f.weight === 0 ? " is-zero" : ""}`}
                    data-testid="inbox-score-weight"
                  >
                    {signedWeight(f.weight)}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="g-inbox-actions">
          {isRec ? (
            // A declined recommendation offers only "Bring this back" (the
            // hub sends no `yes`/`no` actions for one — see `_rec_actions`),
            // dispatched through the same generic `onRunAction` a non-rec
            // row's buttons use: it is a plain clear, nothing to poll.
            row.actions.some((a) => a.kind === "restore") ? (
              <button
                type="button"
                disabled={pending}
                onClick={() => onRunAction(row.actions.find((a) => a.kind === "restore")!)}
              >
                Bring this back
              </button>
            ) : recStatus === undefined || recStatus === "idle" ? (
              <>
                <button type="button" disabled={pending} onClick={() => onRecDecision(true)}>
                  Yes
                </button>
                <button type="button" disabled={pending} onClick={() => onRecDecision(false)}>
                  No
                </button>
              </>
            ) : (
              <Badge tone={recStatus === "failed" ? "alert" : recStatus === "done" ? "good" : "plain"}>
                {recStatus}
              </Badge>
            )
          ) : (
            <>
              {row.actions.map((action) => {
                // F6: the header's own controls (Scan now, speed test,
                // maintenance) render disabled with a title naming the role
                // that is missing, rather than vanish for a role that cannot
                // use them — these follow the same rule now, so a viewer
                // sees the verb and why it is greyed out instead of nothing.
                const roleGate = gate(role, roleForTier(action.tier), action.label);
                return (
                  <button
                    key={action.kind}
                    type="button"
                    disabled={pending || roleGate.disabled}
                    title={roleGate.title}
                    onClick={() => onRunAction(action)}
                  >
                    {action.label}
                  </button>
                );
              })}
              {needsOwner && onAskGuardian && (
                <button
                  type="button"
                  className="g-inbox-ask"
                  disabled={pending}
                  onClick={onAskGuardian}
                  data-testid="inbox-ask-guardian"
                >
                  Ask Vault
                </button>
              )}
            </>
          )}
        </div>
      </div>
    </li>
  );
}

export default InboxRowView;
