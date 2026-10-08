// The ATLAS status orb: how a /healthz reading is judged.
//
// Pure (no DOM): the tests import it. Mirrors the off-box watchdog
// (docs/watchdog.md): `ok` is always true while the process answers, so the
// judgement is on the two heartbeat ages. Any age over 900 s is STALE, and a
// stale age wins over a missing partner. Otherwise a missing, null or
// non-integer age is UNKNOWN. Anything that could not be read is UNKNOWN,
// never OK (CLAUDE.md invariant 7).
//
// Inside ATLAS the page reads its own origin's /healthz, so there is no CORS
// question any more (the standalone app could not read it for that reason).

export const HEALTH_URL = "/healthz";
export const HEARTBEAT_MAX_S = 900;
export const POLL_MS = 60_000;

export type HealthState = "OK" | "STALE" | "UNKNOWN";
export interface HealthReading { error?: string; status?: number; body?: unknown }
export interface HealthVerdict { state: HealthState; detail: string }

const isAge = (v: unknown): v is number => Number.isInteger(v) && (v as number) >= 0;

export function classifyHealth(reading: HealthReading | null | undefined): HealthVerdict {
  if (!reading || reading.error) {
    return { state: "UNKNOWN", detail: `could not be read (${reading?.error ?? "no reading"})` };
  }
  if (reading.status !== 200) return { state: "UNKNOWN", detail: `HTTP ${reading.status ?? "?"}` };
  const body = reading.body as { ok?: unknown; heartbeat?: Record<string, unknown> } | null;
  if (!body || typeof body !== "object" || body.ok !== true) {
    return { state: "UNKNOWN", detail: "no ok:true in the answer" };
  }
  const hb = body.heartbeat;
  const ages = [hb?.cycle_age_s, hb?.observation_age_s];
  const stale = ages.filter(isAge).find((a) => a > HEARTBEAT_MAX_S);
  if (stale !== undefined) return { state: "STALE", detail: `heartbeat ${stale} s old` };
  if (!ages.every(isAge)) return { state: "UNKNOWN", detail: "heartbeat age missing" };
  return { state: "OK", detail: `cycle ${String(ages[0])} s, observation ${String(ages[1])} s` };
}

type FetchLike = (url: string, init?: RequestInit) => Promise<{ status: number; json(): Promise<unknown> }>;

/** One read. Never throws: a failure is a reading with an error. */
export async function readHealth(
  fetchImpl: FetchLike = (u, i) => fetch(u, i), url = HEALTH_URL, timeoutMs = 8000,
): Promise<HealthReading> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const r = await fetchImpl(url, { cache: "no-store", credentials: "same-origin", signal: ctrl.signal });
    let body: unknown = null;
    try { body = await r.json(); } catch { body = null; }
    return { status: r.status, body };
  } catch (e) {
    return { error: (e as { name?: string })?.name === "AbortError" ? "timeout" : "unreachable" };
  } finally {
    clearTimeout(timer);
  }
}
