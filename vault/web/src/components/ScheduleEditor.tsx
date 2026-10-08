import { useEffect, useId, useRef, useState } from "react";
import type { ReportPreset, ReportSchedule } from "../types";

export type ScheduleEditorProps = {
  schedule: ReportSchedule;
  presets: ReportPreset[];
  onSave: (schedule: ReportSchedule) => void;
  canWrite?: boolean;
  writeTooltip?: string;
  saving?: boolean;
};

const HOURS = Array.from({ length: 24 }, (_, h) => h);

/** Weekly (every Monday) / monthly (the 1st) toggles, a preset, the hour to
 *  run at, and whether the finished link should also push through the
 *  alert channels — saves through `POST /api/v1/reports/schedule`. Both
 *  toggles can be on at once (a tenant can want both cadences); the hub
 *  scheduler treats them independently. */
export function ScheduleEditor({
  schedule,
  presets,
  onSave,
  canWrite = true,
  writeTooltip = "admin role required",
  saving = false,
}: ScheduleEditorProps) {
  const presetId = useId();
  const hourId = useId();

  const [weekly, setWeekly] = useState(schedule.weekly);
  const [monthly, setMonthly] = useState(schedule.monthly);
  const [preset, setPreset] = useState(schedule.preset);
  const [hour, setHour] = useState(schedule.hour);
  const [ntfy, setNtfy] = useState(schedule.ntfy);

  // `Reports.tsx` mounts this component immediately against the empty
  // placeholder payload (`schedule: {weekly: false, ...}`, `presets: []`)
  // while the real `["reports"]` query is still loading, so the `useState`
  // initializers above seed the tenant's real schedule with placeholder
  // defaults. `presets` arrives from the same payload and is empty only
  // for that placeholder, so use it going from empty to populated as the
  // signal that `schedule` now holds real data, and re-seed from it once —
  // guarded by `seededRef` so a later prop change never overwrites an edit
  // the user already made.
  const seededRef = useRef(false);
  useEffect(() => {
    if (seededRef.current || presets.length === 0) return;
    seededRef.current = true;
    setWeekly(schedule.weekly);
    setMonthly(schedule.monthly);
    setPreset(schedule.preset);
    setHour(schedule.hour);
    setNtfy(schedule.ntfy);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [presets]);

  return (
    <div className="g-reports-schedule">
      <label className="g-reports-toggle">
        <input
          type="checkbox"
          checked={weekly}
          disabled={!canWrite}
          title={canWrite ? undefined : writeTooltip}
          onChange={(e) => setWeekly(e.target.checked)}
        />
        <span>Weekly, every Monday</span>
      </label>

      <label className="g-reports-toggle">
        <input
          type="checkbox"
          checked={monthly}
          disabled={!canWrite}
          title={canWrite ? undefined : writeTooltip}
          onChange={(e) => setMonthly(e.target.checked)}
        />
        <span>Monthly, on the 1st</span>
      </label>

      <label className="g-reports-field">
        <span id={presetId}>Report template</span>
        <select
          aria-labelledby={presetId}
          value={preset}
          disabled={!canWrite}
          title={canWrite ? undefined : writeTooltip}
          onChange={(e) => setPreset(e.target.value)}
        >
          {presets.map((p) => (
            <option key={p.key} value={p.key}>
              {p.label}
            </option>
          ))}
        </select>
      </label>

      <label className="g-reports-field">
        <span id={hourId}>Schedule hour</span>
        <select
          aria-labelledby={hourId}
          value={hour}
          disabled={!canWrite}
          title={canWrite ? undefined : writeTooltip}
          onChange={(e) => setHour(Number(e.target.value))}
        >
          {HOURS.map((h) => (
            <option key={h} value={h}>
              {h}:00
            </option>
          ))}
        </select>
      </label>

      <label className="g-reports-toggle">
        <input
          type="checkbox"
          checked={ntfy}
          disabled={!canWrite}
          title={canWrite ? undefined : writeTooltip}
          onChange={(e) => setNtfy(e.target.checked)}
        />
        <span>Push the link to my alert channels</span>
      </label>

      <button
        type="button"
        disabled={!canWrite || saving}
        title={canWrite ? undefined : writeTooltip}
        onClick={() => onSave({ weekly, monthly, preset, hour, ntfy })}
      >
        {saving ? "Saving…" : "Save schedule"}
      </button>
    </div>
  );
}

export default ScheduleEditor;
