import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { test as base, expect } from "@playwright/test";
import type { BrowserContext, Page } from "@playwright/test";

// `guardian seed-demo` creates two demo accounts sharing one fixed sign-in
// code (`Tenant.settings["demo_code"]`, matched by username-agnostic
// `routers.auth.verify`): "demo" (viewer) and "demoadmin" (admin). The
// smoke suite signs in as the admin account — a viewer's inbox rows carry
// no actions at all (`_alert_actions` in payloads/inbox.py gates ack/
// snooze/resolve/reopen at "operator"+), so "an ack changes the row
// status" needs the role that can actually see an Ack button.
export const DEMO_USERNAME = "demoadmin";
export const DEMO_CODE = "424242";

/** The first caller in a run signs in through the real UI (username → OTP
 *  → verify) and keeps the resulting session cookie; every later caller
 *  replays that cookie instead of signing in again — exactly what a
 *  returning browser does.
 *
 *  Not an optimisation: `routers/auth.AUTH_START_PER_USER` allows 5 sign-in
 *  code requests per username per 10 minutes, and this file's two
 *  tests across three projects (light / dark / mobile) would need six, so a
 *  per-test login would leave the last project rate-limited by a control
 *  that is working exactly as intended. Runs are single-worker and serial
 *  (`fullyParallel: false`, `workers: 1`), so one module-level cache is
 *  safe. The real OTP flow is still exercised once per run — and by the
 *  login-page assertions in `selftest`/the hub suite besides. */
type Cookies = Awaited<ReturnType<BrowserContext["cookies"]>>;

/** Cached on DISK, not just in module scope: each Playwright project
 *  re-imports this module, so a module-level cache alone still costs one
 *  sign-in per project — three per username per run, and more across re-runs
 *  against a long-lived hub, which walks straight into the five-per-ten-minutes
 *  limit above. A replayed session is what a returning browser does anyway. */
function sessionPath(username: string): string {
  return `${tmpdir()}/guardian-e2e-session-${username}.json`;
}

export function loadSession(username: string): Cookies | null {
  const path = sessionPath(username);
  if (!existsSync(path)) return null;
  try {
    return JSON.parse(readFileSync(path, "utf8")) as Cookies;
  } catch {
    return null;   // a truncated or stale file just means signing in again
  }
}

export function saveSession(username: string, cookies: Cookies): void {
  writeFileSync(sessionPath(username), JSON.stringify(cookies));
}

/** Signs in as the demo admin through the real UI (username → OTP →
 *  verify), then waits for the shell to land on Overview. Never pokes
 *  `localStorage`/cookies directly for the session itself — only the
 *  `dark` project's pre-paint theme seed (see `test` below) reaches into
 *  storage, and that's a display preference, not auth. */
export async function loginAsDemo(page: Page): Promise<void> {
  const cached = loadSession(DEMO_USERNAME);
  if (cached) {
    await page.context().addCookies(cached);
    await page.goto("/overview");
    // A session the hub has since expired lands on /login; fall through to a
    // real sign-in rather than failing on a stale cache.
    if (!/\/login$/.test(new URL(page.url()).pathname)) {
      await passFirstRunWizard(page);
      await page.waitForURL(/\/overview$|\/$/, { timeout: 10_000 });
      return;
    }
  }
  await page.goto("/login");
  await page.getByLabel("Username").fill(DEMO_USERNAME);
  await page.getByRole("button", { name: /send code/i }).click();
  await page.getByLabel("6-digit code").fill(DEMO_CODE);
  await page.getByRole("button", { name: /verify/i }).click();
  await passFirstRunWizard(page);
  await page.waitForURL(/\/overview$|\/$/, { timeout: 10_000 });
  saveSession(DEMO_USERNAME, await page.context().cookies());
}

/** The first-run wizard now fires for every USER who has not been through it,
 *  not only for a tenant with no scan (task 71), so a freshly seeded demo
 *  account lands on `/welcome` on its very first sign-in. This walks past it
 *  the way a person would — "Skip for now", which the hub records against that
 *  user — so the rest of a suite starts on Overview. A no-op once the account
 *  has been through it, and on any run that never sees the wizard. */
export async function passFirstRunWizard(page: Page): Promise<void> {
  try {
    await page.waitForURL(/\/welcome$/, { timeout: 3_000 });
  } catch {
    return;   // never redirected: this account has already seen it
  }
  await page.getByRole("button", { name: /skip for now/i }).click();
  await page.waitForURL(/\/overview$/, { timeout: 10_000 });
}

/** Extends the base `test` so every test in the `dark` project boots with
 *  `data-theme="dark"` already applied — matching how a returning user with
 *  a persisted dark preference actually experiences the app (the pre-paint
 *  `themeBoot.ts` script reads `localStorage["guardian.theme"]` before the
 *  first paint), rather than the light default. Every other project is a
 *  no-op here: no init script, no `data-theme`. */
export const test = base.extend<{ page: Page }>({
  // Playwright's fixture callback signature names its "run the test" thunk
  // `use` — oxlint's react-hooks rule mistakes that for a React hook call
  // (`use(...)`) because of the name alone, not any React semantics here.
  // oxlint-disable-next-line react-hooks(rules-of-hooks)
  page: async ({ page }, runTest, testInfo) => {
    if (testInfo.project.name === "dark") {
      await page.addInitScript(() => {
        window.localStorage.setItem("guardian.theme", "dark");
      });
    }
    await runTest(page);
  },
});

export { expect };
