import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import ApiTokens from "../src/pages/ApiTokens";
import { ToastProvider } from "../src/design";
import type { MeOut } from "../src/api";

vi.mock("../src/lib/api", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/api")>("../src/lib/api");
  return { ...actual, get: vi.fn(), post: vi.fn(), del: vi.fn() };
});
import { del, get, post } from "../src/lib/api";

vi.mock("../src/api", async () => {
  const actual = await vi.importActual<typeof import("../src/api")>("../src/api");
  return { ...actual, me: vi.fn() };
});
import { me } from "../src/api";

afterEach(() => vi.clearAllMocks());

function meFixture(role: "viewer" | "operator" | "admin"): MeOut {
  return {
    user: { username: "miro", display_name: "Miro", is_superadmin: false },
    tenants: [{ id: "t1", slug: "home", name: "Home", role }],
    active_tenant: { id: "t1", slug: "home", name: "Home", role },
    chat_providers: [],
  };
}

const ROW = {
  id: "tok1",
  name: "Zapier",
  prefix: "gapi_9f2c41ab",
  scope: "write",
  role: "operator",
  created_by: "miro",
  created: "2026-09-01T10:00:00Z",
  expires: "2027-09-01T10:00:00Z",
  last_used: "2026-09-10T09:00:00Z",
  last_used_ip: "203.0.113.9",
  revoked: false,
};

const PAYLOAD = { tokens: [ROW], max_role: "admin", base_url: "https://api.guarder.io" };

function renderPage(): ReactElement {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter initialEntries={["/settings/api-tokens"]}>
          <ApiTokens />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>
  );
}

describe("ApiTokens", () => {
  it("lists tokens by their prefix, never a secret", async () => {
    vi.mocked(me).mockResolvedValue(meFixture("admin"));
    vi.mocked(get).mockResolvedValue(PAYLOAD);
    render(renderPage());

    await waitFor(() => expect(screen.getByText("Zapier")).toBeInTheDocument());
    expect(screen.getByText(/gapi_9f2c41ab/)).toBeInTheDocument();
    expect(screen.getByText("Read and write · operator")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
    // The reference link points at the API host the hub reported.
    expect(screen.getByRole("link", { name: /API reference/i })).toHaveAttribute(
      "href",
      "https://api.guarder.io/v1/docs",
    );
  });

  it("shows a freshly minted secret once, with a copy button", async () => {
    vi.mocked(me).mockResolvedValue(meFixture("admin"));
    vi.mocked(get).mockResolvedValue({ ...PAYLOAD, tokens: [] });
    vi.mocked(post).mockResolvedValue({
      token: { ...ROW, name: "CI", last_used: null },
      secret: "gapi_9f2c41ab_" + "a".repeat(48),
    });
    render(renderPage());

    await waitFor(() => expect(screen.getByLabelText(/Name/i)).toBeInTheDocument());
    await userEvent.type(screen.getByLabelText(/Name/i), "CI");
    await userEvent.click(screen.getByRole("button", { name: "Create token" }));

    await waitFor(() =>
      expect(screen.getByText(new RegExp("gapi_9f2c41ab_a{10}"))).toBeInTheDocument(),
    );
    expect(screen.getByText(/Copy this now/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy token" })).toBeInTheDocument();
    expect(vi.mocked(post)).toHaveBeenCalledWith("/api/v1/settings/api-tokens", {
      name: "CI",
      scope: "read",
      role: "viewer",
      days: 365,
    });
  });

  it("revoking asks first, then calls the hub", async () => {
    vi.mocked(me).mockResolvedValue(meFixture("admin"));
    vi.mocked(get).mockResolvedValue(PAYLOAD);
    vi.mocked(del).mockResolvedValue({ ...ROW, revoked: true });
    render(renderPage());

    await waitFor(() => expect(screen.getByText("Zapier")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "Revoke" }));
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();

    await userEvent.click(
      screen.getAllByRole("button", { name: "Revoke" }).find((b) => b.closest("[role=alertdialog]"))!,
    );
    await waitFor(() =>
      expect(vi.mocked(del)).toHaveBeenCalledWith("/api/v1/settings/api-tokens/tok1"),
    );
  });

  it("a non-admin sees the role notice and no form, and the hub is never asked", async () => {
    vi.mocked(me).mockResolvedValue(meFixture("operator"));
    render(renderPage());

    await waitFor(() => expect(screen.getByText(/admin access is required/i)).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Create token" })).not.toBeInTheDocument();
    expect(vi.mocked(get)).not.toHaveBeenCalled();
  });

  it("only offers roles the signed-in admin could actually grant", async () => {
    vi.mocked(me).mockResolvedValue(meFixture("admin"));
    vi.mocked(get).mockResolvedValue({ ...PAYLOAD, max_role: "operator" });
    render(renderPage());

    // Wait for the payload, not merely for the form: the form renders as soon as
    // the role is known, and `max_role` arrives with the token list.
    await waitFor(() => expect(screen.getByText("Zapier")).toBeInTheDocument());
    const options = Array.from(screen.getByLabelText(/Role/i).querySelectorAll("option")).map(
      (o) => o.value,
    );
    expect(options).toEqual(["viewer", "operator"]);
  });
});
