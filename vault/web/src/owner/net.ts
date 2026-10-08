/**
 * What every network screen in the console shares.
 *
 * The console shows ONE customer's network at a time -- the one the bar's
 * picker has made the session's active tenant -- and every screen under
 * Operations and Inventory reads that tenant's own payloads, the same ones
 * the customer sees on my.guarder.io. Deliberately the same endpoints rather
 * than an owner-only aggregate: two readings of "what is open on Northwind's
 * network" that could disagree is exactly what an owner sitting with that
 * customer cannot afford.
 */
import { useMe } from "../lib/auth";
import type { TenantOut } from "../api";
import type { Sev } from "../types";

export function useActiveTenant(): TenantOut | null {
  return useMe().data?.active_tenant ?? null;
}

/** "now", "4 min", "3 h", "6 d" -- the reference's relative time, short
 *  enough for a mono column. */
export function ago(iso: string | null | undefined): string {
  if (!iso) return "never";
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms)) return "never";
  const m = Math.round(ms / 60000);
  if (m < 1) return "now";
  if (m < 60) return `${m} min`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h} h`;
  return `${Math.round(h / 24)} d`;
}

/** Seconds of age to the same shorthand, for payloads that carry `age_s`. */
export function agoS(s: number | null | undefined): string {
  if (s === null || s === undefined) return "never";
  if (s < 60) return "now";
  if (s < 3600) return `${Math.round(s / 60)} min`;
  if (s < 172800) return `${Math.round(s / 3600)} h`;
  return `${Math.round(s / 86400)} d`;
}

/** "14:22" -- the clock time the reference's activity rows lead with. */
export function clock(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false });
}

/** "Sep 04" for a tick or a date column. */
export function shortDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, { month: "short", day: "2-digit" });
}

export type Tone = "good" | "warn" | "crit" | "info" | "plain";

export function sevTone(sev: Sev | string | null | undefined): Tone {
  const s = String(sev ?? "").toUpperCase();
  if (s === "CRIT" || s === "CRITICAL") return "crit";
  if (s === "WARN" || s === "HIGH" || s === "MEDIUM") return "warn";
  if (s === "INFO") return "info";
  return "plain";
}

/** The reference's severity vocabulary for a pill: CRITICAL / HIGH / MEDIUM. */
export function sevLabel(sev: Sev | string | null | undefined): string {
  const s = String(sev ?? "").toUpperCase();
  if (s === "CRIT") return "CRITICAL";
  if (s === "WARN") return "HIGH";
  if (s === "INFO") return "MEDIUM";
  return s || "—";
}

/** A CSV the browser saves, built from rows already on screen. The audit
 *  trail has no export route of its own, and a file made of exactly what the
 *  page shows is the honest one: what you filtered is what you get. */
export function downloadCsv(name: string, header: string[], rows: string[][]): void {
  const esc = (v: string) => (/[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);
  const text = [header, ...rows].map((r) => r.map(esc).join(",")).join("\n");
  const blob = new Blob([text], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = name; a.click();
  URL.revokeObjectURL(url);
}
