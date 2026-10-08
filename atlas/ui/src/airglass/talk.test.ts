// "this", "here" and "that one" mean what the hand was doing when the word
// was said -- not when the sentence ended.
import { describe, expect, it } from "vitest";
import {
  GestureHistory, interpret, libraryQuery, resolveDeixis, utteranceEndingNow, wordTimes,
  type Named, type Snap, type Utterance,
} from "./talk";

const GLOBE: Named = { key: "tile:globe", name: "Globe" };
const KNOT: Named = { key: "tile:knot", name: "Torus knot" };
const CRYSTAL: Named = { key: "tile:crystal", name: "Crystal" };

/**
 * 0.0-2.0 s  holding the globe, cursor sliding from (200,200) to (400,200)
 * 2.0-4.0 s  let go; the cursor moves down to the tray, over the knot tile from 3.0 s
 * 4.0-6.0 s  over the crystal tile at (700, 600)
 */
function history(): GestureHistory {
  const h = new GestureHistory();
  for (let i = 0; i <= 600; i++) {
    const t = i / 100;
    let s: Snap;
    if (t < 2) {
      s = { t, cursor: { x: 200 + 100 * t, y: 200 }, held: GLOBE, shown: GLOBE, under: { ...GLOBE, kind: "model" } };
    } else if (t < 4) {
      const k = (t - 2) / 2;
      s = { t, cursor: { x: 400 + 100 * k, y: 200 + 400 * k }, held: null, shown: GLOBE,
        under: t >= 3 ? { ...KNOT, kind: "tile" } : null };
    } else {
      s = { t, cursor: { x: 700, y: 600 }, held: null, shown: GLOBE, under: { ...CRYSTAL, kind: "tile" } };
    }
    h.push(s);
  }
  return h;
}

describe("words get the moment they were said", () => {
  it("spreads words over the utterance by position, or uses the recogniser's times", () => {
    const w = wordTimes({ text: "put it here", start: 1, end: 2 });
    expect(w.map((x) => x.word)).toEqual(["put", "it", "here"]);
    expect(w[0]?.t).toBeLessThan(w[1]?.t ?? 0);
    expect(w[2]?.t).toBeGreaterThan(1.7);
    const given = wordTimes({ text: "put it here", start: 1, end: 2,
      words: [{ word: "put", t: 1.1 }, { word: "it", t: 1.2 }, { word: "here", t: 1.95 }] });
    expect(given.map((x) => x.t)).toEqual([1.1, 1.2, 1.95]);
  });

  it("the ⌘ conversation's words are taken as just said, at a speaking pace", () => {
    const u = utteranceEndingNow("make this bigger", 10);
    expect(u.end).toBe(10);
    expect(u.start).toBeCloseTo(10 - 0.3 - 0.99, 6);
  });
});

describe("resolving this / here / that one from the gesture state at the word", () => {
  const h = history();

  it("“it” said while holding the globe is the globe, even after letting go", () => {
    // "put it ... there": "it" at ~1.5 s (holding), "there" at ~3.9 s (over the tray)
    const u: Utterance = { text: "put it over there", start: 1.2, end: 4.0,
      words: [{ word: "put", t: 1.3 }, { word: "it", t: 1.5 }, { word: "over", t: 3.4 }, { word: "there", t: 3.9 }] };
    const d = resolveDeixis(u, h);
    expect(d.thing).toEqual(GLOBE);
    expect(d.thingAt).toBe(1.5);
    expect(d.place?.x).toBeCloseTo(400 + 100 * 0.95, 6);
    expect(d.place?.y).toBeCloseTo(200 + 400 * 0.95, 6);
  });

  it("“here” is where the cursor was when “here” was said, not at the end", () => {
    const u: Utterance = { text: "put it here", start: 0.5, end: 1.5,
      words: [{ word: "put", t: 0.6 }, { word: "it", t: 0.8 }, { word: "here", t: 1.0 }] };
    expect(resolveDeixis(u, h).place).toEqual({ x: 300, y: 200 });
  });

  it("“that one” is the tile under the cursor as it was said", () => {
    const early: Utterance = { text: "bring that one out", start: 3.2, end: 3.9,
      words: [{ word: "bring", t: 3.3 }, { word: "that", t: 3.45 }, { word: "one", t: 3.55 }, { word: "out", t: 3.8 }] };
    expect(resolveDeixis(early, h).tile).toEqual(KNOT);
    const late: Utterance = { text: "bring that one out", start: 4.2, end: 4.9 };
    expect(resolveDeixis(late, h).tile).toEqual(CRYSTAL);
  });

  it("“this” with nothing held is the model on show", () => {
    const u: Utterance = { text: "make this bigger", start: 2.5, end: 2.9 };
    expect(resolveDeixis(u, h).thing).toEqual(GLOBE);
  });
});

describe("what he meant", () => {
  const h = history();
  const names: Named[] = [GLOBE, KNOT, CRYSTAL];
  const said = (text: string, start: number, end: number) => interpret({ text, start, end }, h, names);

  it("simple object commands run locally, on the thing meant", () => {
    expect(said("make this bigger", 0.5, 1.2)).toEqual({ kind: "local", action: { verb: "bigger" }, thing: GLOBE });
    expect(said("smaller", 0.5, 0.8)).toMatchObject({ kind: "local", action: { verb: "smaller" } });
    expect(said("spin it slowly", 0.5, 1.4)).toMatchObject({ action: { verb: "spin", speed: "slow" } });
    expect(said("stop spinning", 0.5, 1.4)).toMatchObject({ action: { verb: "stop" } });
    expect(said("give it to ATLAS", 0.5, 1.4)).toMatchObject({ kind: "local", action: { verb: "give" } });
    expect(said("put it here", 0.5, 1.5)).toMatchObject({ kind: "local", action: { verb: "move" } });
    expect(said("bring that one out", 4.2, 4.9)).toMatchObject({ action: { verb: "pick", key: "tile:crystal" } });
    expect(said("show me the torus knot", 4.2, 4.9)).toMatchObject({ action: { verb: "pick", key: "tile:knot" } });
  });

  it("“what is this?” explains the hologram in hand; with nothing of Air Glass's, ATLAS looks", () => {
    expect(said("what is this", 0.5, 1.0)).toEqual({ kind: "local", action: { verb: "explain" }, thing: GLOBE });
    const empty = new GestureHistory();
    empty.push({ t: 0, cursor: { x: 1, y: 1 }, held: null, shown: null, under: null });
    expect(interpret({ text: "what is this?", start: 0.1, end: 0.6 }, empty)).toMatchObject({ kind: "look" });
  });

  it("“ATLAS, look” and “what am I holding” take the one still", () => {
    expect(said("ATLAS, look at what I'm holding", 0.2, 1.6)).toMatchObject({ kind: "look" });
    expect(said("what am I holding?", 0.2, 1.0)).toMatchObject({ kind: "look" });
    expect(said("Atlas what do you see", 0.2, 1.0)).toMatchObject({ kind: "look" });
    const look = said("look", 0.5, 0.7);
    expect(look.kind === "look" && look.context.held).toEqual(GLOBE);
  });

  it("a question about the thing goes to ATLAS with the context", () => {
    const i = said("how old is this kind of map", 0.5, 1.8);
    expect(i.kind).toBe("ask");
    expect(i.kind === "ask" && i.context.meant).toEqual(GLOBE);
  });

  it("addressed to ATLAS and about nothing in hand: the ordinary conversation", () => {
    expect(said("ATLAS, how is the agency doing today", 4.2, 5.5)).toEqual({
      kind: "general", question: "how is the agency doing today" });
  });

  it("“show me a car” asks the free library for a thing Air Glass has not got", () => {
    expect(said("show me a car", 4.2, 4.9)).toEqual({ kind: "library", query: "car" });
    expect(said("ATLAS, find a helmet please", 4.2, 5.0)).toEqual({ kind: "library", query: "helmet" });
    expect(said("can you get me the red armchair", 4.2, 5.0)).toEqual({ kind: "library", query: "red armchair" });
    expect(said("pull up a heart model", 4.2, 5.0)).toEqual({ kind: "library", query: "heart" });
  });

  it("…but never for its own things, a pointed-at tile, or the shelf's order", () => {
    expect(said("show me the globe", 4.2, 4.9)).toMatchObject({ kind: "local", action: { verb: "pick", key: "tile:globe" } });
    expect(said("show me the torus knot", 4.2, 4.9)).toMatchObject({ action: { verb: "pick", key: "tile:knot" } });
    expect(said("show me that one", 4.2, 4.9)).toMatchObject({ action: { verb: "pick", key: "tile:crystal" } });
    expect(said("show me the next one", 4.2, 4.9)).not.toMatchObject({ kind: "library" });
    expect(said("next one", 4.2, 4.9)).toMatchObject({ action: { verb: "next" } });
    expect(said("bring it here", 0.5, 1.5)).toMatchObject({ action: { verb: "move" } });
    expect(said("give the globe to ATLAS", 0.5, 1.5)).toMatchObject({ action: { verb: "give" } });
    expect(said("ATLAS, show me my sales", 4.2, 5.0)).toMatchObject({ kind: "general" });
    expect(said("ATLAS, show me how this works", 4.2, 5.0)).not.toMatchObject({ kind: "library" });
  });

  it("the library phrase alone, unit by unit", () => {
    expect(libraryQuery("show me a car")).toBe("car");
    expect(libraryQuery("bring me an old lantern")).toBe("old lantern");
    expect(libraryQuery("show me this")).toBeNull();
    expect(libraryQuery("show me air glass")).toBeNull();
    expect(libraryQuery("show me")).toBeNull();
    expect(libraryQuery("I'll show you a car later")).toBeNull();
  });

  it("the dashboard screens ATLAS Menu opens stay the Menu's", () => {
    for (const s of ["show me the fleet", "show the brain", "show me memory", "show me marketing",
      "ATLAS, show me your face"]) {
      expect(libraryQuery(s.replace(/^ATLAS, /, ""))).toBeNull();
      const i = interpret(utteranceEndingNow(s, 10), new GestureHistory());
      expect(i.kind === "general" || i.kind === "ignore").toBe(true);
    }
  });

  it("the room's chatter, not addressed and not about the thing, is ignored", () => {
    expect(said("I'll call you back after lunch", 4.2, 5.5)).toMatchObject({ kind: "ignore" });
    expect(said("atlas", 4.2, 4.5)).toMatchObject({ kind: "ignore" });
  });
});
