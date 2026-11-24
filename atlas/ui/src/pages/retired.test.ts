/** Retired lanes never reach the screen: names, modules, and live data. */
import { describe, expect, it } from "vitest";
import { HIDDEN_MODULES, academyLabel, isRetiredText, safeText, visible } from "./retired";

describe("retired", () => {
  it("lists the hidden modules", () => {
    expect([...HIDDEN_MODULES].sort()).toEqual(
      ["clips", "content", "flycheap", "guardian", "instagram", "tiktok", "whop"]);
  });

  it("isRetiredText: banned whole words, case-insensitive", () => {
    for (const s of ["Guardian down", "FlyCheap api", "instagram", "TikTok post", "Whop payout",
                     "Postiz", "clips", "clip", "Reels lane", "reel", "concierge",
                     "sentinel"]) {
      expect(isRetiredText(s), s).toBe(true);
    }
    expect(isRetiredText("YOUTUBE_CLIPS_CHANNEL_ID unset")).toBe(false);
    expect(isRetiredText("Atlas Academy")).toBe(false);
    // Community came back inside Content (2026-10-27): its cards read [Content · AMSM].
    expect(isRetiredText("[Content · AMSM] #4 · Post the Sunday notice")).toBe(false);
    expect(isRetiredText("mail.draft_reply")).toBe(false);
  });

  it("isRetiredText: underscores and dots break words", () => {
    for (const s of ["youtube.clips_stats", "clips_token", "tiktok_followers", "x.instagram_reach",
                     "guardian.health", "reels_run", "flycheap-api"]) {
      expect(isRetiredText(s), s).toBe(true);
    }
    expect(isRetiredText("clipsy thing")).toBe(false);
    expect(isRetiredText("YOUTUBE_CLIPS_CHANNEL_ID unset, tiktok_followers")).toBe(true);
  });

  it("safeText: never prints a retired key", () => {
    expect(safeText("youtube.clips_stats")).toBe("youtube.Atlas Academy stats");
    expect(safeText("tiktok_followers")).toBe("(detail not shown)");
    expect(safeText("clips_token")).not.toMatch(/clips/i);
  });

  it("academyLabel: the channel key is shown as Atlas Academy", () => {
    expect(academyLabel("clips: 1,234 subscribers")).toBe("Atlas Academy: 1,234 subscribers");
    expect(academyLabel("channel 'clips' refused")).toBe("channel 'Atlas Academy' refused");
    expect(academyLabel("clips token and clips stats"))
      .toBe("Atlas Academy token and Atlas Academy stats");
    expect(academyLabel("YOUTUBE_CLIPS_CHANNEL_ID unset")).toBe("YOUTUBE_CLIPS_CHANNEL_ID unset");
  });

  it("visible: drops by module or text and counts what it dropped", () => {
    const rows = [
      { m: "mail", s: "a" }, { m: "guardian", s: "b" }, { m: "agency", s: "reels run" },
      { m: "clips", s: "c" }, { m: "academy", s: "d" },
    ];
    const out = visible(rows, (r) => ({ module: r.m, text: [r.s] }));
    expect(out.rows.map((r) => r.s)).toEqual(["a", "d"]);
    expect(out.hidden).toBe(3);
  });
});
