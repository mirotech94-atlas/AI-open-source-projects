import type { Lead } from "../api";

/**
 * Pure logic behind the Leads tab (Task 62): the chips, the search box, the
 * sortable columns, the client-side page, the row drawer's open/close, and
 * the CSV export -- all as plain functions over the rows `/api/leads`
 * already returned, so none of it needs a DOM to test (the app has no
 * component-rendering test library, `fleetModel.ts`/`marketingModel.ts`
 * being the established pattern for that).
 *
 * The server (`atlas/api/dashboard.py`) accepts `q`/`status`/`sort` too, for
 * any other caller and for the invariants that only it can enforce (a
 * status the schema does not have, an expired proposal read as "waiting").
 * This page applies them again here, client-side, against the whole table
 * it already holds (the Places budget caps `agency_leads` at a few hundred
 * rows a month, so "the whole table" is never a lot to hold) -- chips and
 * search read as instant, not as a network round trip on every keystroke.
 */

// The header's chips, in a fixed order so a chip does not jump around as
// leads move through the pipeline. Covers every `agency_leads.status` value
// the schema declares, plus the outreach pipeline's own derived states.
export const CHIP_ORDER: readonly string[] = [
  "new", "audited", "mocked", "contacted", "closed", "dropped",
  "drafted", "waiting on you", "sent", "replied", "bounced",
];

export interface Chip {
  key: string;
  label: string;
  n: number;
}

/** `counts` as chips, known statuses first in a fixed order, then anything
 *  the server reports that this page has never heard of -- appended, never
 *  dropped, since a status the DB actually has must never disappear. */
export function buildChips(counts: Record<string, number>): Chip[] {
  const known = CHIP_ORDER.filter((key) => key in counts)
    .map((key) => ({ key, label: key, n: counts[key] ?? 0 }));
  const extra = Object.keys(counts)
    .filter((key) => !CHIP_ORDER.includes(key))
    .sort()
    .map((key) => ({ key, label: key, n: counts[key] ?? 0 }));
  return [...known, ...extra];
}

/** One flat filter over two columns: a lead's own status, or the outreach
 *  state derived from its latest draft -- the same either/or the server
 *  applies (`atlas/api/dashboard.py::_matches_status`). */
export function matchesStatus(lead: Lead, status: string | null): boolean {
  if (!status) return true;
  return lead.status === status || lead.outreach_state === status;
}

export function matchesSearch(lead: Lead, q: string): boolean {
  const needle = q.trim().toLowerCase();
  if (!needle) return true;
  const haystack = [
    lead.business, lead.category, lead.address, lead.contact.phone,
    lead.contact.site, lead.contact.email, lead.audit_finding,
  ].filter((v): v is string => Boolean(v)).join(" ").toLowerCase();
  return haystack.includes(needle);
}

export function filterLeads(
  leads: readonly Lead[], q: string, status: string | null,
): Lead[] {
  return leads.filter((lead) => matchesSearch(lead, q) && matchesStatus(lead, status));
}

export type SortKey = "business" | "source" | "status" | "score" | "last_touch" | "next_step";
export type SortDir = "asc" | "desc";

export const SORT_COLUMNS: ReadonlyArray<{ key: SortKey; label: string }> = [
  { key: "business", label: "Business" },
  { key: "source", label: "Source" },
  { key: "status", label: "Status" },
  { key: "score", label: "Score" },
  { key: "last_touch", label: "Last touch" },
  { key: "next_step", label: "Next step" },
];

const SORT_ACCESSORS: Record<SortKey, (lead: Lead) => string | number> = {
  business: (l) => l.business.toLowerCase(),
  source: (l) => l.source.toLowerCase(),
  status: (l) => l.status.toLowerCase(),
  // Unranked is not a zero -- it is lower than every real score there is,
  // so it settles at whichever end "lowest" lands on for the direction
  // chosen, rather than reading as the worst lead there is either way.
  score: (l) => (l.score === null ? -1 : l.score),
  last_touch: (l) => l.last_touch_at ?? "",
  next_step: (l) => l.next_step?.card_id ?? 0,
};
const DEFAULT_DESCENDING = new Set<SortKey>(["score", "last_touch", "next_step"]);

/** Which way a column reads the first time it is clicked: newest/highest
 *  first for score, last touch and next step; alphabetical for the rest. */
export function defaultSortDir(key: SortKey): SortDir {
  return DEFAULT_DESCENDING.has(key) ? "desc" : "asc";
}

/** Stable: two leads that tie on the sort key keep a fixed order (highest
 *  id first) rather than reshuffling between renders. */
export function sortLeads(
  leads: readonly Lead[], sort: SortKey | null, dir: SortDir | null,
): Lead[] {
  if (!sort) {
    return [...leads].sort((a, b) => {
      const at = a.last_touch_at ?? "", bt = b.last_touch_at ?? "";
      if (at !== bt) return at < bt ? 1 : -1;
      return b.id - a.id;
    });
  }
  const accessor = SORT_ACCESSORS[sort];
  const reverse = dir ? dir === "desc" : DEFAULT_DESCENDING.has(sort);
  return [...leads].sort((a, b) => {
    const av = accessor(a), bv = accessor(b);
    const cmp = av === bv ? a.id - b.id : (av < bv ? -1 : 1);
    return reverse ? -cmp : cmp;
  });
}

export function paginate<T>(rows: readonly T[], offset: number, limit: number): T[] {
  return rows.slice(offset, offset + limit);
}

/** A row click toggles its own drawer shut; clicking a different row swaps
 *  which one is open. Exactly what Reports.tsx already does inline for its
 *  own expandable rows -- pulled out here only because Leads has more than
 *  one place that needs to ask "is this the open one". */
export function toggleDrawer(openId: number | null, clicked: number): number | null {
  return openId === clicked ? null : clicked;
}

const CSV_HEADERS = [
  "Business", "Category", "Source", "Status", "Score", "Last touch",
  "Next step", "Phone", "Email", "Website", "Address",
] as const;

function csvCell(value: string | number | null | undefined): string {
  const text = value === null || value === undefined ? "" : String(value);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

/** The export button's entire job: no request leaves the browser for this,
 *  because the rows are already on screen (invariant 1 has nothing to do
 *  with a read that never crosses back out). */
export function leadsToCsv(leads: readonly Lead[]): string {
  const lines = [CSV_HEADERS.join(",")];
  for (const lead of leads) {
    lines.push([
      lead.business, lead.category, lead.source, lead.status,
      lead.score === null ? "" : lead.score.toFixed(2),
      lead.last_touch_at ?? "", lead.next_step?.label ?? "",
      lead.contact.phone ?? "", lead.contact.email ?? "", lead.website ?? "",
      lead.address ?? "",
    ].map(csvCell).join(","));
  }
  return lines.join("\n");
}
