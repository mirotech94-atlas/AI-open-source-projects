import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useToast } from "../../design";
import { hostsPayload, saveSettingsSection, sitesPayload } from "../../lib/api";
import { atLeast } from "../../lib/auth";
import type { Role } from "../../lib/auth";
import type { HostCard, SettingsPayload, SiteCard } from "../../types";
import { pct } from "../../lib/format";
import { ADMIN_TOOLTIP, RoleNotice } from "./shared";

export type ThresholdsProps = {
  settings: SettingsPayload;
  role: Role | null;
  onInvalidate: () => void;
};

type ThresholdDef = {
  key: string;
  label: string;
  min: number;
  max: number;
  step: number;
  unit: string;
  /** What this threshold is worth when the tenant has never set it — the
   *  sensor's own default from `guardian_sensor/config.py` DEFAULTS.
   *
   *  This field is load-bearing and must never go back to being `min`. The
   *  form seeds every input from the saved config, and submitting sends
   *  every input, so whatever is displayed for an unset threshold is what
   *  gets written. Seeding from `min` meant that opening this page and
   *  pressing Save silently ratcheted every threshold the owner had not set
   *  down to the lowest value the slider allows. That is how WEME's config
   *  acquired `latency_warn_s: 0.2`.
   *
   *  A tenant created after `tenant_defaults` landed already has explicit
   *  values, so this only shows through for tenants older than that — but
   *  for those it is the value actually in force. */
  fallback: number;
  /** The current live value, worded exactly like "prod is at 56%" —
   *  `null` when there is nothing to compare it against yet. */
  live: (hosts: HostCard[], sites: SiteCard[]) => { name: string; text: string } | null;
};

function worstHost(hosts: HostCard[], pick: (h: HostCard) => number | null): { name: string; value: number } | null {
  let best: { name: string; value: number } | null = null;
  for (const h of hosts) {
    const v = pick(h);
    if (v === null) continue;
    if (best === null || v > best.value) best = { name: h.name, value: v };
  }
  return best;
}

// The hub's own ranges (Task 39: `POST /settings/thresholds` validation).
const THRESHOLD_DEFS: ThresholdDef[] = [
  {
    key: "disk_pct_warn",
    label: "Disk warn",
    min: 50,
    max: 99,
    step: 1,
    unit: "%",
    fallback: 80,
    live: (hosts) => {
      const w = worstHost(hosts, (h) => h.disk_pct);
      return w ? { name: w.name, text: `${w.name} is at ${pct(w.value)}` } : null;
    },
  },
  {
    key: "mem_pct_warn",
    label: "Memory warn",
    min: 50,
    max: 99,
    step: 1,
    unit: "%",
    fallback: 90,
    live: (hosts) => {
      const w = worstHost(hosts, (h) => h.mem_pct);
      return w ? { name: w.name, text: `${w.name} is at ${pct(w.value)}` } : null;
    },
  },
  {
    key: "load_per_core_warn",
    label: "Load per core warn",
    min: 0.5,
    max: 8,
    step: 0.1,
    unit: "",
    fallback: 2,
    live: (hosts) => {
      const w = worstHost(hosts, (h) => h.load_per_core);
      return w ? { name: w.name, text: `${w.name} is at ${w.value.toFixed(2)}` } : null;
    },
  },
  {
    key: "cert_days_warn",
    label: "Certificate days warn",
    min: 1,
    max: 120,
    step: 1,
    unit: "d",
    fallback: 21,
    live: (_hosts, sites) => {
      let worst: { name: string; value: number } | null = null;
      for (const s of sites) {
        if (s.cert_days === null) continue;
        if (worst === null || s.cert_days < worst.value) worst = { name: s.name, value: s.cert_days };
      }
      return worst ? { name: worst.name, text: `${worst.name} has ${worst.value}d left` } : null;
    },
  },
  {
    key: "http_latency_warn_s",
    label: "Latency warn",
    min: 0.2,
    max: 30,
    step: 0.1,
    unit: "s",
    fallback: 4,
    live: (_hosts, sites) => {
      const w = sites.reduce<{ name: string; value: number } | null>((acc, s) => {
        if (s.ms === null) return acc;
        return acc === null || s.ms > acc.value ? { name: s.name, value: s.ms } : acc;
      }, null);
      return w ? { name: w.name, text: `${w.name} is at ${(w.value / 1000).toFixed(2)}s` } : null;
    },
  },
];

/** Alert thresholds: a number input per known threshold, with the hub's own
 *  min/max as the input's range and the current worst live value shown
 *  beside it — "disk warn 80% · prod is at 56%" — so an admin sees the
 *  actual effect of a change before saving it. */
export function Thresholds({ settings, role, onInvalidate }: ThresholdsProps) {
  const toast = useToast();
  const canWrite = atLeast(role, "admin");

  const hostsQuery = useQuery({ queryKey: ["hosts"], queryFn: hostsPayload });
  const sitesQuery = useQuery({ queryKey: ["sites"], queryFn: sitesPayload });
  const hosts = hostsQuery.data?.hosts ?? [];
  const sites = sitesQuery.data?.sites ?? [];

  const [values, setValues] = useState<Record<string, number>>(() => {
    const initial: Record<string, number> = {};
    for (const def of THRESHOLD_DEFS) {
      initial[def.key] = settings.thresholds[def.key] ?? def.fallback;
    }
    return initial;
  });
  const [errors, setErrors] = useState<Record<string, string>>({});

  const mutation = useMutation({
    mutationFn: (body: Record<string, number>) => saveSettingsSection("thresholds", body),
    onSuccess: () => {
      onInvalidate();
      toast.show("Thresholds saved.", "good");
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const nextErrors: Record<string, string> = {};
    for (const def of THRESHOLD_DEFS) {
      const v = values[def.key];
      if (Number.isNaN(v) || v < def.min || v > def.max) {
        nextErrors[def.key] = `Enter a value between ${def.min}${def.unit} and ${def.max}${def.unit}.`;
      }
    }
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length === 0) mutation.mutate(values);
  }

  return (
    <form className="g-settings-thresholds" onSubmit={onSubmit} noValidate>
      {!canWrite && <RoleNotice role={role} required="admin" />}

      {THRESHOLD_DEFS.map((def) => {
        const live = def.live(hosts, sites);
        const fieldId = `settings-threshold-${def.key}`;
        return (
          <div key={def.key} className="g-settings-threshold-row">
            <label htmlFor={fieldId}>{def.label}</label>
            <input
              id={fieldId}
              type="number"
              min={def.min}
              max={def.max}
              step={def.step}
              value={values[def.key]}
              disabled={!canWrite}
              title={canWrite ? undefined : ADMIN_TOOLTIP}
              aria-invalid={errors[def.key] ? true : undefined}
              onChange={(e) => setValues((prev) => ({ ...prev, [def.key]: Number(e.target.value) }))}
            />
            <span className="g-settings-threshold-live">
              {def.label.toLowerCase()} {values[def.key]}
              {def.unit} {live && `· ${live.text}`}
            </span>
            {errors[def.key] && <p className="g-field-error">{errors[def.key]}</p>}
          </div>
        );
      })}

      <button type="submit" disabled={!canWrite || mutation.isPending} title={canWrite ? undefined : ADMIN_TOOLTIP}>
        Save
      </button>
    </form>
  );
}

export default Thresholds;
