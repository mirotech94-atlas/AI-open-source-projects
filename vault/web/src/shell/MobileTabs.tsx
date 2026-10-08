import { useEffect, useRef, useState } from "react";
import { NavLink } from "react-router-dom";
import { Badge } from "../design";
import { NO_BADGES, ownBadge, rolledUpBadge } from "./badges";
import type { NavBadge, NavBadges } from "./badges";
import { visibleTabs } from "./nav";

const PRIMARY_KEYS = ["overview", "inbox", "assets", "security", "dns"];

/**
 * Bottom tab bar for narrow viewports. Rendered unconditionally and hidden
 * above 700px by `shell.css` (`@media (min-width: 700px)`) rather than a JS
 * media-query check — jsdom's `matchMedia` stub always reports
 * `matches: false`, so a JS-gated bar would never mount under Vitest.
 *
 * The bar holds five parents; "More" holds *the rest of the tree*, not just the
 * rest of the parents. That distinction matters: a phone has neither the
 * sidebar's disclosure buttons nor its collapsed flyout, so if the sheet listed
 * only parents then every dropdown child — CVEs, Reboot planner, Users & roles,
 * Networks, the Inbox filters — would be unreachable on mobile. So the sheet is
 * the whole nav: each parent, then its children indented beneath it. The five
 * bar parents appear in it too; a duplicate route is cheaper than a missing one.
 *
 * Counts appear here too, and they have to roll up: the bar has no disclosure
 * control, so a child's number (Recommendations, say) can only reach a phone
 * through its parent's badge.
 */
/** Plain text — what the number counts rides on the row's own accessible name
 *  (`rowLabel`), because a bare `<span>` may not carry an `aria-label`. */
function renderBadge(b: NavBadge | null) {
  if (b === null) return null;
  return <Badge tone={b.tone}>{b.text}</Badge>;
}

function rowLabel(label: string, b: NavBadge | null): string | undefined {
  return b === null ? undefined : `${label}, ${b.label}`;
}

export function MobileTabs({
  badges = NO_BADGES,
  advanced = true,
}: {
  badges?: NavBadges;
  /** Simple mode drops the advanced-only rows. Defaults to advanced so a
   *  direct render still sees the whole tree. */
  advanced?: boolean;
}) {
  const [moreOpen, setMoreOpen] = useState(false);
  const moreButtonRef = useRef<HTMLButtonElement | null>(null);
  const sheetRef = useRef<HTMLDivElement | null>(null);

  // Every other dismissible surface in the shell (the ⌘K palette, the chat
  // popover, `design/Drawer.tsx`) closes on an outside click and on Escape.
  // This sheet had neither — the only way to dismiss it was tapping a link
  // inside it — so a tap on the scrim, a primary tab, or the page behind it
  // left it standing open. `mousedown` (not `click`) is what a native menu
  // dismisses on, so it beats the click that would otherwise also activate
  // whatever is underneath.
  useEffect(() => {
    if (!moreOpen) return;
    function onPointerDown(e: MouseEvent) {
      const target = e.target as Node;
      if (sheetRef.current?.contains(target)) return;
      if (moreButtonRef.current?.contains(target)) return;
      setMoreOpen(false);
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      setMoreOpen(false);
      moreButtonRef.current?.focus();
    }
    document.addEventListener("mousedown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown, true);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [moreOpen]);

  const tabs = visibleTabs(advanced);
  // Two of the five preferred bar keys (Security, DNS filter) are
  // advanced-only, so in simple mode the intersection is three — a bar with
  // two empty slots. Fill it from the remaining visible parents in tree order
  // instead, keeping the bar five wide in both modes.
  const primary = [
    ...tabs.filter((t) => PRIMARY_KEYS.includes(t.key)),
    ...tabs.filter((t) => !PRIMARY_KEYS.includes(t.key)),
  ].slice(0, 5);

  return (
    <nav className="g-mobile-tabs" aria-label="Tabs">
      <ul className="g-mobile-tabs-list">
        {primary.map((tab) => (
          <li key={tab.key}>
            <NavLink
              to={tab.path}
              className={({ isActive }) => `g-mobile-tab${isActive ? " g-mobile-tab-active" : ""}`}
              aria-label={rowLabel(tab.label, rolledUpBadge(tab, badges) ?? ownBadge(tab.key, badges))}
            >
              <tab.Icon />
              <span>{tab.label}</span>
              {renderBadge(rolledUpBadge(tab, badges) ?? ownBadge(tab.key, badges))}
            </NavLink>
          </li>
        ))}
        <li>
          <button
            type="button"
            className="g-mobile-tab"
            aria-haspopup="true"
            aria-expanded={moreOpen}
            ref={moreButtonRef}
            onClick={() => setMoreOpen((v) => !v)}
          >
            <span>More</span>
          </button>
        </li>
      </ul>

      {moreOpen && (
        <div className="g-mobile-more" role="menu" aria-label="All tabs" ref={sheetRef}>
          {tabs.map((tab) => (
            <div className="g-mobile-more-group" key={tab.key}>
              <NavLink
                to={tab.path}
                role="menuitem"
                className="g-mobile-more-item"
                aria-label={rowLabel(tab.label, ownBadge(tab.key, badges))}
                onClick={() => setMoreOpen(false)}
              >
                <tab.Icon />
                <span>{tab.label}</span>
                {renderBadge(ownBadge(tab.key, badges))}
              </NavLink>
              {(tab.children ?? []).map((child) => (
                <NavLink
                  key={child.key}
                  to={child.path}
                  role="menuitem"
                  className="g-mobile-more-child"
                  aria-label={rowLabel(child.label, ownBadge(child.key, badges))}
                  onClick={() => setMoreOpen(false)}
                >
                  <span>{child.label}</span>
                  {renderBadge(ownBadge(child.key, badges))}
                </NavLink>
              ))}
            </div>
          ))}
        </div>
      )}
    </nav>
  );
}

export default MobileTabs;
