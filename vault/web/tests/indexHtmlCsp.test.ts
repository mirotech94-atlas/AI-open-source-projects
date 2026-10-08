import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

/* The app is served under a Content-Security-Policy of `script-src 'self'`
 * with no `'unsafe-inline'`, no nonce and no hash (deploy/nginx-guardian.conf).
 * An inline <script> in index.html would therefore be refused by the browser
 * and silently do nothing. The pre-paint theme boot used to be inline, so this
 * guards the file-ness of it rather than trusting it.
 *
 * The same policy sets `default-src 'self'` with no third-party origin, which
 * is also why no font, stylesheet or script may be fetched from a CDN. Both
 * families are self-hosted in src/styles/fonts.css, and a watchdog has to
 * render on an isolated or WAN-down network anyway. */
describe("index.html under the app's CSP", () => {
  const html = readFileSync(resolve(__dirname, "../index.html"), "utf8");

  it("has no inline script", () => {
    expect(html).not.toMatch(/<script(?![^>]*\bsrc=)[^>]*>/);
  });

  it("loads the theme boot from a real URL, before the module graph", () => {
    expect(html).toContain('<script src="/theme-boot.js"></script>');
    expect(html.indexOf("/theme-boot.js")).toBeLessThan(html.indexOf("/src/main.tsx"));
  });

  it("fetches nothing from a third-party origin", () => {
    expect(html).not.toContain("fonts.googleapis.com");
    expect(html).not.toContain("fonts.gstatic.com");
    expect(html).not.toMatch(/(src|href)="https?:\/\//);
  });
});
