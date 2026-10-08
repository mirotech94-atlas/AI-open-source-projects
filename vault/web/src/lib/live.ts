// The Vault live feed: one `WebSocket` per app instance to `WS /api/v1/ws`
// (spec §8, hub Task 21 — not landed yet, so this only ever talks to a real
// socket outside tests). `useLiveFeed()` is the only thing components call;
// everything else in this module is the module-level singleton that makes it
// safe to mount from more than one component (in practice: once, in `Shell`)
// and, critically, idempotent under React 19 StrictMode's synchronous
// mount → cleanup → mount on the very first commit.
//
// The singleton is ref-counted rather than opened per hook instance:
//   - mount increments the ref count; the first mount (0 -> 1) opens the
//     socket, and it also cancels any pending scheduled close.
//   - unmount decrements the ref count; the last unmount (1 -> 0) does not
//     close synchronously — it *schedules* a close. StrictMode's immediate
//     re-mount cancels that schedule before it runs, so the socket that was
//     already open is reused rather than closed-then-reopened.
//
// The scheduling primitive is injectable (`schedule`), not `setTimeout`
// directly, so `tests/live.test.ts` can assert the exact reconnect backoff
// sequence and flush the deferred close synchronously — no fake timers.

import { useCallback, useEffect, useRef, useState } from "react";
import { LIVE_EVENT_TYPES } from "../types";
import type { LiveEvent } from "../types";

const KNOWN_LIVE_EVENT_TYPES = new Set<string>(LIVE_EVENT_TYPES);

/** A frame is only ever handed to `dispatch` once it passes this: parsed
 *  JSON that is a non-null, non-array object carrying a `type` that is one
 *  of the known `LiveEvent["type"]` literals. Everything else — a bare
 *  `null`/number/array, an object with no `type`, or a `type` this build
 *  does not recognize — is a dropped frame, not a thrown error: a future
 *  hub sending a frame type this bundle predates must degrade quietly, not
 *  crash the socket. */
function isLiveEvent(value: unknown): value is LiveEvent {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const type = (value as Record<string, unknown>).type;
  return typeof type === "string" && KNOWN_LIVE_EVENT_TYPES.has(type);
}

function isPing(value: unknown): boolean {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    && (value as Record<string, unknown>).type === "ping";
}

export type LiveStatus = "connecting" | "open" | "closed";

/** The subset of the `WebSocket` API this module touches. A real
 *  `WebSocket` satisfies this; a test's fake socket only needs to. */
export type SocketLike = {
  onopen: ((ev: Event) => void) | null;
  onmessage: ((ev: MessageEvent) => void) | null;
  onclose: ((ev: CloseEvent) => void) | null;
  onerror: ((ev: Event) => void) | null;
  close(code?: number, reason?: string): void;
  send(data: string): void;
};

export type SocketFactory = () => SocketLike;

/** Timer id type, generic over `setTimeout`'s return (`number` in a browser,
 *  `NodeJS.Timeout` under ts-node) — the module never inspects it, only
 *  passes it back to `clearTimeout`/`clearSchedule`. */
type TimerHandle = ReturnType<typeof setTimeout>;

export type Schedule = (fn: () => void, ms: number) => TimerHandle;
export type Unschedule = (handle: TimerHandle) => void;

export type UseLiveFeedOptions = {
  /** Defaults to a real `WebSocket` at `wss?://<host>/api/v1/ws`. */
  socketFactory?: SocketFactory;
  /** Defaults to `setTimeout`. */
  schedule?: Schedule;
  /** Defaults to `clearTimeout`. */
  unschedule?: Unschedule;
  /** Opt into one or more frame types the hub only sends after the client
   *  asks for them (currently just "dns" — see hub Task 34). A `{"subscribe":
   *  [...]}` frame carrying every currently-wanted type is sent as soon as
   *  the socket is open, and again after every reconnect (the hub does not
   *  remember a client's subscriptions across a dropped connection). Ref-
   *  counted across every mounted `useLiveFeed({subscribe})` caller, so a
   *  second component wanting the same type is a no-op and the type drops
   *  out of the next frame once the last caller wanting it unmounts. */
  subscribe?: string[];
};

export type LiveFeed = {
  status: LiveStatus;
  last: LiveEvent | null;
  /** Subscribes to one frame `type` ("scan", "alert", "device", "fix",
   *  "dns", "upgrade" per spec §8). Returns an unsubscribe function. */
  on: (type: string, fn: (event: LiveEvent) => void) => () => void;
  /** Count of frames received on the socket and dropped — unparseable JSON,
   *  or parsed but not a recognized `LiveEvent`. Exposed for debugging only
   *  (nothing in the product surfaces it); it never throws and never stops
   *  the socket from processing the next frame. */
  dropped: number;
};

// Reconnect backoff, in ms, capped at the last value.
const BACKOFF = [1000, 2000, 5000, 15000];

function defaultSocketFactory(): SocketLike {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return new WebSocket(
    `${protocol}//${window.location.host}/api/v1/ws`,
  ) as unknown as SocketLike;
}

// --------------------------------------------------------------------------
// Module-level singleton state. One live feed per loaded app, however many
// components call the hook.

let socket: SocketLike | null = null;
let refCount = 0;
let closeHandle: TimerHandle | null = null;
let reconnectHandle: TimerHandle | null = null;
let backoffIndex = 0;
let shuttingDown = false;
let activeFactory: SocketFactory = defaultSocketFactory;
let activeSchedule: Schedule = setTimeout;
let activeUnschedule: Unschedule = clearTimeout;

let status: LiveStatus = "closed";
let lastEvent: LiveEvent | null = null;
let droppedCount = 0;

const renderSubscribers = new Set<() => void>();
const typeListeners = new Map<string, Set<(event: LiveEvent) => void>>();

// Ref-counted opt-in frame types (see `UseLiveFeedOptions.subscribe`): type ->
// number of mounted callers currently wanting it.
const subscriptionRefs = new Map<string, number>();

function sendSubscribeFrame(): void {
  if (!socket || subscriptionRefs.size === 0) return;
  socket.send(JSON.stringify({ subscribe: Array.from(subscriptionRefs.keys()) }));
}

function addSubscriptions(types: string[]): void {
  let changed = false;
  for (const type of types) {
    const prev = subscriptionRefs.get(type) ?? 0;
    subscriptionRefs.set(type, prev + 1);
    if (prev === 0) changed = true;
  }
  // Only worth a frame if the socket is already open — mount before open (the
  // common case) is covered by `onopen` sending the full set once it connects.
  if (changed && status === "open") sendSubscribeFrame();
}

function removeSubscriptions(types: string[]): void {
  let changed = false;
  for (const type of types) {
    const prev = subscriptionRefs.get(type) ?? 0;
    if (prev <= 1) {
      subscriptionRefs.delete(type);
      changed = true;
    } else {
      subscriptionRefs.set(type, prev - 1);
    }
  }
  if (changed && status === "open") sendSubscribeFrame();
}

function notifyRenderSubscribers(): void {
  for (const fn of renderSubscribers) fn();
}

function setStatus(next: LiveStatus): void {
  if (status === next) return;
  status = next;
  notifyRenderSubscribers();
}

function recordDropped(): void {
  droppedCount += 1;
  notifyRenderSubscribers();
}

function dispatch(event: LiveEvent): void {
  lastEvent = event;
  notifyRenderSubscribers();
  const fns = typeListeners.get(event.type);
  if (!fns) return;
  for (const fn of fns) {
    // A listener's bug is that listener's problem, not the socket's — one
    // throwing must not stop the rest of this frame's listeners from
    // running, and must not take the connection down for the next frame.
    try {
      fn(event);
    } catch (err) {
      console.error("live feed listener threw", err);
    }
  }
}

function connect(): void {
  if (socket) return;
  shuttingDown = false;
  setStatus("connecting");
  const s = activeFactory();
  socket = s;

  s.onopen = () => {
    backoffIndex = 0;
    setStatus("open");
    sendSubscribeFrame();
  };

  s.onmessage = (ev: MessageEvent) => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(ev.data as string);
    } catch {
      recordDropped();
      return;
    }
    // The hub's heartbeat. It is not an event and never reaches `dispatch`,
    // but it must be answered: the hub closes a socket that has not sent a
    // `pong` for 60 s (`routers/live.py` PONG_TIMEOUT_S). Before this, `ping`
    // fell through to the unknown-type branch below and was counted as a
    // dropped frame, so every open dashboard was disconnected and reconnected
    // every ~77 s, and whatever the hub published during each gap was lost.
    if (isPing(parsed)) {
      s.send(JSON.stringify({ type: "pong" }));
      return;
    }
    if (!isLiveEvent(parsed)) {
      recordDropped();
      return;
    }
    dispatch(parsed);
  };

  s.onclose = () => {
    socket = null;
    if (shuttingDown) {
      setStatus("closed");
      return;
    }
    setStatus("closed");
    scheduleReconnect();
  };

  s.onerror = () => {
    // The socket's own `onclose` still fires after an error; nothing to do
    // here beyond letting that handler run the reconnect.
  };
}

function scheduleReconnect(): void {
  if (refCount <= 0) return;
  const delay = BACKOFF[Math.min(backoffIndex, BACKOFF.length - 1)];
  backoffIndex = Math.min(backoffIndex + 1, BACKOFF.length - 1);
  reconnectHandle = activeSchedule(() => {
    reconnectHandle = null;
    if (refCount > 0) connect();
  }, delay);
}

function cancelScheduledClose(): void {
  if (closeHandle !== null) {
    activeUnschedule(closeHandle);
    closeHandle = null;
  }
}

function cancelReconnect(): void {
  if (reconnectHandle !== null) {
    activeUnschedule(reconnectHandle);
    reconnectHandle = null;
  }
}

function acquire(factory?: SocketFactory, schedule?: Schedule, unschedule?: Unschedule): void {
  cancelScheduledClose();
  // The first acquirer after the singleton is fully idle picks the transport;
  // later acquirers (a second hook instance, or a StrictMode remount) reuse
  // whatever is already running rather than each supplying their own.
  if (refCount === 0) {
    activeFactory = factory ?? defaultSocketFactory;
    activeSchedule = schedule ?? setTimeout;
    activeUnschedule = unschedule ?? clearTimeout;
    backoffIndex = 0;
  }
  refCount += 1;
  if (!socket) connect();
}

function release(): void {
  refCount = Math.max(0, refCount - 1);
  if (refCount > 0) return;
  cancelReconnect();
  // Deferred, not immediate: a StrictMode double-mount unmounts and remounts
  // synchronously, and the scheduled close is cancelled by the next `acquire`
  // before this callback ever runs.
  closeHandle = activeSchedule(() => {
    closeHandle = null;
    if (refCount > 0) return;
    if (socket) {
      shuttingDown = true;
      socket.close();
      socket = null;
    }
    setStatus("closed");
    lastEvent = null;
  }, 0);
}

function addTypeListener(type: string, fn: (event: LiveEvent) => void): () => void {
  let set = typeListeners.get(type);
  if (!set) {
    set = new Set();
    typeListeners.set(type, set);
  }
  set.add(fn);
  return () => {
    set?.delete(fn);
  };
}

/**
 * The live feed hook. Holds one `WebSocket` per app instance regardless of
 * how many components call it, reconnecting on an unexpected close with
 * backoff 1s -> 2s -> 5s -> 15s (capped). `status`/`last` re-render this
 * component on change; `on(type, fn)` is for a handler that must not itself
 * cause a re-render on every frame (e.g. invalidating a query, raising a
 * toast).
 */
export function useLiveFeed(options: UseLiveFeedOptions = {}): LiveFeed {
  const [, setTick] = useState(0);
  const optionsRef = useRef(options);

  // Refreshed in an effect rather than during render (`lib/keys.ts`'s
  // `useHotkeys` does the same) — the mount effect below reads
  // `optionsRef.current`, and effects run in declaration order on mount, so
  // this one has already run by the time that one does.
  useEffect(() => {
    optionsRef.current = options;
  });

  useEffect(() => {
    const rerender = () => setTick((t) => t + 1);
    renderSubscribers.add(rerender);
    acquire(optionsRef.current.socketFactory, optionsRef.current.schedule, optionsRef.current.unschedule);
    return () => {
      renderSubscribers.delete(rerender);
      release();
    };
    // Intentionally empty deps: this effect's job is exactly "acquire on
    // mount, release on unmount" once per component instance, regardless of
    // whether the caller passes a fresh options object on every render —
    // `optionsRef` is read at the time of the call, not captured here.
  }, []);

  useEffect(() => {
    const types = optionsRef.current.subscribe;
    if (!types || types.length === 0) return;
    addSubscriptions(types);
    return () => removeSubscriptions(types);
    // Same rationale as the acquire effect above: read once at mount time via
    // `optionsRef`, not re-run when the caller's array identity changes.
  }, []);

  const on = useCallback(
    (type: string, fn: (event: LiveEvent) => void) => addTypeListener(type, fn),
    [],
  );

  return { status, last: lastEvent, on, dropped: droppedCount };
}

/** Test-only escape hatch: resets the module singleton between test files
 *  (or test cases) that each mount their own `useLiveFeed`. Not exported
 *  from the package's public surface — imported directly from this file's
 *  path by `tests/live.test.ts`. */
export function __resetLiveFeedForTests(): void {
  cancelScheduledClose();
  cancelReconnect();
  if (socket) {
    shuttingDown = true;
    socket.close();
  }
  socket = null;
  refCount = 0;
  backoffIndex = 0;
  shuttingDown = false;
  status = "closed";
  lastEvent = null;
  droppedCount = 0;
  renderSubscribers.clear();
  typeListeners.clear();
  subscriptionRefs.clear();
}
