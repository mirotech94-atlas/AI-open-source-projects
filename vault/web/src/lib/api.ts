// The Vault hub transport plus one typed function per phase 2–4 route.
//
// Every call sends `credentials: "include"` so the `guardian_session` cookie
// rides along, and a 401 from anywhere means the session is gone (never signed
// in, expired, or signed out elsewhere) — the transport redirects to /login
// rather than making each caller handle it.
//
// Naming rule: a read function is named after the `types.ts` type it returns,
// camelCased (`overviewPayload()` -> `OverviewPayload`). Writes are named after
// the action and return the hub's raw ack — an ack is not a contract type, and
// anything long-running reports its real state through `queueStatus()`.

import type {
  PlatformWorkPayload,
  AlertHandoff,
  AuditPayload,
  BillingPayload,
  ChatRequest,
  ChatResponse,
  CheckDecision,
  CheckDetail,
  EmailVerdictOut,
  FirewallPayload,
  FirewallRule,
  DeviceDetail,
  DevicesPayload,
  DeviceTraffic,
  DnsPayload,
  FixConfirmation,
  HeartbeatSetup,
  HistoryPayload,
  HostsPayload,
  InboxPayload,
  InboxTriagePayload,
  IncidentDetail,
  IntegrationsPayload,
  JobsPayload,
  LivePayload,
  NetworksPayload,
  OnboardingState,
  OverviewPayload,
  AccountPayload,
  AccountTrendsPayload,
  PlatformTrendsPayload,
  PlatformAuditPayload,
  PlatformPayload,
  PlatformSettingsPayload,
  QueueStatus,
  RebootWindow,
  RecorderRow,
  RecordersPayload,
  ReportCard,
  ReportsPayload,
  ScreenTimePayload,
  SecurityPayload,
  ThreatDetail,
  SettingsPayload,
  SitesPayload,
  TicketDetail,
  TicketsPayload,
  TopologyPayload,
  TrafficOverview,
  ConnectionsPayload,
  UpdatesPayload,
  VulnScanRow,
  VulnScansPayload,
} from "../types";

/** The shape shared by `NextAction` and `RowAction`: enough to dispatch a
 *  request exactly as the hub described it, without the caller having to
 *  know which contract type it came from. */
export type ActionRequest = {
  method: string;
  path: string;
  body: Record<string, unknown>;
};

import { forgetNetwork } from "./network";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** Ack shape for a write: whatever the hub returned. Callers that need more
 *  than "it was accepted" poll `queueStatus()` with the returned queue id. */
export type Ack = Record<string, unknown>;

function isLoginPath(): boolean {
  return window.location.pathname === "/login";
}

/** The `?network=<id>` a path is scoped to, or "" when it is unscoped. */
function scopedNetwork(path: string): string {
  const m = /[?&]network=([^&]+)/.exec(path);
  return m ? decodeURIComponent(m[1]) : "";
}

/** Recovers from a scoped read whose network no longer exists.
 *
 *  A network deleted inside its own tenant leaves its id in that tenant's
 *  `localStorage` key, and from then on every network-scoped page 404s with a
 *  Retry that can never succeed -- the only cure was clearing site data. This
 *  drops the dead selection and lets the caller retry unscoped.
 *
 *  It confirms the id is really gone before forgetting it, because a 404 on a
 *  scoped path can also mean the sub-resource is missing (an unknown site
 *  name) while the network is perfectly valid -- and silently clearing a good
 *  selection would be its own bug. */
async function staleNetwork(path: string): Promise<string> {
  const id = scopedNetwork(path);
  if (!id) return "";
  try {
    const res = await fetch("/api/v1/networks", { credentials: "include" });
    if (!res.ok) return "";
    const body = (await res.json()) as { networks?: { id: string }[] };
    if ((body.networks ?? []).some((n) => n.id === id)) return "";
  } catch {
    return "";
  }
  return id;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });

  if (res.status === 401) {
    if (!isLoginPath()) {
      const { pathname, search } = window.location;
      // "/" is already where a signed-in user lands, so a `next` back to it
      // would be redundant -- only deep links are worth round-tripping
      // through the login screen.
      const next = pathname === "/" ? "" : `?next=${encodeURIComponent(pathname + search)}`;
      window.location.href = `/login${next}`;
    }
    throw new ApiError(401, "not signed in");
  }

  if (!res.ok) {
    // Only a read, and only once: the retry drops `?network=` entirely, so it
    // cannot 404 for the same reason and cannot loop.
    if (res.status === 404 && (init?.method ?? "GET") === "GET") {
      const dead = await staleNetwork(path);
      if (dead) {
        forgetNetwork(dead);
        return request<T>(path.replace(/([?&])network=[^&]*&?/, "$1").replace(/[?&]$/, ""), init);
      }
    }
    let detail = res.statusText || `request failed (${res.status})`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body?.detail) detail = body.detail;
    } catch {
      // no JSON body; keep the fallback message
    }
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) {
    return undefined as T;
  }
  return (await res.json()) as T;
}

/** Generic read. `path` is the full hub path including any query string. */
export function get<T>(path: string): Promise<T> {
  return request<T>(path);
}

/** Generic write. */
export function post<T>(path: string, body: unknown = {}): Promise<T> {
  return request<T>(path, { method: "POST", body: JSON.stringify(body) });
}

/** Generic delete — used by the report library and the integration cards. */
export function del<T>(path: string): Promise<T> {
  return request<T>(path, { method: "DELETE" });
}

/** Generic delete with a JSON body — the settings "remove user" route takes
 *  `{username}` on the collection path rather than a `/users/{username}`
 *  sub-route, so a plain `del()` (no body) cannot express it. */
export function delBody<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: "DELETE", body: JSON.stringify(body) });
}

/** Generic partial update — the superadmin tenant editor (rename, brand,
 *  ntfy topic, demo toggle) per Task 39's `PATCH /settings/tenants`. */
export function patch<T>(path: string, body: unknown = {}): Promise<T> {
  return request<T>(path, { method: "PATCH", body: JSON.stringify(body) });
}

export function put<T>(path: string, body: unknown = {}): Promise<T> {
  return request<T>(path, { method: "PUT", body: JSON.stringify(body) });
}

/** Generic read for a route that answers with markup rather than JSON. */
export async function getText(path: string): Promise<string> {
  const res = await fetch(path, { credentials: "include" });
  if (res.status === 401) {
    if (!isLoginPath()) window.location.href = "/login";
    throw new ApiError(401, "not signed in");
  }
  if (!res.ok) {
    throw new ApiError(res.status, res.statusText || `request failed (${res.status})`);
  }
  return await res.text();
}

function query(params: Record<string, string | number | boolean | undefined>): string {
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === "") continue;
    usp.set(k, String(v));
  }
  const s = usp.toString();
  return s ? `?${s}` : "";
}

/* ---------------------------------------------------------------- reads --- */

export function overviewPayload(): Promise<OverviewPayload> {
  return get("/api/v1/overview");
}

export function livePayload(): Promise<LivePayload> {
  return get("/api/v1/live");
}

// `network` scopes the ALERTS view only — incidents and recommendations are not
// network-scoped rows on the hub (task 51 §4, §11.7). No caller passes it yet:
// the brief gives the network chooser three homes (the Assets sub-header, the
// Topology toolbar, the Security header) and Inbox is not one of them, so inbox
// rows carry a `NetworkBadge` for attribution but the list stays unfiltered.
// The parameter exists so wiring a chooser in later is a one-line change rather
// than a transport change.
/** The owner's ledger: every tenant on the platform. Superadmin only — a
 *  tenant admin gets 403, so this is never called outside the owner portal. */
export function platformPayload(): Promise<PlatformPayload> {
  return get("/api/v1/platform");
}

/** One alert sparkline per customer, for the Ledger's trend column. Served
 *  separately from `platformPayload` so the table renders on the first payload
 *  and the trend fills in after — a month of history for every tenant should
 *  not be on the critical path to drawing the list. */
export function platformTrends(days = 30): Promise<PlatformTrendsPayload> {
  return get(`/api/v1/platform/trends?days=${days}`);
}

/** One customer's own configuration: their networks, sensors, people,
 *  integrations and recent trail. Carries no credential — see
 *  `hub/tests/test_account_payload.py` for the guard that keeps it that way. */
export function accountPayload(tid: string): Promise<AccountPayload> {
  return get(`/api/v1/platform/tenants/${encodeURIComponent(tid)}`);
}

/** How one customer has actually been doing: their alerts, devices and score
 *  over `days`, each with its direction of travel in a sentence. The series
 *  come from the same builder that draws that customer's own History tab, so
 *  the owner and the customer are never looking at two different numbers for
 *  the same question. */
export type BillingAdjust =
  | { action: "extend_trial"; days: number }
  | { action: "grant_plan"; plan: string; interval: "month" | "year" };

/** The console's two billing writes. A customer Stripe is billing answers 409
 *  with the dashboard link in the message; the page shows that verbatim. */
export function adjustAccountBilling(tid: string, body: BillingAdjust): Promise<AccountPayload> {
  return post(`/api/v1/platform/tenants/${encodeURIComponent(tid)}/billing`, body);
}

export function accountTrends(tid: string, days = 30): Promise<AccountTrendsPayload> {
  return get(`/api/v1/platform/tenants/${encodeURIComponent(tid)}/trends?days=${days}`);
}

/** What has been done across every tenant, newest first. */
/** Who closed the work, per day -- or per hour with `hours`, and on one
 *  customer's network with `tenant`. Off the audit trail, so it can never
 *  disagree with the Audit screen. */
export function platformWork(days = 14, hours = 0, tenant = ""): Promise<PlatformWorkPayload> {
  return get(`/api/v1/platform/work${query({ days, hours: hours || undefined, tenant: tenant || undefined })}`);
}

export function platformAudit(): Promise<PlatformAuditPayload> {
  return get("/api/v1/platform/audit");
}

/** How the platform behaves, plus which credentials are configured. The
 *  credential VALUES never leave the hub — only whether each one is set. */
export function platformSettings(): Promise<PlatformSettingsPayload> {
  return get("/api/v1/platform/settings");
}

/** Change one platform setting. Returns the whole payload back, so the page
 *  re-renders from the hub's own reading rather than an optimistic guess. */
export function savePlatformSetting(key: string, value: string): Promise<PlatformSettingsPayload> {
  return post("/api/v1/platform/settings", { key, value });
}

// --- support tickets ------------------------------------------------------
// Two surfaces over one pair of tables: a customer sees only their own
// tenant's threads, the owner console sees every customer's. The owner calls
// are superadmin-only on the hub, so a tenant admin reaching them gets 403.

export function myTickets(): Promise<TicketsPayload> {
  return get("/api/v1/support/tickets");
}

export function openTicket(body: {
  subject: string; body: string; priority: string; category?: string;
  context_kind?: string; context_id?: string; context_label?: string;
}): Promise<TicketDetail> {
  return post("/api/v1/support/tickets", body);
}

export function myTicket(id: string): Promise<TicketDetail> {
  return get(`/api/v1/support/tickets/${encodeURIComponent(id)}`);
}

export function replyToMyTicket(id: string, body: string): Promise<TicketDetail> {
  return post(`/api/v1/support/tickets/${encodeURIComponent(id)}/reply`, { body });
}

export function ownerTickets(status = ""): Promise<TicketsPayload> {
  return get(`/api/v1/platform/tickets${status ? `?status=${encodeURIComponent(status)}` : ""}`);
}

export function ownerTicket(id: string): Promise<TicketDetail> {
  return get(`/api/v1/platform/tickets/${encodeURIComponent(id)}`);
}

export function ownerReply(id: string, body: string): Promise<TicketDetail> {
  return post(`/api/v1/platform/tickets/${encodeURIComponent(id)}/reply`, { body });
}

/** Close a ticket whose issue is fixed, or put one back in the queue. */
export function setTicketStatus(id: string, status: "resolved" | "open", note = ""): Promise<TicketDetail> {
  return post(`/api/v1/platform/tickets/${encodeURIComponent(id)}/status`, { status, note });
}

export function inboxPayload(view = "alerts", network?: string | null, status?: string): Promise<InboxPayload> {
  return get(`/api/v1/inbox${query({ view, network: network ?? undefined, status })}`);
}

/** The prioritised inbox (task 83): one lead item, the rest one line each,
 *  the three outcome counters. Not network-scoped, for the same reason
 *  `inboxPayload` above is not. */
export function inboxTriage(): Promise<InboxTriagePayload> {
  return get("/api/v1/inbox/triage");
}

/** One incident's own page (Task 83/A2) -- narrative, timeline, the assets
 *  involved, a scoped graph and the evidence rows. */
export function incidentDetail(iid: string): Promise<IncidentDetail> {
  return get(`/api/v1/incidents/${encodeURIComponent(iid)}`);
}

export function sitesPayload(): Promise<SitesPayload> {
  return get("/api/v1/sites");
}

export function hostsPayload(): Promise<HostsPayload> {
  return get("/api/v1/hosts");
}

export function devicesPayload(network?: string | null): Promise<DevicesPayload> {
  return get(`/api/v1/devices${query({ network: network ?? undefined })}`);
}

export function deviceDetail(mac: string, network?: string | null): Promise<DeviceDetail> {
  return get(`/api/v1/devices/${encodeURIComponent(mac)}${query({ network: network ?? undefined })}`);
}

/** Org-wide screen time (presence table + hour heatmap). The *per-device*
 *  `PresenceProfile` is not its own route — it arrives inside `DeviceDetail`. */
export function screenTimePayload(days = 7, network?: string | null): Promise<ScreenTimePayload> {
  return get(`/api/v1/presence${query({ days, network: network ?? undefined })}`);
}

/** Manual vulnerability scans (Security → Vulnerability scans): history plus
 *  the targets a scan can be pointed at. */
export function vulnScans(): Promise<VulnScansPayload> {
  return get("/api/v1/security/vuln-scans");
}

export function vulnScan(id: string): Promise<VulnScanRow> {
  return get(`/api/v1/security/vuln-scans/${encodeURIComponent(id)}`);
}

export function startVulnScan(target: string, kind: string, profile: "quick" | "standard" | "full"): Promise<Ack> {
  return post("/api/v1/security/vuln-scans", { target, kind, profile });
}

export function securityPayload(network?: string | null): Promise<SecurityPayload> {
  return get(`/api/v1/security${query({ network: network ?? undefined })}`);
}

/** Identifies one Wi-Fi guard row, checklist row, or posture deduction to
 *  the check-detail/decision/handoff routes below — the same three fields
 *  every one of them takes (`hub/guardian_hub/routers/security.py`). */
export type CheckRef = { key: string; network?: string; target?: string };

/** The drill-down for one failing check (owner request: clicking a failing
 *  Wi-Fi guard row like "evil twin" should open detail and offer resolve or
 *  leave it, same for Security posture). */
/** One attack class from the Threats and Protection matrix, opened up: what
 *  Vault found and, for anything no longer open, how it was settled. The
 *  card itself cannot answer the second half — its signals are open alerts
 *  only, and a settled alert is by definition not open. */
export function threatDetail(key: string, days = 7): Promise<ThreatDetail> {
  return get(`/api/v1/security/threats/${encodeURIComponent(key)}?days=${days}`);
}

/** "Someone just emailed me — is this legit?" Reads DNS only; the address
 *  never leaves the hub unless a reputation provider has been switched on. */
export function checkEmailAddress(address: string): Promise<EmailVerdictOut> {
  return get(`/api/v1/security/email-check${query({ address })}`);
}

/** Item 21: gateway rules Vault proposes. There is no `apply` helper
 *  because there is nothing to apply to yet — the hub refuses with the
 *  reason, and the UI reads `can_apply` rather than finding out by trying. */
export function firewallRules(): Promise<FirewallPayload> {
  return get("/api/v1/security/firewall");
}

export function declineFirewallRule(id: string, reason: string): Promise<FirewallRule> {
  return post(`/api/v1/security/firewall/${encodeURIComponent(id)}/decline`, { reason });
}

export function checkDetail(ref: CheckRef): Promise<CheckDetail> {
  return get(`/api/v1/security/checks/detail${query({ key: ref.key, network: ref.network,
                                                       target: ref.target })}`);
}

/** Records (or undoes) the owner's own "leave it" call on one failing check
 *  — the other half of the resolve-or-leave-it pair from `checkDetail`
 *  above. `op` is `"leave"` or `"undo"`. */
export function checkDecision(op: "leave" | "undo", ref: CheckRef, reason = "", expires_at = ""): Promise<CheckDecision> {
  return post(`/api/v1/security/checks/${op}`, { key: ref.key, network: ref.network ?? "",
                                                  target: ref.target ?? "", reason, expires_at });
}

/** Opens the chat hand-off for a check Vault cannot simply fix — the
 *  check-scoped twin of `alertHandoff`, for a row that carries no `Alert` of
 *  its own. */
export function checkHandoff(ref: CheckRef, thread: string | null = null): Promise<AlertHandoff> {
  return post("/api/v1/chat/checks/handoff", { key: ref.key, network: ref.network ?? "",
                                               target: ref.target ?? "", thread });
}

export function updatesPayload(): Promise<UpdatesPayload> {
  return get("/api/v1/updates");
}

export function jobsPayload(): Promise<JobsPayload> {
  return get("/api/v1/jobs");
}

/** Re-queues a failed job with its exact original arguments. 409s when the
 *  job is not `failed`, or is not safe to retry without a fresh confirmation
 *  (a disruptive `TIER3_FIXES` action) -- `JobRow.retryable` already reflects
 *  that, so the button should be hidden/disabled before this is ever called. */
export function jobRetry(id: string): Promise<Ack> {
  return post(`/api/v1/jobs/${encodeURIComponent(id)}/retry`);
}

export function dnsPayload(hours = 24, network?: string | null): Promise<DnsPayload> {
  return get(`/api/v1/dns${query({ hours, network: network ?? undefined })}`);
}

// The live DNS query stream (`DnsStreamRow`) is deliberately *not* an HTTP
// route: it is the `dns` frame type on `WS /ws`, opted into with
// `{"subscribe": ["dns"]}`. See the WS client hook in Task 23.

export function topologyPayload(
  clusterBy: "kind" | "area" | "network" = "kind",
  network?: string | null,
): Promise<TopologyPayload> {
  return get(`/api/v1/topology${query({ cluster_by: clusterBy, network: network ?? undefined })}`);
}

/** Live connections, grouped by device and named (`payloads/traffic.connections`). */
export function trafficConnections(minutes = 15, network?: string | null): Promise<ConnectionsPayload> {
  return get(`/api/v1/traffic/connections${query({ minutes, network: network ?? undefined })}`);
}

export function trafficOverview(hours = 1, network?: string | null): Promise<TrafficOverview> {
  return get(`/api/v1/traffic${query({ hours, network: network ?? undefined })}`);
}

export function deviceTraffic(mac: string, hours = 24, network?: string | null): Promise<DeviceTraffic> {
  return get(`/api/v1/devices/${encodeURIComponent(mac)}/traffic${query({ hours, network: network ?? undefined })}`);
}

/** `days` is the range; `network` narrows the network-scoped series. */
/** `hours` (when > 0) asks the hub for a sub-day window and wins over `days` —
 *  the only way to get the 6-hour range, since `days` cannot go below 1. */
export function historyPayload(days = 7, network?: string | null, hours = 0): Promise<HistoryPayload> {
  const q = hours > 0 ? new URLSearchParams({ hours: String(hours) }) : new URLSearchParams({ days: String(days) });
  if (network !== undefined && network !== null && network !== "") q.set("network", network);
  return get(`/api/v1/history?${q.toString()}`);
}

export function reportsPayload(): Promise<ReportsPayload> {
  return get("/api/v1/reports");
}

/** Report preview markup for the builder's live pane. Not a `types.ts` payload
 *  and not JSON — the hub renders the report and returns raw HTML, freshly, so
 *  nothing is persisted by a preview. */
export function reportPreview(
  days: number,
  preset: string,
  sections: string[],
): Promise<string> {
  return getText(
    `/api/v1/reports/preview${query({ days, preset, sections: sections.join(",") })}`,
  );
}

export function settingsPayload(): Promise<SettingsPayload> {
  return get("/api/v1/settings");
}

export function integrationsPayload(): Promise<IntegrationsPayload> {
  return get("/api/v1/integrations");
}

export function auditPayload(
  limit = 200,
  action = "",
  user = "",
): Promise<AuditPayload> {
  return get(`/api/v1/audit${query({ limit, action, user })}`);
}

export function queueStatus(id: string): Promise<QueueStatus> {
  return get(`/api/v1/queue/${encodeURIComponent(id)}`);
}

/** The ⌘K palette's search hit shape — one flat, already-ranked list, one
 *  row per hit, exactly as `GET /api/v1/search` (`payloads/search.py`)
 *  returns it. Deliberately *not* in `types.ts`: that file is paired
 *  one-for-one with a `schemas_dash.py` pydantic model by
 *  `hub/tests/test_contracts.py`, and this type only needs to track the one
 *  endpoint's shape, not the full `SearchResult` schema. `href` is the
 *  in-app route the hub already computed for the hit — the palette
 *  navigates there verbatim rather than reconstructing a path from `kind` +
 *  `id`. */
export type SearchKind = "device" | "host" | "site" | "alert" | "route";
export type SearchHit = { kind: SearchKind; id: string; title: string; subtitle: string; href: string };
export type SearchResults = { results: SearchHit[] };

export function search(q: string): Promise<SearchResults> {
  return get(`/api/v1/search${query({ q })}`);
}

/* --------------------------------------------------------------- writes --- */

/** Inbox row ops: ack / snooze / resolve / unack, one alert key at a time. */
export function alertOp(key: string, op: string, body: Ack = {}): Promise<Ack> {
  return post(`/api/v1/alerts/${encodeURIComponent(key)}/${encodeURIComponent(op)}`, body);
}

export function alertBulk(keys: string[], op: string, hours?: number): Promise<Ack> {
  // `hours` only means anything to a snooze; the hub ignores it otherwise
  // and defaults to 8 when it is absent.
  return post("/api/v1/alerts/bulk", hours ? { keys, op, hours } : { keys, op });
}

/** Dispatches a `RowAction`/`NextAction` exactly as the hub described it —
 *  its own `method`, `path` and `body`, never a hand-built alternative. This
 *  is the only way row/hero actions reach the network: a hard-coded
 *  `alertOp`/`recDecision`-style helper drifts the moment the hub adds a new
 *  action kind or reuses an existing kind for a different route (e.g. "fix"
 *  posts to `/api/v1/fix`, not `/api/v1/alerts/{id}/fix`). */
export function runAction<T = Ack>(action: ActionRequest): Promise<T> {
  const method = action.method || "POST";
  const init: RequestInit = { method };
  if (method !== "GET" && method !== "DELETE") {
    init.body = JSON.stringify(action.body ?? {});
  }
  return request<T>(action.path, init);
}

/** Queues a fix for the sensor; poll the returned queue id. The hub's
 *  `FixIn` (`hub/guardian_hub/routers/write.py`) is `{host, action, arg}` —
 *  not `{kind, target}` — so every caller (the scorecard "Fix now" and the
 *  attack map's "Block this /24") posts that exact shape. `arg` is `null`,
 *  not omitted, when the action takes none. */
export function requestFix(
  host: string,
  action: string,
  arg: string | null = null,
  confirmToken: string | null = null,
): Promise<Ack> {
  return post("/api/v1/fix", { host, action, arg, confirm_token: confirmToken });
}

/** Either the fix was queued, or the hub demands a confirmation for it first.
 *  Exactly one side is non-null. */
export type FixOutcome = { ack: Ack; confirmation: null } | { ack: null; confirmation: FixConfirmation };

/** Queue a fix — or come back with the confirmation the hub demands before a
 *  disruptive one can run.
 *
 *  The HUB decides which actions those are: `POST /api/v1/fix` answers 409
 *  (with nothing queued) for anything in its tier-3 set, and only then is a
 *  confirmation minted. So no page carries its own copy of that list, and a
 *  new disruptive action classified in `chat/tiers.py` is asked about here
 *  without a web change. */
export async function requestFixOrConfirm(
  host: string,
  action: string,
  arg: string | null = null,
): Promise<FixOutcome> {
  try {
    return { ack: await requestFix(host, action, arg), confirmation: null };
  } catch (err) {
    if (err instanceof ApiError && err.status === 409) {
      return { ack: null, confirmation: await fixConfirmation(host, action, arg) };
    }
    throw err;
  }
}

/** Mint the one-shot confirmation a disruptive fix needs. Only the hub's
 *  tier-3 actions (`full_upgrade`, `reboot`, …) have one — a reversible fix
 *  422s here and goes straight to `requestFix`. The returned `summary` is what
 *  the reader has to agree to, and `token` is single-use: it is bound to this
 *  exact host, action and argument, to this user, and it expires. */
export function fixConfirmation(
  host: string,
  action: string,
  arg: string | null = null,
): Promise<FixConfirmation> {
  return post("/api/v1/fix/confirm", { host, action, arg }) as Promise<FixConfirmation>;
}

/** Download the alert log as a CSV file.
 *
 *  Fetched rather than linked: the viewer's sandbox aside, a plain `<a href>`
 *  bypasses the transport's own 401 handling and error reporting, and this way
 *  the hub's `Content-Disposition` filename is the one the browser saves. */
export async function exportAlertsCsv(days = 30, status = "all", network?: string | null): Promise<void> {
  const q = new URLSearchParams({ days: String(days), status });
  if (network !== undefined && network !== null && network !== "") q.set("network", network);
  const res = await fetch(`/api/v1/export/alerts.csv?${q.toString()}`, { credentials: "include" });
  if (!res.ok) throw new ApiError(res.status, `export failed (${res.status})`);
  const disposition = res.headers.get("content-disposition") ?? "";
  const named = /filename="([^"]+)"/.exec(disposition);
  const url = URL.createObjectURL(await res.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = named?.[1] ?? "vault-alerts.csv";
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export function requestRun(kind: string, target: string, body: Ack = {}): Promise<Ack> {
  return post("/api/v1/run", { kind, target, ...body });
}

export function requestSpeedtest(): Promise<Ack> {
  return post("/api/v1/speedtest", {});
}

/** Starts (or extends) a maintenance pause of `hours`, during which the hub
 *  suppresses non-CRIT delivery. The hub answers with the new `until`
 *  deadline; `LivePayload.maintenance_until` is the durable read of it. */
export function startMaintenance(hours: number): Promise<{ until: string }> {
  return post("/api/v1/maintenance", { hours });
}

export function setAttackerMethod(method: string): Promise<Ack> {
  return post("/api/v1/attacker", { method });
}

// The heartbeat setup line contains that heartbeat's own token, which is a
// write credential — anyone holding it can claim the job ran. Both routes
// are admin-only and audited on the hub, and the command is fetched only
// when an admin asks for it rather than riding along in the Security
// payload (which every viewer reads and which gets printed into shareable
// reports).
export function heartbeatSetup(name: string): Promise<HeartbeatSetup> {
  return get(`/api/v1/heartbeats/${encodeURIComponent(name)}/setup`);
}

// Rotation is immediate and has no grace period: the previous URL 404s from
// the moment this resolves, so the job must be given the new line.
export function rotateHeartbeat(name: string): Promise<HeartbeatSetup> {
  return post(`/api/v1/heartbeats/${encodeURIComponent(name)}/rotate`, {});
}

// `network` scopes the write to one network's row for this MAC. Without it
// the hub acts on the newest-`last_seen` row (task 51 §4) — right for a
// single-network tenant, wrong the moment the same laptop is on the Wi-Fi and
// in the lab, so every caller that knows which network it is looking at
// passes it.
export function updateDevice(mac: string, patch: Ack, network?: string | null): Promise<Ack> {
  return post(`/api/v1/devices/${encodeURIComponent(mac)}${query({ network: network ?? undefined })}`, patch);
}

export function forgetDevice(mac: string, network?: string | null): Promise<Ack> {
  return post(
    `/api/v1/devices/${encodeURIComponent(mac)}/forget${query({ network: network ?? undefined })}`,
    {},
  );
}

// Quarantine and merge/split are NOT their own hub routes — the hub's
// `DeviceUpdate` body (see `hub/guardian_hub/routers/write.py`) accepts
// `quarantined` and `parent` on `POST /api/v1/devices/{mac}` exactly like
// `label`/`trusted`/`always_on`. `quarantineDevice`/`mergeDevice` used to
// post to invented `/quarantine` and `/merge` sub-routes that never existed
// on the hub; the drawer now calls `updateDevice(mac, { quarantined })` and
// `updateDevice(mac, { parent })` (or `{ parent: null }` to split) instead.

export function resetLanBaseline(): Promise<Ack> {
  return post("/api/v1/reset-lan-baseline", {});
}

/** Adds or removes one domain from the allow/block list — the DNS tab's
 *  `RuleManager`. One rule at a time, not a whole-list replace: `kind` is
 *  which list, `op` is "add" | "remove". */
export function writeDnsRule(kind: "allow" | "block", domain: string, op: "add" | "remove"): Promise<Ack> {
  return post("/api/v1/dns/rules", { kind, domain, op });
}

/** The hub's `POST /api/v1/reboot-plan` contract is `{host, window_start,
 *  window_end}` — one host per request, not a `{hosts: [...]}` batch. Every
 *  caller (`RebootPlanner` schedules one candidate host at a time) already
 *  calls this once per host; a caller that ever needs several hosts must
 *  call it once per host itself. */
export function planReboot(host: string, window: RebootWindow): Promise<Ack> {
  return post("/api/v1/reboot-plan", { host, window_start: window.start, window_end: window.end });
}

/** Renders, persists, and returns the new `ReportCard` — per Task 38's
 *  `POST /api/v1/reports/build {days, preset, sections, title}`. Typed to
 *  return the card itself (not a bare `Ack`) because the builder prepends
 *  it straight into the `["reports"]` cache without a refetch. */
export function buildReport(
  days: number,
  preset: string,
  sections: string[],
  title: string,
): Promise<ReportCard> {
  return post("/api/v1/reports/build", { days, preset, sections, title });
}

/** The hub's ack for a share/rotate call: the fresh URL, plus whether a push
 *  of that link was attempted (`push: true` on the request). */
export type ShareResult = { share_url: string; pushed?: boolean };

/** Mints (or rotates) an expiring share link — Task 38's
 *  `POST /api/v1/reports/{id}/share {hours}` (`hours` capped at 720 by the
 *  hub). Re-sharing the same report rotates the token, invalidating the
 *  previous link; there is no separate revoke route. */
export function shareReport(id: string, hours: number, push = false): Promise<ShareResult> {
  return post(`/api/v1/reports/${encodeURIComponent(id)}/share`, { hours, push });
}

export function deleteReport(id: string): Promise<Ack> {
  return del(`/api/v1/reports/${encodeURIComponent(id)}`);
}

export function scheduleReports(body: Ack): Promise<Ack> {
  return post("/api/v1/reports/schedule", body);
}

export function saveConfig(body: Ack): Promise<Ack> {
  return post("/api/v1/config", body);
}

export function saveSettingsSection(section: string, body: Ack): Promise<Ack> {
  return post(`/api/v1/settings/${encodeURIComponent(section)}`, body);
}

/** Removes one membership from `Tenant.settings["users"]`/`accounts`. Per
 *  Task 39 this is `DELETE /api/v1/settings/users` with `{username}` in the
 *  body (not a `/users/{username}` sub-route) — the hub 422s when it would
 *  strip the tenant's last admin, and callers must surface that inline. */
export function removeUser(username: string): Promise<Ack> {
  return delBody("/api/v1/settings/users", { username });
}

/** Revokes one sensor's token — `status="revoked"`, `token_hash=null` on the
 *  hub. Assumed sub-route per Task 39's "`POST /settings/sensors/{id}/revoke`
 *  (admin)"; the enrol code itself is minted by `generateEnrolCode()` in
 *  `../api`, a separate pre-existing route. */
export function revokeSensor(id: string): Promise<Ack> {
  return post(`/api/v1/settings/sensors/${encodeURIComponent(id)}/revoke`, {});
}

/** Superadmin tenant CRUD. Per the controller's fix-round ruling:
 *  `POST /settings/tenants {name, slug}` creates; `PATCH
 *  /settings/tenants/{id}` handles rename (`{name}`), brand
 *  (`{brand: {name?, accent?, domain?}}`), ntfy topic (`{ntfy_topic}`), and
 *  the demo toggle (`{demo}`, mapped hub-side to `settings.demo_code`) —
 *  all the same PATCH, one field group at a time; delete removes the
 *  resource. */
export function createTenant(body: Ack): Promise<Ack> {
  return post("/api/v1/settings/tenants", body);
}

export function updateTenant(id: string, body: Ack): Promise<Ack> {
  return patch(`/api/v1/settings/tenants/${encodeURIComponent(id)}`, body);
}

export function deleteTenant(id: string): Promise<Ack> {
  return del(`/api/v1/settings/tenants/${encodeURIComponent(id)}`);
}

export function saveIntegration(kind: string, body: Ack): Promise<Ack> {
  return post("/api/v1/integrations", { kind, ...body });
}

export function testIntegration(kind: string): Promise<Ack> {
  return post(`/api/v1/integrations/${encodeURIComponent(kind)}/test`, {});
}

export function deleteIntegration(kind: string): Promise<Ack> {
  return del(`/api/v1/integrations/${encodeURIComponent(kind)}`);
}

export function pairTelegram(): Promise<Ack> {
  return post("/api/v1/integrations/telegram/pair", {});
}

/* -------------------------------------------------------------- router --- */

/** The site gateway router card. Kept out of `types.ts` for the same reason
 *  as `SearchHit`: that file is paired one-for-one with a `schemas_dash.py`
 *  model, and this shape mirrors a single hand-rolled route
 *  (`routers/router.py`), not a pydantic schema. Never carries the password —
 *  only whether one is stored (`has_credentials`) and the last Test outcome. */
export type RouterCard = {
  configured: boolean;
  vendor: string;
  vendor_label: string;
  base_url: string;
  username: string;
  has_credentials: boolean;
  enabled: boolean;
  status: string;
  last_test_ok: boolean | null;
  last_test_msg: string;
  last_test: string;
  /** When Vault last turned remote administration off on this gateway, or
   *  "" if it never has. It is a record of what Vault DID: the router's
   *  config API has no getter, so an empty value means "Vault has not run
   *  this", never "remote administration is on". */
  remote_admin_off_at: string;
  remote_admin_msg: string;
};

export type RouterSaveBody = {
  base_url?: string;
  username?: string;
  password?: string;
  enabled?: boolean;
};

/** Miro's call: Vault points the router's DNS at itself, so nobody has
 *  to open 192.168.1.1 and hunt for the right box. Two steps by design —
 *  the confirm returns the summary a person has to read before the change
 *  is queued, the same shape the remote-admin disable uses. */
export function confirmRouterDns(server: string): Promise<{ token: string; summary: string; tier: number }> {
  return post("/api/v1/router/set-dns/confirm", { server });
}

export function setRouterDns(server: string, confirmToken: string): Promise<Ack> {
  return post("/api/v1/router/set-dns", { server, confirm_token: confirmToken });
}

export function routerCard(): Promise<RouterCard> {
  return get("/api/v1/router");
}

export function saveRouter(body: RouterSaveBody): Promise<RouterCard> {
  return put("/api/v1/router", body);
}

export function deleteRouter(): Promise<{ ok: boolean }> {
  return del("/api/v1/router");
}

export function testRouter(): Promise<Ack> {
  return post("/api/v1/router/test", {});
}

/** The one-shot tier-3 confirmation for turning remote administration off,
 *  minted by the hub. The card shows `summary`, then posts `token` back to
 *  `disableRemoteAdmin`. Mirrors `FixConfirmation` but for the router. */
export type RouterDisableConfirmation = {
  token: string;
  expires: string;
  tier: number;
  summary: string;
};

export function disableRemoteAdminConfirm(): Promise<RouterDisableConfirmation> {
  return post("/api/v1/router/disable-remote-admin/confirm", {});
}

export function disableRemoteAdmin(confirmToken: string): Promise<Ack> {
  return post("/api/v1/router/disable-remote-admin", { confirm_token: confirmToken });
}

/* ------------------------------------------------------------- networks --- */

// `GET` is viewer-readable; every write is admin-only on the hub (task 51
// §4), and the UI gates the same way rather than letting an operator click
// through to a 403.

export function networksPayload(): Promise<NetworksPayload> {
  return get("/api/v1/networks");
}

export function createNetwork(body: Ack): Promise<Ack> {
  return post("/api/v1/networks", body);
}

export function updateNetwork(id: string, body: Ack): Promise<Ack> {
  return patch(`/api/v1/networks/${encodeURIComponent(id)}`, body);
}

/** Deletes one network. `reassign` is the id of the network its devices (and
 *  events, alerts, DNS rows and presence samples) move to — the hub 422s a
 *  delete that would strand rows, and always 422s the primary network. */
export function deleteNetwork(id: string, reassign?: string | null): Promise<Ack> {
  return del(
    `/api/v1/networks/${encodeURIComponent(id)}${query({ reassign: reassign ?? undefined })}`,
  );
}

// -------------------------------------------------------------------------
// Camera recorders (NVR/DVR). The password is write-only in both directions:
// it goes up in the body and never comes back down -- a row carries
// `has_credentials`, not the value.
// -------------------------------------------------------------------------
export function recordersPayload(): Promise<RecordersPayload> {
  return get("/api/v1/recorders");
}

export function createRecorder(body: Ack): Promise<RecorderRow> {
  return post("/api/v1/recorders", body);
}

export function updateRecorder(id: string, body: Ack): Promise<RecorderRow> {
  return patch(`/api/v1/recorders/${encodeURIComponent(id)}`, body);
}

export function deleteRecorder(id: string): Promise<Ack> {
  return del(`/api/v1/recorders/${encodeURIComponent(id)}`);
}

/** Vault chat. A tier-2+ answer comes back as a `ChatProposal`; re-post the
 *  same message with `confirm_token` set to that proposal's token to run it. */
export function chat(body: ChatRequest): Promise<ChatResponse> {
  return post("/api/v1/chat", body);
}

/** Opens the conversation about one alert: Vault's own explanation of what
 *  it can and cannot do, plus the real tier-3 proposal when the repair only
 *  needs a yes. Pass `thread` to keep an already-open conversation. Nothing is
 *  repaired by calling this. */
export function alertHandoff(key: string, thread: string | null = null): Promise<AlertHandoff> {
  return post("/api/v1/chat/handoff", { key, thread });
}

/* --------------------------------------------------- first-run wizard --- */

/** Whether THIS user has been through the first-run wizard on THIS tenant, and
 *  which walkthrough they should get. Viewer-readable; the hub answers about
 *  the caller only. */
export function onboardingState(): Promise<OnboardingState> {
  return get("/api/v1/onboarding");
}

/** Records wizard progress for the current user and tenant. Every field is
 *  optional and omitted means "leave as it is": the wizard saves `step` as it
 *  advances, "Skip for now" saves `skipped`, "Done" saves `completed`, and
 *  Settings' "Re-run setup" saves both back to false. */
export function saveOnboarding(
  body: { step?: string; completed?: boolean; skipped?: boolean },
): Promise<OnboardingState> {
  return post("/api/v1/onboarding", body);
}

/* ------------------------------------------------------------- billing --- */

// Admin-only on the hub, and the UI gates the same way rather than letting an
// operator click through to a 403. Nothing here holds a price: every number
// the Billing page shows arrives on `BillingPayload`, built from the hub's
// `plans.py`, which is the only place a Vault price is written down.

export function billingPayload(): Promise<BillingPayload> {
  return get("/api/v1/billing");
}

/** Claims the three free months. No card, no redirect — the hub creates the
 *  trial subscription locally and answers with the refreshed payload. */
export function startTrial(): Promise<BillingPayload> {
  return post("/api/v1/billing/trial", {});
}

/** Asks the hub for a Stripe Checkout URL. The caller redirects to it; this
 *  app never renders a card field, so no card data ever touches Vault.
 *  `quantity` is the monitored-site count, ignored for per-account plans. */
export function billingCheckout(
  plan: string,
  interval: string,
  quantity = 1,
): Promise<{ url: string }> {
  return post("/api/v1/billing/checkout", { plan, interval, quantity });
}

/** Asks the hub for a Stripe customer-portal URL: change plan, update card,
 *  cancel. Cancelling lives there rather than here so there is one authority
 *  on subscription state, and it is Stripe's. */
export function billingPortal(): Promise<{ url: string }> {
  return post("/api/v1/billing/portal", {});
}
