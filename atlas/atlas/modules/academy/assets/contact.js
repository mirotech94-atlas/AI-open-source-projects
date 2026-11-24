/* The contact and Help forms (2026-11-14): sends the form to /public/agency/contact
   with fetch and says what happened. Without it the form posts natively and the
   API answers with a plain "Message received" page. Loaded on those two pages only. */
(function () {
  'use strict';
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function init() {
    if (!window.fetch) return;
    $$('[data-contact-form]').forEach(function (form) {
      var status = $('[data-contact-status]', form), btn = $('[data-contact-submit]', form);
      var label = btn ? btn.textContent : '';
      function say(t, bad) {
        if (!status) return;
        status.textContent = t;
        status.classList.toggle('is-bad', !!bad);
      }
      form.addEventListener('submit', function (e) {
        e.preventDefault();
        say('');
        if (btn) { btn.disabled = true; btn.textContent = 'Sending'; }
        var fail = 'That did not send. Please try again, or email the address above.';
        fetch(form.action, {
          method: 'POST', headers: { 'Accept': 'application/json' },
          body: new URLSearchParams(new FormData(form)), credentials: 'omit'
        }).then(function (r) {
          return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, j: j }; });
        }).then(function (res) {
          if (res.ok && res.j.ok) { form.reset(); say('Sent. Miro has your message and will reply by email.'); }
          else say((res.j && res.j.error) || fail, true);
        }).catch(function () { say(fail, true); }).then(function () {
          if (btn) { btn.disabled = false; btn.textContent = label; }
        });
      });
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
