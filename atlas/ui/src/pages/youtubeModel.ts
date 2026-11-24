/**
 * The Atlas Academy YouTube figures, from the youtube module's checks for one
 * channel key. The key (`clips`) goes in requests and check names and is
 * never shown: the screen says "Atlas Academy".
 *
 * Anything that cannot be trusted is Unknown with a reason: no tile, a
 * module switched off, a check missing, set aside, stale, or not OK. A figure
 * the channel did not report is null (shown "unreported"), never 0.
 */
import { normaliseHealth, type CheckSummary, type Health, type ModuleTile } from "../api";
import { safeText } from "./retired";

export type YouTubeReading =
  | { state: "unknown"; why: string }
  | {
      state: "ok"; subscribers: number | null; views: number | null; readAt: string | null;
      latestVideos: number | null; latestReadAt: string | null;
    }
  | { state: "ok-nonumbers"; text: string; readAt: string | null };

export interface TokenReading {
  health: Health;
  readAt: string | null;
  detail: string;
}

const NUMBERS = /([\d,]+|unreported)\s+subscribers?\s*,\s*([\d,]+|unreported)\s+views?/i;

function num(s: string | undefined): number | null {
  if (!s || /unreported/i.test(s)) return null;
  const n = Number(s.replace(/,/g, ""));
  return Number.isFinite(n) ? n : null;
}

function firstClause(detail: string): string {
  const cut = safeText(detail.split(/\s\(|;|\n/)[0]?.trim() ?? "");
  return cut.length > 100 ? `${cut.slice(0, 99)}…` : cut;
}

function find(modules: ModuleTile[], channel: string, name: string): CheckSummary | undefined {
  return modules.find((m) => m.module === "youtube")?.checks.find((c) => c.name === `${channel}_${name}`);
}

export function youtubeChannel(modules: ModuleTile[] | null, channel: string): YouTubeReading {
  const unknown = (why: string): YouTubeReading => ({ state: "unknown", why: safeText(why) });
  if (modules === null) return unknown("not read");
  const tile = modules.find((m) => m.module === "youtube");
  if (!tile) return unknown("not watched");
  if (!tile.enabled) return unknown(tile.setup ?? tile.disabled_reason ?? "switched off");
  if (tile.setup) return unknown(tile.setup);
  const stats = find(modules, channel, "stats");
  if (!stats) return unknown("its check is not reporting");
  if (stats.state) return unknown(stats.state.label);
  if (stats.stale) return unknown("last reading too old to count");
  if (normaliseHealth(stats.health) !== "ok") return unknown(firstClause(stats.detail) || "not OK");
  const m = NUMBERS.exec(stats.detail);
  if (!m) return { state: "ok-nonumbers", text: safeText(stats.detail), readAt: stats.observed_at };
  const videos = find(modules, channel, "latest_videos");
  const vOk = videos && !videos.stale && !videos.state && normaliseHealth(videos.health) === "ok"
    && typeof videos.value === "number";
  return {
    state: "ok", subscribers: num(m[1]), views: num(m[2]), readAt: stats.observed_at,
    latestVideos: vOk ? videos.value ?? null : null,
    latestReadAt: vOk ? videos.observed_at : null,
  };
}

export function youtubeToken(modules: ModuleTile[] | null, channel: string): TokenReading | null {
  if (modules === null) return null;
  const c = find(modules, channel, "token");
  if (!c) return null;
  const health: Health = c.stale || c.state ? "unknown" : normaliseHealth(c.health);
  return { health, readAt: c.observed_at, detail: health === "ok" ? "" : firstClause(c.detail) };
}

const LABELS: Record<string, string> = {
  token: "Token", stats: "Channel stats", latest_videos: "Latest videos",
};

/** A channel check's fixed display label (its name carries the key). */
export function channelCheckLabel(name: string, channel = "clips"): string {
  const at = name.toLowerCase().indexOf(`${channel}_`);
  return LABELS[at < 0 ? name : name.slice(at + channel.length + 1)] ?? "Channel check";
}

export interface AcademyCheck {
  key: string;
  label: string;
  health: Health;
  readAt: string | null;
  detail: string;
}

/** The channel's own checks only, with fixed display labels. The tile also
 *  carries other lanes' checks: they are never listed. */
export function academyChecks(tile: ModuleTile | undefined, channel: string): AcademyCheck[] {
  const prefix = `${channel}_`;
  return (tile?.checks ?? [])
    .filter((c) => c.name.startsWith(prefix))
    .map((c) => {
      const health: Health = c.stale || c.state ? "unknown" : normaliseHealth(c.health);
      return {
        key: c.name.slice(prefix.length), label: LABELS[c.name.slice(prefix.length)] ?? "Channel check", health,
        readAt: c.observed_at,
        detail: health === "ok" ? "" : c.stale ? "too old to count" : firstClause(c.state?.label ?? c.detail),
      };
    });
}
