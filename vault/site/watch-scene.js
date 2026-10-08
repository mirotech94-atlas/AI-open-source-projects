/* "Quiet Watch" — an original scene, authored for Vault through GetLayers'
 * Scene Lab, which supplies the brief, the CONFIG contract and reference
 * scenes to learn technique from. Nothing here is copied from a reference.
 *
 * WHY IT IS NOT THREE.JS, WHICH SCENE LAB'S GUIDE ASKS FOR
 *
 * The guide's output spec is a self-contained HTML file pulling three.js from a
 * pinned CDN importmap, plus EffectComposer and UnrealBloomPass. That is a good
 * default and it is not available here: Vault's surfaces reach no third-party
 * origin (CLAUDE.md § Design assets, and `fonts.css` for the reason). Vendoring
 * three.js and its post-processing chain would mean roughly three quarters of a
 * megabyte of dependency on a marketing page whose buyers run it on practice
 * reception PCs -- to draw a field of dots.
 *
 * So the guide's *contract* is honoured and its delivery is not: one CONFIG
 * object holding every colour as an '#rrggbb' string and every art-direction
 * value as a number, applied to uniforms and never by editing the shader. The
 * implementation is plain WebGL2 -- one fullscreen triangle, one fragment
 * shader, no library -- exactly as `hero-gradient.js` is. There is nothing to
 * download and nothing to go stale.
 *
 * WHAT IT DEPICTS, AND WHY THAT PARTICULAR THING
 *
 * The catalogue's scenes are built for other products, and its nearest match to
 * a watching product was an eye that blinks and follows the reader -- exactly
 * the wrong idea for something that guards a network on the owner's behalf. So
 * this draws what Vault actually does instead:
 *
 *   a field of small lights, one per device, resting at different depths and
 *   breathing slowly; every so often ONE goes quiet, a ring of attention travels
 *   outward through its neighbours, and the quiet one comes back.
 *
 * It is a loop that always resolves. Nothing in it is ever left broken, there is
 * no alarm colour, and it never tracks the cursor -- because the subject is the
 * network being looked after, not the reader being watched. It is placed behind
 * "How it works", where that loop is what the copy is describing.
 *
 * The palette is Vault's, and only Vault's: --bg, --deep and --acc. No
 * red and no green appear anywhere in it, because on every Vault surface
 * those two mean alert and good, and a decoration that borrows them teaches the
 * reader something false.
 */
(function () {
  "use strict";

  var canvas = document.getElementById("watch-gl");
  if (!canvas) return;

  /* ====== CONFIG ====== *
   * Colours are '#rrggbb' strings; everything else is a number. */
  var CONFIG = {
    bgColor:        '#080d17',   // --bg
    // A device at rest is --acc, NOT --deep. This was wrong the first time and
    // wrong in an instructive way: --deep is 2.95:1 on this ground, which is
    // why site/styles.css only ever uses it as a button FILL with white on top,
    // and why a naive contrast audit flags it (see the brand-spine spec). Drawn
    // as a MARK on the dark ground it is a dark blue on a near-black field --
    // technically present, effectively invisible. --acc is 7.82:1 and is the
    // token this skin already uses for anything that has to read as a mark.
    pointColor:     '#6ba5ff',   // --acc, a device at rest
    // The ring lifts a device to --fg, the page's own ink at 16.82:1. The
    // contrast between the two IS the event: attention passing through.
    liveColor:      '#e9eff8',   // --fg

    cells:           11.0,   // devices across the short axis
    jitter:           0.34,  // how far each sits off its lattice point
    pointSize:        0.082, // radius of one light, in cell units
    softness:         3.4,   // falloff exponent; higher is tighter

    breathe:          0.16,  // how much the resting brightness rises and falls
    breatheSpeed:     0.22,  // and how slowly

    depth:            0.55,  // spread of the parallax layers
    drift:            0.014, // slow sideways travel of the whole field

    noticePeriod:    7.4,    // seconds between one device going quiet
    ringSpeed:       1.45,   // cells per second the attention travels
    ringWidth:       0.42,   // thickness of the travelling ring
    ringLift:        1.70,   // how much brighter a device is as it passes
    quietDepth:      0.86,   // how far the one device dims
    quietRecover:    2.10,   // seconds it takes to come back

    baseAlpha:       0.95,   // overall strength of the whole field
    vignette:        0.30,
    maxDpr:          1.0
  };

  var gl = canvas.getContext("webgl2", {
    alpha: true, antialias: false, depth: false, powerPreference: "low-power"
  });
  // Same rule as the hero: no context, no notice, no scene. The section is
  // designed to read on its flat ground and this only ever sat on top of it.
  if (!gl) return;

  function hexToVec3(hex) {
    var n = parseInt(hex.slice(1), 16);
    return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
  }

  var VERT =
    "#version 300 es\n" +
    "void main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.0-1.0,0.0,1.0);}";

  var FRAG =
    "#version 300 es\n" +
    "precision highp float;\n" +
    "out vec4 o;\n" +
    "uniform vec2 iRes; uniform float iTime;\n" +
    "uniform vec3 uBg, uPoint, uLive;\n" +
    "uniform float uCells,uJitter,uSize,uSoft,uBreathe,uBreatheSpeed,uDepth,uDrift;\n" +
    "uniform float uPeriod,uRingSpeed,uRingWidth,uRingLift,uQuiet,uRecover,uAlpha,uVig;\n" +
    // One hash, used for every per-cell constant. Cheap and stable: the same
    // cell always returns the same jitter, depth and phase, so the field does
    // not shimmer as it drifts.
    "vec3 hash3(vec2 p){vec3 q=vec3(dot(p,vec2(127.1,311.7)),dot(p,vec2(269.5,183.3)),dot(p,vec2(419.2,371.9)));return fract(sin(q)*43758.5453);}\n" +
    "void main(){\n" +
    "  vec2 uv=(gl_FragCoord.xy-0.5*iRes)/iRes.y;\n" +
    "  uv.x+=iTime*uDrift;\n" +
    "  vec2 g=uv*uCells; vec2 gi=floor(g);\n" +
    // Which device is having its moment, and how far into that moment we are.
    "  float epoch=floor(iTime/uPeriod); float age=iTime-epoch*uPeriod;\n" +
    "  vec3 pick=hash3(vec2(epoch,epoch*1.7+3.0));\n" +
    "  vec2 src=floor((pick.xy-0.5)*uCells*0.9);\n" +
    "  float ring=age*uRingSpeed;\n" +
    "  float light=0.0; float live=0.0;\n" +
    // 3x3 neighbourhood: a light's glow can only reach one cell out at these
    // sizes, so anything wider is wasted work on every fragment.
    "  for(int y=-1;y<=1;y++){for(int x=-1;x<=1;x++){\n" +
    "    vec2 cell=gi+vec2(float(x),float(y));\n" +
    "    vec3 h=hash3(cell);\n" +
    "    float z=mix(1.0-uDepth,1.0+uDepth,h.z);\n" +      // depth layer
    "    vec2 pos=cell+0.5+(h.xy-0.5)*uJitter*2.0;\n" +
    "    float d=length(g-pos)/max(uSize*z,0.0005);\n" +
    "    float glow=exp(-pow(d,uSoft));\n" +
    // Resting: a slow breath, each device on its own phase so the field is
    // never in unison -- a field that pulses together reads as a heartbeat,
    // which is an alarm, not a watch.
    "    float rest=1.0+uBreathe*sin(iTime*uBreatheSpeed*6.2831+h.x*6.2831);\n" +
    // The ring of attention: brightest exactly where it is passing.
    "    float dist=length(cell-src);\n" +
    "    float band=exp(-pow((dist-ring)/uRingWidth,2.0));\n" +
    "    float fade=exp(-age*0.45);\n" +          // the whole event settles
    "    float lift=band*fade*uRingLift;\n" +
    // The one that went quiet, and its recovery. `age/uRecover` climbs back to
    // full, so by the next epoch every device is at rest again.
    "    float isSrc=step(length(cell-src),0.01);\n" +
    "    float quiet=isSrc*uQuiet*exp(-age/uRecover);\n" +
    "    float b=max(rest*(1.0-quiet)+lift,0.0)/z;\n" +
    "    light+=glow*b; live+=glow*lift;\n" +
    "  }}\n" +
    "  float v=1.0-uVig*dot(uv,uv);\n" +
    "  vec3 col=mix(uPoint,uLive,clamp(live*1.6,0.0,1.0));\n" +
    "  float a=clamp(light,0.0,1.6)*uAlpha*v;\n" +
    "  o=vec4(mix(uBg,col,clamp(a,0.0,1.0)),clamp(a*1.25,0.0,1.0));\n" +
    "}";

  function compile(type, src) {
    var s = gl.createShader(type);
    gl.shaderSource(s, src); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
      // Fail quiet, not loud: this is decoration on a marketing page.
      if (window.console && console.warn) console.warn(gl.getShaderInfoLog(s));
      return null;
    }
    return s;
  }
  var vs = compile(gl.VERTEX_SHADER, VERT), fs = compile(gl.FRAGMENT_SHADER, FRAG);
  if (!vs || !fs) return;
  var prog = gl.createProgram();
  gl.attachShader(prog, vs); gl.attachShader(prog, fs); gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return;
  gl.useProgram(prog);
  // Attribute-less rendering still needs a vertex array object bound. The whole
  // scene is one triangle generated from gl_VertexID with no buffers at all, so
  // it is easy to assume none is required — and the draw then silently produces
  // nothing, with no GL error and no shader warning to point at it.
  gl.bindVertexArray(gl.createVertexArray());

  var U = {};
  function loc(n) { if (!(n in U)) U[n] = gl.getUniformLocation(prog, n); return U[n]; }

  /* Skin tint: the light skin needs a white ground and a blue lattice. Applied
     through CONFIG exactly as getlayers.json requires -- the shader is untouched. */
  try { if (window.__GUARDIAN_WATCH_TINT) { for (var _k in window.__GUARDIAN_WATCH_TINT) { CONFIG[_k] = window.__GUARDIAN_WATCH_TINT[_k] } } } catch (e) {}

  function applyConfig() {
    gl.useProgram(prog);
    gl.uniform3fv(loc("uBg"), hexToVec3(CONFIG.bgColor));
    gl.uniform3fv(loc("uPoint"), hexToVec3(CONFIG.pointColor));
    gl.uniform3fv(loc("uLive"), hexToVec3(CONFIG.liveColor));
    var map = {
      uCells: "cells", uJitter: "jitter", uSize: "pointSize", uSoft: "softness",
      uBreathe: "breathe", uBreatheSpeed: "breatheSpeed", uDepth: "depth",
      uDrift: "drift", uPeriod: "noticePeriod", uRingSpeed: "ringSpeed",
      uRingWidth: "ringWidth", uRingLift: "ringLift", uQuiet: "quietDepth",
      uRecover: "quietRecover", uAlpha: "baseAlpha", uVig: "vignette"
    };
    for (var u in map) gl.uniform1f(loc(u), CONFIG[map[u]]);
  }

  var dpr = 1;
  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, CONFIG.maxDpr);
    var r = canvas.getBoundingClientRect();
    var w = Math.max(1, Math.round((r.width || 1) * dpr));
    var h = Math.max(1, Math.round((r.height || 1) * dpr));
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
    gl.viewport(0, 0, w, h);
    gl.useProgram(prog);
    gl.uniform2f(loc("iRes"), w, h);
  }

  gl.enable(gl.BLEND);
  gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
  applyConfig();
  resize();

  var still = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function draw(t) {
    gl.useProgram(prog);
    gl.uniform1f(loc("iTime"), t);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }

  // Lazy: nothing runs until the section is actually on screen, and it stops
  // again when it leaves. A scene below the fold that renders from page load is
  // the cost without the benefit -- the visitor has not seen a frame of it.
  // `is-lit` is what the stylesheet fades in on. It is added here rather than
  // being the default, so the canvas never shows a half-drawn first frame on
  // the way past; and it is added on the FIRST intersection only, so scrolling
  // back and forth does not re-fade it.
  function light() { if (canvas.className.indexOf("is-lit") < 0) canvas.className += " is-lit"; }

  var visible = false;
  if (window.IntersectionObserver) {
    new IntersectionObserver(function (es) {
      visible = es[0].isIntersecting;
      if (!visible) return;
      draw(still ? 2.2 : 0);   // a frame exists before the fade starts
      light();
      if (!still) requestAnimationFrame(frame);
    }, { threshold: 0 }).observe(canvas);
  } else {
    // No observer: no lazy mount to manage, so just run. The loop has to be
    // started here too -- it is only kicked off from the observer's callback
    // otherwise, so this branch used to paint a single frame at t=0 and sit
    // there. Harmless in any browser that has IntersectionObserver, which is
    // all of them, and wrong anywhere else.
    visible = true;
    draw(still ? 2.2 : 0);
    light();
    if (!still) requestAnimationFrame(frame);
  }

  // A frozen but complete picture under reduced motion: t is fixed part-way
  // into an event, so the ring is visible rather than the field being a plain
  // grid of dots. Reduce, don't delete.
  if (still) return;   // the observer above has already drawn and lit it

  var t0 = 0;
  function frame(now) {
    if (!visible) return;
    if (!t0) t0 = now;
    draw((now - t0) / 1000);
    requestAnimationFrame(frame);
  }

  var queued = false;
  function onResize() {
    if (queued) return;
    queued = true;
    requestAnimationFrame(function () { queued = false; resize(); if (still) draw(2.2); });
  }
  addEventListener("resize", onResize, { passive: true });
  if (window.ResizeObserver) new ResizeObserver(onResize).observe(canvas);
})();
