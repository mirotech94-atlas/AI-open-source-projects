// Task 83: the rearranged header — site switcher, overflow menu close
// behaviour, and the live dot's pulse tied to an actual running scan.
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { MeOut } from "../src/api";
import { TopBar } from "../src/shell/TopBar";
import type { LivePayload } from "../src/types";

const baseMe: MeOut = {
  user: { username: "miro", display_name: "Miro", is_superadmin: false },
  tenants: [
    { id: "t1", slug: "home", name: "Home", role: "admin" },
    { id: "t2", slug: "office", name: "Office", role: "viewer" },
  ],
  active_tenant: { id: "t1", slug: "home", name: "Home", role: "admin" },
  chat_providers: ["rules"],
};

function makeProps(overrides: Partial<Parameters<typeof TopBar>[0]> = {}) {
  return {
    me: baseMe,
    role: "admin" as const,
    overview: null,
    overviewFetchedAt: null,
    live: null,
    scanState: "idle" as const,
    maintenanceUntil: null,
    advanced: false,
    onToggleAdvanced: vi.fn(),
    theme: "system" as const,
    onCycleTheme: vi.fn(),
    onOpenPalette: vi.fn(),
    onOpenWallboard: vi.fn(),
    onRefresh: vi.fn(),
    onScanNow: vi.fn(),
    onSpeedtest: vi.fn(),
    onMaintenance: vi.fn(),
    onSignOut: vi.fn(),
    onSwitchTenant: vi.fn(),
    ...overrides,
  };
}

function renderTopBar(overrides: Partial<Parameters<typeof TopBar>[0]> = {}) {
  const props = makeProps(overrides);
  render(<TopBar {...props} />);
  return props;
}

/* The site switcher used to live in the header and is tested there no longer.
 * The owner asked for it to come out: which account you are in never changes
 * during a session, and repeating it beside the actions cost the header its
 * middle third. It is in the sidebar now, where `sidebar.test.tsx` covers both
 * cases -- a switcher when there is more than one account, and a quiet line
 * naming the one you are in when there is not. */

describe("TopBar overflow menu", () => {
  async function openMenu() {
    const header = screen.getByRole("banner");
    const moreBtn = within(header).getByRole("button", { name: /more actions/i });
    await userEvent.click(moreBtn);
    return moreBtn;
  }

  it("opens with aria-expanded and closes on outside click", async () => {
    renderTopBar();
    const moreBtn = await openMenu();
    expect(moreBtn).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("menu")).toBeInTheDocument();

    await userEvent.click(document.body);
    expect(moreBtn).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("closes on Escape and returns focus to the opening button", async () => {
    renderTopBar();
    const moreBtn = await openMenu();
    await userEvent.keyboard("{Escape}");
    expect(moreBtn).toHaveAttribute("aria-expanded", "false");
    expect(moreBtn).toHaveFocus();
  });


  it("is operable by keyboard alone", async () => {
    renderTopBar();
    const header = screen.getByRole("banner");
    const moreBtn = within(header).getByRole("button", { name: /more actions/i });
    moreBtn.focus();
    await userEvent.keyboard("{Enter}");
    expect(moreBtn).toHaveAttribute("aria-expanded", "true");
    await userEvent.keyboard("{Escape}");
    expect(moreBtn).toHaveAttribute("aria-expanded", "false");
  });

  it("gates a control below the caller's role, disabled with a title naming the role needed", async () => {
    renderTopBar({ role: "viewer" as const });
    await openMenu();
    const speedTest = screen.getByRole("menuitem", { name: /speed test/i });
    expect(speedTest).toBeDisabled();
    expect(speedTest).toHaveAttribute("title", expect.stringMatching(/operator/i));
  });
});

describe("TopBar live dot pulse", () => {
  const liveIdle: LivePayload = {
    ts: null, sites: [], scanning: false, sensor_age_s: 10, load1: null, cores: null,
    maintenance_until: null,
  };
  const liveRunning: LivePayload = { ...liveIdle, scanning: true };

  it("does not pulse merely because time passes", () => {
    vi.useFakeTimers();
    try {
      renderTopBar({ live: liveIdle, scanState: "idle" as const });
      const dot = screen.getByRole("img", { hidden: true, name: /sensor/i });
      expect(dot.className).not.toMatch(/g-pulse-busy/);
      vi.advanceTimersByTime(60_000);
      expect(dot.className).not.toMatch(/g-pulse-busy/);
    } finally {
      vi.useRealTimers();
    }
  });

  it("pulses while live.scanning is true", () => {
    renderTopBar({ live: liveRunning, scanState: "idle" as const });
    const dot = screen.getByRole("img", { hidden: true, name: /sensor/i });
    expect(dot.className).toMatch(/busy/i);
  });

  it("does not pulse for a merely queued scan that has not started running", () => {
    renderTopBar({ live: liveIdle, scanState: "queued" as const });
    const dot = screen.getByRole("img", { hidden: true, name: /sensor/i });
    expect(dot.className).not.toMatch(/busy/i);
  });
});
