import { useEffect, useRef } from "react";

// Key format for the `useHotkeys` map, lowercase, modifiers first in this
// order: "mod+" (⌘ on macOS, Ctrl elsewhere), "alt+", "shift+", then the key.
// Examples: "k" · "mod+k" · "escape" · "shift+?" · "j" · "arrowdown".
function eventKey(e: KeyboardEvent): string {
  const parts: string[] = [];
  if (e.metaKey || e.ctrlKey) parts.push("mod");
  if (e.altKey) parts.push("alt");
  // Shift is only significant for non-printable keys: "shift+/" would never
  // match, because the browser already reports "?" as the key.
  if (e.shiftKey && e.key.length > 1) parts.push("shift");
  parts.push(e.key.toLowerCase());
  return parts.join("+");
}

function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  return target.isContentEditable;
}

/**
 * Document-level hotkeys. Events whose target is an input, textarea, select or
 * contenteditable are ignored, so typing a device name never fires "j"/"k".
 *
 * The map lives in a ref, so callers may pass a fresh object literal on every
 * render without needing `useCallback` and without re-binding the listener.
 * The listener itself is registered once and removed on unmount — React 19
 * StrictMode double-invokes effects in development and this must be idempotent.
 */
export function useHotkeys(map: Record<string, (e: KeyboardEvent) => void>): void {
  const mapRef = useRef(map);
  // Refreshed in an effect rather than during render, so render stays pure.
  // Effects run before any user keystroke can reach the listener.
  useEffect(() => {
    mapRef.current = map;
  });

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (isTyping(e.target)) return;
      const handler = mapRef.current[eventKey(e)];
      if (!handler) return;
      handler(e);
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);
}

/** Exported for the palette's own hint line ("⌘K" on a Mac, "Ctrl K" else). */
export function modLabel(): string {
  const platform =
    typeof navigator === "undefined" ? "" : navigator.platform || "";
  return /mac|iphone|ipad/i.test(platform) ? "⌘" : "Ctrl";
}
