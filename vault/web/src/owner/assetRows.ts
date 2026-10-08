import type { DeviceCard, HostCard, SiteCard } from "../types";
import type { Tone } from "./net";

/**
 * One row of the Assets table, whichever of the three payloads it came from.
 *
 * Devices, servers and websites are three payloads on the hub because they
 * are three different kinds of evidence; the reference's Assets screen is one
 * list because the owner's question -- "what do I have, and what state is it
 * in" -- does not care which. So the three are mapped into one row shape
 * here, once, and the table and the detail page both read it.
 *
 * The posture pill states the specific problem rather than a severity word:
 * `CERT 9 D`, `3 UPDATES`, `UNVERIFIED` -- the reference's vocabulary.
 */
export type AssetKind = "device" | "server" | "site";

export type AssetRow = {
  kind: AssetKind;
  id: string;
  name: string;
  sub: string;
  type: string;
  address: string;
  platform: string;
  lastSeen: string;
  posture: { label: string; tone: Tone };
  unmanaged: boolean;
  /** Green when the sensor currently sees it. */
  seen: boolean;
};

export function fromDevice(d: DeviceCard): AssetRow {
  const posture = d.quarantined ? { label: "quarantined", tone: "crit" as const }
    : d.unverified ? { label: "unverified", tone: "warn" as const }
    : d.ports.some((p) => p.risky) ? { label: `${d.ports.filter((p) => p.risky).length} risky ${d.ports.filter((p) => p.risky).length === 1 ? "port" : "ports"}`, tone: "warn" as const }
    : d.risk >= 50 ? { label: `risk ${d.risk}`, tone: "warn" as const }
    : d.online ? { label: "clean", tone: "good" as const }
    : { label: "offline", tone: "plain" as const };
  return {
    kind: "device", id: d.mac,
    name: d.label || d.name || d.vendor || d.mac,
    sub: [d.vendor, d.network?.name].filter(Boolean).join(" · "),
    type: d.kind || "device",
    address: d.ip || d.mac,
    platform: d.vendor || "—",
    lastSeen: d.last_seen,
    posture, unmanaged: d.unverified, seen: d.online,
  };
}

export function fromHost(h: HostCard): AssetRow {
  const posture = h.reachable === false ? { label: "unreachable", tone: "crit" as const }
    : h.cve_fixable > 0 ? { label: `${h.cve_fixable} ${h.cve_fixable === 1 ? "cve" : "cves"}`, tone: "warn" as const }
    : h.security_updates > 0 ? { label: `${h.security_updates} security ${h.security_updates === 1 ? "update" : "updates"}`, tone: "warn" as const }
    : h.reboot_required ? { label: "reboot due", tone: "warn" as const }
    : (h.disk_pct ?? 0) >= 85 ? { label: `disk ${Math.round(h.disk_pct!)}%`, tone: "warn" as const }
    : h.stale ? { label: "stale", tone: "plain" as const }
    : { label: "clean", tone: "good" as const };
  return {
    kind: "server", id: h.name,
    name: h.name, sub: h.hostname && h.hostname !== h.name ? h.hostname : `up ${h.uptime || "—"}`,
    type: "server", address: h.address, platform: h.hardening?.grade ? `hardening ${h.hardening.grade}` : "—",
    lastSeen: h.last_checked,
    posture, unmanaged: false, seen: h.reachable === true && !h.stale,
  };
}

export function fromSite(s: SiteCard): AssetRow {
  const posture = s.ok === false ? { label: `down${s.code ? ` · ${s.code}` : ""}`, tone: "crit" as const }
    : s.cert_days !== null && s.cert_days < 14 ? { label: `cert ${s.cert_days} d`, tone: "crit" as const }
    : s.cert_days !== null && s.cert_days < 30 ? { label: `cert ${s.cert_days} d`, tone: "warn" as const }
    : s.stale ? { label: "stale", tone: "plain" as const }
    : { label: "clean", tone: "good" as const };
  let host = s.url;
  try { host = new URL(s.url).host; } catch { /* keep the raw url */ }
  return {
    kind: "site", id: s.name,
    name: s.name, sub: s.public ? "public" : "internal",
    type: "website", address: host, platform: s.cert_issuer || "—",
    lastSeen: s.last_checked,
    posture, unmanaged: false, seen: s.ok === true && !s.stale,
  };
}
