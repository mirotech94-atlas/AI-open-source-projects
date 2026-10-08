/**
 * Home's rules: a failed read is UNKNOWN (never the read before it), a
 * missing or stale check is UNKNOWN with its reason, the diary past its
 * 72-hour read is "not read" (never an empty day), and UNKNOWN reaches the
 * DOM with its word and a treatment of its own.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { StaticRouter } from "react-router-dom/server";
import { describe, expect, it } from "vitest";
import type { Agenda, Board, BoardAgent, CheckSummary, ModuleTile, WorkflowGraph } from "../api";
import { Day, FigureTile } from "./Home";
import {
  busySeats, flowRows, healthLine, healthSummary, jobsDue, leadClause, marketingFigures,
  readOf, salesLines, waitingCards, weekDays, WEEK_HOURS,
} from "./homeModel";

const READ = "2026-10-06T08:00:00Z";

function check(over: Partial<CheckSummary> = {}): CheckSummary {
  return {
    name: "x", health: "ok", detail: "", observed_at: READ, next_run_at: null,
    interval_s: 3600, stale: false, state: null, ...over,
  };
}

function mod(module: string, checks: CheckSummary[], over: Partial<ModuleTile> = {}): ModuleTile {
  return {
    module, title: module, health: "ok", checks, last_check_at: READ, next_check_at: null,
    enabled: true, latest_finding: null, setup: null, ...over,
  };
}

function agent(over: Partial<BoardAgent> = {}): BoardAgent {
  return {
    name: "ops", label: "Marcus", title: "Operations", department: "ops",
    department_name: "Operations", head: true, head_label: "Marcus", modules: [],
    oversees: [], in_flight: [], waiting: [], jobs: [],
    recent: { missions: [], nightshift: [], outbox: [], executions: {}, verify: {} },
    progress: [], paused: [], unread: [], counts: { in_flight: 0, waiting: 0 },
    headline: "nothing in hand", ...over,
  };
}

function board(agents: BoardAgent[], waiting: number | null = 0): Board {
  return {
    as_of: READ, read_at: READ, since: READ, window_days: 7,
    totals: { waiting_on_you: waiting, in_flight: 0, agents_busy: 0, paused: 0 },
    agents, errors: {},
  };
}

describe("readOf", () => {
  it("drops the old data when the latest read failed", () => {
    expect(readOf({ data: 5, error: "503", fetchedAt: new Date() })).toEqual({ data: null, error: "503" });
    expect(readOf({ data: 5, error: null, fetchedAt: new Date() })).toEqual({ data: 5, error: null });
  });
});

describe("marketing figures", () => {
  const modules = [
    mod("content", [check({ name: "reel_metrics", value: 5, unit: "reel posts, 30 d",
                            detail: "read 5 of 5 posted reel(s) (read 08:00 UTC)" })]),
    mod("clips", [
      check({ name: "clips_posted", value: 4, unit: "clips posted, all time" }),
      check({ name: "clips_earnings", stale: true, value: 2, unit: "clips submitted",
              detail: "4 clip(s) posted, 2 submitted to Whop, 2 awaiting submission" }),
    ]),
    mod("shop", [
      check({ name: "sales_7d", value: 77, unit: "usd",
              detail: "Course A: 2 sale(s), $58.00; Kit B: 1 sale(s), $19.00 (last 7 days)" }),
      check({ name: "sales_count_7d", health: "unknown", value: 3, unit: "sales, 7 d",
              detail: "Stripe answered 401 for Course A" }),
    ]),
  ];
  const parts = (i: number) => marketingFigures(modules, null)[i]?.parts ?? [];

  it("shows the check's own value and unit with its read time, never a number from words", () => {
    const [reels] = parts(0);
    expect(reels?.state).toBe("ok");
    expect(reels?.display).toEqual({ text: "5", unit: "reel posts, 30 d" });
    expect(reels?.text).toBe("");
    expect(reels?.readAt?.toISOString()).toBe(new Date(READ).toISOString());
    // An older server sends no value: the words, and no big number.
    const old = marketingFigures([mod("agency", [check({ name: "leads_waiting",
      detail: "4 lead(s) waiting on an audit or a decision" })])], null)[2]?.parts[0];
    expect(old?.display).toBeNull();
    expect(old?.text).toBe("4 lead(s) waiting on an audit or a decision");
  });

  it("a stale check is UNKNOWN, not its old figure, even though it carries one", () => {
    const [posted, submitted] = parts(1);
    expect(posted?.display?.text).toBe("4");
    expect(submitted?.state).toBe("unknown");
    expect(submitted?.value).toBeNull();
    expect(submitted?.display).toBeNull();
    expect(submitted?.text).toBe("the last reading is too old to count");
  });

  it("a missing check is UNKNOWN", () => {
    const [leads] = parts(2);
    expect(leads?.state).toBe("unknown");
    expect(leads?.text).toBe("this check is not reporting");
  });

  it("store sales: the count and the dollars are two checks, each with its own state", () => {
    const [count, dollars] = parts(3);
    expect(count?.state).toBe("unknown");
    expect(count?.value).toBeNull();
    expect(count?.text).toBe("Stripe answered 401 for Course A");
    expect(dollars?.display).toEqual({ text: "$77.00", unit: null });
    expect(marketingFigures(modules, null)[3]?.lines).toEqual(
      ["Course A: 2 sale(s), $58.00", "Kit B: 1 sale(s), $19.00"]);
  });

  it("a failed overview makes every number UNKNOWN with the reason", () => {
    const all = marketingFigures(null, "HTTP 502").flatMap((f) => f.parts);
    expect(all.every((p) => p.state === "unknown")).toBe(true);
    expect(all[0]?.text).toContain("HTTP 502");
  });

  it("cuts only the tail of a detail", () => {
    expect(leadClause("4 lead(s) waiting on an audit or a decision")).toBe(
      "4 lead(s) waiting on an audit or a decision");
    expect(leadClause("3 clip(s) posted, 1 submitted (slug: 3 posted); read 06 Oct")).toBe(
      "3 clip(s) posted, 1 submitted");
    expect(salesLines("no product on sale yet (last 7 days)")).toEqual(["no product on sale yet"]);
  });
});

describe("health summary", () => {
  it("ranks worst first and keeps set-aside modules out of the counts", () => {
    const s = healthSummary([
      mod("a", [], { health: "ok" }),
      mod("b", [], { health: "unknown" }),
      mod("c", [], { health: "failing" }),
      mod("d", [], { health: "unknown", setup: "not set up" }),
      mod("e", [], { health: "ok", enabled: false }),
    ]);
    expect(s.rows.map((r) => r.module)).toEqual(["c", "b", "a", "d"]);
    expect(s.counts).toEqual({ ok: 1, degraded: 0, failing: 1, unknown: 1 });
    expect(healthLine(s)).toBe("1 failing · 1 unknown · 1 OK · 1 not set up or paused");
  });

  it("an unreadable overview is unknown, never all clear", () => {
    const s = healthSummary(null);
    expect(s.known).toBe(false);
    expect(healthLine(s)).toContain("unknown");
  });

  it("an unrecognised health word is unknown", () => {
    expect(healthSummary([mod("a", [], { health: "great" })]).rows[0]?.health).toBe("unknown");
  });
});

describe("needs you", () => {
  const now = Date.parse("2026-10-06T09:00:00Z");
  const card = (id: number, expires_at: string | null) => ({
    id, action: "shop.create_payment_link", tier: 2, reason: "why",
    created_at: READ, expires_at,
  });

  it("soonest expiry first, already expired last", () => {
    const b = board([agent({ waiting: [
      card(1, "2026-10-06T08:00:00Z"), card(2, "2026-10-06T12:00:00Z"),
      card(3, "2026-10-06T10:00:00Z"), card(4, null),
    ] })], 4);
    expect(waitingCards(b, now)?.map((c) => c.id)).toEqual([3, 2, 4, 1]);
  });

  it("a card two seats hold is one decision", () => {
    const card = { id: 5, action: "a", tier: 2, reason: "r", created_at: READ,
                   expires_at: "2026-10-06T12:00:00Z" };
    const b = board([agent({ name: "x", label: "X", waiting: [card] }),
                     agent({ name: "y", label: "Y", waiting: [card] })], 1);
    expect(waitingCards(b, Date.parse(READ))?.map((c) => [c.id, c.who])).toEqual([[5, "X"]]);
  });

  it("is unknown when the proposals could not be read", () => {
    expect(waitingCards(board([agent()], null), now)).toBeNull();
    expect(waitingCards(null, now)).toBeNull();
  });
});

describe("the week", () => {
  const now = new Date(2026, 9, 6, 9, 0);       // Tue 6 Oct, 09:00 local
  const agenda: Agenda = {
    as_of: READ, unavailable: null, horizon_hours: 72,
    items: [
      { when: new Date(2026, 9, 6, 14, 0).toISOString(), title: "Call", kind: "event",
        detail: "", minutes: 30, source: "calendar", today: true },
      { when: new Date(2026, 9, 8, 17, 0).toISOString(), title: "Invoice due", kind: "deadline",
        detail: "", minutes: null, source: "billing", today: false },
    ],
  };

  it("marks the days past the 72-hour read as not read, never empty", () => {
    const days = weekDays({ now, agenda, agendaError: null, agendaReadAt: now, board: null });
    expect(days).toHaveLength(7);
    expect(days.map((d) => d.diary)).toEqual(
      ["read", "read", "read", "partial", "beyond", "beyond", "beyond"]);
    expect(days[3]?.until?.getHours()).toBe(9);
    expect(days[0]?.items.map((i) => i.title)).toEqual(["Call"]);
    expect(days[2]?.items.map((i) => i.kind)).toEqual(["deadline"]);
  });

  it("a failed diary read is unknown on every day, and its items are not shown", () => {
    const days = weekDays({ now, agenda, agendaError: "timeout", agendaReadAt: now, board: null });
    expect(days.every((d) => d.diary === "unknown")).toBe(true);
    expect(days.every((d) => d.items.length === 0)).toBe(true);
    const unavailable = weekDays({ now, agenda: { ...agenda, unavailable: "calendar down" },
                                   agendaError: null, agendaReadAt: now, board: null });
    expect(unavailable.every((d) => d.diary === "unknown")).toBe(true);
  });

  it("places jobs at the server's next_due and cards at their expiry", () => {
    const job = { name: "morning_brief", when: "daily 07:00", layer: "automation" as const,
                  recorded: true, last_run: null, last_run_known: true,
                  next_due: new Date(2026, 9, 7, 7, 0).toISOString() };
    const b = board([
      agent({ jobs: [job], waiting: [{ id: 9, action: "a", tier: 2, reason: "r",
                                        created_at: READ,
                                        expires_at: new Date(2026, 9, 6, 18, 0).toISOString() }] }),
      agent({ name: "x", jobs: [job] }),
    ], 1);
    const days = weekDays({ now, agenda, agendaError: null, agendaReadAt: now, board: b });
    expect(days[1]?.items.map((i) => i.title)).toEqual(["morning brief"]);
    expect(days[0]?.items.map((i) => i.kind)).toEqual(["event", "card"]);
    expect(jobsDue(b.agents)).toHaveLength(1);
  });
});

describe("workflows and agents", () => {
  const g = (id: string, outcome: WorkflowGraph["last_run"]["outcome"], at: string | null): WorkflowGraph => ({
    id, title: id, trigger: "cron", trigger_detail: "", nodes: [], edges: [],
    last_run: { at, outcome },
  });

  it("puts trouble first, then the latest run", () => {
    expect(flowRows([
      g("a", "ok", "2026-10-06T07:00:00Z"), g("b", "failed", "2026-10-05T07:00:00Z"),
      g("c", "ok", "2026-10-06T08:00:00Z"), g("d", "never", null),
    ]).map((r) => r.id)).toEqual(["b", "d", "c", "a"]);
  });

  it("lists busy seats with their headline, and an unknown count as unknown", () => {
    const seats = busySeats(board([
      agent({ name: "a", counts: { in_flight: 2, waiting: 0 }, headline: "two missions" }),
      agent({ name: "b" }),
      agent({ name: "c", counts: { in_flight: null, waiting: null } }),
    ]));
    expect(seats.map((s) => [s.name, s.inFlight])).toEqual([["a", 2], ["c", null]]);
  });
});

describe("what reaches the DOM", () => {
  const render = (el: ReturnType<typeof createElement>): string =>
    renderToStaticMarkup(createElement(StaticRouter, { location: "/" }, el));

  it("an UNKNOWN figure carries the word and its own treatment, never OK's", () => {
    const [unknown] = marketingFigures([], null);
    const ok = marketingFigures([mod("content", [check({ name: "reel_metrics", value: 0,
      unit: "reel posts, 30 d", detail: "no reels posted in the last 30 days" })])], null)[0];
    if (!unknown || !ok) throw new Error("fixtures");
    const u = render(createElement(FigureTile, { f: unknown }));
    const o = render(createElement(FigureTile, { f: ok }));
    expect(u).toContain("Unknown");
    expect(u).toContain('data-health="unknown"');
    expect(u).toContain("hatch-edge");
    expect(o).toContain('data-health="ok"');
    expect(o).not.toContain("Unknown");
    expect(o).toContain("read ");
  });

  it("a day past the read says so", () => {
    const now = new Date(2026, 9, 6, 9, 0);
    const days = weekDays({ now, agenda: { as_of: READ, items: [], unavailable: null, horizon_hours: 72 },
                            agendaError: null, agendaReadAt: now, board: null });
    const html = render(createElement(Day, { day: days[6]! }));
    expect(html).toContain("Diary not read this far ahead");
    expect(html).toContain('data-health="unknown"');
    expect(html).not.toContain("Nothing on");
  });

  it("a week read fills all seven days; a module that could not be read is never 'Nothing on'", () => {
    const now = new Date(2026, 9, 6, 9, 0);
    const read = { as_of: READ, items: [], unavailable: null, horizon_hours: WEEK_HOURS };
    const free = weekDays({ now, agenda: read, agendaError: null, agendaReadAt: now, board: null });
    // 168 hours from 09:00 ends at 09:00 a week on: six whole days and the
    // seventh read to the end of its day.
    expect(free.map((d) => d.diary)).toEqual(Array(7).fill("read"));
    expect(render(createElement(Day, { day: free[6]! }))).toContain("Nothing on");

    const partly = weekDays({ now, agenda: { ...read, unread: ["calendar (TimeoutError)"] },
                              agendaError: null, agendaReadAt: now, board: null });
    expect(partly.every((d) => d.unread.length === 1)).toBe(true);
    const html = render(createElement(Day, { day: partly[4]! }));
    expect(html).toContain("Diary partly unknown");
    expect(html).toContain('data-health="unknown"');
    expect(html).not.toContain("Nothing on");
  });
});
