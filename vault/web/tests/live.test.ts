import { act, renderHook } from "@testing-library/react";
import { createElement, StrictMode } from "react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { __resetLiveFeedForTests, useLiveFeed } from "../src/lib/live";
import type { Schedule, SocketLike, Unschedule } from "../src/lib/live";
import type { LiveEvent } from "../src/types";

// This file is `.ts`, not `.tsx` (the brief's own file list), so the
// StrictMode wrapper is built with `createElement` rather than JSX.
function StrictWrapper({ children }: { children?: ReactNode }) {
  return createElement(StrictMode, null, children);
}

/** A fake `WebSocket`. `close()` is the *client* hanging up (what the hook
 *  calls when its ref count hits zero) and always fires `onclose`, same as
 *  the real API. `simulateServerClose()` is the socket dying on its own —
 *  the case the hook must reconnect from. */
class FakeSocket implements SocketLike {
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  private closed = false;
  /** Every frame the hook sent on this socket, raw — tests assert on the
   *  parsed JSON rather than the string. */
  sent: unknown[] = [];

  close(): void {
    if (this.closed) return;
    this.closed = true;
    this.onclose?.(new Event("close") as CloseEvent);
  }

  send(data: string): void {
    this.sent.push(JSON.parse(data));
  }

  open(): void {
    this.onopen?.(new Event("open"));
  }

  message(event: LiveEvent): void {
    this.onmessage?.({ data: JSON.stringify(event) } as MessageEvent);
  }

  simulateServerClose(): void {
    this.closed = true;
    this.onclose?.(new Event("close") as CloseEvent);
  }
}

function mockWebSocket() {
  const sockets: FakeSocket[] = [];
  let opened = 0;
  let closedCount = 0;
  const factory = () => {
    const s = new FakeSocket();
    const realClose = s.close.bind(s);
    s.close = () => {
      const before = (s as unknown as { closed: boolean }).closed;
      realClose();
      if (!before) closedCount += 1;
    };
    sockets.push(s);
    opened += 1;
    return s;
  };
  return {
    factory,
    sockets,
    get opened() {
      return opened;
    },
    get closed() {
      return closedCount;
    },
  };
}

type ScheduledCall = { id: number; fn: () => void; ms: number; cancelled: boolean };

/** A fully synchronous stand-in for `setTimeout`/`clearTimeout`, so the
 *  deferred-close and the reconnect backoff can be asserted without fake
 *  timers or real waiting. */
function mockScheduler() {
  let seq = 0;
  const calls: ScheduledCall[] = [];
  const schedule: Schedule = (fn, ms) => {
    const id = ++seq;
    calls.push({ id, fn, ms, cancelled: false });
    return id as unknown as ReturnType<typeof setTimeout>;
  };
  const unschedule: Unschedule = (handle) => {
    const call = calls.find((c) => c.id === (handle as unknown as number));
    if (call) call.cancelled = true;
  };
  function flushAll(): void {
    for (const call of calls) {
      if (!call.cancelled) call.fn();
    }
  }
  function latestPending(): ScheduledCall {
    const pending = calls.filter((c) => !c.cancelled);
    const last = pending[pending.length - 1];
    if (!last) throw new Error("no pending scheduled call");
    return last;
  }
  return { schedule, unschedule, calls, flushAll, latestPending };
}

afterEach(() => {
  __resetLiveFeedForTests();
});

describe("useLiveFeed", () => {
  it("opens exactly one socket across a StrictMode double mount and closes it once", () => {
    const ws = mockWebSocket();
    const sched = mockScheduler();
    const { unmount } = renderHook(
      () => useLiveFeed({ socketFactory: ws.factory, schedule: sched.schedule, unschedule: sched.unschedule }),
      { wrapper: StrictWrapper },
    );

    // Any close deferred by StrictMode's synchronous mount -> cleanup ->
    // remount was cancelled by the remount before this ever runs.
    sched.flushAll();
    expect(ws.opened).toBe(1);
    expect(ws.closed).toBe(0);

    unmount();
    sched.flushAll();
    expect(ws.closed).toBe(1);
  });

  it("reuses the open socket across two live components and closes only after both unmount", () => {
    const ws = mockWebSocket();
    const sched = mockScheduler();
    const optsA = { socketFactory: ws.factory, schedule: sched.schedule, unschedule: sched.unschedule };
    const a = renderHook(() => useLiveFeed(optsA));
    const b = renderHook(() => useLiveFeed({}));

    expect(ws.opened).toBe(1);

    a.unmount();
    sched.flushAll();
    expect(ws.closed).toBe(0); // b is still mounted

    b.unmount();
    sched.flushAll();
    expect(ws.closed).toBe(1);
  });

  it("dispatches a parsed frame to on(type, fn) and updates status/last", () => {
    const ws = mockWebSocket();
    const sched = mockScheduler();
    const { result } = renderHook(() =>
      useLiveFeed({ socketFactory: ws.factory, schedule: sched.schedule, unschedule: sched.unschedule }),
    );
    expect(result.current.status).toBe("connecting");

    const received: LiveEvent[] = [];
    act(() => {
      result.current.on("scan", (event) => received.push(event));
    });
    act(() => {
      ws.sockets[0].open();
    });
    expect(result.current.status).toBe("open");

    const frame: LiveEvent = { type: "scan", ts: "2026-09-09T00:00:00Z", data: { ok: true } };
    act(() => {
      ws.sockets[0].message(frame);
    });

    expect(received).toEqual([frame]);
    expect(result.current.last).toEqual(frame);
  });

  it("ignores a frame for a type nothing subscribed to", () => {
    const ws = mockWebSocket();
    const sched = mockScheduler();
    const { result } = renderHook(() =>
      useLiveFeed({ socketFactory: ws.factory, schedule: sched.schedule, unschedule: sched.unschedule }),
    );
    const scanFn = () => {
      throw new Error("must not fire for an alert frame");
    };
    act(() => {
      result.current.on("scan", scanFn);
    });
    act(() => {
      ws.sockets[0].message({ type: "alert", ts: "t", data: {} });
    });
    // No throw means the mismatched-type listener never ran.
    expect(result.current.last?.type).toBe("alert");
  });

  it("reconnects on an unexpected close with capped backoff 1s,2s,5s,15s,15s", () => {
    const ws = mockWebSocket();
    const sched = mockScheduler();
    renderHook(() =>
      useLiveFeed({ socketFactory: ws.factory, schedule: sched.schedule, unschedule: sched.unschedule }),
    );
    expect(ws.opened).toBe(1);

    const delays: number[] = [];
    for (let i = 0; i < 5; i++) {
      const current = ws.sockets[ws.sockets.length - 1];
      act(() => {
        current.simulateServerClose();
      });
      const pending = sched.latestPending();
      delays.push(pending.ms);
      act(() => {
        pending.fn();
      });
    }

    expect(delays).toEqual([1000, 2000, 5000, 15000, 15000]);
    expect(ws.opened).toBe(6); // the initial socket plus the five reconnects
  });

  it("resets the backoff to 1s after a reconnect succeeds", () => {
    const ws = mockWebSocket();
    const sched = mockScheduler();
    renderHook(() =>
      useLiveFeed({ socketFactory: ws.factory, schedule: sched.schedule, unschedule: sched.unschedule }),
    );

    act(() => {
      ws.sockets[0].simulateServerClose();
    });
    const first = sched.latestPending();
    expect(first.ms).toBe(1000);
    act(() => {
      first.fn();
    });
    act(() => {
      ws.sockets[1].open();
    });

    act(() => {
      ws.sockets[1].simulateServerClose();
    });
    const second = sched.latestPending();
    expect(second.ms).toBe(1000);
  });

  it("does not reconnect after a deliberate close from ref count reaching zero", () => {
    const ws = mockWebSocket();
    const sched = mockScheduler();
    const { unmount } = renderHook(() =>
      useLiveFeed({ socketFactory: ws.factory, schedule: sched.schedule, unschedule: sched.unschedule }),
    );
    unmount();
    sched.flushAll();
    expect(ws.closed).toBe(1);
    // If the deliberate close had also scheduled a reconnect, flushing every
    // pending call above would have opened a second socket.
    expect(ws.opened).toBe(1);
  });

  describe("frame validation", () => {
    // `FakeSocket.message()` JSON-stringifies a well-formed `LiveEvent`, so
    // these send the raw wire text directly through `onmessage` — exactly
    // what a malformed or hostile frame looks like on the actual socket.
    function sendRaw(socket: FakeSocket, raw: string): void {
      socket.onmessage?.({ data: raw } as MessageEvent);
    }

    it.each([
      ["a bare null", "null"],
      ["a bare number", "42"],
      ["a bare array", "[]"],
      ["an object with an unrecognized type", JSON.stringify({ type: "nope" })],
      ["an object with no type at all", JSON.stringify({ data: {} })],
      ["unparseable JSON", "{not json"],
    ])("ignores %s without throwing and counts it as dropped", (_label, raw) => {
      const ws = mockWebSocket();
      const sched = mockScheduler();
      const { result } = renderHook(() =>
        useLiveFeed({ socketFactory: ws.factory, schedule: sched.schedule, unschedule: sched.unschedule }),
      );

      expect(() => act(() => sendRaw(ws.sockets[0], raw))).not.toThrow();

      expect(result.current.last).toBeNull();
      expect(result.current.dropped).toBe(1);
    });

    it("still dispatches a well-formed frame after dropping a malformed one", () => {
      const ws = mockWebSocket();
      const sched = mockScheduler();
      const { result } = renderHook(() =>
        useLiveFeed({ socketFactory: ws.factory, schedule: sched.schedule, unschedule: sched.unschedule }),
      );

      act(() => sendRaw(ws.sockets[0], "null"));
      expect(result.current.dropped).toBe(1);

      const frame: LiveEvent = { type: "scan", ts: "2026-09-09T00:00:00Z", data: {} };
      act(() => {
        ws.sockets[0].message(frame);
      });
      expect(result.current.last).toEqual(frame);
      expect(result.current.dropped).toBe(1);
    });

    it("a throwing listener does not prevent a later listener or a later frame", () => {
      const ws = mockWebSocket();
      const sched = mockScheduler();
      const { result } = renderHook(() =>
        useLiveFeed({ socketFactory: ws.factory, schedule: sched.schedule, unschedule: sched.unschedule }),
      );

      const received: LiveEvent[] = [];
      act(() => {
        result.current.on("scan", () => {
          throw new Error("boom");
        });
        result.current.on("scan", (event) => received.push(event));
      });

      const first: LiveEvent = { type: "scan", ts: "t1", data: {} };
      const second: LiveEvent = { type: "scan", ts: "t2", data: {} };
      expect(() =>
        act(() => {
          ws.sockets[0].message(first);
        }),
      ).not.toThrow();
      act(() => {
        ws.sockets[0].message(second);
      });

      // The throwing listener never stopped the second listener from
      // running on either frame, and the socket kept processing frames
      // after the throw instead of dying.
      expect(received).toEqual([first, second]);
      // The socket itself is still alive — a throwing listener never closed
      // it or forced a reconnect.
      expect(result.current.status).not.toBe("closed");
      expect(ws.closed).toBe(0);
    });
  });

  describe("heartbeat", () => {
    // The hub pings every 25 s and closes a socket that has not answered for
    // 60 s. This client never answered -- `ping` was not a known type, so it
    // was counted as a dropped frame and ignored -- and production logged the
    // same browser reconnecting every 77 seconds, all day. Anything published
    // in the gap before the reconnect was simply never seen.
    it("answers a ping with a pong, and does not count it as dropped", () => {
      const ws = mockWebSocket();
      const sched = mockScheduler();
      const { result } = renderHook(() =>
        useLiveFeed({ socketFactory: ws.factory, schedule: sched.schedule, unschedule: sched.unschedule }),
      );
      act(() => ws.sockets[0].open());
      const before = ws.sockets[0].sent.length;

      act(() => {
        ws.sockets[0].onmessage?.({ data: JSON.stringify({ type: "ping", data: {} }) } as MessageEvent);
      });

      expect(ws.sockets[0].sent.slice(before)).toEqual([{ type: "pong" }]);
      expect(result.current.dropped).toBe(0);
      expect(result.current.last).toBeNull();
    });
  });

  describe("subscribe option", () => {
    it("sends a subscribe frame once the socket opens, and again after a reconnect", () => {
      const ws = mockWebSocket();
      const sched = mockScheduler();
      renderHook(() =>
        useLiveFeed({
          socketFactory: ws.factory,
          schedule: sched.schedule,
          unschedule: sched.unschedule,
          subscribe: ["dns"],
        }),
      );

      // Mounting before the socket opens must not send early — the hub can't
      // see a frame before the connection is actually up.
      expect(ws.sockets[0].sent).toEqual([]);

      act(() => {
        ws.sockets[0].open();
      });
      expect(ws.sockets[0].sent).toEqual([{ subscribe: ["dns"] }]);

      act(() => {
        ws.sockets[0].simulateServerClose();
      });
      const pending = sched.latestPending();
      act(() => {
        pending.fn();
      });
      act(() => {
        ws.sockets[1].open();
      });

      expect(ws.sockets[1].sent).toEqual([{ subscribe: ["dns"] }]);
    });

    it("sends the frame immediately when a second caller subscribes to an already-open socket", () => {
      const ws = mockWebSocket();
      const sched = mockScheduler();
      const a = renderHook(() =>
        useLiveFeed({ socketFactory: ws.factory, schedule: sched.schedule, unschedule: sched.unschedule }),
      );
      act(() => {
        ws.sockets[0].open();
      });
      expect(ws.sockets[0].sent).toEqual([]);

      renderHook(() => useLiveFeed({ subscribe: ["dns"] }));
      expect(ws.sockets[0].sent).toEqual([{ subscribe: ["dns"] }]);

      a.unmount();
    });

    it("is ref-counted: the type drops out of the frame only once every subscriber has unmounted", () => {
      const ws = mockWebSocket();
      const sched = mockScheduler();
      const a = renderHook(() =>
        useLiveFeed({
          socketFactory: ws.factory,
          schedule: sched.schedule,
          unschedule: sched.unschedule,
          subscribe: ["dns"],
        }),
      );
      act(() => {
        ws.sockets[0].open();
      });
      expect(ws.sockets[0].sent).toEqual([{ subscribe: ["dns"] }]);

      const b = renderHook(() => useLiveFeed({ subscribe: ["dns"] }));
      // Both callers already want "dns" — no new frame for a no-op change.
      expect(ws.sockets[0].sent).toEqual([{ subscribe: ["dns"] }]);

      b.unmount();
      // a is still mounted and still wants "dns" — nothing changed.
      expect(ws.sockets[0].sent).toEqual([{ subscribe: ["dns"] }]);

      a.unmount();
      sched.flushAll();
    });
  });
});
