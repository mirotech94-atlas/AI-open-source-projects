// Contrast on the glass chrome, which `contrast.test.ts` cannot speak to.
//
// That file asserts foreground/background pairs where BOTH are opaque token
// values, which is the only case where a contrast ratio is a property of the
// palette. The chrome is not that case: the top bar is sticky and the page
// travels underneath it, so the effective background of its text is the tint
// composited over whatever happens to be behind it at that scroll position.
//
// The risk is specific and it was real. Shipped at alpha 0.58, the dark skin's
// top-bar text measured 4.13:1 against its own worst case -- under AA -- and
// nothing in the suite could have said so, because every value involved was
// individually fine. A reader would have met it only as "the tenant name goes
// hard to read when a pale panel scrolls past".
//
// WHAT "WORST CASE" MEANS HERE, and why it is not pessimism: alpha compositing
// is monotonic, so for a given tint and text colour the worst backdrop is
// always the extreme one -- pure white behind a dark tint, the page's own ink
// behind a light one. Anything that can actually scroll under the chrome sits
// between the ground and that extreme, so clearing the extreme clears all of
// them. This does not need to know what the app renders.
//
// The fix for a failure here is the tint's alpha, never the text colour: the
// text is a palette token that `contrast.test.ts` already governs everywhere
// else, and changing it to suit the chrome would break it in the twenty places
// it is opaque.
/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const TOKENS = readFileSync(resolve(process.cwd(), "src/styles/tokens.css"), "utf-8");

type Rgb = [number, number, number];

function hexToRgb(hex: string): Rgb {
  const n = Number.parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** Pulls `--name: rgba(r, g, b, a);` out of one block. */
function rgbaIn(css: string, selector: RegExp, name: string): { rgb: Rgb; alpha: number } {
  const hit = selector.exec(css);
  if (!hit) throw new Error(`selector ${selector} not found`);
  const brace = css.indexOf("{", hit.index);
  const body = css.slice(brace, css.indexOf("}", brace));
  const m = new RegExp(`--${name}:\\s*rgba\\(\\s*(\\d+)[,\\s]+(\\d+)[,\\s]+(\\d+)[,\\s/]+([\\d.]+)\\s*\\)`).exec(body);
  if (!m) throw new Error(`--${name} not found as an rgba() in ${selector}`);
  return { rgb: [Number(m[1]), Number(m[2]), Number(m[3])], alpha: Number(m[4]) };
}

function hexIn(css: string, selector: RegExp, name: string): string {
  const hit = selector.exec(css);
  if (!hit) throw new Error(`selector ${selector} not found`);
  const brace = css.indexOf("{", hit.index);
  const body = css.slice(brace, css.indexOf("}", brace));
  const m = new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})`).exec(body);
  if (!m) throw new Error(`--${name} not found in ${selector}`);
  return m[1];
}

/** Source-over compositing: what the eye actually receives. */
function composite(tint: Rgb, alpha: number, behind: Rgb): Rgb {
  return tint.map((c, i) => alpha * c + (1 - alpha) * behind[i]) as Rgb;
}

// WCAG 2.x relative luminance (spec §1.4.3's formula, sRGB).
function relativeLuminance([r, g, b]: Rgb): number {
  const [rs, gs, bs] = [r, g, b].map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * rs + 0.7152 * gs + 0.0722 * bs;
}

function contrastRatio(a: Rgb, b: Rgb): number {
  const la = relativeLuminance(a);
  const lb = relativeLuminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

const AA_TEXT = 4.5;
const WHITE: Rgb = [255, 255, 255];

const LIGHT = /:root\s*\{/;
const DARK = /:root\[data-theme=["']dark["']\]\s*\{/;

/** Each skin, with the most hostile thing that can pass under its chrome.
 *
 *  Two rows, not three. The owner console used to declare a glass tint of its
 *  own -- a near-white frosted sheet, because its chrome sat on paper -- and
 *  was checked here as a third skin. It shares the app's tokens now, so its
 *  bar and rail are painted by whichever of these two skins is active and are
 *  covered by them. A third row would mean re-adding a console-specific glass
 *  token purely to give this test something to read.
 *  A light tint is worst over the darkest ink the page can show; a dark tint
 *  is worst over pure white. */
const SKINS = [
  {
    name: "light skin",
    text: hexIn(TOKENS, LIGHT, "color-text"),
    tint: rgbaIn(TOKENS, LIGHT, "color-glass-tint"),
    worstBehind: hexToRgb(hexIn(TOKENS, LIGHT, "color-text")),
  },
  {
    name: "dark skin",
    text: hexIn(TOKENS, DARK, "color-text"),
    tint: rgbaIn(TOKENS, DARK, "color-glass-tint"),
    worstBehind: WHITE,
  },
] as const;

describe("text on the glass chrome stays legible over anything behind it", () => {
  it.each(SKINS.map((s) => [s.name, s] as const))(
    "%s clears AA against its worst-case backdrop",
    (_name, skin) => {
      const ground = composite(skin.tint.rgb, skin.tint.alpha, skin.worstBehind);
      const ratio = contrastRatio(hexToRgb(skin.text), ground);
      expect(ratio).toBeGreaterThanOrEqual(AA_TEXT);
    },
  );

  // The tint is only glass while you can see through it. Without this the
  // cheapest way to pass the test above is to drive every alpha to 1, which
  // would leave the material passing its own guard and no longer being the
  // material.
  it.each(SKINS.map((s) => [s.name, s] as const))(
    "%s is still translucent enough to be glass",
    (_name, skin) => {
      expect(skin.tint.alpha).toBeLessThanOrEqual(0.85);
    },
  );
});
