import { normaliseHealth, type CheckSummary, type SelfTest } from "../api";

/**
 * How the board draws one check (invariants 3 and 7). Six looks, and no two
 * share a treatment: OK is a solid lit dot; UNKNOWN is grey, unlit, dashed and
 * hatched; PARKED is a double border in violet (someone said why, until when);
 * RETIRED is struck through and dotted (the code no longer has the check).
 * Every look carries its word, so colour never carries the meaning alone.
 */
export type LookKind =
  | "ok" | "degraded" | "failing" | "unknown" | "parked" | "retired";

export interface CheckLook {
  kind: LookKind;
  word: string;
  /** The one CSS treatment: the stylesheet keys on this, and no two differ
   *  in name or in rule. */
  treatment: "lit" | "amber" | "red" | "dashed-hatch" | "double" | "struck-dotted";
  /** Why it looks this way, in words (parked reason, "last read at ..."). */
  note: string;
}

const WORD: Record<LookKind, string> = {
  ok: "OK", degraded: "Degraded", failing: "Failing",
  unknown: "Unknown", parked: "Parked", retired: "Retired",
};

const TREATMENT: Record<LookKind, CheckLook["treatment"]> = {
  ok: "lit", degraded: "amber", failing: "red",
  unknown: "dashed-hatch", parked: "double", retired: "struck-dotted",
};

export function readAt(iso: string | null | undefined): string {
  return iso ? `last read at ${iso.replace("T", " ").slice(0, 16)} UTC` : "never read";
}

type LookInput = Pick<CheckSummary, "health" | "state" | "last_read_at"
  | "observed_at" | "detail">;

export function checkLook(check: LookInput): CheckLook {
  const stateKind = check.state?.kind;
  const kind: LookKind =
    stateKind === "retired" || stateKind === "parked"
      ? stateKind
      : normaliseHealth(check.health);
  const when = check.last_read_at ?? check.observed_at ?? null;
  const note =
    kind === "retired" || kind === "parked"
      ? check.state?.label ?? ""
      : kind === "unknown"
        ? `${readAt(when)}${check.detail ? `: ${check.detail}` : ""}`
        : check.detail;
  return { kind, word: WORD[kind], treatment: TREATMENT[kind], note };
}

/** The self-test line as words, each count with its read time. */
export function selfTestLine(t: SelfTest | undefined | null): string[] {
  if (!t) return ["self-test: not read"];
  const at = (iso: string) => iso.replace("T", " ").slice(11, 19) + " UTC";
  return [
    `${t.checks_run.n} of ${t.checks_run.of} checks ran (read ${at(t.checks_run.read_at)})`,
    `${t.unknown.n} unknown (read ${at(t.unknown.read_at)})`,
    `${t.parked.n} parked (read ${at(t.parked.read_at)})`,
  ];
}
