/**
 * The Tasks tab: its pure model, and the page to static markup (no jsdom,
 * like Brain.test.ts). Busy seats first; unknown reads UNKNOWN and never 0;
 * the filter matches a person, a department or a module; the strip links
 * WAITING ON YOU to the approvals; the nightshift table sits with Marcus.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { Board, BoardAgent, BoardRow } from "../api";
import { TasksContent, TasksView } from "./Tasks";
import {
  cardLine, countText, filterAgents, jobLastRun, left, nightshiftRows, orderAgents,
  progressPct, progressText, recentLine, rowKind, rowMeta, startsOpen, totalsLine,
} from "./tasksModel";

function row(over: Partial<BoardRow> & { kind: BoardRow["kind"]; id: number }): BoardRow {
  return { status: "running", summary: "", started: "2026-09-29T08:00:00+00:00",
           updated: "2026-09-29T09:00:00+00:00", finished: null, ...over };
}

function agent(over: Partial<BoardAgent> & { name: string; label: string }): BoardAgent {
  return {
    title: over.name, department: "sales", department_name: "Sales & Course",
    head: false, head_label: "Nora", modules: [], oversees: [], in_flight: [],
    waiting: [], jobs: [], recent: { missions: [], nightshift: [], outbox: [],
                                     executions: {}, verify: {} },
    progress: [], paused: [], unread: [], counts: { in_flight: 0, waiting: 0 },
    headline: "nothing in hand; idle in the last 7d", ...over,
  };
}

const hazel = agent({
  name: "agency", label: "Hazel", title: "Agency", modules: ["agency"],
  in_flight: [
    row({ kind: "mission", id: 1, recipe: "agency.sites", phase: "build", steps: 9,
          summary: "build 50 websites", blocked: [],
          progress: { done: 12, total: 50, unit: "sites" } }),
    row({ kind: "outbox", id: 4, action: "agency.deploy_site", status: "pending",
          attempts: 0, max_attempts: 5, summary: "deploy acme" }),
  ],
  waiting: [{ id: 7, action: "agency.send_outreach", tier: 2, reason: "first email",
              created_at: null, expires_at: "2026-09-29T12:00:00+00:00" }],
  progress: [{ module: "agency", read_at: "2026-09-29T09:00:00+00:00", error: null,
               oversight: false,
               figures: [{ label: "sites built", value: 12, read_at: "x", note: "" },
                         { label: "sites live", value: null, read_at: "x", note: "" }] }],
  paused: [{ scope: "agency", says: "the agency module is paused", shadow: false }],
  counts: { in_flight: 2, waiting: 1 },
  headline: "mission #1 agency.sites: 12/50 sites, running",
});
const marcus = agent({
  name: "ops", label: "Marcus", title: "Operations", department: "ops",
  department_name: "Ops & Infra", head: true, head_label: "Marcus",
  modules: ["nightshift", "cloudco"],
  in_flight: [row({ kind: "nightshift", id: 3, summary: "add the board", repo: "the owner/ai",
                    branch: "board", preview_url: null })],
  recent: { missions: [], outbox: [],
            nightshift: [row({ kind: "nightshift", id: 2, status: "done", summary: "old",
                               repo: "the owner/ai", branch: "x", finished: "2026-09-28T02:00:00+00:00" })],
            executions: { "cloudco.snapshot": { ok: 3, failed: 1 } }, verify: { verified: 2 } },
  jobs: [{ name: "checks", when: "every cycle", layer: "automation", recorded: false,
           last_run: null, last_run_known: false, next_due: null }],
  counts: { in_flight: 1, waiting: 0 }, headline: "1 nightshift job(s) queued or running",
});
const ivan = agent({ name: "markets", label: "Ivan", department: "money",
                     department_name: "Money & Markets", modules: ["markets"] });
const unknownSeat = agent({
  name: "inbox", label: "Clara", department: "admin", department_name: "Admin & Comms",
  waiting: null, unread: ["missions", "proposals"], counts: { in_flight: null, waiting: null },
  headline: "unknown: its tables could not be read",
});
const you = agent({ name: "you", label: "You", title: "sign-off", department: "strategy",
                    department_name: "Strategy", head: true, head_label: "You",
                    waiting: [{ id: 9, action: "retired.thing", tier: 2, reason: "stray",
                                created_at: null, expires_at: null }],
                    counts: { in_flight: 0, waiting: 1 }, headline: "1 card(s) waiting on the owner" });

function board(agents: BoardAgent[] = [ivan, hazel, unknownSeat, marcus, you]): Board {
  return {
    as_of: "2026-09-29T09:05:00+00:00", read_at: "2026-09-29T09:05:00+00:00",
    since: "2026-09-22T09:05:00+00:00", window_days: 7,
    totals: { waiting_on_you: 2, in_flight: 3, agents_busy: 2, paused: 1 },
    agents, errors: {},
  };
}

function render(b: Board, filter = ""): string {
  return renderToStaticMarkup(createElement(MemoryRouter, null,
    createElement(TasksView, { board: b, filter, now: Date.parse("2026-09-29T09:05:00+00:00") })));
}

describe("tasksModel", () => {
  it("puts seats with work in hand first, then waiting, then roster order", () => {
    expect(orderAgents(board().agents).map((a) => a.name))
      .toEqual(["agency", "ops", "you", "markets", "inbox"]);
  });

  it("says unknown, never 0", () => {
    expect(countText(null)).toBe("unknown");
    expect(countText(0)).toBe("0");
    expect(progressPct({ done: null, total: 50, unit: "sites" })).toBeNull();
    expect(progressPct({ done: 12, total: 50, unit: "sites" })).toBe(24);
    expect(progressPct({ done: 3, total: 0, unit: "x" })).toBeNull();
    expect(progressText({ done: null, total: 50, unit: "sites" })).toBe("?/50 sites");
    expect(jobLastRun(marcus.jobs[0]!)).toMatch(/no per-run record/);
    expect(jobLastRun({ ...marcus.jobs[0]!, recorded: true })).toBe("last run unknown");
    expect(jobLastRun({ ...marcus.jobs[0]!, recorded: true, last_run_known: true }))
      .toBe("never recorded");
  });

  it("filters by person, department or module", () => {
    const all = board().agents;
    expect(filterAgents(all, "hazel").map((a) => a.name)).toEqual(["agency"]);
    expect(filterAgents(all, "Ops").map((a) => a.name)).toEqual(["ops"]);
    expect(filterAgents(all, "cloudco").map((a) => a.name)).toEqual(["ops"]);
    expect(filterAgents(all, "money").map((a) => a.name)).toEqual(["markets"]);
    expect(filterAgents(all, "")).toHaveLength(5);
    expect(filterAgents(all, "zzz")).toHaveLength(0);
  });

  it("opens the busy and the unknown, folds the idle", () => {
    expect(startsOpen(hazel)).toBe(true);
    expect(startsOpen(unknownSeat)).toBe(true);
    expect(startsOpen(ivan)).toBe(false);
  });

  it("words a row, a card, the window and the strip", () => {
    expect(rowKind(hazel.in_flight[0]!)).toBe("mission #1 agency.sites · build");
    expect(rowMeta(hazel.in_flight[1]!)).toMatch(/^pending · attempt 0\/5 · started/);
    expect(cardLine(hazel.waiting![0]!, Date.parse("2026-09-29T09:05:00+00:00")))
      .toBe("#7 agency.send_outreach · Tier 2 · 2h 55m");
    expect(left("2026-09-29T09:05:30+00:00", Date.parse("2026-09-29T09:05:00+00:00"))).toBe("<1m");
    expect(left("2026-09-29T09:04:00+00:00", Date.parse("2026-09-29T09:05:00+00:00"))).toBe("expired");
    expect(recentLine(marcus, 7))
      .toBe("1 nightshift finished · 4 actions ran, 1 failed · read-backs verified 2");
    expect(recentLine(ivan, 7)).toBe("nothing in the last 7 days");
    expect(totalsLine(board())).toBe("3 in flight · 2 of 5 seats busy · 1 paused");
    expect(nightshiftRows(marcus).map((r) => r.id)).toEqual([3, 2]);
  });
});

describe("TasksView", () => {
  it("shows decisions by soonest expiry across seats", () => {
    const urgent = agent({ ...marcus, waiting: [{
      id: 8, action: "cloudco.snapshot", tier: 2, reason: "backup",
      created_at: null, expires_at: "2026-09-29T09:30:00+00:00",
    }], counts: { in_flight: 1, waiting: 1 } });
    const html = render(board([hazel, urgent, you]));
    const cards = html.slice(html.indexOf('<div class="panel wb-waiting"'));
    const positions = ["#8 cloudco.snapshot", "#7 agency.send_outreach", "#9 retired.thing"]
      .map((text) => cards.indexOf(text));
    expect(positions.every((at) => at >= 0)).toBe(true);
    expect(positions).toEqual([...positions].sort((a, b) => a - b));
  });

  it("leaves expired decisions after ones that can still be answered", () => {
    const expired = agent({ ...marcus, waiting: [{
      id: 8, action: "cloudco.snapshot", tier: 2, reason: "backup",
      created_at: null, expires_at: "2026-09-29T08:00:00+00:00",
    }], counts: { in_flight: 1, waiting: 1 } });
    const html = render(board([hazel, expired]));
    const cards = html.slice(html.indexOf('<div class="panel wb-waiting"'));
    expect(cards.indexOf("#7 agency.send_outreach")).toBeLessThan(cards.indexOf("#8 cloudco.snapshot"));
  });

  it("marks a failed board refresh unknown instead of showing cached counts", () => {
    const html = renderToStaticMarkup(createElement(TasksContent,
      { board: board(), error: "HTTP 502", filter: "", now: 0 }));
    expect(html).toContain("Could not read the work board: HTTP 502");
    expect(html).toContain("UNKNOWN");
    expect(html).not.toContain('class="bn-strip wb-strip"');
    expect(html).not.toContain("#7 agency.send_outreach");
  });

  it("renders the strip, one section per seat in busy-first order, read-only", () => {
    const html = render(board());
    expect(html).toContain('href="#wb-waiting"');
    expect(html).toMatch(/Waiting on you[\s\S]*?>2</);
    const order = ["Hazel", "Marcus", "You", "Ivan", "Clara"]
      .map((n) => html.indexOf(`<span class="wb-name">${n}</span>`));
    expect(order.every((i) => i >= 0)).toBe(true);
    expect([...order].sort((a, b) => a - b)).toEqual(order);
    expect(html).not.toMatch(/<button/);
    expect(html).not.toMatch(/<form/);
  });

  it("draws a progress bar from done/total, and a dashed unknown without", () => {
    const html = render(board());
    expect(html).toContain('class="wb-bar"');
    expect(html).toContain("width:24%");
    expect(html).toContain("12/50 sites");
    // the figure that could not be read is the word, in the unknown treatment
    expect(html).toMatch(/data-unknown="true"[^>]*>sites live unknown</);
  });

  it("marks an unread seat UNKNOWN with the tables it could not read", () => {
    const html = render(board());
    expect(html).toMatch(/Clara[\s\S]*?UNKNOWN[\s\S]*?could not read: missions, proposals/);
    expect(html).not.toMatch(/Clara[\s\S]{0,400}>0 in flight/);
  });

  it("keeps the nightshift table inside Marcus's section", () => {
    const html = render(board());
    const marcusAt = html.indexOf('data-agent="ops"');
    const nextAt = html.indexOf("<details", marcusAt + 1);
    const table = html.indexOf("wb-nightshift", marcusAt);
    expect(marcusAt).toBeGreaterThan(-1);
    expect(table).toBeGreaterThan(marcusAt);
    expect(nextAt === -1 || table < nextAt).toBe(true);
    expect(html).toContain("add the board");
    expect(html).toContain('<td class="wb-wide">the owner/ai</td>');
  });

  it("filters the sections and says so when nothing matches", () => {
    expect(render(board(), "ivan")).toContain("Ivan");
    expect(render(board(), "ivan")).not.toContain("wb-name\">Hazel");
    expect(render(board(), "nobody")).toContain("No seat matches");
  });

  it("names the pause and the head", () => {
    const html = render(board());
    expect(html).toContain("the agency module is paused");
    expect(html).toMatch(/Hazel[\s\S]*?head Nora/);
    expect(html).toMatch(/Marcus[\s\S]*?head of Ops &amp; Infra/);
  });
});
