/* Mapfront AI chat assistant (atlas/modules/agency/sitechat.py).
 * No dependencies, no cookies, no storage, no trackers. The conversation
 * lives in this page's memory only and is gone when the page closes. Every
 * reply is shown with textContent, never parsed as HTML. */
(function () {
  "use strict";
  var C = /*MAPFRONT_CHAT_CONFIG*/null;
  if (!C || !C.endpoint || window.__mapfrontChat) { return; }
  window.__mapfrontChat = true;

  var d = document;
  var turns = [];            // [{role: "user"|"assistant", text}], last 6 sent
  var busy = false;
  var MAX = 500;

  var css =
    ".mfc,.mfc *{box-sizing:border-box;font-family:system-ui,-apple-system,sans-serif}" +
    ".mfc-bubble{position:fixed;right:16px;bottom:16px;z-index:60;display:flex;" +
    "flex-direction:column;align-items:flex-start;min-height:48px;padding:.55rem 1rem;" +
    "border:0;border-radius:1.5rem;cursor:pointer;font:600 1rem/1.15 system-ui,sans-serif;" +
    "background:var(--atlas-primary,#0f6e8c);color:var(--atlas-on-primary,#fff);" +
    "box-shadow:0 2px 10px rgba(0,0,0,.25)}" +
    ".mfc-bubble small{font-weight:400;font-size:.75rem;opacity:.9}" +
    ".mfc-bubble:focus-visible,.mfc button:focus-visible,.mfc a:focus-visible," +
    ".mfc input:focus-visible{outline:3px solid var(--atlas-text,#1b2430);outline-offset:2px}" +
    ".mfc-panel{position:fixed;right:16px;bottom:80px;z-index:61;width:min(23rem,calc(100vw - 32px));" +
    "max-height:min(34rem,calc(100vh - 110px));display:flex;flex-direction:column;" +
    "background:var(--atlas-bg,#fff);color:var(--atlas-text,#1b2430);border-radius:.75rem;" +
    "box-shadow:0 6px 24px rgba(0,0,0,.3);overflow:hidden}" +
    ".mfc-panel[hidden]{display:none}" +
    ".mfc-head{display:flex;align-items:center;justify-content:space-between;gap:.5rem;" +
    "padding:.6rem .8rem;background:var(--atlas-primary,#0f6e8c);color:var(--atlas-on-primary,#fff)}" +
    ".mfc-head h2{margin:0;font-size:1rem}" +
    ".mfc-close{min-width:44px;min-height:44px;border:0;background:transparent;color:inherit;" +
    "font-size:1.4rem;cursor:pointer}" +
    ".mfc-note{margin:0;padding:.5rem .8rem;font-size:.8rem;line-height:1.4;" +
    "border-bottom:1px solid rgba(0,0,0,.12)}" +
    ".mfc-quick{display:flex;flex-wrap:wrap;gap:.4rem;padding:.5rem .8rem}" +
    ".mfc-quick a,.mfc-quick button{display:inline-flex;align-items:center;min-height:40px;" +
    "padding:.3rem .7rem;border:1px solid var(--atlas-primary,#0f6e8c);border-radius:1rem;" +
    "background:transparent;color:inherit;font:inherit;font-size:.85rem;text-decoration:none;" +
    "cursor:pointer}" +
    ".mfc-log{flex:1;overflow-y:auto;padding:.5rem .8rem;display:flex;flex-direction:column;" +
    "gap:.45rem;min-height:5rem}" +
    ".mfc-msg{margin:0;padding:.45rem .65rem;border-radius:.6rem;max-width:88%;" +
    "line-height:1.4;font-size:.9rem;white-space:pre-wrap;overflow-wrap:anywhere}" +
    ".mfc-me{align-self:flex-end;background:var(--atlas-primary,#0f6e8c);" +
    "color:var(--atlas-on-primary,#fff)}" +
    ".mfc-ai{align-self:flex-start;background:rgba(0,0,0,.07)}" +
    ".mfc-form{display:flex;gap:.4rem;padding:.6rem .8rem;border-top:1px solid rgba(0,0,0,.12)}" +
    ".mfc-form input{flex:1;min-width:0;min-height:44px;padding:.4rem .6rem;font:inherit;" +
    "border:1px solid rgba(0,0,0,.35);border-radius:.4rem;background:#fff;color:#1b2430}" +
    ".mfc-form button{min-height:44px;padding:.4rem .9rem;border:0;border-radius:.4rem;" +
    "background:var(--atlas-primary,#0f6e8c);color:var(--atlas-on-primary,#fff);font:600 .95rem system-ui,sans-serif;cursor:pointer}" +
    ".mfc-form button[disabled]{opacity:.6;cursor:wait}" +
    ".mfc-sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}" +
    "@media (max-width:640px){.mfc-bubble{bottom:calc(5rem + env(safe-area-inset-bottom))}" +
    ".mfc-panel{right:8px;left:8px;width:auto;bottom:calc(9rem + env(safe-area-inset-bottom));" +
    "max-height:calc(100vh - 11rem)}}" +
    "@media (prefers-reduced-motion:no-preference){.mfc-panel{transition:opacity .15s}}";

  function el(tag, attrs, text) {
    var node = d.createElement(tag);
    var k;
    for (k in attrs || {}) {
      if (Object.prototype.hasOwnProperty.call(attrs, k)) { node.setAttribute(k, attrs[k]); }
    }
    if (text) { node.textContent = text; }
    return node;
  }

  function contactLine() {
    var ways = [];
    if (C.phone) { ways.push("call " + C.phone); }
    if (C.email) { ways.push("email " + C.email); }
    return ways.length ? ways.join(" or ") : "use the contact page";
  }

  var style = el("style", {"data-mapfront": "chat"});
  style.textContent = css;
  d.head.appendChild(style);

  var root = el("div", {"class": "mfc", "data-mapfront": "chat"});
  var bubble = el("button", {"class": "mfc-bubble", "type": "button",
    "aria-expanded": "false", "aria-controls": "mfc-panel"});
  bubble.appendChild(el("span", null, "Questions?"));
  bubble.appendChild(el("small", null, "AI assistant"));

  var panel = el("div", {"class": "mfc-panel", "id": "mfc-panel", "role": "dialog",
    "aria-modal": "false", "aria-labelledby": "mfc-title"});
  panel.hidden = true;
  var head = el("div", {"class": "mfc-head"});
  head.appendChild(el("h2", {"id": "mfc-title"}, "AI assistant"));
  var close = el("button", {"class": "mfc-close", "type": "button",
    "aria-label": "Close the chat"}, "×");
  head.appendChild(close);
  panel.appendChild(head);
  panel.appendChild(el("p", {"class": "mfc-note"},
    "I'm an AI assistant. Answers come from " + C.name + "'s own info; for " +
    "anything else, " + contactLine() + ". Please don't type card, bank, ID or " +
    "health details here."));

  var quick = el("div", {"class": "mfc-quick", "role": "group",
    "aria-label": "Contact " + C.name + " directly"});
  if (C.phone && C.tel) { quick.appendChild(el("a", {"href": "tel:" + C.tel}, "Call " + C.phone)); }
  if (C.email) { quick.appendChild(el("a", {"href": "mailto:" + C.email}, "Email")); }
  if (C.booking) { quick.appendChild(el("a", {"href": C.booking, "rel": "noopener"}, "Book online")); }
  var hoursBtn = null;
  if (C.hours && C.hours.length) {
    hoursBtn = el("button", {"type": "button"}, "Opening hours");
    quick.appendChild(hoursBtn);
  }
  if (quick.childNodes.length) { panel.appendChild(quick); }

  var log = el("div", {"class": "mfc-log", "role": "log", "aria-live": "polite",
    "aria-relevant": "additions", "tabindex": "0", "aria-label": "Conversation"});
  panel.appendChild(log);

  var form = el("form", {"class": "mfc-form"});
  form.appendChild(el("label", {"class": "mfc-sr", "for": "mfc-input"}, "Your question"));
  var input = el("input", {"id": "mfc-input", "type": "text", "maxlength": String(MAX),
    "autocomplete": "off", "placeholder": "Ask about " + C.name});
  var send = el("button", {"type": "submit"}, "Send");
  form.appendChild(input);
  form.appendChild(send);
  panel.appendChild(form);

  root.appendChild(panel);
  root.appendChild(bubble);
  d.body.appendChild(root);

  function say(text, mine) {
    var p = el("p", {"class": "mfc-msg " + (mine ? "mfc-me" : "mfc-ai")});
    if (!mine) { p.appendChild(el("span", {"class": "mfc-sr"}, "Assistant: ")); }
    p.appendChild(d.createTextNode(text));
    log.appendChild(p);
    log.scrollTop = log.scrollHeight;
  }

  function open() {
    panel.hidden = false;
    bubble.setAttribute("aria-expanded", "true");
    input.focus();
  }

  function shut() {
    panel.hidden = true;
    bubble.setAttribute("aria-expanded", "false");
    bubble.focus();
  }

  bubble.addEventListener("click", function () { if (panel.hidden) { open(); } else { shut(); } });
  close.addEventListener("click", shut);
  panel.addEventListener("keydown", function (e) {
    if (e.key === "Escape" || e.key === "Esc") { e.preventDefault(); shut(); }
  });
  if (hoursBtn) {
    hoursBtn.addEventListener("click", function () {
      say(C.name + "'s opening hours: " + C.hours.join("; ") + ".", false);
    });
  }

  function resting(status) {
    if (status === 429) {
      return "That's a lot of questions at once. Please wait a minute, or " + contactLine() + ".";
    }
    return "Chat is resting right now. Please " + contactLine() + ".";
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var text = (input.value || "").replace(/\s+/g, " ").trim().slice(0, MAX);
    if (!text || busy) { return; }
    busy = true;
    send.disabled = true;
    input.value = "";
    say(text, true);
    var body = JSON.stringify({message: text, history: turns.slice(-6)});
    fetch(C.endpoint, {method: "POST", mode: "cors", credentials: "omit",
      cache: "no-store", referrerPolicy: "no-referrer",
      headers: {"Content-Type": "application/json"}, body: body})
      .then(function (r) {
        return r.json().then(function (j) { return {status: r.status, json: j}; },
          function () { return {status: r.status, json: null}; });
      })
      .then(function (res) {
        var reply = res.json && typeof res.json.reply === "string" ? res.json.reply : "";
        if (res.status === 200 && reply) {
          say(reply, false);
          // A refused message (it carried a card or ID number) is never kept,
          // so it is not sent back with the next question.
          if (res.json.status === "ok") {
            turns.push({role: "user", text: text});
            turns.push({role: "assistant", text: reply.slice(0, MAX)});
            turns = turns.slice(-6);
          }
        } else {
          say(resting(res.status), false);
        }
      }, function () { say(resting(0), false); })
      .then(function () { busy = false; send.disabled = false; input.focus(); });
  });
}());
