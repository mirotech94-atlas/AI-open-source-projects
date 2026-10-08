# Site fonts

`../styles.css` declares `@font-face` for **Manrope** (UI text) and **IBM Plex
Mono** (numbers, code, the sensor install command), and expects the files in
this directory:

    Manrope-Regular.woff2
    Manrope-SemiBold.woff2
    Manrope-Bold.woff2
    IBMPlexMono-Regular.woff2

They are deliberately NOT fetched from a CDN: this is a sales page that has to
load on a slow link and keep working when a third party is down, and the app
next door has the same rule (a watchdog must render on an isolated network).

The files are not in the repo yet. Task 57 vendors the same two families for
the app under `web/public/fonts/`; `deploy/setup-site.sh` copies whatever it
finds there into this directory on its way to the server, best-effort. Until
that lands, every `@font-face` simply fails to load and the page renders on the
`system-ui` / `ui-monospace` fallback stacks named in `--font-sans` and
`--font-mono` — no layout shift, no blocking request, nothing to fix.
