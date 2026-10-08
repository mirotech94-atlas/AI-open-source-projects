import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { useHotkeys } from "../src/lib/keys";

function Harness({ map }: { map: Record<string, (e: KeyboardEvent) => void> }) {
  useHotkeys(map);
  return <input aria-label="filter" />;
}

describe("useHotkeys", () => {
  it("fires on a document-level key and ignores keys typed into an input", async () => {
    const open = vi.fn();
    render(<Harness map={{ k: open, "mod+k": open }} />);

    await userEvent.keyboard("k");
    expect(open).toHaveBeenCalledTimes(1);

    await userEvent.click(screen.getByLabelText("filter"));
    await userEvent.keyboard("k");
    expect(open).toHaveBeenCalledTimes(1);
  });

  it("unbinds on unmount so a remounted tree does not double-fire", async () => {
    const hit = vi.fn();
    const { unmount } = render(<Harness map={{ j: hit }} />);
    await userEvent.keyboard("j");
    unmount();
    await userEvent.keyboard("j");
    expect(hit).toHaveBeenCalledTimes(1);
  });
});
