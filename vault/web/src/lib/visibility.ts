import { useEffect, useState } from "react";

/** Whether the document is currently the visible tab. Used to gate an
 *  interval poll (`enabled: visible`) so a backgrounded tab stops making
 *  requests entirely rather than continuing at the foreground cadence --
 *  the same discipline `site/app.js` applies to the public feed, just via
 *  React Query's `enabled` instead of a hand-rolled scheduler. Coming back
 *  to the tab re-enables the query on its normal interval rather than
 *  forcing an immediate refetch; React Query already refetches on window
 *  focus by default, which covers the same case.
 */
export function useTabVisible(): boolean {
  const [visible, setVisible] = useState(
    typeof document === "undefined" ? true : document.visibilityState === "visible",
  );
  useEffect(() => {
    function onChange() {
      setVisible(document.visibilityState === "visible");
    }
    document.addEventListener("visibilitychange", onChange);
    return () => document.removeEventListener("visibilitychange", onChange);
  }, []);
  return visible;
}
