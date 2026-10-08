import { useMemo, useState } from "react";
import type { PatchDay } from "../types";
import { EmptyState } from "./EmptyState";
import { day as fmtDay, plural } from "../lib/format";
import type { Tone } from "../lib/format";

/** This calendar's own four states, from `payloads/updates.py`: `patched`,
 *  `reboot`, `failed`, `none`.
 *
 *  It used to ask `stateTone`, the shared state->tone map -- which knows
 *  `ok`/`down`/`stale` and friends but not one of these except `failed`. So
 *  three of the four fell through to "plain" and a day a host was patched
 *  looked exactly like a day nothing happened: the whole calendar rendered
 *  as one flat colour, which is what Miro was looking at. A grid whose
 *  vocabulary is its own needs its own map. */
const PATCH_TONES: Record<string, Tone> = {
  patched: "good",
  reboot: "warn",   // it took a reboot: real work, and worth seeing
  failed: "alert",
  none: "plain",
};

export type PatchCalendarProps = {
  calendar: PatchDay[];
};

/** One row per host, one column per day — a `Heatmap`-shaped grid, but cell
 *  tone comes from `PatchDay.state` (a bare string, not a 0–100 number) and
 *  every cell that had a reboot carries a small dot, so this is its own
 *  component rather than a reuse of the numeric `Heatmap` primitive.
 *  Positioned by CSS grid line rather than a dense row×col loop, so exactly
 *  one cell renders per `calendar` entry — no filler cell for a host/day
 *  combination the hub never sent. */
export function PatchCalendar({ calendar }: PatchCalendarProps) {
  const [hover, setHover] = useState<PatchDay | null>(null);

  const hosts = useMemo(() => Array.from(new Set(calendar.map((c) => c.host))).sort(), [calendar]);
  const days = useMemo(() => Array.from(new Set(calendar.map((c) => c.day))).sort(), [calendar]);

  const hostIndex = useMemo(() => new Map(hosts.map((h, i) => [h, i])), [hosts]);
  const dayIndex = useMemo(() => new Map(days.map((d, i) => [d, i])), [days]);

  if (calendar.length === 0) {
    return (
      <EmptyState
        title="No patch history recorded yet."
        body="Once a host applies its first patch or takes a reboot, its calendar row shows up here."
      />
    );
  }

  function readout(c: PatchDay): string {
    return `${plural(c.patches, "patch", "es")} · ${plural(c.reboots, "reboot")} · ${fmtDay(c.day)}`;
  }

  return (
    <div className="g-patchcal">
      <div
        className="g-patchcal-grid"
        style={{
          gridTemplateColumns: `120px repeat(${days.length}, minmax(20px, 1fr))`,
          gridTemplateRows: `24px repeat(${hosts.length}, 24px)`,
        }}
      >
        {days.map((d, i) => (
          <div key={d} className="g-patchcal-daylabel" style={{ gridColumn: i + 2, gridRow: 1 }}>
            {i % 2 === 0 ? fmtDay(d) : ""}
          </div>
        ))}
        {hosts.map((h, i) => (
          <div key={h} className="g-patchcal-hostlabel" style={{ gridColumn: 1, gridRow: i + 2 }} title={h}>
            {h}
          </div>
        ))}
        {calendar.map((c) => {
          const col = (dayIndex.get(c.day) ?? 0) + 2;
          const row = (hostIndex.get(c.host) ?? 0) + 2;
          const tone = PATCH_TONES[c.state] ?? "plain";
          const label = `${c.host}, ${readout(c)}`;
          return (
            <button
              key={`${c.host}|${c.day}`}
              type="button"
              className={`g-patchcal-cell g-tone-${tone}`}
              data-state={c.state}
              data-testid="patch-cell"
              style={{ gridColumn: col, gridRow: row }}
              title={label}
              aria-label={label}
              onMouseEnter={() => setHover(c)}
              onFocus={() => setHover(c)}
              onMouseLeave={() => setHover(null)}
              onBlur={() => setHover(null)}
            >
              {c.reboots > 0 && (
                <span className="g-patchcal-reboot" data-testid="reboot-mark" aria-hidden="true" />
              )}
            </button>
          );
        })}
      </div>
      <div className="g-patchcal-foot">
        {/* Colour is the whole point of a calendar; unlabelled colour is a
            puzzle. */}
        <ul className="g-patchcal-key" aria-label="What the colours mean">
          <li><span className="g-patchcal-swatch g-tone-good" aria-hidden="true" />Patched</li>
          <li><span className="g-patchcal-swatch g-tone-warn" aria-hidden="true" />Rebooted</li>
          <li><span className="g-patchcal-swatch g-tone-alert" aria-hidden="true" />Failed</li>
          <li><span className="g-patchcal-swatch g-patchcal-swatch-none" aria-hidden="true" />Nothing that day</li>
        </ul>
        <p className="g-patchcal-readout" role="status">
          {hover ? `${hover.host} · ${readout(hover)}` : "Hover or focus a cell for detail."}
        </p>
      </div>
    </div>
  );
}

export default PatchCalendar;
