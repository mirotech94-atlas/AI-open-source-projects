import { useQuery } from "@tanstack/react-query";
import { get } from "../lib/api";
import { clock, day } from "../lib/format";
import type { JobRow, ScanSummary } from "../types";

/** The scan a finished scan request opens, in counts, with the whole scan
 *  one click away as a download. */
function ScanPanel({ scanId }: { scanId: string }) {
  const scan = useQuery({
    queryKey: ["scan", scanId],
    queryFn: () => get<ScanSummary>(`/api/v1/scans/${encodeURIComponent(scanId)}`),
  });
  if (scan.isError) return <p className="hint">Could not load that scan.</p>;
  if (!scan.data) return <p className="hint">Loading the scan…</p>;
  const s = scan.data;
  return (
    <section aria-label="Scan" className="g-job-scan">
      {/* The request's own record says only that a scan was asked for; the
          sensor then sends it like any other. So this is honestly "the scan
          that followed", not "this request's scan". */}
      <h3>The scan that followed this request</h3>
      <dl className="g-dns-why g-job-facts">
        <dt>Taken</dt><dd>{day(s.ts)} {clock(s.ts)}</dd>
        {s.score !== null && (<><dt>Score</dt><dd>{s.score}</dd></>)}
        <dt>Devices seen</dt><dd>{s.devices}</dd>
        <dt>Sites up</dt><dd>{s.sites_up} of {s.sites_total}</dd>
        <dt>Servers reachable</dt><dd>{s.hosts_reachable} of {s.hosts_total}</dd>
        <dt>Alerts raised</dt><dd>{s.alerts}</dd>
      </dl>
      <a className="g-button-link" href={`/api/v1/scans/${encodeURIComponent(s.id)}/download`} download>
        Download the full scan (JSON)
      </a>
    </section>
  );
}

/** Everything the Activity row knows, and -- for a report or a scan -- a way
 *  to open or download the thing itself. */
export function JobDetail({ row }: { row: JobRow }) {
  const reportId = row.report_id ?? null;
  const scanId = row.scan_id ?? null;
  return (
    <div className="g-job-detail">
      <dl className="g-dns-why g-job-facts">
        <dt>Status</dt><dd>{row.status}{row.ok === false ? " — did not succeed" : ""}</dd>
        {row.target && (<><dt>Target</dt><dd>{row.target}</dd></>)}
        {row.who && (<><dt>Started by</dt><dd>{row.who}</dd></>)}
        {row.queued_at && (<><dt>Requested</dt><dd>{day(row.queued_at)} {clock(row.queued_at)}</dd></>)}
        {row.finished_at && (<><dt>Finished</dt><dd>{day(row.finished_at)} {clock(row.finished_at)}</dd></>)}
        {row.why_failed && (<><dt>Why it failed</dt><dd>{row.why_failed}</dd></>)}
        {row.out && !row.why_failed && (<><dt>Result</dt><dd className="mono">{row.out}</dd></>)}
      </dl>

      {reportId && (
        <div className="g-job-actions">
          <a className="g-button-link" href={`/api/v1/reports/${encodeURIComponent(reportId)}`} target="_blank" rel="noreferrer">
            Open the report
          </a>
          <a className="g-button-link" href={`/api/v1/reports/${encodeURIComponent(reportId)}/pdf`} download>
            Download PDF
          </a>
        </div>
      )}

      {scanId && <ScanPanel scanId={scanId} />}
      {row.kind === "scan" && !scanId && row.status === "done" && (
        <p className="hint">The sensor has not sent a scan since this request finished.</p>
      )}
    </div>
  );
}

export default JobDetail;
