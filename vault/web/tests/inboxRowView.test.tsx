// F6: a viewer used to see the alert row's action buttons (Ack/Snooze/
// Resolve/Reopen/Fix) removed entirely, with no clue any of them existed —
// the header's own controls (Scan now, speed test, maintenance) get this
// right: they render disabled with a title naming the role missing. Row
// actions must follow the same rule.
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { InboxRowView } from "../src/components/InboxRowView";
import type { AlertRemedy, ConfidenceFactor, InboxRow, RowAction } from "../src/types";

function action(kind: string, label: string, tier: number): RowAction {
  return { kind, label, method: "POST", path: `/api/v1/alerts/x/${kind}`, body: {}, tier };
}

function alertRow(actions: RowAction[]): InboxRow {
  return {
    id: "alert:1", view: "alerts", sev: "WARN", title: "Something happened",
    entity: "safehome", why: "", what_changed: "", status: "open",
    first_seen: "", last_seen: "", count: 1, actions, children: [],
    ticket_key: "", confidence: null, network: null, remedy: null, drift: null,
  };
}

const ACTIONS = [
  action("ack", "Ack", 2),
  action("snooze", "Snooze", 2),
  action("resolve", "Resolve", 2),
  action("reopen", "Reopen", 2),
];

function renderRow(role: "viewer" | "operator" | "admin" | null) {
  return render(
    <ul>
      <InboxRowView
        row={alertRow(ACTIONS)}
        role={role}
        isCursor={false}
        isSelected={false}
        isExpanded={false}
        pending={false}
        onToggleExpand={() => {}}
        onToggleSelect={() => {}}
        onRunAction={() => {}}
        onRecDecision={() => {}}
      />
    </ul>,
  );
}

describe("InboxRowView action gating (F6)", () => {
  it("renders every action for an operator, enabled", () => {
    renderRow("operator");
    for (const a of ACTIONS) {
      const btn = screen.getByRole("button", { name: a.label });
      expect(btn).toBeEnabled();
    }
  });

  it("still renders every action for a viewer, but disabled with the role it needs — never removed", () => {
    renderRow("viewer");
    for (const a of ACTIONS) {
      const btn = screen.getByRole("button", { name: a.label });
      expect(btn).toBeDisabled();
      expect(btn).toHaveAttribute("title", `${a.label} — needs the operator role`);
    }
  });

  it("disables the same way while role is still loading (null)", () => {
    renderRow(null);
    for (const a of ACTIONS) {
      expect(screen.getByRole("button", { name: a.label })).toBeDisabled();
    }
  });
});

// ---------------------------------------------------------------------------
// The confidence rubric's working (`hub/guardian_hub/confidence.py`).
//
// The division under test is the one the owner chose, and it is not cosmetic:
// the SENTENCE goes on the row, the NUMBER and all six factors go only in the
// expanded region. The row already prints `InboxRow.confidence` as
// "High / Moderate / Limited evidence" — the sensor's confidence that the
// finding is real, a different quantity wearing the same word — so a second
// bare percentage beside it would read as one number contradicting itself.
// These tests pin the split rather than the styling, because the split is the
// decision; anything that moves the number onto the collapsed row fails here
// with the reason in this comment.

/** The six factors `confidence.assess` always returns, with the one that found
 *  nothing carrying `weight: 0` — the case the drawer exists for ("what was
 *  considered and found absent rather than only what fired"). Names and
 *  weights follow the real module so a reader can check them against it. */
const FACTORS: ConfidenceFactor[] = [
  { name: "host_history", question: "Has Guardian run install security updates on clinic-nas successfully before?", answer: "yes, 4 times", weight: 30 },
  { name: "held", question: "Did the last successful install security updates on clinic-nas make the alert stop firing?", answer: "not seen again since that repair", weight: 10 },
  { name: "verified", question: "Did later evidence confirm those repairs worked?", answer: "4 of 4 confirmed", weight: 15 },
  { name: "undone", question: "Has a person reopened sec:secupd:clinic-nas after Guardian called it resolved?", answer: "never reopened by a person", weight: 0 },
  { name: "reversible", question: "Is install security updates reversible?", answer: "yes", weight: 10 },
  { name: "sensor_fresh", question: "Is the sensor reporting often enough to confirm the result (within 30 min)?", answer: "last scan 3 min ago", weight: 5 },
];

function scoredRemedy(overrides: Partial<AlertRemedy> = {}): AlertRemedy {
  return {
    kind: "auto", action: "security_updates", host: "clinic-nas", arg: "", tier: 2,
    tool: "apply_fix", min_role: "admin", can_run_now: true,
    summary: "I can fix this myself: install security updates on clinic-nas.",
    need: "", impact: "", opening: "",
    confidence: 78,
    confidence_why: "Guardian has done this here 4 times, each confirmed afterwards.",
    confidence_factors: FACTORS,
    ...overrides,
  };
}

function renderScored(rem: AlertRemedy, isExpanded: boolean) {
  return render(
    <ul>
      <InboxRowView
        row={{ ...alertRow([]), remedy: rem }}
        role="admin"
        isCursor={false}
        isSelected={false}
        isExpanded={isExpanded}
        pending={false}
        onToggleExpand={() => {}}
        onToggleSelect={() => {}}
        onRunAction={() => {}}
        onRecDecision={() => {}}
      />
    </ul>,
  );
}

describe("InboxRowView confidence", () => {
  it("prints the explanation sentence on the collapsed row", () => {
    renderScored(scoredRemedy(), false);
    expect(screen.getByTestId("inbox-confidence-why")).toHaveTextContent(
      "Guardian has done this here 4 times, each confirmed afterwards.",
    );
  });

  it("keeps the 0-100 number out of the collapsed row and shows it in the drawer", () => {
    // Two renders rather than a click, so this measures what `isExpanded`
    // controls and not whether the parent's toggle is wired — that plumbing
    // has its own page-level test in `inbox.test.tsx`.
    const collapsed = renderScored(scoredRemedy(), false);
    expect(collapsed.queryByTestId("inbox-score")).toBeNull();
    // Not `queryByText("78")`: this has to fail if the number reaches the
    // collapsed row in ANY wording, so it looks for the digits anywhere in the
    // row's text rather than in one node.
    expect(collapsed.container.textContent).not.toContain("78");
    collapsed.unmount();

    renderScored(scoredRemedy(), true);
    expect(screen.getByTestId("inbox-score-value")).toHaveTextContent("78 / 100");
  });

  it("shows all six factors in the drawer, including the one that found nothing", () => {
    renderScored(scoredRemedy(), true);
    expect(screen.getAllByTestId("inbox-score-factor")).toHaveLength(6);
    for (const f of FACTORS) {
      expect(screen.getByText(f.question)).toBeInTheDocument();
      expect(screen.getByText(f.answer)).toBeInTheDocument();
    }
    // The zero is present, printed bare rather than as "+0", and is the one
    // drawn quiet: a factor that found nothing is worth seeing and must not be
    // the loudest mark in the table. Asserted on the class because that
    // recession is weight and muting, never a colour of its own.
    const weights = screen.getAllByTestId("inbox-score-weight");
    expect(weights.map((n) => n.textContent)).toEqual(["+30", "+10", "+15", "0", "+10", "+5"]);
    expect(weights[3].className).toContain("is-zero");
    expect(weights[0].className).not.toContain("is-zero");
  });

  it("says the number is a rubric rather than a prediction", () => {
    // The design requires this in the UI copy, not only in a docstring: the
    // weights were chosen by a person and are meant to be argued with, and a
    // bare score above six factors looks exactly like something fitted.
    renderScored(scoredRemedy(), true);
    expect(screen.getByTestId("inbox-score")).toHaveTextContent(/rubric/i);
    expect(screen.getByTestId("inbox-score")).toHaveTextContent(/not a prediction/i);
  });

  it("gives a row that already has a title toggle no second button for the same flag", () => {
    // An incidents-view header has children AND a remedy of its own
    // (`payloads/inbox.py` sets `remedy=lead_remedy`), and both expanded
    // regions read the one `expanded` flag. Sharing the flag is right --
    // expanding a row shows everything it has. A second BUTTON over it is
    // not: each would claim a different region through `aria-controls` while
    // either one opened both, so a screen-reader user is told about two
    // independent disclosures and gets one.
    const withChildren: InboxRow = {
      ...alertRow([]),
      children: [alertRow([])],
      remedy: scoredRemedy(),
    };
    const { queryByTestId, rerender } = render(
      <ul>
        <InboxRowView
          row={withChildren} role="admin" isCursor={false} isSelected={false}
          isExpanded={false} pending={false}
          onToggleExpand={() => {}} onToggleSelect={() => {}}
          onRunAction={() => {}} onRecDecision={() => {}}
        />
      </ul>,
    );
    expect(queryByTestId("inbox-score-toggle")).toBeNull();
    expect(queryByTestId("inbox-score-value")).toBeNull();

    rerender(
      <ul>
        <InboxRowView
          row={withChildren} role="admin" isCursor={false} isSelected={false}
          isExpanded={true} pending={false}
          onToggleExpand={() => {}} onToggleSelect={() => {}}
          onRunAction={() => {}} onRecDecision={() => {}}
        />
      </ul>,
    );
    // Expanded it still offers no button, but the heading and the score are
    // there: the title toggle already revealed them.
    expect(queryByTestId("inbox-score-toggle")).toBeNull();
    expect(queryByTestId("inbox-score-value")).toHaveTextContent("78 / 100");
    expect(queryByTestId("inbox-score")).toHaveTextContent(/rubric/i);
  });

  it("renders nothing at all for a remedy with no factors — never an empty table", () => {
    // An empty list is a real answer: either nothing scored this row, or
    // hub-side scoring fell open (`remedy._apply_confidence` leaves the
    // decision untouched, with no partial list). A table with no rows would
    // say Vault considered nothing, and the control that opens it would be
    // an invitation to see something that is not there.
    const bare = scoredRemedy({ confidence: 0, confidence_why: "", confidence_factors: [] });
    const { queryByTestId, container } = renderScored(bare, true);
    expect(queryByTestId("inbox-score")).toBeNull();
    expect(queryByTestId("inbox-score-toggle")).toBeNull();
    expect(queryByTestId("inbox-confidence-why")).toBeNull();
    expect(container.textContent).toContain("I can fix this myself");
  });
});
