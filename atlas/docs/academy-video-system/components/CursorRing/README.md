The action preset: a focus zoom into a prepared crop of the real panel, then one cursor ring at the meaningful click, then nothing moves. It is how the learner finds the exact control.

**The editor supplies:** the crop rectangle (recorded at a UI scale where `support` text is the smallest thing on screen, so the 115% push stays sharp), the click frame, and the control's size for the ring.

**Behaviour:** `focus-zoom` (9–12 frames) from 100% to 115% on `settle`, then hold. At the click: the ring reveals in 6 frames (`cursor-reveal`) with a `blue` 6px stroke and a `blue-tint` halo, holds briefly, fades in 6 frames. One ring per click; the crop stays until the result is inspected.

| Do | Don't |
|---|---|
| Zoom into a region, not onto the cursor | Chase the cursor around the screen |
| Stop moving once the control is found | Pulse the ring continuously or on every mouse move |
| Hold the result after the click long enough to read | Cut away the moment the button is pressed |
