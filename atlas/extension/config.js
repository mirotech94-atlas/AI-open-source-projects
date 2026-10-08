/**
 * Shared configuration and storage helpers.
 *
 * Two rules this file exists to enforce:
 *
 *  1. The deny-list never leaves the browser. It is read here, applied here,
 *     and is never included in a payload. The server deliberately has no copy
 *     to keep in sync — a second copy is a second thing that can leak.
 *  2. Nothing is sent anywhere except the one endpoint configured by hand.
 *     There is no default endpoint and no telemetry.
 */

export const DEFAULTS = {
  endpoint: "",           // e.g. http://100.x.y.z:8080/ingest/browser
  token: "",
  paused: false,
  sendUrls: true,         // off = domains and dwell only, never a full URL
  denylist: [
    // Seeded with the categories most people regret collecting. Editable, and
    // matched on the registrable domain and any subdomain of it.
    "bank", "chase.com", "paypal.com",
    "health", "nhs.uk",
    "pornhub.com", "xvideos.com",
    "mail.google.com", "web.whatsapp.com",
  ],
  flushMinutes: 5,
  localRetentionDays: 90,
};

export async function settings() {
  const stored = await chrome.storage.local.get(Object.keys(DEFAULTS));
  return { ...DEFAULTS, ...stored };
}

export async function save(patch) {
  await chrome.storage.local.set(patch);
}

/** The registrable-ish domain. Good enough for matching, no network calls. */
export function domainOf(url) {
  try {
    const host = new URL(url).hostname.toLowerCase();
    return host.startsWith("www.") ? host.slice(4) : host;
  } catch {
    return "";
  }
}

/**
 * True when this URL must never be recorded.
 *
 * A entry matches if the domain equals it, is a subdomain of it, or — for
 * bare words like "bank" — if the domain contains it as a label. Deliberately
 * generous: a false positive loses one data point, a false negative records
 * something Miro did not want recorded.
 */
export function isDenied(url, denylist) {
  const domain = domainOf(url);
  if (!domain) return true;                 // unparseable: do not record
  const scheme = (() => { try { return new URL(url).protocol; } catch { return ""; } })();
  if (scheme !== "http:" && scheme !== "https:") return true;
  return denylist.some((raw) => {
    const entry = String(raw).trim().toLowerCase();
    if (!entry) return false;
    if (entry.includes(".")) {
      return domain === entry || domain.endsWith("." + entry);
    }
    return domain.split(".").some((label) => label.includes(entry));
  });
}
