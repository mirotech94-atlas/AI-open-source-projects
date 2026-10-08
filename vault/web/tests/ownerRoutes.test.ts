// Every link in the owner rail must have a route behind it.
//
// `owner.test.tsx` already asserts WHICH links the rail lists, and its comment
// says that assertion is updated as each section lands rather than relaxed,
// "because a nav entry leading to a placeholder makes the console feel broken
// rather than unfinished".
//
// That guard has a hole, and the hole was walked straight into: it checks the
// links, not the routes. Overview, Fleet and Billing were added to the rail and
// shipped with NO matching <Route> in App.tsx, because the edit that was meant
// to add them silently did not apply. The nav test went green -- the links were
// all present and correct -- and every one of them fell through to the
// catch-all, which redirects to the tenant dashboard. A console where three of
// seven tabs bounce you out of the console.
//
// Nothing else could catch it. The components existed, were imported nowhere,
// and an unused module is not a type error; the build was clean and 644 tests
// passed.
//
// So this reads the two files as text and checks they agree. Deliberately
// static rather than a render test: rendering proves a route resolves under
// whatever router the test sets up, while the actual failure was that the
// route was never written down at all.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const SHELL = readFileSync(resolve(process.cwd(), "src/owner/OwnerShell.tsx"), "utf-8");
const APP = readFileSync(resolve(process.cwd(), "src/App.tsx"), "utf-8");

/** Every `to:` in the rail's SECTIONS list. */
function railTargets(): string[] {
  const block = SHELL.slice(SHELL.indexOf("const SECTIONS"), SHELL.indexOf("];", SHELL.indexOf("const SECTIONS")));
  return [...block.matchAll(/to:\s*"([^"]+)"/g)].map((m) => m[1]);
}

/** The owner subtree's routes, as the paths they resolve to. `index` is the
 *  parent itself; a child `path="x"` resolves to `/owner/x`. */
function ownerRoutes(): string[] {
  const start = APP.indexOf('<Route path="/owner"');
  expect(start, "App.tsx no longer mounts an /owner subtree").toBeGreaterThan(-1);
  // The subtree ends at its closing tag; everything after belongs to the app.
  const end = APP.indexOf("</Route>", start);
  const block = APP.slice(start, end);
  const out: string[] = [];
  if (/<Route\s+index/.test(block)) out.push("/owner");
  for (const m of block.matchAll(/<Route\s+path="([^"]+)"/g)) {
    if (m[1] === "/owner") continue;
    out.push(`/owner/${m[1]}`);
  }
  return out;
}

describe("the owner rail and the router agree", () => {
  it("has a route behind every link the rail lists", () => {
    const routes = new Set(ownerRoutes());
    const dead = railTargets().filter((t) => !routes.has(t));
    expect(
      dead,
      `the rail links to ${dead.join(", ")}, which App.tsx has no route for — ` +
        "clicking falls through to the catch-all and leaves the console",
    ).toEqual([]);
  });

  it("imports a component for every owner route it declares", () => {
    // The other half of the same mistake: a route can be written down and its
    // component never imported, which IS a type error -- but only once the
    // route exists. Checked here so both halves fail in the same place.
    const block = APP.slice(APP.indexOf('<Route path="/owner"'), APP.indexOf("</Route>", APP.indexOf('<Route path="/owner"')));
    const used = [...block.matchAll(/element=\{<(\w+)\s*\/>\}/g)].map((m) => m[1]);
    const missing = [...new Set(used)].filter((c) => !new RegExp(`import\\s+${c}\\s+from`).test(APP));
    expect(missing, `owner routes render ${missing.join(", ")} with no import`).toEqual([]);
  });
});
