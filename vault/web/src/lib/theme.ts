// The top bar's theme toggle: cycles `system -> light -> dark -> system`,
// writes `data-theme` on `<html>` and persists the choice in
// `localStorage["guardian.theme"]`.
//
// "system" is not a `data-theme` value — `tokens.css` keys its dark palette
// off `:root:not([data-theme="light"])` inside a `prefers-color-scheme: dark`
// media query, so "follow the OS" means the attribute is *absent*, never
// `data-theme="system"`.

import { useCallback, useEffect, useLayoutEffect, useState } from "react";

export type Theme = "system" | "light" | "dark";

const STORAGE_KEY = "guardian.theme";
const ORDER: readonly Theme[] = ["system", "light", "dark"];

// Two places call `useTheme()` at once: the top bar's cycle button (`Shell`)
// and Settings → Appearance's three-way control — each is its own React
// state, so a change made in one would otherwise sit unreflected in the
// other until something unrelated caused it to re-render. This is a plain
// listener set, not a persisted store: every mount still seeds its `theme`
// from `readStored()` (so tests that clear `localStorage` between cases stay
// isolated), and a listener only exists while its component is mounted.
const listeners = new Set<(theme: Theme) => void>();

function broadcast(theme: Theme): void {
  for (const listener of listeners) listener(theme);
}

function isTheme(value: string | null): value is Theme {
  return value === "system" || value === "light" || value === "dark";
}

/** Reads the persisted choice. Wrapped in try/catch per the sidebar's own
 *  convention (`localStorage["guardian.sidebar"]`) — a private window or a
 *  browser with site data blocked must not throw on load. */
function readStored(): Theme {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return isTheme(value) ? value : "system";
  } catch {
    return "system";
  }
}

function writeStored(theme: Theme): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // Persistence is a convenience; the in-memory state still cycles.
  }
}

function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  if (theme === "system") {
    delete root.dataset.theme;
  } else {
    root.dataset.theme = theme;
  }
}

export function nextTheme(theme: Theme): Theme {
  return ORDER[(ORDER.indexOf(theme) + 1) % ORDER.length];
}

/** `theme` is the persisted choice; `cycle()` advances it and `set()` jumps to
 *  a specific value — both write the `data-theme` attribute immediately (not
 *  just on the next render) so a chart or `<Ring>` reading a CSS variable
 *  synchronously after the click sees the new skin, and both are mirrored
 *  into every other mounted `useTheme()` caller. */
export function useTheme(): { theme: Theme; cycle: () => void; set: (next: Theme) => void } {
  const [theme, setTheme] = useState<Theme>(() => readStored());

  // `useLayoutEffect`, not `useEffect`: the index.html inline boot script
  // (see `lib/themeBoot.ts`) already applied the persisted choice before
  // first paint, but on a *change* (the theme toggle, or a state update from
  // any other cause) a plain effect would still run after the browser has
  // painted the previous palette — a visible flash on every cycle, not just
  // on load. `cycle()`/`set()` below already apply the attribute
  // synchronously on click for the same reason; this effect covers every
  // other path to a new `theme` value.
  useLayoutEffect(() => {
    applyTheme(theme);
  }, [theme]);

  // Registers for cross-instance updates for exactly as long as this
  // component is mounted; `setTheme` itself is the listener, so a change
  // broadcast from another `useTheme()` caller re-renders this one too.
  useEffect(() => {
    listeners.add(setTheme);
    return () => {
      listeners.delete(setTheme);
    };
  }, []);

  const cycle = useCallback(() => {
    setTheme((current) => {
      const next = nextTheme(current);
      writeStored(next);
      applyTheme(next);
      broadcast(next);
      return next;
    });
  }, []);

  const set = useCallback((next: Theme) => {
    writeStored(next);
    applyTheme(next);
    setTheme(next);
    broadcast(next);
  }, []);

  return { theme, cycle, set };
}
