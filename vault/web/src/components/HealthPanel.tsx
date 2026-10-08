import type { ReactNode } from "react";
import { Badge } from "../design";
import { age } from "../lib/format";
import type { GuardianHealth } from "../types";

export type HealthPanelProps = {
  health: GuardianHealth;
  className?: string;
};

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="g-health-kv-row">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

/** Proof that the watchdog is watching: sensor state and how long its scan
 *  took, how many sensors are reporting, the feed pills, the last event,
 *  auto-fix, notifications and the DNS filter. Pure.
 *
 *  Anything the sensor did not report reads "—" rather than a zero: 0 s would
 *  claim an instant scan and 0 scans would claim a dead sensor. */
export function HealthPanel({ health: h, className = "" }: HealthPanelProps) {
  const live = h.sensor_state === "online";
  return (
    <dl className={`g-health-kv ${className}`.trim()}>
      <Row label="Sensor">
        <Badge tone={live ? "good" : h.sensor_state === "silent" ? "alert" : "plain"}>
          {live ? "online" : h.sensor_state === "silent" ? "silent" : "no scan yet"}
        </Badge>{" "}
        scan took {h.run_seconds === null ? "—" : `${h.run_seconds} s`}
        {h.scans_last_hour !== null && ` · ${h.scans_last_hour} in the last hour`}
      </Row>

      <Row label="Reporting">
        {h.sensors_total === 0 ? (
          <Badge tone="warn">no sensor enrolled</Badge>
        ) : (
          <>
            <Badge tone={h.sensors_active === h.sensors_total ? "good" : "warn"}>
              {h.sensors_active} of {h.sensors_total}
            </Badge>{" "}
            {h.sensors_total === 1 ? "sensor" : "sensors"} active
          </>
        )}
      </Row>

      <Row label="Feeds">
        {h.feeds.length === 0 ? (
          <span className="hint">nothing reported yet</span>
        ) : (
          <span className="g-feed-pills">
            {h.feeds.map((f) => (
              <Badge key={f.name} tone={f.ok ? "good" : "plain"}>
                {f.name}
              </Badge>
            ))}
          </span>
        )}
      </Row>

      <Row label="Last event">
        {h.last_event_age_s === null ? <span className="hint">none recorded</span> : `${age(h.last_event_age_s)} ago`}
      </Row>

      <Row label="Auto-fix">
        {h.autofix_enabled ? (
          <>
            <Badge tone="good">on</Badge> {h.autofix_24h} {h.autofix_24h === 1 ? "action" : "actions"} / 24 h
          </>
        ) : (
          <Badge tone="warn">off</Badge>
        )}
      </Row>

      <Row label="Notifications">
        {h.notify_channel === "" ? (
          <Badge tone="warn">no channel</Badge>
        ) : (
          <Badge tone="good">{h.notify_channel}</Badge>
        )}
      </Row>

      <Row label="DNS filter">
        {h.dns_filter_state === "on" ? (
          <>
            <Badge tone="good">on</Badge> {h.dns_blocked_24h} blocked / 24 h
          </>
        ) : h.dns_filter_state === "down" ? (
          <Badge tone="alert">down</Badge>
        ) : (
          <Badge tone="plain">off</Badge>
        )}
      </Row>
    </dl>
  );
}

export default HealthPanel;
