import { describe, expect, it } from "vitest";
import { isWakePhrase } from "./wakePhrase";
import { containsWake } from "./localWake";

const WAKES = [
  "atlas",
  "Atlas.",
  "ATLAS!",
  "hey atlas",
  "Hey, Atlas.",
  "hey atlas?",
  "ok atlas",
  "okay Atlas please",
  "Atlas, you up?",
  "atlas what's the status",
  "ok atlas what's new",
  "tell me something, hey atlas",
  "atlas atlas",
];

const STAYS_ASLEEP = [
  "the atlas report",
  "open the atlas report",
  "I read the atlas yesterday",
  "an atlas of europe",
  "atlas report",
  "atlas shrugged",
  "atlases are heavy",
  "the atlassian ticket",
  "At last, the report",
  "at last",
  "hey look at last quarter",
  "",
  "   ",
];

describe("isWakePhrase", () => {
  it.each(WAKES)("wakes on %j", (text) => {
    expect(isWakePhrase(text)).toBe(true);
  });

  it.each(STAYS_ASLEEP)("stays asleep on %j", (text) => {
    expect(isWakePhrase(text)).toBe(false);
  });
});

describe("the local gate and the browser recogniser share the one rule", () => {
  it.each([...WAKES, ...STAYS_ASLEEP])("agree on %j", (text) => {
    expect(containsWake(text)).toBe(isWakePhrase(text));
  });
});

describe("an interim result", () => {
  it("does not wake on the bare word that 'atlas report' begins as", () => {
    expect(isWakePhrase("atlas", false)).toBe(false);
    expect(isWakePhrase("atlas", true)).toBe(true);
  });

  it.each(["hey atlas", "atlas what's the status"])("still wakes at once on %j", (t) => {
    expect(isWakePhrase(t, false)).toBe(true);
  });
});
