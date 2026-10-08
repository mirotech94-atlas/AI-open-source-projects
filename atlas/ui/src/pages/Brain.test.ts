/**
 * The Brain to static markup (no jsdom, like NeuralMap.test.ts), and its
 * pure model: unknown is never 0% or 100%, the strip links WAITING ON YOU
 * to the approvals, every seat carries its layer chip, and an SOP is a
 * vertical list of steps from trigger to result.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { Brain } from "../api";
import { BrainView, Sop } from "./Brain";
import { brainFixture, runs } from "./brainFixture";
import {
  pctText, ranState, seatsLine, signoffDepartments, sopFor, unknownWhy, verifyLine,
} from "./brainModel";

function render(brain: Brain, selected: string | null = null): string {
  return renderToStaticMarkup(createElement(MemoryRouter, null,
    createElement(BrainView, { brain, selected, onPick: () => undefined })));
}

describe("brainModel", () => {
  it("says unknown, never 0% or 100%, when there is nothing to count", () => {
    expect(pctText(runs(0, 0))).toBe("unknown");
    expect(pctText(null)).toBe("unknown");
    expect(pctText(undefined)).toBe("unknown");
    expect(pctText({ ...runs(0, 0), pct: 0 })).toBe("unknown");
    expect(pctText(runs(3, 1))).toBe("75%");
    expect(pctText(runs(0, 2))).toBe("0%");
    expect(pctText(runs(2, 1))).toBe("66.7%");
    expect(pctText(runs(4, 0))).toBe("100%");
  });

  it("says why a share is unknown", () => {
    expect(unknownWhy(null, 7, false)).toMatch(/could not be read/);
    expect(unknownWhy(runs(0, 0), 7, true)).toMatch(/watch-only/);
    expect(unknownWhy(runs(0, 0), 7, false)).toMatch(/last 7 days/);
  });

  it("a seat with no per-run record is unknown, not idle", () => {
    expect(ranState({ ran_7d: null })).toBe("unknown");
    expect(ranState({ ran_7d: 0 })).toBe("idle");
    expect(ranState({ ran_7d: 3 })).toBe("ran");
  });

  it("counts seats that ran, and says which jobs keep no record", () => {
    const money = brainFixture().departments.find((d) => d.key === "money")!;
    expect(seatsLine(money)).toBe("1/1 agent ran · 1/1 recorded job ran (+1 not recorded)");
    const ops = brainFixture().departments.find((d) => d.key === "ops")!;
    expect(seatsLine(ops)).toBe("0/1 agent ran · 1 job, none recorded per run");
  });

  it("wires YOU only to departments with a Tier 2 action", () => {
    expect(signoffDepartments(brainFixture())).toEqual(["money"]);
  });

  it("reads back what was read back", () => {
    expect(verifyLine({ verified: 4, contradicted: 1, unknown: 2 }, true))
      .toBe("4 verified · 1 contradicted · 2 unsure");
    expect(verifyLine({}, false)).toBe("read-back is off");
    expect(verifyLine(null, true)).toMatch(/unknown/);
  });

  it("finds an SOP by id, or none", () => {
    expect(sopFor(brainFixture(), "job:playlist")?.[0]?.kind).toBe("trigger");
    expect(sopFor(brainFixture(), "job:nope")).toBeNull();
  });
});

describe("BrainView", () => {
  it("shows the whole business: share, waiting on you (linked), pauses, read-backs", () => {
    const html = render(brainFixture());
    expect(html).toContain("88.9%");
    expect(html).toMatch(/<a[^>]*class="bn-cell bn-waiting"[^>]*href="\/tasks"/);
    expect(html).toContain(">3<");
    expect(html).toContain("4 verified · 1 contradicted · 2 unsure");
    expect(html).toContain("markets");
  });

  it("draws an unknown share as UNKNOWN in the dashed treatment, never 0%", () => {
    const b = brainFixture();
    b.whole.runs_without_you = runs(0, 0);
    const html = render(b);
    expect(html).toMatch(/class="bn-pct" data-unknown="true" data-big="true"[^>]*>UNKNOWN</);
    expect(html).not.toMatch(/>0%</);
    // Ops ran nothing: its card says UNKNOWN too.
    const ops = html.slice(html.indexOf('data-dept="ops"'));
    expect(ops).toMatch(/data-unknown="true"[^>]*>UNKNOWN/);
  });

  it("a failed read is an error bar and its figures are UNKNOWN", () => {
    const b = brainFixture();
    b.errors = { waiting_on_you: "proposals unreadable" };
    b.whole.waiting_on_you = null;
    const html = render(b);
    expect(html).toContain("proposals unreadable");
    expect(html).toMatch(/bn-waiting" data-unknown="true"/);
  });

  it("names each department's head, or YOU", () => {
    const html = render(brainFixture());
    expect(html).toContain("Head: <strong>Ivan</strong>");
    expect(html).toContain("Head: <strong>YOU</strong>");
  });

  it("gives every seat a layer chip and a legend explains the three layers", () => {
    const html = render(brainFixture());
    expect(html).toContain('class="bn-layers"');
    for (const word of ["Automation", "AI agent", "Agentic system"]) expect(html).toContain(word);
    const chips = html.match(/class="bn-chip" data-layer="/g) ?? [];
    // 3 in the legend + 7 seats in the cards.
    expect(chips.length).toBe(3 + 7);
  });

  it("opens the picked seat's SOP as steps from trigger to result", () => {
    const html = render(brainFixture(), "job:playlist");
    const sop = html.slice(html.indexOf('class="bn-sop"'));
    const kinds = [...sop.matchAll(/class="bn-step" data-kind="(\w+)"/g)].map((m) => m[1]);
    expect(kinds).toEqual(["trigger", "reads", "decides", "acts", "signoff", "result"]);
    expect(sop).toContain("markets.paper_rebalance");
    expect(sop).toMatch(/data-tier="2"/);
    expect(sop).toContain("needs you");
  });

  it("shows no SOP for a department pick, and none for an unknown id", () => {
    expect(render(brainFixture(), "dept:money")).not.toContain('class="bn-sop"');
    const html = renderToStaticMarkup(createElement(Sop, { brain: brainFixture(), id: "job:nope" }));
    expect(html).toBe("");
  });
});
