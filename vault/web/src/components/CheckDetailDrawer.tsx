import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge, Drawer, useToast } from "../design";
import { checkDecision, checkDetail } from "../lib/api";
import type { CheckRef } from "../lib/api";
import { ago } from "../lib/format";
import type { CheckDetail } from "../types";

export type CheckDetailDrawerProps = {
  /** `null` closes the drawer. Identifies the check by the same three
   *  fields every check-detail/decision/handoff route takes. */
  checkRef: CheckRef | null;
  onClose: () => void;
  /** May run a repair Vault owns (`detail.remedy.action` non-empty) —
   *  gated the same as the Security page's other fix buttons (admin). */
  canResolveFix: boolean;
  resolveFixTooltip: string;
  /** May record "leave it" / "undo" — gated at operator, same bar as
   *  acking an alert. */
  canDecide: boolean;
  decideTooltip: string;
  /** `detail.remedy.action` is set: run the fix through the page's own
   *  propose-and-confirm flow (`requestFixOrConfirm`). */
  onResolveFix: (detail: CheckDetail, ref: CheckRef) => void;
  /** `detail.remedy.action` is empty: Vault cannot repair this itself —
   *  hand off to chat, exactly the way the inbox's `offerRemedy` already
   *  does for an alert only a person can fix. */
  onResolveChat: (detail: CheckDetail, ref: CheckRef) => void;
};

/** The Wi-Fi guard / checklist / posture drill-down (owner request:
 *  "when something is wrong... clicking on evil twin for example it should
 *  give me more information and I can say yes resolve or leave it -- same
 *  thing for security posture as well").
 *
 *  A drawer, not a second full-page view: the row stays in context (the
 *  Security tab underneath is still visible and still scrollable), one
 *  detail is open at a time, Escape closes it, and `Drawer` is already the
 *  app's own pattern for this (Inbox uses it for the keyboard-shortcut
 *  sheet; the alert hand-off opens a whole chat panel the same way).
 */
export function CheckDetailDrawer({
  checkRef,
  onClose,
  canResolveFix,
  resolveFixTooltip,
  canDecide,
  decideTooltip,
  onResolveFix,
  onResolveChat,
}: CheckDetailDrawerProps) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [reason, setReason] = useState("");
  const [expires, setExpires] = useState("");

  const detailQuery = useQuery({
    queryKey: ["check-detail", checkRef?.key, checkRef?.network, checkRef?.target],
    queryFn: () => checkDetail(checkRef as CheckRef),
    enabled: checkRef !== null,
  });

  const decisionMutation = useMutation({
    mutationFn: (op: "leave" | "undo") => checkDecision(op, checkRef as CheckRef, op === "leave" ? reason : "", op === "leave" && expires ? new Date(expires).toISOString() : ""),
    onSuccess: async (_out, op) => {
      toast.show(op === "leave" ? "Left as-is" : "Reopened", "plain");
      setReason("");
      await queryClient.invalidateQueries({ queryKey: ["check-detail"] });
      await queryClient.invalidateQueries({ queryKey: ["security"] });
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  const detail = detailQuery.data ?? null;
  const open = checkRef !== null;

  function resolve() {
    if (detail === null || checkRef === null) return;
    if (detail.remedy.action) onResolveFix(detail, checkRef);
    else onResolveChat(detail, checkRef);
  }

  const resolveDisabled = detail !== null && Boolean(detail.remedy.action) && !canResolveFix;
  const left = detail?.decision.status === "left";

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={detail?.title ?? "Check detail"}
      subtitle={detail ? `${detail.area} · ${detail.target}` : undefined}
    >
      {!open ? null : detailQuery.isLoading ? (
        <p className="g-asset-empty">Loading…</p>
      ) : detail === null ? (
        <p className="g-asset-empty">This check could not be loaded — it may no longer apply.</p>
      ) : (
        <div className="g-check-detail">
          <p className="g-check-detail-seen">{detail.what_we_saw}</p>

          <dl className="g-facts">
            <div className="g-facts-row">
              <dt>How sure</dt>
              <dd>
                <Badge tone={detail.confidence === "uncertain" ? "warn" : "good"}>
                  {detail.confidence === "uncertain" ? "Worth checking — not confirmed" : "Confirmed"}
                </Badge>
              </dd>
            </div>
            <div className="g-facts-row">
              <dt>Last verified</dt>
              <dd>{detail.last_verified ? ago(detail.last_verified) : "—"}</dd>
            </div>
            <div className="g-facts-row">
              <dt>Evidence</dt>
              <dd className="mono g-check-detail-evidence">{detail.evidence}</dd>
            </div>
          </dl>

          <p className="g-check-detail-why">{detail.why_it_matters}</p>

          {/* Item 25: "a red finding opens a popup that proves it was
              actually fixed, not merely found".

              A check going green proves nothing on its own — the thing may
              just have stopped being visible to the scan. This is the
              record: what Vault ran, and whether a LATER look at the
              target found the world actually changed. The two are never
              merged into one "fixed" tick, because a command that exits 0
              has reported success, not achieved it. */}
          {detail.repairs.length > 0 && (
            <section className="g-proof" data-testid="repair-proof">
              <h3>What Vault did about it</h3>
              <ol>
                {detail.repairs.map((r, i) => (
                  <li key={`${r.ran_at}:${i}`} data-confirmed={r.confirmed_at ? "1" : undefined}>
                    <span className="g-proof-head">
                      <Badge tone={r.confirmed_at ? "good" : r.ran_ok ? "warn" : "alert"}>
                        {r.confirmed_at ? "Confirmed fixed" : r.ran_ok ? "Ran — unconfirmed" : "Did not run"}
                      </Badge>
                      <strong>{r.label}</strong>
                    </span>
                    <span className="g-proof-when">
                      {r.by_person ? `${r.by} asked for this` : "Vault, on its own"}
                      {r.ran_at ? ` · ${ago(r.ran_at)}` : ""}
                      {r.confirmed_at ? ` · checked again ${ago(r.confirmed_at)}` : ""}
                    </span>
                    <span className="g-proof-line">{r.proof}</span>
                    {r.output && <pre className="g-proof-out mono">{r.output}</pre>}
                  </li>
                ))}
              </ol>
            </section>
          )}

          {left && (
            // A decision on record, not a failure going unwatched: the icon
            // and the closing sentence both say the same thing the "Undo —
            // reopen" button below does — this was chosen, and it can be
            // unchosen just as easily.
            <p className="g-check-detail-decision" data-testid="check-decision-left">
              <span className="g-check-detail-decision-icon" aria-hidden="true">
                ↺
              </span>
              <span>
                Left as-is by {detail.decision.decided_by || "someone"}
                {detail.decision.decided_at ? ` on ${ago(detail.decision.decided_at)}` : ""}.
                {detail.decision.reason ? ` "${detail.decision.reason}"` : ""} Reversible any time with Undo below.
              </span>
            </p>
          )}

          <div className="g-check-detail-actions">
            <button
              type="button"
              disabled={resolveDisabled}
              title={resolveDisabled ? resolveFixTooltip : undefined}
              onClick={resolve}
            >
              Resolve
            </button>

            {left ? (
              <button
                type="button"
                disabled={!canDecide}
                title={!canDecide ? decideTooltip : undefined}
                onClick={() => decisionMutation.mutate("undo")}
              >
                Undo — reopen
              </button>
            ) : (
              <>
                <label>Optional expiry <input type="datetime-local" value={expires} disabled={!canDecide} onChange={e => setExpires(e.target.value)} /></label>
                <input
                  type="text"
                  className="g-check-detail-reason"
                  placeholder="Why leave it? (required)"
                  value={reason}
                  disabled={!canDecide}
                  onChange={(e) => setReason(e.target.value)}
                  aria-label="Reason for leaving this check"
                />
                <button
                  type="button"
                  disabled={!canDecide || !reason.trim()}
                  title={!canDecide ? decideTooltip : undefined}
                  onClick={() => decisionMutation.mutate("leave")}
                >
                  Leave it
                </button>
              </>
            )}
          </div>
        </div>
      )}
    </Drawer>
  );
}

export default CheckDetailDrawer;
