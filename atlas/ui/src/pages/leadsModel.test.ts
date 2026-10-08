import { describe, expect, it } from "vitest";
import type { Lead } from "../api";
import {
  buildChips, filterLeads, leadsToCsv, matchesSearch, matchesStatus, paginate,
  sortLeads, toggleDrawer,
} from "./leadsModel";

function lead(over: Partial<Lead> = {}): Lead {
  return {
    id: 1, business: "Guardian Plumbing", category: "plumber", source: "agency",
    address: "12 Elm St",
    contact: { name: null, email: null, phone: "+1-555-0100", site: "https://g.example" },
    website: "https://g.example", score: 0.71, score_read_at: "2026-09-27T09:00:00+00:00",
    status: "audited", audit_finding: "served over plain http", audit_severity: 2,
    audit_at: "2026-09-20T09:00:00+00:00", outreach_kind: null, outreach_state: null,
    outreach_at: null, last_touch_at: "2026-09-20T09:00:00+00:00",
    created_at: "2026-09-19T08:00:00+00:00", next_step: null,
    ...over,
  };
}

describe("buildChips", () => {
  it("orders known statuses and outreach states in a fixed sequence", () => {
    const chips = buildChips({ new: 2, dropped: 1, sent: 3, audited: 0 });
    expect(chips.map((c) => c.key)).toEqual(["new", "audited", "dropped", "sent"]);
    expect(chips.find((c) => c.key === "new")?.n).toBe(2);
  });

  it("appends a status the client has never heard of, never dropping it", () => {
    const chips = buildChips({ new: 1, "mystery-status": 4 });
    expect(chips.map((c) => c.key)).toEqual(["new", "mystery-status"]);
  });
});

describe("matchesStatus", () => {
  it("matches the lead's own status", () => {
    expect(matchesStatus(lead({ status: "new" }), "new")).toBe(true);
    expect(matchesStatus(lead({ status: "new" }), "dropped")).toBe(false);
  });

  it("matches the derived outreach state too, the same either/or the server applies", () => {
    const waiting = lead({ status: "audited", outreach_state: "waiting on you" });
    expect(matchesStatus(waiting, "waiting on you")).toBe(true);
    expect(matchesStatus(waiting, "audited")).toBe(true);
    expect(matchesStatus(waiting, "sent")).toBe(false);
  });

  it("passes everything when no chip is active", () => {
    expect(matchesStatus(lead(), null)).toBe(true);
  });
});

describe("matchesSearch", () => {
  it("matches the business name, case-insensitively", () => {
    expect(matchesSearch(lead({ business: "Riverside Cafe" }), "river")).toBe(true);
    expect(matchesSearch(lead({ business: "Riverside Cafe" }), "RIVER")).toBe(true);
  });

  it("matches a contact phone or site", () => {
    const l = lead({ contact: { name: null, email: null, phone: "+1-555-0200", site: null } });
    expect(matchesSearch(l, "0200")).toBe(true);
  });

  it("matches everything for an empty query", () => {
    expect(matchesSearch(lead(), "")).toBe(true);
    expect(matchesSearch(lead(), "   ")).toBe(true);
  });

  it("does not match unrelated text", () => {
    expect(matchesSearch(lead({ business: "Guardian Plumbing" }), "bakery")).toBe(false);
  });
});

describe("filterLeads", () => {
  it("combines the search box and the active chip", () => {
    const rows = [
      lead({ id: 1, business: "Guardian Plumbing", status: "new" }),
      lead({ id: 2, business: "Riverside Cafe", status: "dropped" }),
      lead({ id: 3, business: "Guardian Cafe", status: "dropped" }),
    ];
    expect(filterLeads(rows, "guardian", "dropped").map((l) => l.id)).toEqual([3]);
  });
});

describe("sortLeads", () => {
  it("sorts by score, descending by default, unranked leads settling low", () => {
    const rows = [
      lead({ id: 1, business: "High", score: 0.9 }),
      lead({ id: 2, business: "Unscored", score: null }),
      lead({ id: 3, business: "Low", score: 0.1 }),
    ];
    expect(sortLeads(rows, "score", null).map((l) => l.business))
      .toEqual(["High", "Low", "Unscored"]);
    expect(sortLeads(rows, "score", "asc").map((l) => l.business))
      .toEqual(["Unscored", "Low", "High"]);
  });

  it("sorts by business name alphabetically, ascending by default", () => {
    const rows = [
      lead({ id: 1, business: "Zebra Plumbing" }),
      lead({ id: 2, business: "Acme Cafe" }),
    ];
    expect(sortLeads(rows, "business", null).map((l) => l.business))
      .toEqual(["Acme Cafe", "Zebra Plumbing"]);
  });

  it("keeps its own most-recently-touched-first order when no column is chosen", () => {
    const rows = [
      lead({ id: 1, business: "Older", last_touch_at: "2026-09-01T00:00:00+00:00" }),
      lead({ id: 2, business: "Newer", last_touch_at: "2026-09-20T00:00:00+00:00" }),
    ];
    expect(sortLeads(rows, null, null).map((l) => l.business)).toEqual(["Newer", "Older"]);
  });
});

describe("paginate", () => {
  it("slices a page out of the middle of a longer list", () => {
    const rows = Array.from({ length: 30 }, (_, i) => i);
    expect(paginate(rows, 10, 5)).toEqual([10, 11, 12, 13, 14]);
  });
});

describe("toggleDrawer", () => {
  it("opens a closed row", () => {
    expect(toggleDrawer(null, 5)).toBe(5);
  });

  it("closes the row already open when it is clicked again", () => {
    expect(toggleDrawer(5, 5)).toBe(null);
  });

  it("swaps to a different row without needing a second click", () => {
    expect(toggleDrawer(5, 9)).toBe(9);
  });
});

describe("leadsToCsv", () => {
  it("writes a header row and one line per lead, never sending a request", () => {
    const csv = leadsToCsv([lead({ business: "Guardian Plumbing", score: 0.71 })]);
    const [header, row] = csv.split("\n");
    expect(header).toBe(
      "Business,Category,Source,Status,Score,Last touch,Next step,Phone,Email,Website,Address");
    expect(row).toContain("Guardian Plumbing");
    expect(row).toContain("0.71");
  });

  it("quotes a field that contains a comma", () => {
    const csv = leadsToCsv([lead({ business: "Guardian, Plumbing & Heating" })]);
    expect(csv).toContain('"Guardian, Plumbing & Heating"');
  });

  it("writes an empty cell for a lead with no score, never a guessed 0", () => {
    const csv = leadsToCsv([lead({ score: null })]);
    const row = csv.split("\n")[1] ?? "";
    const cells = row.split(",");
    expect(cells[4]).toBe("");
  });
});
