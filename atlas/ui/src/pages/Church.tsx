import { type JSX } from "react";
import { api } from "../api";
import { useFresh } from "../hooks/useFresh";
import "./church.css";
import {
  PLATFORMS, PLATFORM_LABEL, budgetLine, calendarRows, dayLabel, gapSummary,
  ideaCounts, nyWhen, statusLabel, statusTone, syncLine, windowWarnings,
  type ChurchOverview,
} from "./churchModel";

/**
 * The ECC Channel tab: the church bookstore's YouTube and Facebook
 * calendar, read from `/api/church/overview`. Read-only: posting happens
 * only through the daily shift's batch cards, each approved by the owner.
 * The brand accents (teal, parchment, gold) are scoped to `.church`; health
 * and status keep the HUD's own colours.
 */
export function Church({ reloadKey }: { reloadKey: number }): JSX.Element {
  const view = useFresh(() => api.church(), [reloadKey]);
  return (
    <div className="church">
      <h2 className="label">ECC Channel</h2>
      <p className="stamp" style={{ marginBottom: 12 }}>
        {view.fetchedAt ? `Read ${view.fetchedAt.toLocaleTimeString()}` : "Reading…"}
      </p>
      {view.error && <p className="errorbar">Could not load the calendar: {view.error}</p>}
      {view.data && <ChurchView data={view.data} now={new Date(view.data.served_at)} />}
    </div>
  );
}

export function ChurchView({ data, now }: { data: ChurchOverview; now: Date }): JSX.Element {
  const warnings = windowWarnings(data.facebook_window);
  return (
    <>
      <section className="church-head panel">
        <div className="inner">
          <div className="church-name">{data.brand.name}</div>
          <div className="church-name-ar" lang="ar" dir="rtl">{data.brand.name_ar}</div>
          <div className="stamp">
            {data.brand.address} · {data.brand.hours} · {data.brand.phone} ·{" "}
            {syncLine(data.last_sync, now)}
          </div>
          <div className="stamp">
            YouTube {data.configured.youtube ? "connected" : "not connected (uploads by hand)"} ·
            Facebook {data.configured.facebook ? "connected" : "not connected (scheduled by hand)"}
          </div>
        </div>
      </section>

      <section className="church-strip">
        <div className="panel inner church-stat">
          <span className="k">YouTube budget</span>
          <span>{budgetLine(data.youtube_budget)}</span>
        </div>
        {PLATFORMS.map((p) => (
          <div key={p} className="panel inner church-stat"
               data-warn={data.gaps_7d[p] > 0 || undefined}>
            <span className="k">{PLATFORM_LABEL[p]} next 7 days</span>
            <span>{data.gaps_7d[p]} empty slot(s); {gapSummary(data, p)}</span>
          </div>
        ))}
      </section>

      {warnings.length > 0 && (
        <section className="church-warn panel inner" role="status">
          <span className="k">Facebook {data.facebook_window.window_days}-day window</span>
          <ul>{warnings.map((w) => <li key={w}>{w}</li>)}</ul>
        </section>
      )}

      {data.review.length > 0 && (
        <section className="panel inner church-warn">
          <span className="k">Awaiting the owner's review</span>
          <ul>
            {data.review.map((r) => (
              <li key={`${r.platform}-${r.id}`} data-urgent={r.urgent || undefined}>
                {PLATFORM_LABEL[r.platform]} · {nyWhen(r.publish_at)} · {r.title}
                {r.urgent ? " — due within 3 days" : ""}
              </li>
            ))}
          </ul>
        </section>
      )}

      {data.contact.length > 0 && (
        <section className="panel inner church-warn">
          <span className="k">Contact facts and spelling</span>
          <ul>
            {data.contact.map((c) => (
              <li key={`${c.platform}-${c.id}`}>
                {PLATFORM_LABEL[c.platform]} · {c.title}: {c.missing.join(", ")}
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="church-cols">
        {PLATFORMS.map((p) => (
          <section key={p} className="panel">
            <h3 className="church-h">{PLATFORM_LABEL[p]}</h3>
            <table className="sheet">
              <thead><tr><th>When (New York)</th><th>Title</th><th>Status</th></tr></thead>
              <tbody>
                {calendarRows(data, p, now).map((r) => (
                  <tr key={r.key} data-gap={r.gap || undefined}>
                    <td>{r.when}</td>
                    <td>
                      {r.url ? <a href={r.url} target="_blank" rel="noreferrer">{r.title}</a>
                        : r.title}
                    </td>
                    <td>
                      <span className="church-chip" data-tone={r.tone}>
                        {r.gap ? "gap" : statusLabel(r.status)}
                      </span>
                      {r.reviewed === false && r.tone !== "done" && (
                        <span className="church-chip" data-tone="review">unreviewed</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        ))}
      </div>

      <div className="church-cols">
        <section className="panel">
          <h3 className="church-h">Feasts ahead</h3>
          {data.feasts.length === 0 ? <p className="empty inner">None in 60 days.</p> : (
            <ul className="church-list">
              {data.feasts.map((f) => (
                <li key={`${f.day}-${f.name}`} data-major={f.major || undefined}>
                  <b>{dayLabel(f.day)}</b> {f.name}{" "}
                  <span lang="ar" dir="rtl">{f.name_ar}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
        <section className="panel">
          <h3 className="church-h">Content bank</h3>
          <p className="stamp inner">
            {ideaCounts(data.ideas).map(([s, n]) => `${n} ${s}`).join(" · ") || "no ideas"}
          </p>
          <table className="sheet">
            <thead><tr><th>#</th><th>Idea</th><th>Series</th><th>Status</th></tr></thead>
            <tbody>
              {data.ideas.map((i) => (
                <tr key={i.code}>
                  <td>{String(i.code).padStart(2, "0")}</td>
                  <td>{i.title}</td>
                  <td>{i.series}</td>
                  <td><span className="church-chip" data-tone={
                    i.status === "published" || i.status === "scheduled" ? "done"
                      : i.status === "dropped" ? "bad" : "planned"}>{i.status}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>

      <section className="panel">
        <h3 className="church-h">Recent events</h3>
        {data.events.length === 0 ? <p className="empty inner">Nothing logged yet.</p> : (
          <ul className="church-list">
            {data.events.map((e) => (
              <li key={e.id}>
                <span className="stamp">{nyWhen(e.at)}</span>{" "}
                <span className="church-chip" data-tone={statusTone(
                  e.kind === "failed" ? "failed" : e.kind === "scheduled" ? "scheduled"
                    : "planned")}>{e.kind}</span>{" "}
                {e.message}
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
