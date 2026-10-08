import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { NeedsYou } from "../src/components/NeedsYou";
import type { FixedWithoutYou, NeedsYouItem } from "../src/types";

// The Overview's first three seconds. Two failure modes matter more than
// looks: claiming safety that cannot be backed, and turning a tier-3
// approval into a one-click repair.

const FIXED: FixedWithoutYou = { days: 14, automatic: 412, approved: 6, daily: [] };

function item(over: Partial<NeedsYouItem> = {}): NeedsYouItem {
  return {
    key: "sec:ufw:vps1", title: "Firewall is off on vps1", severity: "WARN",
    detail: "I would enable ufw and allow 22 plus the ports already listening.",
    cost: "Existing connections stay up; nothing reboots.",
    action: "enable_ufw", host: "vps1", ...over,
  };
}

describe("needs you", () => {
  it("leads with the decisions, and says how many", () => {
    render(<NeedsYou items={[item(), item({ key: "b", title: "Second" })]} fixed={FIXED}
                     onApprove={() => {}} onDismiss={() => {}} />);
    expect(screen.getByText("Needs you")).toBeInTheDocument();
    expect(screen.getByText("2 waiting")).toBeInTheDocument();
  });

  it("never says 'you are protected' when the queue is empty", () => {
    render(<NeedsYou items={[]} fixed={FIXED} onApprove={() => {}} onDismiss={() => {}} />);
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/protected|secure|you are safe/i);
    expect(screen.getByText(/Nothing needs you/)).toBeInTheDocument();
  });

  it("reports what it handled alone rather than a bare total", () => {
    render(<NeedsYou items={[]} fixed={FIXED} onApprove={() => {}} onDismiss={() => {}} />);
    expect(screen.getByText(/412/)).toBeInTheDocument();
    expect(screen.getByText(/6 with your approval/)).toBeInTheDocument();
  });

  it("an empty history is not dressed up as a clean record", () => {
    render(<NeedsYou items={[]} fixed={{ days: 14, automatic: 0, approved: 0, daily: [] }}
                     onApprove={() => {}} onDismiss={() => {}} />);
    expect(screen.getByText(/Nothing has needed fixing/)).toBeInTheDocument();
  });

  it("shows what saying yes costs, not only what it does", () => {
    render(<NeedsYou items={[item()]} fixed={FIXED} onApprove={() => {}} onDismiss={() => {}} />);
    expect(screen.getByText(/I would enable ufw/)).toBeInTheDocument();
    expect(screen.getByText(/nothing reboots/)).toBeInTheDocument();
  });

  it("Approve asks the parent rather than acting on its own", async () => {
    const onApprove = vi.fn();
    render(<NeedsYou items={[item()]} fixed={FIXED} onApprove={onApprove} onDismiss={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(onApprove).toHaveBeenCalledOnce();
  });

  it("a row awaiting confirmation demands a second, different yes", () => {
    // The scar this guards: "a plain button that rebooted a host on one
    // click". A repeated "Approve" would be confirmed by reflex.
    render(<NeedsYou items={[item()]} fixed={FIXED} onApprove={() => {}} onDismiss={() => {}}
                     pendingKey="sec:ufw:vps1" />);
    expect(screen.getByRole("button", { name: "Yes, do it" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  });

  it("only the pending row changes its button", () => {
    render(<NeedsYou items={[item(), item({ key: "other", title: "Other" })]} fixed={FIXED}
                     onApprove={() => {}} onDismiss={() => {}} pendingKey="sec:ufw:vps1" />);
    expect(screen.getByRole("button", { name: "Yes, do it" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
  });

  it("Not now is offered on every row", async () => {
    const onDismiss = vi.fn();
    render(<NeedsYou items={[item()]} fixed={FIXED} onApprove={() => {}} onDismiss={onDismiss} />);
    await userEvent.click(screen.getByRole("button", { name: "Not now" }));
    expect(onDismiss).toHaveBeenCalledOnce();
  });
});

describe("fixed without you", () => {
  const days = (n: number) =>
    Array.from({ length: n }, (_, i) => ({
      day: `2026-09-${String(i + 1).padStart(2, "0")}`,
      automatic: i === 0 ? 5 : 0,
      approved: i === 0 ? 1 : 0,
    }));

  it("draws a column for every day, including the quiet ones", () => {
    // A closed-up chart would make a quiet fortnight look busy.
    render(
      <NeedsYou items={[]} fixed={{ days: 14, automatic: 5, approved: 1, daily: days(14) }}
                onApprove={() => {}} onDismiss={() => {}} />,
    );
    expect(document.querySelectorAll(".g-fixgrid-cells li")).toHaveLength(14);
  });

  it("keeps the split visible in the key", () => {
    render(
      <NeedsYou items={[]} fixed={{ days: 14, automatic: 412, approved: 31, daily: days(14) }}
                onApprove={() => {}} onDismiss={() => {}} />,
    );
    expect(screen.getByText(/Automatic 412/)).toBeInTheDocument();
    expect(screen.getByText(/You approved 31/)).toBeInTheDocument();
  });

  it("draws nothing rather than an empty axis when there is no history", () => {
    render(
      <NeedsYou items={[]} fixed={{ days: 14, automatic: 0, approved: 0, daily: [] }}
                onApprove={() => {}} onDismiss={() => {}} />,
    );
    expect(document.querySelector(".g-fixgrid")).toBeNull();
  });
});
