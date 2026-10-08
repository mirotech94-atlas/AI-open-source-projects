/* Guardian's service worker, which deliberately caches nothing.
 *
 * It exists so the dashboard can be INSTALLED — Chrome will not offer to add
 * a site to the home screen unless a service worker with a fetch handler is
 * registered — and the plan's reason for wanting that is a specific one: the
 * best moment this product has is approving a repair from a phone at 2am
 * (trust plan §2.3, P2 #33). Two taps from a notification beats finding a
 * laptop.
 *
 * WHY IT CACHES NOTHING, which is the whole design and not laziness.
 *
 * Guardian is an instrument panel someone opens because something is broken.
 * A service worker that served a cached shell with cached data would show
 * yesterday's "all clear" to someone standing in front of a fire — and it
 * would look completely normal, because a cached page renders exactly like a
 * live one. For a watchdog that is the worst failure mode there is: the same
 * "unmeasured must never render as healthy" rule the score coverage gate and
 * the staleness flags exist to enforce, except here the lie would be served
 * by our own code.
 *
 * So every request goes to the network, and when the network is gone the
 * request fails and the app's own error states say so. An offline Guardian
 * that admits it is offline is worth more than one that quietly shows old
 * numbers.
 *
 * If caching is ever added, cache the SHELL ONLY — never `/api/`, never the
 * websocket — and make the app render an explicit "served from cache, last
 * updated X" banner before it draws a single figure.
 */
self.addEventListener("install", function () {
  // Take over immediately: a stale worker is the same hazard as stale data.
  self.skipWaiting();
});

self.addEventListener("activate", function (event) {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", function (event) {
  // Network only. Present so the app is installable, and pointedly not an
  // offline cache — see the note above before changing this.
  event.respondWith(fetch(event.request));
});
