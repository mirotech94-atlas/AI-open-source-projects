import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { Tile } from "../components/Tile";
import { current, llmTile, memoryTile, regionsTile, type TileWords } from "./core-tiles";
import type { BrainRegion } from "./brain-layout";

const llm = { vendors: ["anthropic", "ollama"], needs: { reason: { connected: true } } };
const mem = { total: 412 };
const region: BrainRegion = {
  region: "SENSORY", namespaces: ["infra"], memories: 4,
  last_read_at: null, reads_1h: 3, firing: 1,
};

/** The markup he would see, including the Link the Memory tile renders. */
function tile(words: TileWords, to?: string): string {
  return renderToStaticMarkup(createElement(MemoryRouter, null, createElement(Tile, {
    glyph: "◈", name: "T", readAt: new Date(2026, 8, 24, 10, 0), ...words,
    ...(to ? { to } : {}),
  })));
}

describe("current", () => {
  it("is the data only when the latest read succeeded", () => {
    expect(current({ data: 1, error: null })).toBe(1);
    expect(current({ data: 1, error: "502" })).toBeNull();
    expect(current({ data: null, error: null })).toBeNull();
  });
});

describe("a stale read behind a failed refresh is UNKNOWN, never live", () => {
  it.each([
    ["LLM", llmTile({ data: llm, error: "502 Bad Gateway" }), "2 connected"],
    ["Memory", memoryTile({ data: mem, error: "502 Bad Gateway" }), "412 stored"],
    ["Regions", regionsTile({ data: { regions: [region] }, error: "502 Bad Gateway" }),
     "1/1 firing"],
  ])("%s", (_name, words, stale) => {
    expect(words.state).toBe("unknown");
    expect(words.value).toBe("unreadable");
    expect(words.note).toContain("could not be read: 502 Bad Gateway");

    const html = tile(words, "/memory");
    expect(html).toContain('data-state="unknown"');
    expect(html).toContain("unreadable");
    expect(html).not.toContain(stale);
  });
});

describe("before the first read", () => {
  it.each([
    ["LLM", llmTile({ data: null, error: null })],
    ["Memory", memoryTile({ data: null, error: null })],
    ["Regions", regionsTile({ data: null, error: null })],
  ])("%s says not read yet, not could-not-be-read", (_name, words) => {
    expect(words).toEqual({ state: "unknown", value: "unreadable", note: "not read yet" });
  });
});

describe("a fresh read", () => {
  it("lights each tile with the figure", () => {
    expect(llmTile({ data: llm, error: null }))
      .toEqual({ state: "live", value: "2 connected", note: "anthropic · ollama" });
    expect(memoryTile({ data: mem, error: null }).value).toBe("412 stored");
    expect(regionsTile({ data: { regions: [region] }, error: null }))
      .toMatchObject({ state: "live", value: "1/1 firing" });
    expect(tile(memoryTile({ data: mem, error: null }))).toContain('data-state="live"');
  });

  it("a read LLM with no route is unknown, and says no route", () => {
    const words = llmTile({ data: { vendors: [], needs: { reason: { connected: false } } },
                            error: null });
    expect(words).toEqual({ state: "unknown", value: "no route", note: "no vendor is wired" });
  });
});
