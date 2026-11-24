/**
 * Alerts by source and decisions by kind: fixed order, empty is plain (not
 * green), retired lanes dropped and counted, an expired card says so.
 */
import { describe, expect, it } from "vitest";
import type { ActivityRow } from "../api";
import {
  ALERT_SOURCES, alertSource, decisionKind, decisionTag, groupAlerts, pendingDecisions, waitingCount,
} from "./alertsModel";

function row(over: Partial<ActivityRow> = {}): ActivityRow {
  return { kind: "finding", at: "2026-10-27T08:00:00Z", module: "mail", subject: "s",
           health: "degraded", detail: "", value: null, unit: null, id: 1, ...over };
}

describe("alertSource", () => {
  it("maps modules to the six sources; qa.* is its own", () => {
    expect(alertSource("qa")).toBe("qa");
    expect(alertSource("mail")).toBe("mail");
    expect(alertSource("calendar")).toBe("calendar");
    expect(alertSource("academy")).toBe("video");
    expect(alertSource("youtube")).toBe("video");
    expect(alertSource("agency")).toBe("sites");
    expect(alertSource("backups")).toBe("ops");
  });
});

describe("groupAlerts", () => {
  it("always returns the six groups in fixed order", () => {
    const { groups } = groupAlerts([], []);
    expect(groups.map((g) => g.source)).toEqual(["mail", "calendar", "video", "sites", "qa", "ops"]);
    expect(ALERT_SOURCES).toEqual(["mail", "calendar", "video", "sites", "qa", "ops"]);
  });

  it("an empty group has no health at all: not unknown, not ok", () => {
    const { groups } = groupAlerts([row()], []);
    const cal = groups.find((g) => g.source === "calendar")!;
    expect(cal).toMatchObject({ count: 0, worst: null, rows: [] });
  });

  it("counts, takes the worst health, keeps the newest three, ignoring input order", () => {
    const rows = [
      row({ id: 1, at: "2026-10-27T01:00:00Z", subject: "old", health: "ok" }),
      row({ id: 2, at: "2026-10-27T05:00:00Z", subject: "b", health: "failing" }),
      row({ id: 3, at: "2026-10-27T09:00:00Z", subject: "newest", health: "degraded" }),
      row({ id: 4, at: "2026-10-27T07:00:00Z", subject: "c", health: "degraded" }),
    ];
    const g = groupAlerts(rows, []).groups.find((x) => x.source === "mail")!;
    expect(g.count).toBe(4);
    expect(g.worst).toBe("failing");
    expect(g.rows.map((r) => r.subject)).toEqual(["newest", "c", "b"]);
  });

  it("a finding with no health is unknown, ranked between ok and degraded", () => {
    const g = groupAlerts([row({ health: null }), row({ id: 2, health: "ok" })], [])
      .groups.find((x) => x.source === "mail")!;
    expect(g.worst).toBe("unknown");
  });

  it("failed workflow runs go into ops", () => {
    const { groups } = groupAlerts([], [
      { id: "w1", title: "Weekly digest", outcome: "failed", at: "2026-10-27T06:00:00Z", trigger: "" },
    ]);
    const ops = groups.find((g) => g.source === "ops")!;
    expect(ops.count).toBe(1);
    expect(ops.worst).toBe("failing");
    expect(ops.rows[0]?.subject).toContain("Weekly digest");
  });

  it("drops rows from retired lanes and counts them", () => {
    const rows = [
      row({ module: "guardian", subject: "hub down" }),
      row({ module: "flycheap", subject: "api" }),
      row({ module: "clips", subject: "post failed" }),
      row({ module: "agency", subject: "reels render stuck" }),
      row({ module: "agency", subject: "site slow", id: 9 }),
    ];
    const out = groupAlerts(rows, []);
    expect(out.hidden).toBe(4);
    expect(out.groups.reduce((n, g) => n + g.count, 0)).toBe(1);
    expect(JSON.stringify(out.groups)).not.toMatch(/guardian|flycheap|reels/i);
  });
});

describe("decisionKind", () => {
  it("separates YouTube posts from Mapfront drafts", () => {
    for (const a of ["academy.publish_tutorial", "academy.autopublish_tutorial",
                     "academy.relaunch_tutorial", "academy.add_to_series_playlist",
                     "academy.pin_comment", "youtube.upload", "youtube.set_visibility",
                     "youtube.draft_metadata"]) {
      expect(decisionKind(a), a).toBe("YouTube post");
    }
    expect(decisionKind("agency.build_site")).toBe("Mapfront draft");
    expect(decisionKind("academy.publish_shop")).toBe("other");
    expect(decisionKind("mail.draft_reply")).toBe("other");
  });

  it("names the six agents' card kinds", () => {
    expect(decisionKind("agency.send_outreach")).toBe("Sales email");
    expect(decisionKind("agency.send_agreement")).toBe("Proposal");
    expect(decisionKind("bizops.send_invoice")).toBe("Invoice");
    expect(decisionKind("seo.apply_change")).toBe("SEO change");
    expect(decisionKind("seo.publish_blog_post")).toBe("SEO change");
    expect(decisionKind("research.send_client_report")).toBe("Research");
    expect(decisionKind("community.schedule_youtube_shorts")).toBe("Community post");
    expect(decisionKind("reddit.submit_post")).toBe("Reddit post");
    expect(decisionKind("bizops.propose_automation")).toBe("Automation");
    expect(decisionKind("academy.send_comment_reply")).toBe("YouTube post");
  });
});

describe("decisionTag", () => {
  it("tags agency cards with agent and client, and nothing else", () => {
    expect(decisionTag("sales.qualify", { lead_id: 3 }, { client: "Joe's Plumbing" }))
      .toBe("Sales · Joe's Plumbing");
    expect(decisionTag("community.schedule_youtube_shorts", { post_ids: [1] })).toBe("Content · AMSM");
    expect(decisionTag("academy.switch_title", {})).toBe("Content · Atlas Academy");
    expect(decisionTag("agency.send_outreach", {})).toBe("Websites · Mapfront");
    expect(decisionTag("linkedin.publish_post", { page: "x" })).toBeNull();
  });
});

describe("pendingDecisions", () => {
  const NOW = Date.parse("2026-10-27T12:00:00Z");
  const card = (id: number, action: string, expires_at: string, tier = 2) =>
    ({ id, action, tier, expires_at });

  it("sorts by expiry, says expired, and hides retired lanes", () => {
    const out = pendingDecisions([
      card(3, "agency.build_site", "2026-10-27T14:00:00Z"),
      card(1, "academy.publish_tutorial", "2026-10-27T11:00:00Z"),
      card(2, "mail.draft_reply", "2026-10-27T12:30:00Z"),
      card(7, "clips.post_reel", "2026-10-27T13:00:00Z"),
      card(8, "content.render", "2026-10-27T13:00:00Z"),
    ], NOW);
    expect(out.rows.map((r) => r.id)).toEqual([1, 2, 3]);
    expect(out.hidden).toBe(2);
    expect(out.rows[0]).toMatchObject({ kind: "YouTube post", tier: 2, expiresIn: "expired" });
    expect(out.rows[1]?.expiresIn).toBe("30m");
    expect(out.rows[2]?.kind).toBe("Mapfront draft");
  });

  it("waitingCount: the header counts what the panel lists, not the server total", () => {
    const cards = [
      card(1, "academy.publish_tutorial", "2026-10-27T11:00:00Z"),
      card(2, "tiktok.post", "2026-10-27T13:00:00Z"),
      card(3, "guardian.restart", "2026-10-27T13:00:00Z"),
    ];
    expect(waitingCount(cards, NOW)).toBe(pendingDecisions(cards, NOW).rows.length);
    expect(waitingCount(cards, NOW)).toBe(1);
    expect(waitingCount(null, NOW)).toBeNull();
  });
});
