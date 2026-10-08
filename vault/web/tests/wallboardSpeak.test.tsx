/* The wallboard's speaker.
 *
 * Default-off is the assertion that matters: a wall display that starts
 * talking on its own the first time somebody opens it is one that gets
 * muted at the OS and then never heard again, which is worse than silence
 * because the owner believes it is armed. */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { Wallboard } from "../src/shell/Wallboard";

const base = {
  overview: null, overviewFetchedAt: null, live: null, rows: [],
  maintenanceUntil: null, onClose: () => {},
};

it("offers no speaker when the hub cannot speak", () => {
  render(<Wallboard {...base} voice={false} speakOn={false} onToggleSpeak={vi.fn()} />);
  expect(screen.queryByRole("button", { name: /speak new criticals/i })).toBeNull();
});

it("starts silent", () => {
  render(<Wallboard {...base} voice speakOn={false} onToggleSpeak={vi.fn()} />);
  expect(screen.getByRole("button", { name: /speak new criticals/i })
    .getAttribute("aria-pressed")).toBe("false");
});

it("asks the shell to turn it on rather than deciding for itself", async () => {
  const onToggleSpeak = vi.fn();
  render(<Wallboard {...base} voice speakOn={false} onToggleSpeak={onToggleSpeak} />);
  await userEvent.click(screen.getByRole("button", { name: /speak new criticals/i }));
  expect(onToggleSpeak).toHaveBeenCalled();
});

// The dialog's Tab guard was written when the close button was its only
// focusable control ("trapping focus is exactly Tab never leaves it"). Adding
// the speaker button without touching that guard would make it render but be
// unreachable by keyboard -- present to the eye, invisible to Tab, which is
// the same failure axe cannot see because it checks roles, not tab order.
it("cycles Tab between the speaker and the close button rather than trapping on one", async () => {
  render(<Wallboard {...base} voice speakOn={false} onToggleSpeak={vi.fn()} />);
  const close = screen.getByRole("button", { name: /close wallboard/i });
  const speaker = screen.getByRole("button", { name: /speak new criticals/i });
  await waitFor(() => expect(close).toHaveFocus());

  await userEvent.keyboard("{Tab}");
  expect(speaker).toHaveFocus();

  await userEvent.keyboard("{Tab}");
  expect(close).toHaveFocus();

  await userEvent.keyboard("{Shift>}{Tab}{/Shift}");
  expect(speaker).toHaveFocus();
});
