import { useEffect, useRef, useState, type JSX, type ReactNode } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import { applyTheme, nextTheme, readTheme, syncThemeColor, type ThemeChoice } from "../theme";

/**
 * The ATLAS Academy chrome around every screen: the brand, Home, one
 * "More" menu holding every other page, the health pill, the feed's state,
 * the device badges, the theme and sign out. Miro, 2026-10-06: "simpler to
 * understand ... one page only with all the necessary things I need" --
 * the home page is that page; everything else is one click into More.
 */

/** The ATLAS mark on the Academy tile: the load and the bearing frame of
 *  `Mark.tsx`, white on navy, its span in the store's marigold. */
export function AcademyMark({ size = 34 }: { size?: number }): JSX.Element {
  return (
    <svg viewBox="0 0 32 32" width={size} height={size} role="img" aria-label="ATLAS"
         className="ac-mark">
      <rect width="32" height="32" rx="7" className="ac-mark-tile" />
      <g fill="none" strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}>
        <circle cx="16" cy="8.4" r="2.9" className="ac-mark-load" stroke="none" />
        <path d="M6.6 25.6L13.2 13.4M25.4 25.6L18.8 13.4" className="ac-mark-legs" />
        <path d="M9.6 20.2H22.4" className="ac-mark-span" />
      </g>
    </svg>
  );
}

export interface MoreItem { to: string; label: string }

/** Every page that is not Home, plus the two links the old bottom bar held. */
export function MoreMenu({ items, waiting, onSignOut }: {
  items: readonly MoreItem[]; waiting: number | null; onSignOut: () => void;
}): JSX.Element {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const location = useLocation();

  // A page picked from the menu closes it; so do Escape and a click outside.
  useEffect(() => setOpen(false), [location.pathname]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent): void => { if (e.key === "Escape") setOpen(false); };
    const onClick = (e: MouseEvent): void => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onClick);
    return (): void => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onClick);
    };
  }, [open]);

  const here = items.some((i) => i.to === location.pathname);
  return (
    <div className="ac-more" ref={box}>
      <button type="button" className="ac-navlink" aria-expanded={open}
              aria-controls="ac-more-menu" data-on={here || undefined}
              onClick={() => setOpen((o) => !o)}>
        More
        <svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true">
          <path d="M2.5 4.5 6 8l3.5-3.5" fill="none" stroke="currentColor" strokeWidth="1.6"
                strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {open && (
        <div className="ac-more-menu" id="ac-more-menu">
          <nav aria-label="All pages">
            <ul>
              {items.map((i) => (
                <li key={i.to}>
                  <NavLink to={i.to} end className={({ isActive }) => (isActive ? "on" : "")}>
                    {i.label}
                    {i.to === "/tasks" && waiting !== null && waiting > 0 && (
                      <span className="ac-count">{waiting}</span>
                    )}
                  </NavLink>
                </li>
              ))}
            </ul>
          </nav>
          <div className="ac-more-extra">
            <a href="/brief">Executive briefing</a>
            <a href="/api/self/atlas.vcf" download="ATLAS.vcf"
               title="ATLAS's number and face, so the phone shows it when ATLAS calls">
              Save ATLAS to Contacts
            </a>
            {/* The header's own sign-out is hidden at phone width. */}
            <button type="button" className="ac-btn ac-btn-ghost ac-more-signout" onClick={onSignOut}>
              Sign out
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

const THEME_WORD: Record<ThemeChoice, string> = {
  system: "Theme: device", light: "Theme: light", dark: "Theme: dark",
};

function ThemeButton(): JSX.Element {
  const [choice, setChoice] = useState<ThemeChoice>(() => readTheme());
  return (
    <button type="button" className="ac-icon-btn" title={THEME_WORD[choice]}
            aria-label={`${THEME_WORD[choice]}. Change theme`}
            onClick={() => {
              const next = nextTheme(choice);
              applyTheme(next);
              setChoice(next);
            }}>
      <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">
        {choice === "dark" ? (
          <path d="M15.5 12.2A6.5 6.5 0 0 1 7.8 4.5a6.5 6.5 0 1 0 7.7 7.7z" fill="currentColor" />
        ) : choice === "light" ? (
          <g fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
            <circle cx="10" cy="10" r="3.4" />
            <path d="M10 2.5v2M10 15.5v2M2.5 10h2M15.5 10h2M4.7 4.7l1.4 1.4M13.9 13.9l1.4 1.4M4.7 15.3l1.4-1.4M13.9 6.1l1.4-1.4" />
          </g>
        ) : (
          <g fill="none" stroke="currentColor" strokeWidth="1.6">
            <circle cx="10" cy="10" r="6.5" />
            <path d="M10 3.5a6.5 6.5 0 0 1 0 13z" fill="currentColor" stroke="none" />
          </g>
        )}
      </svg>
    </button>
  );
}

export function AcademyHeader({
  items, health, pill, pillWhy, feed, updated, waiting, badges, search, onSignOut,
}: {
  items: readonly MoreItem[];
  health: string;
  pill: string;
  pillWhy: string;
  feed: "live" | "connecting" | "dropped";
  updated: Date | null;
  waiting: number | null;
  badges: ReactNode;
  search?: ReactNode;
  onSignOut: () => void;
}): JSX.Element {
  // The app bar wears the paper while the shell is up and follows the
  // device's light/dark switch live; leaving the shell restores the HUD's.
  useEffect(() => {
    syncThemeColor(true);
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    const onChange = (): void => syncThemeColor(true);
    media?.addEventListener("change", onChange);
    return (): void => {
      media?.removeEventListener("change", onChange);
      syncThemeColor(false);
    };
  }, []);
  return (
    <header className="ac-top ac-skin">
      <Link to="/" className="ac-brand" aria-label="ATLAS Academy, home">
        <AcademyMark />
        <span className="ac-brand-name">ATLAS <small>Academy</small></span>
      </Link>
      <nav className="ac-nav" aria-label="Main">
        <NavLink to="/" end className={({ isActive }) => `ac-navlink${isActive ? " on" : ""}`}>
          Home
        </NavLink>
        <MoreMenu items={items} waiting={waiting} onSignOut={onSignOut} />
      </nav>
      {search}
      <span className="ac-pill" data-health={health} title={pillWhy}>
        <span className={`d${health === "unknown" ? " hatch" : ""}`} aria-hidden="true" />
        {pill}
      </span>
      <span className="ac-feed" data-state={feed}
            title={updated ? `Last update ${updated.toLocaleTimeString([], { hour12: false })}` : undefined}>
        <span className="dot" aria-hidden="true" />
        {feed === "live" ? "Live" : feed === "connecting" ? "Connecting" : "Disconnected"}
        <span className="ac-feed-t">
          {updated ? ` · ${updated.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })}` : ""}
        </span>
      </span>
      <span className="ac-badges">{badges}</span>
      <ThemeButton />
      <button type="button" className="ac-btn ac-btn-ghost ac-signout" onClick={onSignOut}>
        Sign out
      </button>
    </header>
  );
}

/** Where, the weather and the link: the old bottom bar's three cells. */
export function AcademyFooter({ location, weather, network, networkOk }: {
  location: string;
  weather: ReactNode;
  network: string;
  networkOk: boolean;
}): JSX.Element {
  return (
    <footer className="ac-foot ac-skin">
      <span><span className="k">Location</span> {location}</span>
      <span><span className="k">Weather</span> {weather}</span>
      <span data-ok={networkOk || undefined}><span className="k">Network</span> {network}</span>
    </footer>
  );
}
