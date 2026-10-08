import { describe, expect, it } from "vitest";
import { linkReasons, linkStatus } from "../src/components/LinkIssueDrawer";
import { nodeTone } from "../src/lib/topologyLayout";
import type { TopoNode } from "../src/types";

const node = (over: Partial<TopoNode> = {}): TopoNode => ({
  id: "n1", kind: "device", group: "computer", label: "laptop", ip: "192.168.1.20",
  mac: "aa:bb:cc:dd:ee:ff", state: "online", trusted: true, quarantined: false,
  alert_sev: "", risk: 0, pulse: false, ...over,
} as TopoNode);

describe("every link colour states what it means (item 18)", () => {
  it("a green link has something to say, not an empty list", () => {
    // Green covers most of the map and was the one colour a client could
    // not interrogate. "Nothing is wrong" only reassures if you can see
    // what was checked to reach it.
    const n = node();
    expect(nodeTone(n)).toBe("good");
    expect(linkReasons(n)).toEqual([]);
    const status = linkStatus(n);
    expect(status.length).toBeGreaterThan(2);
    expect(status.join(" ")).toMatch(/no alert open/i);
  });

  it("green reports the facts behind the verdict", () => {
    const status = linkStatus(node({ state: "online", trusted: true, risk: 0 }));
    expect(status.join(" ")).toMatch(/answering scans/i);
    expect(status.join(" ")).toMatch(/marked as yours/i);
  });

  it("red and amber still explain themselves", () => {
    expect(nodeTone(node({ alert_sev: "CRIT" }))).toBe("alert");
    expect(linkReasons(node({ alert_sev: "CRIT" })).join(" ")).toMatch(/critical alert/i);
    expect(nodeTone(node({ risk: 55 }))).toBe("warn");
    expect(linkReasons(node({ risk: 55 })).join(" ")).toMatch(/risk score/i);
  });
});
