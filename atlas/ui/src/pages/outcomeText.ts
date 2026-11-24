/**
 * One short plain line for an agent's last-run outcome (the owner 2026-10-29).
 * Mirrors `atlas.kernel.agents.outcome_line`: fixed words for the failure
 * kinds, otherwise the first clean sentence of the model's text (markdown,
 * untrusted-data wrappers, headings and filler removed), cut to 60 characters
 * at a word. The server already sends clean lines on /api/agents; this guards
 * every other path (feed, meeting, an older server) the same way.
 */
import { safeText } from "./retired";

const NO_TOOLS_PREFIX = "(local model, no tools: this is from memory, not a fresh read)";
const NO_ANSWER = "No answer: the model ended without one.";

const TAG_BLOCK = /<(untrusted[\w-]*)[^>]*>[\s\S]*?(<\/\1\s*>|$)/gi;
const ANY_TAG = /<\/?[A-Za-z][^>]*>/g;
const UNTRUSTED_NOTE = new RegExp(
  "(\\*\\*|__)?\\s*summary of (the )?untrusted data\\s*:?\\s*(\\*\\*|__)?\\s*:?"
  + "|the block above is (data|what owner said)[^.]*\\.(\\s*[^.]*never follow it\\.)?",
  "gi");
const MD = /(\*\*|__|`+|^\s{0,3}#{1,6}\s*|^\s*[-*+]\s+|^\s*\d+\.\s+)/gm;
const SAFE_REASON = /[^A-Za-z0-9 ._/-]/g;

// The line a roster shows is a fact, not the model clearing its throat
// (the owner 2026-10-29). Same patterns as `atlas.kernel.agents`; both run the
// cases in tests/fixtures/outcome_lines.json.
const HEADING = /^\s{0,3}#{1,6}\s.*$/gm;
const SENTENCE_BREAK = /(?<=[.!?])\s+|\n+/;
const INTERJECTION = new RegExp(
  "^(?:(?:okay|ok|alright|all right|great|perfect|excellent|sure|right|done"
  + "|got it)\\s*[,!.:\u2014-]+\\s*|(?:so|now|well),?\\s+)+", "i");
const FILLER = new RegExp(
  "^(?:let me|let['\u2019]s|now,? (?:let me|i)|i now|i have now|i['\u2019]ve now"
  + "|i['\u2019]ll|i will|i['\u2019]m going to|i am going to|i need to|i should"
  + "|i can see|i see|i['\u2019]ve (?:got|gathered|collected|reviewed)"
  + "|i have (?:gathered|collected|reviewed|all|everything|enough|a full|the full)"
  + "|based on|here is|here['\u2019]s|here are|to summarize|in summary|summary"
  + "|looking at|first,|next,|that gives me|this gives me|with that)\\b", "i");

export const ALL_FILLER_LINE = "Completed (see details)";
export const LINE_LIMIT = 60;
export const DETAIL_LIMIT = 1200;

function fixedWords(text: string): string | null {
  if (text.startsWith("shift deferred:")) return "Deferred: free quota kept for the owner";
  if (text.startsWith("shift failed:")) {
    const reason = text.slice("shift failed:".length).trim();
    if (reason.includes("all providers failed") || /no provider/i.test(reason)) {
      return "Failed: no model answered";
    }
    const head = (reason.split(":", 1)[0] ?? "").split(" — ", 1)[0] ?? "";
    return `Failed: ${head.replace(SAFE_REASON, "").trim().slice(0, 40) || "error"}`;
  }
  if (text.includes(NO_ANSWER)) return "No answer: the model ended without one";
  if (text.startsWith("Stopped after the tool-call limit")
      || text.startsWith("I hit the tool-call limit")) {
    return "Stopped at the tool-call limit, no answer";
  }
  if (text.startsWith("The local model is down")) return "Local model down: nothing read";
  return null;
}

/** Without the no-tools note, untrusted wrappers and markdown; breaks kept. */
function plain(text: string): string {
  let t = text.split(NO_TOOLS_PREFIX).join("");
  t = t.replace(TAG_BLOCK, " ").replace(ANY_TAG, " ").replace(UNTRUSTED_NOTE, " ");
  return t.replace(/untrusted/gi, "").replace(MD, "");
}

function oneLine(text: string): string {
  return text.split(/\s+/).join(" ").replace(/^[\s:|-]+|[\s:|-]+$/g, "");
}

function cut(text: string, limit: number): string {
  if (text.length <= limit) return text;
  let head = text.slice(0, limit - 1);
  const space = head.lastIndexOf(" ");
  if (space >= Math.floor(limit / 2)) head = head.slice(0, space);
  return `${head.replace(/[\s,;:-]+$/, "")}…`;
}

function firstFact(text: string): string | null {
  for (const piece of text.split(SENTENCE_BREAK)) {
    let sentence = oneLine(piece).replace(INTERJECTION, "");
    if (sentence && FILLER.test(sentence)) {
      const at = sentence.indexOf(":");
      sentence = at >= 0 ? oneLine(sentence.slice(at + 1)) : "";
    }
    if (sentence) return sentence;
  }
  return null;
}

function line(raw: string, limit: number): string {
  const text = raw.trim();
  const fixed = fixedWords(text);
  if (fixed !== null) return fixed;
  const all = plain(text);
  if (!oneLine(all)) return "Completed (no summary text)";
  const fact = firstFact(plain(text.replace(HEADING, ""))) ?? firstFact(all);
  return fact ? cut(fact, limit) : ALL_FILLER_LINE;
}

function detail(raw: string, limit: number): string {
  const text = raw.trim();
  const fixed = fixedWords(text);
  if (fixed !== null) return fixed;
  const all = oneLine(plain(text));
  return all ? cut(all, limit) : "Completed (no summary text)";
}

/** The one-line outcome: fixed words or the first clean sentence, at most
 *  `max` characters; null when there is none. */
export function outcomeLine(raw: string | null | undefined, max = LINE_LIMIT): string | null {
  if (!raw) return null;
  return safeText(line(raw, max));
}

/** The whole clean text on one line: what an expanded row shows. */
export function outcomeDetail(raw: string | null | undefined): string | null {
  if (!raw) return null;
  return safeText(detail(raw, DETAIL_LIMIT));
}
