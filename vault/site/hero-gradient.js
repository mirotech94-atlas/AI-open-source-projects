/* The hero lattice — Astragal, from the GetLayers library, vendored.
 *
 * WHY THIS FILE EXISTS AT ALL, rather than a <script src> to a CDN: every
 * Vault surface is first-party only, for the reason `fonts.css` sets out —
 * a network watchdog has to paint on an isolated or WAN-down network, and a
 * page that needs a third party to render goes blank in exactly the situation
 * the owner bought it for. See CLAUDE.md § Design assets. The upstream asset
 * is plain WebGL2 with no library and no build step, so vendoring it costs
 * nothing but this comment.
 *
 * WHAT WAS CHANGED FROM THE LIBRARY COPY, and what deliberately was not:
 *
 *  - COLOUR, yes. The five-stop ramp below is Vault's own, read off
 *    `styles.css`: --bg, --panel, --line, --deep, --acc. GetLayers offered a
 *    tint derived from its Evolve style whose mid-stop was #ff1f4a — a red.
 *    That was refused rather than applied: on every Vault surface red means
 *    an alert and nothing else, and a decorative red behind the headline would
 *    teach a first-time reader the opposite. Colour is the one layer the
 *    asset's contract marks mutable, so re-tinting here is sanctioned; the
 *    values simply come from our spine instead of theirs.
 *
 *  - MOTION AND COMPOSITION, no. The contract marks BOTH preserved, and is
 *    specific about why: "the lattice parameters are the picture — change the
 *    fold count or the bar spacing and it stops being a quasicrystal and
 *    becomes a grid." So `pitch`, `turn`, `barWidth`, `speed` and the pointer
 *    knobs are upstream's, untouched. If this ever needs a different rhythm,
 *    the answer is a different asset, not edits here.
 *
 *  - The authoring panel was stripped (19 elements and its ~370 lines of
 *    controls). It is a tool for rolling looks in the library, not part of the
 *    asset, and upstream says to remove it for production.
 *
 * REDUCED MOTION is handled here rather than inherited, and that is
 * deliberate. GetLayers' own choreography guide warns that two of its
 * templates ship a real bug — the shader keeps running because a canvas loop
 * is not covered by an animation library's global skip flag. It has to be
 * turned off by hand, which is what `still` below does: one frame is drawn and
 * no loop is scheduled, so the lattice is still *there*, just not moving. The
 * asset supports this natively — its own contract notes that at
 * `pointerStrength` 0 it is "effectively a still" — so nothing is lost but the
 * drift. Reduce, don't delete.
 *
 * The canvas pauses itself off-screen through an IntersectionObserver that
 * came with the asset, so scrolling past the hero stops the work.
 */
(function () {
'use strict';
if (!document.getElementById('hero-gl')) return;

const still = window.matchMedia
  && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/* ====== CONFIG ====== */
const CONFIG = {
  bgColor:  '#080d17',
  colorA:   '#0e1524',
  colorB:   '#1e2b42',
  colorC:   '#1257c4',
  colorD:   '#6ba5ff',

  scale:            1.05,
  speed:            0.33,
  roughness:        0.52,
  lacunarity:       2.05,

  pitch:           26.00,
  turn:             0.35,
  level:            0.12,
  barWidth:         0.36,
  barLift:          0.42,
  paneField:        0.32,

  contrast:         1.06,
  midpoint:         0.50,
  glow:             0.08,
  sink:             0.11,

  grain:            0,        // off by default — house grain, turn it up to taste

  grainAnim:        0,        // 0 static · 1 re-seeds the grain like film
  dither:           1.25,
  vignette:         0.16,

  cursor:           1,         // 1 follows the hand · 0 ignores it (fully ambient)

  pointerRadius:    0.32,
  pointerBend:      1.05,
  pointerStrength:  0.10,
  parallax:         0.0033,

  maxDpr:           1.0,
}


function hexToVec3(hex) {
  const n = parseInt(hex.slice(1), 16)
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255]
}


/* ====== GL BOILERPLATE (frozen chrome — do not edit per gradient) ====== */
const canvas = document.getElementById('hero-gl')
const gl = canvas.getContext('webgl2', { alpha: false, antialias: false, depth: false, stencil: false, powerPreference: 'high-performance' })
if (!gl) {
  // Upstream puts a fixed, full-viewport "WebGL2 is not available" notice on
  // the page here. That is right for a library page whose entire subject is the
  // shader, and quite wrong here: it would cover the whole marketing site for
  // anyone on an older machine or a locked-down corporate browser — which is a
  // fair description of the people this product is sold to. The hero is
  // designed to read on its flat --bg ground; the lattice is an enhancement on
  // top of it. So clear the reveal veil and leave silently.
  document.getElementById('hero-fade').style.opacity = 0
  return
}

const VERT = `#version 300 es
void main() {
  vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
  gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}`

/* ======================================================================
   Astragal — glazing bars on a five-fold quasi-periodic grid, bowing under the hand

   A lattice with no unit cell. Five plane waves at equal angular spacing are
   summed and the bars are drawn on one level set of the sum — five is the
   number that cannot tile the plane, so the pattern is ordered everywhere and
   periodic nowhere, and the eye keeps finding rosettes that never quite repeat.

   The bars are drawn as bands around the level, widened by the local gradient
   so a bar seen edge-on stays the same visual weight as one seen flat. Between
   them the panes carry a very quiet field, which is what stops the whole thing
   from reading as line art.

   The cursor bows it. The trail is added to each wave's PHASE — not to the
   geometry — so the lattice bends as one piece, bars stay continuous through
   the bend, and it straightens again from the far end as the wake catches up.
   ====================================================================== */
const FRAG = `#version 300 es
precision highp float;
out vec4 fragColor;

uniform vec2  iResolution;
uniform float iTime;
uniform vec2  iMouse;          // aspect-corrected units, same space as uv — the body node
uniform vec2  iMouseVel;       // lead node minus body node — a velocity proxy
uniform vec2  iMouseWake;      // a third, much slower node — the tail of the trail

uniform vec3  uBgColor, uColorA, uColorB, uColorC, uColorD;
uniform float uScale, uSpeed, uRoughness, uLacunarity;
uniform float uPitch, uTurn, uLevel, uBarWidth, uBarLift, uPaneField;
uniform float uContrast, uMidpoint, uGlow, uSink;
uniform float uGrain, uDither, uVignette;
uniform float uPointerRadius, uPointerBend, uPointerStrength, uParallax;

#define TRAIL_TAPS 6
#define OCTAVES 3
#define WAVES 5
#define PI 3.14159265359

vec2 hash2(vec2 p) {
  p = vec2(dot(p, vec2(127.1, 311.7)), dot(p, vec2(269.5, 183.3)));
  return -1.0 + 2.0 * fract(sin(p) * 43758.5453123);
}

float hash1(vec2 p) {
  return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123);
}

float snoise(vec2 p) {
  const float K1 = 0.366025404, K2 = 0.211324865;
  vec2 i = floor(p + (p.x + p.y) * K1);
  vec2 a = p - i + (i.x + i.y) * K2;
  float m = step(a.y, a.x);
  vec2 o = vec2(m, 1.0 - m);
  vec2 b = a - o + K2;
  vec2 c = a - 1.0 + 2.0 * K2;
  vec3 h = max(0.5 - vec3(dot(a, a), dot(b, b), dot(c, c)), 0.0);
  vec3 n = h * h * h * h * vec3(dot(a, hash2(i)), dot(b, hash2(i + o)), dot(c, hash2(i + 1.0)));
  return dot(n, vec3(70.0));
}

float fbm(vec2 p) {
  float v = 0.0, amp = 0.5;
  for (int i = 0; i < OCTAVES; i++) {
    v += amp * snoise(p);
    p *= uLacunarity;
    amp *= uRoughness;
  }
  return v;
}

/* The trail. The shader has no memory, so the tail is reconstructed from three
   pointer nodes: TRAIL_TAPS gaussians strung along the segment between the slow
   wake node and the body node, weighted toward the head. A resting cursor puts
   every tap in the same place and the result is one clean gaussian of exactly
   1.0 at the centre; a moving cursor stretches them into a tapered ribbon that
   melts away from the back as the wake catches up. No branch, no jump, and the
   falloff is the same gaussian at every point along it. */
float trail(vec2 uv, float radius) {
  /* The sample point is wobbled by a slow field first. A gaussian is a perfect
     circle, and a perfect circle parked under a resting cursor is the one thing
     that reads as drawn rather than felt; warping the coordinate by a fraction
     of the radius makes the touch an organic patch that breathes, at the cost
     of two noise taps and nothing else. */
  vec2 wob = vec2(snoise(uv * 1.7 + vec2(0.0, iTime * 0.05)),
                  snoise(uv * 1.7 + vec2(4.3, -iTime * 0.04)));
  uv += wob * radius * 0.20;
  float a = 0.0, wsum = 0.0;
  float r2 = max(1e-4, radius * radius);
  for (int i = 0; i < TRAIL_TAPS; i++) {
    float k = float(i) / float(TRAIL_TAPS - 1);
    vec2 c = mix(iMouseWake, iMouse, k);
    vec2 dd = uv - c;
    float w = mix(0.30, 1.0, k);
    a += w * exp(-dot(dd, dd) / r2);
    wsum += w;
  }
  return a / wsum;
}

vec3 ramp4(float t) {
  vec3 c = mix(uColorA, uColorB, smoothstep(0.00, 0.36, t));
  c = mix(c, uColorC, smoothstep(0.32, 0.70, t));
  c = mix(c, uColorD, smoothstep(0.66, 1.00, t));
  return c;
}

// triangular-PDF dither — the only reliable cure for 8-bit gradient banding
float triDither(vec2 fc) {
  float a = fract(sin(dot(fc, vec2(12.9898, 78.233))) * 43758.5453);
  float b = fract(sin(dot(fc + 17.0, vec2(12.9898, 78.233))) * 43758.5453);
  return (a + b - 1.0) / 255.0;
}

/* Five plane waves at equal angular spacing. Five-fold symmetry cannot tile the
   plane, so the sum is ordered everywhere and periodic nowhere — the cheapest
   honest quasicrystal there is, at one cosine per wave. The bend is added to
   the PHASE, which is why the bars bow instead of breaking. */
float quasi(vec2 p, float t, float bend) {
  float v = 0.0;
  for (int i = 0; i < WAVES; i++) {
    float a = float(i) * PI / float(WAVES) + t * uTurn * 0.05;
    vec2 k = vec2(cos(a), sin(a));
    v += cos(dot(k, p) * uPitch + bend * (0.6 + 0.4 * float(i)));
  }
  return v / float(WAVES);
}

// ---- house grain. ONE look across the collection: an integer hash (no sin() streaks),
// triangular so it reads as film rather than static, weighted into the midtones so it
// never crusts a black or a white. Static by default; uGrainAnim re-seeds it 24×/s.
uniform float uGrainAnim;
float houseGrain(vec2 fc) {
  uvec2 q = uvec2(fc) * uvec2(1597334677u, 3812015801u)
          + uint(floor(iTime * 24.0 * uGrainAnim)) * 2654435769u;
  uint n = q.x ^ q.y; n = n * 1664525u + 1013904223u; n ^= n >> 16u; n *= 2246822519u; n ^= n >> 13u;
  float a = float(n & 0xffffu) / 65535.0;
  n *= 3266489917u; n ^= n >> 16u;
  float b = float(n & 0xffffu) / 65535.0;
  return a + b - 1.0;
}
void main() {
  vec2 uv = (gl_FragCoord.xy - 0.5 * iResolution) / iResolution.y;
  float t = iTime * uSpeed;

  vec2 d = uv - iMouse;
  float near = exp(-dot(d, d) / max(1e-4, uPointerRadius * uPointerRadius));
  float tr = trail(uv, uPointerRadius);

  vec2 p = (uv - iMouse * uParallax) * uScale + d * near * uPointerStrength * 0.5;

  float v = quasi(p, t, tr * uPointerBend);

  /* The bar is a band around one level of the field, widened by the local
     gradient so a bar crossing a steep part of the field keeps the same visual
     weight as one crossing a flat part. */
  float g = max(fwidth(v), 1e-4);
  float bar = 1.0 - smoothstep(uBarWidth * g * 6.0, (uBarWidth + 0.35) * g * 12.0, abs(v - uLevel));

  // the panes: a very quiet field, so the lattice sits on something
  float pane = fbm(p * 0.55 + vec2(0.0, t * 0.07)) * 0.5 + 0.5;

  float f = 0.30 + pane * uPaneField + bar * uBarLift * 0.55;
  f = clamp((f - uMidpoint) * uContrast + 0.5, 0.0, 1.0);

  vec3 col = ramp4(f);
  col += uColorD * uGlow * bar * (0.35 + 0.65 * pane);
  col = mix(uBgColor, col, smoothstep(0.0, max(0.01, uSink), f) * 0.90 + 0.10);

  col *= 1.0 - uVignette * dot(uv, uv);
  { float hgL = clamp(dot(col, vec3(0.299, 0.587, 0.114)), 0.0, 1.0);
    col += houseGrain(gl_FragCoord.xy) * uGrain * mix(1.0, 4.0 * hgL * (1.0 - hgL), 0.6); }
  col += triDither(gl_FragCoord.xy) * uDither;

  fragColor = vec4(clamp(col, 0.0, 1.0), 1.0);
}`

function compile(type, src) {
  const sh = gl.createShader(type)
  gl.shaderSource(sh, src)
  gl.compileShader(sh)
  if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(sh))
  return sh
}

const program = gl.createProgram()
gl.attachShader(program, compile(gl.VERTEX_SHADER, VERT))
gl.attachShader(program, compile(gl.FRAGMENT_SHADER, FRAG))
gl.linkProgram(program)
if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program))
gl.useProgram(program)
gl.bindVertexArray(gl.createVertexArray())

const LOC = {}
const loc = n => (n in LOC ? LOC[n] : (LOC[n] = gl.getUniformLocation(program, n)))
const u1f = (n, v) => gl.uniform1f(loc(n), v)
const u2f = (n, x, y) => gl.uniform2f(loc(n), x, y)
const u3c = (n, hex) => { const c = hexToVec3(hex); gl.uniform3f(loc(n), c[0], c[1], c[2]) }

/* ====== CONFIG → UNIFORMS ====== */
/* Skin tint: the light skin needs a white ground and a blue lattice. Applied
   through CONFIG exactly as getlayers.json requires -- the shader is untouched. */
try { if (window.__GUARDIAN_HERO_TINT) { for (var _k in window.__GUARDIAN_HERO_TINT) { CONFIG[_k] = window.__GUARDIAN_HERO_TINT[_k] } } } catch (e) {}

function applyConfig() {
  gl.useProgram(program)
  u3c('uBgColor', CONFIG.bgColor)
  u3c('uColorA', CONFIG.colorA)
  u3c('uColorB', CONFIG.colorB)
  u3c('uColorC', CONFIG.colorC)
  u3c('uColorD', CONFIG.colorD)
  u1f('uScale', CONFIG.scale)
  u1f('uSpeed', CONFIG.speed)
  u1f('uRoughness', CONFIG.roughness)
  u1f('uLacunarity', CONFIG.lacunarity)
  u1f('uPitch', CONFIG.pitch)
  u1f('uTurn', CONFIG.turn)
  u1f('uLevel', CONFIG.level)
  u1f('uBarWidth', CONFIG.barWidth)
  u1f('uBarLift', CONFIG.barLift)
  u1f('uPaneField', CONFIG.paneField)
  u1f('uContrast', CONFIG.contrast)
  u1f('uMidpoint', CONFIG.midpoint)
  u1f('uGlow', CONFIG.glow)
  u1f('uSink', CONFIG.sink)
  u1f('uGrain', CONFIG.grain)
  u1f('uGrainAnim', CONFIG.grainAnim)
  u1f('uDither', CONFIG.dither)
  u1f('uVignette', CONFIG.vignette)
  u1f('uPointerRadius', CONFIG.pointerRadius)
  u1f('uPointerBend', CONFIG.pointerBend)
  u1f('uPointerStrength', CONFIG.pointerStrength)
  u1f('uParallax', CONFIG.parallax)
  resize()
}

/* ====== RESIZE ====== */
let dpr = 1
/* The canvas's own box, cached. Upstream sizes the drawing buffer from
   innerWidth/innerHeight because in the library this asset IS the window. Here
   it is a band inside the hero -- 1024x615 in a 1024x768 viewport on a laptop --
   and feeding the shader the viewport's aspect instead of the element's stretches
   the lattice and crops it. Measuring the element is the whole of the embedding
   adaptation; nothing about the asset's own geometry is touched.

   It is cached rather than read per event because the pointer handler runs on
   every pointermove, and getBoundingClientRect forces layout. Refreshed on
   resize and on scroll, which are the only things that move it. */
let box = { left: 0, top: 0, width: 1, height: 1 }
function measure() {
  const r = canvas.getBoundingClientRect()
  box = { left: r.left, top: r.top, width: r.width || 1, height: r.height || 1 }
}
function resize() {
  dpr = Math.min(window.devicePixelRatio || 1, CONFIG.maxDpr)
  measure()
  const w = Math.max(1, Math.round(box.width * dpr))
  const h = Math.max(1, Math.round(box.height * dpr))
  if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h }
  gl.viewport(0, 0, w, h)
  gl.useProgram(program)
  u2f('iResolution', w, h)
}
/* Coalesced to one resize per frame. A window drag or a phone rotation fires resize far
   faster than the display refreshes, and every raw call reallocates the drawing buffer and
   re-uploads iResolution — that burst is the hitch you feel while dragging an edge. */
let resizeQueued = false
addEventListener('resize', () => {
  if (resizeQueued) return
  resizeQueued = true
  requestAnimationFrame(() => { resizeQueued = false; resize() })
}, { passive: true })

/* The hero is a content-sized band, so it can change height without the window
   ever resizing -- a font landing, a wrapped headline at a new width. A window
   listener alone leaves the buffer at the old height in exactly those cases. */
if (window.ResizeObserver) {
  let roQueued = false
  new ResizeObserver(() => {
    if (roQueued) return
    roQueued = true
    requestAnimationFrame(() => { roQueued = false; resize() })
  }).observe(canvas)
}
addEventListener('scroll', measure, { passive: true })

/* ====== POINTER (a handful of scalars — the only per-frame CPU maths allowed) ====== */
const mouse = { x: 0, y: 0, ax: 0, ay: 0, wx: 0, wy: 0, tx: 0, ty: 0 }
/* One handler for both events, and it does exactly one job: write the target. Everything
   that moves is integrated in the render loop, so a flick that fires forty events inside a
   single frame costs the same as one that fires one — and a touch now aims on contact
   instead of staying dead until the first drag. */
const aim = e => {
  // Against the canvas's box, for the same reason resize is: with the viewport's
  // numbers the lattice bends toward a point that is not where the hand is, and
  // keeps reacting to a pointer three sections down the page.
  const a = box.width / box.height
  mouse.tx = ((e.clientX - box.left) / box.width - 0.5) * a
  mouse.ty = (0.5 - (e.clientY - box.top) / box.height)
}
addEventListener('pointermove', aim, { passive: true })
addEventListener('pointerdown', aim, { passive: true })

/* ====== RENDER LOOP ====== */
/* The loop does exactly four things: step the pointer followers by elapsed time, upload the
   pointer uniforms, draw one triangle, tick the fps readout. Everything else is the shader's job. */
const fadeEl = document.getElementById('hero-fade')

let visible = true
new IntersectionObserver(es => { visible = es[0].isIntersecting }, { threshold: 0 }).observe(canvas)

const t0 = performance.now()
let prevT = t0, clock = 0
function frame(now) {
  requestAnimationFrame(frame)

  /* PACING — one clamped clock drives everything below it.
     `ms` is the real frame interval pinned to [4.17, 50] and `s` is that interval measured
     in 60 Hz frames, so every ease in this loop advances by elapsed TIME rather than by
     frame count — the same follow-lag on a 60, 120 or 144 Hz panel instead of a chase that
     doubles in speed when the monitor does. The upper clamp on `s` is what stops a dropped
     frame or a GC pause from flinging the chain across the screen in one step, and it sits
     low enough that no follower can overshoot into a wobble. The shader clock is accumulated
     from the SAME clamped interval instead of read off the wall clock — rAF stops in a
     background tab but wall time does not, and handing the shader that gap is exactly what
     makes a field lurch on the way back. Clamped, an alt-tab costs a pause and never a jump. */
  const raw = now - prevT
  prevT = now
  if (!visible || document.hidden) return
  const ms = raw > 50 ? 50 : raw < 4.167 ? 4.167 : raw
  const s = ms > 36.7 ? 2.2 : ms * 0.06
  clock += ms * 0.001

  /* Three poles, not one — a lead, a body and a wake. A single lerp can only decelerate INTO its target: a direction
     change hinges on a corner and the tail of every move reads as dead weight. A quick lead
     node feeds a slower body — the body is what the field follows, and it swings through a
     turn and coasts for a beat after the hand stops. The wake is a third node an order of
     magnitude slower again; it is never drawn on its own, it only says where the body WAS,
     which is what lets the shader lay a tapered trail between the two without keeping a
     frame of history anywhere. All three rates scale with `s`, so the lag is identical at
     60 Hz and at 144. */
  const kLead = 0.105 * s, kBody = 0.043 * s, kWake = 0.017 * s
  mouse.ax += (mouse.tx - mouse.ax) * kLead
  mouse.ay += (mouse.ty - mouse.ay) * kLead
  mouse.x  += (mouse.ax - mouse.x)  * kBody
  mouse.y  += (mouse.ay - mouse.y)  * kBody
  mouse.wx += (mouse.x  - mouse.wx) * kWake
  mouse.wy += (mouse.y  - mouse.wy) * kWake

  u1f('iTime', clock)
  /* cursor 0: the hand is ignored and the pointer eases back to where it rested — the
     gradient goes fully ambient. Nothing else changes, so it can be flipped live. */
  if (mouse.rest === undefined) mouse.rest = { x: mouse.tx, y: mouse.ty }
  if (!CONFIG.cursor) { mouse.tx = mouse.rest.x; mouse.ty = mouse.rest.y }
  u2f('iMouse', mouse.x, mouse.y)
  u2f('iMouseVel', mouse.ax - mouse.x, mouse.ay - mouse.y)
  u2f('iMouseWake', mouse.wx, mouse.wy)
  gl.drawArrays(gl.TRIANGLES, 0, 3)
}

applyConfig()
gl.drawArrays(gl.TRIANGLES, 0, 3)        // one draw before the reveal, so we never fade onto a blank canvas
fadeEl.style.opacity = 0

// The reduced-motion branch. One frame is already on the canvas from the draw
// above, so returning here leaves the lattice fully rendered and simply never
// advances its clock -- the asset's own "effectively a still" state. Scheduling
// the loop and short-circuiting inside it would keep waking the GPU every frame
// to do nothing, which is the bug this branch exists to avoid.
if (!still) requestAnimationFrame(frame)


})();
