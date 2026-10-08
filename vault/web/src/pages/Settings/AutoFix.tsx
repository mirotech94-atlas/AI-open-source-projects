import { ExceptionPolicy } from "../../components/ExceptionPolicy";
import { AutoFixOverrides } from "../../components/AutoFixOverrides";
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useToast } from "../../design";
import { saveSettingsSection } from "../../lib/api";
import { atLeast } from "../../lib/auth";
import type { Role } from "../../lib/auth";
import type { AutoFixPolicy, MaintenanceWindow, SettingsPayload } from "../../types";
import { ADMIN_TOOLTIP, ConfirmDialog, RoleNotice } from "./shared";

export type AutoFixProps = {
  settings: SettingsPayload;
  role: Role | null;
  onInvalidate: () => void;
};

// Ports every method the sensor's fix module accepts for an attacker IP
// (`guardian-sensor`'s `fix` module — `auto` lets the sensor pick per its own
// cooldown/severity rules). Not part of any payload; the hub has nowhere
// else that lists these, so this is the one place they're spelled out.
const ATTACKER_METHODS = ["auto", "block_ip", "block_subnet", "ban_long", "limit_ssh", "off"];

function splitWindow(window: MaintenanceWindow | null): [string, string] {
  return [window?.start ?? "02:00", window?.end ?? "05:00"];
}

/** Auto-fix policy: the kill switch, the daily cap, the attacker-method
 *  select, the maintenance window, and a read-only list of current
 *  cooldowns. Turning the switch off needs confirmation — an unattended
 *  fixer going quiet is the kind of change nobody should make with one
 *  misplaced click. */
export function AutoFix({ settings, role, onInvalidate }: AutoFixProps) {
  const toast = useToast();
  const canWrite = atLeast(role, "admin");
  const policy = settings.autofix;
  const [start0, end0] = splitWindow(policy.maintenance_window);

  const [maxPerDay, setMaxPerDay] = useState(policy.max_per_day);
  const [method, setMethod] = useState(policy.attacker_method);
  const [start, setStart] = useState(start0);
  const [end, setEnd] = useState(end0);
  const [confirmOff, setConfirmOff] = useState(false);

  const mutation = useMutation({
    mutationFn: (body: Partial<AutoFixPolicy> & { maintenance_window: MaintenanceWindow }) =>
      saveSettingsSection("autofix", body),
    onSuccess: () => {
      onInvalidate();
      toast.show("Auto-fix policy saved.", "good");
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  function save(overrides: Partial<{ enabled: boolean }> = {}) {
    mutation.mutate({
      enabled: overrides.enabled ?? policy.enabled,
      max_per_day: maxPerDay,
      attacker_method: method,
      maintenance_window: { start, end },
    });
  }

  function onToggle() {
    if (!canWrite) return;
    if (policy.enabled) {
      setConfirmOff(true);
    } else {
      save({ enabled: true });
    }
  }

  const cooldownEntries = Object.entries(policy.cooldowns);

  return (
    <div className="g-settings-autofix">
      {!canWrite && <RoleNotice role={role} required="admin" />}

      <div className="g-settings-killswitch">
        <button
          type="button"
          role="switch"
          aria-checked={policy.enabled}
          aria-label="Automatic fixes"
          disabled={!canWrite}
          title={canWrite ? undefined : ADMIN_TOOLTIP}
          className={policy.enabled ? "g-toggle-on" : undefined}
          onClick={onToggle}
        >
          {policy.enabled ? "Automatic fixes: on" : "Automatic fixes: off"}
        </button>
      </div>

      <label htmlFor="settings-autofix-cap">Daily cap</label>
      <input
        id="settings-autofix-cap"
        type="number"
        min={1}
        value={maxPerDay}
        disabled={!canWrite}
        title={canWrite ? undefined : ADMIN_TOOLTIP}
        onChange={(e) => setMaxPerDay(Number(e.target.value))}
      />

      <label htmlFor="settings-autofix-method">Attacker method</label>
      <select
        id="settings-autofix-method"
        value={method}
        disabled={!canWrite}
        title={canWrite ? undefined : ADMIN_TOOLTIP}
        onChange={(e) => setMethod(e.target.value)}
      >
        {ATTACKER_METHODS.map((m) => (
          <option key={m} value={m}>
            {m}
          </option>
        ))}
      </select>

      <fieldset className="g-settings-window">
        <legend>Maintenance window</legend>
        <label htmlFor="settings-autofix-start">Start</label>
        <input
          id="settings-autofix-start"
          type="time"
          value={start}
          disabled={!canWrite}
          title={canWrite ? undefined : ADMIN_TOOLTIP}
          onChange={(e) => setStart(e.target.value)}
        />
        <label htmlFor="settings-autofix-end">End</label>
        <input
          id="settings-autofix-end"
          type="time"
          value={end}
          disabled={!canWrite}
          title={canWrite ? undefined : ADMIN_TOOLTIP}
          onChange={(e) => setEnd(e.target.value)}
        />
      </fieldset>

      <button type="button" onClick={() => save()} disabled={!canWrite || mutation.isPending} title={canWrite ? undefined : ADMIN_TOOLTIP}>
        Save
      </button>

      <div className="g-settings-cooldowns">
        <h3>Current cooldowns</h3>
        {cooldownEntries.length === 0 ? (
          <p className="hint">No fixes are cooling down.</p>
        ) : (
          <ul>
            {cooldownEntries.map(([key, seconds]) => (
              <li key={key}>
                <span>{key}</span>
                <span className="mono">{seconds}s</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <AutoFixOverrides canWrite={canWrite} />
      <ExceptionPolicy canWrite={canWrite} />
      <ConfirmDialog
        open={confirmOff}
        title="Turn off automatic fixes?"
        body="Vault will stop fixing things on its own until you turn this back on."
        confirmLabel="Turn it off"
        onCancel={() => setConfirmOff(false)}
        onConfirm={() => {
          setConfirmOff(false);
          save({ enabled: false });
        }}
      />
    </div>
  );
}

export default AutoFix;
