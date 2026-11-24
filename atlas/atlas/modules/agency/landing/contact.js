(function () {
  'use strict';
  var form = document.querySelector('[data-contact-form]');
  if (!form || !window.fetch) return;
  var status = form.querySelector('[data-contact-status]');
  var btn = form.querySelector('[data-contact-submit]');
  var label = btn ? btn.textContent : '';
  function say(text, bad) {
    if (!status) return;
    status.textContent = text;
    status.className = 'status' + (bad ? ' bad' : '');
  }
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    say('');
    if (btn) { btn.disabled = true; btn.textContent = 'Sending'; }
    fetch(form.action, {
      method: 'POST',
      headers: { 'Accept': 'application/json' },
      body: new URLSearchParams(new FormData(form)),
      credentials: 'omit'
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, j: j }; });
    }).then(function (res) {
      if (res.ok && res.j.ok) {
        if (form.querySelector('input[name="source"]').value === 'landing') {
          window.location.assign('/thanks/');
          return;
        }
        form.reset();
        say('Thanks, Miro has your message.');
        return;
      }
      say((res.j && res.j.error) || 'That did not send. Please try again, or email owner@example.com.', true);
    }).catch(function () {
      say('That did not send. Please try again, or email owner@example.com.', true);
    }).then(function () {
      if (btn) { btn.disabled = false; btn.textContent = label; }
    });
  });
}());
