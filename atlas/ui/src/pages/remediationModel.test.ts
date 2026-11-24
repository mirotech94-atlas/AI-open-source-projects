/**
 * Remediation tasks (GET /api/remediation, the QA agent's board rows): open
 * first, newest first; a count per owner that is null, never 0, when the
 * board was not read; projects per client with their open task counts.
 */
import { describe, expect, it } from "vitest";
import {
  openCountFor, projectRows, remediationRows, type ProjectRow, type RemediationTask,
} from "./remediationModel";

function task(id: number, over: Partial<RemediationTask> = {}): RemediationTask {
  return {
    id, project_id: null, title: `Fix ${id}`, owner: "qa", status: "todo",
    evidence: { url: "https://agency.storefront.test/preview/joes/" }, suggested_fix: "shorten the title",
    source_action: "qa.check_site", source_ref: "site:joes", due_at: null,
    created_at: `2026-10-27T0${id % 10}:00:00Z`, done_at: null, open: true, ...over,
  };
}

describe("remediationRows", () => {
  it("puts open tasks first, newest first, and keeps at most ten closed", () => {
    const tasks = [
      task(1), task(3), task(2, { status: "done", open: false, done_at: "2026-10-27T05:00:00Z" }),
      ...Array.from({ length: 12 }, (_, i) =>
        task(20 + i, { status: "done", open: false })),
    ];
    const { open, closed } = remediationRows(tasks);
    expect(open.map((r) => r.id)).toEqual([3, 1]);
    expect(closed).toHaveLength(10);
    expect(open[0]).toMatchObject({ owner: "qa", source: "qa.check_site · site:joes",
                                    fix: "shorten the title", createdAt: "2026-10-27T03:00:00Z" });
  });

  it("never prints a retired lane's name", () => {
    const { open } = remediationRows([task(1, { title: "tiktok token expired",
                                                suggested_fix: "renew the tiktok token" })]);
    expect(JSON.stringify(open)).not.toMatch(/tiktok/i);
  });
});

describe("openCountFor", () => {
  it("counts open tasks owned by any of the owners", () => {
    const tasks = [task(1), task(2, { owner: "seo" }), task(3, { owner: "seo", open: false }),
                   task(4, { owner: "sales" })];
    expect(openCountFor(["qa"], tasks)).toBe(1);
    expect(openCountFor(["seo", "x"], tasks)).toBe(1);
    expect(openCountFor(["bizops"], tasks)).toBe(0);
  });

  it("is null, not zero, when the board was not read", () => {
    expect(openCountFor(["qa"], null)).toBeNull();
  });
});

describe("projectRows", () => {
  it("groups projects by client, active first, with open task counts", () => {
    const rows: ProjectRow[] = [
      { id: 1, client_lead_id: 7, client_name: "Joe's Bakery", name: "Site", status: "active",
        kickoff_at: null, created_at: null, updated_at: "2026-10-27T08:00:00Z", open_tasks: 2 },
      { id: 2, client_lead_id: 7, client_name: "Joe's Bakery", name: "SEO", status: "done",
        kickoff_at: null, created_at: null, updated_at: "2026-10-27T09:00:00Z", open_tasks: 0 },
      { id: 3, client_lead_id: null, client_name: null, name: "Internal", status: "active",
        kickoff_at: null, created_at: null, updated_at: "2026-10-27T07:00:00Z", open_tasks: 1 },
    ];
    const clients = projectRows(rows);
    expect(clients.map((c) => c.client)).toEqual(["Joe's Bakery", "No client"]);
    expect(clients[0]!.projects.map((p) => [p.name, p.status, p.openTasks]))
      .toEqual([["Site", "active", 2], ["SEO", "done", 0]]);
    expect(projectRows([])).toEqual([]);
  });
});
