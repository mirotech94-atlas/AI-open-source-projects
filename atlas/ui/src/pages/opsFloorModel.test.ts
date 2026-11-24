import { describe, expect, it } from "vitest";
import {
  alertPanel, decisionButtons, decisionNote, rosterRow, shiftOutcome, summaryStrip,
  type RosterAgent,
} from "./opsFloorModel";

const AT = "2026-10-29T09:00:00Z";

function agent(over: Partial<RosterAgent> = {}): RosterAgent {
  return {
    name: "scout", role: "Scout", status: "ok", shift_state: "done",
    last_shift_at: AT, last_shift_outcome: "Your shift: ok", last_shift_outcome_detail: null,
    next_shift_at: null,
    attempts_today: 1, today_output: 2, alert_unrouted: false, read_at: AT, ...over,
  };
}

describe("rosterRow", () => {
  it("maps unknown to the grey dashed UNKNOWN treatment", () => {
    const r = rosterRow(agent({ status: "unknown" }));
    expect(r).toEqual({ state: "unknown", word: "UNKNOWN", className: "cmd-unknown" });
  });

  it("maps failing, overdue and failed to cmd-failing", () => {
    expect(rosterRow(agent({ status: "failing" })).className).toBe("cmd-failing");
    expect(rosterRow(agent({ status: "ok", shift_state: "overdue" })).className)
      .toBe("cmd-failing");
    expect(rosterRow(agent({ status: "ok", shift_state: "failed" })).className)
      .toBe("cmd-failing");
  });

  it("maps ok to cmd-ok", () => {
    expect(rosterRow(agent()).className).toBe("cmd-ok");
  });

  it("an unread status is UNKNOWN, never OK", () => {
    expect(rosterRow(agent({ status: null })).state).toBe("unknown");
    expect(rosterRow(agent({ status: "something new" })).state).toBe("unknown");
  });

  it("never shares a class between unknown, failing and ok", () => {
    const classes = ["unknown", "failing", "ok"].map(
      (s) => rosterRow(agent({ status: s, shift_state: "done" })).className);
    expect(new Set(classes).size).toBe(3);
    for (const c of classes) expect(c.split(" ")).toHaveLength(1);
  });
});

describe("decisionButtons", () => {
  it("offers Decline and Approve only on a pending Tier 2 card", () => {
    const b = decisionButtons({ status: "pending", tier: 2 }, false);
    expect(b.map((x) => x.verdict)).toEqual(["decline", "approve"]);
    expect(b[1]?.label).toBe("Approve");
    expect(decisionButtons({ status: "pending", tier: 1 }, false)).toEqual([]);
    expect(decisionButtons({ status: "pending", tier: 3 }, false)).toEqual([]);
    expect(decisionButtons({ status: "declined", tier: 2 }, false)).toEqual([]);
    expect(decisionButtons({ status: "expired", tier: 2 }, false)).toEqual([]);
  });

  it("labels Approve as having no effect while watch-only", () => {
    const b = decisionButtons({ status: "pending", tier: 2 }, true);
    expect(b[1]?.label).toBe("Approve (no effect: watch-only, stays pending)");
    expect(b[0]?.label).toBe("Decline");
  });
});

describe("decisionNote", () => {
  // Miro 2026-10-29: a click never fails silently. The note outlives the
  // card (a declined card leaves the list) and says what happened in words.
  it("says a decline was done", () => {
    const n = decisionNote(412, "decline",
      { kind: "verdict", outcome: "declined", detail: "Declined." }, true);
    expect(n).toEqual({ pid: 412, tone: "ok", text: "#412 declined: nothing was done." });
  });

  it("says approve has no effect while watch-only", () => {
    const n = decisionNote(412, "approve",
      { kind: "verdict", outcome: "refused", detail: "watch-only" }, true);
    expect(n.tone).toBe("warn");
    expect(n.text).toBe(
      "#412: Approve has no effect while watch-only; the card stays pending.");
  });

  it("gives the retry time when rate limited", () => {
    const n = decisionNote(412, "decline", { kind: "error", status: 429,
      detail: "rate limited: too many decisions this minute; try again in 42 s" }, true);
    expect(n).toEqual({ pid: 412, tone: "bad",
                        text: "#412 not sent: rate limited, try again in 42 s." });
    expect(decisionNote(1, "decline", { kind: "error", status: 429, detail: "" }, true).text)
      .toBe("#1 not sent: rate limited, try again in a minute.");
  });

  it("names every other answer, and an unreachable server", () => {
    const v = (outcome: string, detail = "x") => decisionNote(
      7, "approve", { kind: "verdict", outcome, detail }, false).text;
    expect(v("executed")).toBe("#7 approved and run.");
    expect(v("started")).toBe("#7 approved: running in the background.");
    expect(v("failed", "timeout")).toBe("#7 approved, but the action failed: timeout");
    expect(v("already")).toBe("#7 was already answered: x");
    expect(v("expired")).toBe("#7 had expired: nothing was done.");
    expect(v("missing")).toBe("#7 no longer exists.");
    expect(v("superseded")).toBe("#7 changed since it was made: a fresh card replaces it.");
    expect(v("refused", "tier 3")).toBe("#7 refused: tier 3");
    expect(decisionNote(7, "decline", { kind: "error", status: 403,
      detail: "bad origin" }, true).text).toBe("#7 not sent (403): bad origin");
    expect(decisionNote(7, "decline", { kind: "error", status: null, detail: "" }, true)
      .text).toBe("#7 not sent: the server could not be reached.");
  });
});

describe("shiftOutcome", () => {
  it("is one line of at most 60 characters with the whole text behind it", () => {
    const o = shiftOutcome({
      last_shift_outcome: "I now have a full picture. The QA sweep read three sites "
        + "and found two failing checks on fiverr.",
      last_shift_outcome_detail: "I now have a full picture. The QA sweep read three sites.",
    })!;
    expect(o.line).toBe("The QA sweep read three sites and found two failing checks…");
    expect(o.line.length).toBeLessThanOrEqual(60);
    expect(o.detail).toBe("I now have a full picture. The QA sweep read three sites.");
    expect(shiftOutcome({ last_shift_outcome: null, last_shift_outcome_detail: null }))
      .toBeNull();
  });

  it("has no expand when the line already says it all", () => {
    const o = shiftOutcome({ last_shift_outcome: "content: ten drafts waiting.",
                             last_shift_outcome_detail: "content: ten drafts waiting." })!;
    expect(o.detail).toBeNull();
  });
});

describe("summaryStrip", () => {
  it("returns 3 to 5 numbers, each with its read time", () => {
    const s = summaryStrip({
      roster: { agents: [agent(), agent({ name: "b", status: "failing" })], readAt: AT },
      pending: { count: 3, readAt: AT },
      pipeline: { count: 4, readAt: AT },
      places: { used: 12, readAt: AT },
    });
    expect(s.length).toBeGreaterThanOrEqual(3);
    expect(s.length).toBeLessThanOrEqual(5);
    for (const item of s) {
      expect(item.readAt).toBe(AT);
      expect(item.text).not.toBe("UNKNOWN");
    }
    expect(s.find((i) => i.key === "failing")?.value).toBe(1);
    expect(s.find((i) => i.key === "ok")?.value).toBe(1);
  });

  it("says UNKNOWN, never 0, for a figure that was not read", () => {
    const s = summaryStrip({ roster: null, pending: null, pipeline: null, places: null });
    expect(s.length).toBeGreaterThanOrEqual(3);
    for (const item of s) {
      expect(item.value).toBeNull();
      expect(item.text).toBe("UNKNOWN");
    }
  });

  it("an unknown roster row makes the agent counts UNKNOWN", () => {
    const s = summaryStrip({
      roster: { agents: [agent({ status: "unknown" })], readAt: AT },
      pending: null, pipeline: null, places: null,
    });
    expect(s.find((i) => i.key === "ok")?.text).toBe("UNKNOWN");
  });
});

describe("alertPanel", () => {
  it("says the alert channel is UNKNOWN when an alert was not routed", () => {
    const lines = alertPanel([agent({ status: "failing", alert_unrouted: true })]);
    expect(lines.some((l) => l.text.includes("alert channel: UNKNOWN"))).toBe(true);
  });

  it("lists a failing agent and no channel notice when alerts are routed", () => {
    const lines = alertPanel([agent({ status: "failing" }), agent({ name: "ok" })]);
    expect(lines.some((l) => l.text.includes("alert channel: UNKNOWN"))).toBe(false);
    expect(lines.filter((l) => l.health === "failing")).toHaveLength(1);
  });

  it("an unread roster is UNKNOWN, never quiet", () => {
    const lines = alertPanel(null);
    expect(lines).toHaveLength(1);
    expect(lines[0]?.health).toBe("unknown");
  });
});
