/**
 * What counts as saying ATLAS's name to wake him -- one rule for both phrase
 * detectors (the on-device whisper gate and the browser's SpeechRecognition),
 * so they can never disagree.
 *
 * the owner, 2026-11-10: "Atlas" and "Hey Atlas" both wake him, and wake him at
 * once. But the word is also an ordinary one -- "the atlas report", "an atlas
 * of Europe" -- and a microphone that is always listening must not open the
 * HUD on those. So the bare word wakes only when it is plainly addressed to
 * him:
 *
 *   - it is the whole utterance ("Atlas", "Hey Atlas", "ok atlas please");
 *   - it follows a greeting anywhere ("... hey atlas");
 *   - it opens the utterance and a request follows ("Atlas, what's the
 *     status", "atlas you there").
 *
 * "The atlas report", "atlas report", "atlases" and "Atlassian" do not wake.
 * "at last" (what whisper writes for a mumble) never does either.
 */

const GREETING = "(?:hey|hay|hi|hello|ok|okay|yo)";

/** Words that open a request, so "atlas <word> ..." is a person talking TO him
 *  and not a noun phrase. Deliberately a short closed list. */
const REQUEST_OPENERS = [
  "what", "whats", "how", "hows", "where", "who", "when", "why", "which",
  "can", "could", "would", "will", "should", "do", "does", "did", "are", "is",
  "you", "show", "open", "tell", "give", "run", "check", "start", "stop",
  "read", "list", "find", "search", "play", "turn", "set", "remind", "send",
  "call", "go", "take", "wake", "listen", "status", "please", "i", "im", "let",
  "make", "bring", "pull", "look", "any", "anything", "good",
];

const WHOLE = new RegExp(
  `^(?:${GREETING}\\s+)?(?:atlas\\s+)*atlas(?:\\s+(?:please|are you there|you there|wake up|hello|hi))?$`,
);
const AFTER_GREETING = new RegExp(`\\b${GREETING}\\s+atlas\\b`);
const OPENS_REQUEST = new RegExp(`^atlas\\s+(?:${REQUEST_OPENERS.join("|")})\\b`);

/** Lower-case, letters only: "Hey, Atlas!" and "hey atlas" read the same. */
export function normalise(text: string): string {
  return text
    .toLowerCase()
    .replace(/['’]/g, "")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

/**
 * True when `text` is the owner addressing ATLAS: "Atlas" or "Hey Atlas".
 *
 * `final` is false for a recogniser's interim result: "atlas report" first
 * arrives as the interim "atlas", so the bare word on its own wakes only once
 * the phrase is final. "Hey atlas" and "atlas, what's ..." need no wait.
 */
export function isWakePhrase(text: string, final = true): boolean {
  const said = normalise(text);
  if (!said) return false;
  return (final && WHOLE.test(said)) || AFTER_GREETING.test(said)
    || OPENS_REQUEST.test(said);
}
