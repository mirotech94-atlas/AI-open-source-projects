/**
 * The Agency tab is the agency map alone (Miro, 2026-10-28): every roster
 * agent is a neuron, a failed roster read is UNKNOWN, the `sites` neuron
 * carries the preview cards waiting on Miro, and none of the desk sections
 * that moved to the Command Center are drawn here.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { AgentSummary, Approvals, Proposal } from "../api";
import { AgencyMapBody } from "./AgencyTab";
import { previewBadge } from "./agencyMapModel";
import type { Read } from "./homeModel";

const NOW = Date.parse("2026-10-28T12:00:00Z");
const AT = new Date(NOW);

function agent(name: string, group: AgentSummary["group"] = "functions"): AgentSummary {
  return {
    name, title: name, display: name, brief: `${name} brief`, need: null, available: true,
    providers: [], tools: 1, last_run_at: null, last_outcome: null, runs_24h: 0,
    model: null, model_at: null, handoff: [], memory_scope: [], ready: true, reasons: [],
    group, next_shift_at: null,
  };
}

function card(id: number, action: string, expires = "2026-10-29T12:00:00Z"): Proposal {
  return {
    id, action, params: {}, reason: "", tier: 2, tier_reason: "", status: "pending",
    confidence: null, rollback_plan: "", evidence: {}, created_at: "2026-10-28T11:00:00Z",
    expires_at: expires, dry_run: null, dry_run_ok: null, dry_run_at: null, finding: null,
  };
}

const ok = <T,>(data: T): Read<T> => ({ data, error: null, fetchedAt: AT });
const failed = <T,>(error: string): Read<T> => ({ data: null, error, fetchedAt: AT });
const approvalsOf = (proposals: Proposal[]): Read<Approvals> =>
  ok({ proposals, watch_only: true } as unknown as Approvals);

const ROSTER = ["inbox", "sites", "sales", "content", "bizops", "seo", "research", "qa"];

function render(roster: Read<{ agents: AgentSummary[] }>, approvals: Read<Approvals>): string {
  return renderToStaticMarkup(createElement(AgencyMapBody, { roster, approvals, now: NOW }));
}

describe("Agency tab: the map alone", () => {
  it("draws every agent on the roster", () => {
    const agents = ROSTER.map((n, i) => agent(n, i === 0 ? "lead" : "functions"));
    const html = render(ok({ agents }), approvalsOf([]));
    for (const n of ROSTER) expect(html, n).toContain(`data-agent="${n}"`);
    expect((html.match(/data-agent="/g) ?? [])).toHaveLength(ROSTER.length);
    expect(html).toContain("8 agents");
  });

  it("draws exactly the eight kept agents, never a retired name the server still sent", () => {
    const agents = [...ROSTER, "concierge", "scheduler", "agency"]
      .map((n, i) => agent(n, i === 0 ? "lead" : "functions"));
    const html = render(ok({ agents }), approvalsOf([]));
    expect((html.match(/data-agent="/g) ?? [])).toHaveLength(8);
    for (const n of ["concierge", "scheduler", "agency"]) {
      expect(html, n).not.toContain(`data-agent="${n}"`);
    }
    expect(html).toContain("8 agents");
  });

  it("draws none of the desk sections that moved to the Command Center", () => {
    const html = render(ok({ agents: [agent("sites")] }), approvalsOf([]));
    for (const id of ["clients", "projects", "decisions", "calendar", "reports", "team",
                      "settings", "remediation"]) {
      expect(html, id).not.toContain(`id="${id}"`);
    }
    expect(html).not.toContain("desk-top");
    expect(html).not.toContain("cmd-seat");
  });

  it("a roster that cannot be read is Unknown, never an empty OK map", () => {
    const html = render(failed("502"), approvalsOf([]));
    expect(html).toContain("cmd-word-unknown");
    expect(html).toContain("data-read-failed");
    expect(html).toContain("Unknown: the roster could not be read.");
  });

  it("the sites neuron carries the preview cards waiting on Miro", () => {
    const html = render(ok({ agents: [agent("sites"), agent("sales")] }), approvalsOf([
      card(1, "agency.send_outreach"), card(2, "agency.send_outreach"),
      card(3, "agency.deploy_demo"), card(4, "sales.qualify"),
    ]));
    const sites = html.slice(html.indexOf('data-agent="sites"'));
    expect(sites).toContain("3 previews to approve");
    expect((html.match(/nn-badge/g) ?? [])).toHaveLength(1);
  });
});

describe("previewBadge", () => {
  it("counts pending, unexpired preview cards only", () => {
    const b = previewBadge(approvalsOf([
      card(1, "agency.send_outreach"),
      card(2, "agency.send_outreach", "2026-10-28T10:00:00Z"),
      card(3, "agency.send_reply"),
    ]), NOW);
    expect(b.label).toBe("1 preview to approve");
    expect(b.unknown).toBeUndefined();
  });

  it("zero is a read zero, with its read time", () => {
    const b = previewBadge(approvalsOf([]), NOW);
    expect(b.label).toBe("0 previews to approve");
    expect(b.title).toMatch(/read \d\d:\d\d/);
  });

  it("a failed read is Unknown, never 0", () => {
    const b = previewBadge(failed("timeout"), NOW);
    expect(b.unknown).toBe(true);
    expect(b.label).toContain("Unknown");
    expect(b.label).not.toContain("0");
  });

  it("a read not back yet is not a count", () => {
    const b = previewBadge({ data: null, error: null, fetchedAt: null }, NOW);
    expect(b.unknown).toBe(true);
    expect(b.label).not.toMatch(/\d/);
  });
});
