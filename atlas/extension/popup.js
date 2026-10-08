import { settings, DEFAULTS } from "./config.js";

const $ = (id) => document.getElementById(id);

function ago(ts) {
  if (!ts) return "never";
  const mins = Math.round((Date.now() - ts) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  return `${Math.round(mins / 60)}h ago`;
}

async function render() {
  const config = await settings();
  const { buffer = [] } = await chrome.storage.local.get("buffer");

  const toggle = $("toggle");
  toggle.textContent = config.paused ? "Paused — click to resume" : "Collecting — click to pause";
  toggle.classList.toggle("paused", !!config.paused);

  $("buffered").textContent = String(buffer.length);
  $("sent").textContent = ago(config.lastSent);
  $("error").textContent = config.lastError ? `Last attempt: ${config.lastError}` : "";

  if (!config.endpoint || !config.token) {
    $("error").textContent = "Not configured — set the endpoint and token.";
  }
}

$("toggle").addEventListener("click", async () => {
  const config = await settings();
  await chrome.runtime.sendMessage({ type: "pause", value: !config.paused });
  await render();
});

$("flush").addEventListener("click", async () => {
  await chrome.runtime.sendMessage({ type: "flush" });
  await render();
});

$("purge").addEventListener("click", async () => {
  if (!confirm("Discard everything buffered locally? It has not been sent.")) return;
  await chrome.runtime.sendMessage({ type: "purge" });
  await render();
});

$("options").addEventListener("click", (event) => {
  event.preventDefault();
  chrome.runtime.openOptionsPage();
});

void DEFAULTS;
render();
