import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

// `import.meta.url` under Vitest's own module transform is not a real
// `file:` URL (see tests/contrast.test.ts's note), and Vite's static
// `new URL(literal, import.meta.url)` asset-URL transform additionally
// tries to resolve the path through its dev server's module graph, which is
// denied for a path outside `web/` (the site lives in the sibling `site/`
// directory). Resolve against `process.cwd()` instead, which Vitest always
// runs from this package's root (`web/`).
const site = (p: string) => resolve(process.cwd(), "../site", p);
const read = (p: string) => readFileSync(site(p), "utf8");

describe("the sign-up page", () => {
  it("exists, carries the form with a required terms checkbox, and links to Log in", () => {
    expect(existsSync(site("signup.html"))).toBe(true);
    const html = read("signup.html");
    expect(html).toMatch(/<form id="lead-form"/);
    expect(html).toMatch(/name="site_name"[^>]*required/);
    expect(html).toMatch(/name="accept_terms"[^>]*type="checkbox"[^>]*required/);
    expect(html).toMatch(/href="\/terms\.html"/);
    expect(html).toMatch(/Already have an account\?/);
    expect(html).toMatch(/href="https:\/\/my\.guarder\.io\/login">Log in<\/a>/);
    // CSP: nothing inline, nothing third-party (mirrors hub/tests/test_site_csp.py)
    expect(html).not.toMatch(/<style|\sstyle="|fonts\.googleapis|cdn/);
  });

  // Inverted on 2026-09-19. This used to assert that [COMPANY LEGAL NAME] was
  // PRESENT, which was right while the page was an unfinished draft and wrong
  // the moment it was finished -- it failed for the completion rather than for
  // a fault, and a test that fails when the work is done teaches people to
  // ignore it.
  //
  // What actually needs guarding is the opposite: Stripe's activation flow asks
  // for the Terms and Privacy URLs by name and a reviewer reads them, so a
  // bracketed placeholder reaching a live legal page is the failure. Both pages
  // are checked, because privacy.html now carries the same three fields.
  it("has legal pages that name the operator, with no placeholders left", () => {
    for (const page of ["terms.html", "privacy.html"]) {
      const html = read(page);
      expect(html).not.toMatch(/\[[A-Z][A-Z ]+\]/);
      expect(html).toMatch(/4087 Richmond Avenue/);
      expect(html).toMatch(/support@guarder\.io/);
      expect(html).not.toMatch(/<style|\sstyle="/);
    }
    expect(read("terms.html")).toMatch(/Terms of Service/);
    expect(read("privacy.html")).toMatch(/Privacy Policy/);
    // The governing-law clause is the one field with no other home.
    expect(read("terms.html")).toMatch(/State of New York/);
    expect(read("terms.html")).toMatch(/operates from/);
  });

  it("sends every CTA on the home page to the sign-up page and says Log in", () => {
    const html = read("index.html");
    expect(html).not.toMatch(/href="#access"/);
    expect((html.match(/href="\/signup\.html"/g) ?? []).length).toBeGreaterThanOrEqual(3);
    expect(html).not.toMatch(/<form id="lead-form"/);
    expect(html).not.toMatch(/>Sign in</);
    expect((html.match(/href="https:\/\/my\.guarder\.io\/login">Log in<\/a>/g) ?? []).length).toBe(2);
    // The footer's Support column links to the terms page now that it exists.
    expect(html).toMatch(/<a href="\/terms\.html">Terms<\/a>/);
    for (const loc of ["ar", "es", "fr", "de", "pt", "it", "zh", "hi"]) {
      const l = read(`${loc}/index.html`);
      expect(l).not.toMatch(/href="#access"/);
      expect(l).toMatch(/href="\/signup\.html"/);
      expect(l).toMatch(/href="https:\/\/my\.guarder\.io\/login"/);
    }
  });

  it("posts site_name and accept_terms, validates both, and uses the new copy", () => {
    const js = read("app.js");
    expect(js).toMatch(/site_name:\s*siteName/);
    expect(js).toMatch(/accept_terms:\s*true/);
    expect(js).toMatch(/Please give the site a name\./);
    expect(js).toMatch(/Please accept the Terms of Service\./);
    expect(js).toMatch(/review it and email you when your account is ready/);
  });
});
