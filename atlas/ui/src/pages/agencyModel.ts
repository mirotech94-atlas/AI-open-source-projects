import type { AgentSummary, Meeting, MeetingExchange, SpeakVoice } from "../api";
import { FRONT_DOOR_AGENT } from "./roster";
import { outcomeLine } from "./outcomeText";

/** One spoken line of the meeting, in order: ATLAS opens, each agent takes
 *  its turn, ATLAS closes. `key` is "atlas-open", the agent's roster name,
 *  or "atlas-close" -- what the page highlights while it plays. */
export interface ScriptLine {
  key: string;
  speaker: string;
  voice: SpeakVoice;
  text: string;
}

export const OPENING = "atlas-open";
export const CLOSING = "atlas-close";

/** ATLAS opens, each agent answers the day's question, the conversation's
 *  lines follow in thread order (keyed by exchange id), ATLAS closes. */
export function meetingScript(m: Meeting): ScriptLine[] {
  const lines: ScriptLine[] = [
    { key: OPENING, speaker: "ATLAS", voice: "atlas", text: m.opening.text },
  ];
  for (const t of m.turns) {
    lines.push({ key: t.agent, speaker: t.persona.name, voice: t.voice, text: t.speech });
  }
  for (const x of m.exchanges ?? []) {
    lines.push({ key: x.id, speaker: x.speaker_name, voice: x.voice, text: x.text });
  }
  lines.push({ key: CLOSING, speaker: "ATLAS", voice: "atlas", text: m.closing.text });
  return lines.filter((l) => l.text.trim().length > 0);
}

/** The conversation as threads, in order: each a question and its reply. */
export function threadsOf(exchanges: MeetingExchange[] | undefined): MeetingExchange[][] {
  const byThread = new Map<number, MeetingExchange[]>();
  for (const x of exchanges ?? []) {
    const thread = byThread.get(x.thread) ?? [];
    thread.push(x);
    byThread.set(x.thread, thread);
  }
  return [...byThread.keys()].sort((a, b) => a - b).map((k) => byThread.get(k) ?? []);
}

function hhmm(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

/** Where the meeting on screen came from, and when: a stored one says when
 *  it was built and when its records were read; a live one says it is live. */
export function provenance(m: Meeting): string {
  if (m.source === "stored" && m.built_at) {
    return `This morning's meeting · built ${hhmm(m.built_at)} · records read ${hhmm(m.read_at)}`;
  }
  return `Live · rebuilt from the records · read ${hhmm(m.read_at)}`;
}

/** "Next shift in 3h", computed from `next.at` now -- so a meeting stored at
 *  07:00 does not still say "in 3 hours" in the afternoon. Without a time,
 *  the turn's own words ("No shift until it is ready."). */
export function nextShiftLine(next: { at: string | null; text: string },
                              now: number = Date.now()): string {
  if (!next.at) return next.text;
  const mins = Math.floor((new Date(next.at).getTime() - now) / 60000);
  if (mins <= 0) return "Next shift is due now.";
  const text = mins < 60 ? `${mins}m` : `${Math.floor(mins / 60)}h`;
  return `Next shift in ${text}.`;
}

/** Speaks one line; resolves when it has finished (or was stopped), rejects
 *  with a readable reason when it could not be spoken at all. */
export type SpeakOne = (line: ScriptLine, signal: AbortSignal) => Promise<string | null | void>;

/** The env var behind each persona voice -- named in the note the page
 *  shows when the server spoke a line in ATLAS's voice instead. */
const VOICE_ENV: Record<string, string> = {
  female: "FISH_VOICE_FEMALE", male: "FISH_VOICE_MALE",
};

/** A non-fatal note when the voice that actually spoke (the server's
 *  `X-Voice` header) is not the one asked for, else null. */
export function voiceNote(asked: string, spoke: string | null): string | null {
  if (!spoke || spoke === asked) return null;
  const env = VOICE_ENV[asked];
  return env
    ? `${env} is not set on the server, so ${asked}-voiced seats speak in ATLAS's voice.`
    : `Asked for the ${asked} voice; the server spoke in the ${spoke} voice.`;
}

export type PlayResult =
  | { kind: "finished" }
  | { kind: "stopped" }
  | { kind: "failed"; at: string; reason: string };

/** Plays the script in order, one line at a time. Stops at the first line
 *  that cannot be spoken rather than skipping it silently, and stops at
 *  once when `signal` aborts. `onLine` is told who is speaking (null when
 *  done). */
export async function playScript(
  script: ScriptLine[],
  speakOne: SpeakOne,
  onLine: (key: string | null) => void,
  signal: AbortSignal,
  onNote: (note: string) => void = () => undefined,
): Promise<PlayResult> {
  try {
    for (const line of script) {
      if (signal.aborted) return { kind: "stopped" };
      onLine(line.key);
      try {
        const note = await speakOne(line, signal);
        if (note) onNote(note);
      } catch (err) {
        if (signal.aborted) return { kind: "stopped" };
        return { kind: "failed", at: line.key,
                 reason: err instanceof Error ? err.message : String(err) };
      }
    }
    return signal.aborted ? { kind: "stopped" } : { kind: "finished" };
  } finally {
    onLine(null);
  }
}

/** The page's speaker: POST /api/voice/speak with the line's voice, then
 *  play the mp3 as a blob -- the same fetch → blob → Audio approach as
 *  `useVoice`'s `speak()`, but its own element, so it never fights the
 *  voice overlay's. Aborting cancels the fetch or pauses the audio. */
export const speakOverHttp: SpeakOne = async (line, signal) => {
  const response = await fetch("/api/voice/speak", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: line.text, voice: line.voice }),
    credentials: "same-origin",
    signal,
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: string };
    throw new Error(body.detail ?? `Speech failed (${response.status}).`);
  }
  const note = voiceNote(line.voice, response.headers.get("x-voice"));
  const blob = await response.blob();
  if (signal.aborted) return note;
  const url = URL.createObjectURL(blob);
  const element = new Audio(url);
  const stop = (): void => element.pause();   // `pause` settles the promise
  try {
    await new Promise<void>((resolve, reject) => {
      signal.addEventListener("abort", stop, { once: true });
      element.onended = () => resolve();
      element.onpause = () => resolve();
      element.onerror = () => reject(new Error("The browser could not play the audio."));
      element.play().catch((err: unknown) => {
        reject(new Error(
          err instanceof Error && err.name === "NotAllowedError"
            ? "The browser held the voice back — click Play meeting again."
            : `Could not play the audio — ${err instanceof Error ? err.message : String(err)}`));
      });
    });
  } finally {
    signal.removeEventListener("abort", stop);
    element.onended = element.onpause = element.onerror = null;
    element.pause();
    URL.revokeObjectURL(url);
  }
  return note;
};

/** The roster the agency map draws. `/api/agents` is the source (it alone
 *  carries handoffs and memory scopes); a persona it lacks is taken from
 *  the meeting's own turn. When the roster could not be read, the map still
 *  seats every agent the meeting names -- from the turn, with no synapses
 *  it cannot know -- and the caller draws them all UNKNOWN. */
export function mapAgents(meeting: Meeting | null, roster: AgentSummary[] | null): AgentSummary[] {
  const turns = new Map((meeting?.turns ?? []).map((t) => [t.agent, t]));
  if (roster) {
    return roster.map((a) => {
      const t = turns.get(a.name);
      return a.persona || !t ? a : { ...a, persona: t.persona };
    });
  }
  return (meeting?.turns ?? []).map((t) => ({
    name: t.agent, title: t.title, display: t.title, brief: t.persona.specialty,
    need: null, available: false, providers: [], tools: 0,
    last_run_at: t.did.last_at, last_outcome: outcomeLine(t.did.last_outcome),
    runs_24h: t.did.runs_24h, model: null, model_at: null,
    handoff: [], memory_scope: [], ready: t.ready, reasons: t.issues.items,
    group: t.agent === FRONT_DOOR_AGENT ? "lead" : "functions",
    next_shift_at: t.next.at, persona: t.persona,
  }));
}
