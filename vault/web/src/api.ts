// Phase-1 typed API surface, kept so the OTP login page and the placeholder
// Home shell keep working unchanged. The transport now lives in `lib/api.ts`
// and is re-exported by name (never `export *`) — `ApiError` is the same class
// on both sides, and the phase-2 payload types in `types.ts` deliberately do
// *not* leak in here: `OverviewOut` below is the older phase-1 shape, and new
// components import their types from `types.ts` only. `NextAction` itself is
// *not* duplicated here (retired in Task 23) — it is the one field this file
// shares with `types.ts`, so it is imported from there instead of forked.

import { get, post } from "./lib/api";
import type { Ack } from "./lib/api";
import type { NextAction } from "./types";

export { ApiError } from "./lib/api";
export type { NextAction } from "./types";

export type TenantOut = {
  id: string;
  slug: string;
  name: string;
  role: string;
};

export type UserOut = {
  username: string;
  display_name: string;
  is_superadmin: boolean;
};

export type MeOut = {
  user: UserOut;
  tenants: TenantOut[];
  active_tenant: TenantOut | null;
  /** The configured chat provider ladder, in the order the hub walks it
   *  (e.g. `["groq", "anthropic", "rules"]`) — rendered as the chat panel's
   *  header caption. Provider names only; never a key or a model name. */
  chat_providers: string[];
};

export type SiteOut = {
  name: string;
  ok: boolean | null;
  ms: number | null;
  cert_days: number | null;
};

export type HostOut = {
  name: string;
  ok: boolean | null;
  load: number | null;
  disk_pct: number | null;
  mem_pct: number | null;
  reboot_required: boolean | null;
};

export type OverviewOut = {
  ts: string | null;
  score: number | null;
  health: Record<string, unknown>;
  alerts: { crit: number; warn: number; info: number; open: number };
  devices: { total: number; online: number; unverified: number };
  sites: SiteOut[];
  hosts: HostOut[];
  verdict: string;
  next_action: NextAction | null;
};

export type AlertSeverity = "CRIT" | "WARN" | "INFO";

export type AlertOut = {
  key: string;
  sev: AlertSeverity;
  msg: string;
  status: string;
  first_seen: string;
  last_seen: string;
  count: number;
};

export type EnrolCodeOut = {
  code: string;
  expires: string;
  sensor_id: string;
};

export function authStart(username: string): Promise<{ sent: boolean }> {
  return post("/api/v1/auth/start", { username });
}

export function authVerify(
  username: string,
  code: string,
  remember: boolean,
): Promise<MeOut> {
  return post("/api/v1/auth/verify", { username, code, remember });
}

export function authLogout(): Promise<{ ok: boolean }> {
  return post("/api/v1/auth/logout", {});
}

export function me(): Promise<MeOut> {
  return get("/api/v1/me");
}

/** Switches the signed-in user's active tenant. The sidebar's tenant switcher
 *  posts here, then invalidates every query — every payload in `types.ts` is
 *  tenant-scoped, so a stale cache after a switch would show the wrong org. */
export function setActiveTenant(tenantId: string): Promise<Ack> {
  return post("/api/v1/me/tenant", { tenant_id: tenantId });
}

export function overview(): Promise<OverviewOut> {
  return get("/api/v1/overview");
}

export function inboxAlerts(): Promise<AlertOut[]> {
  return get("/api/v1/inbox?view=alerts");
}

// Mints an enrol code for the signed-in user's active tenant. Gated
// server-side on that tenant membership being "admin" (or superadmin) —
// this is the tenant-admin-facing route, distinct from the
// superadmin-only `POST /api/v1/tenants/{tid}/enrol-code`.
export function generateEnrolCode(): Promise<EnrolCodeOut> {
  return post("/api/v1/sensors/enrol-code", {});
}
