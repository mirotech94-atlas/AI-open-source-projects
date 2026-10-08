import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

/**
 * Sub-view routing for a page that draws all of its panels at once.
 *
 * Four tabs (Security, Updates, DNS filter, Reports) are one dense page with
 * several panels, and the sidebar's dropdown offers each panel as its own row.
 * Rather than splitting those pages into four routed children each — which
 * would hide panels the owner wants visible together — every panel carries
 * `id={sectionId("<key>")}` and the child route scrolls to it.
 *
 * The result is what the nav promises: each child is a real, linkable,
 * bookmarkable route, and landing on it puts that panel in view.
 *
 * `prefers-reduced-motion` is respected — the scroll is instant then.
 */
export function sectionId(key: string): string {
  return `g-sec-${key}`;
}

/** The id `Shell` puts on the scrolling `<main>` — the ancestor every panel
 *  actually scrolls inside (`.g-shell-content` has its own `overflow: auto`,
 *  so the window itself never moves). Shared here rather than re-typed at
 *  each call site. */
const SCROLL_CONTAINER_ID = "g-shell-main-content";

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function scrollSectionIntoView(section: string): boolean {
  const el = document.getElementById(sectionId(section));
  if (el === null) return false;
  el.scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth", block: "start" });
  return true;
}

/** How long after a route change this keeps correcting the scroll position.
 *  Long enough for the page's own queries (security/updates/dns/reports
 *  payloads) to land and their placeholders to grow into real content —
 *  short enough that it is long gone before anyone would call the result
 *  "the page won't stop scrolling itself". */
const SETTLE_MS = 2500;

export function useSectionAnchor(): string | undefined {
  const { section } = useParams<{ section?: string }>();

  useEffect(() => {
    if (section === undefined || section === "") return;

    // The first attempt runs against whatever is on screen right now, which
    // on a cold load or a fresh navigation is very often still the panels'
    // loading placeholders — Security's `EMPTY_SECURITY`, Updates'/DNS's/
    // Reports' equivalents. That first scroll lands correctly for the layout
    // as it exists in this instant, and is worth doing immediately rather
    // than waiting on anything async.
    scrollSectionIntoView(section);

    const container = document.getElementById(SCROLL_CONTAINER_ID);
    if (container === null) return;

    // A reader who scrolls by hand mid-settle has made a deliberate choice;
    // a correction that fought that would be worse than the bug this is
    // fixing. `wheel`/`touchmove` are actual input, unlike the plain
    // `scroll` events the corrective scrollIntoView calls themselves fire.
    let userScrolled = false;
    function onUserScroll() {
      userScrolled = true;
    }
    container.addEventListener("wheel", onUserScroll, { passive: true });
    container.addEventListener("touchmove", onUserScroll, { passive: true });

    // Re-issue the scroll whenever the container's own subtree changes — a
    // panel above the target swapping its loading placeholder for real
    // content (a query resolving) silently pushes the target down again
    // below the fold, with nothing else here that would notice. A
    // `MutationObserver` on the scroll container catches that regardless of
    // which panel caused it; `ResizeObserver` was the first instinct here but
    // does not actually fire in every environment this needs to run in, and
    // a DOM mutation is what causes the growth in the first place anyway.
    let observer: MutationObserver | null = null;
    if (typeof MutationObserver !== "undefined") {
      observer = new MutationObserver(() => {
        if (userScrolled) return;
        scrollSectionIntoView(section);
      });
      observer.observe(container, { childList: true, subtree: true, attributes: true });
    }

    const timeoutId = window.setTimeout(() => {
      observer?.disconnect();
      observer = null;
    }, SETTLE_MS);

    return () => {
      observer?.disconnect();
      window.clearTimeout(timeoutId);
      container.removeEventListener("wheel", onUserScroll);
      container.removeEventListener("touchmove", onUserScroll);
    };
  }, [section]);

  return section;
}

/** Which of `ids` (each a bare key, as passed to `sectionId`) has scrolled
 *  furthest past the top of `#g-shell-main-content` without going past it
 *  entirely — the reader has "arrived" at a section once its heading has
 *  crossed the top of the scroll area, same rule any scrollspy nav uses.
 *
 *  Exists so the sidebar's highlighted sub-item can track where a reader
 *  actually is on one of these long pages, not just where the URL's
 *  `:section` last pointed — a reader who scrolls by hand past "Attack map"
 *  and on to "Exposure" should see the sidebar follow, even though there is
 *  no `/security/exposure` route to have navigated to.
 *
 *  Returns `null` when none of `ids` has a matching element in the document
 *  at all — the page at this route does not use anchors (Assets, Inbox,
 *  Settings…), and the sidebar should fall back to its plain route-based
 *  "current" check instead of pinning the first child forever. */
export function useScrollSpy(ids: readonly string[]): string | null {
  const [active, setActive] = useState<string | null>(null);
  const idsKey = ids.join("|");

  useEffect(() => {
    const container = document.getElementById(SCROLL_CONTAINER_ID);

    function recompute() {
      if (container === null) {
        setActive(null);
        return;
      }
      const top = container.getBoundingClientRect().top;
      let best: string | null = null;
      let bestOffset = -Infinity;
      let anyFound = false;
      for (const id of idsKey === "" ? [] : idsKey.split("|")) {
        const el = document.getElementById(sectionId(id));
        if (el === null) continue;
        anyFound = true;
        const offset = el.getBoundingClientRect().top - top;
        // The band a few pixels below the container's own top edge counts as
        // "arrived"; a section whose top is still further down has not been
        // reached yet, and loses to whichever earlier one has.
        if (offset <= 24 && offset > bestOffset) {
          bestOffset = offset;
          best = id;
        }
      }
      if (!anyFound) {
        setActive(null);
        return;
      }
      // Nothing has scrolled past yet (top of the page) — the first section
      // is the one "in view".
      setActive(best ?? idsKey.split("|")[0] ?? null);
    }

    recompute();
    if (container === null) return;

    let raf = 0;
    function onScroll() {
      if (raf !== 0) return;
      raf = window.requestAnimationFrame(() => {
        raf = 0;
        recompute();
      });
    }
    container.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      container.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      if (raf !== 0) window.cancelAnimationFrame(raf);
    };
  }, [idsKey]);

  return active;
}
