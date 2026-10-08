/**
 * ATLAS · Command Center's rules: the health figure counts only what read,
 * the pill never says "operational" while anything is unknown, a service is
 * Online only from a check that read it, and the AI core strip lights only
 * what is known.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { StaticRouter } from "react-router-dom/server";
import { describe, expect, it } from "vitest";
import type { CheckSummary, ModuleTile, Models } from "../api";
import {
  coreStates, lastAnswered, llmService, overall, providersReady, runningAgents, service,
  services, statusPill, storage, uptimeWords, SERVICES,
} from "./commandModel";
import { CommandHeader } from "./CommandCenter";

const READ = "2026-10-06T08:00:00Z";

function check(over: Partial<CheckSummary> = {}): CheckSummary {
  return { name: "x", health: "ok", detail: "", observed_at: READ, next_run_at: null,
           interval_s: 3600, stale: false, state: null, ...over };
}

function mod(module: string, health: string, checks: CheckSummary[] = [check({ health })],
             over: Partial<ModuleTile> = {}): ModuleTile {
  return { module, title: module, health, checks, last_check_at: READ, next_check_at: null,
           enabled: true, latest_finding: null, setup: null, ...over };
}

describe("overall health", () => {
  it("is the OK share of the modules that read; unknown is counted beside it, not in it", () => {
    const o = overall([mod("a", "ok"), mod("b", "ok"), mod("c", "ok"), mod("d", "failing"),
                       mod("e", "unknown"), mod("f", "unknown", [], { setup: "not set up" }),
                       mod("g", "ok", [], { enabled: false })]);
    expect(o.percent).toBe(75);
    expect([o.ok, o.failing, o.unknown, o.setAside]).toEqual([3, 1, 1, 1]);
    expect(o.word).toBe("attention");
  });

  it("nothing read is no percentage and never 'operational'", () => {
    const o = overall([mod("a", "unknown")]);
    expect(o.percent).toBeNull();
    expect(statusPill(o, false)).toEqual({ health: "unknown", text: "Unknown: 1 unmeasured" });
    expect(statusPill(overall(null), true).health).toBe("unknown");
  });

  it("all-OK only when every watched module read OK", () => {
    expect(statusPill(overall([mod("a", "ok"), mod("b", "ok")]), false).text)
      .toBe("All systems operational");
    expect(statusPill(overall([mod("a", "ok"), mod("b", "unknown")]), false).health).toBe("unknown");
    expect(statusPill(overall([mod("a", "ok"), mod("b", "degraded")]), false).health).toBe("degraded");
  });
});

describe("services", () => {
  const modules = [
    mod("guardian", "failing", [check({ name: "hub_api", health: "failing",
                                         detail: "hub API answered HTTP 502. Retrying" })]),
    mod("comms", "ok", [check({ name: "slack_auth" }),
                        check({ name: "twilio_auth", health: "unknown", detail: "timed out" })]),
    mod("flycheap", "ok", [check({ name: "api", stale: true })], { health: "ok" }),
    mod("calendar", "unknown", [check({ health: "unknown",
      state: { kind: "not_set_up", label: "not set up" } })], { setup: "not set up" }),
  ];

  it("a failing module is Down with its reason and read time", () => {
    const g = service(SERVICES[0]!, modules, null);
    expect(g).toMatchObject({ state: "down", word: "Down", readAt: READ });
    expect(g.why).toBe("hub_api: hub API answered HTTP 502");
  });

  it("one check can stand for a service; an unknown one is Unknown, never Online", () => {
    const all = services(modules, null);
    const by = Object.fromEntries(all.map((s) => [s.name, s]));
    expect(by.Slack?.state).toBe("online");
    expect(by.Twilio?.state).toBe("unknown");
    expect(by.Twilio?.why).toBe("timed out");
    expect(by.Calendar).toMatchObject({ state: "unknown", why: "not set up" });
    expect(by.Gmail).toMatchObject({ state: "unknown", why: "not watched" });
    expect(by.Whop).toMatchObject({ state: "unknown", why: "no check reads Whop" });
    expect(by.Postiz?.state).toBe("unknown");
  });

  it("a lane that contacted nothing is not a probe of the service", () => {
    const spec = SERVICES.find((x) => x.name === "YouTube")!;
    const off = service(spec, [mod("content", "ok", [check({ name: "shorts_lane",
      detail: "Shorts are off (SHORTS_PER_WEEK=0)" })])], null);
    expect(off.state).toBe("unknown");
    expect(off.why).toMatch(/^not probed/);
    const on = service(spec, [mod("content", "ok", [check({ name: "shorts_lane",
      detail: "last Short 2h ago" })])], null);
    expect(on.state).toBe("online");
  });

  it("the pill names unknowns beside trouble", () => {
    expect(statusPill(overall([mod("a", "failing"), mod("b", "unknown")]), false).text)
      .toBe("1 need attention · 1 unknown");
  });

  it("a failed read makes every service Unknown with the reason", () => {
    const all = services(null, "HTTP 502");
    expect(all.every((s) => s.state === "unknown")).toBe(true);
    expect(all.filter((s) => s.name !== "Whop" && s.name !== "Postiz")
      .every((s) => s.why.includes("502"))).toBe(true);
  });
});

function models(over: Partial<Models> = {}): Models {
  return {
    as_of: READ, free_only: true, needs: [],
    deployments: [
      { provider: "groq", model: "a", needs: ["reason"], dropped: null,
        breaker: { state: "open", why: "rate limited", until: READ, fails: 1 } },
      { provider: "groq", model: "b", needs: ["reason"], dropped: null,
        breaker: { state: "half-open", why: null, until: null, fails: 3 } },
      { provider: "groq", model: "c", needs: ["draft"], dropped: null,
        breaker: { state: "closed", why: null, until: null, fails: 0 } },
      { provider: "cerebras", model: "d", needs: [], dropped: "refused at boot: HTTP 402", breaker: null },
    ],
    agents: [
      { name: "ops", need: "reason", pin: null, next: null, last: "groq:c", last_at: "2026-10-06T07:00:00Z" },
      { name: "growth", need: "draft", pin: null, next: null, last: "groq:a", last_at: "2026-10-06T07:30:00Z" },
      { name: "idle", need: "draft", pin: null, next: null, last: null, last_at: null },
    ],
    ...over,
  };
}

describe("diagnostics", () => {
  it("counts providers that can be asked now: closed and half-open, not open or dropped", () => {
    expect(providersReady(models())).toEqual({ ready: 2, total: 4 });
    expect(llmService(models(), null, READ)).toMatchObject({ state: "degraded", why: "2 of 4 not askable now" });
    expect(llmService(null, "HTTP 500", null).state).toBe("unknown");
  });

  it("names the model that answered the most recent run, and none when nothing ran", () => {
    expect(lastAnswered(models())).toMatchObject({ model: "groq:a", agent: "growth" });
    expect(lastAnswered(models({ agents: [] }))).toBeNull();
    expect(lastAnswered(null)).toBeNull();
  });

  it("storage is the backups disk check's own reading, Unknown without it", () => {
    const disk = (over: Partial<CheckSummary>) => [mod("backups", "ok", [check({ name: "disk", ...over })])];
    expect(storage(disk({ value: 23.4, unit: "% free", health: "degraded" })))
      .toMatchObject({ text: "23% free", health: "degraded" });
    expect(storage(disk({ value: 23.4, unit: "% free", stale: true })).text).toBeNull();
    expect(storage(disk({ value: null, health: "unknown" })).health).toBe("unknown");
    expect(storage([mod("vpsie", "ok")])).toMatchObject({ text: null, sub: "disk check not reporting" });
    expect(storage(null).text).toBeNull();
  });

  it("uptime in words, and null when it was not read", () => {
    expect(uptimeWords(90061)).toBe("1d 1h");
    expect(uptimeWords(3700)).toBe("1h 1m");
    expect(uptimeWords(null)).toBeNull();
  });
});

describe("AI core strip", () => {
  it("lights what is known: listening from the voice loop, executing from the board", () => {
    const s = coreStates({ phase: "listening", agentsRunning: [], executing: 2 });
    expect(s.filter((x) => x.lit).map((x) => x.step)).toEqual(["listening", "executing"]);
    expect(s.find((x) => x.step === "executing")?.note).toBe("2 approved cards running");
  });

  it("standby only when every other state is known to be off", () => {
    expect(coreStates({ phase: "dormant", agentsRunning: [], executing: 0 })
      .find((x) => x.step === "standby")?.lit).toBe(true);
    const unknown = coreStates({ phase: "dormant", agentsRunning: null, executing: null });
    expect(unknown.find((x) => x.step === "standby")?.lit).toBe(false);
    expect(unknown.filter((x) => x.unknown).map((x) => x.step)).toEqual(["planning", "executing"]);
  });

  it("an agent run is planning from 'started' until 'finished', and a lost finish expires", () => {
    const ev = [{ agent: "ops", phase: "started", at: 0 }, { agent: "growth", phase: "started", at: 10 },
                { agent: "ops", phase: "finished", at: 20 }];
    expect(runningAgents(ev, 30)).toEqual(["growth"]);
    expect(runningAgents(ev, 11 * 60_000)).toEqual([]);
  });
});

describe("what reaches the DOM", () => {
  it("the header pill says Unknown, dashed, while anything is unmeasured", () => {
    const voice = { tap: () => undefined, phase: "dormant" } as unknown as Parameters<typeof CommandHeader>[0]["voice"];
    const overview = { data: { as_of: READ, modules: [mod("a", "ok"), mod("b", "unknown")],
                               pending_approvals: 0, watch_only: true },
                       error: null, loading: false, fetchedAt: new Date(READ), reload: () => undefined };
    const html = renderToStaticMarkup(createElement(StaticRouter, { location: "/" },
      createElement(CommandHeader, { items: [], overview, waiting: null, feed: "live",
                                     onSignOut: () => undefined, voice })));
    expect(html).toContain('class="cmd-pill" data-health="unknown"');
    expect(html).toContain("Unknown: 1 unmeasured");
    expect(html).not.toContain("operational");
    expect(html).toContain("needs you: unknown");
  });
});
