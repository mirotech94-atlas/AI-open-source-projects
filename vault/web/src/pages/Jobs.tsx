import {DataNotice} from "../components/DataNotice";
import {JobStory} from "../components/JobStory";
import {ScanComparison} from "../components/ScanComparison";
import { JobDetail } from "../components/JobDetail";
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge, Bars, Chips, Drawer, Feature, Page, PageHeader, StatStrip, Table, useToast } from "../design";
import { ExportMenu } from "../components/ExportMenu";
import type { Column, PageAction, SortState, StatItem } from "../design";
import { EmptyState } from "../components/EmptyState";
import { jobRetry, jobsPayload, post } from "../lib/api";
import { atLeast, roleOf, useMe } from "../lib/auth";
import { age, clock, day } from "../lib/format";
import type { JobRow, JobsPayload } from "../types";

const EMPTY: JobsPayload = { jobs: [] };

//: 3-5 s is the product's whole "feels live" band (see `Shell.tsx`'s
// `LIVE_POLL_MS`) — this page polls at the slow end of it since a queue row
// changes state far less often than the live probe does.
const POLL_MS = 5_000;

const OPERATOR_TOOLTIP = "operator role required";

const STATUS_ITEMS = [
  { value: "scheduled", label: "Scheduled" },
  { value: "queued", label: "Queued" },
  { value: "missed", label: "Window missed" },
  { value: "cancelled", label: "Cancelled" },
  { value: "running", label: "Running" },
  { value: "done", label: "Done" },
  { value: "failed", label: "Failed" },
];

const KIND_LABEL: Record<string, string> = {
  fix: "Fix",
  speedtest: "Speed test",
  scan: "Scan",
  "reboot-plan": "Reboot",
  "vulnerability-scan": "Vulnerability scan",
  "deep-dive": "Device deep dive",
  report: "Report",
};

function kindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? kind;
}

function statusTone(row: JobRow): "plain" | "good" | "warn" | "alert" {
  if (row.status === "missed") return "warn";
  if (row.status === "scheduled" || row.status === "cancelled") return "plain";
  if (row.status === "failed") return "alert";
  if (row.stuck) return "alert";
  if (row.status === "running" || row.status === "queued") return "warn";
  if (row.ok === false) return "alert";
  return "good";
}

function statusLabel(row: JobRow): string {
  if (row.status === "scheduled") return "Scheduled";
  if (row.status === "missed") return "Window missed";
  if (row.status === "cancelled") return "Cancelled";
  if (row.status === "running" && row.stuck) return "Stuck";
  // A job still queued long past the sensor's ten-second poll is not waiting
  // its turn, it is waiting for a sensor that is not there. Saying "Queued"
  // to that reads as ordinary progress, which is how a fix Vault promised
  // to run sat untouched with nothing on this page contradicting it.
  if (row.status === "queued" && row.stuck) return "No sensor picked this up";
  if (row.status === "queued") return "Queued";
  if (row.status === "running") return "Running";
  if (row.status === "failed") return "Failed";
  return "Done";
}

function whenText(row: JobRow): string {
  return `${day(row.queued_at)} ${clock(row.queued_at)}`;
}

function durationText(row: JobRow): string {
  if (row.duration_s === null) return "—";
  const text = age(row.duration_s);
  return row.status === "running" || row.status === "queued" ? `${text} so far` : text;
}

/** The active/finished split the spec asks for: anything still `queued` or
 *  `running` is a live thing to watch, everything else is history. */
function isActive(row: JobRow): boolean {
  return row.status === "scheduled" || row.status === "queued" || row.status === "running";
}

/** A stuck row is the one thing this page must not let a person miss, and
 *  colour alone is not allowed to carry that (a badge tone is invisible to
 *  colour-blind readers and to anyone scanning fast). So it gets three
 *  independent signals: sorted to the top by default (`activeSort`'s
 *  `duration_s desc`), a leading glyph next to its status text, and a
 *  left-edge mark on the row itself (`rowClassName` below) — the same
 *  inset-shadow mechanism `Table`'s own `.g-row-selected` already uses. */
function rowClassName(row: JobRow): string | undefined {
  if (row.stuck) return "g-activity-row-stuck";
  if (row.status === "failed") return "g-activity-row-failed";
  if (row.status === "running") return "g-activity-row-running";
  return undefined;
}

export function Jobs() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const meQuery = useMe();
  const role = roleOf(meQuery.data);
  const canRetry = atLeast(role, "operator");

  const [statusFilter, setStatusFilter] = useState<string | null>(null);
  const [kindFilter, setKindFilter] = useState<string | null>(null);
  // Time is the log's natural order; duration is what a person watching the
  // queue actually wants first — it puts the longest-waiting (and so the
  // likeliest-stuck) row at the top without any colour doing that work.
  const [activeSort, setActiveSort] = useState<SortState>({ key: "duration_s", dir: "desc" });
  const [historySort, setHistorySort] = useState<SortState>({ key: "queued_at", dir: "desc" });
  // "Pause feed" stops the 5 s poll; the rows on screen stay as they were.
  const [paused, setPaused] = useState(false);
  // The row whose drawer is open. Held by id, not by object, so the 5 s poll
  // replacing every row object does not close the drawer or show stale data.
  const [openId, setOpenId] = useState<string | null>(null);

  const jobsQuery = useQuery({
    queryKey: ["jobs"],
    queryFn: jobsPayload,
    refetchInterval: paused ? false : POLL_MS,
    // Polling pauses on a hidden tab by itself (React Query's default
    // refetchIntervalInBackground is false). Gating `enabled` on visibility
    // did more than pause: it blocked the FIRST fetch too, so a page opened in
    // a background tab sat on "Loading" until someone looked at it -- which
    // reads as a dead tab, not a paused one.
    refetchIntervalInBackground: false,
  });
  const payload = jobsQuery.data ?? EMPTY;

  const kindItems = useMemo(() => {
    const kinds = Array.from(new Set(payload.jobs.map((j) => j.kind))).sort();
    return kinds.map((k) => ({ value: k, label: kindLabel(k) }));
  }, [payload.jobs]);

  const filtered = payload.jobs.filter(
    (j) => (statusFilter === null || j.status === statusFilter) && (kindFilter === null || j.kind === kindFilter),
  );
  const activeRows = filtered.filter(isActive);
  const finishedRows = filtered.filter((r) => !isActive(r));
  const openRow = openId ? payload.jobs.find((r) => r.id === openId) ?? null : null;
  const stuckCount = filtered.filter((r) => r.stuck).length;

  // Never a claim of calm this page cannot back up. No job has ever run for
  // this tenant is a different fact from "nothing queued right now" — the
  // first means Vault has done nothing yet, the second means it just
  // finished the last thing cleanly. Conflating them is how "no data" reads
  // as "all clear".
  const neverRun = payload.jobs.length === 0;
  const filteredToNothing = !neverRun && filtered.length === 0;

  const retryMutation = useMutation({
    mutationFn: (id: string) => jobRetry(id),
    onSuccess: async () => {
      toast.show("Retry queued", "plain");
      await queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  const cancelMutation = useMutation({
    mutationFn: (id: string) => post(`/api/v1/jobs/${encodeURIComponent(id)}/cancel`),
    onSuccess: () => { toast.show("Waiting job cancelled", "plain"); void queryClient.invalidateQueries({queryKey:["jobs"]}); },
    onError: (error: Error) => toast.show(error.message, "alert"),
  });

  function retryButton(row: JobRow) {
    if (row.status === "queued" || row.status === "scheduled") return <button
      disabled={!atLeast(role, row.kind === "fix" || row.kind === "reboot-plan" || row.kind === "vulnerability-scan" ? "admin" : "operator") || cancelMutation.isPending}
      onClick={e => { e.stopPropagation(); cancelMutation.mutate(row.id); }}>Cancel waiting job</button>;
    if (row.status === "running") return <span className="hint">Safe interruption is not available for this job.</span>;
    if (row.status !== "failed" || !row.retryable) return null;
    const disabled = !canRetry || retryMutation.isPending;
    return (
      <button
        type="button"
        className="g-button-link"
        disabled={disabled}
        title={canRetry ? "Run this again with the same arguments" : OPERATOR_TOOLTIP}
        onClick={(e) => {
          e.stopPropagation();
          retryMutation.mutate(row.id);
        }}
      >
        Retry
      </button>
    );
  }

  const columns: Column<JobRow>[] = [
    {
      key: "queued_at",
      header: "Time",
      width: 128,
      render: (r) => <span className="g-activity-num">{whenText(r)}</span>,
    },
    {
      key: "duration_s",
      header: "Duration",
      align: "right",
      width: 116,
      render: (r) => <span className="g-activity-num g-activity-duration">{durationText(r)}</span>,
    },
    {
      key: "status",
      header: "Status",
      width: 200,
      render: (r) => {
        const retry = retryButton(r);
        return (
          <span className="g-activity-status">
            {r.stuck && (
              <span className="g-activity-alarm" aria-hidden="true">
                ⚠
              </span>
            )}
            <Badge tone={statusTone(r)}>{statusLabel(r)}</Badge>
            {retry}
          </span>
        );
      },
    },
    {
      key: "label",
      header: "Job",
      render: (r) => (
        <div className="g-activity-job">
          <span className="g-activity-job-label" title={r.label}>
            {r.label}
          </span>
          {["vulnscan","deepdive"].includes(r.kind) && r.status === "done" && <ScanComparison id={r.id} />}
          {!r.id.startsWith("auto:") && <JobStory id={r.id} />}
          {r.wait_reason && <div className="hint">{r.wait_reason}</div>}
          {r.not_before && <div className="hint">Window starts {new Date(r.not_before).toLocaleString()} · {Intl.DateTimeFormat().resolvedOptions().timeZone}</div>}
          {r.sensor_id && <div className="hint">Sensor {r.sensor_id} · waited {age(r.waiting_s)}{r.running_s != null ? ` · ran ${age(r.running_s)}` : ""}</div>}
          {r.status === "failed" && r.why_failed && <div className="hint">{r.why_failed}</div>}
        </div>
      ),
    },
    {
      key: "target",
      header: "Target",
      width: 140,
      render: (r) =>
        r.target ? (
          <span className="g-activity-truncate" title={r.target}>
            {r.target}
          </span>
        ) : (
          "—"
        ),
    },
    { key: "who", header: "Who", width: 100, render: (r) => r.who || "—" },
  ];

  function clearFilters() {
    setStatusFilter(null);
    setKindFilter(null);
  }

  const running = payload.jobs.filter((j) => j.status === "running").length;
  const queued = payload.jobs.filter((j) => j.status === "queued" || j.status === "scheduled").length;
  const failed = payload.jobs.filter((j) => j.status === "failed" || (j.status === "done" && j.ok === false)).length;
  const dayAgo = Date.now() - 24 * 3_600_000;
  const doneToday = payload.jobs.filter(
    (j) => j.status === "done" && j.ok !== false && j.finished_at !== null && Date.parse(j.finished_at) >= dayAgo,
  ).length;
  const stats: StatItem[] = [
    { label: "Running", value: String(running), deltaTone: stuckCount > 0 ? "alert" : undefined, delta: stuckCount > 0 ? `${stuckCount} stuck` : undefined },
    { label: "Queued", value: String(queued) },
    { label: "Done · 24h", value: String(doneToday), deltaTone: "good" },
    { label: "Failed", value: String(failed), deltaTone: failed > 0 ? "alert" : undefined },
  ];
  // Jobs queued per hour over the last 24 hours, oldest hour first.
  const hourBars = Array.from({ length: 24 }, (_, i) => {
    const from = dayAgo + i * 3_600_000;
    const to = from + 3_600_000;
    const all = payload.jobs.filter((j) => { const t = Date.parse(j.queued_at); return t >= from && t < to; });
    const bad = all.filter((j) => j.status === "failed" || j.ok === false).length;
    return { a: all.length - bad, b: bad, title: `${all.length} job${all.length === 1 ? "" : "s"}${bad ? `, ${bad} failed` : ""}` };
  });
  const actions: PageAction[] = [
    { label: paused ? "Resume feed" : "Pause feed", primary: true, onClick: () => setPaused((v) => !v), title: paused ? "Polling is paused" : "Stop the 5-second refresh" },
  ];

  if (jobsQuery.isPending) return <p role="status">Loading activity…</p>;
  if (jobsQuery.isError && !jobsQuery.data) return <div role="alert">Could not load activity. <button onClick={() => void jobsQuery.refetch()}>Retry</button></div>;
  return (
    <Page>
      <PageHeader
        eyebrow="Watch · Vault at work"
        title="Activity"
        sub="Every check Vault is running or has just run, so being protected is something you can watch rather than take on trust."
        actions={actions}
        extra={<ExportMenu path="/api/v1/export/activity" query={{ days: 30 }}
                           onError={(msg) => toast.show(msg, "alert")} />}
      />
      <StatStrip items={stats} />
      {!neverRun && (
        <Feature title="Jobs per hour, 24 hours" note={paused ? "Feed paused" : "Refreshes every 5 seconds"}>
          <Bars bars={hourBars} axis={["24h ago", "now"]} empty="No jobs were queued in the last 24 hours." />
        </Feature>
      )}
      <DataNotice query={jobsQuery} />
      <section className="sk-records">
      {/* Always shown: two chip rows are the filter, and a button that hides
          them is a control for a control. */}
      <div className="g-activity-filters">
        <Chips label="Filter by status" items={STATUS_ITEMS} value={statusFilter} onChange={setStatusFilter} />
        <Chips label="Filter by kind" items={kindItems} value={kindFilter} onChange={setKindFilter} />
      </div>

      {neverRun ? (
        <EmptyState
          title="No jobs have run yet."
          body="Vault hasn't queued a fix, scan, speed test or reboot for this tenant. Once it does, every one of them shows up here, live."
        />
      ) : filteredToNothing ? (
        <EmptyState
          title="Nothing matches these filters."
          body="The queue isn't empty — just nothing under Queued/Running/Done/Failed and this kind at once."
          action={{ label: "Clear filters", onClick: clearFilters }}
        />
      ) : (
        <div className="g-activity">
          <section className="g-activity-section">
            <header className="g-activity-section-head">
              <h3>
                In progress
                {activeRows.length > 0 && (
                  <span
                    className={`g-activity-live-dot${stuckCount > 0 ? " g-activity-live-dot-stuck" : ""}`}
                    aria-hidden="true"
                  />
                )}
              </h3>
              <span className="g-activity-section-sub">
                {activeRows.length === 0
                  ? "Queued or running right now."
                  : stuckCount > 0
                    ? `${activeRows.length} job${activeRows.length === 1 ? "" : "s"} live — ${stuckCount} stuck, waiting on nobody.`
                    : `${activeRows.length} job${activeRows.length === 1 ? "" : "s"} live, moving normally.`}
              </span>
            </header>
            {activeRows.length === 0 ? (
              <p className="g-activity-empty-line">
                Nothing queued or running right now — the queue is caught up.
              </p>
            ) : (
              <Table
                columns={columns}
                rows={activeRows}
                rowKey={(r) => r.id}
                sort={activeSort}
                onSort={setActiveSort}
                rowClassName={rowClassName}
                onRowClick={(r) => setOpenId(r.id)}
                className="g-activity-table"
                empty="Nothing queued or running."
              />
            )}
          </section>

          <section className="g-activity-section g-activity-section-history">
            <header className="g-activity-section-head">
              <h3>History</h3>
              <span className="g-activity-section-sub">Finished jobs, newest first.</span>
            </header>
            {finishedRows.length === 0 ? (
              <p className="g-activity-empty-line">No finished jobs yet.</p>
            ) : (
              <Table
                columns={columns}
                rows={finishedRows}
                rowKey={(r) => r.id}
                sort={historySort}
                onSort={setHistorySort}
                rowClassName={rowClassName}
                onRowClick={(r) => setOpenId(r.id)}
                className="g-activity-table"
                empty="No finished jobs yet."
              />
            )}
          </section>
        </div>
      )}
      </section>
      <Drawer
        open={openRow !== null}
        onClose={() => setOpenId(null)}
        title={openRow ? openRow.label : ""}
        subtitle={openRow ? kindLabel(openRow.kind) : undefined}
      >
        {openRow && <JobDetail row={openRow} />}
      </Drawer>
    </Page>
  );
}

export default Jobs;
