import { expect, loginAsDemo, test } from "./fixtures";

// Task: the owner's report — "sometimes collapse menu does not work and
// closing dropdowns doesn't work either" — plus an independent verification
// walk that recorded synthetic clicks landing on the right sidebar link and
// producing no click event at the document at all. This spec checks the two
// things a jsdom component test cannot: real hit-target geometry, and
// keyboard-only reachability once CSS (not React) is deciding what's
// visible.
test.describe("sidebar collapse + dropdown interactions", () => {
  test("the pinned collapse control is the actual top hit at its own coordinates", async ({ page }) => {
    await loginAsDemo(page);
    await page.goto("/overview");

    const collapseButton = page.getByRole("button", { name: "Collapse sidebar" });
    await expect(collapseButton).toBeVisible();

    // Expand every dropdown that has one, so the list is as tall as it ever
    // gets — the CSS comment in shell.css says this is exactly the condition
    // that used to push the pinned control out of view before the scrolling
    // list was added. If something still overlaps it, this is where it shows.
    // Re-query each time: expanding one row turns its own button from
    // "Expand X" into "Collapse X", which changes both the match set and the
    // layout, so a stale snapshot of locators drifts out of sync with the DOM.
    const expandButton = page.getByRole("button", { name: /^expand /i }).first();
    while (await expandButton.count() > 0) {
      await expandButton.click();
    }

    // The element actually hit at the button's own center must be the button
    // itself (or a descendant of it), not a sibling that happens to overlap.
    const box = await collapseButton.boundingBox();
    expect(box).not.toBeNull();
    const { x, y, width, height } = box!;
    const hit = await page.evaluate(
      ([cx, cy]) => {
        const el = document.elementFromPoint(cx, cy);
        return el?.closest(".g-sidebar-collapse") !== null;
      },
      [x + width / 2, y + height / 2] as [number, number],
    );
    expect(hit).toBe(true);

    // And the click actually collapses the sidebar.
    await collapseButton.click();
    await expect(page.getByRole("button", { name: "Expand sidebar" })).toBeVisible();
  });

  test("a parent's children are reachable by keyboard alone when the sidebar is collapsed", async ({ page }) => {
    await loginAsDemo(page);
    await page.goto("/settings/profile");

    await page.getByRole("button", { name: "Collapse sidebar" }).click();
    await expect(page.getByRole("button", { name: "Expand sidebar" })).toBeVisible();

    // Collapsed, there is no disclosure button (the flyout is hover/focus
    // driven purely by CSS) — so the only way in in is Tab. Config's own row
    // link, then its children, should be reachable in document order.
    const configLink = page.getByRole("link", { name: /^Config/ });
    await configLink.focus();
    await expect(configLink).toBeFocused();

    await page.keyboard.press("Tab");
    const focused = await page.evaluate(() => document.activeElement?.getAttribute("href"));
    // A keyboard user tabbing past the collapsed Config row must land on one
    // of its children next (a flyout link), not skip straight past the whole
    // group to the next top-level tab — that would mean the flyout never
    // became visible/focusable at all.
    expect(focused).not.toBeNull();
    expect(focused).toMatch(/^\/settings\//);
  });
});
