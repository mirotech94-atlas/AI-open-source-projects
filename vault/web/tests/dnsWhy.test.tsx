import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { QueryStream } from "../src/components/QueryStream";
import type { DnsStreamRow } from "../src/types";

const row = (over: Partial<DnsStreamRow> = {}): DnsStreamRow => ({
  ts: "2026-09-22T10:00:00-04:00", mac: "aa:bb:cc:dd:ee:ff", client: "192.168.1.20",
  name: "ads.example.com", qtype: "A", blocked: true, list: "phishing-army", ms: 12,
  device: "laptop", network: null, ...over,
});

describe("the live query stream (item 36)", () => {
  it("lets you ask why a name was blocked", async () => {
    // The stream said THAT a name was blocked — naming the list in a pill —
    // and never why that mattered or what to do about it.
    const onSelectName = vi.fn();
    render(
      <QueryStream rows={[row()]} paused={false} onPause={() => {}} filter="" onFilter={() => {}}
                   onSelectName={onSelectName} />,
    );
    // The button's accessible name is its text (the domain); the question
    // lives in the tooltip, which is what a mouse user sees on hover.
    const nameButton = screen.getByRole("button", { name: "ads.example.com" });
    expect(nameButton).toHaveAttribute("title", "Why was ads.example.com blocked?");
    await userEvent.click(nameButton);
    expect(onSelectName).toHaveBeenCalledWith(expect.objectContaining({ name: "ads.example.com" }));
  });

  it("asks the same question of a name that was allowed", async () => {
    const onSelectName = vi.fn();
    render(
      <QueryStream rows={[row({ blocked: false, list: null, name: "cdn.example.com" })]}
                   paused={false} onPause={() => {}} filter="" onFilter={() => {}}
                   onSelectName={onSelectName} />,
    );
    const nameButton = screen.getByRole("button", { name: "cdn.example.com" });
    expect(nameButton).toHaveAttribute("title", "Why was cdn.example.com allowed?");
    await userEvent.click(nameButton);
    expect(onSelectName).toHaveBeenCalled();
  });

  it("is inert when no handler is wired, rather than a dead-looking control", () => {
    render(
      <QueryStream rows={[row()]} paused={false} onPause={() => {}} filter="" onFilter={() => {}} />,
    );
    expect(screen.getByRole("button", { name: "ads.example.com" })).toBeDisabled();
  });
});
