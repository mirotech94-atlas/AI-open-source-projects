// The three-step coach shown on first open. Pure, so the order rules are
// tested (coach.test.ts): a step is ticked when the person (or the demo hand)
// actually does it, in whatever order, and the coach finishes on its own once
// all three are done. Dismissing it is remembered in this browser only.

export type CoachEvent = "picked" | "grabbed" | "given";
export interface CoachState { done: readonly CoachEvent[]; dismissed: boolean }

export const COACH_STEPS: ReadonlyArray<{ event: CoachEvent; title: string; detail: string }> = [
  { event: "picked", title: "Pinch and slide up", detail: "Pinch a model in the tray, either hand, and slide it up: it comes out." },
  { event: "grabbed", title: "Slide to move and turn", detail: "Pinch the model and slide: it follows, and turns like a ball. Let go: it stays." },
  { event: "given", title: "Let go over ATLAS", detail: "Slide it onto ATLAS and let go. Say “ATLAS, look” to ask about what you hold." },
];

export const COACH_KEY = "atlas.airglass.coach";

export function coachInitial(stored: string | null): CoachState {
  return { done: [], dismissed: stored === "done" };
}

export function coachAdvance(s: CoachState, e: CoachEvent): CoachState {
  if (s.done.includes(e)) return s;
  const done = [...s.done, e];
  return { done, dismissed: s.dismissed || done.length === COACH_STEPS.length };
}

/** 0-based index of the step to highlight, or null when the coach is gone. */
export function coachCurrent(s: CoachState): number | null {
  if (s.dismissed) return null;
  const i = COACH_STEPS.findIndex((step) => !s.done.includes(step.event));
  return i < 0 ? null : i;
}
