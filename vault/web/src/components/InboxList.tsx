import { Fragment } from "react";
import type { HTMLAttributes } from "react";
import { Chips, Tabs } from "../design";
import type { Role } from "../lib/auth";
import type { InboxCounts, InboxRow, RowAction } from "../types";
import { EmptyState } from "./EmptyState";
import type { RecDecisionStatus } from "./InboxRowView";
import { InboxRowView } from "./InboxRowView";

/** The three kinds of row the hub keeps. There is deliberately no "needs
 *  you" view here: `InboxTriage` sits directly above this list and answers
 *  exactly that question, so a fourth tab repeating it underneath was the
 *  same thing twice on one screen. */
export type InboxView = "incidents" | "alerts" | "recs";
export type InboxStatusFilter = "open" | "all";

export type InboxListProps = {
  view: InboxView;
  /** Passed straight through to every row — see `InboxRowView`'s own doc. */
  role: Role | null;
  onViewChange: (view: InboxView) => void;
  counts: InboxCounts;
  status: InboxStatusFilter;
  onStatusChange: (status: InboxStatusFilter) => void;
  search: string;
  onSearchChange: (search: string) => void;
  /** Already status- and search-filtered, top-level rows in payload order. */
  rows: InboxRow[];
  cursor: number;
  selected: Set<string>;
  expanded: Set<string>;
  pendingIds: Set<string>;
  recStatus: Record<string, RecDecisionStatus>;
  onToggleExpand: (id: string) => void;
  onToggleSelect: (id: string) => void;
  onRunAction: (row: InboxRow, action: RowAction) => void;
  onRecDecision: (row: InboxRow, accept: boolean) => void;
  /** Opens the chat hand-off for a row whose remedy needs the owner. */
  onAskGuardian: (row: InboxRow) => void;
  className?: string;
  // "role" is spread onto `<div {...rest}>` too, and `HTMLAttributes` already
  // has its own "role" — the ARIA attribute — so without excluding it here
  // this prop's `Role | null` intersects with that `AriaRole | undefined`
  // down to a non-nullable type, and every caller passing `null` fails to
  // typecheck for a reason that has nothing to do with this prop's own
  // declaration below.
} & Omit<HTMLAttributes<HTMLDivElement>, "children" | "role">;

/** What an empty view says, per view.
 *
 *  It used to be one sentence for all four. On a tenant where every view is
 *  empty — the ordinary case for a healthy customer — switching tabs then
 *  changed nothing at all on screen, which is indistinguishable from a tab
 *  strip that does not work. Naming the view is the cheapest possible proof
 *  that the click did something.
 */
const EMPTY_COPY: Record<InboxView, { title: string; body: string }> = {
  incidents: {
    title: "No open incidents.",
    body: "An incident is several related alerts told as one story. None are open.",
  },
  alerts: {
    title: "No open alerts.",
    body: "Nothing is currently wrong that Vault has noticed.",
  },
  recs: {
    title: "No recommendations.",
    body: "Vault has nothing to suggest that you have not already decided on.",
  },
};

const VIEW_ITEMS: { value: InboxView; label: string }[] = [
  // "Needs you" leads: it is the only tab whose rows a person must act on,
  // and it is a filter over Alerts rather than a fourth pile of its own.
  { value: "incidents", label: "Incidents" },
  { value: "alerts", label: "Alerts" },
  { value: "recs", label: "Recommendations" },
];

const STATUS_ITEMS = [
  { value: "open", label: "Open" },
  { value: "all", label: "All" },
];

/** The one inbox list: view tabs (with counts), a status filter, a client-side
 *  search box and the rows themselves. Incident rows expand inline to their
 *  `children`. */
export function InboxList({
  view,
  role,
  onViewChange,
  counts,
  status,
  onStatusChange,
  search,
  onSearchChange,
  rows,
  cursor,
  selected,
  expanded,
  pendingIds,
  recStatus,
  onToggleExpand,
  onToggleSelect,
  onRunAction,
  onRecDecision,
  onAskGuardian,
  className = "",
  ...rest
}: InboxListProps) {
  const countFor = (v: InboxView) =>
    v === "incidents" ? counts.incidents
    : v === "alerts" ? counts.alerts
    : counts.recs;

  return (
    <div className={`g-inbox-list ${className}`.trim()} {...rest}>
      <div className="g-inbox-toolbar">
        <Tabs
          label="Inbox filter"
          items={VIEW_ITEMS.map((item) => ({ ...item, count: countFor(item.value) }))}
          value={view}
          onChange={(v) => onViewChange(v as InboxView)}
        />
        <div className="g-inbox-toolbar-row">
          <Chips
            label="Status"
            items={STATUS_ITEMS}
            value={status}
            onChange={(v) => onStatusChange((v ?? "open") as InboxStatusFilter)}
          />
          <input
            type="search"
            aria-label="Search inbox"
            placeholder="Search…"
            value={search}
            onChange={(e) => onSearchChange(e.target.value)}
          />
        </div>
      </div>

      {rows.length === 0 ? (
        <EmptyState title={EMPTY_COPY[view].title} body={EMPTY_COPY[view].body} />
      ) : (
        <ul className="g-inbox-rows" aria-label="Inbox rows">
          {rows.map((row, i) => (
            <Fragment key={row.id}>
              <InboxRowView
                row={row}
                role={role}
                isCursor={i === cursor}
                isSelected={selected.has(row.id)}
                isExpanded={expanded.has(row.id)}
                pending={pendingIds.has(row.id)}
                recStatus={recStatus[row.id]}
                onToggleExpand={() => onToggleExpand(row.id)}
                onToggleSelect={() => onToggleSelect(row.id)}
                onRunAction={(action) => onRunAction(row, action)}
                onRecDecision={(accept) => onRecDecision(row, accept)}
                onAskGuardian={() => onAskGuardian(row)}
              />
              {expanded.has(row.id) &&
                row.children.map((child) => (
                  <InboxRowView
                    key={child.id}
                    row={child}
                    role={role}
                    isCursor={false}
                    isSelected={selected.has(child.id)}
                    // A child alert has its own row in `expanded` and its own
                    // real toggle, where both used to be hardcoded inert. It
                    // reveals only its confidence working: a child's own
                    // `children` are never rendered (an incident's members are
                    // alerts, not further incidents), so there is nothing else
                    // for this to open. Left inert, the score control would
                    // render on every child alert and do nothing when clicked.
                    isExpanded={expanded.has(child.id)}
                    pending={pendingIds.has(child.id)}
                    recStatus={recStatus[child.id]}
                    onToggleExpand={() => onToggleExpand(child.id)}
                    onToggleSelect={() => onToggleSelect(child.id)}
                    onRunAction={(action) => onRunAction(child, action)}
                    onRecDecision={() => {}}
                    onAskGuardian={() => onAskGuardian(child)}
                    depth={1}
                  />
                ))}
            </Fragment>
          ))}
        </ul>
      )}
    </div>
  );
}

export default InboxList;
