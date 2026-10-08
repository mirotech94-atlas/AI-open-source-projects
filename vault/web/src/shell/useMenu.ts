// A small popup-menu behaviour shared by the header's site switcher and its
// overflow (`⋯`) menu (Task 83, design proposal §3): outside click closes it,
// Escape closes it and returns focus to the button that opened it, and
// opening one of these menus closes every other one that is currently open --
// there is never more than one popup open in the header at a time.
//
// `OPEN_MENUS` is a module-level registry (not React state) precisely so two
// unrelated `useMenu()` instances -- the site switcher and the overflow menu,
// neither aware of the other -- can still close each other. It holds a close
// function per open menu; opening a new one calls every other registered
// close function before registering its own.
import { useCallback, useEffect, useRef, useState } from "react";

const OPEN_MENUS = new Set<() => void>();

let nextId = 0;

export function useMenu() {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const idRef = useRef(`menu-${++nextId}`);

  const close = useCallback((opts?: { restoreFocus?: boolean }) => {
    setOpen(false);
    if (opts?.restoreFocus) buttonRef.current?.focus();
  }, []);

  const openMenu = useCallback(() => {
    // Closing every other open menu before this one registers itself is what
    // makes "opening one closes another" work without either menu knowing
    // the other exists.
    for (const closeOther of OPEN_MENUS) closeOther();
    setOpen(true);
  }, []);

  const toggle = useCallback(() => {
    setOpen((prev) => {
      if (prev) {
        buttonRef.current?.focus();
        return false;
      }
      for (const closeOther of OPEN_MENUS) closeOther();
      return true;
    });
  }, []);

  useEffect(() => {
    const closer = () => close();
    if (open) {
      OPEN_MENUS.add(closer);
      return () => {
        OPEN_MENUS.delete(closer);
      };
    }
    return undefined;
  }, [open, close]);

  useEffect(() => {
    if (!open) return;

    function onPointerDown(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.stopPropagation();
        close({ restoreFocus: true });
      }
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open, close]);

  return { open, toggle, openMenu, close, rootRef, buttonRef, id: idRef.current };
}

export type UseMenuResult = ReturnType<typeof useMenu>;
