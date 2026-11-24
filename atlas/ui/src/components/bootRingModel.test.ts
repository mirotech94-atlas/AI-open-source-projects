import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { ModuleTile } from "../api";
import { overall } from "../pages/commandModel";
import { ringLook, ringState, tickPath } from "./bootRingModel";
import { BootRing } from "./BootRing";

const mod = (health: string, over: Partial<ModuleTile> = {}): ModuleTile =>
  ({ name: "m", enabled: true, setup: false, health, ...over } as unknown as ModuleTile);
const state = (mods: ModuleTile[] | null, failed = false) =>
  ringState(overall(mods), failed);

describe("ringState: health -> ring state", () => {
  it("is ok only when every watched module reads ok", () => {
    expect(state([mod("ok"), mod("ok")])).toBe("ok");
  });
  it("one unknown module is unknown, never ok", () => {
    expect(state([mod("ok"), mod("unknown")])).toBe("unknown");
  });
  it("nothing read, or a failed read, is unknown", () => {
    expect(state(null)).toBe("unknown");
    expect(state([])).toBe("unknown");
    expect(state([mod("ok")], true)).toBe("unknown");
  });
  it("degraded and failing win over unknown", () => {
    expect(state([mod("ok"), mod("degraded"), mod("unknown")])).toBe("degraded");
    expect(state([mod("ok"), mod("failing")])).toBe("failing");
  });
});

describe("ringLook", () => {
  it("ok is steady blue and turning", () => {
    const l = ringLook("ok", false);
    expect(l).toMatchObject({ tone: "brand", spinning: true, pulsing: true, lit: true,
                              dashed: false, segment: "none", word: "ONLINE" });
  });
  it("degraded keeps the blue ring and adds an amber segment", () => {
    expect(ringLook("degraded", false)).toMatchObject({ tone: "brand", segment: "amber" });
  });
  it("unknown is grey, dashed, unlit, not turning, with the word", () => {
    const l = ringLook("unknown", false);
    expect(l).toMatchObject({ tone: "grey", dashed: true, lit: false, spinning: false,
                              pulsing: false, segment: "none", word: "UNKNOWN" });
  });
  it("unknown never shares a single look field with ok", () => {
    const u = ringLook("unknown", false);
    const o = ringLook("ok", false);
    for (const k of ["tone", "dashed", "lit", "spinning", "pulsing", "word"] as const) {
      expect(u[k]).not.toBe(o[k]);
    }
  });
  it("reduced motion stops all turning and pulsing for every state", () => {
    for (const s of ["ok", "degraded", "failing", "unknown"] as const) {
      const l = ringLook(s, true);
      expect(l.spinning).toBe(false);
      expect(l.pulsing).toBe(false);
    }
  });
});

describe("tickPath", () => {
  it("draws the requested number of ticks", () => {
    const d = tickPath(200, 188, 6, 72);
    expect((d.match(/M/g) ?? []).length).toBe(72);
  });
});

const html = (p: Parameters<typeof BootRing>[0]): string =>
  renderToStaticMarkup(createElement(BootRing, p));

describe("BootRing render", () => {
  const base = { overview: { data: null, error: null, fetchedAt: null }, watchOnly: true } as const;
  it("reduced motion renders no animation or boot class", () => {
    const out = html({ ...base, reducedMotion: true, boot: true });
    expect(out).not.toMatch(/br-anim/);
    expect(out).not.toMatch(/br-boot/);
    expect(out).not.toMatch(/br-spin/);
  });
  it("shows ATLAS, UNKNOWN and not-read when nothing was read, and no OK look", () => {
    const out = html({ ...base, reducedMotion: false, boot: false });
    expect(out).toContain("ATLAS");
    expect(out).toContain("UNKNOWN");
    expect(out).toContain("br-s-unknown");
    expect(out).not.toContain("br-s-ok");
    expect(out).not.toContain("br-spin");
    expect(out).toContain("not read yet");
  });
  it("has an accessible label and the watch-only label", () => {
    const out = html({ ...base, reducedMotion: true, boot: false });
    expect(out).toMatch(/role="img"/);
    expect(out).toMatch(/aria-label="ATLAS status: unknown/i);
    expect(out).toContain("Watch-only");
  });
  it("plays the boot class only when asked and motion is allowed", () => {
    expect(html({ ...base, reducedMotion: false, boot: true })).toContain("br-boot");
  });
  it("names neither the film nor its character", () => {
    const out = html({ ...base, reducedMotion: false, boot: true });
    expect(out.toLowerCase()).not.toMatch(/jarvis|marvel|iron man/);
  });
});
