import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, get } from "../src/lib/api";

// The transport's 401 handling (`lib/api.ts`'s `request`): a session that has
// expired must send the user back through login carrying where they were, so
// approving a lead from `/settings/leads?lead=...` (the link in the owner's
// email/Slack ping) round-trips instead of dropping them on a bare "/".
//
// jsdom refuses to actually perform a full-document navigation (assigning
// `window.location.href` logs "Not implemented: navigation to another
// Document" and leaves the location unchanged), so the location this test
// reads from is a small stand-in that only records what was assigned to
// `href`, mirroring how a real browser would resolve a relative path against
// the current one.

function mockFetch401() {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      status: 401,
      ok: false,
      statusText: "Unauthorized",
      json: async () => ({}),
    }),
  );
}

function stubLocation(pathname: string, search = "") {
  const state = { pathname, search };
  const fakeLocation = {
    get pathname() {
      return state.pathname;
    },
    get search() {
      return state.search;
    },
    set href(value: string) {
      const url = new URL(value, "http://localhost");
      state.pathname = url.pathname;
      state.search = url.search;
    },
  };
  Object.defineProperty(window, "location", { value: fakeLocation, writable: true, configurable: true });
  return state;
}

const realLocation = window.location;

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
  Object.defineProperty(window, "location", { value: realLocation, writable: true, configurable: true });
});

describe("a 401 redirects to /login carrying the deep link", () => {
  it("appends ?next=<path+query> when on a page other than / or /login", async () => {
    stubLocation("/settings/leads", "?lead=l2&decide=approve");
    mockFetch401();
    await expect(get("/api/v1/me")).rejects.toBeInstanceOf(ApiError);
    expect(window.location.pathname).toBe("/login");
    expect(window.location.search).toBe("?next=%2Fsettings%2Fleads%3Flead%3Dl2%26decide%3Dapprove");
  });

  it("redirects with no ?next= when already at /", async () => {
    stubLocation("/", "");
    mockFetch401();
    await expect(get("/api/v1/me")).rejects.toBeInstanceOf(ApiError);
    expect(window.location.pathname).toBe("/login");
    expect(window.location.search).toBe("");
  });

  it("does not redirect again when already on /login", async () => {
    stubLocation("/login", "?next=%2Fsettings%2Fleads");
    mockFetch401();
    await expect(get("/api/v1/me")).rejects.toBeInstanceOf(ApiError);
    // Unchanged: a second bounce would drop the `next` this page is trying to honour.
    expect(window.location.pathname).toBe("/login");
    expect(window.location.search).toBe("?next=%2Fsettings%2Fleads");
  });
});
