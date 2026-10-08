import type { HTMLAttributes } from "react";
import { Badge } from "../design";
import { plural, ringTone, stateTone } from "../lib/format";
import type { HostCard } from "../types";

export type HostCardViewProps = {
  host: HostCard;
  onOpen: () => void;
  className?: string;
} & Omit<HTMLAttributes<HTMLDivElement>, "children" | "onClick">;

/** The seven hardening checks, joined into one hover readout — the grade
 *  badge itself only has room for the letter. */
function checksTitle(host: HostCard): string {
  return host.hardening.checks.map((c) => `${c.passed ? "✓" : "✗"} ${c.name}`).join("\n");
}

/** The one patch-state fact a person opens this card to check, collapsed to
 *  a single tiered line instead of a scatter of pills — the same "worst
 *  fact wins" idea the website card's cert headline uses, so hosts and
 *  sites feel like siblings rather than the same card with different data
 *  poured in. */
function patchState(host: HostCard): { label: string; tone: "good" | "warn" | "alert" } {
  if (host.security_updates > 0) return { label: `${plural(host.security_updates, "security update")} pending`, tone: "alert" };
  if (host.reboot_required) return { label: "Reboot required", tone: "warn" };
  if (host.pending_updates > 0) return { label: `${plural(host.pending_updates, "update")} pending`, tone: "warn" };
  return { label: "Up to date", tone: "good" };
}

export function HostCardView({ host, onOpen, className = "", ...rest }: HostCardViewProps) {
  // See SiteCardView: a host the sensor has stopped reporting keeps a green
  // reachable badge and a hardening grade that are both as old as the rest of
  // the card. Neither is asserted while the row is stale.
  const liveTone = host.reachable === false ? "alert" : host.reachable === null ? "plain" : "good";
  const tone = host.stale ? "plain" : liveTone;
  const gradeTone = host.stale ? "plain" : ringTone(host.hardening.score);
  const patch = patchState(host);

  return (
    <div
      role="button"
      tabIndex={0}
      data-testid="host-card"
      className={`g-asset-card g-host-card g-tone-${tone}${host.stale ? " g-asset-stale" : ""} ${className}`.trim()}
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
        <span className="g-asset-card-name">{host.name}</span>
        <Badge tone={gradeTone} title={checksTitle(host)}>
          Hardening {host.hardening.grade}
        </Badge>
      </div>
      <p className="g-asset-card-url">{host.address || host.hostname}</p>

      {host.stale && (
        <p className="g-asset-stale-note" data-testid="host-stale-note">
          {host.stale_note}
        </p>
      )}

      {/* The ops inventory reads by column: label on the left, a scannable
          figure on the right, tabular mono so nothing shifts as it updates.
          This is deliberately not the website card's chart — load and disk
          are current-state facts, not a trend a sparkline would earn. */}
      <dl className="g-host-specs">
        {host.gauges.map((gauge) => {
          if (gauge.value === null) {
            return (
              <div className="g-host-spec-row" key={gauge.name}>
                <dt>{gauge.name}</dt>
                <dd>
                  <span className="hint num">—</span>
                </dd>
              </div>
            );
          }
          const pct = gauge.max > 0 ? Math.round((gauge.value / gauge.max) * 100) : 0;
          const gt = pct >= 90 ? "alert" : pct >= 75 ? "warn" : "good";
          return (
            <div className="g-host-spec-row" key={gauge.name}>
              <dt>{gauge.name}</dt>
              <dd>
                <span className="g-host-spec-track" aria-hidden="true">
                  <span className={`g-host-spec-fill g-tone-${gt}`} style={{ width: `${Math.min(100, pct)}%` }} />
                </span>
                <span className="num">{pct}%</span>
              </dd>
            </div>
          );
        })}
      </dl>

      <div className="g-asset-card-foot">
        <Badge tone={patch.tone}>{patch.label}</Badge>
        <span className={`g-asset-ports g-tone-${host.ports.some((p) => p.risky) ? "warn" : "plain"}`}>
          {plural(host.ports.length, "open port")}
        </span>
      </div>

      {host.docker.length > 0 && (
        <ul className="g-asset-minilist" aria-label="Containers">
          {host.docker.map((c) => (
            <li key={c.name} className={`g-tone-${stateTone(c.status)}`}>
              {c.name} · {c.status}
            </li>
          ))}
        </ul>
      )}

      {host.services.length > 0 && (
        <ul className="g-asset-minilist" aria-label="Services">
          {host.services.map((s) => (
            <li key={s.name} className={`g-tone-${stateTone(s.state)}`}>
              {s.name} · {s.state}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default HostCardView;
