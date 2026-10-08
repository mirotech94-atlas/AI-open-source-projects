/** First-run wizard state.
 *
 *  **The hub is the authority.** `GET/POST /api/v1/onboarding` holds whether
 *  THIS user has been through the wizard on THIS tenant, so the second person
 *  to join an established site still gets shown around, and the same person on
 *  a second machine does not. See `hub/guardian_hub/onboarding.py`.
 *
 *  Two browser-local things remain, and neither is an authority on completion:
 *
 *  1. The resumable step (`guardian.onboarding.<tenant>`), which `Onboarding`
 *     writes as it advances. The hub stores the step too; this is the local
 *     copy that survives a reload before the first save lands.
 *  2. The LEGACY per-tenant "dismissed" flag, which used to be the whole
 *     gate. It is read exactly once per tenant, by `Shell`, to migrate it into
 *     the hub as "skipped" — and then deleted. Nothing else may read it, and
 *     nothing writes it any more.
 */

const DISMISSED_PREFIX = "guardian.onboarding.dismissed.";

function dismissedKey(tenantId: string): string {
  return `${DISMISSED_PREFIX}${tenantId}`;
}

/** LEGACY. True when this browser still carries the old per-tenant dismissal
 *  for `tenantId`. Read once, by `Shell`'s reconciliation, and never as a
 *  reason to hide the wizard on its own — a second user on the same browser
 *  would inherit the first user's dismissal, which is the bug this whole
 *  change is about. Best-effort: private browsing just means there is nothing
 *  to migrate. */
export function hasLegacyDismissal(tenantId: string): boolean {
  try {
    return window.localStorage.getItem(dismissedKey(tenantId)) === "1";
  } catch {
    return false;
  }
}

/** Deletes the legacy flag for one tenant, once it has been migrated onto the
 *  hub. There is deliberately no way to *set* it: the hub is the only place
 *  "this person has finished the wizard" is now written. */
export function clearLegacyDismissal(tenantId: string): void {
  try {
    window.localStorage.removeItem(dismissedKey(tenantId));
  } catch {
    // Best-effort only, same as every other localStorage write in this app.
    // A flag that cannot be deleted is re-migrated on the next load, which is
    // idempotent (it posts `skipped` to a row that already says skipped).
  }
}
