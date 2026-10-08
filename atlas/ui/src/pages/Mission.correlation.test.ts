import { describe, expect, it } from "vitest";
import type { GuardianFeed, IncidentRef, IncidentVector } from "../api";
import { stepKey, stepMatchesFor } from "./Mission";

// Fix round 1, I1: a time-only correlation could show a step as DONE for the
// WRONG incident once mark_untrusted/rescan carry no alert_id at all (mac or
// network, not an alert key). The fix restores the "proposal names one of
// this incident's own alerts" filter, using each action's own narration-only
// alert_key/alert_id, and makes an unparseable `opened` match nothing rather
// than everything.

type FeedStep = GuardianFeed["steps"][number];

function step(overrides: Partial<FeedStep>): FeedStep {
  return {
    id: 1, at: "2026-09-24T03:00:00Z", action: "guardian.mark_untrusted",
    params: {}, status: "executed", state: "DONE", reason: "",
    decided_at: "2026-09-24T03:01:00Z", expires_at: null, ...overrides,
  };
}

function feed(steps: FeedStep[]): GuardianFeed {
  return { as_of: "2026-09-24T04:00:00Z", open_incidents: null, steps, log: [] };
}

function incidentRef(overrides: Partial<IncidentRef>): IncidentRef {
  return { iid: "1", entity: "web1", sev: "WARN", opened: "2026-09-24T03:00:00Z",
          alert_keys: ["dns:beacon:10.0.0.5:evil.example"], ...overrides };
}

function beaconVector(overrides: Partial<IncidentVector>): IncidentVector {
  return {
    kind: "beacon", target: "evil.example",
    evidence: { alert: "dns:beacon:10.0.0.5:evil.example", host: "10.0.0.5" },
    first_seen: null, reputation: null, mac: "AA:BB:CC:DD:EE:01", network: null,
    step: ["mark_untrusted", "AA:BB:CC:DD:EE:01"], manual: null, ...overrides,
  };
}

describe("stepKey gives each action its own identity, not a shared kind/target pair", () => {
  it("mark_untrusted keys on mac, rescan on network, alert_op on alert_key", () => {
    expect(stepKey("guardian.mark_untrusted", { mac: "AA:BB:CC:DD:EE:01" }))
      .toBe("mark_untrusted|AA:BB:CC:DD:EE:01");
    expect(stepKey("guardian.rescan", { network: "office" })).toBe("rescan|office");
    expect(stepKey("guardian.alert_op", { alert_key: "k" })).toBe("alert_op|k");
  });
});

describe("stepMatchesFor only matches a proposal naming one of this incident's own alerts", () => {
  it("a mark_untrusted proposal for the same mac from an UNRELATED incident does not match", () => {
    const ours = incidentRef({ opened: "2026-09-24T03:00:00Z",
                               alert_keys: ["dns:beacon:10.0.0.5:evil.example"] });
    const theirs = feed([step({
      params: { mac: "AA:BB:CC:DD:EE:01", network: "office", alert_key: "dns:beacon:9.9.9.9:other.example" },
      at: "2026-09-24T03:30:00Z",
    })]);
    const latest = stepMatchesFor(theirs, ours, [beaconVector({})]);
    expect(latest.size).toBe(0);
  });

  it("a mark_untrusted proposal naming this incident's own alert DOES match", () => {
    const ours = incidentRef({});
    const mine = feed([step({
      params: { mac: "AA:BB:CC:DD:EE:01", network: "office",
               alert_key: "dns:beacon:10.0.0.5:evil.example" },
      at: "2026-09-24T03:30:00Z",
    })]);
    const latest = stepMatchesFor(mine, ours, [beaconVector({})]);
    expect(latest.get("mark_untrusted|AA:BB:CC:DD:EE:01")).toBeDefined();
  });

  it("an unparseable plan_incident.opened matches NOTHING, never everything", () => {
    const noOpened = incidentRef({ opened: null });
    const anything = feed([step({
      params: { mac: "AA:BB:CC:DD:EE:01", network: "office",
               alert_key: "dns:beacon:10.0.0.5:evil.example" },
      at: "2026-09-24T03:30:00Z",
    })]);
    const latest = stepMatchesFor(anything, noOpened, [beaconVector({})]);
    expect(latest.size).toBe(0);
  });

  it("a proposal raised BEFORE the incident opened does not match", () => {
    const ours = incidentRef({ opened: "2026-09-24T03:00:00Z" });
    const stale = feed([step({
      params: { mac: "AA:BB:CC:DD:EE:01", network: "office",
               alert_key: "dns:beacon:10.0.0.5:evil.example" },
      at: "2026-09-24T02:00:00Z",       // before opened
    })]);
    const latest = stepMatchesFor(stale, ours, [beaconVector({})]);
    expect(latest.size).toBe(0);
  });

  it("a rescan proposal is matched by its own alert_key too", () => {
    const ours = incidentRef({});
    const mine = feed([step({
      action: "guardian.rescan",
      params: { network: "office", alert_key: "dns:beacon:10.0.0.5:evil.example" },
      at: "2026-09-24T03:30:00Z",
    })]);
    const latest = stepMatchesFor(mine, ours, [beaconVector({})]);
    expect(latest.get("rescan|office")).toBeDefined();
  });

  it("with no plan_incident at all, nothing matches", () => {
    const mine = feed([step({
      params: { mac: "AA:BB:CC:DD:EE:01", network: "office",
               alert_key: "dns:beacon:10.0.0.5:evil.example" },
    })]);
    const latest = stepMatchesFor(mine, null, [beaconVector({})]);
    expect(latest.size).toBe(0);
  });
});
