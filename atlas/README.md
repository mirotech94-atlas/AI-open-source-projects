# ATLAS

A personal operations platform for Miro. Not an app — a kernel with modules.

It watches his inbox, calendar, browser, work chat and every business he runs;
tells him when something is going wrong; fixes the safe things itself; asks
before the rest; and answers the phone when he calls it.

**Host:** `atlas.example.com` (203.0.113.10) — private, mesh-only except webhooks.
**Repos:** `aicode.example.com` (business) · `code.example.org` (work)

---

## The rules

Not style preferences. Breaking one turns a helpful service into an outage
generator, or worse.

1. **Collectors only read. The executor only acts.** Nothing else touches the
   outside world.
2. **The LLM never executes.** It picks from a fixed catalog. It cannot invent
   an action and it cannot run a shell.
3. **Tiers are assigned in code**, never inferred at runtime.
   Tier 1 auto · Tier 2 ask · Tier 3 permanently denied.
4. **Every ingested byte is untrusted.** Email, web pages, buyer messages and
   chat are written by other people. They are data, never instructions.
5. **Silence is never consent.** Unanswered proposals expire.
6. **Never guess a number.** Query, or say you can't reach it. Conversation can
   be loose; data cannot.
7. **Phase 1 ships with zero actions.** Two weeks of watching before it gets
   hands.

---

## Shape

```
atlas/
├── kernel/          the OS. Knows nothing about any business.
│   ├── contracts.py   Module / Check / Action / Tier — the system call table
│   ├── registry.py    module discovery and lifecycle
│   ├── permissions.py tier enforcement + the permanent deny-list
│   ├── executor.py    the ONLY place an action runs
│   ├── audit.py       append-only, hash-chained log
│   ├── scheduler.py   the loop
│   ├── detect.py      rules first, rolling baselines second
│   ├── reason.py      the only place an LLM touches an incident
│   ├── llm.py         multi-provider routing + failover
│   ├── harness.py     the agent loop: memory in, skills in, tools out
│   ├── agents.py      the specialist roster
│   ├── skills.py      procedural knowledge, injected when it matches
│   ├── memory.py      episodic / semantic / procedural / working
│   ├── tools.py       read tools run; write tools inherit an action's tier
│   ├── approvals.py   proposals, expiry, "approve + always"
│   ├── comms.py       severity-based channel routing
│   └── state.py       Postgres
├── modules/         everything domain-specific. Drop-in.
│   ├── flycheap/  guardian/  fiverr/  linkedin/  mirotech/
│   ├── instagram/ tiktok/ youtube/
│   ├── inroots/     status.example.org — attendance, goals, team presence
│   ├── stripe_billing/  Guardian + flycheap payments
│   ├── gitlab/      aicode.example.com + code.example.org
│   ├── agency/      local businesses with a bad site: prospect, audit,
│   │                mockup, outreach (Tier 2), build, deploy (Tier 2)
│   ├── mail/ calendar/ messages/ messaging/ screening/   (personal layer:
│   │                follow-ups, focus guard, Instagram DMs,
│   │                Slack reading, the line ATLAS answers)
│   ├── research/ vault/ content/ school/  (reports, documents and reading,
│   │                drafts and renders, college through Brightspace)
│   ├── browser/     a headless browser that never signs in
│   ├── nightshift/  overnight builds in a sandbox; merging is Tier 2
│   ├── purchasing/  fares and carts prepared, never paid
│   ├── marketing/ lessons/ rhythm/ perplexity/   (experiments and the
│   │                playbook, never-repeat, his hours, the research cap)
│   └── netbird/ vpsie/             (infrastructure)
├── channels/        how ATLAS reaches Miro
│   ├── slack.py       primary
│   ├── matrix.py      example.org homeserver
│   ├── twilio.py      voice for critical (the phone line in api/phone.py)
│   └── gvoice.py      Google Voice, read-only via Gmail forwarding
├── bootstrap/       run once on the server
└── db/              schema + migrations
```

**Adding anything** — a new business, a data source, a capability — means
writing one module and dropping it in. The kernel does not change.

---

## Agents

Nine role specialists and one agent per business — seventeen in all. Each has
its own system prompt, scoped tools, memory namespaces and model tier. An
unclassifiable message goes to the concierge, because most of what Miro says
is conversation, not a query.

| Agent | Owns |
| --- | --- |
| **concierge** | default. Conversation, on-demand questions, handoff and relay |
| **ops** | infrastructure, incidents, remediation |
| **inbox** | email triage, drafting, commitments — runs on the *local* model |
| **growth** | LinkedIn, Instagram, TikTok, SEO, content |
| **commerce** | Fiverr, Mirotech, pricing, buyers |
| **scheduler** | calendar, bookings, meeting prep, coursework deadlines |
| **analyst** | numbers, correlation, recall, the money roll-up |
| **sentinel** | security posture, credentials, injection attempts |
| **marketing** | experiments measured against a 4-week baseline, the playbook, monetization ideas — publishing stays his word |

One agent per business (`kernel/businesses.py`): **flycheap**, **guardian**,
**inroots**, **vpsie**, **mirotech**, **fiverr**, **agency** and **school**
(on the local model: his coursework stays on the box). Each answers for its
own numbers only, every number with the time it was read.

Scoping is enforced in `tools.invoke`, not in the prompt. A prompt is a
request; a boundary is code.

---

## Models

ATLAS has a routing table, not "a model". Work declares what it *needs*; the
table decides who serves it.

| Need | Chain |
| --- | --- |
| `TRIVIAL` | local → Haiku → Hermes-70B |
| `PRIVATE` | **local only** — personal mail and browser history never leave the box |
| `DRAFT` | Sonnet → Haiku → Hermes |
| `REASON` | Sonnet → Opus → Hermes |
| `HARD` | Opus → Sonnet → Hermes |
| `CRITICAL` | Opus **and** a second provider — they must agree, or it escalates unresolved |

Hermes (Nous Research, via OpenRouter) is the open-weight failover: it is
trained for tool-calling and holds a system prompt tightly, which is what the
harness needs. A provider outage degrades quality instead of stopping ATLAS.

---

## Memory

Four kinds, because conflating them is what makes agent memory either useless
or unmanageably large.

| Kind | Holds | Lifetime |
| --- | --- | --- |
| `episodic` | what happened and when | rolled up after 90 days |
| `semantic` | durable facts about Miro, his people, his businesses | permanent |
| `procedural` | runbooks — how to fix a class of problem | earns trust, demoted on failure |
| `working` | the current task's scratch | dies with the task |

Namespaced and scoped per agent: Growth cannot read his mail, Inbox cannot read
security findings. Embeddings are generated locally, so memory stays searchable
without an API round trip over personal content.

---

## Voice and messaging

- **Slack** is primary; Matrix takes a message only when Slack is down.
  (iMessage, Telegram and WhatsApp were removed on 2026-09-28.)
- **Twilio** carries SMS and voice for CRITICAL pages. A2P 10DLC registration is
  required and takes days — start it first.
- **Fish Audio** does the voice: every line on a call, either way, is
  rendered to MP3 and played by Twilio's `<Play>` from a short-lived token; when
  it is unset, failing or slow, Twilio's `<Say>` speaks the same words. The
  conversation itself is Twilio's own `<Gather>` (speech or keypad).
- **Google Voice** is read-only by design. It has no API; every library that
  claims otherwise impersonates the web client and risks the account. Instead it
  forwards texts and voicemail transcripts to Gmail, which ATLAS already reads.
  Miro's Google Voice number can forward to the Twilio number, so callers see a
  familiar number while the machinery stays on an API that supports this.
- **ATLAS's face on a call.** ATLAS cannot set a caller-ID image; no carrier or
  Twilio API can. "Save ATLAS to Contacts" in the top bar downloads a vCard
  (`/api/self/atlas.vcf`) with `TWILIO_FROM`, `SCREENING_FROM` and the particle
  avatar as a PNG, and the iPhone shows that face once the contact is saved.
  `tools/render_avatar.py` draws both the SVG and the PNG in pure Python; the
  PNG is committed, so nothing on the box renders anything.

Routing is by severity, and quiet hours hold everything below CRITICAL for the
morning brief. Without that, he mutes the channel in week three and the whole
thing is worthless.

---

## Quick start

```bash
git clone https://aicode.example.com/<you>/atlas.git /opt/atlas
cd /opt/atlas

sudo bash bootstrap/00-harden.sh              # ssh, ufw, fail2ban, sysctl
sudo bash bootstrap/01-netbird.sh <setup-key> # join the mesh
# verify mesh access from your laptop FIRST, then:
sudo bash bootstrap/01-netbird.sh --close-ssh

cp .env.example .env && $EDITOR .env
sudo bash bootstrap/02-install.sh

# after any upgrade that adds new settings:
sudo bash bootstrap/03-sync-env.sh

docker compose up -d
docker compose exec api python -m atlas.cli init-db
docker compose exec api python -m atlas.cli selftest    # prints every action + tier
docker compose exec api python -m atlas.cli check       # run all checks once
docker compose exec api python -m atlas.cli brief       # print the morning brief
```

### Phase 1: watch-only

`ATLAS_WATCH_ONLY=true` is the default and it is enforced in
`executor.execute`, the single path to the outside world. Checks run, findings
are detected, proposals are created and still reach you — nothing executes.
`selftest` states the mode in its first line.

Leave it on for the first two weeks. The point is to find out which alerts are
noise and which thresholds are wrong *before* ATLAS can act on them. Almost
every version of this project that fails, fails by giving the agent hands in
week one.

To arm it later, set `ATLAS_WATCH_ONLY=false` and restart the API.

### TLS

Caddy provisions and renews the certificate for `atlas.example.com` automatically;
ports 80 and 443 must both be reachable, 80 for the ACME challenge.

The ACME contact address and CA are plain directives in the `Caddyfile`, not
environment variables — Caddy refuses to start when a directive's variable
expands to an empty string, which takes the entire public listener down. To
test against Let's Encrypt staging, uncomment the `acme_ca` line; production
allows only 5 failed issuances per hostname per hour.

### Browser extension

See `extension/README.md`. It posts to `/ingest/browser`, which is mesh-only
*and* requires `BROWSER_INGEST_TOKEN` — an address on the mesh is not a
credential. The deny-list lives only in the browser; there is deliberately no
server-side copy.

Raw browsing history is deleted after `BROWSER_RETENTION_DAYS` (90) by
`kernel/privacy.py`, which the scheduler runs hourly. The per-domain roll-up is
permanent.

### Dashboard

Mesh-only, session-authenticated, and never added to the public listener — not
behind a login, not behind anything. Verified on deploy: every dashboard path
returns 404 from `https://atlas.example.com`, while `/healthz` still answers.

```bash
docker compose exec api python -m atlas.cli set-password   # prints a hash
# put DASHBOARD_PASSWORD_HASH=<hash> in .env, then:
docker compose up -d api
```

Then open `http://<mesh-ip>:8080/`.

The screens, every figure on each with the time it was read:

| Screen | Path | What it is |
|---|---|---|
| Command Center (Mission) | `/` | the mission-control board: tiles per business, waiting approvals, the live feed |
| AI Core | `/core` | the humanoid voice stage and the brain view, firing only on real recalls |
| Agents (Fleet) | `/fleet` | every agent's last real run, model and handoffs as a two-level org chart, with a deep-dive per business |
| Tasks | `/tasks` | work still open: decisions waiting on him, unfinished night-shift jobs, queued research |
| Calendar | `/calendar` | today and everything due within 72 hours — the same items the morning brief lists |
| Memory | `/memory` | what the concierge can recall, by namespace, as a graph |
| Conversations | `/conversations` | everything said to ATLAS or to him, from voice and both message inboxes |
| Knowledge Base | `/knowledge` | the vault, the reading library, research reports, PDFs |
| Tools & Skills | `/tools` | every tool and action with its tier; Perplexity's month against its cap |
| Workflows | `/workflows` | standing rules he set, and the scheduler's steps on a clock |
| Marketing | `/marketing` | experiments, baselines and results, ideas |
| Leads | `/leads` | the agency pipeline: score, audit finding, outreach state |
| Status, Activity, Reports | `/status`, `/activity`, `/reports` | module health, the audit and decision log, the brief and review archive |

Two pages are *additionally* server-rendered with no JS and no bundle, so a
texted link opens instantly on mobile data: `/brief` and `/f/<finding-id>`.

Three rules the UI is built around:

- **UNKNOWN never looks like OK.** Grey, dashed border, hatched marker and the
  word — drawn by one macro shared between the SPA and the server-rendered
  pages, so the two cannot drift. `tests/test_render.py` fails if OK ever gains
  the hatch or UNKNOWN resolves to the OK colour.
- **The only writes are approve and deny**, and both go through
  `approvals.decide` → `executor.execute`. No admin panel, no "run this check
  now", no UI-only shortcut.
- **Nothing is approved without the dry run on screen.** `propose()` captures
  it via `executor.preview`; if it is missing, Approve is disabled — and that is
  on top of the server-side refusal, never instead of it.

Approve/deny revalidate server-side on arrival. The claim is a single
conditional UPDATE, so a tab left open overnight cannot approve something that
lapsed and a double-submit executes exactly once.

To look at the design without a database:

```bash
python tools/preview.py     # seeded data, password: preview
```

### Tests

```bash
python -m venv .venv && .venv/bin/pip install -e . pytest pytest-asyncio ruff
.venv/bin/pytest -q          # kernel, permissions, executor, detect, audit, brief,
                            # approvals, auth, dashboard API, UNKNOWN rendering
node extension/denylist.test.mjs
.venv/bin/ruff check .
cd ui && npm ci && npx tsc -b --noEmit && npm run build
```

`selftest` is the one to read carefully. It prints every registered action with
its tier and the reason, plus any module that was disabled for missing
credentials. A module with a missing token is disabled loudly rather than
reporting healthy forever.

---

## Access

| Surface | Where |
| --- | --- |
| Webhooks (public, signature-verified) | `https://atlas.example.com/webhooks/*` |
| Health (public, no data) | `https://atlas.example.com/healthz` |
| One rendered video for Instagram to fetch (public, token-gated, 24 h) | `https://atlas.example.com/media/<token>` — the Caddy block is Miro's sign-off, see CONFIGURE.md |
| API, `/ask`, dashboard | NetBird mesh only, port 8080 |
| Postgres | container network only — no `ports:` entry, ever |
| SSH | key-only, port 2202, mesh-only once NetBird is verified |
| Browser ingest | NetBird mesh only, port 8080, bearer token required |

Mesh-only paths are enforced twice: Caddy does not proxy them on 443, and the
API refuses any source address outside `MESH_ALLOWED_CIDRS`. A Caddyfile edit
alone cannot expose `/ask`.

## Credentials

Every token is scoped down to the narrowest thing that works. Specifically:

- **Stripe: restricted keys, read-only.** Money leaving an account — refunds,
  payouts, transfers, price changes, cancellations — is Tier 3 and cannot be
  unlocked at runtime. Retrying a failed invoice is Tier 2 dunning, not a new
  charge, and still asks every time.
- **GitLab:** a project-scoped token per instance, `read_api` plus `api` only on
  the projects ATLAS files issues against.
- **Google:** its own OAuth client for ATLAS. Do not reuse a token from
  anywhere else.
- Nothing is written to logs, to Postgres, or into a prompt. The reasoner sees
  *"the flycheap admin API returned 401"*, never the key.
