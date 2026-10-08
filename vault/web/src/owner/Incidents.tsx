import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { StatStrip, Verdict, useToast } from "../design";
import { inboxPayload, requestFix, requestFixOrConfirm } from "../lib/api";
import type { FixConfirmation, InboxRow } from "../types";
import { ago, clock, sevLabel, sevTone, useActiveTenant } from "./net";
import { IncidentChat } from "./IncidentChat";

/**
 * Open and recently resolved -- the reference's Incidents, on the selected
 * customer's alerts.
 *
 * The distinguishing element is the plan strip: the literal thing Vault
 * intends to do, next to what it will cost, next to the one button that
 * authorises it. Showing the exact action is the point -- the owner should
 * never have to guess what approval authorises. The hub's `AlertRemedy`
 * already carries all three (`summary`, `impact`, `action`), so nothing here
 * is composed; it is printed.
 */

type Filter = "open" | "you" | "resolved" | "all";

const SEV_ORDER: Record<string, number> = { crit: 0, warn: 1, info: 2, plain: 3 };

function isResolved(r: InboxRow): boolean {
  return r.status === "resolved" || r.status === "closed" || r.status === "done";
}
/** Waiting on the owner's yes -- and only that. A `human` remedy is the
 *  other case: nothing Vault runs fixes it, so there is nothing to
 *  approve, and calling it "needs you" put a latency blip in the same red as
 *  a reboot waiting for permission. What that alert needs is to be read,
 *  which is what the conversation under it is for. */
function needsYou(r: InboxRow): boolean {
  return !isResolved(r) && r.remedy !== null && r.remedy.kind === "approval";
}

function Incidents() {
  const tenant = useActiveTenant();
  const toast = useToast();
  // "all" carries the resolved rows the reference's filter promises; the
  // open set is not a second read, it is a subset.
  const q = useQuery({
    queryKey: ["inbox", "alerts", "all"],
    queryFn: () => inboxPayload("alerts", null, "all"),
    refetchInterval: 60_000, enabled: tenant !== null,
  });
  const [filter, setFilter] = useState<Filter>("open");
  // The incident whose conversation is open. One at a time: the thread is
  // the reading surface, and two open threads is two half-read ones.
  const [talking, setTalking] = useState<string | null>(null);

  const [pending, setPending] = useState<{ key: string; confirmation: FixConfirmation } | null>(null);
  const approve = useMutation({
    mutationFn: async (r: InboxRow) => {
      const rem = r.remedy!;
      if (pending?.key === r.id) {
        return { r, ran: true, confirmation: null as FixConfirmation | null,
          ack: await requestFix(rem.host, rem.action, rem.arg || null, pending.confirmation.token) };
      }
      const out = await requestFixOrConfirm(rem.host, rem.action, rem.arg || null);
      return { r, ran: out.ack !== null, confirmation: out.confirmation, ack: out.ack };
    },
    onSuccess: ({ r, ran, confirmation }) => {
      if (!ran && confirmation) {
        setPending({ key: r.id, confirmation });
        toast.show(confirmation.summary || "Press Confirm to run it.", "plain");
        return;
      }
      setPending(null);
      toast.show("Approved — Vault is running it.", "good");
      void q.refetch();
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  const rows = useMemo(() => q.data?.rows ?? [], [q.data]);
  const counts = useMemo(() => ({
    open: rows.filter((r) => !isResolved(r)).length,
    you: rows.filter(needsYou).length,
    resolved: rows.filter(isResolved).length,
    all: rows.length,
  }), [rows]);

  const shown = useMemo(() => {
    const pick = rows.filter((r) =>
      filter === "all" ? true : filter === "open" ? !isResolved(r) : filter === "you" ? needsYou(r) : isResolved(r));
    // Severity, then age: the reference's order, and the order the work has
    // to be done in.
    return pick.sort((a, b) =>
      (SEV_ORDER[sevTone(a.sev)] - SEV_ORDER[sevTone(b.sev)]) || (a.first_seen < b.first_seen ? -1 : 1));
  }, [rows, filter]);

  if (tenant === null) return <p className="g-own-lede">Pick a customer in the bar to see their incidents.</p>;
  if (q.isPending) return <p role="status">Reading incidents…</p>;
  if (q.isError && !q.data) {
    return <div role="alert">Could not read incidents. <button onClick={() => void q.refetch()}>Retry</button></div>;
  }

  const chips: { key: Filter; label: string; warn?: boolean }[] = [
    { key: "open", label: `Open · ${counts.open}` },
    { key: "you", label: `Waiting on you · ${counts.you}`, warn: counts.you > 0 },
    { key: "resolved", label: `Resolved · ${counts.resolved}` },
    { key: "all", label: "All" },
  ];

  const crit = rows.filter((r) => !isResolved(r) && sevTone(r.sev) === "crit").length;
  const iTone = crit > 0 ? "alert" : counts.you > 0 ? "warn" : counts.open > 0 ? "warn" : "good";
  const iHead = crit > 0
    ? `${crit} critical incident${crit === 1 ? "" : "s"} open`
    : counts.you > 0
      ? `${counts.you} waiting on you`
      : counts.open > 0
        ? `${counts.open} open, none waiting on you`
        : "Nothing needs you";
  return (
    <div className="g-own-page">
      <Verdict tone={iTone} headline={iHead}
               body={counts.open === 0 ? `${counts.resolved} resolved recently.` : "Severity first, then age. Disruptive fixes wait for your approval."} />
      <StatStrip items={[
        { label: "Open", value: String(counts.open), deltaTone: counts.open > 0 ? "warn" : undefined },
        { label: "Waiting on you", value: String(counts.you), deltaTone: counts.you > 0 ? "alert" : undefined },
        { label: "Critical", value: String(crit), deltaTone: crit > 0 ? "alert" : undefined },
        { label: "Resolved recently", value: String(counts.resolved), deltaTone: "good" },
      ]} />
      <div className="g-own-toolbar g-own-toolbar-bare">
        {chips.map((c) => (
          <button key={c.key} type="button" className={`g-own-fchip${c.warn ? " is-warn" : ""}`}
                  aria-pressed={filter === c.key} onClick={() => setFilter(c.key)}>{c.label}</button>
        ))}
        <span className="g-own-toolbar-note">Severity, then age</span>
      </div>

      {shown.length === 0 && (
        <p className="g-own-quiet">
          {filter === "open" ? "Nothing is open." : filter === "you" ? "Nothing is waiting on you." : "Nothing here."}
        </p>
      )}

      {shown.map((r) => {
        const done = isResolved(r);
        const tone = done ? "good" : sevTone(r.sev);
        const rem = r.remedy;
        // Four states, three of them the reference's. "Watching" is the
        // fourth: open, nothing to approve, Vault keeps looking.
        const state = done ? { label: "closed", tone: "good" as const }
          : needsYou(r) ? { label: "needs you", tone: "crit" as const }
          : rem?.kind === "auto" ? { label: "waiting", tone: "warn" as const }
          : { label: "watching", tone: "plain" as const };
        return (
          <article key={r.id} className={`g-own-section g-inc g-inc-${tone}`}>
            <div className="g-inc-body">
              <div className="g-inc-main">
                <span className="g-inc-meta">
                  <span className={`g-own-pill g-own-pill-${done ? "good" : sevTone(r.sev)}`}>{done ? "resolved" : sevLabel(r.sev)}</span>
                  <span className="g-inc-id">{r.id}</span>
                  <span className="g-inc-age">{ago(r.first_seen)} ago{r.count > 1 ? ` · ${r.count}×` : ""}</span>
                </span>
                <h3 className="g-inc-title">{r.title}</h3>
                {/* States the consequence, not only the symptom: the hub's
                    `why` is written that way, and `what_changed` follows it. */}
                {(r.why || r.what_changed) && (
                  <p className="g-inc-detail">{r.why}{r.why && r.what_changed ? " " : ""}{r.what_changed}</p>
                )}
              </div>
              <div className="g-inc-side">
                <span className={`g-own-pill g-own-pill-${state.tone} g-inc-state`}>
                  <span className="g-own-dot" aria-hidden="true" />{state.label}
                </span>
                {r.entity && <span className="g-inc-target">{r.entity}</span>}
                <button type="button" className="g-own-btn g-inc-talk" aria-expanded={talking === r.id}
                        onClick={() => setTalking(talking === r.id ? null : r.id)}>
                  {talking === r.id ? "Close" : "Ask Vault"}
                </button>
              </div>
            </div>

            {/* The plan strip: what Vault intends to do, what it costs,
                and the button that authorises it. Only when there is a plan;
                an alert with no remedy has no strip rather than an empty one. */}
            {rem && rem.summary && (
              <div className="g-own-plan">
                <span className="g-own-plan-l">{rem.kind === "human" ? "No fix to run" : "Plan"}</span>
                {/* The literal action -- `block_attacker 192.0.2.107 @ booking`
                    -- rather than the sentence about it: the reference is
                    explicit that the owner should never have to guess what
                    approval authorises. The sentence is the tooltip. A human
                    remedy has no action, so it prints what only a person can
                    do instead, in prose, because that is what it is. */}
                {rem.kind === "human" || !rem.action ? (
                  <span className="g-own-plan-cmd g-own-plan-prose" title={rem.summary}>{rem.need || rem.summary}</span>
                ) : (
                  <span className="g-own-plan-cmd" title={rem.summary}>
                    {rem.action}{rem.arg ? ` ${rem.arg}` : ""}{rem.host ? ` @ ${rem.host}` : ""}
                  </span>
                )}
                {rem.impact && <span className="g-own-plan-impact">{rem.impact}</span>}
                <span className="g-own-plan-side">
                  {done ? (
                    <span className="g-own-plan-fixed">fixed {clock(r.last_seen)}</span>
                  ) : rem.kind === "approval" ? (
                    <button type="button" className="g-own-plan-go" disabled={approve.isPending}
                            onClick={() => approve.mutate(r)}>
                      {pending?.key === r.id ? "Confirm to run" : "Approve to run"}
                    </button>
                  ) : rem.kind === "auto" ? (
                    <span className="g-own-plan-auto">automatic</span>
                  ) : (
                    <button type="button" className="g-own-plan-go" onClick={() => setTalking(r.id)}>What do I do?</button>
                  )}
                </span>
              </div>
            )}
            {talking === r.id && <IncidentChat alertKey={r.id} />}
          </article>
        );
      })}
    </div>
  );
}

export default Incidents;
