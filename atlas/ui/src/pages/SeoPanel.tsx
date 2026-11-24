import type { JSX } from "react";
import { api, type SeoPanelData, type SeoSite } from "../api";
import { useFresh } from "../hooks/useFresh";
import type { Line } from "./agentActivityModel";
import { stamp } from "./cmdParts";
import { readOf, type Read } from "./homeModel";
import {
  checkLines, issueLines, siteHealth, siteLines, yesterdayLines,
} from "./seoPanelModel";

/**
 * Beside the Agency map, under the agents' activity: the SEO agent's now
 * (its collectors' last readings), yesterday (readings and reports stamped
 * the previous New York day) and issues, then its readings per site -- Search Console clicks, position, striking-distance
 * queries, losing pages, PageSpeed scores, issues -- each with its read
 * time, and the weekly reports it stored. UNKNOWN is grey, dashed and the
 * word, never a zero. Reads only (`GET /api/agency/seo`).
 */

function Lines({ lines }: { lines: Line[] }): JSX.Element {
  return (
    <ul className="act-lines">
      {lines.map((l, i) => (
        <li key={i} data-health={l.health === "plain" ? undefined : l.health}>
          {l.health === "unknown" && <span className="cmd-word-unknown">Unknown</span>}{" "}
          {l.text}
        </li>
      ))}
    </ul>
  );
}

function SiteCard({ s }: { s: SeoSite }): JSX.Element {
  const health = siteHealth(s);
  return (
    <article className="act-card" data-site={s.slug} data-health={health}>
      <header>
        <i className="act-lamp" aria-hidden="true" />
        <b>{s.domain || s.slug}</b>
        {health === "unknown" && <span className="cmd-word-unknown">Unknown</span>}
      </header>
      <Lines lines={siteLines(s)} />
    </article>
  );
}

/** The panel from its read: pure, so it can be tested. */
export function SeoPanelBody({ seo, now = new Date() }: { seo: Read<SeoPanelData>; now?: Date }): JSX.Element {
  const r = readOf(seo);
  const d = r.data;
  return (
    <section className="act-panel" aria-labelledby="seo-h">
      <header className="desk-map-head">
        <h2 id="seo-h">SEO</h2>
        <span className="cmd-at">{stamp(seo.fetchedAt, r.error !== null)}</span>
      </header>
      {r.error !== null ? (
        <p className="cmd-unread" data-health="unknown">
          <span className="cmd-word-unknown">Unknown</span>{" "}
          The SEO readings could not be read: {r.error}
        </p>
      ) : !d ? <p className="cmd-empty">Reading…</p> : (
        <div className="act-grid">
          <article className="act-card" data-agent="seo" data-health={
            checkLines(d).some((l) => l.health === "unknown") ? "unknown" : "ok"}>
            <header><i className="act-lamp" aria-hidden="true" /><b>SEO agent</b></header>
            <section>
              <h4>Now <small>{stamp(d.read_at)}</small></h4>
              <Lines lines={checkLines(d)} />
            </section>
            <section>
              <h4>Yesterday <small>{stamp(d.read_at)}</small></h4>
              <Lines lines={yesterdayLines(d, now)} />
            </section>
            <section>
              <h4>Issues <small>{stamp(d.read_at)}</small></h4>
              <Lines lines={issueLines(d)} />
            </section>
          </article>
          {d.sites.map((s) => <SiteCard key={s.slug} s={s} />)}
        </div>
      )}
    </section>
  );
}

export function SeoPanel({ reloadKey }: { reloadKey: number }): JSX.Element {
  const seo = useFresh(() => api.agencySeo(), [reloadKey]);
  return <SeoPanelBody seo={seo} />;
}
