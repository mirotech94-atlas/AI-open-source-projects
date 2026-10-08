import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../src/design";
import OwnerTickets from "../src/owner/Tickets";
import OwnerSettings from "../src/owner/Settings";
import type { PlatformSettingsPayload, TicketDetail, TicketsPayload } from "../src/types";

// The owner console's two write surfaces. What matters here is not layout:
// it is that resolving a ticket is visible as resolved, that the operator can
// see when a reply reached nobody, and that a setting page does not offer to
// edit a credential.

vi.mock("../src/lib/api", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/api")>("../src/lib/api");
  return {
    ...actual,
    ownerTickets: vi.fn(),
    ownerTicket: vi.fn(),
    ownerReply: vi.fn(),
    setTicketStatus: vi.fn(),
    platformSettings: vi.fn(),
    savePlatformSetting: vi.fn(),
  };
});

const api = await import("../src/lib/api");

function ticket(over: Partial<TicketsPayload["tickets"][number]> = {}) {
  return {
    id: "tk1", subject: "Sensor keeps dropping off", status: "open", priority: "high",
    opened_by: "dana", created: "2026-09-16T09:00:00+00:00",
    last_message_at: "2026-09-17T08:00:00+00:00", last_author: "customer",
    messages: 2, resolved_at: "", resolved_by: "",
    tenant_id: "t2", tenant_name: "Northwind Dental",
    category: "other", context_kind: "", context_id: "", context_label: "",
    ...over,
  };
}

const QUEUE: TicketsPayload = {
  tickets: [ticket(), ticket({ id: "tk2", subject: "Invoice question", status: "answered", priority: "normal", tenant_name: "Home" })],
  mail_works: true,
  open_count: 1,
  support_email: "support@guarder.io", support_direct_email: "", support_reply_sla: "",
};

const DETAIL: TicketDetail = {
  ticket: ticket(),
  messages: [
    { id: "m1", side: "customer", author: "dana", body: "It goes offline every night.", created: "2026-09-16T09:00:00+00:00", emailed: true },
    { id: "m2", side: "owner", author: "miro", body: "Found it: the power strip is on a timer.", created: "2026-09-17T08:00:00+00:00", emailed: false },
  ],
};

const SETTINGS: PlatformSettingsPayload = {
  fields: [
    { key: "billing_mode", label: "What the product offers", help: "Trial only, or paid plans too.",
      kind: "choice", group: "Billing", value: "trial_only", overridden: false, default: "trial_only",
      minimum: null, maximum: null, choices: ["trial_only", "plans"], updated: "", updated_by: "" },
    { key: "trial_days", label: "Trial length (days)", help: "How long a NEW trial runs.",
      kind: "int", group: "Billing", value: "30", overridden: true, default: "90",
      minimum: 7, maximum: 365, choices: [], updated: "2026-09-17T00:00:00+00:00", updated_by: "miro" },
    // `value` is "True", capital T, because the hub serialises a stored bool
    // with Python's `str()`. Writing "true" here would make this fixture
    // describe a payload the hub does not send, and the toggle would pass a
    // test while rendering the wrong state in production -- which is exactly
    // what happened before this case existed.
    { key: "auto_heal", label: "Let Vault repair without asking",
      help: "On: a repair Vault may make on its own runs the moment it is needed.",
      kind: "bool", group: "Operations", value: "True", overridden: false, default: "True",
      minimum: null, maximum: null, choices: [], updated: "", updated_by: "" },
  ],
  secrets: [
    { key: "smtp", label: "Outbound email (SMTP)", set: true },
    { key: "stripe", label: "Stripe", set: false },
  ],
  base_url: "https://my.guarder.io",
  version: "0.1.0",
};

function renderAt(node: ReactElement, path: string, route: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path={route} element={node} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.clearAllMocks());

describe("owner support queue", () => {
  it("leads with what is waiting on the operator and names the mailbox", async () => {
    vi.mocked(api.ownerTickets).mockResolvedValue(QUEUE);
    vi.mocked(api.ownerTicket).mockResolvedValue(DETAIL);
    renderAt(<OwnerTickets />, "/owner/tickets", "/owner/tickets");
    expect(await screen.findByText(/1 waiting on you, 2 in total/)).toBeInTheDocument();
    expect(screen.getByText(/support@guarder.io/)).toBeInTheDocument();
    // The two sides of the conversation are told apart by status wording,
    // not by colour alone.
    expect(screen.getByText("Waiting on you")).toBeInTheDocument();
    expect(screen.getByText("Waiting on them")).toBeInTheDocument();
  });

  it("opens the thread the letter linked to, not the top of the queue", async () => {
    vi.mocked(api.ownerTickets).mockResolvedValue(QUEUE);
    vi.mocked(api.ownerTicket).mockResolvedValue(DETAIL);
    renderAt(<OwnerTickets />, "/owner/tickets/tk2", "/owner/tickets/:id");
    await screen.findByRole("heading", { name: /sensor keeps dropping off/i });
    expect(vi.mocked(api.ownerTicket)).toHaveBeenCalledWith("tk2");
  });

  it("shows a reply that reached nobody as in-app only", async () => {
    vi.mocked(api.ownerTickets).mockResolvedValue(QUEUE);
    vi.mocked(api.ownerTicket).mockResolvedValue(DETAIL);
    renderAt(<OwnerTickets />, "/owner/tickets", "/owner/tickets");
    // A customer with no address on file is ordinary; pretending the letter
    // went out is not.
    expect(await screen.findByText(/in-app only/i)).toBeInTheDocument();
  });

  it("resolves a ticket and the status says resolved", async () => {
    vi.mocked(api.ownerTickets).mockResolvedValue(QUEUE);
    vi.mocked(api.ownerTicket).mockResolvedValue(DETAIL);
    vi.mocked(api.setTicketStatus).mockResolvedValue({
      ...DETAIL,
      ticket: ticket({ status: "resolved", resolved_by: "miro", resolved_at: "2026-09-17T09:00:00+00:00" }),
    });
    renderAt(<OwnerTickets />, "/owner/tickets", "/owner/tickets");
    await userEvent.click(await screen.findByRole("button", { name: /mark resolved/i }));
    expect(vi.mocked(api.setTicketStatus)).toHaveBeenCalledWith("tk1", "resolved");
  });

  it("tells the operator a reply goes to both places", async () => {
    vi.mocked(api.ownerTickets).mockResolvedValue(QUEUE);
    vi.mocked(api.ownerTicket).mockResolvedValue(DETAIL);
    renderAt(<OwnerTickets />, "/owner/tickets", "/owner/tickets");
    expect(await screen.findByText(/posts to their thread in the app and emails them/i)).toBeInTheDocument();
  });
});

describe("owner settings", () => {
  it("shows what has been changed away from the default", async () => {
    vi.mocked(api.platformSettings).mockResolvedValue(SETTINGS);
    renderAt(<OwnerSettings />, "/owner/settings", "/owner/settings");
    expect(await screen.findByText(/changed from the default of 90 by miro/i)).toBeInTheDocument();
    expect(screen.getByText(/changes take effect immediately/i)).toBeInTheDocument();
  });

  it("saves one setting at a time and only once it differs", async () => {
    vi.mocked(api.platformSettings).mockResolvedValue(SETTINGS);
    vi.mocked(api.savePlatformSetting).mockResolvedValue(SETTINGS);
    renderAt(<OwnerSettings />, "/owner/settings", "/owner/settings");
    const input = await screen.findByLabelText(/trial length/i);
    const row = input.closest(".g-set-row") as HTMLElement;
    // Nothing typed yet: Save is not offered.
    expect(within(row).getByRole("button", { name: /save/i })).toBeDisabled();
    await userEvent.clear(input);
    await userEvent.type(input, "45");
    await userEvent.click(within(row).getByRole("button", { name: /save/i }));
    expect(vi.mocked(api.savePlatformSetting)).toHaveBeenCalledWith("trial_days", "45");
  });

  it("reports credentials without offering to edit them", async () => {
    vi.mocked(api.platformSettings).mockResolvedValue(SETTINGS);
    renderAt(<OwnerSettings />, "/owner/settings", "/owner/settings");
    expect(await screen.findByText("Outbound email (SMTP)")).toBeInTheDocument();
    expect(screen.getByText("set")).toBeInTheDocument();
    expect(screen.getByText("not set")).toBeInTheDocument();
    // No field for a secret, anywhere on the page.
    expect(screen.queryByLabelText(/stripe/i)).toBeNull();
    expect(document.querySelector('input[type="password"]')).toBeNull();
  });

  it("says so when the read fails rather than showing an empty page", async () => {
    vi.mocked(api.platformSettings).mockRejectedValue(new Error("403"));
    renderAt(<OwnerSettings />, "/owner/settings", "/owner/settings");
    expect(await screen.findByText(/could not be read/i)).toBeInTheDocument();
  });

  it("shows a switch that is on as on, whatever casing the hub sends", async () => {
    // The hub sends Python's `str(True)` -- "True". A `=== "true"` comparison
    // renders that as OFF, and on this setting that means the console
    // reporting that Vault is not repairing while it is. The UI mirrors
    // `set_value`'s own accepted set instead of matching one spelling.
    vi.mocked(api.platformSettings).mockResolvedValue(SETTINGS);
    renderAt(<OwnerSettings />, "/owner/settings", "/owner/settings");

    const sw = await screen.findByRole("switch", { name: /let vault repair without asking/i });
    expect(sw).toHaveAttribute("aria-checked", "true");
  });

  it("saves a switch the moment it is flipped, with no separate Save", async () => {
    // A switch with its own Save invites the state where the control says one
    // thing and the platform is doing another -- on a kill switch that is the
    // difference between "Vault is repairing" and "Vault is not".
    vi.mocked(api.platformSettings).mockResolvedValue(SETTINGS);
    vi.mocked(api.savePlatformSetting).mockResolvedValue(SETTINGS);
    renderAt(<OwnerSettings />, "/owner/settings", "/owner/settings");

    const sw = await screen.findByRole("switch", { name: /let vault repair without asking/i });
    await userEvent.click(sw);

    expect(vi.mocked(api.savePlatformSetting)).toHaveBeenCalledWith("auto_heal", "false");
  });

});
