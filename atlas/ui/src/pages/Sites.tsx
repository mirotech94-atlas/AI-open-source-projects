import { useState, type JSX } from "react";
import { api } from "../api";
import { HealthBadge } from "../components/Health";
import { useFresh } from "../hooks/useFresh";
import {
  DOC_KIND_LABEL, DOC_STATUS_LABEL, GROUP_ORDER, KIND_LABEL, groupSites, isStale,
  orderEvents, readLabel, safeDocUrl, summaryLine,
  type SiteRow, type SitesOverview,
} from "./sitesModel";

/**
 * The Client Sites tab: the agency module's site registry, read from
 * `/api/agency/sites`. Read-only: a demo goes live only through the
 * `agency.deploy_demo` card, a built site through `agency.deploy_site`.
 * UNKNOWN is drawn by `HealthBadge` (grey, hatched, with the word); every
 * reading shows when it was read.
 */
export function Sites({ reloadKey }: { reloadKey: number }): JSX.Element {
  const view = useFresh(() => api.sites(), [reloadKey]);
  return (
    <div className="sites">
      <h2 className="label">Client Sites</h2>
      <p className="stamp" style={{ marginBottom: 12 }}>
        {view.fetchedAt ? `Read ${view.fetchedAt.toLocaleTimeString()}` : "Reading…"}
      </p>
      {view.error && <p className="errorbar">Could not read the client sites: {view.error}</p>}
      {view.data && <SitesView data={view.data} now={new Date(view.data.read_at)} />}
    </div>
  );
}

export function SitesView({ data, now }: { data: SitesOverview; now: Date }): JSX.Element {
  const groups = groupSites(data, now);
  return (
    <>
      <p className="stamp">{summaryLine(groups)}</p>
      {GROUP_ORDER.map((key) =>
        groups[key].length === 0 ? null : (
          <section className="panel" key={key} style={{ marginTop: 12 }}>
            <div className="inner">
              {groups[key].map((row) => (
                <SiteCard key={row.slug} row={row} now={now} every={data.check_every_s} />
              ))}
            </div>
          </section>
        ),
      )}
    </>
  );
}

function SiteCard({ row, now, every }: { row: SiteRow; now: Date; every: number }): JSX.Element {
  const stale = isStale(row, now, every);
  const shown = stale && row.health === "ok" ? "unknown" : row.health;
  return (
    <article className="site-row" style={{ padding: "8px 0" }}>
      <div>
        <HealthBadge health={shown} />{" "}
        <strong>{row.hostname ?? row.slug}</strong>{" "}
        <span className="stamp">{KIND_LABEL[row.kind] ?? row.kind}</span>
      </div>
      <div className="stamp">
        {readLabel(row.health_read_at)}
        {stale && row.health_read_at ? " (stale)" : ""}
        {row.health_detail ? ` · ${row.health_detail}` : ""}
      </div>
      <div className="stamp">
        Live release: {row.live_release ?? "nothing live"}
        {row.live_problem ? ` · ${row.live_problem}` : ""}
        {row.source ? ` · source ${row.source}` : ""}
      </div>
      <SiteDocs row={row} />
      {row.kind !== "app" && <SiteEventsList slug={row.slug} />}
    </article>
  );
}

/**
 * Client-site documents. Each is a Claude Docs artifact the coding agent
 * made after the publish card was approved; the link is its report, so it
 * says so. No link yet, or no readable list, is UNKNOWN (grey, hatched,
 * with the word), never a blank.
 */
function SiteDocs({ row }: { row: SiteRow }): JSX.Element {
  if (row.docs === null || row.docs === undefined) {
    return (
      <div className="stamp">
        Docs: <HealthBadge health="unknown" /> {row.docs_problem ?? "documents could not be read"}
      </div>
    );
  }
  if (row.docs.length === 0) {
    return <div className="stamp">Docs: No Docs artifact yet.</div>;
  }
  return (
    <ul className="stamp">
      {row.docs.map((d) => {
        const url = safeDocUrl(d.artifact_url);
        const label = `${DOC_KIND_LABEL[d.kind] ?? d.kind}: ${d.title}`;
        return (
          <li key={d.id}>
            {url ? (
              <>
                <a href={url} target="_blank" rel="noopener noreferrer">{label}</a>
                {" "}· Claude Docs link reported by the coding agent, not verified ·{" "}
                {readLabel(d.reported_at)}
              </>
            ) : (
              <>
                <HealthBadge health="unknown" /> {label} · no Docs link yet
                {" "}({DOC_STATUS_LABEL[d.status] ?? d.status})
              </>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function SiteEventsList({ slug }: { slug: string }): JSX.Element {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button type="button" className="linkish" onClick={() => setOpen((o) => !o)}>
        {open ? "Hide deploy history" : "Deploy history"}
      </button>
      {open && <EventsBody slug={slug} />}
    </div>
  );
}

function EventsBody({ slug }: { slug: string }): JSX.Element {
  const view = useFresh(() => api.siteEvents(slug), [slug]);
  if (view.error) return <p className="errorbar">Could not read the history: {view.error}</p>;
  if (!view.data) return <p className="stamp">Reading…</p>;
  const events = orderEvents(view.data.events);
  if (events.length === 0) {
    return <p className="stamp">No deploys recorded ({readLabel(view.data.read_at)}).</p>;
  }
  return (
    <ul className="stamp">
      {events.map((e, i) => (
        <li key={`${e.at ?? "x"}-${i}`}>
          {e.at ? new Date(e.at).toLocaleString() : "unknown time"} · {e.event}
          {e.release ? ` · ${e.release}` : ""}
          {e.detail ? ` · ${e.detail}` : ""}
        </li>
      ))}
    </ul>
  );
}
