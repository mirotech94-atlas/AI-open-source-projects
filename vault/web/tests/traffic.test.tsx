import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { TrafficSection } from "../src/components/TrafficSection";
import type { TrafficOverview } from "../src/types";

const base: TrafficOverview = { mode: "mirror", why_none: "", truncated: false, window_hours: 1, top_talkers: [], top_peers: [], new_peers: [], external_share: 0 };

const twoPeers: TrafficOverview["top_peers"] = [
  { peer: "cdn.example", peer_ip: "1.1.1.1", peer_port: 443, proto: "tcp", bytes_in: 2e6, bytes_out: 1e5, first_seen: "", last_seen: "" },
  { peer: "storage.example", peer_ip: "2.2.2.2", peer_port: 443, proto: "tcp", bytes_in: 1e6, bytes_out: 5e4, first_seen: "", last_seen: "" },
];

describe("TrafficSection", () => {
  it("says what it cannot see in router mode instead of drawing empty columns", () => {
    render(<TrafficSection data={{ ...base, mode: "router" }} />);
    expect(screen.getByText(/only bytes per device/i)).toBeInTheDocument();
    expect(screen.queryByText(/top destinations/i)).toBeNull();
  });
  it("lists talkers biggest first and flags new peers", () => {
    render(<TrafficSection data={{ ...base,
      top_talkers: [{ mac: "a", name: "X-ray PC", ip: "1", bytes_in: 5e6, bytes_out: 1e5, top_peer: "vendor.example" }],
      new_peers: [{ peer: "odd.example", peer_ip: "9.9.9.9", peer_port: 443, proto: "tcp", bytes_in: 1, bytes_out: 1, first_seen: "", last_seen: "" }] }} />);
    expect(screen.getByText("X-ray PC")).toBeInTheDocument();
    expect(screen.getByText(/new/i).closest("section, div")).toHaveTextContent("odd.example");
  });
  it("renders nothing loud when there is nothing to say", () => {
    render(<TrafficSection data={{ ...base, mode: "none" }} />);
    expect(screen.getByText(/cannot see traffic/i)).toBeInTheDocument();
  });
  it("names the top destinations in mirror mode, and never in router mode", () => {
    const { unmount } = render(<TrafficSection data={{ ...base, top_peers: twoPeers }} />);
    const mirrorSection = within(screen.getByRole("region", { name: "Traffic" }));
    expect(mirrorSection.getByRole("heading", { name: /top destinations/i })).toBeInTheDocument();
    expect(mirrorSection.getByText("cdn.example")).toBeInTheDocument();
    expect(mirrorSection.getByText("storage.example")).toBeInTheDocument();
    unmount();

    render(<TrafficSection data={{ ...base, mode: "router", top_peers: twoPeers }} />);
    const routerSection = within(screen.getByRole("region", { name: "Traffic" }));
    expect(routerSection.queryByText(/top destinations/i)).toBeNull();
    expect(routerSection.queryByText("cdn.example")).toBeNull();
  });
  it("says when the sensor dropped flows, and stays quiet when it did not", () => {
    const { unmount } = render(<TrafficSection data={{ ...base, truncated: true }} />);
    expect(
      within(screen.getByRole("region", { name: "Traffic" })).getByText(/saw more flows than it could send/i),
    ).toBeInTheDocument();
    unmount();

    render(<TrafficSection data={{ ...base, truncated: false }} />);
    expect(
      within(screen.getByRole("region", { name: "Traffic" })).queryByText(/saw more flows than it could send/i),
    ).toBeNull();
  });
});

// --------------------------------------------------------------------------
// Item 20: an unwatched network is not a quiet one
// --------------------------------------------------------------------------

describe("host mode -- the sensor's own traffic only", () => {
  it("says it is one machine before showing that machine's destinations", () => {
    render(<TrafficSection data={{ ...base, mode: "host", top_peers: twoPeers }} />);
    const section = within(screen.getByRole("region", { name: "Traffic" }));
    expect(section.getByTestId("traffic-host-only")).toHaveTextContent(/sensor machine's own traffic only/i);
    // Its destinations are real, so they are shown -- under a heading that
    // does not claim to be the network's.
    expect(section.getByRole("heading", { name: /where the sensor connected/i })).toBeInTheDocument();
    expect(section.queryByRole("heading", { name: /top destinations/i })).toBeNull();
    expect(section.getByText("cdn.example")).toBeInTheDocument();
  });

  it("never shows the host caveat in mirror mode", () => {
    render(<TrafficSection data={{ ...base, top_peers: twoPeers }} />);
    expect(screen.queryByTestId("traffic-host-only")).toBeNull();
  });
});

describe("why there is no traffic", () => {
  it("says nobody switched it on, rather than blaming the hardware", () => {
    render(
      <MemoryRouter>
        <TrafficSection data={{
          ...base, mode: "none",
          why_none: "Vault is not watching traffic on this network. Nothing here is quiet — nobody has turned it on.",
        }} />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("traffic-why").textContent).toContain("nobody has turned it on");
    // And it offers the way to switch it on, for a network you own.
    expect(screen.getByRole("link", { name: /Turn it on for a network you own/ })
      .getAttribute("href")).toBe("/settings/networks");
  });

  it("keeps the old hardware sentence when the hub has no better answer", () => {
    render(
      <MemoryRouter>
        <TrafficSection data={{ ...base, mode: "none", why_none: "" }} />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("traffic-why").textContent).toContain("no mirror port is configured");
    expect(screen.queryByRole("link", { name: /Turn it on/ })).toBeNull();
  });
});
