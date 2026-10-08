// The one line under the score saying why it moved. The score has always been
// a number, a grade and a trend line, none of which answer the question an
// owner asks when it drops: what happened?
//
// The rule that matters most here is the negative one: when there is no scan
// old enough to compare against, the payload sends null and the hero renders
// NOTHING. A delta of zero nobody measured is not a fact.
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { VerdictHero } from "../src/components/VerdictHero";
import type { OverviewPayload, ScoreChange } from "../src/types";
import { NO_HERO } from "./overviewHero";

// Built here rather than imported from another test file, whose module-level
// mocks would come with it.
const BASE: Omit<OverviewPayload, keyof typeof NO_HERO> = {
  ts: "2026-09-14T10:00:00-04:00",
  age_s: 0,
  score: 83,
  grade: "B",
  potential: 98,
  score_model: "",
  score_coverage: null,
  verdict: "Mostly fine.",
  next_action: null,
  deductions: [],
  score_change: null,
  strip: [],
  timeline: [],
  score_trend: { name: "score", points: [], color: null },
  questions: [],
  alerts: { crit: 0, warn: 0, info: 0, open: 0, acked: 0, snoozed: 0 },
  devices: { total: 0, online: 0, sleeping: 0, unverified: 0, quarantined: 0 },
  sensor: null,
  networks: [],
  chat_example_ip: "",
  voice: false,
  needs_you: [],
  fixed_without_you: { days: 14, automatic: 0, approved: 0, daily: [] },
  coverage: [],
  certs_expiring: [],
};

function renderHero(score_change: ScoreChange | null) {
  const payload: OverviewPayload = { ...BASE, ...NO_HERO, score_change };
  return render(
    <MemoryRouter>
      <VerdictHero payload={payload} onAction={() => {}} busy={false} />
    </MemoryRouter>,
  );
}

const CHANGE: ScoreChange = {
  delta: -4,
  was: 87,
  since: "2026-09-13T10:00:00+00:00",
  hours: 24,
  appeared: [{ category: "Exposure", points: 4, why: "extra internet ports on safehome", key: "" }],
  cleared: [],
  changed: [],
  summary: "Down 4 since yesterday — extra internet ports on safehome is new.",
  last_moved: null,
};

describe("the score-change line", () => {
  it("renders nothing at all when there is nothing comparable", () => {
    renderHero(null);
    expect(screen.queryByTestId("score-change")).not.toBeInTheDocument();
  });

  it("explains a drop, naming the finding that caused it", () => {
    renderHero(CHANGE);
    expect(screen.getByTestId("score-change")).toHaveTextContent(
      "Down 4 since yesterday — extra internet ports on safehome is new.",
    );
  });

  it("reads as a problem when the score fell and as progress when it rose", () => {
    const { unmount } = renderHero(CHANGE);
    expect(screen.getByTestId("score-change").className).toContain("g-tone-alert");
    unmount();

    renderHero({ ...CHANGE, delta: 6, summary: "Up 6 since yesterday — 3 unverified devices is gone." });
    expect(screen.getByTestId("score-change").className).toContain("g-tone-good");
  });

  it("stays neutral on an unchanged day rather than reading as good news", () => {
    renderHero({ ...CHANGE, delta: 0, appeared: [], summary: "Unchanged since yesterday." });
    const line = screen.getByTestId("score-change");
    expect(line).toHaveTextContent("Unchanged since yesterday.");
    expect(line.className).toContain("g-tone-plain");
  });
});
