/* The client-side identifiers the Vault rebrand must never touch.
 *
 * Every one of these is a key somebody's browser already has a value under.
 * Renaming one does not migrate it -- it abandons it, and the user's theme,
 * sidebar state or speaker preference silently reverts to the default with
 * nothing to indicate why.
 *
 * `guardian_session` is NOT in the list below, even though it is the session
 * cookie and the worst thing on this page to get wrong. web/src never sets
 * or reads that literal in code -- the hub sets the cookie; this app only
 * sends `credentials: "include"` and lets the browser carry it. The one
 * place the string appears here at all is a comment in `lib/api.ts`
 * explaining that behaviour. Once this guard stops trusting comments (see
 * `stripComments` below), a comment is exactly what that occurrence is, so
 * it can't satisfy an `it.each` case that means "real code still uses this."
 * The identifier is not unguarded: it is pinned to its actual definition,
 * `COOKIE = "guardian_session"` in `hub/guardian_hub/deps.py`, and enforced
 * there by `hub/tests/test_brand_invariants.py`.
 *
 * See docs/superpowers/specs/2026-09-24-vault-rebrand-design.md. */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = resolve(__dirname, "../src");

/** [literal, what breaks if it moves] */
const FROZEN: [string, string][] = [
  ["guardian.theme", "localStorage — resets every user's light/dark choice"],
  ["guardian.sidebar", "localStorage — resets the sidebar"],
  ["guardian.sidebar.expanded", "localStorage — resets the sidebar"],
  ["guardian.chat.open", "localStorage — resets whether the chat panel was open"],
  ["guardian.chat.speak", "localStorage — resets the read-answers-aloud preference"],
  ["guardian.topbar.advanced", "localStorage — resets advanced mode"],
  ["guardian.network.", "localStorage — forgets the selected network"],
  ["guardian.onboarding.dismissed.", "localStorage — re-shows onboarding to everyone"],
  // guardian.docs.token is NOT here: it lives in hub/guardian_hub/apidocs.py
  // (the API reference console's inline JS), backed by sessionStorage, not
  // under web/src at all -- so it belongs to the hub-side guard
  // (hub/tests/test_brand_invariants.py), which is where it's frozen.
];

/** Strip comments before searching, so a `//` aside or a `/* ... *\/` block
 *  that merely *mentions* a frozen key can't stand in for the code that
 *  actually uses it. `lib/api.ts:3` is the concrete case: it explains the
 *  `guardian_session` cookie in a comment, and a raw-text search used to
 *  treat that sentence as proof the identifier was still in use.
 *
 *  This is a regex, not a parser -- unlike the hub guard, which has Python's
 *  own `ast` module to hand, there is no TypeScript/JS parser bundled here.
 *  It is not comment-in-a-string safe: a string literal containing `//`
 *  earlier on the same line would truncate the rest of that line, frozen
 *  key and all. None of the FROZEN literals below sit after a `//` on their
 *  own line today, so that gap doesn't bite in practice, and the failure
 *  mode if it ever did is the guard staying red a beat longer -- not the
 *  opposite failure this rewrite exists to close, where a comment keeps the
 *  guard green over a real rename. */
function stripComments(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
}

function allSource(dir: string): string {
  let out = "";
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out += allSource(p);
    else if (/\.(ts|tsx)$/.test(name)) out += stripComments(readFileSync(p, "utf8"));
  }
  return out;
}

describe("identifiers the rebrand must not touch", () => {
  const src = allSource(SRC);

  it.each(FROZEN)("keeps %s", (literal, why) => {
    expect(src, `${literal} is gone — ${why}`).toContain(literal);
  });
});
