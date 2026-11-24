/**
 * Deny-list tests. Run: node extension/denylist.test.mjs
 *
 * This is the privacy boundary. A false negative here means something the owner
 * explicitly excluded gets recorded, so the cases below are deliberately
 * adversarial.
 */
import assert from "node:assert/strict";
import { isDenied, domainOf, DEFAULTS } from "./config.js";

const list = DEFAULTS.denylist;
let passed = 0;
const check = (name, fn) => { fn(); passed++; console.log("  ok  " + name); };

check("exact domain is denied", () => {
  assert.equal(isDenied("https://chase.com/account", list), true);
});

check("subdomain is denied", () => {
  assert.equal(isDenied("https://secure.chase.com/login", list), true);
});

check("www is normalised", () => {
  assert.equal(domainOf("https://www.chase.com/x"), "chase.com");
  assert.equal(isDenied("https://www.chase.com/x", list), true);
});

check("bare word matches any label containing it", () => {
  assert.equal(isDenied("https://mybank.co.uk/", list), true);
  assert.equal(isDenied("https://bankofamerica.com/", list), true);
  assert.equal(isDenied("https://healthline.com/", list), true);
});

check("a lookalike domain is NOT denied by the dotted rule", () => {
  // notchase.com must not be treated as a subdomain of chase.com.
  assert.equal(isDenied("https://notchase.com/", ["chase.com"]), false);
});

check("case is ignored", () => {
  assert.equal(isDenied("https://SECURE.CHASE.COM/", list), true);
});

check("an unrelated site is allowed", () => {
  assert.equal(isDenied("https://news.ycombinator.com/", list), false);
});

check("non-http schemes are never recorded", () => {
  for (const url of ["file:///home/user/secret.txt", "chrome://extensions",
                     "chrome-extension://abc/page.html", "data:text/html,hi",
                     "about:blank", "javascript:alert(1)"]) {
    assert.equal(isDenied(url, []), true, url);
  }
});

check("an unparseable url is never recorded", () => {
  assert.equal(isDenied("", []), true);
  assert.equal(isDenied("not a url", []), true);
  assert.equal(isDenied(null, []), true);
});

check("an empty deny-list still blocks non-http", () => {
  assert.equal(isDenied("https://example.com", []), false);
  assert.equal(isDenied("chrome://settings", []), true);
});

check("blank entries are ignored rather than matching everything", () => {
  assert.equal(isDenied("https://example.com", ["", "   "]), false);
});

check("whitespace around an entry is tolerated", () => {
  assert.equal(isDenied("https://chase.com", ["  chase.com  "]), true);
});

console.log(`\n${passed} deny-list assertions passed`);
