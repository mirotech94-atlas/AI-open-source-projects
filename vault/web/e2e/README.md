# Playwright smoke suite

One end-to-end walk through the phase-2 dashboard, run three times — light
desktop, dark desktop, and a 375×812 mobile viewport (`playwright.config.ts`'s
three projects).

## Running it

```bash
cd web
npm run build            # the hub serves web/dist; the suite fails without a build
npx playwright test       # runs all three projects, headless
npx playwright test --headed --project=light   # one project, visible browser
npx playwright show-report                      # after a run, open the HTML report
```

By default the suite starts its own hub: a fresh temp sqlite DB, migrated
and seeded with `guardian seed-demo`, served by `uvicorn` on
`127.0.0.1:8800` (see `webServer` in `playwright.config.ts`). That needs
nothing beyond the repo's own `hub/.venv` already existing — no server to
start by hand, no env vars to set.

To point the suite at an already-running hub instead (e.g. to smoke a
deployed instance), set `E2E_BASE_URL` and the suite skips starting its own
server:

```bash
E2E_BASE_URL=https://my.guarder.io npx playwright test
```

That hub must already have the `demo`/`demoadmin` accounts from
`guardian seed-demo` — the suite signs in as `demoadmin` (see below) and
does not seed anything itself when `E2E_BASE_URL` is set.

## What it covers (`smoke.spec.ts`)

Signs in, then walks every phase-2 tab as the seeded demo admin:

- **Overview** — the score hero (`VerdictHero`'s "Security score" label) and
  the 24-hour health strip (`data-testid="hour-block"`, one full row of 24
  per monitored site/host).
- **Inbox** — rows render, and clicking **Ack** on the first row both
  returns a 2xx from `POST /api/v1/alerts/{key}/ack` and is confirmed
  server-side by re-fetching `/api/v1/inbox` and checking the row's
  `status` actually flipped to `"acked"` (not just the optimistic UI
  patch).
- **Assets** — the device-kind chips render, clicking a device opens the
  `/assets/devices/:mac` drawer, and the screen-time heatmap renders.
- **⌘K / Ctrl-K** — the command palette opens, a text search finds a real
  seeded device, and Escape closes it.
- **Theme toggle** — clicking it flips `document.documentElement.dataset.theme`.
- **Sidebar collapse** — desktop projects only (the sidebar is replaced by
  bottom tabs under 700px, so this step is skipped on `mobile`).

## Accounts

`guardian seed-demo` creates two demo accounts sharing the tenant's one
fixed sign-in code (`424242`, `Tenant.settings["demo_code"]`,
username-agnostic in `routers.auth.verify`):

- `demo` — **viewer** role. Inbox rows carry no action buttons at all for a
  viewer (`payloads/inbox.py`'s `_alert_actions` gates ack/snooze/resolve/
  reopen at "operator"+), so this account can't exercise the ack flow.
- `demoadmin` — **admin** role. `fixtures.ts`'s `loginAsDemo()` signs in as
  this one, so the whole suite (including the ack assertion) has the
  actions it needs.

## Projects and the `dark` skin

The app's dark palette only activates via an explicit
`data-theme="dark"` on `<html>` (see `src/lib/theme.ts`) —
`prefers-color-scheme: dark` alone only drives the *system* default. The
`dark` project's Chromium context sets `colorScheme: "dark"`, and
`fixtures.ts`'s extended `page` fixture adds an init script that seeds
`localStorage["guardian.theme"] = "dark"` before every page load in that
project, so the pre-paint `themeBoot.ts` script stamps `data-theme="dark"`
the same way a returning user with a persisted dark preference would see
it.

## Debugging a failure

- `npx playwright show-trace test-results/.../trace.zip` — full trace
  (DOM snapshots, network, console) for a failed run.
- Screenshots for failed steps land under `test-results/` automatically
  (`screenshot: "only-on-failure"` in the config).
- The suite's own temp DB is a fresh `/tmp/guardian-e2e-<pid>-<ts>.db` per
  run — deleting it is safe, and a new one is created on the next run.
