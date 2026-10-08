// F17: sub-nav routes are anchors into one long page (`lib/section.ts`
// documents why — Security/Updates/DNS filter/Reports draw every panel at
// once rather than splitting into routed children). A verification walk of
// the live product found the anchor never actually lands: `/security/
// attack-map` opened at scrollY 0 with its heading thousands of pixels below
// the fold, on both a cold load of the URL and a client-side nav.
//
// The reproducer below is the mechanism the live walk almost certainly hit:
// panels above the target fill in from a loading placeholder to their real
// (much taller) content as their queries resolve — same shape as
// `Security.tsx`'s `EMPTY_SECURITY` — which silently un-does a scroll taken
// before that growth happens. A single scroll-into-view at mount only ever
// gets the *first* layout right.
import { act, render } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { sectionId, useScrollSpy, useSectionAnchor } from "../src/lib/section";

/** A stand-in for a Security/Updates-shaped page: the target section exists
 *  from the first paint (an id on a `Card`, unconditional), but a panel
 *  ABOVE it grows once data "arrives" a tick later — exactly what turns a
 *  correct scroll into a wrong one without anything re-triggering the hook. */
function GrowingPage({ grow }: { grow: boolean }) {
  useSectionAnchor();
  return (
    <div id="g-shell-main-content" style={{ overflow: "auto", height: 600 }}>
      <div style={{ height: grow ? 6000 : 40 }}>panel above</div>
      <div id={sectionId("attack-map")}>attack map</div>
    </div>
  );
}

function renderAt(path: string, grow: boolean) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/security" element={<GrowingPage grow={grow} />} />
        <Route path="/security/:section" element={<GrowingPage grow={grow} />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("useSectionAnchor", () => {
  beforeEach(() => {
    // jsdom does not implement `scrollIntoView` at all; every test here
    // overrides it with its own spy anyway, but the property has to exist
    // first for `vi.spyOn` to have something to wrap.
    if (!("scrollIntoView" in HTMLElement.prototype)) {
      (HTMLElement.prototype as { scrollIntoView?: () => void }).scrollIntoView = () => {};
    }
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("re-issues the scroll once a panel above the target grows after mount", async () => {
    const calls: Element[] = [];
    vi.spyOn(HTMLElement.prototype, "scrollIntoView").mockImplementation(function (this: Element) {
      calls.push(this);
    });

    const { rerender } = render(
      <MemoryRouter initialEntries={["/security/attack-map"]}>
        <Routes>
          <Route path="/security/:section" element={<GrowingPage grow={false} />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(calls.length).toBeGreaterThan(0);
    calls.length = 0;

    // The panel above the target grows — a query resolving — with no route
    // change and so no re-run of `[section]`'s dependency array. A correct
    // implementation must still correct the scroll.
    await act(async () => {
      rerender(
        <MemoryRouter initialEntries={["/security/attack-map"]}>
          <Routes>
            <Route path="/security/:section" element={<GrowingPage grow={true} />} />
          </Routes>
        </MemoryRouter>,
      );
      await new Promise((r) => setTimeout(r, 50));
    });

    expect(calls.some((el) => el.id === sectionId("attack-map"))).toBe(true);
  });

  it("scrolls the target into view on a cold mount of the URL", () => {
    const spy = vi
      .spyOn(HTMLElement.prototype, "scrollIntoView")
      .mockImplementation(function () {});
    renderAt("/security/attack-map", false);
    expect(spy).toHaveBeenCalled();
  });

  it("respects prefers-reduced-motion by scrolling instantly", () => {
    const spy = vi
      .spyOn(HTMLElement.prototype, "scrollIntoView")
      .mockImplementation(function () {});
    const original = window.matchMedia;
    window.matchMedia = ((q: string) => ({
      matches: q.includes("prefers-reduced-motion"),
      media: q,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    })) as typeof window.matchMedia;

    renderAt("/security/attack-map", false);
    expect(spy).toHaveBeenCalledWith(expect.objectContaining({ behavior: "auto" }));
    window.matchMedia = original;
  });

  it("re-runs the scroll when the section param changes under one mounted instance — same shape as back/forward", async () => {
    const calls: string[] = [];
    vi.spyOn(HTMLElement.prototype, "scrollIntoView").mockImplementation(function (this: Element) {
      calls.push(this.id);
    });

    function TwoSections() {
      useSectionAnchor();
      const navigate = useNavigate();
      return (
        <div id="g-shell-main-content">
          <button onClick={() => navigate("/security/attack-map")}>go</button>
          <div id={sectionId("scorecards")}>scorecards</div>
          <div id={sectionId("attack-map")}>attack map</div>
        </div>
      );
    }

    const { getByText } = render(
      <MemoryRouter initialEntries={["/security/scorecards"]}>
        <Routes>
          <Route path="/security/:section" element={<TwoSections />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(calls).toContain(sectionId("scorecards"));
    calls.length = 0;

    await act(async () => {
      getByText("go").click();
    });
    expect(calls).toContain(sectionId("attack-map"));
  });
});

// F17 (second half): the sidebar's highlighted sub-item must track where the
// reader actually scrolled to, not just the route they last landed on — a
// reader who scrolls from "Attack map" on to "Exposure" by hand should see
// the highlight follow, even with no `/security/exposure` route to have
// clicked. `useScrollSpy` is the geometry behind that; jsdom lays out
// nothing, so each section's `getBoundingClientRect` is mocked directly
// rather than actually scrolling real content.
describe("useScrollSpy", () => {
  function Probe({ ids }: { ids: string[] }) {
    const active = useScrollSpy(ids);
    return <div data-testid="active">{active ?? "(none)"}</div>;
  }

  function mockRect(el: Element, top: number) {
    vi.spyOn(el, "getBoundingClientRect").mockReturnValue({
      top,
      bottom: top + 10,
      left: 0,
      right: 0,
      width: 0,
      height: 10,
      x: 0,
      y: top,
      toJSON: () => ({}),
    });
  }

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns null when the route's children are not anchors on this page (Assets, Inbox…)", () => {
    const { getByTestId } = render(
      <div id="g-shell-main-content">
        <Probe ids={["websites", "devices"]} />
      </div>,
    );
    expect(getByTestId("active").textContent).toBe("(none)");
  });

  it("picks the section whose heading has scrolled to (or past) the top of the container", async () => {
    const { getByTestId, container } = render(
      <div id="g-shell-main-content">
        <Probe ids={["scorecards", "attack-map", "exposure"]} />
        <div id={sectionId("scorecards")} />
        <div id={sectionId("attack-map")} />
        <div id={sectionId("exposure")} />
      </div>,
    );
    const shell = container.querySelector("#g-shell-main-content")!;
    mockRect(shell, 0);
    mockRect(document.getElementById(sectionId("scorecards"))!, -400);
    mockRect(document.getElementById(sectionId("attack-map"))!, 10);
    mockRect(document.getElementById(sectionId("exposure"))!, 900);

    // The reader has scrolled past "scorecards" entirely and is sitting just
    // below the top of "attack-map" — that is the one in view, not
    // "scorecards" (already passed) and not "exposure" (not reached).
    await act(async () => {
      shell.dispatchEvent(new Event("scroll"));
      await new Promise((r) => requestAnimationFrame(() => setTimeout(r, 0)));
    });
    expect(getByTestId("active").textContent).toBe("attack-map");

    // Scroll on further, past attack-map and onto exposure.
    mockRect(document.getElementById(sectionId("attack-map"))!, -1200);
    mockRect(document.getElementById(sectionId("exposure"))!, 5);
    await act(async () => {
      shell.dispatchEvent(new Event("scroll"));
      await new Promise((r) => requestAnimationFrame(() => setTimeout(r, 0)));
    });
    expect(getByTestId("active").textContent).toBe("exposure");
  });
});
