# Guardian Phases 2–4 — Full Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the phase-1 Guardian hub (OTP login + sensor ingest + a placeholder shell) into the complete product a demo user can open and explore: every Nexus tab rebuilt as a Guardian tab — full network detail, topology, security posture, updates, DNS, reports, settings, integrations and the Guardian chat that *acts* — served from `guardian.example.com`, driven entirely by real ingested sensor data.

**Architecture:** The hub precomputes one rendering-ready payload per tab from `Scan.raw` plus the relational tables, and pushes deltas over `WS /ws`. The web app is a React SPA with an own design system (no UI kit), one route per tab, a right-side Guardian chat panel and a ⌘K palette. Work splits into a **hub track** (Python: routers, payload builders, reports, integrations, chat) and a **web track** (TypeScript: design system, shell, tabs) that run concurrently once the contracts task (Task 16) has landed.

**Tech Stack (as pinned in the repo today — do not "upgrade" these):**
- hub: Python 3.13 on the VPS / 3.10 on the Mac (tests run on 3.10), FastAPI 0.115, SQLAlchemy 2, Alembic, APScheduler 3, uvicorn, pytest. New hub dependency added in Task 16: `cryptography` (for `Integration.secret`).
- web: **React 19.2.8, TypeScript 6.0.2, Vite 8.2.2**, react-router-dom 6.30.6, @tanstack/react-query 5.102.8, oxlint 1.79.0 (`web/package.json`). No UI kit. D3 + world-atlas + topojson-client added in Task 22 (exact versions resolved and pinned there).
- sensor: stdlib-only Python 3.10.

**Spec:** `docs/superpowers/specs/2026-09-09-guardian-design.md`
**Predecessor plan:** `docs/superpowers/plans/2026-09-09-guardian-phase1-foundation.md` (Tasks 1–15; task numbering continues from 16).
**Nexus reference (read-only, port logic, never import or copy files):** `~/.claude/nexus-agent/ui/server.py`, `~/.claude/nexus-agent/ui/index.html`, `~/.claude/nexus-agent/checks.py`.

---

## Global Constraints

Copied from the spec, plus the rules this plan adds.

**From the spec (§1, §4, §7, §8, §10, §11, §12):**
- Product name everywhere is **Guardian**; never "Nexus" in user-facing copy (the importer and code comments may mention it). No "NEXUS" branding on report covers or the public status page.
- Multi-tenant from day one. Roles per tenant: **viewer** (read + chat tier 1), **operator** (ack/trust/merge/run/speed test/maintenance/rec Yes-No), **admin** (config, fixes, users, integrations), plus `User.is_superadmin` (tenant management, sensors of any tenant, hub settings).
- **Every read and every write is tenant-scoped through `deps.require_role(...)`.** No route may read a table without a `tenant_id` filter derived from the dependency's tenant.
- **Every human write is audit-logged** via `audit.log(db, tenant_id, user, role, action, target, ip, body)`. Ingest is not audit-logged. Chat-initiated writes are audited with `user="chat:<username>"`.
- Writes that must run on the client network (fix, run, speed test, reboot, upgrade) are **queued** for the sensor (`Queue` rows, `≤5 min` pickup); the UI shows queued → running → done from the queue state. The hub never SSHes anywhere.
- Auth is OTP-first. No password path.
- ntfy is the only default alert channel; Slack/Teams/webhook/Discord/PagerDuty/Telegram/Jira-ServiceNow/Home Assistant are optional per-tenant integrations with severity routes, a Test button, encrypted secrets and per-integration inbound tokens.
- Secrets only in `hub/.env` and the sensor token file (0600). Never in git, docs, tests, fixtures or chat.
- Visual identity: two skins, **calm light** and **console dark**, both **blue and white only**; **red = alert, green = good, yellow = warning, and nothing else**. Manrope for text, IBM Plex Mono for numbers. Charts follow the dataviz rules: one accent, tabular numerals, hover values, brushing on time series.
- Sensor runtime dependencies: **stdlib only**, Python **3.10** compatible (no `match`, no `tomllib`, no `datetime.UTC`).
- Behaviour parity with Nexus is the acceptance test: same alerts, same score, same recommendations, same report sections.

**Added by this plan:**
- **Rendering-ready payloads.** The hub returns what the UI draws. All bucketing, aggregation and derivation happens hub-side: the 24 h health strip, the 30-day score trend, the patch calendar, the presence hour-heatmap, topology nodes/links, DNS top-lists. The web app may format (units, relative time, colour class) but must never aggregate a time series or join two collections.
- **Precompute from `Scan.raw` + tables**, never from a live re-scan. `Scan.raw` is the whole `last.json`; `Device` rows come from `state.lan.known_macs`; time series come from `Metric`; presence comes from `PresenceSample` (added in Task 16).
- **Public routes are the one exception to tenant scoping** — `/act/<token>/…`, `/status/<slug>`, `/hb/<name>/<token>`, `/r/<share_token>`. Each authenticates on its own token (or the `Site.public` opt-in flag for the status page), must expose nothing beyond what that flag opts into, and must be rate-limited with the existing `guardian_hub.ratelimit.allow`.
- **Demo access is a tenant flag, not a fourth role.** A tenant whose `settings["demo_code"]` is a 6-digit string accepts that fixed code at `POST /auth/verify` for its members. `ROLE_ORDER` and every `require_role` call site stay untouched.
- **Parallel-track verification discipline.** Web tasks verify with **Vitest** against `web/src/types.ts` and a fixture mock — never against a live hub. **Playwright smoke runs only in the three phase-closeout tasks** (27, 36, 50), after both tracks for that phase have landed. Two hard sequencing edges: Task 17 (`guardian seed-demo`) must land before any Playwright run, and Task 21 (`WS /ws`) must land before Task 23's WS client hook.
- **React 19 StrictMode double-invokes effects in development.** The WebSocket hook, the D3 force simulation and any `requestAnimationFrame` loop must be idempotent on mount and clean up fully on unmount.
- Commit after every task with the trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`, identity `-c user.name=Miro -c user.email=you@example.com`:
  ```bash
  git -c user.name=Miro -c user.email=you@example.com commit -m "$(printf '%s\n\n%s' 'hub: <what>' 'Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>')"
  ```
- No placeholders in shipped code: no `TBD`, no `// add validation`, no "same as the tab above". Every handler returns real data or a real error.

---

## Track map — what can run concurrently

| Phase | Blocking first | Hub track (parallel) | Web track (parallel) | Closeout (serial) |
|---|---|---|---|---|
| 2 | **16** (contracts + migration), then **17** (seed-demo) | 18, 19, 20, 21 | 22, then 23, then 24 ‖ 25 ‖ 26 | 27 |
| 3 | — (16 already landed) | 28, 29, 30, 31 | 32, 33, 34, 35 | 36 |
| 4 | — | 37 → 38, 39, 40 → 41, 42, 43 → 44 | 45 (after 38), 46 (after 39), 47 (after 41), 48 (after 44), 49 (after all) | 50 |
| Addendum A (networks) | **51** (network table + migration 0003), after **16** | 52 (sensor-only), 54 (after 37 **and** 51) | 53 (web-only) | folded into 56 |
| Addendum B (retention, cutover) | — | 55 (anytime after 16) | — | **56** (last task in the plan) |

Rules for the controller:
- **Task 16 is the only global blocker.** It creates the migration and both sides of the contract (`hub/guardian_hub/schemas_dash.py` and `web/src/types.ts`). Nothing else starts until it is merged.
- **Task 51 is the second global blocker** — for the addendum only. It creates migration 0003, the `Network` model, the `network_id` columns and the four new contract types, so Tasks 52, 53 and 54 all wait on it. Once it has landed, **52 ‖ 53** run at the same time (52 touches only `sensor/`, 53 only `web/`) and **54** follows 37 + 51 in the hub track.
- **Task 55 has no ordering constraint beyond Task 16** — it touches `retention.py`, `scheduler.py`, `cli.py`, `dnsview.py` and migration 0004, so it can run alongside any phase-3 or phase-4 task; when 51 has already landed, its rollup rows carry `network_id`.
- **Task 56 is last** and serial: it depends on 50 (a fully verified dashboard) plus 51–55, and it is the only task that touches production nginx, launchd or the Nexus installation.
- **The addendum's Playwright cases run in Task 56, not in Task 53** — the rule that Playwright runs only in closeout tasks (27, 36, 50) still holds; Task 53 writes its `@networks` e2e cases and Task 56's Step 2 runs the whole suite.
- **Task 17 before any Playwright** (Tasks 27, 36, 50) and before a developer runs the web app against a local hub.
- **Task 21 before Task 23** (the shell mounts the `useLiveFeed` hook).
- **Task 22 before Tasks 23–26, 32–35, 45–49** (every web tab imports design-system primitives).
- **Task 37 before Task 38** (the API serves what the engine builds) and **before Task 45**.
- **Task 40 before Task 41** (the five new integrations register into the core registry).
- **Task 43 before Task 44** (providers dispatch through the tool registry).
- Within the hub track, Tasks 18/19/20/21, 28/29/30/31 and 39/40/42 touch disjoint files and may run at the same time. Within the web track, Tasks 24/25/26 and 32/33/34/35 and 45/46/47/48 touch disjoint files and may run at the same time.

---

## File structure added by phases 2–4

```
Guardian/
  hub/guardian_hub/
    schemas_dash.py          # T16 — every tab payload as a pydantic model
    crypto.py                # T16 — Fernet wrapper for Integration.secret
    payloads/                # T18-T31 — one precompute module per tab
      __init__.py
      overview.py assets.py inbox.py security.py updates.py dnsview.py topology.py presence.py
    live.py                  # T21 — in-process pub/sub + WS connection registry
    routers/
      live.py                # T21 — WS /ws, GET /live, GET /queue/{id}
      security.py            # T28
      updates.py             # T29
      dns.py                 # T30
      topology.py            # T31
      reports.py             # T38
      settings.py            # T39 — /settings/*, /audit, /maintenance
      integrations.py        # T40, T41
      public.py              # T42 — /act /status/<slug> /hb /r/<token> /metrics
      chat.py                # T43, T44
    reports/
      __init__.py builder.py sections.py presets.py compliance.py css.py   # T37
    integrations/
      __init__.py base.py registry.py ntfy.py slack.py teams.py webhook.py # T40
      discord.py pagerduty.py telegram.py tickets.py homeassistant.py      # T41
    chat/
      __init__.py tools.py registry.py tiers.py providers.py prompt.py     # T43, T44
    geoip.py                 # T28 — ip-api.com lookups cached in IpGeo
    seed.py                  # T17 — guardian seed-demo
  hub/alembic/versions/0002_dashboard.py            # T16
  hub/tests/                 # one test module per task, named in each task
  web/src/
    types.ts                 # T16 — the contract, mirrored from schemas_dash.py
    lib/{api.ts,format.ts,live.ts,keys.ts}
    design/                  # T22
      tokens.css primitives.css
      Card.tsx Stat.tsx Table.tsx Drawer.tsx Tabs.tsx Chips.tsx Badge.tsx
      Sparkline.tsx Chart.tsx Ring.tsx Heatmap.tsx Timeline.tsx Toast.tsx
    shell/                   # T23
      Shell.tsx Sidebar.tsx TopBar.tsx CommandPalette.tsx MobileTabs.tsx ChatPanel.tsx
    pages/
      Overview.tsx Inbox.tsx Assets.tsx Topology.tsx Security.tsx Updates.tsx
      Dns.tsx Reports.tsx Settings/*.tsx Integrations.tsx Onboarding.tsx
    components/              # tab-local pieces (AttackMap, PatchCalendar, DeviceDrawer, …)
  web/tests/                 # Vitest unit tests
  web/e2e/                   # Playwright smoke (T27, T36, T50)
  web/playwright.config.ts   # T27
```

---

# Phase 2 — Core UI

Ships: the design system, the shell, and the Overview / Inbox / Assets tabs with the drawer, ⌘K and the live WebSocket. At the end of phase 2 a demo user logs in and sees real network detail, not a score and a list.

---

### Task 16: API contracts, payload schemas, TypeScript types, schema migration 0002

The single blocker for both tracks. It defines every payload name used by Tasks 18–50 on both sides, and lands the four schema additions the later tabs need so there is exactly one migration.

**Files:**
- Create: `hub/guardian_hub/schemas_dash.py`, `hub/guardian_hub/crypto.py`, `hub/guardian_hub/payloads/__init__.py`, `hub/alembic/versions/0002_dashboard.py`, `hub/tests/test_contracts.py`, `hub/tests/test_crypto.py`, `hub/scripts/check_types.py`, `web/src/types.ts`, `web/tests/types.test.ts`
- Modify: `hub/guardian_hub/models.py` (four additions below), `hub/guardian_hub/ingest.py` (write `PresenceSample` rows and `DnsQuery.mac`), `hub/pyproject.toml` (add `cryptography>=42`), `hub/tests/test_ingest.py` (two new assertions)

**Interfaces:**

*Model additions (`models.py`):*
```python
class PresenceSample(Base):            # one row per (history line, live mac)
    __tablename__ = "presence_sample"
    __table_args__ = (Index("ix_pres_t", "tenant_id", "ts"), Index("ix_pres_mac", "tenant_id", "mac", "ts"),
                      UniqueConstraint("tenant_id", "ts", "mac"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(32))
    ts: Mapped[str] = mapped_column(String(32))
    mac: Mapped[str] = mapped_column(String(17))

class IpGeo(Base):                     # ip-api.com cache (Task 28)
    __tablename__ = "ip_geo"
    ip: Mapped[str] = mapped_column(String(45), primary_key=True)
    country: Mapped[str] = mapped_column(String(64), default="")
    country_code: Mapped[str] = mapped_column(String(4), default="")
    city: Mapped[str] = mapped_column(String(80), default="")
    org: Mapped[str] = mapped_column(String(160), default="")
    isp: Mapped[str] = mapped_column(String(160), default="")
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    hosting: Mapped[bool] = mapped_column(Boolean, default=False)
    reverse: Mapped[str] = mapped_column(String(255), default="")
    fetched: Mapped[dt.datetime] = mapped_column(TZDateTime, default=utcnow)

class ActToken(Base):                  # ntfy / Telegram button targets (Task 42)
    __tablename__ = "act_token"
    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(16))      # device|alert
    target: Mapped[str] = mapped_column(String(200))   # mac or alert key
    actions: Mapped[list] = mapped_column(JSON, default=list)  # ["trust","snooze","ack","fix"]
    expires: Mapped[dt.datetime] = mapped_column(TZDateTime)
    used: Mapped[str] = mapped_column(String(16), default="")  # "" | the action used
    created: Mapped[dt.datetime] = mapped_column(TZDateTime, default=utcnow)
```
Plus column additions: `DnsQuery.mac: Mapped[str | None] = mapped_column(String(17), nullable=True)`; `Alert.ticket_key: Mapped[str] = mapped_column(String(64), default="")`; `Report.title: Mapped[str] = mapped_column(String(200), default="")` and `Report.meta: Mapped[dict] = mapped_column(JSON, default=dict)`; on `Integration` — `name: String(60) default ""`, `config: JSON default dict`, `inbound_token: String(64) nullable unique`, `last_test: TZDateTime nullable`, `last_error: Text default ""`, and widen `kind` to `String(24)`.

*`crypto.py`:* `key() -> bytes` (urlsafe-base64 of `sha256(settings.secret_key)`), `encrypt(plain: str) -> str`, `decrypt(token: str) -> str` (returns `""` on an undecryptable value rather than raising, so a rotated `secret_key` degrades to "reconfigure this integration").

*`schemas_dash.py`:* every payload model below, as pydantic `BaseModel`s. These exact names are the contract; Tasks 18–50 import them and `web/src/types.ts` mirrors them one-for-one.

```
Shared      Sev(Literal["CRIT","WARN","INFO"])  TrendPoint{ts,value}  Series{name,points,color}
            HourBlock{hour,state}  NextAction{label,method,path,body}  Deduction{category,points,why}
            TimelineItem{ts,kind,title,detail,sev,entity,href}  NameCount{name,count}
            EventRow{ts,type,entity,text,payload}  QueueStatus{id,path,status,body,result,ts}
            HeatmapCell{row,col,value}
Overview    OverviewPayload{ts,age_s,score,grade,potential,verdict,next_action,deductions,strip,
                            timeline,score_trend,questions,alerts,devices,sensor}
            HealthStrip{kind,name,ok,blocks}  QuestionItem{question,answer,state}
            AlertCounts{crit,warn,info,open,acked,snoozed}
            DeviceCounts{total,online,sleeping,unverified,quarantined}
            SensorStatus{name,status,last_seen,age_s,platform,version}
Live        LivePayload{ts,sites,scanning,sensor_age_s}  LiveSite{name,url,code,ms,ok}
            LiveEvent{type,ts,data}
Assets      SitesPayload{sites}  SiteCard{name,url,ok,code,ms,uptime_24h,uptime_7d,latency_spark,
                            cert_days,cert_expires,cert_issuer,dns_a,headers,incidents_7d,public}
            SecurityHeaders{hsts,nosniff,frame,csp,https_redirect,old_tls,server}
            HostsPayload{hosts}  HostCard{name,address,reachable,hostname,uptime,load1,load_per_core,
                            disk_pct,mem_pct,failed_ssh_24h,banned,fail2ban,reboot_required,
                            pending_updates,security_updates,cve_fixable,hardening,ports,docker,
                            services,gauges}
            HardeningGrade{score,grade,checks}  HardeningCheck{name,passed,detail}
            ContainerRow{name,status,image}  ServiceRow{name,state}  Gauge{name,value,unit,max,state}
            DevicesPayload{groups,chips,total,online,unverified}  DeviceGroup{group,devices}
            KindChip{group,count,online}
            DeviceCard{mac,ip,name,label,vendor,kind,group,trusted,always_on,quarantined,parent,risk,
                       presence,online,first_seen,last_seen,ports,unverified,randomized}
            PortRow{port,since,risky,service}
            DeviceDetail{device,timeline,presence,ports,dns,alerts,children,events}
            PresenceProfile{online_pct_24h,online_pct_7d,hours,typical_hours,days,strip}
            DayHours{day,hours}  DeviceDnsProfile{queries_24h,blocked_24h,top,blocked_top}
            ScreenTimePayload{days,scans,devices,heatmap}
            ScreenTimeRow{mac,name,ip,trusted,total_h,pct,hours_per_day,hours}
Inbox       InboxPayload{counts,rows}  InboxCounts{incidents,alerts,recs,unread}
            InboxRow{id,view,sev,title,entity,why,what_changed,status,first_seen,last_seen,count,
                     actions,children,ticket_key,confidence}
            RowAction{kind,label,method,path,body,tier}
Security    SecurityPayload{scorecards,wifi,checks,attackers,exposure,attacker_method,methods}
            Scorecard{target,kind,grade,score,deductions,fix}
            WifiGuard{verdict,state,ssid,security,channel,phy,signal,evil_twin,twin_channels,
                      rogue_dhcp,dhcp_server,expected_gateway,unknown_devices,rows}
            SecurityCheck{key,area,title,passed,last_verified,evidence,autofix,target}
            Attacker{ip,host,fails,days,first_seen,last_seen,geo,action,action_ts,alert_key}
            Geo{country,country_code,city,org,isp,lat,lon,hosting,reverse}
            ExposureTile{label,value,state,detail}
Updates     UpdatesPayload{calendar,hosts,cves,progress,reboot_plan}
            PatchDay{day,host,patches,reboots,state}
            UpdateHost{name,os,kernel,kernel_latest,pending,security_pending,cve_fixable,unattended,
                       reboot_required,last_apt}
            CveRow{cve,package,urgency,host,fixable,fixed_on,tracker}
            UpgradeProgress{queue_id,host,action,status,done,total,log_tail,started,finished}
            RebootPlan{hosts,windows}  RebootCandidate{host,reason,services,queued}
            RebootWindow{label,start,end}
DNS         DnsPayload{status,top,blocked_top,clients,rules,lists,beaconing,suspicious,wizard}
            DnsStatus{enabled,running,queries_24h,blocked_24h,block_pct,cache,upstream_ms_avg,
                      total_blocked_domains,family_filter,uptime_s,ts}
            DnsClient{mac,ip,name,queries,blocked}  DnsRules{allow,block}
            DnsList{name,category,entries,blocked_24h,enabled}
            DnsWizard{resolver_ip,router_hint,working,clients_using,checked_at}
            DnsStreamRow{ts,mac,client,name,qtype,blocked,list,ms,device}
Topology    TopologyPayload{nodes,links,clusters}
            TopoNode{id,kind,group,label,ip,mac,icon,state,risk,ring,trusted,quarantined,pulse,detail}
            TopoLink{source,target,kind,pulse}  TopoCluster{id,label,members}
Reports     ReportsPayload{library,presets,sections,schedule}
            ReportCard{id,kind,period_days,preset,title,created,score,url,share_url,share_expires}
            ReportPreset{key,label,sections}  ReportSection{key,title}
            ReportSchedule{weekly,monthly,preset,ntfy,hour}
Settings    SettingsPayload{profile,users,sites,hosts,autofix,thresholds,appearance,sensors,tenants}
            ProfileSettings{owner,email,address,company}
            UserRow{username,display_name,email,role,is_superadmin,last_seen}
            SiteTarget{name,url,expect,public,host}
            HostTarget{name,target,optional,expect_services,expect_containers}
            AutoFixPolicy{enabled,max_per_day,attacker_method,maintenance_window,cooldowns}
            Appearance{theme,accent}  SensorRow{id,name,platform,version,status,last_seen,age_s}
            TenantRow{id,slug,name,brand,ntfy_topic,users,sensors,demo}
Integrations IntegrationsPayload{integrations}
            IntegrationCard{kind,title,help,enabled,configured,fields,routes,inbound_url,last_test,
                            last_error,direction}
            IntegrationField{name,label,kind,value,required,options}
Chat        ChatRequest{message,thread,tab,confirm_token}  ChatTurn{role,text,tool_steps}
            ToolStep{tool,args,tier,status,result,queue_id}
            ChatResponse{text,verdict,bullets,next,tool_steps,proposal,provider,thread_id}
            ChatProposal{token,summary,actions,expires}
            ProposedAction{tool,args,tier,effect,reversible,host}
Audit       AuditPayload{rows}  AuditRow{ts,user,role,action,target,ip,body}
```
Field naming rules that the whole plan obeys: booleans that would shadow a Python/TS keyword are suffixed (`passed`, not `pass`); every timestamp is an ISO-8601 string named `ts`/`*_seen`/`*_at`; every "how long ago" is an integer `age_s`; every percentage is a number `0–100` named `*_pct`; every list field is plural.

*`web/src/types.ts`:* the same names as `export type`, `snake_case` fields preserved verbatim (no camelCase conversion anywhere in the app).

*`hub/scripts/check_types.py`:* `main() -> int` — builds the app, reads `app.openapi()["components"]["schemas"]`, reads the `export type <Name>` identifiers out of `web/src/types.ts`, prints and fails on any name present on one side only.

- [ ] **Step 1: Write the failing tests**

`hub/tests/test_contracts.py`:
```python
import pathlib, re
from guardian_hub import schemas_dash as sd

TYPES_TS = pathlib.Path(__file__).resolve().parents[2] / "web" / "src" / "types.ts"

def test_every_payload_model_is_mirrored_in_types_ts():
    py = {n for n in dir(sd) if n[:1].isupper() and hasattr(getattr(sd, n), "model_fields")}
    ts = set(re.findall(r"^export type (\w+)", TYPES_TS.read_text(), re.M))
    assert py - ts == set(), f"missing from web/src/types.ts: {sorted(py - ts)}"
    assert ts - py == set(), f"missing from schemas_dash.py: {sorted(ts - py)}"

def test_field_names_match_for_every_shared_model():
    src = TYPES_TS.read_text()
    for name in ("OverviewPayload", "DeviceCard", "InboxRow", "SecurityPayload", "DnsPayload",
                 "UpdatesPayload", "TopologyPayload", "ReportCard", "IntegrationCard", "ChatResponse"):
        block = re.search(rf"export type {name} = \{{(.*?)\n\}};", src, re.S).group(1)
        ts_fields = set(re.findall(r"^\s{2}(\w+)\??:", block, re.M))
        assert ts_fields == set(getattr(sd, name).model_fields), name

def test_presence_and_acttoken_tables_exist(client):
    from sqlalchemy import inspect
    from guardian_hub.db import engine
    names = set(inspect(engine).get_table_names())
    assert {"presence_sample", "ip_geo", "act_token"} <= names
```

`hub/tests/test_crypto.py`:
```python
from guardian_hub import crypto

def test_roundtrip_and_soft_failure():
    tok = crypto.encrypt("https://hooks.slack.com/services/AAA/BBB/CCC")
    assert tok != "https://hooks.slack.com/services/AAA/BBB/CCC"
    assert crypto.decrypt(tok) == "https://hooks.slack.com/services/AAA/BBB/CCC"
    assert crypto.decrypt("not-a-fernet-token") == ""
```

New assertions appended to `hub/tests/test_ingest.py` (inside `test_store_scan_tables`, after the ingest call):
```python
    # history live_macs became presence samples, and dns rows carry the mac
    assert db.query(m.PresenceSample).count() == sum(len(h.get("live_macs") or []) for h in hist)
    ip2mac = {ip: mac for ip, mac in (last["lan"]["devices"] or {}).items()}
    for row in db.query(m.DnsQuery).all():
        assert row.mac == ip2mac.get(row.client)
```
(The existing `hist` line built in that test must gain `"live_macs": sorted(set(last["lan"]["devices"].values()))`, and the ingest call must pass one `dns_queries` row whose `client` is a known LAN IP.)

`web/tests/types.test.ts`:
```ts
import { describe, expect, it } from "vitest";
import type { OverviewPayload, DeviceCard, InboxRow } from "../src/types";

describe("contract types", () => {
  it("accepts a hub-shaped overview payload", () => {
    const o: OverviewPayload = {
      ts: "2026-09-09T18:30:26-04:00", age_s: 42, score: 83, grade: "B", potential: 98,
      verdict: "Mostly fine. 4 warnings to review.", next_action: null, deductions: [],
      strip: [], timeline: [], score_trend: { name: "score", points: [], color: null },
      questions: [], alerts: { crit: 0, warn: 4, info: 5, open: 9, acked: 2, snoozed: 0 },
      devices: { total: 24, online: 19, sleeping: 5, unverified: 0, quarantined: 0 },
      sensor: { name: "mac", status: "active", last_seen: "2026-09-09T18:30:26-04:00", age_s: 42,
                platform: "darwin", version: "0.1.0" },
    };
    expect(o.score).toBe(83);
  });
  it("keeps snake_case device and inbox fields", () => {
    const d: DeviceCard = { mac: "58:96:71:61:05:e3", ip: "192.168.1.1", name: "cr1000a",
      label: "Router", vendor: "WNC Corporation", kind: "router", group: "Network gear",
      trusted: true, always_on: true, quarantined: false, parent: null, risk: 15,
      presence: "ONLINE", online: true, first_seen: "2026-09-08T20:45:29-04:00",
      last_seen: "2026-09-09T18:30:26-04:00", ports: [], unverified: false, randomized: false };
    const r: InboxRow = { id: "lan:unverified", view: "alerts", sev: "WARN", title: "x", entity: "home",
      why: "", what_changed: "", status: "open", first_seen: "", last_seen: "", count: 1,
      actions: [], children: [], ticket_key: "", confidence: 95 };
    expect(d.always_on && r.count === 1).toBe(true);
  });
});
```
(Vitest itself is installed in Task 22; until then run this file with `npx vitest run web/tests/types.test.ts` after a one-off `npm i -D vitest` — Task 22 pins it properly.)

- [ ] **Step 2: Run** — `cd hub && pytest -q tests/test_contracts.py tests/test_crypto.py` → FAIL (`No module named 'guardian_hub.schemas_dash'`).

- [ ] **Step 3: Implement** `models.py` additions, `crypto.py`, `schemas_dash.py`, `web/src/types.ts`, and the two `ingest.py` changes:
  - after the history loop, for each history line: `for mac in h.get("live_macs") or []: insert PresenceSample(tenant_id, ts=hts, mac=mac)` guarded by the same "already present" check the metric writer uses (so re-ingest stays idempotent);
  - build `ip2mac = {ip: mac for ip, mac in ((last.get("lan") or {}).get("devices") or {}).items() if mac}` once, and set `DnsQuery.mac = ip2mac.get(q_client)` on insert.

- [ ] **Step 4: Migration** — `cd hub && alembic revision -m "dashboard tables and columns" --rev-id 0002_dashboard`, then hand-write `upgrade()` with `op.create_table` for the three tables and `op.add_column` for the eight columns (SQLite-safe: every added column is nullable or has a server default). `alembic upgrade head`, then `alembic downgrade -1 && alembic upgrade head` to prove both directions.

- [ ] **Step 5: Run** — `cd hub && pytest -q` → all pass, including the amended `test_ingest.py`. Then `python scripts/check_types.py` → exit 0.

- [ ] **Step 6: Commit** — `hub+web: dashboard payload contracts, types.ts, presence/geo/act-token schema (0002)`

---

### Task 17: `guardian seed-demo`, the demo tenant, and the fixed demo sign-in code

Hub-only. Must land before any Playwright run or local web development, because every tab renders from this data.

**Files:**
- Create: `hub/guardian_hub/seed.py`, `hub/tests/test_seed.py`
- Modify: `hub/guardian_hub/cli.py` (add the `seed-demo` subcommand), `hub/guardian_hub/routers/auth.py` (accept `Tenant.settings["demo_code"]`), `hub/tests/test_auth.py` (one new test)

**Interfaces:**
- `seed.seed_demo(db, slug="demo", name="Guardian Demo Network", fixtures: Path | None = None, demo_code="424242") -> dict` — idempotent: creates the tenant (with `settings={"demo_code": ..., "retention_days": 90, "profile": {...}}`, `config` = the fixture `config.json` shape with sites/ssh_hosts/lan/thresholds), a `demo` user with role `viewer` and a `demoadmin` user with role `admin`, an `active` sensor row named `demo-sensor`, then calls `ingest.store_scan` with `last.json` + `state.json` from `hub/tests/fixtures/` plus **30 days of synthesised history**: one history line every 30 minutes derived from the fixture line by jittering `sites[*].lat` ±25 %, flipping one site to `ok=False` for two consecutive lines on day 3 and day 11, walking `score` between 78 and 92, dropping/adding `live_macs` per device on a per-device duty cycle (always-on devices always present; phones present 07:00–23:00; TVs 18:00–23:00), a `speed` line every 12 h, and `dns_ms`. It also generates 40 `events` (joins/leaves/port_open/port_closed/fix), 12 `fixes` and 3 000 `dns_queries` spread over 24 h across the fixture's LAN IPs with ~35 % blocked and a `list` of `stevenblack`/`urlhaus`/`phishing-army`/`custom`. Returns `{"tenant_id","scans","history","events","dns","devices"}`.
- `seed.DEMO_CODE = "424242"` and `seed.synth_history(last, days=30, step_minutes=30) -> list[dict]` (pure, unit-tested).
- CLI: `guardian seed-demo [--slug demo] [--days 30] [--reset]` (`--reset` deletes every row for that tenant first, so re-seeding is safe).
- `routers/auth.py`: in `verify()`, before the OTP lookup — if the user has a membership in a tenant whose `settings.get("demo_code")` equals `body.code`, mint the session directly against that tenant and return `_me(...)`; nothing else changes. Rate-limited by the existing `/auth/*` limiter. Never accepted when `demo_code` is falsy.

- [ ] **Step 1: Write the failing tests**

`hub/tests/test_seed.py`:
```python
from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from guardian_hub.seed import seed_demo, synth_history
import json, pathlib
FX = pathlib.Path(__file__).parent / "fixtures"

def test_synth_history_is_dense_and_bounded():
    last = json.load(open(FX / "last.json"))
    rows = synth_history(last, days=30, step_minutes=30)
    assert 1400 <= len(rows) <= 1500
    assert all(78 <= r["score"] <= 92 for r in rows)
    assert any(r["speed"] for r in rows) and any(r["live_macs"] for r in rows)
    assert sorted(rows, key=lambda r: r["ts"]) == rows

def test_seed_demo_fills_every_tab(client):
    db = SessionLocal()
    out = seed_demo(db)
    t = db.query(m.Tenant).filter_by(slug="demo").one()
    assert t.settings["demo_code"] == "424242"
    assert db.query(m.Device).filter_by(tenant_id=t.id).count() >= 20
    assert db.query(m.Metric).filter_by(tenant_id=t.id, name="score").count() > 1000
    assert db.query(m.PresenceSample).filter_by(tenant_id=t.id).count() > 5000
    assert db.query(m.DnsQuery).filter_by(tenant_id=t.id).count() == 3000
    assert db.query(m.Alert).filter_by(tenant_id=t.id).count() > 0
    assert out["tenant_id"] == t.id
    seed_demo(db)   # idempotent
    assert db.query(m.Tenant).filter_by(slug="demo").count() == 1
```

Appended to `hub/tests/test_auth.py`:
```python
def test_demo_code_signs_in_without_an_emailed_otp(client):
    from guardian_hub.seed import seed_demo
    from guardian_hub.db import SessionLocal
    seed_demo(SessionLocal())
    r = client.post("/api/v1/auth/verify", json={"username": "demo", "code": "424242"})
    assert r.status_code == 200 and r.json()["active_tenant"]["slug"] == "demo"
    assert client.post("/api/v1/auth/verify", json={"username": "demo", "code": "111111"}).status_code == 401
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** `seed.py`, the CLI subcommand and the `auth.py` branch.
- [ ] **Step 4: Run** — `cd hub && pytest -q` → all pass. Then end-to-end: `GUARDIAN_DATABASE_URL=sqlite:///$PWD/data/dev.db guardian migrate && guardian seed-demo --reset` and `curl -s localhost:8800/api/v1/health`.
- [ ] **Step 5: Commit** — `hub: guardian seed-demo, demo tenant, fixed demo sign-in code`

---

### Task 18: Overview payload and `GET /live`

Hub-only. Concurrent with 19, 20, 21.

**Files:**
- Create: `hub/guardian_hub/payloads/overview.py`, `hub/guardian_hub/routers/live.py` *(the `GET /live` half only; Task 21 adds `WS /ws` and `GET /queue/{id}` to the same file — the two tasks must not both edit it, so 18 creates it with `GET /live` and 21 appends)*, `hub/tests/test_overview.py`
- Modify: `hub/guardian_hub/routers/read.py` (replace the phase-1 `overview()` body with a call into `payloads.overview.build`), `hub/guardian_hub/main.py` (include `live.router`)

**Interfaces:**
- `payloads.overview.build(db, tenant) -> schemas_dash.OverviewPayload`
- `payloads.overview.health_strip(db, tenant_id, hours=24) -> list[HealthStrip]` — one `HourBlock` per hour per site/host, state from the `up` metric for sites and the `disk_pct` metric's presence for hosts: `"up"` when every sample in the hour passed, `"down"` when every sample failed, `"partial"` when mixed, `"nodata"` when the hour has no sample. **Bucketing is hub-side; the UI draws 24 squares.**
- `payloads.overview.score_trend(db, tenant_id, days=30) -> Series` — the `score` metric, decimated to at most 240 points by hourly mean.
- `payloads.overview.timeline(db, tenant_id, limit=40) -> list[TimelineItem]` — merges `Event` rows (join/leave/port_open/port_closed/wan/rotated), `Fix` rows, `Alert` first_seen, and `Scan` rows into one reverse-chronological list. `kind` ∈ `join|leave|port|wan|fix|alert|scan`; `href` is the in-app route the row links to (`/assets/devices/<mac>`, `/inbox?row=<key>`, `/updates`).
- `payloads.overview.questions(db, tenant, raw) -> list[QuestionItem]` — the five questions, ported from the Nexus overview strip (`~/.claude/nexus-agent/ui/index.html`, the `TABS`/overview renderer around line 262 which derives `upd`, `rc`, `c`, `w`, `nd`, `sc`, `sw`): *Is anything broken right now?* / *Is anyone attacking me?* / *Is everything patched?* / *Who is on my Wi-Fi?* / *Is my internet healthy?* — each with a one-line answer and `state`.
- Verdict + `next_action`: keep the phase-1 rules, and extend `next_action` with `method` (`"POST"`) and the tier the chat would assign, so the hero button and the chat agree.
- Route `GET /api/v1/live` (viewer+): concurrent HTTP HEAD/GET probe of each `Tenant.config["sites"]` entry with a 6 s timeout in a `ThreadPoolExecutor(8)` — the port of Nexus `server.py:829` `/api/live` — returning `LivePayload`. It also reports `sensor_age_s` from `Sensor.last_seen` and `scanning` from whether a `/api/run` queue row is `running`. Rate-limited to 12 calls/minute/tenant via `ratelimit.allow(f"live:{tenant.id}", 12, 60)`.

- [ ] **Step 1: Write the failing test**

`hub/tests/test_overview.py`:
```python
from tests.test_seed_helpers import seeded_demo   # helper added in this task's Step 3

def test_overview_payload_is_render_ready(client):
    seeded_demo(client)
    o = client.get("/api/v1/overview").json()
    assert o["score"] and o["grade"] and o["age_s"] is not None
    assert len(o["strip"]) >= 4 and all(len(s["blocks"]) == 24 for s in o["strip"])
    assert {b["state"] for s in o["strip"] for b in s["blocks"]} <= {"up", "down", "partial", "nodata"}
    assert 2 <= len(o["score_trend"]["points"]) <= 240
    assert len(o["questions"]) == 5 and all(q["state"] in ("good", "warn", "bad") for q in o["questions"])
    assert o["timeline"] and o["timeline"][0]["ts"] >= o["timeline"][-1]["ts"]
    assert o["sensor"]["status"] == "active"

def test_live_probes_and_is_rate_limited(client, monkeypatch):
    seeded_demo(client)
    import guardian_hub.routers.live as live
    monkeypatch.setattr(live, "_probe", lambda s: (s["name"], {"ms": 12, "code": 200, "ok": True}))
    body = client.get("/api/v1/live").json()
    assert body["sites"] and all(v["ok"] for v in body["sites"].values())
    for _ in range(12):
        client.get("/api/v1/live")
    assert client.get("/api/v1/live").status_code == 429
```
`hub/tests/test_seed_helpers.py` (created here, reused by Tasks 19–21, 28–31, 37–44):
```python
from guardian_hub.db import SessionLocal
from guardian_hub.seed import seed_demo

def seeded_demo(client):
    """Seed the demo tenant and sign in as its admin. Returns the tenant id."""
    out = seed_demo(SessionLocal())
    r = client.post("/api/v1/auth/verify", json={"username": "demoadmin", "code": "424242"})
    assert r.status_code == 200, r.text
    return out["tenant_id"]
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** `payloads/overview.py`, `routers/live.py` (with a module-level `_probe(site)` so the test can monkeypatch it), and the `read.py` swap.
- [ ] **Step 4: Run** — `pytest -q` → all pass, and confirm the old `test_read_write.py::test_overview_and_lists` still passes (the payload is a superset).
- [ ] **Step 5: Commit** — `hub: overview payload (health strip, timeline, score trend, five questions) + /live probes`

---

### Task 19: Assets payloads — sites, hosts, devices, device drawer, screen time

Hub-only. Concurrent with 18, 20, 21.

**Files:**
- Create: `hub/guardian_hub/payloads/assets.py`, `hub/guardian_hub/payloads/presence.py`, `hub/tests/test_assets.py`
- Modify: `hub/guardian_hub/routers/read.py` (replace `devices()`; add `/sites`, `/hosts`, `/devices/{mac}`, `/presence`)

**Interfaces:**
- `assets.sites(db, tenant) -> SitesPayload` — from `Site.last` + `Metric` (`up`, `latency_ms`, `cert_days`). `uptime_24h`/`uptime_7d` = percentage of `up` samples that are 1, rounded to 2 dp; `latency_spark` = the last 48 `latency_ms` points; `incidents_7d` = count of False→True→False transitions, the port of Nexus `build_report`'s inner `incidents(n)` (`server.py:378`). `headers` from `Site.last["sec"]`.
- `assets.hosts(db, tenant) -> HostsPayload` — from `Host.last`. `gauges` = three `Gauge` rows (`load/core`, `disk %`, `memory %`) with `state` from `Tenant.config["thresholds"]` (`disk_pct_warn`, `mem_pct_warn`, `load_per_core_warn`). `hardening` is the port of the Nexus `CK` scorecard list (`server.py:411`): Key-only SSH, Firewall, fail2ban, No security updates pending, Auto-updates, No malware signs, No reboot pending — `score = round(100 * passed / 7)`, `grade` A ≥ 90, B ≥ 80, C ≥ 70, D ≥ 60, else F.
- `assets.devices(db, tenant) -> DevicesPayload` — `group` from a port of Nexus `kind_of(d)` + `KIND_GROUPS` (`server.py:282–297`) into `assets.kind_of(device_row) -> str` and `assets.KIND_GROUPS`, so the chip set is exactly Cameras / Phones & tablets / Computers / TVs & media / Smart home / Printers & appliances / Network gear / Unknown. `online` = `mac in Scan.raw["lan"]["devices"].values()`. `unverified` from `Scan.raw["home"]["unverified"]`. `randomized` = `bool(int(mac.split(":")[0], 16) & 2)` (Nexus `device_history`, `server.py:145`). `ports` is a sorted `PortRow[]` with `risky` from the sensor's `lan.RISKY_PORTS` list and `service` from a static port→name map defined in this module.
- `assets.device_detail(db, tenant, mac) -> DeviceDetail` — the port of Nexus `device_history(mac)` (`server.py:131–147`): timeline merges `Event` rows for that mac, alerts whose key contains the mac, and fix rows targeting it; `presence` from `presence.profile`; `dns` from `DnsQuery` rows with that `mac` (which is why Task 16 stores it); `children` = devices whose `parent == mac`.
- `presence.profile(db, tenant_id, mac, days=7) -> PresenceProfile` and `presence.screen_time(db, tenant_id, days=7) -> ScreenTimePayload` — the port of Nexus `presence_stats` (`server.py:117–130`) and `/api/presence` (`server.py:767`), reading `PresenceSample` instead of `history.jsonl`. Each sample represents one 5-minute scan, so `total_h = round(samples * 5 / 60, 1)`; `typical_hours` = hours whose count ≥ 15 % of the device's samples; `heatmap` is `HeatmapCell{row=mac, col=hour, value=samples}` already normalised to `0–100`.
- Routes (all viewer+): `GET /api/v1/sites`, `GET /api/v1/hosts`, `GET /api/v1/devices`, `GET /api/v1/devices/{mac}`, `GET /api/v1/presence?days=7`.

- [ ] **Step 1: Write the failing test** (`hub/tests/test_assets.py`)
```python
from tests.test_seed_helpers import seeded_demo

def test_sites_hosts_devices(client):
    seeded_demo(client)
    s = client.get("/api/v1/sites").json()["sites"]
    assert s and 0 <= s[0]["uptime_7d"] <= 100 and len(s[0]["latency_spark"]) > 1
    h = client.get("/api/v1/hosts").json()["hosts"]
    assert h and len(h[0]["gauges"]) == 3 and len(h[0]["hardening"]["checks"]) == 7
    assert h[0]["hardening"]["grade"] in "ABCDF"
    d = client.get("/api/v1/devices").json()
    assert {c["group"] for c in d["chips"]} <= {"Cameras", "Phones & tablets", "Computers",
        "TVs & media", "Smart home", "Printers & appliances", "Network gear", "Unknown"}
    assert sum(c["count"] for c in d["chips"]) == d["total"] >= 20

def test_device_drawer_and_screen_time(client):
    seeded_demo(client)
    mac = client.get("/api/v1/devices").json()["groups"][0]["devices"][0]["mac"]
    dd = client.get(f"/api/v1/devices/{mac}").json()
    assert dd["device"]["mac"] == mac and len(dd["presence"]["hours"]) == 24
    assert dd["presence"]["online_pct_7d"] is not None and dd["dns"]["queries_24h"] >= 0
    st = client.get("/api/v1/presence?days=7").json()
    assert st["devices"] and st["devices"][0]["total_h"] > 0 and st["heatmap"]
    assert all(0 <= c["value"] <= 100 for c in st["heatmap"])
```
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → pass.
- [ ] **Step 5: Commit** — `hub: assets payloads (sites, hosts, devices, drawer, screen time)`

---

### Task 20: Inbox payload, bulk actions, recommendation decisions that queue

Hub-only. Concurrent with 18, 19, 21.

**Files:**
- Create: `hub/guardian_hub/payloads/inbox.py`, `hub/tests/test_inbox.py`
- Modify: `hub/guardian_hub/routers/read.py` (replace `inbox()`), `hub/guardian_hub/routers/write.py` (add `/alerts/bulk`, extend `rec_decide`)

**Interfaces:**
- `inbox.build(db, tenant, view: str, status: str) -> InboxPayload` — one row shape for all three views.
  - `view="incidents"`: one `InboxRow` per open `Incident`, `children` = the `InboxRow`s of its `alert_keys`, `title` from `Incident.story` when it is a string else the incident's derived title, `why` = the first story step, `what_changed` = the newest story step.
  - `view="alerts"`: one row per `Alert`, `why` = `Alert.why` or, when empty, a sentence built from `Alert.evidence`; `what_changed` = `"seen {count}× since {first_seen}"` when `count > 1` else `"first seen {first_seen}"`.
  - `view="recs"`: one row per `Recommendation`, `sev` mapped from `priority` (Critical/High → CRIT, Medium → WARN, Low → INFO).
  - `actions` are real routes the UI POSTs verbatim: ack/snooze/resolve/reopen (`/api/v1/alerts/{key}/{op}`, tier 2), `fix` when `Alert.fix` names a `FIX_ACTIONS` key (`/api/v1/fix`, tier 2 or 3 per the chat tier table in Task 43), yes/no (`/api/v1/recs/{key}`, tier 2), trust (`/api/v1/devices/{mac}`, tier 2). Every action a **viewer** may not perform is omitted from `actions` rather than shown disabled.
  - `counts.unread` = alerts + incidents whose `last_seen` is newer than `Session.last_seen` at request start.
- `POST /api/v1/alerts/bulk {keys: [...], op: "ack"|"snooze"|"resolve"|"reopen", hours?}` (operator) → `{"updated": n}`; writes one `Edit` per key (the sensor replay contract is per-key) and **one** `Audit` row with `action="alert.bulk"` and `body={"op":…, "keys":[…]}`.
- `rec_decide` extension: when `decision == "yes"` and `Recommendation.action` is a `{host, action, arg?}` dict, also enqueue `Queue(path="/api/fix", body=action)` and set `Recommendation.status = "queued"`; when the action is null set `status = "needs-you"`. The returned `rec_out` gains `queue_id`.

- [ ] **Step 1: Write the failing test** (`hub/tests/test_inbox.py`)
```python
from tests.test_seed_helpers import seeded_demo
from guardian_hub.db import SessionLocal
from guardian_hub import models as m

def test_three_views_share_one_row_shape(client):
    seeded_demo(client)
    keys = {"id","view","sev","title","entity","why","what_changed","status","first_seen",
            "last_seen","count","actions","children","ticket_key","confidence"}
    for view in ("alerts", "incidents", "recs"):
        body = client.get(f"/api/v1/inbox?view={view}").json()
        assert set(body["counts"]) == {"incidents", "alerts", "recs", "unread"}
        for row in body["rows"]:
            assert set(row) == keys and row["view"] == view
            assert all(a["method"] == "POST" and a["path"].startswith("/api/v1/") for a in row["actions"])

def test_bulk_ack_writes_one_audit_and_one_edit_per_key(client):
    seeded_demo(client)
    keys = [r["id"] for r in client.get("/api/v1/inbox?view=alerts").json()["rows"][:3]]
    r = client.post("/api/v1/alerts/bulk", json={"keys": keys, "op": "ack"})
    assert r.json()["updated"] == len(keys)
    db = SessionLocal()
    assert db.query(m.Audit).filter_by(action="alert.bulk").count() == 1
    assert db.query(m.Edit).filter_by(path="/api/alert").count() == len(keys)

def test_yes_on_an_actionable_rec_queues_the_fix(client):
    seeded_demo(client)
    rec = next(r for r in client.get("/api/v1/inbox?view=recs").json()["rows"]
               if any(a["kind"] == "yes" for a in r["actions"]))
    out = client.post(f"/api/v1/recs/{rec['id']}", json={"decision": "yes"}).json()
    assert out["status"] in ("queued", "needs-you")
    if out["status"] == "queued":
        assert SessionLocal().query(m.Queue).filter_by(path="/api/fix").count() >= 1
```
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → pass (including the phase-1 `test_read_write.py`).
- [ ] **Step 5: Commit** — `hub: unified inbox rows, bulk alert ops, recommendations that queue their fix`

---

### Task 21: WebSocket `/ws` live feed, event bus, queue polling

Hub-only. Concurrent with 18, 19, 20. **Must land before Task 23.**

**Files:**
- Create: `hub/guardian_hub/live.py`, `hub/tests/test_ws.py`
- Modify: `hub/guardian_hub/routers/live.py` (append `WS /ws` and `GET /queue/{id}`), `hub/guardian_hub/ingest.py` (publish on store), `hub/guardian_hub/routers/write.py` (publish on every write), `hub/guardian_hub/routers/sensor.py` (publish on queue result)

**Interfaces:**
- `live.Bus` — an in-process async pub/sub keyed by `tenant_id`. `bus.publish(tenant_id: str, type: str, data: dict) -> None` (safe to call from sync request handlers: it does `asyncio.run_coroutine_threadsafe` onto the app loop captured at startup, and is a no-op when no loop is running, so tests and the CLI never break). `bus.subscribe(tenant_id) -> AsyncIterator[LiveEvent]` with a per-connection `asyncio.Queue(maxsize=200)` that **drops the oldest frame** when full (a slow browser must never back-pressure ingest). `live.bus` is the module-level singleton.
- Event types published: `scan` (`{ts, score, grade, devices, alerts}`), `alert` (`{key, sev, msg, status}`), `device` (`{mac, event: "join"|"leave"|"port_open"|"port_closed", ip, label}`), `fix` (`{host, action, ok, by}`), `queue` (`{id, path, status}`), `upgrade` (`{queue_id, host, done, total}`), `dns` (`{ts, mac, name, blocked, list}` — only sent to sockets that opted in).
- `WS /api/v1/ws` — authenticates with the same `guardian_session` cookie (`deps.current_session` re-implemented for the websocket scope; a missing/expired session closes with code 4401). First client frame may be `{"subscribe": ["dns"]}` to opt into the DNS stream; the server answers `{"type": "ready", "data": {"tenant": slug}}` and then streams `LiveEvent` frames. A `{"type":"ping"}` is sent every 25 s and an unanswered pong for 60 s closes the socket.
- `GET /api/v1/queue/{qid}` (viewer+, tenant-scoped) → `QueueStatus`. This is what the chat and the UI poll for "queued → running → done".

- [ ] **Step 1: Write the failing test** (`hub/tests/test_ws.py`)
```python
from tests.test_seed_helpers import seeded_demo

def test_ws_streams_writes_and_gates_on_the_session(client):
    seeded_demo(client)
    with client.websocket_connect("/api/v1/ws") as ws:
        assert ws.receive_json()["type"] == "ready"
        key = client.get("/api/v1/inbox?view=alerts").json()["rows"][0]["id"]
        client.post(f"/api/v1/alerts/{key}/ack")
        frame = ws.receive_json()
        assert frame["type"] == "alert" and frame["data"]["status"] == "acked"

def test_ws_rejects_a_signed_out_client(client):
    import pytest
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/v1/ws") as ws:
            ws.receive_json()

def test_queue_status_is_pollable(client):
    seeded_demo(client)
    qid = client.post("/api/v1/fix", json={"host": "prod", "action": "security_updates"}).json()["id"]
    body = client.get(f"/api/v1/queue/{qid}").json()
    assert body["id"] == qid and body["status"] == "queued" and body["path"] == "/api/fix"
```
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** `live.py`, the two routes, and the four publish call sites. In `ingest.store_scan`, publish `scan` once and one `device` frame per new `Event` row of type join/leave/port_open/port_closed.
- [ ] **Step 4: Run** → pass. Then manual: `uvicorn guardian_hub.main:app --port 8800`, open two browser tabs on `/api/docs`, and confirm with `websocat ws://127.0.0.1:8800/api/v1/ws` (cookie header) that an ack in one tab appears in the socket.
- [ ] **Step 5: Commit** — `hub: WebSocket live feed, event bus, queue status polling`

---

### Task 22: Design system — tokens, primitives, chart engine, Vitest harness

Web-only. **Blocks every other web task.** Depends on Task 16 only.

**Files:**
- Create: `web/src/design/{primitives.css,Card.tsx,Stat.tsx,Table.tsx,Drawer.tsx,Tabs.tsx,Chips.tsx,Badge.tsx,Sparkline.tsx,Chart.tsx,Ring.tsx,Heatmap.tsx,Timeline.tsx,Toast.tsx,index.ts}`, `web/src/lib/{format.ts,api.ts,keys.ts}`, `web/vitest.config.ts`, `web/tests/setup.ts`, `web/tests/design.test.tsx`, `web/tests/format.test.ts`
- Modify: `web/src/styles/tokens.css` (add the tokens listed below), `web/package.json` (dependencies + `test` script), `web/tsconfig.app.json` (include `tests/`)

**Interfaces:**
- Token additions to `tokens.css`, defined on bare `:root` and redefined in **both** the `@media (prefers-color-scheme: dark) :root:not([data-theme="light"])` block and the `:root[data-theme="dark"]` block (never only in one): `--space-1..6` (4/8/12/16/24/32 px), `--radius-sm`, `--font-size-xs..xl`, `--line-height-tight/normal`, `--color-grid` (chart gridlines), `--color-accent-2` (the single permitted second blue for a two-series chart), `--color-track` (ring/gauge track), `--z-drawer/--z-palette/--z-toast`, `--sidebar-w`, `--sidebar-w-collapsed`, `--topbar-h`, `--chat-w`. **No hue outside blue / white / grey / `--color-good` / `--color-warn` / `--color-alert` may be introduced.**
- Components (all take `className` and forward `...rest`, all are keyboard-reachable):
  - `Card({title, subtitle, actions, tone, children})` — `tone` ∈ `"plain"|"good"|"warn"|"alert"` sets the left border only.
  - `Stat({label, value, unit, sub, tone, mono})` — `mono` renders the value in IBM Plex Mono with `font-variant-numeric: tabular-nums`.
  - `Table({columns, rows, sort, onSort, empty, dense, rowKey, onRowClick, selected})` — `columns: {key, header, align, width, render}[]`; sorting is local to the column data given, never a re-fetch; the whole table lives inside `overflow-x: auto`.
  - `Drawer({open, onClose, title, subtitle, tabs, children, width})` — right-side, focus-trapped, closes on Escape and on backdrop click, restores focus to the trigger.
  - `Tabs({items, value, onChange})`, `Chips({items, value, onChange, multiple})`, `Badge({tone, children})`.
  - `Sparkline({points, height, tone})` — pure SVG path, no library, no axes.
  - `Chart({series, height, yLabel, yMax, format, brush, onBrush})` — the workhorse. `d3-scale` + `d3-shape` for scales and the line generator; own rendering (no `d3-selection` DOM mutation, React owns the DOM). Hover shows a crosshair with one value per series and tabular numerals; `brush` enables a drag-to-zoom range that reports `[from, to]` ISO strings through `onBrush`. Max two series per chart (`--color-primary`, `--color-accent-2`).
  - `Ring({value, max, grade, size, label})` — the score ring, an SVG arc with the good/warn/alert tone chosen by value.
  - `Heatmap({cells, rows, cols, format, onCell})` — takes `HeatmapCell[]` already normalised 0–100 by the hub; renders a labelled grid with a hover value.
  - `Timeline({items, onSelect})` — renders `TimelineItem[]` with a severity stripe, relative time and an optional link.
  - `Toast` — `ToastProvider` + `useToast() -> {show(msg, tone), dismiss(id)}`; toasts auto-dismiss after 6 s, are `role="status"`, and stack bottom-right (bottom-centre under 700 px).
- `lib/format.ts`: `ago(ts) -> string` ("42s ago" / "7m ago" / "3h ago" / "2d ago"), `pct(n)`, `ms(n)`, `bytes(n)`, `hours(n)`, `sevTone(sev) -> "alert"|"warn"|"plain"`, `stateTone(state)`, `shortHost(name)` (drops the `" (…)"` suffix Nexus site names carry).
- `lib/api.ts`: extends the phase-1 `web/src/api.ts` with a generic `get<T>(path)` / `post<T>(path, body)` and one typed function per route added in phases 2–4, each returning the `types.ts` type of the same name. `web/src/api.ts` re-exports from `lib/api.ts` so the phase-1 login page keeps working.
- `lib/keys.ts`: `useHotkeys(map: Record<string, (e: KeyboardEvent) => void>)` — ignores events whose target is an input/textarea/contenteditable.

- [ ] **Step 1: Install and pin the new dependencies**
```bash
cd web
for p in d3-scale d3-shape d3-array d3-force d3-geo topojson-client world-atlas; do
  echo "$p -> $(npm view $p version)"
done
npm install --save-exact d3-scale d3-shape d3-array d3-force d3-geo topojson-client world-atlas
npm install -D --save-exact vitest @vitest/ui jsdom @testing-library/react @testing-library/user-event \
  @testing-library/jest-dom @types/d3-scale @types/d3-shape @types/d3-array @types/d3-force \
  @types/d3-geo @types/topojson-client
git diff --stat package.json package-lock.json
```
Record the resolved versions: `--save-exact` writes them into `package.json` with no range prefix, and `package-lock.json` is committed. Every later task imports these pinned versions and must not change them. Add `"test": "vitest run"` and `"test:watch": "vitest"` to `scripts`.

- [ ] **Step 2: Write the failing tests**

`web/tests/format.test.ts`:
```ts
import { describe, expect, it } from "vitest";
import { ago, pct, sevTone, shortHost } from "../src/lib/format";

describe("format", () => {
  it("renders relative time in four bands", () => {
    const now = Date.now();
    expect(ago(new Date(now - 42_000).toISOString())).toBe("42s ago");
    expect(ago(new Date(now - 7 * 60_000).toISOString())).toBe("7m ago");
    expect(ago(new Date(now - 3 * 3_600_000).toISOString())).toBe("3h ago");
    expect(ago(new Date(now - 2 * 86_400_000).toISOString())).toBe("2d ago");
  });
  it("maps severity to the only three tones", () => {
    expect([sevTone("CRIT"), sevTone("WARN"), sevTone("INFO")]).toEqual(["alert", "warn", "plain"]);
  });
  it("trims Nexus-style site names and clamps percentages", () => {
    expect(shortHost("Roots prod (status.example.org)")).toBe("Roots prod");
    expect(pct(99.456)).toBe("99.46%");
  });
});
```

`web/tests/design.test.tsx`:
```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Chips, Drawer, Ring, Table } from "../src/design";

describe("design system", () => {
  it("Table sorts locally and renders an empty state", async () => {
    render(<Table columns={[{ key: "n", header: "N" }]} rows={[]} empty="nothing here" rowKey={(r) => r.n} />);
    expect(screen.getByText("nothing here")).toBeInTheDocument();
  });
  it("Drawer closes on Escape and restores focus", async () => {
    const onClose = vi.fn();
    render(<Drawer open title="Device" onClose={onClose}><p>body</p></Drawer>);
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledOnce();
  });
  it("Chips report the selected group", async () => {
    const onChange = vi.fn();
    render(<Chips items={[{ value: "Cameras", label: "Cameras", count: 2 }]} value={[]} multiple onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: /Cameras/ }));
    expect(onChange).toHaveBeenCalledWith(["Cameras"]);
  });
  it("Ring exposes its value to assistive tech", () => {
    render(<Ring value={83} max={100} grade="B" label="Security score" />);
    expect(screen.getByRole("meter", { name: /Security score/ })).toHaveAttribute("aria-valuenow", "83");
  });
});
```

- [ ] **Step 3: Run** — `npm test` → FAIL. **Step 4: Implement** the tokens, primitives and lib modules. **Step 5: Run** — `npm test` → pass; `npx tsc -b` → clean; `npm run lint` → clean.
- [ ] **Step 6: Visual check** — a scratch route `/__design` (removed before commit) rendering every primitive; screenshot in both skins with `data-theme` forced, confirming no colour outside the palette.
- [ ] **Step 7: Commit** — `web: design system (tokens, primitives, chart engine), format/api/keys libs, Vitest harness`

---

### Task 23: App shell — sidebar, top bar, ⌘K palette, live-feed hook, chat slot, mobile tabs

Web-only. Depends on Tasks 16, 21, 22.

**Files:**
- Create: `web/src/shell/{Shell.tsx,Sidebar.tsx,TopBar.tsx,CommandPalette.tsx,MobileTabs.tsx,ChatPanel.tsx,shell.css}`, `web/src/lib/live.ts`, `web/src/lib/theme.ts`, `web/tests/shell.test.tsx`, `web/tests/live.test.ts`
- Modify: `web/src/App.tsx` (routes for all nine tabs, wrapped in `Shell`), `web/src/main.tsx` (ToastProvider)

**Interfaces:**
- `Shell` — the layout: sidebar, top bar, `<Outlet/>`, right chat panel, mobile bottom tabs under 700 px. Holds `useLiveFeed()` once and provides it through `LiveContext`.
- `Sidebar` — the nine tabs in spec order (Overview, Inbox, Assets, Topology, Security, Updates, DNS, Reports, Settings) plus Integrations under Settings; collapsible to icons (state in `localStorage["guardian.sidebar"]`, wrapped in try/catch); unread badge on Inbox from `InboxCounts.unread`, warn badge on Updates from hosts needing a reboot, crit/warn badge on Security; tenant switcher at the top when `me.tenants.length > 1` (POSTs `/api/v1/me/tenant` then invalidates every query).
- `TopBar` — ⌘K search button, sensor pulse dot (green `active` / yellow `age_s > 900` / red `age_s > 1800`), "last scan {ago}", theme toggle cycling `system → light → dark` (writes `data-theme` on `<html>`, persists in `localStorage["guardian.theme"]`), user menu (display name, role in the active tenant, Sign out).
- `CommandPalette` — ⌘K / Ctrl-K opens; searches devices, hosts, sites, alerts and a fixed action list (Run scan, Speed test, Build report, Toggle theme, Open chat) from one `GET /api/v1/search?q=` call debounced 150 ms — **the hub route is added in this task** (`routers/read.py`, `search(q)` querying `Device.label/name/ip/mac`, `Host.name`, `Site.name/url`, `Alert.key/msg`, limit 8 each, tenant-scoped, viewer+). Arrow keys move, Enter navigates, Escape closes.
- `lib/live.ts` — `useLiveFeed(): {status, last, on(type, fn)}`. One `WebSocket` per app instance to `/api/v1/ws`, **idempotent under React 19 StrictMode double-mount** (a module-level ref-counted singleton; the effect cleanup decrements and only the last unmount closes the socket). Reconnects with backoff 1 s → 2 s → 5 s → 15 s, capped; on `scan` and `alert` frames it calls `queryClient.invalidateQueries` for the affected keys and raises a toast for a new CRIT.
- `ChatPanel` — a right-side panel toggled from the top bar and by `⌘/`; in phase 2 it renders the panel chrome, the current tab name as context and a disabled composer reading "Guardian chat arrives in phase 4". Task 48 fills it in.
- `MobileTabs` — bottom bar under 700 px with the five most-used tabs (Overview, Inbox, Assets, Security, DNS) and a "More" sheet for the rest.

- [ ] **Step 1: Write the failing tests** (`web/tests/shell.test.tsx`, `web/tests/live.test.ts`)
```tsx
it("shows the unread badge, the scan age and switches theme", async () => {
  renderShell({ overview: { ...fixtureOverview, age_s: 42 }, inbox: { counts: { unread: 3 } } });
  expect(await screen.findByText("3")).toBeInTheDocument();
  expect(screen.getByText(/42s ago/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /theme/i }));
  expect(document.documentElement.dataset.theme).toBe("light");
});
it("opens the palette on meta+k and navigates on Enter", async () => {
  renderShell({ search: { devices: [{ mac: "aa:bb", label: "Kitchen TV" }] } });
  await userEvent.keyboard("{Meta>}k{/Meta}");
  await userEvent.type(screen.getByRole("combobox"), "kitchen");
  await userEvent.keyboard("{Enter}");
  expect(window.location.pathname).toBe("/assets/devices/aa:bb");
});
```
```ts
it("opens exactly one socket across a StrictMode double mount and closes it once", () => {
  const sockets = mockWebSocket();
  const { unmount } = renderHook(() => useLiveFeed(), { wrapper: StrictWrapper });
  expect(sockets.opened).toBe(1);
  unmount();
  expect(sockets.closed).toBe(1);
});
```
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement**, including the hub `GET /api/v1/search` route and its pytest in `hub/tests/test_assets.py` (`assert client.get("/api/v1/search?q=router").json()["devices"]`).
- [ ] **Step 4: Run** — `npm test`, `npx tsc -b`, `npm run lint` → clean. Manual: `npm run dev` against a seeded local hub; collapse the sidebar, switch tenant, switch theme, open ⌘K.
- [ ] **Step 5: Commit** — `web: app shell, sidebar, top bar, command palette, live feed hook, mobile tabs`

---

### Task 24: Overview tab

Web-only. Depends on 22, 23; concurrent with 25, 26.

**Files:**
- Create: `web/src/pages/Overview.tsx`, `web/src/components/{HealthStrip.tsx,VerdictHero.tsx,QuestionStrip.tsx}`, `web/tests/overview.test.tsx`
- Modify: `web/src/App.tsx` (route `/`)

**Interfaces:**
- `Overview` renders, top to bottom: **hero** (`Ring` with score + grade, the one-sentence verdict, one primary button from `next_action` that POSTs the given path/body and toasts the result); **health strip** (one `HealthStrip` row per site/host, 24 hour-blocks, hover shows the hour and state); **live "what changed" timeline** (the `Timeline` primitive; new frames from `useLiveFeed().on("scan"|"alert"|"device"|"fix")` are prepended with a one-off highlight class, capped at 60 rows); **score trend card** (`Chart` with the 30-day series, brushing on, plus the top `Deduction` rendered under it as "−12 Vulnerabilities · 0 security updates and 403 fixable CVEs pending"); **five questions** as a compact strip under the hero.
- `VerdictHero({payload, onAction})`, `HealthStrip({rows})`, `QuestionStrip({items})` — all pure, all driven by `OverviewPayload`.
- Data: one `useQuery(["overview"], api.overview)` with `refetchInterval: 60_000`; `useQuery(["live"], api.live, {refetchInterval: 10_000})` only while the tab is visible (`document.visibilityState`).

- [ ] **Step 1: Write the failing test** (`web/tests/overview.test.tsx`)
```tsx
it("renders the hero, 24 blocks per row, and the five questions", async () => {
  renderPage(<Overview />, { overview: fixtureOverview });
  expect(await screen.findByRole("meter", { name: /Security score/ })).toHaveAttribute("aria-valuenow", "83");
  expect(screen.getByText(fixtureOverview.verdict)).toBeInTheDocument();
  expect(screen.getAllByTestId("hour-block")).toHaveLength(fixtureOverview.strip.length * 24);
  expect(screen.getAllByTestId("question")).toHaveLength(5);
});
it("prepends a live alert frame into the timeline without a refetch", async () => {
  const { emit, fetchCount } = renderPage(<Overview />, { overview: fixtureOverview });
  emit({ type: "alert", ts: "2026-09-09T19:00:00-04:00",
         data: { key: "sec:attacker:demo:1.2.3.4", sev: "CRIT", msg: "brute force", status: "open" } });
  expect(await screen.findByText(/brute force/)).toBeInTheDocument();
  expect(fetchCount("/api/v1/overview")).toBe(1);
});
```
- [ ] **Step 2–4:** run → FAIL → implement → `npm test` + `tsc -b` + lint clean.
- [ ] **Step 5: Commit** — `web: Overview tab (hero, health strip, live timeline, score trend, five questions)`

---

### Task 25: Inbox tab

Web-only. Depends on 22, 23; concurrent with 24, 26.

**Files:**
- Create: `web/src/pages/Inbox.tsx`, `web/src/components/{InboxList.tsx,InboxRowView.tsx}`, `web/tests/inbox.test.tsx`
- Modify: `web/src/App.tsx` (route `/inbox`)

**Interfaces:**
- One list with a `Tabs` filter (Incidents / Alerts / Recommendations) carrying unread counts, a status filter (`open` / `all`), and a search box filtering the loaded rows client-side (a filter, not an aggregation).
- Row: severity stripe (`sevTone`), title, entity chip, "why it fired" line, "what changed" line, action buttons rendered **from `row.actions`** (never hard-coded), a `ticket_key` badge when set. Incident rows expand to their `children`.
- Keyboard: `j`/`k` move the cursor, `Enter` expands, `a` acks the cursor row, `x` toggles selection, `Shift+A` bulk-acks the selection through `POST /api/v1/alerts/bulk`, `?` shows the shortcut sheet. Registered through `useHotkeys` so typing in the search box is unaffected.
- Recommendations rows show Yes / No buttons; after Yes the row shows `status` (`queued` → polls `GET /api/v1/queue/{id}` every 5 s until `done`/`failed`, then toasts).
- Optimistic updates through TanStack Query `onMutate` with rollback on error; the `alert` live frame reconciles.

- [ ] **Step 1: Write the failing test** (`web/tests/inbox.test.tsx`)
```tsx
it("renders only the actions the hub returned", async () => {
  renderPage(<Inbox />, { inbox: { alerts: viewerInboxFixture } });   // rows with actions: []
  expect(await screen.findByText(viewerInboxFixture.rows[0].title)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /^Ack$/ })).toBeNull();
});
it("j/k moves the cursor and Shift+A bulk-acks the selection", async () => {
  const { posted } = renderPage(<Inbox />, { inbox: { alerts: adminInboxFixture } });
  await userEvent.keyboard("jxjx{Shift>}A{/Shift}");
  expect(posted("/api/v1/alerts/bulk")).toMatchObject({ op: "ack", keys: expect.any(Array) });
  expect(posted("/api/v1/alerts/bulk").keys).toHaveLength(2);
});
it("expands an incident to its child alerts", async () => {
  renderPage(<Inbox />, { inbox: { incidents: incidentFixture } });
  await userEvent.click(await screen.findByText(incidentFixture.rows[0].title));
  expect(screen.getByText(incidentFixture.rows[0].children[0].title)).toBeInTheDocument();
});
```
- [ ] **Step 2–4:** run → FAIL → implement → tests, `tsc -b`, lint clean.
- [ ] **Step 5: Commit** — `web: Inbox tab (three filters, j/k, bulk ack, rec Yes/No)`

---

### Task 26: Assets tab and the device drawer

Web-only. Depends on 22, 23; concurrent with 24, 25.

**Files:**
- Create: `web/src/pages/Assets.tsx`, `web/src/components/{SiteCardView.tsx,HostCardView.tsx,DeviceGrid.tsx,DeviceDrawer.tsx,ScreenTime.tsx}`, `web/tests/assets.test.tsx`
- Modify: `web/src/App.tsx` (routes `/assets`, `/assets/:view`, `/assets/devices/:mac`)

**Interfaces:**
- Sub-views via `Tabs`: **Websites** (cards: uptime 24 h / 7 d, `Sparkline` of latency, cert days with a warn tone under the tenant's `cert_days_warn`, the six security-header pills), **Servers** (gauge cards for load/disk/memory, hardening grade badge with the seven checks on hover, ports list, containers/services table), **Devices** (`Chips` kind filter from `DevicesPayload.chips`, grid of device cards with an online dot, risk badge, trust badge), **Screen time** (`Table` of `ScreenTimeRow` + `Heatmap` of the hub's cells, day-range selector 1/7/30 that changes the `days` query param).
- Clicking anything opens `DeviceDrawer` (the `Drawer` primitive) at `/assets/devices/:mac`, with tabs Timeline / Ports / Presence / DNS and the write controls the hub allowed: label (inline edit → `POST /api/v1/devices/{mac}`), trusted toggle, always-on toggle, quarantine toggle, merge into parent (a MAC picker → `parent`), Forget (admin only, confirm dialog). Sites and hosts open a matching drawer with their raw `last` facts rendered as a definition list.
- Deep links work: `/assets/devices/58:96:71:61:05:e3` opens the drawer over the Devices sub-view.

- [ ] **Step 1: Write the failing test** (`web/tests/assets.test.tsx`)
```tsx
it("filters devices by kind chip", async () => {
  renderPage(<Assets />, { devices: devicesFixture }, { route: "/assets/devices" });
  const before = (await screen.findAllByTestId("device-card")).length;
  await userEvent.click(screen.getByRole("button", { name: /Cameras/ }));
  expect(screen.getAllByTestId("device-card").length).toBeLessThan(before);
});
it("opens the drawer from a deep link and saves a label", async () => {
  const { posted } = renderPage(<Assets />, { devices: devicesFixture, device: deviceDetailFixture },
                                { route: "/assets/devices/58:96:71:61:05:e3" });
  expect(await screen.findByRole("dialog", { name: /Router/ })).toBeInTheDocument();
  await userEvent.clear(screen.getByLabelText("Label"));
  await userEvent.type(screen.getByLabelText("Label"), "Front door router{Enter}");
  expect(posted("/api/v1/devices/58:96:71:61:05:e3")).toMatchObject({ label: "Front door router" });
});
it("draws the screen-time heatmap from hub cells only", async () => {
  renderPage(<Assets />, { presence: presenceFixture }, { route: "/assets/screen-time" });
  expect(await screen.findAllByTestId("heat-cell")).toHaveLength(presenceFixture.heatmap.length);
});
```
- [ ] **Step 2–4:** run → FAIL → implement → tests, `tsc -b`, lint clean.
- [ ] **Step 5: Commit** — `web: Assets tab (websites, servers, devices, screen time) and the device drawer`

---

### Task 27: Phase 2 closeout — Playwright smoke, build, deploy, live verification

Serial. Depends on every task 16–26.

**Files:**
- Create: `web/playwright.config.ts`, `web/e2e/{fixtures.ts,smoke.spec.ts}`, `web/e2e/README.md`
- Modify: `deploy/deploy.sh` (run `guardian seed-demo` on the VPS when `GUARDIAN_SEED_DEMO=1`), `hub/guardian_hub/selftest.py` (add a "dashboard payloads" check)

**Interfaces:**
- `playwright.config.ts`: `baseURL` from `E2E_BASE_URL` (default `http://127.0.0.1:8800`), one project per skin (`light`, `dark` via `colorScheme` + an init script setting `data-theme`), plus a `mobile` project at 375 × 812. `webServer` starts `uvicorn guardian_hub.main:app --port 8800` with `GUARDIAN_DATABASE_URL` pointing at a temp DB seeded by `guardian seed-demo`.
- `e2e/smoke.spec.ts` covers: sign in with the demo code → Overview shows a score and a 24-block strip → Inbox lists rows and an ack changes the row status → Assets shows chips and opens a device drawer → screen-time heatmap renders → ⌘K finds a device → theme toggle flips `data-theme` → sidebar collapses.
- `selftest.run` gains `_check_dashboard(get)`: `GET /api/v1/overview` for the first tenant with an active sensor returns `strip`, `timeline` and `score_trend` non-empty (using an internal session, not a network login).

- [ ] **Step 1:** `cd web && npm install -D --save-exact @playwright/test && npx playwright install --with-deps chromium`; record the resolved version in `package.json`.
- [ ] **Step 2:** write `smoke.spec.ts`; `npx playwright test` → FAIL until the app is built (`npm run build`) and the hub serves `web/dist`.
- [ ] **Step 3:** `npm run build && npx playwright test` → all three projects green. Fix real bugs, never weaken an assertion.
- [ ] **Step 4:** `cd hub && pytest -q` → green. `cd web && npm test && npx tsc -b && npm run lint` → green.
- [ ] **Step 5: Deploy** — `GUARDIAN_SEED_DEMO=1 deploy/deploy.sh`, then on the VPS `guardian selftest` → all ok.
- [ ] **Step 6: Browser check** — open `https://guardian.example.com`, sign in, and visually confirm Overview / Inbox / Assets in both skins and at 375 px. Record what the demo user now sees.
- [ ] **Step 7: Commit** — `web+deploy: Playwright smoke (both skins, 375px), phase-2 deploy and selftest check`

---

# Phase 3 — Security surface

Ships: Security, Updates (reboot planner + live upgrade progress), DNS (stream, per-device profile, rules, wizard) and Topology.

---

### Task 28: `/security` payload — scorecards, Wi-Fi guard, checks with proof, attackers with geo, exposure

Hub-only. Concurrent with 29, 30, 31.

**Files:**
- Create: `hub/guardian_hub/payloads/security.py`, `hub/guardian_hub/geoip.py`, `hub/guardian_hub/routers/security.py`, `hub/tests/test_security.py`
- Modify: `hub/guardian_hub/routers/write.py` (add `POST /attacker`), `hub/guardian_hub/main.py`

**Interfaces:**
- `security.build(db, tenant) -> SecurityPayload`, reading `Scan.raw` (`ssh[*].sec`, `sites[*].sec`, `home`, `rogue`, `external_sites`, `admin_probe`, `email_sec`, `score`) plus `Alert`, `Fix` and `Device`.
- `security.scorecards(...) -> list[Scorecard]` — one per host, one per site, one for "Home network". `grade`/`score` per target from the same seven checks as the assets hardening grade for hosts, from the six header/TLS facts for sites, from the Wi-Fi/exposure/unverified facts for home. `deductions` = top 3 by points. `fix` is the `NextAction` for the highest-value repair (`POST /api/v1/fix` with the matching `FIX_ACTIONS` key) or `null` when nothing is auto-fixable.
- `security.checks(...) -> list[SecurityCheck]` — the checklist with proof. Each row carries `evidence` (the literal fact string that decided it, e.g. `"sshd -T: passwordauthentication no"`, `"ufw status: active"`, `"debsecan: 200 fixable"`, `"headers: strict-transport-security present"`), `last_verified` (the `Scan.ts` the fact came from) and `autofix` ∈ `"auto"|"manual"|"none"` from `fix.FIX_ACTIONS`/`MANUAL_ONLY` membership. Areas: SSH, Firewall, Brute force, Patching, Malware, Config drift, Web headers, TLS, Admin panels, Email, Exposure, Wi-Fi.
- `security.wifi_guard(...) -> WifiGuard` — one verdict line from `home.wifi` (SSID, encryption), `rogue.twin_channels` (evil twin), `rogue.dhcp_server != lan.expected_gateway` (rogue DHCP), `rogue.default_gateway` drift and the unverified-device count. `state` ∈ `good|warn|bad`.
- `security.attackers(db, tenant) -> list[Attacker]` — from `Alert` keys matching `sec:attacker:<host>:<ip>` plus `ssh[*].sec.top_attackers`; `fails`/`days` parsed from the alert message and evidence; `action`/`action_ts` from the newest `Fix` row whose `arg` is that IP; `geo` from `geoip.lookup`.
- `geoip.lookup(db, ip) -> Geo | None` — reads `IpGeo` first; on a miss calls `http://ip-api.com/json/<ip>?fields=status,country,countryCode,city,org,isp,lat,lon,hosting,reverse` with a 4 s timeout, stores the row, and re-uses it for 30 days. Private/reserved IPs return `None` without a call. Batched at most 15 lookups per request (ip-api's free tier is 45/min); the rest come back `null` and fill in on the next load. Failure is non-fatal — the payload still renders.
- `security.exposure(...) -> list[ExposureTile]` — WAN IP, open ports from the internet (`home.exposure.open_ports`), per-site external ports (`external_sites`), gateway ports, admin-panel probe hits, DMARC/SPF state.
- `GET /api/v1/security` (viewer+). `POST /api/v1/attacker {method}` (admin) where `method` ∈ `auto|block_ip|block_subnet|ban_long|limit_ssh|off` → writes `Edit(path="/api/attacker", body={"method": …})`, updates `Tenant.config["auto_fix"]["attacker_method"]`, audits `attacker.method`.

- [ ] **Step 1: Write the failing test** (`hub/tests/test_security.py`)
```python
from tests.test_seed_helpers import seeded_demo

def test_security_payload(client, monkeypatch):
    import guardian_hub.geoip as geoip
    monkeypatch.setattr(geoip, "_fetch", lambda ip: {"country": "Turkey", "country_code": "TR",
        "city": "Istanbul", "org": "ISP", "isp": "ISP", "lat": 41.0, "lon": 29.0,
        "hosting": False, "reverse": ""})
    s = client.get("/api/v1/security").json()
    assert s["scorecards"] and all(c["grade"] in "ABCDF" for c in s["scorecards"])
    assert s["wifi"]["state"] in ("good", "warn", "bad") and s["wifi"]["verdict"]
    assert s["checks"] and all(c["evidence"] and c["last_verified"] for c in s["checks"])
    assert all(c["autofix"] in ("auto", "manual", "none") for c in s["checks"])
    assert s["attackers"] and s["attackers"][0]["geo"]["country_code"] == "TR"
    assert s["exposure"] and s["attacker_method"] in s["methods"]

def test_geoip_skips_private_ips_and_caches(client, monkeypatch):
    from guardian_hub import geoip, models as m
    from guardian_hub.db import SessionLocal
    calls = []
    monkeypatch.setattr(geoip, "_fetch", lambda ip: calls.append(ip) or {"country": "X"})
    db = SessionLocal()
    assert geoip.lookup(db, "192.168.1.5") is None and calls == []
    geoip.lookup(db, "8.8.8.8"); geoip.lookup(db, "8.8.8.8")
    assert calls == ["8.8.8.8"] and db.query(m.IpGeo).count() == 1

def test_attacker_method_requires_admin_and_writes_an_edit(client):
    seeded_demo(client)
    from guardian_hub.db import SessionLocal
    from guardian_hub import models as m
    assert client.post("/api/v1/attacker", json={"method": "block_subnet"}).status_code == 200
    e = SessionLocal().query(m.Edit).filter_by(path="/api/attacker").one()
    assert e.body == {"method": "block_subnet"}
```
- [ ] **Step 2–4:** run → FAIL → implement → pass.
- [ ] **Step 5: Commit** — `hub: security payload (scorecards, wifi guard, checks with proof, attackers+geo, exposure)`

---

### Task 29: `/updates` payload, reboot planner, and sensor-reported upgrade progress

Hub + sensor. Concurrent with 28, 30, 31.

**Files:**
- Create: `hub/guardian_hub/payloads/updates.py`, `hub/guardian_hub/routers/updates.py`, `hub/tests/test_updates.py`, `sensor/tests/test_upgrade_progress.py`
- Modify: `hub/guardian_hub/routers/write.py` (`POST /reboot-plan`), `hub/guardian_hub/routers/sensor.py` (accept progress frames), `sensor/guardian_sensor/fix.py` (progress callback), `sensor/guardian_sensor/runner.py` (`run_queue` reports progress), `sensor/guardian_sensor/sync.py` (`HubClient.progress`)

**Interfaces:**
- `updates.build(db, tenant, days=30) -> UpdatesPayload`.
- `updates.calendar(db, tenant_id, days=30) -> list[PatchDay]` — one cell per (day, host): `patches` = count of `Fix` rows with `action in {"security_updates","full_upgrade"}` and `ok`, `reboots` = count of `reboot`/`rebooted:` events, `state` ∈ `"patched"|"reboot"|"failed"|"none"`. **Bucketed hub-side.**
- `updates.hosts(...) -> list[UpdateHost]` — from `Scan.raw["ssh"][*]["updates"]` + `sec`, the port of the Nexus "updates" report section (`server.py:426–431`).
- `updates.cves(...) -> list[CveRow]` — flattens `ssh[*].cve_list` (`{cve, package, urgency}`) with `host`, `fixable` (`urgency != "(fixed)"`), `fixed_on` (the `Fix.ts` of the first successful `full_upgrade`/`security_updates` after the CVE first appeared, else `null`) and `tracker` = `https://security-tracker.debian.org/tracker/<cve>`.
- `updates.progress(db, tenant_id) -> list[UpgradeProgress]` — from `Queue` rows whose `path == "/api/fix"` and `body.action` ∈ `{"security_updates","full_upgrade"}` in the last 24 h; `done`/`total`/`log_tail` come from `Queue.result["progress"]` written by the sensor.
- `updates.reboot_plan(db, tenant) -> RebootPlan` — `hosts` = every host with `reboot_required`, with `reason` from the alert message and `services` = the containers/services the hub expects back (`Host.last["docker"]` names + `services` keys); `windows` = four presets (Now, Tonight 02:00–05:00, This weekend Sat 02:00–05:00, Next maintenance window from `Tenant.config["auto_fix"]["maintenance_window"]`).
- `POST /api/v1/reboot-plan {host, window_start, window_end}` (admin) → `Queue(path="/api/reboot-plan", body={host, window_start, window_end})`, audited `reboot.plan`. The sensor already honours the window (`runner.run_queue`, `runner.py:116`).
- Sensor: `fix.apply_fix(..., progress=None)` — an optional `progress(done: int, total: int, line: str)` callback. For `security_updates` and `full_upgrade` the ssh command is wrapped so its stdout is streamed line by line; the counter parses `^(Get|Unpacking|Setting up|Preparing) ` lines against the `Inst ` count captured before the run. `runner.run_queue` passes a callback that calls `client.progress(qid, done, total, line)`; `HubClient.progress(qid, done, total, line)` POSTs `/api/v1/sensor/queue/{qid}/progress {done,total,line}`, which merges into `Queue.result["progress"]` and publishes a `live` `upgrade` frame. Progress posts are throttled to one every 3 s.

- [ ] **Step 1: Write the failing tests**

`hub/tests/test_updates.py`:
```python
from tests.test_seed_helpers import seeded_demo

def test_updates_payload(client):
    seeded_demo(client)
    u = client.get("/api/v1/updates").json()
    assert u["calendar"] and all(c["state"] in ("patched","reboot","failed","none") for c in u["calendar"])
    assert u["hosts"] and u["cves"]
    assert all(c["tracker"].startswith("https://security-tracker.debian.org/tracker/CVE-") for c in u["cves"])
    assert u["reboot_plan"]["windows"] and len(u["reboot_plan"]["windows"]) == 4

def test_reboot_plan_queues_with_a_window_and_progress_lands(client):
    tid = seeded_demo(client)
    qid = client.post("/api/v1/reboot-plan", json={"host": "safehome",
        "window_start": "02:00", "window_end": "05:00"}).json()["id"]
    # the sensor picks the queue up, then reports progress on a separate upgrade job
    fix_id = client.post("/api/v1/fix", json={"host": "prod", "action": "full_upgrade"}).json()["id"]
    H = {"Authorization": f"Bearer {enrol(client, tid)}"}
    client.get("/api/v1/sensor/queue", headers=H)
    client.post(f"/api/v1/sensor/queue/{fix_id}/progress",
                json={"done": 12, "total": 40, "line": "Setting up libc6"}, headers=H)
    prog = client.get("/api/v1/updates").json()["progress"]
    assert any(p["queue_id"] == fix_id and p["done"] == 12 and p["total"] == 40 for p in prog)
    assert client.get(f"/api/v1/queue/{qid}").json()["path"] == "/api/reboot-plan"
```
(`enrol` is the existing helper in `tests/test_sensor_api.py`, imported at the top of this module.)

`sensor/tests/test_upgrade_progress.py`:
```python
from guardian_sensor import fix

def test_apply_fix_reports_progress_lines():
    seen = []
    lines = ["Get:1 http://deb.debian.org sid/main libc6", "Unpacking libc6", "Setting up libc6", "done"]
    def runner(h, cmd, timeout=120, on_line=None):
        for l in lines:
            if on_line: on_line(l)
        return "\n".join(lines)
    cfg = {"ssh_hosts": [{"name": "prod", "host": "1.2.3.4", "user": "root"}],
           "auto_fix": {"enabled": True, "max_per_day": 20}}
    r = fix.apply_fix(cfg, "prod", "full_upgrade", by="test", lock=False, runner=runner,
                      progress=lambda done, total, line: seen.append((done, line)))
    assert r["ok"] and [d for d, _ in seen] == [1, 2, 3]
    assert seen[-1][1] == "Setting up libc6"
```
- [ ] **Step 2–4:** run both suites → FAIL → implement → pass under Python 3.10 for the sensor.
- [ ] **Step 5: Commit** — `hub+sensor: updates payload, patch calendar, CVE table, reboot planner, live upgrade progress`

---

### Task 30: `/dns` payload, WebSocket query stream, block/allow rules, setup wizard

Hub + sensor. Concurrent with 28, 29, 31.

**Files:**
- Create: `hub/guardian_hub/payloads/dnsview.py`, `hub/guardian_hub/routers/dns.py`, `hub/tests/test_dns.py`, `sensor/tests/test_dns_rules_edit.py`
- Modify: `hub/guardian_hub/routers/write.py` (`POST /dns/rules`, `POST /reset-lan-baseline`), `sensor/guardian_sensor/sync.py` (`apply_edit` handles `/api/dns/rules`)

**Interfaces:**
- `dnsview.build(db, tenant, hours=24) -> DnsPayload` — `status` from `Scan.raw["dns_filter"]`; `top`, `blocked_top`, `beaconing`, `suspicious` from the same block (already computed by the sensor's `dnsstats.dns_analyse`); `clients` joins `DnsQuery.mac` to `Device` so each client row carries a real device name; `lists` from `dns_filter.status.lists` with per-list `blocked_24h` counted from `DnsQuery.list`; `rules` from `DnsRule` rows; `wizard` from `Tenant.config["lan"]["expected_gateway"]`, the sensor's LAN IP (`Scan.raw["home"]["gateway"]` / `dns_filter`), `working = dns_filter.running and queries_24h > 0`, and `clients_using = len({DnsQuery.client})`.
- `dnsview.device_profile(db, tenant_id, mac, hours=24) -> DeviceDnsProfile` — reused by the device drawer (Task 19 calls it once this lands; until then the drawer's `dns` field is computed inline there and the two must produce the same shape — this task replaces the inline version with a call, and `test_assets.py` must still pass).
- `GET /api/v1/dns?hours=24` (viewer+); `GET /api/v1/dns/stream` is **not** a route — the stream is the `dns` frame type on `WS /ws`, opted into with `{"subscribe": ["dns"]}`. The hub publishes a `dns` frame per ingested `DnsQuery` row, capped at 20 frames/second per tenant (excess dropped, with a `dropped` counter on the next frame).
- `POST /api/v1/dns/rules {kind: "allow"|"block", domain, op: "add"|"remove"}` (admin) → upserts/deletes a `DnsRule`, recomputes `Tenant.config["dns"]["allow"|"block"]` from the table, writes `Edit(path="/api/dns/rules", body={"allow": [...], "block": [...]})`, audits `dns.rule`. Domains are normalised (lower-cased, trailing dot stripped, `www.` kept) and validated against `^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$`.
- Sensor `sync.apply_edit`: add `"/api/dns/rules"` to `EDIT_PATHS` and a branch setting `cfg.setdefault("dns", {})["allow"|"block"]` from the body, returning `"config"`. No reload code is needed — `dnsfilter.housekeeping()` already re-reads the config every 60 s (`dnsfilter.py:345`).
- `POST /api/v1/reset-lan-baseline` (admin) → `Edit(path="/api/reset-lan-baseline", body={})`, audited. The sensor branch already exists (`sync.py:317`).

- [ ] **Step 1: Write the failing tests**

`hub/tests/test_dns.py`:
```python
from tests.test_seed_helpers import seeded_demo

def test_dns_payload_joins_clients_to_devices(client):
    seeded_demo(client)
    d = client.get("/api/v1/dns").json()
    assert d["status"]["running"] and d["status"]["queries_24h"] > 0
    assert d["top"] and d["blocked_top"] and d["lists"]
    assert d["clients"] and any(c["name"] and c["mac"] for c in d["clients"])
    assert d["wizard"]["resolver_ip"] and d["wizard"]["working"] is True

def test_rules_validate_normalise_and_write_one_edit(client):
    seeded_demo(client)
    from guardian_hub.db import SessionLocal
    from guardian_hub import models as m
    assert client.post("/api/v1/dns/rules", json={"kind": "block", "domain": "Ads.Example.COM.",
                                                  "op": "add"}).status_code == 200
    assert client.post("/api/v1/dns/rules", json={"kind": "block", "domain": "not a domain",
                                                  "op": "add"}).status_code == 422
    db = SessionLocal()
    assert db.query(m.DnsRule).filter_by(kind="block", domain="ads.example.com").count() == 1
    e = db.query(m.Edit).filter_by(path="/api/dns/rules").order_by(m.Edit.id.desc()).first()
    assert "ads.example.com" in e.body["block"]

def test_dns_frames_only_reach_subscribers(client):
    tid = seeded_demo(client)
    with client.websocket_connect("/api/v1/ws") as plain:
        assert plain.receive_json()["type"] == "ready"
        with client.websocket_connect("/api/v1/ws") as sub:
            sub.send_json({"subscribe": ["dns"]}); assert sub.receive_json()["type"] == "ready"
            # ingest one dns query as the sensor would; the hub publishes a `dns` frame
            H = {"Authorization": f"Bearer {enrol(client, tid)}"}
            client.post("/api/v1/sensor/ingest", headers=H, json={"last": {}, "state": {},
                "events": [], "history": [], "fixes": [], "dns_queries": [
                    {"ts": "2026-09-09T18:31:00-04:00", "client": "192.168.1.159",
                     "name": "ads.example.com", "type": "A", "blocked": True,
                     "list": "stevenblack", "ms": 1}]})
            assert sub.receive_json()["type"] == "dns"
```

`sensor/tests/test_dns_rules_edit.py`:
```python
from guardian_sensor import sync

def test_dns_rules_edit_updates_config_only():
    cfg, st = {"dns": {"enabled": True}}, {}
    touched = sync.apply_edit({"path": "/api/dns/rules",
                               "body": {"allow": ["ok.com"], "block": ["ads.example.com"]}}, st, cfg)
    assert touched == "config"
    assert cfg["dns"]["block"] == ["ads.example.com"] and cfg["dns"]["allow"] == ["ok.com"]
    assert st == {}
```
- [ ] **Step 2–4:** run → FAIL → implement → pass (hub and sensor, sensor under Python 3.10).
- [ ] **Step 5: Commit** — `hub+sensor: DNS payload, live query stream, block/allow rules, setup wizard, LAN baseline reset`

---

### Task 31: `/topology` payload

Hub-only. Concurrent with 28, 29, 30.

**Files:**
- Create: `hub/guardian_hub/payloads/topology.py`, `hub/guardian_hub/routers/topology.py`, `hub/tests/test_topology.py`
- Modify: `hub/guardian_hub/main.py`

**Interfaces:**
- `topology.build(db, tenant, cluster_by: str = "kind") -> TopologyPayload`. Nodes: one `internet` node, one `router` node (the device whose MAC equals `Scan.raw["lan"]["gw_mac"]`), one `extender` node per device whose `kind == "extender"`, and one node per remaining `Device`; plus one `server` node per `Host` and one `site` node per `Site`, both attached to `internet`.
- Node fields the UI draws directly: `icon` (a stable key from `assets.kind_of`: `router|extender|camera|printer|phone|tablet|laptop|server|tv|speaker|appliance|plug|unknown|site|internet`), `state` ∈ `online|offline|sleeping|flapping|archived` (from `Device.presence` + liveness), `ring` ∈ `none|good|warn|alert` (alert when untrusted **and** `kind == "camera"`, or `risk >= 60`; warn when unverified or `risk >= 40`), `pulse` (`true` when the device appeared in the newest scan), `risk`, `detail` (a one-line hover card string: `"{label} · {ip} · {vendor} · risk {risk}"`).
- Links: each device → its `parent` when set, else → the extender it is closest to when `Device.extra["via"]` names one, else → the router. Router → internet. `TopoLink.kind` ∈ `lan|wan|uplink`; `pulse` mirrors the child node.
- `clusters`: with `cluster_by="kind"` one cluster per `KIND_GROUPS` value; with `cluster_by="area"` one per the `area` key in `Device.extra` (falling back to a single "Home" cluster). The layout itself is the client's job; the hub supplies membership.
- `GET /api/v1/topology?cluster_by=kind|area` (viewer+).

- [ ] **Step 1: Write the failing test** (`hub/tests/test_topology.py`)
```python
from tests.test_seed_helpers import seeded_demo

def test_topology_is_a_connected_graph_the_ui_can_draw(client):
    seeded_demo(client)
    t = client.get("/api/v1/topology").json()
    ids = {n["id"] for n in t["nodes"]}
    assert "internet" in ids and any(n["kind"] == "router" for n in t["nodes"])
    assert all(l["source"] in ids and l["target"] in ids for l in t["links"])
    assert all(n["ring"] in ("none", "good", "warn", "alert") for n in t["nodes"])
    assert all(n["icon"] for n in t["nodes"])
    reachable, frontier = {"internet"}, ["internet"]
    adj = {}
    for l in t["links"]:
        adj.setdefault(l["source"], []).append(l["target"]); adj.setdefault(l["target"], []).append(l["source"])
    while frontier:
        n = frontier.pop()
        for x in adj.get(n, []):
            if x not in reachable: reachable.add(x); frontier.append(x)
    assert reachable == ids, f"orphans: {sorted(ids - reachable)}"

def test_cluster_by_area_falls_back_to_one_cluster(client):
    seeded_demo(client)
    c = client.get("/api/v1/topology?cluster_by=area").json()["clusters"]
    assert c and sum(len(x["members"]) for x in c) >= 20
```
- [ ] **Step 2–4:** run → FAIL → implement → pass.
- [ ] **Step 5: Commit** — `hub: topology payload (nodes, links, clusters)`

---

### Task 32: Security tab, including the attack map

Web-only. Depends on 22, 23; concurrent with 33, 34, 35.

**Files:**
- Create: `web/src/pages/Security.tsx`, `web/src/components/{ScorecardGrid.tsx,WifiGuardPanel.tsx,CheckList.tsx,AttackMap.tsx,ExposureTiles.tsx,AttackerMethod.tsx}`, `web/src/assets/world-110m.ts`, `web/tests/security.test.tsx`
- Modify: `web/src/App.tsx` (route `/security`)

**Interfaces:**
- `ScorecardGrid({scorecards, onFix})` — one card per target: grade letter, score, top 3 deductions, a "Fix now" button when `scorecard.fix` is set (POSTs it, toasts, then polls `GET /api/v1/queue/{id}`).
- `WifiGuardPanel({wifi})` — one big verdict line with tone, then the four facts (SSID + encryption, evil twin, rogue DHCP, unknown devices) as rows with pass/fail.
- `CheckList({checks})` — grouped by `area`; each row shows pass/fail, title, `last_verified` as relative time, the `evidence` line in mono type, and an auto-fix chip (`Auto` / `Manual` / `—`).
- `AttackMap({attackers})` — `d3-geo`'s `geoNaturalEarth1` projection over a bundled world outline. `web/src/assets/world-110m.ts` is generated once at build-setup time from the pinned `world-atlas` package:
  ```bash
  cd web && node -e "const t=require('world-atlas/countries-110m.json');const {feature}=require('topojson-client');\
  const g=feature(t,t.objects.countries);require('fs').writeFileSync('src/assets/world-110m.ts',\
  'export const WORLD = '+JSON.stringify(g)+' as const;\n')"
  ```
  Markers are sized by `fails`, tinted by whether Guardian acted (`--color-good` when `action` is set, `--color-alert` when not), and clicking one opens a `Drawer` with `ip`, `geo.org`, `fails`, `days`, the action Guardian took and a "Block this /24" button (`POST /api/v1/fix {action:"block_subnet"}`). Attackers with `geo === null` appear in a "Location unknown" list beside the map, never dropped.
- `ExposureTiles({tiles})` — `Stat` cards with tone from `tile.state`.
- `AttackerMethod({value, methods, onChange})` — a select posting `/api/v1/attacker`; disabled for non-admins with a tooltip naming the role that can change it.

- [ ] **Step 1: Write the failing test** (`web/tests/security.test.tsx`)
```tsx
it("shows a grade, the wifi verdict and evidence on every check", async () => {
  renderPage(<Security />, { security: securityFixture });
  expect(await screen.findByText(/Security posture/)).toBeInTheDocument();
  expect(screen.getByTestId("wifi-verdict")).toHaveTextContent(securityFixture.wifi.verdict);
  for (const row of screen.getAllByTestId("check-row")) expect(row).toHaveTextContent(/verified/i);
});
it("plots located attackers and lists the unlocated ones", async () => {
  renderPage(<Security />, { security: securityFixture });
  const located = securityFixture.attackers.filter((a) => a.geo);
  expect(await screen.findAllByTestId("attack-marker")).toHaveLength(located.length);
  expect(screen.getByTestId("attackers-unlocated")).toHaveTextContent(
    String(securityFixture.attackers.length - located.length));
});
it("disables the attacker-method select for a viewer", async () => {
  renderPage(<Security />, { security: securityFixture, me: viewerMe });
  expect(await screen.findByLabelText(/attacker method/i)).toBeDisabled();
});
```
- [ ] **Step 2–4:** run → FAIL → implement → tests, `tsc -b`, lint clean. Check the map's viewBox scales at 375 px.
- [ ] **Step 5: Commit** — `web: Security tab (scorecards, Wi-Fi guard, checks with proof, attack map, exposure, attacker method)`

---

### Task 33: Updates tab

Web-only. Depends on 22, 23; concurrent with 32, 34, 35.

**Files:**
- Create: `web/src/pages/Updates.tsx`, `web/src/components/{PatchCalendar.tsx,CveTable.tsx,UpgradeProgressPanel.tsx,RebootPlanner.tsx}`, `web/tests/updates.test.tsx`
- Modify: `web/src/App.tsx` (route `/updates`)

**Interfaces:**
- `PatchCalendar({calendar})` — a `Heatmap`-style grid, one row per host and one column per day, cell tone from `PatchDay.state`, a reboot mark (a small dot) when `reboots > 0`, hover shows "3 patches · 1 reboot · 14 Aug".
- `CveTable({cves})` — the `Table` primitive with columns CVE / package / urgency / host / fixable / fixed on / tracker link, a "critical only" toggle (filters `urgency` in `high|critical`), and a text filter. Sorting is local.
- `UpgradeProgressPanel({progress})` — one card per in-flight job with a determinate bar (`done / total`), the host, the action label and a 10-line log tail; subscribes to the `upgrade` live frame and updates without a refetch; on `status: "done"` shows the result and stops.
- `RebootPlanner({plan, onSchedule})` — one card per candidate host: reason, the services Guardian will confirm afterwards, a window picker built from `plan.windows`, and a Schedule button POSTing `/api/v1/reboot-plan`. After scheduling the card shows the queue status polled from `GET /api/v1/queue/{id}` and, when done, the confirmation line.

- [ ] **Step 1: Write the failing test** (`web/tests/updates.test.tsx`)
```tsx
it("renders one calendar cell per hub row and marks reboots", async () => {
  renderPage(<Updates />, { updates: updatesFixture });
  expect(await screen.findAllByTestId("patch-cell")).toHaveLength(updatesFixture.calendar.length);
  expect(screen.getAllByTestId("reboot-mark")).toHaveLength(
    updatesFixture.calendar.filter((c) => c.reboots > 0).length);
});
it("filters the CVE table to critical only", async () => {
  renderPage(<Updates />, { updates: updatesFixture });
  const all = (await screen.findAllByTestId("cve-row")).length;
  await userEvent.click(screen.getByRole("checkbox", { name: /critical only/i }));
  expect(screen.getAllByTestId("cve-row").length).toBeLessThan(all);
});
it("advances the upgrade bar from a live frame", async () => {
  const { emit } = renderPage(<Updates />, { updates: updatesFixture });
  emit({ type: "upgrade", ts: "…", data: { queue_id: updatesFixture.progress[0].queue_id,
        host: "prod", done: 30, total: 40 } });
  expect(await screen.findByRole("progressbar")).toHaveAttribute("aria-valuenow", "30");
});
it("schedules a reboot in the chosen window", async () => {
  const { posted } = renderPage(<Updates />, { updates: updatesFixture });
  await userEvent.selectOptions(await screen.findByLabelText(/window/i), "Tonight 02:00–05:00");
  await userEvent.click(screen.getByRole("button", { name: /schedule reboot/i }));
  expect(posted("/api/v1/reboot-plan")).toMatchObject({ window_start: "02:00", window_end: "05:00" });
});
```
- [ ] **Step 2–4:** run → FAIL → implement → clean.
- [ ] **Step 5: Commit** — `web: Updates tab (patch calendar, CVE table, live upgrade progress, reboot planner)`

---

### Task 34: DNS tab

Web-only. Depends on 22, 23; concurrent with 32, 33, 35.

**Files:**
- Create: `web/src/pages/Dns.tsx`, `web/src/components/{QueryStream.tsx,DnsProfile.tsx,RuleManager.tsx,DnsWizard.tsx}`, `web/tests/dns.test.tsx`
- Modify: `web/src/App.tsx` (route `/dns`)

**Interfaces:**
- `QueryStream({rows, paused, onPause, filter, onFilter})` — a virtualised (windowed by a simple slice of the newest 300) list fed by the `dns` live frame after `subscribe(["dns"])`; each row shows time, a client badge (device label from `row.device`, falling back to the IP), the domain, the qtype, a blocked pill with the list name and the latency. Pause freezes the buffer (frames keep accumulating up to 1 000, then drop oldest). The search box filters the buffer.
- `DnsProfile({mac})` — the per-device profile: 24 h/7 d query and block counts, top domains, top blocked; reachable from a client badge click and from the device drawer.
- `RuleManager({rules, lists, onAdd, onRemove, onToggleList})` — two lists (allow, block) with add/remove, plus the subscribed block lists with per-list entry counts and `blocked_24h`; list toggles write through `POST /api/v1/dns/rules`' sibling `POST /api/v1/config` path already used for `dns.lists`.
- `DnsWizard({wizard})` — three steps: "Point your router's DNS at **{resolver_ip}**" with a copy button and a router-specific hint, "Reboot or renew the lease on one device", and a live check that turns green when `wizard.working && wizard.clients_using > 0`, re-polled every 10 s while the wizard is open.

- [ ] **Step 1: Write the failing test** (`web/tests/dns.test.tsx`)
```tsx
it("streams frames, pauses, and keeps the buffer", async () => {
  const { emit } = renderPage(<Dns />, { dns: dnsFixture });
  emit({ type: "dns", ts: "…", data: { ts: "…", mac: "aa:bb", name: "ads.example.com",
        blocked: true, list: "stevenblack", qtype: "A", ms: 1, client: "192.168.1.159",
        device: "Windows PC" } });
  expect(await screen.findByText("ads.example.com")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /pause/i }));
  emit({ type: "dns", ts: "…", data: { name: "second.example.com", blocked: false } });
  expect(screen.queryByText("second.example.com")).toBeNull();
});
it("adds a block rule through the hub route", async () => {
  const { posted } = renderPage(<Dns />, { dns: dnsFixture });
  await userEvent.type(await screen.findByLabelText(/block a domain/i), "tracker.example.com{Enter}");
  expect(posted("/api/v1/dns/rules")).toEqual({ kind: "block", domain: "tracker.example.com", op: "add" });
});
it("shows the wizard as working when the hub says so", async () => {
  renderPage(<Dns />, { dns: { ...dnsFixture, wizard: { ...dnsFixture.wizard, working: true, clients_using: 3 } } });
  expect(await screen.findByTestId("wizard-check")).toHaveTextContent(/working/i);
});
```
- [ ] **Step 2–4:** run → FAIL → implement → clean.
- [ ] **Step 5: Commit** — `web: DNS tab (live stream, per-device profile, rule manager, setup wizard)`

---

### Task 35: Topology tab

Web-only. Depends on 22, 23; concurrent with 32, 33, 34.

**Files:**
- Create: `web/src/pages/Topology.tsx`, `web/src/components/{ForceGraph.tsx,DeviceIcon.tsx,HoverCard.tsx}`, `web/tests/topology.test.tsx`
- Modify: `web/src/App.tsx` (route `/topology`)

**Interfaces:**
- `ForceGraph({nodes, links, clusters, clusterBy, onSelect})` — `d3-force` simulation (`forceLink`, `forceManyBody`, `forceCollide`, plus `forceX`/`forceY` pulling each node toward its cluster centroid) run in a `useEffect` that **stops and nulls the simulation on cleanup**, so React 19's StrictMode double-mount does not leave two simulations running. React renders the SVG from simulation ticks throttled to `requestAnimationFrame`; drag uses pointer events and pins a node (`fx`/`fy`) until double-click releases it. Zoom/pan through `d3-zoom`-free manual transform state (wheel + drag on the background) so no extra dependency is needed.
- `DeviceIcon({icon, size})` — one inline SVG per `TopoNode.icon` key, all 14 keys covered, `currentColor` stroked.
- Node chrome: a status ring (`online` solid, `sleeping` dashed, `offline` faint), a pulsing halo when `pulse`, the risk badge for `risk >= 40`, a red ring for `ring === "alert"`.
- `HoverCard` on hover with `node.detail`; click opens the device drawer at `/assets/devices/:mac` (reusing Task 26's component) or, for a host/site, its drawer.
- A `Chips` control switches `cluster_by` between Kind and Area (re-queries `/api/v1/topology`). New devices arriving on the `device` live frame animate in.

- [ ] **Step 1: Write the failing test** (`web/tests/topology.test.tsx`)
```tsx
it("renders one node and one link element per hub row", async () => {
  renderPage(<Topology />, { topology: topologyFixture });
  expect(await screen.findAllByTestId("topo-node")).toHaveLength(topologyFixture.nodes.length);
  expect(screen.getAllByTestId("topo-link")).toHaveLength(topologyFixture.links.length);
});
it("starts exactly one simulation under StrictMode and stops it on unmount", () => {
  const spy = vi.spyOn(d3Force, "forceSimulation");
  const { unmount } = renderPage(<Topology />, { topology: topologyFixture }, { strict: true });
  expect(spy).toHaveBeenCalledTimes(1);
  const sim = spy.mock.results[0].value;
  unmount();
  expect(sim.alpha()).toBe(0);
});
it("opens the device drawer on click", async () => {
  renderPage(<Topology />, { topology: topologyFixture, device: deviceDetailFixture });
  await userEvent.click((await screen.findAllByTestId("topo-node"))[1]);
  expect(await screen.findByRole("dialog")).toBeInTheDocument();
});
```
- [ ] **Step 2–4:** run → FAIL → implement → clean. Manual: drag, zoom, pan, switch clustering, confirm 60 fps with 24 devices.
- [ ] **Step 5: Commit** — `web: Topology tab (force-directed map, icons, rings, clusters, hover card)`

---

### Task 36: Phase 3 closeout — smoke, deploy, live verification

Serial. Depends on 28–35.

**Files:** Modify `web/e2e/smoke.spec.ts` (add the four new tabs), `hub/guardian_hub/selftest.py` (add DNS-resolve/block and security-payload checks)

- [ ] **Step 1:** extend `smoke.spec.ts`: Security shows a grade and at least one attack marker or the unlocated list; Updates shows a calendar and a CVE row; DNS shows a stream row within 15 s of subscribing (seeded queries are replayed on connect) and adds a block rule; Topology renders nodes and opens a drawer. All three projects (light, dark, 375 px).
- [ ] **Step 2:** `cd hub && pytest -q` and `cd web && npm test && npx tsc -b && npm run lint && npx playwright test` → all green.
- [ ] **Step 3:** `selftest.run` gains `_check_dns()` (the tenant's newest scan has `dns_filter.running` and `blocked_24h >= 0`) and `_check_security()` (`/api/v1/security` returns scorecards) — both `_skip` when the tenant has no sensor.
- [ ] **Step 4: Deploy** — `deploy/deploy.sh`; on the VPS `guardian selftest` → all ok.
- [ ] **Step 5: Browser check** — Security, Updates, DNS and Topology on the live site in both skins and at 375 px; confirm the DNS stream ticks and a topology drag works.
- [ ] **Step 6: Commit** — `web+hub: phase-3 smoke coverage, selftest DNS/security checks, deploy`

---

# Phase 4 — Product surface

Ships: Reports (builder, library, share, schedule), the Settings area, all nine Integrations, the public/ops endpoints, and the Guardian chat panel that acts.

---

### Task 37: Report engine — server-rendered HTML, all sections, presets, compliance

Hub-only. **Blocks Tasks 38 and 45.**

**Files:**
- Create: `hub/guardian_hub/reports/{__init__.py,builder.py,sections.py,presets.py,compliance.py,css.py}`, `hub/tests/test_report_builder.py`
- Modify: none

**Interfaces:**
- `builder.build_report(db, tenant, days=7, sections=None, title=None, preset=None) -> str` — the port of Nexus `build_report` (`~/.claude/nexus-agent/ui/server.py:360–521`), rendering the same 19 sections with the same numbering, KPI cards, tables, heartbeat bars and inline SVG charts. **Guardian branding replaces NEXUS on the cover** (`<b>GUARDIAN</b> home &amp; infrastructure security` and the tenant's own domain, from `Tenant.brand`), and the footer says "Generated by Guardian".
- `sections.py` helpers ported one-for-one, with Guardian names: `esc` (← `_H`, 305), `pct` (← `_pct`, 306), `svg_line` (← `_svg_line`, 307), `svg_bars` (← `_svg_bars`, 321), `heartbeat_row` (← `_hb`, 328), `kpis` (← `_kpi`, 330), `table` (← `_tbl`, 331), `pill` (← `_pill`, 333), `ago_hours` (← `_ago_h`, 334).
- `compliance.py`: `COMPLIANCE` — the same twelve controls as Nexus `COMPLIANCE` (`server.py:338–351`), each `(area, control, predicate)` where the predicate reads the `last` dict; `rows(last) -> list[dict]` (← `compliance_rows`, 352).
- `sections.SECTION_KEYS` (ordered list of the 19 keys) and `sections.SECTION_TITLES: dict[str, str]` (key → the human title printed as the section heading), both ported from Nexus `REPORT_SECTIONS` + `DEVICE_SECTIONS` (`server.py:298–303`). `builder.last_of(db, tenant) -> dict` returns `scans.latest_scan(db, tenant.id).raw` (or `{}`), and is the single accessor every section uses. The key order:
  `summary, availability, servers, updates, home, devices, presence, alerts, fixes, internet, email, compliance, recommendations, dev_cameras, dev_people, dev_media, dev_smart, dev_net, dev_unknown`.
- `presets.PRESETS` — **new in Guardian; Nexus has no preset concept.** An ordered mapping `key -> (label, [section keys])`:

| key | label | sections |
|---|---|---|
| `full` | Full report | every key in `SECTION_KEYS` |
| `executive` | Executive summary | summary, availability, compliance, recommendations |
| `security` | Security review | summary, servers, updates, home, alerts, fixes, email, compliance, recommendations |
| `network` | Network & devices | summary, home, devices, presence, internet, dev_net |
| `ops` | Operations | summary, availability, servers, updates, fixes, internet, alerts |
| `compliance` | Compliance pack | summary, compliance, servers, updates, email, alerts, recommendations |
| `cameras` | Cameras only | summary, dev_cameras, home, alerts, recommendations |
| `people` | Phones, tablets & computers | summary, dev_people, presence, home, recommendations |
| `tvs` | TVs & media only | summary, dev_media, presence, home |
| `smart_home` | Smart home | summary, dev_smart, home, devices, recommendations |
| `network_gear` | Network gear | summary, dev_net, home, internet, devices |

- **Data-source translation table** — Nexus read flat files; Guardian reads tables. This mapping *is* the work of this task:

| Nexus source | Guardian source |
|---|---|
| `load("last")` | `scans.latest_scan(db, tenant.id).raw` |
| `load("state")["alerts_db"]` | `Alert` rows for the tenant. Nexus `state` → `Alert.status`; Nexus `resolved_at` → `Alert.last_seen` when `status == "resolved"`, else `None`; Nexus `resolved_by` → `Alert.resolved_by`. |
| `load("state")["lan"]["known_macs"]` | `Device` rows (`extra` carries anything not columnar) |
| `load("config")` | `Tenant.config`, `Tenant.settings["profile"]`, `Tenant.brand` |
| `jsonl("history.jsonl", since=cut)` | `Metric` rows regrouped by `ts` into the Nexus history-line shape by `builder.history_lines(db, tenant_id, since) -> list[dict]` (`{ts, sites:{name:{ok,lat,cert}}, hosts:{name:{disk,mem,load,failed}}, lan, score, speed}`) |
| `h["live_macs"]` | `PresenceSample` rows, grouped by `ts` inside `history_lines` |
| `jsonl("remediation.jsonl")` | `Fix` rows since the cut |
| `jsonl("events.jsonl")` | `Event` rows since the cut |
| `last["incidents"]` | `Incident` rows (open, or opened within the period) |
| `last["recommendations"]` | `Recommendation` rows |

- `builder.render_to_file(db, tenant, **kw) -> (Report, Path)` — writes `settings.data_dir / "reports" / tenant.slug / f"{kind}-{YYYY-MM-DD}-{short id}.html"` and inserts the `Report` row (`title`, `preset`, `sections`, `period_days`, `meta={"score": …, "scans": …, "alerts": …}`).

- [x] **Step 1: Write the failing test** (`hub/tests/test_report_builder.py`)
```python
import re
from tests.test_seed_helpers import seeded_demo
from guardian_hub.db import SessionLocal
from guardian_hub import models as m
from guardian_hub.reports import builder, presets, sections, compliance

def test_full_report_has_every_section_and_no_nexus_branding(client):
    seeded_demo(client)
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    html = builder.build_report(db, t, days=30, preset="full")
    for _, title in sections.SECTION_TITLES.items():
        assert title in html, title
    assert "GUARDIAN" in html and "NEXUS" not in html.upper().replace("GUARDIAN", "")
    assert re.search(r"<section class=\"sec\"><h2><span class=\"num\">01</span>", html)
    assert "<svg" in html and "@page" in html

def test_presets_are_subsets_in_canonical_order(client):
    for key, (label, keys) in presets.PRESETS.items():
        assert label and set(keys) <= set(sections.SECTION_KEYS), key
        assert keys == [k for k in sections.SECTION_KEYS if k in keys], f"{key} out of order"
    assert presets.PRESETS["full"][1] == sections.SECTION_KEYS

def test_history_lines_rebuild_the_nexus_shape(client):
    seeded_demo(client)
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    rows = builder.history_lines(db, t.id, since="2000-01-01T00:00:00+00:00")
    assert rows and set(rows[0]) >= {"ts", "sites", "hosts", "lan", "score", "live_macs"}
    assert isinstance(rows[0]["sites"], dict) and "ok" in next(iter(rows[0]["sites"].values()))
    assert rows == sorted(rows, key=lambda r: r["ts"])

def test_compliance_answers_twelve_controls(client):
    seeded_demo(client)
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    rows = compliance.rows(builder.last_of(db, t))
    assert len(rows) == 12 and all(set(r) == {"area", "control", "met"} for r in rows)
```
- [x] **Step 2–4:** run → FAIL → implement → pass. Open the produced HTML in a browser and print-preview it to confirm the print CSS holds.
- [x] **Step 5: Commit** — `hub: report engine (19 sections, 11 presets, compliance, Guardian branding)`

---

### Task 38: Reports API — library, build, share links, schedule

Hub-only. Depends on 37.

**Files:**
- Create: `hub/guardian_hub/routers/reports.py`, `hub/tests/test_reports_api.py`
- Modify: `hub/guardian_hub/main.py`, `hub/guardian_hub/scheduler.py` *(created here if phase 1 left the APScheduler wiring in `main.py`; otherwise extend it)*

**Interfaces:**
- `GET /api/v1/reports` (viewer+) → `ReportsPayload` — `library` newest first, `presets` from `presets.PRESETS`, `sections` from `sections.SECTION_TITLES`, `schedule` from `Tenant.settings["report_schedule"]`.
- `GET /api/v1/reports/{id}` (viewer+) → the stored HTML with `Content-Type: text/html`.
- `GET /api/v1/reports/preview?days=&preset=&sections=` (viewer+) → freshly rendered HTML, **not** persisted; this is what the builder's live preview iframe loads. Rate-limited to 20/min/tenant.
- `POST /api/v1/reports/build {days, preset, sections, title}` (operator) → renders, persists, returns `ReportCard`; audited `report.build`.
- `POST /api/v1/reports/{id}/share {hours}` (operator, `hours` ≤ 720) → mints `share_token` (32 hex) and `share_expires`, returns `{share_url}`; audited `report.share`. Re-sharing rotates the token, invalidating the old link.
- `DELETE /api/v1/reports/{id}` (admin) → removes the row and the file; audited.
- `POST /api/v1/reports/schedule {weekly, monthly, preset, hour, ntfy}` (admin) → writes `Tenant.settings["report_schedule"]`; audited.
- Scheduler job `reports_job()` running hourly: for each tenant whose schedule is due (Monday for weekly, the 1st for monthly, at the configured hour in the tenant's local offset taken from the newest `Scan.ts`), build the report, mint a 30-day share link and push it through the integration registry at severity `INFO`. Idempotent: a report of that `kind` already created in the current period is skipped.
- The public `/r/<token>` route lives in Task 42 with the other public endpoints.

- [x] **Step 1: Write the failing test** (`hub/tests/test_reports_api.py`)
```python
from tests.test_seed_helpers import seeded_demo

def test_build_library_share_and_rotate(client):
    seeded_demo(client)
    payload = client.get("/api/v1/reports").json()
    assert len(payload["presets"]) == 11 and len(payload["sections"]) == 19
    card = client.post("/api/v1/reports/build",
                       json={"days": 7, "preset": "security", "sections": [], "title": "Week 37"}).json()
    assert card["id"] and card["preset"] == "security" and card["title"] == "Week 37"
    assert any(c["id"] == card["id"] for c in client.get("/api/v1/reports").json()["library"])
    first = client.post(f"/api/v1/reports/{card['id']}/share", json={"hours": 168}).json()["share_url"]
    second = client.post(f"/api/v1/reports/{card['id']}/share", json={"hours": 168}).json()["share_url"]
    assert first != second
    assert client.post(f"/api/v1/reports/{card['id']}/share", json={"hours": 10_000}).status_code == 422

def test_preview_is_not_persisted(client):
    seeded_demo(client)
    before = len(client.get("/api/v1/reports").json()["library"])
    html = client.get("/api/v1/reports/preview?days=7&preset=executive").text
    assert "Executive summary" in html
    assert len(client.get("/api/v1/reports").json()["library"]) == before

def test_scheduler_is_idempotent_within_a_period(client, monkeypatch):
    seeded_demo(client)
    from guardian_hub import scheduler
    import datetime as dt
    monkeypatch.setattr(scheduler, "_now", lambda: dt.datetime(2026, 9, 7, 8, tzinfo=dt.timezone.utc))  # a Monday
    client.post("/api/v1/reports/schedule", json={"weekly": True, "monthly": False,
                                                  "preset": "executive", "hour": 8, "ntfy": True})
    scheduler.reports_job(); scheduler.reports_job()
    weekly = [c for c in client.get("/api/v1/reports").json()["library"] if c["kind"] == "weekly"]
    assert len(weekly) == 1
```
- [x] **Step 2–4:** run → FAIL → implement → pass.
- [x] **Step 5: Commit** — `hub: reports API (library, build, preview, expiring share links, schedule job)`

---

### Task 39: Settings API — profile, users & roles, targets, auto-fix policy, thresholds, appearance, sensors, tenants, audit, maintenance

Hub-only. Concurrent with 38, 40, 42.

**Files:**
- Create: `hub/guardian_hub/routers/settings.py`, `hub/tests/test_settings.py`
- Modify: `hub/guardian_hub/main.py`, `hub/guardian_hub/routers/write.py` (move `/config` and `/profile` under the settings router, keeping their existing paths as aliases so the sensor edit contract is untouched)

**Interfaces:** all under `/api/v1/settings`, all tenant-scoped, all audited.
- `GET /api/v1/settings` (viewer+) → `SettingsPayload`; secrets are never included (integration secrets live in Task 40's payload, masked).
- `POST /settings/profile` (admin) → `ProfileSettings` into `Tenant.settings["profile"]` + `Edit("/api/profile")` (existing contract shape: `{owner, profile:{email,address}}`).
- `GET/POST/DELETE /settings/users` (admin) → list, upsert `{username, email, display_name, role}` via `accounts.upsert_user`/`upsert_membership`, remove a membership. An admin may not remove their own last admin membership (422).
- `POST /settings/targets` (admin) → replaces `Tenant.config["sites"]` and `Tenant.config["ssh_hosts"]` after validating each site has a `name` and an `http(s)` URL and each host a `name` and `target` (the port of Nexus `server.py:921`'s guard) + `Edit("/api/config")`.
- `POST /settings/autofix` (admin) → `Tenant.config["auto_fix"]` (`enabled`, `max_per_day`, `attacker_method`, `maintenance_window` `{start,end}`) + `Edit("/api/config")`. The kill switch is `enabled: false`.
- `POST /settings/thresholds` (admin) → `Tenant.config["thresholds"]`, validated as numbers within sane ranges (`cert_days_warn` 1–120, `disk_pct_warn` 50–99, `mem_pct_warn` 50–99, `load_per_core_warn` 0.5–8, `latency_warn_s` 0.2–30) + `Edit("/api/config")`.
- `POST /settings/appearance` (viewer+, per-user) → `{theme}` stored on `Session`-independent `User`-level preference in `Tenant.settings["appearance"]` for admins and in `localStorage` for everyone else; the route only accepts `theme ∈ {system,light,dark}` and `accent` is fixed (blue) — it exists so the choice survives a new browser.
- `GET /settings/sensors` (admin) → `SensorRow[]`; `POST /settings/sensors/{id}/revoke` (admin) → `status="revoked"`, `token_hash=None`, audited. Enrol codes stay on the existing `POST /api/v1/sensors/enrol-code`.
- `GET/POST/PATCH/DELETE /settings/tenants` (superadmin) → thin wrappers over the phase-1 `routers/tenants.py`, plus `demo` toggling (`settings["demo_code"]`), so the UI has one place to call.
- `POST /api/v1/maintenance {hours}` (operator) → `Edit(path="/api/maintenance", body={"hours": hours})` (the sensor branch already exists), audited `maintenance`; suppresses non-CRIT notifications for that window.
- `GET /api/v1/audit?limit=200&action=&user=` (admin) → `AuditPayload`, tenant-scoped, newest first.

- [x] **Step 1: Write the failing test** (`hub/tests/test_settings.py`)
```python
from tests.test_seed_helpers import seeded_demo
from tests.test_auth import seed_user, login

def test_settings_round_trip_and_audit(client):
    seeded_demo(client)
    s = client.get("/api/v1/settings").json()
    assert set(s) == {"profile","users","sites","hosts","autofix","thresholds","appearance","sensors","tenants"}
    assert client.post("/api/v1/settings/thresholds", json={"cert_days_warn": 30}).status_code == 200
    assert client.post("/api/v1/settings/thresholds", json={"cert_days_warn": 900}).status_code == 422
    assert client.post("/api/v1/settings/autofix", json={"enabled": False, "max_per_day": 5,
        "attacker_method": "auto", "maintenance_window": {"start": "02:00", "end": "05:00"}}).status_code == 200
    assert client.get("/api/v1/settings").json()["autofix"]["enabled"] is False
    rows = client.get("/api/v1/audit").json()["rows"]
    assert {"thresholds.update", "autofix.update"} <= {r["action"] for r in rows}

def test_targets_are_validated_and_reach_the_sensor(client):
    seeded_demo(client)
    from guardian_hub.db import SessionLocal
    from guardian_hub import models as m
    assert client.post("/api/v1/settings/targets", json={"sites": [{"name": "x", "url": "ftp://x"}],
                                                         "hosts": []}).status_code == 422
    assert client.post("/api/v1/settings/targets", json={"sites": [{"name": "x", "url": "https://x.example"}],
                                                         "hosts": []}).status_code == 200
    e = SessionLocal().query(m.Edit).filter_by(path="/api/config").order_by(m.Edit.id.desc()).first()
    assert e.body["config"]["sites"][0]["url"] == "https://x.example"

def test_viewer_reads_but_cannot_write_and_audit_is_admin_only(client):
    seed_user(username="ann", email="a@x.com", tenant_slug="other", role="viewer"); login(client, "ann", "a@x.com")
    assert client.get("/api/v1/settings").status_code == 200
    assert client.post("/api/v1/settings/thresholds", json={"cert_days_warn": 30}).status_code == 403
    assert client.get("/api/v1/audit").status_code == 403

def test_admin_cannot_remove_their_own_last_admin_membership(client):
    seeded_demo(client)
    r = client.request("DELETE", "/api/v1/settings/users", json={"username": "demoadmin"})
    assert r.status_code == 422
```
- [x] **Step 2–4:** run → FAIL → implement → pass (phase-1 `test_read_write.py` must stay green through the alias paths).
- [x] **Step 5: Commit** — `hub: settings API (profile, users, targets, auto-fix, thresholds, sensors, tenants), maintenance, audit`

---

### Task 40: Integrations core — registry, encrypted secrets, severity routes, Test button, ntfy / Slack / Teams / webhook

Hub-only. **Blocks Task 41.** Concurrent with 38, 39, 42.

**Files:**
- Create: `hub/guardian_hub/integrations/{__init__.py,base.py,registry.py,ntfy.py,slack.py,teams.py,webhook.py}`, `hub/guardian_hub/routers/integrations.py`, `hub/tests/test_integrations_core.py`, `sensor/tests/test_notify_local_only.py`
- Modify: `hub/guardian_hub/main.py`, `hub/guardian_hub/ingest.py` (call `registry.deliver` on alert transitions), **`sensor/guardian_sensor/notify.py`** (reduce to the local banner — see the delivery-ownership decision below)

**Interfaces:**
- `base.Integration` — an abstract class every provider subclasses:
  ```python
  class Integration:
      kind: str; title: str; help: str; direction: str      # "out" | "out+in" | "out+command"
      fields: list[IntegrationField]                        # name/label/kind/required/options
      supports_routes: bool = True
      def send(self, ctx: SendContext, msg: Message) -> SendResult: ...
      def test(self, ctx: SendContext) -> SendResult: ...
      def inbound(self, ctx: SendContext, request_body: dict, headers: dict) -> InboundResult: ...
      def watchdog(self, ctx: SendContext) -> None: ...     # periodic duty, default no-op
  ```
  `Message` = `{title, body, severity, url, actions: [(label, act_url)], kind, dedup_key: str = ""}` where `kind` ∈ `alert|digest|weekly|report|resolve` and `dedup_key` is the Guardian alert key (used by PagerDuty and ticket sync). `SendContext` carries `tenant`, the decrypted `secret`, `config`, `db` and `base_url`. `SendResult` = `{ok: bool, error: str}`; `InboundResult` = `{ok: bool, body: dict, status: int}`.
- `registry` re-exports `Integration`, `Message`, `SendContext`, `SendResult` and `InboundResult` from `base`, so callers and tests import everything from `guardian_hub.integrations.registry`. `registry.REGISTRY: dict[str, Integration]` populated by `registry.register(cls)`; `registry.deliver(db, tenant, msg) -> list[SendResult]` walks the tenant's enabled `Integration` rows whose `routes[msg.severity]` is true, calls `send`, records `last_test`/`last_error` on failure, and audits `integration.send` **only** for failures (successes are high-volume). ntfy is the default: a tenant with no `Integration` rows still gets ntfy from `Tenant.ntfy_topic`.
- `registry.watchdog_job()` — an hourly APScheduler job calling `watchdog()` on every enabled integration, so each one "does a job, not just relays text" (§10.1).
- **Delivery ownership — decided here, once.** Phase 1 shipped `sensor/guardian_sensor/notify.py` with the full Nexus ladder, called from `runner.run_once` on every scan. This task moves alert delivery **to the hub** and must not leave both running, or every alert pushes twice. Hub-side is the right half because `/act` action buttons need `acttokens.mint(db, …)` (Task 42), because the five §10.1 integrations in Task 41 need `Incident`/`Alert`/`Recommendation` rows the sensor does not hold, and because per-tenant routing lives in `Integration` rows. The trade Miro accepts: while the hub is unreachable the sensor still fixes and still logs, but pushes nothing except the local macOS banner.
  - `ingest.store_scan` calls `registry.deliver(db, tenant, msg)` **after** its `db.commit()` (so a failing webhook can never roll back a scan) for each `Alert` that transitioned to `open` in this ingest or re-fired (`count` increased while `status == "open"`), with `Message(kind="alert", severity=alert.sev, title=…, body=alert.msg, url=f"{base_url}/inbox?row={alert.key}", dedup_key=alert.key, actions=…)`. Resolved alerts send `kind="resolve"` with the same `dedup_key`, which is what lets PagerDuty and the ticket sync close their side (Task 41).
  - `sensor/guardian_sensor/notify.py`: `notify(...)` keeps only the macOS banner path and returns `["local"]`; every network branch (ntfy, Slack, Teams, webhook) is removed. It short-circuits to a no-op when `config["notify"]["local_only"]` is false **and** the sensor is enrolled, so an un-enrolled standalone sensor is not left silent. The hub writes `notify.local_only = true` into the tenant config when the first `Integration` row is created.
- Providers (all stdlib `urllib` with a 10 s timeout, no new dependency):
  - `ntfy.NtfyIntegration` — topic; action buttons rendered as ntfy `Actions` headers pointing at `{base_url}/act/{token}/{action}`; `watchdog` re-sends any CRIT unacked for 6 h and escalates to `priority=urgent` after 30 min (the port of Nexus `checks.notify`'s ladder).
  - `slack.SlackIntegration` — incoming-webhook URL, Block Kit payload with a severity colour attachment; `watchdog` posts the daily digest at the tenant's configured hour.
  - `teams.TeamsIntegration` — incoming-webhook URL, MessageCard payload; same digest duty.
  - `webhook.WebhookIntegration` — `https` URL only (422 otherwise, the port of `server.py:991`); POSTs `{title, message, severity, priority, url, ts, tenant, kind}`; `watchdog` sends a `heartbeat` kind hourly so the receiver can alert on Guardian's own silence.
- `GET /api/v1/integrations` (admin) → `IntegrationsPayload`; every secret is masked as `value[:14] + "…" + value[-6:]` for values longer than 24 chars, else `"set"`/`""` (the port of `server.py:822`'s `mask`). The real secret is never returned.
- `POST /api/v1/integrations {kind, enabled, routes, fields: {...}}` (admin) → upserts the row, encrypts the secret through `crypto.encrypt`, mints `inbound_token` (32 hex) when the provider's direction includes `in`, audits `integration.update` with the secret redacted to `"(set)"`.
- `POST /api/v1/integrations/{kind}/test` (admin) → calls `test()`, stores `last_test`/`last_error`, returns `SendResult`; audited.
- `DELETE /api/v1/integrations/{kind}` (admin) → removes the row; audited.

- [x] **Step 1: Write the failing test** (`hub/tests/test_integrations_core.py`)
```python
from tests.test_seed_helpers import seeded_demo
from guardian_hub.db import SessionLocal
from guardian_hub import models as m, crypto
from guardian_hub.integrations import registry

def test_secret_is_encrypted_at_rest_and_masked_in_the_payload(client):
    seeded_demo(client)
    url = "https://hooks.slack.com/services/T000/B000/examplewebhooktoken"
    assert client.post("/api/v1/integrations", json={"kind": "slack", "enabled": True,
        "routes": {"CRIT": True, "WARN": True, "INFO": False}, "fields": {"url": url}}).status_code == 200
    row = SessionLocal().query(m.Integration).filter_by(kind="slack").one()
    assert row.secret != url and crypto.decrypt(row.secret) == url
    card = next(c for c in client.get("/api/v1/integrations").json()["integrations"] if c["kind"] == "slack")
    assert card["configured"] and url not in str(card) and "…" in card["fields"][0]["value"]

def test_routes_decide_delivery_and_ntfy_is_the_default(client, monkeypatch):
    seeded_demo(client)
    sent = []
    for kind in ("ntfy", "slack", "teams", "webhook"):
        monkeypatch.setattr(registry.REGISTRY[kind], "send",
                            lambda ctx, msg, k=kind: sent.append((k, msg.severity)) or registry.SendResult(True, ""))
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    registry.deliver(db, t, registry.Message(title="t", body="b", severity="INFO", url="", actions=[], kind="alert"))
    assert sent == [("ntfy", "INFO")]                      # slack row routes INFO off; no other rows exist
    sent.clear()
    registry.deliver(db, t, registry.Message(title="t", body="b", severity="CRIT", url="", actions=[], kind="alert"))
    assert set(k for k, _ in sent) == {"ntfy", "slack"}

def test_ingest_delivers_a_new_crit_exactly_once(client, monkeypatch):
    tid = seeded_demo(client)
    calls = []
    monkeypatch.setattr(registry, "deliver", lambda db, t, msg: calls.append((msg.kind, msg.severity, msg.dedup_key)))
    payload = ingest_payload_with_new_crit("sec:attacker:demo:203.0.113.9")   # helper in this module
    H = {"Authorization": f"Bearer {enrol(client, tid)}"}
    client.post("/api/v1/sensor/ingest", json=payload, headers=H)
    assert calls == [("alert", "CRIT", "sec:attacker:demo:203.0.113.9")]
    client.post("/api/v1/sensor/ingest", json=payload, headers=H)   # same alert, no new fire
    assert len(calls) == 1


def test_webhook_rejects_plain_http_and_test_records_the_error(client, monkeypatch):
    seeded_demo(client)
    assert client.post("/api/v1/integrations", json={"kind": "webhook", "enabled": True,
        "routes": {}, "fields": {"url": "http://insecure.example"}}).status_code == 422
    monkeypatch.setattr(registry.REGISTRY["ntfy"], "test",
                        lambda ctx: registry.SendResult(False, "ntfy 503"))
    body = client.post("/api/v1/integrations/ntfy/test").json()
    assert body["ok"] is False and body["error"] == "ntfy 503"
    card = next(c for c in client.get("/api/v1/integrations").json()["integrations"] if c["kind"] == "ntfy")
    assert card["last_error"] == "ntfy 503" and card["last_test"]
```
`sensor/tests/test_notify_local_only.py`:
```python
from guardian_sensor import notify

def test_notify_never_reaches_the_network_when_the_hub_owns_delivery(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("sensor must not push alerts; the hub delivers")
    monkeypatch.setattr(notify.urllib.request, "urlopen", boom)
    banners = []
    monkeypatch.setattr(notify, "_mac_banner", lambda title, msg: banners.append(title))
    out = notify.notify("Guardian", "a new device joined", sev="WARN",
                        env={}, cfg={"notify": {"local_only": True}})
    assert out == ["local"] and banners == ["Guardian"]
```

- [x] **Step 2–4:** run both suites → FAIL → implement → pass (sensor suite under Python 3.10). Grep the sensor package afterwards for `ntfy.sh`, `hooks.slack.com` and `WEBHOOK_URL` — the only remaining hits may be in comments explaining the move.
- [x] **Step 5: Commit** — `hub+sensor: integration registry, encrypted secrets, severity routes, Test, ntfy/Slack/Teams/webhook; hub now owns alert delivery`

---

### Task 41: The five new integrations — Discord, PagerDuty, Telegram, Jira/ServiceNow, Home Assistant

Hub-only. Depends on 40.

**Files:**
- Create: `hub/guardian_hub/integrations/{discord.py,pagerduty.py,telegram.py,tickets.py,homeassistant.py}`, `hub/guardian_hub/routers/inbound.py`, `hub/tests/test_integrations_new.py`
- Modify: `hub/guardian_hub/integrations/registry.py` (register the five), `hub/guardian_hub/main.py`

**Interfaces:** each provider implements the §10.1 duty, not just a relay.
- `discord.DiscordIntegration` — fields: webhook URL, optional application public key + guild id. `send` posts an embed with the severity colour; `watchdog` posts the daily digest and the Monday summary and, when a weekly/monthly report exists with a live share link, posts the link. `inbound` handles the `/guardian status` slash-command interaction (Ed25519 signature check with `X-Signature-Ed25519`/`X-Signature-Timestamp` verified against the public key using `cryptography`), replying with score, open CRIT/WARN and unverified-device counts for the bound tenant. Direction `out+command`.
- `pagerduty.PagerDutyIntegration` — field: Events API v2 routing key. `send` for `severity == "CRIT"` triggers an event with `dedup_key = alert key`; on `Message.kind == "resolve"` it sends `event_action: "resolve"`. `inbound` accepts PagerDuty's webhook v3 (`incident.acknowledged` → ack the Guardian alert whose key matches `dedup_key`; `incident.resolved` → resolve it) with the per-integration token in the URL. When enabled, this integration **replaces** ntfy's 30-minute urgent escalation for that tenant (`config["owns_escalation"] = True`, read by `ntfy.watchdog`). Direction `out+in`.
- `telegram.TelegramIntegration` — fields: bot token, plus a pairing flow: `POST /api/v1/integrations/telegram/pair` (admin) returns a 6-char code; the user sends `/start <code>` to the bot, the inbound handler binds `chat_id` into `Integration.config`. `send` includes an inline keyboard whose buttons are Trust / Ignore 24 h / Ack / Fix now, each an `url` button to `{base_url}/act/{token}/{action}` (Task 42 mints the token). `inbound` handles `/status`, `/devices`, `/report` and `/start`. Direction `out+in`.
- `tickets.TicketsIntegration` — one class serving both Jira and ServiceNow, selected by a `flavour` field (`jira|servicenow`); fields: base URL, user/email, API token, project key (Jira) or table (ServiceNow). Ticket-per **incident**, never per alert: `watchdog` reconciles — for every open `Incident` without `Alert.ticket_key`, create a ticket with the story and actions and write the key onto each member alert; for each incident that gained a `Fix`, comment on the ticket; for each closed incident, transition it to done. A `Recommendation` whose `status == "needs-you"` gets a task ticket. Direction `out+status sync`.
- `homeassistant.HomeAssistantIntegration` — fields: MQTT host/port/user/password, discovery prefix, plus optional HA base URL + long-lived token. `watchdog` publishes MQTT-discovery config and state for: `security_score`, `grade`, `alerts_crit`, `alerts_warn`, `unverified_devices`, `sensor_online`, `dns_blocked_24h` and one `binary_sensor` per device's online state. MQTT is spoken over a tiny stdlib client in this module (CONNECT/PUBLISH/SUBSCRIBE/PINGREQ only, QoS 0) so no new dependency is added. `inbound` exposes `quarantine_device`, `run_scan` and `ack_alert` as REST calls HA automations can make (token in the URL, body validated, executed through the same write paths as the dashboard and audited as `user="ha:<tenant>"`). It also **reads** HA `device_tracker` entities when the HA base URL is set and writes the results into `Device.extra["ha_presence"]`, which `presence.profile` prefers over scan-derived presence when present. Direction `out+in`.
- `POST /inbound/{kind}/{token}` (public, no session) in `routers/inbound.py` — looks the token up in `Integration.inbound_token` (constant-time compare), rate-limited `ratelimit.allow(f"inbound:{token[:8]}", 60, 60)`, dispatches to the provider's `inbound()`. Returns the provider's own expected response shape. Never leaks whether a token merely expired.

- [x] **Step 1: Write the failing test** (`hub/tests/test_integrations_new.py`)
```python
from tests.test_seed_helpers import seeded_demo
from guardian_hub.db import SessionLocal
from guardian_hub import models as m
from guardian_hub.integrations import registry

def configure(client, kind, fields, routes=None):
    r = client.post("/api/v1/integrations", json={"kind": kind, "enabled": True,
        "routes": routes or {"CRIT": True, "WARN": True, "INFO": False}, "fields": fields})
    assert r.status_code == 200, r.text
    return SessionLocal().query(m.Integration).filter_by(kind=kind).one()

def test_all_five_are_registered_with_a_duty(client):
    for kind in ("discord", "pagerduty", "telegram", "jira", "homeassistant"):
        i = registry.REGISTRY[kind]
        assert i.title and i.help and i.fields and i.direction
        assert i.__class__.watchdog is not registry.Integration.watchdog, f"{kind} has no watchdog duty"

def test_pagerduty_dedups_on_the_alert_key_and_acks_inbound(client, monkeypatch):
    seeded_demo(client)
    row = configure(client, "pagerduty", {"routing_key": "R" * 32})
    posts = []
    monkeypatch.setattr("guardian_hub.integrations.pagerduty._post", lambda url, body: posts.append(body))
    key = client.get("/api/v1/inbox?view=alerts").json()["rows"][0]["id"]
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    registry.deliver(db, t, registry.Message(title="x", body="y", severity="CRIT", url="",
                                             actions=[], kind="alert", dedup_key=key))
    assert posts[0]["dedup_key"] == key and posts[0]["event_action"] == "trigger"
    r = client.post(f"/inbound/pagerduty/{row.inbound_token}",
                    json={"event": {"event_type": "incident.acknowledged",
                                    "data": {"dedup_key": key}}})
    assert r.status_code == 200
    assert db.query(m.Alert).filter_by(tenant_id=t.id, key=key).one().status == "acked"

def test_tickets_open_one_per_incident_and_stamp_the_alerts(client, monkeypatch):
    seeded_demo(client)
    configure(client, "jira", {"flavour": "jira", "base_url": "https://x.atlassian.net",
                               "email": "a@b.c", "token": "t", "project": "GRD"})
    created = []
    monkeypatch.setattr("guardian_hub.integrations.tickets._create",
                        lambda ctx, summary, body: created.append(summary) or f"GRD-{len(created)}")
    registry.watchdog_job()
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    incidents = db.query(m.Incident).filter_by(tenant_id=t.id).all()
    assert len(created) == len([i for i in incidents if not i.closed])
    stamped = db.query(m.Alert).filter(m.Alert.tenant_id == t.id, m.Alert.ticket_key != "").count()
    assert stamped >= len(created)
    registry.watchdog_job()
    assert len(created) == len([i for i in incidents if not i.closed])   # no duplicates

def test_inbound_token_is_required_and_rate_limited(client):
    seeded_demo(client)
    configure(client, "telegram", {"bot_token": "123:abc"})
    assert client.post("/inbound/telegram/wrong-token", json={}).status_code == 404
```
- [x] **Step 2–4:** run → FAIL → implement → pass. Manual smoke where credentials exist (ntfy for sure; the rest are verified through the Test button with a real endpoint only when Miro supplies one — record which are untested in the task report).
- [x] **Step 5: Commit** — `hub: Discord, PagerDuty, Telegram, Jira/ServiceNow and Home Assistant integrations with inbound webhooks`

---

### Task 42: Public and ops endpoints — `/act`, `/status/<slug>`, `/hb`, `/r/<token>`, `/metrics`

Hub-only. Concurrent with 38, 39, 40.

**Files:**
- Create: `hub/guardian_hub/routers/public.py`, `hub/guardian_hub/acttokens.py`, `hub/tests/test_public.py`
- Modify: `hub/guardian_hub/main.py`, `hub/guardian_hub/config.py` (add `metrics_token: str = ""`), `hub/guardian_hub/integrations/ntfy.py` and `telegram.py` (mint tokens through `acttokens.mint`)

**Interfaces:**
- `acttokens.mint(db, tenant, kind, target, actions, hours=48) -> str` and `acttokens.consume(db, token, action) -> ActToken` (single-use per action; a second use returns 410).
- `GET /act/{token}/{action}` (public, rate-limited `ratelimit.allow(f"act:{token[:8]}", 10, 60)`) — actions `trust`, `snooze`, `ack`, `fix`. Performs the same write the dashboard would (device trust → `Device.trusted` + `Edit("/api/device")`; snooze → alert snooze 24 h; ack → alert ack; fix → `Queue("/api/fix")` from the alert's `fix` action), audits with `user="act:<kind>"`, and returns a small Guardian-branded HTML page confirming what happened. An expired or already-used token returns a 410 page reading "This link expired or was already used." Never reveals the tenant name to an invalid token.
- `GET /status/{slug}` (public) — the port of Nexus `/status` (`server.py:727–748`), rendered from the tenant's `Site` rows where `Site.public` is true: per-site operational/down state, 24 h and 7 d uptime from `Metric`, and a 90-bar heartbeat strip. Guardian branding, `<meta http-equiv="refresh" content="120">`, responsive at 600 px. A slug with no public sites renders "No public services." A tenant whose `settings["status_page"]` is false returns 404. **Nothing beyond the opted-in sites is exposed** — no host names, no device counts, no scores.
- `GET /hb/{name}/{token}` (public) — the port of `server.py:797`: looks the `Heartbeat` row up by `(tenant, name)` with a constant-time token compare, stamps `last_ping`, returns `{"ok": true, "name": …}`; unknown name/token → 404. Rate-limited 60/min per name.
- `GET /r/{share_token}` (public) — serves the stored report HTML when `share_expires` is in the future, else a 410 page. Rate-limited 30/min per token.
- `GET /metrics` (public, Prometheus text) — the port of `server.py:803`, renamed to the `guardian_` prefix and **labelled with `tenant`**: `guardian_site_up`, `guardian_site_latency_seconds`, `guardian_site_cert_days`, `guardian_host_reachable`, `guardian_host_disk_pct`, `guardian_host_mem_pct`, `guardian_host_load_per_core`, `guardian_host_failed_ssh_24h`, `guardian_host_security_updates`, `guardian_alerts{severity}`, `guardian_lan_devices_online`, `guardian_lan_unverified`, `guardian_speed_down_mbps`, `guardian_speed_up_mbps`, `guardian_dns_blocked_24h`, `guardian_data_age_seconds`, `guardian_sensor_up`. Gated on `settings.metrics_token` when set (`?token=` or `Authorization: Bearer`); when unset it binds only for `127.0.0.1` callers.

- [x] **Step 1: Write the failing test** (`hub/tests/test_public.py`)
```python
from tests.test_seed_helpers import seeded_demo
from guardian_hub.db import SessionLocal
from guardian_hub import models as m, acttokens

def test_act_token_is_single_use_and_leaks_nothing(client):
    seeded_demo(client)
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    mac = db.query(m.Device).filter_by(tenant_id=t.id).first().mac
    tok = acttokens.mint(db, t, "device", mac, ["trust", "snooze"]); db.commit()
    r = client.get(f"/act/{tok}/trust")
    assert r.status_code == 200 and "Guardian" in r.text
    assert db.query(m.Device).filter_by(tenant_id=t.id, mac=mac).one().trusted is True
    assert db.query(m.Audit).filter_by(action="device.update", user="act:device").count() == 1
    assert client.get(f"/act/{tok}/trust").status_code == 410
    bad = client.get("/act/deadbeef/trust")
    assert bad.status_code == 410 and "demo" not in bad.text.lower()

def test_status_page_shows_only_public_sites(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    sites = db.query(m.Site).filter_by(tenant_id=tid).all()
    sites[0].public = True
    for s in sites[1:]: s.public = False
    db.commit()
    html = client.get("/status/demo").text
    assert sites[0].name.split(" (")[0] in html
    for s in sites[1:]: assert s.name.split(" (")[0] not in html
    assert "192.168." not in html and "Security score" not in html

def test_heartbeat_and_share_link_expiry(client):
    import datetime as dt
    tid = seeded_demo(client)
    db = SessionLocal()
    hb = m.Heartbeat(tenant_id=tid, name="roots-daily-report", token="t" * 32, expect_minutes=1500)
    db.add(hb); db.commit()
    assert client.get("/hb/roots-daily-report/" + "t" * 32).json()["ok"] is True
    assert client.get("/hb/roots-daily-report/wrong").status_code == 404
    card = client.post("/api/v1/reports/build", json={"days": 7, "preset": "executive",
                                                      "sections": [], "title": "T"}).json()
    tok = client.post(f"/api/v1/reports/{card['id']}/share", json={"hours": 1}).json()["share_url"].rsplit("/", 1)[1]
    assert client.get(f"/r/{tok}").status_code == 200
    rep = db.query(m.Report).filter_by(share_token=tok).one()
    rep.share_expires = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1); db.commit()
    assert client.get(f"/r/{tok}").status_code == 410

def test_metrics_are_guardian_prefixed_and_tenant_labelled(client):
    seeded_demo(client)
    body = client.get("/metrics").text
    assert "guardian_site_up{" in body and 'tenant="demo"' in body
    assert "nexus_" not in body
    assert "guardian_data_age_seconds{" in body and "guardian_sensor_up{" in body
```
- [x] **Step 2–4:** run → FAIL → implement → pass.
- [x] **Step 5: Commit** — `hub: public endpoints (/act, /status/<slug>, /hb, /r/<token>) and Prometheus /metrics`

---

### Task 43: Guardian chat — tool registry, action tiers, role gates, confirmation, audit, queue polling

Hub-only, **no network calls in this task** (so every rule is unit-tested deterministically). **Blocks Task 44.** Depends on 39, 42 (it dispatches through their write paths).

**Files:**
- Create: `hub/guardian_hub/chat/{__init__.py,tools.py,registry.py,tiers.py}`, `hub/tests/test_chat_tools.py`
- Modify: none (the HTTP route arrives in Task 44)

**Interfaces:**
- `tiers.Tier = Literal[1, 2, 3]`. **Tier and role are independent axes**: the tier decides whether Guardian asks first, `min_role` mirrors the permission of the matching dashboard route exactly.
- `registry` re-exports `ToolContext`, `ToolResult` and the `ChatProposal`/`ProposedAction` models from `schemas_dash`, so callers and tests import everything from `guardian_hub.chat.registry`. `registry.Tool` — `{name, description, schema (JSON schema for the LLM), tier, min_role, handler, effect, reversible}` where `handler(ctx: ToolContext, **args) -> ToolResult`. `ToolContext` = `{db, tenant, user, role, request_ip}`. `ToolResult` = `{ok, text, data, queue_id}`.
- `registry.TOOLS: dict[str, Tool]` — the complete set:

| tool | tier | min_role | what it does | dashboard route it mirrors |
|---|---|---|---|---|
| `device_deep_dive(target)` | 1 | viewer | live port scan + names + presence history | queued `/api/fix`-style probe job `path="/api/deep-dive"` |
| `host_command(host, command, arg?)` | 1 | viewer | read-only whitelist (the Nexus `HOST_CMDS` map, `server.py:159`) | queued `path="/api/host-command"` |
| `site_check(url)` | 1 | viewer | live HTTP + TLS check, hub-side | `GET /api/v1/live` probe |
| `lookup_ip(ip)` | 1 | viewer | `geoip.lookup` | — |
| `explain_alert(key)` | 1 | viewer | reads `Alert`, its `Fix`/`Audit` history, and says "this was us" when the cause is a recorded Guardian fix or an audited dashboard write | — |
| `vuln_check(host)` | 1 | viewer | reads `CveRow`s + `install_debsecan` state | `GET /api/v1/updates` |
| `build_report(days, preset)` | 1 | viewer | renders and stores a report, returns the share URL | `POST /api/v1/reports/build` |
| `rescan()` | 1 | operator | queue a scan | `POST /api/v1/run` |
| `speedtest()` | 1 | operator | queue a speed test | `POST /api/v1/speedtest` |
| `ack_alert(key)` / `snooze_alert(key, hours)` / `resolve_alert(key)` | 2 | operator | alert lifecycle | `POST /api/v1/alerts/{key}/{op}` |
| `trust_device(mac, label?)` / `label_device(mac, label)` | 2 | operator | device edits | `POST /api/v1/devices/{mac}` |
| `quarantine_device(mac, on)` | 2 | operator | quarantine / release | `POST /api/v1/devices/{mac}` |
| `decide_recommendation(key, yes\|no)` | 2 | operator | answer a rec | `POST /api/v1/recs/{key}` |
| `apply_fix(host, action, arg?)` — action in `TIER2_FIXES` | 2 | admin | block_ip, security_updates, restart_container, restart_service, enable_fail2ban, enable_ufw, cleanup_disk, harden_fail2ban, install_debsecan, kill_miner | `POST /api/v1/fix` |
| `apply_fix(host, action, arg?)` — action in `TIER3_FIXES` | 3 | admin | reboot, full_upgrade, disable_ssh_password, block_subnet, ban_long, limit_ssh | `POST /api/v1/fix` |
| `forget_device(mac)` | 3 | admin | delete the device and its history | `POST /api/v1/devices/{mac}/forget` |
| `set_policy(section, values)` | 3 | admin | thresholds / auto-fix / targets | `POST /api/v1/settings/*` |
| `schedule_reboot(host, window_start, window_end)` | 3 | admin | reboot planner | `POST /api/v1/reboot-plan` |

- `tiers.effective_tier(db, tenant, tool, args) -> Tier` — the tool's tier, **escalated to 3** when the action targets a host for which no successful `Fix` row exists ("anything on a host Guardian has never fixed before", §7), and **escalated to 3** when `apply_fix` would touch the hub's own listening port (a `block_ip`/`enable_ufw` whose effect would include `settings.base_url`'s port) — in which case the proposal text must say so explicitly.
- `registry.dispatch(ctx, name, args, confirm_token=None) -> ToolResult | ChatProposal`:
  1. unknown tool → `ToolResult(ok=False, text="I do not have a tool for that.")`;
  2. `ROLE_ORDER[ctx.role] < ROLE_ORDER[tool.min_role]` → `ToolResult(ok=False, text=f"I cannot {…} with your **{ctx.role}** access — an **{tool.min_role}** on this site can, or I can prepare it for them.")` — it **names who can**, never a generic refusal, and never a shell recipe;
  3. `effective_tier == 3` and no matching `confirm_token` → returns a `ChatProposal` (token = 32 hex stored in `ActToken`-style rows with `kind="chat"`, 10-minute expiry, `actions` = the serialised action list) **without calling the handler**;
  4. otherwise call the handler, `audit.log(..., user=f"chat:{ctx.user.username}", ...)`, and return the result. A tier-2 result's `text` always states the undo ("I quarantined 192.168.1.99 — say *release it* to undo").
- `registry.capability_gap(ctx, name, args) -> str | None` — when the tool cannot run for a *capability* reason rather than a permission one (no sensor enrolled, the sensor has no SSH key for that host, the host is not in `Tenant.config["ssh_hosts"]`), returns the precise sentence naming what is missing, e.g. `"to change ufw on safehome I need the sensor to have SSH access: add ~/.guardian/keys/safehome on the sensor Mac, or grant my user the admin role for this site"`. `dispatch` returns this instead of executing.
- Queued tools return `queue_id`; the caller (Task 44) polls `GET /api/v1/queue/{id}`.

- [x] **Step 1: Write the failing test** (`hub/tests/test_chat_tools.py`)
```python
import pytest
from tests.test_seed_helpers import seeded_demo
from guardian_hub.db import SessionLocal
from guardian_hub import models as m
from guardian_hub.chat import registry, tiers

def ctx_for(role="admin", username="demoadmin"):
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    u = db.query(m.User).filter_by(username=username).one()
    return registry.ToolContext(db=db, tenant=t, user=u, role=role, request_ip="127.0.0.1")

def test_every_tool_declares_a_tier_a_role_and_an_effect():
    for name, tool in registry.TOOLS.items():
        assert tool.tier in (1, 2, 3) and tool.min_role in ("viewer", "operator", "admin"), name
        assert tool.description and tool.schema and tool.effect and tool.handler, name

def test_viewer_is_refused_a_tier2_tool_and_told_who_can(client):
    seeded_demo(client)
    r = registry.dispatch(ctx_for(role="viewer"), "ack_alert", {"key": "lan:unverified"})
    assert r.ok is False and "operator" in r.text and "sudo" not in r.text

def test_tier3_returns_a_proposal_then_executes_with_the_token(client):
    seeded_demo(client)
    ctx = ctx_for()
    prop = registry.dispatch(ctx, "apply_fix", {"host": "safehome", "action": "reboot"})
    assert isinstance(prop, registry.ChatProposal) and prop.token
    assert prop.actions[0].tier == 3 and prop.actions[0].reversible is False
    assert SessionLocal().query(m.Queue).filter_by(path="/api/fix").count() == 0
    res = registry.dispatch(ctx, "apply_fix", {"host": "safehome", "action": "reboot"},
                            confirm_token=prop.token)
    assert res.ok and res.queue_id
    db = SessionLocal()
    assert db.query(m.Queue).filter_by(id=res.queue_id).one().body["action"] == "reboot"
    assert db.query(m.Audit).filter_by(user="chat:demoadmin", action="fix").count() == 1

def test_tier2_executes_immediately_and_states_the_undo(client):
    seeded_demo(client)
    ctx = ctx_for()
    mac = SessionLocal().query(m.Device).filter_by(tenant_id=ctx.tenant.id).first().mac
    res = registry.dispatch(ctx, "quarantine_device", {"mac": mac, "on": True})
    assert res.ok and "undo" in res.text.lower()
    assert SessionLocal().query(m.Device).filter_by(mac=mac).first().quarantined is True

def test_unseen_host_escalates_a_tier2_fix_to_a_proposal(client):
    seeded_demo(client)
    ctx = ctx_for()
    # 'demo' has fix history in the fixtures; invent a host with none
    ctx.tenant.config = {**ctx.tenant.config,
                         "ssh_hosts": ctx.tenant.config["ssh_hosts"] + [{"name": "brandnew", "target": "root@1.1.1.1"}]}
    ctx.db.commit()
    assert tiers.effective_tier(ctx.db, ctx.tenant, registry.TOOLS["apply_fix"],
                               {"host": "brandnew", "action": "enable_ufw"}) == 3
    out = registry.dispatch(ctx, "apply_fix", {"host": "brandnew", "action": "enable_ufw"})
    assert isinstance(out, registry.ChatProposal)

def test_capability_gap_names_exactly_what_is_missing(client):
    seeded_demo(client)
    ctx = ctx_for()
    gap = registry.capability_gap(ctx, "apply_fix", {"host": "not-configured", "action": "enable_ufw"})
    assert gap and "not-configured" in gap and "ssh" in gap.lower()

def test_no_tool_ever_returns_a_shell_recipe(client):
    seeded_demo(client)
    ctx = ctx_for()
    for name, tool in registry.TOOLS.items():
        assert "sudo " not in tool.effect and "ssh " not in tool.effect.lower(), name
```
- [x] **Step 2–4:** run → FAIL → implement → pass. No test in this file may make a network call; `host_command` and `device_deep_dive` only enqueue.
- [x] **Step 5: Commit** — `hub: chat tool registry with three action tiers, role gates, confirmation tokens, audit`

---

### Task 44: Guardian chat — `POST /chat`, providers, context blob, answer format, streaming

Hub-only. Depends on 43.

**Files:**
- Create: `hub/guardian_hub/chat/{providers.py,prompt.py,rules.py}`, `hub/guardian_hub/routers/chat.py`, `hub/tests/test_chat_api.py`
- Modify: `hub/guardian_hub/main.py`, `hub/guardian_hub/config.py` (add `groq_api_key`, `anthropic_api_key`, `chat_model_first`, `chat_model_tools`)

**Interfaces:**
- `prompt.context_blob(db, tenant, tab: str | None, budget_chars=12000) -> str` — the port of Nexus `context_blob` (`server.py:522–571`): DATA TIMESTAMP + WAN IP, SITES, HOSTS, LAN devices, alerts, score/deductions, recommendations, DNS, home/Wi-Fi, speed, domains/email. **Capped**: sections are added in priority order until the budget is spent, and the tab the user is looking at is promoted to the front (`tab="dns"` puts the DNS block first) so the 8 k TPM window is spent on what is being asked about. The blob never contains a token, topic, share link or OTP.
- `prompt.SYSTEM` — the port of Nexus `SYSTEM` (`server.py:572`) rewritten for Guardian, and it must state the behaviour the tests check: answer only from the data; **strict answer format** = one verdict line, then at most six bullets shaped `Name · IP · fact`, then a `Next:` line; the `Next:` line is always an **offer** ("Say *yes* and I will reboot safehome now — about one minute of downtime for the dashboards"), never an instruction to the user; never emit shell commands; when a tool is refused, name the role that can do it.
- `providers.chat(messages, tools=None, want_tools=False) -> ProviderReply` — tries Groq `settings.chat_model_first` (`openai/gpt-oss-120b`) for the first call and `settings.chat_model_tools` (`openai/gpt-oss-20b`) for tool rounds (the port of Nexus `call_groq`, `server.py:603`, including the `reasoning_effort: "low"` extra for `openai/` models), then Anthropic when `anthropic_api_key` is set (port of `call_anthropic`, `server.py:582`), then `rules.answer(...)`. Records which provider served the turn in `ProviderReply.provider`. Every provider call has a 30 s timeout and at most 4 tool rounds per turn.
- `rules.answer(db, tenant, question) -> str` — the port of Nexus `rules_answer` (`server.py:644`): a deterministic keyword responder over the same data, so chat still works with no API key and no credits. It must obey the same answer format.
- `POST /api/v1/chat` (viewer+) → `ChatResponse`. Flow: build context → provider call with `registry.TOOLS` filtered to the caller's role → for each requested tool call `registry.dispatch` → a `ChatProposal` short-circuits the turn and is returned in `ChatResponse.proposal` with the proposal text as `text` → queued results are polled for up to 20 s (`GET /api/v1/queue/{id}` internally) and reported as "done" / "failed: <reason>"; if still running the response says so and the web client keeps polling the `queue` live frame. `ChatRequest.confirm_token` carries the user's *yes* back; a message that is just "yes"/"do it"/"go ahead" with a live proposal token executes it without another provider round trip.
- Rate limits: `ratelimit.allow(f"chat:{user.id}", 20, 60)`.

- [x] **Step 1: Write the failing test** (`hub/tests/test_chat_api.py`)
```python
from tests.test_seed_helpers import seeded_demo
from guardian_hub.chat import providers, rules

def fake_provider(monkeypatch, *replies):
    seq = list(replies)
    monkeypatch.setattr(providers, "chat", lambda *a, **k: seq.pop(0))

def test_answer_format_is_enforced(client, monkeypatch):
    seeded_demo(client)
    fake_provider(monkeypatch, providers.ProviderReply(
        text="Everything is fine.\n- Router · 192.168.1.1 · online\nNext: say *yes* and I will run a scan.",
        tool_calls=[], provider="groq/openai/gpt-oss-120b"))
    body = client.post("/api/v1/chat", json={"message": "how are things?", "thread": [], "tab": "overview"}).json()
    assert body["verdict"] and len(body["bullets"]) <= 6 and body["next"].startswith("say")
    assert "sudo" not in body["text"] and body["provider"].startswith("groq/")

def test_fix_it_proposes_then_runs_after_yes(client, monkeypatch):
    seeded_demo(client)
    fake_provider(monkeypatch, providers.ProviderReply(text="", provider="groq/openai/gpt-oss-20b",
        tool_calls=[{"name": "apply_fix", "args": {"host": "safehome", "action": "reboot"}}]))
    first = client.post("/api/v1/chat", json={"message": "fix the reboot on safehome",
                                              "thread": [], "tab": "updates"}).json()
    assert first["proposal"] and first["proposal"]["token"]
    assert "reboot safehome" in first["text"].lower() and "yes" in first["text"].lower()
    second = client.post("/api/v1/chat", json={"message": "yes", "thread": [], "tab": "updates",
                                               "confirm_token": first["proposal"]["token"]}).json()
    assert second["tool_steps"][0]["queue_id"] and second["tool_steps"][0]["status"] in ("queued", "running", "done")
    assert second["proposal"] is None

def test_tier1_runs_immediately_without_a_provider_round_trip(client, monkeypatch):
    seeded_demo(client)
    fake_provider(monkeypatch, providers.ProviderReply(text="", provider="groq/openai/gpt-oss-20b",
        tool_calls=[{"name": "lookup_ip", "args": {"ip": "8.8.8.8"}}]),
        providers.ProviderReply(text="8.8.8.8 is Google.\nNext: nothing needed.", tool_calls=[],
                                provider="groq/openai/gpt-oss-120b"))
    body = client.post("/api/v1/chat", json={"message": "who is 8.8.8.8?", "thread": [], "tab": None}).json()
    assert body["proposal"] is None and body["tool_steps"][0]["tier"] == 1

def test_rules_fallback_works_with_no_keys(client, monkeypatch):
    seeded_demo(client)
    monkeypatch.setattr(providers, "_groq_available", lambda: False)
    monkeypatch.setattr(providers, "_anthropic_available", lambda: False)
    body = client.post("/api/v1/chat", json={"message": "what is my security score?",
                                             "thread": [], "tab": "overview"}).json()
    assert body["provider"] == "rules" and "83" in body["text"]

def test_context_blob_is_capped_and_carries_no_secrets(client):
    seeded_demo(client)
    from guardian_hub.chat import prompt
    from guardian_hub.db import SessionLocal
    from guardian_hub import models as m
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    blob = prompt.context_blob(db, t, tab="dns", budget_chars=4000)
    assert len(blob) <= 4000 and blob.index("DNS") < blob.index("SITES")
    for bad in ("Bearer ", "ntfy.sh/", "share_token", "hooks.slack.com"):
        assert bad not in blob
```
- [x] **Step 2–4:** run → FAIL → implement → pass. Then one live check with a real Groq key set in `hub/.env` on the VPS: ask "is anyone attacking me?" and confirm the answer format and that a *fix it* is proposed, not executed.
- [x] **Step 5: Commit** — `hub: chat API with Groq/Anthropic/rules providers, capped context, strict answer format`

---

### Task 45: Reports tab — builder with live preview, library, share, schedule

Web-only. Depends on 22, 23, 38; concurrent with 46, 47, 48.

**Files:**
- Create: `web/src/pages/Reports.tsx`, `web/src/components/{ReportBuilder.tsx,ReportLibrary.tsx,ShareDialog.tsx,ScheduleEditor.tsx}`, `web/tests/reports.test.tsx`
- Modify: `web/src/App.tsx` (route `/reports`)

**Interfaces:**
- `ReportBuilder({presets, sections, onBuild})` — a preset dropdown (the eleven presets, `full` default), section checkboxes that pre-tick from the preset and become a custom selection when edited, a period selector (7 / 30 / 90 days, plus a custom number), a title field, and a **live preview** `<iframe>` pointed at `GET /api/v1/reports/preview?…` and refreshed 400 ms after the last change. The preview iframe is `sandbox="allow-same-origin"` (no scripts).
- `ReportLibrary({library, onOpen, onShare, onDelete})` — cards showing title, kind badge, period, created date, `meta.score`, and Open / Print / Share buttons. Print opens the report in a new tab and calls `print()` through the report's own toolbar button.
- `ShareDialog({card, onShare})` — an expiry selector (24 h / 7 d / 30 d), the resulting URL with a copy button, a plain warning that anyone with the link can read the report, and a "Rotate link" action that invalidates the previous one.
- `ScheduleEditor({schedule, onSave})` — weekly (Mondays) / monthly (the 1st) toggles, preset, hour, and a "push the link to my alert channels" checkbox; saves through `POST /api/v1/reports/schedule`.
- Building shows a progress state and toasts the finished card; the new card is prepended without a full refetch.

- [x] **Step 1: Write the failing test** (`web/tests/reports.test.tsx`)
```tsx
it("pre-ticks a preset's sections and debounces the preview", async () => {
  vi.useFakeTimers();
  const { fetchCount } = renderPage(<Reports />, { reports: reportsFixture });
  await userEvent.selectOptions(await screen.findByLabelText(/preset/i), "security");
  expect(screen.getByLabelText("Servers & security posture")).toBeChecked();
  expect(screen.getByLabelText("Presence & screen time")).not.toBeChecked();
  vi.advanceTimersByTime(500);
  expect(fetchCount("/api/v1/reports/preview")).toBe(1);
});
it("builds with the chosen period and title", async () => {
  const { posted } = renderPage(<Reports />, { reports: reportsFixture });
  await userEvent.type(await screen.findByLabelText(/title/i), "Week 37");
  await userEvent.selectOptions(screen.getByLabelText(/period/i), "30");
  await userEvent.click(screen.getByRole("button", { name: /build report/i }));
  expect(posted("/api/v1/reports/build")).toMatchObject({ days: 30, title: "Week 37" });
});
it("shows the share URL and warns that it is public", async () => {
  renderPage(<Reports />, { reports: reportsFixture,
    share: { share_url: "https://guardian.example.com/r/abc123" } });
  await userEvent.click((await screen.findAllByRole("button", { name: /share/i }))[0]);
  await userEvent.click(screen.getByRole("button", { name: /create link/i }));
  expect(await screen.findByDisplayValue(/\/r\/abc123$/)).toBeInTheDocument();
  expect(screen.getByText(/anyone with the link/i)).toBeInTheDocument();
});
```
- [x] **Step 2–4:** run → FAIL → implement → clean.
- [x] **Step 5: Commit** — `web: Reports tab (builder with live preview, library, share links, schedule)`

---

### Task 46: Settings area

Web-only. Depends on 22, 23, 39; concurrent with 45, 47, 48.

**Files:**
- Create: `web/src/pages/Settings/{index.tsx,Profile.tsx,Users.tsx,Targets.tsx,AutoFix.tsx,Thresholds.tsx,Appearance.tsx,Sensors.tsx,Tenants.tsx,AuditLog.tsx}`, `web/tests/settings.test.tsx`
- Modify: `web/src/App.tsx` (routes `/settings` and `/settings/:section`)

**Interfaces:**
- A sub-nav with Profile, Users & roles, Sites & targets, Auto-fix policy, Alert thresholds, Appearance, Sensors, Audit log, and — for superadmins only — Tenants. Integrations is its own tab (Task 47) linked from here.
- `Profile` — owner/company, e-mail, address (used on report covers), with a live preview line of how the cover will read.
- `Users` — a `Table` of members with an inline role select, an "Invite" row (username + e-mail + role) and a Remove button; the last-admin guard's 422 is surfaced as an inline error, not a toast.
- `Targets` — two editable tables (Websites: name, URL, expected codes, public toggle; Servers: name, ssh target, optional flag, expected services/containers) with add/remove rows and a single Save that POSTs the whole set; invalid rows are flagged inline before the request.
- `AutoFix` — the kill switch (a prominent toggle with a confirmation for turning it off), daily cap, attacker-method select, maintenance window pickers, and a read-only list of the current cooldowns.
- `Thresholds` — number inputs with the hub's ranges as `min`/`max`, showing the current live value beside each ("disk warn 80% · prod is at 56%").
- `Appearance` — the three-way theme control, mirrored with the top-bar toggle.
- `Sensors` — a table of sensors with status, platform, version, last seen; a "Generate enrol code" button that shows the code with a countdown and the exact copy-able command `guardian-sensor enrol <CODE> --hub https://guardian.example.com`; a Revoke button with confirmation.
- `Tenants` (superadmin) — list/create/rename, brand accent, ntfy topic, and a demo toggle that shows the fixed sign-in code.
- `AuditLog` — a filterable `Table` (action, user, date range) rendering `AuditRow`s, with the `body` expandable per row.
- Every control is disabled with a role explanation for users below the required role; nothing is hidden, so a viewer can see what an admin could change.

- [x] **Step 1: Write the failing test** (`web/tests/settings.test.tsx`)
```tsx
it("saves thresholds and shows the live value beside each field", async () => {
  const { posted } = renderPage(<Settings />, { settings: settingsFixture, hosts: hostsFixture },
                                { route: "/settings/thresholds" });
  expect(await screen.findByText(/prod is at 56%/)).toBeInTheDocument();
  await userEvent.clear(screen.getByLabelText(/disk warn/i));
  await userEvent.type(screen.getByLabelText(/disk warn/i), "85");
  await userEvent.click(screen.getByRole("button", { name: /save/i }));
  expect(posted("/api/v1/settings/thresholds")).toMatchObject({ disk_pct_warn: 85 });
});
it("confirms before turning the auto-fix kill switch off", async () => {
  const { posted } = renderPage(<Settings />, { settings: settingsFixture }, { route: "/settings/autofix" });
  await userEvent.click(await screen.findByRole("switch", { name: /automatic fixes/i }));
  expect(screen.getByRole("alertdialog")).toHaveTextContent(/stop fixing things on its own/i);
  await userEvent.click(screen.getByRole("button", { name: /turn it off/i }));
  expect(posted("/api/v1/settings/autofix")).toMatchObject({ enabled: false });
});
it("shows an enrol code with the exact sensor command", async () => {
  renderPage(<Settings />, { settings: settingsFixture, enrol: { code: "ABCD2345", expires: "…" } },
             { route: "/settings/sensors" });
  await userEvent.click(await screen.findByRole("button", { name: /generate enrol code/i }));
  expect(await screen.findByText(/guardian-sensor enrol ABCD2345 --hub /)).toBeInTheDocument();
});
it("disables writes for a viewer and says which role is needed", async () => {
  renderPage(<Settings />, { settings: settingsFixture, me: viewerMe }, { route: "/settings/thresholds" });
  expect(await screen.findByLabelText(/disk warn/i)).toBeDisabled();
  expect(screen.getByText(/admin access/i)).toBeInTheDocument();
});
```
- [x] **Step 2–4:** run → FAIL → implement → clean.
- [x] **Step 5: Commit** — `web: Settings area (profile, users, targets, auto-fix, thresholds, appearance, sensors, tenants, audit)`

---

### Task 47: Integrations tab

Web-only. Depends on 22, 23, 40, 41; concurrent with 45, 46, 48.

**Files:**
- Create: `web/src/pages/Integrations.tsx`, `web/src/components/{IntegrationCardView.tsx,RouteMatrix.tsx,InboundHint.tsx}`, `web/tests/integrations.test.tsx`
- Modify: `web/src/App.tsx` (route `/integrations`)

**Interfaces:**
- One card per integration in the hub's order (ntfy first as the default channel, then Slack, Teams, webhook, Discord, PagerDuty, Telegram, Jira/ServiceNow, Home Assistant). Each card shows: title, one-line "what it does for you" from `help`, an enabled switch, the `fields` rendered by `kind` (`secret` fields render masked with a "Replace" affordance that clears before typing — the masked value is never sent back), a `RouteMatrix` of CRIT/WARN/INFO checkboxes, a **Test** button showing a spinner then a green/red result line with `last_error` verbatim, and the last-tested relative time.
- `InboundHint` for integrations whose `direction` includes `in`: the inbound URL with a copy button, plus the one-line instruction for that provider ("Paste this into PagerDuty → Integrations → Webhooks v3", "Set this as your Discord app's Interactions Endpoint URL", "Send `/start <code>` to your bot"). The Telegram card additionally has a Pair button that shows the 6-character code and polls until `config.chat_id` appears.
- Saving posts only the changed fields; a card in an error state keeps its input values so nothing is retyped.

- [x] **Step 1: Write the failing test** (`web/tests/integrations.test.tsx`)
```tsx
it("lists all nine integrations with ntfy first", async () => {
  renderPage(<Integrations />, { integrations: integrationsFixture });
  const cards = await screen.findAllByTestId("integration-card");
  expect(cards).toHaveLength(9);
  expect(cards[0]).toHaveTextContent(/ntfy/i);
});
it("never sends the masked secret back to the hub", async () => {
  const { posted } = renderPage(<Integrations />, { integrations: integrationsFixture });
  const slack = (await screen.findAllByTestId("integration-card"))[1];
  await userEvent.click(within(slack).getByRole("checkbox", { name: "INFO" }));
  await userEvent.click(within(slack).getByRole("button", { name: /save/i }));
  const body = posted("/api/v1/integrations");
  expect(body.routes.INFO).toBe(true);
  expect(JSON.stringify(body)).not.toContain("…");
});
it("shows the test error verbatim", async () => {
  renderPage(<Integrations />, { integrations: integrationsFixture,
                                 test: { ok: false, error: "ntfy 503" } });
  await userEvent.click((await screen.findAllByRole("button", { name: /^test$/i }))[0]);
  expect(await screen.findByText("ntfy 503")).toBeInTheDocument();
});
it("shows the inbound URL only for inbound-capable integrations", async () => {
  renderPage(<Integrations />, { integrations: integrationsFixture });
  const cards = await screen.findAllByTestId("integration-card");
  const inbound = integrationsFixture.integrations.filter((i) => i.direction.includes("in")).length;
  expect(screen.getAllByTestId("inbound-url")).toHaveLength(inbound);
  expect(within(cards[0]).queryByTestId("inbound-url")).toBeNull();   // ntfy is out-only
});
```
- [x] **Step 2–4:** run → FAIL → implement → clean.
- [x] **Step 5: Commit** — `web: Integrations tab (nine cards, severity routes, Test, inbound URLs, Telegram pairing)`

---

### Task 48: Guardian chat panel

Web-only. Depends on 22, 23, 43, 44; concurrent with 45, 46, 47.

**Files:**
- Create: `web/src/shell/chat/{Composer.tsx,Message.tsx,ToolSteps.tsx,ProposalCard.tsx,useChat.ts}`, `web/tests/chat.test.tsx`
- Modify: `web/src/shell/ChatPanel.tsx` (replace the phase-2 stub)

**Interfaces:**
- The panel is a right-side column (360 px, full-width sheet under 700 px), toggled from the top bar and by `⌘/`. It sends `{message, thread, tab}` where `tab` is the current route's tab key, so Guardian knows what the user is looking at.
- `Message` renders the strict format: the verdict line as a heading, the bullets as `Name · IP · fact` rows with the entity linked into the app where it resolves to a device/host/site, and the `Next:` line as a distinct offer row. Markdown is **not** rendered as HTML — only `*emphasis*` is turned into `<em>`; everything else is text, so a model cannot inject markup.
- `ToolSteps` shows each tool as it runs: name, tier badge (1/2/3), status (running → done/failed), and, for queued tools, a live status driven by the `queue` live frame with a spinner until `done`, then the result line.
- `ProposalCard` renders `ChatResponse.proposal`: the summary, each `ProposedAction` with its host, effect and reversibility, and two buttons — **Yes, do it** (re-POSTs with `confirm_token`) and **No**. The card expires visually at `proposal.expires` and the buttons disable.
- `useChat()` keeps the thread in memory (not persisted), disables the composer while a turn is in flight, retries once on a network error, and surfaces a rate-limit 429 as "Guardian is catching up — try again in a moment."
- A viewer sees the panel and can chat; the refusal text the hub returns is rendered as-is.

- [x] **Step 1: Write the failing test** (`web/tests/chat.test.tsx`)
```tsx
it("renders the verdict, bullets and the Next offer", async () => {
  renderShellWithChat({ chat: chatAnswerFixture });
  await userEvent.type(screen.getByRole("textbox", { name: /ask guardian/i }), "how are things?{Enter}");
  expect(await screen.findByRole("heading", { name: chatAnswerFixture.verdict })).toBeInTheDocument();
  expect(screen.getAllByTestId("chat-bullet")).toHaveLength(chatAnswerFixture.bullets.length);
  expect(screen.getByTestId("chat-next")).toHaveTextContent(chatAnswerFixture.next);
});
it("asks before a tier-3 action and confirms with the token", async () => {
  const { posted } = renderShellWithChat({ chat: chatProposalFixture });
  await userEvent.type(screen.getByRole("textbox", { name: /ask guardian/i }), "fix it{Enter}");
  expect(await screen.findByTestId("proposal")).toHaveTextContent(/reboot safehome/i);
  await userEvent.click(screen.getByRole("button", { name: /yes, do it/i }));
  expect(posted("/api/v1/chat").confirm_token).toBe(chatProposalFixture.proposal.token);
});
it("never renders model HTML", async () => {
  renderShellWithChat({ chat: { ...chatAnswerFixture, text: "<img src=x onerror=alert(1)>", bullets: [] } });
  await userEvent.type(screen.getByRole("textbox", { name: /ask guardian/i }), "hi{Enter}");
  expect(await screen.findByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
  expect(document.querySelector("img[src='x']")).toBeNull();
});
it("tracks a queued tool through the live frame", async () => {
  const { emit } = renderShellWithChat({ chat: chatQueuedFixture });
  await userEvent.type(screen.getByRole("textbox", { name: /ask guardian/i }), "run a scan{Enter}");
  expect(await screen.findByTestId("tool-step")).toHaveTextContent(/queued/i);
  emit({ type: "queue", ts: "…", data: { id: chatQueuedFixture.tool_steps[0].queue_id, status: "done" } });
  expect(await screen.findByTestId("tool-step")).toHaveTextContent(/done/i);
});
```
- [x] **Step 2–4:** run → FAIL → implement → clean.
- [x] **Step 5: Commit** — `web: Guardian chat panel (strict format, tool steps, tier-3 proposal cards, queue tracking)`

---

### Task 49: Onboarding wizard, mobile pass, accessibility pass

Web-only. Depends on 22–26, 32–35, 45–48.

**Files:**
- Create: `web/src/pages/Onboarding.tsx`, `web/src/components/EmptyState.tsx`, `web/tests/onboarding.test.tsx`, `web/tests/a11y.test.tsx`
- Modify: `web/src/App.tsx` (route `/welcome` + redirect), every page (empty states, mobile layout fixes)

**Interfaces:**
- `Onboarding` — four steps, shown on the first login of a tenant with no `Scan`: (1) name the site (`POST /api/v1/settings/profile`), (2) install the sensor — the OS picker (macOS / Linux / Docker) with the exact command for each and the enrol code from `POST /api/v1/sensors/enrol-code` with its countdown, (3) waiting for the first scan — a live spinner driven by the `scan` WebSocket frame with a "still nothing?" troubleshooting list, (4) label unknown devices — the unverified list with Trust / Label inline, then Done. Progress is stored in `Tenant.settings["onboarding_step"]` so the wizard resumes.
- `EmptyState({icon, title, body, action})` — used by every tab when its payload is empty, so no tab ever renders a blank panel (e.g. DNS with the filter off links to the wizard; Reports with no library offers "Build your first report").
- Mobile pass: at 375 px every tab is usable — tables scroll inside their own container, the drawer becomes a bottom sheet, the topology graph gets pinch-zoom and a "fit" button, the report preview collapses behind a "Preview" toggle, the chat panel is a full-height sheet, and the ⌘K palette is reachable from a search icon in the mobile top bar.
- Accessibility pass: every interactive element is reachable and operable by keyboard; focus rings are visible in both skins; the drawer and palette trap focus and restore it; every icon-only button has an `aria-label`; the score ring and gauges expose `role="meter"` with `aria-valuenow`/`aria-valuetext`; live regions (`role="status"`) announce toasts and new CRIT alerts; colour is never the only signal (every state also carries a shape, icon or word); contrast ≥ 4.5:1 for text in both skins.

- [x] **Step 1: Write the failing tests**
```tsx
// onboarding.test.tsx
it("walks a fresh tenant to the enrol command and advances on the first scan", async () => {
  const { emit } = renderPage(<Onboarding />, { me: freshMe, overview: emptyOverview,
                                                enrol: { code: "ABCD2345", expires: "…" } });
  await userEvent.type(await screen.findByLabelText(/what should we call/i), "Home{Enter}");
  await userEvent.click(screen.getByRole("button", { name: /macOS/i }));
  expect(screen.getByText(/guardian-sensor enrol ABCD2345/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /next/i }));
  expect(screen.getByText(/waiting for the first scan/i)).toBeInTheDocument();
  emit({ type: "scan", ts: "…", data: { score: 83, devices: 24, alerts: 9 } });
  expect(await screen.findByText(/label unknown devices/i)).toBeInTheDocument();
});
// a11y.test.tsx
it.each(TABS)("%s has no icon-only button without a label and traps focus in its drawer", async (tab) => {
  const { container } = renderTab(tab);
  for (const b of container.querySelectorAll("button")) {
    if (!b.textContent?.trim()) expect(b).toHaveAttribute("aria-label");
  }
  expect(await axe(container)).toHaveNoViolations();
});
```
(`axe` from `jest-axe`, installed with `npm install -D --save-exact jest-axe @types/jest-axe` in Step 1 and pinned as in Task 22.)
- [x] **Step 2–4:** run → FAIL → implement → clean. Manual: walk all nine tabs at 375 px in both skins and with the keyboard only.
- [x] **Step 5: Commit** — `web: onboarding wizard, empty states, mobile pass, accessibility pass`

---

### Task 50: Phase 4 closeout — full smoke, deploy, live verification of every tab

Serial. Depends on 37–49.

**Files:** Modify `web/e2e/smoke.spec.ts` (all nine tabs + chat + reports), `hub/guardian_hub/selftest.py` (report build, share-link expiry, ntfy test, chat rules fallback), `deploy/deploy.sh`, `docs/superpowers/plans/2026-09-09-guardian-phase2-4-dashboard.md` (tick every box)

- [x] **Step 1:** extend `smoke.spec.ts`: Reports builds an executive report and shares it (the `/r/<token>` page loads in a fresh context with no cookie); Settings changes a threshold and it survives a reload; Integrations shows nine cards and a Test result; the chat panel answers with the rules provider and a "fix it" produces a proposal card that is **not** executed until Yes; the public `/status/demo` page loads with no session. All three projects (light, dark, 375 px).
- [x] **Step 2:** full suite green — `cd hub && pytest -q`; `cd sensor && pytest -q` (Python 3.10); `cd web && npm test && npx tsc -b && npm run lint && npx playwright test`.
- [ ] **Step 3 — deliberately not done:** `selftest.run` still has `_check_ntfy()` and `_check_reports_writable()` only. The three new checks fall outside Task 50 as the controller re-scoped it, and checks that can fail would blur the accepted deploy baseline of "`FAIL dns:home` only". See `task-50-report.md` §9.1.
- [x] **Step 4: Deploy** — `deploy/deploy.sh` against the live host `safehome.example.com:8443` (not the `guardian.example.com` name this plan predates); `guardian selftest` reports `FAIL dns:home` only, the accepted baseline until the DNS cutover; `/metrics` serves `guardian_` metrics on loopback and is now **403 from the internet** (it was not before — see the report); `/status/demo` renders with real service rows.
- [x] **Step 5: Browser check of every tab** — walked as both `demoadmin` and `miro` (superadmin), both skins, desktop and 375 px, including all eleven Settings sub-pages and the viewer's admin-only view. The per-tab table in `task-50-report.md` §8 stands in for the per-tab screenshots, and lists the three defects the walk found.
- [ ] **Step 6 — not this session:** push to `aicode.example.com` GitLab (`ai/guardian`) and the `project_guardian_app` memory update. The closeout session is explicitly not the one that pushes.
- [x] **Step 7: Commit** — landed as `web+hub: phase-4 closeout — merge, smoke coverage, deploy`.

---

## Self-review — spec coverage

Written after the plan, by walking spec §6 route by route and §8 bullet by bullet. `P1` means the route already shipped in the phase-1 plan (Tasks 1–15) and this plan only extends or re-homes it.

### §6 — Hub API routes → task

| Route | Task |
|---|---|
| `POST /auth/start`, `POST /auth/verify`, `POST /auth/logout`, `GET /me`, `POST /me/tenant` | P1 (T3); demo fixed code added in **17** |
| tenants CRUD, brand, `POST /tenants/{id}/enrol-code` | P1 (T4); UI-facing wrappers + demo toggle in **39**, rendered in **46** |
| `POST /sensor/enrol`, `POST /sensor/ingest`, `GET /sensor/edits`, `GET /sensor/queue`, `POST /sensor/queue/{id}/result`, `GET /sensor/config` | P1 (T5); `POST /sensor/queue/{id}/progress` added in **29** |
| `GET /overview` | **18** |
| `GET /sites` | **19** |
| `GET /hosts` | **19** |
| `GET /devices` | **19** |
| `GET /devices/{mac}` (timeline, presence, ports, dns profile) | **19** (dns profile re-homed onto `dnsview.device_profile` in **30**) |
| `GET /topology` | **31** |
| `GET /metrics?name&range` (time-series read) | P1 (T6), consumed by **24**, **26**, **33** |
| `GET /events` | P1 (T6); merged into the Overview timeline in **18** |
| `GET /inbox?view=incidents\|alerts\|recs` | **20** |
| `GET /security` (scorecards, checks with proof, attackers with geo, wifi guard) | **28** |
| `GET /updates` (calendar, cves, upgrade progress) | **29** |
| `GET /dns` | **30** |
| `/dns/stream` via WebSocket | **21** (transport) + **30** (`dns` frames, opt-in) |
| `GET /reports`, `GET /reports/{id}` | **38** (engine in **37**) |
| `GET /integrations` | **40** |
| `GET /settings/*` | **39** |
| `GET /audit` | **39** |
| `GET /live` (10 s probes) | **18** |
| `POST /alerts/{id}/{ack\|snooze\|resolve\|reopen}` | P1 (T6); `POST /alerts/bulk` added in **20** |
| `POST /recs/{key} {decision}` | P1 (T6); queues the rec's action in **20** |
| `POST /devices/{mac}`, `POST /devices/{mac}/forget` | P1 (T6) |
| `POST /fix`, `POST /run`, `POST /speedtest` | P1 (T6) |
| `POST /maintenance` | **39** |
| `POST /attacker {method}` | **28** |
| `POST /reboot-plan {host, window}` | **29** |
| `POST /dns/rules` | **30** (plus `POST /reset-lan-baseline`) |
| `POST /reports/build`, `POST /reports/{id}/share`, `POST /reports/schedule` | **38** |
| `POST /integrations` (+ `/test`, `DELETE`) | **40**; five new providers in **41** |
| `POST /settings/*` | **39** |
| `POST /chat` | **44** (tools, tiers and gates in **43**) |
| `WS /ws` — scan-complete, new alert, device join/leave, fix result, dns stream, upgrade progress | **21**; `dns` frames **30**, `upgrade` frames **29** |
| `GET /metrics` (Prometheus) | **42** |
| `GET /status/<slug>` | **42** |
| `/hb/<name>/<token>` | **42** |
| `/act/<token>/…` | **42** (tokens minted by **40**/**41**) |
| `/r/<token>` | **42** |
| `POST /inbound/{kind}/{token}` (not in §6; required by §10.1 inbound integrations) | **41** |
| `GET /search?q=` (not in §6; required by the §8 ⌘K palette) | **23** |
| `GET /queue/{id}` (not in §6; required by §6's "queued → running → done" UI rule) | **21** |

### §8 — Web app design bullets → task

| Spec §8 bullet | Task |
|---|---|
| Two skins, blue/white palette, red/green/yellow only, Manrope + IBM Plex Mono, dataviz rules | **22** (tokens + Chart/Sparkline/Ring/Heatmap), enforced in every web task |
| Guardian shield logo mark | P1 (`web/src/Logo.tsx`); reused by **23** and the report cover **37** |
| Collapsible left sidebar, Inbox unread badge, tenant switcher | **23** |
| Top bar: ⌘K global search, sensor pulse dot, "last scan Ns ago", theme toggle, user menu | **23** |
| Right-side Guardian panel toggled from the top bar | **23** (chrome) + **48** (chat) |
| Mobile: bottom tab bar under 700 px, every tab usable on a phone | **23** (bar) + **49** (pass) |
| Onboarding wizard (name site → install sensor → waiting for first scan → label devices) | **49** |
| **1 Overview** — score ring, verdict sentence, one next-action button | **18** + **24** |
| **1 Overview** — per-site/host 24 h health strip (hour blocks) | **18** (bucketing) + **24** |
| **1 Overview** — live "what changed" timeline with live inserts | **18** + **24** (+ **21** transport) |
| **1 Overview** — 30-day score trend with the deduction that moved it | **18** + **24** |
| **1 Overview** — five questions strip | **18** + **24** |
| **2 Inbox** — one list, three filters, unread counts, j/k, bulk ack | **20** + **25** |
| **2 Inbox** — row: severity stripe, title, entity, why it fired, what changed, actions; incidents expand | **20** + **25** |
| **3 Assets** — Websites cards (uptime 24h/7d, latency spark, cert days, headers) | **19** + **26** |
| **3 Assets** — Servers gauge cards, hardening grade, ports, containers/services | **19** + **26** |
| **3 Assets** — Devices grid, kind filter chips, online dot, risk badge | **19** + **26** |
| **3 Assets** — Screen time (presence table + hour heatmap) | **19** (+ `PresenceSample` in **16**) + **26** |
| **3 Assets** — drawer: timeline, ports, presence, DNS profile, merge/split, quarantine, always-on | **19** + **26** |
| **4 Topology** — force-directed internet → router → extenders → devices, drag/zoom/pan | **31** + **35** |
| **4 Topology** — cluster by kind or area, real icons, status ring, pulsing link, risk badge, red ring on untrusted cameras, new device pops in, hover card, click → drawer | **31** + **35** |
| **5 Security** — posture scorecard per target (grade, top 3 deductions, fix now) | **28** + **32** |
| **5 Security** — Wi-Fi guard panel with one verdict | **28** + **32** |
| **5 Security** — checks as a checklist with pass/fail, last verified, evidence, auto-fix status | **28** + **32** |
| **5 Security** — attack map with counts and Guardian's action | **28** (geo) + **32** (map) |
| **5 Security** — exposure tiles | **28** + **32** |
| **5 Security** — attacker method selector | **28** + **32** |
| **6 Updates** — patch calendar heatmap per host with reboot marks | **29** + **33** |
| **6 Updates** — CVE table with critical filter and tracker link | **29** + **33** |
| **6 Updates** — live upgrade progress (packages done/total, log tail) | **29** (sensor + hub) + **33** |
| **6 Updates** — reboot planner (window → Guardian reboots → confirms services back) | **29** + **33** |
| **7 DNS** — live query stream (pause, search, client badge) | **21**/**30** + **34** |
| **7 DNS** — per-device DNS profile | **30** + **34** (also in the drawer, **26**) |
| **7 DNS** — block/allow manager with list toggles and per-list block counts | **30** + **34** |
| **7 DNS** — setup wizard with a live is-it-working check | **30** + **34** |
| **8 Reports** — builder with live preview, preset dropdown (11), section checkboxes, period | **37**/**38** + **45** |
| **8 Reports** — library cards (score, period, open/print/share) | **38** + **45** |
| **8 Reports** — expiring share links | **38** (mint) / **42** (serve) + **45** |
| **8 Reports** — scheduled report editor (weekly/monthly, preset, ntfy push) | **38** + **45** |
| **9 Settings** — Profile, Users & roles, Sites & targets, Auto-fix policy, Alert thresholds, Appearance, Sensors | **39** + **46** |
| **9 Settings** — Integrations (ntfy, Slack, Teams, webhook + severity routes + test) | **40** + **47** |
| **9 Settings** — Superadmin: Tenants | **39** + **46** |

### Other spec sections

| Item | Task |
|---|---|
| §7 chat tools `device_deep_dive`, `host_command`, `site_check`, `lookup_ip`, `rescan` | **43** |
| §7 action tools `apply_fix`, `ack_alert`, `resolve_alert`, `trust_device`, `quarantine_device`, `decide_recommendation`, `speedtest` | **43** |
| §7 three action tiers, role gates, confirm-before-tier-3, `by="chat:<user>"` audit, queue polling | **43** (+ **44** for the HTTP turn) |
| §7 "ask first, then act", capability-gap sentences, never a shell recipe, never break Guardian itself | **43** (`capability_gap`, hub-port escalation) + **44** (`prompt.SYSTEM`) |
| §7 explains its own fixes as its own ("this was us") | **43** (`explain_alert`) |
| §7 provider ladder Groq 120b → 20b → Anthropic → rules, capped context, strict answer format | **44** |
| §9 report sections, weekly/monthly archives, retention | **37** (sections) + **38** (schedule, library) |
| §10 ntfy default, action buttons, dedupe, CRIT resend, urgent escalation, digest, Monday summary | **40** (ntfy `watchdog`; hub takes delivery over from `sensor/guardian_sensor/notify.py`) + **42** (`/act` buttons) |
| §10.1 Discord, PagerDuty, Telegram, Jira/ServiceNow, Home Assistant — each with a watchdog duty | **41** |
| §10.1 common rules: per tenant, Test button, audited, encrypted secret, inbound token | **40** (core) + **41** (inbound route) |
| §11 rate limits on public routes, tenant-scoped sensor token, audit every human write | Global Constraints; enforced in **39**, **41**, **42** |
| §14 Playwright smoke in both skins at 375 px; browser verification before a phase closes | **27**, **36**, **50** |

### Not mapped to a task (deliberately out of scope for this plan)

Revised by the 2026-09-09 addendum: the three items that used to head this list (§12 cutover, §12 DNS
cutover, §9 retention enforcement) are now Tasks 56, 56 Step 6 and 55. What remains are true non-goals.

1. **Billing / Stripe** — explicitly deferred by spec §9's "Billing (later)" and §1's non-goals.
2. **Mobile app** — spec §1 non-goal ("comes after the web app is proven").
3. **Marketing website on its own domain** — spec §1 non-goal; the business-plan artifact covers it.
4. **SMS and e-mail alert delivery** — spec §1/§10 non-goal. ntfy is the only default channel; e-mail is
   used for the login code only. The optional integrations of §10.1 are all in Tasks 40–41.
5. **Postgres migration** — spec §2 says the schema uses no SQLite-only features so Postgres is a
   connection-string change. Nothing in this plan runs against Postgres, and no task migrates data; the
   0002/0003/0004 migrations are written SQLite-safe (nullable or server-defaulted columns, `batch_alter_table`
   with `copy_from` for the two recreated tables) so the switch stays a config change.
6. **`Scan.raw` retention (known gap).** Task 55 prunes `metric`, `event`, `dns_query`, `presence_sample`,
   `audit` and `report` per the enumerated windows and deliberately never touches `Scan` — every payload
   builder reads the newest `Scan.raw`, and the report engine reads older ones. `scan` is therefore the
   largest table on disk (one full `last.json` per 5 min per tenant) and grows without bound. Compacting it
   (keep every scan for 7 days, then one per hour, then one per day) needs a decision about what the
   Overview timeline and the report engine may lose, so it is left as a follow-up rather than guessed at
   here.

### Consistency check performed

- **Placeholder scan:** `grep -nEi "TBD|to be determined|similar to task|as above|same as task|add validation|etc\.|FIXME|XXX|lorem"` over this file returns only the Global-Constraints rule that *bans* placeholders and the abstract-method `...` bodies in the `base.Integration` protocol sketch (which are intentional Python ellipsis stubs in an interface declaration, not unwritten work).
- **Type-name parity:** every type named in a task's Interfaces or test code appears in the Task 16 contract table, and Task 16's own test (`test_every_payload_model_is_mirrored_in_types_ts`) enforces the same set across `schemas_dash.py` and `web/src/types.ts` at build time, so drift fails CI rather than review.
- **Route-name parity:** every `POST`/`GET` path quoted in a web task's test exists in a hub task's Interfaces block; `/api/v1/search` (Task 23) and `/api/v1/queue/{id}` (Task 21) are the two routes this plan adds beyond spec §6, both listed above.
- **Field-name conventions:** `passed` (never `pass`), `age_s`, `*_pct`, `*_seen`, plural list fields — applied uniformly across `schemas_dash.py`, `types.ts` and every test snippet.
- **Nexus citations:** every ported feature names its source file and line range (`server.py:117`, `:131`, `:282–303`, `:305–334`, `:338–352`, `:360–521`, `:522–571`, `:582`, `:603`, `:644`, `:727–748`, `:767`, `:780`, `:797`, `:803`, `:822`, `:829`, `:921`, `:991`; `index.html:253`, `:262`; and the sensor's own `fix.py`, `sync.py`, `runner.py`, `dnsfilter.py` line references).

---

## Addendum (2026-09-09): Tasks 51–56

Two spec areas the main plan deliberately left out are now in scope, because both are cheap to get
wrong later and expensive to retrofit: **multiple networks per tenant** (spec §3.1, Miro's "my Wi-Fi
and my lab's private network") and **retention + the phase-5 cutover** (spec §9, §12, §13.5).
Numbering continues after Task 50. Everything in the Global Constraints above still applies.

### Addendum invariants (read before touching 51–54)

These four rules exist so multi-network support stays additive and the Nexus parity tests keep
passing. Every task below cites them.

1. **The primary network is the legacy path, by reference.** `res["lan"]` and
   `state["lan"]["known_macs"]` continue to be *the primary network's* sweep and known-devices map —
   the same dict objects that live at `state["lan"]["networks"][<primary slug>]["known_macs"]`, shared
   by reference, never copied. Consequence: `alerts.build_alerts`, `runner`'s history line, the hub's
   `ingest.store_scan` device loop and the phase-1/phase-2 tests all keep working untouched for a
   single-network sensor. Additional networks are *added alongside*, never by rewriting the primary
   path (this is the "never alter existing bot logic" rule applied to the collector).
2. **Alert keys: legacy for the primary network, namespaced for the rest.** One key builder,
   `alerts.lan_key(kind, net, mac=None, port=None)`:
   - primary network → `lan:new:<mac>`, `lan:port:<mac>:<port>`, `lan:unverified`, `home:rogue-dhcp`,
     `home:evil-twin`, `home:gw-mac` — byte-identical to today, so `sensor/tests/test_parity_alerts.py`
     stays green with the single-network fixture;
   - any other network → `lan:new:<slug>:<mac>`, `lan:port:<slug>:<mac>:<port>`,
     `lan:unverified:<slug>`, `home:rogue-dhcp:<slug>`, `home:evil-twin:<slug>`, `home:gw-mac:<slug>`.
3. **The hub never parses an alert key to learn its network.** The sensor stamps every LAN/home alert
   dict with `"net": "<slug>"` (the primary network included), and `ingest` resolves that slug to
   `Network.id`. Same for `events` lines, `fixes` rows that name a device, and `dns_queries` lines.
   The web app reads `row.network`, never a substring of `row.id`.
4. **`slug` is the cross-boundary network identity.** `slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24]`,
   unique per tenant; the hub owns `Network.id` (opaque) and `Network.slug` (stable, sensor-visible).
   Sensor config, `state.lan.networks`, alert `net` fields and `?network=` all accept the slug; the API
   also accepts the id. `Network.primary` is true for exactly one network per tenant — the one created
   from the sensor's `lan` block.

---

### Task 51: `network` table, migration 0003, network-scoped ingest and reads

Hub-only. **Depends on Task 16. Second global blocker: Tasks 52, 53 and 54 all wait on it.**

**Files:**
- Create: `hub/guardian_hub/networks.py`, `hub/guardian_hub/routers/networks.py`,
  `hub/alembic/versions/0003_networks.py`, `hub/tests/test_networks.py`
- Modify: `hub/guardian_hub/models.py` (the `Network` model, `network_id` on four tables + `presence_sample`,
  two widened uniqueness rules), `hub/guardian_hub/ingest.py` (auto-create + backfill + four dedup keys),
  `hub/guardian_hub/main.py` (include `networks.router`), `hub/guardian_hub/routers/read.py`
  (`?network=` on `/devices`, `/devices/{mac}`, `/inbox`, `/events`, `/presence`),
  `hub/guardian_hub/routers/write.py` (`?network=` on `/devices/{mac}`, `/devices/{mac}/forget`; `net`
  in the four device `Edit` bodies), `hub/guardian_hub/payloads/{overview,assets,inbox,topology,dnsview,presence}.py`,
  `hub/guardian_hub/schemas_dash.py`, `web/src/types.ts`, `hub/guardian_hub/seed.py`,
  `hub/tests/{test_ingest,test_assets,test_inbox,test_topology,test_dns,test_seed}.py`,
  `web/tests/types.test.ts` (Task 16's exhaustive `OverviewPayload` / `DeviceCard` / `InboxRow` object
  literals gain the new fields, or they stop type-checking)

**Interfaces:**

*Model (`models.py`):*
```python
class Network(Base):
    __tablename__ = "network"
    __table_args__ = (UniqueConstraint("tenant_id", "slug"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))                      # "Office Wi-Fi", "Lab"
    slug: Mapped[str] = mapped_column(String(24))                      # invariant 4
    kind: Mapped[str] = mapped_column(String(8), default="wifi")       # wifi|wired|lab|vlan|guest
    subnet_prefix: Mapped[str] = mapped_column(String(15), default="") # "192.168.1"
    expected_gateway: Mapped[str] = mapped_column(String(45), default="")
    ssid: Mapped[str] = mapped_column(String(64), default="")
    sensor_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    colour: Mapped[str] = mapped_column(String(16), default="")        # a token name, never a hex value
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="pending") # pending|active|unreachable|no-sensor
    created: Mapped[dt.datetime] = mapped_column(TZDateTime, default=utcnow)
```
Column additions: `network_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)`
on `Device`, `Event`, `Alert`, `DnsQuery` **and** `PresenceSample`. Two uniqueness rules widen:
`Device` loses `UniqueConstraint("tenant_id", "mac")` and gains
`UniqueConstraint("tenant_id", "network_id", "mac")`; `PresenceSample` goes from
`UniqueConstraint("tenant_id", "ts", "mac")` to `UniqueConstraint("tenant_id", "ts", "mac", "network_id")`.
**Decision (Task 54 depends on it):** presence is per network, not per tenant — the same laptop live on
Wi-Fi and in the lab at the same `ts` is two samples, so `presence.screen_time` can split by network and
its per-tenant totals are computed as `count(distinct (ts, mac))`, documented in that function's docstring.

*`networks.py`:*
- `slugify(name: str) -> str` — invariant 4, and the single implementation the whole hub imports.
- `ensure_primary(db, tenant, cfg: dict, raw: dict, sensor) -> Network` — called from `store_scan`. Reads
  `cfg["networks"][0]` when the sensor sent one, else `cfg["lan"]`; `name = cfg.get("name") or "Home network"`,
  `kind = "wifi" if (raw.get("home") or {}).get("wifi", {}).get("ssid") else "wired"`,
  `ssid` from the same place, `subnet_prefix`/`expected_gateway` straight from the block, `is_primary=True`,
  `status="active"`, `sensor_id=sensor.id if sensor else None`. Idempotent on `(tenant_id, slug)` and on
  "a primary already exists" (an existing primary is updated, never duplicated).
- `sync_from_config(db, tenant, cfg) -> list[Network]` — upserts one `Network` per `cfg["networks"]` entry
  in order (first = primary), sets `status="no-sensor"` for an entry no sensor reported, and never deletes.
- `backfill(db, tenant, network) -> dict` — one-shot: `UPDATE <table> SET network_id = :nid WHERE tenant_id = :tid
  AND network_id IS NULL` for `device`, `event`, `alert`, `dns_query`, `presence_sample`; returns row counts per
  table. Runs inside `store_scan` right after `ensure_primary`, so an existing single-network deployment
  lands wholly on its primary network on the first ingest after this task deploys.
- `resolve(db, tenant_id, ref: str | None) -> Network | None` — accepts an id or a slug; `None`/`""`/`"all"`
  → `None` (meaning "every network"). Raises `HTTPException(404, "no such network")` for an unknown ref.
- `for_client_ip(db, tenant_id, ip: str) -> Network | None` — longest `subnet_prefix` match; used as the
  fallback attribution when a pushed row carries no `net`.
- `health(db, tenant) -> list[NetworkHealth]` — the overview strip: per network the device count, online
  count, unverified count, open CRIT/WARN counts (alerts whose `network_id` matches), `sensor_ok`
  (`Sensor.last_seen` within 15 min) and `status`.

*`ingest.store_scan` changes (all four dedup keys, per the same-MAC-on-two-networks case):*
- after the `Scan` insert: `primary = networks.ensure_primary(db, tenant, tenant.config or {}, last, sensor)`,
  then `networks.backfill(db, tenant, primary)` when any of the five tables still has a `NULL network_id`
  row for this tenant, then `nets = networks.sync_from_config(db, tenant, tenant.config or {})`.
- `p["networks"]` (Task 52's push shape) is walked *in addition to* `state["lan"]["known_macs"]`: for each
  entry, `net = by_slug[entry["slug"]]`; devices come from `state["lan"]["networks"][slug]["known_macs"]`
  when present, else — for the primary only — from the legacy `state["lan"]["known_macs"]` (invariant 1).
  An entry with `{"status": "unreachable"}` updates `Network.status` and touches no device row.
- device upsert key becomes `{"tenant_id": tenant.id, "network_id": net.id, "mac": mac}`.
- `Event` dedup gains `network_id`; `DnsQuery` dedup gains `network_id`; `PresenceSample` (written by
  Task 16's loop) gains `network_id`; `Alert` keeps its `(tenant_id, key)` key — keys are already unique
  per network by invariant 2 — and gains `network_id` from `cur.get("net")`.
- attribution order for every row: the row's own `net` slug → `networks.for_client_ip` (dns queries) →
  the primary network.

*Routes (`routers/networks.py`, prefix `/api/v1`):*
- `GET /networks` (viewer+) → `NetworksPayload{networks}`.
- `POST /networks` (admin) `{name, kind, subnet_prefix, expected_gateway, ssid?, sensor_id?, colour?}` →
  `Network`; 422 on a duplicate slug, an unknown `kind`, a `subnet_prefix` failing
  `^(\d{1,3}\.){2}\d{1,3}$`, or an `expected_gateway` outside that prefix.
- `PATCH /networks/{id}` (admin) — same validation; `is_primary` is read-only.
- `DELETE /networks/{id}` (admin) — 422 for the primary network, 422 when the network still has devices
  unless `?reassign=<other network id>` is given (which moves its `device`/`event`/`alert`/`dns_query`/
  `presence_sample` rows first).
- Every write audits `network.create|update|delete` **and** writes exactly one
  `Edit(path="/api/config", body={"config": <the whole Tenant.config, with the rebuilt networks[] inside>})`.
  It must be the whole config: `sync.apply_edit`'s `/api/config` branch does `cfg.clear(); cfg.update(...)`,
  so a partial body would wipe the sensor's sites, hosts and thresholds.
- `?network=<id|slug>` accepted (and ignored when absent) by `GET /devices`, `/devices/{mac}`, `/inbox`,
  `/events`, `/presence`, `/topology`, `/dns`, `/security` and `POST /devices/{mac}`,
  `POST /devices/{mac}/forget`. When a MAC exists on more than one network and no `?network=` is given,
  the device routes act on the row with the newest `last_seen` and the response's `network` field says
  which — never an arbitrary `.first()`.
- The device `Edit` bodies (`/api/device`, `/api/device/forget`, `/api/device/quarantine`,
  `/api/device/merge`) gain `"net": <slug>` so Task 52's `apply_edit` can route the replay.
- `GET /overview` gains `networks: NetworkHealth[]`.

*Contract additions (`schemas_dash.py` + `web/src/types.ts`, same names both sides):*
```
Networks    NetworksPayload{networks}
            Network{id,slug,name,kind,subnet_prefix,expected_gateway,ssid,sensor_id,sensor_name,
                    colour,is_primary,status,devices,online,unverified,created}
            NetworkRef{id,slug,name,colour}
            NetworkHealth{id,slug,name,colour,state,status,devices,online,unverified,crit,warn,sensor_ok}
```
plus a `network: NetworkRef | null` field on `DeviceCard`, `InboxRow`, `EventRow`, `TopoNode`,
`DnsClient`, `DnsStreamRow`, `Attacker` and `ScreenTimeRow`; `network_id: str | None` on `KindChip` and
`TopoCluster`; `networks: list[NetworkHealth]` on `OverviewPayload`, `DevicesPayload` and `SecurityPayload`;
`network: NetworkRef | null` on `DeviceGroup` and on `WifiGuard`; `wifis: list[WifiGuard]` on
`SecurityPayload` (the single `wifi` field stays as the primary network's guard, so Task 32 keeps working).

*`seed.py` changes:* `seed_demo` creates two networks — `Office Wi-Fi` (`wifi`, `192.168.1`, gateway
`192.168.1.1`, primary, ssid `HomeWiFi`) and `Lab` (`lab`, `10.10.0`, gateway `10.10.0.1`) — and splits the
24 fixture devices between them: the router, phones, TVs, speakers and Amazon devices stay on
`192.168.1.x`; the six computer/printer/unknown MACs move to the Lab, **with their IPs rewritten into
`10.10.0.x`** (same host octet) so client-IP→network attribution, the per-network gateway node and the DNS
client join all render real data. The Lab gets its own synthetic gateway device
(`10.10.0.1`, mac `02:10:0a:00:00:01`, kind `router`, label `Lab switch`, trusted). Synthesised history
`live_macs`, the 3 000 DNS queries and the 40 events are split across both prefixes in the same proportion.

- [ ] **Step 1: Write the failing tests** (`hub/tests/test_networks.py`)
```python
from tests.test_seed_helpers import seeded_demo
from guardian_hub.db import SessionLocal
from guardian_hub import models as m, networks


def test_seed_makes_two_networks_with_real_subnets(client):
    seeded_demo(client)
    body = client.get("/api/v1/networks").json()["networks"]
    assert {n["name"] for n in body} == {"Office Wi-Fi", "Lab"}
    prim = next(n for n in body if n["is_primary"])
    assert prim["slug"] == "office-wi-fi" and prim["subnet_prefix"] == "192.168.1"
    lab = next(n for n in body if not n["is_primary"])
    assert lab["slug"] == "lab" and lab["subnet_prefix"] == "10.10.0" and lab["devices"] >= 6
    db = SessionLocal()
    for d in db.query(m.Device).filter_by(network_id=lab["id"]).all():
        assert d.ip.startswith("10.10.0."), d.ip


def test_every_scoped_route_filters_by_network(client):
    seeded_demo(client)
    nets = client.get("/api/v1/networks").json()["networks"]
    ids = {n["slug"]: n["id"] for n in nets}
    total = client.get("/api/v1/devices").json()["total"]
    per = {s: client.get(f"/api/v1/devices?network={i}").json()["total"] for s, i in ids.items()}
    assert sum(per.values()) == total and all(v > 0 for v in per.values())
    for path in ("/api/v1/inbox?view=alerts&", "/api/v1/events?", "/api/v1/topology?",
                 "/api/v1/dns?", "/api/v1/presence?days=7&"):
        r = client.get(f"{path}network={ids['lab']}")
        assert r.status_code == 200, path
    topo = client.get(f"/api/v1/topology?network={ids['lab']}").json()
    assert all(n["network"] is None or n["network"]["slug"] == "lab" for n in topo["nodes"])
    assert client.get("/api/v1/devices?network=nope").status_code == 404


def test_overview_carries_a_health_strip_per_network(client):
    seeded_demo(client)
    strip = client.get("/api/v1/overview").json()["networks"]
    assert len(strip) == 2
    assert all(s["state"] in ("good", "warn", "bad") and s["status"] in
               ("active", "pending", "unreachable", "no-sensor") for s in strip)
    assert sum(s["devices"] for s in strip) == client.get("/api/v1/devices").json()["total"]


def test_the_same_mac_on_two_networks_is_two_devices(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    nets = {n.slug: n for n in db.query(m.Network).filter_by(tenant_id=tid).all()}
    mac = "74:04:f1:4f:5a:ca"
    db.add(m.Device(tenant_id=tid, network_id=nets["lab"].id, mac=mac, ip="10.10.0.159",
                    presence="ONLINE", last_seen="2026-09-09T18:30:26-04:00"))
    db.commit()
    assert db.query(m.Device).filter_by(tenant_id=tid, mac=mac).count() == 2
    d = client.get(f"/api/v1/devices/{mac}?network={nets['lab'].id}").json()["device"]
    assert d["ip"] == "10.10.0.159" and d["network"]["slug"] == "lab"


def test_crud_validates_audits_and_ships_the_whole_config(client):
    tid = seeded_demo(client)
    bad = client.post("/api/v1/networks", json={"name": "Guest", "kind": "guest",
                                                "subnet_prefix": "192.168", "expected_gateway": "192.168.5.1"})
    assert bad.status_code == 422
    ok = client.post("/api/v1/networks", json={"name": "Guest Wi-Fi", "kind": "guest",
                                               "subnet_prefix": "192.168.5", "expected_gateway": "192.168.5.1"})
    assert ok.status_code == 200 and ok.json()["slug"] == "guest-wi-fi"
    db = SessionLocal()
    e = db.query(m.Edit).filter_by(tenant_id=tid, path="/api/config").order_by(m.Edit.id.desc()).first()
    cfg = e.body["config"]
    assert [n["slug"] for n in cfg["networks"]][-1] == "guest-wi-fi"
    assert cfg["sites"] and cfg["thresholds"], "the /api/config edit must carry the WHOLE config"
    assert db.query(m.Audit).filter_by(action="network.create").count() == 1
    prim = next(n for n in client.get("/api/v1/networks").json()["networks"] if n["is_primary"])
    assert client.request("DELETE", f"/api/v1/networks/{prim['id']}").status_code == 422
    lab = next(n for n in client.get("/api/v1/networks").json()["networks"] if n["slug"] == "lab")
    assert client.request("DELETE", f"/api/v1/networks/{lab['id']}").status_code == 422   # has devices
    assert client.request("DELETE", f"/api/v1/networks/{lab['id']}?reassign={prim['id']}").status_code == 200
    assert db.query(m.Device).filter_by(network_id=lab["id"]).count() == 0


def test_slugify_and_ip_attribution():
    assert networks.slugify("Office Wi-Fi") == "office-wi-fi"
    assert networks.slugify("Lab / VLAN 40!") == "lab-vlan-40"
    assert len(networks.slugify("a" * 60)) == 24
```
Appended to `hub/tests/test_ingest.py`:
```python
def test_ingest_creates_the_primary_network_and_backfills(client, db, tenant, sensor, last, state, hist):
    # a pre-existing row with no network (the state a live deployment upgrades from)
    db.add(m.Device(tenant_id=tenant.id, mac="aa:bb:cc:dd:ee:ff", ip="192.168.1.240"))
    db.commit()
    ingest.store_scan(db, tenant, sensor, {"last": last, "state": state, "history": hist})
    net = db.query(m.Network).filter_by(tenant_id=tenant.id, is_primary=True).one()
    assert net.subnet_prefix == "192.168.1" and net.expected_gateway == "192.168.1.1"
    assert net.ssid == "HomeWiFi" and net.status == "active"
    assert db.query(m.Device).filter_by(tenant_id=tenant.id, network_id=None).count() == 0
    assert db.query(m.PresenceSample).filter_by(tenant_id=tenant.id, network_id=None).count() == 0
    for row in db.query(m.DnsQuery).all():
        assert row.network_id == net.id
```
- [ ] **Step 2: Run** — `cd hub && pytest -q tests/test_networks.py` → FAIL (`No module named 'guardian_hub.networks'`).

- [ ] **Step 3: Implement** `networks.py`, `routers/networks.py`, the `models.py` additions, the
  `ingest.py` changes, the `?network=` plumbing in `read.py`/`write.py`/`payloads/*`, the contract
  additions in `schemas_dash.py` + `web/src/types.ts`, and the two-network `seed.py`.

- [ ] **Step 4: Migration** — `cd hub && alembic revision -m "networks" --rev-id 0003_networks`
  (`down_revision = "0002_dashboard"`), then hand-write `upgrade()`. SQLite cannot drop an unnamed
  `UNIQUE`, so the two widened tables are recreated with `copy_from` describing the *new* shape without
  the old constraint:
```python
def upgrade() -> None:
    op.create_table(
        "network",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("slug", sa.String(24), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False, server_default="wifi"),
        sa.Column("subnet_prefix", sa.String(15), nullable=False, server_default=""),
        sa.Column("expected_gateway", sa.String(45), nullable=False, server_default=""),
        sa.Column("ssid", sa.String(64), nullable=False, server_default=""),
        sa.Column("sensor_id", sa.String(32), nullable=True),
        sa.Column("colour", sa.String(16), nullable=False, server_default=""),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "slug"),
    )
    op.create_index("ix_network_tenant", "network", ["tenant_id"])
    for table in ("event", "alert", "dns_query"):
        op.add_column(table, sa.Column("network_id", sa.String(32), nullable=True))
        op.create_index(f"ix_{table}_net", table, ["network_id"])

    device_v3 = sa.Table(                      # the NEW shape: no (tenant_id, mac) unique constraint
        "device", sa.MetaData(),
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("mac", sa.String(17), nullable=False),
        sa.Column("ip", sa.String(45), nullable=False, server_default=""),
        sa.Column("name", sa.String(120), nullable=False, server_default=""),
        sa.Column("vendor", sa.String(120), nullable=False, server_default=""),
        sa.Column("kind", sa.String(24), nullable=False, server_default="unknown"),
        sa.Column("label", sa.String(120), nullable=False, server_default=""),
        sa.Column("trusted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("always_on", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("quarantined", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("parent", sa.String(17), nullable=True),
        sa.Column("risk", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("presence", sa.String(12), nullable=False, server_default="NEW"),
        sa.Column("ports", sa.JSON(), nullable=False),
        sa.Column("first_seen", sa.String(32), nullable=False, server_default=""),
        sa.Column("last_seen", sa.String(32), nullable=False, server_default=""),
        sa.Column("extra", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="CASCADE"),
    )
    with op.batch_alter_table("device", copy_from=device_v3, recreate="always") as b:
        b.add_column(sa.Column("network_id", sa.String(32), nullable=True))
        b.create_unique_constraint("uq_device_net_mac", ["tenant_id", "network_id", "mac"])
    op.create_index("ix_device_tenant", "device", ["tenant_id"])

    pres_v3 = sa.Table(                        # same trick for presence_sample (0002 shape, no unique)
        "presence_sample", sa.MetaData(),
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("ts", sa.String(32), nullable=False),
        sa.Column("mac", sa.String(17), nullable=False),
    )
    with op.batch_alter_table("presence_sample", copy_from=pres_v3, recreate="always") as b:
        b.add_column(sa.Column("network_id", sa.String(32), nullable=True))
        b.create_unique_constraint("uq_pres_ts_mac_net", ["tenant_id", "ts", "mac", "network_id"])
    op.create_index("ix_pres_t", "presence_sample", ["tenant_id", "ts"])
    op.create_index("ix_pres_mac", "presence_sample", ["tenant_id", "mac", "ts"])
```
  `downgrade()` mirrors it (drop the indexes and `network_id` columns, recreate both tables with their
  0002 constraints, drop `network`). Prove both directions: `alembic upgrade head && alembic downgrade -1
  && alembic upgrade head`.

- [ ] **Step 5: Run** — `cd hub && pytest -q` → all pass, including the phase-1/phase-2 suites
  (`test_ingest.py`, `test_read_write.py`, `test_assets.py`, `test_inbox.py`, `test_topology.py`,
  `test_dns.py`, `test_seed.py`), then `python scripts/check_types.py` → exit 0 with the four new type
  names present on both sides.

- [ ] **Step 6: Commit** — `hub: multiple networks per tenant (network table, 0003, scoped ingest/reads, two-network demo)`

---

### Task 52: sensor — `config.networks[]`, per-network scan loop, per-network state and alert keys

Sensor-only. Depends on 51. Concurrent with 53.

**Files:**
- Create: `sensor/tests/test_networks.py`
- Modify: `sensor/guardian_sensor/config.py` (`networks` default + normalisation helpers),
  `sensor/guardian_sensor/lan.py` (`local_subnets`, `reachable`), `sensor/guardian_sensor/runner.py`
  (the per-network loop, per-network state, the history line, the push shape),
  `sensor/guardian_sensor/alerts.py` (`lan_key` + the extracted `_lan_alerts`),
  `sensor/guardian_sensor/home.py` (`rogue_checks` per network),
  `sensor/guardian_sensor/dnsstats.py` (`by_network`), `sensor/guardian_sensor/sync.py`
  (`net` routing in `apply_edit`), `sensor/tests/{test_config,test_runner,test_sync,test_collectors}.py`

**Interfaces:**
- `config.DEFAULTS["networks"] = []`. After the deep merge, `config.load()` normalises: when `networks`
  is empty, it is built from the `lan` block —
  `[{"name": cfg["lan"].get("name") or "Home network", "kind": "wifi", "subnet_prefix": …,
  "expected_gateway": …, "enabled": cfg["lan"].get("enabled", True), "interface": None}]` — and, either way,
  `cfg["lan"]` is left pointing at the **primary** (first) network's values, so every existing reader
  (`alerts.py`'s `cfg["lan"]["expected_gateway"]`, `cfg["lan"]["watch_ips"]`, `lan.lan_sweep`) is unchanged
  (invariant 1). Helpers: `config.networks_of(cfg) -> list[dict]` (each entry gains a computed `slug`) and
  `config.slugify(name)` — the same rule as `hub/guardian_hub/networks.slugify`, duplicated deliberately
  because the sensor imports nothing from the hub.
- `lan.local_subnets() -> dict[str, str]` — `{"192.168.1": "en0", "10.10.0": "en8"}` from `ifconfig -a`
  (macOS) / `ip -o -4 addr show` (Linux), i.e. the sensor's *own* addresses; `lan.reachable(net, subs)`
  is `net["subnet_prefix"] in subs` (or `net["interface"] in subs.values()` when the config pins one).
  Reachability is decided from local interface addresses, **not** from `default_gateway()`, so a
  multi-homed sensor scans both networks even though only one holds the default route.
- `runner.run_once` gains `scan_networks(cfg, state, no_lan) -> list[dict]`, which for each enabled entry
  returns either `lan.lan_sweep(net)` enriched with `{"name","slug","kind","subnet_prefix",
  "expected_gateway","primary","status":"active"}`, or `{"name","slug",…,"status":"unreachable",
  "reason":"no local interface on 10.10.0.0/24"}` (never an empty sweep, per spec §3.1). `res["networks"]`
  is that list; `res["lan"]` **is** the primary entry, by reference (invariant 1), so `last.json` keeps its
  existing `lan` block.
- Per-network state lives at `state["lan"]["networks"][slug]` = `{known_macs, last_live_macs,
  last_live_count, miss_count, transitions, gw_mac, home_ssid, twin_channels, presence}`. **Migration:** on
  the first run after this task, `runner._migrate_lan_state(state, primary_slug)` moves the existing
  top-level `state["lan"]` keys into `networks[<primary slug>]` and then re-points
  `state["lan"]["known_macs"]`, `["last_live_macs"]`, `["miss_count"]` and `["transitions"]` at those same
  objects **by reference** — so `state.json` still shows the legacy keys (a hub on pre-0003 code, and Task
  16's ingest device loop, keep working) with zero divergence risk. The migration is idempotent and
  recorded as `state["lan"]["schema"] = 2`.
- The port scan / presence / flapping / risk block and the join/leave event block run once per network with
  that network's views; every emitted event line gains `"net": slug`. `state["gw_mac"]` and
  `state["twin_channels"]` keep their top-level values for the primary network and gain per-network copies.
- `alerts.lan_key(kind, net, mac=None, port=None) -> str` — invariant 2 — and
  `alerts._lan_alerts(res, cfg, state, net, add)`, extracted verbatim from the LAN/home parts of
  `build_alerts` (`alerts.py:107–128`, `:176–184`) with every literal key replaced by a `lan_key` call and
  every `state["lan"]` read replaced by that network's slice. `build_alerts` calls it once per
  `res["networks"]` entry and stamps each returned alert with `"net": slug`. `unusual_hours` gains the same
  per-network loop (`alerts.py:329–339`).
- `home.rogue_checks(net, wifi, on_net, netstate)` — the Wi-Fi guard per network: `expected_gateway` comes
  from `net`, `home_ssid`/`twin_channels` from `netstate`. A back-compat wrapper
  `rogue_checks_legacy(cfg, wifi, on_home_lan, state)` keeps the old signature working for the primary
  network so `test_collectors.py` is unchanged. `res["rogue"]` stays the primary network's dict;
  `res["rogues"] = {slug: dict}` carries all of them.
- `dnsstats.dns_analyse(cfg, state)` gains `by_network: {slug: {queries_24h, blocked_24h, top, clients}}`,
  attributing each logged query by client IP → `subnet_prefix` (longest match, unmatched → primary), and
  each pushed `dns_queries` line gains `"net": slug`.
- `sync.HubClient.push` body gains `networks` (the `res["networks"]` list, minus the bulky `devices`/`names`
  maps for an `unreachable` entry). `sync.apply_edit` routes by `b.get("net")`: `_known(st, net)` returns
  `st["lan"]["networks"][net]["known_macs"]` when `net` is given and known, else the primary's map — so a
  pre-addendum edit body with no `net` still replays exactly as before. `/api/reset-lan-baseline` with a
  `net` resets only that network's `known_macs`.

- [ ] **Step 1: Write the failing tests** (`sensor/tests/test_networks.py`)
```python
import json
from guardian_sensor import alerts, config, lan, runner, sync


def test_old_lan_block_becomes_one_primary_network(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"lan": {"subnet_prefix": "192.168.1", "expected_gateway": "192.168.1.1"}}))
    cfg = config.load(str(p))
    nets = config.networks_of(cfg)
    assert len(nets) == 1 and nets[0]["slug"] == "home-network" and nets[0]["primary"] is True
    assert cfg["lan"]["subnet_prefix"] == "192.168.1"      # invariant 1: legacy readers untouched


def test_two_networks_keep_order_and_slugs(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"networks": [
        {"name": "Office Wi-Fi", "kind": "wifi", "subnet_prefix": "192.168.1", "expected_gateway": "192.168.1.1"},
        {"name": "Lab", "kind": "lab", "subnet_prefix": "10.10.0", "expected_gateway": "10.10.0.1"}]}))
    cfg = config.load(str(p))
    nets = config.networks_of(cfg)
    assert [n["slug"] for n in nets] == ["office-wi-fi", "lab"]
    assert cfg["lan"]["expected_gateway"] == "192.168.1.1"


def test_unreachable_network_reports_a_status_not_an_empty_scan(monkeypatch, tmp_path):
    monkeypatch.setattr(lan, "local_subnets", lambda: {"192.168.1": "en0"})
    monkeypatch.setattr(lan, "lan_sweep", lambda net: {"live_count": 2, "devices": {}, "names": {},
                                                      "gw_mac": None, "watch_ips": {}})
    cfg = {"lan": {"enabled": True, "subnet_prefix": "192.168.1", "expected_gateway": "192.168.1.1",
                   "watch_ips": {}},
           "networks": [{"name": "Office Wi-Fi", "kind": "wifi", "subnet_prefix": "192.168.1",
                         "expected_gateway": "192.168.1.1", "enabled": True},
                        {"name": "Lab", "kind": "lab", "subnet_prefix": "10.10.0",
                         "expected_gateway": "10.10.0.1", "enabled": True}]}
    nets = runner.scan_networks(cfg, {}, no_lan=False)
    assert [n["status"] for n in nets] == ["active", "unreachable"]
    assert nets[1]["reason"] and "devices" not in nets[1]


def test_lan_key_is_legacy_for_primary_and_namespaced_after_it():
    prim = {"slug": "office-wi-fi", "primary": True}
    lab = {"slug": "lab", "primary": False}
    assert alerts.lan_key("new", prim, mac="aa:bb") == "lan:new:aa:bb"
    assert alerts.lan_key("new", lab, mac="aa:bb") == "lan:new:lab:aa:bb"
    assert alerts.lan_key("port", lab, mac="aa:bb", port=23) == "lan:port:lab:aa:bb:23"
    assert alerts.lan_key("unverified", prim) == "lan:unverified"
    assert alerts.lan_key("unverified", lab) == "lan:unverified:lab"


def test_state_migration_keeps_the_legacy_keys_pointing_at_the_primary():
    state = {"lan": {"known_macs": {"aa:bb": {"ip": "192.168.1.9"}}, "last_live_macs": ["aa:bb"],
                     "miss_count": {}, "transitions": {}}}
    runner._migrate_lan_state(state, "office-wi-fi")
    nets = state["lan"]["networks"]
    assert nets["office-wi-fi"]["known_macs"] is state["lan"]["known_macs"]     # same object
    nets["office-wi-fi"]["known_macs"]["cc:dd"] = {"ip": "192.168.1.10"}
    assert "cc:dd" in state["lan"]["known_macs"]
    assert state["lan"]["schema"] == 2
    runner._migrate_lan_state(state, "office-wi-fi")                            # idempotent
    assert len(state["lan"]["networks"]) == 1


def test_edits_route_to_the_named_network_and_default_to_primary():
    state = {"lan": {"networks": {"office-wi-fi": {"known_macs": {"aa:bb": {}}},
                                  "lab": {"known_macs": {"aa:bb": {}}}},
                     "primary": "office-wi-fi"}}
    state["lan"]["known_macs"] = state["lan"]["networks"]["office-wi-fi"]["known_macs"]
    assert sync.apply_edit({"path": "/api/device", "body": {"mac": "aa:bb", "label": "Lab PC",
                                                            "net": "lab"}}, state, {}) == "state"
    assert state["lan"]["networks"]["lab"]["known_macs"]["aa:bb"]["label"] == "Lab PC"
    assert "label" not in state["lan"]["networks"]["office-wi-fi"]["known_macs"]["aa:bb"]
    sync.apply_edit({"path": "/api/device", "body": {"mac": "aa:bb", "label": "Desk"}}, state, {})
    assert state["lan"]["known_macs"]["aa:bb"]["label"] == "Desk"               # no net = primary


def test_push_body_carries_networks(stubbed_client):
    body = stubbed_client.last_push
    assert [n["slug"] for n in body["networks"]] == ["office-wi-fi", "lab"]
```
(`stubbed_client` extends the existing `sensor/tests/test_runner.py` stub with a two-network config and
records the last push body.)

- [ ] **Step 2: Run** — `cd sensor && python3.10 -m pytest -q tests/test_networks.py` → FAIL.
- [ ] **Step 3: Implement** the config normalisation, `lan.local_subnets`/`reachable`, the runner loop and
  state migration, the `alerts.lan_key`/`_lan_alerts` extraction, the per-network `rogue_checks`,
  `dnsstats.by_network`, and the `sync` push/edit changes. Stdlib only, Python 3.10 syntax.
- [ ] **Step 4: Run** — `cd sensor && python3.10 -m pytest -q` → all pass, and specifically
  `tests/test_parity_alerts.py` (the single-network fixture must still reproduce Nexus's keys, score and
  recommendations byte-for-byte — the primary network's keys are unnamespaced by invariant 2) and
  `tests/test_runner.py` / `tests/test_sync.py` unchanged in behaviour. Then `cd hub && pytest -q` to
  confirm the hub's ingest tests still accept the new push shape.
- [ ] **Step 5: Commit** — `sensor: config.networks[], per-network sweep/state/alerts/DNS, namespaced LAN alert keys`

---

### Task 53: web — networks in Assets, Topology, Security, Settings, Overview and onboarding

Web-only. Depends on 51 (contract) and 22, 23, 26, 32, 35, 46, 49 (the components it extends).
Concurrent with 52.

**Files:**
- Create: `web/src/pages/Settings/Networks.tsx`, `web/src/components/{NetworkChips.tsx,NetworkBadge.tsx}`,
  `web/tests/networks.test.tsx`
- Modify: `web/src/pages/{Overview.tsx,Assets.tsx,Topology.tsx,Security.tsx,Onboarding.tsx}`,
  `web/src/components/{ForceGraph.tsx,DeviceDrawer.tsx,WifiGuard.tsx}`, `web/src/pages/Settings/index.tsx`
  (sub-nav entry), `web/src/lib/keys.ts` (a `network` query key + the persisted selection),
  `web/e2e/smoke.spec.ts` (the two-network cases; **run in Task 56's verification, not here** — the
  plan's rule that Playwright runs only in closeout tasks still holds, and 50 has already closed by then)

**Interfaces:**
- `NetworkChips({networks, value, onChange})` — an "All networks" chip plus one chip per
  `NetworkHealth`, each showing the network name, its online/total count and a state dot; the selection is
  Zustand UI state persisted to `localStorage` and appended as `?network=` to every scoped query. It sits
  in the Assets sub-header, the Topology toolbar and the Security header.
- `NetworkBadge({network})` — the small inline chip drawn on a device card, an inbox row, a DNS client
  badge, an attacker row and a topology hover card. Renders nothing when `network` is `null`.
- Assets → Devices: one section per network (name, chip counts, "needs a sensor on this network" pill when
  `status !== "active"`), and inside it the existing kind-chip grid from Task 26. The kind chips filter
  within the selected network. The device drawer's header shows the network, and its actions send
  `?network=<id>`.
- Topology: one cluster per network (`TopoCluster.network_id`), each with its own router/gateway node
  (`router:<slug>`); `internet` stays a single node. A node that appears in two networks with the same MAC
  (a multi-homed sensor, or a router bridging two networks) is drawn **once** — de-duplicated by MAC — with
  one link per network, and its hover card lists both. The cluster force pulls each network's members to
  its own centroid, so the two networks read as two islands joined at the internet node.
- Security → Wi-Fi guard: one `WifiGuard` panel per entry in `SecurityPayload.wifis`, each headed by its
  network name; the scorecard list gains a network badge on LAN/home targets.
- Settings → **Networks** (new sub-nav entry, admin-only writes): a table of networks (name, kind, subnet,
  gateway, sensor, devices, status) with add / edit / delete rows, a sensor picker per network, a
  "copy enrol command" button (reusing Task 49's OS picker and `POST /api/v1/sensors/enrol-code`) and a
  **"needs a sensor on this network"** badge whenever `status` is `no-sensor` or `unreachable`. Delete asks
  which network to move the devices to (the `?reassign=` parameter) and refuses the primary network with an
  inline explanation.
- Onboarding: a new step 2 — *"Which networks should Guardian watch?"* — pre-filled with the detected
  subnet, an "Add another network" row, and a note that each network needs a sensor that can reach it. The
  wizard writes them with `POST /api/v1/networks` before the install step, so the enrol command it then
  shows is already bound to the right network.
- Overview: the per-network health strip under the hero, one pill per `OverviewPayload.networks` entry,
  clicking through to Assets with that network selected.

- [ ] **Step 1: Write the failing tests** (`web/tests/networks.test.tsx`)
```tsx
it("groups devices by network and scopes the query to the chosen chip", async () => {
  const { posted, queried } = renderPage(<Assets />, { devices: twoNetworkDevicesFixture });
  expect(await screen.findByRole("heading", { name: /Office Wi-Fi/ })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: /^Lab$/ })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /^Lab/ }));
  expect(queried()).toContain("/api/v1/devices?network=net_lab");
  expect(posted).toBeDefined();
});

it("shows the needs-a-sensor badge for an unreachable network", async () => {
  renderPage(<Assets />, { devices: { ...twoNetworkDevicesFixture,
    networks: [{ ...twoNetworkDevicesFixture.networks[1], status: "unreachable" }] } });
  expect(await screen.findByText(/needs a sensor on this network/i)).toBeInTheDocument();
});

it("draws one cluster and one gateway per network, and a multi-homed node once", async () => {
  renderPage(<Topology />, { topology: twoNetworkTopologyFixture });
  expect(await screen.findAllByTestId("topo-cluster")).toHaveLength(2);
  expect(screen.getAllByTestId("topo-node").filter(n => n.dataset.kind === "router")).toHaveLength(2);
  const sensor = screen.getAllByTestId("topo-node").filter(n => n.dataset.mac === "3c:22:fb:aa:bb:cc");
  expect(sensor).toHaveLength(1);
  expect(screen.getAllByTestId("topo-link").filter(l => l.dataset.target === "3c:22:fb:aa:bb:cc")).toHaveLength(2);
});

it("shows one Wi-Fi guard panel per network", async () => {
  renderPage(<Security />, { security: twoNetworkSecurityFixture });
  expect(await screen.findAllByTestId("wifi-guard")).toHaveLength(2);
});

it("adds a network, refuses to delete the primary, and asks where to move devices", async () => {
  const { posted } = renderPage(<Networks />, { networks: twoNetworksFixture });
  await userEvent.click(await screen.findByRole("button", { name: /add network/i }));
  await userEvent.type(screen.getByLabelText(/name/i), "Guest Wi-Fi");
  await userEvent.type(screen.getByLabelText(/subnet/i), "192.168.5");
  await userEvent.type(screen.getByLabelText(/gateway/i), "192.168.5.1");
  await userEvent.click(screen.getByRole("button", { name: /^save$/i }));
  expect(posted("/api/v1/networks")).toEqual({ name: "Guest Wi-Fi", kind: "guest",
    subnet_prefix: "192.168.5", expected_gateway: "192.168.5.1", ssid: "", colour: "" });
  expect(screen.getByTestId("delete-office-wi-fi")).toBeDisabled();
  await userEvent.click(screen.getByTestId("delete-lab"));
  expect(await screen.findByLabelText(/move its devices to/i)).toBeInTheDocument();
});

it("asks which networks to watch during onboarding, before the install step", async () => {
  const { posted } = renderPage(<Onboarding />, { me: freshMe, overview: emptyOverview,
                                                  enrol: { code: "ABCD2345", expires: "…" } });
  await userEvent.type(await screen.findByLabelText(/what should we call/i), "Home{Enter}");
  expect(await screen.findByText(/which networks should Guardian watch/i)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /next/i }));
  expect(posted("/api/v1/networks")).toBeTruthy();
  expect(screen.getByRole("button", { name: /macOS/i })).toBeInTheDocument();
});
```
- [ ] **Step 2–4:** run → FAIL → implement → `npm test && npx tsc -b && npm run lint` clean. Manual: both
  skins, 375 px, and confirm the topology still starts exactly one D3 simulation under StrictMode with two
  clusters mounted.
- [ ] **Step 5:** append the two-network cases to `web/e2e/smoke.spec.ts` (Assets shows both network
  headings; the Lab chip filters the grid; Topology shows two clusters; Settings → Networks adds and
  deletes a network) **tagged `@networks` and left unrun here** — Task 56 runs the suite as part of the
  cutover verification.
- [ ] **Step 6: Commit** — `web: networks everywhere (Assets grouping, Topology clusters, per-network Wi-Fi guard, Settings → Networks, onboarding step)`

---

### Task 54: reports split per network

Hub-only. Depends on 37 and 51.

**Files:**
- Modify: `hub/guardian_hub/reports/{sections.py,builder.py,compliance.py}`,
  `hub/tests/test_report_builder.py`

**Interfaces:**
- `builder.networks_of(db, tenant) -> list[m.Network]` — ordered primary-first, then by name; the single
  accessor every section uses (mirroring `builder.last_of`).
- `sections.devices(...)`, `sections.presence(...)` and every `dev_*` group section take a `network`
  argument and render **one block per network**, each headed `"<Network name> — <section title>"`, with the
  network's subnet in the sub-caption. `SECTION_KEYS` and `SECTION_TITLES` are unchanged (19 keys), so
  presets, the section checkboxes and Task 38's `len(sections) == 19` assertion all still hold; a section
  simply emits N blocks instead of one. A network with no devices renders a one-line "no devices seen on
  this network in this period" block rather than being dropped, so the report never silently hides a
  network.
- `dev_*` grouping is *within* each network: `dev_cameras` for the Lab lists only Lab cameras.
- `sections.summary(...)`: the executive summary KPI row gains a **Networks watched** card
  (`len(networks_of(...))`), and the verdict sentence names any network whose `status != "active"`
  ("Guardian could not reach the Lab network during this period").
- `builder.history_lines` is unchanged (`Metric` rows are tenant-scoped); `PresenceSample` rows are grouped
  by `network_id` when the presence section splits, using the per-network semantics decided in Task 51.
- `compliance.rows(last)` is unchanged — the twelve controls are tenant-level, not per network.

- [ ] **Step 1: Write the failing tests** — appended to `hub/tests/test_report_builder.py`
```python
def test_device_and_presence_sections_split_per_network(client):
    seeded_demo(client)
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    html = builder.build_report(db, t, days=30, preset="full")
    assert html.count("Device inventory") >= 2
    assert "Office Wi-Fi — Device inventory" in html and "Lab — Device inventory" in html
    assert "Office Wi-Fi — Screen time" in html and "Lab — Screen time" in html
    assert "Networks watched" in html and ">2<" in html
    # a Lab-only device appears under the Lab block, never under the Wi-Fi one
    lab_block = html.split("Lab — Device inventory", 1)[1].split("</section>", 1)[0]
    assert "10.10.0." in lab_block and "192.168.1." not in lab_block

def test_a_network_with_no_devices_still_gets_a_block(client):
    seeded_demo(client)
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    db.add(m.Network(tenant_id=t.id, name="Guest Wi-Fi", slug="guest-wi-fi", kind="guest",
                     subnet_prefix="192.168.5", expected_gateway="192.168.5.1", status="no-sensor"))
    db.commit()
    html = builder.build_report(db, t, days=7, preset="network")
    assert "Guest Wi-Fi — Device inventory" in html
    assert "no devices seen on this network" in html
    assert "could not reach" in html.lower()

def test_section_keys_and_presets_are_unchanged_by_the_split():
    assert len(sections.SECTION_KEYS) == 19 and presets.PRESETS["full"][1] == sections.SECTION_KEYS
```
- [ ] **Step 2–4:** run → FAIL → implement → pass, and re-run Task 38's `test_reports_api.py` unchanged.
  Open the produced HTML and print-preview it: each network's blocks must not straddle a page break mid-table.
- [ ] **Step 5: Commit** — `hub: reports split device inventory, presence and device groups per network`

---

### Task 55: retention job, `guardian prune`, and the DNS daily rollup

Hub-only. Depends on 16 (anytime after). Independent of 51–54; when 51 has landed, the rollup rows carry
`network_id` too.

**Files:**
- Create: `hub/guardian_hub/retention.py`, `hub/alembic/versions/0004_dns_rollup.py`,
  `hub/tests/test_retention.py`
- Modify: `hub/guardian_hub/models.py` (`DnsRollup`), `hub/guardian_hub/scheduler.py` (register the job),
  `hub/guardian_hub/cli.py` (`prune` subcommand), `hub/guardian_hub/payloads/dnsview.py` (read rollups for
  windows longer than the raw retention), `hub/guardian_hub/selftest.py` (one check)

**Interfaces:**
- Policy, read per tenant from `Tenant.settings` with these defaults — **the only knobs**:
  `retention_days` 90 (applies to `metric`, `event`, `presence_sample` and `report` files/rows),
  `dns_raw_days` 7 (raw `dns_query` rows; older ones are rolled up first, then deleted),
  `dns_rollup_days` 365, `audit_days` 365.
- `DnsRollup` model: `id` (int pk), `tenant_id`, `day` (`String(10)`, `YYYY-MM-DD`), `network_id`
  (nullable), `mac` (nullable), `name` (`String(255)`), `queries` (int), `blocked` (int),
  `UniqueConstraint("tenant_id", "day", "network_id", "mac", "name")`, `Index("ix_dnsroll", "tenant_id", "day")`.
- `retention.rollup_dns(db, tenant, before_day: str) -> int` — groups the raw rows older than `before_day`
  by `(day, network_id, mac, name)`, keeps the **top 200 domains per day per network** by query count plus
  one `name="(other)"` bucket carrying the remainder, upserts `DnsRollup`, returns the number of rollup rows
  written. Idempotent: re-running for the same day replaces that day's rows.
- `retention.prune_tenant(db, tenant, now=None, dry_run=False) -> dict` — returns
  `{"metric": n, "event": n, "dns_query": n, "dns_rollup": n, "presence_sample": n, "audit": n,
  "report": n, "rolled_up": n}`. Order matters: roll up DNS **before** deleting raw rows; delete a
  `Report` row's HTML file before its row (missing file = not an error); never touch `Scan`, `Device`,
  `Alert`, `Incident`, `Recommendation`, `Fix`, `Network` or anything a human curated. Deletes in batches of
  5 000 with a commit per batch so a large first run cannot hold one long SQLite write lock.
- `retention.prune_all(db, now=None, dry_run=False) -> dict[str, dict]` — per tenant slug.
- `scheduler.retention_job()` — registered as `CronTrigger(hour=3, minute=30)` in hub-local time, using the
  existing `scheduler._now` seam (the same one Task 38's report job is tested through) so the tests can
  freeze time without a new dependency. Logs one line per tenant with the counts; never raises into the
  scheduler.
- CLI: `guardian prune [--slug SLUG] [--dry-run]` — prints the same dict as a table and exits non-zero only
  on an error, so it is safe in cron.
- `dnsview.build(db, tenant, hours=24)` gains: when `hours > dns_raw_days * 24`, `top`/`blocked_top`/
  `clients` are read from `DnsRollup` for the days outside the raw window and merged with the raw rows
  inside it (counts summed, `(other)` excluded from the top list but added to the totals) — so the rollup
  table has a real reader and a 30-day DNS view keeps working after a prune.
- `selftest.run` gains `_check_retention()`: the newest `metric` row older than
  `retention_days + 1` days must not exist for any tenant (i.e. the job has run at least once), `_skip`
  when the hub has been up for less than a day.

- [ ] **Step 1: Write the failing tests** (`hub/tests/test_retention.py`)
```python
import datetime as dt
from tests.test_seed_helpers import seeded_demo
from guardian_hub.db import SessionLocal
from guardian_hub import models as m, retention, scheduler

FROZEN = dt.datetime(2026, 9, 9, 3, 30, tzinfo=dt.timezone.utc)


def _old(db, tenant_id, days, n=3):
    ts = (FROZEN - dt.timedelta(days=days)).isoformat(timespec="seconds")
    for i in range(n):
        db.add(m.Metric(tenant_id=tenant_id, ts=ts, name="score", entity="", value=80))
        db.add(m.Event(tenant_id=tenant_id, ts=ts, type="join", entity=f"aa:bb:{i:02d}", payload={}))
        db.add(m.PresenceSample(tenant_id=tenant_id, ts=ts, mac=f"aa:bb:{i:02d}"))
        db.add(m.DnsQuery(tenant_id=tenant_id, ts=ts, client="192.168.1.9", name=f"d{i}.example",
                          qtype="A", blocked=bool(i % 2), list="stevenblack", ms=1))
        db.add(m.Audit(tenant_id=tenant_id, ts=FROZEN - dt.timedelta(days=days), user="x",
                       action="device.update", target="aa:bb"))
    db.commit()
    return ts


def test_prune_respects_every_window_and_rolls_dns_up_first(client, monkeypatch):
    tid = seeded_demo(client)
    monkeypatch.setattr(retention, "_now", lambda: FROZEN)
    db = SessionLocal()
    _old(db, tid, days=200)          # outside retention_days and audit_days
    _old(db, tid, days=30)           # inside retention_days, outside dns_raw_days
    counts = retention.prune_tenant(db, db.get(m.Tenant, tid))
    assert counts["metric"] == 3 and counts["event"] == 3 and counts["presence_sample"] == 3
    assert counts["audit"] == 0                      # 200 d < audit_days 365
    assert counts["dns_query"] == 6 and counts["rolled_up"] > 0
    rolled = db.query(m.DnsRollup).filter_by(tenant_id=tid).all()
    assert rolled and sum(r.queries for r in rolled) == 6
    assert {r.day for r in rolled} == {(FROZEN - dt.timedelta(days=d)).date().isoformat() for d in (200, 30)}
    # rows inside every window survive
    assert db.query(m.Metric).filter_by(tenant_id=tid).count() > 1000


def test_dry_run_changes_nothing(client, monkeypatch):
    tid = seeded_demo(client)
    monkeypatch.setattr(retention, "_now", lambda: FROZEN)
    db = SessionLocal(); _old(db, tid, days=200)
    before = db.query(m.Metric).filter_by(tenant_id=tid).count()
    counts = retention.prune_tenant(db, db.get(m.Tenant, tid), dry_run=True)
    assert counts["metric"] == 3
    assert db.query(m.Metric).filter_by(tenant_id=tid).count() == before
    assert db.query(m.DnsRollup).count() == 0


def test_reports_older_than_retention_lose_row_and_file(client, monkeypatch):
    tid = seeded_demo(client)
    monkeypatch.setattr(retention, "_now", lambda: FROZEN)
    db = SessionLocal()
    card = client.post("/api/v1/reports/build", json={"days": 7, "preset": "executive",
                                                      "sections": [], "title": "Old"}).json()
    row = db.get(m.Report, card["id"]); row.created = FROZEN - dt.timedelta(days=200); db.commit()
    import pathlib; path = pathlib.Path(row.path); assert path.exists()
    assert retention.prune_tenant(db, db.get(m.Tenant, tid))["report"] == 1
    assert db.get(m.Report, card["id"]) is None and not path.exists()


def test_rollup_is_idempotent_and_capped(client, monkeypatch):
    tid = seeded_demo(client)
    monkeypatch.setattr(retention, "_now", lambda: FROZEN)
    db = SessionLocal(); ts = _old(db, tid, days=30, n=250)
    day = ts[:10]
    first = retention.rollup_dns(db, db.get(m.Tenant, tid), before_day=FROZEN.date().isoformat())
    second = retention.rollup_dns(db, db.get(m.Tenant, tid), before_day=FROZEN.date().isoformat())
    assert first == second
    rows = db.query(m.DnsRollup).filter_by(tenant_id=tid, day=day).all()
    assert len(rows) <= 201 and any(r.name == "(other)" for r in rows)


def test_a_thirty_day_dns_view_reads_the_rollup(client, monkeypatch):
    tid = seeded_demo(client)
    monkeypatch.setattr(retention, "_now", lambda: FROZEN)
    db = SessionLocal(); _old(db, tid, days=20)
    retention.prune_tenant(db, db.get(m.Tenant, tid))
    body = client.get("/api/v1/dns?hours=720").json()
    assert body["status"]["queries_24h"] >= 0
    assert any(t["name"].endswith(".example") for t in body["top"]), "rollup rows must feed the top list"


def test_the_job_runs_daily_and_is_safe_to_repeat(client, monkeypatch):
    tid = seeded_demo(client)
    monkeypatch.setattr(scheduler, "_now", lambda: FROZEN)
    monkeypatch.setattr(retention, "_now", lambda: FROZEN)
    db = SessionLocal(); _old(db, tid, days=200)
    scheduler.retention_job(); scheduler.retention_job()
    assert db.query(m.Metric).filter(m.Metric.ts < "2026-06-01").count() == 0
    job = next(j for j in scheduler.jobs() if j.id == "retention")
    assert (job.trigger.fields_map["hour"].expressions[0].first == 3
            and job.trigger.fields_map["minute"].expressions[0].first == 30)
```
- [ ] **Step 2: Run** → FAIL (`No module named 'guardian_hub.retention'`).
- [ ] **Step 3: Implement** `retention.py`, the `DnsRollup` model, the scheduler registration, the CLI
  subcommand, the `dnsview` rollup reader and the selftest check.
- [ ] **Step 4: Migration** — `alembic revision -m "dns rollup" --rev-id 0004_dns_rollup`
  (`down_revision = "0003_networks"`); `op.create_table("dns_rollup", …)` with the unique constraint and
  index above; `downgrade()` drops it. Prove both directions.
- [ ] **Step 5: Run** — `cd hub && pytest -q` → all pass. Then, against the demo tenant,
  `guardian prune --slug demo --dry-run` and confirm the printed counts match the test expectations.
- [ ] **Step 6: Commit** — `hub: retention job (daily 03:30), DNS daily rollup, guardian prune CLI`

---

### Task 56: phase-5 cutover — parity soak, `safehome.example.com` switch, retire Nexus

Serial. **Last task in the plan.** Depends on 50–55. This is a checklist, not code: every step is a real
command run on the Mac (sensor) or the safehome VPS (`root@203.0.113.10`), and each has a verification
line. **Nothing here is destructive to Nexus until step 7**, and step 9 is the rollback.

- [ ] **Step 1: Parity soak — 24 h, tenant `home`.** Guardian and Nexus both run; compare hourly:
```bash
# on the Mac, hourly for 24 h (launchd or `while :; do … sleep 3600; done` in a tmux pane)
mkdir -p ~/Desktop/guardian-soak && cd ~/Desktop/guardian-soak
curl -s -b ~/.guardian/soak.cookie https://safehome.example.com:8443/api/v1/overview  > g.$(date +%H).json
curl -s http://127.0.0.1:8787/api/overview                                          > n.$(date +%H).json
python3 - <<'PY'
import glob, json
for g, n in zip(sorted(glob.glob("g.*.json")), sorted(glob.glob("n.*.json"))):
    G, N = json.load(open(g)), json.load(open(n))
    ga = {a for a in G.get("alert_keys", [])} or {r["id"] for r in G.get("timeline", []) if r["kind"] == "alert"}
    print(g, "score", G["score"], N["score"], "delta", abs((G["score"] or 0) - (N["score"] or 0)),
          "devices", G["devices"]["total"], N["devices"]["total"])
PY
```
  **Pass criteria (spec §12), all three for 24 consecutive hours:** identical open-alert key sets (ignoring
  the `lan:*` namespacing of any non-primary network, which Nexus cannot produce); identical device count
  and identical trusted/label state; `|score_guardian − score_nexus| ≤ 2`. Record the worst hour in the task
  report. **Do not continue if any hour fails** — investigate and restart the 24 h clock.
- [ ] **Step 2: Green gates.** `cd hub && pytest -q`; `cd sensor && python3.10 -m pytest -q`;
  `cd web && npm test && npx tsc -b && npm run lint && npx playwright test` (this is where Task 53's
  `@networks` e2e cases run). On the VPS: `/opt/guardian/venv/bin/guardian selftest` → all ok.
- [ ] **Step 3: Point the hub at its final URL.**
```bash
ssh root@203.0.113.10 "sed -i 's#^GUARDIAN_BASE_URL=.*#GUARDIAN_BASE_URL=https://safehome.example.com#' /opt/guardian/hub/.env && grep GUARDIAN_BASE_URL /opt/guardian/hub/.env"
ssh root@203.0.113.10 "systemctl restart guardian-hub && sleep 2 && curl -fsS http://127.0.0.1:8800/api/v1/health"
```
  Verify: share links and `/act` buttons minted after this point contain `https://safehome.example.com/…`
  with no `:8443`.
- [ ] **Step 4: Switch nginx `:443` to Guardian, keep `:8443` for one week.** Edit
  `deploy/nginx-guardian.conf` so it holds **two** server blocks — the existing `listen 8443 ssl` block
  unchanged, plus a `listen 443 ssl` / `listen [::]:443 ssl` block with the same `proxy_pass
  http://127.0.0.1:8800` — and move the Nexus `:443` block out of the way:
```bash
ssh root@203.0.113.10 'cp /etc/nginx/sites-available/nexus.conf /root/nexus.conf.pre-guardian.bak \
  && rm -f /etc/nginx/sites-enabled/nexus.conf'
deploy/deploy.sh                     # re-installs nginx-guardian.conf and reloads nginx
ssh root@203.0.113.10 'nginx -T | grep -A2 "listen 443" | head -20'
curl -sI https://safehome.example.com/ | head -3
curl -s  https://safehome.example.com/api/v1/health
curl -sI https://safehome.example.com:8443/ | head -3   # still Guardian, kept one week as the fallback path
```
  Diary note in the task report: **remove the `:8443` block and close the ufw port one week after cutover**
  (`ufw delete allow 8443/tcp`).
- [ ] **Step 5: Restore the sensor's live behaviour.** Guardian's sensor has run with auto-fix, DNS and
  ntfy off while Nexus owned them.
```bash
python3 - <<'PY'
import json, os
p = os.path.expanduser("~/.guardian/config.json"); c = json.load(open(p))
c.setdefault("auto_fix", {})["enabled"] = True
c.setdefault("dns", {})["enabled"] = True
json.dump(c, open(p, "w"), indent=2)
print(json.dumps({k: c[k] for k in ("auto_fix", "dns")}, indent=2))
PY
grep -q '^NTFY_TOPIC=' ~/.guardian/.env || printf 'NTFY_TOPIC=%s\n' "$(grep '^NTFY_TOPIC=' ~/.claude/nexus-agent/.env | cut -d= -f2-)" >> ~/.guardian/.env
chmod 600 ~/.guardian/.env && ls -l ~/.guardian/.env      # must be -rw-------
```
  Verify: one `guardian-sensor run --once` shows `auto_fix` in the run output and a test ntfy arrives
  (`guardian-sensor notify-test` / the hub's Integrations → Test button). The topic value is **only** ever
  in `~/.guardian/.env` — never in this plan, a commit or a screenshot.
- [ ] **Step 6: Hand port 53 to `guardian-dns`.** One resolver at a time, so the house never loses DNS for
  more than a few seconds:
```bash
launchctl unload ~/Library/LaunchAgents/com.myron.nexus-dns.plist
sudo lsof -nP -iUDP:53 -sUDP:* || echo "53 free"
guardian-sensor install --launchd            # writes/loads com.guardian.dns now that dns.enabled is true
launchctl list | grep com.guardian
dig +short @127.0.0.1 example.com            # resolves
dig +short @127.0.0.1 doubleclick.net        # blocked -> 0.0.0.0 (or NXDOMAIN, per the filter's mode)
dig +short @192.168.1.1 example.com          # the router still answers, so LAN names keep working
```
  Verify in the browser: DNS tab shows a live stream row within 15 s and `wizard.working === true`.
- [ ] **Step 7: Unload the remaining Nexus agents and stop its hub.**
```bash
launchctl unload ~/Library/LaunchAgents/com.myron.nexus-collector.plist
launchctl unload ~/Library/LaunchAgents/com.myron.nexus-ui.plist
launchctl list | grep -i nexus || echo "no nexus agents loaded"
ssh root@203.0.113.10 'systemctl disable --now nexus-hub; systemctl is-active nexus-hub || true'
```
  Leave the plist files in place for the week-long fallback window; only the load is removed.
- [ ] **Step 8: Archive Nexus and update the memory + agent definition.**
```bash
tar -czf ~/Desktop/nexus-agent-archive-$(date +%Y%m%d).tar.gz \
    --exclude '__pycache__' -C ~/.claude nexus-agent
ls -lh ~/Desktop/nexus-agent-archive-*.tar.gz
tar -tzf ~/Desktop/nexus-agent-archive-*.tar.gz | wc -l     # sanity: thousands of entries, not 0
```
  Then: rewrite `~/.claude/agents/nexus-agent.md` to point at Guardian (`safehome.example.com`, the hub API,
  `guardian` CLI) or delete it in favour of a `guardian-agent` definition; update the
  `project_nexus_agent` / `project_guardian_app` memory notes to say Guardian is live on
  `safehome.example.com`, Nexus is archived to that tarball, and what is still pending (SMTP creds, Groq key,
  the `:8443` removal date, any integration Miro has not configured). **Do not delete
  `~/.claude/nexus-agent` in this task** — the archive plus one week of the `:8443` fallback is the safety
  net; deleting the directory is a separate, later decision for Miro.
- [ ] **Step 9: Rollback (only if a gate fails).** In this order, each step independently sufficient:
```bash
ssh root@203.0.113.10 'cp /root/nexus.conf.pre-guardian.bak /etc/nginx/sites-available/nexus.conf \
  && ln -sf /etc/nginx/sites-available/nexus.conf /etc/nginx/sites-enabled/nexus.conf \
  && rm -f /etc/nginx/sites-enabled/guardian.conf && nginx -t && systemctl reload nginx'
ssh root@203.0.113.10 'systemctl enable --now nexus-hub'
launchctl load ~/Library/LaunchAgents/com.myron.nexus-collector.plist
launchctl load ~/Library/LaunchAgents/com.myron.nexus-ui.plist
launchctl unload ~/Library/LaunchAgents/com.guardian.dns.plist \
  && launchctl load ~/Library/LaunchAgents/com.myron.nexus-dns.plist
curl -sI https://safehome.example.com/ | head -3      # Nexus again
```
  Guardian stays reachable on `:8443` throughout, so a rollback costs no Guardian data: its hub, DB and
  sensor state are untouched by any step above.
- [ ] **Step 10: Browser verification of the live cutover** — sign in at `https://safehome.example.com` (real
  OTP, not the demo code), walk all nine tabs plus Settings → Networks in both skins and at 375 px,
  confirm the per-network health strip shows the real home network, `guardian selftest` green, and
  `curl -s https://safehome.example.com/metrics | head` shows `guardian_` metrics. Screenshot per tab into the
  task report.
- [ ] **Step 11: Commit** — `deploy+docs: phase-5 cutover (safehome.example.com → Guardian, Nexus retired)`
  and push to `aicode.example.com` GitLab (`ai/guardian`).

### Addendum self-review — placeholder scan and name/route consistency (Tasks 51–56)

Run after appending the addendum, against the file as it now stands.

**Placeholder scan** — `grep -cEi "TBD|to be determined|similar to task|as above|same as task|add validation|etc\.|FIXME|XXX|lorem"` over this file returns **2**, both pre-existing and both meta:

```
48:   - No placeholders in shipped code: no `TBD`, no `// add validation`, no "same as the tab above". …
2471: - **Placeholder scan:** `grep -nEi "TBD|…"` over this file returns only the Global-Constraints rule …
```

The wider `\.\.\.` pass matches only (a) the `base.Integration` protocol's intentional Python ellipsis
bodies (line 1627–1630), (b) TypeScript/JS spread in test fixtures (`{ ...dnsFixture, … }`,
`{ ...twoNetworkDevicesFixture, … }`), and (c) elided argument lists in prose signatures that the same
bullet then specifies in full (`security.scorecards(...)`, `sections.devices(...)`). No task in 51–56
contains an unwritten step, an unnamed file or a "same as the task above".

**Type-name parity.** The addendum adds exactly four `export type` names — `NetworksPayload`, `Network`,
`NetworkRef`, `NetworkHealth` — and Task 51 lands all four in **both** `hub/guardian_hub/schemas_dash.py`
and `web/src/types.ts`. Task 16's `test_every_payload_model_is_mirrored_in_types_ts` fails CI if either
side is missed, and `hub/scripts/check_types.py` catches it at build time. Every *existing* model the
addendum extends is already in the Task 16 contract table: `OverviewPayload`, `DevicesPayload`,
`DeviceGroup`, `DeviceCard`, `KindChip`, `InboxRow`, `EventRow`, `TopoNode`, `TopoCluster`,
`SecurityPayload`, `WifiGuard`, `Attacker`, `DnsClient`, `DnsStreamRow`, `ScreenTimeRow`. No new name
collides with one already in that table.

**Route parity.** Every path the addendum extends already exists in a hub task's Interfaces block:
`GET /devices`, `/devices/{mac}`, `/presence` (Task 19); `GET /inbox`, `POST /alerts/bulk` (Task 20);
`GET /events`, `POST /devices/{mac}`, `/devices/{mac}/forget` (phase-1 T6); `GET /topology` (Task 31);
`GET /dns` (Task 30); `GET /security` (Task 28); `GET /overview` (Task 18). The addendum adds one new
route family, `/api/v1/networks` (GET/POST/PATCH/DELETE, Task 51), which is spec §3.1 verbatim, and no
new sensor route — the sensor contract changes are the additive `networks` key in the existing
`POST /sensor/ingest` body and the additive `net` key in existing `Edit` bodies.

**Four assertions in already-written tests that Task 51 must update as it lands** (found by re-reading
Tasks 18–20/30/31 against the new fields — each is listed in Task 51's Files block):
1. `hub/tests/test_inbox.py::test_three_views_share_one_row_shape` asserts `set(row) == keys` **exactly**;
   `InboxRow` gains `network`, so that literal set needs the new key or the test fails.
2. `web/tests/types.test.ts` builds exhaustive `OverviewPayload`, `DeviceCard` and `InboxRow` literals;
   `networks` / `network` are required fields, so the literals must gain them to type-check.
3. `hub/tests/test_assets.py::test_sites_hosts_devices` asserts `sum(c["count"] for c in d["chips"]) == d["total"]`.
   This still holds after the split **because chips become one row per (network, kind group)** and every
   device counts in exactly one such pair — the assertion is preserved deliberately, not by luck.
4. `hub/tests/test_topology.py::test_topology_is_a_connected_graph_the_ui_can_draw` walks the graph from
   `internet` and demands no orphans. A `?network=`-filtered topology payload therefore **must still
   include the `internet` node and that network's gateway link**, or the same test re-run with a filter
   would fail; Task 51's own `test_every_scoped_route_filters_by_network` asserts the filtered node set.

**Two deliberate non-extensions, recorded so they are not read as omissions:** the DNS **setup wizard**
(`DnsWizard`, Task 30/34) stays primary-network-only — spec §3.1 asks for per-network DNS *attribution*,
not one resolver wizard per network, and the sensor runs a single `guardian-dns` instance; and
`compliance.rows` (Task 37) stays tenant-level, because all twelve controls are tenant facts.
