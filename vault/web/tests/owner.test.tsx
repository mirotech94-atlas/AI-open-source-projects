import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../src/design";
import OwnerShell from "../src/owner/OwnerShell";
import Ledger from "../src/owner/Ledger";
import type { MeOut } from "../src/api";
import type { PlatformPayload } from "../src/types";

// The owner console at admin.guarder.io. Two things matter more than the
// layout: a customer who lands here is told plainly instead of being shown a
// broken page, and the ledger's numbers say what they mean — a paused tenant
// has to be visibly *stopped*, because that is the whole point of the column.

vi.mock("../src/lib/api", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/api")>("../src/lib/api");
  return { ...actual, platformPayload: vi.fn(), get: vi.fn() };
});
vi.mock("../src/api", async () => {
  const actual = await vi.importActual<typeof import("../src/api")>("../src/api");
  return { ...actual, me: vi.fn(), setActiveTenant: vi.fn(() => Promise.resolve({ ok: true })) };
});

const api = await import("../src/lib/api");
const rootApi = await import("../src/api");

function meOut(superadmin: boolean): MeOut {
  const tenant = { id: "t1", slug: "home", name: "Home", role: "admin" as const };
  return {
    user: { username: "miro", display_name: "Miro", is_superadmin: superadmin },
    tenants: [tenant],
    active_tenant: tenant,
    chat_providers: [],
  } as unknown as MeOut;
}

function row(over: Partial<PlatformPayload["tenants"][number]> = {}) {
  return {
    id: "t1", slug: "home", name: "Home", plan: "trial", plan_status: "trialing",
    interval: "none", per_site: false, mrr_cents: 0,
    trial_ends: "2026-12-16T00:00:00+00:00", trial_days_left: 40, ingesting: true,
    sensors_total: 2, sensors_online: 2, last_scan: new Date(Date.now() - 4 * 60000).toISOString(),
    devices: 26, users: 2, open_warn: 0, open_crit: 0, created: "2026-06-01T00:00:00+00:00",
    ...over,
  };
}

const PAYLOAD: PlatformPayload = {
  totals: { tenants: 2, sensors_online: 2, sensors_total: 3, open_crit: 1, open_warn: 2,
            trials_ending: 1, open_requests: 1, paused: 1,
            open_tickets: 0, mrr_cents: 0, paying: 0, past_due: 0, mrr_is_floor: false },
  tenants: [
    row(),
    row({ id: "t2", slug: "northwind", name: "Northwind Dental", plan: "trial",
          plan_status: "paused", ingesting: false, trial_days_left: 0,
          sensors_total: 1, sensors_online: 0, open_crit: 1, open_warn: 2, devices: 44 }),
  ],
  mail_configured: true, billing_mode: "trial_only", auto_heal: true, base_url: "https://my.guarder.io",
};

function renderOwner(node: ReactElement, path = "/owner") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/owner" element={node}>
              <Route index element={<Ledger />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.clearAllMocks());

/** The ledger's own rows, not the whole document.
 *
 * The shell's customer jump lists every customer by name in a `<select>`, so a
 * bare `getByText("Northwind Dental")` now matches twice -- once in the row and
 * once in the option. Scoping says what these assertions always meant ("the
 * LEDGER lists this customer") and keeps them from passing on the picker alone,
 * which would be the failure worth catching if the table ever stopped
 * rendering. */
async function ledger() {
  // `findByRole("table")` resolves on the EMPTY table the first render puts up,
  // so the scope has to be re-taken after the rows arrive -- hence waiting on a
  // row rather than on the element.
  await screen.findAllByText("Northwind Dental");
  return within(screen.getByRole("table"));
}

describe("owner console", () => {
  it("tells a customer plainly that this is not their dashboard", async () => {
    vi.mocked(rootApi.me).mockResolvedValue(meOut(false));
    vi.mocked(api.platformPayload).mockResolvedValue(PAYLOAD);
    renderOwner(<OwnerShell />);
    expect(await screen.findByText(/owner console/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /my.guarder.io/ })).toBeInTheDocument();
    // And it does not render the ledger's data to them.
    expect(screen.queryByText("Northwind Dental")).toBeNull();
  });

  it("lists every customer with sensors, alerts and trial state", async () => {
    vi.mocked(rootApi.me).mockResolvedValue(meOut(true));
    vi.mocked(api.platformPayload).mockResolvedValue(PAYLOAD);
    renderOwner(<OwnerShell />);
    const rows = await ledger();
    expect(rows.getByText("Northwind Dental")).toBeInTheDocument();
    expect(rows.getByText("Home")).toBeInTheDocument();
    // The ledger's whole job: a paused customer reads as stopped, not quiet.
    expect(screen.getByText(/not collecting/i)).toBeInTheDocument();
    expect(screen.getByText("0/1")).toBeInTheDocument();     // sensors down
    expect(screen.getByText("1 crit")).toBeInTheDocument();
  });

  it("summarises the platform in one status line, not a row of stat cards", async () => {
    vi.mocked(rootApi.me).mockResolvedValue(meOut(true));
    vi.mocked(api.platformPayload).mockResolvedValue(PAYLOAD);
    renderOwner(<OwnerShell />);
    const line = await screen.findByRole("status");
    expect(line).toHaveTextContent("2 customers");
    expect(line).toHaveTextContent("2/3 sensors reporting");
    expect(line).toHaveTextContent("1 paused");
    expect(line).toHaveTextContent("1 requests waiting");
  });

  it("opens one customer's detail without leaving the ledger", async () => {
    vi.mocked(rootApi.me).mockResolvedValue(meOut(true));
    vi.mocked(api.platformPayload).mockResolvedValue(PAYLOAD);
    renderOwner(<OwnerShell />);
    await userEvent.click((await ledger()).getByText("Northwind Dental"));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/no — paused/)).toBeInTheDocument();
    expect(within(dialog).getByText("0 of 1 reporting")).toBeInTheDocument();
    expect(within(dialog).getByText("1 critical, 2 warning")).toBeInTheDocument();
  });

  it("opens a customer's own dashboard from their row", async () => {
    // A superadmin already holds admin on every tenant, so this is a
    // shortcut rather than new access — and it must switch the session
    // before navigating, or the dashboard renders the previous customer.
    vi.mocked(rootApi.me).mockResolvedValue(meOut(true));
    vi.mocked(api.platformPayload).mockResolvedValue(PAYLOAD);
    renderOwner(<OwnerShell />);
    await userEvent.click((await ledger()).getByText("Northwind Dental"));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: /open northwind dental/i }));
    expect(vi.mocked(rootApi.setActiveTenant)).toHaveBeenCalledWith("t2");
  });

  it("says so rather than showing an empty grid when the read fails", async () => {
    vi.mocked(rootApi.me).mockResolvedValue(meOut(true));
    vi.mocked(api.platformPayload).mockRejectedValue(new Error("401"));
    renderOwner(<OwnerShell />);
    expect(await screen.findByText(/could not be loaded/i)).toBeInTheDocument();
  });

  it("only lists sections that work", async () => {
    vi.mocked(rootApi.me).mockResolvedValue(meOut(true));
    vi.mocked(api.platformPayload).mockResolvedValue(PAYLOAD);
    renderOwner(<OwnerShell />);
    const nav = await screen.findByRole("navigation", { name: /owner console/i });
    const links = within(nav).getAllByRole("link").map((a) => a.textContent ?? "");
    // The rail lists what is built and nothing else. This assertion is the
    // guard on that rule, so it is updated as each section lands rather than
    // relaxed: a nav entry leading to a placeholder makes the console feel
    // broken rather than unfinished.
    const hrefs = within(nav).getAllByRole("link").map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual([
      "/owner",           // Overview
      "/owner/incidents",
      "/owner/maintenance",
      "/owner/audit",
      "/owner/assets",
      "/owner/sites",
      "/owner/certificates",
      "/owner/reports",
      "/owner/ledger",
      "/owner/fleet",
      "/owner/requests",
      "/owner/tickets",
      "/owner/settings",
      "/owner/billing",
    ]);
    for (const label of ["Overview", "Incidents", "Maintenance", "Audit log", "Assets", "Websites & DNS", "Certificates", "Reports", "Customers", "Sensors", "Access requests", "Support", "Settings", "Billing"]) {
      expect(links.some((l) => l.includes(label)), `rail is missing ${label}`).toBe(true);
    }
    // The rail groups its links, and the headings are what stop fourteen of
    // them reading as a pile. They are not links, so the assertion above
    // cannot see them -- checked separately rather than left untested.
    for (const group of ["Operations", "Inventory", "Insight", "Estate", "Account"]) {
      expect(within(nav).getByText(group), `rail is missing the ${group} heading`).toBeTruthy();
    }
  });

  it("finds a customer by name from the keyboard, without a request", async () => {
    // The palette indexes the customer list the shell already holds, so it
    // answers on the first keystroke. A palette that hesitates is one people
    // stop opening.
    vi.mocked(rootApi.me).mockResolvedValue(meOut(true));
    vi.mocked(api.platformPayload).mockResolvedValue(PAYLOAD);
    renderOwner(<OwnerShell />);
    await ledger();

    await userEvent.click(screen.getByRole("button", { name: /find a customer or a screen/i }));
    const box = await screen.findByRole("combobox", { name: /find a customer or a screen/i });
    await userEvent.type(box, "north");

    // Scoped to the dialog: the customer picker is a <select>, and its
    // <option>s carry the same role.
    const palette = within(screen.getByRole("dialog"));
    const options = palette.getAllByRole("option");
    expect(options[0]).toHaveTextContent("Northwind Dental");
    // Its slug is searchable and shown, because that is how a customer is
    // named in a log or a shell.
    expect(options[0]).toHaveTextContent("northwind");
  });

  it("offers the console's own screens too, so 'billing' is reachable by name", async () => {
    vi.mocked(rootApi.me).mockResolvedValue(meOut(true));
    vi.mocked(api.platformPayload).mockResolvedValue(PAYLOAD);
    renderOwner(<OwnerShell />);
    await ledger();

    await userEvent.click(screen.getByRole("button", { name: /find a customer or a screen/i }));
    const box = await screen.findByRole("combobox", { name: /find a customer or a screen/i });
    await userEvent.type(box, "billing");

    expect(within(screen.getByRole("dialog")).getAllByRole("option")[0]).toHaveTextContent("Billing");
  });

  it("says so plainly when nothing matches", async () => {
    vi.mocked(rootApi.me).mockResolvedValue(meOut(true));
    vi.mocked(api.platformPayload).mockResolvedValue(PAYLOAD);
    renderOwner(<OwnerShell />);
    await ledger();

    await userEvent.click(screen.getByRole("button", { name: /find a customer or a screen/i }));
    const box = await screen.findByRole("combobox", { name: /find a customer or a screen/i });
    await userEvent.type(box, "zzzznothing");

    expect(within(screen.getByRole("dialog")).queryAllByRole("option")).toHaveLength(0);
    expect(screen.getByText(/no customer or screen by that name/i)).toBeInTheDocument();
  });


  it("says so in the frame while automatic repair is off", async () => {
    // A state an owner can leave switched on by accident for a week, where
    // the only other thing that would tell them is an incident that did not
    // fix itself. The banner lives in the shell, not on Settings, because
    // Settings is the one screen where they already know.
    vi.mocked(rootApi.me).mockResolvedValue(meOut(true));
    vi.mocked(api.platformPayload).mockResolvedValue({ ...PAYLOAD, auto_heal: false });
    renderOwner(<OwnerShell />);

    // By its text, not by role: the Ledger's own summary line is also a
    // `role="status"`, so an unscoped role query finds two.
    const banner = await screen.findByText(/automatic repair is off/i);
    expect(banner).toBeInTheDocument();
    // It must not read as "Vault has stopped", which is the thing an owner
    // would panic about — watching continues.
    expect(banner).toHaveTextContent(/still watching/i);
    expect(screen.getByRole("link", { name: /turn it back on/i })).toBeInTheDocument();
  });

  it("says nothing about repair while it is working normally", async () => {
    vi.mocked(rootApi.me).mockResolvedValue(meOut(true));
    vi.mocked(api.platformPayload).mockResolvedValue(PAYLOAD);
    renderOwner(<OwnerShell />);
    await ledger();
    expect(screen.queryByText(/automatic repair is off/i)).toBeNull();
  });

});
