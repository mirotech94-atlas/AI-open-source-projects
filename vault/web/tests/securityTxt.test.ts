import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

/* A security product with no disclosure address is a procurement blocker
 * (trust plan §2.7), but a security.txt is one of the few files that gets
 * WORSE with age rather than just going stale: RFC 9116 makes `Expires`
 * mandatory precisely so a reader can tell a maintained contact from an
 * abandoned one, and an expired file advertises the second.
 *
 * So the interesting assertion here is not "the file exists" — it is that
 * the date in it is still in the future. This test is designed to fail on
 * a perfectly good working tree one day, with nobody having touched the
 * repo. That is the point: the failure is the reminder, and it arrives in
 * CI rather than in a researcher's bounced email. Renew the date, do not
 * delete the test.
 *
 * The same logic is why `Contact` is checked for being a real address and
 * not a security@ alias that was never created — a disclosure address that
 * bounces is worse than no file at all. */
const txt = readFileSync(
  resolve(__dirname, "..", "..", "site", ".well-known", "security.txt"),
  "utf8",
);

function field(name: string): string {
  const m = txt.match(new RegExp(`^${name}:\\s*(.+)$`, "m"));
  if (!m) throw new Error(`security.txt has no ${name} field`);
  return m[1].trim();
}

describe("security.txt", () => {
  it("names a contact", () => {
    expect(field("Contact")).toMatch(/^mailto:\S+@\S+\.\S+$/);
  });

  it("has not expired", () => {
    const expires = new Date(field("Expires"));
    expect(Number.isNaN(expires.getTime())).toBe(false);
    expect(expires.getTime()).toBeGreaterThan(Date.now());
  });

  it("does not promise more than a year of validity", () => {
    // RFC 9116 §2.5.5. A five-year Expires defeats the whole mechanism.
    const expires = new Date(field("Expires"));
    const year = 366 * 24 * 60 * 60 * 1000;
    expect(expires.getTime() - Date.now()).toBeLessThanOrEqual(year);
  });

  it("is self-canonical, so a copy served elsewhere cannot impersonate it", () => {
    expect(field("Canonical")).toBe("https://guarder.io/.well-known/security.txt");
  });
});
