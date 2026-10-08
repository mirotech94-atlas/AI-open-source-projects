import { Badge } from "../../design";
import type { Tone } from "../../lib/format";
import type { ToolStep } from "../../types";

const TIER_TONE: Record<number, Tone> = { 1: "good", 2: "warn", 3: "alert" };

function statusLabel(step: ToolStep): string {
  switch (step.status) {
    case "queued":
      return "queued…";
    case "running":
      return "running…";
    case "done":
      return step.result && typeof step.result.text === "string" ? step.result.text : "done";
    case "failed":
      return step.result && typeof step.result.text === "string" ? `failed: ${step.result.text}` : "failed";
    default:
      return step.status || "…";
  }
}

/** One tool call as Vault ran it: name, tier badge, and a status that
 *  moves queued -> running -> done/failed. A queued step's status is driven
 *  by `useChat`'s "queue" live-frame listener, not by this component — it
 *  only ever renders whatever `step.status` currently says. */
export function ToolSteps({ steps }: { steps: ToolStep[] }) {
  return (
    <ul className="g-chat-tool-steps" aria-label="Vault's steps">
      {steps.map((step, i) => {
        const pending = step.status === "queued" || step.status === "running";
        return (
          <li key={i} data-testid="tool-step" className="g-chat-tool-step">
            {pending && <span className="g-chat-spinner" aria-hidden="true" />}
            <span className="g-chat-tool-name">{step.tool}</span>
            <Badge tone={TIER_TONE[step.tier] ?? "plain"}>Tier {step.tier}</Badge>
            <span className="g-chat-tool-status">{statusLabel(step)}</span>
          </li>
        );
      })}
    </ul>
  );
}

export default ToolSteps;
