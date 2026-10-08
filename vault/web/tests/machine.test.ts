import { describe, expect, it } from "vitest";
import { isMachineValue, machineClass } from "../src/lib/machine";

/**
 * Vault's typographic rule is that monospace means machine-produced. The
 * rule only survives contact with real data if the classifier behind it is
 * conservative, because the two failure directions cost very different things:
 * a MAC left in the sans face is mildly harder to read, while a customer's
 * device called "Reception iPad" set in a typewriter face is visibly wrong and
 * shows up on every row of their dashboard.
 */
describe("what counts as machine-produced", () => {
  it("recognises the values a person reads character by character", () => {
    for (const v of [
      "192.168.1.170", "10.0.4.12", "127.0.0.1",          // IPv4
      "6e:f8:af:e3:20:61", "d8-be-65-72-45-31",            // MAC, both separators
      "api.guarder.io", "web-01.local", "example.com",     // hostnames
      "2.8.1", "v1.20", "0.1.0",                           // versions
      "fe80::1", "2001:db8::8a2e:370:7334",                // IPv6, compressed
    ]) {
      expect(isMachineValue(v), `${v} should be machine-produced`).toBe(true);
    }
  });

  it("leaves anything a person wrote alone", () => {
    for (const v of [
      "Reception iPad", "Miro's iPhone", "Apple, Inc.", "Cisco Systems",
      "Northwind Dental", "Unknown device", "", "   ",
      "Kitchen TV 2", "Samsung Electronics Co.,Ltd",
    ]) {
      expect(isMachineValue(v), `${v} should NOT be machine-produced`).toBe(false);
    }
  });

  it("does not mistake near-misses for addresses", () => {
    // These are the ones that would quietly set prose in a typewriter face.
    for (const v of [
      "1.2.3.4.5",        // too many octets and too many version parts
      "14:22",            // a clock, not IPv6 — the `::` requirement catches it
      "Ltd.",             // ends in a dot, is not a hostname
      "3.5",              // a rating, not a version
      "co.,ltd",          // punctuation a hostname cannot contain
      "...",              // dots alone
    ]) {
      expect(isMachineValue(v), `${v} should NOT be machine-produced`).toBe(false);
    }
  });

  it("still calls a dotted number machine-produced when it is not a valid address", () => {
    // The IPv4 pattern range-checks each octet, so `999.1.1.1` is not an
    // address. It is still a four-part dotted number, which the version
    // pattern matches and which no human writes as a name -- so the answer is
    // the same either way and the range check costs nothing. What the range
    // check buys is that `1.2.3.4.5` and friends stay out.
    expect(isMachineValue("999.1.1.1")).toBe(true);
    expect(isMachineValue("1.2.3.4.5")).toBe(false);
  });

  it("treats any dotted, space-free string as a hostname, on purpose", () => {
    // `hello.world.and.more.text` is a syntactically valid hostname, and on the
    // DNS page that is exactly what such a string is. There is no test that
    // separates it from a hypothetical human label of the same shape, so the
    // classifier does not try: dotted and space-free wins. The cost is bounded
    // -- a vendor name would have to contain no spaces and end in a TLD-shaped
    // label to be caught.
    expect(isMachineValue("hello.world.and.more.text")).toBe(true);
    expect(isMachineValue("ads.doubleclick.net")).toBe(true);
  });

  it("gives back the app's own utility class, or nothing", () => {
    expect(machineClass("10.0.0.1")).toBe("mono");
    expect(machineClass("Reception iPad")).toBe("");
  });
});
