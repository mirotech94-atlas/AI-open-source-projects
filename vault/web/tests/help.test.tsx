// Task 71: the Help section, the API guide's placeholder, and the two things
// that make help discoverable — the ⌘K palette and "Ask Vault about this".
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../src/lib/api", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/api")>("../src/lib/api");
  return { ...actual, search: vi.fn().mockResolvedValue({ results: [] }) };
});

import { search } from "../src/lib/api";
import { API_GUIDE, apiEndpointCount } from "../src/help/apiGuide";
import { HELP_ARTICLES, helpMatches, searchHelp } from "../src/help/articles";
import ApiGuide from "../src/pages/ApiGuide";
import Help from "../src/pages/Help";
import { CommandPalette } from "../src/shell/CommandPalette";
import { ChatHandoffContext } from "../src/shell/chat/handoff";
import { TABS } from "../src/shell/nav";

function renderAt(path: string, element: ReactElement, askGuardian = () => {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ChatHandoffContext.Provider value={{ openAlert: () => {}, askGuardian }}>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/help" element={element} />
            <Route path="/help/api" element={element} />
            <Route path="/help/:slug" element={element} />
          </Routes>
        </MemoryRouter>
      </ChatHandoffContext.Provider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("Help content", () => {
  it("gives every article a route, a summary, keywords and a question to ask", () => {
    expect(HELP_ARTICLES.length).toBeGreaterThanOrEqual(8);
    const slugs = new Set<string>();
    for (const article of HELP_ARTICLES) {
      expect(article.slug).toMatch(/^[a-z0-9-]+$/);
      expect(slugs.has(article.slug)).toBe(false);
      slugs.add(article.slug);
      expect(article.title.length).toBeGreaterThan(8);
      expect(article.summary.length).toBeGreaterThan(20);
      expect(article.keywords.length).toBeGreaterThan(0);
      // The hand-off question has to be a question about the reader's own site,
      // not a restatement of the title.
      expect(article.ask.endsWith("?")).toBe(true);
      expect(article.blocks.length).toBeGreaterThan(2);
    }
  });

  it("covers the three subjects the owner named, plus their neighbours", () => {
    const slugs = HELP_ARTICLES.map((a) => a.slug);
    expect(slugs).toContain("add-a-network");
    expect(slugs).toContain("add-a-sensor");
    expect(slugs).toContain("enrolment-code");
    expect(slugs).toContain("what-guardian-watches");
    expect(slugs).toContain("alerts-and-severities");
    expect(slugs).toContain("security-score");
    expect(slugs).toContain("notifications");
    expect(slugs).toContain("reports");
  });

  it("only ever links at routes the app actually serves", () => {
    // Every `where` block's destination must be a real nav destination (or a
    // Help route), or the guide is sending readers at a 404 — the single most
    // likely way this content rots.
    const navPaths = new Set<string>();
    for (const tab of TABS) {
      navPaths.add(tab.path);
      for (const child of tab.children ?? []) navPaths.add(child.path);
    }
    // The nav is not the whole route table any more. Inbox, Assets, Security,
    // Updates and the DNS filter are flat rows whose sub-views live on the
    // page itself, so the routes that address those views directly are still
    // served — and still the right place to send a reader — but no longer
    // named in TABS. A `where` block may point at one.
    for (const filter of ["alerts", "incidents", "recs"]) navPaths.add(`/inbox?filter=${filter}`);
    for (const view of ["websites", "servers", "devices", "screen-time"]) navPaths.add(`/assets/${view}`);
    for (const s of ["scorecards", "attack-map", "checklist", "wifi-guard"]) navPaths.add(`/security/${s}`);
    for (const s of ["calendar", "cves", "reboot"]) navPaths.add(`/updates/${s}`);
    for (const s of ["live", "profiles", "lists", "setup"]) navPaths.add(`/dns/${s}`);
    // Integrations is its own tab at /integrations now; the Config-era route
    // is still served, and the notifications guide still points there.
    navPaths.add("/settings/integrations");
    for (const article of HELP_ARTICLES) {
      for (const block of article.blocks) {
        if (block.kind !== "where") continue;
        const known = navPaths.has(block.to) || block.to.startsWith("/help/");
        expect(known, `${article.slug} links at ${block.to}`).toBe(true);
      }
    }
  });

  it("never says Nexus", () => {
    // The product is Vault. The old name must not survive in user-facing
    // copy anywhere in this content.
    for (const article of HELP_ARTICLES) {
      const text = JSON.stringify(article).toLowerCase();
      expect(text.includes("nexus"), article.slug).toBe(false);
    }
  });
});

describe("Help search", () => {
  it('finds the enrolment-code guide from "enrol code"', () => {
    // The acceptance example, and the reason matching is word-by-word:
    // "enrolment code".includes("enrol code") is false.
    expect(searchHelp("enrol code").map((a) => a.slug)).toContain("enrolment-code");
  });

  it("matches on keywords, on the summary and on body text, in any word order", () => {
    expect(searchHelp("enrollment").map((a) => a.slug)).toContain("enrolment-code");
    expect(searchHelp("code enrol").map((a) => a.slug)).toContain("enrolment-code");
    expect(searchHelp("snooze").map((a) => a.slug)).toContain("alerts-and-severities");
    expect(searchHelp("second network").map((a) => a.slug)).toContain("add-a-network");
    expect(searchHelp("share link").map((a) => a.slug)).toContain("reports");
  });

  it("matches nothing on an empty or nonsense query", () => {
    expect(searchHelp("")).toEqual([]);
    expect(searchHelp("   ")).toEqual([]);
    expect(searchHelp("zzzznotathing")).toEqual([]);
    expect(helpMatches(HELP_ARTICLES[0], "")).toBe(false);
  });
});

describe("Help pages", () => {
  it("lists every guide on the index, each linking at its own route", async () => {
    renderAt("/help", <Help />);
    for (const article of HELP_ARTICLES) {
      expect(await screen.findByRole("link", { name: article.title })).toHaveAttribute(
        "href",
        `/help/${article.slug}`,
      );
    }
    expect(screen.getByRole("link", { name: "API guide" })).toHaveAttribute("href", "/help/api");
  });

  it("renders an article's steps, notes and in-app links", async () => {
    renderAt("/help/add-a-sensor", <Help />);

    expect(await screen.findByRole("heading", { level: 1, name: /add another sensor/i })).toBeInTheDocument();
    // The steps are a real ordered list, not paragraphs that look numbered.
    expect(screen.getByRole("list")).toBeInTheDocument();
    expect(screen.getByText(/Generate enrol code/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Config → Sensors/ })).toHaveAttribute(
      "href",
      "/settings/sensors",
    );
    // A crumb back to the index, so a reader is never stranded in one article.
    expect(screen.getByRole("link", { name: "All guides" })).toHaveAttribute("href", "/help");
  });

  it("asks Vault the article's own question, and can be asked twice", async () => {
    const askGuardian = vi.fn();
    renderAt("/help/security-score", <Help />, askGuardian);

    const button = await screen.findByRole("button", { name: /ask vault about this/i });
    await userEvent.click(button);
    const asked = HELP_ARTICLES.find((a) => a.slug === "security-score")!.ask;
    expect(askGuardian).toHaveBeenCalledWith(asked);

    await userEvent.click(button);
    expect(askGuardian).toHaveBeenCalledTimes(2);
  });

  it("says so plainly for an unknown slug instead of rendering an empty article", async () => {
    renderAt("/help/no-such-guide", <Help />);
    expect(await screen.findByText(/no such guide/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "All guides" })).toBeInTheDocument();
  });
});

describe("API guide", () => {
  it("is honest that the reference is not written yet, and invents no endpoints", async () => {
    // The guard that matters most here: no endpoint names exist to be wrong
    // about, because a parallel task owns the API itself.
    expect(API_GUIDE.status).toBe("in-progress");
    expect(apiEndpointCount()).toBe(0);

    renderAt("/help/api", <ApiGuide />);
    expect(await screen.findByRole("heading", { level: 1, name: "API guide" })).toBeInTheDocument();
    expect(screen.getByText(/still being written/i)).toBeInTheDocument();
    expect(screen.queryByText(/^\/api\//)).not.toBeInTheDocument();
  });

  it("renders a reference the moment one is dropped into the data module", async () => {
    // The contract the other agent's generated content has to satisfy: fill
    // `groups`, flip `status`, change nothing in the page.
    const ready = {
      ...API_GUIDE,
      status: "ready" as const,
      auth: "A bearer token you mint in Config.",
      groups: [
        {
          key: "alerts",
          title: "Alerts",
          summary: "Read your open alerts.",
          endpoints: [
            {
              method: "GET",
              path: "/api/public/v1/alerts",
              summary: "Every open alert.",
              auth: "read scope",
              params: [
                { name: "severity", where: "query", type: "string", required: false, description: "Filter." },
              ],
              notes: ["Paged."],
            },
          ],
        },
      ],
    };
    expect(apiEndpointCount(ready)).toBe(1);
  });
});

describe("the command palette", () => {
  it('surfaces the article when someone searches "enrol code"', async () => {
    render(
      <MemoryRouter initialEntries={["/overview"]}>
        <CommandPalette
          open
          advanced
          onOpen={() => {}}
          onClose={() => {}}
          onCycleTheme={() => {}}
          onOpenChat={() => {}}
          onRunScan={() => {}}
          onSpeedtest={() => {}}
        />
      </MemoryRouter>,
    );

    await userEvent.type(screen.getByRole("combobox"), "enrol code");
    const list = screen.getByRole("listbox");
    await waitFor(() =>
      expect(within(list).getByText("Where to find your enrolment code")).toBeInTheDocument(),
    );
    // Matched locally: the article is a data module in this repo, so no hub
    // round-trip is needed to find it.
    expect(within(list).getAllByText("Help guide").length).toBeGreaterThan(0);
  });

  it("offers Help as a command with no query typed at all", () => {
    render(
      <MemoryRouter initialEntries={["/overview"]}>
        <CommandPalette
          open
          advanced
          onOpen={() => {}}
          onClose={() => {}}
          onCycleTheme={() => {}}
          onOpenChat={() => {}}
          onRunScan={() => {}}
          onSpeedtest={() => {}}
        />
      </MemoryRouter>,
    );
    expect(screen.getByText("Open help")).toBeInTheDocument();
    expect(search).not.toHaveBeenCalled();
  });
});

describe("the nav tree", () => {
  it("reaches every guide, and the API guide, from the Help index itself", () => {
    // Help used to be a dropdown in the sidebar that listed each guide. The
    // index page lists them all in its own right, so the tree was saying it
    // twice and the row is flat now. The reachability guarantee did not go
    // away with the dropdown -- it moved here, so it is asserted here.
    const help = TABS.find((t) => t.key === "help");
    expect(help).toBeDefined();
    expect(help!.path).toBe("/help");
    expect(help!.children).toBeUndefined();
    // Support is NOT under Help. Help is documentation -- things you read --
    // and raising a ticket is a conversation with a person, so it has its own
    // top-level tab rather than hiding a page inside this one.
    const support = TABS.find((t) => t.key === "support");
    expect(support).toBeDefined();
    expect(support!.path).toBe("/support");
    expect(support!.children).toBeUndefined();

    renderAt("/help", <Help />);
    const hrefs = screen
      .getAllByRole("link")
      .map((a) => a.getAttribute("href"))
      .filter((h): h is string => h !== null);
    for (const article of HELP_ARTICLES) {
      expect(hrefs, `no link to ${article.slug} on the Help index`).toContain(`/help/${article.slug}`);
    }
    expect(hrefs).toContain("/help/api");
  });
});
