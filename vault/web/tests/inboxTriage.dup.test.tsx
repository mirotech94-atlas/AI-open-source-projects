import { describe, expect, it } from "vitest";
import { primaryAction } from "../src/components/InboxTriage";
import type { TriageItem } from "../src/types";

/* The lead card renders one filled primary button and then the remaining
 * actions beside it. The two lists have to agree on which action was
 * promoted. They did not: the primary fell back to the first action when an
 * item had no fix, while the secondary list excluded only actions of kind
 * "fix", so the live inbox showed "Yes, do it" twice on the reboot item. */
const item = (actions: TriageItem["actions"]): TriageItem =>
  ({ actions } as unknown as TriageItem);

describe("the lead card's primary action", () => {
  it("is the fix when there is one", () => {
    const fix = { kind: "fix", label: "Fix now" } as TriageItem["actions"][number];
    const ack = { kind: "ack", label: "Acknowledge" } as TriageItem["actions"][number];
    expect(primaryAction(item([ack, fix]))).toBe(fix);
  });

  it("falls back to the first action, and that action is then not a secondary", () => {
    const yes = { kind: "confirm", label: "Yes, do it" } as TriageItem["actions"][number];
    const no = { kind: "dismiss", label: "No thanks" } as TriageItem["actions"][number];
    const it_ = item([yes, no]);
    expect(primaryAction(it_)).toBe(yes);
    const secondaries = it_.actions.filter((a) => a !== primaryAction(it_));
    expect(secondaries).toEqual([no]);
    expect(secondaries.map((a) => a.label)).not.toContain("Yes, do it");
  });

  it("is undefined when there is nothing to do", () => {
    expect(primaryAction(item([]))).toBeUndefined();
  });
});
