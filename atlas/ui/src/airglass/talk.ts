// Speech understood together with what the hand is doing (the owner, 2026-10-06:
// "I should be able to speak in Air Glass with the movements so he
// understands better").
//
// Pure: no DOM, no network. The controller pushes a snapshot of the gesture
// state every frame into a GestureHistory; when he stops speaking, each word
// is given the moment it was said, and "this" / "it" resolve to what the
// hand held (or was over) at THAT moment, "here" / "there" to where the
// cursor was, and "that one" to the tile under it. Simple object commands
// run locally with no model round trip; a question about the thing goes to
// ATLAS with a small JSON of that context; "look" takes the one still.
import type { Pt } from "./gestures";

export interface Named { key: string; name: string }
export type UnderKind = "tile" | "button" | "model";

/** The gesture state at one instant. */
export interface Snap {
  t: number;
  cursor: Pt | null;
  /** The model being held right now (pinched), if any. */
  held: Named | null;
  /** The model on show, held or not. */
  shown: Named | null;
  /** What the cursor is over. */
  under: (Named & { kind: UnderKind }) | null;
}

/** A ring of recent snapshots, oldest first. */
export class GestureHistory {
  private snaps: Snap[] = [];
  constructor(private readonly keepS = 15) {}
  push(s: Snap): void {
    this.snaps.push(s);
    const cut = s.t - this.keepS;
    while (this.snaps.length > 1 && (this.snaps[0]?.t ?? cut) < cut) this.snaps.shift();
  }
  /** The state at time t: the last snapshot at or before t (the first, when
   * t is older than the history; null when there is none). */
  at(t: number): Snap | null {
    let lo = 0, hi = this.snaps.length - 1, best = -1;
    while (lo <= hi) {
      const m = (lo + hi) >> 1;
      if ((this.snaps[m]?.t ?? Infinity) <= t) { best = m; lo = m + 1; } else hi = m - 1;
    }
    return this.snaps[best] ?? this.snaps[0] ?? null;
  }
  get latest(): Snap | null { return this.snaps[this.snaps.length - 1] ?? null; }
  get size(): number { return this.snaps.length; }
}

/** One thing he said, on the same clock as the history (seconds). */
export interface Utterance {
  text: string;
  start: number;
  end: number;
  /** When each word was heard, if the recogniser said (on-device interim
   * results do); otherwise words are spread over [start, end] by position. */
  words?: ReadonlyArray<{ word: string; t: number }>;
}

export interface TimedWord { word: string; t: number; index: number }

/** The words of an utterance and the moment each was said. */
export function wordTimes(u: Utterance): TimedWord[] {
  const out: TimedWord[] = [];
  const re = /[a-z0-9']+/gi;
  const text = u.text;
  const len = Math.max(1, text.length);
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    const word = m[0].toLowerCase().replace(/'/g, "");
    const given = u.words?.[i];
    const midChar = m.index + m[0].length / 2;
    const t = given && given.word.toLowerCase().replace(/'/g, "") === word ? given.t
      : u.start + (u.end - u.start) * (midChar / len);
    out.push({ word, t, index: i });
    i += 1;
  }
  return out;
}

export interface Deixis {
  /** "this" / "it": what the hand held, or was over, as the word was said. */
  thing: Named | null;
  thingAt: number | null;
  /** "here" / "there": where the cursor was as the word was said. */
  place: Pt | null;
  placeAt: number | null;
  /** "that one": the tile under the cursor as it was said. */
  tile: Named | null;
  tileAt: number | null;
}

const THING_WORDS = new Set(["this", "it", "that", "these", "those"]);
const PLACE_WORDS = new Set(["here", "there"]);

/** Resolve the pointing words of an utterance against the gesture history. */
export function resolveDeixis(u: Utterance, history: GestureHistory): Deixis {
  const out: Deixis = { thing: null, thingAt: null, place: null, placeAt: null, tile: null, tileAt: null };
  const words = wordTimes(u);
  words.forEach((w, i) => {
    const next = words[i + 1]?.word;
    if (w.word === "that" && next === "one") {
      const s = history.at(w.t);
      if (s?.under?.kind === "tile" && !out.tile) {
        out.tile = { key: s.under.key, name: s.under.name };
        out.tileAt = w.t;
      }
    }
    if (THING_WORDS.has(w.word) && !out.thing) {
      const s = history.at(w.t);
      if (!s) return;
      const overTile = s.under?.kind === "tile" ? { key: s.under.key, name: s.under.name } : null;
      const thing = s.held ?? (s.under?.kind === "model" ? s.shown : null)
        ?? (w.word === "that" ? overTile : null) ?? s.shown ?? overTile;
      if (thing) { out.thing = thing; out.thingAt = w.t; }
    }
    if (PLACE_WORDS.has(w.word) && !out.place) {
      const s = history.at(w.t);
      if (s?.cursor) { out.place = { x: s.cursor.x, y: s.cursor.y }; out.placeAt = w.t; }
    }
  });
  return out;
}

export type LocalAction =
  | { verb: "bigger" | "smaller" | "give" | "explain" | "reset" | "next" | "stop" }
  | { verb: "spin"; speed: "slow" | "normal" | "fast" }
  | { verb: "move"; to: Pt }
  | { verb: "pick"; key: string };

/** What the page tells the server about the hand, with a question. */
export interface GestureContext {
  held: Named | null;
  shown: Named | null;
  under: (Named & { kind: UnderKind }) | null;
  /** The thing "this" / "it" meant, when he said it. */
  meant: Named | null;
  cursor: Pt | null;
  place: Pt | null;
}

export type Intent =
  | { kind: "local"; action: LocalAction; thing: Named | null }
  | { kind: "look"; question: string; context: GestureContext }
  | { kind: "ask"; question: string; context: GestureContext }
  /** "show me a car": search the free library for it and bring out the first. */
  | { kind: "library"; query: string }
  | { kind: "general"; question: string }
  | { kind: "ignore"; why: string };

export const norm = (s: string): string =>
  s.toLowerCase().replace(/[’']/g, "").replace(/[^a-z0-9\s]/g, " ").replace(/\s+/g, " ").trim();

const ADDRESS = /^(?:(?:hey|ok|okay|so|um|uh)\s+)?atlas\b\s*/;
const LOOK = /\b(look|have a look|what (am i|im|i am) holding|what do you see|what can you see|can you see (this|it|that|me)|whats in my hand|what is in my hand)\b/;
const GIVE = /\b(give (it|this|that)( one)?( to atlas)?|give .{0,20}to atlas|hand it over|take (it|this|that)( one)?$)\b/;
const MOVE = /\b(put|move|place|drop|bring|set|leave) (it|this|that)( one)?\b.*\b(here|there|over here|over there)\b/;
const STOP = /\b(stop( spinning| turning| it)?|freeze|hold still|stay still|dont spin)\b/;
const SPIN = /\b(spin|rotate|turn)\b/;
const BIGGER = /\b(bigger|larger|grow|enlarge|zoom in|scale up)\b/;
const SMALLER = /\b(smaller|shrink|zoom out|scale down|tinier)\b/;
const RESET = /\b(reset|start over)\b/;
const NEXT = /\b(next|another one|next one|switch)\b/;
const BRING_ONE = /\b(bring|show|pick|take|get|pull) (me )?(that|this) one\b|\bthat one\b/;
const EXPLAIN = /\b(explain|what is (it|this|that)|whats (this|that|it)|tell me about (it|this|that)|what is this thing)\b/;
const QUESTION = /^(what|whats|how|hows|why|who|whos|where|wheres|when|which|is|are|can|could|does|do|did|tell|describe|should|would|will)\b/;
const FETCH = /^(?:(?:can you|could you|please)\s+)?(?:show|bring|get|find|fetch|pull up)(?: me)?\s+(.+)$/;
// The first word of a thing that is not something to look up: a pointing
// word ("show me that one"), a question ("show me how"), his own things
// ("show me my sales"), or the shelf's own order ("show me the next one").
const NOT_A_THING = /^(this|that|it|these|those|them|him|her|me|us|what|whats|how|why|who|where|when|which|my|your|our|his|their|next|another|previous|last|first|more|everything|all|something|anything|air ?glass|atlas)\b/;
// The dashboard screens ATLAS Menu opens by voice (atlas/api/voice.py VIEW
// and SCREENS: "show me the fleet", "show me the brain"). The Menu offers
// every phrase to Air Glass first while its window is open, so these must
// stay the Menu's -- typed, the library still finds a brain.
const SCREEN = /^(fleet|brain|memory|marketing|knowledge|mission control)$/;

/**
 * The thing in "show me a car" / "find a helmet" / "get me the red chair",
 * articles and fillers dropped; null when the words are not a request for a
 * thing to look up.
 */
export function libraryQuery(t: string): string | null {
  const m = FETCH.exec(norm(t));
  let q = (m?.[1] ?? "").replace(/^(?:(?:a|an|the|some)\s+)+/, "")
    .replace(/\s+(?:please|for me|hologram|model|in 3d|in three d)$/g, "").trim();
  q = q.replace(/^(?:a|an|the|some)\s+/, "").trim();
  if (!q || NOT_A_THING.test(q) || SCREEN.test(q) || q.split(" ").length > 6) return null;
  return q;
}

/**
 * What he meant. `names`: everything that can be picked by saying its name.
 * Words not addressed to ATLAS and not about what is in his hand are
 * ignored: the page listens all the time, and the room is not talking to it.
 */
export function interpret(u: Utterance, history: GestureHistory,
                          names: readonly Named[] = []): Intent {
  const raw = norm(u.text);
  if (!raw) return { kind: "ignore", why: "nothing heard" };
  const addressed = ADDRESS.test(raw);
  const t = raw.replace(ADDRESS, "").trim();
  if (!t) return { kind: "ignore", why: "only the name" };
  const d = resolveDeixis(u, history);
  const now = history.at(u.end);
  const context: GestureContext = {
    held: now?.held ?? null, shown: now?.shown ?? null, under: now?.under ?? null,
    meant: d.thing, cursor: now?.cursor ?? null, place: d.place,
  };
  const thing = d.thing ?? now?.held ?? now?.shown ?? null;
  const local = (action: LocalAction): Intent => ({ kind: "local", action, thing });

  if (LOOK.test(t)) return { kind: "look", question: t, context };
  const hit = names
    .map((n) => ({ n, w: norm(n.name) }))
    .filter(({ w }) => w && new RegExp(`\\b${w.replace(/ /g, "\\s")}\\b`).test(t))
    .sort((a, b) => b.w.length - a.w.length)[0];
  // "show me a car": a thing Air Glass has not got, from the free library --
  // unless it is one of its own ("show me the globe") or one he points at.
  const wanted = libraryQuery(t);
  if (wanted && !hit && !MOVE.test(t)) return { kind: "library", query: wanted };
  if (MOVE.test(t)) {
    return d.place ? local({ verb: "move", to: d.place })
      : { kind: "ignore", why: "“here” without a cursor in view" };
  }
  if (GIVE.test(t)) return local({ verb: "give" });
  if (STOP.test(t)) return local({ verb: "stop" });
  if (SPIN.test(t)) {
    const speed = /\b(slow|slowly|slower|gently)\b/.test(t) ? "slow"
      : /\b(fast|faster|quick|quickly)\b/.test(t) ? "fast" : "normal";
    return local({ verb: "spin", speed });
  }
  if (BIGGER.test(t)) return local({ verb: "bigger" });
  if (SMALLER.test(t)) return local({ verb: "smaller" });
  if (RESET.test(t)) return local({ verb: "reset" });
  if (BRING_ONE.test(t) && d.tile) return { kind: "local", action: { verb: "pick", key: d.tile.key }, thing: d.tile };
  if (EXPLAIN.test(t)) {
    // A hologram in hand (or pointed at): its own words, read fresh. Nothing
    // of Air Glass's in play: he means something real he is holding up.
    return d.thing ? { kind: "local", action: { verb: "explain" }, thing: d.thing }
      : { kind: "look", question: t, context };
  }
  if (hit && (/\b(show|bring|get|pick|open|take out)\b/.test(t) || t === norm(hit.n.name))) {
    return { kind: "local", action: { verb: "pick", key: hit.n.key }, thing: hit.n };
  }
  if (NEXT.test(t) && t.split(" ").length <= 4) return local({ verb: "next" });
  if (d.thing && QUESTION.test(t)) return { kind: "ask", question: t, context };
  if (addressed) return { kind: "general", question: t };
  return { kind: "ignore", why: "not addressed to ATLAS" };
}

/** For ATLAS Menu's ⌘ conversation, which hands over finished words with no
 * timing: assume they were just said, at a speaking pace. */
export function utteranceEndingNow(text: string, now: number): Utterance {
  const words = Math.max(1, text.trim().split(/\s+/).length);
  return { text, start: now - (0.3 + 0.33 * words), end: now };
}
