# Production system

How an episode is assembled so the same six templates produce every lesson, and where each check happens before anything is published. No paid effect pack and no ad spend; the editor's keyframes, text, crop and audio tools are enough.

## Pipeline

```
record ──▶ rough cut ──▶ clarity pass ──▶ brand pass ──▶ captions + mix ──▶ export ──▶ phone QA ──▶ publish
  │            │              │               │                │                              │
  V1 source    V1 only,       V2 crops,       V4 sting,        V3 captions,                   Studio: real
  at readable  no effects     callouts,       end card         A1 cleaned,                    end-screen
  UI scale                    traces          (last 20 s)      A2 music ducked                element + links
```

Each stage has one output and one gate. Nothing moves right until the gate passes; a failed gate goes back one stage, never two.

| Stage | Input | Output | Gate |
|---|---|---|---|
| Record | Outline with the promised outcome | Clean screen recording, UI scaled so `support` text is the smallest thing on screen; a copy kept for reframing | The demonstration actually works end to end |
| Rough cut | Recording + narration | V1 + A1 with dead time removed, no effects | A viewer can follow it with no graphics |
| Clarity pass | Rough cut | V2 crops (`focus-zoom`), CursorRing at each meaningful click, StepMap at each orientation point, CheckpointTag at each verified result | Every added effect answers where/what/next |
| Brand pass | Clarity cut | ResultTitle at 0 s, BrandSting after it, EndCard in the last 20 s | Sting ≤ `sting`; end card keeps `endscreen-reserve` empty |
| Captions + mix | Brand cut | V3 captions on a navy plate, technical names corrected, A2 music ducked under explanations | No caption covers a command or result; fades don't cut words |
| Export | Final timeline | 1920×1080 at 30 fps H.264 MP4 (AAC audio); a separate 1080×1920 edit for Shorts | Listen to the exported file, not the timeline |
| Phone QA | Export | Pass/fail against the checklist | Key labels, code and values readable on a phone |
| Publish | QA'd export | Studio upload with chapters, the real end-screen element and the resource link | Next lesson exists; links resolve |

## Track layout

| Track | Holds | Source of truth |
|---|---|---|
| V1 demonstration | The screen recording | The real app |
| V2 explanation | Crops, masks, CursorRing, WorkflowTrace, StepMap, CheckpointTag | This system's components |
| V3 text | Chapter labels, glossary cards, captions | `tokens.json` type scale |
| V4 brand | BrandSting, EndCard | This system |
| A1 narration | Speech with pauses matched to actions | Recorded take |
| A2 sound | Light transition cues, licensed music, ducked | Licensed sources only |

## The six templates (build once)

1. BrandSting — one second.
2. ResultTitle — result-first title overlay.
3. StepMap — step label and workflow map.
4. CursorRing — cursor and focus preset.
5. CheckpointTag + ErrorRecovery — checkpoint and error/recovery cards.
6. EndCard — 15-second end card with the native-element reserve.

Save each as a project preset with its text fields exposed. An episode changes text and timecodes; it does not restyle.

## Failure modes and rollbacks

| Symptom in Studio | Likely stage | Fix |
|---|---|---|
| Drop in the first 30 s | Opening | Result not visible by 3 s, or title/thumbnail promise differs from the frame |
| Drop at the sting or an explanation card | Brand / Clarity | Sting over 1 s, or a concept card that never returned to the real app |
| Rewinds at a checkpoint | Clarity | Value not held for `hold-tag`; crop too wide for a phone |
| Low end-screen clicks | Publish | Fake thumbnail painted instead of the native element; next video not specific |

A rewind can mean usefulness or confusion; a departure can mean the viewer got the answer. Compare equivalent 7-day and 28-day windows across four similar tutorials before changing the system; small audiences don't support conclusions from a handful of views.

## Delivery spec

| Output | Spec |
|---|---|
| Long-form | 1920×1080, 30 fps, H.264 High, 16–24 Mbps, AAC 48 kHz; captions as SRT uploaded separately |
| Short | 1080×1920 separate edit, same codec; burned-in captions inside the safe area plus SRT |
| Thumbnail | 1280×720 PNG, one real screenshot, 2–5 words in `chapter`, the same outcome as the title |
| Hand-off to an editor | Source recording, narration, this system's link, the exact next-video and resource destinations |
