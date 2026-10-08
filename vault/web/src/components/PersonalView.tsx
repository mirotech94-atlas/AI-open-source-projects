import { useEffect } from "react";
import { usePreferences } from "../lib/preferences";
import { useTheme } from "../lib/theme";

/**
 * Applies the signed-in person's stored display preferences. Renders nothing.
 *
 * This used to be a control strip above every tab — a View picker
 * (Owner / Customer / Technician), a Spacing select, a response-time-strip
 * checkbox and a saved-views list. All of it is gone: it sat at the top-left
 * of all fourteen tabs, it was the first thing a client read on a security
 * product, and none of it answered a question they had. Spacing and theme are
 * set in Config, where settings live.
 *
 * What survives is the part that was never a control: reading the stored
 * preferences and applying them to the document, which the stylesheets and
 * the theme both depend on. Deleting the component outright would have
 * dropped that with the UI and quietly reverted everyone to defaults.
 */
export function PersonalView() {
  const prefs = usePreferences();
  const { set } = useTheme();
  useEffect(() => {
    if (!prefs.ready) return;
    document.documentElement.dataset.density = prefs.value.density;
    document.documentElement.dataset.ticker = prefs.value.show_ticker ? "show" : "hide";
    set(prefs.value.theme);
  }, [prefs.ready, prefs.value.density, prefs.value.show_ticker, prefs.value.theme, set]);
  return null;
}
