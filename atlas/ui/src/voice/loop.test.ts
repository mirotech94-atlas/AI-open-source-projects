import { describe, expect, it } from "vitest";
import {
  ACKNOWLEDGEMENTS,
  FAREWELLS,
  FOLLOWUP_QUIET_MS,
  isFarewell,
  next,
  speakable,
  statusOf,
  stripAcknowledgement,
  type LoopState,
} from "./loop";

const dormant: LoopState = { open: false, handsFree: true, turns: 0 };

describe("next", () => {
  it("wake opens the loop and goes to listening", () => {
    const { state, go } = next(dormant, "wake");
    expect(go).toBe("listening");
    expect(state.open).toBe(true);
    expect(state.turns).toBe(0);
  });

  it("a second wake while the loop is already open changes nothing", () => {
    const open = next(dormant, "wake").state;
    const { state, go } = next(open, "wake");
    expect(go).toBe("stay");
    expect(state).toEqual(open);
  });

  it("turn_done goes back to listening with the loop still open", () => {
    const open = next(dormant, "wake").state;
    const { state, go } = next(open, "turn_done");
    expect(go).toBe("listening");
    expect(state.open).toBe(true);
    expect(state.turns).toBe(1);
  });

  it("quiet closes the loop", () => {
    const open = next(next(dormant, "wake").state, "turn_done").state;
    const { state, go } = next(open, "quiet");
    expect(go).toBe("dormant");
    expect(state.open).toBe(false);
    expect(state.turns).toBe(0);
  });

  it("farewell closes the loop", () => {
    const open = next(dormant, "wake").state;
    const { state, go } = next(open, "farewell");
    expect(go).toBe("dormant");
    expect(state.open).toBe(false);
  });

  it("dismiss closes the loop but leaves hands-free as it was", () => {
    const open = next(dormant, "wake").state;
    const { state, go } = next(open, "dismiss");
    expect(go).toBe("dormant");
    expect(state.open).toBe(false);
    expect(state.handsFree).toBe(true);
  });

  it("mute closes the loop AND turns hands-free off", () => {
    const open = next(dormant, "wake").state;
    const { state, go } = next(open, "mute");
    expect(go).toBe("dormant");
    expect(state.open).toBe(false);
    expect(state.handsFree).toBe(false);
  });

  it("closing keeps hands-free on, so the mic stays with the wake gate", () => {
    for (const event of ["quiet", "farewell", "dismiss"] as const) {
      const open = next(dormant, "wake").state;
      expect(next(open, event).state.handsFree).toBe(true);
    }
  });

  it("nothing about the loop is ever 'closed on a timer into acting' -- quiet only closes", () => {
    // Silence is never consent: the only thing quiet can do is close.
    const open = next(dormant, "wake").state;
    expect(next(open, "quiet").go).toBe("dormant");
    expect(FOLLOWUP_QUIET_MS).toBe(12000);
  });
});

describe("isFarewell", () => {
  it.each([
    "that's all",
    "Thats all, thanks",
    "that will be all",
    "thanks atlas",
    "Thanks, Atlas.",
    "thank you, atlas",
    "goodbye",
    "Good night",
    "goodnight atlas",
  ])("recognises %j", (said) => {
    expect(isFarewell(said)).toBe(true);
  });

  it.each([
    "thanks for the atlas report",
    "what's the weather tonight",
    "is that all of the invoices",
    "good morning",
    "",
  ])("does not mistake %j for a farewell", (said) => {
    expect(isFarewell(said)).toBe(false);
  });

  it("is the regex the brief pinned", () => {
    expect(FAREWELLS.source).toBe(
      "\\b(that'?s all|that will be all|thanks,? atlas|thank you,? atlas|goodbye|good ?night)\\b",
    );
  });
});

describe("statusOf", () => {
  it("maps every phase onto the one status the stage pill shows", () => {
    expect(statusOf("dormant")).toBe("IDLE");
    expect(statusOf("waking")).toBe("LISTENING");
    expect(statusOf("listening")).toBe("LISTENING");
    expect(statusOf("thinking")).toBe("THINKING");
    expect(statusOf("responding")).toBe("SPEAKING");
    expect(statusOf("dismissing")).toBe("IDLE");
  });
});

describe("stripAcknowledgement", () => {
  it("removes ATLAS's own acknowledgement if the mic caught it", () => {
    expect(stripAcknowledgement("At your service. What's on today?"))
      .toBe("What's on today?");
    expect(stripAcknowledgement("go ahead, what's the weather")).toBe("what's the weather");
    expect(stripAcknowledgement("Listening.")).toBe("");
  });

  it("leaves everything else alone", () => {
    expect(stripAcknowledgement("Listening to music later?")).toBe("Listening to music later?");
    expect(stripAcknowledgement("what is listening mode")).toBe("what is listening mode");
    // Unpunctuated, "go ahead" may be his own words -- only a clearly
    // separate acknowledgement is removed.
    expect(stripAcknowledgement("go ahead and book it")).toBe("go ahead and book it");
  });

  it("knows exactly the three fixed acknowledgements", () => {
    expect([...ACKNOWLEDGEMENTS]).toEqual(["At your service.", "Go ahead.", "Listening."]);
  });
});

describe("speakable", () => {
  it("passes short text through", () => {
    expect(speakable("Two proposals are waiting.", 100)).toBe("Two proposals are waiting.");
  });

  it("cuts long text at a sentence end, never mid-word", () => {
    const text = "First sentence here. Second sentence is longer. Third.";
    expect(speakable(text, 30)).toBe("First sentence here.");
  });

  it("falls back to a word boundary when there is no sentence end", () => {
    expect(speakable("one two three four five", 12)).toBe("one two");
  });
});
