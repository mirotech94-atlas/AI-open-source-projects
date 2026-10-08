/** Is this browser tab the owner portal?
 *
 *  The portal is the same bundle as the tenant app, served on its own
 *  hostname. The host decides which surface mounts; `is_superadmin` decides
 *  whether the person may see it. Both are required, and the host alone
 *  grants nothing — the API gates every read on the session's superadmin
 *  flag regardless of which hostname asked.
 *
 *  `admin.localhost` and the `?owner=1` escape hatch exist so the portal can
 *  be worked on against the dev server, where there is only one hostname.
 */
export const OWNER_HOSTS = ["admin.guarder.io", "admin.localhost"];

export function isOwnerHost(): boolean {
  if (typeof window === "undefined") return false;
  const host = window.location.hostname.toLowerCase();
  if (OWNER_HOSTS.includes(host)) return true;
  return new URLSearchParams(window.location.search).get("owner") === "1";
}
