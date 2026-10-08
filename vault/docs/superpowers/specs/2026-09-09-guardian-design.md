# Guardian — product design spec

Date: 2026-09-09 · Owner: Miro · Status: draft for review

Guardian is Miro's own home-network and infrastructure security product. It replaces Nexus
(`~/.claude/nexus-agent`) with a new engine, a new web app and a new sensor, while keeping every
behaviour Nexus already has. Nexus stays running until Guardian passes the same self-test; then
`safehome.example.com` is switched over and Nexus is retired.

## 1. Goals and non-goals

Goals
- Same logic and the same tabs as Nexus, rebuilt as an owned, sellable product (see the business
  plan artifact and the 60-section product blueprint).
- Multi-tenant from day one: one hub, many sites, each site with its own sensor.
- Real login (username + email one-time code), roles, audit trail.
- A dashboard designed per tab from the 2026-09-09 Q&A (section 8).
- A sensor a technician installs in minutes on macOS, Linux or Docker.

Non-goals (for the first release)
- Mobile app (comes after the web app is proven), marketing website (own domain later),
  billing/Stripe (later phase), SMS or email alert delivery (ntfy is the only alert channel;
  email is used for the login code only).

## 2. Components

```
client site                              cloud (safehome VPS 203.0.113.10)
┌──────────────────────┐   HTTPS POST    ┌───────────────────────────────────┐
│ guardian-sensor      │ ──ingest──────▶ │ guardian-hub  (FastAPI, :8800)     │
│  pip pkg / launchd / │ ◀─edits/queue── │  SQLite (→ Postgres)  scheduler    │
│  systemd / Docker    │                 │  serves guardian-web static build  │
│  + guardian-dns      │                 │  WebSocket /ws live feed           │
└──────────────────────┘                 └───────────────────────────────────┘
                                                 ▲ nginx TLS, safehome.example.com
                                        browser: guardian-web (React + Vite + TS)
```

- **guardian-hub** — Python 3.12, FastAPI, SQLAlchemy 2, Alembic, APScheduler, uvicorn. One
  process. SQLite file per deployment; the schema uses no SQLite-only features so Postgres is a
  connection-string change.
- **guardian-web** — React 18, Vite, TypeScript, React Router, TanStack Query, Zustand for UI
  state, D3 (force layout + scales) for topology and charts, no UI kit (own design system, section 8).
  Built to `/static` and served by the hub.
- **guardian-sensor** — Python package (`pip install guardian-sensor`), stdlib-only at runtime
  like Nexus. Contains the collector (all Nexus checks), the auto-fix engine, the edit replayer,
  and the optional DNS filter (`guardian-dns`). Installers: `guardian-sensor install --launchd`
  (macOS), `--systemd` (Linux), and a Docker image (`--network host`).
- **guardian-cli** — inside the hub package: `guardian selftest`, `guardian tenant add`,
  `guardian user add`, `guardian enrol-code`.

## 3. Data model (hub)

Tables (all rows carry `tenant_id` except `user`, `session`, `otp`):

| table | purpose |
|---|---|
| tenant | id, slug, name, brand{name,accent}, ntfy_topic, retention, report_schedule, created |
| user | id, username (unique), email, display_name, is_superadmin |
| membership | user_id, tenant_id, role (viewer/operator/admin) |
| otp | user_id, code_hash, expires, attempts, used |
| session | id, user_id, tenant_id (active), device_label, trusted_until, created, last_seen |
| sensor | id, tenant_id, name, token_hash, platform, version, last_seen, enrol_code, status |
| site | monitored website (url, expected status, public flag, allowed ports) |
| host | monitored server (ssh target, thresholds, expected services/containers) |
| device | LAN device (mac, ip, name, vendor, kind, label, trusted, always_on, parent, quarantined, risk, presence_state, first/last seen) |
| scan | one collector run: ts, duration, health, security_score, raw json (compressed) |
| metric | ts, entity_type, entity_id, name, value — time series for charts |
| event | ts, kind, entity, payload — joins/leaves/ports/wan/fixes (the Nexus events.jsonl) |
| alert | dedup_key, severity, title, why, evidence, confidence, first/last seen, count, status (open/acked/snoozed/resolved), resolved_by, snoozed_until |
| incident | stable id, entity, story, actions, alert ids, opened/closed |
| recommendation | key, priority, why, action, decision (none/yes/no), decided_by, status (queued/done/failed/needs-you) |
| fix | ts, action, target, by (auto/dashboard/accepted-recommendation), ok, output, reboot_needed |
| dns_query | ts, client_mac, name, type, blocked, list, cached, ms (rolled up daily after 7 d) |
| dns_rule | allow/block domain, list toggles |
| edit | tenant edit log replayed by the sensor (label/trust/merge/quarantine/config/fix/run) |
| report | id, kind (weekly/monthly/manual), period, preset, html path, share_token, expires |
| integration | kind (ntfy/slack/teams/webhook), secret (encrypted), routes by severity, enabled |
| audit | ts, user, role, action, target, ip |
| heartbeat | name, token, last ping, expected interval |

### 3.1 Multiple networks per tenant (Miro, 2026-09-09: "my Wi-Fi and my lab's private network")

- New table **network**: id, tenant_id, name ("Home Wi-Fi", "Lab"), kind (wifi|wired|lab|vlan|guest), subnet_prefix
  ("192.168.1"), expected_gateway, ssid (optional), sensor_id (the sensor that watches it), colour, created.
  A tenant has one or more networks; the first network is created automatically from the first sensor's
  config (`lan.subnet_prefix`/`expected_gateway`) so existing setups keep working.
- **device**, **event**, **alert** (LAN keys) and **dns_query** carry `network_id`. Alert keys for LAN
  findings are namespaced per network as `lan:<slug>:<sub>:<rest>` — the network's slug goes directly
  after the `lan:` namespace, before the sub-kind (`lan:new:<mac>` on the `lab` network becomes
  `lan:lab:new:<mac>`), so the same MAC on two networks is two devices (e.g. a laptop on Wi-Fi and on
  the lab VLAN) and a key's sub-kind stays at a fixed position for the parsers that read it
  (`networks.lan_alert_key` / `networks.canonical_lan_key`).
- Sensor: `config.lan` becomes `config.networks: [{name, kind, subnet_prefix, expected_gateway, interface?}]`
  (old `lan` block still accepted = one network). A sensor scans every network in its list that it can
  reach on a local interface (ARP/ping/Bonjour need L2 adjacency); for a network it cannot reach it
  reports `unreachable` instead of an empty scan, and the hub shows "needs a sensor on this network".
  Each network gets its own baseline, known-devices map, presence state, DNS attribution (by client IP
  → network) and Wi-Fi guard (SSID/evil-twin/rogue-DHCP per network).
- Hub API: `GET/POST/PATCH/DELETE /api/v1/networks`, every device/inbox/topology/dns response includes
  `network` and accepts `?network=<id>` filters; overview shows a per-network health strip.
- UI: Assets → Devices grouped by network (chips); Topology shows one cluster per network linked to the
  router/gateway of each (a multi-homed sensor or router bridging two networks is drawn once with both
  links); Security → Wi-Fi guard is per network; Settings → **Networks** (add network: name, kind,
  subnet, gateway; assign a sensor; download its enrol command); onboarding wizard asks "which networks
  should Guardian watch?".
- Reports: device inventory and presence sections are split per network; the executive summary counts
  networks watched.

## 4. Auth and roles

- Login page at `/`: username → hub emails a 6-digit code (SMTP creds in hub `.env`) → code
  entry → session cookie (HttpOnly, SameSite=Lax, 30 d when "remember this device", else 12 h).
  Codes expire in 10 min, 5 attempts, one active code per user. Same OTP-first rule as the
  attendance app; no password path.
- Roles per tenant: **viewer** (read + chat), **operator** (ack/trust/merge/run/speed test/
  maintenance/rec Yes-No), **admin** (config, fixes, users, integrations). **superadmin** (Miro):
  tenant management, sensors of any tenant, hub settings.
- Tenant switcher in the top bar for users with more than one membership.
- Every write is audit-logged with user, role, tenant and IP. Ingest is not audit-logged.
- Sensor auth: `Authorization: Bearer <sensor token>` (hash stored). Enrolment: dashboard shows a
  one-time 8-char code valid 15 min; `guardian-sensor enrol CODE` exchanges it for a token.
- Public endpoints without login: `/status/<tenant-slug>` (opt-in status page),
  `/r/<share_token>` (expiring report links), `/hb/<name>/<token>` (heartbeats),
  `/act/<token>/...` (ntfy action buttons).

## 5. Sensor (guardian-sensor)

Ports every Nexus collector function 1:1, as modules:

| module | Nexus origin | what it does |
|---|---|---|
| sites | check_site, site_security, admin_probe, external_sites | HTTP/latency/TLS-days/DNS, HSTS/CSP/frames/redirect/obsolete TLS, admin-panel probe with SPA baseline, external port scan from the hub |
| hosts | check_ssh, ssh_run, home_security host part | uptime/load/disk/mem, failed ssh, fail2ban, updates, reboot flag, docker, services, listening ports, sshd/ufw/unattended/preload/miners/cron/authkeys/users/logins/attackers/tmp-exec, drift hashes, .env perms/age, debsecan CVEs |
| lan | lan_sweep, lan_ports, mdns_names, host_name, mac_vendor, presence_state, risk_score, unusual_hours | ping sweep + ARP, names (router DNS + Bonjour), vendor lookup (macvendors → maclookup), randomized-MAC linking, per-device port scan, presence state machine, risk, unusual hours |
| home | wifi_info, gateway_ports, external_exposure, rogue_checks, wan_ip, wan_path, speed_test, dns_check | SSID/encryption, evil twin, rogue DHCP/route, WAN IP change, path loss, speed baseline, resolver check |
| domains | domain_expiry, email_security | whois expiry, SPF/DKIM/DMARC (example.org DMARC p=none is reported only, never nagged) |
| dns | dns_analyse | reads guardian-dns logs → top/blocked/clients, beaconing, DGA |
| checks | extra_checks, heartbeats_check | tcp/icmp/dns probes, heartbeat expectations |
| alerts | build_alerts, security_score, recommendations, correlate_incidents | the 105 alert keys, deductions, grade, recs, incidents — computed on the sensor, stored by the hub |
| fix | apply_fix, remediate + FIX_ACTIONS | security_updates, enable_unattended, kill_miner, block_ip, block_subnet, ban_long, limit_ssh, restart_container, restart_service, cleanup_disk, disable_ssh_password, enable_ufw, reboot (notify-only), install_debsecan, full_upgrade; cooldowns, daily cap, kill switch, attacker_method auto/block_ip/block_subnet/ban_long/limit_ssh/off, Docker-touching upgrades only 02:00–05:00 |
| notify | notify | ntfy with action buttons → macOS banner → Slack → Teams → webhook, severity routes; CRIT unacked 30 min → urgent; Monday summary; daily digest |
| sync | hub_pull, hub_push, hubsync | pull edits/queued jobs, push scan; offsets; truncation-safe |

Runs every 5 min under launchd/systemd/Docker loop with a single-instance lock. Linux shims
(`ip neigh`, `nmcli`, `avahi-browse`, `apt`) as in Nexus. `guardian-dns` is the Nexus `dns.py`
resolver (UDP/TCP :53, StevenBlack/URLhaus/Phishing-Army + family list, DoH Cloudflare→Quad9→UDP,
local names to the router, TTL cache, query log) shipped inside the same package.

## 6. Hub API (FastAPI, `/api/v1`)

- auth: `POST /auth/start {username}`, `POST /auth/verify {username, code, remember}`, `POST /auth/logout`, `GET /me`
- tenants (superadmin): CRUD, brand, `POST /tenants/{id}/enrol-code`
- sensors: `POST /sensor/enrol`, `POST /sensor/ingest` (scan + events + metrics + alerts + recs + incidents + fixes + dns summary), `GET /sensor/edits?after=`, `GET /sensor/queue`, `POST /sensor/queue/{id}/result`
- read: `GET /overview`, `/sites`, `/hosts`, `/devices`, `/devices/{mac}` (timeline, presence, ports, dns profile), `/topology`, `/metrics?name&range`, `/events`, `/inbox?view=incidents|alerts|recs`, `/security` (scorecards, checks with proof, attackers with geo, wifi guard), `/updates` (calendar, cves, upgrade progress), `/dns` (+ `/dns/stream` via WebSocket), `/reports`, `/reports/{id}`, `/integrations`, `/settings/*`, `/audit`, `/live` (10 s probes)
- write: `POST /alerts/{id}/{ack|snooze|resolve|reopen}`, `POST /recs/{key} {decision}`, `POST /devices/{mac} {label,trusted,always_on,parent,quarantined}`, `POST /devices/{mac}/forget`, `POST /fix {action,target}`, `POST /run`, `POST /speedtest`, `POST /maintenance`, `POST /attacker {method}`, `POST /reboot-plan {host, window}`, `POST /dns/rules`, `POST /reports/build`, `POST /reports/{id}/share`, `POST /reports/schedule`, `POST /integrations`, `POST /settings/*`, `POST /chat`
- live: `WS /ws` pushes scan-complete, new alert, device join/leave, fix result, dns query stream (opt-in), upgrade progress.
- `GET /metrics` Prometheus, `GET /status/<slug>` public page, `/hb`, `/act`, `/r/<token>`.

Writes that must run on the client network (fix, run, speed test, reboot) are queued and picked
up by the sensor on its next pull (≤ 5 min), same as the Nexus hub; the UI shows "queued → running
→ done" from the queue state.

## 7. Guardian chat

Same behaviour as the Nexus Guardian: Groq `openai/gpt-oss-120b` first call with a capped context
blob, `gpt-oss-20b` for tool rounds, Anthropic when credits exist, rules fallback. Tools:
`device_deep_dive`, `host_command` (read-only whitelist), `site_check`, `lookup_ip`, `rescan`.

**Guardian acts, not just explains (Miro, 2026-09-09).** When the user tells Guardian in chat to fix,
block, ack, trust, quarantine, reboot or rescan, Guardian executes it through the same paths the
dashboard uses — never by generating shell commands. Action tools: `apply_fix(host, action, arg)`
(any `FIX_ACTIONS` entry incl. `block_ip`, `reboot`), `ack_alert(key)`, `resolve_alert(key)`,
`trust_device(mac, label)`, `quarantine_device(mac, on)`, `decide_recommendation(key, yes|no)`,
`rescan()`, `speedtest()`. Rules: the tool is gated by the chatting user's role exactly like the
matching API route (viewer → Guardian says it cannot and names who can); destructive or
reboot-class actions require the user to confirm in the chat ("yes, reboot safehome") before the
tool runs; every chat action is audited with `by="chat:<user>"`; actions that must run on the client
network are queued for the sensor and Guardian keeps the conversation open, polling the queue result
and reporting "done / failed: <reason>" in the same thread; Guardian never invents a result. Guardian
must also know what it and the operators changed themselves: drift/port alerts whose cause is a
recorded Guardian fix or an audited dashboard/deploy action are explained as such, not as suspicious.

**Ask first, then act (Miro, 2026-09-09).** "Fix it" in chat never executes immediately. Guardian answers
"Sure — here is what I will do: <exact action, host, effect, reversibility>. Reply *yes* to run it."
and runs only after the user's yes (one confirmation covers one action list). If Guardian lacks the
capability or permission for a specific action, it says so and states precisely what it needs, per
action — for example "to change ufw on safehome I need the sensor to have SSH access: add
`~/.guardian/keys/safehome` on the sensor Mac, or grant my user the *admin* role for this site" —
instead of a generic refusal or a shell recipe. Capability comes from the sensor (SSH keys, LAN
position) and from the user's role; the hub-side assistant never holds server keys. Guardian must
never propose an action that would break Guardian itself (e.g. closing the hub's own port) without
saying so explicitly.

**Guardian does the work itself (Miro, 2026-09-09).** Guardian never ends an answer with "run this
yourself" (no `sudo reboot`, no "log in via SSH and…"). Every remediation it can name, it can perform:
reboot included. Reboots run after the user's chat *yes* or through the Reboot planner window, with
Guardian confirming afterwards that the host and its expected services came back; reboot remains
notify-only ONLY for auto-fix (no unattended reboots), never for an explicit user request. The
"Next:" line of a chat answer is therefore always an offer ("Say *yes* and I will reboot safehome
now — about one minute of downtime for the dashboards"), never an instruction to the user.

**Three action tiers (Miro, 2026-09-09: "if I ask for anything not urgent, he should just do it").**
- Tier 1 — investigate / read-only: vulnerability check on a host, deep-dive a new device, site or
  certificate check, IP lookup, rescan, speed test, build or push a report, explain an alert. Guardian
  does it immediately and answers with the result. No confirmation.
- Tier 2 — reversible changes: ack/snooze/resolve an alert, label or trust a device, answer a
  recommendation yes/no, quarantine a device, block an attacking IP, run security updates, restart a
  container or service, enable fail2ban/ufw. Guardian does it immediately, states what it did and how
  to undo it ("I quarantined 192.168.1.99 — say *release it* to undo").
- Tier 3 — disruptive or hard to undo: reboot, full upgrade that touches Docker, changing firewall
  rules beyond a single attacker block, disabling SSH password login, forgetting/deleting a device or
  its history, changing config/thresholds/auto-fix policy, anything on a host Guardian has never fixed
  before. Guardian proposes and waits for *yes*.
Role gates still apply on top (viewer: tier 1 only; operator: tiers 1–2; admin: all). Guardian may
say which tier an action is when it explains why it asked.
Strict answer format (verdict line, ≤ 6 bullets `Name · IP · fact`, `Next:` line). Lives in a right
side panel (section 8), knows which tab is open, streams tool steps.

## 8. Web app design (from the Q&A)

Visual identity: two skins, **calm light** and **console dark**, both blue and white; red = alert,
green = good, yellow = warning, nothing else; Manrope for text, IBM Plex Mono for numbers; new
shield-style Guardian logo mark. All charts follow the dataviz rules (one accent, tabular numerals,
hover values, brushing on time series).

Shell
- Collapsible left sidebar (icons + labels → icons), unread badge on Inbox, tenant switcher on top.
- Top bar: ⌘K global search (devices, hosts, sites, alerts, actions), sensor pulse dot, "last scan
  Ns ago", theme toggle, user menu.
- Right-side Guardian panel toggled from the top bar.
- Mobile: bottom tab bar under 700 px; every tab usable on a phone browser.
- Onboarding wizard on first login of a new tenant: name the site → install sensor (copy command +
  enrol code) → waiting for first scan (live) → label unknown devices → done.

Tabs (sidebar order)
1. **Overview** — mission-control hero: score ring, one verdict sentence, one next-action button;
   per-site/host 24 h health strip (hour blocks); live "what changed" timeline (joins, fixes,
   alerts, scans) with live inserts; 30-day score trend card with the deduction that moved it;
   the five questions as a compact strip under the hero.
2. **Inbox** — one list, filters Incidents / Alerts / Recommendations; unread counts; j/k keys;
   bulk ack; each row: severity stripe, title, entity, why it fired, what changed, actions
   (ack/snooze/resolve, Yes/No for recs, "fix now"); incident rows expand to their alerts.
3. **Assets** — sub-views Websites / Servers / Devices. Websites: cards with uptime 24h/7d, latency
   spark, cert days, security headers; Servers: gauge cards (load/disk/mem), hardening grade,
   ports, containers/services; Devices: cards grid with kind filter chips (Cameras, Phones &
   tablets, Computers, TVs & media, Smart home, Network gear, Unknown), online dot, risk badge;
   Screen time sub-view (presence table + hour heatmap). Click → drawer with timeline, ports,
   presence, DNS profile, merge/split, quarantine, always-on.
4. **Topology** — force-directed D3 map: internet → router → extenders → devices; drag, zoom,
   pan; cluster layout by kind or area; real device icons; status ring (online/offline/sleeping),
   pulsing link when seen in the last scan, risk badge, red ring on untrusted cameras; new device
   pops in; hover card; click → drawer.
5. **Security** — posture scorecard per target (grade, top 3 deductions, fix now); Wi-Fi guard
   panel with one verdict (SSID/encryption, evil twin, rogue DHCP, unknown devices); checks as a
   checklist with pass/fail, last verified, evidence line, auto-fix status; attack map (world map
   of attacker IPs with counts and Guardian's action); exposure tiles; attacker method selector.
6. **Updates** — patch calendar heatmap per host with reboot marks; CVE table (id, package,
   severity, fixable, fixed-on, tracker link, filter critical); live upgrade progress panel
   (packages done/total, log tail); reboot planner (pick window → Guardian reboots → confirms
   services back).
7. **DNS** — live query stream (pause, search, client badge); per-device DNS profile; block/allow
   manager with list toggles and per-list block counts; setup wizard ("point your router DNS at
   X" with a live is-it-working check).
8. **Reports** — builder with live preview (preset dropdown: full, executive, security, network,
   ops, compliance, cameras only, phones/tablets/computers only, TVs only, smart home, network
   gear; section checkboxes; period); report library cards (score, period, open/print/share);
   expiring share links; scheduled report editor (weekly/monthly, preset, ntfy push).
9. **Settings** — sub-nav: Profile (name, email, address for report covers), Users & roles,
   Sites & targets, Auto-fix policy (kill switch, cap, attacker method, maintenance window),
   Alert thresholds, Integrations (ntfy, Slack, Teams, webhook + severity routes + test),
   Appearance (skin), Sensors (enrol, status, version). Superadmin only: Tenants, Billing (later).

## 9. Reports

Server-rendered HTML (print CSS) as in Nexus `build_report`: summary/verdict + MTTR, availability
with heartbeat bars and latency chart, servers + hardening scorecard, patching/CVEs, home network,
device inventory, per-device-group sections, presence, alerts per day, remediation, internet
speed, domains/email, compliance checklist (12 controls), recommendations, score trend and
deductions, incidents. Weekly (Mon) and monthly (1st) archives per tenant with retention.

## 10. Notifications

ntfy is the only alert channel (topic per tenant), with action buttons (Trust / Ignore 24 h).
Slack, Teams and generic webhook are optional integrations with severity routes. No SMS, no email
alerts. Dedupe, resend of CRIT every 6 h, urgent escalation after 30 min unacked, daily digest,
Monday summary — unchanged from Nexus.

### 10.1 Additional integrations (Miro, 2026-09-09) — each must do a watchdog job, not just relay text

| Integration | What it contributes to Guardian | Direction |
|---|---|---|
| **Discord** | Alert channel with severity routes + the daily digest and Monday summary; weekly/monthly report link posted to a channel; a `/guardian status` slash command answers with score, open CRIT/WARN and unverified devices for the tenant bound to that server. | out + command |
| **PagerDuty** | CRIT alerts open a PagerDuty incident (Events API v2, dedup key = Guardian alert key); Guardian resolves it when the alert resolves or a fix succeeds; PagerDuty acknowledgement acks the Guardian alert (webhook); replaces the "urgent after 30 min unacked" escalation for tenants that enable it. | out + in |
| **Telegram** | Bot delivers alerts with inline buttons **Trust / Ignore 24 h / Ack / Fix now** (same actions as the ntfy buttons, via the hub `/act` route); `/status`, `/devices`, `/report` commands; one chat bound per tenant with a pairing code. | out + in |
| **Jira / ServiceNow** | One ticket per *incident* (not per alert): created when an incident opens with the story and actions, updated with each fix, closed when the incident closes; recommendations answered "needs-you" create a task ticket; ticket key shown on the Inbox row. | out + status sync |
| **Home Assistant** | Guardian publishes MQTT-discovery entities: security score, grade, open alerts by severity, unverified devices, sensor online, DNS blocked/24h, per-device online binary sensors; HA automations can call Guardian services **quarantine device / run scan / ack alert** via the REST API; Guardian can read HA `device_tracker` presence to improve screen-time and "unusual hour" accuracy. | out + in |

Common rules: every integration is per tenant, configured in Settings → Integrations with a **Test** button, has severity routes where it delivers alerts, is audited, and stores its secret encrypted in `integration.secret`. Inbound webhooks carry a per-integration token. ntfy stays the default channel.

## 11. Security rules

Secrets only in `.env` (hub) and the sensor token file (0600); never in git, docs or chat. OTP
codes hashed, sessions revocable, CSRF via SameSite + origin check on writes, rate limits on
`/auth/*` and `/sensor/enrol`. Sensor token scoped to one tenant. Host-command tool stays a
read-only whitelist. Auto-fix whitelist, cooldowns, daily cap and kill switch are enforced on the
sensor, not the hub. Audit log for every human write.

## 12. Deployment and cutover

- Repo: `~/Documents/Code/AI Projects/Guardian` (`hub/`, `web/`, `sensor/`, `deploy/`, `docs/`).
  Push to `aicode.example.com` GitLab.
- Hub on safehome VPS as `guardian-hub.service` on :8800, first at `guardian.example.com`
  (Let's Encrypt via acme.sh webroot). Nexus keeps serving safehome.example.com meanwhile.
- Sensor on Miro's Mac (LaunchAgent) alongside the Nexus collector; both may run until cutover
  (different ports, different state dirs; DNS filter runs only once — Guardian's copy takes :53
  at cutover).
- Migration: `guardian import-nexus` copies tenants, users, devices (labels/trust), history,
  events, alerts, fixes, recommendations decisions, reports and config from `/opt/nexus-hub`.
- Cutover gate: `guardian selftest` (services, login, ingest freshness, DNS resolve+block, public
  status page, report build, ntfy test) all green AND a side-by-side parity check (same alerts,
  same devices, same score ±2) for 24 h. Then nginx `safehome.example.com` → Guardian, Nexus
  LaunchAgents unloaded, Nexus repo archived.

## 13. Build phases (each gets its own implementation plan)

1. **Foundation** — hub skeleton, DB + migrations, OTP login, tenants/roles/memberships, sensor
   package with enrolment + ingest + edit/queue sync, port of all collector modules with parity
   tests against Nexus fixtures, `import-nexus`, `selftest`, deploy at guardian.example.com.
2. **Core UI** — design system, shell, Overview, Assets, Inbox, drawer, ⌘K, live WebSocket.
3. **Security surface** — Security, Updates (incl. reboot planner + upgrade progress), DNS tab
   with query stream and wizard, Topology.
4. **Product surface** — Reports (builder, library, share, schedule), Settings area, Integrations
   (ntfy, Slack, Teams, webhook + Discord, PagerDuty, Telegram, Jira/ServiceNow, Home Assistant per §10.1),
   Guardian chat panel with tools, hub `/act` route for action buttons.
5. **Polish and cutover** — onboarding wizard, mobile pass, accessibility, parity soak, switch
   safehome.example.com, retire Nexus.

## 14. Testing

- Sensor: pytest with recorded Nexus fixtures (last.json/state.json/events) → identical alerts,
  score and recommendations; fix-engine tests with fake ssh runner (cooldowns, caps, kill switch,
  attacker methods).
- Hub: pytest + httpx for auth (OTP expiry/attempts), roles (viewer blocked from writes), ingest
  idempotency, edit replay, queue lifecycle, report build, share-link expiry.
- Web: Vitest for stores/formatters, Playwright smoke for login → overview → inbox ack →
  device label → report build, in both skins and at 375 px.
- Live: `guardian selftest` on the VPS, browser verification of every tab before a phase closes.
