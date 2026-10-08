import { beforeEach, describe, expect, it, vi } from "vitest";
import { get } from "../src/lib/api";
import { forgetNetwork, readSelectedNetwork, setSelectedNetwork } from "../src/lib/network";

// A network deleted inside its own tenant used to leave its id in that
// tenant's localStorage key. Every scoped read then 404'd, the page showed a
// Retry that could never succeed, and the only cure was clearing site data —
// which is not something the people this product is for can be asked to do.

const TENANT = "t1";
const DEAD = "dead-network-id";
const LIVE = "live-network-id";

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 404 ? "Not Found" : "OK",
    json: async () => body,
  } as unknown as Response;
}

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("a scoped read whose network no longer exists", () => {
  it("drops the dead selection and retries unscoped", async () => {
    setSelectedNetwork(TENANT, DEAD);
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      calls.push(url);
      if (url === "/api/v1/networks") return jsonResponse({ networks: [{ id: LIVE }] });
      if (url.includes("network=")) return jsonResponse({ detail: "not found" }, 404);
      return jsonResponse({ ok: true });
    }));

    await expect(get("/api/v1/security?network=" + DEAD)).resolves.toEqual({ ok: true });
    // The retry carries no ?network= at all, so it cannot fail the same way.
    expect(calls.at(-1)).toBe("/api/v1/security");
    expect(readSelectedNetwork(TENANT)).toBeNull();
  });

  it("leaves a VALID selection alone when the 404 is the sub-resource", async () => {
    // The bug this guards against is the mirror image: silently clearing a
    // good selection because an unknown site name 404'd.
    setSelectedNetwork(TENANT, LIVE);
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url === "/api/v1/networks") return jsonResponse({ networks: [{ id: LIVE }] });
      return jsonResponse({ detail: "no such site" }, 404);
    }));

    await expect(get("/api/v1/security/sites/nope?network=" + LIVE)).rejects.toThrow();
    expect(readSelectedNetwork(TENANT)).toBe(LIVE);
  });

  it("does not retry an unscoped 404", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ detail: "gone" }, 404));
    vi.stubGlobal("fetch", fetchMock);
    await expect(get("/api/v1/security")).rejects.toThrow();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not silently retry a write", async () => {
    setSelectedNetwork(TENANT, DEAD);
    const fetchMock = vi.fn(async () => jsonResponse({ detail: "gone" }, 404));
    vi.stubGlobal("fetch", fetchMock);
    const { post } = await import("../src/lib/api");
    await expect(post("/api/v1/thing?network=" + DEAD, {})).rejects.toThrow();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("forgetNetwork", () => {
  it("clears the id under whichever tenant holds it, and only that one", () => {
    setSelectedNetwork("a", DEAD);
    setSelectedNetwork("b", LIVE);
    forgetNetwork(DEAD);
    expect(readSelectedNetwork("a")).toBeNull();
    expect(readSelectedNetwork("b")).toBe(LIVE);
  });

  it("is a no-op for an id nobody has selected", () => {
    setSelectedNetwork("a", LIVE);
    forgetNetwork(DEAD);
    expect(readSelectedNetwork("a")).toBe(LIVE);
  });
});
