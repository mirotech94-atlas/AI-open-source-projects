/* Pre-paint theme boot.
 *
 * Loaded as a classic, non-deferred <script src> from the first line of
 * <head>, so it runs synchronously before the first paint and before the
 * module graph loads. A dark-mode user therefore never sees a flash of the
 * light palette while src/lib/theme.ts's useTheme() mounts.
 *
 * It lives in a real file rather than inline in index.html so the app can be
 * served under a Content-Security-Policy of script-src 'self' with no
 * 'unsafe-inline' and no hash to keep in step. See deploy/nginx-guardian.conf.
 *
 * This duplicates src/lib/themeBoot.ts's applyPersistedTheme() by hand, on
 * purpose: importing it would put it in the module graph, which is exactly
 * what makes it too late. Keep the two in sync. themeBoot.test.ts covers the
 * logic, not this copy.
 */
(function () {
  try {
    var value = window.localStorage.getItem("guardian.theme");
    if (value === "light" || value === "dark") {
      document.documentElement.dataset.theme = value;
    }
  } catch (e) {
    // A private window or blocked site data can throw on read. The page still
    // renders, just with the system palette.
  }
})();
