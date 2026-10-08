import type { ReactElement, SVGProps } from "react";

/** One child row inside a parent tab's dropdown. `path` is a real route (or a
 *  real route plus a query the page reads) — never an in-page anchor, so a
 *  child is linkable, bookmarkable and back-buttonable on its own. */
export type NavChild = {
  key: string;
  label: string;
  path: string;
  /** Present, and true, when this row only exists in advanced mode. See
   *  `ADVANCED_RULES` below for how the flag was assigned. */
  adv?: true;
};

/** One top-level tab. `Icon` is a tiny monoline glyph (no icon library — the
 *  product ships no UI kit) that also does duty as the collapsed sidebar's
 *  only visible mark for that tab.
 *
 *  `path` is the row's own destination. For a tab with children it is the
 *  default child's route, so clicking the parent lands somewhere real rather
 *  than on a router redirect.
 *
 *  `match` is the prefix that decides "is this tab active" — needed because
 *  `path` may be a child route (`/assets/websites`) while the tab owns a
 *  whole subtree (`/assets`).
 *
 *  `key` is a stable identifier and is NOT the label: it is sent to the hub as
 *  `ChatRequest.tab`, so `settings` stays `settings` even though the row now
 *  reads "Config", and `dns` stays `dns` behind "DNS filter".
 */
export type NavGroup = "WATCH" | "NETWORK" | "RECORD" | "ACCOUNT";

/** Sidebar section order. "Watch" first because that is the daily loop;
 *  "Account" last because it is the least-visited. */
export const GROUP_ORDER: NavGroup[] = ["WATCH", "NETWORK", "RECORD", "ACCOUNT"];

export type TabDef = {
  key: string;
  label: string;
  /** Which sidebar section the row sits under (Vault Portal v2). Rows are
   *  grouped by this in `Sidebar`, in GROUP_ORDER, and their order WITHIN a
   *  group is their order in this array — so the array stays the one place
   *  that decides sequence. */
  group: NavGroup;
  path: string;
  match: string;
  Icon: (props: SVGProps<SVGSVGElement>) => ReactElement;
  children?: NavChild[];
  /** Present, and true, when the whole tab only exists in advanced mode. A
   *  parent carries it if and only if every one of its children does. */
  adv?: true;
  /** Present, and true, when the sidebar must NOT render this tab's children
   *  as a dropdown. The children stay in the tree: they are what the mobile
   *  More sheet lists, what the command palette searches, and what
   *  `redirectForMode` reads to know which sections are advanced-only. Only
   *  the sidebar disclosure is suppressed — see the note on TABS. */
  flat?: true;
};

// `icon` is a factory for the tiny per-tab glyph component, in the same module
// as the tree it decorates — the tree is the point of this file, and splitting
// eight one-line SVG paths into a second file to satisfy fast refresh would be
// worse than the lost refresh granularity on a data module that rarely changes.
function icon(d: string) {
  // oxlint-disable-next-line react/only-export-components
  return function TabIcon(props: SVGProps<SVGSVGElement>) {
    return (
      <svg viewBox="0 0 20 20" width={18} height={18} aria-hidden="true" {...props}>
        <path
          d={d}
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    );
  };
}

/**
 * The navigation tree — one source of truth, consumed by `Sidebar`,
 * `MobileTabs`, `Shell` (the chat panel's context line and `ChatRequest.tab`)
 * and `CommandPalette`.
 *
 * Shape follows the owner's reference console: every tab that has sub-views is
 * a parent row that expands into a dropdown, and the three that do not
 * (Overview, Topology, History) are plain rows.
 */
// No tab shows a dropdown in the sidebar any more. Five tabs (Inbox, Assets,
// Security, Updates, DNS filter) dropped their children outright, because each
// is one page whose panels all render at once and whose `:section` route only
// scrolls to a panel — there was nothing behind the dropdown that the page did
// not already show.
//
// Reports and Config are different and keep their children behind `flat: true`,
// which suppresses the sidebar disclosure and nothing else. Deleting them would
// have cost three things the dropdown was not the only consumer of:
//
//   - `redirectForMode` reads the children's `adv` flags. Config has nine
//     advanced-only sections; with no children, simple mode would quietly start
//     showing Users & roles, Auto-fix policy, API tokens and the rest.
//   - MobileTabs lists children in the More sheet. A phone has no flyout, so
//     for Config — whose sections are real, separate panels — that sheet is a
//     genuine route in.
//   - The command palette searches them by name.
//
// Config's own grouped nav rail still renders on a phone (it becomes a wrapping
// row under 900px), and every `:section` route is still served, so no bookmark
// or in-app link breaks either way.
export const TABS: TabDef[] = [
  {
    key: "overview",
    group: "WATCH",
    label: "Overview",
    path: "/overview",
    match: "/overview",
    Icon: icon("M3 11 L10 4 L17 11 M5 9 V16 H15 V9"),
  },
  {
    key: "inbox",
    group: "WATCH",
    label: "Inbox",
    path: "/inbox",
    match: "/inbox",
    Icon: icon("M3 5 H17 V15 H3 Z M3 5 L10 12 L17 5"),
    },
  {
    key: "assets",
    group: "NETWORK",
    label: "Assets",
    path: "/assets",
    match: "/assets",
    Icon: icon("M4 4 H16 V16 H4 Z M4 9 H16 M9 4 V16"),
    },
  {
    key: "topology",
    group: "NETWORK",
    label: "Topology",
    path: "/topology",
    match: "/topology",
    adv: true,
    Icon: icon(
      "M10 3 A2 2 0 1 0 10 7 A2 2 0 1 0 10 3 M4 16 A2 2 0 1 0 4 12 A2 2 0 1 0 4 16 M16 16 A2 2 0 1 0 16 12 A2 2 0 1 0 16 16 M10 7 L4 12 M10 7 L16 12",
    ),
  },
  {
    key: "history",
    group: "RECORD",
    label: "History",
    path: "/history",
    match: "/history",
    adv: true,
    Icon: icon("M10 5 V10 L13.5 12 M3.5 10 A6.5 6.5 0 1 0 6 4.8 M3 3 V6 H6"),
  },
  {
    key: "activity",
    group: "WATCH",
    label: "Activity",
    path: "/activity",
    match: "/activity",
    // A little pulse/heartbeat trace -- "things happening right now", which
    // is what this tab is: every queue/job Vault is running or has just
    // run (spec: "a place where i could find all the queues and jobs").
    Icon: icon("M3 11 H7 L9 6 L12 15 L14 10 L16 11 H17"),
  },
  {
    key: "security",
    group: "NETWORK",
    label: "Security",
    path: "/security",
    match: "/security",
    adv: true,
    Icon: icon("M10 3 L17 6 V10 C17 14 14 17 10 18 C6 17 3 14 3 10 V6 Z"),
    },
  {
    key: "updates",
    group: "NETWORK",
    label: "Updates",
    path: "/updates",
    match: "/updates",
    adv: true,
    Icon: icon("M10 3 V10 L14 12 M10 17 A7 7 0 1 1 16.5 8"),
    },
  {
    key: "dns",
    group: "NETWORK",
    label: "DNS filter",
    path: "/dns",
    match: "/dns",
    adv: true,
    Icon: icon("M3 10 A7 4 0 1 0 17 10 A7 4 0 1 0 3 10 M3 10 V13 A7 4 0 0 0 17 13 V10"),
    },
  {
    // Live connections: device -> destination as the sensor reports them.
    // Sits directly under DNS filter because the two answer the same
    // question from two sides -- what a device LOOKED UP, and what it then
    // actually CONNECTED to. Advanced like DNS filter: it is only useful on
    // a network whose traffic Vault has been asked to watch.
    key: "connections",
    group: "NETWORK",
    label: "Live connections",
    path: "/connections",
    match: "/connections",
    adv: true,
    Icon: icon("M4 6 H12 M12 6 L10 4 M12 6 L10 8 M16 14 H8 M8 14 L10 12 M8 14 L10 16"),
  },
  {
    key: "integrations",
    group: "ACCOUNT",
    label: "Integrations",
    path: "/integrations",
    match: "/integrations",
    // Where alerts actually go: Slack, Teams, PagerDuty, Discord, Telegram,
    // Home Assistant, webhooks. Its own row (the reference always kept it
    // top-level); it used to hide as a Config child. Visible in simple mode
    // too -- "set up my notifications" is a first-week task, not an advanced
    // one.
    Icon: icon("M4 10 H8 M12 10 H16 M8 6 A4 4 0 0 1 8 14 M12 6 A4 4 0 0 0 12 14"),
  },
  {
    key: "reports",
    group: "RECORD",
    label: "Reports",
    flat: true,
    path: "/reports",
    match: "/reports",
    Icon: icon("M5 3 H12 L15 6 V17 H5 Z M12 3 V6 H15 M7 10 H13 M7 13 H13"),
    children: [
      { key: "reports-builder", label: "Builder", path: "/reports/builder" },
      { key: "reports-library", label: "Library", path: "/reports/library" },
      { key: "reports-schedule", label: "Schedule", path: "/reports/schedule" },
    ],
  },
  {
    key: "settings",
    group: "ACCOUNT",
    label: "Config",
    flat: true,
    path: "/settings",
    match: "/settings",
    Icon: icon(
      "M10 7 A3 3 0 1 0 10 13 A3 3 0 1 0 10 7 M10 2 V4 M10 16 V18 M4 4 L5.5 5.5 M14.5 14.5 L16 16 M2 10 H4 M16 10 H18 M4 16 L5.5 14.5 M14.5 5.5 L16 4",
    ),
    children: [
      { key: "settings-profile", label: "Profile", path: "/settings/profile" },
      { key: "settings-users", label: "Users & roles", path: "/settings/users", adv: true },
      { key: "settings-targets", label: "Sites & targets", path: "/settings/targets", adv: true },
      { key: "settings-networks", label: "Networks", path: "/settings/networks", adv: true },
      { key: "settings-autofix", label: "Auto-fix policy", path: "/settings/autofix", adv: true },
      { key: "settings-thresholds", label: "Alert thresholds", path: "/settings/thresholds", adv: true },
      { key: "settings-appearance", label: "Appearance", path: "/settings/appearance" },
      { key: "settings-sensors", label: "Sensors", path: "/settings/sensors", adv: true },
      // These three are sections of the Settings page (its own tab strip
      // renders them) that the sidebar never listed, so on a phone — which
      // has no tab strip, only the More sheet — they were unreachable.
      { key: "settings-recorders", label: "Camera recorders", path: "/settings/recorders", adv: true },
      { key: "settings-audit", label: "Audit log", path: "/settings/audit", adv: true },
      { key: "settings-billing", label: "Billing", path: "/settings/billing", adv: true },
      // Where "Request access" on the public site lands. Admin-only to read,
      // like every other Config child; the page itself enforces that.
      { key: "settings-leads", label: "Requests", path: "/settings/leads", adv: true },
      { key: "settings-api-tokens", label: "API tokens", path: "/settings/api-tokens", adv: true },
    ],
  },
  {
    // Support is its own tab, not a child of Help. Help is documentation --
    // things you read. Support is a conversation with a person, which is a
    // different act and belongs at the top level where it can be found
    // without going looking. Any signed-in member can use it: the person who
    // notices a problem is not always an admin.
    key: "support",
    group: "ACCOUNT",
    label: "Support",
    path: "/support",
    match: "/support",
    // Two speech bubbles: a conversation, which is what a ticket is. The
    // collapsed sidebar shows the glyph alone, so it has to read without its
    // label and must not be mistaken for Help's life ring.
    Icon: icon("M3 5.5 A1.5 1.5 0 0 1 4.5 4 H11.5 A1.5 1.5 0 0 1 13 5.5 V9.5 A1.5 1.5 0 0 1 11.5 11 H7 L4 13.5 V11 H4.5 A1.5 1.5 0 0 1 3 9.5 Z M15.5 7.5 A1.5 1.5 0 0 1 17 9 V13 A1.5 1.5 0 0 1 15.5 14.5 V16.5 L13 14.5 H10"),
  },
  {
    key: "help",
    group: "ACCOUNT",
    // Item 48: the API guide lives behind this tab and nothing said so, so
    // a developer looking for it went to Support. The KEY stays "help" --
    // it is sent to the hub and is not a label.
    label: "Help & API",
    path: "/help",
    match: "/help",
    // A life ring: the one glyph that reads as "help" without a letterform,
    // which the collapsed sidebar needs (it shows the mark and nothing else).
    Icon: icon("M10 3 A7 7 0 1 0 10 17 A7 7 0 1 0 10 3 M10 7 A3 3 0 1 0 10 13 A3 3 0 1 0 10 7 M5 5 L7.9 7.9 M12.1 12.1 L15 15 M15 5 L12.1 7.9 M7.9 12.1 L5 15"),
    // Every article is a child, so the whole section is reachable from the
    // mobile More sheet — a phone has no flyout and no disclosure control, so
    // anything not listed here is unreachable there.
    },
];

/** The tab that owns `pathname`, by longest matching prefix. */
export function tabFor(pathname: string): TabDef | undefined {
  return TABS.find((t) => pathname === t.match || pathname.startsWith(`${t.match}/`));
}

/*
 * ADVANCED_RULES — how the `adv` flags above were assigned.
 *
 * The owner's reference console flags eight of its fourteen flat tabs as
 * advanced-only (Sites, Hosts, Security, DNS filter, Presence, Updates,
 * History, Config) and leaves six always visible (Overview, Devices, Alerts,
 * Recommendations, Reports, Integrations). Vault's nav is a tree, not a
 * flat list, so three rules map that onto it:
 *
 *  1. A reference tab's flag carries over to whichever Vault row does its
 *     job — which for several is a dropdown CHILD, not a tab. Sites -> Assets
 *     ▸ Websites, Hosts -> Assets ▸ Servers, Devices -> Assets ▸ Devices
 *     (always), Alerts/Recommendations -> Inbox children (always),
 *     Integrations -> Config ▸ Integrations (always).
 *  2. A parent is advanced-only if and only if EVERY one of its children is.
 *     So Security, Updates and DNS filter go advanced whole; Assets, Inbox and
 *     Config stay visible with a reduced dropdown, because each keeps at least
 *     one always-visible child.
 *  3. A row Vault has and the reference never did stays VISIBLE unless it is
 *     operator plumbing sitting beside advanced siblings — hiding something the
 *     reference never hid would be a restriction Vault invented. Visible:
 *     Incidents, Screen time, Profile, Appearance, Help and every article
 *     including the API guide. Advanced: Topology (an operator map, the same
 *     class as the reference's advanced Hosts/Sites) and the rest of Config's
 *     plumbing — Users & roles, Sites & targets, Networks, Auto-fix policy,
 *     Alert thresholds, Sensors, Camera recorders, Audit log, Billing,
 *     Requests, API tokens — all of which the reference's single advanced
 *     "Config" tab (a raw JSON editor) covered between them.
 */

/** A child is current when its own path (ignoring the query) matches, and —
 *  for the query-addressed children like `/inbox?filter=recs` — when the
 *  query matches too. `/inbox` with no query counts as the first child, which
 *  is what the parent row links to.
 *
 *  Lives here rather than in `Sidebar` because `redirectForMode` has to answer
 *  the same question ("which child is the browser actually on?") and the two
 *  must never disagree. */
export function childIsCurrent(child: NavChild, pathname: string, search: string, isFirst: boolean): boolean {
  const [childPath, childQuery = ""] = child.path.split("?");
  if (pathname !== childPath) return false;
  if (childQuery === "") return true;
  const want = new URLSearchParams(childQuery);
  const have = new URLSearchParams(search);
  for (const [k, v] of want) {
    const got = have.get(k);
    if (got === null) return isFirst; // no explicit filter yet -> the default child
    if (got !== v) return false;
  }
  return true;
}

/**
 * The nav tree as the current mode sees it: every tab in advanced mode, and in
 * simple mode only the rows without `adv`.
 *
 * Hidden rows are REMOVED, never CSS-hidden the way the reference's
 * `body[data-mode=simple] nav button[data-adv]{display:none}` did it. That was
 * safe for a flat list of buttons; here the sidebar runs one roving arrow-key
 * ring over `querySelectorAll("a[href], button")`, so a display:none row would
 * still take keyboard focus — and would fail axe besides.
 *
 * A visible parent's own `path` is rewritten to its first VISIBLE child when
 * its default child is advanced-only: Assets links to `/assets/websites`, which
 * simple mode does not have, so in simple mode the row must land on
 * `/assets/devices` instead. The reference's flat list had no analogue of this.
 */
export function visibleTabs(advanced: boolean): TabDef[] {
  if (advanced) return TABS;
  const out: TabDef[] = [];
  for (const tab of TABS) {
    if (tab.adv === true) continue;
    if (tab.children === undefined) {
      out.push(tab);
      continue;
    }
    const children = tab.children.filter((c) => c.adv !== true);
    if (children.length === 0) continue; // cannot happen under rule 2; belt and braces
    // A flat tab's row goes to the page itself, which is never one of the
    // children, so the re-point below would drag it onto a child in simple
    // mode only -- Config would be "/settings" in advanced and
    // "/settings/profile" in simple. Its path is already right in both.
    const path =
      tab.flat === true || children.some((c) => c.path === tab.path) ? tab.path : children[0].path;
    out.push({ ...tab, children, path });
  }
  return out;
}

/**
 * Where the browser should go when the mode changes, or `null` when the current
 * route is reachable as it stands.
 *
 * The reference does exactly this on `toggleMode()`: leaving advanced mode
 * while sitting on an advanced-only tab sends you back to Overview. Two rules
 * rather than one, because Vault has a level the reference did not:
 *
 *  - the whole TAB is advanced-only -> `/overview`, as the reference does;
 *  - the tab is visible but the CHILD is not (Assets ▸ Websites) -> that
 *    parent's first visible child, which keeps you in the section you were
 *    looking at instead of dropping you at the front door.
 *
 * A route in a visible subtree that is not a listed child at all — a device
 * detail page, say — is left alone.
 */
export function redirectForMode(pathname: string, search: string, advanced: boolean): string | null {
  if (advanced) return null;
  const tab = tabFor(pathname);
  if (tab === undefined) return null;
  if (tab.adv === true) return "/overview";
  const children = tab.children;
  if (children === undefined) return null;
  const current =
    children.find((c, i) => childIsCurrent(c, pathname, search, i === 0)) ??
    children.find((c) => pathname.startsWith(`${c.path.split("?")[0]}/`));
  if (current === undefined || current.adv !== true) return null;
  const first = children.find((c) => c.adv !== true);
  return first === undefined ? "/overview" : first.path;
}
