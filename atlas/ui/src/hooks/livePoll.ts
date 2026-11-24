/**
 * A read that repeats on its own, as a pure controller: no React, no window.
 *
 * Used for the header pill and the status ring, which must never show a count
 * that is minutes old. Rules (the owner, 2026-11-12):
 *   - one read every `intervalMs` (about 10 s);
 *   - never two at once: a tick while one is in flight is dropped;
 *   - nothing while the tab is hidden;
 *   - coming back (focus / visible) reads at once, not at the next tick.
 * Timers and visibility are injected so the rules are testable without a clock.
 */
export interface PollDeps {
  load: () => Promise<void>;
  visible: () => boolean;
  every: (fn: () => void, ms: number) => unknown;
  cancel: (handle: unknown) => void;
}

export interface Poller {
  /** Start ticking; reads once straight away when the tab is visible. */
  start: () => void;
  stop: () => void;
  /** Read now (focus, visible, a stream event). False when dropped. */
  poke: () => boolean;
}

export function createPoller(deps: PollDeps, intervalMs: number): Poller {
  let inFlight = false;
  let handle: unknown = null;
  let running = false;

  const poke = (): boolean => {
    if (!running || inFlight || !deps.visible()) return false;
    inFlight = true;
    // `load` is expected to catch its own errors; a throw must still release
    // the slot, or the pill would freeze on its last reading.
    let started: Promise<void>;
    try { started = deps.load(); } catch { started = Promise.resolve(); }
    void started
      .catch(() => undefined)
      .finally(() => { inFlight = false; });
    return true;
  };

  return {
    start: (): void => {
      if (running) return;
      running = true;
      handle = deps.every(() => { poke(); }, intervalMs);
      poke();
    },
    stop: (): void => {
      running = false;
      if (handle !== null) deps.cancel(handle);
      handle = null;
    },
    poke,
  };
}

/** Wait at most `ms` for `p`; a read that hangs counts as a failed read, so
 *  the pill goes UNKNOWN instead of waiting on a dead connection. */
export function withTimeout<T>(p: Promise<T>, ms: number, what: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const t = setTimeout(() => reject(new Error(`${what} timed out after ${Math.round(ms / 1000)}s`)), ms);
    p.then((v) => { clearTimeout(t); resolve(v); },
           (e: unknown) => { clearTimeout(t); reject(e instanceof Error ? e : new Error(String(e))); });
  });
}
