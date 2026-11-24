# Air Glass

Air Glass is a hand-tracking HUD that runs in the browser on a Mac with a webcam.
You see yourself mirrored on screen with a translucent HUD over the picture.
You pick a 3D prop from the side menu and it appears as a hologram between your
hands. You can grab it, turn it, scale it, or flick it off to the right, where
ATLAS catches it and says a short line.

## The main version is inside ATLAS

Since 2026-10-06 Air Glass lives in the ATLAS dashboard at **`/airglass`**
(`ui/src/pages/AirGlass.tsx`, `ui/src/airglass/`), and in ATLAS Menu's own **Air Glass**
window (right-click the menu-bar mark, or say "open Air Glass"). That version is the
one to use. It differs from this folder in these ways:

- **One gesture: pinch and slide, either hand** (2026-10-06, after the owner tried it with
  his real camera and his forefinger could not pick a model). Pinch a model in the tray
  and slide it up: it comes out, held. Pinch the model and slide: it follows your
  fingers and turns like a ball under them. Let go: it stays and settles. Let go over
  the **ATLAS** zone: ATLAS takes it. Pinch-tap + / − to size it. With both hands up,
  the one that pinched first owns the grab. A soft dot sits on your pinch, with a ring
  that fills as the pinch closes. There is no hover-to-pick, no push/pull depth, no
  two-hand scale and no flick. Every target sits where a raised hand can reach it with
  its palm still in the camera's frame (`ui/src/airglass/layout.ts`; the old bottom
  tray was out of reach, which is why picking failed).
- **Talk while you do it.** Listening is on while the page is open (the Mic badge
  mutes it). "Make this bigger", "put it here", "spin it slowly", "give it to ATLAS":
  "this" is what your hand held when you said it, "here" is where your pinch was.
  "ATLAS, look" (or the Ask ATLAS button) shows ATLAS **one still** from the camera, at
  that moment, through a free vision model; it is never kept. ATLAS answers in its own
  Fish Audio voice, beside the object.
- **Four shelves.** Models (the built-in ones, plus any `.glb`/`.gltf` you add). **Your
  ATLAS data** as floating panels: Health, Today's tasks, Store sales and Agents
  working, each read fresh and stamped with its read time. **ATLAS Academy** products
  as 3D cards with their covers. A **free library**, which is marked coming soon
  because it needs a server endpoint.
- **Explain it** (E, the Explain button, or say "explain"). ATLAS says what it is
  holding: fixed words for models and products, and for a data panel the numbers it
  just read and when it read them. It never makes anything up.
- **Hands, voice, buttons and keys** (1–9, arrows, + −, R, A, E, L to look, M to mute).
  Speech is recognised on the Mac when the browser can promise that; otherwise only the
  stretches where you speak go to ATLAS's own transcriber, and the Mic badge says which.
  ATLAS Menu's ⌘ conversation works too, and understands "this" the same way.
- **Everything is same-origin.** three.js and MediaPipe are bundled. The hand model
  comes from ATLAS's `/wake`, `/healthz` is read from the same origin (so the CORS
  limit below does not apply), and models you drop in are parsed in memory. It runs
  under the dashboard's CSP with no CDN.
- **The camera.** While the page is open it borrows the camera from pinch-to-wake and
  gives it back when you leave.

## This folder: the standalone, offline version

This folder keeps the **original** standalone page and its original controls (palm
hold for the menu, wrist turn to rotate), for use without ATLAS. It has no build step
and no install. It is not part of the ATLAS service: nothing under `atlas/`, `ui/` or
`db/` uses it, and the production image leaves it out (`.dockerignore`).

## Run it

```sh
apps/airglass/serve.sh          # serves on http://127.0.0.1:8765 and opens it
apps/airglass/serve.sh demo     # opens straight into demo mode
```

`serve.sh` runs `python3 -m http.server 8765 --bind 127.0.0.1` from this folder.
Browsers only give camera access on `localhost`, `127.0.0.1` or https, so open
the page from that address and not as a `file://` path. Allow the camera when the
browser asks.

With no camera, or with access denied, the page says *Camera unavailable — allow
camera access or use the demo mode*. **Demo mode** (button, `D` key, or `?demo` in
the URL) runs a synthetic hand through the same gesture code: grab, rotate,
two-hand scale, open the menu, pick the torus knot, toss it to ATLAS.

## Gestures

| Gesture | What it does |
|---|---|
| Pinch (thumb tip to index tip) | Grab the prop. Hysteresis: a pinch starts below 0.32 hand sizes and ends above 0.48, so it does not flicker at the edge. |
| Move a pinched hand | Moves the prop |
| Turn the pinched hand (roll, pitch, yaw) | Rotates the prop on X, Y and Z by following the palm's orientation |
| Pinch with both hands | Hands apart or together scales the prop. Turning the line between the hands rotates it. Moving both hands moves it. |
| Flick and let go | A release faster than 0.9 screen widths per second tosses the prop. It flies with damping and no gravity. If it crosses the right edge (or the menu's left edge when the menu is open), ATLAS catches it, it goes into the VAULT, and ATLAS says its line. Other edges bounce it back. |
| Open palm held 1 s | Opens or closes the side menu. A ring around the palm fills while you hold. |
| Point, then pinch on a menu item | Selects it. The pinch reticle is the cursor. A pinch that starts over the open menu selects and never grabs. |

The mouse works too. Click menu items, click **Open file…**, or drop a `.glb` or
`.gltf` anywhere on the page. A `.gltf` that uses separate `.bin` or texture files
loads when you pick all of those files together.

Keys: `H` hologram/solid, `V` voice, `D` demo, `M` menu, `S` ATLAS status.

Landmarks are smoothed with a one-euro filter (steady at rest, quick in
motion). All gesture thresholds scale with the hand's size in the picture, so
they hold whether you sit close to the camera or lean back.

## Privacy

Everything runs in this browser tab. No frame, landmark or image leaves the
machine: no code here sends one anywhere. A Content-Security-Policy in
`index.html` also limits what the page can reach. Scripts may load only from
this folder and `cdn.jsdelivr.net`, and connections are allowed only to this
server, the two hosts below that serve the pinned library and model files, and
ATLAS's public `/healthz` (used only when the status toggle is on). The CSP
limits where the page can connect; it is the code that sends nothing.

| What | Pinned URL |
|---|---|
| three.js 0.186.1 | `https://cdn.jsdelivr.net/npm/three@0.186.1/…` |
| MediaPipe Tasks Vision 1.1.0 | `https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@1.1.0/…` (JS + WASM) |
| Hand landmarker model | `https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task` |

MediaPipe and the model download only when the camera starts. Demo mode fetches
nothing beyond three.js. Models opened from disk are read through `blob:` URLs,
and any URL a model points at outside the picked files resolves to nothing.

ATLAS's lines show as captions only, and nothing is spoken. The owner wants ATLAS to
speak only in his chosen Fish Audio voice. This page has no ATLAS session, so it
can reach neither Fish Audio nor ATLAS's offline pack of Fish clips, and it does not
use the browser's `speechSynthesis` (the Mac's system voices). The Air Glass inside
ATLAS speaks in the Fish voice, offline included. The lines are fixed text with no
model call. The VOICE toggle shows CAPTIONS while it is on.

If you edit the importmap in `index.html`, recompute its hash in the CSP or
nothing will load:

```sh
python3 -c "import re,hashlib,base64;s=open('apps/airglass/index.html').read();m=re.search(r'<script type=\"importmap\">(.*?)</script>',s,re.S);print(base64.b64encode(hashlib.sha256(m.group(1).encode()).digest()).decode())"
```

## ATLAS status orb (optional, off by default)

`S` or the **ATLAS STATUS** toggle reads local ATLAS at `http://127.0.0.1:8080/healthz` once a
minute and judges it the way the watchdog does (`docs/watchdog.md`):

- **OK** (green): both heartbeat ages are 900 s or less.
- **STALE** (amber): either age is over 900 s. A stale age wins over a missing one.
- **UNKNOWN** (grey, unlit, dashed, with the word): anything that could not be
  read or judged. That covers a network error, a timeout, a non-200 answer, bad
  JSON, a missing age, or CORS. It is never shown as green (CLAUDE.md invariant 7).

Each reading shows the time it was taken.

**Known limit:** `/healthz` sends no `Access-Control-Allow-Origin` header today,
so a browser page served from `127.0.0.1` cannot read it. Until the server sends
CORS headers for that path, the orb shows UNKNOWN.

## Browser support

- **Chrome and Arc** (Chromium) on macOS: supported, and this is where it was tested.
  The GPU delegate runs hand tracking, with a CPU fallback.
- **Safari 16.4+**: should work, since it has import maps, WebGL2, WASM and
  getUserMedia on localhost, but it has not been tested. MediaPipe's GPU path
  is slower there.
- Firefox: not a target.

Chrome blocks speech until the page has had a click or a key press, and a
gesture does not count. Until then the page shows "Click once anywhere to let
ATLAS speak" and catches appear as captions only. One click and ATLAS talks.

## Known limits

- Tracking needs reasonable light and both hands in frame. Fast flicks blur, and
  the tracker can lose the hand. A hand that vanishes mid-grab counts as a
  release at its last speed, so the toss still happens.
- The webcam gives no reliable depth, so you cannot push or pull a prop
  toward the camera. Two-hand rotation turns about the screen axis only, and full
  X/Y/Z rotation comes from turning one pinched hand.
- Wrist rotation reads MediaPipe's relative depth, which is noisy. Large pitch
  and yaw are less steady than roll.
- `.glb`/`.gltf` files that need Draco or KTX2 decoders do not load. Those
  decoders would mean more third-party downloads. Skinned or animated models
  show their rest pose, and the hologram shader does not skin.
- Some faces of a model can be flat to the camera. Those look faint under the
  hologram shader, which lights edges. The solid look (`H`) shows them normally.
- Catches are named by prop. Models opened from disk get the generic "Caught it."

## Tests

```sh
node --test apps/airglass/test/
```

The tests use Node's built-in runner and need no install. They cover pinch
hysteresis and scale invariance, two-hand scale and rotation (including the
±180° wrap), release velocity and the toss threshold, the flight step, the
one-euro filter, the cover-crop and mirror mapping, the palm frame, the gesture
engine (toss, vanish mid-flick, two-hand to one-hand, menu select, palm hold),
the demo script, and the `/healthz` classifier.

## Files

| File | Role |
|---|---|
| `index.html`, `style.css` | Page, CSP, importmap and HUD markup |
| `main.js` | Wires camera or demo, gestures, scene, HUD and voice together |
| `gestures.js` | Gesture maths and the gesture engine, with no DOM (tested) |
| `scene.js` | three.js stage, hologram shader, procedural props and glTF loading |
| `hud.js` | Panel, status, overlay, captions and the orb |
| `voice.js` | Captions only: no system voice (ATLAS speaks only in Fish) |
| `demo.js` | Synthetic hand and the demo script (tested) |
| `health.js` | `/healthz` reading and judgement (tested) |
| `serve.sh` | Local server on 127.0.0.1:8765 |
