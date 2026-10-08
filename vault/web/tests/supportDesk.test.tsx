import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import Support from "../src/pages/Support";
import type { TicketDetail, TicketRow, TicketsPayload } from "../src/types";

// Support as a ticket desk. What matters is that a customer can tell, at a
// glance, which tickets are waiting on THEM — and that every number on the
// page is a count of rows they can see below it.

vi.mock("../src/lib/api", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/api")>("../src/lib/api");
  return { ...actual, myTickets: vi.fn(), myTicket: vi.fn(), openTicket: vi.fn(), replyToMyTicket: vi.fn() };
});
import { myTicket, myTickets } from "../src/lib/api";

function row(over: Partial<TicketRow> = {}): TicketRow {
  return {
    id: "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6", subject: "Wi-Fi drops in the back office", status: "open",
    priority: "normal", opened_by: "dana", created: "2026-09-20T09:00:00Z", last_message_at: "2026-09-20T09:00:00Z",
    last_author: "dana", messages: 1, resolved_at: "", resolved_by: "", tenant_id: "t", tenant_name: "T",
    category: "other", context_kind: "", context_id: "", context_label: "", ...over,
  };
}
const PAYLOAD: TicketsPayload = {
  tickets: [
    row(),
    row({ id: "ffffffffffffffffffffffffffff9999", subject: "Printer shows open port", status: "answered", priority: "high", last_author: "Vault", messages: 3 }),
    row({ id: "0000000000000000000000000000abcd", subject: "Old camera question", status: "resolved", resolved_at: "2026-09-19T10:00:00Z", messages: 4 }),
  ],
  mail_works: true,
  open_count: 2, support_email: "support@guarder.io", support_direct_email: "", support_reply_sla: "under 2 hours, weekdays",
};

function mount(path = "/support") {
  vi.mocked(myTickets).mockResolvedValue(PAYLOAD);
  vi.mocked(myTicket).mockImplementation(async (id: string) => ({
    ticket: PAYLOAD.tickets.find((t) => t.id === id)!,
    messages: [{ id: "m1", side: "customer", author: "dana", body: "It drops every afternoon.", created: "2026-09-20T09:00:00Z" }],
  }) as unknown as TicketDetail);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter initialEntries={[path]}>
      <QueryClientProvider client={qc}>
        <Routes><Route path="/support" element={<Support />} /><Route path="/support/:id" element={<Support />} /></Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}
afterEach(() => vi.restoreAllMocks());

describe("support desk", () => {
  it("counts open, waiting-on-you and resolved from the rows it shows", async () => {
    mount();
    await within(screen.getByRole("navigation", { name: "Your tickets" })).findByText("Wi-Fi drops in the back office");
    const strip = screen.getByRole("list", { name: "Ticket counts" });
    expect(strip.textContent).toMatch(/1\s*Waiting on Vault/);
    expect(strip.textContent).toMatch(/1\s*Waiting on you/);
    expect(strip.textContent).toMatch(/1\s*Resolved/);
    expect(strip.textContent).toMatch(/under 2 hours, weekdays/);
  });

  it("filters narrow the list and say how many they hold", async () => {
    mount();
    const list = screen.getByRole("navigation", { name: "Your tickets" });
    await within(list).findByText("Wi-Fi drops in the back office");
    await userEvent.click(screen.getByRole("button", { name: /Resolved · 1/ }));
    expect(within(list).queryByText("Wi-Fi drops in the back office")).toBeNull();
    expect(within(list).getByText("Old camera question")).toBeInTheDocument();
  });

  it("finds a ticket by its short reference", async () => {
    mount();
    const list = screen.getByRole("navigation", { name: "Your tickets" });
    await within(list).findByText("Wi-Fi drops in the back office");
    await userEvent.type(screen.getByRole("searchbox", { name: "Search tickets" }), "#FF9999");
    expect(within(list).getByText("Printer shows open port")).toBeInTheDocument();
    expect(within(list).queryByText("Wi-Fi drops in the back office")).toBeNull();
  });

  it("a high-priority ticket wears its priority in the list; a normal one does not", async () => {
    mount();
    await screen.findByText("Printer shows open port");
    const items = screen.getAllByRole("button", { name: /messages?$/ });
    const high = items.find((b) => b.textContent?.includes("Printer shows open port"))!;
    const normal = items.find((b) => b.textContent?.includes("Wi-Fi drops"))!;
    expect(high.textContent).toMatch(/High/);
    expect(normal.textContent).not.toMatch(/Normal/);
  });

  it("the thread shows the reference, priority and where the ticket is in its life", async () => {
    mount("/support/ffffffffffffffffffffffffffff9999");
    await screen.findByRole("heading", { name: "Printer shows open port" });
    expect(screen.getAllByText("#FF9999").length).toBeGreaterThan(0);
    expect(screen.getByText(/High priority/)).toBeInTheDocument();
    const steps = screen.getByRole("list", { name: "Ticket progress" });
    const done = [...steps.querySelectorAll("li[data-done]")].map((li) => li.querySelector("span")?.textContent);
    expect(done).toEqual(["Opened", "Vault replied"]);
  });
});

// --------------------------------------------------------------------------
// Item 46: a ticket desk built for a security product
// --------------------------------------------------------------------------

/** `mount` pins its own PAYLOAD, so these tests -- which are all about what
 *  a SINGLE row carries -- bring their own. */
function mountRows(tickets: TicketRow[], path = "/support") {
  vi.mocked(myTickets).mockResolvedValue({ ...PAYLOAD, tickets });
  vi.mocked(myTicket).mockImplementation(async (id: string) => ({
    ticket: tickets.find((t) => t.id === id)!,
    messages: [{ id: "m1", side: "customer", author: "dana", body: "It drops.", created: "2026-09-20T09:00:00Z" }],
  }) as unknown as TicketDetail);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter initialEntries={[path]}>
      <QueryClientProvider client={qc}>
        <Routes><Route path="/support" element={<Support />} /></Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe("what a ticket is about", () => {
  it("says the category on every row, so the queue is triageable at a glance", async () => {
    mountRows([row({ category: "missed" })]);
    const list = screen.getByRole("navigation", { name: "Your tickets" });
    await within(list).findByText("Wi-Fi drops in the back office");
    expect(within(list).getByText("Vault missed it")).toBeTruthy();
  });

  it("shows how long an unanswered ticket has been waiting, and nothing for a resolved one", async () => {
    const old = new Date(Date.now() - 3 * 86_400_000).toISOString();
    mountRows([
      row({ last_message_at: old }),
      row({ id: "0000000000000000000000000000abcd", subject: "Old camera question",
            status: "resolved", last_message_at: old }),
    ]);
    const list = screen.getByRole("navigation", { name: "Your tickets" });
    await within(list).findByText("Wi-Fi drops in the back office");
    expect(within(list).getByText("waiting 3d")).toBeTruthy();
    // One row is ageing; the resolved one is not waiting for anybody.
    expect(within(list).queryAllByText(/^waiting /)).toHaveLength(1);
  });

  it("carries the thing a ticket was raised about, and links to it", async () => {
    const about = row({
      context_kind: "device", context_id: "aa:bb:cc:dd:ee:ff",
      context_label: "Hikvision camera · 192.168.1.64",
    });
    mountRows([about]);
    const panel = await screen.findByTestId("thread-about");
    const link = within(panel).getByRole("link", { name: "Hikvision camera · 192.168.1.64" });
    expect(link.getAttribute("href")).toBe("/assets/devices/aa%3Abb%3Acc%3Add%3Aee%3Aff");
  });

  it("opens the composer with the thing attached when arrived at from elsewhere", async () => {
    mountRows([], "/support?about_kind=device&about_id=aa%3Abb&about_label=Back+office+camera");
    const attached = await screen.findByTestId("ticket-about");
    expect(attached.textContent).toContain("Back office camera");
    // And the subject is seeded with it, rather than leaving a blank box
    // under a chip that already says what this is.
    expect((screen.getByLabelText("What is it about?") as HTMLInputElement).value)
      .toBe("Back office camera");
  });
});
