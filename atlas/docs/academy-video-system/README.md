Use this system for every Atlas Academy tutorial: long-form 1920×1080 lessons on Claude, n8n and MCP, and their 1080×1920 Shorts. It exists so each episode looks like Atlas, teaches in the same rhythm, and keeps viewers past the first 30 seconds because the lesson moves with the idea, not because it is decorated.

## The promise that holds attention

Open on the finished result, not on a logo. The first three seconds show the payoff on screen (`promise` text over the real app, a `success` chip reading "Ready for review"); the brand sting comes after it and lasts one second. Title, thumbnail and opening describe the same visible outcome, or the viewer leaves at the mismatch.

One moving focal point at a time. A stable screen is engaging when the viewer is watching a meaningful action; automatic zooms, transitions on a timer and a constantly pulsing cursor are the fastest way to look like every other channel. Each effect answers the question "where do I look, what changed, or what happens next".

Never let animation imply something the demo did not do. A trace reaching a node means the data really went there; a `success` chip means the test really passed. Label omitted waiting time ("Processing time removed").

## Content fundamentals

Speak to one learner as "you"; refer to the lesson as "we'll build". Sentence case everywhere; no all-caps labels, no exclamation marks, no emoji in frame text. Name controls exactly as the app does (`Execute node`, not "the run button"). Read key code and values aloud, then hold them for at least `hold-tag`.

The reusable opening line: "Here is the result: [specific visible outcome]. In this lesson we'll build [small scope] and check [success condition]. You'll need [brief prerequisite]. Start with [first action]."

## Color

The frame is `navy`; cards and plates are `navy-raised` with a `navy-line` hairline. Text is `paper` (primary) and `paper-muted` (secondary). `blue` is the single accent: the drawn underline, active connectors, focus rings and chapter rules, and labels of 24px and up. For small blue text use `blue-text`; `blue` alone is 3.9:1 on navy and fails below 24px.

States always carry a word or glyph as well as a colour: `amber` for pending/processing with a clock, `success` (a teal-leaning green) for a passed checkpoint with a check, `error` for the real error with a cross. Success and error are also told apart by lightness, so the pair reads for colour-blind viewers.

Use shadow and tilt only in concept scenes: a `navy-raised` card may sit on navy with a short layered reveal, then return flat before the learner reads details. No particle clouds, rotating objects, flashes or looping background motion in a demonstration.

## Typography

One sans (IBM Plex Sans, Google Fonts) and one mono (IBM Plex Mono) for anything read as code. On a 1920×1080 frame the scale is `promise` 72 / `chapter` 56 / `callout` 36 / `support` 28, with `caption` at 44 for phrase-level captions; `code` 32 and `value-tag` 26 for values. Nothing on a 16:9 frame is set smaller than `support`. On a Short, `shorts-hook` 88 and `shorts-caption` 60. Phone-test every size and enlarge rather than shrink.

At most one headline above a demonstration; never stack narration, captions, animated icons and a second paragraph on one frame.

## Layout and safe areas

Every readable element sits inside `safe-16-9` (96px on each 1080p edge). Place persistent labels (chapter, lower-third) in the top-left or bottom-left, away from the task area the cursor works in. Captions sit on a navy plate at 80% opacity, bottom-centre, never covering a command or a result.

On a Short, recompose the active app panel for 1080×1920; never squeeze a desktop into portrait. Text stays inside `safe-9-16-x` sides, below `safe-9-16-top` and above `safe-9-16-bottom`.

The end card keeps `endscreen-reserve` (640px) empty on the right for YouTube's native next-video element. A painted thumbnail is not clickable; add the real element in Studio and preview it.

## Motion

Every preset is a `duration` token on the `settle` ease. Title fade + rise (`title-rise`, 16px travel), crossfade (`crossfade`, graphics only), focus zoom (`focus-zoom`, 100→115% into a prepared crop), cursor emphasis (`cursor-reveal` in, brief hold, `cursor-reveal` out), workflow trace (`trace`, one connector, stops at the node), checkpoint tag and lower-third (`title-rise` in, `hold-tag`, `title-rise` out), outro fade (`title-rise`, final card stays until the video ends). The sting is `sting` long, once per episode, after the result.

Cut, don't crossfade, between software states: the viewer must see the exact frame that changed.

## Components

Each component in this system is a reusable overlay or scene an editor rebuilds once as a template: ResultTitle, BrandSting, StepMap, CursorRing, WorkflowTrace, CheckpointTag, LowerThird, ErrorRecovery, EndCard and ShortsFrame. Their README says what the editor supplies (text, timecode, the real screenshot underneath) and the do/don'ts that keep the motion honest.

## Iconography

Glyphs only, drawn as single-stroke 2px line icons in `paper` or the state colour: check, cross, clock, cursor arrow, play, arrow-right. No emoji, no filled icon packs, no third-party logo marks inside the frame. No Atlas logo was supplied, so the wordmark is set in `chapter` type; replace it with the approved mark when it exists.
