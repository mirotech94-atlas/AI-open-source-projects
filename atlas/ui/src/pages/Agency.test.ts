/**
 * The Agency room: what reaches the DOM for a meeting (static markup, no
 * jsdom -- the same approach as `AgentMap.test.ts`), and the playback
 * sequence with a fake speaker: in order, one voice per line, stops on
 * Stop, stops (never skips) on a line that cannot be spoken.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { AgentSummary, Meeting, MeetingExchange, MeetingTurn } from "../api";
import { Agency, AgencyRoom, Conversation, LIVE_REFRESH_MS, readNotice } from "./Agency";
import {
  CLOSING, OPENING, mapAgents, meetingScript, nextShiftLine, playScript, provenance,
  threadsOf, voiceNote, type ScriptLine, type SpeakOne,
} from "./agencyModel";

const READ = "2026-09-28T12:00:00+00:00";

function turn(over: Partial<MeetingTurn> = {}): MeetingTurn {
  return {
    agent: "ops", title: "Operations",
    persona: { agent: "ops", name: "Marcus", specialty: "Infrastructure", voice: "male" },
    voice: "male", ready: true, status: "idle",
    did: { runs_24h: 0, shifts_24h: 0, self_tests_24h: 0, last_at: null,
           last_outcome: null, text: "No runs in the last 24 hours; it has never run." },
    issues: { items: [], failed_24h: 0, last_failure_at: null, text: "none" },
    improve: { text: "Nothing on the record to fix." },
    next: { at: null, text: "No shift." },
    speech: "Marcus, Infrastructure. Did: nothing.",
    read_at: READ,
    ...over,
  };
}

function meeting(turns: MeetingTurn[]): Meeting {
  return {
    as_of: READ, read_at: READ, window_hours: 24,
    opening: { speaker: "atlas", voice: "atlas", text: "Good day, everyone.", read_at: READ },
    turns,
    closing: {
      speaker: "atlas", voice: "atlas", text: "That's the room.", read_at: READ,
      counts: { agents: turns.length, ready: 1, worked: 0, idle: 1, blocked: 0 },
    },
  };
}

const ava = turn({
  agent: "concierge", title: "Concierge", voice: "female", status: "worked",
  persona: { agent: "concierge", name: "Ava", specialty: "General conversation", voice: "female" },
  did: { runs_24h: 3, shifts_24h: 1, self_tests_24h: 0, last_at: READ,
         last_outcome: "Answered.", text: "3 runs in the last 24 hours (1 shift). Last: Answered." },
  speech: "Ava, General conversation. Did: 3 runs.",
});

function render(m: Meeting, speaking: string | null = null): string {
  return renderToStaticMarkup(createElement(AgencyRoom, { meeting: m, speaking }));
}

describe("AgencyRoom draws the meeting it is given", () => {
  it("ATLAS at the head, one seat per agent, ATLAS at the foot", () => {
    const markup = render(meeting([ava, turn()]));
    const head = markup.indexOf("agency-head");
    const seat = markup.indexOf('data-agent="concierge"');
    const foot = markup.indexOf("agency-foot");
    expect(head).toBeGreaterThanOrEqual(0);
    expect(seat).toBeGreaterThan(head);
    expect(foot).toBeGreaterThan(seat);
    expect((markup.match(/class="agency-seat"/g) ?? [])).toHaveLength(2);
    expect(markup).toContain("Good day, everyone.");
    expect(markup).toContain("That&#x27;s the room.");
  });

  it("each seat has the persona name, title, specialty and the four items", () => {
    const markup = render(meeting([ava]));
    expect(markup).toContain("Ava");
    expect(markup).toContain("Concierge");
    expect(markup).toContain("General conversation");
    for (const item of ["Did", "Issues", "Improve", "Next"]) {
      expect(markup).toContain(`<dt>${item}</dt>`);
    }
    expect(markup).toContain("3 runs in the last 24 hours");
  });

  it("every figure carries a read time", () => {
    const markup = render(meeting([ava]));
    expect(markup).toMatch(/Read \d/);
    expect(markup).toContain("last run");
  });

  it("nobody is lit until the meeting plays", () => {
    const markup = render(meeting([ava, turn()]));
    expect(markup).not.toContain("data-speaking=");
    expect(markup).not.toContain("SPEAKING");
  });

  it("lights only whoever is speaking", () => {
    const markup = render(meeting([ava, turn()]), "concierge");
    expect((markup.match(/data-speaking="true"/g) ?? [])).toHaveLength(1);
    expect(markup).toMatch(/data-agent="concierge"[^>]*data-speaking="true"/);
    expect(render(meeting([ava]), OPENING)).toMatch(/agency-head"[^>]*data-speaking="true"/);
    expect(render(meeting([ava]), CLOSING)).toMatch(/agency-foot"[^>]*data-speaking="true"/);
  });

  it("a blocked agent is drawn as blocked, never as worked", () => {
    const markup = render(meeting([turn({ status: "blocked", ready: false })]));
    expect(markup).toContain('data-status="blocked"');
    expect(markup).toContain("BLOCKED");
    expect(markup).not.toContain('data-status="worked"');
  });

  it("an empty roster says so rather than drawing an empty table", () => {
    const markup = render(meeting([]));
    expect(markup).toContain("no one at the table");
    expect(markup).not.toContain("agency-seat");
  });
});

describe("the Agency page before and around a read", () => {
  it("renders the sentence-case title and says it is reading", () => {
    const markup = renderToStaticMarkup(createElement(Agency, { reloadKey: 0 }));
    expect(markup).toContain('<h2 class="label">Agency</h2>');
    expect(markup).toContain("Reading…");
    // Nothing to play until the meeting has been read.
    expect(markup).toMatch(/<button[^>]*disabled=""[^>]*>▶ Play meeting/);
  });

  it("a failed read names the reason and never passes an old room off as now", () => {
    expect(readNotice("HTTP 503", null)).toContain("nothing is shown");
    const old = readNotice("HTTP 503", meeting([ava]));
    expect(old).toContain("HTTP 503");
    expect(old).toContain("not now");
  });
});

describe("meetingScript and playScript", () => {
  it("ATLAS opens and closes in his own voice; each agent in its own", () => {
    const script = meetingScript(meeting([ava, turn()]));
    expect(script.map((l) => [l.key, l.voice])).toEqual([
      [OPENING, "atlas"], ["concierge", "female"], ["ops", "male"], [CLOSING, "atlas"],
    ]);
    expect(script[1]?.text).toBe(ava.speech);
  });

  it("speaks every line in order and clears the highlight at the end", async () => {
    const spoken: ScriptLine[] = [];
    const lit: (string | null)[] = [];
    const speak: SpeakOne = async (line) => { spoken.push(line); };
    const result = await playScript(meetingScript(meeting([ava])), speak,
                                    (k) => lit.push(k), new AbortController().signal);
    expect(result).toEqual({ kind: "finished" });
    expect(spoken.map((l) => l.key)).toEqual([OPENING, "concierge", CLOSING]);
    expect(lit).toEqual([OPENING, "concierge", CLOSING, null]);
  });

  it("Stop ends the meeting at once -- no later line is spoken", async () => {
    const controller = new AbortController();
    const spoken: string[] = [];
    const speak: SpeakOne = async (line) => {
      spoken.push(line.key);
      if (line.key === "concierge") controller.abort();
    };
    const lit: (string | null)[] = [];
    const result = await playScript(meetingScript(meeting([ava, turn()])), speak,
                                    (k) => lit.push(k), controller.signal);
    expect(result).toEqual({ kind: "stopped" });
    expect(spoken).toEqual([OPENING, "concierge"]);
    expect(lit[lit.length - 1]).toBeNull();
  });

  it("a voice the server lacks is a note, not a stop -- the meeting plays on", async () => {
    const notes: string[] = [];
    const speak: SpeakOne = async (line) =>
      voiceNote(line.voice, line.voice === "female" ? "atlas" : line.voice);
    const result = await playScript(meetingScript(meeting([ava, turn()])), speak,
                                    () => undefined, new AbortController().signal,
                                    (n) => notes.push(n));
    expect(result).toEqual({ kind: "finished" });
    expect(notes).toEqual([
      "FISH_VOICE_FEMALE is not set on the server, so female-voiced seats speak in ATLAS's voice.",
    ]);
    expect(voiceNote("male", "male")).toBeNull();
    expect(voiceNote("atlas", "atlas")).toBeNull();
    expect(voiceNote("male", null)).toBeNull();
  });

  it("a line that cannot be spoken stops the meeting and says where and why", async () => {
    const spoken: string[] = [];
    const speak: SpeakOne = async (line) => {
      spoken.push(line.key);
      if (line.key === "concierge") throw new Error("No voice is configured");
    };
    const result = await playScript(meetingScript(meeting([ava, turn()])), speak,
                                    () => undefined, new AbortController().signal);
    expect(result).toEqual({ kind: "failed", at: "concierge", reason: "No voice is configured" });
    expect(spoken).toEqual([OPENING, "concierge"]);
  });
});

describe("the agency map's roster and the picked seat", () => {
  const rosterRow = (over: Partial<AgentSummary> = {}): AgentSummary => ({
    name: "concierge", title: "Concierge", display: "Concierge", brief: "chat",
    need: null, available: true, providers: [], tools: 1, last_run_at: null,
    last_outcome: null, runs_24h: 3, model: null, model_at: null,
    handoff: ["ops"], memory_scope: ["people"], ready: true, reasons: [],
    group: "lead", next_shift_at: null, ...over,
  });

  it("uses the roster (it alone has handoffs), taking a missing persona from the turn", () => {
    const out = mapAgents(meeting([ava]), [rosterRow()]);
    expect(out).toHaveLength(1);
    expect(out[0]?.handoff).toEqual(["ops"]);
    expect(out[0]?.persona?.name).toBe("Ava");
  });

  it("with no roster, seats the meeting's agents with no synapses it cannot know", () => {
    const out = mapAgents(meeting([ava, turn()]), null);
    expect(out.map((a) => a.name)).toEqual(["concierge", "ops"]);
    for (const a of out) {
      expect(a.handoff).toEqual([]);
      expect(a.memory_scope).toEqual([]);
    }
    expect(out[0]?.runs_24h).toBe(3);
    expect(mapAgents(null, null)).toEqual([]);
  });

  it("marks only the seat picked on the map", () => {
    const markup = renderToStaticMarkup(createElement(AgencyRoom, {
      meeting: meeting([ava, turn()]), speaking: null, highlighted: "ops" }));
    expect((markup.match(/data-highlighted="true"/g) ?? [])).toHaveLength(1);
    expect(markup).toMatch(/data-agent="ops"[^>]*data-highlighted="true"/);
    expect(render(meeting([ava]))).not.toContain("data-highlighted");
  });
});

// ------------------------------------------------ the daily theme and the conversation

const BUILT = "2026-09-28T07:01:00+00:00";

function exchange(over: Partial<MeetingExchange> = {}): MeetingExchange {
  return {
    id: "x1q", thread: 1, kind: "follow_up", speaker: "atlas", speaker_name: "ATLAS",
    voice: "atlas", to: "ops", to_name: "Marcus", reply_to: null,
    text: "Marcus, your last run failed: no model answered. What's next?",
    plain: "Marcus, your last run failed: no model answered. What's next?",
    polished: false, read_at: READ, ...over,
  };
}

const thread1 = [
  exchange(),
  exchange({ id: "x1a", speaker: "ops", speaker_name: "Marcus", voice: "male",
             to: "atlas", to_name: "ATLAS", reply_to: "x1q",
             text: "Next shift in 4 hours.", plain: "Next shift in 4 hours." }),
];
const thread2 = [
  exchange({ id: "x2q", thread: 2, kind: "dependency", speaker: "commerce",
             speaker_name: "Owen", voice: "male", to: "fiverr", to_name: "Jasper",
             text: "Jasper, I use the fiverr module too. How did your last run go?",
             plain: "Jasper, I use the fiverr module too. How did your last run go?" }),
  exchange({ id: "x2a", thread: 2, kind: "dependency", speaker: "fiverr",
             speaker_name: "Jasper", voice: "male", to: "commerce", to_name: "Owen",
             reply_to: "x2q", text: "At 06:12 my last run said two orders are open.",
             plain: "My last run, at 06:12, said: Two orders are open.", polished: true }),
];

function themed(over: Partial<Meeting> = {}): Meeting {
  const answered = turn({
    question: "Marcus, your wins and your blockers?",
    answer: { text: "Nothing in my way on the record.",
              plain: "Nothing in my way on the record.", polished: false },
  });
  return {
    ...meeting([answered]),
    day: "2026-09-28",
    theme: { key: "wins_blockers", title: "Wins and blockers",
             prompt: "What went right in the last day, and what is in your way." },
    source: "live", built_at: READ, polished: 0,
    exchanges: [...thread1, ...thread2],
    ...over,
  };
}

describe("the day's theme and each seat's question", () => {
  it("shows the theme at the head of the table", () => {
    const markup = render(themed());
    expect(markup).toContain("Today&#x27;s theme");
    expect(markup).toContain("Wins and blockers");
    expect(markup).toContain('data-theme="wins_blockers"');
  });

  it("each seat carries ATLAS's question and its answer", () => {
    const markup = render(themed());
    expect(markup).toContain("Marcus, your wins and your blockers?");
    expect(markup).toContain("Nothing in my way on the record.");
  });

  it("an older meeting with no theme still renders", () => {
    const markup = render(meeting([ava]));
    expect(markup).not.toContain("Today&#x27;s theme");
    expect(markup).not.toContain("agency-talk");
  });
});

describe("the conversation", () => {
  it("draws each thread as a question and the reply under it", () => {
    const markup = renderToStaticMarkup(createElement(Conversation, {
      exchanges: [...thread1, ...thread2], speaking: null }));
    expect((markup.match(/class="agency-thread"/g) ?? [])).toHaveLength(2);
    expect((markup.match(/class="agency-say"/g) ?? [])).toHaveLength(4);
    expect(markup).toMatch(/data-speaker="ops" data-reply-to="x1q"/);
    expect(markup).toContain("ATLAS follows up");
    expect(markup).toContain("Depends on");
    // Speaker, to whom, and the time every line was read.
    expect(markup).toContain("Jasper");
    expect(markup).toContain("Owen");
    expect((markup.match(/read \d/g) ?? [])).toHaveLength(4);
    // The question comes before its reply.
    expect(markup.indexOf('data-speaker="atlas"'))
      .toBeLessThan(markup.indexOf('data-reply-to="x1q"'));
  });

  it("marks a reworded line subtly, with the record's own words on hover", () => {
    const markup = renderToStaticMarkup(createElement(Conversation, {
      exchanges: thread2, speaking: null }));
    expect((markup.match(/class="agency-polished"/g) ?? [])).toHaveLength(1);
    expect(markup).toContain(
      "the record&#x27;s own words: My last run, at 06:12, said: Two orders are open.");
  });

  it("an empty conversation says why", () => {
    const markup = renderToStaticMarkup(createElement(Conversation, {
      exchanges: [], speaking: null }));
    expect(markup).toContain("nothing on the record called for one");
  });

  it("lights the line being spoken", () => {
    const markup = renderToStaticMarkup(createElement(Conversation, {
      exchanges: thread1, speaking: "x1a" }));
    expect((markup.match(/data-speaking="true"/g) ?? [])).toHaveLength(1);
    expect(markup).toMatch(/data-reply-to="x1q" data-speaking="true"/);
  });

  it("threadsOf groups by thread, in order", () => {
    const out = threadsOf([...thread2, ...thread1]);
    expect(out.map((t) => t.map((x) => x.id))).toEqual([["x1q", "x1a"], ["x2q", "x2a"]]);
    expect(threadsOf(undefined)).toEqual([]);
  });

  it("the script plays the conversation after the turns, before the close", () => {
    const script = meetingScript(themed());
    expect(script.map((l) => l.key)).toEqual(
      [OPENING, "ops", "x1q", "x1a", "x2q", "x2a", CLOSING]);
    expect(script[2]?.voice).toBe("atlas");
    expect(script[3]?.voice).toBe("male");
  });
});

describe("where the meeting came from, and when", () => {
  it("a stored meeting says when it was built and when its records were read", () => {
    const text = provenance(themed({ source: "stored", built_at: BUILT }));
    expect(text).toMatch(/^This morning's meeting · built \d\d:\d\d · records read \d\d:\d\d$/);
    expect(render(themed({ source: "stored", built_at: BUILT }))).toContain("built ");
  });

  it("a live one says it is live, with its read time", () => {
    expect(provenance(themed())).toMatch(/^Live · rebuilt from the records · read \d\d:\d\d$/);
    // An older server with no `source`: live, never passed off as stored.
    expect(provenance(meeting([ava]))).toMatch(/^Live/);
  });

  it("the next shift is computed from its time now, not from the stored words", () => {
    const now = Date.parse("2026-09-28T12:00:00Z");
    expect(nextShiftLine({ at: "2026-09-28T15:30:00Z", text: "Next shift in 9 hours." }, now))
      .toBe("Next shift in 3h.");
    expect(nextShiftLine({ at: "2026-09-28T12:20:00Z", text: "x" }, now))
      .toBe("Next shift in 20m.");
    expect(nextShiftLine({ at: "2026-09-28T11:00:00Z", text: "x" }, now))
      .toBe("Next shift is due now.");
    expect(nextShiftLine({ at: null, text: "No shift until it is ready." }, now))
      .toBe("No shift until it is ready.");
  });

  it("refreshes live every 60 seconds", () => {
    expect(LIVE_REFRESH_MS).toBe(60_000);
  });
});

describe("what the old Agents tab showed, now on the Agency page", () => {
  const row: AgentSummary = {
    name: "ops", title: "Operations", display: "Operations", brief: "infra",
    need: "reason", available: true, providers: ["anthropic"], tools: 5,
    last_run_at: READ, last_outcome: "ok", runs_24h: 1, model: "anthropic:claude",
    model_at: READ, handoff: [], memory_scope: [], ready: true, reasons: [],
    group: "functions", next_shift_at: null,
    ladder: { watches: 3, acts_alone: 1, asks_first: 1, never: 0, unknown: 0 },
  };

  it("each seat has the model that answered its last run and its autonomy", () => {
    const markup = renderToStaticMarkup(createElement(AgencyRoom, {
      meeting: themed(), speaking: null, roster: [row] }));
    expect(markup).toContain("<dt>Model</dt>");
    expect(markup).toContain("anthropic:claude");
    expect(markup).toContain("<dt>Autonomy</dt>");
    expect(markup).toContain("watches 3 · does 1 by itself · asks you before 1");
    expect(markup).toContain("5 tools");
  });

  it("no roster read: no model or autonomy line is guessed", () => {
    const markup = render(themed());
    expect(markup).not.toContain("<dt>Model</dt>");
    expect(markup).not.toContain("<dt>Autonomy</dt>");
  });

  it("the page carries the warm-up, the live/this-morning switch and the live feed", () => {
    const markup = renderToStaticMarkup(createElement(Agency, { reloadKey: 0 }));
    expect(markup).toContain("Run all agents");
    expect(markup).toContain("Live feed");
    expect(markup).toMatch(/aria-pressed="true"[^>]*>Live</);
    expect(markup).toContain("This morning&#x27;s");
  });
});
