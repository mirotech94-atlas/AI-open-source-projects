// Role derivation for the active session. The hub's own `ROLE_ORDER` (see
// `hub/guardian_hub/deps.py`) is `viewer(0) < operator(1) < admin(2)`; a
// superadmin's membership is already reported as role "admin" for every
// tenant (see `deps.memberships`), so nothing here needs a separate
// is_superadmin branch — `active_tenant.role` already reflects it.
//
// One `useMe()` hook, one `["me"]` query key — the same key `Shell` already
// populates, so this is a cache hit there and a dedup'd single fetch
// anywhere else it is used (React Query, not a second request).

import { useQuery } from "@tanstack/react-query";
import { me } from "../api";
import type { MeOut } from "../api";

export type Role = "viewer" | "operator" | "admin";

const ROLE_ORDER: Record<Role, number> = { viewer: 0, operator: 1, admin: 2 };

function isRole(v: string | undefined): v is Role {
  return v === "viewer" || v === "operator" || v === "admin";
}

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: me });
}

/** The caller's role in their active tenant, or `null` while `me()` is still
 *  loading / there is no active tenant. */
export function roleOf(meOut: MeOut | undefined): Role | null {
  const r = meOut?.active_tenant?.role;
  return isRole(r) ? r : null;
}

/** Whether `role` meets or exceeds `min` on the hub's own role ladder. A
 *  `null` role (still loading, or no active tenant) never satisfies any
 *  minimum — controls stay disabled rather than flashing enabled early. */
export function atLeast(role: Role | null, min: Role): boolean {
  if (role === null) return false;
  return ROLE_ORDER[role] >= ROLE_ORDER[min];
}

/** The hub's own tier numbers (`RowAction.tier`, `AlertRemedy.tier`) read
 *  back onto this same ladder: 1 = every role, 2 = operator, 3 = admin (see
 *  `hub/guardian_hub/deps.py`'s `ROLE_ORDER` and `payloads/inbox.py`'s
 *  `ALERT_OPS`, which tags the everyday alert verbs tier 2). Anything that
 *  gates on a payload row's `tier` rather than a hard-coded role name goes
 *  through this rather than re-deriving the mapping at each call site. */
export function roleForTier(tier: number): Role {
  return tier >= 3 ? "admin" : tier >= 2 ? "operator" : "viewer";
}

/** A control every role can see. When the caller's role is below `min` it
 *  renders disabled with a title that names the role needed — the control
 *  stays, only the ability goes. `TopBar`'s header actions (Scan now, speed
 *  test, maintenance) and the inbox row verbs (Ack/Snooze/Resolve/Reopen)
 *  both gate through this one function so a viewer reads the same voice in
 *  both places. */
export function gate(role: Role | null, min: Role, label: string): { disabled: boolean; title: string } {
  const allowed = atLeast(role, min);
  return { disabled: !allowed, title: allowed ? label : `${label} — needs the ${min} role` };
}
