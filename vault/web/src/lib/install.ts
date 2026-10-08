/** The install command the dashboard advertises, in one place.
 *
 *  This module exists because of a real, shipped bug: Onboarding and
 *  Settings → Networks each carried their own copy of the command, and both
 *  copies named hosts Vault's operator does not own —
 *  `https://get.guardian.sh/...` (a third party's server, so the advertised
 *  line piped a stranger's script into a root shell) and
 *  `ghcr.io/guardian/sensor` (an unpublished image, 401 on pull).
 *
 *  Two rules follow, and `web/tests/install.test.tsx` enforces both:
 *
 *  1. **The only host in a command is the app's own origin.** Never a literal
 *     `my.guarder.io` either — a self-hosted customer's dashboard has to hand
 *     out *their* hub's URL, and the browser already knows it. `origin` is
 *     passed in (rather than read here) so the caller keeps one source of
 *     truth and the function stays pure and testable.
 *  2. **Only platforms that actually have a distribution channel appear.**
 *     Docker was removed rather than pointed somewhere plausible: no Vault
 *     sensor image is published anywhere, and an invented registry path is the
 *     same class of defect as the domain above. Add it back — here and in
 *     `PLATFORMS` — the day an image really exists.
 */

export const PLATFORMS = [
  { value: "macos", label: "macOS" },
  { value: "linux", label: "Linux" },
] as const;

export type Platform = (typeof PLATFORMS)[number]["value"];

export const PLATFORM_LABEL: Record<Platform, string> = {
  macos: "macOS",
  linux: "Linux",
};

/** What the script will install once it has enrolled, per platform — shown
 *  next to the command so the picker earns its place even though the command
 *  itself is identical (the script reads `uname -s` and decides). */
export const PLATFORM_SERVICE: Record<Platform, string> = {
  macos: "a launchd agent",
  linux: "a systemd service (run it with sudo)",
};

/** `curl <origin>/install | sh` — the hub serves both the script and the
 *  sensor package (`hub/guardian_hub/installer.py`), so the command names
 *  exactly one host and it is the one the operator controls. The enrol code
 *  is an argument the human pastes; nothing downloaded carries a secret. */
export function installCommand(code: string, origin: string): string {
  return `curl -fsSL ${origin}/install | sh -s -- --code ${code}`;
}
