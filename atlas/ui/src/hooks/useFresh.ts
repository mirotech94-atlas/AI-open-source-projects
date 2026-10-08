import { useCallback, useEffect, useState } from "react";

/**
 * A fetch that always records when it happened.
 *
 * Every number on a dashboard screen is supposed to be a fresh read with a
 * timestamp beside it. Bundling the timestamp into the fetch makes that the
 * default rather than something each page has to remember.
 */
export interface Fresh<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  fetchedAt: Date | null;
  reload: () => void;
}

export function useFresh<T>(load: () => Promise<T>, deps: unknown[] = []): Fresh<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchedAt, setFetchedAt] = useState<Date | null>(null);
  const [tick, setTick] = useState(0);

  const reload = useCallback((): void => setTick((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    load()
      .then((result) => {
        if (cancelled) return;
        setData(result);
        setError(null);
        setFetchedAt(new Date());
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        // The previous data is deliberately kept so the screen can show it
        // alongside the staleness, rather than blanking to nothing.
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return (): void => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, ...deps]);

  return { data, error, loading, fetchedAt, reload };
}
