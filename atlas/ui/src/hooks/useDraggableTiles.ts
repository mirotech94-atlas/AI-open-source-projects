import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import type { GestureEvent } from "../voice/gestures";
import { useHand } from "./useHand";

export interface TileOffset {
  x: number;
  y: number;
}

function loadPositions(key: string): Record<string, TileOffset> {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object") return {};
    return parsed as Record<string, TileOffset>;
  } catch {
    // Private window, storage disabled, or corrupt JSON from an older
    // shape -- the tiles simply start back at their default layout.
    return {};
  }
}

function savePositions(key: string, positions: Record<string, TileOffset>): void {
  try {
    localStorage.setItem(key, JSON.stringify(positions));
  } catch {
    // Quota exceeded or storage blocked. Positions just do not survive a
    // reload; not worth surfacing over a layout preference.
  }
}

function tileIdAt(clientX: number, clientY: number): string | null {
  const el = document.elementFromPoint(clientX, clientY)?.closest<HTMLElement>("[data-tile]");
  return el?.dataset.tile ?? null;
}

/**
 * Lets any page's cards be picked up by a fist-grab and dropped somewhere
 * else, with the layout remembered per browser under `storageKey`. A page
 * opts in with one call, then tags each draggable element with
 * `data-tile={id}` and spreads `style={styleFor(id)}` onto it. Fleet's cards
 * were the first demonstration; Mission Control's AI Core Overview tiles and
 * its row-2/row-3 panels are the second, each page under its own
 * `storageKey` so the two never collide.
 *
 * Gestures are opt-in server-side (`VOICE_GESTURES`), so this checks
 * `/api/voice/capabilities` itself rather than assuming a caller already
 * has it -- a page reached to look at, not to talk or wave at, must not
 * open a camera on its own.
 */
export function useDraggableTiles(storageKey: string): {
  styleFor: (id: string) => CSSProperties | undefined;
} {
  const [enabled, setEnabled] = useState(false);
  const [positions, setPositions] = useState<Record<string, TileOffset>>(
    () => loadPositions(storageKey),
  );
  const positionsRef = useRef(positions);
  positionsRef.current = positions;
  // The object `loadPositions` returned on mount, kept by reference so the
  // save effect below can tell "still exactly what we just read back" from
  // "a drag actually changed something" -- without this, every mount writes
  // straight back what it loaded (or `{}` the first time ever), which is
  // harmless but pointless. Stable under StrictMode's double-invoked effects
  // too: nothing between the two mounts ever calls `setPositions`, so this
  // ref and `positions` are still the same object the second time around.
  const loadedPositions = useRef(positions);
  const lastCursor = useRef<{ x: number; y: number } | null>(null);
  const dragging = useRef<
    { id: string; startX: number; startY: number; base: TileOffset } | null
  >(null);

  useEffect(() => {
    let cancelled = false;
    void fetch("/api/voice/capabilities", { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : null))
      .then((body: { gestures?: boolean } | null) => {
        if (!cancelled) setEnabled(body?.gestures === true);
      })
      .catch(() => setEnabled(false));
    return (): void => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (positions === loadedPositions.current) return;
    savePositions(storageKey, positions);
  }, [storageKey, positions]);

  const onEvent = useCallback((event: GestureEvent): void => {
    if (event.kind === "cursor") {
      lastCursor.current = { x: event.x, y: event.y };
      const drag = dragging.current;
      if (!drag) return;
      const dx = (event.x - drag.startX) * window.innerWidth;
      const dy = (event.y - drag.startY) * window.innerHeight;
      setPositions((prev) => ({
        ...prev,
        [drag.id]: { x: drag.base.x + dx, y: drag.base.y + dy },
      }));
      return;
    }
    if (event.kind === "grab_start") {
      const point = lastCursor.current;
      if (!point) return;
      const id = tileIdAt(point.x * window.innerWidth, point.y * window.innerHeight);
      if (!id) return;
      dragging.current = {
        id,
        startX: point.x,
        startY: point.y,
        base: positionsRef.current[id] ?? { x: 0, y: 0 },
      };
      return;
    }
    if (event.kind === "grab_end") {
      dragging.current = null;
    }
  }, []);

  useHand(enabled, onEvent);

  const styleFor = useCallback(
    (id: string): CSSProperties | undefined => {
      const p = positions[id];
      if (!p || (p.x === 0 && p.y === 0)) return undefined;
      return { transform: `translate(${p.x}px, ${p.y}px)` };
    },
    [positions],
  );

  return { styleFor };
}
