import { useEffect, useState } from "react";
import { useLiveFeed } from "../lib/live";
import type { UpgradeProgress } from "../types";

export type UpgradeProgressPanelProps = {
  progress: UpgradeProgress[];
};

const LOG_LINES = 10;

/** Merges an `upgrade` live frame's `data` into the job it names by
 *  `queue_id`. A frame for a job this panel does not know about is dropped —
 *  the job list itself only ever comes from the payload (a queued job the
 *  hub has not reported yet has nothing here to update). */
function mergeFrame(jobs: UpgradeProgress[], data: Record<string, unknown>): UpgradeProgress[] {
  const queueId = typeof data.queue_id === "string" ? data.queue_id : null;
  if (queueId === null) return jobs;
  let matched = false;
  const next = jobs.map((job) => {
    if (job.queue_id !== queueId) return job;
    matched = true;
    return {
      ...job,
      host: typeof data.host === "string" ? data.host : job.host,
      action: typeof data.action === "string" ? data.action : job.action,
      status: typeof data.status === "string" ? data.status : job.status,
      done: typeof data.done === "number" ? data.done : job.done,
      total: typeof data.total === "number" ? data.total : job.total,
      log_tail: typeof data.log_tail === "string" ? data.log_tail : job.log_tail,
      finished: typeof data.finished === "string" ? data.finished : job.finished,
    };
  });
  return matched ? next : jobs;
}

/** One card per in-flight (or just-finished) upgrade job: a determinate
 *  progress bar, the host and action, and a 10-line log tail. Subscribes to
 *  the `upgrade` live frame and patches its local copy of `progress` in
 *  place — never a refetch. Once a job's `status` reaches "done"/"failed"
 *  the card stops animating and shows the final state; it is not removed,
 *  so the result stays visible until the next full payload load drops it. */
export function UpgradeProgressPanel({ progress }: UpgradeProgressPanelProps) {
  const live = useLiveFeed();
  const [jobs, setJobs] = useState<UpgradeProgress[]>(progress);
  const [lastProgress, setLastProgress] = useState(progress);

  // A real payload refetch (new jobs queued, finished ones aged out) replaces
  // the local list wholesale; live frames only ever patch what is already
  // here. Adjusted during render rather than in an effect — this is React's
  // documented pattern for "reset state when a prop changes" and avoids the
  // extra render an effect-driven setState would cause.
  if (progress !== lastProgress) {
    setLastProgress(progress);
    setJobs(progress);
  }

  useEffect(() => {
    return live.on("upgrade", (event) => {
      setJobs((prev) => mergeFrame(prev, event.data));
    });
  }, [live]);

  if (jobs.length === 0) {
    return <p className="g-asset-empty">No upgrade jobs queued.</p>;
  }

  return (
    <div className="g-upgrade-panel">
      {jobs.map((job) => {
        const pct = job.total > 0 ? Math.min(100, Math.round((job.done / job.total) * 100)) : 0;
        const settled = job.status === "done" || job.status === "failed";
        const tail = job.log_tail
          .split("\n")
          .filter((line) => line !== "")
          .slice(-LOG_LINES);
        return (
          <article key={job.queue_id} className="g-upgrade-card" data-testid="upgrade-card">
            <header className="g-upgrade-head">
              <span className="g-upgrade-host">{job.host}</span>
              <span className="g-upgrade-action">{job.action}</span>
              <span className={`g-badge g-tone-${settled ? (job.status === "failed" ? "alert" : "good") : "plain"}`}>
                {job.status}
              </span>
            </header>
            <div
              className="g-upgrade-bar-track"
              role="progressbar"
              aria-valuenow={job.done}
              aria-valuemin={0}
              aria-valuemax={job.total}
              aria-label={`${job.host} ${job.action} progress`}
            >
              <div className="g-upgrade-bar-fill" style={{ width: `${pct}%` }} />
            </div>
            <p className="g-upgrade-count mono">
              {job.done} / {job.total} packages
            </p>
            <pre className="g-upgrade-log mono">{tail.length > 0 ? tail.join("\n") : "—"}</pre>
          </article>
        );
      })}
    </div>
  );
}

export default UpgradeProgressPanel;
