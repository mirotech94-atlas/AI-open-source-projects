/**
 * The ops floor's pure read models (Miro 2026-10-29): roster rows, the
 * decision buttons, the summary strip and the alert lines. No fetching
 * here, so every rule is tested on its own (opsFloorModel.test.ts).
 *
 * Invariant 7 in the interface: UNKNOWN, FAILING and OK never share a
 * class, and a figure that was not read is the word UNKNOWN, never 0.
 */
import { outcomeDetail, outcomeLine } from "./outcomeText";
import { safeText } from "./retired";

export interface RosterAgent {
  name: string;
  role: string;
  status: string | null;
  shift_state: string | null;
  last_shift_at: string | null;
  last_shift_outcome: string | null;
  /** The whole clean outcome of that same shift, for the row's expand. */
  last_shift_outcome_detail: string | null;
  next_shift_at: string | null;
  attempts_today: number | null;
  today_output: number | null;
  alert_unrouted: boolean;
  read_at: string | null;
}

export type RosterState = "ok" | "idle" | "failing" | "unknown";

export interface RosterView {
  state: RosterState;
  word: string;
  className: "cmd-ok" | "cmd-idle" | "cmd-failing" | "cmd-unknown";
}

const FAILING_SHIFT = new Set(["failed", "overdue"]);

/** One roster row's state: unknown first (a read that failed is never OK),
 *  then failing (status, or a failed/overdue shift), then ok and idle. */
export function rosterRow(a: Pick<RosterAgent, "status" | "shift_state">): RosterView {
  const status = a.status ?? "unknown";
  if (status === "unknown" || !["ok", "idle", "failing"].includes(status)) {
    return { state: "unknown", word: "UNKNOWN", className: "cmd-unknown" };
  }
  if (status === "failing" || FAILING_SHIFT.has(a.shift_state ?? "")) {
    return { state: "failing", word: "FAILING", className: "cmd-failing" };
  }
  if (status === "ok") return { state: "ok", word: "OK", className: "cmd-ok" };
  return { state: "idle", word: "IDLE", className: "cmd-idle" };
}

export interface DecisionButton {
  verdict: "decline" | "approve";
  label: string;
}

export const WATCH_ONLY_APPROVE = "Approve (no effect: watch-only, stays pending)";

/** Decline and Approve, only on a pending Tier 2 card. Tier 1 acts alone and
 *  Tier 3 is never offered (the server refuses both anyway). */
export function decisionButtons(
  card: { status: string; tier: number }, watchOnly: boolean,
): DecisionButton[] {
  if (card.status !== "pending" || card.tier !== 2) return [];
  return [
    { verdict: "decline", label: "Decline" },
    { verdict: "approve", label: watchOnly ? WATCH_ONLY_APPROVE : "Approve" },
  ];
}

/** What the server said to one Decline or Approve: its Verdict, or the
 *  error status and detail (null status: the server was not reached). */
export type DecisionResult =
  | { kind: "verdict"; outcome: string; detail: string }
  | { kind: "error"; status: number | null; detail: string };

export interface DecisionNote {
  pid: number;
  tone: "ok" | "warn" | "bad";
  text: string;
}

/** The server's answer to a click, in plain words (Miro 2026-10-29: a click
 *  never fails silently). The panel keeps the note after the card leaves the
 *  list, so a decline is visibly done. */
export function decisionNote(
  pid: number, verdict: "approve" | "decline", r: DecisionResult, watchOnly: boolean,
): DecisionNote {
  const id = `#${pid}`;
  const detail = safeText(r.detail).trim();
  if (r.kind === "error") {
    if (r.status === 429) {
      const wait = /try again in (\d+) s/.exec(r.detail)?.[1];
      return { pid, tone: "bad", text: `${id} not sent: rate limited, try again in `
        + `${wait ? `${wait} s` : "a minute"}.` };
    }
    if (r.status === null) {
      return { pid, tone: "bad", text: `${id} not sent: the server could not be reached.` };
    }
    return { pid, tone: "bad", text: `${id} not sent (${r.status}): ${detail}` };
  }
  switch (r.outcome) {
    case "declined": return { pid, tone: "ok", text: `${id} declined: nothing was done.` };
    case "executed": return { pid, tone: "ok", text: `${id} approved and run.` };
    case "started":
      return { pid, tone: "ok", text: `${id} approved: running in the background.` };
    case "failed":
      return { pid, tone: "bad", text: `${id} approved, but the action failed: ${detail}` };
    case "already": return { pid, tone: "warn", text: `${id} was already answered: ${detail}` };
    case "expired": return { pid, tone: "warn", text: `${id} had expired: nothing was done.` };
    case "missing": return { pid, tone: "warn", text: `${id} no longer exists.` };
    case "superseded":
      return { pid, tone: "warn",
               text: `${id} changed since it was made: a fresh card replaces it.` };
    case "refused":
      if (verdict === "approve" && watchOnly) {
        return { pid, tone: "warn", text:
          `${id}: Approve has no effect while watch-only; the card stays pending.` };
      }
      return { pid, tone: "bad", text: `${id} refused: ${detail}` };
    default:
      return { pid, tone: "warn", text: `${id} ${safeText(r.outcome)}: ${detail}` };
  }
}

/** A roster row's last shift: one line of at most 60 characters, and the
 *  whole clean text for the expand, only when it says more (Miro 2026-10-29). */
export function shiftOutcome(
  a: Pick<RosterAgent, "last_shift_outcome" | "last_shift_outcome_detail">,
): { line: string; detail: string | null } | null {
  const line = outcomeLine(a.last_shift_outcome);
  if (!line) return null;
  const detail = outcomeDetail(a.last_shift_outcome_detail);
  return { line, detail: detail && detail !== line ? detail : null };
}

export interface StripItem {
  key: string;
  label: string;
  value: number | null;
  text: string;
  readAt: string | null;
}

function item(key: string, label: string, value: number | null,
              readAt: string | null): StripItem {
  return { key, label, value, readAt,
           text: value === null ? "UNKNOWN" : value.toLocaleString("en-US") };
}

/** Five numbers across the top, each with its read time; UNKNOWN when the
 *  source was not read (or any roster row is unknown, for the agent counts). */
export function summaryStrip(src: {
  roster: { agents: readonly RosterAgent[]; readAt: string | null } | null;
  pending: { count: number; readAt: string | null } | null;
  pipeline: { count: number; readAt: string | null } | null;
  places: { used: number | null; readAt: string | null } | null;
}): StripItem[] {
  const views = src.roster?.agents.map(rosterRow) ?? null;
  const known = views !== null && views.every((v) => v.state !== "unknown");
  const count = (s: RosterState): number | null =>
    known && views ? views.filter((v) => v.state === s).length : null;
  const rosterAt = src.roster?.readAt ?? null;
  return [
    item("ok", "Agents OK", count("ok"), rosterAt),
    item("failing", "Agents failing", count("failing"), rosterAt),
    item("pending", "Needs you", src.pending?.count ?? null, src.pending?.readAt ?? null),
    item("pipeline", "In pipeline", src.pipeline?.count ?? null,
         src.pipeline?.readAt ?? null),
    item("places", "Places calls (month)", src.places?.used ?? null,
         src.places?.readAt ?? null),
  ];
}

export interface AlertLine {
  key: string;
  text: string;
  health: "failing" | "unknown";
}

/** What the Alerts panel lists: every failing agent, and the alert channel
 *  as UNKNOWN when a FAILING alert had nowhere to go. */
export function alertPanel(agents: readonly RosterAgent[] | null): AlertLine[] {
  if (agents === null) {
    return [{ key: "roster", text: "agent roster: UNKNOWN (could not be read)",
              health: "unknown" }];
  }
  const lines: AlertLine[] = [];
  for (const a of agents) {
    const v = rosterRow(a);
    if (v.state === "failing") {
      lines.push({ key: `f-${a.name}`, health: "failing",
                   text: `${a.name}: FAILING (shift ${a.shift_state ?? "unknown"})` });
    } else if (v.state === "unknown") {
      lines.push({ key: `u-${a.name}`, health: "unknown",
                   text: `${a.name}: status UNKNOWN` });
    }
  }
  if (agents.some((a) => a.alert_unrouted)) {
    lines.push({ key: "channel", health: "unknown",
                 text: "alert channel: UNKNOWN (no Slack channel set; FAILING alerts "
                   + "were recorded but not delivered)" });
  }
  return lines;
}
