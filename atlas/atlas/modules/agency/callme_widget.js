/* "Have Mapfront call me" (atlas/modules/agency/callme.py). On preview sites
 * only. No dependencies, no cookies, no storage, no trackers. Built here, in
 * the browser, so the page's own static HTML carries no form of Mapfront's.
 * Everything shown is set with textContent, never parsed as HTML. */
(function () {
  "use strict";
  var C = /*MAPFRONT_CALLME_CONFIG*/null;
  if (!C || !C.endpoint || !C.consent || window.__mapfrontCallMe) { return; }
  window.__mapfrontCallMe = true;
  var d = document;

  var css =
    ".mfm{margin:2rem auto;max-width:40rem;padding:1.25rem;border-radius:.75rem;" +
    "border:1px solid rgba(0,0,0,.15);background:var(--atlas-bg,#fff);" +
    "color:var(--atlas-text,#1b2430);font-family:system-ui,-apple-system,sans-serif}" +
    ".mfm h2{margin:0 0 .4rem;font-size:1.25rem}.mfm p{margin:.4rem 0;line-height:1.45}" +
    ".mfm label{display:block;font-weight:600;margin:.7rem 0 .25rem}" +
    ".mfm input[type=text],.mfm input[type=tel]{width:100%;min-height:44px;padding:.45rem .6rem;" +
    "font:inherit;border:1px solid rgba(0,0,0,.4);border-radius:.4rem;background:#fff;color:#1b2430}" +
    ".mfm .mfm-ok{display:flex;gap:.6rem;align-items:flex-start;font-weight:400;margin-top:.9rem}" +
    ".mfm .mfm-ok input{width:22px;height:22px;flex:none;margin-top:.1rem}" +
    ".mfm button{margin-top:.9rem;min-height:44px;padding:.5rem 1.1rem;border:0;border-radius:.4rem;" +
    "background:var(--atlas-primary,#0f6e8c);color:var(--atlas-on-primary,#fff);" +
    "font:600 1rem system-ui,sans-serif;cursor:pointer}" +
    ".mfm button[disabled]{opacity:.6;cursor:wait}" +
    ".mfm .mfm-hp{position:absolute;left:-9999px;width:1px;height:1px;overflow:hidden}" +
    ".mfm .mfm-small{font-size:.85rem;opacity:.85}" +
    ".mfm a{color:inherit}" +
    ".mfm :focus-visible{outline:3px solid var(--atlas-text,#1b2430);outline-offset:2px}";

  function el(tag, attrs, text) {
    var node = d.createElement(tag);
    var k;
    for (k in attrs || {}) {
      if (Object.prototype.hasOwnProperty.call(attrs, k)) { node.setAttribute(k, attrs[k]); }
    }
    if (text) { node.textContent = text; }
    return node;
  }

  function digits(value) { return String(value || "").replace(/\D/g, ""); }

  function usNumber(value) {
    var n = digits(value);
    if (n.length === 11 && n.charAt(0) === "1") { n = n.slice(1); }
    if (n.length !== 10 || /[01]/.test(n.charAt(0)) || /[01]/.test(n.charAt(3))) {
      return null;
    }
    return n;
  }

  function pretty(n) {
    return "(" + n.slice(0, 3) + ") " + n.slice(3, 6) + "-" + n.slice(6);
  }

  function lineLink(prefix) {
    var p = el("p", {"class": "mfm-small"}, prefix);
    var a = el("a", {href: C.lineTel}, C.line);
    p.appendChild(a);
    p.appendChild(d.createTextNode("."));
    return p;
  }

  var style = el("style", {"data-mapfront": "callme"});
  style.textContent = css;
  d.head.appendChild(style);

  // The preview banner gets Mapfront's line too, when there is one.
  if (C.line && C.lineTel) {
    var banner = d.querySelector("[data-atlas=preview] p");
    if (banner) {
      banner.appendChild(d.createTextNode(" · Questions? Call or text "));
      banner.appendChild(el("a", {href: C.lineTel}, C.line));
    }
  }

  var mount = d.getElementById("call-me") || el("div", {id: "call-me"});
  var contact = d.getElementById("contact");
  if (contact && contact.parentNode && mount.previousElementSibling !== contact) {
    contact.parentNode.insertBefore(mount, contact.nextSibling);
  } else if (!mount.parentNode) {
    (d.querySelector("main") || d.body).appendChild(mount);
  }

  var box = el("section", {"class": "mfm", "aria-labelledby": "mfm-title"});
  box.appendChild(el("h2", {id: "mfm-title"}, "Is this your business? Have Mapfront call you"));
  box.appendChild(el("p", null,
    "Mapfront made this preview for " + (C.business || "your business") + ". Leave your " +
    "number and Miro's assistant, an AI, will call you about it. This goes to " +
    "Mapfront, not to " + (C.business || "the business") + "."));

  var form = el("form", {novalidate: "novalidate"});
  form.appendChild(el("label", {"for": "mfm-name"}, "Your name"));
  var name = el("input", {id: "mfm-name", type: "text", autocomplete: "name",
                          maxlength: "80", required: "required"});
  form.appendChild(name);
  form.appendChild(el("label", {"for": "mfm-phone"}, "Phone number"));
  var phone = el("input", {id: "mfm-phone", type: "tel", autocomplete: "tel",
                           inputmode: "tel", maxlength: "20", required: "required"});
  form.appendChild(phone);
  var confirmLine = el("p", {"class": "mfm-small", "aria-live": "polite"});
  form.appendChild(confirmLine);

  var hpWrap = el("div", {"class": "mfm-hp", "aria-hidden": "true"});
  hpWrap.appendChild(el("label", {"for": "mfm-website"}, "Leave this empty"));
  var hp = el("input", {id: "mfm-website", type: "text", tabindex: "-1", autocomplete: "off"});
  hpWrap.appendChild(hp);
  form.appendChild(hpWrap);

  var okLabel = el("label", {"class": "mfm-ok", "for": "mfm-consent"});
  var ok = el("input", {id: "mfm-consent", type: "checkbox", required: "required"});
  okLabel.appendChild(ok);
  okLabel.appendChild(el("span", null, C.consent));
  form.appendChild(okLabel);

  var button = el("button", {type: "submit"}, "Have Mapfront call me");
  form.appendChild(button);
  var status = el("p", {role: "status", "aria-live": "polite"});
  form.appendChild(status);
  box.appendChild(form);
  if (C.line && C.lineTel) {
    box.appendChild(lineLink("Rather call now? Call or text Mapfront at "));
  }
  mount.appendChild(box);

  function showNumber() {
    var n = usNumber(phone.value);
    confirmLine.textContent = n ? "We'll call you at " + pretty(n) + "." : "";
  }
  phone.addEventListener("input", showNumber);

  var busy = false;
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (busy) { return; }
    var who = name.value.replace(/\s+/g, " ").trim();
    var n = usNumber(phone.value);
    if (!who) { status.textContent = "Please add your name."; name.focus(); return; }
    if (!n) { status.textContent = "Please enter a US phone number."; phone.focus(); return; }
    if (!ok.checked) {
      status.textContent = "Please tick the box so we may call you.";
      ok.focus();
      return;
    }
    busy = true;
    button.disabled = true;
    status.textContent = "Sending…";
    var payload = {name: who.slice(0, 80), phone: "+1" + n, consent: true,
                   version: C.version, page: String(location.href).slice(0, 300),
                   website: hp.value};
    fetch(C.endpoint, {method: "POST", credentials: "omit", mode: "cors",
                       headers: {"Content-Type": "application/json"},
                       body: JSON.stringify(payload)})
      .then(function (r) {
        if (r.status === 200) {
          form.hidden = true;
          status.textContent = "";
          var done = el("p", {role: "status"},
            "Thanks. Mapfront has your request to call " + pretty(n) + ", and Miro " +
            "reviews every one before anyone calls. Say \"don't call\" anytime.");
          box.insertBefore(done, form);
          return;
        }
        status.textContent = r.status === 429 ?
          "Too many requests just now. Please try again in a few minutes." :
          "That didn't go through." + (C.line ? " Please call or text " + C.line + "." : "");
      })
      .catch(function () {
        status.textContent = "That didn't go through." +
          (C.line ? " Please call or text " + C.line + "." : "");
      })
      .then(function () { busy = false; button.disabled = false; });
  });

  if (location.hash === "#call-me") {
    mount.scrollIntoView();
    name.focus();
  }
})();
