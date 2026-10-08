import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge, useToast } from "../design";
import { atLeast, roleOf, useMe } from "../lib/auth";
import { sevTone } from "../lib/format";
import { alertHandoff, inboxTriage, runAction as dispatchAction } from "../lib/api";
import type { Ack } from "../lib/api";
import { useChatHandoff } from "../shell/chat/handoff";
import type { Role } from "../lib/auth";
import type { RowAction, TriageItem } from "../types";
import { EmptyState } from "./EmptyState";

const ROLE_LABEL: Record<string, Role> = { operator: "operator", admin: "admin" };

/** `title` for a control this viewer's role cannot use — the same convention
 *  `Dns.tsx`/`ApiTokens.tsx`/`DeviceDrawer.tsx` use (`"<role> role required"`),
 *  reused here instead of inventing a second wording for the same idea. */
function roleTitle(minRole: string): string {
  return `${minRole} role required`;
}

/** One row's one action: whatever the hub already decided this role may run
 *  (`item.actions`, role-filtered exactly like the plain inbox), or — when
 *  the row has one to name but this role falls short — a disabled control
 *  carrying the role it would need. Never a hidden, silent nothing. */
/* Which action the lead card promotes to its filled button.
 *
 * The secondary row has to exclude exactly this one, and "everything that is
 * not a fix" is not the same rule: when an item has no fix action the primary
 * falls back to the first action it does have, and that action was then
 * rendered a second time alongside it. The live inbox showed "Yes, do it"
 * twice on the reboot item for precisely that reason. One function, two
 * callers, no way for them to disagree. */
export function primaryAction(item: TriageItem): RowAction | undefined {
  return item.actions.find((a) => a.kind === "fix") ?? item.actions[0];
}

function PrimaryControl({
  item,
  role,
  pending,
  onRun,
}: {
  item: TriageItem;
  role: Role | null;
  pending: boolean;
  onRun: (action: RowAction) => void;
}) {
  const fix = primaryAction(item);
  if (fix) {
    return (
      <button type="button" className="primary" disabled={pending} onClick={() => onRun(fix)}>
        {pending ? "Working…" : fix.label}
      </button>
    );
  }
  if (item.primary_min_role) {
    const need = ROLE_LABEL[item.primary_min_role] ?? "admin";
    const has = atLeast(role, need);
    // `has` is always false here (an action would already be in `item.actions`
    // otherwise) — kept explicit so this still renders correctly if a future
    // caller reuses `PrimaryControl` somewhere actions aren't pre-filtered.
    return (
      <button type="button" className="primary" disabled={!has} title={has ? undefined : roleTitle(item.primary_min_role)}>
        {item.primary_label}
      </button>
    );
  }
  return null;
}

function RestActions({
  item,
  pendingIds,
  onRun,
  onAsk,
}: {
  item: TriageItem;
  pendingIds: Set<string>;
  onRun: (id: string, action: RowAction) => void;
  onAsk: (id: string) => void;
}) {
  const pending = pendingIds.has(item.id);
  return (
    <div className="g-triage-acts">
      {item.actions.map((a) => (
        <button key={a.kind} type="button" className={a.kind === "fix" || a.kind === "yes" ? "primary" : undefined}
                disabled={pending} onClick={() => onRun(item.id, a)}>
          {pending ? "Working…" : a.label}
        </button>
      ))}
      {item.actions.length === 0 && item.primary_min_role && (
        <button type="button" disabled title={roleTitle(item.primary_min_role)}>
          {item.primary_label}
        </button>
      )}
      {item.remedy && item.remedy.kind !== "none" && item.remedy.kind !== "auto" && (
        <button type="button" onClick={() => onAsk(item.id)}>Ask Vault…</button>
      )}
    </div>
  );
}

/** The three outcome counters (pattern A6) — the primary axis of the inbox,
 *  replacing the backlog feeling a flat count gives. */
function Outcomes({ outcomes }: { outcomes: import("../types").TriageOutcomes }) {
  return (
    <div className="g-triage-outcomes">
      <div className="g-triage-oc g-triage-oc-handled">
        <b>{outcomes.handled}</b>
        <span className="g-triage-oc-lbl">
          <strong>Recovery verified</strong>
          <span>{outcomes.handled_period}, without asking</span>
        </span>
      </div>
      <div className="g-triage-oc g-triage-oc-yes">
        <b>{outcomes.needs_yes}</b>
        <span className="g-triage-oc-lbl">
          <strong>Needs your yes</strong>
          <span>Vault can act, one click each</span>
        </span>
      </div>
      <div className="g-triage-oc g-triage-oc-you">
        <b>{outcomes.needs_you}</b>
        <span className="g-triage-oc-lbl">
          <strong>Needs you</strong>
          <span>outside Vault's reach — instructions inside</span>
        </span>
      </div>
    </div>
  );
}

/** The prioritised inbox (task 83, patterns A3 + A6): one lead item sized
 *  like it matters, everything else one line and expanding on demand, the
 *  three outcome counters as the primary axis instead of a flat backlog
 *  count. Self-contained — its own query, its own action dispatch — so it
 *  can sit above the untouched `InboxList` without threading its state
 *  through `Inbox.tsx`'s existing mutations. */
export function InboxTriage() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const openHandoff = useChatHandoff();
  const meQuery = useMe();
  const role = roleOf(meQuery.data);

  const [pendingIds, setPendingIds] = useState<Set<string>>(new Set());
  const [openRest, setOpenRest] = useState<Set<string>>(new Set());

  const query = useQuery({ queryKey: ["inbox", "triage"], queryFn: inboxTriage });

  function invalidate() {
    void queryClient.invalidateQueries({ queryKey: ["inbox"] });
  }

  const actionMutation = useMutation({
    mutationFn: (action: RowAction) => dispatchAction<Ack>(action),
    onError: (err: Error) => toast.show(err.message, "alert"),
    onSettled: invalidate,
  });

  function run(id: string, action: RowAction) {
    // "Open the conversation" is a hand-off, not a write. It POSTs to
    // `/chat/handoff`, which answers with the opening line and thread to show
    // -- there is no `status` on that reply, so the generic path below fell
    // straight through to a "Done" toast and the chat never opened. The
    // button reported success for work it had not done and could not do:
    // opening the panel is the entire point of pressing it.
    if (action.kind === "ask") {
      ask(id);
      return;
    }
    setPendingIds((prev) => new Set(prev).add(id));
    actionMutation.mutate(action, {
      onSuccess: (ack) => {
        // "Done" is a claim, and it has to be earned. The hub dispatches a
        // recommendation only when it carries a runnable action (a host and
        // an action name); otherwise it answers `status: "needs-you"` and the
        // work is still entirely the owner's.
        //
        // Saying "Done" to that is the worst answer this card can give: the
        // owner walks away believing a CRIT was handled while nothing ran and
        // nothing is queued. It happened on the live hub, on this very card --
        // a failing system DNS resolver was accepted, recorded `needs-you`,
        // and reported as Done, with no row on the Activity page to
        // contradict it.
        if ((ack as { status?: string } | null)?.status === "needs-you") {
          toast.show("Noted, but this one needs you. Opening Vault.", "plain");
          ask(id);
          return;
        }
        toast.show(action.kind === "fix" ? "Fix queued" : "Done", "good");
      },
      onSettled: () => {
        setPendingIds((prev) => {
          const next = new Set(prev);
          next.delete(id);
          return next;
        });
        invalidate();
      },
    });
  }

  const handoffMutation = useMutation({
    mutationFn: (id: string) => alertHandoff(id, null),
    onSuccess: (handoff) => openHandoff(handoff),
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  function ask(id: string) {
    handoffMutation.mutate(id);
  }

  function toggleRest(id: string) {
    setOpenRest((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  if (query.isLoading || !query.data) return null;
  const payload = query.data;

  if (!payload.lead && payload.rest.length === 0) {
    return (
      <EmptyState title="Nothing here." body="No incidents, alerts or recommendations match this view right now — that's a good sign." />
    );
  }

  return (
    <div className="g-triage">
      <Outcomes outcomes={payload.outcomes} />

      {payload.lead && (
        <div className={`g-triage-lead g-tone-${sevTone(payload.lead.sev)}`}>
          <span className="g-triage-kicker">Most dangerous right now</span>
          <h3 className="g-triage-lead-title">{payload.lead.title.split(" — ")[0]}</h3>
          <div className="g-triage-lead-meta">
            <Badge tone={sevTone(payload.lead.sev)}>{payload.lead.sev}</Badge>
            <Badge tone="plain">{payload.lead.since_text}</Badge>
            {payload.lead.count > 1 && <Badge tone="plain">{payload.lead.count} alerts grouped</Badge>}
            {payload.lead.confidence !== null && <Badge tone="plain">{payload.lead.confidence >= 80 ? "High" : payload.lead.confidence >= 60 ? "Moderate" : "Limited"} evidence</Badge>}
          </div>
          {(payload.lead.recommendations ?? []).length > 0 && <details><summary>Supporting recommendation</summary>
              {(payload.lead.recommendations ?? []).map(a => <p key={`rec:${a.id}`}><strong>{a.title}</strong><br/>{a.why}</p>)}
            </details>}
            {payload.lead.why && <details className="g-triage-lead-evidence"><summary>Evidence and details</summary><p className="g-triage-lead-why">{payload.lead.why}</p></details>}
          <div className="g-triage-lead-acts">
            <PrimaryControl item={payload.lead} role={role} pending={pendingIds.has(payload.lead.id)}
                             onRun={(a) => run(payload.lead!.id, a)} />
            {payload.lead.actions.filter((a) => a !== primaryAction(payload.lead!)).map((a) => (
              <button key={a.kind} type="button" disabled={pendingIds.has(payload.lead!.id)}
                      onClick={() => run(payload.lead!.id, a)}>
                {a.label}
              </button>
            ))}
            {payload.lead.remedy && payload.lead.remedy.kind !== "none" && payload.lead.remedy.kind !== "auto" && (
              <button type="button" onClick={() => ask(payload.lead!.id)}>Ask Vault…</button>
            )}
            <span className="g-triage-rank-reason" title="Why this ranks first">{payload.lead.rank_reason}</span>
          </div>
        </div>
      )}

      {payload.rest.length > 0 && (
        <div className="g-triage-list">
          <header>
            <h3>Everything else</h3>
            <span className="g-triage-roll">{payload.rest.length} rows · grouped by cause · click to open</span>
          </header>
          <ul>
            {payload.rest.map((item) => {
              const open = openRest.has(item.id);
              return (
                <li key={item.id} className={`g-triage-row g-tone-${sevTone(item.sev)}`}>
                  <button type="button" className="g-triage-row-summary" aria-expanded={open}
                          onClick={() => toggleRest(item.id)}>
                    <span className="g-triage-row-sev" aria-hidden="true" />
                    <span className="g-triage-row-txt">
                      <b>{item.title}</b>
                      {item.entity && <span className="g-triage-row-entity"> — {item.entity}</span>}
                    </span>
                    {item.primary_min_role !== "" && item.actions.length === 0 && (
                      <Badge tone="warn" title={roleTitle(item.primary_min_role)}>
                        {item.primary_min_role} role needed
                      </Badge>
                    )}
                    {item.primary_min_role === "" && item.remedy?.kind === "human" && (
                      <Badge tone="warn">needs you</Badge>
                    )}
                    <span className="g-triage-row-when">{item.since_text}</span>
                  </button>
                  {open && (
                    <div className="g-triage-row-body">
                      {item.why && <p>{item.why}</p>}
                      <RestActions item={item} pendingIds={pendingIds} onRun={run} onAsk={ask} />
                      {item.count > 1 && (
                        <p className="g-triage-roll">
                          Rolled up from {item.count} alert{item.count === 1 ? "" : "s"} — one cause, one row.
                        </p>
                      )}
                      <p className="g-triage-rank-reason">{item.rank_reason}</p>
                      {(item.recommendations ?? []).length > 0 && <details><summary>Supporting recommendation</summary>
                        {(item.recommendations ?? []).map(a => <p key={`rec:${a.id}`}><strong>{a.title}</strong><br/>{a.why}</p>)}
                      </details>}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}

export default InboxTriage;
