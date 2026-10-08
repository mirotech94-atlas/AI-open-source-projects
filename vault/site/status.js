/* The live half of /status.html.
 *
 * Deliberately tiny and deliberately client-side. Asking the browser to
 * check means the answer describes the reader's own path to the API rather
 * than the server's opinion of itself, which is the only part of a
 * same-machine status page that is worth anything.
 *
 * It reports three states and never collapses them. "Answering" and "not
 * answering" are measurements; "could not check" is the absence of one, and
 * rendering that as trouble would cry wolf at every captive portal and
 * offline laptop. Unknown is not down, the same rule the product applies to
 * its own coverage.
 */
(function () {
  var HEALTH = "https://api.guarder.io/v1/health";
  var line = document.getElementById("status-line");
  var checked = document.getElementById("status-checked");
  if (!line) return;

  function show(cls, text) {
    line.className = "status-line " + cls;
    line.textContent = text;
    if (checked) {
      checked.textContent = "Checked " + new Date().toLocaleString() + ", from this browser.";
    }
  }

  // A hung request is its own answer: a hub that never replies is not up.
  var controller = typeof AbortController === "function" ? new AbortController() : null;
  var timer = setTimeout(function () { if (controller) controller.abort(); }, 8000);

  fetch(HEALTH, { cache: "no-store", signal: controller ? controller.signal : undefined })
    .then(function (r) {
      clearTimeout(timer);
      if (r.ok) {
        show("status-ok", "The hub and API are answering.");
      } else {
        show("status-bad", "The API answered with an error (HTTP " + r.status + ").");
      }
    })
    .catch(function () {
      clearTimeout(timer);
      // Could be the hub, could be this browser's network. Say both rather
      // than pick the alarming one.
      show("status-bad",
        "No answer from the API. Either the hub is down, or this browser cannot reach it.");
    });
})();
