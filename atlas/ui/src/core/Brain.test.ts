import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { Brain } from "./Brain";
import type { BrainRegion } from "./brain-layout";

/** Rendered to a string: no DOM needed, and what is asserted is exactly the
 *  markup he would see. `createElement` rather than JSX so this stays a
 *  `.test.ts` file inside vitest's existing include. */
function render(props: Partial<Parameters<typeof Brain>[0]>): string {
  return renderToStaticMarkup(createElement(Brain, {
    regions: null, recent: null, error: null,
    onAsk: () => undefined, asking: false, ...props,
  }));
}

const quiet: BrainRegion = {
  region: "SENSORY", namespaces: ["infra"], memories: 4,
  last_read_at: null, reads_1h: 0, firing: 0,
};

describe("Brain, when the source could not be read", () => {
  it("says UNKNOWN and why, never that nothing was recalled", () => {
    const html = render({ error: "502 Bad Gateway" });
    expect(html).toContain('data-state="unknown"');
    expect(html).toContain("UNKNOWN");
    expect(html).toContain("502 Bad Gateway");
    expect(html).toContain("the recall log could not be read");
    expect(html).not.toContain("loading…");
    expect(html).not.toContain("Nothing has been recalled");
    expect(html).not.toContain("<svg");
  });

  it("says UNKNOWN while it has not been read yet", () => {
    const html = render({});
    expect(html).toContain("UNKNOWN");
    expect(html).toContain("loading…");
    expect(html).not.toContain("could not be read");
    expect(html).not.toContain("Nothing has been recalled");
  });

  it("does not draw a stale read beside a failed refresh", () => {
    const html = render({ regions: [quiet], recent: [], error: "timeout" });
    expect(html).toContain("UNKNOWN");
    expect(html).not.toContain("<svg");
    expect(html).not.toContain("Nothing has been recalled");
  });
});

describe("Brain, read and empty", () => {
  it("says nothing was recalled only when the log was read and is empty", () => {
    const html = render({ regions: [quiet], recent: [] });
    expect(html).toContain("<svg");
    expect(html).toContain("Nothing has been recalled yet.");
    expect(html).not.toContain("UNKNOWN");
    expect(html).toContain("4 memories · quiet");
  });
});
