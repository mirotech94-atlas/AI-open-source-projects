import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent, PointerEvent } from "react";

import { useDictation } from "../../lib/speech";

/** One suggestion chip: the short label on the button, and the full question
 *  it actually sends. */
export type Chip = { label: string; question: string };

export type ComposerProps = {
  disabled: boolean;
  chips: Chip[];
  /** The composer's placeholder, built by `ChatPanel` around a real IP from
   *  this tenant's own network. */
  placeholder: string;
  onSend: (message: string) => void;
  /** Whether this hub can hear at all (`OverviewPayload.voice`). False renders
   *  no microphone rather than a disabled one — a dead affordance on a
   *  security product reads as a broken product. */
  voice: boolean;
};

/** The chat input row: a growable textarea (Enter sends, Shift+Enter inserts
 *  a newline) under the reference console's `.chips` row, which stays visible
 *  for the whole conversation rather than only while the transcript is empty. */
export function Composer({ disabled, chips, placeholder, onSend, voice }: ComposerProps) {
  const [value, setValue] = useState("");
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const wasDisabled = useRef(disabled);

  // Sending disables (and so blurs) the textarea for the turn's duration —
  // once it re-enables, focus belongs back on the field the user was just
  // typing in, not wherever the browser happens to leave it.
  useEffect(() => {
    if (wasDisabled.current && !disabled) {
      inputRef.current?.focus();
    }
    wasDisabled.current = disabled;
  }, [disabled]);

  // The transcript lands in the textarea, never in a sent turn. That is not a
  // nicety: it is what keeps voice exactly as powerful as the keyboard. A
  // sentence Vault misheard gets corrected here, and an approval-tier
  // repair still waits for the on-screen confirm button, because nothing on
  // this path can construct a confirm token. See
  // hub/tests/test_voice_authority.py and the voice spec.
  const dictation = useDictation({
    onText: (heard) => {
      setValue((prev) => (prev.trim() ? `${prev.replace(/\s*$/, " ")}${heard}` : heard));
      inputRef.current?.focus();
    },
  });

  function submit() {
    const message = value.trim();
    if (!message || disabled) return;
    onSend(message);
    setValue("");
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <div className="g-chat-composer-wrap">
      {chips.length > 0 && (
        <div className="g-chat-suggestions" aria-label="Suggested questions">
          {chips.map((chip) => (
            <button
              key={chip.label}
              type="button"
              className="g-chat-suggestion"
              disabled={disabled}
              title={chip.question}
              onClick={() => onSend(chip.question)}
            >
              {chip.label}
            </button>
          ))}
        </div>
      )}
      <form
        className="g-chat-composer"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <textarea
          ref={inputRef}
          rows={1}
          value={value}
          disabled={disabled}
          aria-label="Ask Vault"
          placeholder={placeholder}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKeyDown}
        />
        {voice && (
          <button
            type="button"
            className={`g-chat-mic${dictation.recording ? " g-chat-mic-on" : ""}`}
            aria-label="Hold to talk"
            aria-pressed={dictation.recording}
            disabled={disabled}
            // Pointer capture keeps this press bound to the button even when
            // the finger drifts off it mid-sentence — the real case is a
            // phone held one-handed, where a few pixels of thumb drift would
            // otherwise truncate the question early. It does not change
            // *release*: pointerup still fires here regardless of where the
            // pointer physically ends up, and the spec's own release order is
            // pointerup -> lostpointercapture -> pointerout/pointerleave — so
            // `stop()` below runs once from pointerup and once more from the
            // pointerleave that follows. The second call is a harmless no-op
            // only because `useDictation`'s own `stop()` is idempotent, and
            // the branch that makes it so is NOT the `if (!rec)` guard: a
            // real `MediaRecorder.stop()` sets `state` to "inactive"
            // synchronously, while `release()` — the only thing that nulls
            // `recRef.current` — runs a microtask later from `onstop`'s async
            // IIFE. So at the pointerleave immediately after pointerup
            // `recRef.current` is still non-null, and what catches the second
            // call is `else release()` on the already-inactive recorder. That
            // safety belongs to speech.ts, not to this call site, so a future
            // stop binding elsewhere should not assume the same protection
            // for free.
            //
            // `setPointerCapture` throws for a pointer id that is no longer
            // active, and jsdom does not implement it at all (checked: 29.x
            // has none of setPointerCapture/releasePointerCapture/
            // hasPointerCapture on Element.prototype) — the three plain stop
            // bindings below are the fallback and were the whole mechanism
            // before capture was added, so losing capture silently must not
            // lose the ability to release the microphone.
            onPointerDown={(e: PointerEvent<HTMLButtonElement>) => {
              try {
                e.currentTarget.setPointerCapture(e.pointerId);
              } catch {
                // See above: no capture support, or the pointer already
                // ended between the event firing and this handler running.
              }
              dictation.start();
            }}
            onPointerUp={dictation.stop}
            onPointerLeave={dictation.stop}
            onPointerCancel={dictation.stop}
          >
            ●
          </button>
        )}
        <button type="submit" disabled={disabled || !value.trim()}>
          Send
        </button>
      </form>
      {dictation.error && <p className="g-chat-mic-err">{dictation.error}</p>}
    </div>
  );
}

export default Composer;
