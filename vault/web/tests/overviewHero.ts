// The Task 74 half of `OverviewPayload` — the five hero cards' figures and the
// panels under them — as "nothing measured", for a fixture that only cares
// about something else on the page. Zero counts because zero really is zero;
// every sensor-reported figure null because a fixture has not run a scan.
import type { OverviewPayload } from "../src/types";

export const NO_HERO: Pick<
  OverviewPayload,
  "score_trend_7d" | "assets" | "changed_24h" | "incidents" | "incident_briefs" | "recs" | "health"
  | "paths" | "cameras"
> = {
  score_trend_7d: { name: "score", points: [], color: null },
  assets: { total: 0, servers: 0, sites: 0 },
  changed_24h: 0,
  incidents: { open: 0, crit: 0, warn: 0 },
  incident_briefs: [],
  recs: { open: 0, critical: 0, high: 0, top: "" },
  health: {
    sensor_state: "unknown",
    run_seconds: null,
    scans_last_hour: null,
    sensors_active: 0,
    sensors_total: 0,
    feeds: [],
    last_event_ts: "",
    last_event_age_s: null,
    autofix_enabled: false,
    autofix_24h: 0,
    notify_channel: "",
    dns_filter_state: "off",
    dns_blocked_24h: 0,
  },
  paths: [],
  cameras: { known: 0, online: 0, expected: 0, exposed: false, exposed_ports: [], gaps: [] },
};
