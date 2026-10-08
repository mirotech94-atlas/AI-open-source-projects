import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

// The sidebar section headings are decorative and must not take clicks, so
// their class carries `pointer-events: none`. Every tab's <li> already had a
// class of its own (`g-sidebar-group` — the tab plus its dropdown). Reusing
// that name for the headings once put `pointer-events: none` on every link
// in the sidebar: Inbox, Activity, all of them, dead to the mouse while a
// programmatic click still worked. jsdom does not apply stylesheets, so a
// rendered test cannot see this; the source can.

const tsx = readFileSync(resolve(__dirname, "../src/shell/Sidebar.tsx"), "utf8");
const css = readFileSync(resolve(__dirname, "../src/shell/shell.css"), "utf8");

/** Every selector whose rule block sets pointer-events: none. */
function noPointerSelectors(): string[] {
  const out: string[] = [];
  const re = /([^{}]+)\{([^{}]*)\}/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(css)) !== null) {
    if (/pointer-events\s*:\s*none/.test(m[2])) out.push(m[1].trim());
  }
  return out;
}

/** Class names used on the <li> that wraps each real tab link. */
function tabLiClasses(): string[] {
  // The tab <li> is the one whose className is computed from `open`.
  const m = /<li key=\{tab\.key\} className=\{`([a-z-]+)/.exec(tsx);
  expect(m, "could not find the tab <li> in Sidebar.tsx").not.toBeNull();
  return [m![1]];
}

describe("sidebar tabs stay clickable", () => {
  it("the heading <li> does not share a class with the tab <li>", () => {
    const heading = /<li key=\{`g-\$\{group\}`\} className="([a-z-]+)"/.exec(tsx);
    expect(heading, "could not find the section heading <li>").not.toBeNull();
    for (const cls of tabLiClasses()) expect(heading![1]).not.toBe(cls);
  });

  it("no pointer-events:none rule can reach a tab", () => {
    const tabs = tabLiClasses();
    for (const sel of noPointerSelectors()) {
      for (const cls of tabs) {
        expect(sel, `${sel} disables pointer events and matches the tab class .${cls}`)
          .not.toMatch(new RegExp(`(^|[\\s,>+~])\\.${cls}(?![a-z0-9_-])`));
      }
    }
  });
});
