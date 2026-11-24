import { useCallback, useEffect, useRef, useState } from "react";
import type { Fresh } from "./useFresh";
import { createPoller, withTimeout, type Poller } from "./livePoll";

export const LIVE_HEALTH_MS = 10_000;
const READ_TIMEOUT_MS = 8_000;

export interface LiveRead<T> extends Fresh<T> {
  /** When the newest read ENDED, ok or not: the pill's own read time. */
  attemptedAt?: Date | null;
}

/**
 * `useFresh`, but it repeats itself every ~10 s (never overlapping, paused
 * while the tab is hidden, an immediate read on focus) and a failed read
 * replaces the data with an error rather than leaving the last good one on
 * screen: the caller shows UNKNOWN, never a stale OK. `bump` (the stream's
 * reload counter) also triggers a read.
 */
export function useLiveRead<T>(
  load: () => Promise<T | null>,
  enabled: boolean,
  bump: number,
  what = "read",
  intervalMs = LIVE_HEALTH_MS,
): LiveRead<T | null> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchedAt, setFetchedAt] = useState<Date | null>(null);
  const [attemptedAt, setAttemptedAt] = useState<Date | null>(null);
  const loadRef = useRef(load);
  loadRef.current = load;
  const poller = useRef<Poller | null>(null);

  useEffect(() => {
    if (!enabled) { setData(null); return undefined; }
    let alive = true;
    const p = createPoller({
      load: async () => {
        try {
          const result = await withTimeout(loadRef.current(), READ_TIMEOUT_MS, what);
          if (!alive) return;
          setData(result);
          setError(null);
          const now = new Date();
          setFetchedAt(now);
          setAttemptedAt(now);
        } catch (err: unknown) {
          if (!alive) return;
          // The last good reading is NOT kept: a failed read must show as
          // unknown (invariant 7), not as the last number it happened to see.
          setData(null);
          setError(err instanceof Error ? err.message : String(err));
          setAttemptedAt(new Date());
        } finally {
          if (alive) setLoading(false);
        }
      },
      visible: () => typeof document === "undefined" || document.visibilityState !== "hidden",
      every: (fn, ms) => window.setInterval(fn, ms),
      cancel: (h) => window.clearInterval(h as number),
    }, intervalMs);
    poller.current = p;
    const wake = (): void => { p.poke(); };
    document.addEventListener("visibilitychange", wake);
    window.addEventListener("focus", wake);
    p.start();
    return (): void => {
      alive = false;
      p.stop();
      poller.current = null;
      document.removeEventListener("visibilitychange", wake);
      window.removeEventListener("focus", wake);
    };
  }, [enabled, intervalMs, what]);

  // A stream event (or any caller bump) reads now, subject to the same
  // no-overlap rule.
  useEffect(() => { poller.current?.poke(); }, [bump]);

  const reload = useCallback((): void => { poller.current?.poke(); }, []);
  return { data, error, loading, fetchedAt, attemptedAt, reload };
}
