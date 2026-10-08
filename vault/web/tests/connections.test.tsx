import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ConnectionsPayload, LiveEvent } from "../src/types";

let push: ((e: LiveEvent) => void) | null = null;
vi.mock("../src/lib/live", () => ({
  useLiveFeed: () => ({
    status: "open", last: null, dropped: 0,
    on: (_type: string, fn: (e: LiveEvent) => void) => { push = fn; return () => { push = null; }; },
  }),
}));

const dest = (over: Partial<ConnectionsPayload["devices"][0]["destinations"][0]>) => ({
  host: "", peer_ip: "203.0.113.9", port: 443, proto: "tcp", name: "203.0.113.9", what: "",
  sure: false, port_meaning: "Encrypted web", local: false, bytes_in: 1000, bytes_out: 100,
  first_seen: "2026-09-28T04:00:00+00:00", last_seen: "2026-09-28T04:05:00+00:00", is_new: false, ...over,
});

let payload: ConnectionsPayload;
vi.mock("../src/lib/api", () => ({ trafficConnections: () => Promise.resolve(payload) }));

import { Connections } from "../src/pages/Connections";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter><Connections /></MemoryRouter></QueryClientProvider>);
}

afterEach(() => { push = null; });

function basePayload(): ConnectionsPayload {
  return {
    mode: "mirror", why_none: "", window_minutes: 15, destinations_total: 3, new_total: 1,
    devices: [{
      mac: "aa:bb:cc:dd:ee:01", name: "Miro Mac", ip: "192.168.1.20", bytes_in: 3_900_000, bytes_out: 300_000,
      last_seen: "2026-09-28T04:05:00+00:00",
      destinations: [
        dest({ name: "Anthropic", what: "AI service", sure: true, host: "api.anthropic.com", bytes_in: 3_800_000 }),
        dest({ name: "Slack", what: "team chat", sure: true, host: "slack.com", bytes_in: 100_000 }),
        dest({ is_new: true }),
      ],
    }],
  };
}

describe("Live connections", () => {
  it("groups by device and names what each one talked to", async () => {
    payload = basePayload();
    renderPage();
    const device = await screen.findByTestId("conn-device");
    expect(within(device).getByRole("heading", { name: "Miro Mac" })).toBeInTheDocument();
    const rows = within(device).getAllByTestId("conn-row");
    expect(rows[0]).toHaveTextContent("Anthropic");
    expect(rows[0]).toHaveTextContent("AI service");
    expect(rows[0]).toHaveTextContent("Encrypted web");
    expect(rows[2]).toHaveTextContent("New");
    expect(screen.getByTestId("conn-summary")).toHaveTextContent(/1 device connected to 3 places.*1 is new/);
  });

  it("explains a connection in a sentence when you open it", async () => {
    payload = basePayload();
    renderPage();
    fireEvent.click((await screen.findAllByTestId("conn-row"))[0]);
    expect(await screen.findByTestId("conn-sentence")).toHaveTextContent(
      "Miro Mac connected to Anthropic (AI service) using encrypted web.");
  });

  it("can show the same data by destination", async () => {
    payload = basePayload();
    renderPage();
    await screen.findByTestId("conn-device");
    fireEvent.click(screen.getByRole("button", { name: "By destination" }));
    expect(screen.queryByTestId("conn-device")).toBeNull();
    expect(screen.getAllByTestId("conn-row")[0]).toHaveTextContent("from Miro Mac");
  });

  it("says it is one machine when the network is declared sensor-only", async () => {
    payload = { ...basePayload(), mode: "host" };
    renderPage();
    expect(await screen.findByText(/sensor machine's own connections only/i)).toBeInTheDocument();
  });

  it("keeps the raw feed streaming underneath", async () => {
    payload = basePayload();
    renderPage();
    await screen.findByTestId("conn-device");
    act(() => {
      push!({ type: "flow", ts: "", data: { ts: "2026-09-28T04:06:00+00:00", mac: "aa:bb:cc:dd:ee:01", ip: "192.168.1.20",
        device: "Miro Mac", peer: "api.anthropic.com", peer_ip: "160.79.104.10", peer_port: 443, proto: "tcp",
        bytes_in: 10, bytes_out: 5 } } as LiveEvent);
    });
    expect(screen.getAllByTestId("conn-raw-row")[0]).toHaveTextContent("api.anthropic.com");
  });
});
