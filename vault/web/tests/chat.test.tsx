import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MeOut } from "../src/api";
import { ToastProvider } from "../src/design";
import Shell from "../src/shell/Shell";
import Inbox from "../src/pages/Inbox";
import type {
  AlertHandoff, ChatRequest, ChatResponse, InboxPayload, LiveEvent, OverviewPayload, UpdatesPayload,
} from "../src/types";

vi.mock("../src/api", async () => {
  const actual = await vi.importActual<typeof import("../src/api")>("../src/api");
  return {
    ...actual,
    me: vi.fn(),
    authLogout: vi.fn().mockResolvedValue({ ok: true }),
    setActiveTenant: vi.fn().mockResolvedValue({ ok: true }),
  };
});

vi.mock("../src/lib/api", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/api")>("../src/lib/api");
  return {
    ...actual,
    overviewPayload: vi.fn(),
    inboxPayload: vi.fn(),
    updatesPayload: vi.fn(),
    chat: vi.fn(),
    queueStatus: vi.fn(),
    runAction: vi.fn(),
    alertHandoff: vi.fn(),
    onboardingState: vi.fn(),
    saveOnboarding: vi.fn(),
  };
});

vi.mock("../src/lib/live", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/live")>("../src/lib/live");
  return { ...actual, useLiveFeed: vi.fn() };
});

import { me } from "../src/api";
import {
  alertHandoff, chat, inboxPayload, onboardingState, overviewPayload, queueStatus, runAction,
  saveOnboarding, updatesPayload,
} from "../src/lib/api";
import Help from "../src/pages/Help";
import { useLiveFeed } from "../src/lib/live";
import { NO_HERO } from "./overviewHero";

const fixtureMe: MeOut = {
  user: { username: "miro", display_name: "Miro", is_superadmin: true },
  tenants: [
    { id: "t1", slug: "home", name: "Home", role: "admin" },
    { id: "t2", slug: "office", name: "Office", role: "admin" },
  ],
  active_tenant: { id: "t1", slug: "home", name: "Home", role: "admin" },
  chat_providers: ["groq", "anthropic", "rules"],
};

const fixtureMeT2: MeOut = { ...fixtureMe, active_tenant: { id: "t2", slug: "office", name: "Office", role: "admin" } };

const fixtureOverview: OverviewPayload = {
  ts: "2026-09-09T18:30:26-04:00",
  age_s: 0,
  score: 83,
  grade: "B",
  potential: 98,
  score_model: "",
  score_coverage: null,
  verdict: "Mostly fine.",
  next_action: null,
  deductions: [],
  score_change: null,
  strip: [],
  timeline: [],
  score_trend: { name: "score", points: [], color: null },
  questions: [],
  alerts: { crit: 0, warn: 0, info: 0, open: 0, acked: 0, snoozed: 0 },
  devices: { total: 10, online: 8, sleeping: 2, unverified: 0, quarantined: 0 },
  sensor: {
    name: "mac",
    status: "active",
    last_seen: "2026-09-09T18:30:26-04:00",
    age_s: 5,
    platform: "darwin",
    version: "0.1.0",
  },
  networks: [],
  chat_example_ip: "192.168.1.166",
  voice: false,
  needs_you: [],
  fixed_without_you: { days: 14, automatic: 0, approved: 0, daily: [] },
  coverage: [],
  certs_expiring: [],
  ...NO_HERO,
};

const fixtureInbox: InboxPayload = {
  counts: { incidents: 0, alerts: 0, recs: 0, unread: 0, needs: 0 },
  rows: [],
};

const fixtureUpdates: UpdatesPayload = {
  calendar: [],
  hosts: [],
  cves: [],
  progress: [],
  posture: { pending: 0, security_pending: 0, needs_reboot: 0, servers_checked: 0, servers_unchecked: 0, unmeasured: 0 },
  reboot_plan: { hosts: [], windows: [] },
  mac: { os: "", available: [], checked: "", error: "", reported: false },
};

export const chatAnswerFixture: ChatResponse = {
  text: "Mostly fine.\nRouter · 192.168.1.1 · online\nNext: say *yes* and I will run a scan.",
  verdict: "Mostly fine.",
  bullets: [
    { text: "Router · 192.168.1.1 · online", entity: { kind: "device", id: "aa:bb:cc:dd:ee:ff" } },
    { text: "NAS · 192.168.1.20 · backup ran last night", entity: { kind: "device", id: "11:22:33:44:55:66" } },
  ],
  next: [{ label: "Say *yes* and I will run a scan.", method: "POST", path: "/api/v1/run", body: {} }],
  tool_steps: [],
  proposal: null,
  provider: "groq/openai/gpt-oss-120b",
  thread_id: "thread-1",
};

export const chatProposalFixture: ChatResponse = {
  text: "I will reboot safehome now — about one minute of downtime. Reply *yes* to confirm.",
  verdict: "",
  bullets: [],
  next: [],
  tool_steps: [],
  proposal: {
    token: "confirm-token-abc",
    summary: "I will reboot safehome now — about one minute of downtime for the dashboards.",
    actions: [
      {
        tool: "apply_fix",
        args: { host: "safehome", action: "reboot" },
        tier: 3,
        effect: "reboot safehome",
        reversible: false,
        host: "safehome",
      },
    ],
    expires: new Date(Date.now() + 10 * 60_000).toISOString(),
  },
  provider: "groq/openai/gpt-oss-20b",
  thread_id: "thread-2",
};

const chatConfirmedFixture: ChatResponse = {
  text: "Rebooting safehome now.",
  verdict: "",
  bullets: [],
  next: [],
  tool_steps: [
    { tool: "apply_fix", args: { host: "safehome", action: "reboot" }, tier: 3, status: "queued", result: null, queue_id: "q-reboot-1" },
  ],
  proposal: null,
  provider: "groq/openai/gpt-oss-20b",
  thread_id: "thread-2",
};

export const chatQueuedFixture: ChatResponse = {
  text: "Running a scan now.",
  verdict: "",
  bullets: [],
  next: [],
  tool_steps: [{ tool: "rescan", args: {}, tier: 1, status: "queued", result: null, queue_id: "q-scan-1" }],
  proposal: null,
  provider: "groq/openai/gpt-oss-20b",
  thread_id: "thread-3",
};

// One bullet per entity kind, plus one that never resolved to anything —
// covers the full `ChatEntity.kind` union for the entity-link test.
const chatEntityFixture: ChatResponse = {
  text: "",
  verdict: "Mixed entities.",
  bullets: [
    { text: "Router · 192.168.1.1 · online", entity: { kind: "device", id: "aa:bb:cc:dd:ee:ff" } },
    { text: "laptop.local · 192.168.1.50 · sleeping", entity: { kind: "host", id: "laptop.local" } },
    { text: "safehome · vps · reachable", entity: { kind: "site", id: "safehome" } },
    { text: "Unknown · - · no match", entity: { kind: null, id: null } },
  ],
  next: [],
  tool_steps: [],
  proposal: null,
  provider: "groq/openai/gpt-oss-120b",
  thread_id: "thread-entities",
};

// Eight bullets — the strict format caps display at six.
const chatManyBulletsFixture: ChatResponse = {
  text: "",
  verdict: "Lots going on.",
  bullets: Array.from({ length: 8 }, (_, i) => ({
    text: `Device ${i} · 192.168.1.${i} · fact ${i}`,
    entity: { kind: null, id: null },
  })),
  next: [],
  tool_steps: [],
  proposal: null,
  provider: "groq/openai/gpt-oss-120b",
  thread_id: "thread-many",
};

type ChatOverride = ChatResponse | ChatResponse[];

function renderShellWithChat(overrides: { chat?: ChatOverride; path?: string; inbox?: InboxPayload } = {}) {
  window.history.pushState({}, "", overrides.path ?? "/overview");

  vi.mocked(me).mockResolvedValue(fixtureMe);
  vi.mocked(overviewPayload).mockResolvedValue(fixtureOverview);
  vi.mocked(inboxPayload).mockResolvedValue(overrides.inbox ?? fixtureInbox);
  vi.mocked(updatesPayload).mockResolvedValue(fixtureUpdates);
  // Default: still running, so a test that only cares about the "queue"
  // live frame (not the HTTP poller) is not raced by an immediate resolve.
  // Tests exercising the poller itself override this per-call.
  vi.mocked(queueStatus).mockResolvedValue({ id: "", path: "", status: "queued", body: {}, result: null, ts: "" });
  vi.mocked(runAction).mockResolvedValue({});
  // This user has already been through the first-run wizard, so `Shell`'s gate
  // leaves them where the test put them.
  vi.mocked(onboardingState).mockResolvedValue({
    path: "tour", step: "", completed: true, skipped: false, show: false, tenant_ready: true, site_named: true, networks: [], next: null,
  });
  vi.mocked(saveOnboarding).mockResolvedValue({
    path: "tour", step: "", completed: true, skipped: false, show: false, tenant_ready: true, site_named: true, networks: [], next: null,
  });

  const queue = Array.isArray(overrides.chat) ? [...overrides.chat] : overrides.chat ? [overrides.chat] : [];
  const calls: ChatRequest[] = [];
  vi.mocked(chat).mockImplementation(async (body: ChatRequest) => {
    calls.push(body);
    const next = queue.length > 1 ? queue.shift() : queue[0];
    if (!next) throw new Error("chat.test.tsx: no chat fixture queued for this call");
    return next;
  });

  const listeners = new Map<string, Set<(event: LiveEvent) => void>>();
  vi.mocked(useLiveFeed).mockReturnValue({
    status: "open",
    last: null,
    dropped: 0,
    on: (type: string, fn: (event: LiveEvent) => void) => {
      let set = listeners.get(type);
      if (!set) {
        set = new Set();
        listeners.set(type, set);
      }
      set.add(fn);
      return () => set?.delete(fn);
    },
  });

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  const utils = render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <BrowserRouter>
          <Routes>
            <Route element={<Shell />}>
              <Route path="/overview" element={<div>Overview placeholder</div>} />
              {/* The alerts list inside the same shell, so a hand-off from a
                  row can be followed all the way into the chat panel. */}
              <Route path="/inbox" element={<Inbox />} />
              {/* And a Help article in the same shell, so "Ask Vault about
                  this" can be followed into the panel as well. */}
              <Route path="/help/:slug" element={<Help />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );

  return {
    ...utils,
    /** The most recent `ChatRequest` body posted to `path` — there is only
     *  one chat route, so `path` is asserted rather than dispatched on. */
    posted(path: string): ChatRequest {
      expect(path).toBe("/api/v1/chat");
      const last = calls[calls.length - 1];
      if (!last) throw new Error("chat.test.tsx: chat() was never called");
      return last;
    },
    /** Simulates a live-feed frame arriving on the socket the panel's
     *  `useChat` subscribed to via `live.on(type, fn)`. */
    emit(event: LiveEvent) {
      for (const fn of listeners.get(event.type) ?? []) fn(event);
    },
  };
}

async function openChat() {
  await screen.findByText("Overview placeholder");
  await userEvent.click(screen.getByRole("button", { name: /toggle vault chat/i }));
}

beforeEach(() => {
  document.documentElement.removeAttribute("data-theme");
});

afterEach(() => {
  vi.clearAllMocks();
  // `Shell` persists the chat popover's open state per tenant, so a test that
  // left it open would make the next test's click on the floating button
  // *close* the popover instead of opening it.
  window.localStorage.clear();
});

describe("Vault chat panel", () => {
  it("renders the verdict, bullets and the Next offer", async () => {
    renderShellWithChat({ chat: chatAnswerFixture });
    await openChat();

    await userEvent.type(screen.getByRole("textbox", { name: /ask vault/i }), "how are things?{Enter}");

    expect(await screen.findByRole("heading", { name: chatAnswerFixture.verdict })).toBeInTheDocument();
    expect(screen.getAllByTestId("chat-bullet")).toHaveLength(chatAnswerFixture.bullets.length);
    // `*yes*` renders as `<em>yes</em>` (emphasis-only inline markdown), so
    // the asserted text drops the asterisks the fixture's label carries.
    expect(screen.getByTestId("chat-next")).toHaveTextContent("Say yes and I will run a scan.");
  });

  // F17's cousin bug in the chat panel: replies land in the DOM but the
  // panel keeps whatever scroll position it already had, so a reader sees
  // the previous message (or, on a blank transcript, nothing at all) until
  // they scroll down by hand. jsdom lays out nothing, so `scrollHeight`/
  // `clientHeight` are stubbed directly on the transcript element — the
  // panel reads them the same way a real browser would once content grows
  // past the viewport.
  it("scrolls the transcript to the newest reply as it lands (F9)", async () => {
    renderShellWithChat({ chat: chatAnswerFixture });
    await openChat();

    const body = document.querySelector(".g-chat-body") as HTMLDivElement;
    Object.defineProperty(body, "scrollHeight", { value: 900, configurable: true });
    Object.defineProperty(body, "clientHeight", { value: 100, configurable: true });

    await userEvent.type(screen.getByRole("textbox", { name: /ask vault/i }), "how are things?{Enter}");
    await screen.findByRole("heading", { name: chatAnswerFixture.verdict });

    expect(body.scrollTop).toBe(900);
  });

  it("leaves the reader's own scroll position alone once they have scrolled away from the bottom (F9)", async () => {
    renderShellWithChat({ chat: chatAnswerFixture });
    await openChat();

    const body = document.querySelector(".g-chat-body") as HTMLDivElement;
    Object.defineProperty(body, "scrollHeight", { value: 900, configurable: true });
    Object.defineProperty(body, "clientHeight", { value: 100, configurable: true });

    // The reader deliberately scrolls up, away from the bottom, before
    // asking anything.
    body.scrollTop = 50;
    body.dispatchEvent(new Event("scroll"));

    await userEvent.type(screen.getByRole("textbox", { name: /ask vault/i }), "how are things?{Enter}");
    await screen.findByRole("heading", { name: chatAnswerFixture.verdict });

    expect(body.scrollTop).toBe(50);
  });

  it("asks before a tier-3 action and confirms with the token", async () => {
    const { posted } = renderShellWithChat({ chat: [chatProposalFixture, chatConfirmedFixture] });
    await openChat();

    await userEvent.type(screen.getByRole("textbox", { name: /ask vault/i }), "fix it{Enter}");
    expect(await screen.findByTestId("proposal")).toHaveTextContent(/reboot safehome/i);

    await userEvent.click(screen.getByRole("button", { name: /yes, do it/i }));
    expect(posted("/api/v1/chat").confirm_token).toBe(chatProposalFixture.proposal?.token);
  });

  it("shows a rules-fallback answer's verdict and Next line exactly once", async () => {
    // The rules rung answers with no bullets, and the hub returns the raw
    // `text` alongside the verdict/next it already split out of it. The
    // panel renders verdict and Next as their own rows, so echoing the raw
    // text verbatim underneath printed both of them twice.
    renderShellWithChat({
      chat: {
        ...chatAnswerFixture,
        text: "Everything is up.\nNext: nothing needed right now. (data from 2026-09-09 18:30)",
        verdict: "Everything is up.",
        bullets: [],
        next: [{ label: "nothing needed right now. (data from 2026-09-09 18:30)", method: "", path: "", body: {} }],
        provider: "rules",
      },
    });
    await openChat();

    await userEvent.type(screen.getByRole("textbox", { name: /ask vault/i }), "status{Enter}");

    expect(await screen.findByRole("heading", { name: "Everything is up." })).toBeInTheDocument();
    const panel = screen.getByRole("dialog", { name: /vault chat/i });
    // The panel's first `.g-chat-msg-guardian` is the static greeting, so the
    // reply under test is the last one.
    const turns = panel.querySelectorAll(".g-chat-msg-guardian");
    const guardianTurn = turns[turns.length - 1] as HTMLElement;
    expect(within(guardianTurn).getAllByText(/Everything is up\./)).toHaveLength(1);
    expect(within(guardianTurn).getAllByText(/nothing needed right now/)).toHaveLength(1);
    expect(guardianTurn.querySelector(".g-chat-text")).toBeNull();
  });

  it("opens with the greeting, the five chips and a placeholder built from a real device IP", async () => {
    renderShellWithChat({ chat: chatAnswerFixture });
    await openChat();

    expect(screen.getByText(/^I am Vault\. Ask about your servers/)).toBeInTheDocument();
    // The reference's own five chips, in its order.
    for (const label of ["Everything up?", "New devices?", "Alerts?", "Certs?", "Internet?"]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
    // `chat_example_ip` off the overview payload, not a hard-coded address.
    expect(screen.getByRole("textbox", { name: /ask vault/i })).toHaveAttribute(
      "placeholder",
      "Ask Vault, e.g. what is 192.168.1.166?",
    );
  });

  it("sends a chip's full question, not its short label", async () => {
    renderShellWithChat({ chat: chatAnswerFixture });
    await openChat();

    await userEvent.click(screen.getByRole("button", { name: "New devices?" }));

    await waitFor(() => expect(vi.mocked(chat)).toHaveBeenCalled());
    expect(vi.mocked(chat).mock.calls[0][0].message).toBe(
      "Any new or unknown devices on the Wi-Fi? Should I worry?",
    );
  });

  it("puts a provenance line under every reply — tools when they ran, provider always", async () => {
    renderShellWithChat({ chat: [chatAnswerFixture, chatConfirmedFixture] });
    await openChat();

    await userEvent.type(screen.getByRole("textbox", { name: /ask vault/i }), "how are things?{Enter}");
    await screen.findByRole("heading", { name: chatAnswerFixture.verdict });
    // No tools ran on that turn, so the line is just the rung that answered.
    expect(screen.getByTestId("chat-prov")).toHaveTextContent("via groq/openai/gpt-oss-120b");

    await userEvent.type(screen.getByRole("textbox", { name: /ask vault/i }), "reboot it{Enter}");
    await waitFor(() => expect(screen.getAllByTestId("chat-prov")).toHaveLength(2));
    expect(screen.getAllByTestId("chat-prov")[1]).toHaveTextContent(
      "investigated with apply_fix · via groq/openai/gpt-oss-20b",
    );
  });

  it("never renders model HTML", async () => {
    renderShellWithChat({ chat: { ...chatAnswerFixture, text: "<img src=x onerror=alert(1)>", bullets: [] } });
    await openChat();

    await userEvent.type(screen.getByRole("textbox", { name: /ask vault/i }), "hi{Enter}");

    expect(await screen.findByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(document.querySelector("img[src='x']")).toBeNull();
  });

  it("tracks a queued tool through the live frame", async () => {
    const { emit } = renderShellWithChat({ chat: chatQueuedFixture });
    await openChat();

    await userEvent.type(screen.getByRole("textbox", { name: /ask vault/i }), "run a scan{Enter}");
    expect(await screen.findByTestId("tool-step")).toHaveTextContent(/queued/i);

    emit({ type: "queue", ts: "2026-09-10T00:00:00Z", data: { id: chatQueuedFixture.tool_steps[0].queue_id, status: "done" } });
    expect(await screen.findByTestId("tool-step")).toHaveTextContent(/done/i);
  });

  it("disables the composer while a turn is in flight", async () => {
    renderShellWithChat();
    await openChat();

    // A deferred promise, not the fixture-backed mock, so the assertion
    // between "sent" and "resolved" cannot race the mock's own microtask.
    let resolveTurn!: (value: ChatResponse) => void;
    vi.mocked(chat).mockReturnValue(new Promise<ChatResponse>((resolve) => (resolveTurn = resolve)));

    const input = screen.getByRole("textbox", { name: /ask vault/i });
    await userEvent.type(input, "hi{Enter}");
    expect(screen.getByRole("textbox", { name: /ask vault/i })).toBeDisabled();

    resolveTurn(chatAnswerFixture);
    expect(await screen.findByRole("heading", { name: chatAnswerFixture.verdict })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: /ask vault/i })).toBeEnabled();
  });

  it("surfaces a 429 as a friendly rate-limit notice", async () => {
    renderShellWithChat();
    await openChat();
    const { ApiError } = await import("../src/lib/api");
    vi.mocked(chat).mockRejectedValue(new ApiError(429, "slow down"));

    await userEvent.type(screen.getByRole("textbox", { name: /ask vault/i }), "hi{Enter}");
    expect(await screen.findByText(/catching up/i)).toBeInTheDocument();
  });

  it("closes on Escape and hands focus back to the floating button", async () => {
    renderShellWithChat();
    await openChat();
    expect(screen.getByLabelText("Vault chat")).toBeInTheDocument();

    await userEvent.keyboard("{Escape}");
    expect(screen.queryByLabelText("Vault chat")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /toggle vault chat/i })).toHaveFocus();
  });

  it("marks the floating button expanded while the popover is open", async () => {
    renderShellWithChat();
    await screen.findByText("Overview placeholder");
    const fab = screen.getByRole("button", { name: /toggle vault chat/i });
    expect(fab).toHaveAttribute("aria-expanded", "false");

    await userEvent.click(fab);
    expect(screen.getByRole("button", { name: /toggle vault chat/i })).toHaveAttribute("aria-expanded", "true");
  });

  it("resets the transcript and drops a pending confirm token on a tenant switch", async () => {
    renderShellWithChat({ chat: [chatAnswerFixture, chatProposalFixture] });
    await openChat();

    await userEvent.type(screen.getByRole("textbox", { name: /ask vault/i }), "how are things?{Enter}");
    await screen.findByRole("heading", { name: chatAnswerFixture.verdict });

    await userEvent.type(screen.getByRole("textbox", { name: /ask vault/i }), "fix it{Enter}");
    await screen.findByTestId("proposal");

    // The superadmin fixture belongs to two tenants, so the sidebar renders
    // the switcher. Switching triggers `setActiveTenant` -> `me` refetch ->
    // a fresh `active_tenant.id`, which remounts `ChatPanel` (see the `key`
    // on it in `Shell.tsx`).
    vi.mocked(me).mockResolvedValueOnce(fixtureMeT2);
    await userEvent.selectOptions(screen.getByLabelText(/organization/i), "t2");

    await waitFor(() => {
      expect(screen.queryByTestId("proposal")).not.toBeInTheDocument();
    });
    expect(screen.queryByRole("heading", { name: chatAnswerFixture.verdict })).not.toBeInTheDocument();
    // A reset transcript is the greeting bubble on its own.
    const panel = screen.getByRole("dialog", { name: /vault chat/i });
    expect(panel.querySelectorAll(".g-chat-msg-guardian")).toHaveLength(1);
    expect(screen.getByText(/^I am Vault\./)).toBeInTheDocument();
  });

  it("links a bullet's name to its resolved entity, and renders an unresolved one as plain text", async () => {
    renderShellWithChat({ chat: chatEntityFixture });
    await openChat();

    await userEvent.type(screen.getByRole("textbox", { name: /ask vault/i }), "entities?{Enter}");
    await screen.findByRole("heading", { name: chatEntityFixture.verdict });

    const bullets = screen.getAllByTestId("chat-bullet");
    expect(bullets).toHaveLength(4);
    expect(within(bullets[0]).getByRole("link", { name: "Router" })).toHaveAttribute(
      "href",
      "/assets/devices/aa%3Abb%3Acc%3Add%3Aee%3Aff",
    );
    expect(within(bullets[1]).getByRole("link", { name: "laptop.local" })).toHaveAttribute(
      "href",
      "/assets/hosts/laptop.local",
    );
    expect(within(bullets[2]).getByRole("link", { name: "safehome" })).toHaveAttribute(
      "href",
      "/security/sites/safehome",
    );
    expect(within(bullets[3]).queryByRole("link")).not.toBeInTheDocument();
  });

  it("caps the rendered bullets at six even when the reply sends more", async () => {
    renderShellWithChat({ chat: chatManyBulletsFixture });
    await openChat();

    await userEvent.type(screen.getByRole("textbox", { name: /ask vault/i }), "status{Enter}");
    await screen.findByRole("heading", { name: chatManyBulletsFixture.verdict });

    expect(screen.getAllByTestId("chat-bullet")).toHaveLength(6);
  });

  it("polls GET /api/v1/queue/{id} for a queued tool's actual result", async () => {
    renderShellWithChat({ chat: chatQueuedFixture });
    // `renderShellWithChat` sets its own "still queued" default for
    // `queueStatus` (so tests that only care about the live-frame path
    // are not raced by an immediate resolve) — override it after, for this
    // test only.
    vi.mocked(queueStatus).mockResolvedValue({
      id: "q-scan-1",
      path: "/api/v1/run",
      status: "done",
      body: {},
      result: { text: "3 hosts scanned" },
      ts: "2026-09-10T00:00:00Z",
    });
    await openChat();

    await userEvent.type(screen.getByRole("textbox", { name: /ask vault/i }), "run a scan{Enter}");

    await waitFor(() => {
      expect(screen.getByTestId("tool-step")).toHaveTextContent(/3 hosts scanned/i);
    });
    expect(queueStatus).toHaveBeenCalledWith("q-scan-1");
  });

  it("never retries a request carrying confirm_token after a network error", async () => {
    renderShellWithChat({ chat: chatProposalFixture });
    await openChat();

    await userEvent.type(screen.getByRole("textbox", { name: /ask vault/i }), "fix it{Enter}");
    await screen.findByTestId("proposal");

    vi.mocked(chat).mockRejectedValue(new TypeError("Failed to fetch"));
    await userEvent.click(screen.getByRole("button", { name: /yes, do it/i }));

    expect(await screen.findByText(/could not be reached/i)).toBeInTheDocument();
    // One call for "fix it", one for the confirm — no silent retry of the
    // confirm_token call, which the hub would reject as already-consumed.
    expect(chat).toHaveBeenCalledTimes(2);
  });

  it("keeps the transcript aria-busy while a turn is in flight and refocuses the composer once it lands", async () => {
    renderShellWithChat();
    await openChat();

    let resolveTurn!: (value: ChatResponse) => void;
    vi.mocked(chat).mockReturnValue(new Promise<ChatResponse>((resolve) => (resolveTurn = resolve)));

    const input = screen.getByRole("textbox", { name: /ask vault/i });
    await userEvent.type(input, "hi{Enter}");
    expect(screen.getByRole("log")).toHaveAttribute("aria-busy", "true");

    resolveTurn(chatAnswerFixture);
    await screen.findByRole("heading", { name: chatAnswerFixture.verdict });

    expect(screen.getByRole("log")).toHaveAttribute("aria-busy", "false");
    expect(screen.getByRole("textbox", { name: /ask vault/i })).toHaveFocus();
  });

  it("Tab moves focus out of the composer without sending", async () => {
    renderShellWithChat();
    await openChat();

    const input = screen.getByRole("textbox", { name: /ask vault/i });
    await userEvent.type(input, "draft");
    await userEvent.tab();

    expect(chat).not.toHaveBeenCalled();
    expect(input).toHaveValue("draft");
    expect(screen.getByRole("button", { name: /send/i })).toHaveFocus();
  });

  it("dispatches a Next: offer's action through runAction and toasts the result", async () => {
    renderShellWithChat({ chat: chatAnswerFixture });
    await openChat();

    await userEvent.type(screen.getByRole("textbox", { name: /ask vault/i }), "how are things?{Enter}");
    await screen.findByRole("heading", { name: chatAnswerFixture.verdict });

    await userEvent.click(screen.getByRole("button", { name: /do it/i }));

    expect(runAction).toHaveBeenCalledWith(chatAnswerFixture.next[0]);
    expect(await screen.findByText("Done")).toBeInTheDocument();
  });

  // ---------------------------------------------------------------- hand-off

  const silentRowInbox: InboxPayload = {
    counts: { incidents: 0, alerts: 1, recs: 0, unread: 1, needs: 0 },
    rows: [{
      id: "sensor:silent", view: "alerts", sev: "CRIT",
      title: "No data from the sensor for 39 min — Vault cannot see this network right now.",
      entity: "", why: "", what_changed: "", status: "open",
      first_seen: "", last_seen: "", count: 1,
      actions: [{ kind: "ack", label: "Ack", method: "POST", path: "/api/v1/alerts/sensor:silent/ack", body: {}, tier: 2 }],
      children: [], ticket_key: "", confidence: null, network: null,
      remedy: {
        kind: "human", action: "", host: "", arg: "", tier: 0, tool: "", min_role: "",
        can_run_now: false,
        summary: "I cannot repair my own sensor from here.",
        need: "On safehome-sensor, run `systemctl status guardian-sensor`.",
        impact: "",
        confidence: 0, confidence_why: "", confidence_factors: [],
        opening: "No data from the sensor for 39 min — Vault cannot see this network right now.\n\nI cannot repair my own sensor from here — every fix I apply travels through it.\n\nWhat I need from you: On safehome-sensor, run `systemctl status guardian-sensor`.",
      },
      drift: null,
    }],
  };

  const silentHandoff: AlertHandoff = {
    key: "sensor:silent",
    remedy: silentRowInbox.rows[0].remedy!,
    opening: silentRowInbox.rows[0].remedy!.opening,
    proposal: null,
    thread_id: "thread-handoff",
  };

  const rebootHandoff: AlertHandoff = {
    key: "reboot:safehome",
    remedy: {
      kind: "approval", action: "reboot", host: "safehome", arg: "", tier: 3,
      tool: "apply_fix", min_role: "admin", can_run_now: false,
      summary: "I can repair this by running reboot host on safehome, but not without your go-ahead.",
      need: "Say yes to the exact action I propose and I will run it.",
      impact: "",
      confidence: 0, confidence_why: "", confidence_factors: [],
      opening: "safehome needs a reboot to finish its updates.\n\nI can repair this by running reboot host on safehome, but not without your go-ahead.\n\nWhat I need from you: Say yes to the exact action I propose and I will run it.",
    },
    opening: "safehome needs a reboot to finish its updates.\n\nI can repair this by running reboot host on safehome, but not without your go-ahead.\n\nWhat I need from you: Say yes to the exact action I propose and I will run it.",
    proposal: {
      token: "handoff-token-1",
      summary: "Sure, I will reboot on safehome. The host restarts. Not reversible. Reply *yes* and I will do it.",
      actions: [{ tool: "apply_fix", args: { host: "safehome", action: "reboot" }, tier: 3,
                  effect: "the host restarts and is unreachable for about a minute",
                  reversible: false, host: "safehome" }],
      expires: "2099-01-01T00:00:00+00:00",
    },
    thread_id: "thread-handoff-2",
  };

  it("opens with Vault already explaining an alert only a person can fix", async () => {
    vi.mocked(alertHandoff).mockResolvedValue(silentHandoff);
    renderShellWithChat({ path: "/inbox", inbox: silentRowInbox });

    await userEvent.click(await screen.findByRole("button", { name: /^Ack$/ }));

    // The panel opened by itself, with the whole story already in it: what is
    // wrong, what Vault cannot do, and the one command it needs run.
    const panel = await screen.findByRole("dialog", { name: /vault chat/i });
    expect(await within(panel).findByRole("heading", { name: /no data from the sensor for 39 min/i })).toBeInTheDocument();
    expect(within(panel).getByText(/every fix I apply travels through it/)).toBeInTheDocument();
    expect(within(panel).getByTestId("chat-next")).toHaveTextContent("systemctl status guardian-sensor");
    // Nothing was asked of a provider — the opening came from the hub.
    expect(chat).not.toHaveBeenCalled();
    // And it is not a proposal: there is nothing here Vault could run.
    expect(within(panel).queryByTestId("proposal")).toBeNull();
  });

  it("a hand-off that needs approval still takes a real yes, bound to that proposal", async () => {
    vi.mocked(alertHandoff).mockResolvedValue(rebootHandoff);
    const { posted } = renderShellWithChat({
      path: "/inbox",
      inbox: { ...silentRowInbox, rows: [{ ...silentRowInbox.rows[0], id: "reboot:safehome", remedy: rebootHandoff.remedy }] },
      chat: chatConfirmedFixture,
    });

    await userEvent.click(await screen.findByTestId("inbox-ask-guardian"));

    const panel = await screen.findByRole("dialog", { name: /vault chat/i });
    expect(await within(panel).findByTestId("proposal")).toHaveTextContent(/reboot on safehome/i);
    // Still nothing has run: a proposal is a question.
    expect(chat).not.toHaveBeenCalled();

    await userEvent.click(within(panel).getByRole("button", { name: /yes, do it/i }));
    expect(posted("/api/v1/chat").confirm_token).toBe("handoff-token-1");
    expect(posted("/api/v1/chat").thread).toBe("thread-handoff-2");
  });

  // The Help section's hand-off. Deliberately a different mechanism from the
  // alert one above: an article has no alert key, so `POST /chat/handoff` has
  // nothing to resolve — this is an ordinary chat turn, which is what makes the
  // answer about this site rather than a canned paragraph.
  it("asks Vault an article's question as a real chat turn, and asks again on a second click", async () => {
    const { posted } = renderShellWithChat({ path: "/help/security-score", chat: chatAnswerFixture });

    await userEvent.click(await screen.findByRole("button", { name: /ask vault about this/i }));

    const panel = await screen.findByRole("dialog", { name: /vault chat/i });
    await waitFor(() => expect(chat).toHaveBeenCalledTimes(1));
    expect(posted("/api/v1/chat").message).toMatch(/security score/i);
    // The question appears in the transcript as the reader's own, not as a
    // system aside.
    expect(within(panel).getByText(/why is my security score/i)).toBeInTheDocument();
    // …and Vault's reply is rendered like any other turn.
    expect(await within(panel).findByText(/mostly fine/i)).toBeInTheDocument();

    // A second click must land: the panel outlives article navigation, so a
    // dedupe keyed only on the question would silently swallow it.
    await userEvent.click(screen.getByRole("button", { name: /ask vault about this/i }));
    await waitFor(() => expect(chat).toHaveBeenCalledTimes(2));
  });
});
