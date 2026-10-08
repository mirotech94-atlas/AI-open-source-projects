/* The reveal choreography, adapted from the GetLayers house pattern.
 *
 * WHAT WAS TAKEN AND WHAT WAS REFUSED
 *
 * The house default opens with an immersive loader over a progress counter.
 * That was refused here, on the library's own terms: its guide says "be honest
 * about what you are measuring, or don't show a number", and this is a static
 * page with nothing to measure — no bundle, no model, no scene to compile
 * above the fold. A counter here would be a setTimeout dressed as progress, on
 * a site whose whole claim is that it reports what it actually observed. The
 * guide's second sanctioned design is the one used instead: no counter, just a
 * veil. `#hero-fade` already exists for the lattice, so the hero has a curtain
 * without anything new being introduced to lift.
 *
 * THE GATE FLIPS AT THE START OF THE EXIT, NOT ON ITS REST. This is the guide's
 * most consistent finding and the one worth honouring precisely: the content
 * must animate in THROUGH the departing veil, so the two read as one gesture
 * rather than a curtain followed by a page. Both begin on this script's own
 * execution, which is also when the veil starts lifting.
 *
 * NOTHING IS HIDDEN UNLESS THIS SCRIPT RUNS. `app.js` states the site's rule —
 * the page has to keep working without a script — so the hiding is applied by
 * JS and removed by JS. The `.js-reveal` class this adds to <html> is what arms
 * the CSS; with scripting off it is never added, no rule matches, and every
 * element renders exactly as it would have. That is also why the elements are
 * selected here rather than marked up with a data- attribute: the locale
 * generator pins whole tags, so adding an attribute to a pinned <h3> would fail
 * the build for eight languages.
 *
 * NO CLIPPED REVEALS. Both the Evolve style's contract and the choreography
 * guide forbid it, for different reasons that agree: a clip shears the blur
 * halo at the line box, which is the entire effect, and the display face's cap
 * trimming means a clip shaves glyphs. Entrances are blur + opacity + a short
 * lift, never an overflow mask.
 *
 * REDUCED MOTION reduces rather than deletes. The guide's own example keeps
 * opacity and drops translate and blur; a page that snaps has not resolved, it
 * has skipped. The CSS at the end of styles.css already kills transitions
 * globally under the query, so everything lands at its final value on the first
 * frame — which is the behaviour wanted, not a compromise.
 */
(function () {
  "use strict";

  var root = document.documentElement;

  // Armed as early as this file executes. It is `defer`red, so the page has
  // already parsed and painted its text once; the hide-then-reveal is a single
  // frame's worth of flicker at worst, and the alternative — hiding in the
  // stylesheet — leaves a no-JS visitor with a blank page.
  root.className += " js-reveal";

  var still = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function show(el, delay) {
    if (still) { el.className += " is-in"; return; }
    el.style.transitionDelay = delay + "ms";
    // Two frames: one for the browser to record the hidden start value, one to
    // change it. Setting both in the same frame is the classic way to get no
    // transition at all.
    requestAnimationFrame(function () {
      requestAnimationFrame(function () { el.className += " is-in"; });
    });
  }

  function collect(sel, within) {
    return Array.prototype.slice.call((within || document).querySelectorAll(sel));
  }

  /* ---- above the fold: one staggered sweep, started now ----
     90ms between steps and 700ms per step are the committed Style's own
     numbers (Evolve: 700ms fades over a 0.75rem lift), inside the guide's
     90-140ms line band. The readout is last because it is the payload: the
     figure should land after the sentence that frames it. */
  var heroOrder = [".pill", "h1", ".sub", ".cta-row", ".cta-note"];
  var hero = document.querySelector(".hero");
  var step = 0;
  if (hero) {
    heroOrder.forEach(function (sel) {
      var el = hero.querySelector(sel);
      if (!el) return;
      el.className += " rv";
      show(el, step * 90);
      step++;
    });
    var readout = hero.querySelector(".readout");
    if (readout) { readout.className += " rv"; show(readout, step * 90 + 120); }
  }

  /* ---- below the fold: plain viewport entry, once ----
     The guide is explicit that only above-the-fold content is gated on the
     curtain; gating what nobody can see yet just delays it. A single observer
     for every target, unobserving as it fires, so a long page does not keep
     dozens of live entries. */
  var targets = collect("main section:not(.hero) > .wrap > *, main section:not(.hero) > .wrap > * > article");
  if (!targets.length || !window.IntersectionObserver) return;

  targets.forEach(function (el) { el.className += " rv"; });

  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (!entry.isIntersecting) return;
      show(entry.target, 0);
      io.unobserve(entry.target);
    });
  }, { threshold: 0, rootMargin: "0px 0px -12% 0px" });

  targets.forEach(function (el) { io.observe(el); });
})();
