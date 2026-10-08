import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// The owner console has no palette of its own.
//
// It used to have one, and this test used to enforce the opposite of what it
// enforces now. The console committed to a paper ground and re-declared the
// app's whole `--color-*` set on `.g-owner`, so that shared components -- the
// Table, the Badge, the Drawer, buttons, form controls -- arrived in paper and
// ink rather than in the app's colours. The guard here made the console answer
// for every semantic token the app declared, so that adding a token to
// `tokens.css` failed loudly instead of being discovered from a screenshot.
//
// That property is deliberately gone, and this comment exists so the next
// reader does not restore it as an oversight. The console shares the customer
// portal's skin now; inheriting a new token silently IS the correct behaviour,
// and a test demanding the console re-state each one would be a test demanding
// it diverge again.
//
// What replaces it is the stronger invariant, because it makes drift
// impossible rather than merely noisy: this file declares no colour. Every
// `--own-*` name is an alias onto an app token, and there is no hex or rgb()
// literal anywhere in it. A console that cannot name a colour cannot acquire a
// second palette by accident, which is exactly how the first one grew.

const SRC = join(__dirname, "..", "src");
const owner = readFileSync(join(SRC, "owner", "owner.css"), "utf8");

/** The file with comments stripped -- the rewrite's rationale quotes the brand
 *  blue by hex, and prose about a colour is not a declaration of one. */
function code(): string {
  return owner.replace(/\/\*[\s\S]*?\*\//g, "");
}

describe("the owner console shares the app's palette", () => {
  it("does not rescope the app's tokens onto a palette of its own", () => {
    // The tripled selector was how the console outranked `tokens.css`'s dark
    // blocks. Its presence means someone has re-skinned the console; its
    // absence is what lets the app's tokens reach it untouched.
    expect(
      code(),
      "owner.css rescopes the app palette again — the console is meant to inherit it",
    ).not.toContain(".g-owner.g-owner.g-owner");
  });

  it("never re-declares a --color-* token", () => {
    const redeclared = [...code().matchAll(/^\s*(--(?:color|shadow)[a-z0-9-]*)\s*:/gm)].map((m) => m[1]);
    expect(
      [...new Set(redeclared)],
      "the console must read the app's tokens, not answer for them",
    ).toEqual([]);
  });

  it("resolves every --own-* name to an app token", () => {
    // The one indirection the file is allowed. It exists so the rest of
    // `owner.css` can keep its own vocabulary; it is not a place to put a
    // value. `--own-act` may point at a token via the same rule as the rest.
    const bad = [...code().matchAll(/^\s*(--own-[a-z0-9-]*)\s*:\s*([^;]+);/gm)]
      .filter((m) => !/^var\(--color-[a-z0-9-]+\)$/.test(m[2].trim()))
      .map((m) => `${m[1]}: ${m[2].trim()}`);
    expect(bad, `these name a value instead of an app token: ${bad.join("; ")}`).toEqual([]);
  });

  it("contains no colour literal at all", () => {
    const literals = [...code().matchAll(/#[0-9A-Fa-f]{3,8}\b|\brgba?\(/g)].map((m) => m[0]);
    expect(
      literals,
      `owner.css names ${literals.join(", ")} directly; colour belongs in tokens.css`,
    ).toEqual([]);
  });
});
