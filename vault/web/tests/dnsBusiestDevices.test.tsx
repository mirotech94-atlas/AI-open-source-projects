import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DnsInsights } from "../src/components/DnsInsights";

// Two devices called "iPhone" were one React key: the list warned on every
// render, and even when both bars drew, the owner could not tell which phone
// was which. Seen on the demo's DNS page, 2026-10-06.
describe("Busiest devices", () => {
  it("tells two devices with the same name apart, and keys them by device", () => {
    const err = vi.spyOn(console, "error").mockImplementation(() => {});
    const client = (mac: string, ip: string, name: string, queries: number) =>
      ({ mac, ip, name, queries, blocked: 0, network: null });
    render(
      <DnsInsights
        top={[]}
        blockedTop={[]}
        clients={[client("aa:00:00:00:00:01", "192.168.1.163", "iPhone", 90),
                  client("aa:00:00:00:00:02", "192.168.1.164", "iPhone", 40),
                  client("aa:00:00:00:00:03", "192.168.1.20", "Front desk PC", 30)]}
      />,
    );
    const list = screen.getByTestId("dns-clients");
    expect(within(list).getByText("iPhone · 192.168.1.163")).toBeInTheDocument();
    expect(within(list).getByText("iPhone · 192.168.1.164")).toBeInTheDocument();
    expect(within(list).getByText("Front desk PC")).toBeInTheDocument();
    expect(err.mock.calls.some((c) => String(c[0]).includes("same key"))).toBe(false);
    err.mockRestore();
  });
});
