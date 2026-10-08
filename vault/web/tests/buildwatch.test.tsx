import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BuildWatch } from "../src/lib/buildwatch";

/**
 * A single-page app never reloads itself, so a tab opened before a deploy runs
 * the old app until somebody reloads. This is the watcher that notices.
 * `__BUILD_SHA__` is "dev" under vitest, so each test stubs it to a real-looking
 * value first -- a "dev" bundle deliberately never checks, since it has no
 * deployed counterpart to compare with.
 */
function serverSays(sha: string) {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ sha }) })));
}

afterEach(() => { vi.unstubAllGlobals(); });

describe("noticing a newer build", () => {
  it("says so when the server has deployed a different commit", async () => {
    vi.stubGlobal("__BUILD_SHA__", "aaaaaaa");
    serverSays("bbbbbbb");
    render(<MemoryRouter><BuildWatch /></MemoryRouter>);
    expect(await screen.findByText(/has been updated since this page was opened/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /reload now/i })).toBeInTheDocument();
  });

  it("stays silent while the running build is the deployed one", async () => {
    vi.stubGlobal("__BUILD_SHA__", "aaaaaaa");
    serverSays("aaaaaaa");
    render(<MemoryRouter><BuildWatch /></MemoryRouter>);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(screen.queryByText(/has been updated/i)).toBeNull();
  });

  it("asks the server with the cache turned off, or the answer is worthless", async () => {
    vi.stubGlobal("__BUILD_SHA__", "aaaaaaa");
    serverSays("aaaaaaa");
    render(<MemoryRouter><BuildWatch /></MemoryRouter>);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(vi.mocked(fetch).mock.calls[0][1]).toMatchObject({ cache: "no-store" });
  });

  it("does not check at all from a dev bundle", async () => {
    vi.stubGlobal("__BUILD_SHA__", "dev");
    serverSays("bbbbbbb");
    render(<MemoryRouter><BuildWatch /></MemoryRouter>);
    await new Promise((r) => setTimeout(r, 30));
    expect(fetch).not.toHaveBeenCalled();
  });
});
