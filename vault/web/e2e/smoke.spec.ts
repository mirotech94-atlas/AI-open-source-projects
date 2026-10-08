import { expect, loginAsDemo, test } from "./fixtures";

// One end-to-end walk through every phase-2 tab as the seeded `demo` viewer.
// Runs three times (light desktop, dark desktop, 375x812 mobile — see
// playwright.config.ts) against a hub freshly migrated + seeded for this
// run, so every assertion below is checking real data, not a mock.

test.describe("guardian phase-2 smoke", () => {
  test("sign in, then every tab renders with real data", async ({ page }, testInfo) => {
    const isMobile = testInfo.project.name === "mobile";

    await test.step("sign in with the demo code", async () => {
      await loginAsDemo(page);
      await expect(page).toHaveURL(/\/overview$|\/$/);
    });

    await test.step("Overview: score hero + 24-block health strip", async () => {
      await page.goto("/overview");
      // VerdictHero's Ring renders the score as text inside the SVG/label —
      // assert on the accessible label rather than a specific number, since
      // the demo score is derived from synthesized history and must not be
      // hard-coded here.
      await expect(page.getByLabel("Security score")).toBeVisible();
      await expect(page.locator(".g-verdict-text")).not.toBeEmpty();

      // One row of 24 blocks per monitored site/host — assert at least one
      // full row's worth rendered, and that the count is an exact multiple
      // of 24 (never a partial row).
      const hourBlocks = page.getByTestId("hour-block");
      await expect(hourBlocks.first()).toBeVisible({ timeout: 10_000 });
      const blockCount = await hourBlocks.count();
      expect(blockCount).toBeGreaterThanOrEqual(24);
      expect(blockCount % 24).toBe(0);
    });

    await test.step("Inbox: rows render and an ack changes the row status", async () => {
      await page.goto("/inbox");
      const rows = page.getByRole("list", { name: "Inbox rows" }).getByRole("listitem");
      await expect(rows.first()).toBeVisible({ timeout: 10_000 });
      const rowCountBefore = await rows.count();
      expect(rowCountBefore).toBeGreaterThan(0);

      const firstRow = rows.first();
      const ackButton = firstRow.getByRole("button", { name: "Ack" });
      await expect(ackButton).toBeVisible();

      // Capture the row's entity/title text so the API check below can find
      // the same alert unambiguously (`InboxRow.id` isn't rendered as text).
      const rowTitle = (await firstRow.locator(".g-inbox-title, .g-inbox-title-button").first().textContent())?.trim();
      expect(rowTitle).toBeTruthy();

      const ackResponse = page.waitForResponse(
        (r) => /\/api\/v1\/alerts\/.+\/ack$/.test(r.url()) && r.request().method() === "POST",
      );
      await ackButton.click();
      const resp = await ackResponse;
      expect(resp.ok()).toBeTruthy();

      // Confirm the status change server-side (not just the optimistic UI
      // patch): re-fetch the inbox over the same session and find the row
      // that was just acked.
      const inbox = await page.request.get("/api/v1/inbox?view=alerts&status=all");
      expect(inbox.ok()).toBeTruthy();
      const body = await inbox.json();
      const acked = (body.rows as Array<{ title: string; status: string }>).find(
        (r) => r.title.trim() === rowTitle,
      );
      expect(acked).toBeTruthy();
      expect(acked?.status).toBe("acked");
    });

    await test.step("Assets: device chips, drawer, and the screen-time heatmap", async () => {
      await page.goto("/assets/devices");
      // One kind-chip group per network section, each named after its own
      // network ("Filter Office Wi-Fi by device kind") — see DeviceGrid's
      // `chipsLabel`. Match the shared tail and take the first section's.
      await expect(page.getByRole("group", { name: /by device kind$/ }).first()).toBeVisible();

      const deviceCard = page.getByText("iPhone").first();
      await expect(deviceCard).toBeVisible({ timeout: 10_000 });
      await deviceCard.click();

      // Opening a device navigates to /assets/devices/:mac and renders the
      // drawer — assert the route change, which only happens once the click
      // actually resolved to a real device with a MAC.
      await page.waitForURL(/\/assets\/devices\/.+/);
      await expect(page.locator(".g-drawer, [role='dialog']").first()).toBeVisible();

      await page.goto("/assets/screen-time");
      await expect(page.locator(".g-heatmap, [class*='heat']").first()).toBeVisible({ timeout: 10_000 });
    });

    await test.step("Command palette: ⌘K finds a device", async () => {
      await page.goto("/overview");
      await page.keyboard.press(isMobile ? "Control+k" : "Meta+k");
      const palette = page.getByRole("dialog", { name: "Command palette" });
      await expect(palette).toBeVisible();

      await page.getByRole("combobox").fill("iPhone");
      const option = page.getByRole("option", { name: /iPhone/i });
      await expect(option.first()).toBeVisible({ timeout: 10_000 });
      await page.keyboard.press("Escape");
      await expect(palette).toBeHidden();
    });

    await test.step("theme toggle flips data-theme", async () => {
      const before = await page.evaluate(() => document.documentElement.dataset.theme ?? "");
      const themeButton = page.locator("[aria-label^='Theme:']");
      await themeButton.click();
      await expect
        .poll(() => page.evaluate(() => document.documentElement.dataset.theme ?? ""))
        .not.toBe(before);
    });

    await test.step("Security: a graded scorecard, plus attackers on the map or the unlocated list", async () => {
      await page.goto("/security");
      const scorecard = page.getByTestId("scorecard").first();
      await expect(scorecard).toBeVisible({ timeout: 10_000 });
      await expect(scorecard.locator(".g-sec-scorecard-letter")).not.toBeEmpty();
      await expect(scorecard.locator(".g-sec-scorecard-score")).not.toBeEmpty();

      // `AttackMap` is lazy-loaded (Task 33) — give its chunk a moment, then
      // accept either a located marker or the unlocated list: seeded
      // attackers may or may not have resolved geo, and the assertion below
      // must hold either way rather than assuming one shape.
      const marker = page.getByTestId("attack-marker").first();
      const unlocated = page.getByTestId("attackers-unlocated");
      await expect(marker.or(unlocated).first()).toBeVisible({ timeout: 10_000 });
    });

    await test.step("Updates: patch calendar and a CVE row", async () => {
      await page.goto("/updates");
      await expect(page.getByTestId("patch-cell").first()).toBeVisible({ timeout: 10_000 });
      await expect(page.getByTestId("cve-row").first()).toBeVisible({ timeout: 10_000 });
    });

    await test.step("DNS: the live stream ticks within 15s of subscribing, and a block rule can be added", async () => {
      // Wait for the initial `GET /api/v1/dns` (the page's own `useQuery`)
      // to settle before touching the rule form below. The app now cancels
      // any in-flight `["dns"]` query in `onMutate` before writing, so this
      // wait is belt-and-braces rather than a workaround — it just keeps
      // the test from adding a rule faster than any real click ever could.
      const initialLoad = page.waitForResponse(
        (r) => r.url().includes("/api/v1/dns?") && r.request().method() === "GET",
      );
      await page.goto("/dns");
      await initialLoad;

      // The page's `useLiveFeed({ subscribe: ["dns"] })` sends the
      // subscribe frame as soon as the socket is open; the hub replays a
      // burst of the tenant's seeded `DnsQuery` rows right back, so the
      // stream should never sit empty on a freshly-seeded demo tenant.
      await expect(page.getByTestId("dns-row").first()).toBeVisible({ timeout: 15_000 });

      const blockInput = page.getByLabel("Block a domain");
      const domain = `smoke-test-${Date.now()}.example`;
      await blockInput.fill(domain);
      const ruleResponse = page.waitForResponse(
        (r) => r.url().endsWith("/api/v1/dns/rules") && r.request().method() === "POST",
      );
      await blockInput.press("Enter");
      const resp = await ruleResponse;
      expect(resp.ok()).toBeTruthy();
      await expect(page.getByText(domain, { exact: true })).toBeVisible({ timeout: 10_000 });
    });

    await test.step("Topology: nodes render and clicking one opens a drawer", async () => {
      await page.goto("/topology");
      const node = page.getByTestId("topo-node").first();
      await expect(node).toBeVisible({ timeout: 10_000 });
      await node.click();
      const drawer = page.locator(".g-drawer, [role='dialog']").first();
      await expect(drawer).toBeVisible({ timeout: 10_000 });

      // Leave it closed for the next test.step / run — same convention as
      // the sidebar-collapse step below, which otherwise finds the drawer's
      // backdrop still intercepting clicks.
      await page.keyboard.press("Escape");
      await expect(drawer).toBeHidden();
    });

    if (!isMobile) {
      await test.step("sidebar collapses", async () => {
        const sidebar = page.locator("nav.g-sidebar");
        await expect(sidebar).not.toHaveClass(/g-sidebar-collapsed/);
        await page.getByRole("button", { name: "Collapse sidebar" }).click();
        await expect(sidebar).toHaveClass(/g-sidebar-collapsed/);
        // Leave it as found for the next test.step / run.
        await page.getByRole("button", { name: "Expand sidebar" }).click();
        await expect(sidebar).not.toHaveClass(/g-sidebar-collapsed/);
      });
    }
  });
});

// ---------------------------------------------------------------------------
// Phase 4: Reports, Settings (every sub-page), Integrations, the Guardian
// chat panel, the onboarding wizard, and the session-less public status page.
// Same three projects as the walk above (light desktop, dark desktop,
// 375x812 mobile) against the same freshly-seeded hub.
// ---------------------------------------------------------------------------

/** Every Settings sub-page the demo admin can reach, with one locator that
 *  only resolves if that section actually rendered its own content (not the
 *  shell, not a loading hint). `demoadmin` is an admin but not a superadmin,
 *  so `tenants`/`billing` are covered by their role notice instead. */
const SETTINGS_SECTIONS: { section: string; check: (page: import("@playwright/test").Page) => unknown }[] = [
  { section: "profile", check: (p) => p.locator("#settings-profile-owner") },
  { section: "users", check: (p) => p.getByRole("heading", { name: "Invite a member" }) },
  { section: "targets", check: (p) => p.getByRole("heading", { name: "Websites" }) },
  { section: "networks", check: (p) => p.getByRole("button", { name: "Add network" }) },
  { section: "autofix", check: (p) => p.locator("#settings-autofix-cap") },
  { section: "thresholds", check: (p) => p.locator("#settings-threshold-disk_pct_warn") },
  { section: "appearance", check: (p) => p.locator(".g-settings-theme-option").first() },
  { section: "sensors", check: (p) => p.getByRole("button", { name: "Generate enrol code" }) },
  { section: "audit", check: (p) => p.locator("#audit-filter-action") },
  { section: "tenants", check: (p) => p.getByText("Tenants is a superadmin-only area.") },
  { section: "billing", check: (p) => p.getByText("Billing is a superadmin-only area.") },
];

const PLACEHOLDER_WEBHOOK = "https://guardian-smoke.invalid/hook";
// A webhook URL is a credential (anyone holding it can post), so the hub
// masks `fields[0]` on the way out — `routers/integrations._mask`: first 14
// chars, an ellipsis, last 6. Asserting the exact mask still pins the exact
// URL that was stored, without the API ever echoing it back in clear.
const MASKED_WEBHOOK = `${PLACEHOLDER_WEBHOOK.slice(0, 14)}\u2026${PLACEHOLDER_WEBHOOK.slice(-6)}`;

test.describe("guardian phase-4 smoke", () => {
  test("reports, settings, integrations, chat, onboarding, public status", async ({ page, browser }) => {
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    // A 5xx from the hub reaches the console only as Chrome's generic
    // "Failed to load resource: the server responded with a status of
    // 500" — which any filter loose enough to drop the deliberate network
    // failures below would also swallow. (That is exactly how a
    // `GET /settings` 500 survived a green smoke run and had to be caught
    // by hand in the browser.) Watch the responses themselves instead.
    // One API answer is expected here and nothing else is:
    //  - `GET /api/v1/live` 429s — `routers/live.py` caps the probe at 12
    //    calls per minute per tenant, and six tests landing on Overview
    //    back to back in one minute is far more pressure than the app's
    //    own 10-second poll (6/min for one tab) ever applies.
    // A 5xx is never expected, from any route.
    // `GET /api/v1/networks` used to be listed here as an expected 404 — it
    // is a real route since task 51, and Settings → Networks reads it, so a
    // 404 from it is now a failure like any other.
    const EXPECTED_API_FAILURES = new Set(["429 GET /api/v1/live"]);
    const apiFailures: string[] = [];
    page.on("response", (r) => {
      const path = new URL(r.url()).pathname;
      if (r.status() >= 400 && path.startsWith("/api/")) {
        const line = `${r.status()} ${r.request().method()} ${path}`;
        if (r.status() >= 500 || !EXPECTED_API_FAILURES.has(line)) apiFailures.push(line);
      }
    });

    await test.step("sign in with the demo code", async () => {
      await loginAsDemo(page);
    });

    // -- Reports: build -> share -> read the link with no session -> delete --
    let shareUrl = "";
    let builtTitle = "";
    await test.step("Reports: build an executive report", async () => {
      await page.goto("/reports");
      await expect(page.getByLabel("Preset")).toBeVisible({ timeout: 10_000 });

      builtTitle = `Smoke ${Date.now()}`;
      await page.getByLabel("Title").fill(builtTitle);
      const build = page.waitForResponse(
        (r) => r.url().endsWith("/api/v1/reports/build") && r.request().method() === "POST",
      );
      await page.getByRole("button", { name: /build report/i }).click();
      const resp = await build;
      expect(resp.ok()).toBeTruthy();

      // The card is prepended into the query cache, not refetched — so a
      // visible card with this run's title proves the real 200 body came
      // back with the title the form sent.
      await expect(page.getByTestId("report-card").filter({ hasText: builtTitle })).toBeVisible({
        timeout: 10_000,
      });
    });

    await test.step("Reports: share the report and read it with no cookie", async () => {
      const card = page.getByTestId("report-card").filter({ hasText: builtTitle });
      await card.getByRole("button", { name: "Share" }).click();

      const share = page.waitForResponse(
        (r) => /\/api\/v1\/reports\/.+\/share$/.test(r.url()) && r.request().method() === "POST",
      );
      await page.getByRole("button", { name: /create link/i }).click();
      expect((await share).ok()).toBeTruthy();

      const urlInput = page.getByLabel("Share link");
      await expect(urlInput).toBeVisible();
      shareUrl = await urlInput.inputValue();
      expect(shareUrl).toContain("/r/");

      // A genuinely session-less read: `page.request` and a second tab in
      // this context both carry the signed-in cookie, so the share link has
      // to be opened from a brand-new browser context to prove the `/r/`
      // route needs no auth at all.
      const anon = await browser.newContext();
      try {
        const anonPage = await anon.newPage();
        const opened = await anonPage.goto(shareUrl);
        expect(opened?.status()).toBe(200);
        await expect(anonPage.locator("body")).toContainText(/Guardian/i);
      } finally {
        await anon.close();
      }

      await page.keyboard.press("Escape");
    });

    await test.step("Reports: delete the report this run built", async () => {
      const card = page.getByTestId("report-card").filter({ hasText: builtTitle });
      const del = page.waitForResponse(
        (r) => /\/api\/v1\/reports\/[^/]+$/.test(r.url()) && r.request().method() === "DELETE",
      );
      await card.getByRole("button", { name: "Delete" }).click();
      expect((await del).ok()).toBeTruthy();
      await expect(card).toHaveCount(0, { timeout: 10_000 });

      // The share link must die with the report, not linger as a readable
      // orphan — checked from a session-less context for the same reason.
      const anon = await browser.newContext();
      try {
        const anonPage = await anon.newPage();
        const opened = await anonPage.goto(shareUrl);
        expect(opened?.status()).not.toBe(200);
      } finally {
        await anon.close();
      }
    });

    // -- Settings: every sub-page, plus a threshold that survives a reload --
    await test.step("Settings: every sub-page renders", async () => {
      for (const { section, check } of SETTINGS_SECTIONS) {
        await page.goto(`/settings/${section}`);
        await expect(check(page) as ReturnType<typeof page.locator>).toBeVisible({ timeout: 10_000 });
      }
    });

    await test.step("Settings: a changed threshold survives a reload", async () => {
      await page.goto("/settings/thresholds");
      const field = page.locator("#settings-threshold-disk_pct_warn");
      await expect(field).toBeVisible({ timeout: 10_000 });
      const before = Number(await field.inputValue());
      const after = before === 91 ? 92 : 91;

      const save = page.waitForResponse(
        (r) => r.url().endsWith("/api/v1/settings/thresholds") && r.request().method() === "POST",
      );
      await field.fill(String(after));
      await page.getByRole("button", { name: "Save", exact: true }).click();
      expect((await save).ok()).toBeTruthy();

      await page.reload();
      await expect(page.locator("#settings-threshold-disk_pct_warn")).toHaveValue(String(after), {
        timeout: 10_000,
      });

      // Put it back so the three projects (and any re-run) all start from
      // the same seeded value.
      const restore = page.waitForResponse(
        (r) => r.url().endsWith("/api/v1/settings/thresholds") && r.request().method() === "POST",
      );
      await page.locator("#settings-threshold-disk_pct_warn").fill(String(before));
      await page.getByRole("button", { name: "Save", exact: true }).click();
      expect((await restore).ok()).toBeTruthy();
    });

    // -- Integrations: nine cards, a real Test result, and an add/delete
    //    round-trip on a webhook that is left DISABLED the whole time --
    await test.step("Integrations: every provider card renders and Test reports a result", async () => {
      await page.goto("/settings/integrations");
      const cards = page.getByTestId("integration-card");
      await expect(cards.first()).toBeVisible({ timeout: 10_000 });
      expect(await cards.count()).toBeGreaterThanOrEqual(9);

      // ntfy is the always-on default channel and the one card guaranteed
      // to be configured on a seeded tenant, so it is the honest subject
      // for "the Test button reaches the hub and reports something".
      // Filtered by the card's own toggle label (`"<title> enabled"`), not
      // by free text: several cards' help copy contains the word "webhook"
      // or "ntfy", so `hasText` would resolve to the wrong article.
      const ntfy = cards.filter({ has: page.getByRole("switch", { name: "ntfy enabled" }) });
      const test$ = page.waitForResponse(
        (r) => /\/api\/v1\/integrations\/[^/]+\/test$/.test(r.url()) && r.request().method() === "POST",
      );
      await ntfy.getByRole("button", { name: "Test", exact: true }).click();
      expect((await test$).ok()).toBeTruthy();
      // Either outcome is a pass: this hub has no outbound network in CI, so
      // what is being verified is that the admin-only Test round-trip works
      // and its verdict is rendered, not that ntfy.sh is reachable.
      await expect(ntfy.locator(".g-tone-good, .g-tone-alert").first()).toBeVisible({ timeout: 15_000 });
    });

    await test.step("Integrations: add a disabled placeholder webhook, then delete it", async () => {
      const webhook = page
        .getByTestId("integration-card")
        .filter({ has: page.getByRole("switch", { name: "Webhook enabled" }) });
      await expect(webhook).toBeVisible();

      // Left OFF on purpose: a smoke run must never arm a delivery channel.
      const toggle = webhook.getByRole("switch");
      await expect(toggle).toHaveAttribute("aria-checked", "false");

      await webhook.locator("#integration-webhook-url").fill(PLACEHOLDER_WEBHOOK);
      const save = page.waitForResponse(
        (r) => r.url().endsWith("/api/v1/integrations") && r.request().method() === "POST",
      );
      await webhook.getByRole("button", { name: "Save", exact: true }).click();
      expect((await save).ok()).toBeTruthy();

      // Persisted, not just an optimistic cache patch.
      await page.reload();
      const reloaded = page
        .getByTestId("integration-card")
        .filter({ has: page.getByRole("switch", { name: "Webhook enabled" }) });
      await expect(reloaded.locator("#integration-webhook-url")).toHaveValue(MASKED_WEBHOOK, {
        timeout: 10_000,
      });
      await expect(reloaded.getByRole("switch")).toHaveAttribute("aria-checked", "false");

      // The card UI has no Delete affordance (a provider card is always
      // present, configured or not) — removal is `DELETE /integrations/
      // {kind}`, which is what Guardian's own teardown uses. Called over
      // the signed-in session so it is a real authorised admin write.
      const deleted = await page.request.delete("/api/v1/integrations/webhook");
      expect(deleted.ok()).toBeTruthy();
      await page.reload();
      await expect(
        page
          .getByTestId("integration-card")
          .filter({ has: page.getByRole("switch", { name: "Webhook enabled" }) })
          .locator("#integration-webhook-url"),
      ).toHaveValue("", { timeout: 10_000 });
    });

    // -- Chat: a real answer from the rules provider, then the tier-3 gate --
    await test.step("Chat: asking for status gets a real answer", async () => {
      await page.goto("/overview");
      await page.getByRole("button", { name: "Toggle Guardian chat" }).click();
      const panel = page.getByRole("complementary", { name: "Guardian chat" });
      await expect(panel).toBeVisible();

      const turn = page.waitForResponse(
        (r) => r.url().endsWith("/api/v1/chat") && r.request().method() === "POST",
      );
      await panel.getByLabel("Ask Guardian").fill("status");
      await panel.getByRole("button", { name: "Send" }).click();
      const body = await (await turn).json();

      // The local hub has no Groq/Anthropic key, so this is the deterministic
      // rules rung answering — which still has to obey the strict format.
      expect(body.provider).toBe("rules");
      expect(String(body.text)).toBeTruthy();
      await expect(panel.locator(".g-chat-msg-guardian").first()).toBeVisible({ timeout: 20_000 });
      await expect(panel.getByTestId("chat-next").first()).toBeVisible();
    });

    await test.step("Chat: a tier-3 ask proposes and does not execute", async () => {
      const panel = page.getByRole("complementary", { name: "Guardian chat" });

      // The proposal itself can only originate from a model tool call
      // (`providers.chat` returns no tool calls on the rules rung), so the
      // LLM turn is stubbed at the HTTP seam — the same seam the hub's own
      // tests monkeypatch. Everything below the stub is real app code:
      // `useChat`, `Message`, `ProposalCard`, and the confirm request the
      // web would send. `confirmSeen` is the security assertion: if the web
      // ever confirms a tier-3 action without a click on "Yes, do it", this
      // step fails.
      let confirmSeen = false;
      await page.route("**/api/v1/chat", async (route) => {
        const posted = route.request().postDataJSON() as { message?: string; confirm_token?: string };
        if (posted?.confirm_token) {
          confirmSeen = true;
          await route.abort();
          return;
        }
        if (!/reboot/i.test(posted?.message ?? "")) {
          await route.fallback();
          return;
        }
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            text: "I can reboot safehome, but I need your go-ahead first.",
            verdict: "",
            bullets: [],
            next: [],
            tool_steps: [
              {
                tool: "host_command",
                args: { host: "safehome", command: "reboot" },
                tier: 3,
                status: "proposed",
                result: null,
                queue_id: null,
              },
            ],
            proposal: {
              token: "smoke-proposal-token",
              summary: "Reboot safehome and confirm its services come back.",
              actions: [
                {
                  tool: "host_command",
                  args: { host: "safehome", command: "reboot" },
                  tier: 3,
                  effect: "reboots the host",
                  reversible: false,
                  host: "safehome",
                },
              ],
              expires: new Date(Date.now() + 5 * 60_000).toISOString(),
            },
            provider: "stub",
            thread_id: "smoke-thread",
          }),
        });
      });

      await panel.getByLabel("Ask Guardian").fill("reboot safehome");
      await panel.getByRole("button", { name: "Send" }).click();

      const proposal = panel.getByTestId("proposal").first();
      await expect(proposal).toBeVisible({ timeout: 20_000 });
      await expect(proposal).toContainText("not reversible");
      await expect(proposal.getByRole("button", { name: /yes, do it/i })).toBeEnabled();
      await expect(panel.getByTestId("tool-step").first()).toBeVisible();

      // Declining is the safe path and the one this suite takes — "Yes" is
      // deliberately never clicked here (a reboot is not a smoke-test side
      // effect), and nothing may have been confirmed on our behalf.
      await proposal.getByRole("button", { name: "No", exact: true }).click();
      await expect(proposal).toHaveCount(0);
      expect(confirmSeen).toBe(false);

      await page.unroute("**/api/v1/chat");

      // And the real hub's own half of the gate, with no stub in the way: a
      // confirm_token that names no stored proposal must come back as an
      // expired confirmation with zero tool steps — never as an execution.
      const forged = await page.request.post("/api/v1/chat", {
        data: { message: "yes", confirm_token: "not-a-real-token", tab: "overview" },
      });
      expect(forged.ok()).toBeTruthy();
      const forgedBody = await forged.json();
      expect(forgedBody.tool_steps).toEqual([]);
      expect(String(forgedBody.text)).toMatch(/expired or was already used/i);

      await panel.getByRole("button", { name: "Close chat" }).click();
    });

    // -- Onboarding: re-run from Settings, then skip back out --
    await test.step("Onboarding: Re-run setup opens the wizard, Skip for now leaves it", async () => {
      await page.goto("/settings/sensors");
      await page.getByRole("button", { name: "Re-run setup" }).click();
      await page.waitForURL(/\/welcome$/);
      await expect(page.getByRole("list", { name: "Setup progress" })).toBeVisible();

      await page.getByRole("button", { name: "Skip for now" }).click();
      await page.waitForURL(/\/overview$/);
      await expect(page.getByLabel("Security score")).toBeVisible({ timeout: 10_000 });

      // Re-runnable, not a one-shot: the same button opens it again after a
      // skip (the dismissed flag is cleared, not consumed).
      await page.goto("/settings/sensors");
      await page.getByRole("button", { name: "Re-run setup" }).click();
      await page.waitForURL(/\/welcome$/);
      await page.getByRole("button", { name: "Skip for now" }).click();
      await page.waitForURL(/\/overview$/);
    });

    // -- The public status page, with no session at all --
    await test.step("public /status/demo renders for a visitor with no session", async () => {
      const anon = await browser.newContext();
      try {
        const anonPage = await anon.newPage();
        const opened = await anonPage.goto("/status/demo");
        expect(opened?.status()).toBe(200);
        await expect(anonPage.getByRole("heading", { name: "Service status" })).toBeVisible();
        // `seed-demo` publishes the demo tenant's sites, so the page must
        // list real services rather than the empty-state line.
        await expect(anonPage.locator(".row").first()).toBeVisible();
        await expect(anonPage.locator("body")).not.toContainText("No public services");
      } finally {
        await anon.close();
      }
    });

    // Nothing under /api/ may fail during the whole walk, bar the one
    // known-absent route above.
    expect(apiFailures).toEqual([]);

    // ...and no console errors either. Exactly two classes of non-app
    // noise are dropped, both named rather than pattern-matched loosely:
    //  - the requests this test deliberately makes fail — the unreachable
    //    placeholder webhook host, ntfy.sh (this hub has no outbound
    //    network), and the confirm request the tier-3 route handler
    //    aborts on purpose;
    //  - "Blocked script execution in 'about:srcdoc'", logged once per
    //    load of the report-preview iframe. That frame is sandboxed
    //    WITHOUT allow-scripts by design and the report HTML it renders
    //    carries no script of its own (verified: `builder.build_report`
    //    with `toolbar=False` emits none) — the blocked script is the test
    //    runner's own injected frame utility, i.e. the sandbox working.
    // A generic "Failed to load resource" is NOT filtered: that is how a
    // 5xx surfaces, and `serverErrors` above exists for the same reason.
    const realErrors = consoleErrors.filter(
      (e) =>
        !/guardian-smoke\.invalid|ntfy\.sh|net::ERR_FAILED.*\/api\/v1\/chat/i.test(e) &&
        // Accounted for by `apiFailures` above, which knows the path the
        // bare console line does not carry.
        !/the server responded with a status of (404|429)/i.test(e) &&
        !/Blocked script execution in 'about:srcdoc'/i.test(e),
    );
    expect(realErrors).toEqual([]);
  });
});

// --- Task 53: several networks per tenant -----------------------------------
//
// Tagged `@networks`, and run — Playwright only runs in a closeout task, and
// this is that task. The demo seed builds exactly two networks — "Office
// Wi-Fi" (primary, 192.168.1, `demo-sensor`) and "Lab" (10.10.0, no sensor) —
// so every assertion below is against real seeded data.

test.describe("guardian networks smoke @networks", () => {
  test("both networks appear, the chip filters, the map clusters, and Settings can add and delete one", async ({
    page,
  }) => {
    await loginAsDemo(page);

    await test.step("Assets: one section per network, and the Lab chip narrows the grid", async () => {
      await page.goto("/assets/devices");

      const sections = page.getByTestId("network-section");
      await expect(sections.first()).toBeVisible({ timeout: 10_000 });
      await expect(page.getByRole("heading", { name: "Office Wi-Fi" })).toBeVisible();
      await expect(page.getByRole("heading", { name: "Lab", exact: true })).toBeVisible();
      expect(await sections.count()).toBe(2);

      // The Lab has no sensor in the seed, so it must say so in words.
      await expect(page.getByTestId("needs-sensor").first()).toBeVisible();

      const scoped = page.waitForResponse(
        (r) => r.url().includes("/api/v1/devices") && r.url().includes("network="),
      );
      await page.getByTestId("network-chip").filter({ hasText: "Lab" }).click();
      await scoped;

      // Scoped to one network: the Office section is gone.
      await expect(page.getByRole("heading", { name: "Office Wi-Fi" })).toHaveCount(0);
      await expect(page.getByRole("heading", { name: "Lab", exact: true })).toBeVisible();

      // Back to everything, so the persisted selection does not leak into the
      // steps below (it is stored per tenant in localStorage).
      await page.getByRole("button", { name: "All networks" }).click();
      await expect(page.getByRole("heading", { name: "Office Wi-Fi" })).toBeVisible();
    });

    await test.step("Topology: one cluster per network, and the multi-homed node is drawn once", async () => {
      await page.goto("/topology");
      await expect(page.getByTestId("topo-node").first()).toBeVisible({ timeout: 15_000 });

      await page.getByRole("button", { name: "Network", exact: true }).click();
      const clusters = page.getByTestId("topo-cluster");
      await expect(clusters.first()).toBeVisible();
      expect(await clusters.count()).toBeGreaterThanOrEqual(2);

      // No MAC is drawn twice, whatever the hub sent.
      const macs = await page.getByTestId("topo-node").evaluateAll((nodes) =>
        nodes.map((n) => (n as HTMLElement).dataset.mac ?? "").filter((m) => m !== ""),
      );
      expect(new Set(macs).size).toBe(macs.length);
    });

    await test.step("Security: one Wi-Fi guard panel per network", async () => {
      await page.goto("/security");
      const guards = page.getByTestId("wifi-guard");
      await expect(guards.first()).toBeVisible({ timeout: 10_000 });
      expect(await guards.count()).toBe(2);
    });

    await test.step("Settings → Networks: add one, then delete it with a reassign", async () => {
      await page.goto("/settings/networks");
      await expect(page.getByRole("button", { name: "Add network" })).toBeVisible({ timeout: 10_000 });

      // The primary can never be deleted.
      await expect(page.getByTestId("delete-office-wi-fi")).toBeDisabled();

      await page.getByRole("button", { name: "Add network" }).click();
      await page.getByLabel("Name", { exact: true }).fill("Guest Wi-Fi");
      // By role, not by label: `getByLabel` matches a wrapping <label>'s
      // textContent, and this label wraps the <select>, so its text is
      // "Kind" + every option ("wifiwiredlab…"). The accessible NAME is
      // "Kind", which is what getByRole matches.
      await page.getByRole("combobox", { name: "Kind" }).selectOption("guest");
      await page.getByLabel("Subnet", { exact: true }).fill("192.168.5");
      await page.getByLabel("Gateway", { exact: true }).fill("192.168.5.1");
      const created = page.waitForResponse(
        (r) => r.url().endsWith("/api/v1/networks") && r.request().method() === "POST",
      );
      await page.getByRole("button", { name: "Save", exact: true }).click();
      expect((await created).status()).toBeLessThan(400);
      await expect(page.getByTestId("delete-guest-wi-fi")).toBeVisible();

      await page.getByTestId("delete-guest-wi-fi").click();
      await expect(page.getByLabel("Move its devices to")).toBeVisible();
      const deleted = page.waitForResponse(
        (r) => r.url().includes("/api/v1/networks/") && r.request().method() === "DELETE",
      );
      await page.getByRole("button", { name: "Delete network" }).click();
      expect((await deleted).status()).toBeLessThan(400);
      await expect(page.getByTestId("delete-guest-wi-fi")).toHaveCount(0);
    });
  });
});
