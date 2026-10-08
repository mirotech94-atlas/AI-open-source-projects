import { describe, expect, it } from "vitest";
import { commandSupport, Endpointer, InterimTracker, SILENCE_S } from "./voice";
import { coachAdvance, coachCurrent, coachInitial } from "./coach";

describe("the turn ends when he stops speaking (~0.7 s)", () => {
  /** Feed a level track at 20 Hz: quiet room, speech, quiet. */
  const feed = (track: Array<[number, number]>): ReturnType<Endpointer["feed"]>[] => {
    const e = new Endpointer();
    const out: ReturnType<Endpointer["feed"]>[] = [];
    let t = 0;
    for (const [dur, level] of track) {
      for (let s = 0; s < dur; s += 0.05) { t += 0.05; const ev = e.feed(level, t, 0.05); if (ev) out.push({ ...ev }); }
    }
    return out;
  };

  it("an onset, then the end 0.7 s after the last loud frame", () => {
    const evs = feed([[2, 0.02], [1.2, 0.4], [1.5, 0.02]]);
    expect(evs.map((e) => e?.type)).toEqual(["onset", "end"]);
    const end = evs[1];
    expect(end?.type === "end" && end.end - end.start).toBeGreaterThan(1.0);
    expect(end?.type === "end" && end.end).toBeCloseTo(3.2, 1);
  });

  it("a short pause inside a sentence does not end it", () => {
    const evs = feed([[1, 0.02], [0.8, 0.4], [0.4, 0.02], [0.8, 0.4], [1.2, 0.02]]);
    expect(evs.map((e) => e?.type)).toEqual(["onset", "end"]);
  });

  it("a click is dropped, not sent", () => {
    expect(feed([[1, 0.02], [0.15, 0.5], [1.2, 0.02]]).map((e) => e?.type)).toEqual(["onset", "drop"]);
  });

  it("on-device: words are stamped when first heard, and the turn ends on 0.7 s of no change", () => {
    const tr = new InterimTracker();
    tr.update("put", 1.0);
    tr.update("put it", 1.2);
    tr.update("put it here", 1.6);
    expect(tr.poll(1.6 + SILENCE_S - 0.05)).toBeNull();
    const u = tr.poll(1.6 + SILENCE_S);
    expect(u?.text).toBe("put it here");
    expect(u?.words?.map((w) => w.t)).toEqual([1.0, 1.2, 1.6]);
    expect(tr.pending).toBe(false);
  });

  it("the recogniser's late final for words already acted on does not act again", () => {
    const tr = new InterimTracker();
    tr.update("make this", 1.0, 0);
    tr.update("make this bigger", 1.3, 0);
    expect(tr.poll(2.0)?.text).toBe("make this bigger");      // ended on 0.7 s of quiet
    tr.update("Make this bigger.", 2.2, 0, true);             // its own final, late
    expect(tr.poll(3.0)).toBeNull();
    expect(tr.poll(4.0)).toBeNull();
    tr.update("smaller", 5.0, 1);                              // the next thing he says
    expect(tr.poll(5.7)?.text).toBe("smaller");
  });

  it("a final that comes first ends the turn at once", () => {
    const tr = new InterimTracker();
    tr.update("put it", 1.0, 0);
    tr.update("put it here", 1.2, 0, true);
    expect(tr.poll(1.25)?.text).toBe("put it here");
    expect(tr.poll(2.5)).toBeNull();
  });

  it("words heard while ATLAS spoke are dropped, and a new session counts from 0", () => {
    const tr = new InterimTracker();
    tr.update("give it to atlas", 1.0, 0);
    tr.clear();                                                // ATLAS was talking
    tr.update("give it to atlas.", 1.4, 0, true);
    expect(tr.poll(3)).toBeNull();
    tr.restart();
    tr.update("look", 4.0, 0, true);
    expect(tr.poll(4.1)?.text).toBe("look");
  });
});

describe("listening", () => {
  it("never claims on-device recognition where the browser cannot promise it", async () => {
    expect(await commandSupport()).toBe("unsupported");
  });
});

describe("coach", () => {
  it("starts on step one, ticks steps in any order, and finishes itself", () => {
    let s = coachInitial(null);
    expect(coachCurrent(s)).toBe(0);
    s = coachAdvance(s, "grabbed");
    expect(coachCurrent(s)).toBe(0);
    s = coachAdvance(s, "picked");
    expect(coachCurrent(s)).toBe(2);
    expect(coachAdvance(s, "picked")).toBe(s);
    s = coachAdvance(s, "given");
    expect(s.dismissed).toBe(true);
    expect(coachCurrent(s)).toBeNull();
  });

  it("stays away once dismissed in this browser", () => {
    expect(coachCurrent(coachInitial("done"))).toBeNull();
  });
});
