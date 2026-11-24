/* ATLAS Academy, agency.storefront.test. The site's only script.
 *
 * The mobile menu, the product shelf, the learning-path
 * filter, the finder (its data is window.FINDER, written by site.py from the
 * catalog), the comparison picker, the approval-gate demonstration, edition
 * choice, the kit view, cover thumbs, the sticky buy bar and the in-page nav.
 *
 * Every scene is complete HTML before this runs; the script adds controls and
 * motion only. Under prefers-reduced-motion nothing animates and nothing
 * advances on a timer. It makes no request and stores nothing:
 * tests/test_academy_site.py checks. */
(function () {
  var root = document.documentElement;
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)');
  // Dark only (Miro, 2026-10-11): no theme switch.
  root.setAttribute('data-theme', 'dark');
  root.classList.add('js');
  function $(sel, el) { return (el || document).querySelector(sel); }
  function $$(sel, el) { return Array.prototype.slice.call((el || document).querySelectorAll(sel)); }
  function still() { return reduce.matches; }
  function later(fn, ms) { return window.setTimeout(fn, still() ? 0 : ms); }

  document.addEventListener('DOMContentLoaded', function () {
    // Header shadow + mobile menu
    var top = $('.top');
    var onScroll = function () { if (top) top.classList.toggle('scrolled', window.scrollY > 8); };
    window.addEventListener('scroll', onScroll, { passive: true }); onScroll();
    var mb = $('.menu-btn'), nav = $('.nav');
    if (mb && nav) {
      var close = function () { nav.classList.remove('open'); mb.setAttribute('aria-expanded', 'false'); mb.setAttribute('aria-label', 'Open menu'); };
      mb.addEventListener('click', function () {
        var open = nav.classList.toggle('open');
        mb.setAttribute('aria-expanded', open ? 'true' : 'false');
        mb.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
        if (open) { var first = $('a', nav); if (first) first.focus(); }
      });
      nav.addEventListener('keydown', function (e) { if (e.key === 'Escape') { close(); mb.focus(); } });
      $$('a', nav).forEach(function (a) { a.addEventListener('click', close); });
    }

    // ---------------------------------------------------- catalog paths
    var chips = $$('[data-filter]');
    var cards = $$('.card[data-paths]');
    // Search and filters (plan F02/F37): the catalog's own words, a small
    // dictionary of synonyms, title and tool matches ranked first. The state
    // is mirrored into the address (?q=&path=&level=&format=) so a filtered
    // view can be shared; nothing is stored.
    var tools = $('[data-cat-tools]');
    var grid = cards.length ? cards[0].parentNode : null;
    var state = { path: 'all', q: '', level: '', format: '' };
    var SYN = {
      workflow: ['automation', 'automate', 'n8n'], workflows: ['automation', 'automate', 'n8n'],
      automation: ['automate', 'workflow', 'n8n'], automate: ['automation', 'workflow', 'n8n'],
      agent: ['operator', 'agents'], agents: ['operator', 'agent'], notes: ['obsidian', 'vault'],
      scam: ['scams', 'fraud'], scams: ['scam', 'fraud'], family: ['home', 'senior'],
      seniors: ['senior', '60+'], ebook: ['pdf', 'book'], book: ['pdf', 'ebook'],
      video: ['video lessons'], videos: ['video lessons'], sell: ['selling', 'store', 'product'],
      money: ['sell', 'selling', 'offer']
    };
    var score = function (card, words) {
      if (!words.length) return 1;
      var title = card.getAttribute('data-title'), text = card.getAttribute('data-search');
      var total = 0;
      for (var i = 0; i < words.length; i++) {
        var opts = [words[i]].concat(SYN[words[i]] || []), best = 0;
        opts.forEach(function (w) {
          if (title.indexOf(w) > -1) best = Math.max(best, 3);
          else if (text.indexOf(w) > -1) best = Math.max(best, 1);
        });
        if (!best) return 0;
        total += best;
      }
      return total;
    };
    var syncUrl = function () {
      var params = new URLSearchParams(window.location.search);
      [['q', state.q], ['path', state.path === 'all' ? '' : state.path], ['level', state.level], ['format', state.format]]
        .forEach(function (kv) { if (kv[1]) params.set(kv[0], kv[1]); else params.delete(kv[0]); });
      var qs = params.toString();
      window.history.replaceState(null, '', window.location.pathname + (qs ? '?' + qs : '') + window.location.hash);
    };
    var apply = function (fromUser) {
      chips.forEach(function (x) { x.setAttribute('aria-pressed', x.getAttribute('data-filter') === state.path ? 'true' : 'false'); });
      var words = state.q.toLowerCase().split(/\s+/).filter(Boolean);
      var hits = [];
      cards.forEach(function (card, i) {
        var s = score(card, words);
        var ok = s > 0 &&
          (state.path === 'all' || card.getAttribute('data-paths').split(' ').indexOf(state.path) > -1) &&
          (!state.level || card.getAttribute('data-fits').split(' ').indexOf(state.level) > -1) &&
          (!state.format || card.getAttribute('data-formats').split(' ').indexOf(state.format) > -1);
        card.hidden = !ok;
        if (ok) hits.push({ card: card, s: s, i: i });
      });
      // Best matches first; with no query the catalog keeps its own order.
      hits.sort(function (a, b) { return words.length ? (b.s - a.s) || (a.i - b.i) : a.i - b.i; });
      if (grid) {
        hits.forEach(function (h) { grid.appendChild(h.card); });
        cards.forEach(function (c) { if (c.hidden) grid.appendChild(c); });
      }
      if (fromUser && grid && !still()) {
        var k = 0;
        hits.forEach(function (h) { h.card.style.setProperty('--k', String(Math.min(k, 8))); k += 1; });
        grid.classList.remove('is-swapping'); void grid.offsetWidth; grid.classList.add('is-swapping');
      }
      var n = hits.length;
      var live = document.getElementById('filter-status');
      var active = state.q || state.path !== 'all' || state.level || state.format;
      if (live) live.textContent = active ? (n === 1 ? 'One title' : n + ' titles') + ' match' + (n === 1 ? 'es' : '') + ' your filters.' : '';
      var empty = $('[data-cat-empty]');
      if (empty) empty.hidden = n > 0;
      if (fromUser) syncUrl();
    };
    var setPath = function (f) { state.path = f || 'all'; apply(true); };
    chips.forEach(function (c) { c.addEventListener('click', function () { setPath(c.getAttribute('data-filter')); }); });
    $$('[data-show-path]').forEach(function (a) {
      a.addEventListener('click', function () { setPath(a.getAttribute('data-show-path')); });
    });
    if (tools && cards.length) {
      tools.hidden = false;
      var qIn = $('input[name=q]', tools), lvl = $('select[name=level]', tools) || document.createElement('select'),
        fmt = $('select[name=format]', tools) || document.createElement('select');
      var read = function () { state.q = qIn.value.trim(); state.level = lvl.value; state.format = fmt.value; apply(true); };
      qIn.addEventListener('input', read);
      lvl.addEventListener('change', read);
      fmt.addEventListener('change', read);
      tools.addEventListener('submit', function (e) { e.preventDefault(); read(); });
      $$('[data-cat-clear]').forEach(function (b) {
        b.addEventListener('click', function () {
          qIn.value = ''; lvl.value = ''; fmt.value = '';
          state = { path: 'all', q: '', level: '', format: '' };
          apply(true); qIn.focus();
        });
      });
      // A shared address restores its view.
      var params = new URLSearchParams(window.location.search);
      var known = function (sel, v) { return $$('option', sel).some(function (o) { return o.value === v; }) ? v : ''; };
      qIn.value = (params.get('q') || '').slice(0, 80);
      lvl.value = known(lvl, params.get('level') || '');
      fmt.value = known(fmt, params.get('format') || '');
      var p0 = params.get('path') || 'all';
      state = { q: qIn.value.trim(), level: lvl.value, format: fmt.value,
        path: chips.some(function (c) { return c.getAttribute('data-filter') === p0; }) ? p0 : 'all' };
      if (state.q || state.level || state.format || state.path !== 'all') apply(false);
    }

    // ---------------------------------------------------------------- finder
    // Every answer combination was worked out when the site was built
    // (site.finder_routes): eligibility first, then the editorial order. The
    // script only looks the answer up and writes it out.
    var data = window.FINDER || { products: [], lessons: {}, routes: {}, paths: {} };
    var form = $('[data-finder]');
    var out = document.getElementById('finder-result');
    if (form && out) {
      var emptyHtml = out.innerHTML;
      var bySlug = {};
      data.products.forEach(function (r) { bySlug[r.slug] = r; });
      var picked = function (name) { var i = form.querySelector('input[name=' + name + ']:checked'); return i ? i.value : ''; };
      var card = function (r, cls, head) {
        return '<div class="rec ' + cls + '"><img src="' + r.cover + '" alt="" width="88" height="88" loading="lazy">' +
          '<div>' + (head ? '<p class="rec-k">' + head + '</p>' : '') +
          '<h3><a href="' + r.href + '">' + r.title + '</a></h3>' +
          '<p class="rec-level"><span class="level">' + r.level + '</span></p>' +
          '<p class="why"><b>Why it fits:</b> ' + r.why + '</p>' +
          '<p class="needs"><b>What it asks of you:</b> ' + r.needs + '</p>' +
          '<p class="row"><span class="price">' + r.price + '</span><a class="more" href="' + r.href + '">View details</a></p></div></div>';
      };
      var draw = function () {
        var goal = picked('goal'), level = picked('level'), setup = picked('setup');
        if (!goal && !level && !setup) { out.innerHTML = emptyHtml; return; }
        var route = data.routes[goal + '|' + level + '|' + setup] || { best: '', alts: [], out: [] };
        var html = '';
        var lesson = goal && data.lessons[goal];
        if (route.best) {
          html += card(bySlug[route.best], 'rec-best', 'Your best starting point');
          if (route.alts.length) {
            html += '<p class="rec-sub">Also fits your answers</p>';
            html += route.alts.map(function (s) { return card(bySlug[s], 'rec-alt', ''); }).join('');
          }
        } else {
          html += '<div class="rec-free"><p class="rec-k">No paid title fits these answers</p>' +
            '<p>Start free instead: the lessons play in your browser, with nothing to install.</p>' +
            '<p><a class="btn btn-ink btn-sm" href="/videos/">Watch the free lessons</a></p></div>';
        }
        if (lesson) {
          html += '<p class="rec-lesson">Free lesson for this path: <a href="' + lesson.href + '" rel="noopener">' + lesson.title + '</a> (opens YouTube)</p>';
        }
        if (route.out.length) {
          html += '<div class="rec-out"><p class="rec-sub">Set aside for now, because of what it needs</p><ul>' +
            route.out.map(function (s) { var r = bySlug[s]; return '<li><a href="' + r.href + '">' + r.title + '</a>: ' + r.needs + '</li>'; }).join('') +
            '</ul></div>';
        }
        out.innerHTML = html;
      };
      form.addEventListener('change', draw);
      form.addEventListener('reset', function () { window.setTimeout(draw, 0); });
    }

    // ------------------------------------------------------------- compare
    var cpicks = $$('[data-compare-pick]');
    if (cpicks.length) {
      var group = cpicks[0].parentNode; group.hidden = false;
      var rows = $$('tr[data-compare]');
      // The chosen titles go into the address as ?compare=slug,slug (product
      // ids only), so a narrowed table can be shared.
      var narrow = function (share) {
        var chosen = cpicks.filter(function (x) { return x.getAttribute('aria-pressed') === 'true'; })
          .map(function (x) { return x.getAttribute('data-compare-pick'); });
        rows.forEach(function (r) { r.hidden = chosen.length > 0 && chosen.indexOf(r.getAttribute('data-compare')) < 0; });
        if (!share) return;
        var params = new URLSearchParams(window.location.search);
        if (chosen.length) params.set('compare', chosen.join(',')); else params.delete('compare');
        var qs = params.toString();
        window.history.replaceState(null, '', window.location.pathname + (qs ? '?' + qs : '') + window.location.hash);
      };
      cpicks.forEach(function (b) {
        b.addEventListener('click', function () {
          b.setAttribute('aria-pressed', b.getAttribute('aria-pressed') === 'true' ? 'false' : 'true');
          narrow(true);
        });
      });
      var wanted = (new URLSearchParams(window.location.search).get('compare') || '').split(',');
      cpicks.forEach(function (b) {
        if (wanted.indexOf(b.getAttribute('data-compare-pick')) > -1) b.setAttribute('aria-pressed', 'true');
      });
      narrow(false);
    }

    // -------------------------------------------------------- approval gate
    // Request, then draft, then a stop at Review that only the visitor's own
    // click moves past. Nothing is sent: this is a page-only simulation.
    var gate = $('[data-gate]');
    if (gate) {
      var steps = $$('.gate-step', gate);
      var start = $('[data-gate-start]'), reset = $('[data-gate-reset]');
      var approve = $('[data-gate-approve]', gate), edit = $('[data-gate-edit]', gate);
      var result = $('[data-gate-result]', gate);
      var resultHtml = result.innerHTML;
      var timers = [];
      var show = function (n) {
        steps.forEach(function (s, i) {
          s.classList.toggle('is-on', i < n);
          s.classList.toggle('is-now', i === n - 1);
        });
      };
      var clear = function () { timers.forEach(window.clearTimeout); timers = []; };
      var idle = function () {
        clear(); gate.classList.add('is-armed'); show(0);
        approve.disabled = true; edit.disabled = true; result.innerHTML = resultHtml;
        start.hidden = false; reset.hidden = true;
      };
      idle();
      start.addEventListener('click', function () {
        clear(); start.hidden = true; reset.hidden = false;
        approve.disabled = true; edit.disabled = true; result.innerHTML = resultHtml;
        show(1);
        timers.push(later(function () { show(2); }, 550));
        timers.push(later(function () {
          show(3); approve.disabled = false; edit.disabled = false; approve.focus();
        }, 1100));
      });
      approve.addEventListener('click', function () {
        approve.disabled = true; edit.disabled = true;
        result.innerHTML = '<p><b>Approved by you.</b> In the real workflow, this is the moment the reply may go out: you send it, or the approved step runs. This page sends nothing.</p>';
        show(4); reset.focus();
      });
      edit.addEventListener('click', function () {
        approve.disabled = true; edit.disabled = true;
        result.innerHTML = '<p><b>Sent back to draft.</b> Nothing went out. The AI prepares a new draft, and the workflow stops at Review again.</p>';
        show(4); reset.focus();
      });
      reset.addEventListener('click', function () { idle(); start.focus(); });
    }

    // ------------------------------------------------- editions and the kit
    var radios = $$('[data-edition]');
    var kitOffers = $$('.kit-offer');
    var sumsWrap = $('.summaries');
    // Nothing is chosen for the visitor: until an edition is picked, no Buy
    // shows and every edition's files stay listed under its own name.
    var prompt = $('[data-ed-prompt]');
    var syncEdition = function () {
      var r = radios.filter(function (x) { return x.checked; })[0];
      var id = r ? r.value : '';
      if (prompt) prompt.hidden = !!r;
      $$('.summary', sumsWrap || document).forEach(function (s) { s.hidden = s.getAttribute('data-offer') !== id; });
      kitOffers.forEach(function (k) { k.hidden = !!r && k.getAttribute('data-offer') !== id; });
    };
    if (radios.length) {
      if (sumsWrap) sumsWrap.classList.add('is-live');
      radios.forEach(function (r) { r.addEventListener('change', syncEdition); });
      // A link to #<edition id> arrives with that edition chosen.
      var fromHash = window.location.hash.slice(1);
      radios.forEach(function (r) { if (fromHash && r.value === fromHash) r.checked = true; });
      syncEdition();
    }
    // The kit: the cover unfolds into its files once, when it first comes
    // into view, then stays a plain readable list. The button folds it back
    // and replays it. Under reduced motion it is simply open.
    var kit = $('[data-kit]'), kitBtn = $('[data-kit-toggle]');
    if (kit && kitBtn) {
      var setKit = function (folded) {
        kit.classList.toggle('is-folded', folded);
        kitBtn.textContent = folded ? 'Open the kit' : 'Fold it back';
        kitBtn.setAttribute('aria-expanded', folded ? 'false' : 'true');
      };
      kitBtn.hidden = false;
      kitBtn.addEventListener('click', function () { setKit(!kit.classList.contains('is-folded')); });
      if (!still() && 'IntersectionObserver' in window) {
        setKit(true);
        var kio = new IntersectionObserver(function (es) {
          es.forEach(function (e) { if (e.isIntersecting) { setKit(false); kio.disconnect(); } });
        }, { threshold: 0.35 });
        kio.observe(kit);
      } else { setKit(false); }
    }

    // Cover thumbs
    $$('.thumbs button').forEach(function (t) {
      t.addEventListener('click', function () {
        var main = $('.p-cover .frame img');
        main.src = t.getAttribute('data-src'); main.alt = t.getAttribute('data-alt');
        $$('.thumbs button').forEach(function (x) { x.setAttribute('aria-pressed', x === t ? 'true' : 'false'); });
      });
    });

    // Free videos: each lesson is a plain link that opens the video on
    // YouTube in a new tab (Miro, 2026-11-17); nothing is embedded.

    // Sticky buy bar: only once the buy box has scrolled away above.
    var sb = $('.sticky-buy'), bb = $('.buybox');
    if (sb && bb && 'IntersectionObserver' in window) {
      new IntersectionObserver(function (es) {
        es.forEach(function (e) {
          var on = !e.isIntersecting && e.boundingClientRect.top < 0;
          sb.classList.toggle('show', on);
          root.classList.toggle('has-sticky', on);
        });
      }).observe(bb);
    }

    // In-page nav highlight
    var links = $$('.p-nav a');
    if (links.length && 'IntersectionObserver' in window) {
      var map = {};
      links.forEach(function (a) { map[a.getAttribute('href').slice(1)] = a; });
      var nio = new IntersectionObserver(function (es) {
        es.forEach(function (e) {
          if (e.isIntersecting) { links.forEach(function (a) { a.classList.remove('active'); }); var a = map[e.target.id]; if (a) a.classList.add('active'); }
        });
      }, { rootMargin: '-40% 0px -55% 0px' });
      Object.keys(map).forEach(function (id) { var el = document.getElementById(id); if (el) nio.observe(el); });
    }
  });
})();

/* Motion layer and the join / sign-in page (2026-11-10).
 * Scroll reveal, pointer light and tilt on cards, a slow parallax on the
 * page glow, the join stepper and the sign-in field checks. Transform and
 * opacity only. Under prefers-reduced-motion nothing moves: content fades
 * in at most. Nothing here sends anything or stores anything: the sign-in
 * form posts natively and the Buy links are the Stripe links in the HTML. */
(function () {
  var root = document.documentElement;
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)');
  function $(sel, el) { return (el || document).querySelector(sel); }
  function $$(sel, el) { return Array.prototype.slice.call((el || document).querySelectorAll(sel)); }
  function still() { return reduce.matches; }

  document.addEventListener('DOMContentLoaded', function () {
    root.classList.add('fx');

    // Scroll reveal: each block eases in once, as it comes into view.
    var targets = $$('.hero-copy, .hero-media, .strip li, .card, .path, .before-q, .vcard, ' +
      '.incl-item, .pair, .free-poster, .section-head, .bundle .wrap, .close .box, ' +
      '.join-card, .join-head, .faq details, .founder, .shelf-stage, .facts li, ' +
      '.pv-head, .pv-method, .pv-chips, .pv-tools, .gallery .section-head, .compare, .gal-note');
    if (targets.length && 'IntersectionObserver' in window) {
      var seen = 0;
      var rio = new IntersectionObserver(function (es) {
        es.forEach(function (e) {
          if (!e.isIntersecting) return;
          rio.unobserve(e.target);
          e.target.classList.add('rv-in');
        });
      }, { rootMargin: '0px 0px -8% 0px', threshold: 0.05 });
      targets.forEach(function (el) {
        var r = el.getBoundingClientRect();
        el.classList.add('rv');
        var variant = el.matches('.card, .path, .vcard, .incl-item, .facts li') ? 'scale'
          : el.matches('.pv-head, .section-head, .join-head') ? 'left'
          : el.matches('.pv-method, .free-poster, .hero-media') ? 'right' : '';
        if (variant) el.setAttribute('data-rv', variant);
        // Stagger siblings a little so a row of cards cascades.
        el.style.setProperty('--rv-d', (Math.min(seen % 6, 5) * 60) + 'ms');
        seen += 1;
        if (r.top < window.innerHeight && r.bottom > 0) { el.classList.add('rv-in'); }
        else { rio.observe(el); }
      });
    }

    // Counters: every number is already final in the page (the figure the
    // store's own data gives). Once, as it scrolls into view, it counts up
    // to that figure; with reduced motion, or no observer, it just stays.
    var nums = $$('[data-count]');
    if (nums.length && !still() && 'IntersectionObserver' in window) {
      var cio = new IntersectionObserver(function (es) {
        es.forEach(function (e) {
          if (!e.isIntersecting) return;
          cio.unobserve(e.target);
          var el = e.target, end = parseInt(el.getAttribute('data-count'), 10);
          if (!(end > 0) || end > 100000) return;
          var t0 = null;
          var step = function (t) {
            if (t0 === null) t0 = t;
            var p = Math.min(1, (t - t0) / 900), eased = 1 - Math.pow(1 - p, 3);
            el.textContent = String(p < 1 ? Math.round(end * eased) : end);
            if (p < 1) window.requestAnimationFrame(step);
          };
          window.requestAnimationFrame(step);
        });
      }, { threshold: 0.6 });
      nums.forEach(function (el) { cio.observe(el); });
    }

    // Cover images: a soft shimmer holds the place until the picture is in.
    $$('img[loading="lazy"]').forEach(function (img) {
      if (img.complete) return;
      var box = img.parentElement;
      if (!box) return;
      box.classList.add('is-loading');
      var done = function () { box.classList.remove('is-loading'); };
      img.addEventListener('load', done); img.addEventListener('error', done);
    });

    // Pointer light and a small tilt on cards (fine pointers only).
    var fine = window.matchMedia('(hover: hover) and (pointer: fine)');
    if (fine.matches) {
      var raf = 0, last = null;
      var paint = function () {
        raf = 0;
        if (!last) return;
        var el = last.el, r = last.r;
        var x = (last.x - r.left) / r.width, y = (last.y - r.top) / r.height;
        el.style.setProperty('--mx', (x * 100).toFixed(1) + '%');
        el.style.setProperty('--my', (y * 100).toFixed(1) + '%');
        if (!still()) {
          el.style.setProperty('--ry', ((x - 0.5) * 5).toFixed(2) + 'deg');
          el.style.setProperty('--rx', ((0.5 - y) * 4).toFixed(2) + 'deg');
        }
      };
      $$('.card, .jopt-pick, .vcard, .path, .incl-item').forEach(function (el) {
        el.classList.add('lit');
        el.addEventListener('pointermove', function (e) {
          last = { el: el, r: el.getBoundingClientRect(), x: e.clientX, y: e.clientY };
          if (!raf) raf = window.requestAnimationFrame(paint);
        });
        el.addEventListener('pointerleave', function () {
          el.style.removeProperty('--rx'); el.style.removeProperty('--ry');
        });
      });
    }

    // Parallax: the page glow drifts a little slower than the page.
    var ticking = false;
    var drift = function () {
      ticking = false;
      if (still()) { root.style.removeProperty('--py'); return; }
      root.style.setProperty('--py', (Math.min(window.scrollY, 2400) * -0.08).toFixed(1) + 'px');
    };
    window.addEventListener('scroll', function () {
      if (!ticking) { ticking = true; window.requestAnimationFrame(drift); }
    }, { passive: true });
    drift();

    // ---------------------------------------------------------- motion layer
    // Storefront motion (2026-11-11). Under reduced motion none of this runs:
    // the page stays as built.
    if (!still()) {
      // Headline: each word rises in turn (the text itself is unchanged).
      var h1 = $('.pv-hero h1');
      if (h1) {
        var wi = 0;
        $$('span', h1).forEach(function (sp) {
          var words = sp.textContent.trim().split(/\s+/);
          sp.setAttribute('aria-label', words.join(' '));
          sp.textContent = '';
          words.forEach(function (w, k) {
            var el = document.createElement('span');
            el.className = 'w'; el.setAttribute('aria-hidden', 'true');
            el.style.setProperty('--i', String(wi)); wi += 1;
            el.textContent = w;
            sp.appendChild(el);
            if (k < words.length - 1) sp.appendChild(document.createTextNode(' '));
          });
        });
      }

      // The accent word under the lede cycles through the store's own paths.
      var cyc = $('[data-cycle]');
      if (cyc) {
        var names = cyc.getAttribute('data-cycle').split('|').filter(Boolean), at = 0;
        if (names.length > 1) {
          var typeTo = function (word, done) {
            var shown = cyc.textContent;
            cyc.classList.add('is-typing');
            var write = function (n) {
              if (n <= word.length) { cyc.textContent = word.slice(0, n); window.setTimeout(function () { write(n + 1); }, 55); }
              else { cyc.classList.remove('is-typing'); done(); }
            };
            var wipe = function () {
              if (shown.length) { shown = shown.slice(0, -1); cyc.textContent = shown; window.setTimeout(wipe, 28); }
              else write(0);
            };
            wipe();
          };
          var tick = function () {
            if (still() || document.hidden) { window.setTimeout(tick, 2400); return; }
            at = (at + 1) % names.length;
            typeTo(names[at], function () { window.setTimeout(tick, 2600); });
          };
          window.setTimeout(tick, 3200);
        }
      }

      // Scroll progress bar, and the hero orbs drift against the scroll.
      var bar = document.createElement('div');
      bar.className = 'pv-progress'; bar.setAttribute('aria-hidden', 'true');
      document.body.appendChild(bar);
      var hero = $('.pv-hero'), pend = false;
      var paintScroll = function () {
        pend = false;
        var max = document.documentElement.scrollHeight - window.innerHeight;
        bar.style.setProperty('--prog', max > 0 ? Math.min(1, window.scrollY / max).toFixed(4) : '0');
        if (hero && window.scrollY < 1400) hero.style.setProperty('--hp', (window.scrollY * 0.6).toFixed(1) + 'px');
      };
      window.addEventListener('scroll', function () {
        if (!pend) { pend = true; window.requestAnimationFrame(paintScroll); }
      }, { passive: true });
      window.addEventListener('resize', paintScroll);
      paintScroll();
    }

    initJoin();
    initSubscribe();
  });

  // ------------------------------------------------------------ join page
  function initJoin() {
    var join = $('[data-join]');
    if (!join) return;
    join.classList.add('is-live');
    var status = $('[data-join-status]', join);
    var back = $('[data-join-back]', join);
    var picks = $$('[data-join-pick]', join);
    var legend = $('.join-pick legend', join);
    var form = $('[data-join-form]');
    function say(t) { if (status) status.textContent = t; }
    function step(n) {
      join.setAttribute('data-step', String(n));
      $$('[data-step-dot]', join).forEach(function (li) {
        var i = Number(li.getAttribute('data-step-dot'));
        li.classList.toggle('is-done', i < n);
        li.classList.toggle('is-now', i === n);
        if (i === n) li.setAttribute('aria-current', 'step'); else li.removeAttribute('aria-current');
      });
      if (back) back.hidden = n !== 2;
      if (legend) legend.textContent = n === 1 ? '1. Choose a course, kit or the bundle' : '2. Review, then continue';
    }
    function reset() {
      step(1);
      say('');
      $$('.jopt', join).forEach(function (li) { li.classList.remove('is-picked'); });
      $$('.btn-buy', join).forEach(function (a) { a.classList.remove('is-going'); a.removeAttribute('aria-busy'); });
      picks.forEach(function (r) { r.checked = false; });
      if (form) busy(form, false);
    }
    step(1);
    picks.forEach(function (r) {
      r.addEventListener('change', function () {
        var li = r.closest('.jopt');
        $$('.jopt', join).forEach(function (x) { x.classList.toggle('is-picked', x === li); });
        step(2);
        var name = $('.jopt-body b', li);
        say((name ? name.textContent : 'Your pick') + ' chosen. Review the details, then continue to Stripe.');
        var go = $('.btn-buy', li);
        if (go) window.setTimeout(function () { go.focus(); }, still() ? 0 : 160);
      });
    });
    if (back) back.addEventListener('click', function () {
      var cur = $('.jopt.is-picked input', join);
      step(1);
      $$('.jopt', join).forEach(function (li) { li.classList.remove('is-picked'); });
      say('');
      if (cur) cur.focus();
    });
    $$('.jopt-detail a.btn-buy', join).forEach(function (a) {
      a.addEventListener('click', function () {
        step(3);
        a.classList.add('is-going');
        a.setAttribute('aria-busy', 'true');
        say("Opening Stripe's secure checkout. You enter your email and card there; we never see them.");
      });
    });

    // Sign in: checks the same shapes the server checks, in words.
    if (form) {
      var email = $('#jf-email', form), receipt = $('#jf-receipt', form);
      var hint = $('#jf-receipt-m', form).textContent;
      var formMsg = $('[data-join-error]', form);
      var EMAIL = /^[A-Za-z0-9._%+'-]{1,64}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24}$/;
      var RECEIPT = /^[0-9A-Z]{3,12}(?:-[0-9A-Z]{3,12}){0,3}$/;
      var checkEmail = function () {
        var v = email.value.trim();
        if (!v) return 'Type the email address your Stripe receipt was sent to.';
        if (!EMAIL.test(v)) return "That doesn't look like an email address. Check for a missing @ or dot.";
        return '';
      };
      var checkReceipt = function () {
        var v = receipt.value.replace(/\s+/g, '').toUpperCase().replace(/^RECEIPT/, '').replace(/^#+/, '');
        if (!v) return 'Type the receipt number from the same email, like #1234-5678.';
        if (!RECEIPT.test(v)) return 'The receipt number is letters, digits and dashes, like 1234-5678.';
        return '';
      };
      var fields = [[email, checkEmail, $('#jf-email-m', form), ''], [receipt, checkReceipt, $('#jf-receipt-m', form), hint]];
      var mark = function (f, force) {
        var msg = f[1](), box = f[0].closest('.field');
        var show = force || f[0].dataset.touched === '1';
        if (show && msg) {
          f[0].setAttribute('aria-invalid', 'true'); box.classList.add('is-bad'); box.classList.remove('is-ok');
          f[2].textContent = msg;
        } else {
          f[0].removeAttribute('aria-invalid'); box.classList.remove('is-bad');
          box.classList.toggle('is-ok', !msg && f[0].dataset.touched === '1');
          f[2].textContent = f[3];
        }
        return msg;
      };
      fields.forEach(function (f) {
        f[0].addEventListener('blur', function () { f[0].dataset.touched = '1'; mark(f, false); });
        f[0].addEventListener('input', function () { if (f[0].dataset.touched === '1') mark(f, false); formMsg.textContent = ''; });
      });
      form.addEventListener('submit', function (e) {
        var bad = null;
        fields.forEach(function (f) { f[0].dataset.touched = '1'; if (mark(f, true) && !bad) bad = f; });
        if (bad) {
          e.preventDefault();
          formMsg.textContent = 'Fix the highlighted field and try again.';
          form.classList.remove('shake'); void form.offsetWidth; form.classList.add('shake');
          bad[0].focus();
          return;
        }
        formMsg.textContent = '';
        window.setTimeout(function () { busy(form, true); }, 0);
      });
    }
    window.addEventListener('pageshow', function (e) { if (e.persisted) reset(); });
  }

  // Free updates signup: a plain form post. The script only checks the field
  // before the post and shows a busy state; it sends nothing itself.
  function initSubscribe() {
    var okAddress = /^[^\s@]{1,64}@[^\s@]+\.[^\s@]{2,}$/;
    $$('[data-sub-form]').forEach(function (form) {
      var email = $('input[name="email"]', form), msg = $('[data-sub-msg]', form);
      var btn = $('[data-sub-submit]', form), lbl = btn && $('.lbl', btn);
      if (!email) return;
      function say(t) { if (msg) msg.textContent = t; }
      email.addEventListener('input', function () {
        form.classList.remove('is-bad'); email.removeAttribute('aria-invalid'); say('');
      });
      form.addEventListener('submit', function (e) {
        var v = email.value.trim();
        if (!okAddress.test(v) || v.length > 254) {
          e.preventDefault();
          form.classList.add('is-bad'); email.setAttribute('aria-invalid', 'true');
          say('Type your email address, like you@example.com.');
          form.classList.remove('shake'); void form.offsetWidth; form.classList.add('shake');
          email.focus();
          return;
        }
        say('');
        window.setTimeout(function () {
          if (!btn) return;
          btn.classList.add('is-busy'); btn.disabled = true; btn.setAttribute('aria-busy', 'true');
          if (lbl) lbl.textContent = 'Sending';
        }, 0);
      });
      window.addEventListener('pageshow', function (e) {
        if (!e.persisted || !btn) return;
        btn.classList.remove('is-busy'); btn.disabled = false; btn.removeAttribute('aria-busy');
        if (lbl) lbl.textContent = 'Get updates';
      });
    });
  }

  function busy(form, on) {
    var b = $('[data-join-submit]', form);
    if (!b) return;
    b.classList.toggle('is-busy', on);
    b.disabled = on;
    if (on) b.setAttribute('aria-busy', 'true'); else b.removeAttribute('aria-busy');
    var l = $('.lbl', b);
    if (l) l.textContent = on ? 'Checking with Stripe' : 'Open my downloads';
  }
})();
