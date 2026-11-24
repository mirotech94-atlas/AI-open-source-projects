/**
 * The animated black + royal blue backdrop and the portal's motion (the owner,
 * 2026-11-10). One backdrop lives in _tokens.css and reaches both the Vite
 * bundle and the server-rendered pages; motion.css adds panel, route and
 * login motion. These tests pin the safety rules: motion is off for
 * prefers-reduced-motion, nothing loads from outside, no script is added, and
 * UNKNOWN never moves or glows (invariant 7).
 */
import { describe, expect, it } from "vitest";

const FS = "node:fs";
const { readFileSync } = (await import(/* @vite-ignore */ FS)) as {
  readFileSync: (path: URL, encoding: "utf8") => string;
};
const read = (p: string): string => readFileSync(new URL(p, import.meta.url), "utf8");
const tokens = read("../../atlas/api/templates/_tokens.css");
const motion = read("./motion.css");
const main = read("./main.tsx");
const base = read("../../atlas/api/templates/base.html");
const login = read("../../atlas/api/templates/login.html");

/** The text of every `@media (prefers-reduced-motion: no-preference) { ... }` block. */
function motionBlocks(css: string): string {
  const out: string[] = [];
  const re = /@media \(prefers-reduced-motion: no-preference\) \{/g;
  for (let m = re.exec(css); m; m = re.exec(css)) {
    let depth = 1;
    let i = re.lastIndex;
    while (depth > 0 && i < css.length) {
      if (css[i] === "{") depth += 1;
      else if (css[i] === "}") depth -= 1;
      i += 1;
    }
    out.push(css.slice(re.lastIndex, i - 1));
  }
  return out.join("\n");
}

/** Everything outside the no-preference blocks: what a reduced-motion reader gets. */
function staticPart(css: string): string {
  return css.replace(/@media \(prefers-reduced-motion: no-preference\) \{/g, "@@START@@")
    .split("@@START@@")
    .map((chunk, idx) => {
      if (idx === 0) return chunk;
      let depth = 1;
      let i = 0;
      while (depth > 0 && i < chunk.length) {
        if (chunk[i] === "{") depth += 1;
        else if (chunk[i] === "}") depth -= 1;
        i += 1;
      }
      return chunk.slice(i);
    })
    .join("");
}

describe("the shared backdrop (_tokens.css)", () => {
  it("is royal blue on black and lives in the one shared file", () => {
    expect(tokens).toMatch(/--royal:\s*#5b8cff/i);
    expect(tokens).toMatch(/--ground:\s*#0a0d14/i);
    expect(tokens).toContain("body::before");
    expect(tokens).toContain("body::after");
  });

  it("only moves for readers who have not asked for less motion", () => {
    const backdrop = tokens.slice(tokens.indexOf("--- the backdrop"));
    expect(motionBlocks(backdrop)).toMatch(/animation:/);
    expect(staticPart(backdrop)).not.toMatch(/animation/);
  });
});

describe("the portal motion (motion.css)", () => {
  it("is loaded last, after the stylesheets whose rules it overrides", () => {
    const order = ["hud.css", "academy.css", "command.css", "desk.css", "motion.css"]
      .map((f) => main.indexOf(`"./${f}"`));
    expect(order.every((i) => i >= 0)).toBe(true);
    expect([...order].sort((a, b) => a - b)).toEqual(order);
  });

  it("declares every animation inside the no-preference block", () => {
    expect(staticPart(motion)).not.toMatch(/animation\s*:/);
    expect(staticPart(motion)).not.toMatch(/@keyframes/);
    expect(motionBlocks(motion)).toMatch(/@keyframes atlas-rise/);
    expect(motionBlocks(motion)).toMatch(/@keyframes atlas-shake/);
  });

  it("never animates or lights an UNKNOWN state", () => {
    const rules = motionBlocks(motion) + staticPart(motion);
    for (const m of rules.matchAll(/([^{}]*data-health="unknown"[^{}]*)\{([^}]*)\}/g)) {
      expect(m[2]).not.toMatch(/animation\s*:\s*(?!none)/);
      expect(m[2]).not.toMatch(/box-shadow\s*:\s*(?!none)/);
    }
    expect(motion).not.toMatch(/data-health="unknown"[^{]*\{[^}]*(glow|pulse)/);
  });
});

describe("the rules every page keeps", () => {
  const all = { tokens, motion, base, login };
  it("loads nothing from outside", () => {
    for (const [name, text] of Object.entries(all)) {
      expect(text, name).not.toMatch(/https?:\/\/(?!www\.w3\.org)/);
      expect(text, name).not.toMatch(/@import\s+url\(/);
      expect(text, name).not.toMatch(/fonts\.googleapis|cdn\./);
    }
  });

  it("adds no script, so the CSP (script-src 'self') still holds", () => {
    expect(base).not.toMatch(/<script/i);
    expect(login).not.toMatch(/<script/i);
    expect(`${base}${login}`).not.toMatch(/\son[a-z]+\s*=/i);
  });

  it("does not touch the form, its fields or the CSRF/next plumbing", () => {
    expect(login).toContain('method="post" action="/login"');
    expect(login).toContain('name="next" value="{{ next }}"');
    expect(login).toContain('name="password"');
    expect(login).toContain('autocomplete="current-password"');
  });
});
