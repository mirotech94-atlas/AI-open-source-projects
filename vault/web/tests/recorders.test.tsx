// Task 73: Settings → Camera recorders.
//
// The cases that matter are the two the feature exists for. First, that the
// page tells the owner WHY he sees one camera instead of twelve when the
// recorder's HTTP interface is off — the diagnosis and the setting to enable,
// on the page, not buried in an alert. Second, that the password goes up and
// never comes down: an empty password field on an edit must leave the stored
// credential alone rather than silently wiping it.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../src/design";
import { Recorders } from "../src/pages/Settings/Recorders";
import type { RecorderRow, RecordersPayload } from "../src/types";

const { postedCalls } = vi.hoisted(() => ({ postedCalls: [] as { path: string; body: unknown }[] }));

function posted(path: string): unknown {
  return [...postedCalls].reverse().find((c) => c.path === path)?.body;
}

vi.mock("../src/lib/api", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/api")>("../src/lib/api");
  return {
    ...actual,
    recordersPayload: vi.fn(),
    createRecorder: vi.fn((body: unknown) => {
      postedCalls.push({ path: "POST /recorders", body });
      return Promise.resolve({} as RecorderRow);
    }),
    updateRecorder: vi.fn((id: string, body: unknown) => {
      postedCalls.push({ path: `PATCH /recorders/${id}`, body });
      return Promise.resolve({} as RecorderRow);
    }),
    deleteRecorder: vi.fn((id: string) => {
      postedCalls.push({ path: `DELETE /recorders/${id}`, body: {} });
      return Promise.resolve({});
    }),
  };
});

import { recordersPayload } from "../src/lib/api";

function row(over: Partial<RecorderRow> = {}): RecorderRow {
  return {
    id: "rec1",
    name: "Garage NVR",
    vendor: "reolink",
    vendor_label: "Reolink",
    supported: true,
    host: "192.168.1.182",
    port: 80,
    scheme: "http",
    username: "guardian",
    has_credentials: true,
    expected_channels: 12,
    enabled: true,
    status: "ok",
    error_kind: "",
    problem: "",
    owner_action: "",
    model: "RLN8-410",
    firmware: "v3.0.0.0",
    channel_count: 12,
    channels_online: 11,
    channels: [],
    network_id: "net_cams",
    network_slug: "garage-nvr-cameras",
    network_name: "Garage NVR cameras",
    last_ok: "2026-09-10T10:00:00+00:00",
    last_attempt: "2026-09-10T10:00:00+00:00",
    created: "2026-09-10T09:00:00+00:00",
    ...over,
  };
}

const HTTP_OFF = row({
  status: "error",
  error_kind: "http_disabled",
  channel_count: 0,
  channels_online: 0,
  problem:
    "The recorder's HTTP interface appears to be switched off. The HTTP port refused the " +
    "connection, and only the recorder's proprietary client port is answering.",
  owner_action:
    "On the recorder, turn its HTTP port back on: Settings -> Network -> Advanced -> Port " +
    "Settings, and enable the HTTP port (80) or HTTPS (443).",
});

function renderPage(payload: RecordersPayload, role: "admin" | "viewer" = "admin") {
  vi.mocked(recordersPayload).mockResolvedValue(payload);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <Recorders role={role} />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

const VENDORS = [
  { key: "reolink", label: "Reolink", supported: true },
  { key: "hikvision", label: "Hikvision", supported: false },
];

beforeEach(() => {
  postedCalls.length = 0;
});

describe("Settings → Camera recorders", () => {
  it("shows the cameras a recorder reports, not the one device the LAN sees", async () => {
    renderPage({ recorders: [row()], vendors: VENDORS });
    expect(await screen.findByText("Garage NVR")).toBeInTheDocument();
    expect(screen.getByTestId("status-rec1")).toHaveTextContent("11/12 cameras answering");
  });

  it("says the HTTP interface is off and names the setting to enable", async () => {
    renderPage({ recorders: [HTTP_OFF], vendors: VENDORS });
    const notice = await screen.findByTestId("problem-rec1");
    expect(notice).toHaveTextContent(/HTTP interface appears to be switched off/);
    expect(notice).toHaveTextContent(/Port Settings/);
    expect(notice).toHaveTextContent(/HTTP port \(80\)/);
    // ...and the row does not merely read "0 cameras" with no explanation
    expect(screen.getByTestId("status-rec1")).toHaveTextContent("cannot be read");
  });

  it("an empty password on an edit keeps the stored one instead of clearing it", async () => {
    renderPage({ recorders: [row()], vendors: VENDORS });
    await userEvent.click(await screen.findByTestId("edit-rec1"));
    const pw = screen.getByLabelText("Password") as HTMLInputElement;
    expect(pw.value).toBe("");                       // never pre-filled
    await userEvent.clear(screen.getByLabelText("Name"));
    await userEvent.type(screen.getByLabelText("Name"), "Front NVR");
    await userEvent.click(screen.getByRole("button", { name: "Save recorder" }));
    await waitFor(() => expect(posted("PATCH /recorders/rec1")).toBeTruthy());
    const body = posted("PATCH /recorders/rec1") as Record<string, unknown>;
    expect(body.name).toBe("Front NVR");
    expect("password" in body).toBe(false);
  });

  it("clearing the password is its own deliberate action", async () => {
    renderPage({ recorders: [row()], vendors: VENDORS });
    await userEvent.click(await screen.findByTestId("clear-creds-rec1"));
    await waitFor(() => expect(posted("PATCH /recorders/rec1")).toEqual({ password: "" }));
  });

  it("a new recorder posts the password it was given", async () => {
    renderPage({ recorders: [], vendors: VENDORS });
    await userEvent.click(await screen.findByTestId("add-recorder"));
    await userEvent.type(screen.getByLabelText("Name"), "Garage NVR");
    await userEvent.type(screen.getByLabelText("Address on your network"), "192.168.1.182");
    await userEvent.type(screen.getByLabelText("Username"), "guardian");
    await userEvent.type(screen.getByLabelText("Password"), "s3cret");
    await userEvent.click(screen.getByRole("button", { name: "Add recorder" }));
    await waitFor(() => expect(posted("POST /recorders")).toBeTruthy());
    expect(posted("POST /recorders")).toMatchObject({
      name: "Garage NVR", host: "192.168.1.182", username: "guardian", password: "s3cret",
      vendor: "reolink", port: 80, scheme: "http",
    });
  });

  it("catches a bad address before the hub has to", async () => {
    renderPage({ recorders: [], vendors: VENDORS });
    await userEvent.click(await screen.findByTestId("add-recorder"));
    await userEvent.type(screen.getByLabelText("Address on your network"), "nvr.local");
    await userEvent.click(screen.getByRole("button", { name: "Add recorder" }));
    expect(screen.getByText(/looks like 192\.168\.1\.182/)).toBeInTheDocument();
    expect(posted("POST /recorders")).toBeUndefined();
  });

  it("a make with no adapter yet is labelled as such, never silently ignored", async () => {
    renderPage({ recorders: [row({ vendor: "hikvision", vendor_label: "Hikvision", supported: false })], vendors: VENDORS });
    expect(await screen.findByText(/no adapter yet/)).toBeInTheDocument();
  });

  it("a viewer sees everything and can change nothing", async () => {
    renderPage({ recorders: [row()], vendors: VENDORS }, "viewer");
    expect(await screen.findByText("Garage NVR")).toBeInTheDocument();
    expect(screen.getByText(/admin access is required/i)).toBeInTheDocument();
    expect(screen.getByTestId("edit-rec1")).toBeDisabled();
    expect(screen.getByTestId("delete-rec1")).toBeDisabled();
    expect(screen.getByTestId("add-recorder")).toBeDisabled();
  });
});
