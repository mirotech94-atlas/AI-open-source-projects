import { render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import Login from "../src/pages/Login";

vi.mock("../src/api", async () => {
  const actual = await vi.importActual<typeof import("../src/api")>("../src/api");
  return { ...actual, authStart: vi.fn().mockResolvedValue({ sent: true }), authVerify: vi.fn() };
});
import { ApiError, authStart } from "../src/api";

afterEach(() => vi.clearAllMocks());

describe("Login with ?u=", () => {
  it("pre-fills the username, asks for the code once, and lands on the code step", async () => {
    render(
      <MemoryRouter initialEntries={["/login?u=dana%40example.com"]}>
        <Login />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText(/We emailed a code to dana@example.com/)).toBeInTheDocument());
    expect(vi.mocked(authStart)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(authStart)).toHaveBeenCalledWith("dana@example.com");
    expect(screen.getByLabelText("6-digit code")).toBeInTheDocument();
  });

  it("does nothing special without ?u=", () => {
    render(
      <MemoryRouter initialEntries={["/login"]}>
        <Login />
      </MemoryRouter>,
    );
    expect(screen.getByLabelText("Username")).toHaveValue("");
    expect(vi.mocked(authStart)).not.toHaveBeenCalled();
  });

  it("calls authStart exactly once under StrictMode's double-invoked effects", async () => {
    render(
      <StrictMode>
        <MemoryRouter initialEntries={["/login?u=dana%40example.com"]}>
          <Login />
        </MemoryRouter>
      </StrictMode>,
    );
    await waitFor(() => expect(screen.getByText(/We emailed a code to dana@example.com/)).toBeInTheDocument());
    expect(vi.mocked(authStart)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(authStart)).toHaveBeenCalledWith("dana@example.com");
  });

  it("stays on the username step and shows the API error when authStart fails with an ApiError", async () => {
    vi.mocked(authStart).mockRejectedValueOnce(new ApiError(429, "too many requests, try again later"));
    render(
      <MemoryRouter initialEntries={["/login?u=dana%40example.com"]}>
        <Login />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText("too many requests, try again later")).toBeInTheDocument());
    expect(screen.getByLabelText("Username")).toHaveValue("dana@example.com");
    expect(screen.queryByLabelText("6-digit code")).not.toBeInTheDocument();
  });

  it("shows the generic error when authStart fails with a non-ApiError", async () => {
    vi.mocked(authStart).mockRejectedValueOnce(new Error("boom"));
    render(
      <MemoryRouter initialEntries={["/login?u=dana%40example.com"]}>
        <Login />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText("Could not send the code. Try again.")).toBeInTheDocument());
    expect(screen.getByLabelText("Username")).toHaveValue("dana@example.com");
    expect(screen.queryByLabelText("6-digit code")).not.toBeInTheDocument();
  });
});
