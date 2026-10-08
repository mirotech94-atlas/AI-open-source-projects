# Talking to Guardian — voice in and voice out

**Date:** 2026-09-22
**Status:** design, approved in chat

Guardian gains a mouth and an ear. Three surfaces: you can ask Ask Guardian a
question by holding a button and speaking; Guardian can read its answer back;
and a wallboard left running on a wall can say a new critical out loud instead
of only drawing a toast nobody is standing close enough to read.

Speech-to-text and text-to-speech both come from ElevenLabs, which is already
the voice vendor on the owner's other product, so there is one account and one
key to hold rather than two.

The outbound phone call — Guardian ringing the owner when the sensor dies — is
deliberately **out of scope**. It is the strongest argument for voice on paper,
because every channel Guardian has (`email`, `slack`, `ntfy`, `teams`,
`telegram`, `discord`) rides the customer's own uplink and so fails silently in
exactly the outage the customer bought Guardian to catch. It is excluded
anyway, on the owner's call: the product is a web app, and a customer whose
mental model of Guardian is "the thing that phones me" stops opening the
dashboard, which is where everything Guardian actually knows lives. Revisit it
as its own spec if the dead-sensor case ever justifies the Twilio dependency.

## Two constraints, and everything follows from them

### The CSP means nothing talks to ElevenLabs but the hub

`deploy/nginx-api.conf.tmpl:100` serves the app under

    default-src 'self'; base-uri 'none'; form-action 'none';
    frame-ancestors 'none'; object-src 'none'; script-src 'self'

with no third-party origin at all, and `web/tests/indexHtmlCsp.test.ts` holds
it there. A `fetch("https://api.elevenlabs.io/...")` from the dashboard is
refused by the browser. So is `<audio src="blob:...">`, because `media-src`
falls back to `default-src 'self'` and a `blob:` URL is not `'self'`.

This is not an obstacle to route around; it is the same rule `fonts.css`
carries, for the same reason — a watchdog has to render on an isolated network
— and it happens to produce the right architecture anyway. **Every byte in
both directions goes through the hub.** The key never reaches a browser, the
audio arrives over the same origin as everything else, and a customer running
Guardian on a segment with no egress except to `api.guarder.io` still gets a
working dashboard, minus the voice.

Playback therefore does **not** use an `<audio>` element. `speak()` fetches the
MP3 as an `ArrayBuffer` from the hub and plays it through
`AudioContext.decodeAudioData` + an `AudioBufferSourceNode`. Web Audio is not
subject to `media-src`, so no CSP change is needed, and the API's requirement
of a prior user gesture is satisfied by construction — every caller is a click.

Do not "fix" this later by adding `media-src 'self' blob:` to the nginx
template. It would work, and it would also be the first relaxation of a policy
whose value is that it has never been relaxed.

**The gesture rule is about the context's lifetime, not about a click having
happened once.** An `AudioContext` must be *created or resumed* inside a user
gesture, and a browser may suspend an idle one at any point afterwards. This
matters most exactly where it is least visible: the wallboard's toggle is
clicked once when the screen is set up and the first CRIT may arrive four hours
later, by which time a context created at setup and never used can be
`suspended`, and `resume()` outside a gesture can be refused outright. So
`speech.ts` holds **one** module-level context, created in the toggle's own
handler, and every `speak()` checks `ctx.state === "running"` first. If it is
not, the call degrades to silence **and turns the toggle back off**, because a
🔊 that is lit while nothing can be heard is a wall display lying about being
audible — the same failure as a dashboard that goes blank when the uplink dies.

### Voice is exactly as powerful as the keyboard, and not one step more

The temptation is to give the voice path its own permission model. It must not
have one. A transcript goes into the **existing** `POST /api/v1/chat` as
`body.question`, verbatim, on the same session with the same role — so an
`auto`-tier tool runs (which is already what typing the same sentence does),
and an `approval`-tier tool comes back as a `ChatProposal` carrying a
single-use `confirm_token` that only `ProposalCard`'s on-screen button can
spend.

The clamp is therefore not new code. It is the *absence* of a `confirm_token`
on anything the voice path constructs, and a test that says so. That is
stronger than a second permission table, which would be a second definition of
Guardian's authority and would drift from `remedy.py`'s three tiers the first
time a tier changed.

Two things make this the right reading rather than a convenient one. Guardian's
approval tier is a token-bound yes precisely because an intention has to be
attributable, and a sentence spoken in a dental practice's waiting room is not
attributable to anyone. And `trusted` and `label` are human warrants —
`hub/tests/test_warrant_rule.py` fails if an automatic path writes either — so
"Guardian, trust the front desk printer" must reach the same warrant the typed
sentence reaches, through the same route, or not at all. Atlas reached the
identical conclusion from the identical premise: *there is no voice path to the
executor*.

## Push-to-talk, and nothing is kept

No wake word, ever. A wake word means the hub is receiving audio from a medical
practice's waiting room continuously, and `Scan.raw` already taught this
codebase that a security product's own plumbing is a disclosure surface. The
microphone opens on `pointerdown` and closes on `pointerup`; the browser's mic
indicator is lit for exactly as long as the button is held, which is the only
honest signal a customer can actually check.

The audio bytes are transcribed and discarded in the same request. Nothing is
written to a table, nothing reaches `Scan.raw`, nothing is logged but a byte
count and a duration. The transcript is not special either — it becomes a
normal chat turn and is stored exactly as a typed one is, so there is no second
retention rule to get wrong.

## The hub

### `guardian_hub/voice.py`

One module, modelled on `chat/providers.py`: it owns the vendor calls, it never
raises, and every stage reports when it cannot run rather than degrading to
silence or a spinner.

    ELEVEN_STT = "https://api.elevenlabs.io/v1/speech-to-text"
    ELEVEN_TTS = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    TIMEOUT_S  = 30

- `ready() -> bool` — `bool(settings.elevenlabs_api_key)`, mirroring
  `_groq_ready()`.
- `transcribe(data: bytes, content_type: str) -> str` — multipart POST,
  `xi-api-key` header, fields `model_id=scribe_v2` and `file`. Returns
  `resp["text"]`, or `""` on any failure.
- `speak(text: str) -> bytes` — POST to the voice path with
  `{"text": ..., "model_id": settings.elevenlabs_model,
  "output_format": "mp3_44100_128"}`; returns MP3 bytes, or `b""` on failure.

Use `httpx` (already a declared runtime dependency in `hub/pyproject.toml`)
rather than `chat/providers.py`'s hand-rolled `urllib.request`. The precedent
there is stdlib-only, but it is sending JSON; building a multipart body by hand
to save a dependency that is already installed is the wrong trade.

Failures are swallowed for the same reason the provider ladder swallows them —
a vendor having a bad afternoon must not become a 500 on a dashboard somebody
opened because their network is down. `_describe_failure`'s discipline carries
over verbatim: log the status and body (the vendor's error JSON never contains
the key we sent), never the key.

### `guardian_hub/routers/voice.py`

Two routes, both `VIEWER`, both rate-limited through `ratelimit.allow` on the
user id the way `chat_turn` is.

    POST /api/v1/voice/transcribe   multipart audio  -> {"text": "..."}
    POST /api/v1/voice/speak        {"text": "..."}  -> audio/mpeg bytes

`speak` is a POST rather than a GET even though a GET would let an `<audio>`
element point straight at it, because the text is a sentence about somebody's
network and a GET would put it in a URL, an access log and a browser history.
The Web Audio playback path above means the element was never an option anyway.

`transcribe` also pins the content types it will forward —
`audio/webm`, `audio/ogg`, `audio/mp4` — and rejects anything else with a 415.
Safari and Chrome hand `MediaRecorder` output back under different types, so
the set has to be explicit; what it must not be is a header passed through to
the vendor unread, which makes the vendor our input validator.

Both cap their input: `transcribe` rejects a body over ~10 MB (a held button
cannot legitimately produce more, and the vendor's 5 GB ceiling is not a limit
this product wants to inherit), and `speak` rejects text over `SPEAK_CHARS_MAX = 600` — comfortably above a
verdict-plus-next line and far below anything that would bill a surprise.

**Both need an `UNCOVERED_ON_PURPOSE` entry in
`hub/tests/test_chat_coverage.py`**, with reasons, or the suite fails naming
the paths. They are not missing tools. Transcription is the assistant's own input path,
and a tool that let the assistant transcribe would be the assistant speaking to
itself. `speak` is plainer than that: it is a text-to-audio utility any
authenticated viewer may call with arbitrary text, it reads nothing and writes
nothing, and there is no action for a tool to expose.

### The credential

`elevenlabs_api_key: str = ""` and `elevenlabs_voice_id`/`elevenlabs_model`
join `groq_api_key` and `anthropic_api_key` in `config.Settings`, read from the
environment.

It does **not** go in `platform_settings.SPEC`. That module's own rule is
explicit — *"Secrets are never writable here. Credentials belong in the
environment, where they are not in a database that gets backed up, exported and
copied to laptops."* The console shows whether it is set; that is all it may
do.

There is no separate on/off setting. The key's presence is the switch, exactly
as `_groq_ready()` is for the chat ladder. A toggle that can be on while the
key is missing is a way for the owner to watch a save succeed and nothing
happen, which is the failure `platform_settings` was written to prevent.

## The web

### `web/src/lib/speech.ts`

One implementation, three consumers — the same reasoning
`components/ApiTokenPanel.tsx` carries for its two entrances. A second copy of
either hook would mean a second place to get microphone release or the AudioContext
gesture rule right.

- `useDictation()` — `MediaRecorder` over `getUserMedia({audio:true})`, start on
  press, stop on release, POST the blob, resolve to the transcript.
  **It must call `stream.getTracks().forEach(t => t.stop())` on every exit
  path, including the error path.** A `MediaRecorder` that is stopped without
  its tracks being stopped leaves the browser's microphone indicator lit, and a
  security product that appears to be listening when it is not is a worse bug
  than one that fails to listen.
- `useSpeak()` — fetch `POST /voice/speak`, `decodeAudioData`, play; a no-op that
  resolves immediately when voice is not configured.
  **One source plays at a time.** A new `speak()` stops the current
  `AudioBufferSourceNode` before starting its own. Two of the three consumers
  can fire in bursts — a wallboard taking four CRITs in a minute, a reader
  clicking the speaker on a fresh answer while the last one is still going —
  and without this the browser will happily play both at once.

Both read one new boolean, `voice`, added to `OverviewPayload` — the payload
`Shell` already fetches and already hands to `Wallboard`, so both the chat
panel and the wall display learn the capability with no new endpoint and no
extra round trip on load. It is `voice.ready()` at response time.

`web/src/types.ts` is contract-tested against the pydantic models by
`hub/scripts/check_types.py` and `hub/tests/test_contracts.py`, so the field
lands in `schemas_dash.py` and `types.ts` in the same change or the suite
fails.

When it is false the UI renders **no control at all** rather than a disabled
one — `remedy.py`'s human tier is the model: say what is true, or say nothing,
but never offer a button that cannot work.

### The three surfaces

1. **`shell/chat/Composer.tsx`** — a press-and-hold microphone beside the
   textarea. The transcript lands *in the textarea*, not straight into a turn,
   so a misheard sentence is edited rather than sent. This also means the voice
   path physically cannot construct a `confirm_token`; it only ever fills a
   text field.

2. **Reading the answer back** — a speaker toggle in the chat panel header,
   **off by default**, persisted in `localStorage`. What is spoken is the
   `verdict` sentence and the `next` line, never the bullets or the tool steps.
   That is both the cost control (TTS bills per character and Guardian's
   replies are long) and the better interaction: you read the detail and you
   listen to the conclusion.

3. **`shell/Wallboard.tsx`** — a 🔊 toggle that speaks the headline of each new
   CRIT arriving on `live.on("alert")`. The file's existing comment rejects
   `beep()` because `AudioContext` is blocked without a user gesture "on a page
   that has been sitting untouched for hours — the only state a wallboard is
   ever in". Correct, and the toggle is the answer to it: clicking it *is* the
   gesture, it happens once when the screen is set up, and it unlocks the
   context for the rest of the session. Update that comment; do not delete it.
   Default off, persisted, and it speaks CRIT only.

A fourth surface — a speaker button on an alert's remedy explanation — is
**cut**. Every remedy explanation is already on screen next to the button that
would read it, so it buys a reader nothing the chat toggle does not, and it
would put a third caller on `useSpeak` for no new capability.

## Degradation

| State | What the customer sees |
|---|---|
| No API key | No microphone, no speaker toggles, no wallboard 🔊. Chat is exactly what it is today. |
| Key set, vendor down or slow | The mic button returns to rest and a one-line notice appears in the transcript, sourced locally, the way `useChat`'s `isNotice` entries already are. Never a spinner that never ends. |
| Browser denies mic permission | One notice explaining it, and the button stays visible — the customer can grant it and try again. |
| `speak` fails | Silence, and nothing else. The answer is already on screen; an error toast about audio on top of a network alert is noise. |

## Tests

`hub/tests/test_voice.py`

- `ready()` false with no key; both routes then return a plain-language 503 and
  never reach the network.
- `transcribe` sends `model_id=scribe_v2` and the file part, and returns
  `resp["text"]` (vendor call monkeypatched).
- `speak` returns `audio/mpeg` with a non-empty body.
- A vendor 500, a timeout and a malformed body each produce an empty result and
  no exception.
- The oversize guards on both routes.
- **Audio bytes never reach the database.** Assert the row count of every table
  is unchanged across a `transcribe` call, and that the key appears in no log
  record emitted during it.

`hub/tests/test_chat_coverage.py` — two new `UNCOVERED_ON_PURPOSE` entries.

`hub/tests/test_voice_authority.py` — the spine, as an executable claim, in
two tests that fail for different reasons.

The behavioural one is the real guard: send a transcript that would trigger an
`approval`-tier tool through the same path the voice UI uses, and assert the
reply is a `ChatProposal` carrying an **unspent** token — not an execution, and
not an audit row. That tests the claim itself.

The structural one is cheap and catches a different mistake: `routers/voice.py`
contains no reference to `confirm_token`, `acttokens`, `trusted` or `label`,
parsed the way `test_remedy.py` parses `fix.py`. Keep both — the grep cannot
see a future edit that routes transcripts through `run_turn` directly, and the
behavioural test cannot see authority quietly appearing in the voice router
itself.

`web/tests/speech.test.ts`

- `useSpeak` resolves without a fetch when `voice.available` is false.
- `useDictation` stops every media track on the success path **and** the error
  path. Write the error case first; it is the one that regresses.
- No control renders when voice is unavailable.
- A second `speak()` stops the first source rather than overlapping it.
- A `suspended` context plays nothing and flips the caller's toggle off. Write
  this one first; it is the path that only shows up hours into a real
  wallboard and never in a fresh tab.

`web/tests/wallboard.test.tsx` — nothing is spoken with the toggle off, which
is the default; and the toggle survives a remount.

Per `§ Tests` in CLAUDE.md, none of these assert wall-clock. There is no time
budget here to be the honest exception.

## Not in scope

The outbound phone call and the ElevenLabs Agents/Twilio stack (see above).
Voice on the owner console — the owner is at a desk. Wake words. Dubbing the
marketing site. Narrating reports: a PDF somebody files for their insurer does
not want a soundtrack.

## The key itself

The ElevenLabs API key must be created in the owner's own ElevenLabs account
and placed in `/etc/guardian/hub.env` on the production hub by the owner. I
will not create the account, read the key, or type it into any field. The
implementation ships with the key absent, which by the degradation table above
is a fully working dashboard with no voice — so it can merge and deploy before
the key exists.
