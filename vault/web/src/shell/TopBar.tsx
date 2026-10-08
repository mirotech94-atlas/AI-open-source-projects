import { useEffect, useState } from "react";
import type { MeOut } from "../api";
import Logo from "../Logo";
import { gate } from "../lib/auth";
import type { Role } from "../lib/auth";
import { age } from "../lib/format";
import { modLabel } from "../lib/keys";
import type { Theme } from "../lib/theme";
import type { LivePayload, OverviewPayload } from "../types";
import { OverflowMenu } from "./OverflowMenu";
import { Sparkline } from "./Sparkline";

/** Where a queued scan has got to. `queued` and `running` come from the hub
 *  (the queue row, echoed by the live feed's `scan` frame); `done` is the
 *  moment the feed reports a finished scan and is cleared on a timer so the
 *  button does not sit on a stale success forever. */
export type ScanState = "idle" | "queued" | "running" | "done";

export type TopBarProps = {
  me: MeOut | null;
  role: Role | null;
  overview: OverviewPayload | null;
  overviewFetchedAt: number | null;
  live: LivePayload | null;
  scanState: ScanState;
  maintenanceUntil: string | null;
  advanced: boolean;
  onToggleAdvanced: () => void;
  theme: Theme;
  onCycleTheme: () => void;
  onOpenPalette: () => void;
  onOpenWallboard: () => void;
  onRefresh: () => void;
  onScanNow: () => void;
  onSpeedtest: () => void;
  onMaintenance: (hours: number) => void;
  onSignOut: () => void;
};

const THEME_LABEL: Record<string, string> = {
  system: "System",
  light: "Calm",
  dark: "Console",
};

const SCAN_LABEL: Record<ScanState, string> = {
  idle: "Scan now",
  queued: "Queued…",
  running: "Scanning…",
  done: "Scan finished",
};

/** `active` -> green, stale past 15 min -> yellow, past 30 min -> red — the
 *  three tones the product allows, nothing else. A missing sensor reads
 *  plain rather than guessing a colour it cannot justify. */
function sensorTone(ageS: number | null | undefined): "good" | "warn" | "alert" | "plain" {
  if (ageS === null || ageS === undefined) return "plain";
  if (ageS > 1800) return "alert";
  if (ageS > 900) return "warn";
  return "good";
}

function siteTone(ok: boolean | null, ms: number | null): "good" | "warn" | "alert" | "plain" {
  if (ok === false) return "alert";
  if (ok === null) return "plain";
  if (ms !== null && ms >= 1500) return "warn";
  return "good";
}

/** Minutes left on an ISO deadline, or `null` once it has passed. */
function minutesLeft(until: string | null, now: number): number | null {
  if (until === null) return null;
  const at = Date.parse(until);
  if (Number.isNaN(at)) return null;
  const mins = Math.ceil((at - now) / 60_000);
  return mins > 0 ? mins : null;
}

/** "Roots prod (status.example.org)" -> "Roots prod". The reference's header shows the
 *  short name and keeps the full one in the tooltip, which is what lets a row of
 *  pills and the "updated" readout share one line on a laptop screen. */
function shortSiteName(name: string): string {
  const cut = name.indexOf(" (");
  return cut > 0 ? name.slice(0, cut) : name;
}

/** The sub-header: a rail of labelled columns, one per monitored site --
 *  the site's short name over its response time and a latency sparkline
 *  advancing one point per scan (real `LiveSite.spark` history, never a
 *  synthesized line) -- ending with an "updated" column. Renders nothing at
 *  all when no sites are configured. The sensor-host figures do not live
 *  here any more: they sit in the bar's centre status cluster
 *  (`StatusCluster`), so each row has one job -- the bar says what the
 *  sensor is doing, the rail says what the sites are doing. */
function StatusRail({ live, updatedS }: { live: LivePayload | null; updatedS: number | null }) {
  const sites = live?.sites ?? [];
  if (sites.length === 0) return null;
  return (
    <div className="g-status-rail" aria-label="Site response times">
      {sites.map((s) => {
        const tone = siteTone(s.ok, s.ms);
        return (
          <div key={s.name} className={`g-rail-col g-tone-${tone}`} data-testid="site-column">
            <span className="g-rail-label" title={s.name}>{shortSiteName(s.name)}</span>
            <span className="g-rail-value">
              <b className="num g-rail-figure">
                {s.ms === null ? "—" : Math.round(s.ms)}
                <span className="g-rail-unit">ms</span>
              </b>
              <Sparkline points={s.spark} tone={tone} />
            </span>
          </div>
        );
      })}
      <div className="g-rail-col g-rail-col-end" data-testid="rail-updated">
        <span className="g-rail-label">Updated</span>
        <span className="g-rail-value">
          <b className="num g-rail-figure">
            {updatedS === null ? "—" : `${updatedS}s`}
            <span className="g-rail-unit">ago</span>
          </b>
        </span>
      </div>
    </div>
  );
}

/** The bar's centre: what the sensor is doing, as quiet text -- the live
 *  dot, the scan age (the header's ONE per-second motion), scans per hour,
 *  and the sensor host's one-minute load (or its freshness when the sensor
 *  reports no load -- staleness is never lost by that swap, the dot's tone
 *  carries it too). */
function StatusCluster({
  tone,
  pulsing,
  liveAge,
  scansLastHour,
  live,
  sensorAgeS,
  loaded,
}: {
  tone: "good" | "warn" | "alert" | "plain";
  pulsing: boolean;
  liveAge: number | null;
  scansLastHour: number | null | undefined;
  live: LivePayload | null;
  sensorAgeS: number | null;
  /** False until the overview has answered once. Before that `liveAge` is
   *  null because nothing has been read yet, not because no scan exists --
   *  and "no scan yet" is the alarming answer, so it waits for the evidence. */
  loaded: boolean;
}) {
  const load1 = live?.load1 ?? null;
  const cores = live?.cores ?? null;
  const sep = <span className="g-topbar-sep" aria-hidden="true">·</span>;
  return (
    <div className="g-topbar-status" data-testid="topbar-status">
      <span
        className={`g-pulse-dot g-tone-${tone}${pulsing ? " g-pulse-busy" : ""}`}
        role="img"
        aria-label={`Sensor ${liveAge === null ? "unknown" : liveAge >= 900 ? "stale" : "reporting"}${pulsing ? ", scanning" : ""}`}
        title={pulsing ? "A scan is running" : liveAge === null ? "No scan received" : liveAge >= 900 ? "Sensor evidence is stale" : "Sensor recently reported"}
      />
      <span className="g-topbar-scan hint num">
        {liveAge !== null ? `last scan ${age(liveAge)} ago` : loaded ? "no scan yet" : "checking…"}
      </span>
      {scansLastHour !== null && scansLastHour !== undefined && (
        <>
          {sep}
          <span className="g-topbar-scan hint num" title="Scans completed in the last hour">
            {scansLastHour} scans/hr
          </span>
        </>
      )}
      {/* The sensor-load readout and the separator that introduces it, as one
          unit so a narrow bar can drop both together. `display: contents` on
          the wrapper keeps them as direct flex children of the cluster, so
          the row's gaps are unchanged; the 1200px rule in shell.css sets it
          to `none` and takes the pair out in one step. Without that the
          cluster (white-space: nowrap, auto margins) simply grew under the
          actions and the Scan button covered the word "cores". */}
      <span className="g-topbar-load">
      {sep}
      {load1 === null ? (
        // No load figure to show: a sensor that predates it, or a platform with
        // no load average. The slot keeps the freshness readout rather than
        // printing a 0.00 that would read as an idle machine.
        <span className={`g-topbar-scan hint num g-tone-${sensorTone(sensorAgeS)}`} data-testid="sensor-freshness-pill">
          sensor <b className="num">{sensorAgeS === null ? "—" : age(sensorAgeS)}</b>
        </span>
      ) : (
        <span
          className="g-topbar-scan hint num"
          data-testid="sensor-load-pill"
          title={
            cores === null
              ? "One-minute load average on the sensor's own host"
              : `One-minute load average on the sensor's own host, which has ${cores} ${cores === 1 ? "core" : "cores"}`
          }
        >
          sensor load <b className="num">{load1.toFixed(2)}</b>
          {cores !== null && <span className="g-topbar-unit num">/ {cores} {cores === 1 ? "core" : "cores"}</span>}
        </span>
      )}
      </span>
    </div>
  );
}

export function TopBar({
  me,
  role,
  overview,
  overviewFetchedAt,
  live,
  scanState,
  maintenanceUntil,
  advanced,
  onToggleAdvanced,
  theme,
  onCycleTheme,
  onOpenPalette,
  onOpenWallboard,
  onRefresh,
  onScanNow,
  onSpeedtest,
  onMaintenance,
  onSignOut,
}: TopBarProps) {
  const [now, setNow] = useState(() => Date.now());

  // Ticks the "last scan Ns ago" line once a second without a network
  // refetch — `overview.age_s` is the age at `overviewFetchedAt`, and this
  // interval just advances the elapsed-since-fetch part of that math. This is
  // the header's ONE per-second motion (design proposal §3): it is both
  // liveness and the single most important fact in the header, so nothing
  // else here ticks on a plain timer.
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const baseAge = overview?.age_s ?? null;
  const elapsed = overviewFetchedAt !== null ? Math.max(0, Math.floor((now - overviewFetchedAt) / 1000)) : 0;
  const liveAge = baseAge !== null ? baseAge + elapsed : null;

  const sensorAge = overview?.sensor?.age_s ?? live?.sensor_age_s ?? null;
  const tone = sensorTone(sensorAge);
  const busy = scanState === "queued" || scanState === "running" || Boolean(live?.scanning);
  // The dot pulses only while a scan is genuinely running -- `live.scanning`
  // is the Queue row's own `status="running"`, not a decorative interval.
  // `scanState === "running"` is included so the button's own optimistic
  // transition shows immediately rather than waiting out the next poll; a
  // merely `queued` scan (not yet started) does not pulse.
  const pulsing = Boolean(live?.scanning) || scanState === "running";

  const scanGate = gate(role, "operator", "Queue a scan of every site, host and device now");
  const speedGate = gate(role, "operator", "Queue an internet speed test");
  const maintGate = gate(role, "operator", "Pause non-critical alerts for an hour");
  const maintMins = minutesLeft(maintenanceUntil, now);

  const liveTs = live?.ts ? Date.parse(live.ts) : null;
  const updatedS = liveTs !== null && !Number.isNaN(liveTs) ? Math.max(0, Math.floor((now - liveTs) / 1000)) : null;

  const userLine = me !== null ? (me.user.display_name || me.user.username) : null;

  return (
    <>
      <header className="g-topbar" role="banner">
        {/* Zone 1 — identity: logo and name. The sensor state moved to the
            centre cluster below so this zone stops crowding. */}
        <div className="g-topbar-brand">
          <Logo size={24} />
          <h1 className="g-topbar-title">
            <b>Vault</b>
          </h1>
        </div>

        {/* Zone 2 — the centre status cluster: live dot, scan age, scans/hr,
            sensor load. Quiet text, one row, balanced between identity and
            the actions by its own auto margins. */}
        <StatusCluster
          tone={tone}
          pulsing={pulsing}
          liveAge={liveAge}
          scansLastHour={overview?.health.scans_last_hour}
          live={live}
          sensorAgeS={sensorAge}
          loaded={overview !== undefined && overview !== null}
        />

        {/* Which account you are in, and as what, moved to the sidebar: it is
            who you are rather than what is happening, and repeating it here
            cost the header its middle third to a value that never changes
            during a session. Two elastic spacers went with it -- they were
            what spread three zones across an otherwise empty bar. */}

        {/* Zone 3 — action: the two conditional escapes, `Scan now` as the
            only filled button, search, and the overflow menu. */}
        <div className="g-topbar-actions">
          {maintMins !== null && (
            <span
              className="g-topbar-maint g-tone-warn num"
              role="status"
              title="Escapes the overflow menu because it changes what Vault will do"
            >
              ⏸ maintenance · {maintMins}m left
            </span>
          )}

          <button
            type="button"
            className="g-topbar-button g-topbar-button-primary"
            onClick={onScanNow}
            disabled={scanGate.disabled || busy}
            title={scanGate.title}
            aria-live="polite"
          >
            {busy ? SCAN_LABEL[scanState === "idle" ? "running" : scanState] : SCAN_LABEL[scanState]}
          </button>

          <button
            type="button"
            className="g-topbar-search"
            onClick={onOpenPalette}
            aria-label="Search devices, hosts, sites, alerts"
          >
            <svg className="g-topbar-search-icon" viewBox="0 0 20 20" width={16} height={16} aria-hidden="true">
              <circle cx="8.5" cy="8.5" r="6" fill="none" stroke="currentColor" strokeWidth="1.5" />
              <path d="M13 13 L17.5 17.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
            <kbd className="g-topbar-search-kbd">{modLabel()}K</kbd>
          </button>

          <OverflowMenu
            onRefresh={onRefresh}
            speedtest={{ ...speedGate, onClick: onSpeedtest }}
            maintenance={{
              ...maintGate,
              active: maintMins !== null,
              minutesLeft: maintMins,
              onStart: () => onMaintenance(1),
              onEnd: () => onMaintenance(0),
            }}
            advanced={{ on: advanced, onToggle: onToggleAdvanced }}
            onOpenWallboard={onOpenWallboard}
            themeLabel={THEME_LABEL[theme]}
            onCycleTheme={onCycleTheme}
            userLine={userLine}
            onSignOut={onSignOut}
          />
        </div>
      </header>

      <StatusRail live={live} updatedS={updatedS} />
    </>
  );
}

export default TopBar;
