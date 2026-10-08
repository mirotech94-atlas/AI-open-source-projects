/* ATLAS Academy, shop.mapfront.io. The site's only script: theme toggle,
 * header shadow, mobile menu, scroll reveal, catalog filter chips, the
 * "Find your fit" finder (its data is window.FINDER, written by site.py from
 * the catalog), cover thumbs, the sticky buy bar and the in-page nav.
 * It makes no request and stores nothing: tests/test_academy_site.py checks. */
(function () {
  var root = document.documentElement;
  var mq = window.matchMedia('(prefers-color-scheme: dark)');
  var theme = mq.matches ? 'dark' : 'light';
  root.setAttribute('data-theme', theme);
  var sun = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4.5"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
  var moon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';
  function paintToggle() {
    document.querySelectorAll('[data-theme-toggle]').forEach(function (b) {
      b.innerHTML = theme === 'dark' ? sun : moon;
      b.setAttribute('aria-label', 'Switch to ' + (theme === 'dark' ? 'light' : 'dark') + ' mode');
    });
  }
  document.addEventListener('DOMContentLoaded', function () {
    paintToggle();
    document.querySelectorAll('[data-theme-toggle]').forEach(function (b) {
      b.addEventListener('click', function () {
        theme = theme === 'dark' ? 'light' : 'dark';
        root.setAttribute('data-theme', theme);
        paintToggle();
      });
    });

    // Header shadow + mobile menu
    var top = document.querySelector('.top');
    var onScroll = function () { if (top) top.classList.toggle('scrolled', window.scrollY > 8); };
    window.addEventListener('scroll', onScroll, { passive: true }); onScroll();
    var mb = document.querySelector('.menu-btn'), nav = document.querySelector('.nav');
    if (mb && nav) {
      mb.addEventListener('click', function () {
        var open = nav.classList.toggle('open');
        mb.setAttribute('aria-expanded', open ? 'true' : 'false');
      });
      nav.querySelectorAll('a').forEach(function (a) { a.addEventListener('click', function () { nav.classList.remove('open'); mb.setAttribute('aria-expanded', 'false'); }); });
    }

    // Reveal on scroll
    var rv = document.querySelectorAll('.reveal');
    if ('IntersectionObserver' in window) {
      var io = new IntersectionObserver(function (es) { es.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } }); }, { rootMargin: '0px 0px -8% 0px' });
      rv.forEach(function (el) { io.observe(el); });
    } else { rv.forEach(function (el) { el.classList.add('in'); }); }

    // Catalog filters
    var chips = document.querySelectorAll('[data-filter]');
    var cards = document.querySelectorAll('.card[data-tags]');
    var none = document.querySelector('.no-results');
    chips.forEach(function (c) {
      c.addEventListener('click', function () {
        var f = c.getAttribute('data-filter');
        chips.forEach(function (x) { x.setAttribute('aria-pressed', x === c ? 'true' : 'false'); });
        var shown = 0;
        cards.forEach(function (card) {
          var ok = f === 'all' || card.getAttribute('data-tags').split(' ').indexOf(f) > -1;
          card.hidden = !ok; if (ok) shown++;
        });
        if (none) none.hidden = shown > 0;
        var live = document.getElementById('filter-status');
        if (live) live.textContent = shown + (shown === 1 ? ' title' : ' titles') + ' shown';
      });
    });

    // Finder
    var data = window.FINDER || {};
    var out = document.getElementById('finder-result');
    document.querySelectorAll('.choice').forEach(function (b) {
      b.addEventListener('click', function () {
        document.querySelectorAll('.choice').forEach(function (x) { x.setAttribute('aria-pressed', x === b ? 'true' : 'false'); });
        var recs = data[b.getAttribute('data-key')] || [];
        out.innerHTML = recs.map(function (r, i) {
          // r.buy is the Stripe link of a live single offer; '' when the title
          // has editions to choose from or is not on sale yet.
          var buy = r.buy
            ? '<a class="btn btn-buy btn-sm" href="' + r.buy + '" rel="noopener">Buy</a>'
            : '<a class="btn btn-ghost btn-sm" href="' + r.href + '#buy">See options</a>';
          return '<div class="rec"><img src="' + r.cover + '" alt="" width="96" height="96" loading="lazy">' +
            '<div><span class="badge">' + (i === 0 ? 'Start here' : 'Then') + '</span><h3>' + r.title + '</h3>' +
            '<p class="why">' + r.why + '</p><div class="row"><span class="price">' + r.price + '</span>' +
            buy + '<a class="more" href="' + r.href + '">See what\u2019s inside</a></div></div></div>';
        }).join('');
        if (window.innerWidth < 860) out.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
    });

    // Cover thumbs
    document.querySelectorAll('.thumbs button').forEach(function (t) {
      t.addEventListener('click', function () {
        var main = document.querySelector('.p-cover .frame img');
        main.src = t.getAttribute('data-src'); main.alt = t.getAttribute('data-alt');
        document.querySelectorAll('.thumbs button').forEach(function (x) { x.setAttribute('aria-pressed', x === t ? 'true' : 'false'); });
      });
    });

    // Sticky buy bar
    var sb = document.querySelector('.sticky-buy'), bb = document.querySelector('.buybox');
    if (sb && bb && 'IntersectionObserver' in window) {
      new IntersectionObserver(function (es) {
        es.forEach(function (e) { sb.classList.toggle('show', !e.isIntersecting && e.boundingClientRect.top < 0); });
      }).observe(bb);
    }

    // In-page nav highlight
    var links = document.querySelectorAll('.p-nav a');
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
