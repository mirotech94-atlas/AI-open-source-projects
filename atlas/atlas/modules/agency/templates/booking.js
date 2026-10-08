// The booking request on a Mapfront client site (sitebooking.py inlines this).
// No dependency, no network: a preview form sends nothing, a live form is a
// plain POST the browser makes. Every motion is skipped under reduced motion.
(function () {
  var d = document, root = d.documentElement;
  var form = d.querySelector('form[data-atlas="booking"]');
  if (!form) return;
  var mode = form.getAttribute('data-mode');
  var panel = form.closest('[data-book-panel]');
  var sheet = d.getElementById('book-sheet');
  var holder = sheet && sheet.querySelector('[data-sheet-body]');
  var marker = d.createComment('book');
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)');
  var body = form.querySelector('[data-book-body]');
  var done = form.querySelector('[data-book-done]');
  var alertBox = form.querySelector('[data-book-alert]');
  var submit = form.querySelector('[data-atlas="book-submit"]');
  var label = submit.querySelector('[data-label]');
  var idle = label.textContent;
  var opener = null, timer = 0;
  form.noValidate = true;

  function pad(n) { return (n < 10 ? '0' : '') + n; }
  var now = new Date();
  var today = now.getFullYear() + '-' + pad(now.getMonth() + 1) + '-' + pad(now.getDate());
  form.querySelectorAll('input[type=date]').forEach(function (i) { i.min = today; });

  function q(sel) { return form.querySelector(sel); }
  function val(id) { var el = q('#' + id); return el ? el.value.trim() : ''; }
  function picked(group) {
    var el = q('input[name="' + group + '"]:checked');
    return el ? el.value : '';
  }
  function day(v) {
    var p = v.split('-');
    var dt = new Date(+p[0], +p[1] - 1, +p[2]);
    return dt.toLocaleDateString('en-US', {weekday: 'short', month: 'short', day: 'numeric'});
  }
  function clock(v) {
    var p = v.split(':'), h = +p[0];
    return (h % 12 || 12) + ':' + p[1] + (h < 12 ? ' am' : ' pm');
  }
  function replay(el, cls) {
    if (reduce.matches) return;
    el.classList.remove(cls);
    void el.offsetWidth;
    el.classList.add(cls);
  }
  function put(key, text) {
    form.querySelectorAll('[data-echo="' + key + '"]').forEach(function (el) {
      var t = text || el.getAttribute('data-empty') || '';
      if (el.textContent === t) return;
      el.textContent = t;
      el.classList.toggle('is-set', !!text);
      replay(el, 'is-new');
    });
  }

  // The table: one lit place per guest, the stepper's limits disabled.
  function seats() {
    form.querySelectorAll('.mf-stepper').forEach(function (box) {
      var input = box.querySelector('input');
      var lo = +input.min || 1, hi = +input.max || 99;
      var n = parseInt(input.value, 10);
      if (isNaN(n)) n = 0;
      box.querySelector('[data-step="-1"]').disabled = n <= lo;
      box.querySelector('[data-step="1"]').disabled = n >= hi;
      if (input.id === 'b-party') {
        form.querySelectorAll('.mf-seat').forEach(function (s, k) {
          s.classList.toggle('is-on', k < n);
        });
        put('party', n ? (n === 1 ? 'Table for one' : 'Table for ' + n) : '');
      }
    });
  }

  function echo() {
    var d1 = val('b-day'), t1 = val('b-time'), part = picked('part');
    var when = [d1 && day(d1), t1 && clock(t1), part && part !== 'Flexible' ?
      part.toLowerCase() : ''].filter(Boolean).join(', ');
    put('job', picked('job'));
    put('service', picked('service'));
    put('where', val('b-where'));
    put('when', when);
    form.querySelectorAll('.mf-dt').forEach(function (w) {
      w.toggleAttribute('data-filled', !!w.querySelector('input').value);
    });
    var glass = q('.mf-glass__arch');
    if (glass) replay(glass, 'is-lit');
    seats();
  }

  // One message per field, cleared as soon as the visitor changes it.
  function mark(field, msg) {
    var out = field.querySelector('.mf-bfield__msg');
    field.classList.toggle('is-bad', !!msg);
    if (out) out.textContent = msg;
    field.querySelectorAll('input:not([type=radio]),textarea').forEach(function (c) {
      c.setAttribute('aria-invalid', msg ? 'true' : 'false');
    });
  }
  function check() {
    var bad = [];
    form.querySelectorAll('.mf-bfield').forEach(function (f) {
      var need = f.getAttribute('data-need'), msg = '';
      var radios = f.querySelectorAll('input[type=radio]');
      var c = f.querySelector('input:not([type=radio]),textarea');
      var v = c ? c.value.trim() : '';
      if (radios.length) {
        if (need && !f.querySelector('input:checked')) msg = need;
      } else if (!v) {
        if (need) msg = need;
      } else if (c.type === 'tel' && v.replace(/\D/g, '').length < 7) {
        msg = 'Add a phone number we can call back.';
      } else if (c.type === 'date' && v < today) {
        msg = 'Choose today or a later day.';
      } else if (c.type === 'number' && (+v < +c.min || +v > +c.max)) {
        msg = 'Choose a number from ' + c.min + ' to ' + c.max + '.';
      }
      mark(f, msg);
      if (msg) bad.push(f);
    });
    return bad;
  }

  function state(s) {
    clearTimeout(timer);
    submit.setAttribute('data-state', s);
    submit.disabled = s === 'loading';
    var t = s === 'loading' ? submit.getAttribute('data-busy') :
      s === 'error' ? submit.getAttribute('data-bad') : idle;
    label.textContent = t;
    label.setAttribute('data-label', t);
    if (s === 'error') timer = setTimeout(function () { state('idle'); }, 2400);
  }

  form.addEventListener('input', function (e) {
    var f = e.target.closest('.mf-bfield');
    if (f && f.classList.contains('is-bad')) mark(f, '');
    var left = form.querySelectorAll('.mf-bfield.is-bad').length;
    if (alertBox.textContent) alertBox.textContent = !left ? '' : left === 1 ?
      'One detail needs a look.' : left + ' details need a look.';
    echo();
  });
  form.addEventListener('change', echo);
  form.addEventListener('click', function (e) {
    var step = e.target.closest('[data-step]');
    if (step) {
      var input = q('#' + step.getAttribute('data-for'));
      var n = (parseInt(input.value, 10) || 0) + (+step.getAttribute('data-step'));
      input.value = Math.min(+input.max, Math.max(+input.min, n));
      input.dispatchEvent(new Event('input', {bubbles: true}));
      replay(step, 'is-tap');
    }
    if (e.target.closest('[data-book-edit]')) {
      form.removeAttribute('data-state');
      body.hidden = false;
      done.hidden = true;
      state('idle');
      var first = body.querySelector('input:not([type=hidden]),textarea');
      if (first) first.focus();
    }
  });

  form.addEventListener('submit', function (e) {
    var bad = check();
    if (bad.length) {
      e.preventDefault();
      alertBox.textContent = bad.length === 1 ? 'One detail needs a look.' :
        bad.length + ' details need a look.';
      state('error');
      var c = bad[0].querySelector('input,textarea');
      if (c) c.focus();
      return;
    }
    alertBox.textContent = '';
    if (mode !== 'send') {
      // A preview: nothing leaves the page. Say so, and what to do instead.
      e.preventDefault();
      form.setAttribute('data-state', 'done');
      body.hidden = true;
      done.hidden = false;
      if (holder && holder.contains(form)) holder.scrollTop = 0;
      else form.scrollIntoView({block: 'start', behavior: reduce.matches ? 'auto' : 'smooth'});
      done.focus({preventScroll: true});
      return;
    }
    form.setAttribute('data-state', 'sending');
    state('loading');
  });
  window.addEventListener('pageshow', function (e) {
    if (e.persisted) { form.removeAttribute('data-state'); state('idle'); }
  });

  // The sheet: the form moves into the dialog while it is open, and back.
  function open(pick) {
    if (pick) {
      form.querySelectorAll('input[type=radio]').forEach(function (r) {
        if (r.value === pick) { r.checked = true; }
      });
      echo();
    }
    panel.parentNode.insertBefore(marker, panel);
    holder.appendChild(panel);
    root.classList.add('mf-locked');
    sheet.showModal();
    holder.scrollTop = 0;
  }
  function close() {
    if (!sheet.open) return;
    if (reduce.matches) { sheet.close(); return; }
    sheet.classList.add('is-closing');
    setTimeout(function () { sheet.classList.remove('is-closing'); sheet.close(); }, 200);
  }
  if (sheet && typeof sheet.showModal === 'function') {
    sheet.addEventListener('close', function () {
      if (marker.parentNode) marker.parentNode.replaceChild(panel, marker);
      root.classList.remove('mf-locked');
      if (opener) opener.focus({preventScroll: true});
    });
    sheet.addEventListener('click', function (e) { if (e.target === sheet) close(); });
    d.addEventListener('click', function (e) {
      var a = e.target.closest('[data-book-open]');
      if (a) { e.preventDefault(); opener = a; open(a.getAttribute('data-book-pick')); }
      if (e.target.closest('[data-book-close]')) close();
    });
  } else {
    d.addEventListener('click', function (e) {
      var a = e.target.closest('[data-book-open][data-book-pick]');
      if (a) open0(a.getAttribute('data-book-pick'));
    });
  }
  function open0(pick) {
    form.querySelectorAll('input[type=radio]').forEach(function (r) {
      if (r.value === pick) { r.checked = true; }
    });
    echo();
  }

  // Keep the sticky bar out of the way while the keyboard is up.
  d.addEventListener('focusin', function (e) {
    if (e.target.matches('input,textarea,select')) root.classList.add('mf-typing');
  });
  d.addEventListener('focusout', function () { root.classList.remove('mf-typing'); });

  echo();
})();
