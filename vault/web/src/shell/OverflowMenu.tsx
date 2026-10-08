import { useEffect } from "react";
import { useMenu } from "./useMenu";

type Gate = { disabled: boolean; title: string };

export type OverflowMenuProps = {
  onRefresh: () => void;
  speedtest: Gate & { onClick: () => void };
  maintenance: Gate & {
    active: boolean;
    minutesLeft: number | null;
    onStart: () => void;
    onEnd: () => void;
  };
  advanced: { on: boolean; onToggle: () => void };
  onOpenWallboard: () => void;
  themeLabel: string;
  onCycleTheme: () => void;
  /** The signed-in user's display name -- the one thing today's site line
   *  showed that the proposal's context zone (site + role only) drops. It
   *  moves here rather than disappearing (design brief: "do not drop a
   *  control"; a name isn't a control, but losing it from every screen is
   *  the same kind of loss). Plain text, not a button. */
  userLine: string | null;
  onSignOut: () => void;
};

/** The `⋯` action-zone menu (design proposal §3): refresh, speed test,
 *  maintenance, advanced, wallboard, theme and sign out all move here from
 *  the flat twelve-wide row. Every gated control keeps its `disabled` +
 *  role-naming `title` through the move -- the pattern doesn't change, only
 *  where the control lives. */
export function OverflowMenu({
  onRefresh, speedtest, maintenance, advanced, onOpenWallboard, themeLabel, onCycleTheme, userLine, onSignOut,
}: OverflowMenuProps) {
  const menu = useMenu();

  useEffect(() => {
    if (!menu.open) return;
    const panel = menu.rootRef.current?.querySelector<HTMLElement>('[role="menu"]');
    const firstEnabled = panel?.querySelector<HTMLButtonElement>("button:not(:disabled)");
    firstEnabled?.focus();
  }, [menu.open, menu.rootRef]);

  const runAndClose = (fn: () => void) => () => {
    fn();
    menu.close({ restoreFocus: true });
  };

  return (
    <div className="g-menu-wrap" ref={menu.rootRef}>
      <button
        type="button"
        ref={menu.buttonRef}
        className="g-topbar-icon-button g-icon-btn"
        aria-haspopup="menu"
        aria-expanded={menu.open}
        aria-label="More actions"
        onClick={menu.toggle}
      >
        ⋯
      </button>
      {menu.open && (
        <div className="g-menu g-menu-right" role="menu" aria-label="More actions">
          <button type="button" role="menuitem" className="g-menu-item" onClick={runAndClose(onRefresh)}>
            <span>Refresh now</span>
            <span className="g-menu-item-hint">R</span>
          </button>
          <button
            type="button"
            role="menuitem"
            className="g-menu-item"
            disabled={speedtest.disabled}
            title={speedtest.title}
            onClick={runAndClose(speedtest.onClick)}
          >
            <span>Speed test</span>
          </button>
          <button
            type="button"
            role="menuitem"
            className="g-menu-item"
            disabled={maintenance.disabled}
            title={maintenance.title}
            aria-label={
              maintenance.active
                ? `Maintenance active, ${maintenance.minutesLeft} minutes left; extend by an hour`
                : "Start maintenance"
            }
            onClick={runAndClose(maintenance.onStart)}
          >
            <span>{maintenance.active ? "Extend by an hour" : "Start maintenance…"}</span>
            {maintenance.active && <span className="g-menu-item-hint">{maintenance.minutesLeft}m left</span>}
          </button>
          {/* A second, distinct control from the one above -- extending and
              ending are different actions and neither should cost the other
              (the pre-rearrangement header kept them as two separate
              buttons; moving into `⋯` must not quietly merge them). */}
          {maintenance.active && (
            <button
              type="button"
              role="menuitem"
              className="g-menu-item"
              disabled={maintenance.disabled}
              title={maintenance.disabled ? maintenance.title : "End maintenance now"}
              aria-label="End maintenance"
              onClick={runAndClose(maintenance.onEnd)}
            >
              <span>End maintenance now</span>
            </button>
          )}
          <hr className="g-menu-sep" />
          <button
            type="button"
            role="menuitemcheckbox"
            aria-checked={advanced.on}
            aria-label={`Advanced controls: ${advanced.on ? "on" : "off"}`}
            className="g-menu-item"
            onClick={runAndClose(advanced.onToggle)}
          >
            <span>Advanced controls</span>
            <span className="g-menu-item-hint">{advanced.on ? "on" : "off"}</span>
          </button>
          <button
            type="button"
            role="menuitem"
            className="g-menu-item"
            aria-label="Open the wallboard"
            onClick={runAndClose(onOpenWallboard)}
          >
            <span>Wallboard</span>
            <span className="g-menu-item-hint">⛶</span>
          </button>
          <button type="button" role="menuitem" className="g-menu-item" onClick={runAndClose(onCycleTheme)}>
            <span>Theme</span>
            <span className="g-menu-item-hint">{themeLabel}</span>
          </button>
          <hr className="g-menu-sep" />
          {userLine !== null && userLine !== "" && <div className="g-menu-static">{userLine}</div>}
          <button type="button" role="menuitem" className="g-menu-item" onClick={runAndClose(onSignOut)}>
            <span>Sign out</span>
          </button>
        </div>
      )}
    </div>
  );
}

export default OverflowMenu;
