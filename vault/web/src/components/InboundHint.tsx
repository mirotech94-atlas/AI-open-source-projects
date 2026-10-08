import { useId, useState } from "react";

export type InboundHintProps = {
  kind: string;
  url: string;
};

// One line per provider telling the admin exactly where to paste the URL —
// the whole point of showing it is that nobody has to go re-read a vendor's
// docs to know which screen it belongs on. Kept here (not on the hub) since
// it is pure copy with no bearing on delivery; a kind with no entry falls
// back to a generic instruction rather than showing nothing.
const INSTRUCTIONS: Record<string, string> = {
  pagerduty: "Paste this into PagerDuty → Integrations → Webhooks v3.",
  discord: "Set this as your Discord app's Interactions Endpoint URL.",
  telegram: "Send “/start <code>” to your bot — see Pair below to get a code.",
  homeassistant: "Call this from a Home Assistant automation (quarantine device / run scan / ack alert).",
};

function instructionFor(kind: string): string {
  return INSTRUCTIONS[kind] ?? "Paste this into the provider's inbound webhook setting.";
}

/** The inbound URL an admin pastes into the provider's own console, with a
 *  copy button and the one-line instruction for exactly where it goes.
 *  Rendered only by the parent card, and only when `card.direction`
 *  includes `"in"` — this component itself is unconditional so the "does
 *  this integration take inbound calls" decision stays in one place
 *  (`IntegrationCardView`). */
export function InboundHint({ kind, url }: InboundHintProps) {
  const urlId = useId();
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // No clipboard permission / insecure context: the URL stays visible
      // and selectable in the read-only input either way.
    }
  }

  return (
    <div className="g-integrations-inbound" data-testid="inbound-url">
      <label htmlFor={urlId}>Inbound URL</label>
      <div className="g-integrations-inbound-row">
        <input id={urlId} type="text" readOnly value={url} onFocus={(e) => e.target.select()} />
        <button type="button" onClick={() => void copy()}>
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <p className="hint">{instructionFor(kind)}</p>
    </div>
  );
}

export default InboundHint;
