import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Drawer, useToast } from "../design";
import { InboxRowView } from "./InboxRowView";
import { alertHandoff, inboxPayload, queueStatus, runAction } from "../lib/api";
import type { Ack } from "../lib/api";
import { roleOf, useMe } from "../lib/auth";
import { useChatHandoff } from "../shell/chat/handoff";
import { nodeTone } from "../lib/topologyLayout";
import type { InboxRow, RowAction, TopoNode } from "../types";

/** Why the link to this node is not green, in the order the map ranks them
 *  (`nodeTone`). Shown even when no alert row exists — a device can be red
 *  for being offline or quarantined, which is not an alert but is the reason. */
export function linkReasons(n: TopoNode): string[] {
  const out: string[] = [];
  if (n.quarantined) out.push("Quarantined — Vault is blocking it from the network.");
  if (n.state === "offline") out.push("Offline — it has not answered a scan for a while.");
  if (n.alert_sev === "CRIT") out.push("Has a critical alert open.");
  else if (n.alert_sev === "WARN") out.push("Has a warning open.");
  if (n.risk >= 40) out.push(`Risk score ${n.risk} — open its details for the breakdown.`);
  if (n.state === "sleeping") out.push("Sleeping — seen recently, quiet right now.");
  if (n.mac !== null && !n.trusted) out.push("Not marked as yours yet — trust it in its details, or quarantine it.");
  return out;
}

/** Item 18: what a GREEN link has to say for itself.
 *
 *  Red and amber explained themselves and green said nothing, so the one
 *  colour covering most of the map was the one a client could not
 *  interrogate -- and "nothing is wrong" is only reassuring if you can see
 *  what was checked to reach it. These are states, not problems. */
export function linkStatus(n: TopoNode): string[] {
  const out: string[] = [];
  out.push(n.state === "online" ? "Answering scans right now." : `Last seen ${n.state}.`);
  if (n.mac !== null) out.push(n.trusted ? "Marked as yours." : "Not a device Vault tracks by hardware address.");
  out.push(n.risk > 0 ? `Risk score ${n.risk} — below the level Vault raises.` : "No risk score against it.");
  out.push("No alert open against it.");
  return out;
}

/** What a coloured link on the map opens: the node's open alerts as the
 *  inbox's own rows — same hub-described Fix / Ack / Ask Vault actions,
 *  same role gating — so "what is wrong here" and "fix it" never leave the
 *  map. A fix's queue result is polled and reported in place. */
export function LinkIssueDrawer({
  node,
  onClose,
  onOpenDetails,
}: {
  node: TopoNode | null;
  onClose: () => void;
  onOpenDetails: (node: TopoNode) => void;
}) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const role = roleOf(useMe().data);
  const openHandoff = useChatHandoff();
  const handoffThread = useRef<string | null>(null);
  // Which of these rows has its confidence working open. `InboxRowView` takes
  // the expanded state from its parent, and this parent used to hand it a
  // constant `false` and a no-op toggle, which was correct while the only
  // expandable row was an incident (this drawer lists a single device's own
  // alerts, never an incident header). An alert row now has something of its
  // own to reveal — how sure Vault is about repairing it — so a real state
  // is needed here or the control would render and do nothing.
  const [scoreOpen, setScoreOpen] = useState<Set<string>>(new Set());
  const [queueIds, setQueueIds] = useState<Record<string, string>>({}); // rowId -> queueId
  const [fixStatus, setFixStatus] = useState<Record<string, "running" | "done" | "failed">>({});

  // Same key the Inbox uses for its open-alerts view, so the two share cache.
  const inboxQuery = useQuery({
    queryKey: ["inbox", "alerts", "open"],
    queryFn: () => inboxPayload("alerts", undefined, "open"),
    enabled: node !== null,
  });
  const keys = node?.alert_keys ?? [];
  const rows = flatten(inboxQuery.data?.rows ?? []).filter((r) => keys.includes(r.id));

  const actionMutation = useMutation({
    // Dispatched exactly as the hub described it — never rebuilt from the
    // row id and kind (see Inbox.tsx for why).
    mutationFn: ({ action }: { row: InboxRow; action: RowAction }) => runAction<Ack>(action),
    onSuccess: (ack, { row, action }) => {
      if (action.kind === "fix") {
        const id = typeof ack.id === "string" ? ack.id : typeof ack.id === "number" ? String(ack.id) : null;
        toast.show(row.remedy ? `Running: ${row.remedy.summary}` : "Fix queued", "good");
        if (id) {
          setQueueIds((prev) => ({ ...prev, [row.id]: id }));
          setFixStatus((prev) => ({ ...prev, [row.id]: "running" }));
        }
      } else {
        toast.show(`${action.label} — done`, "good");
      }
      void queryClient.invalidateQueries({ queryKey: ["inbox"] });
      void queryClient.invalidateQueries({ queryKey: ["topology"] });
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  const handoffMutation = useMutation({
    mutationFn: (row: InboxRow) => alertHandoff(row.id, handoffThread.current),
    onSuccess: (handoff) => {
      handoffThread.current = handoff.thread_id || handoffThread.current;
      openHandoff(handoff);
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  return (
    <Drawer
      open={node !== null}
      onClose={onClose}
      title={node ? `${node.label} — needs attention` : ""}
      subtitle={node?.ip || undefined}
      width="min(680px, 100vw)"
      data-testid="link-issue-drawer"
    >
      {node && (
        <>
          {/* Item 18: every colour states what it means, green included. */}
          <p className="hint">
            {nodeTone(node) === "alert"
              ? "A red link means this device needs attention now."
              : nodeTone(node) === "warn"
                ? "A yellow link means this device needs a look, but nothing is down."
                : "A green link means Vault has nothing open against this device. Here is what it checked."}
          </p>
          <ul className="g-link-issue-reasons"
              aria-label={nodeTone(node) === "good" ? "What Vault checked" : "Why this link is coloured"}>
            {(nodeTone(node) === "good" ? linkStatus(node) : linkReasons(node)).map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>

          {inboxQuery.isPending ? (
            <p className="hint">Loading the open alerts…</p>
          ) : rows.length > 0 ? (
            <>
            <p className="hint">
              Press <strong>Fix</strong> to have Vault repair it; disruptive fixes ask you to
              confirm first.
            </p>
            <ul className="g-inbox-rows g-link-issues" aria-label="Open alerts on this device">
              {rows.map((row) => (
                <InboxRowView
                  key={row.id}
                  row={row}
                  role={role}
                  isCursor={false}
                  isSelected={false}
                  isExpanded={scoreOpen.has(row.id)}
                  pending={actionMutation.isPending && actionMutation.variables?.row.id === row.id}
                  onToggleExpand={() =>
                    setScoreOpen((prev) => {
                      const next = new Set(prev);
                      if (next.has(row.id)) next.delete(row.id);
                      else next.add(row.id);
                      return next;
                    })
                  }
                  onToggleSelect={() => {}}
                  onRunAction={(action) => actionMutation.mutate({ row, action })}
                  onRecDecision={() => {}}
                  onAskGuardian={
                    row.remedy && row.remedy.kind !== "auto" && row.remedy.kind !== "none"
                      ? () => handoffMutation.mutate(row)
                      : undefined
                  }
                />
              ))}
            </ul>
            </>
          ) : (
            <p className="hint" data-testid="link-issue-empty">
              Nothing here has a Fix button: no open alert points at this device. Its details hold
              the trust and quarantine controls.
            </p>
          )}

          {Object.entries(fixStatus).map(([rowId, status]) => (
            <p key={rowId} className={`g-link-issue-fix g-link-issue-fix-${status}`} role="status">
              {status === "running" ? "Fix running…" : status === "done" ? "Fix done — the link goes green on the next scan." : "Fix failed — see Activity for what happened."}
            </p>
          ))}
          {Object.entries(queueIds).map(([rowId, qid]) => (
            <QueuePoller
              key={qid}
              id={qid}
              onSettled={(status) => {
                setFixStatus((prev) => ({ ...prev, [rowId]: status }));
                setQueueIds((prev) => {
                  const next = { ...prev };
                  delete next[rowId];
                  return next;
                });
                void queryClient.invalidateQueries({ queryKey: ["inbox"] });
                void queryClient.invalidateQueries({ queryKey: ["topology"] });
              }}
            />
          ))}

          <div className="g-link-issue-foot">
            <button type="button" className="g-btn" onClick={() => onOpenDetails(node)}>
              Open device details
            </button>
          </div>
        </>
      )}
    </Drawer>
  );
}

function flatten(rows: InboxRow[]): InboxRow[] {
  return rows.flatMap((r) => [r, ...flatten(r.children ?? [])]);
}

/** Polls `GET /api/v1/queue/{id}` every 5s until the fix settles. */
function QueuePoller({ id, onSettled }: { id: string; onSettled: (status: "done" | "failed") => void }) {
  const query = useQuery({ queryKey: ["queue", id], queryFn: () => queueStatus(id), refetchInterval: 5_000 });
  const status = query.data?.status;
  const fired = useRef(false);
  if ((status === "done" || status === "failed") && !fired.current) {
    fired.current = true;
    queueMicrotask(() => onSettled(status));
  }
  return null;
}
