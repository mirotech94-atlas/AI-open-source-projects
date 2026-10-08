// The accessibility pass's colour-contrast obligation ("contrast ≥ 4.5:1 for
// text in both skins") is verified *here*, deterministically against the
// real `tokens.css` values — not through `jest-axe`'s `color-contrast` rule
// in `tests/a11y.test.tsx`. That rule needs a real layout engine and computed
// styles to find each element's actual foreground/background pair; jsdom has
// no layout engine and `vitest.config.ts` runs with `css: false`, so axe gets
// no computed colors there and the rule comes back "incomplete", never a
// real pass or fail. This file parses the token hex values straight out of
// the stylesheet and computes WCAG 2 contrast ratios in plain TS instead —
// the one place in this suite that actually discriminates light from dark.
// This file's `tsconfig.app.json` (shared with `src/`, DOM lib only, no
// ambient Node types) does not know `__dirname`/`node:fs` on its own — this
// triple-slash reference scopes `@types/node` (already a devDependency) to
// just this one file rather than adding it to the whole app's `types` list.
// Vite's `?raw` CSS import looked like the DOM-only alternative, but
// `vitest.config.ts`'s `css: false` stubs *every* `.css` import — including
// `?raw` ones — to an empty string, so it cannot read the real file either;
// reading it directly off disk is the only way to get the actual bytes.
/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

// `import.meta.url` under Vitest's own module transform is not a real
// `file:` URL, so `fileURLToPath` on it throws — resolve against the
// process's cwd instead, which Vitest always runs from this package's root
// (`web/`, where `vitest.config.ts` lives).
const CSS = readFileSync(resolve(process.cwd(), "src/styles/tokens.css"), "utf-8");

/** Pulls `--token-name: #rrggbb;` out of one named block of the stylesheet.
 *  `selector` is matched loosely (whitespace before the `{` is not
 *  guaranteed — a build step may have already collapsed it) so light vs.
 *  dark can't be confused even though both define the same token names. */
function tokensInBlock(selector: RegExp): Record<string, string> {
  const selectorMatch = selector.exec(CSS);
  if (!selectorMatch) throw new Error(`selector ${selector} not found in tokens.css`);
  const braceIdx = CSS.indexOf("{", selectorMatch.index);
  const endIdx = CSS.indexOf("}", braceIdx);
  const body = CSS.slice(braceIdx, endIdx);
  const tokens: Record<string, string> = {};
  for (const m of body.matchAll(/--(color-[a-z0-9-]+):\s*(#[0-9a-fA-F]{6})/g)) {
    tokens[m[1]] = m[2];
  }
  return tokens;
}

// The light palette sits on the file's first bare `:root` block; the dark
// palette's *forced* block (`data-theme="dark"`, not the
// `prefers-color-scheme` media one) is the one a real dark-mode user with
// `data-theme` set actually gets, and it repeats every token the media block
// does — reading it alone covers both.
const light = tokensInBlock(/:root\s*\{/);
const dark = tokensInBlock(/:root\[data-theme=["']dark["']\]\s*\{/);

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

// Every foreground/background pair the product actually renders body or
// label text with: primary text and muted text over both surfaces the shell
// uses (`--color-bg` behind cards, `--color-surface` inside them), the one
// primary-tinted text color used outside a button (links, the active nav
// item), on-primary text on the primary-tinted button/action fill, and each
// status color's text-on-its-own-soft-background pairing (badges, toasts).
const PAIRS: [string, string, string][] = [
  ["text on bg", "color-text", "color-bg"],
  ["text-muted on bg", "color-text-muted", "color-bg"],
  ["text on surface", "color-text", "color-surface"],
  ["text-muted on surface", "color-text-muted", "color-surface"],
  ["primary-strong on surface", "color-primary-strong", "color-surface"],
  // `--color-primary` as TEXT, not as a filled ground. It is the portal's
  // interactive colour, and an inline control on a card paints its label with
  // it rather than with `primary-strong`: `.g-inbox-open-incident` has done
  // that since before the confidence drawer's heading joined it. Only
  // `primary-strong on surface` was asserted here, which is a neighbouring
  // token — close enough to look covered and not the value the app draws.
  ["primary on surface", "color-primary", "color-surface"],
  ["on-primary on primary", "color-on-primary", "color-primary"],
  // The *text-strength* status token is the one a pill/toast/status label
  // actually paints its text with (`--tone-text` in `primitives.css`); the
  // bare `--color-good/warn/alert` are mark colours — dots, strokes, ring
  // arcs, borders and filled grounds — and are never text on a tinted
  // ground, so pairing those here would test something the app never draws.
  ["good-text on good-soft", "color-good-text", "color-good-soft"],
  ["warn-text on warn-soft", "color-warn-text", "color-warn-soft"],
  ["alert-text on alert-soft", "color-alert-text", "color-alert-soft"],
];

describe("colour contrast (tokens.css, AA)", () => {
  for (const [skinName, tokens] of [
    ["light", light],
    ["dark", dark],
  ] as const) {
    describe(`${skinName} skin`, () => {
      it.each(PAIRS)("%s meets 4.5:1", (_label, fg, bg) => {
        const ratio = contrastRatio(tokens[fg], tokens[bg]);
        expect(ratio).toBeGreaterThanOrEqual(AA_TEXT);
      });
    });
  }
});
