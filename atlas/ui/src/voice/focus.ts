/**
 * Where a spoken answer points the dashboard -- as pure rules, no DOM.
 *
 * Three kinds of focus arrive from `/api/voice/ask`, and they differ in WHEN
 * the screen moves:
 *
 *   - a view command ("open the fleet") is itself a request to navigate, so
 *     it navigates now -- as it always has;
 *   - a system check brings the fleet up now, behind the checklist, so the
 *     screen is in focus while ATLAS speaks (15.3);
 *   - a spoken status that names a module ("how is flycheap doing") shows
 *     that module's tile BESIDE the answer, and only once the conversation
 *     is dismissed does the SPA go to its screen: moving the page out from
 *     under an answer he is still listening to would be the wrong way round.
 *
 * A screen that is not in the SPA's routing table (`SCREEN_ROUTES`) is
 * never navigated to, and a module name that is not a plain identifier is
 * never shown -- both arrive from the server, and neither is trusted to be a
 * path.
 */
import { SCREEN_ROUTES, type SystemCheck, type VoiceFocus } from "../api";

export interface FocusPlan {
  /** Navigate here as soon as the answer arrives. */
  now: string | null;
  /** Navigate here once the conversation closes. */
  onDismiss: string | null;
  /** Show this module's tile beside the answer. */
  card: string | null;
}

const NOWHERE: FocusPlan = { now: null, onDismiss: null, card: null };
const MODULE_NAME = /^[a-z][a-z0-9_]*$/;

/** The row-flip stagger of the system-check checklist, per row. */
export const CHECKLIST_STAGGER_MS = 60;
/** The longest the line waits for the checklist before it is typed. */
export const CHECKLIST_MAX_WAIT_MS = 3000;

/** The URL for a focus, or null when its screen is not one the SPA has. */
export function focusUrl(focus: VoiceFocus): string | null {
  if (!SCREEN_ROUTES.includes(focus.screen)) return null;
  const params = new URLSearchParams();
  if (focus.open) params.set("open", `${focus.open.kind}:${focus.open.id}`);
  if (focus.module && MODULE_NAME.test(focus.module)) params.set("focus", focus.module);
  const query = params.toString();
  return query ? `${focus.screen}?${query}` : focus.screen;
}

export function planFocus(focus: VoiceFocus | null | undefined): FocusPlan {
  if (!focus || typeof focus.screen !== "string") return NOWHERE;
  const url = focusUrl(focus);
  const module = focus.module && MODULE_NAME.test(focus.module) ? focus.module : null;
  if (module) return { now: null, onDismiss: url, card: module };
  if (!url) return NOWHERE;
  return { now: url, onDismiss: null, card: null };
}

/**
 * The conversation's focus after a reply: the reply's own, as a fresh
 * object (so the same command twice navigates twice), or -- when the reply
 * names nothing -- the one the conversation already had, unchanged. A
 * later "thanks, atlas" must not wipe the card, or the navigation promised
 * for when the conversation closes.
 */
export function carryFocus(
  previous: VoiceFocus | null, reply: VoiceFocus | null | undefined,
): VoiceFocus | null {
  if (reply && typeof reply.screen === "string") return { ...reply };
  return previous;
}

/**
 * The navigation owed when the conversation closes, after a focus arrives.
 * A new focus decides it (a view command's "now" leaves nothing owed; a
 * module's card owes its screen); no focus (null, a new conversation's
 * reset) leaves what was owed alone -- wake clears it separately.
 */
export function owedAfter(owed: string | null, focus: VoiceFocus | null): string | null {
  if (!focus) return owed;
  return planFocus(focus).onDismiss;
}

/** How long the spoken line waits for the checklist to finish flipping:
 *  one stagger per row, plus the last row's own flip, capped. */
export function checklistDelayMs(check: SystemCheck | null | undefined): number {
  if (!check) return 0;
  const rows = check.agents.length + check.modules.length;
  if (rows === 0) return 0;
  return Math.min(CHECKLIST_MAX_WAIT_MS, rows * CHECKLIST_STAGGER_MS + 250);
}

/** Positions for the node graph: the concierge at the centre, every other
 *  agent evenly around it. Pure, so the layout is testable. */
export function graphLayout(
  names: string[], size: number,
): { centre: string | null; nodes: Array<{ name: string; x: number; y: number }> } {
  const centre = names.includes("concierge") ? "concierge" : null;
  const around = names.filter((n) => n !== centre);
  const mid = size / 2;
  const radius = size / 2 - 18;
  return {
    centre,
    nodes: around.map((name, i) => {
      const angle = (2 * Math.PI * i) / Math.max(1, around.length) - Math.PI / 2;
      return {
        name,
        x: Math.round((mid + radius * Math.cos(angle)) * 10) / 10,
        y: Math.round((mid + radius * Math.sin(angle)) * 10) / 10,
      };
    }),
  };
}
