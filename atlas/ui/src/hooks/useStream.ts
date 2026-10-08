import { useEffect, useRef, useState } from "react";

/**
 * Live updates over SSE.
 *
 * A dashboard that silently stops updating is worse than one that admits it is
 * stale, so the connection state is part of the return value and every screen
 * renders it. `lastEventAt` distinguishes "connected and quiet" from
 * "connected and dead": the server sends a heartbeat every 15 seconds, so a
 * gap longer than that is a real problem even while the socket looks open.
 */
export type StreamState = "connecting" | "live" | "dropped";

export interface StreamStatus {
  state: StreamState;
  lastEventAt: Date | null;
  reconnects: number;
}

/** The event's JSON payload, or null when it carries none or is not JSON. */
function parse(ev: Event): Record<string, unknown> | null {
  const raw = (ev as MessageEvent<unknown>).data;
  if (typeof raw !== "string") return null;
  try {
    const value: unknown = JSON.parse(raw);
    return value && typeof value === "object" ? value as Record<string, unknown> : null;
  } catch {
    return null;
  }
}

export function useStream(
  onEvent: (kind: string, data: Record<string, unknown> | null) => void,
): StreamStatus {
  const [state, setState] = useState<StreamState>("connecting");
  const [lastEventAt, setLastEventAt] = useState<Date | null>(null);
  const [reconnects, setReconnects] = useState(0);
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    let source: EventSource | null = null;
    let retry: number | undefined;
    let closed = false;

    const connect = (): void => {
      source = new EventSource("/api/stream", { withCredentials: true });

      source.onopen = (): void => setState("live");

      source.onerror = (): void => {
        if (closed) return;
        setState("dropped");
        source?.close();
        // Fixed 5s. Exponential backoff on a single-user mesh dashboard just
        // means a longer wait before it works again.
        retry = window.setTimeout(() => {
          setReconnects((n) => n + 1);
          connect();
        }, 5000);
      };

      const mark = (kind: string) => (ev: Event): void => {
        setLastEventAt(new Date());
        setState("live");
        if (kind !== "heartbeat" && kind !== "hello") handler.current(kind, parse(ev));
      };

      // "agent": an agent run started or finished (harness.record_run and
      // the run loops). A card's expiry arrives as a "decision".
      for (const kind of ["hello", "heartbeat", "check", "finding",
                          "proposal", "decision", "agent"]) {
        source.addEventListener(kind, mark(kind));
      }
    };

    connect();
    return (): void => {
      closed = true;
      if (retry !== undefined) window.clearTimeout(retry);
      source?.close();
    };
  }, []);

  return { state, lastEventAt, reconnects };
}
