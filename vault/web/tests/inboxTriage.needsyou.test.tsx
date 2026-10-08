import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../src/design";
import { InboxTriage } from "../src/components/InboxTriage";
import { ChatHandoffContext } from "../src/shell/chat/handoff";
import type { InboxTriagePayload } from "../src/types";

/* "Done" is a claim, and it has to be earned.
 *
 * The hub dispatches a recommendation only when it carries a runnable action
 * (a host and an action name). Otherwise it answers `status: "needs-you"` and
 * the work is still entirely the owner's to do.
 *
 * This card used to say "Done" to that unconditionally. It happened on the
 * live hub: a failing system DNS resolver was accepted from this very card,
 * recorded `needs-you`, and reported as Done, with no row on the Activity page
 * to contradict it. The owner went looking for the queue and found nothing,
 * which is exactly right, because nothing had been queued.
 */
vi.mock("../src/lib/api", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/api")>("../src/lib/api");
  return { ...actual, inboxTriage: vi.fn(), runAction: vi.fn(), alertHandoff: vi.fn() };
});

import { alertHandoff, inboxTriage, runAction } from "../src/lib/api";

const payload = (): InboxTriagePayload =>
  ({
    outcomes: { handled: 0, needs_yes: 1, needs_you: 0 },
    lead: {
      id: "rec:dns:system",
      sev: "CRIT",
      title: "System DNS resolver is failing (public resolvers work)",
      why: "",
      since: "since today",
      rank_reason: "CRIT severity, 85% sure",
      actions: [
        { kind: "yes", label: "Yes, do it", method: "POST", path: "/api/v1/recs/dns:system", body: { decision: "yes" }, tier: 2 },
        { kind: "no", label: "No thanks", method: "POST", path: "/api/v1/recs/dns:system", body: { decision: "no" }, tier: 2 },
      ],
      remedy: null,
      primary_min_role: null,
      primary_label: "",
    },
    rest: [],
  }) as unknown as InboxTriagePayload;

function renderCard() {
  vi.mocked(inboxTriage).mockResolvedValue(payload());
  vi.mocked(alertHandoff).mockResolvedValue({ thread_id: "t1" } as never);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter>
          <ChatHandoffContext.Provider value={{ openAlert: () => {}, askGuardian: () => {} }}>
            <InboxTriage />
          </ChatHandoffContext.Provider>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe("accepting a recommendation Vault cannot run itself", () => {
  beforeEach(() => vi.clearAllMocks());

  it("says it needs the owner and opens the chat, rather than claiming Done", async () => {
    vi.mocked(runAction).mockResolvedValue({ status: "needs-you", queue_id: null } as never);
    renderCard();

    await userEvent.click(await screen.findByRole("button", { name: /yes, do it/i }));

    // The toast, specifically. "Needs you" also appears as an outcome
    // counter heading on this card, so match the sentence, not the phrase.
    await waitFor(() =>
      expect(screen.getByText(/this one needs you\. opening vault/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/^done$/i)).not.toBeInTheDocument();
    await waitFor(() => expect(vi.mocked(alertHandoff)).toHaveBeenCalled());
  });

  it("still says Done when the hub really did act", async () => {
    vi.mocked(runAction).mockResolvedValue({ status: "queued", queue_id: "q1" } as never);
    renderCard();

    await userEvent.click(await screen.findByRole("button", { name: /yes, do it/i }));

    await waitFor(() => expect(screen.getByText(/^done$/i)).toBeInTheDocument());
    expect(vi.mocked(alertHandoff)).not.toHaveBeenCalled();
  });
});
