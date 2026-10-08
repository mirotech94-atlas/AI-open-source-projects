import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Verdict, useToast } from "../design";
import { CertsExpiringPanel, CoveragePanel } from "../components/CoveragePanel";
import { alertOp, historyPayload, jobsPayload, overviewPayload, platformWork, requestFix, requestFixOrConfirm } from "../lib/api";
import type { FixConfirmation, HistoryEventRow, NeedsYouItem, PlatformWorkDay } from "../types";
import { agoS, clock, shortDate, useActiveTenant } from "./net";
import { useRange, type Range } from "./OwnerShell";

/**
 * The selected customer's network, right now -- the reference's Overview.
 *
 * Four figures, then the work on the left and the standing facts on the
 * right. Everything here is the customer's own `GET /api/v1/overview`, the
 * same payload their dashboard draws, so the owner reading this beside the
 * customer sees the same numbers the customer does. That is the whole reason
 * this page reads a tenant payload instead of an owner-only summary.
 *
 * The business -- revenue, who is on trial, who needs the owner today -- is
 * not on this page any more. It lives under Customers, where the Ledger's
 * opening sentence still answers "does anyone need me" in one line.
 */

type Tone = "good" | "warn" | "crit" | "info" | "plain";

function Kpi({ label, value, unit, note, tone, quiet }: {
  label: string; value: string | number; unit: string; note: string; tone: Tone;
  /** A fault at zero recedes: label weight, muted, out of the numeral face. */
  quiet?: boolean;
}) {
  return (
    <div className={`g-own-tile g-own-tile-static ${quiet ? "g-own-tile-calm" : `g-own-tile-${tone}`}`}>
      <span className="g-own-tile-top">
        <span className="g-own-tile-l">{label}</span>
        <span className={`g-own-tile-dot g-tone-${quiet ? "plain" : tone === "crit" ? "alert" : tone === "warn" ? "warn" : tone === "good" ? "good" : "info"}`} aria-hidden="true" />
      </span>
      <span className="g-own-tile-v">
        {value}
        <span className="g-own-tile-unit">{unit}</span>
      </span>
      <span className="g-own-tile-note">{note}</span>
    </div>
  );
}

/** The reference's tag vocabulary for an activity row, from the event log's
 *  own kind. A repair is AUTO; something new on the network is DISCOVERED; a
 *  port opening is the one that wants a look; the rest is just what it is. */
function tagFor(e: HistoryEventRow): { label: string; tone: Tone } {
  const k = e.kind.toLowerCase();
  if (k.startsWith("fix")) return { label: "auto", tone: "good" };
  if (k.includes("joined") || k.includes("rotated") || k.includes("wan")) return { label: "discovered", tone: "info" };
  if (k.includes("port opened")) return { label: "waiting", tone: "warn" };
  return { label: k.split(" ")[0] || "event", tone: "plain" };
}

/** What the switch asks for: a day in hours, or a week or a month in days. */
function windowFor(range: Range): { days: number; hours: number } {
  return range === "24h" ? { days: 1, hours: 24 } : range === "7d" ? { days: 7, hours: 0 } : { days: 30, hours: 0 };
}

/** A bucket's tick: the hour for a day's chart, the date otherwise. The hub
 *  keys an hourly bucket as `YYYY-MM-DDTHH` (UTC) and a daily one as the date. */
function tickFor(d: PlatformWorkDay): string {
  if (d.day.length === 13) {
    const t = new Date(`${d.day}:00:00Z`);
    return Number.isNaN(t.getTime()) ? d.day.slice(11) : `${String(t.getHours()).padStart(2, "0")}:00`;
  }
  return shortDate(`${d.day}T12:00:00Z`);
}

function Overview() {
  const tenant = useActiveTenant();
  const toast = useToast();
  const q = useQuery({ queryKey: ["overview"], queryFn: overviewPayload, refetchInterval: 60_000, enabled: tenant !== null });
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: jobsPayload, refetchInterval: 60_000, enabled: tenant !== null });
  // The bar's range switch scopes this one read: the event log over a day, a
  // week or a month, which is what Live activity and the self-healing chart
  // draw. The KPIs above them stay on the overview's own windows and say so.
  const [range] = useRange();
  const win = windowFor(range);
  const hist = useQuery({
    queryKey: ["history", win.days, null, win.hours],
    queryFn: () => historyPayload(win.days, null, win.hours),
    refetchInterval: 60_000, enabled: tenant !== null,
  });
  // The self-healing chart reads the audit trail's settled work for THIS
  // customer -- every fix Vault or a person closed -- not the event log,
  // which only carries a fix the sensor happened to report as an event. It
  // is the same read the Audit screen makes, so the two cannot disagree.
  const work = useQuery({
    queryKey: ["platform-work", win.days, win.hours, tenant?.id ?? ""],
    queryFn: () => platformWork(win.days, win.hours, tenant?.id ?? ""),
    refetchInterval: 60_000, enabled: tenant !== null,
  });
  const p = q.data;

  // Approve is NOT a one-click repair. An approval remedy is exactly the case
  // the hub mints a confirmation for; the first press fetches it and the row
  // turns into an explicit "Confirm", and only the second press sends the
  // single-use token back. The codebase carries a scar from a plain button
  // that rebooted a host on one click; this queue is the most tempting place
  // to repeat it.
  const [pending, setPending] = useState<{ key: string; confirmation: FixConfirmation } | null>(null);
  const approve = useMutation({
    mutationFn: async (item: NeedsYouItem) => {
      if (pending?.key === item.key) {
        return { item, ran: true, confirmation: null as FixConfirmation | null,
          ack: await requestFix(item.host, item.action, null, pending.confirmation.token) };
      }
      const out = await requestFixOrConfirm(item.host, item.action, null);
      return { item, ran: out.ack !== null, confirmation: out.confirmation, ack: out.ack };
    },
    onSuccess: ({ item, ran, confirmation }) => {
      if (!ran && confirmation) {
        setPending({ key: item.key, confirmation });
        toast.show(confirmation.summary || "Press Confirm to run it.", "plain");
        return;
      }
      setPending(null);
      toast.show("Approved — Vault is running it.", "good");
      void q.refetch(); void jobs.refetch();
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });
  // "Defer" is the reference's word; what it does here is snooze the alert,
  // which comes back on its own if the condition is still true.
  const defer = useMutation({
    mutationFn: (item: NeedsYouItem) => alertOp(item.key, "snooze"),
    onSuccess: () => { toast.show("Deferred. It comes back if it is still true.", "plain"); void q.refetch(); },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  if (tenant === null) {
    return <p className="g-own-lede">Pick a customer in the bar to see their network.</p>;
  }
  if (q.isPending) return <p role="status">Reading {tenant.name}…</p>;
  if (q.isError && !p) {
    return (
      <div role="alert">
        Could not read {tenant.name}. <button onClick={() => void q.refetch()}>Retry</button>
      </div>
    );
  }

  const o = p!;
  const held = (jobs.data?.jobs ?? []).filter((j) => j.status === "queued" || j.status === "waiting").length;
  const certs = o.coverage.find((r) => r.label === "Certificates");
  const sensorUp = o.sensor !== null && o.sensor.status === "active" && (o.sensor.age_s ?? Infinity) < 900;
  const fixed = o.fixed_without_you;
  const fixedTotal = fixed.automatic + fixed.approved;
  const autoPct = fixedTotal > 0 ? Math.round((fixed.automatic / fixedTotal) * 100) : 0;
  const events = hist.data?.events ?? [];
  const activity = [...events].sort((a, b) => (a.ts < b.ts ? 1 : -1)).slice(0, 10);
  const buckets = work.data?.days ?? [];
  const bucketMax = Math.max(1, ...buckets.map((b) => b.auto + b.human));
  const autoInWindow = work.data?.auto_total ?? 0;
  const humanInWindow = work.data?.human_total ?? 0;
  const fixesInWindow = autoInWindow + humanInWindow;
  const rangeWord = range === "24h" ? "24 hours" : range === "7d" ? "7 days" : "30 days";

  const needs = o.needs_you.length;
  const vTone = o.alerts.crit > 0 ? "alert" : needs > 0 || o.alerts.open > 0 ? "warn" : "good";
  const vHead = o.alerts.crit > 0
    ? `${o.alerts.crit} critical incident${o.alerts.crit === 1 ? "" : "s"} open`
    : needs > 0
      ? `${needs} thing${needs === 1 ? "" : "s"} need${needs === 1 ? "s" : ""} your approval`
      : o.alerts.open > 0
        ? `${o.alerts.open} incident${o.alerts.open === 1 ? "" : "s"} open, Vault is on them`
        : "Nothing needs you";
  const vBody = sensorUp
    ? `Sensor reporting · ${o.devices.online} of ${o.devices.total} devices online · ${fixed.automatic} fixed without you in ${fixed.days} days.`
    : "The sensor has not reported in the last 15 minutes, so everything below is as old as its last scan.";
  return (
    <div className="g-own-page">
      <Verdict tone={vTone} headline={vHead} body={vBody} />
      <section aria-label="Right now" className="g-own-tiles">
        <Kpi label="Assets watched" value={o.assets.total} unit="watched" tone="info"
             note={o.devices.unverified > 0
               ? `${o.devices.unverified} discovered, unverified`
               : `${o.devices.online} of ${o.devices.total} devices online now`} />
        <Kpi label="Open incidents" value={o.alerts.open} unit="open" tone="warn" quiet={o.alerts.open === 0}
             note={o.alerts.open === 0
               ? `${o.changed_24h} changes in the last 24 h, nothing open`
               : `${o.alerts.crit} critical · ${o.alerts.warn} warning`} />
        <Kpi label="Fixed automatically" value={fixed.automatic} unit="actions" tone="good"
             note={fixedTotal === 0
               ? `Nothing has needed fixing in ${fixed.days} days`
               : `${autoPct}% of everything settled in ${fixed.days} days`} />
        <Kpi label="Awaiting approval" value={o.needs_you.length} unit="queued" tone="warn" quiet={o.needs_you.length === 0}
             note={o.needs_you.length === 0 ? "Nothing is waiting on you" : o.needs_you[0].title} />
      </section>

      <div className="g-own-split">
        <div className="g-own-col">
          {/* Hidden when the queue is empty, as the reference says: a card
              reserved for problems that are not there teaches the reader to
              skip the region where problems appear. */}
          {o.needs_you.length > 0 && (
            <section aria-label="Waiting on you" className="g-own-section is-attn">
              <h2>
                <span className="g-own-dot g-tone-warn" aria-hidden="true" />Waiting on you
                <span className="g-own-meta g-own-meta-plain">Disruptive actions need approval</span>
              </h2>
              <ul className="g-own-rows">
                {o.needs_you.map((it) => (
                  <li key={it.key} className="g-own-wait">
                    <div className="g-own-wait-main">
                      <span className="g-own-wait-title">{it.title}</span>
                      {it.detail && <span className="g-own-wait-detail">{it.detail}</span>}
                      <span className="g-own-wait-meta">
                        {it.host && <span className="g-own-target">{it.host}</span>}
                        {/* The cost of saying yes, in the reference's mono
                            impact line. Never hidden behind a disclosure. */}
                        {it.cost && <span className="g-own-impact">{it.cost}</span>}
                      </span>
                    </div>
                    <div className="g-own-wait-actions">
                      <button type="button" className="g-own-btn" disabled={defer.isPending} onClick={() => defer.mutate(it)}>Defer</button>
                      <button type="button" className="g-own-btn g-own-btn-primary" disabled={approve.isPending} onClick={() => approve.mutate(it)}>
                        {pending?.key === it.key ? "Confirm" : "Approve"}
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section aria-label="Self-healing" className="g-own-section">
            <h2>Self-healing activity <span className="g-own-meta">last {rangeWord}</span></h2>
            <div className="g-own-heal">
              {/* A chart of a window in which nothing happened is 108px of
                  empty column plus a legend of two zeros -- four ways of
                  saying the same nothing. When there is nothing to plot the
                  sentence below carries it alone, and it carries the same
                  fact the chart's own `aria-label` did. */}
              {work.isPending ? (
                <p className="g-own-quiet">Reading the last {rangeWord}…</p>
              ) : fixesInWindow > 0 ? (
                /* The reference's bar chart, over the range the bar selected:
                   one column per hour or per day, each the fixes Vault
                   applied then -- plain divs, since eleven bars do not need a
                   charting dependency, stacked with Vault's own fixes in
                   blue over the ones a person approved in the muted step. */
                <>
              <div className="g-own-chart" role="img" aria-label={`${autoInWindow} fixed automatically and ${humanInWindow} after approval in the last ${rangeWord}`}>
                {buckets.map((b) => (
                  <div key={b.day} className="g-own-chart-bar" title={`${tickFor(b)}: ${b.auto} automatic, ${b.human} after approval`}>
                    {b.auto + b.human > 0 ? (
                      <>
                        {b.auto > 0 && <div className="g-own-chart-auto" style={{ height: `${Math.round((b.auto / bucketMax) * 100)}%` }} />}
                        {b.human > 0 && <div className="g-own-chart-human" style={{ height: `${Math.round((b.human / bucketMax) * 100)}%` }} />}
                      </>
                    ) : <div className="g-own-chart-none" />}
                  </div>
                ))}
              </div>
              <div className="g-own-chart-ticks">
                <span>{buckets[0] ? tickFor(buckets[0]) : ""}</span>
                <span>{buckets.length > 2 ? tickFor(buckets[Math.floor(buckets.length / 2)]) : ""}</span>
                <span>{buckets.length > 1 ? tickFor(buckets[buckets.length - 1]) : ""}</span>
              </div>
              <div className="g-own-chart-legend">
                <span className="g-own-chart-key"><span className="g-own-chart-swatch" style={{ background: "var(--own-act)" }} />Fixed automatically <b>{autoInWindow}</b></span>
                <span className="g-own-chart-key"><span className="g-own-chart-swatch" style={{ background: "var(--own-sunken)", border: "1px solid var(--own-rule)" }} />Fixed after approval <b>{humanInWindow}</b></span>
                <span className="g-own-chart-key g-own-quiet">last {rangeWord}</span>
              </div>
                </>
              ) : null}
              <p className="g-own-note">
                {fixesInWindow === 0
                  ? `Nothing was settled in the last ${rangeWord}.`
                  : `${Math.round((autoInWindow / fixesInWindow) * 100)}% of what was settled in the last ${rangeWord} needed nobody's approval.`}
                {fixedTotal > 0 && ` Over ${fixed.days} days: ${fixed.automatic} automatic, ${fixed.approved} after approval (${autoPct}% automatic).`}
              </p>
            </div>
          </section>

          <section aria-label="Live activity" className="g-own-section">
            <h2>
              Live activity <span className="g-own-dot g-tone-good g-own-dot-live" aria-hidden="true" />
              <Link to="/owner/audit" className="g-own-meta g-own-meta-link">Full log</Link>
            </h2>
            {activity.length === 0 ? (
              <p className="g-own-quiet">{hist.isPending ? "Reading the window…" : `Nothing in the last ${rangeWord}.`}</p>
            ) : (
              <ul className="g-own-rows">
                {activity.map((t, i) => {
                  const tag = tagFor(t);
                  return (
                    <li key={`${t.ts}-${i}`} className="g-own-act">
                      <span className="g-own-act-time">{range === "24h" ? clock(t.ts) : `${shortDate(t.ts)} ${clock(t.ts)}`}</span>
                      <span className={`g-own-dot g-tone-${tag.tone === "crit" ? "alert" : tag.tone === "warn" ? "warn" : tag.tone === "good" ? "good" : "plain"}`} aria-hidden="true" />
                      <span className="g-own-act-text">
                        {t.kind}{t.entity ? ` · ${t.entity}` : ""}
                        {t.detail && <span className="g-own-act-detail"> — {t.detail}</span>}
                      </span>
                      <span className={`g-own-pill g-own-pill-${tag.tone}`}>{tag.label}</span>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        </div>

        <div className="g-own-col g-own-col-side">
          <section aria-label="Coverage" className="g-own-section">
            <h2>Coverage <span className="g-own-meta">what the sensor can see</span></h2>
            <div><CoveragePanel rows={o.coverage} /></div>
          </section>

          <section aria-label="Certificates expiring" className="g-own-section">
            <h2>Certificates expiring <span className="g-own-meta">90 d</span></h2>
            <div><CertsExpiringPanel rows={o.certs_expiring} read={certs?.seen ?? 0} sites={certs?.total ?? 0} /></div>
          </section>

          <section aria-label="Sensor health" className="g-own-section">
            <h2>Sensor health</h2>
            <dl className="g-own-grid2">
              <div><dt>Uptime</dt><dd className={sensorUp ? "" : "g-own-warn"}>{sensorUp ? agoS(o.health.run_seconds) : "silent"}</dd></div>
              <div><dt>Last sweep</dt><dd>{agoS(o.age_s)}</dd></div>
              <div><dt>Agent</dt><dd>{o.sensor?.version || "—"}</dd></div>
              <div><dt>Queue</dt><dd>{held}</dd></div>
            </dl>
          </section>
        </div>
      </div>
    </div>
  );
}

export default Overview;
