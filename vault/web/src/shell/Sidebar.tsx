import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import type { MeOut } from "../api";
import { Badge } from "../design";
import { useScrollSpy } from "../lib/section";
import { ownBadge, parentBadge } from "./badges";
import type { NavBadge, NavBadges } from "./badges";
import { childIsCurrent, visibleTabs, GROUP_ORDER } from "./nav";

/** The bare key `useScrollSpy`/`sectionId` key a child's own path resolves
 *  to — the last path segment, ignoring any query string. For a page built
 *  out of `lib/section.ts` anchors (Security, Updates, DNS filter, Reports)
 *  this is exactly the id its panel carries; for a child that is a real
 *  routed page (Inbox's `?filter=`, Assets, Settings…) no element will ever
 *  carry that id, which is what lets `useScrollSpy` tell the two cases apart. */
function sectionKeyOf(path: string): string {
  const withoutQuery = path.split("?")[0];
  return withoutQuery.slice(withoutQuery.lastIndexOf("/") + 1);
}

const EXPANDED_KEY = "guardian.sidebar.expanded";

function readExpanded(): Set<string> {
  try {
    const raw = window.localStorage.getItem(EXPANDED_KEY);
    if (raw === null) return new Set();
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? new Set(parsed.filter((k): k is string => typeof k === "string")) : new Set();
  } catch {
    return new Set();
  }
}

function writeExpanded(keys: Set<string>): void {
  try {
    window.localStorage.setItem(EXPANDED_KEY, JSON.stringify([...keys]));
  } catch {
    // Persistence is a convenience; the in-memory state still toggles.
  }
}

/** Kept as an alias so callers that name the old type still compile; the
 *  figures themselves live in `./badges` now, shared with `MobileTabs`. */
export type SidebarBadges = NavBadges;

export type SidebarProps = {
  collapsed: boolean;
  onToggleCollapsed: () => void;
  badges: SidebarBadges;
  me: MeOut | null;
  onSwitchTenant: (tenantId: string) => void;
  /** Simple mode drops the advanced-only rows (see `nav.tsx`'s
   *  `ADVANCED_RULES`). Defaults to advanced so a direct render — the a11y
   *  sweep, a test — still sees the whole tree. */
  advanced?: boolean;
};

/** The badge itself is plain text — a bare `<span>` may not carry an
 *  `aria-label` — so what the number counts is spoken through the *row's*
 *  accessible name instead (`rowLabel` below). Without that, a rolled-up
 *  number on a shut parent would read as that row's own count. */
function renderBadge(b: NavBadge | null) {
  if (b === null) return null;
  return <Badge tone={b.tone}>{b.text}</Badge>;
}

function rowLabel(label: string, b: NavBadge | null): string | undefined {
  return b === null ? undefined : `${label}, ${b.label}`;
}

/**
 * Collapsible left sidebar. Every tab with sub-views is a parent row that
 * expands into a dropdown of real routes; the parent row itself links to its
 * default child. Expansion is persisted per browser, the active child's
 * parent expands on its own, and the whole list is one arrow-key roving
 * focus ring. Collapsed, the dropdown becomes a hover/focus flyout so the
 * children stay reachable without labels.
 *
 * No row is ever hidden by role: the tab list an admin sees is exactly the
 * tab list a viewer sees, and the write gating lives inside each surface.
 */
export function Sidebar({ collapsed, onToggleCollapsed, badges, me, onSwitchTenant, advanced = true }: SidebarProps) {
  const location = useLocation();
  const tenants = me?.tenants ?? [];
  const showSwitcher = tenants.length > 1;
  const listRef = useRef<HTMLUListElement>(null);

  const [expanded, setExpanded] = useState<Set<string>>(readExpanded);

  const tabs = useMemo(() => visibleTabs(advanced), [advanced]);

  const activeTab = useMemo(
    () => tabs.find((t) => location.pathname === t.match || location.pathname.startsWith(`${t.match}/`)),
    [tabs, location.pathname],
  );

  // F17: on a page built out of anchors (Security/Updates/DNS filter/
  // Reports), the sub-item the reader is actually looking at can drift away
  // from the one the URL last named the moment they scroll by hand — the
  // reference behaviour this restores is "the highlight follows the reader",
  // not "the highlight follows the last click". `useScrollSpy` returns null
  // for every other tab (no elements in the document carry its children's
  // ids), which is what keeps this from ever overriding the plain
  // route-based check on Inbox/Assets/Settings/etc.
  const activeChildIds = useMemo(
    () => (activeTab?.flat === true ? [] : (activeTab?.children?.map((c) => sectionKeyOf(c.path)) ?? [])),
    [activeTab],
  );
  const scrollSpyActive = useScrollSpy(activeChildIds);

  // The active tab's dropdown opens itself — derived during render, not
  // synchronized in through an effect. Deliberately additive: it never closes
  // a parent the user opened by hand, and the auto-open is not written back to
  // storage as if it were a choice.
  const activeKey =
    activeTab?.flat !== true && activeTab?.children !== undefined ? activeTab.key : null;

  // The auto-open above is additive on top of `expanded`, so a plain toggle
  // on the *active* row could never close it: removing the key from
  // `expanded` still leaves it forced open by `activeKey`. `manualCloseKey`
  // is that one exception, recorded only for the active row and only until
  // the active section changes — the bug this fixes was that clicking the
  // disclosure to collapse the section you are currently in did nothing at
  // all, visibly or in `aria-expanded`.
  const [manualCloseKey, setManualCloseKey] = useState<string | null>(null);
  useEffect(() => {
    setManualCloseKey(null);
  }, [activeKey]);

  const openKeys = useMemo(
    () =>
      activeKey === null || activeKey === manualCloseKey
        ? expanded
        : new Set([...expanded, activeKey]),
    [expanded, activeKey, manualCloseKey],
  );

  // `wasOpen` is the row's own rendered state, not whether `key` happens to
  // sit in `expanded` right now — those two disagree exactly for the active
  // tab, which is open via `activeKey` whether or not the user ever added it
  // to `expanded`. Toggling off `wasOpen` (rather than off `expanded`'s own
  // membership) is what makes the very first click on an auto-opened active
  // row actually close it instead of adding a now-redundant entry to
  // `expanded` while `manualCloseKey` fights it to a draw.
  const toggleExpanded = useCallback((key: string, wasOpen: boolean, isActive: boolean) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (wasOpen) next.delete(key);
      else next.add(key);
      writeExpanded(next);
      return next;
    });
    if (isActive) setManualCloseKey(wasOpen ? key : null);
  }, []);

  // Arrow keys move between rows: one roving ring over every focusable
  // control in the list (parent links, their disclosure buttons, and any
  // visible child link), in document order, so it always matches what is
  // actually on screen.
  const onKeyDown = useCallback((e: React.KeyboardEvent<HTMLUListElement>) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp" && e.key !== "Home" && e.key !== "End") return;
    const list = listRef.current;
    if (list === null) return;
    const items = [...list.querySelectorAll<HTMLElement>("a[href], button")];
    if (items.length === 0) return;
    const here = items.indexOf(document.activeElement as HTMLElement);
    let next: number;
    if (e.key === "Home") next = 0;
    else if (e.key === "End") next = items.length - 1;
    else if (here === -1) next = 0;
    else next = (here + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
    e.preventDefault();
    items[next].focus();
  }, []);

  return (
    <nav className={`g-sidebar${collapsed ? " g-sidebar-collapsed" : ""}`} aria-label="Main">
      {/* Which account you are in, and as what. This used to appear in the
          header as well, and only here when there was more than one account to
          switch between -- so an owner with a single account saw it twice on
          some screens and, after the header was condensed, nowhere at all. It
          belongs here: it is who you are, not what is happening. The control
          is a switcher only when there is something to switch to. */}
      <div className="g-sidebar-tenant">
        {showSwitcher ? (
          <>
            <label className="g-sidebar-tenant-label" htmlFor="tenant-switcher">
              {collapsed ? "Org" : "Organization"}
            </label>
            <select
              id="tenant-switcher"
              value={me?.active_tenant?.id ?? ""}
              onChange={(e) => onSwitchTenant(e.target.value)}
            >
              {tenants.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </>
        ) : (
          <p className="g-sidebar-whoami" title={`${me?.active_tenant?.name ?? ""} — signed in as ${me?.active_tenant?.role ?? ""}`}>
            <b>{collapsed ? (me?.active_tenant?.name ?? "—").slice(0, 2) : (me?.active_tenant?.name ?? "—")}</b>
            {!collapsed && me?.active_tenant?.role ? (
              <span className="g-sidebar-whoami-role">{me.active_tenant.role}</span>
            ) : null}
          </p>
        )}
      </div>

      {/* oxlint-disable-next-line jsx-a11y/no-noninteractive-element-to-interactive-role */}
      <ul className="g-sidebar-list" ref={listRef} onKeyDown={onKeyDown}>
        {GROUP_ORDER.flatMap((group) => {
          const inGroup = tabs.filter((t) => t.group === group);
          if (inGroup.length === 0) return [];
          // The heading is presentational: the rows stay one flat list so
          // roving focus and the arrow-key handler above keep working.
          return [
            <li key={`g-${group}`} className="g-sidebar-section" aria-hidden="true">
              {group}
            </li>,
            ...inGroup.map((tab) => {
          const isActive = tab === activeTab;
          // `flat` tabs keep their children for the More sheet, the command
          // palette and mode-gating, but render no disclosure here.
          const hasChildren =
            tab.flat !== true && tab.children !== undefined && tab.children.length > 0;
          // Collapsed, the dropdown is a CSS hover/focus flyout — it is in the
          // DOM whatever the persisted state says, because there is no room
          // for a disclosure control next to an icon.
          const open = collapsed ? hasChildren : hasChildren && openKeys.has(tab.key);
          const panelId = `g-nav-panel-${tab.key}`;
          // Collapsed, the children are only in a hover flyout — not on screen —
          // so the parent must carry their roll-up even though `open` is true
          // for the flyout's sake.
          const rowBadge = parentBadge(tab, badges, open && !collapsed);
          return (
            <li key={tab.key} className={`g-sidebar-group${open ? " g-sidebar-group-open" : ""}`}>
              <div className="g-sidebar-row">
                <Link
                  to={tab.path}
                  className={`g-sidebar-item${isActive ? " g-sidebar-item-active" : ""}`}
                  aria-current={isActive ? "page" : undefined}
                  aria-label={rowLabel(tab.label, rowBadge)}
                  title={collapsed ? tab.label : undefined}
                >
                  <tab.Icon />
                  {!collapsed && <span className="g-sidebar-label">{tab.label}</span>}
                  <span className="g-sidebar-badge">{renderBadge(rowBadge)}</span>
                </Link>
                {hasChildren && !collapsed && (
                  <button
                    type="button"
                    className="g-sidebar-disclosure"
                    aria-expanded={open}
                    aria-controls={panelId}
                    aria-label={`${open ? "Collapse" : "Expand"} ${tab.label}`}
                    onClick={() => toggleExpanded(tab.key, open, tab.key === activeKey)}
                  >
                    <svg viewBox="0 0 20 20" width={14} height={14} aria-hidden="true">
                      <path
                        d="M7 5 L12 10 L7 15"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="1.6"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </button>
                )}
              </div>

              {hasChildren && (
                <ul className="g-sidebar-children" id={panelId} hidden={!open}>
                  {tab.children!.map((child, i) => {
                    const current =
                      tab === activeTab && scrollSpyActive !== null
                        ? sectionKeyOf(child.path) === scrollSpyActive
                        : childIsCurrent(child, location.pathname, location.search, i === 0);
                    const childBadge = ownBadge(child.key, badges);
                    return (
                      <li key={child.key}>
                        <Link
                          to={child.path}
                          className={`g-sidebar-subitem${current ? " g-sidebar-item-active" : ""}`}
                          aria-current={current ? "page" : undefined}
                          aria-label={rowLabel(child.label, childBadge)}
                        >
                          <span className="g-sidebar-sublabel">{child.label}</span>
                          <span className="g-sidebar-badge">{renderBadge(childBadge)}</span>
                        </Link>
                      </li>
                    );
                  })}
                </ul>
              )}
            </li>
          );
            }),
          ];
        })}
      </ul>

      <button
        type="button"
        className="g-sidebar-collapse"
        onClick={onToggleCollapsed}
        aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        aria-pressed={collapsed}
      >
        {collapsed ? "»" : "« Collapse"}
      </button>
    </nav>
  );
}

export default Sidebar;
