import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../src/design";
import type { InboxView } from "../src/components/InboxList";
import { InboxList } from "../src/components/InboxList";
import Inbox from "../src/pages/Inbox";
import { ChatHandoffContext } from "../src/shell/chat/handoff";
import type { AlertHandoff, AlertRemedy, InboxCounts, InboxPayload, InboxRow, LiveEvent } from "../src/types";

// --- fake live feed (Inbox reconciles on the "alert" frame; no test here
// needs it to fire, but the page always subscribes) -------------------------

const { fakeLive } = vi.hoisted(() => {
  const feed = {
    status: "open" as const,
    last: null as LiveEvent | null,
    on: (_type: string, _fn: (event: LiveEvent) => void) => () => {},
  };
  return { fakeLive: feed };
});

vi.mock("../src/lib/live", () => ({
  useLiveFeed: () => fakeLive,
}));

// --- captured writes, so a test can assert on the posted body --------------

const { postedCalls } = vi.hoisted(() => {
  return { postedCalls: [] as { path: string; body: unknown }[] };
});

vi.mock("../src/lib/api", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/api")>("../src/lib/api");
  return {
    ...actual,
    inboxPayload: vi.fn(),
    queueStatus: vi.fn(),
    alertHandoff: vi.fn(),
    exportAlertsCsv: vi.fn((days: number, status: string) => {
      postedCalls.push({ path: "/api/v1/export/alerts.csv", body: { days, status } });
      return Promise.resolve();
    }),
    alertBulk: vi.fn((keys: string[], op: string, hours?: number) => {
      postedCalls.push({
        path: "/api/v1/alerts/bulk",
        body: hours ? { keys, op, hours } : { keys, op },
      });
      return Promise.resolve({});
    }),
    // Every row/hero action is dispatched through this one generic
    // function — its `method`/`path`/`body`, exactly as the hub described
    // them on the `RowAction`. Recorded here the same way `alertOp` used to
    // be, so a test can assert the *exact* path a custom action was sent to.
    runAction: vi.fn((action: { method: string; path: string; body: unknown }) => {
      postedCalls.push({ path: action.path, body: action.body });
      return Promise.resolve({});
    }),
  };
});

// F6: row actions are gated on the caller's role (`InboxRowView`'s `gate()`
// call), so unlike before, this page now actually reads `me()` to render
// them enabled. An admin fixture keeps every test below exercising the same
// "role is sufficient" path it always meant to.
vi.mock("../src/api", async () => {
  const actual = await vi.importActual<typeof import("../src/api")>("../src/api");
  return {
    ...actual,
    me: vi.fn().mockResolvedValue({
      user: { username: "miro", display_name: "Miro", is_superadmin: false },
      tenants: [{ id: "t1", slug: "home", name: "Home", role: "admin" }],
      active_tenant: { id: "t1", slug: "home", name: "Home", role: "admin" },
      chat_providers: [],
    }),
  };
});

import { alertHandoff, inboxPayload } from "../src/lib/api";

function row(overrides: Partial<InboxRow>): InboxRow {
  return {
    id: "row",
    view: "alert",
    sev: "WARN",
    title: "Untitled",
    entity: "",
    why: "",
    what_changed: "",
    status: "open",
    first_seen: "2026-09-09T10:00:00-04:00",
    last_seen: "2026-09-09T18:00:00-04:00",
    count: 1,
    actions: [],
    children: [],
    ticket_key: "",
    confidence: null,
    network: null,
    remedy: null,
    drift: null,
    ...overrides,
  };
}

const EMPTY_PAYLOAD: InboxPayload = {
  counts: { incidents: 0, alerts: 0, recs: 0, unread: 0, needs: 0 },
  rows: [],
};

const viewerInboxFixture: InboxPayload = {
  counts: { incidents: 0, alerts: 1, recs: 0, unread: 1, needs: 0 },
  rows: [
    row({ id: "alert:1", title: "Repeated failed logins", entity: "host:vps1", why: "12 failed SSH logins", actions: [] }),
  ],
};

const adminInboxFixture: InboxPayload = {
  counts: { incidents: 0, alerts: 4, recs: 0, unread: 4, needs: 0 },
  rows: [
    row({ id: "alert:1", title: "Alert one", actions: [{ kind: "ack", label: "Ack", method: "POST", path: "/api/v1/alerts/alert:1/ack", body: {}, tier: 1 }] }),
    row({ id: "alert:2", title: "Alert two", actions: [{ kind: "ack", label: "Ack", method: "POST", path: "/api/v1/alerts/alert:2/ack", body: {}, tier: 1 }] }),
    row({ id: "alert:3", title: "Alert three", actions: [{ kind: "ack", label: "Ack", method: "POST", path: "/api/v1/alerts/alert:3/ack", body: {}, tier: 1 }] }),
    row({ id: "alert:4", title: "Alert four", actions: [{ kind: "ack", label: "Ack", method: "POST", path: "/api/v1/alerts/alert:4/ack", body: {}, tier: 1 }] }),
  ],
};

// A row whose only action is NOT the `/api/v1/alerts/{id}/{kind}` shape a
// hand-built dispatcher would assume — "fix" always posts to `/api/v1/fix`
// with a `{host, action}` body. Proves the dispatcher sends `action.path` /
// `action.body` verbatim rather than rebuilding a path from `row.id` +
// `action.kind`.
const fixActionFixture: InboxPayload = {
  counts: { incidents: 0, alerts: 1, recs: 0, unread: 1, needs: 0 },
  rows: [
    row({
      id: "alert:5",
      title: "Alert five",
      actions: [
        {
          kind: "fix",
          label: "Fix",
          method: "POST",
          path: "/api/v1/fix",
          body: { host: "vps1", action: "apt_upgrade" },
          tier: 3,
        },
      ],
    }),
  ],
};

function remedy(overrides: Partial<AlertRemedy>): AlertRemedy {
  return {
    kind: "human", action: "", host: "", arg: "", tier: 0, tool: "", min_role: "",
    can_run_now: false, summary: "", need: "", impact: "", opening: "",
    confidence: 0, confidence_why: "", confidence_factors: [], ...overrides,
  };
}

const ACK: RowActionLike = { kind: "ack", label: "Ack", method: "POST", path: "/api/v1/alerts/alert:9/ack", body: {}, tier: 2 };
type RowActionLike = InboxRow["actions"][number];

/** An alert Vault can repair itself: the hub sent both the remedy and the
 *  `fix` action that runs it. */
const autoRemedyFixture: InboxPayload = {
  counts: { incidents: 0, alerts: 1, recs: 0, unread: 1, needs: 0 },
  rows: [
    row({
      id: "sec:attacker:demo:1.2.3.4",
      title: "demo: 900 failed SSH logins from 1.2.3.4",
      actions: [
        { ...ACK, path: "/api/v1/alerts/sec:attacker:demo:1.2.3.4/ack" },
        { kind: "fix", label: "Fix", method: "POST", path: "/api/v1/fix",
          body: { host: "demo", action: "block_ip", arg: "1.2.3.4" }, tier: 2 },
      ],
      remedy: remedy({
        kind: "auto", action: "block_ip", host: "demo", arg: "1.2.3.4", tier: 2,
        tool: "apply_fix", min_role: "admin", can_run_now: true,
        summary: "I can fix this myself: block attacker IP in the firewall on demo.",
      }),
    }),
  ],
};

/** The alert Vault provably cannot repair — its own sensor. */
const sensorSilentFixture: InboxPayload = {
  counts: { incidents: 0, alerts: 1, recs: 0, unread: 1, needs: 0 },
  rows: [
    row({
      id: "sensor:silent",
      sev: "CRIT",
      title: "No data from the sensor for 39 min — Vault cannot see this network right now.",
      actions: [{ ...ACK, path: "/api/v1/alerts/sensor:silent/ack" }],
      remedy: remedy({
        kind: "human",
        summary: "I cannot repair my own sensor from here.",
        need: "On safehome-sensor, run `systemctl status guardian-sensor`.",
        impact: "",
        opening: "No data from the sensor for 39 min.\n\nI cannot repair my own sensor from here.\n\nWhat I need from you: On safehome-sensor, run `systemctl status guardian-sensor`.",
      }),
    }),
  ],
};

const handoffFixture: AlertHandoff = {
  key: "sensor:silent",
  remedy: sensorSilentFixture.rows[0].remedy as AlertRemedy,
  opening: (sensorSilentFixture.rows[0].remedy as AlertRemedy).opening,
  proposal: null,
  thread_id: "thread-handoff",
};

const incidentFixture: InboxPayload = {
  counts: { incidents: 1, alerts: 0, recs: 0, unread: 1, needs: 0 },
  rows: [
    row({
      id: "inc:1",
      view: "incident",
      title: "Brute force campaign against vps1",
      children: [row({ id: "alert:child-1", title: "12 failed SSH logins from 1.2.3.4" })],
    }),
  ],
};

type Overrides = {
  inbox?: { incidents?: InboxPayload; alerts?: InboxPayload; recs?: InboxPayload };
  /** Which of the three views to land on. The page reads
   *  `?filter=alerts|incidents|recs` from the router, so a test about a
   *  recommendation has to start on that view. */
  filter?: "alerts" | "incidents" | "recs";
};

function renderPage(element: ReactElement, overrides: Overrides = {}) {
  postedCalls.length = 0;
  const openHandoff = vi.fn();
  vi.mocked(alertHandoff).mockResolvedValue(handoffFixture);
  vi.mocked(inboxPayload).mockImplementation((view = "alerts") => {
    const byView = overrides.inbox ?? {};
    if (view === "incidents") return Promise.resolve(byView.incidents ?? EMPTY_PAYLOAD);
    if (view === "recs") return Promise.resolve(byView.recs ?? EMPTY_PAYLOAD);
    return Promise.resolve(byView.alerts ?? EMPTY_PAYLOAD);
  });

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // Inbox's three views are addressed as `?filter=alerts|incidents|recs`
  // (the sidebar's dropdown links straight at them), so the page now reads the
  // router — it needs one in the tree.
  render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <MemoryRouter initialEntries={[`/inbox?filter=${overrides.filter ?? "alerts"}`]}>
          {/* `ChatHandoffContext` now carries two entry points: the alert
              hand-off this suite exercises, and Help's "Ask Vault about
              this", which never reaches Inbox. */}
          <ChatHandoffContext.Provider value={{ openAlert: openHandoff, askGuardian: () => {} }}>
            {element}
          </ChatHandoffContext.Provider>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );

  return {
    openHandoff,
    posted: (path: string) => {
      for (let i = postedCalls.length - 1; i >= 0; i -= 1) {
        if (postedCalls[i].path === path) return postedCalls[i].body as Record<string, unknown> & { keys?: string[] };
      }
      return undefined;
    },
  };
}

/* A recommendation Vault cannot act on itself.
 *
 * The hub enqueues a fix only when the recommendation carries a runnable
 * action (a host and an action name). Otherwise it answers
 * `status: "needs-you"` and nothing is dispatched. The screen used to say
 * "Recommendation applied" to that, which is the worst answer available: the
 * owner walks away believing a CRIT was handled while nothing ran and nothing
 * is queued.
 *
 * It happened on the live hub. A failing system DNS resolver and an exposed
 * VNC port were both accepted, both recorded `needs-you`, and both reported as
 * applied, with no row on the Activity page to contradict it.
 */
describe("accepting a recommendation Vault cannot run itself", () => {
  const recRow = row({
    id: "rec:dns:system",
    view: "rec",
    sev: "CRIT",
    title: "System DNS resolver is failing (public resolvers work)",
    actions: [
      { kind: "yes", label: "Yes, do it", method: "POST", path: "/api/v1/recs/dns:system", body: { decision: "yes" }, tier: 2 },
      { kind: "no", label: "No thanks", method: "POST", path: "/api/v1/recs/dns:system", body: { decision: "no" }, tier: 2 },
    ],
  });

  it("says it needs the owner and hands off to chat, instead of claiming it applied", async () => {
    // `mockImplementationOnce`, not `mockResolvedValueOnce`: the shared mock
    // also records into `postedCalls`, and replacing its resolved value would
    // drop that recording for whichever test consumed the "once" next.
    const { runAction } = await import("../src/lib/api");
    vi.mocked(runAction).mockImplementationOnce((action) => {
      postedCalls.push({ path: action.path, body: action.body });
      return Promise.resolve({ status: "needs-you", queue_id: null });
    });

    renderPage(<Inbox />, {
      filter: "recs",
      inbox: { recs: { counts: { incidents: 0, alerts: 0, recs: 1, unread: 1, needs: 0 }, rows: [recRow] } },
    });

    await userEvent.click(await screen.findByRole("button", { name: /^yes$/i }));

    await waitFor(() => {
      expect(screen.getByText(/this one needs you/i)).toBeInTheDocument();
    });
    expect(screen.queryByText(/recommendation applied/i)).not.toBeInTheDocument();
    // and the hand-off actually fired, so Vault can say what it needs
    const { alertHandoff } = await import("../src/lib/api");
    await waitFor(() => expect(vi.mocked(alertHandoff)).toHaveBeenCalled());
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Inbox", () => {
  it("renders only the actions the hub returned", async () => {
    renderPage(<Inbox />, { inbox: { alerts: viewerInboxFixture } });

    expect(await screen.findByText(viewerInboxFixture.rows[0].title)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Ack$/ })).toBeNull();
  });

  it("j/k moves the cursor and Shift+A bulk-acks the selection", async () => {
    const { posted } = renderPage(<Inbox />, { inbox: { alerts: adminInboxFixture } });
    await screen.findByText("Alert one");

    await userEvent.keyboard("jxjx{Shift>}A{/Shift}");

    expect(posted("/api/v1/alerts/bulk")).toMatchObject({ op: "ack", keys: expect.any(Array) });
    expect(posted("/api/v1/alerts/bulk")?.keys).toHaveLength(2);
  });

  it("sends a row action to its own path/method/body, not a rebuilt alert path", async () => {
    const { posted } = renderPage(<Inbox />, { inbox: { alerts: fixActionFixture } });

    await userEvent.click(await screen.findByRole("button", { name: /^Fix$/ }));

    expect(posted("/api/v1/fix")).toEqual({ host: "vps1", action: "apt_upgrade" });
    // Never dispatched as if it were an alert op keyed on the row id + kind.
    expect(posted("/api/v1/alerts/alert:5/fix")).toBeUndefined();
  });

  it("shows how sure Vault is, and stays silent when the sensor scored nothing", async () => {
    const fixture: InboxPayload = {
      counts: { incidents: 0, alerts: 3, recs: 0, unread: 3, needs: 0 },
      rows: [
        row({ id: "alert:sure", title: "Gateway MAC changed", confidence: 95 }),
        row({ id: "alert:hedge", title: "Domain looks generated", confidence: 40 }),
        row({ id: "alert:none", title: "No score at all", confidence: null }),
      ],
    };
    renderPage(<Inbox />, { inbox: { alerts: fixture } });

    const chips = await screen.findAllByTestId("inbox-confidence");
    // Two chips for three rows: an unscored alert must not be shown as 0% sure.
    expect(chips).toHaveLength(2);
    expect(chips[0]).toHaveTextContent("High evidence");
    expect(chips[0].className).toContain("g-tone-good");
    // A weak signal is toned down, not dressed up as another alarm colour.
    expect(chips[1]).toHaveTextContent("Limited evidence");
    expect(chips[1].className).toContain("g-tone-warn");
  });

  it("expands an incident to its child alerts", async () => {
    renderPage(<Inbox />, { inbox: { incidents: incidentFixture } });

    await userEvent.click(await screen.findByRole("tab", { name: /incidents/i }));
    await userEvent.click(await screen.findByText(incidentFixture.rows[0].title));

    expect(screen.getByText(incidentFixture.rows[0].children[0].title)).toBeInTheDocument();
  });

  it("prints what a repair will cost, under its own label", async () => {
    // Vault asks permission to change a running network. The cost is the
    // sentence the owner is actually weighing, so it has to be findable at a
    // glance rather than run on to the end of the explanation -- hence its own
    // node and its own label.
    const fixture = structuredClone(autoRemedyFixture);
    fixture.rows[0].remedy = remedy({
      kind: "auto", action: "restart_service", host: "web-01",
      summary: "I can fix this myself: restart service on web-01.",
      impact: "Drops live connections to that service while it restarts: a few seconds for most.",
    });
    renderPage(<Inbox />, { inbox: { alerts: fixture } });

    const cost = await screen.findByTestId("inbox-remedy-cost");
    expect(cost).toHaveTextContent("What it costs");
    expect(cost).toHaveTextContent("Drops live connections");
  });

  it("shows how sure Guardian is as a sentence, and its working only once opened", async () => {
    // The page-level half of `inboxRowView.test.tsx`'s confidence block: that
    // file pins what `isExpanded` renders, this one pins that the row's own
    // control actually drives it. The expanded state lives two components up
    // (`Inbox`'s `expanded` set, threaded through `InboxList`), which used to
    // be reachable only from an incident's title button -- a plain alert had no
    // expandable region at all, so a control here that nothing wired would
    // render and do nothing when clicked.
    const fixture = structuredClone(autoRemedyFixture);
    fixture.rows[0].remedy = remedy({
      kind: "auto", action: "security_updates", host: "clinic-nas",
      summary: "I can fix this myself: install security updates on clinic-nas.",
      confidence: 78,
      confidence_why: "Guardian has done this here 4 times, each confirmed afterwards.",
      confidence_factors: [
        { name: "host_history", question: "Has Guardian run install security updates on clinic-nas successfully before?", answer: "yes, 4 times", weight: 30 },
        { name: "undone", question: "Has a person reopened sec:secupd:clinic-nas after Guardian called it resolved?", answer: "never reopened by a person", weight: 0 },
      ],
    });
    renderPage(<Inbox />, { inbox: { alerts: fixture } });

    expect(await screen.findByTestId("inbox-confidence-why")).toHaveTextContent(
      "Guardian has done this here 4 times",
    );
    expect(screen.queryByTestId("inbox-score")).toBeNull();

    await userEvent.click(screen.getByTestId("inbox-score-toggle"));

    expect(screen.getByTestId("inbox-score-value")).toHaveTextContent("78 / 100");
    // Including the factor that found nothing: the reason for showing all six
    // is that the absent ones are informative.
    expect(screen.getByText("never reopened by a person")).toBeInTheDocument();
  });

  it("opens the same working from the keyboard as from the control", async () => {
    // `Enter` on the cursor row, which used to expand an incident and nothing
    // else (`children.length > 0` spelled out inline). Nothing pressed Enter
    // anywhere in this suite, so reverting `Inbox.tsx` to that predicate left
    // all 902 tests green while the keyboard silently stopped opening the
    // drawer the mouse still opened -- the exact disagreement `rowCanExpand`
    // exists to prevent, unguarded.
    const fixture = structuredClone(autoRemedyFixture);
    fixture.rows[0].remedy = remedy({
      kind: "auto", action: "security_updates", host: "clinic-nas",
      summary: "I can fix this myself: install security updates on clinic-nas.",
      confidence: 78,
      confidence_why: "Guardian has done this here 4 times, each confirmed afterwards.",
      confidence_factors: [
        { name: "host_history", question: "Has Guardian run install security updates on clinic-nas successfully before?", answer: "yes, 4 times", weight: 30 },
      ],
    });
    renderPage(<Inbox />, { inbox: { alerts: fixture } });

    await screen.findByTestId("inbox-confidence-why");
    expect(screen.queryByTestId("inbox-score")).toBeNull();

    await userEvent.keyboard("{Enter}");

    expect(screen.getByTestId("inbox-score-value")).toHaveTextContent("78 / 100");
  });

  it("gives a child alert of an incident one working control, and the incident none of its own", async () => {
    // Two things at once, because they are the same defect from two sides.
    //
    // `InboxList` used to hand every child row `isExpanded={false}` and a
    // no-op toggle -- correct while only an incident header could expand, and
    // a dead control the moment a child alert had a drawer of its own.
    //
    // And an incidents-view header carries a remedy (`payloads/inbox.py` sets
    // `remedy=lead_remedy`), so it has children AND factors. Both regions read
    // the one `expanded` flag, which is fine; two buttons over that one flag
    // is not, because each claims a different region through `aria-controls`
    // and either one opens both. The header's title is already its affordance,
    // so the header must render no score button at all.
    const fixture = structuredClone(incidentFixture);
    const scored = (score: number) =>
      remedy({
        kind: "approval", action: "block_ip", host: "vps1",
        summary: "I can repair this by blocking the attacker, but not with your access.",
        confidence: score,
        confidence_why: "Guardian has never repaired this host this way.",
        confidence_factors: [
          { name: "host_history", question: "Has Guardian run block attacker IP on vps1 successfully before?", answer: "never on this host", weight: -20 },
        ],
      });
    fixture.rows[0].remedy = scored(41);
    fixture.rows[0].children[0].remedy = scored(62);
    renderPage(<Inbox />, { inbox: { incidents: fixture } });

    await userEvent.click(await screen.findByRole("tab", { name: /incidents/i }));
    // Collapsed, the header offers no score control -- its title is the only
    // affordance it has ever needed.
    expect(screen.queryByTestId("inbox-score-toggle")).toBeNull();

    await userEvent.click(screen.getByText(fixture.rows[0].title));

    // Expanded: the child alert is showing, and exactly one score control
    // exists on the page -- the child's. The header's heading is plain text.
    expect(screen.getByText(fixture.rows[0].children[0].title)).toBeInTheDocument();
    const toggles = screen.getAllByTestId("inbox-score-toggle");
    expect(toggles).toHaveLength(1);
    // The header's own score is open (same flag), printed without a button.
    expect(screen.getAllByTestId("inbox-score-value").map((n) => n.textContent)).toEqual(["41 / 100"]);

    await userEvent.click(toggles[0]);

    expect(screen.getAllByTestId("inbox-score-value").map((n) => n.textContent)).toEqual([
      "41 / 100",
      "62 / 100",
    ]);
  });

  it("prints no cost line rather than a reassuring one when the repair has none", async () => {
    // A repair with no entry in `remedy.FIX_IMPACT` yields "". The absence has
    // to render as absence: an empty cost block under a "What it costs" label
    // would read as "this one is free", which is the opposite of what an empty
    // string means and the one way this feature could mislead.
    const fixture = structuredClone(autoRemedyFixture);
    fixture.rows[0].remedy = remedy({
      kind: "auto", action: "something_new", host: "web-01",
      summary: "I can fix this myself: something new on web-01.",
      impact: "",
    });
    renderPage(<Inbox />, { inbox: { alerts: fixture } });

    await screen.findByTestId("inbox-remedy");
    expect(screen.queryByTestId("inbox-remedy-cost")).toBeNull();
  });

  it("says what Vault can fix itself, and offers Fix rather than a chat detour", async () => {
    renderPage(<Inbox />, { inbox: { alerts: autoRemedyFixture } });

    const line = await screen.findByTestId("inbox-remedy");
    expect(line).toHaveTextContent("block attacker IP in the firewall on demo");
    expect(screen.getByRole("button", { name: /^Fix$/ })).toBeInTheDocument();
    // Vault can do this one itself — it must not push the owner into chat.
    expect(screen.queryByTestId("inbox-ask-guardian")).toBeNull();
  });

  it("acknowledging a fixable alert acks it and offers the fix, but never runs it", async () => {
    const { posted, openHandoff } = renderPage(<Inbox />, { inbox: { alerts: autoRemedyFixture } });

    await userEvent.click(await screen.findByRole("button", { name: /^Ack$/ }));

    expect(posted("/api/v1/alerts/sec:attacker:demo:1.2.3.4/ack")).toEqual({});
    // Ack is not a repair: no fix was queued, and no chat was opened.
    expect(posted("/api/v1/fix")).toBeUndefined();
    expect(openHandoff).not.toHaveBeenCalled();
    expect(await screen.findByText(/Press Fix and I will run it/)).toBeInTheDocument();
  });

  it("acknowledging an alert only a person can fix opens Vault with the explanation", async () => {
    const { posted, openHandoff } = renderPage(<Inbox />, { inbox: { alerts: sensorSilentFixture } });

    // The row already says it cannot be fixed from here, and offers the chat.
    expect(await screen.findByTestId("inbox-remedy")).toHaveTextContent("cannot repair my own sensor");
    expect(screen.getByTestId("inbox-ask-guardian")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Fix$/ })).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: /^Ack$/ }));

    // Acknowledged AND handed off, with Vault's own explanation.
    expect(posted("/api/v1/alerts/sensor:silent/ack")).toEqual({});
    await waitFor(() => expect(openHandoff).toHaveBeenCalledWith(handoffFixture));
    expect(alertHandoff).toHaveBeenCalledWith("sensor:silent", null);
  });

  it("Ask Vault hands off without touching the alert's state", async () => {
    const { posted, openHandoff } = renderPage(<Inbox />, { inbox: { alerts: sensorSilentFixture } });

    await userEvent.click(await screen.findByTestId("inbox-ask-guardian"));

    await waitFor(() => expect(openHandoff).toHaveBeenCalledWith(handoffFixture));
    expect(posted("/api/v1/alerts/sensor:silent/ack")).toBeUndefined();
  });

  it("a bulk ack never fans out into a chat hand-off per row", async () => {
    const { openHandoff } = renderPage(<Inbox />, { inbox: { alerts: sensorSilentFixture } });
    await screen.findByText(sensorSilentFixture.rows[0].title);

    await userEvent.keyboard("x{Shift>}A{/Shift}");

    expect(openHandoff).not.toHaveBeenCalled();
  });

  it("Mark all read acks every open alert on screen in one bulk call", async () => {
    const { posted } = renderPage(<Inbox />, { inbox: { alerts: adminInboxFixture } });
    await screen.findByText("Alert one");
    await userEvent.click(screen.getByRole("button", { name: /^mark all read$/i }));
    expect(posted("/api/v1/alerts/bulk")).toMatchObject({
      keys: ["alert:1", "alert:2", "alert:3", "alert:4"],
      op: "ack",
    });
  });

  it("Snooze asks WHEN before it hides anything, and sends the chosen hours", async () => {
    // Item 10b: the old button hid the selection for a silent 8 hours and
    // no reminder ever came. Choosing the time is the whole point, so the
    // click that used to snooze now only opens the dialog.
    const { posted } = renderPage(<Inbox />, { inbox: { alerts: adminInboxFixture } });
    await screen.findByText("Alert one");
    await userEvent.keyboard("x");
    await userEvent.click(screen.getByRole("button", { name: /^snooze$/i }));
    expect(posted("/api/v1/alerts/bulk")).toBeUndefined();

    await screen.findByText("When should Vault bring it back?");
    await userEvent.click(screen.getByRole("radio", { name: /Tomorrow/ }));
    await userEvent.click(screen.getByRole("button", { name: /Remind me tomorrow/i }));
    expect(posted("/api/v1/alerts/bulk")).toMatchObject({
      keys: ["alert:1"], op: "snooze", hours: 24,
    });
  });

  it("leads with a verdict, then the stat strip, then the page's own blocks", async () => {
    renderPage(<Inbox />, { inbox: { alerts: adminInboxFixture } });
    await screen.findByText("Alert one");
    const verdict = screen.getByRole("status", { name: "Verdict" });
    const strip = document.querySelector(".sk-stats");
    expect(strip).not.toBeNull();
    expect(verdict.compareDocumentPosition(strip!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(verdict).toHaveTextContent(/nothing needs you/i);
  });

  it("offers the alert log as PDF, Excel or CSV, over the filter on screen", async () => {
    // One export control everywhere: a client should never have to learn
    // that one tab gives a CSV and another gives something else.
    renderPage(<Inbox />, { inbox: { alerts: adminInboxFixture } });
    await userEvent.click(await screen.findByRole("button", { name: /export the alert log/i }));
    const menu = await screen.findByRole("menu", { name: /export the alert log as/i });
    expect(within(menu).getAllByRole("menuitem").map((i) => i.textContent)).toHaveLength(3);
    for (const fmt of [/PDF/, /Excel/, /CSV/]) {
      expect(within(menu).getByRole("menuitem", { name: fmt })).toBeInTheDocument();
    }
  });

  it("asks the hub for the format the person picked, carrying the filter", async () => {
    const calls: string[] = [];
    const realFetch = global.fetch;
    global.fetch = vi.fn(async (input: RequestInfo | URL) => {
      calls.push(String(input));
      return new Response(new Blob(["k,v"]), {
        status: 200, headers: { "content-disposition": 'attachment; filename="vault-alerts.csv"' },
      });
    }) as unknown as typeof fetch;
    try {
      renderPage(<Inbox />, { inbox: { alerts: adminInboxFixture } });
      await userEvent.click(await screen.findByRole("button", { name: /export the alert log/i }));
      await userEvent.click(await screen.findByRole("menuitem", { name: /CSV/ }));
      const hit = calls.find((c) => c.includes("/api/v1/export/alerts"));
      expect(hit).toBeDefined();
      expect(hit).toContain("fmt=csv");
      expect(hit).toContain("status=open");
    } finally {
      global.fetch = realFetch;
    }
  });
});


/** InboxList has a wide prop surface; these tests are about the tab strip,
 *  so everything else gets an inert default. */
function tabProps(counts: InboxCounts, view: InboxView) {
  return {
    view, counts, role: "admin" as const, status: "open" as const,
    search: "", rows: [], cursor: 0,
    selected: new Set<string>(), expanded: new Set<string>(), pendingIds: new Set<string>(),
    recStatus: {},
    onViewChange: () => {}, onStatusChange: () => {}, onSearchChange: () => {},
    onToggleExpand: () => {}, onToggleSelect: () => {}, onRunAction: () => {},
    onRecDecision: () => {}, onAskGuardian: () => {},
  };
}

describe("there is no Needs you tab", () => {
  it("does not repeat, as a fourth pile, what InboxTriage says directly above it", () => {
    // `InboxTriage` renders immediately above this list and answers exactly
    // "what is waiting on you". A tab underneath restating it was the same
    // question twice on one screen.
    render(
      <InboxList {...tabProps({ incidents: 2, alerts: 9, recs: 7, unread: 0, needs: 3 }, "alerts")} />,
    );
    const tabs = screen.getAllByRole("tab").map((t) => t.textContent ?? "");
    expect(tabs).toHaveLength(3);
    expect(tabs.some((t) => /needs you/i.test(t))).toBe(false);
  });
});

describe("an empty view still proves the tab worked", () => {
  // Every view empty is the ordinary case for a healthy customer. With one
  // shared sentence, switching tabs changed nothing on screen — which reads
  // exactly like a tab strip that does not work. Each view names itself.
  const cases: [InboxView, RegExp][] = [
    ["incidents", /No open incidents/],
    ["alerts", /No open alerts/],
    ["recs", /No recommendations/],
  ];

  it.each(cases)("names the view when %s is empty", (view, copy) => {
    render(
      <InboxList
        {...tabProps({ incidents: 0, alerts: 0, recs: 0, unread: 0, needs: 0 }, view)}
      />,
    );
    expect(screen.getByText(copy)).toBeInTheDocument();
  });

  it("no two views share their empty sentence", () => {
    const seen = new Set<string>();
    for (const [view] of cases) {
      const { unmount } = render(
        <InboxList {...tabProps({ incidents: 0, alerts: 0, recs: 0, unread: 0, needs: 0 }, view)} />,
      );
      const title = document.querySelector(".g-empty-state-title")?.textContent ?? "";
      expect(title).not.toBe("");
      expect(seen.has(title)).toBe(false);
      seen.add(title);
      unmount();
    }
  });
});
