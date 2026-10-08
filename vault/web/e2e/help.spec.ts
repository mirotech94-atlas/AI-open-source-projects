import { DEMO_CODE, expect, loadSession, saveSession, test } from "./fixtures";

// Task 71, walked in a real browser against a real hub: the first-run wizard
// fires for a user who has never seen it (even on a tenant that is fully set
// up), the Help section reads, and "Ask Guardian about this" reaches the chat
// panel and comes back with an answer.
//
// Deliberately does NOT use `loginAsDemo`: that helper caches one session and
// walks past the wizard, and the whole point here is to see the wizard for a
// user who has never been through it.

type Page = import("@playwright/test").Page;

/** One real OTP sign-in per username per machine; every later call replays the
 *  cached session. See `fixtures.ts`'s `loadSession` for why the cache is on
 *  disk — three projects times four tests would otherwise walk into the hub's
 *  five-sign-in-codes-per-ten-minutes limit. The wizard state this suite is
 *  about lives on the hub, keyed by user, never in the cookie, so a replayed
 *  session proves the same things a fresh login would. */
async function signIn(page: Page, username: string) {
  await page.context().clearCookies();
  const cached = loadSession(username);
  if (cached) {
    await page.context().addCookies(cached);
    await page.goto("/overview");
    if (!/\/login$/.test(new URL(page.url()).pathname)) return;
  }
  await page.goto("/login");
  await page.getByLabel("Username").fill(username);
  await page.getByRole("button", { name: /send code/i }).click();
  await page.getByLabel("6-digit code").fill(DEMO_CODE);
  await page.getByRole("button", { name: /verify/i }).click();
  await page.waitForURL(/\/welcome$|\/overview$|\/$/, { timeout: 15_000 });
  saveSession(username, await page.context().cookies());
}

/** Puts this user back to "has never seen the wizard" on this tenant — the same
 *  call Settings → Sensors' "Re-run setup" makes. Without it these tests would
 *  pass once and then never again: completion is real, server-side state, and
 *  the three projects share one hub. */
async function resetWizard(page: Page): Promise<void> {
  const ok = await page.evaluate(async () => {
    const res = await fetch("/api/v1/onboarding", {
      method: "POST",
      credentials: "same-origin",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ completed: false, skipped: false, step: "" }),
    });
    return res.ok;
  });
  expect(ok).toBeTruthy();
}

test.describe("help and the first-run wizard", () => {
  test("a user who has never seen the wizard gets the tour on an established tenant", async ({ page }) => {
    // The seeded demo tenant has a sensor and scans — the old per-tenant gate
    // would show this user nothing at all.
    await signIn(page, "demo");
    await resetWizard(page);
    // Asking for Overview is what a returning user does; the gate is what sends
    // them to the wizard instead.
    await page.goto("/overview");
    await page.waitForURL(/\/welcome$/, { timeout: 15_000 });

    // The ORIENTING path: no site-name field, no OS picker, no enrol command.
    await expect(page.getByRole("heading", { level: 1, name: /welcome to/i })).toBeVisible();
    await expect(page.getByText(/nothing to install/i)).toBeVisible();
    await expect(page.getByLabel(/what should we call/i)).toHaveCount(0);
    await expect(page.getByRole("button", { name: /^macOS$/ })).toHaveCount(0);

    await page.getByRole("button", { name: /^Next$/ }).click();
    await expect(page.getByRole("heading", { level: 1, name: /where the answers live/i })).toBeVisible();
    await page.getByRole("button", { name: /^Next$/ }).click();
    await expect(page.getByRole("heading", { level: 1, name: /when you need a hand/i })).toBeVisible();
    await page.getByRole("button", { name: /^Done$/ }).click();
    await page.waitForURL(/\/overview$/, { timeout: 10_000 });

    // Completed, per user, on the hub: a reload does not put them back in it.
    await page.reload();
    await page.waitForURL(/\/overview$/, { timeout: 10_000 });
    await expect(page.getByLabel("Security score")).toBeVisible();

    const state = await page.evaluate(async () => {
      const res = await fetch("/api/v1/onboarding", { credentials: "same-origin" });
      return (await res.json()) as { show: boolean; path: string; completed: boolean };
    });
    expect(state).toMatchObject({ show: false, path: "tour", completed: true });
  });

  test("two guides read, and Ask Guardian answers about this site", async ({ page }, testInfo) => {
    const isMobile = testInfo.project.name === "mobile";
    await signIn(page, "demoadmin");
    await resetWizard(page);
    await page.goto("/overview");
    // This account has never been through it either — walk past it first.
    await page.waitForURL(/\/welcome$/, { timeout: 15_000 });
    await page.getByRole("button", { name: /skip for now/i }).click();
    await page.waitForURL(/\/overview$/, { timeout: 10_000 });

    // Guide one, reached through the nav rather than by typing a URL — the
    // sidebar on a desktop, the "More" sheet on a phone (which has no sidebar
    // and no flyout, so every nav child has to be reachable there).
    if (isMobile) {
      await page.getByRole("button", { name: "More" }).click();
      await page.getByRole("menu", { name: "All tabs" }).getByRole("menuitem", { name: "Help" }).click();
    } else {
      await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Help", exact: true }).click();
    }
    await page.waitForURL(/\/help$/, { timeout: 10_000 });
    await page.getByRole("link", { name: /where to find your enrolment code/i }).click();
    await expect(page.getByRole("heading", { level: 1, name: /enrolment code/i })).toBeVisible();
    // The guide's own destination is a live link into the app.
    await expect(page.getByRole("link", { name: /Config → Sensors/ })).toHaveAttribute(
      "href",
      "/settings/sensors",
    );

    // Guide two, via the article's own crumb — scoped to the content area,
    // since the sidebar's dropdown carries an "All guides" row too.
    const content = page.locator("#g-shell-main-content");
    await content.getByRole("link", { name: "All guides" }).click();
    await page.getByRole("link", { name: /what the severities mean/i }).click();
    await expect(page.getByRole("heading", { level: 1, name: /severities/i })).toBeVisible();
    await expect(page.getByText(/hides it for eight hours/i)).toBeVisible();

    // ⌘K finds a guide by a phrase nobody would think to put in a title.
    await page.keyboard.press("ControlOrMeta+k");
    await page.getByRole("combobox").fill("enrol code");
    await expect(
      page.getByRole("listbox").getByText("Where to find your enrolment code"),
    ).toBeVisible();
    await page.keyboard.press("Escape");

    // "Ask Guardian about this" opens the panel with the question already asked
    // and answered — a real `POST /chat` turn about this site.
    const answer = page.waitForResponse(
      (r) => r.url().includes("/api/v1/chat") && r.request().method() === "POST",
      { timeout: 25_000 },
    );
    await page.getByRole("button", { name: /ask guardian about this/i }).click();
    const panel = page.getByRole("dialog", { name: /guardian chat/i });
    await expect(panel).toBeVisible();
    const res = await answer;
    expect(res.ok()).toBeTruthy();
    const body = res.request().postDataJSON() as { message: string };
    expect(body.message.toLowerCase()).toContain("alert");
    // Guardian's reply is rendered in the transcript, not just requested.
    await expect(panel.locator(".g-chat-msg-guardian").nth(1)).toBeVisible({ timeout: 25_000 });
  });

  test("neither the index nor an article scrolls sideways, at any width", async ({ page }) => {
    await signIn(page, "demoadmin");
    for (const path of ["/help", "/help/alerts-and-severities", "/help/api"]) {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
      const overflow = await page.evaluate(() => {
        const el = document.documentElement;
        return el.scrollWidth - el.clientWidth;
      });
      expect(overflow, `${path} overflows horizontally`).toBeLessThanOrEqual(1);
    }
  });

  test("the API guide says the reference is not written yet, and names no endpoints", async ({ page }) => {
    await signIn(page, "demoadmin");
    await page.goto("/help/api");
    await expect(page.getByRole("heading", { level: 1, name: "API guide" })).toBeVisible();
    await expect(page.getByText(/still being written/i)).toBeVisible();
    await expect(page.locator(".g-api-endpoint")).toHaveCount(0);
  });
});
