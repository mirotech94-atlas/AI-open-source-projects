import { useQuery, useMutation } from "@tanstack/react-query";
import { StatStrip, Verdict, useToast } from "../design";
import { jobRetry, jobsPayload, settingsPayload, updatesPayload } from "../lib/api";
import type { JobRow } from "../types";
import { ago, clock, shortDate, useActiveTenant } from "./net";

/** "Sep 21 · 02:00–05:00" for a proposed window; the day once, the times in
 *  the mono face, the way the reference sets a schedule. */
function span(start: string, end: string): string {
  const s = new Date(start), e = new Date(end);
  if (Number.isNaN(s.getTime()) || Number.isNaN(e.getTime())) return `${start} – ${end}`;
  const sameDay = s.toDateString() === e.toDateString();
  return sameDay
    ? `${shortDate(start)} · ${clock(start)}–${clock(end)}`
    : `${shortDate(start)} ${clock(start)} – ${shortDate(end)} ${clock(end)}`;
}

/**
 * Windows and held work -- the reference's Maintenance.
 *
 * Left, the windows: the customer's own maintenance window from their
 * auto-fix policy, and the reboot windows the sensor proposes for hosts that
 * need one. Right, the work held for them: every queued job, with why it is
 * waiting and who asked for it. These are already-approved jobs waiting on a
 * window, which is why there is no Approve button here.
 */

function isHeld(j: JobRow): boolean {
  return j.status === "queued" || j.status === "waiting" || j.status === "running";
}

function Maintenance() {
  const tenant = useActiveTenant();
  const toast = useToast();
  const on = tenant !== null;
  const settings = useQuery({ queryKey: ["settings"], queryFn: settingsPayload, enabled: on });
  const updates = useQuery({ queryKey: ["updates"], queryFn: updatesPayload, enabled: on });
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: jobsPayload, refetchInterval: 30_000, enabled: on });
  const retry = useMutation({
    mutationFn: (id: string) => jobRetry(id),
    onSuccess: () => { toast.show("Queued again.", "plain"); void jobs.refetch(); },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  if (tenant === null) return <p className="g-own-lede">Pick a customer in the bar to see their maintenance.</p>;
  if (jobs.isPending) return <p role="status">Reading the queue…</p>;

  const window = settings.data?.autofix.maintenance_window ?? null;
  const plan = updates.data?.reboot_plan;
  const all = jobs.data?.jobs ?? [];
  const held = all.filter(isHeld);
  const failed = all.filter((j) => j.status === "failed").slice(0, 5);

  const proposed = plan?.windows?.length ?? 0;
  return (
    <div className="g-own-page">
      <Verdict tone={held.length > 0 ? "warn" : "good"}
               headline={held.length > 0 ? `${held.length} job${held.length === 1 ? "" : "s"} held for the next window` : "Nothing is held"}
               body={window ? `Disruptive work runs daily between ${window.start} and ${window.end}.` : "No window is set, so disruptive fixes run as soon as you approve them."} />
      <StatStrip items={[
        { label: "Window", value: window ? `${window.start}–${window.end}` : "none" },
        { label: "Held", value: String(held.length), deltaTone: held.length > 0 ? "warn" : undefined },
        { label: "Proposed reboots", value: String(proposed) },
        { label: "Failed recently", value: String(failed.length), deltaTone: failed.length > 0 ? "alert" : undefined },
      ]} />
    <div className="g-own-split g-own-split-maint">
      <div className="g-own-col">
        <section aria-label="Windows" className="g-own-section">
          <h2>Windows</h2>
          <p className="g-own-sub">Anything disruptive that you approve is held until the next window opens.</p>
          <ul className="g-own-rows">
            <li className="g-own-win">
              <span className="g-own-win-main">
                <span className="g-own-win-l">Maintenance window</span>
                <span className="g-own-win-when">
                  {window ? `daily · ${window.start}–${window.end}` : "none set · disruptive fixes run when approved"}
                </span>
              </span>
              <span className="g-own-win-side">
                <span className={`g-own-pill ${window ? "g-own-pill-info" : ""}`}>{window ? "set" : "none"}</span>
                <span className="g-own-win-count">{held.length} held</span>
              </span>
            </li>
            {(plan?.windows ?? []).map((w) => (
              <li key={`${w.start}-${w.end}`} className="g-own-win">
                <span className="g-own-win-main">
                  <span className="g-own-win-l">{w.label || "Proposed reboot window"}</span>
                  <span className="g-own-win-when">{span(w.start, w.end)}</span>
                </span>
                <span className="g-own-win-side">
                  <span className="g-own-pill">proposed</span>
                </span>
              </li>
            ))}
          </ul>
        </section>

        {plan && plan.hosts.length > 0 && (
          <section aria-label="Reboots pending" className="g-own-section">
            <h2>Hosts that need a reboot <span className="g-own-meta">{plan.hosts.length}</span></h2>
            <ul className="g-own-rows">
              {plan.hosts.map((h) => (
                <li key={h.host} className="g-own-win">
                  <span className="g-own-win-main">
                    <span className="g-own-target">{h.host}</span>
                    <span className="g-own-win-when">{h.reason}{h.services.length ? ` · ${h.services.join(", ")}` : ""}</span>
                  </span>
                  <span className="g-own-win-side">
                    <span className={`g-own-pill ${h.queued ? "g-own-pill-info" : "g-own-pill-warn"}`}>{h.queued ? "queued" : "not scheduled"}</span>
                  </span>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>

      <div className="g-own-col">
        <section aria-label="Held for the next window" className="g-own-section">
          <h2>Held for the next window <span className="g-own-meta">{held.length} {held.length === 1 ? "job" : "jobs"}</span></h2>
          {held.length === 0 ? (
            <p className="g-own-quiet">Nothing is waiting. The queue is empty.</p>
          ) : (
            <ul className="g-own-rows">
              {held.map((j) => (
                <li key={j.id} className="g-own-held">
                  <span className="g-own-held-title">{j.label || j.kind}</span>
                  <span className="g-own-held-meta">
                    <span className="g-own-target">{j.target}</span>
                    <span className="g-own-impact">
                      {j.status === "running" ? `running ${Math.round(j.running_s ?? 0)} s` : `queued ${clock(j.queued_at)} · ${j.wait_reason || `waiting ${ago(j.queued_at)}`}`}
                      {j.who ? ` · by ${j.who}` : ""}
                    </span>
                  </span>
                  {j.stuck && <span className="g-own-pill g-own-pill-warn">stuck</span>}
                </li>
              ))}
            </ul>
          )}
        </section>

        {failed.length > 0 && (
          <section aria-label="Failed" className="g-own-section">
            <h2>Did not run <span className="g-own-meta">{failed.length}</span></h2>
            <ul className="g-own-rows">
              {failed.map((j) => (
                <li key={j.id} className="g-own-held">
                  <span className="g-own-held-title">{j.label || j.kind}</span>
                  <span className="g-own-held-meta">
                    <span className="g-own-target">{j.target}</span>
                    <span className="g-own-impact g-own-crit">{j.why_failed || "failed"}</span>
                  </span>
                  {j.retryable && (
                    <button type="button" className="g-own-btn" disabled={retry.isPending} onClick={() => retry.mutate(j.id)}>Retry</button>
                  )}
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </div>
    </div>
  );
}

export default Maintenance;
