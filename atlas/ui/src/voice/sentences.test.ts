import { describe, expect, it } from "vitest";
import { sentencesOf } from "./loop";

describe("sentencesOf: a reply voiced one sentence at a time", () => {
  it("splits on sentence ends so the first is heard first", () => {
    expect(sentencesOf("VPSie's fine, as of 7:52. Billing is the one to watch this week."))
      .toEqual(["VPSie's fine, as of 7:52.", "Billing is the one to watch this week."]);
  });

  it("joins a short sentence to the next, so Fish is never asked for one word", () => {
    expect(sentencesOf("Sure. The reels posted at nine and the clips at ten."))
      .toEqual(["Sure. The reels posted at nine and the clips at ten."]);
  });

  it("is at most four parts", () => {
    const long = Array.from({ length: 7 }, (_, i) => `This is sentence number ${i + 1}.`).join(" ");
    const parts = sentencesOf(long);
    expect(parts).toHaveLength(4);
    expect(parts.join(" ")).toBe(long);
  });

  it("keeps a reply with no sentence end whole", () => {
    expect(sentencesOf("one line with no stop")).toEqual(["one line with no stop"]);
    expect(sentencesOf("")).toEqual([]);
  });
});
