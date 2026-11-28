import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { CheckSummary } from "../api";
import { BoardCheck } from "../components/BoardCheck";
import { checkLook, selfTestLine } from "./boardModel";

const base: CheckSummary = {
  name: "protection", health: "ok", detail: "", observed_at: "2026-11-14T10:00:00Z",
  next_run_at: null, interval_s: 300,
};

const cases: [string, CheckSummary, string][] = [
  ["ok", base, "OK"],
  ["unknown", { ...base, health: "unknown", last_read_at: "2026-11-13T09:15:00Z" }, "Unknown"],
  ["parked", { ...base, health: "unknown",
    state: { kind: "parked", label: "parked until 2026-12-01: waiting on a vendor" } }, "Parked"],
  ["retired", { ...base, health: "unknown",
    state: { kind: "retired", label: "retired: the code no longer has this check" } }, "Retired"],
  ["switched_off", { ...base, health: "failing",
    state: { kind: "switched_off", label: "switched off: not used (ATLAS_UNUSED_CHECKS)" } },
    "Switched off"],
];

describe("the board's four honest looks", () => {
  it("each shows its own word", () => {
    for (const [kind, check, word] of cases) {
      const html = renderToStaticMarkup(createElement(BoardCheck, { check }));
      expect(html).toContain(`>${word}<`);
      expect(html).toContain(`data-look="${kind}"`);
    }
  });

  it("no two share a treatment", () => {
    const treatments = cases.map(([, check]) => checkLook(check).treatment);
    expect(new Set(treatments).size).toBe(cases.length);
  });

  it("OK is never what UNKNOWN, parked or retired look like", () => {
    const ok = checkLook(base);
    for (const [kind, check] of cases.slice(1)) {
      const look = checkLook(check);
      expect(look.kind, kind).not.toBe("ok");
      expect(look.treatment, kind).not.toBe(ok.treatment);
    }
  });

  it("a parked or retired check is never shown as OK even when its row says ok", () => {
    for (const kind of ["parked", "retired", "switched_off"] as const) {
      const look = checkLook({ ...base, health: "ok",
        state: { kind, label: `${kind}: x` } });
      expect(look.kind).toBe(kind);
    }
  });

  it("UNKNOWN names when it was last read", () => {
    const look = checkLook(cases[1]![1]);
    expect(look.note).toContain("last read at 2026-11-13 09:15");
    expect(checkLook({ ...base, health: "unknown", observed_at: null,
      last_read_at: null }).note).toContain("never read");
  });

  it("the stylesheet gives each treatment its own rule, UNKNOWN dashed and unlit", () => {
    const css = readFileSync(new URL("../hud.css", import.meta.url), "utf8");
    const rule = (t: string) => {
      const m = css.match(new RegExp(`\\.bd-check\\[data-treatment="${t}"\\][^{]*\\{([^}]*)\\}`));
      expect(m, `no rule for ${t}`).not.toBeNull();
      return m![1]!.replace(/\s+/g, " ").trim();
    };
    const rules = ["lit", "dashed-hatch", "double", "struck-dotted"].map(rule);
    expect(new Set(rules).size).toBe(4);
    expect(rule("dashed-hatch")).toContain("dashed");
    expect(rule("double")).toContain("double");
    expect(rule("struck-dotted")).toContain("line-through");
    expect(rule("lit")).not.toContain("dashed");
  });

  it("the self-test line carries a read time on every count", () => {
    const at = "2026-11-14T10:00:00+00:00";
    const lines = selfTestLine({
      read_at: at,
      checks_run: { n: 8, of: 10, read_at: at, oldest_reading_at: null },
      unknown: { n: 1, read_at: at }, parked: { n: 1, read_at: at },
      retired: { n: 0, read_at: at },
    });
    expect(lines).toHaveLength(3);
    for (const l of lines) expect(l).toContain("10:00:00 UTC");
    expect(lines[0]).toContain("8 of 10");
    expect(selfTestLine(undefined)[0]).toContain("not read");
  });

  it("names switched-off checks apart from unknown, only when there are some", () => {
    const at = "2026-11-14T10:00:00Z";
    const t = {
      read_at: at,
      checks_run: { n: 8, of: 10, read_at: at, oldest_reading_at: null },
      unknown: { n: 0, read_at: at }, parked: { n: 0, read_at: at },
      retired: { n: 0, read_at: at },
    };
    expect(selfTestLine(t)).toHaveLength(3);
    const lines = selfTestLine({ ...t, switched_off: { n: 7, read_at: at } });
    expect(lines).toHaveLength(4);
    expect(lines[3]).toContain("7 switched off");
  });
});
