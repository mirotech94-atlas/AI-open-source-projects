/**
 * Home's live layer: a change is only between two readings that counted;
 * a chart draws stored readings only, breaks at gaps, and keeps UNKNOWN off
 * the value scale; a model's dot is its breaker, and a dropped one is
 * UNKNOWN/unavailable.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { HistoryPoint, Models } from "../api";
import { HealthBars, ReadingsChart } from "../components/Spark";
import { NeuralMap } from "../components/NeuralMap";
import {
  changeLabel, changeSince, formatValue, freshlyRead, healthBars, layoutSeries, modelBadge,
  modelLegend, shapeSeries, shortModel,
} from "./liveModel";

const H = 3_600_000;
const T0 = Date.parse("2026-10-06T00:00:00Z");
const iso = (hours: number): string => new Date(T0 + hours * H).toISOString();
const pt = (hours: number, value: number | null, health = "ok", unit: string | null = "usd"): HistoryPoint =>
  ({ read_at: iso(hours), value, unit, health });

describe("numbers", () => {
  it("prints dollars with the sign and counts without decimals", () => {
    expect(formatValue(57, "usd")).toEqual({ text: "$57.00", unit: null });
    expect(formatValue(1234, "reel posts, 30 d")).toEqual({ text: "1,234", unit: "reel posts, 30 d" });
    expect(formatValue(2.345, null)).toEqual({ text: "2.35", unit: null });
  });
});

describe("change markers", () => {
  const history = [pt(0, 38), pt(6, null, "unknown"), pt(12, 50), pt(18, 99, "ok", "eur"), pt(24, 57)];
  const current = { value: 57, unit: "usd", health: "ok" as const, readAt: new Date(iso(24)) };

  it("compares with the last reading before it that counted, and names its time", () => {
    const c = changeSince(current, history);
    expect(c).toEqual({ dir: "up", delta: 7, unit: "usd", since: new Date(iso(12)) });
    const label = changeLabel(c!, new Date(iso(25)));
    expect(label.mark).toBe("▲");
    expect(label.amount).toBe("$7.00");
    expect(label.since).toMatch(/^since /);
  });

  it("goes down as well as up", () => {
    const c = changeSince({ ...current, value: 40 }, history);
    expect(c?.dir).toBe("down");
    expect(c?.delta).toBe(10);
    expect(changeLabel(c!, new Date(iso(25))).mark).toBe("▼");
  });

  it("no marker for no change, an UNKNOWN now, nothing before, or another unit", () => {
    expect(changeSince({ ...current, value: 50 }, history)).toBeNull();
    expect(changeSince({ ...current, health: "unknown" }, history)).toBeNull();
    expect(changeSince({ ...current, value: null }, history)).toBeNull();
    expect(changeSince(current, [pt(24, 57)])).toBeNull();          // only itself
    expect(changeSince(current, [pt(1, null, "unknown"), pt(2, 9, "ok", "eur")])).toBeNull();
    expect(changeSince({ ...current, readAt: null }, history)).toBeNull();
  });

  it("a read time seen for the first time is not a change; a moved one is", () => {
    const seen = new Map<string, string | null>();
    expect(freshlyRead(seen, { a: iso(1) })).toEqual([]);
    seen.set("a", iso(1));
    expect(freshlyRead(seen, { a: iso(1), b: iso(1) })).toEqual([]);
    expect(freshlyRead(seen, { a: iso(2) })).toEqual(["a"]);
    expect(freshlyRead(seen, { a: null })).toEqual([]);
  });
});

describe("chart shaping", () => {
  it("draws stored readings only: a gap breaks the line, nothing is filled in", () => {
    const s = shapeSeries([pt(0, 1), pt(6, 2), pt(40, 3), pt(46, 4)], 6 * 3600 * 3 + 600);
    expect(s.segments.map((seg) => seg.map((p) => p.v))).toEqual([[1, 2], [3, 4]]);
    expect(s.segments.flat()).toHaveLength(4);      // exactly the readings
    expect(s.marks).toEqual([]);
  });

  it("UNKNOWN readings are marks off the scale, never zeros, and break the line", () => {
    const s = shapeSeries([pt(0, 10), pt(1, 0, "unknown"), pt(2, null, "unknown"), pt(3, 12)], null);
    expect(s.segments.map((seg) => seg.map((p) => p.v))).toEqual([[10], [12]]);
    expect(s.marks.map((m) => m.kind)).toEqual(["unknown", "unknown"]);
    // The UNKNOWN reading's 0 is not on the scale.
    expect(s.vMin).toBe(10);
    expect(s.vMax).toBe(12);
  });

  it("a reading with no number is a mark; an older unit is left off and counted", () => {
    const s = shapeSeries([pt(0, 5, "ok", "posts read"), pt(1, null, "ok"), pt(2, 7), pt(3, 8)], null);
    expect(s.unit).toBe("usd");
    expect(s.otherUnit).toBe(1);
    expect(s.marks.map((m) => m.kind)).toEqual(["no-number"]);
    expect(s.segments.map((seg) => seg.map((p) => p.v))).toEqual([[7, 8]]);
  });

  it("nothing at all is no scale, not a zero line", () => {
    const s = shapeSeries([pt(0, null, "unknown")], null);
    expect(s.segments).toEqual([]);
    expect(s.vMin).toBeNull();
    expect(s.readings).toBe(1);
  });

  it("lays marks on the track under the value band, and a flat line mid-band", () => {
    const s = shapeSeries([pt(0, 4), pt(1, 4), pt(2, 1, "unknown")], null);
    const g = layoutSeries(s, T0, T0 + 4 * H, 240, 52, 10);
    expect(g.lines[0]?.map((q) => Math.round(q.y))).toEqual([21, 21]);
    expect(g.marks[0]?.y).toBeGreaterThan(40);
    expect(g.marks[0]?.x).toBe(120);
  });

  it("health samples become bars in time order with every state counted", () => {
    const bars = healthBars([
      { at: iso(2), counts: { ok: 3, degraded: 0, failing: 1, unknown: 2 }, set_aside: 1 },
      { at: iso(1), counts: { ok: 5, degraded: 1, failing: 0, unknown: 0 }, set_aside: 1 },
    ]);
    expect(bars.map((b) => b.total)).toEqual([6, 6]);
    expect(bars[1]?.counts.unknown).toBe(2);
  });
});

function models(over: Partial<Models> = {}): Models {
  return {
    as_of: iso(10), free_only: true,
    needs: [],
    deployments: [
      { provider: "groq", model: "openai/gpt-oss-120b", needs: ["reason", "hard"], dropped: null,
        breaker: { state: "open", why: "rate limited", until: iso(10.5), fails: 1 } },
      { provider: "groq", model: "openai/gpt-oss-20b", needs: ["trivial"], dropped: null,
        breaker: { state: "closed", why: null, until: null, fails: 0 } },
      { provider: "openrouter", model: "nvidia/nemotron-3.5-lightning:free", needs: ["draft"],
        dropped: null, breaker: { state: "half-open", why: "failing repeatedly", until: null, fails: 3 } },
      { provider: "cerebras", model: "gpt-oss-120b", needs: [], breaker: null,
        dropped: "refused at boot: HTTP 402" },
    ],
    agents: [
      { name: "ops", need: "reason", pin: null, next: { model: "groq:openai/gpt-oss-20b", kind: "chain" },
        last: "groq:openai/gpt-oss-120b", last_at: iso(9) },
      { name: "growth", need: "draft", pin: null,
        next: { model: "openrouter:nvidia/nemotron-3.5-lightning:free", kind: "chain" },
        last: null, last_at: null },
      { name: "shop", need: "reason", pin: null, next: null, last: "cerebras:gpt-oss-120b",
        last_at: iso(1) },
      { name: "old", need: "reason", pin: null, next: null, last: "anthropic:claude-sonnet-4-5",
        last_at: iso(1) },
    ],
    ...over,
  };
}

describe("models", () => {
  it("names a model shortly", () => {
    expect(shortModel("groq:openai/gpt-oss-120b")).toBe("gpt-oss-120b");
    expect(shortModel("openrouter:nvidia/nemotron-3.5-lightning:free")).toBe("nemotron-3.5-lightning");
    expect(shortModel("@cf/meta/llama")).toBe("llama");
  });

  it("a node shows what answered its last run, with that deployment's breaker now", () => {
    const b = modelBadge(models(), null, "ops", new Date(iso(10)));
    expect(b).toMatchObject({ label: "gpt-oss-120b", which: "last", dot: "open" });
    expect(b.title).toContain("Last answered by groq:openai/gpt-oss-120b");
    expect(b.title).toContain("rate limited");
  });

  it("an agent that has not run says so, and names what it would ask first", () => {
    const b = modelBadge(models(), null, "growth");
    expect(b).toMatchObject({ which: "next", dot: "half-open", label: "nemotron-3.5-lightning" });
    expect(b.title).toContain("not recorded");
  });

  it("a dropped deployment is unavailable; one in no chain says so; a failed read is UNKNOWN", () => {
    expect(modelBadge(models(), null, "shop").dot).toBe("dropped");
    expect(modelBadge(models(), null, "old").dot).toBe("off-chain");
    const failed = modelBadge(null, "HTTP 500", "ops");
    expect(failed).toMatchObject({ label: "Unknown", dot: "unknown" });
    expect(modelBadge(models(), null, "nobody").dot).toBe("unknown");
  });

  it("the legend lists every deployment once, trouble first", () => {
    const rows = modelLegend(models(), new Date(iso(10)));
    expect(rows.map((r) => r.dot)).toEqual(["open", "half-open", "dropped", "closed"]);
    expect(rows[0]?.word).toMatch(/^breaker open: rate limited until /);
    expect(rows[2]?.word).toContain("HTTP 402");
  });
});

describe("what reaches the DOM", () => {
  it("an UNKNOWN reading is a grey hollow mark, not a point on the line", () => {
    const s = shapeSeries([pt(0, 10), pt(1, 0, "unknown"), pt(2, 12)], null);
    const html = renderToStaticMarkup(createElement(ReadingsChart, {
      series: s, from: T0, to: T0 + 3 * H, label: "Revenue" }));
    expect(html).toContain('class="ac-spark-mark" data-kind="unknown"');
    expect(html).not.toContain("polyline");          // two one-point runs, no line across
    expect(html.match(/class="ac-spark-dot"/g)).toHaveLength(2);
    expect(html).toContain("3 readings in 7 days, 1 without a number");
  });

  it("health bars hatch UNKNOWN", () => {
    const html = renderToStaticMarkup(createElement(HealthBars, {
      bars: healthBars([{ at: iso(1), counts: { ok: 1, degraded: 0, failing: 0, unknown: 1 },
                          set_aside: 0 }]),
      from: T0, to: T0 + 2 * H }));
    expect(html).toContain('data-health="unknown"');
    expect(html).toMatch(/fill="url\(#[^)]+\)"/);
  });

  it("a node carries its model's dot; the Agency room's map, given none, is unchanged", () => {
    const agent = {
      name: "ops", title: "Operations", display: "Operations", brief: "b", need: "reason",
      available: true, providers: ["groq"], tools: 1, last_run_at: null, last_outcome: null,
      runs_24h: 0, model: null, model_at: null, handoff: [], memory_scope: [], ready: true,
      reasons: [], group: "functions" as const, next_shift_at: null,
    };
    const withModels = renderToStaticMarkup(createElement(NeuralMap, {
      agents: [agent], variant: "wide", now: T0,
      modelFor: () => ({ label: "gpt-oss-120b", dot: "open", title: "breaker open" }) }));
    expect(withModels).toContain('class="nn-model" data-dot="open"');
    expect(withModels).toContain("gpt-oss-120b");
    const without = renderToStaticMarkup(createElement(NeuralMap, {
      agents: [agent], variant: "wide", now: T0 }));
    expect(without).not.toContain("nn-model");
  });
});
