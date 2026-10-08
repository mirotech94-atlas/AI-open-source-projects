// The marketing site's half of the brand spine.
//
// `contrast.test.ts` guards `src/styles/tokens.css` — the portal. Nothing
// guarded `site/styles.css` at all until this file, even though it is the
// surface a stranger sees first and the only one served to the public
// internet. Two different obligations are checked here, and they are separate
// on purpose.
//
// 1. THE BRAND INVARIANT. The site and the portal are separate stylesheets
//    that cannot import each other — the site is static HTML served from
//    `site/`, with no build step and no bundler to resolve a shared module,
//    and the portal's tokens have to stay literal hex in one file because
//    `contrast.test.ts` parses them out of it by regex. So the two are held
//    together the way `hub/tests/test_remedy.py` holds `remedy`'s fix tables
//    against `sensor/.../fix.py`: by a test that reads the other file and
//    fails when they diverge.
//
//    Only ONE value is pinned, and it is worth saying why it is only one.
//    Measured 2026-09-18, the brand blue `#1257c4` is the sole value the two
//    files share exactly, and it appears in the portal's *light* skin even
//    though the site is dark. That is not an accident: the blue does two
//    different jobs chosen by ground. On a light page it is the interactive
//    colour. On the dark site it is a saturated *fill* carrying white text.
//    On a dark page the interactive colour lightens to `#66a6ff` instead, so
//    the dark skin has no counterpart to pin against.
//
//    Every other role is deliberately left free — see the note on the status
//    triad below.
//
// 2. CONTRAST. Same WCAG 2 arithmetic as `contrast.test.ts`, over the pairs
//    this stylesheet actually paints. Pairs it never draws are not tested:
//    a naive audit flags `--deep` on `--bg` at 2.95:1 and is wrong, because
//    `--deep` appears exactly once (`site/styles.css`'s `.btn` rule) as a
//    BACKGROUND with `color: #fff` on it — and that real pair is 6.59:1.
//    Testing the pair the product never renders would fail the build over a
//    defect that does not exist.
//
// WHY THE STATUS TRIAD IS NOT PINNED TO THE PORTAL'S. The site's alert red is
// `#ff6b5e`; the portal's dark skin uses `#e2647c`. That looks like drift and
// is really a different constraint. The portal splits every status colour in
// two — a mark strength for dots and strokes, and a darker `-text` strength
// that clears AA on its own `-soft` ground. The site has no such split: it
// has exactly one ground, and it paints `--ok`/`--warn`/`--crit` BOTH as
// marks (`.rail-dot`, `.gauge-bar`) and as text (`.badge.ok`, `.price.free`,
// `.form-msg.crit`). A single token doing both jobs on a dark ground has to
// sit lighter and more saturated than a token that only ever has to work on a
// dedicated soft ground. Forcing the portal's values here would be visible,
// and would be optimising for file-diff tidiness over the thing that matters,
// which is the ratio asserted below.
//
// NOTE ON THE FILE IT READS: this is the one test in `web/` that reaches
// outside its own package, to the sibling `site/` directory. That is
// deliberate — it is the whole point, since the two stylesheets cannot import
// each other — but it means the web suite has a dependency `package.json`
// does not express. In a sparse or partial checkout without `site/`, the read
// below throws ENOENT rather than failing an assertion, and the message will
// not explain itself. The repo is a monorepo where `site/` is always present,
// so this is a note rather than a guard.
/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

// Vitest always runs from this package's root (`web/`, where
// `vitest.config.ts` lives), so the site is one level up. Reading across the
// package boundary is the same move `hub/tests` makes to reach `sensor/`.
const SITE = readFileSync(resolve(process.cwd(), "../site/styles.css"), "utf-8");
const PORTAL = readFileSync(resolve(process.cwd(), "src/styles/tokens.css"), "utf-8");

/** Pulls `--name: #rrggbb;` out of one named block. `selector` is matched
 *  loosely (whitespace before `{` is not guaranteed) for the same reason
 *  `contrast.test.ts` does it: a build step may already have collapsed it. */
function tokensInBlock(css: string, selector: RegExp, prefix: string): Record<string, string> {
  const hit = selector.exec(css);
  if (!hit) throw new Error(`selector ${selector} not found`);
  const brace = css.indexOf("{", hit.index);
  const end = css.indexOf("}", brace);
  const out: Record<string, string> = {};
  const re = new RegExp(`--(${prefix}[a-z0-9-]*):\\s*(#[0-9a-fA-F]{6})`, "g");
  for (const m of css.slice(brace, end).matchAll(re)) out[m[1]] = m[2];
  return out;
}

const site = tokensInBlock(SITE, /:root\s*\{/, "");
// The portal's light palette is on its first bare `:root` block — the same
// block `contrast.test.ts` reads as "light".
const portalLight = tokensInBlock(PORTAL, /:root\s*\{/, "color-");

function hexToRgb(hex: string): [number, number, number] {
  const n = Number.parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

// WCAG 2.x relative luminance (spec §1.4.3's formula, sRGB).
function relativeLuminance([r, g, b]: [number, number, number]): number {
  const [rs, gs, bs] = [r, g, b].map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * rs + 0.7152 * gs + 0.0722 * bs;
}

function contrastRatio(hexA: string, hexB: string): number {
  const la = relativeLuminance(hexToRgb(hexA));
  const lb = relativeLuminance(hexToRgb(hexB));
  const lighter = Math.max(la, lb);
  const darker = Math.min(la, lb);
  return (lighter + 0.05) / (darker + 0.05);
}

const AA_TEXT = 4.5;
const BRAND_BLUE = "#1257c4";

// The vocabulary every Vault surface speaks. A role missing from the site
// means a third surface copying this file would have nothing to inherit for
// it — which is the drift this test exists to catch, more than a wrong hex.
const ROLES = [
  "bg", // page ground
  "panel", // recessed surface
  "line", // hairline
  "fg", // primary text
  "dim", // muted text
  "acc", // interactive accent (lightened for the dark ground)
  "accink", // ink on the accent
  "deep", // brand blue, as a fill
  "ok",
  "warn",
  "crit",
];

describe("brand spine (site/styles.css)", () => {
  it("speaks every role the spine defines", () => {
    expect(Object.keys(site).sort()).toEqual(expect.arrayContaining([...ROLES].sort()));
  });

  it("pins the brand blue to the portal's, exactly", () => {
    // Both sides asserted against the literal, not just against each other:
    // if someone restyles both files in one commit the test should still
    // notice that Vault's blue moved.
    expect(site.deep?.toLowerCase()).toBe(BRAND_BLUE);
    expect(portalLight["color-primary"]?.toLowerCase()).toBe(BRAND_BLUE);
  });
});

// Every foreground/background pair the stylesheet actually paints. The status
// colours appear against both grounds because the site uses one token for
// marks and text alike (see the header note), so both have to clear AA.
const PAIRS: [string, string, string][] = [
  ["fg on bg", "fg", "bg"],
  ["fg on panel", "fg", "panel"],
  ["dim on bg", "dim", "bg"],
  ["dim on panel", "dim", "panel"],
  ["acc on bg", "acc", "bg"],
  ["acc on panel", "acc", "panel"],
  ["ok on bg", "ok", "bg"],
  ["ok on panel", "ok", "panel"],
  ["warn on bg", "warn", "bg"],
  ["warn on panel", "warn", "panel"],
  ["crit on bg", "crit", "bg"],
  ["crit on panel", "crit", "panel"],
];

describe("colour contrast (site/styles.css, AA)", () => {
  it.each(PAIRS)("%s meets 4.5:1", (_label, fg, bg) => {
    expect(contrastRatio(site[fg], site[bg])).toBeGreaterThanOrEqual(AA_TEXT);
  });

  // `.btn` is the one place `--deep` is used, and it is a background with a
  // hard-coded white label rather than a token — so this pair cannot be
  // expressed in the table above.
  it("white button label on the brand blue meets 4.5:1", () => {
    expect(contrastRatio("#ffffff", site.deep)).toBeGreaterThanOrEqual(AA_TEXT);
  });
});
