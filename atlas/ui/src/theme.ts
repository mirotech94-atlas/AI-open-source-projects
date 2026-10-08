/**
 * Light, dark, or whatever the device prefers. A per-viewer convenience:
 * kept in localStorage (wrapped -- a private window may refuse it) and put
 * on <html data-theme>, which the Academy tokens read. "system" removes the
 * attribute so `prefers-color-scheme` decides.
 */
export type ThemeChoice = "system" | "light" | "dark";

const KEY = "atlas.theme";

export function readTheme(): ThemeChoice {
  try {
    const v = window.localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

/** The HUD's own bar colour: what the companion window, the pocket and the
 *  login gate keep. */
export const HUD_THEME_COLOR = "#05090d";

/** The browser/app bar follows the Academy paper while the shell is on
 *  screen, and goes back to the HUD's colour when it is not. */
export function syncThemeColor(onShell: boolean): void {
  const meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
  if (!meta) return;
  const paper = getComputedStyle(document.documentElement).getPropertyValue("--paper").trim();
  meta.content = onShell && paper ? paper : HUD_THEME_COLOR;
}

export function applyTheme(choice: ThemeChoice): void {
  const root = document.documentElement;
  if (choice === "system") delete root.dataset.theme;
  else root.dataset.theme = choice;
  if (document.querySelector(".ac-shell")) syncThemeColor(true);
  try {
    if (choice === "system") window.localStorage.removeItem(KEY);
    else window.localStorage.setItem(KEY, choice);
  } catch {
    /* the choice still applies for this page */
  }
}

export function nextTheme(choice: ThemeChoice): ThemeChoice {
  return choice === "system" ? "light" : choice === "light" ? "dark" : "system";
}
