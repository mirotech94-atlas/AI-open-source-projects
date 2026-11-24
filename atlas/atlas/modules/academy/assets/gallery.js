/* ATLAS Academy, agency.storefront.test: the home page's video gallery.
 *
 * Its own script, loaded on the home page only (app.js makes no request).
 * When the section comes near the screen it asks this site for the channel's
 * public videos (GET /public/academy/videos: no cookies, nothing sent, nothing
 * stored) and draws one card each. Every piece of text is set with
 * textContent, never as HTML; the id is checked again here and the picture
 * and player addresses are built from it, not taken from the reply. The
 * pictures come from YouTube's image host (i.ytimg.com), the only third-party
 * host and only for images. The player (youtube-nocookie.com) is created only
 * after a click on a card; nothing from YouTube loads before that except the
 * pictures. If the list cannot be read the section says so and stays plain:
 * an unreadable list is never shown as an empty one. */
(function () {
  'use strict';
  var ENDPOINT = '/public/academy/videos';
  var UNAVAILABLE ='Videos are unavailable right now';
  var ID_RE = /^[A-Za-z0-9_-]{11}$/;
  var DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
  var THUMB = 'https://i.ytimg.com/vi/';
  var EMBED = 'https://www.youtube-nocookie.com/embed/';
  var WATCH = 'https://www.youtube.com/watch?v=';
  var MAX_CARDS = 12;

  function text(tag, cls, value, max) {
    var el = document.createElement(tag);
    if (cls) el.className = cls;
    el.textContent = String(value == null ? '' : value).slice(0, max);
    return el;
  }

  function say(box, state, message) {
    var s = box.querySelector('[data-gal-state]');
    if (!s) return;
    if (!message) { s.hidden = true; return; }
    s.hidden = false;
    s.setAttribute('data-state', state);
    s.textContent = message;
  }

  // The player, made only here and only after a click.
  function play(link, id, title) {
    var f = document.createElement('iframe');
    f.src = EMBED + id + '?autoplay=1&rel=0';
    f.title = title;
    f.allow = 'autoplay; encrypted-media; picture-in-picture; fullscreen';
    f.setAttribute('allowfullscreen', '');
    f.referrerPolicy = 'strict-origin-when-cross-origin';
    f.className = 'gal-frame';
    link.replaceWith(f);
    f.focus();
  }

  function card(v) {
    if (!v || typeof v.id !== 'string' || !ID_RE.test(v.id)) return null;
    var id = v.id;
    var title = String(v.title || '').slice(0, 100);
    if (!title) return null;
    var li = document.createElement('li');
    li.className = 'gal-card';

    var thumb = document.createElement('a');
    thumb.className = 'gal-thumb';
    thumb.href = WATCH + id;
    thumb.rel = 'noopener noreferrer'; thumb.target = '_blank';
    thumb.setAttribute('aria-label', 'Watch on YouTube: ' + title + ' (opens in a new tab)');
    var img = document.createElement('img');
    img.src = THUMB + id + '/hqdefault.jpg';
    img.width = 480; img.height = 360;
    img.alt = '';
    img.loading = 'lazy'; img.decoding = 'async';
    img.referrerPolicy = 'no-referrer';
    thumb.appendChild(img);
    var btn = document.createElement('span');
    btn.className = 'gal-play';
    btn.setAttribute('aria-hidden', 'true');
    btn.textContent = '▶';
    thumb.appendChild(btn);
    thumb.addEventListener('click', function (ev) {
      if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button) return;
      ev.preventDefault();
      play(thumb, id, title);
    });
    li.appendChild(thumb);

    var body = document.createElement('div');
    body.className = 'gal-body';
    body.appendChild(text('h3', 'gal-title', title, 100));
    if (v.description) body.appendChild(text('p', 'gal-desc', v.description, 200));
    var meta = document.createElement('p');
    meta.className = 'gal-meta';
    if (typeof v.published === 'string' && DATE_RE.test(v.published)) {
      var t = text('time', '', v.published, 10);
      t.setAttribute('datetime', v.published);
      meta.appendChild(t);
    }
    var yt = text('a', 'gal-yt', 'Watch on YouTube', 40);
    yt.href = WATCH + id;
    yt.rel = 'noopener';
    meta.appendChild(yt);
    body.appendChild(meta);
    li.appendChild(body);
    return li;
  }

  function draw(box, list) {
    var grid = box.querySelector('[data-gal-grid]');
    if (!grid) return;
    var cards = [];
    for (var i = 0; i < list.length && cards.length < MAX_CARDS; i++) {
      var c = card(list[i]);
      if (c) cards.push(c);
    }
    if (!cards.length && list.length) { say(box, 'unknown', UNAVAILABLE); return; }
    if (!cards.length) { say(box, 'empty', 'No free videos are published yet. Check back soon.'); return; }
    cards.forEach(function (c, n) {
      c.style.setProperty('--n', String(n));
      grid.appendChild(c);
    });
    grid.hidden = false;
    say(box, 'ok', '');
    box.classList.add('gal-ready');
  }

  function load(box) {
    var url = ENDPOINT;
    if (!window.fetch) { say(box, 'unknown', UNAVAILABLE); return; }
    say(box, 'loading', 'Loading videos…');
    var ctl = window.AbortController ? new AbortController() : null;
    var timer = window.setTimeout(function () { if (ctl) ctl.abort(); }, 8000);
    window.fetch(url, { method: 'GET', credentials: 'omit', referrerPolicy: 'no-referrer',
                        headers: { Accept: 'application/json' }, signal: ctl ? ctl.signal : undefined })
      .then(function (r) {
        if (!r.ok) throw new Error('status');
        return r.json();
      })
      .then(function (data) {
        window.clearTimeout(timer);
        if (!data || data.status !== 'ok' || !Array.isArray(data.videos)) throw new Error('shape');
        draw(box, data.videos);
      })
      .catch(function () {
        window.clearTimeout(timer);
        say(box, 'unknown', UNAVAILABLE);
      });
  }

  function start() {
    var box = document.querySelector('[data-gallery]');
    if (!box) return;
    if (!('IntersectionObserver' in window)) { load(box); return; }
    var io = new IntersectionObserver(function (entries) {
      if (entries.some(function (e) { return e.isIntersecting; })) {
        io.disconnect();
        load(box);
      }
    }, { rootMargin: '400px 0px' });
    io.observe(box);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
