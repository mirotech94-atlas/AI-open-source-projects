import { useEffect, useId, useRef } from "react";
import type { HTMLAttributes, ReactNode } from "react";

export type DrawerProps = {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  subtitle?: ReactNode;
  /** Sub-navigation, normally a `<Tabs …>`, pinned under the header. */
  tabs?: ReactNode;
  children?: ReactNode;
  /** Any CSS width. Capped at the viewport, so a phone gets a full-width sheet. */
  width?: string | number;
  className?: string;
} & Omit<HTMLAttributes<HTMLDivElement>, "title" | "children">;

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function Drawer({
  open,
  onClose,
  title,
  subtitle,
  tabs,
  children,
  width = "min(560px, 100vw)",
  className = "",
  ...rest
}: DrawerProps) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const titleId = useId();

  // Escape is bound on `document`, not the panel: the key must close the drawer
  // whatever has focus (including nothing, right after it opens).
  useEffect(() => {
    if (!open) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  // Remember the trigger, move focus into the panel, and put focus back where
  // it came from on close — the drawer is opened from a row or a card, and
  // losing that place makes keyboard browsing unusable.
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement;
    returnFocusRef.current = previous instanceof HTMLElement ? previous : null;
    const panel = panelRef.current;
    const first = panel?.querySelector<HTMLElement>(FOCUSABLE);
    (first ?? panel)?.focus();
    return () => {
      returnFocusRef.current?.focus();
      returnFocusRef.current = null;
    };
  }, [open]);

  if (!open) return null;

  // Tab and Shift-Tab cycle inside the panel while it is open.
  function trapTab(e: React.KeyboardEvent<HTMLDivElement>) {
    if (e.key !== "Tab") return;
    const panel = panelRef.current;
    if (!panel) return;
    const items = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
      (el) => el.offsetParent !== null || el === panel,
    );
    if (items.length === 0) {
      e.preventDefault();
      panel.focus();
      return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement;
    if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    } else if (e.shiftKey && (active === first || active === panel)) {
      e.preventDefault();
      last.focus();
    }
  }

  return (
    <div
      className="g-drawer-backdrop"
      // A click on the scrim closes; a click inside the panel must not, so the
      // test is on the event target rather than on bubbling.
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        style={{ width }}
        className={`g-drawer ${className}`.trim()}
        onKeyDown={trapTab}
        {...rest}
      >
        <div className="g-drawer-head">
          <div className="g-drawer-titles">
            <h2 className="g-drawer-title" id={titleId}>
              {title}
            </h2>
            {subtitle !== undefined && <p className="g-drawer-subtitle">{subtitle}</p>}
          </div>
          <button
            type="button"
            className="g-drawer-close"
            aria-label="Close"
            onClick={onClose}
          >
            ×
          </button>
        </div>
        {tabs !== undefined && <div className="g-drawer-tabs">{tabs}</div>}
        <div className="g-drawer-body">{children}</div>
      </div>
    </div>
  );
}

export default Drawer;
