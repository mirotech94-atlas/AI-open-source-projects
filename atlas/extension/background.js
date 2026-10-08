/**
 * ATLAS browser collector — service worker.
 *
 * Collects three things and nothing else: page visits, time spent on the
 * focused tab, and how many tabs are open. Everything is filtered through the
 * deny-list before it is written to the local buffer, so a denied page is
 * never stored even locally.
 */
import { settings, save, domainOf, isDenied } from "./config.js";

const BUFFER_KEY = "buffer";
const MAX_BUFFER = 2000;

// --------------------------------------------------------------- dwell time

let focused = null;   // { tabId, url, domain, since }

async function closeDwell(now = Date.now()) {
  if (!focused) return;
  const ms = now - focused.since;
  const current = focused;
  focused = null;
  if (ms < 1000) return;               // ignore flicks through tabs
  await record({
    kind: "dwell",
    domain: current.domain,
    url: current.url,
    dwell_ms: ms,
    at: new Date(current.since).toISOString(),
  });
}

async function openDwell(tab) {
  const config = await settings();
  if (config.paused || !tab?.url || isDenied(tab.url, config.denylist)) {
    focused = null;
    return;
  }
  focused = {
    tabId: tab.id,
    url: tab.url,
    domain: domainOf(tab.url),
    since: Date.now(),
  };
}

chrome.tabs.onActivated.addListener(async ({ tabId }) => {
  await closeDwell();
  try {
    await openDwell(await chrome.tabs.get(tabId));
  } catch { /* tab vanished */ }
});

chrome.tabs.onUpdated.addListener(async (tabId, change, tab) => {
  if (!change.url) return;
  if (focused?.tabId === tabId) await closeDwell();
  if (tab.active) await openDwell(tab);
});

chrome.windows.onFocusChanged.addListener(async (windowId) => {
  if (windowId === chrome.windows.WINDOW_ID_NONE) {
    await closeDwell();                // browser lost focus: stop the clock
    return;
  }
  const [tab] = await chrome.tabs.query({ active: true, windowId });
  if (tab) await openDwell(tab);
});

// Going idle must stop the clock, or an unattended laptop reports hours of
// "reading" on whatever was left open.
chrome.idle.setDetectionInterval(60);
chrome.idle.onStateChanged.addListener(async (state) => {
  if (state === "active") {
    const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    if (tab) await openDwell(tab);
  } else {
    await closeDwell();
  }
});

// ------------------------------------------------------------------ visits

chrome.history.onVisited.addListener(async (item) => {
  const config = await settings();
  if (config.paused || !item.url || isDenied(item.url, config.denylist)) return;
  await record({
    kind: "visit",
    domain: domainOf(item.url),
    url: item.url,
    title: item.title || "",
    at: new Date(item.lastVisitTime || Date.now()).toISOString(),
  });
});

// -------------------------------------------------------------- tab counts

async function sampleTabs() {
  const config = await settings();
  if (config.paused) return;
  const tabs = await chrome.tabs.query({});
  await record({
    kind: "tabs",
    domain: "-",
    tab_count: tabs.length,
    at: new Date().toISOString(),
  });
}

// ------------------------------------------------------------------ buffer

async function record(event) {
  const config = await settings();
  if (config.paused) return;
  if (!config.sendUrls) {
    delete event.url;
    delete event.title;
  }
  const { [BUFFER_KEY]: buffer = [] } = await chrome.storage.local.get(BUFFER_KEY);
  buffer.push(event);
  // Drop the OLDEST if the buffer overflows: an offline laptop should not grow
  // without bound, and recent activity is the more useful half.
  if (buffer.length > MAX_BUFFER) buffer.splice(0, buffer.length - MAX_BUFFER);
  await chrome.storage.local.set({ [BUFFER_KEY]: buffer });
  await updateBadge();
}

async function flush() {
  const config = await settings();
  if (config.paused || !config.endpoint || !config.token) return;

  await closeDwell();                  // bank the in-progress dwell first
  const { [BUFFER_KEY]: buffer = [] } = await chrome.storage.local.get(BUFFER_KEY);
  if (!buffer.length) return;

  const batch = buffer.slice(0, 500);
  let response;
  try {
    response = await fetch(config.endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${config.token}`,
      },
      body: JSON.stringify({ events: batch }),
    });
  } catch (error) {
    // Mesh down. Keep the buffer and try again on the next alarm.
    await save({ lastError: `unreachable: ${error.message}`, lastTry: Date.now() });
    return;
  }

  if (!response.ok) {
    await save({
      lastError: `HTTP ${response.status}`,
      lastTry: Date.now(),
    });
    return;                            // 401 must not silently discard data
  }

  await chrome.storage.local.set({ [BUFFER_KEY]: buffer.slice(batch.length) });
  await save({ lastError: "", lastSent: Date.now(), sentTotal:
    ((await settings()).sentTotal || 0) + batch.length });
  await updateBadge();
}

// ----------------------------------------------------- local 90-day expiry

async function expireLocal() {
  const config = await settings();
  const cutoff = Date.now() - config.localRetentionDays * 86400000;
  const { [BUFFER_KEY]: buffer = [] } = await chrome.storage.local.get(BUFFER_KEY);
  const kept = buffer.filter((e) => Date.parse(e.at) >= cutoff);
  if (kept.length !== buffer.length) {
    await chrome.storage.local.set({ [BUFFER_KEY]: kept });
  }
}

// ------------------------------------------------------------------ badge

async function updateBadge() {
  const config = await settings();
  const { [BUFFER_KEY]: buffer = [] } = await chrome.storage.local.get(BUFFER_KEY);
  if (config.paused) {
    await chrome.action.setBadgeText({ text: "❚❚" });
    await chrome.action.setBadgeBackgroundColor({ color: "#b45309" });
    return;
  }
  await chrome.action.setBadgeText({ text: buffer.length ? String(buffer.length) : "" });
  await chrome.action.setBadgeBackgroundColor({ color: "#1f6feb" });
}

// ----------------------------------------------------------------- alarms

chrome.runtime.onInstalled.addListener(async () => {
  const config = await settings();
  chrome.alarms.create("flush", { periodInMinutes: config.flushMinutes });
  chrome.alarms.create("tabs", { periodInMinutes: 15 });
  chrome.alarms.create("expire", { periodInMinutes: 720 });
  await updateBadge();
});

chrome.runtime.onStartup.addListener(updateBadge);

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === "flush") await flush();
  if (alarm.name === "tabs") await sampleTabs();
  if (alarm.name === "expire") await expireLocal();
});

chrome.runtime.onMessage.addListener((message, _sender, reply) => {
  (async () => {
    if (message?.type === "flush") { await flush(); reply({ ok: true }); return; }
    if (message?.type === "pause") {
      await closeDwell();
      await save({ paused: !!message.value });
      await updateBadge();
      reply({ ok: true });
      return;
    }
    if (message?.type === "purge") {
      await chrome.storage.local.set({ [BUFFER_KEY]: [] });
      await updateBadge();
      reply({ ok: true });
      return;
    }
    reply({ ok: false });
  })();
  return true;                          // keep the channel open for async reply
});
