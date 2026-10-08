import type { ReactNode } from "react";
import { Link, useInRouterContext } from "react-router-dom";

// Exactly one of `onClick`/`href`: an in-page action (scroll to and focus a
// section already on this tab) vs. a cross-tab action. `href` renders a
// router `<Link>` when a `<Router>` is present — a client-side navigation
// that keeps the query cache instead of a full reload — and falls back to a
// plain anchor when it isn't, via `useInRouterContext()`. That fallback is
// what keeps this safe for the router-less unit tests that render
// `EmptyState`'s callers (`ScorecardGrid`, `DeviceGrid`) directly: a router
// hook/component throws outside a `<Router>`, so the check has to happen
// before either branch renders, not just at the moment of navigation.
export type EmptyStateAction =
  | { label: string; onClick: () => void; href?: undefined }
  | { label: string; href: string; onClick?: undefined };

export type EmptyStateProps = {
  /** A small monoline glyph, same convention as `Sidebar.tsx`'s tab icons —
   *  decorative only (`aria-hidden`), the `title` already carries the
   *  meaning a screen reader needs. */
  icon?: ReactNode;
  title: string;
  body?: ReactNode;
  action?: EmptyStateAction;
  className?: string;
};

/** The one "this tab (or this tab's section) has nothing to show yet" panel
 *  every page reaches for, so a fresh tenant — or a filtered view with no
 *  matches — never renders a bare blank rectangle. Deliberately heavier than
 *  the older `<p className="g-asset-empty">…</p>` one-liners scattered
 *  through the components: those still suit a small nested list (no queries
 *  recorded, no docker containers), while `EmptyState` is for the moment a
 *  whole panel would otherwise be empty and the user needs a next step, not
 *  just a note. */
export function EmptyState({ icon, title, body, action, className = "" }: EmptyStateProps) {
  const inRouter = useInRouterContext();
  return (
    <div className={`g-empty-state ${className}`.trim()}>
      {icon !== undefined && (
        <div className="g-empty-state-icon" aria-hidden="true">
          {icon}
        </div>
      )}
      <p className="g-empty-state-title">{title}</p>
      {body !== undefined && <p className="g-empty-state-body">{body}</p>}
      {action && "href" in action && action.href !== undefined ? (
        inRouter ? (
          <Link to={action.href} className="g-empty-state-action g-button-link">
            {action.label}
          </Link>
        ) : (
          <a href={action.href} className="g-empty-state-action g-button-link">
            {action.label}
          </a>
        )
      ) : action ? (
        <button type="button" className="g-empty-state-action" onClick={action.onClick}>
          {action.label}
        </button>
      ) : null}
    </div>
  );
}

export default EmptyState;
