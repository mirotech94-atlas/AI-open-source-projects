# ATLAS browser collector

Sends page visits, dwell time and open-tab counts to your own ATLAS instance
over the NetBird mesh. It talks to exactly one host — the one you configure —
and to nothing else.

## Install

1. `chrome://extensions` → enable **Developer mode** → **Load unpacked** →
   select this `extension/` directory.
2. Open the extension's options page and set:
   - **Ingest endpoint** — `https://atlas.storefront.test/ingest/browser`
     (over the mesh; the API itself stays on loopback and Caddy is the
     only door, so the traffic is TLS the whole way)
   - **Ingest token** — the `BROWSER_INGEST_TOKEN` from your ATLAS `.env`
3. Edit the deny-list before you leave the page. It ships with a starting set
   (banking, health, adult, webmail); it is meant to be edited.

Generate the token with:

```bash
head -c 32 /dev/urandom | base64
```

## The three things it promises

**The deny-list never leaves the browser.** Matching happens in
`config.js::isDenied` before anything is buffered, so a denied page is never
written to disk, never queued and never sent. ATLAS has no copy of the list —
a second copy would be a second thing that can leak. Run the tests with
`node extension/denylist.test.mjs`.

**One click to pause.** The toolbar button toggles collection and the badge
shows `❚❚` while paused. Pausing also closes the in-progress dwell timer, so
the pause does not get backfilled when you resume.

**Raw history is deleted after 90 days.** Server-side, `kernel/privacy.py` runs
hourly: it folds events into `browser_rollup` (one row per domain per day, no
URLs) and then deletes raw rows past the window. The roll-up runs *first* and
its failure aborts the delete, so the aggregate is never lost along with the
detail. Unsent events in the local buffer expire on the same schedule.

## What is actually collected

| Event | Fields |
| --- | --- |
| `visit` | domain, URL, title, timestamp |
| `dwell` | domain, URL, milliseconds focused |
| `tabs` | how many tabs are open |

Turn off **Include full URLs and page titles** and the URL and title are
dropped before the event reaches the buffer — ATLAS then sees only the domain,
the time and the duration.

Dwell stops when the browser loses focus and when the OS reports idle for 60
seconds, so an unattended laptop does not record hours of "reading".

## Why it cannot reach ATLAS from a café

`/ingest/browser` is not in the API's `PUBLIC_PATHS`, so it is refused by
source address for anything outside `MESH_ALLOWED_CIDRS`, and Caddy does not
proxy it on 443 at all. It also requires the bearer token: an address on the
mesh is not a credential, and anything else on the mesh could otherwise post
fabricated history.

If the endpoint works while you are off the mesh, something is misconfigured —
check `MESH_BIND` and the Caddyfile.

## Troubleshooting

The popup shows the buffered count, when it last sent, and the last error.

- **"Not configured"** — endpoint or token is blank.
- **`HTTP 401`** — the token does not match `BROWSER_INGEST_TOKEN`. The buffer
  is kept, not discarded.
- **`unreachable`** — the mesh is down. Events keep buffering (up to 2000, then
  the oldest are dropped) and flush when it returns.
