import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useToast } from "../design";
import { queueStatus } from "../lib/api";
import type { Ack } from "../lib/api";
import type { RebootCandidate, RebootPlan, RebootWindow } from "../types";

export type RebootPlannerProps = {
  plan: RebootPlan;
  /** The caller owns the write (`planReboot`) and the queue id it returns —
   *  this component only renders the picker/button and polls that id. */
  onSchedule: (host: string, window: RebootWindow) => Promise<Ack>;
  canWrite?: boolean;
  writeTooltip?: string;
};

/** Polls `GET /api/v1/queue/{id}` every 5s until the reboot job reaches a
 *  terminal state — same shape as Security's fix-queue poller (Task 27). */
function displayTime(value:string) {
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date.toLocaleString(undefined,{month:"short",day:"numeric",hour:"numeric",minute:"2-digit",timeZoneName:"short"}) : value;
}

type RebootOutcome = "done" | "failed" | "missed" | "cancelled";
function QueuePoller({ id, onSettled }: { id: string; onSettled: (status: RebootOutcome) => void }) {
  const query = useQuery({ queryKey: ["queue", id], queryFn: () => queueStatus(id), refetchInterval: 5_000 });
  useEffect(() => {
    if (query.data && ["done", "failed", "missed", "cancelled"].includes(query.data.status)) {
      onSettled(query.data.status as RebootOutcome);
    }
  }, [query.data?.status, onSettled]);
  if (!query.data) return <p className="g-reboot-queue-status hint">Queued…</p>;
  return <p className="g-reboot-queue-status hint">Queue: {query.data.status}</p>;
}

/** One card per candidate host: why it needs a reboot, the services Vault
 *  will confirm afterward, a window picker built from `plan.windows`, and a
 *  Schedule button. Once scheduled the card polls the queue id the write
 *  returned and shows the confirmation line when it settles. */
export function RebootPlanner({
  plan,
  onSchedule,
  canWrite = true,
  writeTooltip = "admin role required",
}: RebootPlannerProps) {
  const toast = useToast();
  const [chosen, setChosen] = useState<Record<string, string>>({});
  const [queueIds, setQueueIds] = useState<Record<string, string>>({});
  const [outcomes, setOutcomes] = useState<Record<string, RebootOutcome>>({});
  const [exactTimes, setExactTimes] = useState<Record<string, string>>({});
  const [scheduling, setScheduling] = useState<Record<string, boolean>>({});

  if (plan.hosts.length === 0) {
    return <p className="g-asset-empty">No reboot is needed right now.</p>;
  }

  function windowFor(host: string): RebootWindow | null {
    const label = chosen[host] ?? plan.windows[0]?.label;
    if (label === "custom") {
      const start = new Date(exactTimes[host] ?? "");
      if (!Number.isFinite(start.getTime()) || start.getTime() <= Date.now()) return null;
      return {label:"Chosen time", start:start.toISOString(), end:new Date(start.getTime()+300_000).toISOString()};
    }
    return plan.windows.find((w) => w.label === label) ?? null;
  }

  async function schedule(candidate: RebootCandidate) {
    const window = windowFor(candidate.host);
    if (!window) return;
    setScheduling((prev) => ({ ...prev, [candidate.host]: true }));
    try {
      const ack = await onSchedule(candidate.host, window);
      const queueId = ack.id ?? ack.queue_id;
      const id = typeof queueId === "string" ? queueId : typeof queueId === "number" ? String(queueId) : null;
      if (id) {
        setQueueIds((prev) => ({ ...prev, [candidate.host]: id }));
        toast.show("Reboot queued", "plain");
      } else {
        toast.show("Reboot scheduled", "good");
      }
    } catch (err) {
      toast.show(err instanceof Error ? err.message : "Failed to schedule reboot", "alert");
    } finally {
      setScheduling((prev) => ({ ...prev, [candidate.host]: false }));
    }
  }

  return (
    <div className="g-reboot-planner">
      {plan.hosts.map((candidate) => {
        const queueId = queueIds[candidate.host];
        const outcome = outcomes[candidate.host];
        const windowLabel = chosen[candidate.host] ?? plan.windows[0]?.label ?? "";
        return (
          <article key={candidate.host} className="g-reboot-card" data-testid="reboot-card">
            <header className="g-reboot-head">
              <span className="g-reboot-host">{candidate.host}</span>
              {candidate.queued && <span className="g-badge g-tone-warn">Already queued</span>}
            </header>
            <p className="g-reboot-reason">{candidate.reason}</p>
            <div className="g-reboot-controls">
              <label className="g-reboot-window">
                <span>Window</span>
                <select
                  aria-label="Window"
                  value={windowLabel}
                  disabled={!canWrite || queueId !== undefined}
                  title={canWrite ? undefined : writeTooltip}
                  onChange={(e) => setChosen((prev) => ({ ...prev, [candidate.host]: e.target.value }))}
                >
                  {plan.windows.map((w) => (
                    <option key={w.label} value={w.label}>
                      {w.label}
                    </option>
                  ))}
                <option value="custom">Choose a time…</option>
                </select>
              </label>
              {windowLabel === "custom" && <label>Start time ({Intl.DateTimeFormat().resolvedOptions().timeZone})<input type="datetime-local" aria-label="Reboot start time" value={exactTimes[candidate.host] ?? ""} disabled={!canWrite || !!queueId} onChange={e=>setExactTimes(prev=>({...prev,[candidate.host]:e.target.value}))}/><small>Starts within five minutes of this time, or is marked missed.</small></label>}
            </div>
            {windowFor(candidate.host) && <p className="hint">Allowed window: {displayTime(windowFor(candidate.host)!.start)} to {displayTime(windowFor(candidate.host)!.end)}</p>}
            {/* The window and what happens because of it, stated in one
                sentence and read before the button that commits to it — a
                reboot is disruptive, always proposed and waited on, never a
                bare click. */}
            {queueId === undefined && windowLabel !== "" && (
              <p className="g-reboot-consequence">
                {candidate.host} will be unavailable while restarting during <strong>{windowLabel === "custom" ? "your chosen time" : windowLabel}</strong>. A missed window requires a new time.
                {candidate.services.length > 0 && (
                  <> Check {candidate.services.join(", ")} after the restart. Command completion alone does not confirm service recovery.</>
                )}
              </p>
            )}
            <button
              type="button"
              className="g-reboot-schedule"
              disabled={!canWrite || queueId !== undefined || scheduling[candidate.host] === true || !windowFor(candidate.host)}
              title={canWrite ? undefined : writeTooltip}
              onClick={() => schedule(candidate)}
            >
              {scheduling[candidate.host] ? "Scheduling…" : "Schedule reboot"}
            </button>
            {queueId && !outcome && (
              <QueuePoller
                id={queueId}
                onSettled={(status) => {
                  setOutcomes((prev) => ({ ...prev, [candidate.host]: status }));
                  toast.show(
                    status === "done" ? "Reboot command completed; check recovery in Activity" : `Reboot ${status}`,
                    status === "done" ? "plain" : "alert",
                  );
                }}
              />
            )}
            {outcome && <p className="g-reboot-confirmation" role="status">{outcome === "done" ? "Command completed. Recovery verification is shown in Activity." : `Reboot ${outcome}. Review the job in Activity before scheduling again.`}</p>}
          </article>
        );
      })}
    </div>
  );
}

export default RebootPlanner;
