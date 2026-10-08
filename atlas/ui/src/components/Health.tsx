import type { JSX } from "react";
import { normaliseHealth } from "../api";

/**
 * The single place health is drawn.
 *
 * Mirrors the Jinja macro exactly, because the rule it enforces is the same on
 * both surfaces: UNKNOWN is grey, dashed and hatched, and every state carries
 * its word. Colour never carries the meaning on its own.
 */
const WORD = {
  ok: "OK",
  degraded: "Degraded",
  failing: "Failing",
  unknown: "Unknown",
} as const;

export function HealthBadge({ health }: { health: string | null | undefined }): JSX.Element {
  const h = normaliseHealth(health);
  return (
    <span className={`badge${h === "unknown" ? " hatch-edge" : ""}`} data-health={h}>
      <span className={`badge-dot${h === "unknown" ? " hatch" : ""}`} aria-hidden="true" />
      {WORD[h]}
    </span>
  );
}

export function HealthRail({ health }: { health: string | null | undefined }): JSX.Element {
  const h = normaliseHealth(health);
  return (
    <span
      className={`rail${h === "unknown" ? " hatch" : ""}`}
      data-health={h}
      aria-label={WORD[h]}
    />
  );
}

const SEVERITY = ["Info", "Normal", "High", "Critical"] as const;

export function Severity({ level }: { level: number }): JSX.Element {
  const index = Math.max(0, Math.min(3, Math.trunc(level)));
  return (
    <span className="sev" data-sev={index}>
      {SEVERITY[index]}
    </span>
  );
}
