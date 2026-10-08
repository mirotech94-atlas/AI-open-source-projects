import { useCallback, useEffect, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent, RefObject } from "react";
import { useMutation } from "@tanstack/react-query";
import { useToast } from "../design";
import type { Ack } from "../lib/api";
import { runAction } from "../lib/api";
import { audioReady, enableAudio, speak, stopSpeaking } from "../lib/speech";
import type { AlertHandoff, NextAction } from "../types";
import ChatMark from "./ChatMark";
import type { Chip } from "./chat/Composer";
import { Composer } from "./chat/Composer";
import { Message } from "./chat/Message";
import type { ChatEntry, QueueLiveSource } from "./chat/useChat";
import { useChat } from "./chat/useChat";

export type ChatPanelProps = {
  open: boolean;
  onClose: () => void;
  /** The current route's tab key, sent as `ChatRequest.tab`. */
  tab: string;
  /** The configured provider ladder (`MeOut.chat_providers`), rendered as the
   *  header's muted caption. Empty until `/me` lands. */
  providers: string[];
  /** A real IP off this tenant's network (`OverviewPayload.chat_example_ip`),
   *  used as the composer placeholder's example. Empty falls back to a
   *  literal address. */
  exampleIp: string;
  /** Whether this hub can hear and speak (`OverviewPayload.voice`). */
  voice: boolean;
  /** `Shell`'s one live-feed instance, so a queued tool step can move
   *  queued -> running -> done as its "queue" frames arrive. */
  live: QueueLiveSource;
  /** An alert hand-off to open with: Vault's explanation is seeded into the
   *  transcript (and its proposal, when there is one) as soon as it arrives.
   *  `null` for an ordinary open. */
  handoff: AlertHandoff | null;
  /** A question to put to Vault on the reader's behalf — the Help section's
   *  "Ask Vault about this". Sent as an ordinary chat turn, so the answer is
   *  about this site rather than a canned paragraph. `nonce` rises on every
   *  request: this panel outlives navigation between articles, so a
   *  question-only value would silently dedupe a second click. */
  ask: { question: string; nonce: number } | null;
  /** DOM id, so the floating button can name this popover in `aria-controls`. */
  id: string;
  /** The floating button. Escape (and closing generally) puts focus back on
   *  it, so a keyboard user is never dropped at the top of the document. */
  returnFocusTo: RefObject<HTMLButtonElement | null>;
};

/** Same list `design/Drawer.tsx` traps against — one definition of "focusable"
 *  across the app's two focus-trapping surfaces. */
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** The reference console's five suggestion chips, in its order, each sending
 *  its full question verbatim. They replace the earlier per-tab suggestion
 *  lists: the reference shows this one fixed row for the whole conversation,
 *  not a set that changes with the tab and disappears after the first turn. */
const CHIPS: Chip[] = [
  { label: "Everything up?", question: "Is everything up?" },
  { label: "New devices?", question: "Any new or unknown devices on the Wi-Fi? Should I worry?" },
  { label: "Alerts?", question: "Which alerts need my attention?" },
  { label: "Certs?", question: "When do the certificates expire?" },
  { label: "Internet?", question: "How is my internet speed and DNS?" },
];

/** The one assistant message the panel opens with, before any input — the
 *  reference console's own greeting, naming Vault. Static markup rather
 *  than a `ChatEntry`, so it never enters the transcript or the thread. */
const GREETING =
  "I am Vault. Ask about your servers, sites, certificates, internet speed, or any device on " +
  "the Wi-Fi. I answer from the latest scan and I can tell you what Vault already fixed.";

/** The example address in the composer's placeholder when this tenant has no
 *  device with an IP yet (a fresh install, before the first scan). */
const FALLBACK_EXAMPLE_IP = "192.168.1.166";

/** Where the "read answers aloud" preference lives. Per-browser and
 *  per-person by design: one office manager wanting Vault to talk should
 *  not make it talk on the receptionist's screen.
 *
 *  Every path that changes `speakOn` writes here, including the one nobody
 *  clicked — the self-disable below, when a reply could not be played and
 *  `audioReady()` says the channel is dead. Skip that write and the stored
 *  value and the live one disagree, which makes the preference
 *  self-destructing in a way that looks like a flaky toggle: restore on, the
 *  icon lights, the first reply turns it off, reload, it is lit again. A
 *  preference that only records the clicks is not recording the preference. */
export const SPEAK_PREF_KEY = "guardian.chat.speak";

/** Remember the speaker preference, or quietly don't.
 *
 *  Private mode and blocked site data both throw here, and neither is a
 *  reason to break the toggle for this tab — it just will not survive a
 *  reload. */
function rememberSpeak(on: boolean): void {
  try {
    localStorage.setItem(SPEAK_PREF_KEY, on ? "1" : "0");
  } catch {
    // See above.
  }
}

/** What gets read out of a reply: the verdict sentence and the `Next:` line,
 *  never the bullets or the tool steps.
 *
 *  Two reasons, and they point the same way. TTS is billed per character and
 *  a Vault reply runs long. And it is the better interaction anyway — the
 *  detail is already on the screen being read, so the thing worth hearing
 *  while your hands are inside a cabinet is the conclusion and what to do.
 *
 *  `entry.next` is `NextAction[]` (a hub can offer at most one `Next:`, but
 *  the type carries a list — see `Message.tsx`'s `entry.next[0]`), so the
 *  offer's label is read off the first element the same way the visible
 *  `Next:` row is. */
export function spokenFrom(entry: Pick<ChatEntry, "verdict" | "next">): string {
  const verdict = (entry.verdict || "").trim();
  if (!verdict) return "";
  const next = entry.next[0]?.label?.trim();
  return next ? `${verdict} Next: ${next}` : verdict;
}

/**
 * The Vault chat popover: a fixed 400x560 panel anchored above the
 * floating button in the bottom-right corner, exactly as the reference
 * console places it. Keeps a per-tenant, in-memory conversation (`useChat`),
 * renders each turn in the strict answer format, and surfaces tool steps and
 * tier-3 proposals inline. Under 700px it stretches to the viewport width and
 * sits clear of the mobile tab bar — CSS only (`shell.css`), so this
 * component renders the same markup at every width.
 */
export function ChatPanel({ open, onClose, tab, providers, exampleIp, voice, live, handoff, ask, id, returnFocusTo }: ChatPanelProps) {
  const { entries, busy, send, confirmProposal, dismissProposal, seedHandoff } = useChat({ tab, live });
  const toast = useToast();
  const panelRef = useRef<HTMLElement | null>(null);
  const bodyRef = useRef<HTMLDivElement | null>(null);
  // Whether the transcript should keep following new content. Starts true
  // (a fresh open sits at the bottom) and flips to false the moment the
  // reader scrolls away from the bottom by hand — a suggestion chip or a
  // reply landing must never yank them back down mid-read.
  const stickToBottom = useRef(true);

  useEffect(() => {
    const el = bodyRef.current;
    if (el === null) return;
    // A fresh mount of the transcript (this open) starts at the bottom,
    // regardless of where a previous open left `stickToBottom` — the reader
    // has not scrolled anywhere in this one yet.
    stickToBottom.current = true;
    function onScroll() {
      const distanceFromBottom = el!.scrollHeight - el!.scrollTop - el!.clientHeight;
      // A few px of slack: the "thinking…" spinner and layout rounding both
      // nudge this off exactly zero even while the reader is, for all
      // purposes, still at the bottom.
      stickToBottom.current = distanceFromBottom < 48;
    }
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
    // `open`: the panel unmounts `.g-chat-body` while closed, so a plain
    // mount-only `[]` here registered the listener once against a `null`
    // ref on the very first render (`open` starts false) and never again —
    // this must re-run every time the element actually exists.
  }, [open]);

  // F9: the transcript used to just sit wherever it was — after Send, after a
  // suggestion chip, and once actually blank, because the newest reply is
  // rendered below the fold with nothing to bring it on screen. `entries` and
  // `busy` are new values on every streamed tool step and on the "thinking…"
  // row appearing/leaving (see `useChat`'s `setEntries` calls), not only when
  // a whole reply lands, so this follows the conversation live rather than
  // catching up only at the end of a turn.
  useEffect(() => {
    const el = bodyRef.current;
    if (el === null || !stickToBottom.current) return;
    el.scrollTop = el.scrollHeight;
    // `open` is a dependency even though this effect never reads it: the
    // panel unmounts `.g-chat-body` while closed (`if (!open) return null`
    // below), so reopening remounts it at `scrollTop: 0` with no change to
    // `entries`/`busy` to otherwise trigger this.
  }, [entries, busy, open]);

  // Dispatches a `Next:` offer's `NextAction` exactly like `VerdictHero`'s
  // primary button does — same `runAction` call, same toast-on-result — so
  // `NextAction.path`/`method`/`body` are actually used here instead of
  // sitting dead in the chat mapping.
  const runNext = useMutation({
    mutationFn: (action: NextAction) => runAction<Ack>(action),
    onSuccess: () => toast.show("Done", "good"),
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  // Off by default. A dashboard that starts talking in a shared office the
  // first time somebody opens it is a dashboard people close.
  //
  // Restored, with an honest limit worth stating rather than rediscovering.
  // An `AudioContext` has to be created or resumed inside a user gesture, so
  // a restored "on" is an intention, not a working speaker: on a panel
  // somebody just opened the next click supplies the gesture soon enough
  // that it never shows, but the wallboard's sibling toggle has the same
  // shape on a display that can reboot untouched, where the first critical
  // after that reboot goes unspoken and the toggle turns itself off. See
  // `Shell.toggleWallSpeak`. The browser forbids anything better without a
  // gesture; do not "fix" it with a document-level one-shot listener.
  const [speakOn, setSpeakOn] = useState(() => {
    try {
      return localStorage.getItem(SPEAK_PREF_KEY) === "1";
    } catch {
      return false; // private mode / blocked site data: read as off, not broken
    }
  });

  // Speak each new Vault reply once. Keyed on the entry id rather than on
  // the array length so a re-render (a tool step's status changing, a
  // handoff/ask effect re-running) never re-reads the same answer. Declared
  // ahead of `toggleSpeak` because turning the speaker on seeds it too — see
  // there.
  const spokenId = useRef<string>("");

  // `speakOn` is read directly rather than through a `setSpeakOn` functional
  // updater: `enableAudio()`, `stopSpeaking()` and the `localStorage` write
  // are side effects, and a functional updater buys stale-closure safety
  // this handler doesn't need (it isn't racing another update to `speakOn`
  // between render and click). Running side effects inside an updater relies
  // on them being safe to call more than once — true today (both are
  // idempotent by their own docstrings), but that safety lives in another
  // file and shouldn't be a precondition for this handler being correct.
  // Doing the effects once, here, and only then setting state, removes the
  // question rather than depending on the answer staying true.
  const toggleSpeak = useCallback(() => {
    const now = !speakOn;
    // `enableAudio` must run inside this handler, not in an effect: the
    // AudioContext gesture rule is about where the call happens, and an
    // effect is not a gesture. See lib/speech.ts.
    if (now && !enableAudio()) return;
    if (!now) stopSpeaking();
    if (now) {
      // The toggle is a preference about what happens NEXT, not a replay
      // button. Seed `spokenId` to whatever is already the newest entry so
      // the speak effect below only ever reads a reply that lands AFTER
      // this click. Skip this and flipping the speaker on immediately reads
      // out whatever reply is already on screen — in a shared office (a
      // dental practice front desk is the case this product is built for),
      // that is the room suddenly hearing a verdict the reader already read
      // silently, which is exactly the surprise that gets the toggle
      // switched off and never back on. There is no replay affordance in
      // this design; a future edit that removes this seed to "catch up" on
      // missed replies is undoing that decision, not fixing a bug.
      const last = entries[entries.length - 1];
      spokenId.current = last?.id ?? "";
    }
    rememberSpeak(now);
    setSpeakOn(now);
  }, [speakOn, entries]);

  useEffect(() => {
    if (!speakOn || !voice) return;
    const last = entries[entries.length - 1];
    if (!last || last.role !== "guardian" || last.id === spokenId.current) return;
    spokenId.current = last.id;
    const line = spokenFrom(last);
    if (!line) return;
    void speak(line).then((played) => {
      // `speak()`'s false is not one thing — voice unconfigured, the request
      // failing, or a newer utterance/stopSpeaking() taking the channel all
      // return false. Only `audioReady()` tells "the speaker is dead" apart
      // from "this sentence was lost but the channel is fine" — see
      // lib/speech.ts's module docstring. Turning the toggle off on a bare
      // `!played` would flip it off mid-burst on the superseded case, which
      // is the same "control lying about what you can hear" failure,
      // inverted.
      if (!played && !audioReady()) {
        // Persist this too. It was not clicked, but it is the state the
        // customer will next see the icon in, and a stored "1" under a dark
        // speaker is the preference lying about itself -- see
        // `SPEAK_PREF_KEY`.
        setSpeakOn(false);
        rememberSpeak(false);
      }
    });
  }, [entries, speakOn, voice]);

  // A hand-off arrives from the alerts list (`Shell` -> `Inbox`); seeding is
  // idempotent per hand-off, so a re-render never repeats the message. It runs
  // even while the panel is closed — `Shell` opens it in the same tick, and
  // the transcript must already carry the context when it appears.
  useEffect(() => {
    if (handoff) seedHandoff(handoff);
  }, [handoff, seedHandoff]);

  // "Ask Vault about this" from a Help article. Asked once per nonce: `send`
  // changes identity whenever `busy` flips, and without the ref that would
  // re-ask the same question the moment the reply landed. Runs even while the
  // panel is closed — `Shell` opens it in the same tick, and the question must
  // already be in the transcript when it appears.
  const askedRef = useRef(0);
  useEffect(() => {
    if (ask === null || askedRef.current === ask.nonce) return;
    askedRef.current = ask.nonce;
    send(ask.question);
  }, [ask, send]);

  // Escape is bound on `document`, not the panel, the same way `Drawer` does
  // it — the key must close the panel regardless of what has focus,
  // including nothing at all right after it opens.
  useEffect(() => {
    if (!open) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  // Opening moves focus into the popover; closing hands it back to the
  // floating button that opened it, whichever way it closed (Escape, the ×,
  // or a second click on the button itself).
  useEffect(() => {
    if (!open) return;
    const panel = panelRef.current;
    const first = panel?.querySelector<HTMLElement>(FOCUSABLE);
    (first ?? panel)?.focus();
    // Captured now: by cleanup time the popover is already unmounting, and
    // the lint rule is right that reading `.current` there is unreliable.
    const fab = returnFocusTo.current;
    return () => {
      fab?.focus();
    };
  }, [open, returnFocusTo]);

  if (!open) return null;

  // Tab and Shift-Tab cycle inside the popover while it is open.
  function trapTab(e: ReactKeyboardEvent<HTMLElement>) {
    if (e.key !== "Tab") return;
    const panel = panelRef.current;
    if (!panel) return;
    const items = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE));
    if (items.length === 0) {
      e.preventDefault();
      panel.focus();
      return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement;
    if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    } else if (e.shiftKey && (active === first || active === panel)) {
      e.preventDefault();
      last.focus();
    }
  }

  // "via anthropic → groq → rules" in the reference's screenshot; Vault's
  // own ladder reads "via groq → anthropic → rules". Just "via rules" when no
  // model provider is configured — the join never leaves a dangling arrow.
  const caption = `via ${(providers.length > 0 ? providers : ["rules"]).join(" → ")}`;
  const placeholder = `Ask Vault, e.g. what is ${exampleIp || FALLBACK_EXAMPLE_IP}?`;

  return (
    <aside
      ref={panelRef}
      id={id}
      className="g-chat-panel g-chat-open"
      role="dialog"
      aria-label="Vault chat"
      tabIndex={-1}
      onKeyDown={trapTab}
    >
      <div className="g-chat-head">
        <ChatMark size={22} />
        <span className="g-chat-title">Vault</span>
        <span className="g-chat-head-spacer" />
        <span className="g-chat-head-prov">{caption}</span>
        {voice && (
          <button
            type="button"
            className="g-chat-speak"
            aria-label="Read answers aloud"
            aria-pressed={speakOn}
            onClick={toggleSpeak}
          >
            {speakOn ? "🔊" : "🔈"}
          </button>
        )}
        <button type="button" className="g-chat-close" aria-label="Close chat" onClick={onClose}>
          ✕
        </button>
      </div>

      <div
        className="g-chat-body"
        role="log"
        aria-live="polite"
        aria-relevant="additions"
        aria-busy={busy}
        ref={bodyRef}
      >
        <div className="g-chat-msg g-chat-msg-guardian g-chat-greeting">
          <p className="g-chat-text">{GREETING}</p>
        </div>
        {entries.map((entry) => (
          <Message
            key={entry.id}
            entry={entry}
            onConfirmProposal={(e) => e.proposal && confirmProposal(e.proposal)}
            onDismissProposal={(e) => dismissProposal(e.id)}
            onRunNext={(action) => runNext.mutate(action)}
            nextBusy={runNext.isPending}
          />
        ))}
        {busy && (
          <p className="g-chat-thinking" aria-hidden="true">
            <span className="g-chat-spinner" /> thinking…
          </p>
        )}
      </div>

      <Composer disabled={busy} chips={CHIPS} placeholder={placeholder} onSend={send} voice={voice} />
    </aside>
  );
}

export default ChatPanel;
