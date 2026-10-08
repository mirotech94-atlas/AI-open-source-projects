// Task 78: the wallboard's own figures, and the maintenance relabel beside the
// button that opens it. Rendered directly rather than through `Shell` — the
// shell's `/live` query is gated on tab visibility (`enabled: tabVisible`), so
// a payload-driven tile cannot be exercised from there without fighting that
// gate for no benefit. `shell.test.tsx` covers the open/close/focus path.

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { TopBar } from "../src/shell/TopBar";
import { Wallboard } from "../src/shell/Wallboard";
import type { InboxRow, LivePayload, OverviewPayload } from "../src/types";

const LIVE: LivePayload = {
  ts: "2026-09-09T18:30:00-04:00",
  sites: [
    { name: "status", url: "https://status.example.com", code: 200, ms: 120, ok: true, spark: [] },
    { name: "shop", url: "https://shop.example.com", code: 502, ms: null, ok: false, spark: [] },
    { name: "docs", url: "https://docs.example.com", code: 200, ms: 90, ok: true, spark: [] },
  ],
  scanning: false,
  sensor_age_s: 30,
  load1: 0.4,
  cores: 8,
  maintenance_until: null,
};

const OVERVIEW = {
  ts: "2026-09-09T18:30:00-04:00",
  age_s: 42,
  score: 83,
  grade: "B",
  potential: 98,
  verdict: "Mostly fine.",
  next_action: null,
  deductions: [],
  score_change: null,
  strip: [],
  timeline: [],
  score_trend: { name: "score", points: [], color: null },
  questions: [],
  alerts: { crit: 2, warn: 5, info: 1, open: 8, acked: 0, snoozed: 0 },
  devices: { total: 25, online: 20, sleeping: 5, unverified: 3, quarantined: 0 },
  sensor: null,
  networks: [],
  chat_example_ip: "",
  voice: false,
  needs_you: [],
  fixed_without_you: { days: 14, automatic: 0, approved: 0, daily: [] },
  score_trend_7d: { name: "score", points: [], color: null },
  assets: { total: 31, servers: 4, sites: 3 },
  changed_24h: 7,
  incidents: { open: 1, crit: 1, warn: 0 },
  incident_briefs: [],
  recs: { open: 0, critical: 0, high: 0, top: "" },
  health: {
    sensor_state: "online", run_seconds: 80, scans_last_hour: 10, sensors_active: 1, sensors_total: 1,
    feeds: [], last_event_ts: "", last_event_age_s: null, autofix_enabled: true, autofix_24h: 12,
    notify_channel: "ntfy", dns_filter_state: "on", dns_blocked_24h: 412,
  },
  paths: [],
  cameras: { known: 0, online: 0, expected: 0, exposed: false, exposed_ports: [], gaps: [] },
  coverage: [],
  certs_expiring: [],
  score_model: "",
  score_coverage: null,
} satisfies OverviewPayload;

function row(id: string, sev: InboxRow["sev"], title: string): InboxRow {
  return {
    id, view: "alerts", sev, title, entity: "vps1", why: "", what_changed: "", status: "open",
    first_seen: "", last_seen: "", count: 1, actions: [], children: [], ticket_key: "",
    confidence: null, network: null, remedy: null, drift: null,
  };
}

function tile(label: string): HTMLElement {
  return screen.getByText(label).closest(".g-wallboard-tile") as HTMLElement;
}

describe("the wallboard's figures", () => {
  it("draws every tile from the payloads the shell already holds", () => {
    render(
      <Wallboard
        overview={OVERVIEW}
        overviewFetchedAt={Date.now()}
        live={LIVE}
        rows={[]}
        maintenanceUntil={null}
        onClose={() => {}}
        voice={false}
        speakOn={false}
        onToggleSpeak={() => {}}
      />,
    );

    // Two of three sites answering: the count AND its denominator, so a wall
    // display never shows a bare number nobody can scale.
    expect(within(tile("Sites up")).getByText("2")).toBeInTheDocument();
    expect(within(tile("Sites up")).getByText("of 3")).toBeInTheDocument();
    expect(within(tile("Security score")).getByText("grade B")).toBeInTheDocument();
    expect(within(tile("Critical")).getByText("2")).toBeInTheDocument();
    expect(within(tile("Warnings")).getByText("5")).toBeInTheDocument();
    expect(within(tile("Open incidents")).getByText("1")).toBeInTheDocument();
    expect(within(tile("Devices online")).getByText("of 25")).toBeInTheDocument();
    expect(within(tile("Unverified")).getByText("3")).toBeInTheDocument();
    expect(within(tile("Servers")).getByText("4")).toBeInTheDocument();
    expect(within(tile("Fixes 24h")).getByText("12")).toBeInTheDocument();

    // Tone, not just value: a wall display is read by colour first.
    expect(tile("Critical").className).toContain("g-tone-alert");
    expect(tile("Warnings").className).toContain("g-tone-warn");
    expect(tile("Sites up").className).toContain("g-tone-alert");
  });

  it("goes green rather than blank when there is nothing wrong", () => {
    render(
      <Wallboard
        overview={{ ...OVERVIEW, alerts: { ...OVERVIEW.alerts, crit: 0, warn: 0 }, incidents: { open: 0, crit: 0, warn: 0 } }}
        overviewFetchedAt={Date.now()}
        live={{ ...LIVE, sites: LIVE.sites.map((s) => ({ ...s, ok: true })) }}
        rows={[]}
        maintenanceUntil={null}
        onClose={() => {}}
        voice={false}
        speakOn={false}
        onToggleSpeak={() => {}}
      />,
    );
    expect(tile("Critical").className).toContain("g-tone-good");
    expect(tile("Sites up").className).toContain("g-tone-good");
    expect(screen.getByText("All quiet.")).toBeInTheDocument();
  });

  it("shows the scan age and a live maintenance window in the meta line", () => {
    const until = new Date(Date.now() + 43 * 60_000).toISOString();
    render(
      <Wallboard
        overview={OVERVIEW}
        overviewFetchedAt={Date.now()}
        live={LIVE}
        rows={[]}
        maintenanceUntil={until}
        onClose={() => {}}
        voice={false}
        speakOn={false}
        onToggleSpeak={() => {}}
      />,
    );
    expect(screen.getByText(/last scan 42s ago/)).toBeInTheDocument();
    expect(screen.getByText(/maintenance 43m left/)).toBeInTheDocument();
  });

  it("lists open alerts worst-case-first and drops the merely informational", () => {
    render(
      <Wallboard
        overview={OVERVIEW}
        overviewFetchedAt={Date.now()}
        live={LIVE}
        rows={[row("1", "CRIT", "vps1 unreachable"), row("2", "INFO", "a routine note"), row("3", "WARN", "disk at 88%")]}
        maintenanceUntil={null}
        onClose={() => {}}
        voice={false}
        speakOn={false}
        onToggleSpeak={() => {}}
      />,
    );
    expect(screen.getByText("vps1 unreachable")).toBeInTheDocument();
    expect(screen.getByText("disk at 88%")).toBeInTheDocument();
    expect(screen.queryByText("a routine note")).not.toBeInTheDocument();
  });

  it("says so rather than inventing a figure when a payload has not arrived", () => {
    render(
      <Wallboard
        overview={null}
        overviewFetchedAt={null}
        live={null}
        rows={[]}
        maintenanceUntil={null}
        onClose={() => {}}
        voice={false}
        speakOn={false}
        onToggleSpeak={() => {}}
      />,
    );
    expect(screen.getByText("no scan yet")).toBeInTheDocument();
    expect(within(tile("Sites up")).getByText("—")).toBeInTheDocument();
    expect(within(tile("Security score")).getByText("—")).toBeInTheDocument();
  });
});

function renderTopBar(
  maintenanceUntil: string | null,
  opts: { role?: "admin" | "viewer"; onMaintenance?: (hours: number) => void } = {},
) {
  return render(
    <TopBar
      me={null}
      role={opts.role ?? "admin"}
      overview={OVERVIEW}
      overviewFetchedAt={Date.now()}
      live={LIVE}
      scanState="idle"
      maintenanceUntil={maintenanceUntil}
      advanced
      onToggleAdvanced={() => {}}
      theme="dark"
      onCycleTheme={() => {}}
      onOpenPalette={() => {}}
      onOpenWallboard={() => {}}
      onRefresh={() => {}}
      onScanNow={() => {}}
      onSpeedtest={() => {}}
      onMaintenance={opts.onMaintenance ?? (() => {})}
      onSignOut={() => {}}
    />,
  );
}

async function openOverflow() {
  await userEvent.click(screen.getByRole("button", { name: /more actions/i }));
}

// Task 83: maintenance and the wallboard opener moved into the header's `⋯`
// overflow menu (design proposal §3) -- the always-visible slot they used to
// occupy is now a plain warn PILL while a window is live (not a button; see
// `TopBar`'s `g-topbar-maint`), and the actions themselves live in the menu.
describe("the maintenance control", () => {
  it("relabels itself while a window is live, the way the reference does", async () => {
    const { rerender } = renderTopBar(null);
    await openOverflow();
    expect(screen.getByRole("menuitem", { name: /start maintenance/i })).toHaveTextContent("Start maintenance…");

    rerender(<div />);
    renderTopBar(new Date(Date.now() + 43 * 60_000).toISOString());
    await openOverflow();
    expect(
      screen.getByRole("menuitem", { name: /maintenance active, 43 minutes left/i }),
    ).toHaveTextContent("Extend by an hour");
  });

  // F1: a cold reload must show the active window immediately, because the
  // header derives its state from `maintenance_until` in the live payload on
  // every render rather than from local component state that resets on load.
  it("shows the active window's remaining minutes on a fresh render, with no prior interaction", async () => {
    renderTopBar(new Date(Date.now() + 43 * 60_000).toISOString());
    expect(screen.getByText("⏸ maintenance · 43m left")).toBeInTheDocument();
    await openOverflow();
    expect(screen.queryByRole("menuitem", { name: /^start maintenance$/i })).not.toBeInTheDocument();
  });

  // F1: the hub's POST /maintenance accepts `hours: 0` to end a window now
  // (routers/settings.py); the header must offer that, not only extend.
  it("offers an end-maintenance item while a window is live, and sends hours: 0", async () => {
    const calls: number[] = [];
    renderTopBar(new Date(Date.now() + 43 * 60_000).toISOString(), { onMaintenance: (h) => calls.push(h) });
    await openOverflow();
    const end = screen.getByRole("menuitem", { name: /end maintenance/i });
    end.click();
    expect(calls).toEqual([0]);
  });

  it("has no end-maintenance item when no window is active", async () => {
    renderTopBar(null);
    await openOverflow();
    expect(screen.queryByRole("menuitem", { name: /end maintenance/i })).not.toBeInTheDocument();
  });

  it("disables the end-maintenance item for a viewer, with a role explanation", async () => {
    renderTopBar(new Date(Date.now() + 43 * 60_000).toISOString(), { role: "viewer" });
    await openOverflow();
    const end = screen.getByRole("menuitem", { name: /end maintenance/i });
    expect(end).toBeDisabled();
    expect(end).toHaveAttribute("title", expect.stringMatching(/needs the operator role/i));
  });

  it("opens the wallboard rather than the Fullscreen API", async () => {
    renderTopBar(null);
    await openOverflow();
    const button = screen.getByRole("menuitem", { name: /open the wallboard/i });
    // The old control was `aria-pressed` because it toggled document
    // fullscreen. This one opens a view; it is not a toggle.
    expect(button).not.toHaveAttribute("aria-pressed");
  });
});
