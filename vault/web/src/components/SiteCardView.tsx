import type { HTMLAttributes } from "react";
import { Badge, Sparkline } from "../design";
import { ms, pct } from "../lib/format";
import type { SiteCard } from "../types";

export type SiteCardViewProps = {
  site: SiteCard;
  /** The tenant's `thresholds.cert_days_warn` — a cert with fewer days left
   *  than this reads warn/alert instead of good. */
  certWarnDays: number;
  onOpen: () => void;
  className?: string;
} & Omit<HTMLAttributes<HTMLDivElement>, "children" | "onClick">;

type HeaderPill = { key: string; label: string; ok: boolean };

/** The six security-header pills, in the order the spec lists them. `old_tls`
 *  is inverted — the hub reports whether an *old* TLS version is in use, so
 *  "ok" here means it is *not* in use. */
function headerPills(headers: SiteCard["headers"]): HeaderPill[] {
  return [
    { key: "hsts", label: "HSTS", ok: headers.hsts },
    { key: "nosniff", label: "No-sniff", ok: headers.nosniff },
    { key: "frame", label: "Frame", ok: headers.frame },
    { key: "csp", label: "CSP", ok: headers.csp },
    { key: "https_redirect", label: "HTTPS", ok: headers.https_redirect },
    { key: "old_tls", label: "TLS", ok: !headers.old_tls },
  ];
}

function certTone(days: number | null, warnDays: number): "plain" | "good" | "warn" | "alert" {
  if (days === null) return "plain";
  if (days <= 0) return "alert";
  if (days <= warnDays) return "warn";
  return "good";
}

/** The cert fact, in words -- 9 days and 9 months read as materially
 *  different situations, so the wording changes with the tier rather than
 *  just the badge colour: an expired cert says so plainly, a cert inside the
 *  warn window counts down, and a cert with months of runway gets a quieter
 *  "valid for" that does not compete for attention with anything urgent. */
function certHeadline(days: number | null, warnDays: number): string {
  if (days === null) return "No certificate on file";
  if (days <= 0) return "Certificate has expired";
  if (days <= warnDays) return `Certificate renews in ${days} day${days === 1 ? "" : "s"}`;
  if (days >= 60) return `Certificate valid for ${Math.round(days / 30)} more months`;
  return `Certificate valid for ${days} more days`;
}

export function SiteCardView({ site, certWarnDays, onOpen, className = "", ...rest }: SiteCardViewProps) {
  // A row the sensor has stopped reporting must not keep wearing a confident
  // "up" badge: the up/down verdict is exactly as old as everything else on
  // the card. Staleness drops the tone to neutral and renames the badge, so
  // the card stops asserting a current state it cannot know.
  const liveTone = site.ok === false ? "alert" : site.ok === null ? "plain" : "good";
  const tone = site.stale ? "plain" : liveTone;
  const cTone = site.stale ? "plain" : certTone(site.cert_days, certWarnDays);

  return (
    <div
      role="button"
      tabIndex={0}
      data-testid="site-card"
      className={`g-asset-card g-site-card g-tone-${tone}${site.stale ? " g-asset-stale" : ""} ${className}`.trim()}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen();
        }
      }}
      {...rest}
    >
      <div className="g-asset-card-head">
        <span className="g-asset-card-name">{site.name}</span>
        <Badge tone={tone}>
          {site.stale ? "not checked" : site.ok === false ? `down · ${site.code ?? "—"}` : site.ok === null ? "unknown" : "up"}
        </Badge>
      </div>
      <p className="g-asset-card-url">{site.url}</p>

      {site.stale && (
        <p className="g-asset-stale-note" data-testid="site-stale-note">
          {site.stale_note}
        </p>
      )}

      {/* The headline fact for a website: a cert expiring in 9 days and one
          expiring in 9 months are different situations, so the wording (not
          just a colour) carries the distinction, and it sits above the
          uptime figures rather than buried in a footer pill. */}
      <p className={`g-site-cert-headline g-tone-${cTone}`}>{certHeadline(site.cert_days, certWarnDays)}</p>

      <div className="g-asset-stat-row">
        {/* The check count sits under each figure because an availability
            percentage without its denominator cannot be checked by the
            person reading it -- 100% over 21 checks and 100% over 2,000 are
            different claims. Below the minimum the hub sends null rather
            than a percentage, and the count explains the dash instead of
            leaving it looking like a bug. */}
        <div className="g-asset-stat">
          <span className="g-asset-stat-label">Uptime 24h</span>
          <span className="g-asset-stat-value">{pct(site.uptime_24h)}</span>
          <span className="g-asset-stat-note hint" data-testid="uptime-24h-checks">
            {site.uptime_24h === null
              ? `${site.uptime_24h_checks} checks — too few`
              : `${site.uptime_24h_checks} checks`}
            {site.uptime_24h_planned > 0 &&
              ` · ${site.uptime_24h_planned} planned, excluded`}
          </span>
        </div>
        <div className="g-asset-stat">
          <span className="g-asset-stat-label">Uptime 7d</span>
          <span className="g-asset-stat-value">{pct(site.uptime_7d)}</span>
          <span className="g-asset-stat-note hint" data-testid="uptime-7d-checks">
            {site.uptime_7d === null
              ? `${site.uptime_7d_checks} checks — too few`
              : `${site.uptime_7d_checks} checks`}
            {/* Named on the card rather than folded silently into the
                figure. A percentage that improved because someone declared
                a window should say so — an unexplained jump in availability
                is exactly the kind of number a customer stops believing. */}
            {site.uptime_7d_planned > 0 &&
              ` · ${site.uptime_7d_planned} planned, excluded`}
          </span>
        </div>
        <div className="g-asset-stat">
          <span className="g-asset-stat-label">Latency</span>
          <span className="g-asset-stat-value">{ms(site.ms)}</span>
        </div>
      </div>

      <Sparkline points={site.latency_spark} tone={tone === "alert" ? "alert" : "plain"} className="g-asset-spark" />

      <div className="g-asset-card-foot">
        <div className="g-asset-pills">
          {headerPills(site.headers).map((pill) => (
            <span
              key={pill.key}
              className={`g-asset-pill g-tone-${pill.ok ? "good" : "warn"}`}
              title={`${pill.label}: ${pill.ok ? "pass" : "fail"}`}
            >
              {pill.label}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

export default SiteCardView;
