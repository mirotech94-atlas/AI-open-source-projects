/**
 * The project board's reads (GET /api/projects, GET /api/remediation) as
 * pure functions. No DOM, no fetch.
 *
 * - A count of a board that was not read is null, never 0.
 * - Free text passes through `safeText`: a retired lane is never named.
 */
import { safeText } from "./retired";

export interface RemediationTask {
  id: number;
  project_id: number | null;
  title: string;
  owner: string;
  status: string;
  evidence: unknown;
  suggested_fix: string | null;
  source_action: string | null;
  source_ref: string | null;
  due_at: string | null;
  created_at: string | null;
  done_at: string | null;
  open: boolean;
}

export interface RemediationRead {
  served_at: string;
  open: number;
  tasks: RemediationTask[];
}

export interface ProjectRow {
  id: number;
  client_lead_id: number | null;
  client_name: string | null;
  name: string;
  status: string;
  kickoff_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  open_tasks: number;
}

export interface ProjectsRead {
  served_at: string;
  projects: ProjectRow[];
}

export interface RemediationRowView {
  id: number;
  title: string;
  owner: string;
  status: string;
  open: boolean;
  fix: string;
  source: string;
  createdAt: string | null;
}

export const CLOSED_LIMIT = 10;

function time(at: string | null): number {
  const t = at ? Date.parse(at) : NaN;
  return Number.isNaN(t) ? -1e15 : t;
}

function view(t: RemediationTask): RemediationRowView {
  return {
    id: t.id, title: safeText(t.title), owner: safeText(t.owner), status: t.status,
    open: t.open, fix: safeText(t.suggested_fix ?? ""),
    source: [t.source_action, t.source_ref].filter(Boolean).map((x) => safeText(x)).join(" · "),
    createdAt: t.created_at,
  };
}

/** Open tasks first, newest first; at most ten closed ones. */
export function remediationRows(
  tasks: readonly RemediationTask[],
): { open: RemediationRowView[]; closed: RemediationRowView[] } {
  const newest = [...tasks].sort((a, b) => time(b.created_at) - time(a.created_at));
  return {
    open: newest.filter((t) => t.open).map(view),
    closed: newest.filter((t) => !t.open).slice(0, CLOSED_LIMIT).map(view),
  };
}

/** Open tasks owned by any of `owners`; null when the board was not read. */
export function openCountFor(
  owners: readonly string[], tasks: readonly Pick<RemediationTask, "owner" | "open">[] | null,
): number | null {
  if (tasks === null) return null;
  return tasks.filter((t) => t.open && owners.includes(t.owner)).length;
}

export interface ClientProjects {
  client: string;
  projects: { id: number; name: string; status: string; openTasks: number;
              updatedAt: string | null }[];
}

const STATUS_RANK: Record<string, number> = { active: 0, paused: 1, done: 2 };

/** Projects grouped per client (named clients first, A-Z), active first. */
export function projectRows(rows: readonly ProjectRow[]): ClientProjects[] {
  const by = new Map<string, ClientProjects>();
  for (const r of rows) {
    const client = r.client_name?.trim() ? safeText(r.client_name.trim()) : "No client";
    const entry = by.get(client) ?? { client, projects: [] };
    entry.projects.push({ id: r.id, name: safeText(r.name), status: r.status,
                          openTasks: r.open_tasks, updatedAt: r.updated_at });
    by.set(client, entry);
  }
  const out = [...by.values()];
  for (const c of out) {
    c.projects.sort((a, b) => (STATUS_RANK[a.status] ?? 3) - (STATUS_RANK[b.status] ?? 3)
      || time(b.updatedAt) - time(a.updatedAt));
  }
  return out.sort((a, b) => (a.client === "No client" ? 1 : 0) - (b.client === "No client" ? 1 : 0)
    || a.client.localeCompare(b.client));
}
