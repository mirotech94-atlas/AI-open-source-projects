// "Someone just emailed me — is this legit?"
//
// The rule the whole feature is written around: it never says "safe". No
// DNS check can establish that, and a green tick on a well-configured
// phishing domain is worse than having no tool at all.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EmailCheck } from "../src/components/EmailCheck";
import type { EmailVerdictOut } from "../src/types";

vi.mock("../src/lib/api", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/api")>("../src/lib/api");
  return { ...actual, checkEmailAddress: vi.fn() };
});
import { checkEmailAddress } from "../src/lib/api";

afterEach(() => vi.restoreAllMocks());

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><EmailCheck /></QueryClientProvider>);
}

async function ask(v: Partial<EmailVerdictOut>) {
  vi.mocked(checkEmailAddress).mockResolvedValue({
    address: "billing@acme.example", domain: "acme.example", verdict: "unproven",
    headline: "Nothing wrong found with acme.example.", findings: [], limits: [],
    reputation_note: "", ...v,
  });
  mount();
  await userEvent.type(screen.getByLabelText("Check an email address"), "billing@acme.example");
  await userEvent.click(screen.getByRole("button", { name: "Check" }));
  return screen.findByTestId("email-check");
}

describe("email check", () => {
  it("never says safe, even when it finds nothing wrong", async () => {
    const panel = await ask({});
    expect(panel.textContent).toContain("Nothing wrong found");
    expect(panel.textContent?.toLowerCase()).not.toContain("safe to");
    // And not green: a reassuring colour is a claim of its own.
    expect(within(panel).getByText("Nothing wrong found")).toBeTruthy();
  });

  it("says a forged domain is forged, and shows why", async () => {
    const panel = await ask({
      verdict: "forged", headline: "nowhere.example does not exist.",
      findings: [{ tone: "bad", text: "The domain has no DNS records at all." }],
    });
    expect(within(panel).getByText("Forged")).toBeTruthy();
    expect(panel.textContent).toContain("no DNS records at all");
  });

  it("carries what it could NOT check, so the answer is not over-read", async () => {
    const panel = await ask({
      limits: ["Vault checked the domain, not the person.",
               "No reputation service is switched on."],
    });
    expect(panel.textContent).toContain("checked the domain, not the person");
    expect(panel.textContent).toContain("No reputation service is switched on");
  });

  it("promises on screen that the address is not sent anywhere", async () => {
    mount();
    expect(screen.getByText(/not sent anywhere/i)).toBeTruthy();
  });

  it("shows nothing until it has been asked", () => {
    mount();
    expect(screen.queryByRole("status")).toBeNull();
  });
});
