import { describe, expect, it } from "vitest";
import type { Turn } from "../voice/useVoice";
import { keepSpoken, pocketStatus, pocketTurns } from "./pocketModel";

const turn = (said: string, minute: number, answer: string | null = "ok"): Turn => ({
  said, answer, problem: answer ? null : "no answer",
  at: new Date(Date.UTC(2026, 8, 29, 10, minute)),
});

describe("pocketStatus", () => {
  it("follows the voice loop", () => {
    expect(pocketStatus("dormant", false, false)).toBe("Idle");
    expect(pocketStatus("waking", false, false)).toBe("Listening");
    expect(pocketStatus("listening", false, false)).toBe("Listening");
    expect(pocketStatus("thinking", false, false)).toBe("Thinking");
    expect(pocketStatus("responding", false, false)).toBe("Speaking");
    expect(pocketStatus("dismissing", false, false)).toBe("Idle");
  });

  it("a typed question is Thinking, then Speaking while it is read aloud", () => {
    expect(pocketStatus("dormant", true, false)).toBe("Thinking");
    expect(pocketStatus("dormant", false, true)).toBe("Speaking");
  });

  it("the voice loop wins over a typed turn", () => {
    expect(pocketStatus("listening", true, true)).toBe("Listening");
  });
});

describe("keepSpoken", () => {
  it("keeps turns across conversations, once each", () => {
    const first = [turn("a", 1), turn("b", 2)];
    let kept = keepSpoken([], first);
    kept = keepSpoken(kept, first);
    expect(kept.map((t) => t.said)).toEqual(["a", "b"]);
    // wake() emptied the log and a new conversation began
    kept = keepSpoken(kept, []);
    kept = keepSpoken(kept, [turn("c", 5)]);
    expect(kept.map((t) => t.said)).toEqual(["a", "b", "c"]);
  });

  it("returns the same array when nothing is new, so no re-render", () => {
    const kept = [turn("a", 1)];
    expect(keepSpoken(kept, [turn("a", 1)])).toBe(kept);
  });
});

describe("pocketTurns", () => {
  it("interleaves spoken and typed by time, oldest first", () => {
    const merged = pocketTurns([turn("s1", 1), turn("s2", 4)], [turn("t1", 2), turn("t2", 6)]);
    expect(merged.map((t) => t.said)).toEqual(["s1", "t1", "s2", "t2"]);
  });

  it("keeps only the latest turns", () => {
    const many = Array.from({ length: 12 }, (_, i) => turn(`q${i}`, i));
    expect(pocketTurns(many, [], 3).map((t) => t.said)).toEqual(["q9", "q10", "q11"]);
    expect(pocketTurns(many, [], 0)).toEqual([]);
  });

  it("does not show one exchange twice", () => {
    expect(pocketTurns([turn("a", 1)], [turn("a", 1)])).toHaveLength(1);
  });

  it("keeps a failed turn with its problem", () => {
    const [only] = pocketTurns([], [turn("why", 3, null)]);
    expect(only?.answer).toBeNull();
    expect(only?.problem).toBe("no answer");
  });
});
