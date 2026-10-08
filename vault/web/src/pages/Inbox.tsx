import {DataNotice} from "../components/DataNotice";
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Badge, Drawer, Feature, Page, PageHeader, StatStrip, Verdict, useToast } from "../design";
import { ExportMenu } from "../components/ExportMenu";
import type { PageAction, SkeletonTone as Tone, StatItem } from "../design";
import { InboxList } from "../components/InboxList";
import { InboxTriage } from "../components/InboxTriage";
import type { InboxView } from "../components/InboxList";
import { rowCanExpand } from "../components/InboxRowView";
import type { RecDecisionStatus } from "../components/InboxRowView";
import {
  alertBulk,
  alertHandoff,
  inboxPayload,
  inboxTriage,
  queueStatus,
  runAction as dispatchAction,
} from "../lib/api";
import type { Ack } from "../lib/api";
import { roleOf, useMe } from "../lib/auth";
import { useHotkeys } from "../lib/keys";
import { useLiveFeed } from "../lib/live";
import { useChatHandoff } from "../shell/chat/handoff";
import type { InboxCounts, InboxPayload, InboxRow, InboxTriagePayload, RowAction } from "../types";

const EMPTY_COUNTS: InboxCounts = { incidents: 0, alerts: 0, recs: 0, unread: 0, needs: 0 };

type StatusFilter = "open" | "all";

/** The sidebar addresses Inbox's three views as `?filter=alerts|incidents|recs`
 *  — that query is the route, so a child row is linkable and the browser's
 *  back button steps between views. Anything else falls back to alerts. */
function viewFromParam(v: string | null): InboxView {
  return v === "incidents" || v === "recs" ? v : "alerts";
}

/** The open-by-default `<details>`'s own summary text, naming which of the
 *  three is actually showing -- the one thing on the page proving a sidebar
 *  click did something, now that `InboxTriage` above it is deliberately the
 *  same on all three. */
const VIEW_BROWSE_LABEL: Record<InboxView, string> = {
  alerts: "Every open alert",
  incidents: "Every open incident",
  recs: "Every open recommendation",
};

/** A single row matches if it does, or any of its `children` do — a client
 *  search over an incident should still find it via a child alert's text,
 *  not just the incident's own title/why/what-changed. */
function matchesSearch(row: InboxRow, needle: string): boolean {
  if (needle === "") return true;
  const haystack = `${row.title} ${row.entity} ${row.why} ${row.what_changed}`.toLowerCase();
  if (haystack.includes(needle.toLowerCase())) return true;
  return row.children.some((child) => matchesSearch(child, needle));
}

function filterRows(rows: InboxRow[], status: StatusFilter, search: string, dismissed: Set<string>): InboxRow[] {
  return rows
    .filter((row) => !dismissed.has(row.id))
    .filter((row) => (status === "all" ? true : row.status !== "resolved"))
    .filter((row) => matchesSearch(row, search));
}

/** Polls `GET /api/v1/queue/{id}` every 5s until the fix reaches a terminal
 *  state, then reports it and stops. Rendered only while a recommendation's
 *  "Yes" has queued a fix — one instance per in-flight queue id. */
function QueuePoller({ id, onSettled }: { id: string; onSettled: (status: "done" | "failed") => void }) {
  const query = useQuery({
    queryKey: ["queue", id],
    queryFn: () => queueStatus(id),
    refetchInterval: 5_000,
  });
  useEffect(() => {
    if (query.data?.status === "done" || query.data?.status === "failed") {
      onSettled(query.data.status);
    }
  }, [query.data?.status, onSettled]);
  return null;
}

/** The choices a snooze offers, and the promise each one makes.
 *
 *  Not a free-text number of hours: "come back tomorrow morning" is what a
 *  person actually means, and a box that accepts 0.5 or 400 invites both. */
const SNOOZE_CHOICES: { hours: number; label: string; note: string }[] = [
  { hours: 1, label: "In an hour", note: "you are in the middle of something" },
  { hours: 8, label: "Later today", note: "after this shift" },
  { hours: 24, label: "Tomorrow", note: "same time, next day" },
  { hours: 72, label: "In three days", note: "it is waiting on someone else" },
  { hours: 168, label: "Next week", note: "as long as Vault will hold it" },
];

function SNOOZE_WHEN(hours: number): string {
  const choice = SNOOZE_CHOICES.find((c) => c.hours === hours);
  return (choice ? choice.label : `in ${hours} hours`).toLowerCase();
}

export function Inbox() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const live = useLiveFeed();
  const role = roleOf(useMe().data);
  const openHandoff = useChatHandoff();
  // The thread the last hand-off landed on, so a second alert continues the
  // same conversation instead of starting a fresh one Vault has no context
  // for. `null` until the first hand-off of the session.
  const handoffThread = useRef<string | null>(null);

  const [searchParams, setSearchParams] = useSearchParams();
  const view = viewFromParam(searchParams.get("filter"));
  const [status, setStatus] = useState<StatusFilter>("open");
  const [search, setSearch] = useState("");
  const [rawCursor, setCursor] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [pendingIds, setPendingIds] = useState<Set<string>>(new Set());
  const [recStatus, setRecStatus] = useState<Record<string, RecDecisionStatus>>({});
  const [queueIds, setQueueIds] = useState<Record<string, string>>({}); // rowId -> queueId
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  // A recommendation the viewer said "No" to hides immediately and stays
  // hidden even after the next refetch — the hub records the decision but
  // never changes `status` for "no" (only "yes" does), so relying on the
  // refetched payload alone would flicker the row straight back.
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());

  // `status` rides in the query key and the request itself: the hub is the
  // one that knows which rows "all" uncovers (a declined recommendation, a
  // resolved alert, a closed incident) — see `inbox.build`'s own `status`
  // handling. Before this, "All" only relabelled whatever "open" already
  // fetched (client-side `filterRows` over a payload the server had
  // already filtered down to "open"), so a declined recommendation was not
  // just hidden, it was unreachable — no toggle on this page could ever
  // bring it back into view.
  const queryKey = ["inbox", view, status];
  const inboxQuery = useQuery({ queryKey, queryFn: () => inboxPayload(view, null, status) });
  // Same key `InboxTriage` reads under, so the verdict and the feature block
  // share one fetch; the page only needs `outcomes` for the verdict + strip.
  const triageQuery = useQuery<InboxTriagePayload>({ queryKey: ["inbox", "triage"], queryFn: inboxTriage });

  // Any live alert reconciles every open inbox view against the server —
  // a WS "alert" frame can mean a new row, a status change or a resolved one.
  useEffect(() => {
    return live.on("alert", () => {
      void queryClient.invalidateQueries({ queryKey: ["inbox"] });
    });
  }, [live, queryClient]);

  const payload: InboxPayload = inboxQuery.data ?? { counts: EMPTY_COUNTS, rows: [] };
  const filteredRows = useMemo(
    () => filterRows(payload.rows, status, search, dismissed),
    [payload.rows, status, search, dismissed],
  );

  // Cursor stays in range as the filtered list shrinks/grows — derived at
  // render time rather than synchronized back into state from an effect.
  const cursor = Math.min(Math.max(rawCursor, 0), Math.max(0, filteredRows.length - 1));

  const withPending = async (id: string, fn: () => Promise<Ack>) => {
    setPendingIds((prev) => new Set(prev).add(id));
    try {
      await fn();
    } finally {
      setPendingIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  };

  function patchRow(rows: InboxRow[], id: string, patch: Partial<InboxRow>): InboxRow[] {
    return rows.map((r) =>
      r.id === id
        ? { ...r, ...patch }
        : r.children.length > 0
          ? { ...r, children: patchRow(r.children, id, patch) }
          : r,
    );
  }

  // Fetches Vault's own explanation for one alert and opens the chat with
  // it already in the transcript. Read-only on the hub except for minting the
  // tier-3 confirmation token when the remedy is one a "yes" may run — the
  // ordinary proposal flow, unchanged: the token is single-use and bound to
  // that exact action.
  const handoffMutation = useMutation({
    mutationFn: (row: InboxRow) => alertHandoff(row.id, handoffThread.current),
    onSuccess: (handoff) => {
      handoffThread.current = handoff.thread_id || handoffThread.current;
      openHandoff(handoff);
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  function askGuardian(row: InboxRow) {
    handoffMutation.mutate(row);
  }

  /** Ack and resolve are not repairs — they say "I have seen this" and "this
   *  is over". So neither one silently runs a fix. But neither may leave the
   *  owner guessing either: when Vault can repair the row itself, this says
   *  so and leaves the Fix button to do it; when the repair needs the owner
   *  (an approval, a role, or something only a person can do), the chat opens
   *  with Vault already explaining what it needs. */
  function offerRemedy(row: InboxRow, verb: string) {
    const remedy = row.remedy;
    if (!remedy || remedy.kind === "none") return;
    if (remedy.kind === "auto") {
      toast.show(`${verb} — ${remedy.summary} Press Fix and I will run it.`, "plain");
      return;
    }
    toast.show(`${verb} — this one needs you. Opening Vault.`, "plain");
    askGuardian(row);
  }

  const runActionMutation = useMutation({
    // Dispatched exactly as the hub described the action — its own
    // `method`/`path`/`body` — never rebuilt from `row.id` + `action.kind`.
    // A hand-built path is wrong the moment a kind's route isn't
    // `/api/v1/alerts/{id}/{kind}` (e.g. "fix" posts to `/api/v1/fix`,
    // "trust" posts to `/api/v1/devices/{mac}`).
    mutationFn: async ({ action }: { row: InboxRow; action: RowAction }) => {
      return dispatchAction<Ack>(action);
    },
    onMutate: async ({ row, action }) => {
      await queryClient.cancelQueries({ queryKey });
      const previous = queryClient.getQueryData<InboxPayload>(queryKey);
      if (previous) {
        const optimisticStatus = action.kind === "ack" ? "acked" : action.kind === "snooze" ? "snoozed" : action.kind === "resolve" ? "resolved" : action.kind === "restore" ? "open" : row.status;
        queryClient.setQueryData<InboxPayload>(queryKey, {
          ...previous,
          rows: patchRow(previous.rows, row.id, { status: optimisticStatus }),
        });
      }
      return { previous };
    },
    onSuccess: (ack, { row, action }) => {
      if (action.kind === "ack") offerRemedy(row, "Acknowledged");
      else if (action.kind === "resolve") {
        // Resolve says "this is dealt with". When the condition underneath is
        // still true the sensor re-raises it on the very next sweep -- which
        // is correct, and from the outside is indistinguishable from the
        // button having done nothing. Say so now rather than let it reappear
        // on its own. Ack is the one that survives a re-fire, because the
        // sensor keeps an acked list; resolve is a claim about the world.
        if ((ack as { still_firing?: boolean } | null)?.still_firing) {
          toast.show(
            "Resolved — but this is still happening, so it will come back on the next scan. Ack it instead to keep it quiet while it lasts.",
            "plain",
          );
        } else {
          offerRemedy(row, "Resolved");
        }
      }
      else if (action.kind === "restore") {
        // Undoes a decline outright (posts `decision: "clear"`) — the row
        // is fresh and undecided again, so any stale dismiss/queue state
        // from a previous session on this id must not linger.
        setDismissed((prev) => {
          const next = new Set(prev);
          next.delete(row.id);
          return next;
        });
        setRecStatus((prev) => {
          const next = { ...prev };
          delete next[row.id];
          return next;
        });
        toast.show("Brought back — it's a live recommendation again.", "good");
      } else if (action.kind === "fix") {
        // The hub's `/api/v1/fix` ack is the queue row (`{id, status}`) — poll
        // it so the row reports what the fix actually did, not just that it
        // was accepted.
        const id = typeof ack.id === "string" ? ack.id : typeof ack.id === "number" ? String(ack.id) : null;
        toast.show(row.remedy ? `Running: ${row.remedy.summary}` : "Fix queued", "good");
        if (id) setQueueIds((prev) => ({ ...prev, [row.id]: id }));
      }
    },
    onError: (err: Error, _vars, context) => {
      if (context?.previous) queryClient.setQueryData(queryKey, context.previous);
      toast.show(err.message, "alert");
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["inbox"] });
    },
  });

  const bulkAckMutation = useMutation({
    mutationFn: (keys: string[]) => alertBulk(keys, "ack"),
    onMutate: async (keys: string[]) => {
      await queryClient.cancelQueries({ queryKey });
      const previous = queryClient.getQueryData<InboxPayload>(queryKey);
      if (previous) {
        let rows = previous.rows;
        for (const key of keys) rows = patchRow(rows, key, { status: "acked" });
        queryClient.setQueryData<InboxPayload>(queryKey, { ...previous, rows });
      }
      return { previous };
    },
    onError: (err: Error, _vars, context) => {
      if (context?.previous) queryClient.setQueryData(queryKey, context.previous);
      toast.show(err.message, "alert");
    },
    onSuccess: (_data, keys) => {
      toast.show(`Acked ${keys.length} alert${keys.length === 1 ? "" : "s"}`, "good");
      setSelected(new Set());
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["inbox"] });
    },
  });

  // Item 10b: how long, chosen rather than assumed. The hub still defaults
  // to 8 hours when nothing is sent (`routers/write.py`), so the dialog
  // changes what the customer gets, not what the API demands.
  const [snoozeOpen, setSnoozeOpen] = useState(false);
  const [snoozeHours, setSnoozeHours] = useState(8);

  const bulkSnoozeMutation = useMutation({
    mutationFn: ({ keys, hours }: { keys: string[]; hours: number }) =>
      alertBulk(keys, "snooze", hours),
    onMutate: async ({ keys }: { keys: string[]; hours: number }) => {
      await queryClient.cancelQueries({ queryKey });
      const previous = queryClient.getQueryData<InboxPayload>(queryKey);
      if (previous) {
        let rows = previous.rows;
        for (const key of keys) rows = patchRow(rows, key, { status: "snoozed" });
        queryClient.setQueryData<InboxPayload>(queryKey, { ...previous, rows });
      }
      return { previous };
    },
    onError: (err: Error, _vars, context) => {
      if (context?.previous) queryClient.setQueryData(queryKey, context.previous);
      toast.show(err.message, "alert");
    },
    onSuccess: (_data, { keys, hours }) => {
      toast.show(
        `Snoozed ${keys.length} alert${keys.length === 1 ? "" : "s"} — ${SNOOZE_WHEN(hours)}`,
        "good",
      );
      setSelected(new Set());
      setSnoozeOpen(false);
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["inbox"] });
    },
  });

  const recMutation = useMutation({
    // Yes/No are `row.actions` entries too (`kind: "yes"|"no"`), carrying the
    // hub's own body (`{"decision": "yes"|"no"}`) — dispatched the same
    // generic way as every other row action, not a hand-built `{ accept }`.
    mutationFn: ({ action }: { row: InboxRow; action: RowAction }) => dispatchAction<Ack>(action),
    onMutate: async ({ row, action }) => {
      if (action.kind === "yes") setRecStatus((prev) => ({ ...prev, [row.id]: "queued" }));
      await queryClient.cancelQueries({ queryKey });
      const previous = queryClient.getQueryData<InboxPayload>(queryKey);
      return { previous, row, action };
    },
    onSuccess: (ack, { row, action }) => {
      if (action.kind !== "yes") {
        // "No" hides the row optimistically; the hub never flips `status`
        // for a "no" decision, so a refetch would otherwise bring it right
        // back.
        setDismissed((prev) => new Set(prev).add(row.id));
        toast.show("Recommendation declined", "plain");
        return;
      }
      // The hub's ack for `rec_decide` carries the queued fix's id as
      // `queue_id` (an int, from `m.Queue.id`) — never `id`.
      const queueId = ack.queue_id;
      const id = typeof queueId === "string" ? queueId : typeof queueId === "number" ? String(queueId) : null;
      if (id) {
        setQueueIds((prev) => ({ ...prev, [row.id]: id }));
        return;
      }
      // No queue id means nothing was dispatched, and there are two very
      // different reasons for that. The hub enqueues a fix only when the
      // recommendation carries a runnable `action` (a host and an action
      // name); otherwise it answers `status: "needs-you"` and the work is
      // still entirely the owner's to do.
      //
      // Saying "Recommendation applied" to that is the worst thing this
      // screen can do: the owner walks away believing a CRIT was handled
      // while nothing ran and nothing is queued. It happened on the live
      // hub -- a failing DNS resolver and an exposed VNC port were both
      // accepted, both recorded `needs-you`, and both reported as applied.
      //
      // So the honest branch hands off to chat, the same way `offerRemedy`
      // already does for an Ack on a repair only a person can make.
      if (ack.status === "needs-you") {
        setRecStatus((prev) => {
          const next = { ...prev };
          delete next[row.id];
          return next;
        });
        toast.show("Noted, but this one needs you. Opening Vault.", "plain");
        askGuardian(row);
        return;
      }
      setRecStatus((prev) => ({ ...prev, [row.id]: "done" }));
      toast.show("Recommendation applied", "good");
    },
    onError: (err: Error, { row }, context) => {
      if (context?.previous) queryClient.setQueryData(queryKey, context.previous);
      setRecStatus((prev) => {
        const next = { ...prev };
        delete next[row.id];
        return next;
      });
      toast.show(err.message, "alert");
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["inbox"] });
    },
  });

  function toggleExpand(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleSelect(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function runAction(row: InboxRow, action: RowAction) {
    // Same rule as the triage card: an "ask" action opens the chat panel.
    // Sent through the generic dispatch it POSTs the hand-off, gets a reply
    // with no `status` on it, and reports "Done" without ever opening
    // anything -- a success message for the one thing it did not do.
    if (action.kind === "ask") {
      askGuardian(row);
      return;
    }
    void withPending(row.id, () => runActionMutation.mutateAsync({ row, action }));
  }

  function recDecide(row: InboxRow, accept: boolean) {
    const kind = accept ? "yes" : "no";
    const action = row.actions.find((a) => a.kind === kind);
    if (!action) return; // the hub decided this viewer/role gets no such action
    recMutation.mutate({ row, action });
  }

  function settleQueue(rowId: string, terminal: "done" | "failed") {
    setRecStatus((prev) => ({ ...prev, [rowId]: terminal }));
    setQueueIds((prev) => {
      const next = { ...prev };
      delete next[rowId];
      return next;
    });
    toast.show(terminal === "done" ? "Fix applied" : "Fix failed", terminal === "done" ? "good" : "alert");
  }

  // Keyboard: j/k move the cursor, Enter expands, "a" acks the cursor row
  // (Shift+A bulk-acks the selection — the browser reports both as key "a"
  // with `shiftKey` set, since `eventKey` only prepends "shift+" for
  // non-printable keys, so one binding checks the modifier itself), "x"
  // toggles selection, "?" opens the shortcut sheet. `useHotkeys` ignores keys
  // typed into the search box, and its map lives in a ref refreshed every
  // render, so this object can close over the latest `cursor`/`selected`
  // without any manual ref-juggling here.
  function moveCursor(delta: number) {
    if (filteredRows.length === 0) return;
    setCursor((c) => Math.min(filteredRows.length - 1, Math.max(0, c + delta)));
  }

  useHotkeys({
    j: () => moveCursor(1),
    k: () => moveCursor(-1),
    enter: () => {
      const current = filteredRows[cursor];
      // `rowCanExpand`, not `children.length > 0` spelled out again: a plain
      // alert with no children now expands too, to show the working behind its
      // confidence score. The predicate lives beside the control that renders
      // it so the keyboard and the mouse cannot come to disagree about which
      // rows open.
      if (current && rowCanExpand(current)) toggleExpand(current.id);
    },
    a: (e) => {
      if (e.shiftKey) {
        const keys = Array.from(selected);
        if (keys.length > 0) bulkAckMutation.mutate(keys);
        return;
      }
      const current = filteredRows[cursor];
      if (!current) return;
      const ack = current.actions.find((a) => a.kind === "ack");
      if (ack) runAction(current, ack);
    },
    x: () => {
      const current = filteredRows[cursor];
      if (current) toggleSelect(current.id);
    },
    "?": () => setShortcutsOpen(true),
  });

  const outcomes = triageQuery.data?.outcomes;
  const openCrit = payload.rows.filter((r) => r.status === "open" && r.sev === "CRIT").length;
  const needs = outcomes?.needs_you ?? payload.counts.needs;
  const tone: Tone = openCrit > 0 ? "alert" : needs > 0 ? "warn" : "good";
  const headline = openCrit > 0
    ? `${openCrit} thing${openCrit === 1 ? "" : "s"} need${openCrit === 1 ? "s" : ""} a look now`
    : needs > 0
      ? `${needs} thing${needs === 1 ? "" : "s"} need${needs === 1 ? "s" : ""} a decision`
      : "Nothing needs you";
  const verdictBody = outcomes
    ? `${outcomes.handled} handled for you ${outcomes.handled_period}; ${outcomes.needs_yes} waiting on a yes.`
    : "Every alert, incident and recommendation, most important first.";
  const stats: StatItem[] = [
    { label: "Needs you", value: String(needs), deltaTone: needs > 0 ? "warn" : "good" },
    { label: "Handled for you", value: String(outcomes?.handled ?? 0), delta: outcomes?.handled_period },
    { label: "Unread", value: String(payload.counts.unread) },
    { label: "Open", value: String(triageQuery.data?.total_open ?? payload.rows.filter((r) => r.status === "open").length) },
  ];
  // "Mark all read" acks what is on screen and still open; "Snooze" takes the
  // selection when there is one, otherwise the same open set.
  // "Ackable" is what the hub says it is: the row carries an `ack` action.
  const openKeys = filteredRows.filter((r) => r.status === "open" && r.actions.some((a) => a.kind === "ack")).map((r) => r.id);
  const snoozeKeys = selected.size > 0 ? Array.from(selected) : openKeys;
  const canAct = role !== "viewer";
  const actions: PageAction[] = [
    {
      label: "Mark all read",
      primary: true,
      disabled: !canAct || openKeys.length === 0 || bulkAckMutation.isPending,
      title: openKeys.length === 0 ? "No open alerts on screen" : `Ack ${openKeys.length} open alert${openKeys.length === 1 ? "" : "s"}`,
      onClick: () => bulkAckMutation.mutate(openKeys),
    },
    {
      label: "Snooze",
      disabled: !canAct || snoozeKeys.length === 0 || bulkSnoozeMutation.isPending,
      title: selected.size > 0 ? `Snooze the ${selected.size} selected` : "Snooze the open alerts on screen",
      onClick: () => setSnoozeOpen(true),
    },
  ];

  if (inboxQuery.isPending) return <p role="status">Loading inbox…</p>;
  if (inboxQuery.isError && !inboxQuery.data) return <div role="alert">Could not load inbox. <button onClick={() => void inboxQuery.refetch()}>Retry</button></div>;
  return (
    <Page>
      <PageHeader
        eyebrow="Watch · things that want a decision"
        title={
          <span className="g-inbox-card-title">
            Inbox
            {payload.counts.unread > 0 && <Badge tone="alert">{payload.counts.unread} unread</Badge>}
          </span>
        }
        sub="All networks · current decisions · verified recoveries in the past 7 days."
        actions={actions}
        extra={
          <ExportMenu
            path="/api/v1/export/alerts"
            query={{ days: 30, status }}
            label="Export the alert log"
            onError={(msg) => toast.show(msg, "alert")}
          />
        }
      />
      <Verdict tone={tone} headline={headline} body={verdictBody} />
      <StatStrip items={stats} />
      <DataNotice query={inboxQuery} />
      {/* `InboxTriage` answers a different question than the three sidebar
          sub-tabs below it: "what matters most across everything", not
          "show me only the alerts / only the incidents / only the
          recommendations". It is deliberately the same regardless of which
          of the three the URL names -- `payloads/triage.py` sources from
          all three on purpose.
          The per-tab list underneath is the part that actually changes with
          the sidebar click, and it used to sit collapsed behind a summary a
          person had to know to open -- so clicking Incidents after Alerts
          visibly changed nothing until that extra click. It defaults open
          now, and its own summary names which of the three is showing, so
          the page immediately shows something different every time the
          sidebar selection changes. */}
      <Feature title="Waiting on you">
        <InboxTriage />
      </Feature>

      <section className="sk-records">
      <details className="g-triage-browse" open>
        <summary>{VIEW_BROWSE_LABEL[view]}</summary>
        <InboxList
        view={view}
        role={role}
        onViewChange={(v) => {
          // `replace` so switching views inside the tab does not stack up
          // history entries the back button then has to walk out of.
          setSearchParams({ filter: v }, { replace: true });
          setCursor(0);
          setSelected(new Set());
          setExpanded(new Set());
        }}
        counts={payload.counts}
        status={status}
        onStatusChange={setStatus}
        search={search}
        onSearchChange={setSearch}
        rows={filteredRows}
        cursor={cursor}
        selected={selected}
        expanded={expanded}
        pendingIds={pendingIds}
        recStatus={recStatus}
        onToggleExpand={toggleExpand}
        onToggleSelect={toggleSelect}
        onRunAction={runAction}
        onRecDecision={recDecide}
        onAskGuardian={askGuardian}
        />
      </details>
      </section>

      {Object.entries(queueIds).map(([rowId, queueId]) => (
        <QueuePoller key={queueId} id={queueId} onSettled={(terminal) => settleQueue(rowId, terminal)} />
      ))}

      {/* Item 10b: snoozing used to be a silent 8 hours with no reminder at
          the end of it -- the alert simply reappeared if anyone happened to
          look. The customer now picks when, and `scheduler.snooze_wake_job`
          tells their channel when the time is up. */}
      <Drawer
        open={snoozeOpen}
        onClose={() => setSnoozeOpen(false)}
        title="Remind me about this"
        subtitle={`${snoozeKeys.length} alert${snoozeKeys.length === 1 ? "" : "s"} · hidden until then, not closed`}
      >
        <fieldset className="g-snooze-choices">
          <legend>When should Vault bring it back?</legend>
          {SNOOZE_CHOICES.map((c) => (
            <label key={c.hours} className="g-snooze-choice">
              <input
                type="radio"
                name="snooze-when"
                value={c.hours}
                checked={snoozeHours === c.hours}
                onChange={() => setSnoozeHours(c.hours)}
              />
              <span className="g-snooze-text">
                <span className="g-snooze-l">{c.label}</span>
                <span className="g-snooze-n">{c.note}</span>
              </span>
            </label>
          ))}
        </fieldset>
        <p className="g-snooze-where">
          It comes back into this inbox, and Vault says so on whichever
          channel you have connected. A snooze hides an alert; it never
          closes one.
        </p>
        <div className="g-snooze-foot">
          <button type="button" className="g-btn-ghost" onClick={() => setSnoozeOpen(false)}>Cancel</button>
          <button
            type="button"
            className="g-btn-primary"
            disabled={bulkSnoozeMutation.isPending || snoozeKeys.length === 0}
            onClick={() => bulkSnoozeMutation.mutate({ keys: snoozeKeys, hours: snoozeHours })}
          >
            {bulkSnoozeMutation.isPending ? "Snoozing…" : `Remind me ${SNOOZE_WHEN(snoozeHours)}`}
          </button>
        </div>
      </Drawer>

      <Drawer open={shortcutsOpen} onClose={() => setShortcutsOpen(false)} title="Keyboard shortcuts">
        <ul>
          <li><kbd>j</kbd> / <kbd>k</kbd> — move the cursor</li>
          <li><kbd>Enter</kbd> — expand an incident</li>
          <li><kbd>a</kbd> — ack the cursor row</li>
          <li><kbd>x</kbd> — toggle selection</li>
          <li><kbd>Shift</kbd>+<kbd>a</kbd> — bulk-ack the selection</li>
          <li><kbd>?</kbd> — this sheet</li>
        </ul>
      </Drawer>
    </Page>
  );
}

export default Inbox;
