/**
 * The Atlas Academy YouTube figures: the youtube module's channel key is the
 * Academy; unreadable is Unknown with a reason, never a number, and the key
 * never reaches the screen.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { CheckSummary, ModuleTile } from "../api";
import { academyChecks, channelCheckLabel, youtubeChannel, youtubeToken } from "./youtubeModel";
import { YouTubePanel } from "./YouTubePanel";

const READ = "2026-10-27T09:15:00Z";
const OK = "clips: 1,234 subscribers, 56,789 views (read 09:15)";

function check(name: string, over: Partial<CheckSummary> = {}): CheckSummary {
  return { name, health: "ok", detail: "", observed_at: READ, next_run_at: null,
           interval_s: 3600, stale: false, state: null, ...over };
}
function tile(checks: CheckSummary[], over: Partial<ModuleTile> = {}): ModuleTile {
  return { module: "youtube", title: "YouTube", health: "ok", checks, last_check_at: READ,
           next_check_at: null, enabled: true, latest_finding: null, setup: null, ...over };
}
const unknownWhy = (r: ReturnType<typeof youtubeChannel>): string => {
  expect(r.state).toBe("unknown");
  return r.state === "unknown" ? r.why : "";
};

describe("youtubeChannel", () => {
  it("is Unknown, with a reason, in every case it cannot be trusted", () => {
    const cases: Array<[ModuleTile[] | null, string]> = [
      [null, "not read"],
      [[], "not watched"],
      [[tile([], { enabled: false })], "switched off"],
      [[tile([], { setup: "not set up" })], "not set up"],
      [[tile([check("flycheap_stats")])], "its check is not reporting"],
      [[tile([check("clips_stats", { state: { kind: "paused", label: "paused until Friday" } })])],
       "paused until Friday"],
      [[tile([check("clips_stats", { stale: true, detail: OK })])], "last reading too old to count"],
      [[tile([check("clips_stats", { health: "failing", detail: "clips: token refresh failed: x" })])],
       "token refresh failed"],
      [[tile([check("clips_stats", { health: "unknown", detail: "channel 'clips' unreachable" })])],
       "Atlas Academy"],
    ];
    for (const [mods, frag] of cases) {
      const why = unknownWhy(youtubeChannel(mods, "clips"));
      expect(why, frag).toContain(frag);
      expect(why).not.toMatch(/\bclips\b/i);
    }
  });

  it("reads subscribers and views from an OK detail, with the read time", () => {
    const r = youtubeChannel([tile([check("clips_stats", { detail: OK })])], "clips");
    expect(r).toMatchObject({ state: "ok", subscribers: 1234, views: 56789, readAt: READ });
  });

  it("an unreported figure is null, never 0", () => {
    const r = youtubeChannel([tile([check("clips_stats",
      { detail: "clips: unreported subscribers, 56,789 views (read 09:15)" })])], "clips");
    expect(r).toMatchObject({ state: "ok", subscribers: null, views: 56789 });
  });

  it("an OK detail that does not parse is shown as words, relabelled", () => {
    const r = youtubeChannel([tile([check("clips_stats", { detail: "clips: all good" })])], "clips");
    expect(r).toMatchObject({ state: "ok-nonumbers", text: "Atlas Academy: all good" });
  });

  it("latest videos come from its own check, null when absent, stale or not ok", () => {
    const withV = (c: CheckSummary[]) => youtubeChannel(
      [tile([check("clips_stats", { detail: OK }), ...c])], "clips");
    const ok = withV([check("clips_latest_videos", { value: 3, unit: "videos" })]);
    expect(ok.state === "ok" && ok.latestVideos).toBe(3);
    for (const bad of [[], [check("clips_latest_videos", { value: 3, stale: true })],
                       [check("clips_latest_videos", { value: 3, health: "failing" })]]) {
      const r = withV(bad);
      expect(r.state === "ok" && r.latestVideos).toBeNull();
    }
  });
});

describe("youtubeToken", () => {
  it("is the token check's own health, or null when it does not report", () => {
    expect(youtubeToken([tile([check("clips_token")])], "clips"))
      .toMatchObject({ health: "ok", readAt: READ });
    expect(youtubeToken([tile([check("clips_token", { stale: true })])], "clips")?.health)
      .toBe("unknown");
    expect(youtubeToken([tile([])], "clips")).toBeNull();
    expect(youtubeToken(null, "clips")).toBeNull();
  });
});

describe("academyChecks", () => {
  it("returns only the Academy's checks, with fixed labels", () => {
    const t = tile([check("clips_token"), check("clips_stats"), check("clips_latest_videos"),
                    check("flycheap_stats"), check("guardian_token"), check("ecc_stats")]);
    const rows = academyChecks(t, "clips");
    expect(rows.map((r) => r.label)).toEqual(["Token", "Channel stats", "Latest videos"]);
    expect(JSON.stringify(rows)).not.toMatch(/flycheap|guardian|ecc|clips_/i);
  });
});

describe("YouTubePanel", () => {
  it("an unknown reading renders Unknown, dashed, without the channel key", () => {
    const html = renderToStaticMarkup(createElement(YouTubePanel, {
      reading: { state: "unknown", why: "Atlas Academy: token refresh failed" },
      token: null, at: "not read yet" }));
    expect(html).toContain('data-health="unknown"');
    expect(html).toContain("Unknown");
    expect(html).toContain("cmd-word-unknown");
    expect(html).toContain("not read yet");
    expect(html).not.toMatch(/\bclips\b/i);
    expect(html).not.toContain("clips_");
  });

  it("an OK reading shows the figures with their read time", () => {
    const html = renderToStaticMarkup(createElement(YouTubePanel, {
      reading: { state: "ok", subscribers: 1234, views: null, readAt: READ,
                 latestVideos: null, latestReadAt: null },
      token: { health: "ok", readAt: READ, detail: "" }, at: "read 09:15" }));
    expect(html).toContain("1,234");
    expect(html).toContain("unreported");
    expect(html).toContain("Atlas Academy");
    expect(html).toContain("read 09:15");
  });
});

describe("channelCheckLabel", () => {
  it("covers clips_ anywhere in the name and never returns the key", () => {
    expect(channelCheckLabel("clips_stats")).toBe("Channel stats");
    expect(channelCheckLabel("youtube.clips_token")).toBe("Token");
    expect(channelCheckLabel("x_clips_latest_videos")).toBe("Latest videos");
    expect(channelCheckLabel("clips_other")).toBe("Channel check");
  });
});
