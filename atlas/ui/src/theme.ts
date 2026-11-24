/**
 * Dark only (brand, 2026-10-12: black + royal blue on everything). The
 * light and "device" choices are gone; the type keeps its old members so an
 * older saved value still reads, but every choice resolves to dark. Put on
 * <html data-theme="dark">, which the Academy tokens read.
 */
export type ThemeChoice = "system" | "light" | "dark";

const KEY = "atlas.theme";

/** The brand, for anything that needs it in code (the bar colour, canvas
 *  drawing). The storefront's dark tokens (atlas/modules/academy/assets/
 *  style.css); command.css and desk.css carry the same values. */
export const BRAND = {
  black: "#0a0d14",
  ground: "#0a0d14",
  surface: "#131a28",
  shelf: "#1b2334",
  royal: "#5b8cff",
  royalBright: "#8fb0ff",
  onRoyal: "#070a12",
  focus: "#8fb0ff",
  ink: "#eef2fa",
  ink2: "#c9d2e3",
  muted: "#8e99ae",
} as const;

export function readTheme(): ThemeChoice {
  return "dark";
}

/** The bar colour off the shell (the companion window, the pocket, the
 *  login gate): the brand ground, as on the storefront. */
export const HUD_THEME_COLOR = BRAND.ground;

/** The browser/app bar follows the Academy paper while the shell is on
 *  screen, and goes back to the HUD's colour when it is not. */
export function syncThemeColor(onShell: boolean): void {
  const meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
  if (!meta) return;
  const paper = getComputedStyle(document.documentElement).getPropertyValue("--paper").trim();
  meta.content = onShell && paper ? paper : HUD_THEME_COLOR;
}

export function applyTheme(_choice: ThemeChoice = "dark"): void {
  const root = document.documentElement;
  root.dataset.theme = "dark";
  if (document.querySelector(".ac-shell")) syncThemeColor(true);
  try {
    window.localStorage.removeItem(KEY);   // an old light/system choice
  } catch {
    /* nothing saved, nothing to clear */
  }
}

export function nextTheme(_choice: ThemeChoice): ThemeChoice {
  return "dark";
}
