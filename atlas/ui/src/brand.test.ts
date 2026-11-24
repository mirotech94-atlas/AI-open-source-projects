/**
 * The portal wears the storefront's brand (the owner, 2026-10-28): the tokens
 * match atlas/modules/academy/assets/style.css (dark), royal blue is never a
 * health colour, and UNKNOWN stays a neutral grey, dashed, never blue/green.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { StaticRouter } from "react-router-dom/server";
import { describe, expect, it } from "vitest";
import { Login } from "./components/Login";
import { Wordmark } from "./components/Wordmark";
import { CommandHeader } from "./pages/CommandCenter";
import { WORDMARK } from "./pages/deskModel";
import { BRAND, HUD_THEME_COLOR } from "./theme";

// Read from disk: vitest stubs a stylesheet import (even `?raw`) to "".
// `node:fs` by name, typed here: the app's tsconfig carries no Node types.
const FS = "node:fs";
const { readFileSync } = (await import(/* @vite-ignore */ FS)) as {
  readFileSync: (path: URL, encoding: "utf8") => string;
};
const read = (p: string): string => readFileSync(new URL(p, import.meta.url), "utf8");
const store = read("../../atlas/modules/academy/assets/style.css");
const storeIcon = read("../../atlas/modules/academy/assets/icon.svg");
const desk = read("./desk.css");
const command = read("./command.css");
const html = read("../index.html");
const favicon = read("../public/favicon.svg");
const manifest = JSON.parse(read("../public/manifest.webmanifest")) as { name: string };

/** The value of `--name` in the last block of `css` that sets it. */
function token(css: string, name: string): string | undefined {
  const all = [...css.matchAll(new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})`, "g"))];
  return all.at(-1)?.[1]?.toLowerCase();
}

describe("the ATLAS Academy brand", () => {
  it("takes royal blue, the ground and the focus from the storefront", () => {
    expect(BRAND.royal).toBe(token(store, "--gold"));
    expect(BRAND.ground).toBe(token(store, "--ivory"));
    expect(BRAND.focus).toBe(token(store, "--focus"));
    for (const css of [desk, command]) {
      expect(token(css, "--brand")).toBe(BRAND.royal);
      expect(token(css, "--focus")).toBe(BRAND.focus);
    }
  });

  it("keeps health apart from the brand and UNKNOWN a neutral grey", () => {
    for (const css of [desk, command]) {
      const ok = token(css, "--cyan");
      const grey = token(css, "--grey");
      expect(ok).toBe("#34d399");
      expect(grey).toBe("#8a8f99");
      for (const status of [ok, grey, token(css, "--amber"), token(css, "--red")]) {
        expect([BRAND.royal, BRAND.royalBright, BRAND.focus]).not.toContain(status);
      }
    }
    expect(desk).toMatch(/\.act-card\[data-health="unknown"\] \{ border: 1px dashed var\(--grey\)/);
  });

  it("names the page, the bar colour and the icon", () => {
    expect(WORDMARK).toBe("ATLAS Agency");
    expect(html).toContain("<title>ATLAS Agency</title>");
    expect(manifest.name).toBe("ATLAS Agency");
    expect(html + JSON.stringify(manifest)).not.toContain("Academy Agency");
    expect(html).toContain('content="#0a0d14"');
    expect(HUD_THEME_COLOR).toBe("#0a0d14");
    expect(favicon).toBe(storeIcon);
  });

  it("puts the mark and the wordmark on the header and the login page", () => {
    const overview = { data: null, error: null, loading: true, fetchedAt: null,
                       reload: () => undefined };
    const head = renderToStaticMarkup(createElement(StaticRouter, { location: "/" },
      createElement(CommandHeader, { overview, waiting: 0, feed: "live" })));
    expect(head).toContain('src="/favicon.svg"');
    expect(head).toContain('class="atlas-wordmark"');
    expect(head).toContain('aria-label="ATLAS Agency, home"');
    expect(head).not.toContain("Academy");
    const login = renderToStaticMarkup(createElement(Login,
      { configured: true, onSignedIn: () => undefined }));
    expect(login).toContain('src="/favicon.svg"');
    expect(login).toContain('class="atlas-wordmark"');
    expect(login).not.toContain("Academy");
  });

  it("draws the logotype: a blue mark, ATLAS heavy, AGENCY light", () => {
    const w = renderToStaticMarkup(createElement(Wordmark));
    expect(w).toMatch(/^<span class="atlas-wordmark"/);
    expect(w).toContain('<b class="aw-atlas">ATLAS</b>');
    expect(w).toContain('<span class="aw-agency">Agency</span>');
    expect(w).toContain('aria-hidden="true"');           // the mark is decoration
    expect(w).toContain(`fill="${BRAND.royal}"`);
    // Styled once, in the brand's display face; AGENCY lighter than ATLAS.
    expect(command).toMatch(/\.aw-atlas \{[^}]*font: 800 [^}]*var\(--font-display\)/);
    expect(command).toMatch(/\.aw-atlas \{[^}]*letter-spacing: \.1\d*em/);
    expect(command).toMatch(/\.aw-agency \{[^}]*font: 500 /);
  });
});
