import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BrowserRouter, Route, Routes, useParams } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MeOut } from "../src/api";
import { ToastProvider } from "../src/design";
import type { InboxPayload, LiveEvent, OverviewPayload, UpdatesPayload } from "../src/types";
import Shell, { WALL_SPEAK_KEY } from "../src/shell/Shell";
import type { SearchResults } from "../src/lib/api";

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
    get: vi.fn().mockResolvedValue({view:"owner",density:"compact",show_ticker:true,theme:"system"}),
    post: vi.fn().mockImplementation((_path, body) => Promise.resolve(body)),
    overviewPayload: vi.fn(),
    inboxPayload: vi.fn(),
    updatesPayload: vi.fn(),
    search: vi.fn(),
    onboardingState: vi.fn(),
    saveOnboarding: vi.fn(),
    requestRun: vi.fn().mockResolvedValue({}),
    requestSpeedtest: vi.fn().mockResolvedValue({}),
  };
});

// A real `vi.fn()` behind `useLiveFeed` rather than the static
// `on: () => () => {}` this file used to ship with -- that no-op meant NO
// test here could ever exercise `Shell`'s `live.on("alert")` handler, which
// is exactly the path the wallboard's speaker fires through. `renderShell`
// gives each render its own `listeners` map (mirroring `chat.test.tsx`'s
// `renderShellWithChat`) and returns an `emit` a test can call to simulate a
// frame arriving on the socket.
vi.mock("../src/lib/live", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/live")>("../src/lib/live");
  return { ...actual, useLiveFeed: vi.fn() };
});

// Controllable stand-ins for the wallboard's speaker. Defaults (set in
// `renderShell`) are the happy path -- voice configured, context running,
// every utterance plays -- so tests that don't care about speech at all
// (most of this file) never have to think about it. A test that does cares
// overrides per-call with `mockResolvedValueOnce`/`mockReturnValueOnce`.
vi.mock("../src/lib/speech", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/speech")>("../src/lib/speech");
  return {
    ...actual,
    speak: vi.fn(),
    enableAudio: vi.fn(),
    audioReady: vi.fn(),
    stopSpeaking: vi.fn(),
    useDictation: vi.fn(),
  };
});

import { authLogout, me, setActiveTenant } from "../src/api";
import {
  ApiError, inboxPayload, onboardingState, overviewPayload, saveOnboarding, search, updatesPayload,
} from "../src/lib/api";
import { useLiveFeed } from "../src/lib/live";
import { audioReady, enableAudio, speak, stopSpeaking, useDictation } from "../src/lib/speech";
import type { OnboardingState } from "../src/types";
import { NO_HERO } from "./overviewHero";
import { setToolsMode } from "../src/lib/toolsMode";

/** The wizard gate's own fixture. `show: false` is the state of a user who has
 *  already been through it, which is what every test here but the redirect one
 *  needs. */
const SEEN_WIZARD: OnboardingState = {
  path: "tour", step: "", completed: true, skipped: false, show: false, tenant_ready: true, site_named: true, networks: [], next: null,
};

const fixtureMe: MeOut = {
  user: { username: "miro", display_name: "Miro", is_superadmin: false },
  tenants: [{ id: "t1", slug: "home", name: "Home", role: "admin" }],
  active_tenant: { id: "t1", slug: "home", name: "Home", role: "admin" },
  chat_providers: ["groq", "anthropic", "rules"],
};

const fixtureTwoTenants: MeOut = {
  ...fixtureMe,
  tenants: [
    { id: "t1", slug: "home", name: "Home", role: "admin" },
    { id: "t2", slug: "office", name: "Office", role: "viewer" },
  ],
};

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

const fixtureSearch: SearchResults = { results: [] };

type Overrides = {
  me?: MeOut;
  overview?: OverviewPayload;
  inbox?: InboxPayload;
  updates?: UpdatesPayload;
  search?: SearchResults;
  onboarding?: OnboardingState;
  /** Where the browser starts. Defaults to `/overview`. */
  at?: string;
};

function DeviceRoute() {
  const { mac } = useParams<{ mac: string }>();
  return <div>Device {mac}</div>;
}

function renderShell(overrides: Overrides = {}) {
  window.history.pushState({}, "", overrides.at ?? "/overview");

  vi.mocked(me).mockResolvedValue(overrides.me ?? fixtureMe);
  vi.mocked(overviewPayload).mockResolvedValue(overrides.overview ?? fixtureOverview);
  vi.mocked(inboxPayload).mockResolvedValue(overrides.inbox ?? fixtureInbox);
  vi.mocked(updatesPayload).mockResolvedValue(overrides.updates ?? fixtureUpdates);
  vi.mocked(search).mockResolvedValue(overrides.search ?? fixtureSearch);
  vi.mocked(onboardingState).mockResolvedValue(overrides.onboarding ?? SEEN_WIZARD);
  vi.mocked(saveOnboarding).mockResolvedValue(overrides.onboarding ?? SEEN_WIZARD);

  // Fresh per render, same as `chat.test.tsx`'s `renderShellWithChat`: a
  // Map rather than a module-level object, so a test never inherits a
  // listener a previous test's unmounted `Shell` registered.
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

  // The happy path: voice configured, the context running, every utterance
  // plays. Tests exercising the burst/failure cases override per-call with
  // `mockResolvedValueOnce`/`mockReturnValueOnce` after calling `renderShell`.
  vi.mocked(speak).mockResolvedValue(true);
  vi.mocked(enableAudio).mockReturnValue(true);
  vi.mocked(audioReady).mockReturnValue(true);
  vi.mocked(stopSpeaking).mockReturnValue(undefined);
  vi.mocked(useDictation).mockReturnValue({ recording: false, error: "", start: () => {}, stop: () => {} });

  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  const utils = render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/welcome" element={<div>Welcome placeholder</div>} />
            <Route element={<Shell />}>
              <Route path="/overview" element={<div>Overview placeholder</div>} />
              <Route path="/inbox" element={<div>Inbox placeholder</div>} />
              <Route path="/assets/devices/:mac" element={<DeviceRoute />} />
              <Route path="/security" element={<div>Security placeholder</div>} />
              <Route path="/updates" element={<div>Updates placeholder</div>} />
              <Route path="/dns" element={<div>DNS placeholder</div>} />
              <Route path="/reports" element={<div>Reports placeholder</div>} />
              <Route path="/settings" element={<div>Settings placeholder</div>} />
            </Route>
          </Routes>
        </BrowserRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );

  return {
    ...utils,
    /** Simulates a live-feed frame arriving on the socket `Shell` (or
     *  anything it renders, like `useChat`) subscribed to via
     *  `live.on(type, fn)`. Mirrors `chat.test.tsx`'s `emit`. */
    emit(event: LiveEvent) {
      for (const fn of listeners.get(event.type) ?? []) fn(event);
    },
    /** Swap what the next `/overview` poll answers, and make it happen now.
     *  The real trigger is the 10s `refetchInterval`; a test that waited for
     *  it would be measuring the clock, so this invalidates instead. */
    async rerenderWithOverview(next: OverviewPayload) {
      vi.mocked(overviewPayload).mockResolvedValue(next);
      await queryClient.invalidateQueries({ queryKey: ["overview"] });
    },
  };
}

beforeEach(() => {
  try {
    window.localStorage.clear();
  } catch {
    // not every jsdom environment needs this, but clearing never hurts
  }
  document.documentElement.removeAttribute("data-theme");
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Shell", () => {
  it("shows the unread badge, the scan age and switches theme", async () => {
    renderShell({
      overview: { ...fixtureOverview, age_s: 42 },
      inbox: { counts: { incidents: 0, alerts: 0, recs: 0, unread: 3, needs: 0 }, rows: [] },
    });

    // Two surfaces carry it now (task 74): the sidebar row and the mobile bar,
    // which is rendered unconditionally and hidden by CSS above 700px.
    expect(await screen.findAllByText("3")).toHaveLength(2);
    expect(screen.getByText(/42s ago/)).toBeInTheDocument();

    // Theme moved into the header's overflow menu (design proposal §3) --
    // nobody reaches for it twice a session.
    await userEvent.click(screen.getByRole("button", { name: /more actions/i }));
    await userEvent.click(screen.getByRole("menuitem", { name: /theme/i }));
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("opens the palette on meta+k and navigates on Enter", async () => {
    renderShell({
      search: {
        results: [
          { kind: "device", id: "aa:bb", title: "Kitchen TV", subtitle: "", href: "/assets/devices/aa:bb" },
        ],
      },
    });
    await screen.findByText("Overview placeholder");

    await userEvent.keyboard("{Meta>}k{/Meta}");
    await userEvent.type(screen.getByRole("combobox", { name: /search/i }), "kitchen");
    await screen.findByText("Kitchen TV");
    await userEvent.keyboard("{Enter}");

    // Navigates to the hub's own `href` verbatim (no client-side
    // encodeURIComponent re-build) — a MAC's colons are valid, unencoded
    // path characters.
    expect(window.location.pathname).toBe("/assets/devices/aa:bb");
    expect(await screen.findByText("Device aa:bb")).toBeInTheDocument();
  });

  it("shows a warn badge on Updates when a host needs a reboot, and crit on Security from open alerts", async () => {
    renderShell({
      overview: { ...fixtureOverview, alerts: { crit: 2, warn: 1, info: 0, open: 3, acked: 0, snoozed: 0 } },
      updates: {
        ...fixtureUpdates,
        hosts: [
          {
            checked: true,
            name: "vps1",
            os: "debian",
            kernel: "6.1",
            kernel_latest: "6.1",
            pending: 0,
            security_pending: 0,
            cve_fixable: 0,
            unattended: null,
            reboot_required: true,
            last_apt: "",
            packages: [],
            security_packages: [],
          },
        ],
      },
    });

    const sidebar = screen.getByLabelText("Main");
    const updatesLink = within(sidebar).getByRole("link", { name: /updates/i });
    expect(await within(updatesLink).findByText("!")).toBeInTheDocument();

    const securityLink = within(sidebar).getByRole("link", { name: /security/i });
    expect(await within(securityLink).findByText("2")).toBeInTheDocument();
  });

  it("shows a tenant switcher only when the user has more than one tenant, and posts on change", async () => {
    renderShell({ me: fixtureTwoTenants });
    const select = await screen.findByLabelText(/organization/i);
    await userEvent.selectOptions(select, "t2");
    expect(setActiveTenant).toHaveBeenCalledWith("t2");
  });

  it("hides the tenant switcher for a single-tenant user", async () => {
    renderShell();
    await screen.findByText("Overview placeholder");
    expect(screen.queryByLabelText(/organization/i)).not.toBeInTheDocument();
  });

  it("toggles the chat popover from the floating button, captioned with the provider ladder", async () => {
    renderShell();
    await screen.findByText("Overview placeholder");
    expect(screen.queryByLabelText("Vault chat")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /toggle vault chat/i }));
    expect(screen.getByLabelText("Vault chat")).toBeInTheDocument();
    // The reference's header caption: which providers are configured, in the
    // order the hub walks them. Replaces the old "Context: <tab>" row — the
    // tab still rides along as `ChatRequest.tab`, it just is not a visible row.
    expect(screen.getByText("via groq → anthropic → rules")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: /ask vault/i })).toBeEnabled();
  });

  it("collapses the sidebar and persists the choice", async () => {
    renderShell();
    await screen.findByText("Overview placeholder");

    const sidebar = screen.getByLabelText("Main");
    expect(within(sidebar).getByText("Overview")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /collapse sidebar/i }));
    expect(within(sidebar).queryByText("Overview")).not.toBeInTheDocument();
    expect(window.localStorage.getItem("guardian.sidebar")).toBe("1");
  });

  it("signs out through the top bar's overflow menu", async () => {
    renderShell();
    await screen.findByText("Overview placeholder");
    await userEvent.click(screen.getByRole("button", { name: /more actions/i }));
    await userEvent.click(screen.getByRole("menuitem", { name: /sign out/i }));
    expect(authLogout).toHaveBeenCalled();
    expect(window.location.pathname).toBe("/login");
  });

  it("still lists actions and shows no error toast when the palette's search 404s", async () => {
    renderShell();
    await screen.findByText("Overview placeholder");

    // A 404 (e.g. an old hub build without `GET /api/v1/search` yet) must
    // degrade to "just the fixed actions", never an unhandled rejection
    // (vitest fails the run on one of those on its own) and never a toast,
    // since a 404 here is not a fault the user caused.
    vi.mocked(search).mockRejectedValue(new ApiError(404, "not found"));

    await userEvent.keyboard("{Meta>}k{/Meta}");
    // "run" matches the fixed "Run scan" action's own label, so the action
    // list survives the query filter regardless of what `search()` does —
    // this isolates "does the 404 wipe out the action list" from the
    // unrelated behavior of the query also filtering actions by text.
    await userEvent.type(screen.getByRole("combobox", { name: /search/i }), "run");

    await waitFor(() => expect(search).toHaveBeenCalledWith("run"));
    // Give the rejected promise's `.catch` a turn to run before asserting
    // on its aftermath.
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(screen.getByText("Run scan")).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("returns focus to the search button when the palette closes", async () => {
    renderShell();
    await screen.findByText("Overview placeholder");

    const searchButton = screen.getByRole("button", { name: /search devices/i });
    searchButton.focus();
    await userEvent.click(searchButton);
    // The dialog's own focus-steal is a mount effect scheduled through
    // `requestAnimationFrame`, so it lands a tick after the click.
    await waitFor(() => expect(screen.getByRole("combobox", { name: /search/i })).toHaveFocus());

    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("combobox", { name: /search/i })).not.toBeInTheDocument();
    expect(searchButton).toHaveFocus();
  });

  it("traps Tab inside the open palette", async () => {
    renderShell();
    await screen.findByText("Overview placeholder");

    await userEvent.keyboard("{Meta>}k{/Meta}");
    const input = screen.getByRole("combobox", { name: /search/i });
    await waitFor(() => expect(input).toHaveFocus());

    await userEvent.keyboard("{Tab}");
    expect(input).toHaveFocus();

    await userEvent.keyboard("{Shift>}{Tab}{/Shift}");
    expect(input).toHaveFocus();
  });

  // The wizard gate is per USER now, not per tenant: the hub answers "has this
  // person been through it here?" and the shell obeys that answer. A tenant
  // with no scan is no longer the trigger, and a browser flag is no longer the
  // exemption.
  it("redirects a user who has not seen the wizard, whatever the tenant's own state", async () => {
    const established: OnboardingState = {
      path: "tour", step: "", completed: false, skipped: false, show: true, tenant_ready: true, site_named: true, networks: [], next: null,
    };
    // A fully-configured tenant — the case the old gate got wrong, because the
    // second person to join never saw the wizard at all.
    renderShell({ onboarding: established });
    expect(await screen.findByText("Welcome placeholder")).toBeInTheDocument();
  });

  it("does not redirect a user who already completed or skipped it", async () => {
    const freshOverview: OverviewPayload = { ...fixtureOverview, ts: null, sensor: null };
    // Even with no scan and no sensor: this person has seen the wizard, and a
    // tenant that lost its sensor must not trap them back in it.
    renderShell({ overview: freshOverview, onboarding: { ...SEEN_WIZARD, path: "setup", tenant_ready: false } });
    expect(await screen.findByText("Overview placeholder")).toBeInTheDocument();
    expect(screen.queryByText("Welcome placeholder")).not.toBeInTheDocument();
  });

  it("migrates a legacy per-tenant dismissal onto the hub instead of re-running the wizard", async () => {
    window.localStorage.setItem("guardian.onboarding.dismissed.t1", "1");
    const unseen: OnboardingState = {
      path: "setup", step: "", completed: false, skipped: false, show: true, tenant_ready: false, site_named: false, networks: [], next: { network_id: "", action: "declare_network" },
    };
    vi.mocked(saveOnboarding).mockResolvedValue({ ...unseen, skipped: true, show: false });

    renderShell({ onboarding: unseen });

    expect(await screen.findByText("Overview placeholder")).toBeInTheDocument();
    await waitFor(() => expect(saveOnboarding).toHaveBeenCalledWith({ skipped: true }));
    // …and the flag is gone, so a second user on this browser cannot inherit it.
    expect(window.localStorage.getItem("guardian.onboarding.dismissed.t1")).toBeNull();
    expect(screen.queryByText("Welcome placeholder")).not.toBeInTheDocument();
  });

  it("spares a superadmin the tour on every tenant they visit, but not a real setup", async () => {
    const superMe: MeOut = { ...fixtureMe, user: { ...fixtureMe.user, is_superadmin: true } };
    const tour: OnboardingState = {
      path: "tour", step: "", completed: false, skipped: false, show: true, tenant_ready: true, site_named: true, networks: [], next: null,
    };
    const first = renderShell({ me: superMe, onboarding: tour });
    expect(await screen.findByText("Overview placeholder")).toBeInTheDocument();
    first.unmount();

    window.history.pushState({}, "", "/overview");
    renderShell({ me: superMe, onboarding: { ...tour, path: "setup", tenant_ready: false } });
    expect(await screen.findByText("Welcome placeholder")).toBeInTheDocument();
  });
});

// Task 78: the shell behaviours the reference console has and Vault had
// ported as appearance only — the mode toggle moving the navigation, and ⛶
// opening a wallboard instead of calling the Fullscreen API.
describe("simple / advanced mode in the shell (task 78)", () => {
  it("defaults to advanced when nothing is stored, so an existing user keeps the whole nav", async () => {
    renderShell();
    await screen.findByText("Overview placeholder");
    // The header no longer carries a chip for this: the mode moved to
    // Settings -> Appearance, because it is a preference rather than
    // something happening now. What the default means is unchanged, and the
    // navigation is the observable proof of it.
    const nav = screen.getByLabelText("Main");
    expect(within(nav).getByRole("link", { name: "DNS filter" })).toBeInTheDocument();
  });

  it("honours an explicit simple choice, which is not the same as nothing stored", async () => {
    window.localStorage.setItem("guardian.topbar.advanced", "0");
    renderShell();
    await screen.findByText("Overview placeholder");
    const nav = screen.getByLabelText("Main");
    expect(within(nav).queryByRole("link", { name: "DNS filter" })).not.toBeInTheDocument();
  });

  it("sends you back to Overview when simple mode hides the tab you were on", async () => {
    renderShell({ at: "/dns" });
    expect(await screen.findByText("DNS placeholder")).toBeInTheDocument();

    // The mode is changed from Settings now, and `Shell` hears about it on the
    // window rather than through a prop. Changing it from anywhere must still
    // bounce a page the new mode no longer shows, which is the behaviour this
    // test exists for.
    setToolsMode(false);

    expect(await screen.findByText("Overview placeholder")).toBeInTheDocument();
    expect(window.location.pathname).toBe("/overview");
    expect(await screen.findByText("Overview placeholder")).toBeInTheDocument();
    // The row is gone from the sidebar too, not merely dimmed.
    const nav = screen.getByLabelText("Main");
    expect(within(nav).queryByRole("link", { name: "DNS filter" })).not.toBeInTheDocument();
    expect(window.localStorage.getItem("guardian.topbar.advanced")).toBe("0");

    // Back to everything: every row returns, and it does not navigate.
    setToolsMode(true);
    await screen.findByText("Overview placeholder");
    expect(window.location.pathname).toBe("/overview");
    expect(within(screen.getByLabelText("Main")).getByRole("link", { name: "DNS filter" })).toBeInTheDocument();
  });

  // F11: switching modes while already sitting on a hidden page bounces
  // (the test just above) — a COLD load straight into that same URL, already
  // in simple mode, used to render it normally instead. `renderShell` mounts
  // `Shell` fresh at `at`, exactly like opening a pasted link in a new tab.
  it("bounces a cold load of an advanced-only URL to Overview when already in simple mode", async () => {
    window.localStorage.setItem("guardian.topbar.advanced", "0");
    renderShell({ at: "/dns" });

    expect(await screen.findByText("Overview placeholder")).toBeInTheDocument();
    expect(window.location.pathname).toBe("/overview");
    expect(screen.queryByText("DNS placeholder")).not.toBeInTheDocument();
  });

  it("leaves a cold load of an advanced-only URL alone in advanced mode", async () => {
    renderShell({ at: "/dns" });
    expect(await screen.findByText("DNS placeholder")).toBeInTheDocument();
    expect(window.location.pathname).toBe("/dns");
  });

  it("stays put when the route survives the switch", async () => {
    renderShell({ at: "/inbox" });
    expect(await screen.findByText("Inbox placeholder")).toBeInTheDocument();
    setToolsMode(false);
    await screen.findByText("Inbox placeholder");
    expect(window.location.pathname).toBe("/inbox");
  });
});

describe("the wallboard (task 78)", () => {
  const busy: InboxPayload = {
    counts: { incidents: 1, alerts: 2, recs: 0, unread: 2, needs: 0 },
    rows: [
      {
        id: "a1", view: "alerts", sev: "CRIT", title: "status.example.org is down", entity: "status.example.org",
        why: "", what_changed: "", status: "open", first_seen: "", last_seen: "", count: 1,
        actions: [], children: [], ticket_key: "", confidence: null, network: null, remedy: null, drift: null,
      },
      {
        id: "a2", view: "alerts", sev: "INFO", title: "a note nobody needs on a wall", entity: "",
        why: "", what_changed: "", status: "open", first_seen: "", last_seen: "", count: 1,
        actions: [], children: [], ticket_key: "", confidence: null, network: null, remedy: null, drift: null,
      },
    ],
  };

  it("opens a big-figure view off the ⛶ button, not the Fullscreen API", async () => {
    const requestFullscreen = vi.fn();
    document.documentElement.requestFullscreen = requestFullscreen;
    renderShell({ overview: { ...fixtureOverview, score: 83, grade: "B" }, inbox: busy });
    await screen.findByText("Overview placeholder");

    await userEvent.click(screen.getByRole("button", { name: /more actions/i }));
    await userEvent.click(screen.getByRole("menuitem", { name: /open the wallboard/i }));

    const wall = await screen.findByRole("dialog", { name: "Wallboard" });
    expect(requestFullscreen).not.toHaveBeenCalled();
    // The figures a wall display is for…
    expect(within(wall).getByText("Security score")).toBeInTheDocument();
    expect(within(wall).getByText(/83/)).toBeInTheDocument();
    // …and the open alerts, with the merely-informational one left out.
    expect(within(wall).getByText("status.example.org is down")).toBeInTheDocument();
    expect(within(wall).queryByText("a note nobody needs on a wall")).not.toBeInTheDocument();
  });

  it("closes on Escape with no mouse, and hands focus back to the button that opened it", async () => {
    renderShell();
    await screen.findByText("Overview placeholder");
    // The wallboard now opens from the overflow menu, which returns focus to
    // its own "More" button the instant an item is picked -- so THAT button,
    // still on screen once the menu itself is gone, is the one the wallboard
    // must hand focus back to.
    const opener = screen.getByRole("button", { name: /more actions/i });
    await userEvent.click(opener);
    await userEvent.click(screen.getByRole("menuitem", { name: /open the wallboard/i }));

    const wall = await screen.findByRole("dialog", { name: "Wallboard" });
    // The close control takes focus on open, so Enter alone also gets you out.
    await waitFor(() => expect(within(wall).getByRole("button", { name: /close wallboard/i })).toHaveFocus());

    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Wallboard" })).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });

  it("says so plainly when there is nothing open", async () => {
    renderShell();
    await screen.findByText("Overview placeholder");
    await userEvent.click(screen.getByRole("button", { name: /more actions/i }));
    await userEvent.click(screen.getByRole("menuitem", { name: /open the wallboard/i }));
    const wall = await screen.findByRole("dialog", { name: "Wallboard" });
    expect(within(wall).getByText("All quiet.")).toBeInTheDocument();
  });
});

// The wallboard's speaker fires through `Shell`'s existing `live.on("alert")`
// handler (see `Wallboard.tsx`'s header comment), not a subscription of its
// own -- so these tests exercise that handler directly via `emit`, which
// `renderShell`'s capturing `useLiveFeed` mock makes possible for the first
// time in this file (see the comment above the `vi.mock("../src/lib/live")`
// call).
describe("the wallboard's speaker", () => {
  async function openWallboardWithSpeakerOn() {
    const shell = renderShell({ overview: { ...fixtureOverview, voice: true } });
    await screen.findByText("Overview placeholder");
    await userEvent.click(screen.getByRole("button", { name: /more actions/i }));
    await userEvent.click(screen.getByRole("menuitem", { name: /open the wallboard/i }));
    const wall = await screen.findByRole("dialog", { name: "Wallboard" });
    const speaker = within(wall).getByRole("button", { name: /speak new criticals/i });
    await userEvent.click(speaker);
    expect(speaker).toHaveAttribute("aria-pressed", "true");
    return { ...shell, wall, speaker };
  }

  const crit = (key: string, msg: string): LiveEvent => ({
    type: "alert",
    ts: "2026-09-22T12:00:00-04:00",
    data: { key, sev: "CRIT", msg },
  });

  it("speaks a new critical once the toggle is on and the wallboard is open", async () => {
    const { emit } = await openWallboardWithSpeakerOn();
    emit(crit("vps1:down", "vps1 unreachable"));
    await waitFor(() => expect(speak).toHaveBeenCalledWith("vps1 unreachable"));
  });

  it("speaks a repeated CRIT key only once -- the same seenCrit gate as the toast", async () => {
    const { emit } = await openWallboardWithSpeakerOn();
    const event = crit("vps1:down", "vps1 unreachable");
    emit(event);
    emit(event);
    await waitFor(() => expect(speak).toHaveBeenCalledTimes(1));
  });

  it("stays silent when the wallboard is closed, even with the toggle left on", async () => {
    const { emit, wall } = await openWallboardWithSpeakerOn();
    // Closing hands focus back to the "More" button and unmounts `Wallboard`,
    // but `wallSpeak` is `Shell` state -- it does not reset just because the
    // screen that offered the toggle went away.
    await userEvent.click(within(wall).getByRole("button", { name: /close wallboard/i }));
    expect(screen.queryByRole("dialog", { name: "Wallboard" })).not.toBeInTheDocument();
    // `speak()` is called synchronously inside the handler, gated on
    // `wallboardOpenRef.current` -- so if the gate holds, there is nothing
    // asynchronous to wait for: the call either happened in this tick or it
    // never will.
    emit(crit("vps1:down", "vps1 unreachable"));
    expect(speak).not.toHaveBeenCalled();
  });

  it("the burst case: a lost sentence with the channel alive leaves the toggle on; a dead channel turns it off", async () => {
    const { emit, speaker } = await openWallboardWithSpeakerOn();

    // First CRIT: speak() loses the channel to something else (a superseded
    // call, or a transient failure) but the context is still running. This
    // is `lib/speech.ts`'s documented false-that-isn't-dead -- the icon must
    // stay lit, because the next sentence will be heard.
    vi.mocked(speak).mockResolvedValueOnce(false);
    vi.mocked(audioReady).mockReturnValueOnce(true);
    emit(crit("a", "first critical"));
    await waitFor(() => expect(speak).toHaveBeenCalledTimes(1));
    expect(speaker).toHaveAttribute("aria-pressed", "true");

    // Second CRIT: speak() fails again, and this time the context is not
    // running -- the speaker really is dead, and the control must say so.
    vi.mocked(speak).mockResolvedValueOnce(false);
    vi.mocked(audioReady).mockReturnValueOnce(false);
    emit(crit("b", "second critical"));
    await waitFor(() => expect(speaker).toHaveAttribute("aria-pressed", "false"));
  });

  it("stays silent when the hub stops being able to speak, even with the toggle still on", async () => {
    // The key can be pulled from the hub at any time. The next `/overview`
    // poll drops `voice` to false, the speaker button disappears with the
    // rest of the voice controls -- and `wallSpeak` is still true, on a
    // control the customer can no longer see in order to turn it off. Without
    // the `voice` check in the alert handler the display goes on making round
    // trips that 503 for as long as it is left up.
    const { emit, rerenderWithOverview, wall } = await openWallboardWithSpeakerOn();
    expect(within(wall).queryByRole("button", { name: /speak new criticals/i })).toBeInTheDocument();

    await rerenderWithOverview({ ...fixtureOverview, voice: false });
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /speak new criticals/i })).toBeNull());

    // Synchronous gate, same as the wallboard-closed case: if it holds there
    // is nothing asynchronous to wait for.
    emit(crit("vps1:down", "vps1 unreachable"));
    expect(speak).not.toHaveBeenCalled();
  });
});

/* The wallboard's speaker survives a remount, and the self-disable is honest
 * about it.
 *
 * Both halves matter and they are one bug. A kiosk that reboots at 3am comes
 * back with a dark speaker in an empty room and swallows the next critical,
 * which is the case the persistence is for. And a self-disable that changes
 * the icon without changing the stored value makes the preference
 * self-destructing: restore on, the icon lights, the first critical flips it
 * off, reload, it is lit again -- a setting that looks flaky rather than
 * wrong.
 *
 * A restored "on" is only an intention until some gesture resumes the
 * AudioContext, so opening the wallboard is made to be that gesture -- the
 * two `enableAudio` tests below hold that, since without it the operator who
 * walks up after the reboot gets a lit speaker over a silent room.
 *
 * What is NOT asserted here, because no browser will allow it: a display that
 * reboots and is never touched at all offers no gesture, so its first
 * critical is silent and flips the toggle back off. See `Shell.tsx`'s comment
 * on `wallSpeak`. */
describe("the wallboard's speaker preference", () => {
  const crit = (key: string, msg: string): LiveEvent => ({
    type: "alert", ts: "2026-09-22T12:00:00-04:00", data: { key, sev: "CRIT", msg },
  });

  async function openWallboard() {
    const shell = renderShell({ overview: { ...fixtureOverview, voice: true } });
    await screen.findByText("Overview placeholder");
    await userEvent.click(screen.getByRole("button", { name: /more actions/i }));
    await userEvent.click(screen.getByRole("menuitem", { name: /open the wallboard/i }));
    const wall = await screen.findByRole("dialog", { name: "Wallboard" });
    return { ...shell, wall, speaker: within(wall).getByRole("button", { name: /speak new criticals/i }) };
  }

  it("comes back on after a reboot when it was left on", async () => {
    window.localStorage.setItem(WALL_SPEAK_KEY, "1");
    const { speaker } = await openWallboard();
    expect(speaker).toHaveAttribute("aria-pressed", "true");
  });

  it("comes back off when it was left off, which is also the default", async () => {
    window.localStorage.setItem(WALL_SPEAK_KEY, "0");
    const { speaker } = await openWallboard();
    expect(speaker).toHaveAttribute("aria-pressed", "false");
  });

  // Opening the wallboard is the gesture that turns a restored preference
  // into audible sound; see `openWallboard` in `Shell.tsx`. `mockClear()`
  // immediately before the click is what makes both of these assert about
  // THIS gesture rather than about any `enableAudio()` a render did earlier.
  it("resumes audio when it opens with the speaker already on", async () => {
    window.localStorage.setItem(WALL_SPEAK_KEY, "1");
    renderShell({ overview: { ...fixtureOverview, voice: true } });
    await screen.findByText("Overview placeholder");
    await userEvent.click(screen.getByRole("button", { name: /more actions/i }));

    vi.mocked(enableAudio).mockClear();
    await userEvent.click(screen.getByRole("menuitem", { name: /open the wallboard/i }));
    await screen.findByRole("dialog", { name: "Wallboard" });

    expect(enableAudio).toHaveBeenCalledTimes(1);
  });

  it("leaves audio alone when it opens with the speaker off", async () => {
    window.localStorage.setItem(WALL_SPEAK_KEY, "0");
    renderShell({ overview: { ...fixtureOverview, voice: true } });
    await screen.findByText("Overview placeholder");
    await userEvent.click(screen.getByRole("button", { name: /more actions/i }));

    vi.mocked(enableAudio).mockClear();
    await userEvent.click(screen.getByRole("menuitem", { name: /open the wallboard/i }));
    await screen.findByRole("dialog", { name: "Wallboard" });

    expect(enableAudio).not.toHaveBeenCalled();
  });

  it("records the click, both ways", async () => {
    const { speaker } = await openWallboard();
    await userEvent.click(speaker);
    expect(window.localStorage.getItem(WALL_SPEAK_KEY)).toBe("1");
    await userEvent.click(speaker);
    expect(window.localStorage.getItem(WALL_SPEAK_KEY)).toBe("0");
  });

  it("records the self-disable too, so the stored value and the icon never disagree", async () => {
    window.localStorage.setItem(WALL_SPEAK_KEY, "1");
    const { emit, speaker } = await openWallboard();
    expect(speaker).toHaveAttribute("aria-pressed", "true");

    // The dead-channel case: the sentence did not play AND the context is not
    // running, which is the only combination that means "the speaker is
    // dead" (see lib/speech.ts).
    vi.mocked(speak).mockResolvedValueOnce(false);
    vi.mocked(audioReady).mockReturnValueOnce(false);
    emit(crit("vps1:down", "vps1 unreachable"));

    await waitFor(() => expect(speaker).toHaveAttribute("aria-pressed", "false"));
    expect(window.localStorage.getItem(WALL_SPEAK_KEY)).toBe("0");
  });
});

// --- DNS filtering, offered without nagging ------------------------------
//
// The setup wizard offers DNS filtering as a skippable last step, because most
// practices do not have the router password to hand on day one. The cost of
// making it skippable is that a headline feature can be skipped and then never
// thought about again — so the Shell keeps asking, quietly.
//
// "Quietly" is the whole design. This is a line, not a modal, and it is not
// dismissible: a dismiss button would turn "keeps asking" into "asked once",
// which is the state this exists to avoid.

function netWithDns(dns: string) {
  return {
    id: "n1", name: "Clinic", slug: "clinic", sensor: "active",
    first_scan_at: "2026-09-19T09:00:00Z", devices: 4,
    by_group: { Cameras: 4 }, dns,
  };
}

describe("the DNS filtering prompt", () => {
  it("asks once setup is done and DNS is still off", async () => {
    renderShell({
      onboarding: { ...SEEN_WIZARD, site_named: true, networks: [netWithDns("off")], next: null },
    });
    expect(await screen.findByText(/dns filtering/i)).toBeInTheDocument();
  });

  it("goes away on its own once traffic is actually flowing", async () => {
    // `dns: "working"` is the hub's live signal — the resolver seeing real
    // queries, not somebody having pressed a button. So the prompt retires
    // itself rather than needing to be dismissed.
    renderShell({
      onboarding: { ...SEEN_WIZARD, site_named: true, networks: [netWithDns("working")], next: null },
    });
    await screen.findByRole("main");
    expect(screen.queryByText(/dns filtering/i)).toBeNull();
  });

  it("stays quiet while setup is still unfinished", async () => {
    // The wizard is already offering it at this point. Two things asking for
    // the same router password in the same minute is how a prompt gets
    // learned as noise.
    renderShell({
      onboarding: {
        ...SEEN_WIZARD, completed: false, skipped: false, show: true,
        site_named: true, networks: [netWithDns("off")],
        next: { network_id: "n1", action: "offer_dns" },
      },
    });
    await screen.findByRole("main");
    expect(screen.queryByText(/dns filtering/i)).toBeNull();
  });

  it("names the network when more than one is missing it", async () => {
    renderShell({
      onboarding: {
        ...SEEN_WIZARD, site_named: true, next: null,
        networks: [netWithDns("working"), { ...netWithDns("off"), id: "n2", name: "Lab", slug: "lab" }],
      },
    });
    expect(await screen.findByText(/Lab/)).toBeInTheDocument();
  });
});
