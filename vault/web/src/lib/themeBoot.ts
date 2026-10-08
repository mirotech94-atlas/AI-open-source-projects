// Pre-paint theme boot. `theme.ts`'s `useTheme()` only applies `data-theme`
// from a `useLayoutEffect`, which still runs after the browser's first
// paint — long enough for a dark-mode user to see a flash of the light
// palette on every reload. `index.html` inlines a tiny script, as the very
// first thing in `<head>`, that runs before any CSS is even parsed and
// applies the same persisted choice synchronously.
//
// This function is that script's logic, factored out so it has a test.
// `index.html` cannot `import` it (it runs before the module graph loads,
// on purpose — that's what makes it pre-paint), so the inline script
// duplicates these same few lines by hand. Keep the two in sync: `theme.ts`
// only ever writes `"system" | "light" | "dark"` to the same storage key,
// and "system" is deliberately not applied here since the absence of
// `data-theme` *is* "system" (see `theme.ts`'s header comment).
export const THEME_STORAGE_KEY = "guardian.theme";

/** The subset of `Storage` this needs — lets a test pass a plain object
 *  instead of touching `window.localStorage`. */
export type StorageLike = { getItem(key: string): string | null };

/** The subset of `Element` this needs, so a test can pass a plain object
 *  instead of `document.documentElement`. */
export type ThemeRootLike = { dataset: { theme?: string } };

export function applyPersistedTheme(storage: StorageLike, root: ThemeRootLike): void {
  try {
    const value = storage.getItem(THEME_STORAGE_KEY);
    if (value === "light" || value === "dark") {
      root.dataset.theme = value;
    }
  } catch {
    // A private window or a browser with site data blocked can throw on
    // read — the page still renders, just with the system palette.
  }
}
