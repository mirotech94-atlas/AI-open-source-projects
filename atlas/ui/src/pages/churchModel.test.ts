/**
 * The ECC Channel tab: its pure model, and the page to static markup (no
 * jsdom, like Tasks). Times read in New York; a gap is its own row and its
 * own tone, never "planned"; the Facebook window names the date a far reel
 * can be scheduled; nothing is filled in that the server did not say.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ChurchView } from "./Church";
import {
  budgetLine, calendarRows, dayLabel, gapSummary, ideaCounts, nyDay, nyWhen,
  slotLabel, statusTone, syncLine, windowWarnings, type ChurchOverview,
} from "./churchModel";

const NOW = new Date("2026-10-15T16:00:00Z");      // noon in New York

function overview(over: Partial<ChurchOverview> = {}): ChurchOverview {
  const brief = { url: null, language: "en", reviewed: true, reviewed_at: null };
  return {
    served_at: NOW.toISOString(), as_of: NOW.toISOString(),
    last_sync: "2026-10-15T14:00:00Z",
    configured: { youtube: false, facebook: false, progress_token: true },
    brand: { name: "Example Community Church",
             name_ar: "الملاك ميخائيل ومارمينا", address: "4095 Amboy Road",
             hours: "Sat/Sun", phone: "(555) 010-0100", youtube: "@example_handle",
             facebook_page: "0000000000000" },
    posts: [
      { id: 9, platform: "youtube", schedule_index: 40, title: "Oval Last Supper",
        file: "music/output/0399.mp4", publish_at: "2026-11-01T23:00:00Z",
        status: "awaiting_review", url: null, video_id: null, language: "en",
        series: "", reviewed: false, reviewed_at: null, scheduled_at: null,
        updated_at: null },
    ],
    coverage: {
      youtube: [
        { day: "2026-10-15", slot: "18:00", state: "covered",
          post: { id: 1, title: "BK02 Shenouda", status: "scheduled", ...brief,
                  url: "https://youtube.com/shorts/P975_Ask-pU" } },
        { day: "2026-10-16", slot: "09:00", state: "gap", post: null },
      ],
      facebook: [],
    },
    gaps_7d: { youtube: 1, facebook: 0 },
    gap_days_30d: { youtube: ["2026-11-05", "2026-11-06"], facebook: [] },
    youtube_budget: { used: 3, limit: 10 },
    facebook_window: {
      window_days: 29, due: [],
      beyond: [{ id: 47, title: "03_statues", status: "rendered", ...brief,
                 publish_at: "2026-11-21T14:00:00Z", schedule_from: "2026-10-23" }],
    },
    review: [], contact: [], feasts: [
      { day: "2026-11-21", name: "Feast of Example Community (12 Hatour)",
        name_ar: "عيد رئيس الملائكة ميخائيل", major: true }],
    ideas: [
      { id: 1, code: 1, title: "How to choose your next spiritual book",
        series: "Choose With Us", category: "guide", format: "long", language: "both",
        status: "idea", notes: "" },
      { id: 2, code: 4, title: "A Pope Shenouda III book", series: "Inside This Book",
        category: "book", format: "short", language: "both", status: "scheduled",
        notes: "" }],
    launch: [], events: [],
    ...over,
  };
}

describe("times", () => {
  it("reads instants in New York time, across the clock change", () => {
    expect(nyWhen("2026-10-16T13:00:00Z")).toContain("9:00");
    expect(nyWhen("2026-11-02T14:00:00Z")).toContain("9:00");
    expect(nyWhen(null)).toBe("no time");
    expect(nyDay("2026-10-16T02:00:00Z")).toBe("2026-10-15");
  });

  it("labels slots and plain days without shifting them", () => {
    expect(slotLabel("09:00")).toBe("9:00 AM");
    expect(slotLabel("18:00")).toBe("6:00 PM");
    expect(dayLabel("2026-11-21")).toContain("21");
  });
});

describe("the calendar", () => {
  it("lists the week slot by slot, a gap as its own highlighted row, then later posts", () => {
    const rows = calendarRows(overview(), "youtube", NOW);
    expect(rows.map((r) => r.gap)).toEqual([false, true, false]);
    expect(rows[1]!.tone).toBe("gap");
    expect(rows[0]!.url).toContain("youtube.com/shorts");
    expect(rows[2]!.title).toBe("Oval Last Supper");
    expect(rows[2]!.reviewed).toBe(false);
  });

  it("gives every status its own tone and never calls a gap planned", () => {
    expect(statusTone("scheduled")).toBe("done");
    expect(statusTone("awaiting_review")).toBe("review");
    expect(statusTone("planned")).toBe("planned");
    expect(statusTone(null)).toBe("gap");
    expect(statusTone("deleted")).toBe("bad");
  });

  it("summarises the gap days and the budget", () => {
    expect(gapSummary(overview(), "youtube")).toContain("2 day(s)");
    expect(gapSummary(overview(), "facebook")).toContain("every day");
    expect(budgetLine({ used: 3, limit: 10 })).toBe(
      "3 of 10 uploads used today (New York), 7 left");
  });
});

describe("the Facebook window", () => {
  it("names the day a far reel can be scheduled", () => {
    const [w] = windowWarnings(overview().facebook_window);
    expect(w).toContain("03_statues");
    expect(w).toContain("29 days");
    expect(w).toContain(dayLabel("2026-10-23"));
  });
});

describe("the bank and the sync", () => {
  it("counts ideas by status in a fixed order", () => {
    expect(ideaCounts(overview().ideas)).toEqual([["idea", 1], ["scheduled", 1]]);
  });

  it("says plainly when the Mac never synced", () => {
    expect(syncLine(null, NOW)).toContain("never synced");
    expect(syncLine("2026-10-15T14:00:00Z", NOW)).toBe("synced 2 h ago");
  });
});

describe("the page", () => {
  it("renders the calendar, the gap, the window warning and the bank", () => {
    const html = renderToStaticMarkup(createElement(ChurchView, { data: overview(), now: NOW }));
    expect(html).toContain("Example Community &amp; Example");
    expect(html).toContain("Example");
    expect(html).toContain("No post in this slot");
    expect(html).toContain('data-tone="gap"');
    expect(html).toContain("3 of 10 uploads used today");
    expect(html).toContain("schedule it on or after");
    expect(html).toContain("How to choose your next spiritual book");
    expect(html).toContain("Feast of Example Community");
  });
});
