import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ValueStrip } from "../src/components/ValueStrip";
import type { ValueStrip as Data } from "../src/types";

// The most flattering surface in the product. The notes are the feature:
// they are where a number that could be over-read gets corrected.

const DATA: Data = {
  days: 30,
  stats: [
    { key: "actions", label: "Actions taken", value: "47", note: "repairs that succeeded in 30 days" },
    { key: "alone", label: "Handled alone", value: "89%", note: "42 of 47 needed no decision from you" },
    { key: "runtime", label: "Median repair time", value: "90s", note: "how long a repair took once it started" },
    { key: "decisions", label: "Decisions you made", value: "5", note: "times Vault asked before acting, in 30 days" },
  ],
  work: [
    { label: "Security updates", count: 30 },
    { label: "Service restarts", count: 12 },
    { label: "Disk and log cleanup", count: 5 },
  ],
};

describe("value strip", () => {
  it("shows each number with the note that qualifies it", () => {
    render(<ValueStrip data={DATA} />);
    expect(screen.getByText("47")).toBeInTheDocument();
    expect(screen.getByText("89%")).toBeInTheDocument();
    expect(screen.getByText(/42 of 47/)).toBeInTheDocument();
  });

  it("never lets the repair time read as how long a problem lasted", () => {
    render(<ValueStrip data={DATA} />);
    expect(screen.getByText(/once it started/)).toBeInTheDocument();
    expect(document.body.textContent ?? "").not.toMatch(/time to resolve|problem lasted|downtime/i);
  });

  it("claims no invented time saved", () => {
    render(<ValueStrip data={DATA} />);
    expect(document.body.textContent ?? "").not.toMatch(/saved|time spent/i);
  });

  it("groups the work by outcome", () => {
    render(<ValueStrip data={DATA} />);
    expect(screen.getByText("Security updates")).toBeInTheDocument();
    expect(screen.getByText("Disk and log cleanup")).toBeInTheDocument();
  });

  it("hides the breakdown rather than drawing an empty chart", () => {
    render(<ValueStrip data={{ days: 30, stats: DATA.stats, work: [] }} />);
    expect(screen.queryByText(/Where the work went/)).toBeNull();
  });
});
