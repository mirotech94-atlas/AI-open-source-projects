import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Badge } from "../design";
import { checkEmailAddress } from "../lib/api";
import type { EmailVerdictOut } from "../types";

const VERDICT: Record<string, { label: string; tone: "alert" | "warn" | "good" | "plain" }> = {
  forged: { label: "Forged", tone: "alert" },
  risky: { label: "Treat with caution", tone: "warn" },
  // Not "Safe", and not green. See the module note below.
  unproven: { label: "Nothing wrong found", tone: "plain" },
  unknown: { label: "Can't tell", tone: "plain" },
};

const TONE: Record<string, "alert" | "warn" | "good" | "plain"> = {
  bad: "alert", warn: "warn", good: "good", info: "plain",
};

/**
 * "Someone just emailed me — is this legit?"
 *
 * The moment this serves is a real one: a letter arrives asking for a
 * payment detail to be changed, and somebody has thirty seconds to decide.
 *
 * Two things about it are deliberate. It never says "safe" — no DNS check
 * can establish that, and a green tick on a well-configured phishing domain
 * is worse than having no tool at all; the best verdict is "nothing wrong
 * found", in grey. And the address never leaves the hub: Vault reads
 * public DNS for the domain and nothing else, unless an operator has
 * switched a reputation provider on and said so.
 */
export function EmailCheck() {
  const [address, setAddress] = useState("");
  const check = useMutation({ mutationFn: (a: string) => checkEmailAddress(a) });
  const v: EmailVerdictOut | undefined = check.data;
  const verdict = v ? VERDICT[v.verdict] ?? VERDICT.unknown : null;

  return (
    // The section and its input must not share an accessible name, or a
    // screen reader announces the same words twice and a test cannot tell
    // the region from the field.
    <section className="g-ec" aria-label="Email address checker" data-testid="email-check">
      <form
        className="g-ec-form"
        onSubmit={(e) => { e.preventDefault(); if (address.trim()) check.mutate(address.trim()); }}
      >
        <label htmlFor="g-ec-addr">Check an email address</label>
        <div className="g-ec-row">
          <input
            id="g-ec-addr"
            type="text"
            inputMode="email"
            placeholder="billing@supplier.example"
            value={address}
            onChange={(e) => setAddress(e.target.value)}
          />
          <button type="submit" className="g-btn-primary" disabled={!address.trim() || check.isPending}>
            {check.isPending ? "Checking…" : "Check"}
          </button>
        </div>
        <p className="hint">
          Vault reads public DNS for the domain. The address is not sent anywhere.
        </p>
      </form>

      {check.isError && <p className="g-ec-err" role="status">That could not be checked. Try again.</p>}

      {v && verdict && (
        <div className="g-ec-out" data-verdict={v.verdict} role="status">
          <p className="g-ec-head">
            <Badge tone={verdict.tone}>{verdict.label}</Badge>
            <strong>{v.headline}</strong>
          </p>
          {v.findings.length > 0 && (
            <ul className="g-ec-findings">
              {v.findings.map((f, i) => (
                <li key={i} data-tone={f.tone}>
                  <Badge tone={TONE[f.tone] ?? "plain"}>{f.tone === "good" ? "ok" : f.tone}</Badge>
                  <span>{f.text}</span>
                </li>
              ))}
            </ul>
          )}
          {/* What Vault could NOT check. A verdict without its limits
              invites the reader to treat it as more than it is. */}
          {v.limits.length > 0 && (
            <ul className="g-ec-limits">
              {v.limits.map((l, i) => <li key={i}>{l}</li>)}
            </ul>
          )}
          {v.reputation_note && <p className="hint">Reputation data from {v.reputation_note}.</p>}
        </div>
      )}
    </section>
  );
}

export default EmailCheck;
