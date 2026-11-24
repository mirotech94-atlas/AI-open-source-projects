# ATLAS Academy: the motion guide, applied

Miro, 2026-10-15, sent two files. The first is *Atlas Academy Video Motion and
Editing Guide* (PDF, dated 2026-10-14). The second is
*Atlas-Academy-Motion-Style-Sample-v2.mp4*: 30 s, 1920×1080, 30 fps, silent.
He asked: "apply this motion and editing style to the ATLAS Academy YouTube
tutorials (and to the teaching Shorts cut from them)".

Code: `atlas/modules/academy/tutorial_motion.py` holds the presets in frames.
`tutorial_design.motion_of` says what moves on each slide. `tutorials.py`
renders it. `tutorial_short.caption_ass` writes the Shorts captions. Tests:
`tests/test_academy_motion.py`. `DESIGN_VERSION` 7 changes `script_key`, so
every unposted episode re-renders once. The cached intro, ending and
presenter parts are rebuilt too.

## What the sample does (frames pulled every second, and at 10 fps around each change)

| Time | What moves | How |
|---|---|---|
| 0–2 s | Eyebrow, then headline line by line, then sub line | Each fades in and rises about 15 px with an eased stop. A mask clips the line as it rises. |
| 1–2 s | Three workflow cards | They come in staggered, each with a slight 3D tilt that settles flat. |
| 2–5 s | A dot on a faint line under the cards | It runs from card 1 to card 3 and stops. The active card's border lights. |
| 5–6 s | "You stay in control." tag | It fades in under the active card (teal). |
| 6–8 s | Brand sting | The scene dissolves to a drawn "A" mark, then ATLAS / Academy, then a blue underline drawn left to right, then the tagline. |
| 9–19 s | "One step at a time." | Same title rise. One card is lit at a time. The dot moves one connector per step. Ticks fill in. A checkpoint bar fades in and holds about 3 s. |
| 19–22 s | Focus | The scene fades. The Review card alone moves and zooms to the centre, then crossfades to a "Ready for your review" card whose tick draws itself. |
| 22–30 s | End card | Headline rises. A next-lesson card settles with a tilt. The ATLAS block and agency.storefront.test/academy/ appear. It holds about 7 s, still. |

The fixed parts never move: the wordmark top left, a tag top right, the store
bottom right. The background is a navy radial with no motion in it. Only one
thing moves at a time, and every transition is a fade, never a slide or a
wipe.

## Rule by rule

| Guide rule / sample | Implemented | Where |
|---|---|---|
| **Crossfade** 6–8 frames between explanation graphics | Every step crossfades in **7 frames** (0.4 s before). The chapter **wipe-left is gone**; a new chapter is a crossfade plus the heading rise. | `motion.CROSSFADE_FRAMES`, `tutorials.TRANSITION`, `segment_argv` |
| **Title fade + rise** 8–10 frames, 12–20 px, eased stop | **9 frames, 16 px, cubic ease-out.** On a scene's first step the heading enters, then the step's new item. Later steps move only the new item: the list line, the node, the bubble, the code card, the quiz options, the subscribe button. The renderer cuts each one out of the same page at its own size, so the rest of the slide holds still. | `motion.RISE_*`, `design.motion_of`, `Shooter.layers` |
| "Animate only the active step"; "one moving focal point at a time" | The motions run in sequence and never overlap: crossfade, then heading, then trace, then item (tested over every scripted slide). State-only changes don't travel: a lit code line or a marked quiz answer just crossfades. | `motion.schedule`, tests |
| **Workflow trace** 12–18 frames along one connector, stop at the next node | **15 frames per connector.** A blue dot runs along a faint track under the nodes and stops under the next node, as in the sample. Diagram step *k* traces node *k−1* → *k*, then node *k* rises. | `motion.Trace`, `.flow::after`, `.dot` |
| Opening 3–6 s: "trace inquiry → draft → review once, stop at review"; fade in "Ready for review" | The **payoff** scene traces its whole workflow once and stops at the last node. Its result bubble then fades in as a checkpoint: no travel, 8 frames. Everything settles by 3 s. | `motion_of("payoff")` |
| **Checkpoint tag** 8-frame fade, hold 2–4 s | Checklist ticks, the quiz answer line and the payoff result **fade in over 8 frames without travel**. A motion only plays when the segment can still hold the result at least 1 s afterwards (`motion.fits`). Otherwise the slide is drawn whole from the first frame. | `CHECK_FRAMES`, `HOLD_AFTER` |
| **Use a cut for precise software-state changes** | A demo step after a demo step has **no fade**: the recording cuts. | render loop (`cut`) |
| **Focus zoom** 9–12 frames, 100→115% of a prepared crop | When the region shown changes from one demo step to the next, the step **pushes in from 115% of its box to the box over 11 frames** (`zoompan` on those frames only, then the exact rule-18 crop). Coming in from a slide, the crossfade is the only motion. It never runs on a timer. | `motion.focus_filter`, `segment_argv(focus=)` |
| **Lower-third label** 8-frame in, ~3 s hold, 8-frame out | Miro's name plate on his intro clip **rises in over 8 frames, holds 3 s and fades out over 8.** The ending plate (store + Subscribe) rises in and **stays**, so the next step stays visible. | `presenter_argv(moving=)` |
| **Outro fade** 8–12 frames; keep the final card visible | The 3D end card **fades in over Miro's held last frame in 10 frames** and then holds to the last frame. The part keeps the same length, so the minutes gate doesn't move. | `presenter_argv(card=)` |
| Topic / intro / ending cards | The one-line promise ("Today" + topic) rises, then the "Under N minutes" clock. The same applies to the welcome and the narrated ending. | `TOPIC_/INTRO_/OUTRO_MOTION` |
| "Avoid automatic zooms or transitions on a fixed timer" | Nothing moves without a change in content. The old `code_focus` push-in was never wired, and none was added. | — |
| Brand sting ≤ ~1 s, blue line under the wordmark | Kept: the 1 s 3D sting (rule 17). | unchanged |
| Rhythm: show → act → inspect → explain; hold key info | Already the script structure: payoff, lesson with demo steps, pause, lab "expect", recap. Pacing (PAD 0.6 s, SCENE_GAP 0.6 s) is unchanged. Motions settle early in each step, so the rest of the step is a still to read. | — |
| **Shorts**: phrase-level captions with correct technical names, kept off app controls | Teaching Shorts now **burn the lesson's own words** (the manifest timings, cues stripped) as phrases of at most 5 words. They sit just under the 16:9 lesson (y ≥ 1300 of 1920), clear of the right-hand buttons, in #EEF2FA type (DejaVu Sans, see below) on a #0F1420 box. If libass fails, the Short is cut without captions and the record says so. | `tutorial_short.caption_ass`, `short_argv(ass=)` |
| Shorts: first second = the result | Already true: the payoff cut starts at the payoff's first line (`tutorial_short._payoff`), so it opens on the traced workflow and its result. | unchanged |

## Not done, and why

- **The guide's palette** (navy #101B30, blue #526DE6, near-white #F6F8FF) and **amber/green state colours**. Miro's 2026-10-12 brand rule is black + royal blue only, and the brand tests enforce it. The sample's teal checkpoint shows as the blue tick and the words. The new dot is in `TEXT_PAIRS`.
- **Payoff before the brand sting.** `bookends_refusal` requires the cached intro as the first part, and `heard_refusal` checks the opener inside the first 30 s. Rule 17 already puts the payoff right after the opener, and the sting stays at 1 s.
- **Re-cutting the sting with a drawn underline.** The sting is the pre-rendered 3D kit (`tools/tutorial3d/build.py`). Changing it means re-rendering the kit on the Mac, which is outside this change.
- **Cursor emphasis timing** (6-frame ring in, hold, 6-frame out). The cursor and the royal-blue ring are baked into the demo recordings by `tools/tutorial_demos/record.py`. Retiming them means re-recording against local n8n and the Inspector.
- **Card tilt that settles flat; the mask-clipped line reveal.** These need a fresh still per frame, which means Chromium per frame on a one-CPU server. Rise + fade gives the same eased entrance for one extra screenshot per element.
- **The "focus" move on slides** (one card flying to centre). On a teaching slide it would be a second focal point and cost a full-frame scale. Push-ins are kept for demos, where the region really changes.
- **End card with a reserved next-video slot.** End screens are set by Miro in Studio (rule 20, no API), and nothing in the video fakes a clickable card. The 3D Subscribe button and the mirrored store label fill the card's right side, so in Studio the next-video element has to go over or beside that button. Freeing that slot means changing the 3D kit's end-card loop.
- **One type family on the Shorts captions.** They are set in DejaVu Sans, not the brand's Atkinson Hyperlegible. libass needs a TTF or OTF file, and `assets/fonts` only has Atkinson as a web `.woff`. Adding the TTF and a `fontsdir=` to the `ass` filter would fix it.
- **Recomposing the active panel for 9:16.** The Short still sets the 16:9 lesson over a blur. A per-scene crop would need per-segment geometry in the manifest. Captions now use the free band.
- **The four-video A/B test and the Studio retention review.** These are a review process for Miro, not render code. Rule 21's Monday table already carries average view duration and percentage.

## Render cost (n8n #1, the same Mac and script, `say -v Daniel` preview)

CPU is user + sys, the stand-in for the one-CPU server. "Warm" is the steady
state per episode: intro, ending and presenter parts are cached. "Cold"
renders them too.

| | Cold CPU | Cold wall | Warm CPU | Warm wall |
|---|---|---|---|---|
| Before (704f736) | 411 s | 157 s | 344 s | 129 s |
| After | 402 s | 155 s | 332 s | 129 s |
| After, rebased on 220f3c9 (cold only) | 408 s | 156 s | — | — |

The cost is level. Per step the renderer takes two or three more Chromium
screenshots, but the crossfade overlay no longer evaluates an x expression on
every frame. Entering elements are small PNGs looped only for their 9 frames.
The trace dot is one 36 px still. `zoompan` runs 11 frames per demo push.
`tests/test_academy_motion.py::test_a_teaching_segment_stays_inside_the_render_budget`
holds the full-frame overlays, the encodes and the looped inputs to that
shape.
