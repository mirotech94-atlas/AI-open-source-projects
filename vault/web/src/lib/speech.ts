import { useCallback, useEffect, useRef, useState } from "react";

/** The app's only microphone and the app's only loudspeaker.
 *
 *  Three surfaces use this — the chat composer's mic, the chat header's
 *  speaker toggle, the wallboard's — and there is exactly one implementation,
 *  for the reason `components/ApiTokenPanel` has one: a second copy would be
 *  a second place to get microphone release and the AudioContext gesture rule
 *  right, and the day they drifted a customer would find out before we did.
 *
 *  Neither half talks to the voice vendor. It could not: the app is served
 *  under `default-src 'self'` with no third-party origin, so `api.elevenlabs.io`
 *  is unreachable from here and every byte goes through the hub. That is also
 *  why playback does not use an `<audio>` element — `media-src` falls back to
 *  `default-src 'self'` and a `blob:` URL is not `'self'`. Web Audio is not
 *  subject to `media-src`, so it works with no CSP change, and do not "fix"
 *  this later by adding `media-src 'self' blob:` to the nginx template.
 *
 *  **Trap one: the gesture rule is about the context's lifetime, not about a
 *  click having happened.** An `AudioContext` must be created or resumed
 *  inside a user gesture, and a browser may suspend an idle one at any point
 *  afterwards. That is invisible in a fresh tab and fatal on a wallboard,
 *  which is the only surface here that is ever idle for hours: the toggle is
 *  clicked once when the screen is set up and the first CRIT can arrive long
 *  after, by which time the context can be `suspended` and a `resume()`
 *  outside a gesture can be refused outright. So there is ONE module-level
 *  context, `enableAudio()` creates or resumes it from inside the toggle's
 *  own handler, and `speak()` returns false rather than pretending when the
 *  context is not running.
 *
 *  `speak()`'s `false` is not one thing, though, and a caller that treats it
 *  as one gets the toggle wrong in one direction or the other. It covers
 *  three different situations: voice isn't configured, or the context isn't
 *  running; the request itself failed; or nothing was wrong at all and a
 *  newer `speak()` (or `stopSpeaking()`) simply took the channel first. Only
 *  the first is "the speaker is dead". The other two are "this sentence was
 *  lost, the channel was not" — which is not the same as "something else is
 *  audible instead", because a 500 from `/voice/speak` leaves the room
 *  silent while the speaker is still perfectly able to play the next
 *  sentence. `audioReady()` is what tells those apart: a
 *  caller should turn its own toggle off on `false` **only when
 *  `audioReady()` is also false**. Getting this backwards is symmetric
 *  damage. Ignore `audioReady()` and a lit speaker icon over a silent room —
 *  a wall display lying about being audible, the same failure as a dashboard
 *  that goes blank when the uplink dies — becomes possible again. But check
 *  only the return value, and a wallboard that fires a burst of CRITs
 *  without awaiting each `speak()` turns its toggle off after every call but
 *  the last (each one superseded before it could finish), including the one
 *  moment audio is genuinely playing — the same lie, inverted.
 *
 *  **Trap two: a stopped MediaRecorder does not release the microphone.** The
 *  browser's recording indicator stays lit until every track is stopped, so
 *  `release` runs on every error path as well as the success path, and on
 *  unmount. Unmount alone is not the whole mechanism, though: `release` only
 *  has something to release once `getUserMedia` has resolved, so unmount
 *  also bumps the same session counter `stop()` bumps, and a permission
 *  grant that lands after the component is gone stops its own tracks rather
 *  than being adopted into refs nothing can reach — see `useDictation`.
 *  Releasing has its own echo, too: ending the tracks makes the stream
 *  inactive, which makes a still-recording `MediaRecorder` fire `onstop`, so
 *  unmount also carries a liveness flag to keep that clip from being
 *  uploaded on behalf of a panel the customer already closed. A security
 *  product that appears to be listening when it is not is a worse bug than
 *  one that fails to listen — and one that uploads after it stopped
 *  appearing to listen is worse than both. */

let ctx: AudioContext | null = null;
let playing: AudioBufferSourceNode | null = null;
// Which `speak()` round trip currently owns the channel. `stopSpeaking()`
// bumps it so a caller can cancel a fetch/decode still in flight, not only
// silence what has already started playing -- see `stopSpeaking` and `speak`.
let playToken = 0;

/** Create or resume the single audio context. **Call only from inside a user
 *  gesture handler** — a click, a pointerdown, a keydown.
 *
 *  Returns whether a usable context now exists, NOT whether it is already
 *  running: `resume()` is asynchronous, so reading `state` on the next line
 *  would report "suspended" in a real browser and "running" against any mock
 *  that resolves eagerly — a test that passes for the wrong reason. `speak()`
 *  does the running check at play time, which is the moment that matters, and
 *  a caller whose first `speak()` comes back false turns its own toggle off
 *  — here, where `audioReady()` is false too, since the context is still
 *  suspended; see the module docstring's note on `speak()`'s `false` for
 *  when a false return does *not* mean that. So the toggle goes on
 *  optimistically and corrects itself; it never claims to be audible on the
 *  strength of a state read that cannot be true yet. */
export function enableAudio(): boolean {
  try {
    if (!ctx) ctx = new AudioContext();
    if (ctx.state === "suspended") void ctx.resume();
    return ctx.state !== "closed";
  } catch {
    // No Web Audio at all (an old browser, a locked-down embed). Silence is
    // the whole degradation: the caller hides its control.
    return false;
  }
}

/** Whether the loudspeaker could play something right now.
 *
 *  Not a display field: this is the discriminator two other docstrings here
 *  name, the one that turns `speak()`'s undifferentiated `false` into an
 *  answerable question. `false` from `speak()` plus `audioReady()` true
 *  means this particular sentence was lost — the request failed, or a newer
 *  utterance took the channel — and the toggle should stay on, because the
 *  next sentence will be heard. `false` from both is the only combination
 *  that means the speaker is dead and the control is lying by staying lit.
 *  It reads `state` live rather than caching, because the whole point of
 *  trap one is that a context can be suspended between two reads. */
export function audioReady(): boolean {
  return ctx !== null && ctx.state === "running";
}

/** Stop whatever is playing, without claiming or invalidating the channel. */
function silence(): void {
  try {
    playing?.stop();
  } catch {
    // Already ended. `stop()` on a finished source throws in some engines
    // and there is nothing to do about it.
  }
  playing = null;
}

export function stopSpeaking(): void {
  // Bumping the token is what makes this cancel a round trip still in the
  // air, not just what is audible now. A caller that silences the speaker
  // and then hears the next sentence anyway has been told a lie by its own
  // control -- the chat header's speaker toggle (`ChatPanel.toggleSpeak`)
  // and the wallboard's (`Shell.toggleWallSpeak`) both call this expecting
  // exactly that: once silenced, nothing arrives late.
  playToken += 1;
  silence();
}

/** Say `text` out loud. Resolves false when nothing was played — but that
 *  covers voice being unconfigured or the context not running, the request
 *  failing, and a superseded call losing the channel to a newer one, and
 *  only the first of those means the speaker is dead. Check `audioReady()`
 *  alongside the return value before deciding to turn a toggle off: a
 *  superseded call is expected to return false while something else plays,
 *  and `audioReady()` stays true through it. See the module docstring's note
 *  on `speak()`'s `false` for the full reasoning. */
export async function speak(text: string): Promise<boolean> {
  const body = text.trim();
  if (!body || !ctx || ctx.state !== "running") return false;
  const mine = ++playToken;
  try {
    const res = await fetch("/api/v1/voice/speak", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: body }),
    });
    if (!res.ok) return false;
    const buf = await ctx.decodeAudioData(await res.arrayBuffer());

    // Someone took the channel while this round trip was in the air -- the
    // owner flicked the toggle off, or a newer speak() claimed it. Also
    // re-check the context: it can be suspended mid-flight, and the
    // pre-fetch check above is too early to catch that.
    if (mine !== playToken || !ctx || ctx.state !== "running") return false;

    // One voice at a time. Two of the three callers can fire in bursts — a
    // wallboard taking four CRITs in a minute, a reader hitting the speaker
    // on a fresh answer while the last is still going — and without this the
    // browser happily plays both at once.
    silence();   // barge-in: stop the previous voice, do not touch the token

    const src = ctx.createBufferSource();
    src.buffer = buf as AudioBuffer;
    src.connect(ctx.destination);
    src.onended = () => { if (playing === src) playing = null; };
    playing = src;
    src.start();
    return true;
  } catch {
    return false;
  }
}

/** The MIME types we ask `MediaRecorder` for, in order. The hub pins the same
 *  set (`voice.ALLOWED_AUDIO`) and rejects anything else with a 415, so asking
 *  for a type the hub will refuse is a round trip wasted. */
const PREFERRED = ["audio/webm", "audio/mp4", "audio/ogg"];

function pickMime(): string {
  const Rec = (globalThis as { MediaRecorder?: { isTypeSupported?(t: string): boolean } }).MediaRecorder;
  if (!Rec?.isTypeSupported) return "";
  return PREFERRED.find((t) => Rec.isTypeSupported?.(t)) ?? "";
}

export type Dictation = {
  recording: boolean;
  error: string;
  start: () => void;
  stop: () => void;
};

/** Push-to-talk. `start` on press, `stop` on release; the transcript arrives
 *  through `onText`.
 *
 *  Push-to-talk and never a wake word. A wake word would mean the hub
 *  receiving audio from a medical practice's waiting room continuously; the
 *  browser's microphone indicator being lit for exactly as long as a button
 *  is held is the only honest signal a customer can actually check. */
export function useDictation({ onText }: { onText: (text: string) => void }): Dictation {
  const [recording, setRecording] = useState(false);
  const [error, setError] = useState("");
  const recRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunks = useRef<Blob[]>([]);
  // Which press this is. `stop()` bumps it, so an in-flight `getUserMedia`
  // can tell that the finger it belongs to has already come up.
  const sessionRef = useRef(0);
  // Whether the component is still alive. Deliberately NOT the session
  // counter, though both are "is this still wanted": `stop()` bumps the
  // session on the ordinary press-and-release path, so an `onstop` that
  // checked the session would throw away every legitimate clip. The question
  // `onstop` has to answer is "is anyone still here to receive this", which
  // only unmount changes.
  const mountedRef = useRef(true);

  // Every exit path goes through here, including the failures and unmount.
  // See trap two. The track loop is unguarded on the strength of
  // `MediaStreamTrack.stop()` carrying no `[Throws]` in its WebIDL -- it is
  // specified as unable to throw, not merely unlikely to. That matters more
  // here than it would elsewhere: this function also runs as an effect
  // cleanup, so a throw here would propagate into React's unmount commit
  // rather than into an ordinary event handler.
  const release = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    recRef.current = null;
    setRecording(false);
  }, []);

  const start = useCallback(() => {
    if (recRef.current) return;
    setError("");
    chunks.current = [];
    const session = ++sessionRef.current;
    void (async () => {
      let stream: MediaStream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch {
        setError("Vault could not use the microphone. Check the browser's permission for this site.");
        setRecording(false);
        return;
      }

      // The permission prompt blocks the await above while the finger is
      // still on the button, so a press shorter than the prompt lands here
      // with the button already up. Opening the microphone now would leave
      // it open with nobody holding anything and no path back to closed --
      // the "appears to be listening" failure this module exists to prevent,
      // on the most ordinary sequence there is: the very first press, where
      // the prompt is the whole delay. `release()` already ran and found
      // nothing to release, so stopping these tracks is ours to do.
      if (session !== sessionRef.current) {
        stream.getTracks().forEach((t) => t.stop());
        return;
      }

      streamRef.current = stream;
      const mime = pickMime();
      let rec: MediaRecorder;
      try {
        rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      } catch {
        // The stream is already ours by this line, so this path owns
        // releasing it -- the same rule as every other exit.
        setError("Vault could not start recording in this browser.");
        release();
        return;
      }
      recRef.current = rec;

      rec.ondataavailable = (e: BlobEvent) => {
        if (e.data && e.data.size) chunks.current.push(e.data);
      };
      rec.onstop = () => {
        const type = rec.mimeType || mime || "audio/webm";
        const blob = new Blob(chunks.current, { type });
        void (async () => {
          try {
            // Nobody has to have pressed stop for this handler to run. On
            // unmount the cleanup calls `release()`, `release()` stops the
            // tracks, an inactive stream makes a still-recording
            // `MediaRecorder` fire its own `stop` (mediacapture-record), and
            // `release()` nulls `recRef.current` without detaching
            // `rec.onstop` — so a component that went away mid-press arrives
            // here with a full clip and nobody waiting for it. Uploading it
            // would send a customer's audio out of the browser after the
            // panel closed, which is the opposite of what push-to-talk
            // promises: the microphone indicator being lit for exactly as
            // long as the button is held is the only honest signal a
            // customer can check, and it says nothing about a clip sent
            // afterwards. The `finally` below still runs, so the tracks are
            // released either way.
            if (!mountedRef.current) return;
            if (!blob.size) return;
            const form = new FormData();
            form.append("file", blob, "clip");
            const res = await fetch("/api/v1/voice/transcribe", {
              method: "POST", credentials: "include", body: form,
            });
            if (!res.ok) {
              setError(res.status === 503
                ? "Voice is not configured on this hub."
                : "Vault could not make out that recording.");
              return;
            }
            const text = ((await res.json()) as { text?: string }).text ?? "";
            if (text) onText(text);
            else setError("Vault heard nothing in that recording.");
          } catch {
            setError("Vault could not reach the hub to transcribe that.");
          } finally {
            release();
          }
        })();
      };

      try {
        rec.start();
      } catch {
        // A USB mic unplugged between `getUserMedia` and here, or a codec
        // `isTypeSupported` claimed and the recorder then refuses anyway.
        // The stream and recorder are already ours by this line, so this
        // exit owns releasing them too -- the same rule as every other one.
        // Without this, the throw escapes as an unhandled rejection with
        // `recording` still `false` and `error` still `""` -- the module's
        // own "reports a denied microphone instead of failing silently"
        // promise broken one call further down from where round 1 fixed it.
        setError("Vault could not start recording in this browser.");
        release();
        return;
      }
      setRecording(true);
    })();
  }, [onText, release]);

  // Unmount has to close two different windows, and neither call below
  // covers the other.
  //
  // The session bump is for the pending-start race with no finger to come
  // up: a press whose permission prompt outlives the component would
  // otherwise adopt the stream into refs nothing can reach. `release()`
  // cannot do that job -- if `getUserMedia` has not resolved yet,
  // `streamRef.current` is still null and there is nothing to release -- so
  // the bump is what sends that late grant down the mismatch branch, where
  // it stops its own tracks.
  //
  // `release()` is for the other case, a recording already in progress.
  // `stop()` would be wrong here for a reason that survives: it leaves the
  // tracks running, and the microphone indicator stays lit for the life of
  // the tab. What it does NOT buy is silence on the wire, and an earlier
  // version of this comment claimed it did. `release()` stops the tracks,
  // an inactive stream makes a still-recording `MediaRecorder` fire its own
  // `stop`, and so `rec.onstop` runs on this path too, holding whatever was
  // captured before the panel closed. What stops that clip being uploaded
  // is the `mountedRef` check inside `onstop`, not the choice of `release()`
  // over `stop()` -- see there.
  //
  // The flag is set true on setup rather than only at declaration because
  // `<StrictMode>` (see `main.tsx`) double-invokes effects: it runs this
  // cleanup once before the real mount, and a flag only ever set false would
  // leave every dev-mode press unable to upload.
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      sessionRef.current += 1;
      release();
    };
  }, [release]);

  const stop = useCallback(() => {
    // Invalidate any `getUserMedia` still in flight from this press.
    sessionRef.current += 1;
    const rec = recRef.current;
    if (!rec) { release(); return; }
    try {
      if (rec.state !== "inactive") rec.stop();   // `onstop` does the rest
      else release();
    } catch {
      release();
    }
  }, [release]);

  return { recording, error, start, stop };
}

/** Test seam. Never called by the app: module state that survives between
 *  tests would make the suspended-context case pass for the wrong reason. */
export function resetSpeechForTests(): void {
  playing = null;
  playToken = 0;
  ctx = null;
}
