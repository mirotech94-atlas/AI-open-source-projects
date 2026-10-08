import { describe, expect, it } from "vitest";
import type { Phase } from "./loop";
import { planInterrupt, tapAction, tapLabel } from "./interrupt";

const PHASES: Phase[] = ["dormant", "waking", "listening", "thinking", "responding",
  "dismissing"];

describe("planInterrupt", () => {
  it("talking over an answer stops everything ATLAS could still say, and records now", () => {
    const plan = planInterrupt("responding", "voice");
    expect(plan).toEqual({
      stopPlayback: true, abortSpeech: true, dropHeld: true, abortAsk: false,
      startRecording: true, alreadySpeaking: true,
    });
  });

  it("a tap while speaking does the same, without assuming he has started talking", () => {
    const plan = planInterrupt("responding", "tap");
    expect(plan?.stopPlayback).toBe(true);
    expect(plan?.abortSpeech).toBe(true);
    expect(plan?.dropHeld).toBe(true);
    expect(plan?.startRecording).toBe(true);
    expect(plan?.alreadySpeaking).toBe(false);
  });

  it("a tap while thinking drops the reply in flight and listens again", () => {
    const plan = planInterrupt("thinking", "tap");
    expect(plan?.abortAsk).toBe(true);
    expect(plan?.abortSpeech).toBe(true);
    expect(plan?.startRecording).toBe(true);
  });

  it("the voice never interrupts a question still being thought about", () => {
    // Nothing is playing then, so there is no echo to tell him apart from --
    // and his own last words are what ended the turn.
    expect(planInterrupt("thinking", "voice")).toBeNull();
  });

  it("there is nothing to interrupt while listening, waking, dormant or closing", () => {
    for (const phase of ["dormant", "waking", "listening", "dismissing"] as Phase[]) {
      expect(planInterrupt(phase, "voice")).toBeNull();
      expect(planInterrupt(phase, "tap")).toBeNull();
    }
  });
});

describe("tapAction", () => {
  it("wakes when dormant, stops ATLAS while it thinks or speaks, dismisses otherwise", () => {
    expect(PHASES.map(tapAction)).toEqual(
      ["wake", "dismiss", "dismiss", "interrupt", "interrupt", "none"]);
  });

  it("says 'Tap to stop' exactly when a tap would stop ATLAS", () => {
    for (const phase of PHASES) {
      expect(tapLabel(phase) === "Tap to stop").toBe(tapAction(phase) === "interrupt");
    }
  });
});
