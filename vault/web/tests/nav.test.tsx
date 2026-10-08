// Task 57: the dropdown sidebar, and the rule that no nav row is ever hidden
// by role — a viewer's tab list must equal an admin's, with writes gated
// inside each surface instead.
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";
import type { MeOut } from "../src/api";
import { MobileTabs } from "../src/shell/MobileTabs";
import { Sidebar } from "../src/shell/Sidebar";
import { NO_BADGES } from "../src/shell/badges";
import type { NavBadges } from "../src/shell/badges";
import { TABS, redirectForMode, visibleTabs } from "../src/shell/nav";

/** Task 74: the owner's own case — three open recommendations, two open
 *  alerts (one critical), one open incident. */
const BADGES: NavBadges = {
  ...NO_BADGES,
  inboxUnread: 4,
  alertsOpen: 2,
  alertsCrit: 1,
  alertsWarn: 1,
  incidents: 1,
  recs: 3,
  securityCrit: 1,
};

function meAt(role: "viewer" | "operator" | "admin"): MeOut {
  return {
    user: { username: role, display_name: role, is_superadmin: false },
    tenants: [{ id: "t1", slug: "home", name: "HomeWiFi", role }],
    active_tenant: { id: "t1", slug: "home", name: "HomeWiFi", role },
    chat_providers: [],
  };
}

function renderSidebar(
  { role = "admin", at = "/overview", collapsed = false, badges = NO_BADGES, advanced = true }:
    { role?: "viewer" | "operator" | "admin"; at?: string; collapsed?: boolean; badges?: NavBadges; advanced?: boolean } = {},
) {
  return render(
    <MemoryRouter initialEntries={[at]}>
      <Sidebar
        collapsed={collapsed}
        onToggleCollapsed={() => {}}
        badges={badges}
        me={meAt(role)}
        onSwitchTenant={() => {}}
        advanced={advanced}
      />
    </MemoryRouter>,
  );
}

/** The parent rows, by their visible label — the "tab list" the owner means. */
function tabLabels(): string[] {
  const nav = screen.getByLabelText("Main");
  return TABS.filter((t) => within(nav).queryByRole("link", { name: t.label }) !== null).map((t) => t.label);
}

beforeEach(() => {
  window.localStorage.clear();
});

describe("nav tree", () => {
  it("gives a viewer exactly the tab list an admin gets", () => {
    const admin = renderSidebar({ role: "admin" });
    const adminTabs = tabLabels();
    admin.unmount();

    renderSidebar({ role: "viewer" });
    expect(tabLabels()).toEqual(adminTabs);
    // Not vacuously equal: every tab in the tree is present at both roles.
    expect(adminTabs).toHaveLength(TABS.length);
  });

  it("shows a viewer the Config tab itself, because a role hides data and not a tab", () => {
    renderSidebar({ role: "viewer", at: "/settings/profile" });
    const nav = screen.getByLabelText("Main");
    expect(within(nav).getByRole("link", { name: "Config" })).toHaveAttribute("href", "/settings");
  });

  it("keeps Config's sections out of the sidebar but still in the tree", () => {
    // The dropdown is gone; the children are not. They are what the mobile
    // More sheet lists, what the command palette searches, and what
    // `redirectForMode` reads to know which sections are advanced-only --
    // deleting them to remove the dropdown would have silently taken all
    // three with it.
    renderSidebar({ at: "/settings/profile" });
    const nav = screen.getByLabelText("Main");
    // "Integrations" IS in the sidebar now -- as its own top-level row, not
    // as a Config child. The Config-era child link must be gone.
    expect(within(nav).getByRole("link", { name: "Integrations" })).toHaveAttribute("href", "/integrations");
    expect(within(nav).queryByRole("link", { name: "Appearance" })).not.toBeInTheDocument();

    const config = TABS.find((t) => t.key === "settings")!;
    expect(config.flat).toBe(true);
    expect(config.children?.some((c) => c.path === "/settings/appearance")).toBe(true);
    // Integrations left Config for a row of its own: where alerts go is a
    // first-week task, not a setting.
    expect(config.children?.some((c) => c.path === "/settings/integrations")).toBe(false);
    expect(TABS.find((t) => t.key === "integrations")!.path).toBe("/integrations");
  });

  it("every row and child points at a real, non-empty route", () => {
    for (const tab of TABS) {
      expect(tab.path.startsWith("/")).toBe(true);
      for (const child of tab.children ?? []) {
        expect(child.path.startsWith("/")).toBe(true);
      }
    }
  });

  it("gives every tab one row that goes straight to its own page, with no disclosure", async () => {
    renderSidebar();
    const nav = screen.getByLabelText("Main");

    // Not one tab renders an expand control any more.
    expect(within(nav).queryAllByRole("button", { name: /^(expand|collapse) / })).toHaveLength(0);

    for (const tab of TABS) {
      const row = within(nav).getByRole("link", { name: tab.label });
      expect(row).toHaveAttribute("href", tab.path);
    }

    // Reports and Config land on their own page rather than a first child.
    expect(within(nav).getByRole("link", { name: "Reports" })).toHaveAttribute("href", "/reports");
    expect(within(nav).getByRole("link", { name: "Config" })).toHaveAttribute("href", "/settings");
    expect(within(nav).queryByRole("link", { name: "Appearance" })).not.toBeInTheDocument();
    expect(within(nav).queryByRole("link", { name: "Builder" })).not.toBeInTheDocument();
  });

  it("marks the active tab current on a sub-route, with no parent to expand", () => {
    // What the four auto-expand/persist tests here used to protect: landing
    // deep in a section must still light up that section's row. There is no
    // dropdown to open any more, so the row itself carries it.
    renderSidebar({ at: "/settings/appearance" });
    const nav = screen.getByLabelText("Main");
    expect(within(nav).getByRole("link", { name: "Config" })).toHaveAttribute("aria-current", "page");
  });

  it("marks Inbox current for any of its filters, now that it owns them itself", () => {
    // The three filters used to be child rows here. They live on the page as
    // its own tabs, so the sidebar keeps one row and it stays current across
    // every filter rather than tracking which one is showing.
    for (const at of ["/inbox?filter=recs", "/inbox?filter=alerts", "/inbox"]) {
      const r = renderSidebar({ at });
      const nav = screen.getByLabelText("Main");
      expect(within(nav).getByRole("link", { name: /^Inbox/ })).toHaveAttribute("aria-current", "page");
      expect(within(nav).queryByRole("link", { name: "Recommendations" })).not.toBeInTheDocument();
      r.unmount();
    }
  });

  it("moves focus between rows with the arrow keys", async () => {
    renderSidebar();
    const nav = screen.getByLabelText("Main");
    const overview = within(nav).getByRole("link", { name: "Overview" });
    overview.focus();
    expect(overview).toHaveFocus();

    await userEvent.keyboard("{ArrowDown}");
    expect(within(nav).getByRole("link", { name: "Inbox" })).toHaveFocus();
    await userEvent.keyboard("{ArrowUp}");
    expect(overview).toHaveFocus();
    await userEvent.keyboard("{End}");
    expect(overview).not.toHaveFocus();
  });

  it("puts the whole tree — children included — in the mobile More sheet", async () => {
    render(
      <MemoryRouter initialEntries={["/overview"]}>
        <MobileTabs />
      </MemoryRouter>,
    );
    // Closed, the bar holds five parents and the More button, nothing deeper.
    expect(screen.queryByRole("menuitem", { name: /networks/i })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "More" }));
    const sheet = screen.getByRole("menu", { name: "All tabs" });

    // A phone has no disclosure control and no flyout, so if the sheet held only
    // parents every dropdown child would be unreachable on mobile.
    expect(within(sheet).getByRole("menuitem", { name: "Networks" })).toHaveAttribute(
      "href",
      "/settings/networks",
    );
    // Updates and Inbox are flat rows now, so their former children are not
    // in the sheet either -- those pages carry their own tabs.
    expect(within(sheet).queryByRole("menuitem", { name: "CVEs" })).not.toBeInTheDocument();
    expect(within(sheet).queryByRole("menuitem", { name: "Recommendations" })).not.toBeInTheDocument();
    expect(within(sheet).getByRole("menuitem", { name: /^Inbox/ })).toHaveAttribute("href", "/inbox");
    // Every parent and every child is present — the sheet is the whole nav.
    const wanted = TABS.flatMap((t) => [t.label, ...(t.children ?? []).map((c) => c.label)]);
    for (const label of wanted) {
      expect(within(sheet).getAllByRole("menuitem", { name: label }).length).toBeGreaterThan(0);
    }
  });

  // The owner's report again: "closing dropdowns doesn't work either." The
  // mobile "More" sheet is the one dropdown in the shell with no way to
  // dismiss it except clicking a link inside it — no outside-click handler
  // and no Escape handler exist at all (see MobileTabs.tsx), so on a phone,
  // opening it and then tapping anywhere else on the page (the scrim, a
  // primary tab, the page behind it) leaves it standing open.
  it("closes the mobile More sheet on an outside click", async () => {
    render(
      <MemoryRouter initialEntries={["/overview"]}>
        <MobileTabs />
      </MemoryRouter>,
    );
    await userEvent.click(screen.getByRole("button", { name: "More" }));
    expect(screen.getByRole("menu", { name: "All tabs" })).toBeInTheDocument();

    await userEvent.click(document.body);
    expect(screen.queryByRole("menu", { name: "All tabs" })).not.toBeInTheDocument();
  });

  // The outside-click listener must not fight the button's own toggle: a
  // click that lands on the More button itself is not an "outside" click.
  it("still toggles closed on a second click of the More button itself", async () => {
    render(
      <MemoryRouter initialEntries={["/overview"]}>
        <MobileTabs />
      </MemoryRouter>,
    );
    const moreButton = screen.getByRole("button", { name: "More" });
    await userEvent.click(moreButton);
    expect(screen.getByRole("menu", { name: "All tabs" })).toBeInTheDocument();
    await userEvent.click(moreButton);
    expect(screen.queryByRole("menu", { name: "All tabs" })).not.toBeInTheDocument();
  });

  it("closes the mobile More sheet on Escape and returns focus to the More button", async () => {
    render(
      <MemoryRouter initialEntries={["/overview"]}>
        <MobileTabs />
      </MemoryRouter>,
    );
    const moreButton = screen.getByRole("button", { name: "More" });
    await userEvent.click(moreButton);
    expect(screen.getByRole("menu", { name: "All tabs" })).toBeInTheDocument();

    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("menu", { name: "All tabs" })).not.toBeInTheDocument();
    expect(moreButton).toHaveFocus();
  });

  it("has no flyout to keep in the DOM when collapsed", () => {
    // Collapsed, a tab with children used to render them as a CSS hover
    // flyout because there is no room for a disclosure next to an icon. No
    // tab has a sidebar dropdown now, so there is no flyout either.
    renderSidebar({ collapsed: true, at: "/settings/profile" });
    const nav = screen.getByLabelText("Main");
    expect(within(nav).queryByRole("link", { name: "Appearance" })).not.toBeInTheDocument();
    expect(within(nav).queryAllByRole("button", { name: /^(expand|collapse) / })).toHaveLength(0);
  });
});

describe("nav badges (task 74)", () => {
  it("sums the inbox's work onto its one row, now that it has no children", () => {
    // The three counts used to ride on child rows. Inbox is a flat row, so the
    // number has to be the total or the sidebar would show the unread count
    // alone -- which is 0 almost always.
    renderSidebar({ at: "/inbox", badges: BADGES });
    const nav = screen.getByLabelText("Main");
    const inbox = within(nav).getByRole("link", { name: /^Inbox/ });
    expect(within(inbox).getByText("6")).toBeInTheDocument();
    // Spoken, not just coloured: a bare "6" says nothing on its own.
    expect(inbox).toHaveAccessibleName("Inbox, 2 open alerts, 1 open incident, 3 recommendations");
  });

  it("rolls the children up onto Inbox while the dropdown is shut", () => {
    renderSidebar({ at: "/overview", badges: BADGES });
    const nav = screen.getByLabelText("Main");
    const inbox = within(nav).getByRole("link", { name: /^Inbox/ });
    // 2 alerts + 1 incident + 3 recommendations, in the worst of their tones.
    expect(within(inbox).getByText("6")).toBeInTheDocument();
    expect(inbox).toHaveAccessibleName("Inbox, 2 open alerts, 1 open incident, 3 recommendations");
  });

  it("keeps the roll-up when the sidebar is collapsed", () => {
    renderSidebar({ at: "/inbox?filter=recs", collapsed: true, badges: BADGES });
    const nav = screen.getByLabelText("Main");
    const inbox = within(nav).getByRole("link", { name: /^Inbox/ });
    expect(within(inbox).getByText("6")).toBeInTheDocument();
  });

  it("still shows Security's and Updates' own counts", () => {
    renderSidebar({ badges: { ...BADGES, updatesWarn: true } });
    const nav = screen.getByLabelText("Main");
    expect(within(within(nav).getByRole("link", { name: /^Security/ })).getByText("1")).toBeInTheDocument();
    expect(within(within(nav).getByRole("link", { name: /^Updates/ })).getByText("!")).toBeInTheDocument();
  });

  // F13: the sidebar's accessible name used to read "Security, 1 security
  // warnings" — a fixed plural noun that never agreed with a count of one.
  it("keeps singular and plural in agreement on every badge label", () => {
    renderSidebar({ badges: { ...NO_BADGES, securityWarn: 1 } });
    let nav = screen.getByLabelText("Main");
    expect(within(nav).getByRole("link", { name: "Security, 1 security warning" })).toBeInTheDocument();
    cleanup();

    renderSidebar({ badges: { ...NO_BADGES, securityWarn: 2 } });
    nav = screen.getByLabelText("Main");
    expect(within(nav).getByRole("link", { name: "Security, 2 security warnings" })).toBeInTheDocument();
    cleanup();

    renderSidebar({ at: "/inbox", badges: { ...NO_BADGES, alertsOpen: 1, incidents: 1, recs: 1 } });
    nav = screen.getByLabelText("Main");
    expect(
      within(nav).getByRole("link", { name: "Inbox, 1 open alert, 1 open incident, 1 recommendation" }),
    ).toBeInTheDocument();
  });

  it("carries the counts onto the phone: rolled up in the bar, per-row in the sheet", async () => {
    render(
      <MemoryRouter initialEntries={["/overview"]}>
        <MobileTabs badges={BADGES} />
      </MemoryRouter>,
    );
    const bar = screen.getByLabelText("Tabs");
    // The bar has no disclosure control, so the inbox's work reaches a phone
    // as the one total on its row.
    expect(within(within(bar).getByRole("link", { name: /^Inbox/ })).getByText("6")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "More" }));
    const sheet = screen.getByRole("menu", { name: "All tabs" });
    expect(within(within(sheet).getByRole("menuitem", { name: /^Inbox/ })).getByText("6")).toBeInTheDocument();
  });
});

// Task 78: the mode toggle changes the NAVIGATION, not just a couple of header
// controls — the behaviour the owner's reference console has had all along
// (`body[data-mode]` plus `toggleMode()`'s bounce off an advanced-only tab).
describe("simple and advanced modes (task 78)", () => {
  it("hides the advanced-only tabs in simple mode and keeps every one in advanced", () => {
    const advancedKeys = visibleTabs(true).map((t) => t.key);
    const simpleKeys = visibleTabs(false).map((t) => t.key);

    expect(advancedKeys).toEqual(TABS.map((t) => t.key));
    // The reference's own always-visible set, mapped onto Vault's tree,
    // plus "activity" — a row Vault has and the reference never did, so
    // rule 3 keeps it visible (it is the owner's own "where are my
    // queues/jobs" ask, not operator plumbing to hide).
    // "support" sits in simple mode on purpose, and nav.tsx says why: a ticket
    // is a conversation with a person, and "the person who notices a problem is
    // not always an admin". Hiding it from the simple view would hide it from
    // exactly the owner most likely to need it.
    expect(simpleKeys).toEqual(["overview", "inbox", "assets", "activity", "integrations", "reports", "settings", "support", "help"]);
    // …and the reference's advanced-only tabs are gone.
    for (const key of ["security", "updates", "dns", "topology", "history"]) {
      expect(simpleKeys).not.toContain(key);
    }
  });

  it("keeps a split parent but drops its advanced children", () => {
    // Inbox/Assets/Security/Updates/DNS are flat rows now -- each page owns
    // its own tabs, so the sidebar does not repeat them. Config is the
    // remaining parent whose children split across the two modes.
    expect(visibleTabs(false).find((t) => t.key === "assets")!.children).toBeUndefined();
    // Rule 2: a parent survives because at least one child does.
    const config = visibleTabs(false).find((t) => t.key === "settings")!;
    expect(config.children!.map((c) => c.key)).toEqual([
      "settings-profile",
      "settings-appearance",
    ]);
  });

  it("re-points a parent whose default child is advanced at its first visible one", () => {
    // Assets is a flat row pointing at its own page in both modes, so there
    // is no advanced default child left to re-point away from.
    expect(visibleTabs(true).find((t) => t.key === "assets")!.path).toBe("/assets");
    expect(visibleTabs(false).find((t) => t.key === "assets")!.path).toBe("/assets");
    // Every simple-mode parent lands somewhere the mode actually shows: on a
    // visible child, or -- for a flat tab, whose row is the page itself and
    // never one of its children -- on its own page.
    for (const tab of visibleTabs(false)) {
      if (tab.children === undefined) continue;
      if (tab.flat === true) {
        expect(tab.children.map((c) => c.path)).not.toContain(tab.path);
        continue;
      }
      expect(tab.children.map((c) => c.path)).toContain(tab.path);
    }
  });

  it("removes the hidden rows from the DOM rather than hiding them with CSS", () => {
    renderSidebar({ advanced: false });
    const nav = screen.getByLabelText("Main");
    // Not merely invisible: absent. The sidebar runs one roving arrow-key ring
    // over every link and button in the list, so a display:none row would still
    // take focus.
    expect(within(nav).queryByRole("link", { name: "DNS filter" })).not.toBeInTheDocument();
    expect(within(nav).queryByRole("link", { name: "Security" })).not.toBeInTheDocument();
    expect(within(nav).queryByRole("link", { name: "Topology" })).not.toBeInTheDocument();
  });

  it("still hides Config's advanced sections in simple mode, with no dropdown to show them in", () => {
    // The half of the test above that used to open the Config dropdown. The
    // gating did not move when the dropdown went -- it is the reason Config
    // keeps its children at all.
    const config = visibleTabs(false).find((t) => t.key === "settings")!;
    const paths = (config.children ?? []).map((c) => c.path);
    expect(paths).not.toContain("/settings/users");
    expect(paths).not.toContain("/settings/api-tokens");
    expect(paths).toContain("/settings/appearance");
  });

  it("leaves a flat tab pointing at its own page in simple mode too", () => {
    // A tab whose first child is advanced gets re-pointed at its first visible
    // child. A flat tab must not: its row goes to the page itself, and that
    // must not change with the mode -- Config would otherwise be "/settings"
    // in advanced mode and "/settings/profile" in simple.
    for (const mode of [true, false]) {
      const tabs = visibleTabs(mode);
      expect(tabs.find((t) => t.key === "settings")!.path).toBe("/settings");
      expect(tabs.find((t) => t.key === "reports")!.path).toBe("/reports");
    }
  });

  it("still fills the phone bar with five tabs when two of its five go advanced", async () => {
    render(
      <MemoryRouter initialEntries={["/overview"]}>
        <MobileTabs advanced={false} />
      </MemoryRouter>,
    );
    const bar = screen.getByLabelText("Tabs");
    // Security and DNS filter are both advanced-only; the bar backfills from the
    // remaining visible parents instead of rendering three items and two gaps.
    expect(within(bar).queryByRole("link", { name: /DNS filter/ })).not.toBeInTheDocument();
    expect(within(bar).getAllByRole("link").length).toBe(5);

    await userEvent.click(within(bar).getByRole("button", { name: "More" }));
    const sheet = screen.getByRole("menu", { name: "All tabs" });
    // The sheet is the whole *visible* nav — no back door into a hidden route.
    expect(within(sheet).queryByRole("menuitem", { name: "CVEs" })).not.toBeInTheDocument();
    // Assets is a flat row, so its views are reached on the page, not here.
    expect(within(sheet).queryByRole("menuitem", { name: "Screen time" })).not.toBeInTheDocument();
    expect(within(sheet).getByRole("menuitem", { name: "Assets" })).toHaveAttribute("href", "/assets");
  });

  it("bounces off a route the new mode cannot show, and leaves reachable ones alone", () => {
    // Advanced mode never redirects.
    expect(redirectForMode("/dns/live", "", true)).toBeNull();
    // A whole advanced-only tab goes back to Overview, as the reference does.
    expect(redirectForMode("/security/scorecards", "", false)).toBe("/overview");
    expect(redirectForMode("/history", "", false)).toBe("/overview");
    // A hidden CHILD under a visible parent keeps you in the section instead.
    // Assets is one flat row in both modes now: the per-view advanced gating
    // lived on its child entries, and the page shows its own tabs regardless,
    // so there is no longer a child route to bounce off.
    expect(redirectForMode("/assets/websites", "", false)).toBeNull();
    expect(redirectForMode("/settings/users", "", false)).toBe("/settings/profile");
    // Reachable routes stay put, query-addressed children included.
    expect(redirectForMode("/overview", "", false)).toBeNull();
    expect(redirectForMode("/inbox", "?filter=recs", false)).toBeNull();
    expect(redirectForMode("/assets/devices", "", false)).toBeNull();
    // A detail route inside a visible subtree is not a listed child; left alone.
    expect(redirectForMode("/assets/devices/aa:bb:cc:dd:ee:ff", "", false)).toBeNull();
  });
});

/* Which account you are in, and as what.
 *
 * This used to sit in the header beside the actions, and only appeared in the
 * sidebar when there was more than one account to switch between. So an owner
 * with a single account saw it twice on some screens, and once the header was
 * condensed at his request it would have vanished entirely. It belongs here:
 * it is who you are, not what is happening.
 */
describe("who you are, in the sidebar", () => {
  it("names the account and the role when there is only one", () => {
    renderSidebar({ role: "admin" });
    const nav = screen.getByLabelText("Main");
    expect(within(nav).getByText("HomeWiFi")).toBeInTheDocument();
    expect(within(nav).getByText("admin")).toBeInTheDocument();
    // Nothing to switch to, so no control that implies there is.
    expect(within(nav).queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("becomes a switcher when there is more than one account", async () => {
    const me: MeOut = {
      user: { username: "miro", display_name: "Miro", is_superadmin: false },
      tenants: [
        { id: "t1", slug: "home", name: "HomeWiFi", role: "admin" },
        { id: "t2", slug: "shop", name: "The Shop", role: "admin" },
      ],
      active_tenant: { id: "t1", slug: "home", name: "HomeWiFi", role: "admin" },
      chat_providers: [],
    };
    const switched: string[] = [];
    render(
      <MemoryRouter initialEntries={["/overview"]}>
        <Sidebar
          collapsed={false}
          onToggleCollapsed={() => {}}
          me={me}
          badges={NO_BADGES}
          advanced
          onSwitchTenant={(id) => switched.push(id)}
        />
      </MemoryRouter>,
    );
    const nav = screen.getByLabelText("Main");
    const select = within(nav).getByRole("combobox");
    await userEvent.selectOptions(select, "t2");
    expect(switched).toEqual(["t2"]);
  });
});
