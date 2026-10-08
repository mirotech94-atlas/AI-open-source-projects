import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterEach } from "vitest";

// Testing Library gives an async query one second to find its element, and on
// a loaded machine that is a measurement of the machine rather than of the
// component. The suite runs 57 files in parallel workers and vitest's own
// summary reports jsdom being constructed 50 times for ~65s of wall clock, a
// third of the run -- so a worker can simply not be scheduled inside a second.
//
// It showed as flakiness with no fixed address: three consecutive full runs
// failed `networks.test.tsx`, then nothing, then `nav.test.tsx`, while each of
// those files passed on its own every time. The element was always there; the
// clock ran out first.
//
// Five seconds is still an upper bound, not a wait: a query that finds its
// element returns the moment it appears, so a passing suite is no slower. A
// component that genuinely never renders still fails, just later and for the
// real reason. This is the wall-clock rule from CLAUDE.md applied to the web
// track -- a test must not be able to fail because something else was running.
configure({ asyncUtilTimeout: 5000 });

// With `globals: false` Testing Library cannot register its own auto-cleanup,
// so unmount between tests explicitly — otherwise a Drawer from one test keeps
// its document-level Escape listener alive during the next one.
afterEach(() => {
  cleanup();
});

// jsdom ships neither of these, and the design system uses both: Chart/Heatmap
// measure their container with a ResizeObserver, and the theme hook reads
// `prefers-color-scheme` through matchMedia.
if (!("ResizeObserver" in globalThis)) {
  class ResizeObserverStub {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  }
  Object.defineProperty(globalThis, "ResizeObserver", {
    writable: true,
    value: ResizeObserverStub,
  });
}

if (!window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}
