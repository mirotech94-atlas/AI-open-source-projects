import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge, useToast } from "../design";
import { declineFirewallRule, firewallRules } from "../lib/api";
import type { FirewallRule } from "../types";

const STATUS: Record<string, { label: string; tone: "alert" | "warn" | "good" | "plain" }> = {
  proposed: { label: "Proposed", tone: "warn" },
  declined: { label: "You said no", tone: "plain" },
  applied: { label: "Applied", tone: "good" },
  rolled_back: { label: "Rolled back", tone: "plain" },
  expired: { label: "No longer open", tone: "good" },
};

/**
 * Item 21: gateway firewall rules Vault PROPOSES from what its scans
 * actually found.
 *
 * Miro's ruling is the shape of this panel: Vault proposes, a person
 * applies, and nothing is written to a router silently. So every rule
 * carries the evidence it came from, Apply is disabled with the reason
 * rather than pretending, and "No" is a real decision that sticks — the hub
 * never re-proposes a rule somebody has already answered.
 *
 * Expired rules stay listed. "This was open last week and is not now" is
 * what an auditor asks about, and a row that vanishes cannot answer.
 */
export function FirewallProposals() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const [declining, setDeclining] = useState<string | null>(null);
  const [reason, setReason] = useState("");

  const q = useQuery({ queryKey: ["firewall"], queryFn: firewallRules });
  const decline = useMutation({
    mutationFn: ({ id, why }: { id: string; why: string }) => declineFirewallRule(id, why),
    onSuccess: async () => {
      setDeclining(null);
      setReason("");
      toast.show("Left as it is. Vault will not propose this again.", "plain");
      await queryClient.invalidateQueries({ queryKey: ["firewall"] });
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  const data = q.data;
  if (!data) return null;
  // Nothing found and nothing decided: silence is the right answer, not an
  // empty panel implying Vault looked and had opinions.
  if (data.rules.length === 0) return null;

  const open = data.rules.filter((r) => r.status === "proposed");

  return (
    <section className="g-fw" aria-label="Proposed firewall rules" data-testid="firewall-proposals">
      <h3>
        Rules for your router
        {open.length > 0 && <Badge tone="warn">{open.length} proposed</Badge>}
      </h3>
      <p className="hint">
        Built from what Vault found reachable from the internet
        {data.gateway ? ` at ${data.gateway}` : ""}. Vault proposes these; it does not
        write to your router.
      </p>
      {!data.can_apply && (
        <p className="hint g-fw-cant" data-testid="firewall-cant-apply">{data.why_not}</p>
      )}

      <ol className="g-fw-list">
        {data.rules.map((r: FirewallRule) => {
          const s = STATUS[r.status] ?? STATUS.proposed;
          return (
            <li key={r.id} data-status={r.status} data-risk={r.risk}>
              <span className="g-fw-head">
                <Badge tone={r.status === "proposed" && r.risk === "CRIT" ? "alert" : s.tone}>
                  {s.label}
                </Badge>
                <strong>{r.title}</strong>
              </span>
              <span className="g-fw-because">{r.because}</span>
              <span className="g-fw-evidence mono">{r.evidence}</span>
              {r.status === "declined" && (
                <span className="g-fw-decided">
                  {r.decided_by || "someone"} left it as it is
                  {r.reason ? ` — "${r.reason}"` : ""}
                </span>
              )}
              {r.status === "proposed" && (
                declining === r.id ? (
                  <span className="g-fw-actions">
                    <input
                      aria-label={`Why leave ${r.title} as it is`}
                      placeholder="Why? (optional)"
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                    />
                    <button type="button" onClick={() => decline.mutate({ id: r.id, why: reason })}>
                      Leave it
                    </button>
                    <button type="button" className="g-btn-ghost" onClick={() => setDeclining(null)}>
                      Cancel
                    </button>
                  </span>
                ) : (
                  <span className="g-fw-actions">
                    <button
                      type="button"
                      disabled={!data.can_apply}
                      title={data.can_apply ? "Apply this rule, with an automatic rollback if nobody confirms it" : data.why_not}
                    >
                      Apply
                    </button>
                    <button type="button" className="g-btn-ghost" onClick={() => setDeclining(r.id)}>
                      Leave it
                    </button>
                  </span>
                )
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

export default FirewallProposals;
