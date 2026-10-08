import { settings, save, DEFAULTS } from "./config.js";

const $ = (id) => document.getElementById(id);

async function load() {
  const config = await settings();
  $("endpoint").value = config.endpoint || "";
  $("token").value = config.token || "";
  $("sendUrls").checked = config.sendUrls !== false;
  $("localRetentionDays").value = String(
    config.localRetentionDays ?? DEFAULTS.localRetentionDays);
  $("denylist").value = (config.denylist || []).join("\n");
}

$("save").addEventListener("click", async () => {
  const days = parseInt($("localRetentionDays").value, 10);
  await save({
    endpoint: $("endpoint").value.trim(),
    token: $("token").value.trim(),
    sendUrls: $("sendUrls").checked,
    // A zero would mean "throw everything away immediately", which is far more
    // likely to be a typo than an intention.
    localRetentionDays: Number.isFinite(days) && days >= 1
      ? days : DEFAULTS.localRetentionDays,
    denylist: $("denylist").value
      .split("\n").map((line) => line.trim()).filter(Boolean),
  });
  $("saved").classList.add("on");
  setTimeout(() => $("saved").classList.remove("on"), 1400);
});

load();
