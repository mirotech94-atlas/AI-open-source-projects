# Guardian

A network watchdog for small sites. An on-site **sensor** watches the LAN and
the things on it, a **hub** stores what it found and decides what is worth
saying, and people read it in the **app**. Everything Guardian runs lives in
this repository.

Live at **my.guarder.io** (customers), **admin.guarder.io** (the operator
console) and **guarder.io** (the public site).

## What is in here

| Path | What it is |
|---|---|
| `hub/` | The FastAPI service: API, database, alerting, billing, chat, integrations, tickets. SQLAlchemy 2 + Alembic. |
| `sensor/` | The agent that runs on the customer's network. Scans the LAN, probes sites and hosts, and pushes findings to the hub. It is the only thing that can reach a customer's gateway. |
| `web/` | The single React app serving BOTH portals: the customer dashboard and, on `admin.guarder.io`, the owner console. One bundle, gated by hostname and `is_superadmin`. |
| `site/` | The public marketing site, in eight languages. |
| `deploy/` | Deployment and the nginx/systemd/ACME setup scripts. |
| `docs/` | Specs and implementation plans. |

## Running it

```bash
cd hub && python3 -m venv .venv && .venv/bin/pip install -e . && .venv/bin/alembic upgrade head
cd web && npm install && npm run dev
```

The hub reads its configuration from `hub/.env`; nothing secret is committed.
`hub/scripts/check_types.py` enforces that `schemas_dash.py` and
`web/src/types.ts` agree, and CI-style checks are the two test suites:

```bash
cd hub && python3 -m pytest          # the hub
cd web && npx vitest run             # the app
```

## Deploying

`deploy/deploy.sh` builds the web bundle, rsyncs `hub/`, `sensor/` and the
build to the server, migrates, and restarts behind a health gate.

> **It rsyncs with `--delete`.** Deploying from a checkout that is missing a
> migration another branch has already applied will remove that file from the
> server and strand the database on a revision it can no longer resolve. Fetch
> and reconcile before deploying.
