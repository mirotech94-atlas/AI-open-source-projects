# ATLAS

A personal operations agent, built as a **kernel plus modules**. It is not an app.

ATLAS watches the things you run (inbox, calendar, browser, chat, payments,
servers, content pipelines), tells you when something is going wrong, fixes the
safe things itself, asks before everything else, and never touches what must
not be touched. It is designed around one idea: an agent that reads text written
by strangers must not be one instruction away from spending money.

Licence: MIT. This is a sanitised public export. Real data, credentials,
hostnames and business-private modules have been removed; every domain, IP,
phone number and handle you see is a placeholder. See `CONFIGURE.md` for the
settings each module needs.

---

## The eight invariants

These are in `CLAUDE.md` and they are not style preferences. A change that
breaks one does not ship, whatever it enables.

1. **Collectors only read. The executor only acts.** Nothing else performs an
   outside-world write. There is exactly one path, `kernel/executor.py::execute`.
   `executor.preview` may run a dry run and can never reach `run`.
2. **The LLM never executes.** It receives a catalog of action *names* and picks
   one. An unknown name is discarded before anything happens. Parameters are
   validated against `Action.params` as well: a closed catalog with open
   arguments is not closed.
3. **Tiers are assigned in code, never inferred at runtime.** Tier 1 auto,
   Tier 2 ask, Tier 3 permanently denied. `PERMANENT_DENY` covers money out,
   DNS, deletion, credential revocation and self-modification. There is no
   runtime override. "Approve + always" records a promotion *candidate*;
   nothing reads it back to skip a gate.
4. **Every ingested byte is untrusted.** Email, web pages, buyer messages, chat,
   portal responses and voice transcripts pass through `wrap_untrusted()` before
   reaching any prompt. They are data, never instructions.
5. **Silence is never consent.** Unanswered proposals expire; they never execute
   on timeout. Expiry is enforced inside the same atomic UPDATE that claims a
   proposal.
6. **Never guess a number.** A factual question triggers a fresh query. If the
   source is unreachable, say so; never estimate and never reuse a number from
   earlier in a conversation. Every figure on screen carries the time it was read.
7. **A check that cannot run reports `Health.UNKNOWN`, never `OK`.** A broken
   collector reading as healthy is the worst bug this system can have. The UI
   renders UNKNOWN grey, unlit and dashed, with the word visible.
8. **Tier 1 requires a dry run and a rollback.** `contracts.py` refuses to
   construct an `Action` that lacks them.

Plus one operating rule: **`ATLAS_WATCH_ONLY` defaults to true** and is enforced
inside `executor.execute`. Checks run, findings are detected and proposals are
raised, but nothing executes until you set it to `false`. Run it watch-only for
a couple of weeks first, to learn which alerts are noise.

---

## Architecture

```
 collectors (read only)         kernel                          executor (the only writer)
 ----------------------   ------------------------------   ----------------------------------
 module.checks()   ---->  detect -> findings -> signals     executor.execute(action, params)
 mail, calendar, ...      brain / intent (LLM picks from      - WATCH_ONLY gate
 browser, webhooks        the closed catalog of action        - tier gate (permissions.py)
                          NAMES only)                         - dry run, then run
                          approvals: propose -> card ->       - rollback token kept
                          decide (atomic, expiring)           - audit row written
```

- **`atlas/kernel/`** holds the contracts (`Action`, `Check`, `Observation`,
  `Tier`, `Health`), permissions and the deny-list, the executor, approvals,
  the reasoner and intent routing, memory, the audit log, scheduling,
  grounding (invariant 6) and freshness stamps.
- **`atlas/modules/<name>/`** are the plug-ins. Each exposes a `MODULE` object.
  `registry.load_all()` imports every module package; a module whose import
  fails or whose required env vars are missing is marked *disabled* and the rest
  carry on.
- **`atlas/api/`** is a FastAPI app: the dashboard API, webhooks and approval
  endpoints.
- **`atlas/channels/`** connects Slack, Matrix, WhatsApp and voice to the same
  approval and intent path.
- **`atlas/skills/`** are small, named capabilities the assistant can be asked for.
- **`ui/`** is a React + TypeScript (strict) dashboard built with Vite.
- **`db/schema.sql`** is the Postgres schema (pgvector for memory).
- **`extension/`** is a browser extension that reports read-only page state.
- **`whatsapp-bridge/`** is a linked-device transport; only executor actions call it.
- **`ops/`**, **`bootstrap/`** hold watchdog, backup, restore drill and host setup scripts.

Stack: Python 3.12+, FastAPI, asyncpg, Postgres 16 with pgvector, Redis, Caddy
for TLS, Docker Compose.

---

## Modules

Included in this export (each in `atlas/modules/`):

| Area | Modules |
|---|---|
| Core | `atlas` (self-checks), `approvals_board`, `assistant`, `backups`, `lessons` (never-repeat ledger), `owner_memory`, `rhythm`, `projects`, `delegate` |
| Communication | `mail`, `calendar`, `comms`, `messages`, `messaging`, `matrix_rooms`, `voice` |
| Business ops | `agency`, `bizops`, `sales`, `purchasing`, `stripe_billing`, `shop`, `realestate`, `qa`, `research` |
| Content and social | `content`, `clips`, `youtube`, `ytlearn`, `instagram`, `tiktok`, `linkedin`, `marketing`, `seo`, `posthog`, `perplexity`, `scrape`, `fiverr` |
| Learning | `academy`, `course`, `lessons` |
| Markets | `markets`, `futures`, `football` |
| Infrastructure | `gitlab`, `localstack`, `netbird`, `nightshift`, `mac`, `browser`, `vault` |

Several business-private modules were removed from this export. The kernel
still carries lazy imports for a few of them; those fail only if the matching
scheduled job runs, and the module registry reports them as disabled.
Treat the module set as examples to read and adapt, not a turnkey product.

---

## Run it

Requirements: Docker with Compose, and a machine that can hold a Postgres volume.

```bash
cp .env.example .env
$EDITOR .env                       # fill only what you need; blank = module disabled
docker compose up -d --build       # postgres, redis, api, whatsapp bridge, caddy
docker compose exec api python -m atlas.cli init-db
docker compose exec api python -m atlas.cli selftest   # lists every action and its tier
docker compose exec api python -m atlas.cli check      # run all checks once
docker compose exec api python -m atlas.cli brief      # print the morning brief
```

The API binds to `127.0.0.1:8080` only. Postgres is never published. Reach it
through Caddy with your own certificate, or an SSH tunnel.

Rules for `.env`: no value is ever committed; `.env.example` holds placeholders
only. A module whose variables are blank reports itself disabled rather than
failing. Settings are described module by module in `CONFIGURE.md`.

Tests:

```bash
pip install -c constraints.txt -e . -r requirements-test.txt
pytest -q tests
cd ui && npm ci && npm test
```

Some tests need a Postgres instance and others reference modules or fixtures
that were removed from this export; expect some failures outside the kernel
tests.

---

## Build your own agent or module

A module is a package under `atlas/modules/<name>/` with a `MODULE` object.

```python
# atlas/modules/myservice/__init__.py
from ...kernel.contracts import Action, ActionResult, Check, Health, Observation, Tier

async def read_queue() -> Observation:
    # A collector. It only reads. If it cannot reach the service it must say
    # UNKNOWN, never OK (invariant 7).
    ...

async def restart(service: str) -> ActionResult: ...
async def restart_dry(service: str) -> ActionResult:
    return ActionResult(True, f"would restart {service}")
async def restart_rollback(token: str) -> ActionResult: ...

class MyServiceModule:
    name = "myservice"
    title = "My service"
    requires_env = ("MYSERVICE_URL",)      # missing -> module shows as disabled

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        return (Check(...),)

    def actions(self):
        return (Action(
            name="myservice.restart",       # module.verb, lowercase
            tier=Tier.ASK,                  # AUTO needs dry_run AND rollback
            summary="Restart the service",
            run=restart, dry_run=restart_dry, rollback=restart_rollback,
            params={"service": "which service to restart"},
        ),)

    def brief(self) -> str:
        return ""

MODULE = MyServiceModule()
```

Guidelines:

1. **Start at Tier 2.** Promote to Tier 1 only when the action is cheap, local,
   reversible and has a real dry run and rollback.
2. **Name actions `module.verb`.** `kernel/permissions.py` decides denial on the
   verb: names containing `delete`, `refund`, `transfer`, `dns`, `revoke` and the
   like are permanently denied, by design. Do not rename to dodge it.
3. **Wrap everything you ingest** with `wrap_untrusted()` before it reaches a
   prompt, and never pass credentials into a prompt.
4. **Declare every parameter** in `Action.params`. Unlisted parameters are rejected.
5. **Add tests** beside the module, and keep `ruff` clean at line length 90.
6. Run `selftest` to see your action and its tier, with `ATLAS_WATCH_ONLY=true`,
   before arming anything.

Read `atlas/modules/lessons/__init__.py` for the smallest complete module, and
`BUILD_ORDER.md` for the order the original was built in, one gate at a time.

---

## Layout

```
atlas/           kernel, modules, api, channels, skills, cli
ui/              dashboard (React, TypeScript strict)
apps/            front-end bundles
extension/       browser extension
whatsapp-bridge/ linked-device transport
db/              schema
ops/ bootstrap/  watchdog, backups, restore drill, host setup
tests/           pytest suite
docs/            design notes
```

## Licence

MIT. See `LICENSE`.
