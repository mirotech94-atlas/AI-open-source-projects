import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../src/design";
import Jobs from "../src/pages/Jobs";

// A page opened in a background tab (Cmd-click on "Activity") must still
// load. Gating the query's `enabled` on document visibility paused polling —
// correctly — but also blocked the first fetch, so the tab sat on "Loading
// activity…" until it was looked at, and read as broken.

vi.mock("../src/lib/api", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/api")>("../src/lib/api");
  return { ...actual, jobsPayload: vi.fn() };
});
import { jobsPayload } from "../src/lib/api";

afterEach(() => vi.restoreAllMocks());

describe("a background tab still loads", () => {
  it("fetches jobs even when the document is hidden", async () => {
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    vi.mocked(jobsPayload).mockResolvedValue({ jobs: [], active: [], history: [], kinds: [] } as unknown as Awaited<ReturnType<typeof jobsPayload>>);
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <MemoryRouter initialEntries={["/activity"]}>
        <QueryClientProvider client={qc}><ToastProvider><Jobs /></ToastProvider></QueryClientProvider>
      </MemoryRouter>,
    );
    await waitFor(() => expect(jobsPayload).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByText(/Loading activity/)).toBeNull());
  });
});
