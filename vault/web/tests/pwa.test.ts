import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

/* Vault is installable so a repair can be approved from a phone at 2am
 * without finding a laptop — the plan calls that the product's best moment
 * (§2.3, P2 #33), and Chrome will not offer to install a site without a
 * service worker that has a fetch handler.
 *
 * The tests that matter here are the ones about what the worker must NOT do.
 * Vault is an instrument panel someone opens because something is broken,
 * so a worker that served a cached shell with cached data would show
 * yesterday's "all clear" to someone standing in front of a fire — and it
 * would look completely normal, because a cached page renders exactly like a
 * live one. That is the same "unmeasured must never render as healthy" rule
 * the score coverage gate enforces, except the lie would be ours.
 */
const ROOT = resolve(__dirname, "..");
const sw = readFileSync(resolve(ROOT, "public/sw.js"), "utf8");
const manifest = JSON.parse(readFileSync(resolve(ROOT, "public/manifest.webmanifest"), "utf8"));
const indexHtml = readFileSync(resolve(ROOT, "index.html"), "utf8");

describe("installable dashboard", () => {
  it("ships a manifest the page actually links", () => {
    expect(indexHtml).toContain('rel="manifest"');
    expect(indexHtml).toContain("/manifest.webmanifest");
  });

  it("declares enough for an install prompt", () => {
    expect(manifest.name).toBe("Vault");
    expect(manifest.start_url).toBe("/");
    expect(manifest.display).toBe("standalone");
    expect(manifest.icons.length).toBeGreaterThan(0);
  });

  it("registers a worker with a fetch handler, or it is not installable", () => {
    expect(sw).toContain('addEventListener("fetch"');
  });
});

describe("what the worker must never do", () => {
  it("opens no cache at all", () => {
    // `caches.open` / `cache.put` are how stale data would get served. Their
    // absence is the property, so it is asserted rather than trusted.
    expect(sw).not.toContain("caches.open");
    expect(sw).not.toContain("cache.put");
    expect(sw).not.toContain("caches.match");
  });

  it("answers every request from the network", () => {
    expect(sw).toContain("fetch(event.request)");
  });

  it("carries the reason, so the next person does not add caching by reflex", () => {
    expect(sw).toMatch(/caches nothing/i);
    expect(sw).toMatch(/all clear/i);
  });
});
