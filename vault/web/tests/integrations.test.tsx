import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type { ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../src/design";
import Integrations from "../src/pages/Integrations";
import type { MeOut } from "../src/api";
import type { IntegrationCard, IntegrationsPayload } from "../src/types";
import type { RouterCard as RouterCardData } from "../src/lib/api";

// --- captured writes ---------------------------------------------------------

const { postedCalls } = vi.hoisted(() => {
  return { postedCalls: [] as { path: string; body: unknown }[] };
});

function posted(path: string): unknown {
  const call = [...postedCalls].reverse().find((c) => c.path === path);
  return call?.body;
}

vi.mock("../src/lib/api", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/api")>("../src/lib/api");
  return {
    ...actual,
    integrationsPayload: vi.fn(),
    saveIntegration: vi.fn((kind: string, body: Record<string, unknown>) => {
      postedCalls.push({ path: "/api/v1/integrations", body: { kind, ...body } });
      return Promise.resolve({});
    }),
    testIntegration: vi.fn(() => Promise.resolve({ ok: true, error: "" })),
    deleteIntegration: vi.fn(() => Promise.resolve({})),
    get: vi.fn(),
    // Far-future by design: this default backs every test that isn't
    // specifically about expiry, and must never be mistaken for expired
    // regardless of the real wall-clock time the suite happens to run at.
    pairTelegram: vi.fn(() => Promise.resolve({ code: "AB12CD", expires: "2099-01-01T00:00:00Z" })),
    routerCard: vi.fn(),
    saveRouter: vi.fn((body: Record<string, unknown>) => {
      postedCalls.push({ path: "/api/v1/router", body });
      return Promise.resolve({
        configured: true, vendor: "fios", vendor_label: "Verizon Fios (CR1000A / CR1000B)",
        base_url: "", username: "", has_credentials: true, enabled: true, status: "pending",
        last_test_ok: null, last_test_msg: "", last_test: "",
        remote_admin_off_at: "", remote_admin_msg: "", ...body,
      });
    }),
    testRouter: vi.fn(() => {
      postedCalls.push({ path: "/api/v1/router/test", body: {} });
      return Promise.resolve({ id: "q1", status: "queued" });
    }),
    deleteRouter: vi.fn(() => {
      postedCalls.push({ path: "DELETE /api/v1/router", body: {} });
      return Promise.resolve({ ok: true });
    }),
    disableRemoteAdminConfirm: vi.fn(() => {
      postedCalls.push({ path: "/api/v1/router/disable-remote-admin/confirm", body: {} });
      return Promise.resolve({ token: "tok-abc", expires: "2099-01-01T00:00:00Z", tier: 3,
                              summary: "Vault will turn OFF remote administration. Nothing else changes." });
    }),
    disableRemoteAdmin: vi.fn((confirm_token: string) => {
      postedCalls.push({ path: "/api/v1/router/disable-remote-admin", body: { confirm_token } });
      return Promise.resolve({ id: "dq1", status: "queued" });
    }),
    queueStatus: vi.fn(() =>
      Promise.resolve({ id: "dq1", path: "/api/router/disable-remote-admin", status: "done",
                        body: {}, result: { out: "remote administration disabled" }, ts: "" }),
    ),
  };
});

import { deleteIntegration, get, integrationsPayload, pairTelegram, testIntegration } from "../src/lib/api";
import { routerCard, testRouter } from "../src/lib/api";
import { disableRemoteAdminConfirm, queueStatus } from "../src/lib/api";

vi.mock("../src/api", async () => {
  const actual = await vi.importActual<typeof import("../src/api")>("../src/api");
  return {
    ...actual,
    me: vi.fn(() =>
      Promise.resolve({
        user: { username: "myron", display_name: "Myron", is_superadmin: false },
        tenants: [{ id: "t1", slug: "t1", name: "T1", role: "admin" }],
        active_tenant: { id: "t1", slug: "t1", name: "T1", role: "admin" },
        chat_providers: [],
      }),
    ),
  };
});

import { me } from "../src/api";

/** Unconfigured channels render collapsed to one line with a "Set up"
 *  button; tests that reach into their fields open them first. */
async function openCollapsed(scope?: HTMLElement) {
  const q = scope ? within(scope) : screen;
  for (const b of q.queryAllByRole("button", { name: /^set up /i })) await userEvent.click(b);
}

afterEach(() => {
  postedCalls.length = 0;
  vi.clearAllMocks();
  vi.useRealTimers();
});

// --- fixtures ----------------------------------------------------------------

function field(
  name: string,
  label: string,
  kind: string,
  value = "",
  required = false,
  options: string[] = [],
): IntegrationCard["fields"][number] {
  return { name, label, kind, value, required, options };
}

export const aiFailoverFixture = {
  ladder: [
    { name: "groq", configured: true, note: "Model pool; a rate-limited model hands the turn to the next one." },
    { name: "anthropic", configured: false, note: "No Anthropic API key set on the hub." },
    { name: "rules", configured: true, note: "Built-in answers; always the last rung." },
  ],
  models: [
    { model: "openai/gpt-oss-120b", role: "chat", in_pool: true, preferred: "first", rpm: 30, rpd: 1000, tpm: 8000, tpd: 200000, cooling_s: 12.4 },
    { model: "qwen/qwen3.8-27b", role: "chat", in_pool: true, preferred: "", rpm: 30, rpd: 1000, tpm: 8000, tpd: 200000, cooling_s: 0 },
    { model: "meta-llama/llama-prompt-guard-2-22m", role: "guard", in_pool: false, preferred: "", rpm: 30, rpd: 14400, tpm: 15000, tpd: 500000, cooling_s: 0 },
  ],
};

export const integrationsFixture: IntegrationsPayload = {
  ai: aiFailoverFixture,
  integrations: [
    {
      kind: "ntfy",
      title: "ntfy",
      help: "The default alert channel — always on, no setup required.",
      enabled: true,
      configured: true,
      fields: [field("topic", "Topic", "text", "guardian-demo", true)],
      routes: { CRIT: true, WARN: true, INFO: true },
      inbound_url: null,
      last_test: "2026-09-10T11:00:00-04:00",
      last_error: "",
      direction: "out",
      commands: [],
    },
    {
      kind: "slack",
      title: "Slack",
      help: "Posts alerts and the daily digest to a Slack channel.",
      enabled: true,
      configured: true,
      fields: [field("url", "Webhook URL", "secret", "https://hooks.slack.…uvwx", true)],
      routes: { CRIT: true, WARN: true, INFO: false },
      inbound_url: null,
      last_test: null,
      last_error: "",
      direction: "out",
      // The hub only sends these for a CONNECTED channel, which is what
      // makes them safe to render as "things you can do right now".
      commands: [
        "Ask it anything: “is anything wrong?”",
        "“yes” runs a change Vault has proposed, “no” drops it",
        "Everyone in the channel is a viewer here unless you list their user IDs above",
      ],
    },
    {
      kind: "teams",
      title: "Microsoft Teams",
      help: "Posts alerts and the daily digest to a Teams channel.",
      enabled: false,
      configured: false,
      fields: [field("url", "Webhook URL", "secret", "", true)],
      routes: { CRIT: true, WARN: false, INFO: false },
      inbound_url: null,
      last_test: null,
      last_error: "",
      direction: "out",
      commands: [],
    },
    {
      kind: "webhook",
      title: "Generic webhook",
      help: "POSTs every alert as JSON to your own HTTPS endpoint.",
      enabled: false,
      configured: false,
      fields: [field("url", "HTTPS URL", "url", "", true)],
      routes: { CRIT: true, WARN: true, INFO: false },
      inbound_url: null,
      last_test: null,
      last_error: "",
      direction: "out",
      commands: [],
    },
    {
      kind: "discord",
      title: "Discord",
      help: "Alert channel plus a /guardian status slash command.",
      enabled: false,
      configured: false,
      fields: [
        field("webhook_url", "Webhook URL", "secret", "", true),
        field("public_key", "Application public key", "text", "", false),
        field("guild_id", "Guild ID", "text", "", false),
      ],
      routes: { CRIT: true, WARN: true, INFO: false },
      inbound_url: "https://hub.example/inbound/discord/tok-discord",
      last_test: null,
      last_error: "",
      direction: "out+command",
      commands: [],
    },
    {
      kind: "pagerduty",
      title: "PagerDuty",
      help: "CRIT alerts open a PagerDuty incident and resolve automatically.",
      enabled: false,
      configured: false,
      fields: [field("routing_key", "Events API v2 routing key", "secret", "", true)],
      routes: { CRIT: true, WARN: false, INFO: false },
      inbound_url: "https://hub.example/inbound/pagerduty/tok-pd",
      last_test: null,
      last_error: "",
      direction: "out+in",
      commands: [],
    },
    {
      kind: "telegram",
      title: "Telegram",
      help: "Bot delivers alerts with Trust / Ignore / Ack / Fix now buttons.",
      enabled: false,
      configured: false,
      fields: [field("bot_token", "Bot token", "secret", "", true)],
      routes: { CRIT: true, WARN: true, INFO: false },
      inbound_url: "https://hub.example/inbound/telegram/tok-tg",
      last_test: null,
      last_error: "",
      direction: "out+in",
      commands: [],
    },
    {
      kind: "jira",
      title: "Jira / ServiceNow",
      help: "One ticket per incident, updated with fixes, closed when resolved.",
      enabled: false,
      configured: false,
      fields: [
        field("flavour", "Flavour", "select", "jira", true, ["jira", "servicenow"]),
        field("base_url", "Base URL", "url", "", true),
        field("email", "User / email", "text", "", true),
        field("token", "API token", "secret", "", true),
        field("project", "Project key / table", "text", "", true),
      ],
      routes: {},
      inbound_url: null,
      last_test: null,
      last_error: "",
      direction: "out+status",
      commands: [],
    },
    {
      kind: "homeassistant",
      title: "Home Assistant",
      help: "Publishes MQTT-discovery sensors and accepts quarantine/scan/ack calls.",
      enabled: false,
      configured: false,
      fields: [
        field("mqtt_host", "MQTT broker host", "text", "", true),
        field("mqtt_port", "MQTT port", "text", "1883", false),
        field("base_url", "Home Assistant base URL", "url", "", false),
        field("token", "Long-lived access token", "secret", "", false),
      ],
      routes: { CRIT: true, WARN: true, INFO: true },
      inbound_url: "https://hub.example/inbound/homeassistant/tok-ha",
      last_test: null,
      last_error: "",
      direction: "out+in",
      commands: [],
    },
  ],
};

const ADMIN_ME: MeOut = {
  user: { username: "a", display_name: "Admin", is_superadmin: false },
  tenants: [{ id: "t1", slug: "t1", name: "T1", role: "admin" }],
  active_tenant: { id: "t1", slug: "t1", name: "T1", role: "admin" },
  chat_providers: [],
};

const VIEWER_ME: MeOut = {
  user: { username: "v", display_name: "Viewer", is_superadmin: false },
  tenants: [{ id: "t1", slug: "t1", name: "T1", role: "viewer" }],
  active_tenant: { id: "t1", slug: "t1", name: "T1", role: "viewer" },
  chat_providers: [],
};

const ROUTER_EMPTY: RouterCardData = {
  configured: false,
  vendor: "fios",
  vendor_label: "Verizon Fios (CR1000A / CR1000B)",
  base_url: "",
  username: "",
  has_credentials: false,
  enabled: true,
  status: "unconfigured",
  last_test_ok: null,
  last_test_msg: "",
  last_test: "",
  remote_admin_off_at: "",
  remote_admin_msg: "",
};

const TOKENS_EMPTY = { tokens: [], max_role: "admin", base_url: "https://api.guarder.io" };

type Overrides = {
  integrations?: IntegrationsPayload;
  tokens?: unknown;
  test?: { ok: boolean; error: string; detail?: string };
  me?: MeOut;
  router?: RouterCardData;
};

function renderPage(element: ReactElement, overrides: Overrides = {}) {
  vi.mocked(integrationsPayload).mockResolvedValue(overrides.integrations ?? integrationsFixture);
  vi.mocked(me).mockResolvedValue(overrides.me ?? ADMIN_ME);
  vi.mocked(routerCard).mockResolvedValue(overrides.router ?? ROUTER_EMPTY);
  vi.mocked(get).mockResolvedValue(overrides.tokens ?? TOKENS_EMPTY);
  if (overrides.test) {
    vi.mocked(testIntegration).mockResolvedValue(overrides.test);
  }

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <MemoryRouter>{element}</MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );

  return { posted };
}

describe("Integrations", () => {
  it("lists all nine integrations with ntfy first", async () => {
    renderPage(<Integrations />, { integrations: integrationsFixture });
    const cards = await screen.findAllByTestId("integration-card");
    expect(cards).toHaveLength(9);
    expect(cards[0]).toHaveTextContent(/ntfy/i);
  });

  it("counts the reference's KPI tiles off the payload already on the wire", async () => {
    renderPage(<Integrations />, { integrations: integrationsFixture });
    const kpis = await screen.findByTestId("integration-kpis");
    const configured = integrationsFixture.integrations.filter((i) => i.configured).length;
    expect(within(kpis).getByText("Connected").parentElement).toHaveTextContent(String(configured));
    expect(within(kpis).getByText("Available").parentElement).toHaveTextContent(
      String(integrationsFixture.integrations.length),
    );
    // Nothing in the fixture is failing, so the third tile reads zero and
    // stays plain rather than warning about nothing.
    expect(within(kpis).getByText("Needs attention").parentElement).toHaveTextContent("0");
  });

  it("counts a connected channel that last failed its own send as needing attention", async () => {
    renderPage(<Integrations />, {
      integrations: {
        integrations: integrationsFixture.integrations.map((i) =>
          i.kind === "ntfy" ? { ...i, last_error: "ntfy 503" } : i,
        ),
      },
    });
    const kpis = await screen.findByTestId("integration-kpis");
    expect(within(kpis).getByText("Needs attention").parentElement).toHaveTextContent("1");
  });

  it("never sends the masked secret back to the hub", async () => {
    const { posted } = renderPage(<Integrations />, { integrations: integrationsFixture });
    const slack = (await screen.findAllByTestId("integration-card"))[1];
    await userEvent.click(within(slack).getByRole("checkbox", { name: "INFO" }));
    await userEvent.click(within(slack).getByRole("button", { name: /save/i }));
    const body = posted("/api/v1/integrations") as { routes: Record<string, boolean> };
    expect(body.routes.INFO).toBe(true);
    expect(JSON.stringify(body)).not.toContain("…");
  });

  it("shows what a successful test found beside OK, and plain OK when it found nothing to say", async () => {
    renderPage(<Integrations />, {
      integrations: integrationsFixture,
      test: { ok: true, error: "", detail: "Connected to HaloPSA. Company 'Northwind Dental' is id 42." },
    });
    await userEvent.click((await screen.findAllByRole("button", { name: /^test$/i }))[0]);
    expect(await screen.findByText(/OK · Connected to HaloPSA\. Company 'Northwind Dental' is id 42\./)).toBeInTheDocument();
  });

  it("shows the test error verbatim", async () => {
    renderPage(<Integrations />, {
      integrations: integrationsFixture,
      test: { ok: false, error: "ntfy 503" },
    });
    await userEvent.click((await screen.findAllByRole("button", { name: /^test$/i }))[0]);
    expect(await screen.findByText("ntfy 503")).toBeInTheDocument();
  });

  it("shows the inbound URL only for inbound-capable integrations", async () => {
    renderPage(<Integrations />, { integrations: integrationsFixture });
    const cards = await screen.findAllByTestId("integration-card");
    await openCollapsed();
    // Explicit allow-list, matching the component's own — direction strings
    // are exactly "out" | "out+in" | "out+command" | "out+status", and
    // "out+command" (Discord) is inbound-capable despite not containing
    // the substring "in".
    const INBOUND_DIRECTIONS = new Set(["out+in", "out+command"]);
    const inbound = integrationsFixture.integrations.filter((i) => INBOUND_DIRECTIONS.has(i.direction)).length;
    expect(screen.getAllByTestId("inbound-url")).toHaveLength(inbound);
    expect(within(cards[0]).queryByTestId("inbound-url")).toBeNull();
  });

  it("shows the inbound hint for Discord's out+command direction, not just out+in", async () => {
    renderPage(<Integrations />, { integrations: integrationsFixture });
    const cards = await screen.findAllByTestId("integration-card");
    const discord = cards.find((c) => c.textContent?.includes("Discord"));
    expect(discord).toBeDefined();
    await openCollapsed(discord as HTMLElement);
    expect(within(discord as HTMLElement).getByTestId("inbound-url")).toBeInTheDocument();
  });

  it("only posts a changed text field, never the untouched ones", async () => {
    const { posted } = renderPage(<Integrations />, { integrations: integrationsFixture });
    const ntfy = (await screen.findAllByTestId("integration-card"))[0];
    const topicInput = within(ntfy).getByLabelText("Topic");
    await userEvent.clear(topicInput);
    await userEvent.type(topicInput, "new-topic");
    await userEvent.click(within(ntfy).getByRole("button", { name: /save/i }));
    const body = posted("/api/v1/integrations") as { fields: Record<string, string> };
    expect(body.fields).toEqual({ topic: "new-topic" });
  });

  it("requires Replace before a secret field can be retyped, then sends the new value", async () => {
    const { posted } = renderPage(<Integrations />, { integrations: integrationsFixture });
    const slack = (await screen.findAllByTestId("integration-card"))[1];
    expect(within(slack).queryByLabelText("Webhook URL")).toBeNull();
    await userEvent.click(within(slack).getByRole("button", { name: /replace/i }));
    const input = within(slack).getByLabelText("Webhook URL");
    expect(input).toHaveValue("");
    await userEvent.type(input, "https://hooks.slack.com/services/NEW");
    await userEvent.click(within(slack).getByRole("button", { name: /save/i }));
    const body = posted("/api/v1/integrations") as { fields: Record<string, string> };
    expect(body.fields.url).toBe("https://hooks.slack.com/services/NEW");
  });

  // `GET /api/v1/integrations` is admin-gated in the hub (a card carries
  // masked credentials and an inbound token URL), so a non-admin must get
  // the explanation and NOTHING else — the earlier version of these two
  // tests mocked a payload at viewer/operator role that the hub would only
  // ever answer with a 403, and asserted disabled buttons on cards the
  // real app could never have rendered.
  it("shows a non-admin the explanation and no cards at all", async () => {
    renderPage(<Integrations />, { integrations: integrationsFixture, me: VIEWER_ME });
    expect(await screen.findByText(/admin-only area/i)).toBeInTheDocument();
    expect(screen.queryAllByTestId("integration-card")).toHaveLength(0);
    // Nor any of the surrounding explanatory blocks: a viewer gets the
    // reason they cannot see this, not a tour of channels and commands
    // they have no access to.
    expect(screen.queryByTestId("channel-commands")).toBeNull();
    expect(screen.queryByTestId("ai-failover")).toBeNull();
    // ...and never asks: the read itself would be refused.
    expect(vi.mocked(integrationsPayload)).not.toHaveBeenCalled();
  });

  it("treats an operator the same as a viewer here", async () => {
    const OPERATOR_ME: MeOut = {
      user: { username: "o", display_name: "Operator", is_superadmin: false },
      tenants: [{ id: "t1", slug: "t1", name: "T1", role: "operator" }],
      active_tenant: { id: "t1", slug: "t1", name: "T1", role: "operator" },
      chat_providers: [],
    };
    renderPage(<Integrations />, { integrations: integrationsFixture, me: OPERATOR_ME });
    expect(await screen.findByText(/admin-only area/i)).toBeInTheDocument();
    expect(screen.queryAllByTestId("integration-card")).toHaveLength(0);
    expect(vi.mocked(integrationsPayload)).not.toHaveBeenCalled();
  });

  it("lets an admin cancel a Replace back to the masked secret view", async () => {
    renderPage(<Integrations />, { integrations: integrationsFixture });
    const slack = (await screen.findAllByTestId("integration-card"))[1];
    await userEvent.click(within(slack).getByRole("button", { name: /replace/i }));
    expect(within(slack).getByLabelText("Webhook URL")).toBeInTheDocument();
    expect(within(slack).getByText(/leave blank to keep the current secret/i)).toBeInTheDocument();
    await userEvent.click(within(slack).getByRole("button", { name: /cancel/i }));
    expect(within(slack).queryByLabelText("Webhook URL")).toBeNull();
    expect(within(slack).getByRole("button", { name: /replace/i })).toBeInTheDocument();
  });

  it("re-masks a secret field after a successful save", async () => {
    renderPage(<Integrations />, { integrations: integrationsFixture });
    const slack = (await screen.findAllByTestId("integration-card"))[1];
    await userEvent.click(within(slack).getByRole("button", { name: /replace/i }));
    const input = within(slack).getByLabelText("Webhook URL");
    await userEvent.type(input, "https://hooks.slack.com/services/NEW");
    await userEvent.click(within(slack).getByRole("button", { name: /save/i }));
    await screen.findByText(/saved/i);
    expect(within(slack).queryByLabelText("Webhook URL")).toBeNull();
    expect(within(slack).getByRole("button", { name: /replace/i })).toBeInTheDocument();
  });

  it("stops polling and shows an expiry message once the pairing code's TTL passes", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    // Expires 2s from "now" (real wall-clock time the fake clock starts
    // from) — advancing the fake clock past that, below, simulates the
    // code going stale without pinning the test to a fixed calendar date.
    // `...Once` rather than `...Value`: `vi.clearAllMocks()` in afterEach
    // resets call history but not a standing `mockResolvedValue` override,
    // which would otherwise leak this near-future expiry into later tests
    // (e.g. the plain pairing-success test) that expect the far-future
    // default from the top-level mock factory.
    const expires = new Date(Date.now() + 2000).toISOString();
    vi.mocked(pairTelegram).mockResolvedValueOnce({ code: "AB12CD", expires });
    renderPage(<Integrations />, { integrations: integrationsFixture });
    const telegram = (await screen.findAllByTestId("integration-card")).find((c) => c.textContent?.includes("Telegram")) as HTMLElement;
    await openCollapsed(telegram);
    await userEvent.click(within(telegram).getByRole("button", { name: /pair/i }));
    expect(await within(telegram).findByText("AB12CD")).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3500);
    });

    expect(within(telegram).getByText(/pairing code expired/i)).toBeInTheDocument();
    expect(within(telegram).getByRole("button", { name: /pair again/i })).toBeInTheDocument();
  });

  it("badges each card with its state instead of relying on colour alone", async () => {
    renderPage(<Integrations />, { integrations: integrationsFixture });
    const cards = await screen.findAllByTestId("integration-card");
    // ntfy: configured, enabled, never failed.
    expect(within(cards[0]).getByText("Connected")).toBeInTheDocument();
    // teams: never configured.
    expect(within(cards[2]).getByText("Not connected")).toBeInTheDocument();
  });

  it("badges a configured channel that last failed its send as needing attention", async () => {
    renderPage(<Integrations />, {
      integrations: {
        integrations: integrationsFixture.integrations.map((i) =>
          i.kind === "slack" ? { ...i, last_error: "connection refused" } : i,
        ),
      },
    });
    const slack = (await screen.findAllByTestId("integration-card"))[1];
    expect(within(slack).getByText("Needs attention")).toBeInTheDocument();
    // Named up front, above the grid, not just inside the one card.
    expect(await screen.findByText(/needs attention: slack/i)).toBeInTheDocument();
  });

  it("badges a configured-but-disabled channel as paused", async () => {
    renderPage(<Integrations />, {
      integrations: {
        integrations: integrationsFixture.integrations.map((i) =>
          i.kind === "slack" ? { ...i, enabled: false } : i,
        ),
      },
    });
    const slack = (await screen.findAllByTestId("integration-card"))[1];
    expect(within(slack).getByText("Paused")).toBeInTheDocument();
  });

  it("offers Disconnect only for a configured channel other than ntfy", async () => {
    renderPage(<Integrations />, { integrations: integrationsFixture });
    const cards = await screen.findAllByTestId("integration-card");
    // ntfy (configured, but the always-on default channel): no Disconnect.
    expect(within(cards[0]).queryByRole("button", { name: /disconnect/i })).toBeNull();
    // slack (configured): offers it.
    expect(within(cards[1]).getByRole("button", { name: /disconnect/i })).toBeInTheDocument();
    // teams (never configured): nothing to disconnect.
    expect(within(cards[2]).queryByRole("button", { name: /disconnect/i })).toBeNull();
  });

  it("asks for confirmation before disconnecting, naming what stops working", async () => {
    renderPage(<Integrations />, { integrations: integrationsFixture });
    const slack = (await screen.findAllByTestId("integration-card"))[1];
    await userEvent.click(within(slack).getByRole("button", { name: /disconnect/i }));
    expect(await screen.findByRole("alertdialog")).toBeInTheDocument();
    expect(screen.getByText(/disconnect slack/i)).toBeInTheDocument();
    expect(screen.getByText(/alerts stop going there/i)).toBeInTheDocument();
    expect(deleteIntegration).not.toHaveBeenCalled();
  });

  it("cancelling the disconnect confirmation calls nothing", async () => {
    renderPage(<Integrations />, { integrations: integrationsFixture });
    const slack = (await screen.findAllByTestId("integration-card"))[1];
    await userEvent.click(within(slack).getByRole("button", { name: /disconnect/i }));
    await userEvent.click(await screen.findByRole("button", { name: /cancel/i }));
    expect(deleteIntegration).not.toHaveBeenCalled();
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("confirming the disconnect calls the delete endpoint for that kind", async () => {
    renderPage(<Integrations />, { integrations: integrationsFixture });
    const slack = (await screen.findAllByTestId("integration-card"))[1];
    await userEvent.click(within(slack).getByRole("button", { name: /disconnect/i }));
    const dialog = await screen.findByRole("alertdialog");
    await userEvent.click(within(dialog).getByRole("button", { name: /^disconnect$/i }));
    expect(deleteIntegration).toHaveBeenCalledWith("slack");
    await screen.findByText(/slack disconnected/i);
  });

  it("pairs Telegram with a 6-character code and polls until it is configured", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderPage(<Integrations />, { integrations: integrationsFixture });
    const telegram = (await screen.findAllByTestId("integration-card")).find((c) => c.textContent?.includes("Telegram")) as HTMLElement;
    await openCollapsed(telegram);
    await userEvent.click(within(telegram).getByRole("button", { name: /pair/i }));
    expect(await within(telegram).findByText("AB12CD")).toBeInTheDocument();

    // Only *after* the initial mount fetch (consumed above) does the mock
    // start returning a bound chat_id — this is what the poll should see.
    vi.mocked(integrationsPayload).mockResolvedValue({
      integrations: integrationsFixture.integrations.map((i) =>
        i.kind === "telegram" ? { ...i, configured: true } : i,
      ),
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3500);
    });
    expect(within(telegram).getByText(/paired/i)).toBeInTheDocument();
    expect(vi.mocked(pairTelegram)).toHaveBeenCalledTimes(1);
  });

  it("shows the router card in its own devices section, separate from channels", async () => {
    renderPage(<Integrations />);
    const routerCardEl = await screen.findByTestId("router-card");
    expect(within(routerCardEl).getByText(/Verizon Fios/i)).toBeInTheDocument();
    // The router is NOT one of the notification channels.
    const channels = screen.getAllByTestId("integration-card");
    expect(channels).toHaveLength(9);
    expect(channels).not.toContain(routerCardEl);
    // Two named sections frame the page.
    expect(screen.getByRole("heading", { name: /notification channels/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /devices on your network/i })).toBeInTheDocument();
  });

  it("saves the router address and password, never echoing the secret back", async () => {
    const { posted } = renderPage(<Integrations />);
    const routerCardEl = await screen.findByTestId("router-card");
    const addr = within(routerCardEl).getByPlaceholderText(/192\.168\.1\.1/i);
    await userEvent.clear(addr);
    await userEvent.type(addr, "https://192.168.1.1");
    const pw = within(routerCardEl).getByPlaceholderText(/router admin password/i);
    await userEvent.type(pw, "s3cret-admin");
    await userEvent.click(within(routerCardEl).getByRole("button", { name: /save router/i }));
    await screen.findByText(/router saved/i);
    const body = posted("/api/v1/router") as Record<string, unknown>;
    expect(body.base_url).toBe("https://192.168.1.1");
    expect(body.password).toBe("s3cret-admin");
  });

  it("a configured router keeps its stored secret and can be tested without resending it", async () => {
    const { posted } = renderPage(<Integrations />, {
      router: { ...ROUTER_EMPTY, configured: true, has_credentials: true, base_url: "https://192.168.1.1", status: "pending" },
    });
    const routerCardEl = await screen.findByTestId("router-card");
    // Secret is masked, not an input, until Replace is clicked.
    expect(within(routerCardEl).queryByPlaceholderText(/router admin password/i)).toBeNull();
    expect(within(routerCardEl).getByText(/saved/i)).toBeInTheDocument();
    await userEvent.click(within(routerCardEl).getByRole("button", { name: /test connection/i }));
    expect(posted("/api/v1/router/test")).toEqual({});
    expect(vi.mocked(testRouter)).toHaveBeenCalledTimes(1);
  });

  it("reports a failed router test on the card", async () => {
    renderPage(<Integrations />, {
      router: {
        ...ROUTER_EMPTY, configured: true, has_credentials: true, base_url: "https://192.168.1.1",
        status: "error", last_test_ok: false, last_test_msg: "router rejected the credential (403)",
      },
    });
    const routerCardEl = await screen.findByTestId("router-card");
    expect(within(routerCardEl).getByText(/rejected the credential/i)).toBeInTheDocument();
    expect(within(routerCardEl).getByText("Test failed")).toBeInTheDocument();
  });

  it("only offers the disable-remote-admin action once the credential is verified", async () => {
    renderPage(<Integrations />, {
      router: { ...ROUTER_EMPTY, configured: true, has_credentials: true, base_url: "https://192.168.1.1", status: "pending" },
    });
    const routerCardEl = await screen.findByTestId("router-card");
    // Not verified yet: no disable button.
    expect(within(routerCardEl).queryByRole("button", { name: /turn off remote administration/i })).toBeNull();
  });

  it("turns off remote administration behind a tier-3 confirmation, then reports the sensor result", async () => {
    const { posted } = renderPage(<Integrations />, {
      router: {
        ...ROUTER_EMPTY, configured: true, has_credentials: true, base_url: "https://192.168.1.1",
        status: "ok", last_test_ok: true, last_test_msg: "credential accepted",
      },
    });
    const routerCardEl = await screen.findByTestId("router-card");
    await userEvent.click(within(routerCardEl).getByRole("button", { name: /turn off remote administration/i }));
    // A confirmation is minted and its summary shown in a dialog.
    expect(vi.mocked(disableRemoteAdminConfirm)).toHaveBeenCalledTimes(1);
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByRole("heading", { name: /turn off remote administration\?/i })).toBeInTheDocument();
    // Nothing was sent to the mutating endpoint until the admin confirms.
    expect(posted("/api/v1/router/disable-remote-admin")).toBeUndefined();
    await userEvent.click(within(dialog).getByRole("button", { name: /turn it off/i }));
    const body = posted("/api/v1/router/disable-remote-admin") as { confirm_token: string };
    expect(body.confirm_token).toBe("tok-abc");
    // The queued job's result is polled and surfaced.
    await screen.findByText(/remote administration disabled/i);
    expect(vi.mocked(queueStatus)).toHaveBeenCalled();
  });

  it("credits a disable that already ran instead of asking for it again", async () => {
    // The bug: the owner pressed the button, the sensor did the work, and the
    // card went on offering the same action on every render because nothing
    // was written down.
    renderPage(<Integrations />, {
      router: {
        ...ROUTER_EMPTY, configured: true, has_credentials: true, base_url: "https://192.168.1.1",
        status: "ok", last_test_ok: true, last_test_msg: "credential accepted",
        remote_admin_off_at: "2026-09-17T14:05:00+00:00",
        remote_admin_msg: "remote administration disabled (remote_ipv4/ipv6_enable=0, https_ipv4/ipv6_enable=0)",
      },
    });
    const routerCardEl = await screen.findByTestId("router-card");
    // No longer phrased as an outstanding request...
    expect(within(routerCardEl).queryByRole("button", { name: /^turn off remote administration$/i })).toBeNull();
    // ...it says Vault did it, and offers a re-run for the case where
    // somebody switched it back on from the router's own page.
    expect(within(routerCardEl).getByText(/Vault turned this off on/i)).toBeInTheDocument();
    expect(within(routerCardEl).getByRole("button", { name: /turn off again/i })).toBeInTheDocument();
  });

  it("an unanswered gateway still gets asked", async () => {
    renderPage(<Integrations />, {
      router: {
        ...ROUTER_EMPTY, configured: true, has_credentials: true, base_url: "https://192.168.1.1",
        status: "ok", last_test_ok: true, last_test_msg: "credential accepted",
      },
    });
    const routerCardEl = await screen.findByTestId("router-card");
    expect(within(routerCardEl).getByRole("button", { name: /^turn off remote administration$/i })).toBeInTheDocument();
    expect(within(routerCardEl).queryByText(/Vault turned this off/i)).toBeNull();
  });

  it("says what a connected channel answers, and only for connected ones", async () => {
    renderPage(<Integrations />, { integrations: integrationsFixture });
    const block = await screen.findByTestId("channel-commands");
    // Slack is connected and answers things, so it is here...
    expect(within(block).getByRole("heading", { name: "Slack" })).toBeInTheDocument();
    expect(within(block).getByText(/is anything wrong/i)).toBeInTheDocument();
    // ...and the gating fact is stated in the copy, not buried in a field
    // label: with no user IDs listed, everyone in the channel is a viewer.
    expect(within(block).getByText(/everyone in the channel is a viewer/i)).toBeInTheDocument();
    // Teams is not connected, so it is not offering commands that would
    // not answer.
    expect(within(block).queryByRole("heading", { name: /teams/i })).toBeNull();
  });

  it("says nothing about commands when no connected channel answers any", async () => {
    renderPage(<Integrations />, {
      integrations: {
        ...integrationsFixture,
        integrations: integrationsFixture.integrations.map((i) => ({ ...i, commands: [] })),
      },
    });
    await screen.findByTestId("integration-kpis");
    expect(screen.queryByTestId("channel-commands")).toBeNull();
  });

  it("points at the ticket system when something will not connect", async () => {
    renderPage(<Integrations />, { integrations: integrationsFixture });
    const link = await screen.findByRole("link", { name: /open a support ticket/i });
    expect(link).toHaveAttribute("href", "/support");
  });

  it("claims only what the ladder actually guarantees about repeats", async () => {
    // The narrow, true statement: the CONFIRMATION is single-use. A broader
    // "nothing that changed something ever replays" would be claiming
    // something `chat()` does not promise between rounds.
    renderPage(<Integrations />, { integrations: integrationsFixture });
    const section = await screen.findByTestId("ai-failover");
    expect(within(section).getByText(/confirmation is single-use/i)).toBeInTheDocument();
    expect(within(section).getAllByText(/if it fails or is rate-limited/i).length).toBeGreaterThan(0);
  });

  it("shows the AI failover ladder and the Groq model pool, with a parked model flagged", async () => {
    renderPage(<Integrations />);
    const section = await screen.findByTestId("ai-failover");
    expect(within(section).getByRole("heading", { name: "AI failover" })).toBeInTheDocument();
    const rungs = within(section).getAllByRole("listitem");
    expect(rungs.map((r) => r.textContent)).toEqual([
      expect.stringContaining("Groq"),
      expect.stringContaining("Anthropic"),
      expect.stringContaining("Built-in rules"),
    ]);
    expect(within(section).getByText("not set up")).toBeInTheDocument();      // anthropic has no key
    expect(within(section).getByText("parked 13s")).toBeInTheDocument();      // 120b is cooling
    expect(within(section).getByText("prompt guard")).toBeInTheDocument();    // listed, marked "not used"
    expect(within(section).getByText("not used")).toBeInTheDocument();
    expect(section.textContent).not.toMatch(/gsk_|sk-ant/);                   // never a key
  });
});

// --- API access --------------------------------------------------------------
//
// The token flow itself is covered by tests/apiTokens.test.tsx against the page.
// What matters here is that Integrations is a second ENTRANCE to that one
// implementation, and that a credential-minting form is not the loudest thing
// on a page every admin sees in simple mode.

describe("Integrations: API access", () => {
  const TOKENS_TWO = {
    max_role: "admin",
    base_url: "https://api.guarder.io",
    tokens: [
      {
        id: "t1", name: "Zapier", prefix: "gapi_9f2c41ab", scope: "write", role: "operator",
        created_by: "miro", created: "2026-09-01T00:00:00Z", expires: null,
        last_used: null, last_used_ip: "", revoked: false,
      },
      {
        id: "t2", name: "Old script", prefix: "gapi_11112222", scope: "read", role: "viewer",
        created_by: "miro", created: "2026-08-01T00:00:00Z", expires: null,
        last_used: null, last_used_ip: "", revoked: true,
      },
    ],
  };

  it("counts only the live tokens, so a revoked one does not read as access", async () => {
    renderPage(<Integrations />, { tokens: TOKENS_TWO });
    const section = await screen.findByTestId("api-access");
    expect(within(section).getByRole("heading", { name: /API access/i })).toBeInTheDocument();
    // Two rows, one revoked -> one still works.
    expect(within(section).getByText("Active").parentElement).toHaveTextContent("1");
  });

  it("stays shut until asked, so minting a key is never the default gesture", async () => {
    renderPage(<Integrations />, { tokens: TOKENS_TWO });
    const section = await screen.findByTestId("api-access");
    expect(within(section).queryByRole("button", { name: /create token/i })).toBeNull();

    await userEvent.click(within(section).getByText(/manage tokens/i));
    expect(within(section).getByRole("button", { name: /create token/i })).toBeInTheDocument();
    // The same table the page shows, not a second implementation of it.
    expect(within(section).getByText("Zapier")).toBeInTheDocument();
  });

  it("says plainly when there are none rather than showing an empty stat", async () => {
    renderPage(<Integrations />, { tokens: TOKENS_EMPTY });
    const section = await screen.findByTestId("api-access");
    expect(within(section).getByText("Active").parentElement).toHaveTextContent("0");
  });
});
