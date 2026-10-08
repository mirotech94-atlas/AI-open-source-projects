/* No customer-visible string in the dashboard says the old product name.
 *
 * The hub has the same guard in Python, where `ast` can tell a string literal
 * from a comment. TypeScript has no stdlib parser to hand, so this one works
 * line by line: it skips comment lines and JSDoc, and skips identifiers, which
 * is where the precision has to come from instead.
 *
 * It exists because the rebrand shipped a bug without it. `InboxRowView.tsx`
 * rendered "How sure Guardian is" on every inbox row in production, because
 * that copy arrived from another branch AFTER the rename swept `src/`, and the
 * hub-side guard is Python-only and structurally could not see a .tsx file.
 * A second deploy fixed it. This is what stops the third.
 *
 * Only CAPITALISED `Guardian` is copy. Every identifier in this codebase
 * spells it lower case -- the localStorage keys, the `role: "guardian"`
 * discriminators, the help-article slugs -- and the camelCase exceptions are
 * listed below by name rather than by pattern, so adding one is a deliberate
 * act a reviewer can see.
 *
 * WHY `offendingLines` IS EXPORTED AND HAS ITS OWN TESTS BELOW. The first
 * version of this guard inlined the whole scan in the assertion, and a later
 * commit that meant to rewrite prose rewrote one token of it as well:
 * `rest.includes("Guardian")` became `rest.includes("Vault")`. The early
 * return two lines above still tested for "Guardian", so the guard from then
 * on flagged a line only when it held BOTH words -- i.e. never. It stayed
 * green through a full 2153-test run and shipped to production disarmed.
 *
 * A guard with no test of its own cannot tell "nothing is wrong" from "I can
 * no longer see anything". The `detects` cases below are that test: they feed
 * the predicate one line that must be flagged and several that must not, so
 * the same one-token edit now fails here instead of passing silently. Any
 * future change to the scan belongs in `offendingLines`, where those cases
 * cover it. */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = resolve(__dirname, "../src");
const PUBLIC = resolve(__dirname, "../public");
const INDEX_HTML = resolve(__dirname, "../index.html");
const SITE = resolve(__dirname, "../../site");

/** The old name as it was written in a script that has no capital letters.
 *
 *  These are why this list exists rather than a single `"Guardian"` needle:
 *  the rebrand's own sweep was a Latin-text search, so `site/ar/index.html`
 *  kept saying the old name nineteen times and `site/hi/index.html` twice,
 *  through a deploy that was reported as finished. Nothing flagged it -- a
 *  reviewer reading the Arabic page did. A locale left behind is a customer
 *  reading the old name in their own language, which the spec calls out and
 *  no test enforced.
 *
 *  Latin `Guardian` is handled separately below, because it needs the
 *  identifier-stripping pass and these do not: no identifier in this codebase
 *  is written in Arabic or Devanagari. */
const STALE_SPELLINGS = ["\u063A\u0627\u0631\u062F\u064A\u0627\u0646", "\u0917\u093E\u0930\u094D\u0921\u093F\u092F\u0928"];

/** Identifiers that carry a capital `Guardian` and are NOT copy. */
const IDENTIFIERS = [
  "askGuardian", "onAskGuardian", "useAskGuardian", "AskGuardian",
  "GuardianHealth", "GUARDIAN_DEFAULT",
];

/** The lines of `source` that render the old product name to a customer.
 *
 *  Returns `[lineNumber, text]` pairs, 1-based, so a failure names the line.
 *  Exported (rather than inlined into the assertion) so the `detects` cases
 *  below can prove it still sees an offender -- see this file's header. */
export function offendingLines(source: string): [number, string][] {
  const bad: [number, string][] = [];
  // Track block comments across lines: a continuation line inside a
  // multi-line `{/* ... */}` starts with ordinary prose, not a `*`, so a
  // per-line test alone reports it as rendered copy. That was this
  // guard's own first false positive.
  let inBlock = false;
  source.split("\n").forEach((line, i) => {
    const wasInBlock = inBlock;
    const opens = line.lastIndexOf("/*");
    const closes = line.lastIndexOf("*/");
    if (opens > closes) inBlock = true;
    else if (closes > opens) inBlock = false;
    if (wasInBlock) return;
    const s = line.trimStart();
    // Comments and JSDoc are out of scope: internal prose, invisible to a
    // customer, and their churn would bury a real change in review.
    if (s.startsWith("//") || s.startsWith("*") || s.startsWith("/*") || s.startsWith("{/*")) return;
    // Strip the known non-copy identifiers, then ask what capital-G
    // occurrences are LEFT. Testing the raw line instead would flag every
    // `askGuardian(` call site; testing for anything other than "Guardian"
    // here is the bug described in this file's header.
    let rest = line;
    for (const id of IDENTIFIERS) rest = rest.split(id).join("");
    if (rest.includes("Guardian")) bad.push([i + 1, line.trim().slice(0, 90)]);
  });
  return bad;
}

/** The lines of `source` written in a non-Latin script that still say the old
 *  name. Separate from `offendingLines` because the comment and identifier
 *  rules above do not apply: these spellings appear only in page copy. */
export function offendingTranslations(source: string): [number, string][] {
  const bad: [number, string][] = [];
  source.split("\n").forEach((line, i) => {
    if (STALE_SPELLINGS.some((w) => line.includes(w))) {
      bad.push([i + 1, line.trim().slice(0, 90)]);
    }
  });
  return bad;
}

function files(dir: string, ext: RegExp): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...files(p, ext));
    else if (ext.test(name)) out.push(p);
  }
  return out;
}

describe("the dashboard says Vault, not the old name", () => {
  // `web/public` and `index.html` are served to a customer byte for byte and
  // never pass through the TypeScript build, so nothing else in this repo
  // looks at them: `tsc` does not compile them, the hub's Python guard cannot
  // read them, and the CSP test only reads index.html's meta tag. A brand
  // string in a `<title>`, a manifest or the service worker's own offline
  // message would have reached production unchallenged.
  // `site/` is the marketing site: nine locales of static HTML plus its own
  // CSS and JS, deployed by `deploy/deploy.sh`'s second rsync and never
  // touched by the portal build. It had no guard of any kind until this one,
  // which is how the Arabic and Hindi pages kept saying the old name in their
  // own scripts through a deploy that was reported as complete -- the sweep
  // had only ever looked at Latin text. Scanning it here rather than writing
  // a second guard keeps one definition of "is this copy", the one the
  // `detects` cases below actually test.
  const ROOTS: [string, string[]][] = [
    ["src", files(SRC, /\.(ts|tsx)$/)],
    ["public", files(PUBLIC, /\.(js|json|html|webmanifest)$/)],
    ["index.html", [INDEX_HTML]],
    ["site", files(SITE, /\.(html|js|css|json|txt|webmanifest|svg)$/)],
  ];

  it.each(ROOTS)("has no customer-visible Guardian left in %s", (label, paths) => {
    // A root that matched nothing would pass this test for the wrong reason:
    // an empty file list is indistinguishable from a clean one, and a moved
    // directory or a tightened extension pattern would quietly turn the scan
    // off. Same failure this whole file exists to prevent -- see the header.
    expect(paths.length, `${label} matched no files -- the scan is looking in `
      + "the wrong place, so a pass here means nothing").toBeGreaterThan(0);

    const bad: string[] = [];
    for (const f of paths) {
      const text0 = readFileSync(f, "utf8");
      const rel = f.slice(resolve(__dirname, "..").length + 1);
      for (const [lineno, text] of offendingLines(text0)) bad.push(`${rel}:${lineno}: ${text}`);
      for (const [lineno, text] of offendingTranslations(text0)) bad.push(`${rel}:${lineno}: ${text}`);
    }
    expect(bad, `These render the old product name to a customer:\n  ${bad.join("\n  ")}\n\n`
      + "The product is Vault. If one is an identifier rather than copy, add it to "
      + "IDENTIFIERS here and to the never-change table in "
      + "docs/superpowers/specs/2026-09-24-vault-rebrand-design.md.").toEqual([]);
  });

  describe("detects what it exists to detect", () => {
    it("flags rendered copy", () => {
      expect(offendingLines(`<span>How sure Guardian is</span>`)).toEqual([
        [1, "<span>How sure Guardian is</span>"],
      ]);
    });

    it("flags a string literal in code", () => {
      expect(offendingLines(`const t = "Ask Guardian";`)).toHaveLength(1);
    });

    it("ignores the allowed identifiers", () => {
      expect(offendingLines(`onAskGuardian(useAskGuardian(GUARDIAN_DEFAULT));`)).toEqual([]);
    });

    it("ignores comments and block comments", () => {
      expect(offendingLines([
        "// Guardian used to be called this",
        "/* Guardian again",
        " continuation line naming Guardian with no leading star",
        " */",
        "{/* Guardian in JSX */}",
      ].join("\n"))).toEqual([]);
    });

    it("flags the Arabic and Hindi spellings of the old name", () => {
      expect(offendingTranslations("<h1>\u063A\u0627\u0631\u062F\u064A\u0627\u0646</h1>")).toHaveLength(1);
      expect(offendingTranslations("<h1>\u0917\u093E\u0930\u094D\u0921\u093F\u092F\u0928</h1>")).toHaveLength(1);
      expect(offendingTranslations("<h1>Vault</h1>")).toEqual([]);
    });

    it("ignores the lower-case identifier spelling", () => {
      expect(offendingLines(`localStorage.getItem("guardian.theme");`)).toEqual([]);
    });
  });
});
