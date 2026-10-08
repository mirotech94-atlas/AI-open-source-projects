/* Skin. The dark console skin is the default and what the two WebGL scenes
 * were authored for; the calm (light) skin follows the system preference.
 * This block runs synchronously at parse time and BEFORE hero-gradient.js and
 * watch-scene.js (script order in index.html), because both read CONFIG once
 * at start: the light tints must already be on window when they run. The
 * override is CONFIG-level, exactly what getlayers.json permits -- no shader
 * is edited. On a preference change both scenes are remounted, since neither
 * re-reads CONFIG. */
(function () {
  var HERO_LIGHT = { bgColor: "#ffffff", colorA: "#f2f6fc", colorB: "#dce7f7", colorC: "#1257c4", colorD: "#7fb0ff" };
  var WATCH_LIGHT = { bgColor: "#ffffff", pointColor: "#1257c4", liveColor: "#0b1b2e" };
  var mq = window.matchMedia ? window.matchMedia("(prefers-color-scheme: light)") : null;
  function apply(light) {
    var root = document.documentElement;
    if (light) root.classList.add("is-light"); else root.classList.remove("is-light");
    var hero = document.querySelector(".hero"), how = document.getElementById("how");
    if (hero) hero.classList.toggle("is-light", light);
    if (how) how.classList.toggle("is-light", light);
    window.__GUARDIAN_HERO_TINT = light ? HERO_LIGHT : null;
    window.__GUARDIAN_WATCH_TINT = light ? WATCH_LIGHT : null;
  }
  function remount() {
    /* Replace each canvas so the old GL context and its loop are dropped,
     * reset the hero fade the scene reveals from, then re-run the scene
     * scripts so they read CONFIG -- and the new tint -- afresh. */
    var ids = ["hero-gl", "watch-gl"];
    for (var i = 0; i < ids.length; i++) {
      var old = document.getElementById(ids[i]);
      if (old && old.parentNode) old.parentNode.replaceChild(old.cloneNode(false), old);
    }
    var fade = document.getElementById("hero-fade");
    if (fade) fade.style.opacity = "1";
    var scripts = ["/hero-gradient.js", "/watch-scene.js"];
    for (var j = 0; j < scripts.length; j++) {
      var s = document.createElement("script");
      s.src = scripts[j] + "?skin=" + (window.__GUARDIAN_HERO_TINT ? "light" : "dark");
      s.setAttribute("data-guardian-scene", "");
      document.body.appendChild(s);
    }
  }
  var light = !!(mq && mq.matches);
  apply(light);
  document.addEventListener("DOMContentLoaded", function () { apply(!!(mq && mq.matches)); });
  if (mq && mq.addEventListener) {
    mq.addEventListener("change", function (e) { apply(e.matches); remount(); });
  }
})();

/* Submit the access request without leaving the page.
 *
 * This was for a long time the ONLY script on the site, and the principle that
 * made it so still holds even though there are now three: everything that can
 * be HTML and CSS is. The other two — `hero-gradient.js` and `reveal.js` — are
 * both decoration that removes itself when absent. The lattice falls back to
 * the flat ground the hero was designed on, and the reveal never hides anything
 * unless its own script runs to arm it. This file is still the only one that
 * changes what the page can DO.
 *
 * Deliberate choices:
 * - The POST goes to the RELATIVE path `/api/v1/public/lead`, which nginx
 *   proxies to the hub from this same origin. An absolute
 *   https://my.guarder.io/... URL would be a cross-origin request and the hub
 *   sends no CORS headers, so it would fail in the browser and only in the
 *   browser.
 * - Nothing the visitor typed is ever written back into the page. The
 *   confirmation is a fixed string, so a submitted value can never be reflected
 *   as markup, and there is nothing here for an injected payload to land in.
 * - Without JavaScript the form simply does not submit; the sign-up page it
 *   lives on (`/signup.html`) is still fully readable, and every link to it
 *   -- the footer's "Request access" and the home page's "Start three months
 *   free" CTA included -- is a plain anchor that needs no script to work.
 */
(function () {
  "use strict";

  var form = document.getElementById("lead-form");
  if (!form) return;
  var msg = document.getElementById("lead-msg");
  var button = document.getElementById("lead-submit");

  function say(text, kind) {
    msg.textContent = text;
    msg.className = "form-msg" + (kind ? " " + kind : "");
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();

    var data = new FormData(form);
    var name = String(data.get("name") || "").trim();
    var email = String(data.get("email") || "").trim();
    var siteName = String(data.get("site_name") || "").trim();
    var terms = form.elements.accept_terms;

    // Checked here only to save a round trip; the hub validates the same
    // things again and is the authority on both.
    if (!name) {
      say("Please tell us your name.", "bad");
      form.elements.name.focus();
      return;
    }
    if (!email || email.indexOf("@") < 1 || email.indexOf(".", email.indexOf("@")) < 0) {
      say("That email does not look right.", "bad");
      form.elements.email.focus();
      return;
    }
    if (!siteName) {
      say("Please give the site a name.", "bad");
      form.elements.site_name.focus();
      return;
    }
    if (terms && !terms.checked) {
      say("Please accept the Terms of Service.", "bad");
      terms.focus();
      return;
    }

    button.disabled = true;
    say("Sending…");

    fetch("/api/v1/public/lead", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: name,
        email: email,
        company: String(data.get("company") || "").trim(),
        site_name: siteName,
        network_size: String(data.get("network_size") || "").trim(),
        note: String(data.get("note") || "").trim(),
        website: String(data.get("website") || ""),
        accept_terms: true
      })
    })
      .then(function (response) {
        if (response.status === 429) {
          say("That is a lot of requests from here. Try again in a few minutes.", "bad");
          button.disabled = false;
          return;
        }
        if (!response.ok) {
          say("That did not go through. Email miro@guarder.io and we will pick it up.", "bad");
          button.disabled = false;
          return;
        }
        form.reset();
        // Leave the button disabled: the request is in, and a second identical
        // submission is noise for whoever reads these. Hide the fields too, so
        // the panel reads as a confirmation rather than an empty form waiting
        // to be filled in again.
        form.querySelectorAll(".field, .check, #lead-submit").forEach(function (el) { el.hidden = true; });
        say("Thanks — we'll review it and email you when your account is ready.", "ok");
      })
      .catch(function () {
        say("Could not reach us just now. Email miro@guarder.io instead.", "bad");
        button.disabled = false;
      });
  });
})();

/* The live panel.
 *
 * Same-origin by default: nginx proxies exactly /api/v1/public/feed on this
 * hostname through to the hub (deploy/nginx-site.conf.tmpl), so the fetch is
 * a relative path for the same reason the lead POST is. The one override is
 * for a local preview, where the page is served by `python3 -m http.server`
 * next to a hub on another port -- and it is gated on the hostname being
 * loopback, so it can never fire on guarder.io no matter what is committed.
 *
 * Nothing from the response is ever inserted as markup. Every value goes in
 * through textContent, and `sev` is matched against a fixed list before it is
 * allowed near a class name -- the hub composes these strings, but this page
 * does not have to trust that, and one day the panel may point somewhere else.
 *
 * Layout never moves: the panel's height is fixed in CSS, the row count is
 * fixed, and a refresh rewrites the text of the existing rows rather than
 * rebuilding the list.
 *
 * Polling discipline -- this is a public, unauthenticated endpoint, so it is
 * this script's job (not just the hub's) to make sure a visitor leaving the
 * tab open all day never turns into abuse:
 * - A single in-flight request at a time. `poll()` bails out immediately if
 *   the previous request has not resolved yet, so a slow or stalled response
 *   cannot pile up overlapping requests.
 * - Backing off on failure. Each consecutive failure doubles the wait, up to
 *   a ceiling, so a hub that is down or throttling does not get hammered at
 *   the base interval by every open tab; one success resets it.
 * - Backing off when hidden. `document.hidden` is checked before every
 *   request and on `visibilitychange`: a backgrounded tab stops polling
 *   entirely rather than continuing at the foreground cadence, and coming
 *   back to the tab polls once immediately rather than waiting out a stale
 *   timer.
 * - Backing off when idle. A *visible* tab with nobody at the keyboard (a
 *   sales page left open in another monitor, a pinned tab) is the case the
 *   hidden check above cannot catch: `document.hidden` is only ever true
 *   when the tab itself is backgrounded. `lastActivity` tracks the most
 *   recent pointer/keyboard/scroll/touch event; once `IDLE_AFTER_MS` has
 *   passed with none, the base interval slows from `REFRESH_MS` to
 *   `IDLE_REFRESH_MS` (still moving, just not at 900/hour). Any activity --
 *   including the tab regaining focus -- resets the clock and, if a slow
 *   idle tick is currently pending, replaces it with an immediate poll so
 *   a visitor coming back doesn't stare at minute-old data for up to
 *   `IDLE_REFRESH_MS` before it catches up.
 */
(function () {
  "use strict";

  var list = document.getElementById("feed-rows");
  var fallback = document.getElementById("feed-fallback");
  var stamp = document.getElementById("feed-stamp");
  var panel = list && list.closest ? list.closest(".panel") : null;
  if (!list || !fallback || !stamp || !panel) return;

  var ROWS = 8;
  // The 3-5s band the panel is meant to feel "live" in; the hub's cache and
  // rate limits (public_feed.py FEED_PER_IP/_TTL_S) are sized to match this
  // exact number, so it must not be turned down without re-tuning those too.
  var REFRESH_MS = 4000;
  var MAX_BACKOFF_MS = 60000;
  var FETCH_TIMEOUT_MS = 8000;
  // A visible-but-idle tab (see the polling-discipline note above) slows to
  // this cadence after IDLE_AFTER_MS with no user interaction. 5 minutes /
  // 30s is a >7x reduction in request volume for a tab nobody is looking at
  // while it's still open, without ever going fully quiet the way a hidden
  // tab does.
  var IDLE_AFTER_MS = 5 * 60 * 1000;
  var IDLE_REFRESH_MS = 30000;
  var SEVS = { crit: "crit", warn: "warn", ok: "ok", info: "" };

  var LOCAL = /^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname);
  // Local preview only: ?feed=http://127.0.0.1:8803 points the panel at a hub
  // running beside a `python3 -m http.server`. Ignored everywhere else.
  var origin = "";
  if (LOCAL) {
    var q = new URLSearchParams(location.search).get("feed");
    if (q && /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(q)) origin = q;
  }
  var URL_FEED = origin + "/api/v1/public/feed";

  /* The hero verdict: derived from the same rows, never typed in. Any critical
   * row is the headline; else any warning; else nothing urgent. The stamp is
   * the feed's own last_scan. */
  function verdictFrom(payload) {
    var box = document.getElementById("verdict");
    if (!box) return;
    var rows = (payload && payload.rows) || [];
    var crit = 0, warn = 0;
    for (var i = 0; i < rows.length; i++) { if (rows[i].sev === "crit") crit++; else if (rows[i].sev === "warn") warn++; }
    var tone = crit ? "crit" : warn ? "warn" : "ok";
    var head = crit ? (crit === 1 ? "One thing needs a look now" : crit + " things need a look now")
             : warn ? (warn === 1 ? "One thing is worth a look this week" : warn + " things are worth a look this week")
             : "Nothing urgent";
    box.setAttribute("data-tone", tone);
    var hEl = document.getElementById("verdict-h"); if (hEl) hEl.textContent = head;
    var st = document.getElementById("verdict-stamp");
    if (st && payload && payload.last_scan) {
      var t = Date.parse(payload.last_scan);
      if (!isNaN(t)) {
        var s = Math.max(0, Math.round((Date.now() - t) / 1000));
        st.textContent = "checked " + (s < 60 ? "just now" : s < 3600 ? Math.round(s / 60) + " min ago" : Math.round(s / 3600) + " h ago");
      }
    }
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  /* Eight rows built once, then only their text changes. Keeping the nodes
   * means a refresh cannot reflow the panel, and means nothing is ever
   * re-parsed as HTML. */
  var rows = [];
  for (var i = 0; i < ROWS; i++) {
    var li = el("li", "feed-row skel");
    li.setAttribute("aria-hidden", "true");
    var badge = el("span", "badge");
    badge.appendChild(el("span", "bar"));
    var what = el("span", "feed-what");
    var title = el("b", null, "");
    var target = el("span", null, "");
    what.appendChild(title);
    what.appendChild(target);
    var detail = el("span", "feed-detail");
    detail.appendChild(el("span", "bar short"));
    var when = el("span", "feed-when", "");
    li.appendChild(badge);
    li.appendChild(what);
    li.appendChild(detail);
    li.appendChild(when);
    list.appendChild(li);
    rows.push({ li: li, badge: badge, title: title, target: target, detail: detail, when: when });
  }

  // Placeholders take over from the fallback line straight away: this script
  // is the last thing in the body, so the swap happens before first paint and
  // a visitor never sees one replaced by the other. Without JavaScript the
  // fallback simply stays, which is the point of writing it as prose.
  fallback.hidden = true;
  list.hidden = false;

  function ago(iso) {
    var then = Date.parse(iso);
    if (isNaN(then)) return "";
    var mins = Math.round((Date.now() - then) / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return mins + " min ago";
    var hours = Math.round(mins / 60);
    if (hours < 24) return hours + "h ago";
    var days = Math.round(hours / 24);
    return days + "d ago";
  }

  function degrade() {
    // Unreachable, throttled, or a hub with nothing to show: the same static
    // explanation in all three cases. An empty box explains nothing and an
    // error message is somebody else's problem on a sales page.
    list.hidden = true;
    fallback.hidden = false;
    panel.classList.add("offline");
    stamp.textContent = "feed unavailable";
  }

  function render(payload) {
    var data = (payload && payload.rows) || [];
    if (!data.length) return degrade();

    for (var i = 0; i < ROWS; i++) {
      var r = rows[i];
      var d = data[i];
      if (!d) {
        // Fewer rows than the panel holds: the spare keeps its height and
        // stays out of the accessibility tree rather than collapsing.
        r.li.className = "feed-row skel";
        r.li.setAttribute("aria-hidden", "true");
        r.badge.textContent = "";
        r.badge.className = "badge";
        r.badge.appendChild(el("span", "bar"));
        r.title.textContent = "";
        r.target.textContent = "";
        r.detail.textContent = "";
        r.detail.appendChild(el("span", "bar short"));
        r.when.textContent = "";
        continue;
      }
      var sev = Object.prototype.hasOwnProperty.call(SEVS, d.sev) ? d.sev : "info";
      r.li.className = "feed-row";
      r.li.removeAttribute("aria-hidden");
      r.badge.className = "badge " + SEVS[sev];
      r.badge.textContent = sev.toUpperCase();
      r.title.textContent = String(d.title || "");
      r.target.textContent = String(d.target || "");
      r.detail.className = "feed-detail";
      r.detail.textContent = String(d.detail || "");
      r.when.textContent = ago(d.ts);
    }

    list.hidden = false;
    fallback.hidden = true;
    panel.classList.remove("offline");
    stamp.textContent = payload.last_scan ? "last scan " + ago(payload.last_scan) : "";
  }

  var inFlight = false;
  var failures = 0;
  var timer = null;
  var idleTimerPending = false;
  var lastActivity = Date.now();

  function schedule(delay) {
    if (timer) window.clearTimeout(timer);
    idleTimerPending = delay >= IDLE_REFRESH_MS && (Date.now() - lastActivity) >= IDLE_AFTER_MS;
    timer = window.setTimeout(poll, delay);
  }

  function markActive() {
    lastActivity = Date.now();
    // A slow idle tick is currently outstanding: don't make a visitor who
    // just came back wait it out, catch up now instead. A tick scheduled at
    // the normal (or backed-off-on-failure) cadence is left alone -- it's
    // already due soon enough that replacing it would just add requests.
    if (idleTimerPending && !inFlight && !document.hidden) {
      schedule(0);
    }
  }
  ["mousemove", "mousedown", "keydown", "touchstart", "scroll", "wheel"].forEach(function (evt) {
    document.addEventListener(evt, markActive, { passive: true });
  });

  function poll() {
    timer = null;
    idleTimerPending = false;

    // Backgrounded tab: don't request at all. visibilitychange below fires a
    // poll immediately when the tab comes back, so nothing is missed for
    // long -- this just stops a pile of hidden tabs from polling forever.
    if (document.hidden) return;

    // One request at a time. If the last one is still outstanding, skip this
    // tick rather than queueing a second -- the next scheduled tick (or the
    // visibilitychange handler) will try again.
    if (inFlight) {
      schedule(REFRESH_MS);
      return;
    }

    inFlight = true;
    // A deadline on every poll. Without one, a request the hub was slow to
    // answer (it used to stall for ~30 s every ten minutes re-seeding the
    // demo) held `inFlight` for as long as it took, every later tick was
    // skipped, and a visitor stared at grey placeholders with nothing
    // happening -- neither the rows nor the "feed unavailable" line. Aborting
    // turns a stall into an ordinary failure: the backoff below retries it.
    var ctl = typeof AbortController === "function" ? new AbortController() : null;
    var deadline = ctl ? window.setTimeout(function () { ctl.abort(); }, FETCH_TIMEOUT_MS) : null;
    fetch(URL_FEED, { headers: { Accept: "application/json" }, signal: ctl ? ctl.signal : undefined })
      .then(function (response) {
        if (!response.ok) throw new Error("feed " + response.status);
        return response.json();
      })
      .then(function (payload) {
        failures = 0;
        render(payload);
        verdictFrom(payload);
      })
      .catch(function () {
        failures += 1;
        degrade();
      })
      .then(function () {
        if (deadline) window.clearTimeout(deadline);
        inFlight = false;
        var idle = (Date.now() - lastActivity) >= IDLE_AFTER_MS;
        var base = idle ? IDLE_REFRESH_MS : REFRESH_MS;
        // Exponential backoff on repeated failure (base interval on success),
        // capped so a long outage still checks back periodically rather than
        // hammering a struggling hub or giving up entirely.
        var delay = failures > 0
          ? Math.min(base * Math.pow(2, failures), MAX_BACKOFF_MS)
          : base;
        schedule(delay);
      });
  }

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) {
      markActive();
      poll();
    }
  });

  poll();

  // Footer year: progressive touch-up only. The generated HTML already has
  // the correct year as plain text (see site/i18n/build_locales.py), so a
  // reader with JavaScript off, or a crawler, still sees a real year rather
  // than a blank span waiting on a script.
  var yearEl = document.getElementById("copyright-year");
  if (yearEl) yearEl.textContent = String(new Date().getFullYear());
})();

/* ──────────────────────────────────────────────────────────────────────
 * The capability switcher and the three charts.
 *
 * Hand-rolled SVG, deliberately: the CSP on this origin is
 * default-src 'self' and hub/tests/test_site_csp.py fails the build if a
 * CDN or an inline style ever appears here, so a charting library is not
 * an option. Each chart is a few dozen lines of path arithmetic instead.
 *
 * On honesty: the live strip above shows real notices from a watched
 * network. These three charts do NOT -- the public feed carries eight
 * rows and no history at all, so a time series cannot be drawn from it.
 * Rather than accumulate a fake one, each chart is drawn from fixed
 * illustrative numbers and says "example" on its face. Showing invented
 * history as a reading is the one thing a monitoring product must never
 * do, and a marketing page is not an exception.
 * ────────────────────────────────────────────────────────────────────── */
(function () {
  "use strict";

  var CAPS = [
    ["Devices on the Wi-Fi", "Who is here, and who is new",
     "Every address on the network, named where it can be, with the ones never seen before pulled to the top. A device that appears at three in the morning is the one you want to know about.",
     [["Devices", "26"], ["Unverified", "4"], ["New today", "1"]]],
    ["Servers", "Disk, memory, patches, intruders",
     "Load, disk and memory on every machine, the security updates waiting, and the addresses failing to log in. When an update is routine, Vault installs it and tells you afterwards.",
     [["Hosts", "3"], ["CVEs with fixes", "0"], ["Failed logins, 24h", "0"]]],
    ["Websites and certificates", "Before the browser warning, not after",
     "Response, status and the certificate clock for every site you are responsible for, checked from outside your own network, because that is where your visitors are.",
     [["Sites", "4"], ["Uptime, 7 days", "99.84%"], ["Soonest expiry", "59d"]]],
    ["DNS and traffic", "What the network is asking for",
     "Every lookup the network makes, with the ad, tracking and malware hosts refused before they resolve. Useful the first time a device starts talking to somewhere it should not.",
     [["Queries, 24h", "18,402"], ["Blocked", "1,247"], ["Block rate", "6.8%"]]],
    ["Exposure", "What the internet can reach",
     "The ports actually answering from outside, checked against the ones you meant to publish. A management port on the public address is a conversation worth having today.",
     [["Open ports", "3"], ["Unexpected", "0"], ["Last sweep", "6 min"]]],
    ["Repairs", "The part other tools leave to you",
     "A fixed catalogue of repairs, each with a cooldown so nothing loops. Anything disruptive stops and asks first, in writing, with the exact steps it intends to run.",
     [["Handled, 7 days", "38"], ["Asked first", "6"], ["Failed", "0"]]]
  ];

  var list = document.getElementById("cap-list");
  var view = document.getElementById("cap-view");

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  function show(i) {
    var c = CAPS[i], k;
    var btns = list.querySelectorAll("button");
    for (k = 0; k < btns.length; k++) btns[k].setAttribute("aria-selected", String(k === i));
    view.textContent = "";
    view.appendChild(el("h3", null, c[0]));
    view.appendChild(el("p", "cap-body", c[2]));
    var kpis = el("div", "cap-kpis");
    for (k = 0; k < c[3].length; k++) {
      var box = el("div");
      box.appendChild(el("p", "cap-k", c[3][k][0]));
      box.appendChild(el("p", "cap-v", c[3][k][1]));
      kpis.appendChild(box);
    }
    view.appendChild(kpis);
  }

  if (list && view) {
    CAPS.forEach(function (c, i) {
      var li = document.createElement("li");
      var b = document.createElement("button");
      b.type = "button";
      b.setAttribute("role", "tab");
      b.setAttribute("aria-selected", String(i === 0));
      b.appendChild(document.createTextNode(c[0]));
      b.appendChild(el("small", null, c[1]));
      b.addEventListener("click", function () { show(i); });
      li.appendChild(b);
      list.appendChild(li);
    });
    show(0);
  }

  /* --- charts ------------------------------------------------------- */
  var SVG = "http://www.w3.org/2000/svg";
  function svg(w, h, label) {
    var s = document.createElementNS(SVG, "svg");
    s.setAttribute("viewBox", "0 0 " + w + " " + h);
    s.setAttribute("role", "img");
    s.setAttribute("aria-label", label);
    return s;
  }
  function node(name, attrs) {
    var n = document.createElementNS(SVG, name);
    for (var k in attrs) if (Object.prototype.hasOwnProperty.call(attrs, k)) n.setAttribute(k, attrs[k]);
    return n;
  }
  function text(x, y, str, anchor) {
    var t = node("text", { x: x, y: y, "class": "ax" });
    if (anchor) t.setAttribute("text-anchor", anchor);
    t.textContent = str;
    return t;
  }
  function mount(id, s) {
    var host = document.getElementById(id);
    if (host) { host.textContent = ""; host.appendChild(s); }
  }

  // 1 — minutes from finding to repair, as a dot plot with the median marked.
  (function () {
    var d = [2, 3, 1, 4, 2, 6, 3, 2, 9, 4, 3, 2, 5, 3, 2, 4, 12, 3, 2, 6], max = 14;
    var s = svg(320, 150, "Minutes from a finding to the repair landing");
    for (var i = 0; i <= 3; i++) {
      var y = 12 + i * 33;
      s.appendChild(node("line", { x1: 34, x2: 312, y1: y, y2: y, stroke: "#1e2b42" }));
      s.appendChild(text(0, y + 3, String(Math.round(max - i * (max / 3))) + "m"));
    }
    d.forEach(function (v, i) {
      s.appendChild(node("circle", {
        cx: 42 + i * (266 / (d.length - 1)), cy: 111 - (v / max) * 99, r: 3.1,
        fill: v > 8 ? "#f2a63c" : "#6ba5ff"
      }));
    });
    var my = 111 - (3 / max) * 99;
    s.appendChild(node("line", { x1: 34, x2: 312, y1: my, y2: my, stroke: "#e9eff8", "stroke-dasharray": "3 3", opacity: ".7" }));
    s.appendChild(text(34, 138, "median 3 min · slowest 12"));
    mount("chart-1", s);
  })();

  // 2 — handled versus escalated, stacked by day.
  (function () {
    var d = [[7, 1], [5, 0], [9, 2], [6, 1], [8, 1], [4, 0], [6, 1]];
    var L = ["M", "T", "W", "T", "F", "S", "S"], max = 11;
    var s = svg(320, 150, "Findings Vault closed itself against those it escalated, by day");
    d.forEach(function (v, i) {
      var x = 40 + i * 38, hh = (v[0] / max) * 98, he = (v[1] / max) * 98;
      s.appendChild(node("rect", { x: x, y: 114 - hh - he, width: 22, height: he, fill: "#f2a63c" }));
      s.appendChild(node("rect", { x: x, y: 114 - hh, width: 22, height: hh, fill: "#46d18b" }));
      s.appendChild(text(x + 11, 129, L[i], "middle"));
    });
    s.appendChild(text(0, 22, "11"));
    s.appendChild(text(0, 117, "0"));
    s.appendChild(text(40, 145, "green closed by Vault · amber asked you"));
    mount("chart-2", s);
  })();

  // 3 — severity mix over a fortnight, stacked areas.
  (function () {
    var n = 14, cols = ["#46d18b", "#8da0bc", "#f2a63c", "#ff6b5e"], ser = [[], [], [], []], i;
    for (i = 0; i < n; i++) {
      ser[0].push(4 + Math.round(Math.sin(i / 2) * 2 + 2));
      ser[1].push(3 + Math.round(Math.cos(i / 3) * 2 + 2));
      ser[2].push(1 + (i % 4 === 0 ? 2 : 0));
      ser[3].push(i === 9 ? 1 : 0);
    }
    var s = svg(320, 150, "Findings by severity over fourteen days"), base = [];
    for (i = 0; i < n; i++) base.push(0);
    ser.forEach(function (row, si) {
      var up = "", dn = "";
      for (var j = 0; j < n; j++) {
        var x = 38 + j * (272 / (n - 1));
        var y0 = 114 - base[j] * 5.2, y1 = 114 - (base[j] + row[j]) * 5.2;
        up += (j ? " L" : "M") + x + " " + y1.toFixed(1);
        dn = " L" + x + " " + y0.toFixed(1) + dn;
        base[j] += row[j];
      }
      s.appendChild(node("path", { d: up + dn + " Z", fill: cols[si], opacity: ".86" }));
    });
    s.appendChild(text(0, 22, "18"));
    s.appendChild(text(0, 117, "0"));
    s.appendChild(text(38, 134, "14 days"));
    s.appendChild(text(38, 145, "ok · info · warning · critical"));
    mount("chart-3", s);
  })();
})();
