import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PatchCalendar } from "../src/components/PatchCalendar";
import type { PatchDay } from "../src/types";

const day = (over: Partial<PatchDay>): PatchDay => ({
  day: "2026-09-20", host: "vps1", patches: 0, reboots: 0, state: "none", ...over,
});

describe("the patch calendar's colours", () => {
  it("gives each of its own four states a distinct tone", () => {
    // It used to ask the shared state->tone map, which knows ok/down/stale
    // but not patched/reboot/none — so three of four fell through to plain
    // and a patched day looked exactly like a day nothing happened.
    const { container } = render(
      <PatchCalendar calendar={[
        day({ day: "2026-09-18", state: "patched", patches: 3 }),
        day({ day: "2026-09-19", state: "reboot", reboots: 1 }),
        day({ day: "2026-09-20", state: "failed" }),
        day({ day: "2026-09-21", state: "none" }),
      ]} />,
    );
    const tones = [...container.querySelectorAll<HTMLElement>(".g-patchcal-cell")].map(
      (c) => [c.dataset.state, [...c.classList].find((n) => n.startsWith("g-tone-"))],
    );
    expect(tones).toEqual([
      ["patched", "g-tone-good"],
      ["reboot", "g-tone-warn"],
      ["failed", "g-tone-alert"],
      ["none", "g-tone-plain"],
    ]);
    expect(new Set(tones.map((t) => t[1])).size).toBe(4);
  });

  it("labels what the colours mean", () => {
    render(<PatchCalendar calendar={[day({ state: "patched", patches: 1 })]} />);
    const key = screen.getByLabelText("What the colours mean");
    expect(key).toHaveTextContent("Patched");
    expect(key).toHaveTextContent("Failed");
    expect(key).toHaveTextContent("Nothing that day");
  });
});
