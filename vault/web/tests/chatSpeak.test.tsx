/* The speaker toggle: off by default, remembered, and speaking only the
 * conclusion.
 *
 * "Only the conclusion" is a design claim with a cost attached — TTS bills
 * per character and a Vault reply runs to bullets and tool steps — so it
 * is asserted rather than left to whoever edits this next.
 *
 * `ChatEntry.next` is `NextAction[]` (see `src/types.ts` and
 * `Message.tsx`'s `entry.next[0].label`), not the single nullable object the
 * task brief sketched — this file's `next` fixtures use the real array
 * shape rather than the brief's literal. */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { ToastProvider } from "../src/design";
import type { ChatResponse, LiveEvent } from "../src/types";

const speak = vi.fn(async (_text: string) => true);
const enableAudio = vi.fn(() => true);
// Controllable rather than a literal `true`: `audioReady()` is the only
// thing that tells `lib/speech.ts`'s three different falses apart, so the
// self-disable test below cannot exist without being able to flip it. The
// default is the happy path, so the tests that don't care never look at it.
const audioReady = vi.fn(() => true);

vi.mock("../src/lib/speech", () => ({
  speak: (t: string) => speak(t),
  enableAudio: () => enableAudio(),
  audioReady: () => audioReady(),
  stopSpeaking: vi.fn(),
  useDictation: () => ({ recording: false, error: "", start: () => {}, stop: () => {} }),
}));

// `useChat` (inside `ChatPanel`) posts through `chat()` in this module, and
// `ChatPanel` itself imports `runAction` from the same file for the `Next:`
// offer button — one mock covers both, `vi.importActual` for everything the
// wiring tests below don't care about (in particular the real `ApiError`
// class, which `useChat`'s retry logic does an `instanceof` check against).
vi.mock("../src/lib/api", async () => {
  const actual = await vi.importActual<typeof import("../src/lib/api")>("../src/lib/api");
  return { ...actual, chat: vi.fn(), queueStatus: vi.fn(), runAction: vi.fn() };
});

import { chat } from "../src/lib/api";
import { ChatPanel, spokenFrom, SPEAK_PREF_KEY } from "../src/shell/ChatPanel";
import type { ChatPanelProps } from "../src/shell/ChatPanel";
import type { QueueLiveSource } from "../src/shell/chat/useChat";

beforeEach(() => {
  localStorage.clear();
  speak.mockClear();
  speak.mockResolvedValue(true);
  enableAudio.mockClear();
  audioReady.mockClear();
  audioReady.mockReturnValue(true);
  vi.mocked(chat).mockReset();
});

it("speaks the verdict and the next line, and nothing else", () => {
  const text = spokenFrom({
    verdict: "Two devices are unverified.",
    bullets: [{ text: "A printer appeared on Tuesday." }, { text: "A laptop appeared today." }],
    next: [{ label: "Review them", method: "POST", path: "", body: {} }],
  } as never);
  expect(text).toContain("Two devices are unverified.");
  expect(text).toContain("Review them");
  expect(text).not.toContain("printer");
});

it("says nothing at all when there is no verdict", () => {
  expect(spokenFrom({ verdict: "", bullets: [], next: [] } as never)).toBe("");
});

/* --- The preference, asserted against the component rather than against
 * `localStorage` itself.
 *
 * What stood here was `setItem(KEY, "1")` followed by `getItem(KEY)` — a
 * test of jsdom's storage that touched no production code and would have
 * passed with `ChatPanel` never reading or writing anything. The three below
 * mount the panel, which is the only way the claim "it survives a reload"
 * can be about this file at all.
 *
 * The honest limit, the same one `Shell`'s wallboard toggle carries: a
 * restored "on" is an intention, not a working speaker. `enableAudio()` only
 * runs inside a click, so a restored preference reaches the speak effect
 * with a context that may still be suspended. On a chat panel somebody just
 * opened, the next click supplies the gesture soon enough that it never
 * shows; on a wall display left untouched through a reboot it does. */

it("comes back on after a reload when it was left on", () => {
  localStorage.setItem(SPEAK_PREF_KEY, "1");
  renderPanel(panelProps);
  expect(screen.getByRole("button", { name: /read answers aloud/i })
    .getAttribute("aria-pressed")).toBe("true");
});

it("records the click, both ways", async () => {
  renderPanel(panelProps);
  const toggle = screen.getByRole("button", { name: /read answers aloud/i });
  await userEvent.click(toggle);
  expect(localStorage.getItem(SPEAK_PREF_KEY)).toBe("1");
  await userEvent.click(toggle);
  expect(localStorage.getItem(SPEAK_PREF_KEY)).toBe("0");
});

it("records the self-disable too, so the stored value and the icon never disagree", async () => {
  // Without this the preference is self-destructing rather than merely
  // wrong: restore on, the icon lights, the first reply that cannot be
  // played turns it off on screen while the stored "1" stays, and the next
  // reload lights it again. A setting that looks flaky instead of off.
  localStorage.setItem(SPEAK_PREF_KEY, "1");
  vi.mocked(chat).mockResolvedValue(speakReply);
  renderPanel(panelProps);
  const toggle = screen.getByRole("button", { name: /read answers aloud/i });
  expect(toggle.getAttribute("aria-pressed")).toBe("true");

  // The one combination that means "the speaker is dead": nothing played AND
  // the context is not running. See lib/speech.ts.
  speak.mockResolvedValueOnce(false);
  audioReady.mockReturnValueOnce(false);

  await sendMessage("status");
  await screen.findByRole("heading", { name: speakReply.verdict });

  await waitFor(() => expect(toggle.getAttribute("aria-pressed")).toBe("false"));
  expect(localStorage.getItem(SPEAK_PREF_KEY)).toBe("0");
});

/* `ChatPanel` uses `useMutation` (react-query) for the `Next:` offer button
 * and `useToast` for its result — both need their providers even for a
 * render that never touches either, or the hooks throw on mount. See
 * `web/tests/chat.test.tsx`'s `renderShellWithChat` for the same pairing. */
function renderPanel(props: ChatPanelProps) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <ChatPanel {...props} />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

const panelProps: ChatPanelProps = {
  open: true, onClose: () => {}, tab: "overview", providers: [],
  exampleIp: "192.168.1.5", voice: true,
  live: { on: () => () => {} },
  handoff: null, ask: null, id: "chat", returnFocusTo: { current: null },
};

it("offers no speaker when the hub cannot speak", () => {
  renderPanel({ ...panelProps, voice: false });
  expect(screen.queryByRole("button", { name: /read answers aloud/i })).toBeNull();
});

it("starts off, and enabling it goes through a real gesture", async () => {
  renderPanel(panelProps);
  const toggle = screen.getByRole("button", { name: /read answers aloud/i });
  expect(toggle.getAttribute("aria-pressed")).toBe("false");
  await userEvent.click(toggle);
  expect(enableAudio).toHaveBeenCalled();
  expect(toggle.getAttribute("aria-pressed")).toBe("true");
});

/* --- The actual wiring: a reply lands, and speak() is (or isn't) called. --- */

/** A `QueueLiveSource` this test drives itself, the same shape
 *  `chat.test.tsx`'s `renderShellWithChat` builds around `useLiveFeed` — a
 *  listener map `useChat`'s `live.on("queue", ...)` subscribes into, plus an
 *  `emit` this file calls to fire a frame. `panelProps.live` (a `{on: () =>
 *  () => {}}` stub) cannot do this: it drops the callback on the floor, so
 *  nothing here could ever reach it. */
function makeLive(): QueueLiveSource & { emit(event: LiveEvent): void } {
  const listeners = new Map<string, Set<(event: LiveEvent) => void>>();
  return {
    on: (type, fn) => {
      let set = listeners.get(type);
      if (!set) {
        set = new Set();
        listeners.set(type, set);
      }
      set.add(fn);
      return () => set?.delete(fn);
    },
    emit(event) {
      for (const fn of listeners.get(event.type) ?? []) fn(event);
    },
  };
}

/** The Next offer's bullet text ("A printer...") must never reach `speak()`
 *  — only the verdict and the label are read. */
const speakReply: ChatResponse = {
  text: "Two devices are unverified.\nA printer appeared on Tuesday.\nNext: Review them",
  verdict: "Two devices are unverified.",
  bullets: [{ text: "A printer appeared on Tuesday.", entity: { kind: null, id: null } }],
  next: [{ label: "Review them", method: "POST", path: "/api/v1/review", body: {} }],
  tool_steps: [],
  proposal: null,
  provider: "rules",
  thread_id: "thread-speak-1",
};

const secondReply: ChatResponse = {
  text: "Everything is fine now.",
  verdict: "Everything is fine now.",
  bullets: [],
  next: [],
  tool_steps: [],
  proposal: null,
  provider: "rules",
  thread_id: "thread-speak-2",
};

async function sendMessage(text: string) {
  await userEvent.type(screen.getByRole("textbox", { name: /ask vault/i }), `${text}{Enter}`);
}

it("speaks a reply that lands while the toggle is on, with the verdict and Next, never the bullet", async () => {
  vi.mocked(chat).mockResolvedValue(speakReply);
  renderPanel(panelProps);
  await userEvent.click(screen.getByRole("button", { name: /read answers aloud/i }));

  await sendMessage("status");
  await screen.findByRole("heading", { name: speakReply.verdict });

  expect(speak).toHaveBeenCalledTimes(1);
  expect(speak).toHaveBeenCalledWith("Two devices are unverified. Next: Review them");
  expect(speak.mock.calls[0][0]).not.toContain("printer");
});

it("does not speak again on a re-render that brings no new entry", async () => {
  vi.mocked(chat).mockResolvedValue(speakReply);
  const live = makeLive();
  renderPanel({ ...panelProps, live });
  await userEvent.click(screen.getByRole("button", { name: /read answers aloud/i }));

  await sendMessage("status");
  await screen.findByRole("heading", { name: speakReply.verdict });
  expect(speak).toHaveBeenCalledTimes(1);

  // `applyQueueFrame` (inside `useChat`) rebuilds the entries array with
  // `.map` on every "queue" frame, whether or not it matches a step in this
  // reply — a new array reference with the same last entry id, exactly the
  // re-render this file's docstring on `spokenId` says must not re-speak.
  act(() => {
    live.emit({ type: "queue", ts: "", data: { id: "unrelated-id", status: "running" } });
  });
  expect(speak).toHaveBeenCalledTimes(1);
});

it("does not speak a reply already on screen when the speaker is turned on afterward", async () => {
  vi.mocked(chat).mockResolvedValue(speakReply);
  renderPanel(panelProps);

  await sendMessage("status");
  await screen.findByRole("heading", { name: speakReply.verdict });
  expect(speak).not.toHaveBeenCalled();

  await userEvent.click(screen.getByRole("button", { name: /read answers aloud/i }));
  expect(speak).not.toHaveBeenCalled();
});

it("speaks the next reply after the speaker is turned on", async () => {
  vi.mocked(chat).mockResolvedValueOnce(speakReply).mockResolvedValueOnce(secondReply);
  renderPanel(panelProps);

  await sendMessage("status");
  await screen.findByRole("heading", { name: speakReply.verdict });

  await userEvent.click(screen.getByRole("button", { name: /read answers aloud/i }));
  expect(speak).not.toHaveBeenCalled();

  await sendMessage("again");
  await screen.findByRole("heading", { name: secondReply.verdict });

  expect(speak).toHaveBeenCalledTimes(1);
  expect(speak).toHaveBeenCalledWith("Everything is fine now.");
});
