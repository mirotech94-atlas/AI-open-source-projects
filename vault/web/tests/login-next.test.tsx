import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import Login from "../src/pages/Login";

vi.mock("../src/api", async () => {
  const actual = await vi.importActual<typeof import("../src/api")>("../src/api");
  return { ...actual, authStart: vi.fn().mockResolvedValue({ sent: true }), authVerify: vi.fn().mockResolvedValue({ ok: true }) };
});
import { authVerify } from "../src/api";

afterEach(() => vi.clearAllMocks());

// Prints the current route's pathname + search into a `data-testid="loc"`
// element, the same probe pattern `leads.test.tsx` uses, so a test can assert
// on where `navigate()` actually landed without reaching into router
// internals.
function LocationProbe() {
  const location = useLocation();
  return <div data-testid="loc">{location.pathname + location.search}</div>;
}

async function verifyWith(initialEntry: string) {
  render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Login />
      <LocationProbe />
    </MemoryRouter>,
  );
  await userEvent.type(screen.getByLabelText("Username"), "dana");
  await userEvent.click(screen.getByRole("button", { name: "Send code" }));
  await waitFor(() => expect(screen.getByLabelText("6-digit code")).toBeInTheDocument());
  await userEvent.type(screen.getByLabelText("6-digit code"), "123456");
  await userEvent.click(screen.getByRole("button", { name: "Verify" }));
  await waitFor(() => expect(vi.mocked(authVerify)).toHaveBeenCalledTimes(1));
}

describe("Login redirects to ?next= after verifying", () => {
  it("lands on the deep link's path and query", async () => {
    await verifyWith("/login?next=%2Fsettings%2Fleads%3Flead%3Dl2%26decide%3Dapprove");
    await waitFor(() =>
      expect(screen.getByTestId("loc")).toHaveTextContent("/settings/leads?lead=l2&decide=approve"),
    );
  });

  it("falls back to / for a protocol-relative next (//evil.example)", async () => {
    await verifyWith("/login?next=%2F%2Fevil.example");
    await waitFor(() => expect(screen.getByTestId("loc")).toHaveTextContent("/"));
  });

  it("falls back to / for an absolute next (https://evil.example)", async () => {
    await verifyWith("/login?next=https%3A%2F%2Fevil.example");
    await waitFor(() => expect(screen.getByTestId("loc")).toHaveTextContent("/"));
  });

  it("falls back to / when there is no next", async () => {
    await verifyWith("/login");
    await waitFor(() => expect(screen.getByTestId("loc")).toHaveTextContent("/"));
  });
});
