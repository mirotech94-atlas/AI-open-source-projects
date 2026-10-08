import { describe, expect, it } from "vitest";
import type { OverviewPayload, DeviceCard, InboxRow } from "../src/types";
import { NO_HERO } from "./overviewHero";

describe("contract types", () => {
  it("accepts a hub-shaped overview payload", () => {
    const o: OverviewPayload = {
      ts: "2026-09-09T18:30:26-04:00", age_s: 42, score: 83, grade: "B", potential: 98,
      score_model: "2026.9", score_coverage: { measured: 3, applicable: 3, pct: 100 },
      verdict: "Mostly fine. 4 warnings to review.", next_action: null, deductions: [], score_change: null,
      strip: [], timeline: [], score_trend: { name: "score", points: [], color: null },
      questions: [], alerts: { crit: 0, warn: 4, info: 5, open: 9, acked: 2, snoozed: 0 },
      devices: { total: 24, online: 19, sleeping: 5, unverified: 0, quarantined: 0 },
      sensor: { name: "mac", status: "active", last_seen: "2026-09-09T18:30:26-04:00", age_s: 42,
                platform: "darwin", version: "0.1.0" },
      networks: [{ id: "n1", slug: "office-wi-fi", name: "Office Wi-Fi", colour: "", state: "good",
                   status: "active", devices: 18, online: 14, unverified: 0, cameras: 2,
                   cameras_online: 2, crit: 0, warn: 0,
                   sensor_ok: true, is_primary: true }],
      chat_example_ip: "192.168.1.166",
      voice: false,
      needs_you: [], fixed_without_you: { days: 14, automatic: 0, approved: 0, daily: [] },
      coverage: [],
      certs_expiring: [],
      ...NO_HERO,
    };
    expect(o.score).toBe(83);
  });
  it("keeps snake_case device and inbox fields", () => {
    const d: DeviceCard = { mac: "58:96:71:61:05:e3", ip: "192.168.1.1", name: "cr1000a",
      label: "Router", vendor: "WNC Corporation", kind: "router", group: "Network gear",
      trusted: true, always_on: true, quarantined: false, parent: null, risk: 15,
      presence: "ONLINE", online: true, first_seen: "2026-09-08T20:45:29-04:00",
      last_seen: "2026-09-09T18:30:26-04:00", ports: [], unverified: false, randomized: false, online_7d: 92.5, camera_reasons: [],
      network: { id: "n1", slug: "office-wi-fi", name: "Office Wi-Fi", colour: "" } };
    const r: InboxRow = { id: "lan:unverified", view: "alerts", sev: "WARN", title: "x", entity: "home",
      why: "", what_changed: "", status: "open", first_seen: "", last_seen: "", count: 1,
      actions: [], children: [], ticket_key: "", confidence: 95, network: null, remedy: null, drift: null };
    expect(d.always_on && r.count === 1).toBe(true);
  });
});
