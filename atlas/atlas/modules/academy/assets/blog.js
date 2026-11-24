/* ATLAS Academy blog (/blog/, blog_pages.py).
 * Search and topic filter over the posts already on the page, and the
 * lessons a visitor ticks as finished. It makes no request. The ticks are
 * the one thing it keeps: a list of lesson ids in this browser's
 * localStorage, read and written only inside try/catch; when storage is
 * unavailable the ticks stay hidden and the page reads as before. */
(function () {
  'use strict';
  var KEY = 'atlas-academy-finished';
  var ID = /^[A-Za-z0-9_-]{11}$/;

  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  // ---------------------------------------------------------------- filter
  var tools = document.querySelector('[data-blog-tools]');
  var list = document.querySelector('[data-blog-list]');
  if (tools && list) {
    var input = tools.querySelector('input[name="q"]');
    var chips = $$('[data-topic]', tools);
    var count = document.querySelector('[data-blog-count]');
    var empty = document.querySelector('[data-blog-empty]');
    var posts = $$('.blog-post', list);
    var topic = '';
    var apply = function () {
      var words = (input.value || '').toLowerCase().split(/\s+/).filter(Boolean);
      var shown = 0;
      posts.forEach(function (li) {
        var tags = (li.getAttribute('data-tags') || '').split(' ');
        var text = li.getAttribute('data-q') || '';
        var ok = (!topic || tags.indexOf(topic) !== -1) &&
          words.every(function (w) { return text.indexOf(w) !== -1; });
        li.hidden = !ok;
        if (ok) shown += 1;
      });
      var filtered = topic || words.length;
      count.hidden = !filtered;
      count.textContent = filtered ? (shown === 1 ? '1 post' : shown + ' posts') : '';
      empty.hidden = shown !== 0;
    };
    chips.forEach(function (b) {
      b.addEventListener('click', function () {
        topic = b.getAttribute('data-topic') || '';
        chips.forEach(function (x) { x.setAttribute('aria-pressed', x === b ? 'true' : 'false'); });
        apply();
      });
    });
    input.addEventListener('input', apply);
    tools.addEventListener('submit', function (ev) { ev.preventDefault(); apply(); });
    tools.hidden = false;
  }

  // -------------------------------------------------------------- progress
  function read() {
    try {
      var raw = window.localStorage.getItem(KEY);
      var ids = raw ? JSON.parse(raw) : [];
      if (!Array.isArray(ids)) return [];
      return ids.filter(function (x) { return typeof x === 'string' && ID.test(x); });
    } catch (e) {
      return null;
    }
  }
  function write(ids) {
    try {
      if (ids.length) window.localStorage.setItem(KEY, JSON.stringify(ids));
      else window.localStorage.removeItem(KEY);
      return true;
    } catch (e) {
      return false;
    }
  }

  var boxes = $$('input[data-done]');
  var done = boxes.length ? read() : null;
  if (done === null) return;                 // no lesson here, or no storage

  var panel = document.querySelector('[data-progress]');
  var label = document.querySelector('[data-progress-count]');
  function paint() {
    var onPage = 0;
    boxes.forEach(function (box) {
      var id = box.getAttribute('data-done');
      box.checked = done.indexOf(id) !== -1;
      if (box.checked) onPage += 1;
      var card = box.closest('.blog-vid');
      if (card) card.classList.toggle('is-done', box.checked);
      box.closest('.blog-done').hidden = false;
    });
    if (panel && label) {
      var total = $$('.blog-vid').length;
      label.textContent = 'You have ticked ' + onPage + ' of the ' + total +
        (total === 1 ? ' lesson' : ' lessons') + ' above as finished.';
      panel.hidden = false;
    }
  }
  boxes.forEach(function (box) {
    box.addEventListener('change', function () {
      var id = box.getAttribute('data-done');
      var next = done.filter(function (x) { return x !== id; });
      if (box.checked) next.push(id);
      if (write(next)) done = next;
      paint();
    });
  });
  var clear = document.querySelector('[data-progress-clear]');
  if (clear) {
    clear.addEventListener('click', function () {
      if (write([])) done = [];
      paint();
    });
  }
  paint();
})();
