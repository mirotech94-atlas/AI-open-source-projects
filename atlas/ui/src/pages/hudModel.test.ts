/** The HUD's rules: UNKNOWN for anything unread or stale, the words for each
 *  task and tile state, and a read time on every figure. */
import { describe, expect, it } from "vitest";
import type { HudSnapshot, HudSurface, HudTask } from "../api";
import {
  figure, hhmmss, liveSnapshot, readStamp, refetchDelay, sortTasks, taskWords,
  tileView, whyUnknown,
} from "./hudModel";

const AT = "2026-11-10T16:00:00Z";

function snap(over: Partial<HudSnapshot> = {}): HudSnapshot {
  return {
    read_at: AT, watch_only: false,
    tasks: { read_at: AT, items: [], unread: [] },
    missions: { read_at: AT, items: [], unread: [] },
    waiting: { read_at: AT, count: 0, cards: [], unread: [] },
    surfaces: { read_at: AT, items: [], unread: [] },
    approved_in_slack: [],
    jobs: { read_at: AT, running: [], background: [], mac: [], mac_last_seen: null, unread: [] },
    errors: {}, ...over,
  };
}

function tile(over: Partial<HudSurface> = {}): HudSurface {
  return {
    key: "gitlab", label: "GitLab", state: "idle", why: null, last_action: null,
    last_at: null, pending_cards: 0, health: "ok", tool_seen: null, read_at: AT,
    unread: [], ...over,
  };
}

function task(over: Partial<HudTask> = {}): HudTask {
  return { id: 1, at: AT, source: "dashboard", route: "cards", status: "held",
           state: "running", cards: [], done_at: null, ...over };
}

describe("hud reads", () => {
  it("null data is UNKNOWN", () => {
    expect(liveSnapshot({ data: null, error: null }, "live")).toBeNull();
    expect(figure(null, () => ({ value: 1, read_at: AT })).state).toBe("unknown");
  });

  it("a stale figure behind a failed refetch is UNKNOWN", () => {
    const read = { data: snap(), error: "503" };
    expect(liveSnapshot(read, "live")).toBeNull();
    expect(whyUnknown(read, "live")).toBe("read failed");
  });

  it("a dropped stream makes every figure stale", () => {
    const read = { data: snap(), error: null };
    expect(liveSnapshot(read, "dropped")).toBeNull();
    expect(whyUnknown(read, "dropped")).toBe("live stream dropped");
    expect(liveSnapshot(read, "live")).not.toBeNull();
    const t = tileView(tile({ state: "working" }), false);
    expect(t.state).toBe("unknown");
    expect(t.health).toBe("unknown");
    expect(t.read_at).toBeNull();
  });

  it("a figure whose own read failed is UNKNOWN, never 0", () => {
    const f = figure(snap(), (s) => ({ value: s.waiting.count, read_at: s.waiting.read_at }));
    expect(f).toEqual({ state: "read", value: 0, read_at: AT });
    const failed = snap({ waiting: { read_at: AT, count: null, cards: null, unread: ["proposals"] } });
    expect(figure(failed, (s) => ({ value: s.waiting.count, read_at: s.waiting.read_at }))
      .state).toBe("unknown");
  });

  it("there is no figure without a read time", () => {
    const f = figure(snap(), () => ({ value: 3, read_at: undefined }));
    expect(f).toEqual({ state: "unknown", why: "no read time" });
  });

  it("formats the read time to the second", () => {
    expect(hhmmss(AT)).toMatch(/^\d{2}:\d{2}:\d{2}$/);
    expect(readStamp(AT)).toMatch(/^read \d{2}:\d{2}:\d{2}$/);
    expect(readStamp(undefined)).toBe("not read yet");
    expect(hhmmss("not a date")).toBe("—");
  });
});

describe("hud tiles", () => {
  it.each([
    ["working", "working"], ["idle", "idle"], ["watch_only", "watch only: nothing ran"],
    ["refused", "refused"], ["paused", "paused"], ["rate_limited", "rate limited"],
    ["qa_held", "held by QA"], ["failed", "last action failed"],
  ] as const)("%s reads as %s", (state, words) => {
    const v = tileView(tile({ state, last_action: "gitlab.open_issue" }), true);
    expect(v.state).toBe(state);
    expect(v.words).toBe(words);
    expect(v.detail).toContain("gitlab.open_issue");
  });

  it("an uninstrumented surface is UNKNOWN with the reason", () => {
    const v = tileView(tile({ state: "unknown", why: "not instrumented" }), true);
    expect(v.state).toBe("unknown");
    expect(v.words).toBe("unknown");
    expect(v.detail).toContain("not instrumented");
  });

  it("a tile with an unread source is UNKNOWN even if it says idle", () => {
    expect(tileView(tile({ state: "idle", unread: ["checks"] }), true).state).toBe("unknown");
  });

  it("a tool seen is a note, never a state", () => {
    const v = tileView(tile({ state: "unknown", why: "not instrumented",
      tool_seen: { agent: "sites", tool: "gitlab_list_issues", at: AT } }), true);
    expect(v.state).toBe("unknown");
    expect(v.detail).toContain("seen: gitlab_list_issues");
  });
});

describe("hud tasks", () => {
  it("waiting for your approval, and expired not run", () => {
    expect(taskWords(task({ state: "waiting_for_you" }))).toBe("waiting for your approval");
    expect(taskWords(task({ state: "expired" }))).toBe("expired, not run");
    expect(taskWords(task({ state: "done" }))).not.toBe("expired, not run");
  });

  it("tasks waiting on him come first", () => {
    const sorted = sortTasks([
      task({ id: 3, state: "done" }), task({ id: 2, state: "waiting_for_you" }),
      task({ id: 4, state: "running" })]);
    expect(sorted.map((t) => t.id)).toEqual([2, 4, 3]);
  });

  it("stream bursts refetch at most once per 5 s", () => {
    expect(refetchDelay(1000, 1000 + 5000)).toBe(0);
    expect(refetchDelay(1000, 1000 + 1200)).toBe(3800);
  });
});
