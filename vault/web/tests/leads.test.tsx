import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import Leads from "../src/pages/Leads";
import type { MeOut } from "../src/api";
import { ToastProvider } from "../src/design";

vi.mock("../src/lib/api", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/api")>("../src/lib/api");
  return { ...actual, get: vi.fn(), post: vi.fn() };
});
import { ApiError, get, post } from "../src/lib/api";

vi.mock("../src/api", async () => {
  const actual = await vi.importActual<typeof import("../src/api")>("../src/api");
  return { ...actual, me: vi.fn() };
});
import { me } from "../src/api";

afterEach(() => vi.clearAllMocks());

function meFixture(isSuperadmin: boolean): MeOut {
  return {
    user: { username: "miro", display_name: "Miro", is_superadmin: isSuperadmin },
    tenants: [{ id: "t1", slug: "home", name: "Home", role: "admin" }],
    active_tenant: { id: "t1", slug: "home", name: "Home", role: "admin" },
    chat_providers: [],
  };
}

const LEADS = {
  leads: [
    {
      id: "l2",
      name: "Dana Okafor",
      email: "dana@example.com",
      company: "Okafor Dental",
      network_size: "one-site",
      note: "Two servers and a shop full of tablets.",
      ip: "203.0.113.9",
      created: "2026-09-10T14:00:00Z",
      site_name: "Okafor Dental — Main St",
      status: "pending",
      decided_at: "",
      decided_by: "",
      tenant_slug: "",
    },
    {
      id: "l1",
      name: "Sam Price",
      email: "sam@example.net",
      company: "",
      network_size: "home",
      note: "",
      ip: "198.51.100.4",
      created: "2026-09-09T09:00:00Z",
      site_name: "",
      status: "pending",
      decided_at: "",
      decided_by: "",
      tenant_slug: "",
    },
  ],
};

// Prints the current URL's search string into a `data-testid="loc"` element
// so a test can assert on it without reaching into the router's history
// object directly.
function LocationProbe() {
  const location = useLocation();
  return <div data-testid="loc">{location.search}</div>;
}

function renderPage(path = "/settings/leads"): ReactElement {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter initialEntries={[path]}>
          <Leads />
          <LocationProbe />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>
  );
}

describe("Leads", () => {
  it("lists what came in from the public site, newest first", async () => {
    vi.mocked(me).mockResolvedValue(meFixture(true));
    vi.mocked(get).mockResolvedValue(LEADS);
    render(renderPage());

    await waitFor(() => expect(screen.getByText("Dana Okafor")).toBeInTheDocument());
    expect(vi.mocked(get)).toHaveBeenCalledWith("/api/v1/leads");

    // Hub order is preserved (the table is handed the rows as they came).
    const names = screen.getAllByText(/Dana Okafor|Sam Price/).map((n) => n.textContent);
    expect(names).toEqual(["Dana Okafor", "Sam Price"]);

    // The email is the thing the owner acts on, so it is a mailto.
    expect(screen.getByRole("link", { name: "dana@example.com" })).toHaveAttribute(
      "href",
      "mailto:dana@example.com",
    );
    // The form's raw value is rendered as the question's own wording.
    expect(screen.getByText("One office or shop")).toBeInTheDocument();
    expect(screen.getByText("Okafor Dental")).toBeInTheDocument();
  });

  it("does not fetch or show anything for a non-superadmin", async () => {
    vi.mocked(me).mockResolvedValue(meFixture(false));
    vi.mocked(get).mockResolvedValue(LEADS);
    render(renderPage());

    await waitFor(() =>
      expect(screen.getByText(/for the Vault account owner/i)).toBeInTheDocument(),
    );
    expect(vi.mocked(get)).not.toHaveBeenCalled();
    expect(screen.queryByText("Dana Okafor")).not.toBeInTheDocument();
  });

  it("says so when nothing has come in yet", async () => {
    vi.mocked(me).mockResolvedValue(meFixture(true));
    vi.mocked(get).mockResolvedValue({ leads: [] });
    render(renderPage());

    await waitFor(() =>
      expect(screen.getByText("No access requests yet.")).toBeInTheDocument(),
    );
  });

  it("shows status and site name, and approves from a pre-filled confirm", async () => {
    const user = userEvent.setup();
    vi.mocked(me).mockResolvedValue(meFixture(true));
    vi.mocked(get).mockResolvedValue(LEADS);
    vi.mocked(post).mockResolvedValue({ ...LEADS.leads[0], status: "approved", decided_by: "miro", tenant_slug: "okafor-dental-main-st" });
    render(renderPage());
    await waitFor(() => expect(screen.getByText("Dana Okafor")).toBeInTheDocument());
    expect(screen.getAllByText("Pending")).toHaveLength(2);
    expect(screen.getByText("Okafor Dental — Main St")).toBeInTheDocument();

    await user.click(screen.getAllByRole("button", { name: "Approve" })[0]);
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByLabelText("Site name")).toHaveValue("Okafor Dental — Main St");
    expect(within(dialog).getByLabelText("Slug")).toHaveValue("okafor-dental-main-st");
    expect(within(dialog).getByLabelText("Username")).toHaveValue("dana@example.com");
    expect(within(dialog).getByLabelText("Display name")).toHaveValue("Dana Okafor");

    await user.click(within(dialog).getByRole("button", { name: "Create account" }));
    await waitFor(() => expect(vi.mocked(post)).toHaveBeenCalledWith("/api/v1/leads/l2/approve", {
      tenant_name: "Okafor Dental — Main St", slug: "okafor-dental-main-st",
      username: "dana@example.com", display_name: "Dana Okafor",
    }));
  });

  it("rejects after a plain confirm", async () => {
    const user = userEvent.setup();
    vi.mocked(me).mockResolvedValue(meFixture(true));
    vi.mocked(get).mockResolvedValue(LEADS);
    vi.mocked(post).mockResolvedValue({ ...LEADS.leads[1], status: "rejected", decided_by: "miro" });
    render(renderPage());
    await waitFor(() => expect(screen.getByText("Sam Price")).toBeInTheDocument());
    await user.click(screen.getAllByRole("button", { name: "Reject" })[1]);
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Reject request" }));
    await waitFor(() => expect(vi.mocked(post)).toHaveBeenCalledWith("/api/v1/leads/l1/reject", {}));
  });

  it("opens the confirm for the lead named in the URL", async () => {
    vi.mocked(me).mockResolvedValue(meFixture(true));
    vi.mocked(get).mockResolvedValue(LEADS);
    render(renderPage("/settings/leads?lead=l2&decide=approve"));
    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    expect(within(screen.getByRole("dialog")).getByLabelText("Username")).toHaveValue("dana@example.com");
  });

  it("shows a decided lead as decided, with no buttons", async () => {
    vi.mocked(me).mockResolvedValue(meFixture(true));
    vi.mocked(get).mockResolvedValue({ leads: [{ ...LEADS.leads[0], status: "approved", decided_by: "miro", decided_at: "2026-09-16T10:00:00Z", tenant_slug: "okafor-dental-main-st" }] });
    render(renderPage());
    await waitFor(() => expect(screen.getByText("Dana Okafor")).toBeInTheDocument());
    // In the table, not the page: the filter row above it also says
    // "Approved · 1", and that chip is not the decision this test is about.
    expect(within(screen.getByRole("table")).getByText(/Approved/)).toBeInTheDocument();
    expect(screen.getByText("okafor-dental-main-st")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  });

  it("closing the confirm clears ?lead= and ?decide=", async () => {
    const user = userEvent.setup();
    vi.mocked(me).mockResolvedValue(meFixture(true));
    vi.mocked(get).mockResolvedValue(LEADS);
    render(renderPage("/settings/leads?lead=l2&decide=approve"));
    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    expect(screen.getByTestId("loc")).toHaveTextContent("?lead=l2&decide=approve");

    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByTestId("loc")).toHaveTextContent("");
  });

  it("refetches the list and toasts on a successful approve", async () => {
    const user = userEvent.setup();
    vi.mocked(me).mockResolvedValue(meFixture(true));
    vi.mocked(get).mockResolvedValue(LEADS);
    vi.mocked(post).mockResolvedValue({ ...LEADS.leads[0], status: "approved", decided_by: "miro", tenant_slug: "okafor-dental-main-st" });
    render(renderPage());
    await waitFor(() => expect(screen.getByText("Dana Okafor")).toBeInTheDocument());

    await user.click(screen.getAllByRole("button", { name: "Approve" })[0]);
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Create account" }));

    await waitFor(() => expect(vi.mocked(get).mock.calls.length).toBeGreaterThanOrEqual(2));
    expect(vi.mocked(get)).toHaveBeenCalledWith("/api/v1/leads");
    await waitFor(() =>
      expect(screen.getByText(/Account created: okafor-dental-main-st/)).toBeInTheDocument(),
    );
  });

  it("toasts the hub's error, refetches, and leaves the confirm open", async () => {
    const user = userEvent.setup();
    vi.mocked(me).mockResolvedValue(meFixture(true));
    vi.mocked(get).mockResolvedValue(LEADS);
    vi.mocked(post).mockRejectedValue(new ApiError(409, "this request was rejected"));
    render(renderPage());
    await waitFor(() => expect(screen.getByText("Sam Price")).toBeInTheDocument());
    const getCallsBefore = vi.mocked(get).mock.calls.length;

    await user.click(screen.getAllByRole("button", { name: "Reject" })[1]);
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Reject request" }));

    await waitFor(() => expect(screen.getByText("this request was rejected")).toBeInTheDocument());
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    // A 409 means someone else already decided this lead — the list must be
    // refetched so the page reflects the current state, not a stale row.
    await waitFor(() => expect(vi.mocked(get).mock.calls.length).toBeGreaterThan(getCallsBefore));
  });

  it("sorts a pending lead first regardless of fetch order", async () => {
    vi.mocked(me).mockResolvedValue(meFixture(true));
    vi.mocked(get).mockResolvedValue({
      leads: [
        {
          id: "l3",
          name: "Casey Kim",
          email: "casey@example.com",
          company: "",
          network_size: "home",
          note: "",
          ip: "",
          created: "2026-09-12T10:00:00Z",
          site_name: "",
          status: "approved",
          decided_at: "2026-09-13T00:00:00Z",
          decided_by: "miro",
          tenant_slug: "casey",
        },
        { ...LEADS.leads[0] },
        {
          ...LEADS.leads[1],
          status: "rejected",
          decided_at: "2026-09-09T12:00:00Z",
          decided_by: "miro",
        },
      ],
    });
    render(renderPage());
    await waitFor(() => expect(screen.getByText("Casey Kim")).toBeInTheDocument());

    const names = screen
      .getAllByText(/^(Casey Kim|Dana Okafor|Sam Price)$/)
      .map((n) => n.textContent);
    expect(names[0]).toBe("Dana Okafor");
  });

  it("re-derives the slug when the site name is edited", async () => {
    const user = userEvent.setup();
    vi.mocked(me).mockResolvedValue(meFixture(true));
    vi.mocked(get).mockResolvedValue(LEADS);
    render(renderPage());
    await waitFor(() => expect(screen.getByText("Dana Okafor")).toBeInTheDocument());

    await user.click(screen.getAllByRole("button", { name: "Approve" })[0]);
    const dialog = screen.getByRole("dialog");
    const siteInput = within(dialog).getByLabelText("Site name");
    await user.clear(siteInput);
    await user.type(siteInput, "Dana Clinic");

    expect(within(dialog).getByLabelText("Slug")).toHaveValue("dana-clinic");
  });
});
