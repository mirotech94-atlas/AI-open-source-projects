// The sign-in lattice and the markup it draws on must agree about element ids.
//
// `public/auth-lattice.js` is the marketing site's `hero-gradient.js`, re-housed
// for the portal. Re-housing it meant renaming the two ids it reaches for, and
// that rename is exactly the kind of edit that can be done PARTIALLY without
// anything noticing.
//
// It was. The first reference was renamed and the second was not, so the file
// shipped with a guard that checked `auth-gl`, passed, and then asked for
// `hero-gl` one line later:
//
//     if (!document.getElementById('auth-gl')) return;   // passes
//     const canvas = document.getElementById('hero-gl')  // null
//     canvas.getContext('webgl2')                        // TypeError
//
// Nothing in the suite could see it. The file is a static asset, not a module,
// so it is never imported, type-checked or bundled; the failure is a runtime
// TypeError on one route, and the only symptom is a sign-in screen that quietly
// renders without its background. It reached production that way.
//
// So this checks the two things a copied asset can get wrong: that no id from
// the file it was copied FROM survives, and that every id it does reach for is
// actually rendered by the page that loads it.
/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const LATTICE = readFileSync(resolve(process.cwd(), "public/auth-lattice.js"), "utf-8");
const LOGIN = readFileSync(resolve(process.cwd(), "src/pages/Login.tsx"), "utf-8");

/** Every `document.getElementById('…')` the asset performs. */
function idsUsed(js: string): string[] {
  return [...js.matchAll(/getElementById\(\s*['"]([^'"]+)['"]\s*\)/g)].map((m) => m[1]);
}

describe("the sign-in lattice agrees with the page it draws on", () => {
  it("keeps no element id from the file it was copied from", () => {
    // `hero-` is the marketing site's namespace. One surviving here means the
    // rename was partial, which is silent at build time and fatal at runtime.
    const stale = idsUsed(LATTICE).filter((id) => id.startsWith("hero-"));
    expect(stale, `auth-lattice.js still reaches for the site's ids: ${stale.join(", ")}`).toEqual([]);
  });

  it("only reaches for ids the sign-in page actually renders", () => {
    const missing = [...new Set(idsUsed(LATTICE))].filter((id) => !LOGIN.includes(`id="${id}"`));
    expect(missing, `Login.tsx renders no element with id: ${missing.join(", ")}`).toEqual([]);
  });

  it("still fetches nothing from a third party", () => {
    // It carries a vendored WebGL shader; the whole reason it is vendored is
    // that Vault reaches no third-party origin. See CLAUDE.md § Design assets.
    expect(LATTICE).not.toMatch(/https?:\/\//);
  });
});
