import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Table, useToast } from "../../design";
import type { Column } from "../../design";
import { generateEnrolCode } from "../../api";
import { revokeSensor, saveOnboarding } from "../../lib/api";
import { atLeast } from "../../lib/auth";
import type { Role } from "../../lib/auth";
import { installCommand } from "../../lib/install";
import type { SensorRow, SettingsPayload } from "../../types";
import { age, ago } from "../../lib/format";
import { ADMIN_TOOLTIP, ConfirmDialog, RoleNotice } from "./shared";

export type SensorsProps = {
  settings: SettingsPayload;
  role: Role | null;
  onInvalidate: () => void;
  /** Null before `useMe()` resolves. "Re-run setup" waits for it: the wizard
   *  state the button clears is scoped to the active tenant on the hub, so
   *  there is nothing to clear until the tenant is known. */
  tenantId: string | null;
};

const ENROL_TTL_S = 15 * 60;

function secondsUntil(expires: string): number {
  const ms = new Date(expires).getTime() - Date.now();
  return Number.isNaN(ms) ? ENROL_TTL_S : Math.max(0, Math.round(ms / 1000));
}

/** Sensors: status/platform/version/last-seen table, a "Generate enrol
 *  code" button whose result shows the exact copy-able install command with
 *  a countdown to expiry, and a confirm-gated Revoke per row. */
export function Sensors({ settings, role, onInvalidate, tenantId }: SensorsProps) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const canWrite = atLeast(role, "admin");

  // Re-opens the wizard on demand. Clears THIS user's completion on THIS
  // tenant on the hub — the same call the wizard's own "Done" and "Skip for
  // now" write to, so there is one authority rather than a browser flag that
  // disagreed with it — then navigates straight there rather than waiting for
  // `Shell`'s gate to fire on some later load. It re-runs the wizard for the
  // person who pressed it, never for their colleagues.
  const rerunMutation = useMutation({
    mutationFn: () => saveOnboarding({ completed: false, skipped: false, step: "" }),
    onSuccess: (state) => {
      // `Shell` and the wizard share this key at `staleTime: Infinity`, so the
      // cache has to be told too — otherwise the wizard would open and the
      // shell would still believe this user had finished it.
      queryClient.setQueryData(["onboarding"], state);
      navigate("/welcome");
    },
    onError: () => toast.show("Could not re-open setup", "alert"),
  });

  const [enrol, setEnrol] = useState<{ code: string; expires: string } | null>(null);
  const [remaining, setRemaining] = useState(ENROL_TTL_S);
  const [copied, setCopied] = useState(false);
  const [revokeTarget, setRevokeTarget] = useState<SensorRow | null>(null);
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const enrolMutation = useMutation({
    mutationFn: generateEnrolCode,
    onSuccess: (result) => {
      setEnrol({ code: result.code, expires: result.expires });
      // The countdown's starting value is set right here, from the event
      // that produced it — the effect below only keeps it ticking every
      // second afterward, it never computes the initial value itself.
      setRemaining(secondsUntil(result.expires));
      setCopied(false);
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  const revokeMutation = useMutation({
    mutationFn: (id: string) => revokeSensor(id),
    onSuccess: () => {
      setRevokeTarget(null);
      onInvalidate();
      toast.show("Sensor revoked.", "good");
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  useEffect(() => {
    if (!enrol) return;
    const expires = enrol.expires;
    tickRef.current = setInterval(() => setRemaining(secondsUntil(expires)), 1000);
    return () => {
      if (tickRef.current) clearInterval(tickRef.current);
    };
  }, [enrol]);

  // The same `curl <origin>/install | sh` line Onboarding and Networks show
  // (`lib/install.ts`): this panel used to print a bare `guardian-sensor enrol`
  // command, which only works on a host where the package is *already*
  // installed — so the one surface an admin reaches for to add a sensor was
  // the one that didn't tell them how to get it.
  const command = enrol ? installCommand(enrol.code, window.location.origin) : "";

  async function copyCommand() {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard access can be denied; the command stays visible as text.
    }
  }

  const columns: Column<SensorRow>[] = [
    { key: "name", header: "Name" },
    { key: "platform", header: "Platform" },
    { key: "version", header: "Version" },
    { key: "status", header: "Status" },
    { key: "last_seen", header: "Last seen", render: (r) => (r.age_s !== null ? age(r.age_s) : ago(r.last_seen)) },
    {
      key: "revoke",
      header: "",
      align: "right",
      render: (r) => (
        <button
          type="button"
          className="g-danger"
          disabled={!canWrite}
          title={canWrite ? undefined : ADMIN_TOOLTIP}
          onClick={() => setRevokeTarget(r)}
        >
          Revoke
        </button>
      ),
    },
  ];

  return (
    <div className="g-settings-sensors">
      {!canWrite && <RoleNotice role={role} required="admin" />}

      <Table columns={columns} rows={settings.sensors} rowKey={(r) => r.id} empty="No sensors enrolled yet." />

      <div className="g-settings-sensors-actions">
        <button
          type="button"
          disabled={!canWrite || enrolMutation.isPending}
          title={canWrite ? undefined : ADMIN_TOOLTIP}
          onClick={() => enrolMutation.mutate()}
        >
          Generate enrol code
        </button>
        <button
          type="button"
          disabled={tenantId === null || rerunMutation.isPending}
          onClick={() => rerunMutation.mutate()}
        >
          Re-run setup
        </button>
      </div>

      {enrol && (
        <div className="enrol-result">
          <p>
            Code expires in <strong className="mono">{remaining}s</strong>
          </p>
          <code>{command}</code>
          <button type="button" onClick={copyCommand}>
            {copied ? "Copied" : "Copy command"}
          </button>
        </div>
      )}

      <ConfirmDialog
        open={revokeTarget !== null}
        title="Revoke this sensor?"
        body={`${revokeTarget?.name ?? "This sensor"} will stop reporting immediately — re-enrolling needs a fresh code.`}
        confirmLabel="Revoke"
        onCancel={() => setRevokeTarget(null)}
        onConfirm={() => {
          if (revokeTarget) revokeMutation.mutate(revokeTarget.id);
        }}
      />
    </div>
  );
}

export default Sensors;
