import { fileURLToPath } from "node:url";
import { defineConfig, devices } from "@playwright/test";

// Local, ephemeral hub for the smoke suite: a temp sqlite DB seeded with
// `guardian seed-demo`, migrated fresh on every run, served by uvicorn on
// port 8800. `E2E_BASE_URL` lets a run point at an already-running hub
// (e.g. to smoke a deployed instance) instead of starting one.
const BASE_URL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:8800";
const STARTS_OWN_SERVER = !process.env.E2E_BASE_URL;

const REPO_ROOT = fileURLToPath(new URL("..", import.meta.url));
const HUB_DIR = `${REPO_ROOT}hub`;
// A fresh temp DB per config load keeps re-runs (and CI) from tripping over
// a stale/partially-migrated file from a previous, differently-shaped run.
const DB_PATH = `/tmp/guardian-e2e-${process.pid}-${Date.now()}.db`;
const DATABASE_URL = `sqlite:///${DB_PATH}`;
// The hub writes built reports under `GUARDIAN_DATA_DIR/reports`, and
// `config.py` defaults that to `./data` — which, since `webServer` cd's into
// `hub/`, means a smoke run that builds a report would drop HTML into the
// repo's own `hub/data/`. Point it at a temp dir instead: the run stays
// self-contained and the working tree stays clean.
const DATA_DIR = `/tmp/guardian-e2e-data-${process.pid}-${Date.now()}`;

const HUB_ENV = {
  ...process.env,
  GUARDIAN_DATABASE_URL: DATABASE_URL,
  GUARDIAN_DATA_DIR: DATA_DIR,
  GUARDIAN_WEB_DIST: `${REPO_ROOT}web/dist`,
  GUARDIAN_DEV_CONSOLE_OTP: "1",
  GUARDIAN_BASE_URL: BASE_URL,
};

// One command: migrate the temp DB, seed the demo tenant, then serve. All
// three run inside the hub's own venv so this needs nothing beyond `hub/
// .venv` already existing (per the repo's own dev setup).
const WEB_SERVER_COMMAND =
  `bash -c 'cd "${HUB_DIR}" && ` +
  `. .venv/bin/activate && ` +
  `guardian migrate && guardian seed-demo && ` +
  `uvicorn guardian_hub.main:app --host 127.0.0.1 --port 8800'`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: [["list"]],
  timeout: 30_000,
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "light",
      use: {
        ...devices["Desktop Chrome"],
        colorScheme: "light",
      },
    },
    {
      name: "dark",
      use: {
        ...devices["Desktop Chrome"],
        colorScheme: "dark",
      },
      // The app's dark palette only activates via an explicit
      // `data-theme="dark"` (see lib/theme.ts) — `prefers-color-scheme`
      // alone drives the *default* ("system") look. `fixtures.ts`'s `page`
      // fixture adds an init script that seeds `localStorage["guardian.theme"]
      // = "dark"` for this project, so `themeBoot.ts`'s pre-paint script
      // stamps `data-theme="dark"` before the app even mounts.
    },
    {
      name: "mobile",
      use: {
        viewport: { width: 375, height: 812 },
        userAgent: devices["Desktop Chrome"].userAgent,
        colorScheme: "light",
      },
    },
  ],
  webServer: STARTS_OWN_SERVER
    ? {
        command: WEB_SERVER_COMMAND,
        url: `${BASE_URL}/api/v1/health`,
        reuseExistingServer: false,
        timeout: 60_000,
        env: HUB_ENV,
      }
    : undefined,
});
