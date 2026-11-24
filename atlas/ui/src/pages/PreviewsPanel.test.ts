/**
 * Previews panel: static markup (no jsdom), like Agency.test.ts. The frame is
 * sandboxed without same-origin, an unreadable list is UNKNOWN (not empty),
 * and a read time is always shown.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { PreviewsList } from "../api";
import { PHONE_PX, PreviewsView, frameWidth, previewSrc } from "./PreviewsPanel";

const AT = "2026-10-30T12:00:00+00:00";
const data: PreviewsList = {
  read_at: AT,
  previews: [
    { lead_id: 70, name: "The Cutting Edge Barbershop", slug: "the-cutting-edge-barbershop-70", built: true,
      src: "/api/agency/preview-t/1.ab/the-cutting-edge-barbershop-70/" },
    { lead_id: 63, name: "Elite Barbers NYC", slug: "elite-barbers-nyc-63", built: false },
  ],
};
const html = (d: PreviewsList | null, e: string | null): string =>
  renderToStaticMarkup(createElement(PreviewsView, { data: d, error: e, at: AT }));

describe("PreviewsPanel", () => {
  it("frames each built demo in a script-only sandbox", () => {
    const h = html(data, null);
    expect(h).toContain('sandbox="allow-scripts allow-top-navigation-to-custom-protocols"');
    expect(h).not.toContain("allow-same-origin");
    expect(h).not.toContain("allow-top-navigation ");
    expect(h).toContain('src="/api/agency/preview-t/1.ab/the-cutting-edge-barbershop-70/"');
    expect(h).toContain("The Cutting Edge Barbershop");
    expect(h).toContain("read ");
  });
  it("shows an unbuilt demo as Not built, with no frame", () => {
    const h = html(data, null);
    expect(h).toContain("Not built");
    expect(h.match(/<iframe/g)).toHaveLength(1);
  });
  it("renders UNKNOWN, not an empty list, when the read failed", () => {
    const h = html(data, "503 the built sites could not be read");
    expect(h).toContain('data-health="unknown"');
    expect(h).toContain("Unknown");
    expect(h).not.toContain("<iframe");
    expect(h).not.toContain("No demo site");
    expect(h).toContain("not read yet");
  });
  it("widths and src", () => {
    expect(frameWidth("phone")).toBe(`${PHONE_PX}px`);
    expect(frameWidth("desktop")).toBe("100%");
    expect(previewSrc("a-1")).toBe("/api/agency/preview/a-1/");
  });
});
