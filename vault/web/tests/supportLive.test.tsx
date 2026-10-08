import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LiveEvent, TicketDetail, TicketRow, TicketsPayload } from "../src/types";

// Support as a LIVE desk. The hub has always published a `ticket` frame on
// every reply; the client dropped it as an unknown type, so Vault's answer
// appeared a minute late at best. These pin that it now lands as it is sent,
// and that the reader can see what they have not read.

let push: ((e: LiveEvent) => void) | null = null;
vi.mock("../src/lib/live", () => ({
  useLiveFeed: () => ({
    status: "open", last: null, dropped: 0,
    on: (type: string, fn: (e: LiveEvent) => void) => {
      if (type === "ticket") push = fn;
      return () => { push = null; };
    },
  }),
}));
vi.mock("../src/lib/api", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/api")>("../src/lib/api");
  return { ...actual, myTickets: vi.fn(), myTicket: vi.fn(), openTicket: vi.fn(), replyToMyTicket: vi.fn() };
});
import { myTicket, myTickets } from "../src/lib/api";
import Support from "../src/pages/Support";

const OLD = "2026-09-20T09:00:00Z";
const NEW = "2026-09-20T10:00:00Z";

function row(over: Partial<TicketRow> = {}): TicketRow {
  return {
    id: "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6", subject: "Wi-Fi drops in the back office", status: "open",
    priority: "normal", opened_by: "dana", created: OLD, last_message_at: OLD, last_author: "dana",
    messages: 1, resolved_at: "", resolved_by: "", tenant_id: "t", tenant_name: "T",
    category: "other", context_kind: "", context_id: "", context_label: "", ...over,
  };
}
function payload(tickets: TicketRow[]): TicketsPayload {
  return { tickets, mail_works: true, open_count: 1, support_email: "", support_direct_email: "", support_reply_sla: "" };
}
function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter initialEntries={["/support"]}>
      <QueryClientProvider client={qc}>
        <Routes><Route path="/support" element={<Support />} /></Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => { try { localStorage.clear(); } catch { /* none */ } });
afterEach(() => { vi.restoreAllMocks(); push = null; });

describe("support is live", () => {
  it("says it is live", async () => {
    vi.mocked(myTickets).mockResolvedValue(payload([row()]));
    vi.mocked(myTicket).mockResolvedValue({ ticket: row(), messages: [] } as unknown as TicketDetail);
    mount();
    expect(await screen.findByTestId("support-live")).toHaveTextContent("Live");
  });

  it("shows Vault's reply as soon as the hub announces it", async () => {
    let current = row();
    vi.mocked(myTickets).mockImplementation(async () => payload([current]));
    vi.mocked(myTicket).mockImplementation(async () => ({
      ticket: current,
      messages: current.messages > 1
        ? [{ id: "m1", side: "customer", author: "dana", body: "It drops.", created: OLD },
           { id: "m2", side: "owner", author: "Vault", body: "Looking at it now.", created: NEW }]
        : [{ id: "m1", side: "customer", author: "dana", body: "It drops.", created: OLD }],
    }) as unknown as TicketDetail);
    mount();
    await screen.findByText("It drops.");
    current = row({ status: "answered", last_author: "Vault", last_message_at: NEW, messages: 2 });
    act(() => { push!({ type: "ticket", ts: NEW, data: { action: "reply", id: current.id } } as LiveEvent); });
    expect(await screen.findByText("Looking at it now.")).toBeInTheDocument();
    const list = screen.getByRole("navigation", { name: "Your tickets" });
    await waitFor(() => expect(within(list).getByText("Waiting on you")).toBeInTheDocument());
  });

  it("marks a ticket Vault answered that this browser has not opened", async () => {
    const answered = row({ id: "ffffffffffffffffffffffffffff9999", subject: "Printer shows open port",
                           status: "answered", last_author: "Vault", last_message_at: NEW, messages: 2 });
    vi.mocked(myTickets).mockResolvedValue(payload([row(), answered]));
    vi.mocked(myTicket).mockImplementation(async (id: string) =>
      ({ ticket: id === answered.id ? answered : row(), messages: [] }) as unknown as TicketDetail);
    mount();
    // The first ticket opens by default; the answered one is unread.
    const dots = await screen.findAllByTestId("ticket-unread");
    expect(dots).toHaveLength(1);
    expect(dots[0].closest("button")).toHaveTextContent("Printer shows open port");
  });
});
