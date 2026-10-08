import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Chart } from "../design";
import { accountPayload, accountTrends, adjustAccountBilling } from "../lib/api";
import type { AccountBilling, AccountPayload, AccountSensor, AccountTrend } from "../types";
import type { BillingAdjust } from "../lib/api";

/**
 * One customer, and what is actually set up for them.
 *
 * The Ledger and the Fleet both answer questions ACROSS customers. This is the
 * one screen about a single customer's own configuration, and it exists
 * because almost every question a customer asks turns out to be a question
 * about their setup rather than about their network: "why did nobody tell me"
 * is answered by the integrations list, "why is that device missing" by the
 * networks list, and "who changed that" by the trail at the bottom.
 *
 * It shows what is wired and never what it authenticates with. There is no
 * integration secret, no sensor token and no enrol code here, because the
 * payload behind it has no field for them -- see
 * `hub/tests/test_account_payload.py`, which fails if one is ever added. An
 * enrol code in particular is a bearer credential: anyone holding it can add a
 * sensor to this tenant.
 */

function ago(iso: string): string {
  if (!iso) return "never";
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms)) return "never";
  const m = Math.round(ms / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

function when(iso: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

/** A sensor that has not spoken recently is the single fact that invalidates
 *  everything else on this page, so it is toned rather than merely listed. */
function sensorTone(s: AccountSensor): "good" | "warn" | "crit" {
  if (s.status !== "active") return s.status === "revoked" ? "crit" : "warn";
  if (!s.last_seen) return "crit";
  const ms = Date.now() - new Date(s.last_seen).getTime();
  if (Number.isNaN(ms)) return "crit";
  if (ms > 3 * 3600_000) return "crit";
  if (ms > 900_000) return "warn";
  return "good";
}

/**
 * The charts, and what each one says.
 *
 * Its own query, not part of the account payload: the setup lists are small
 * and immediate, a month of series is neither, and the page is useful before
 * this arrives. So a slow trend never delays the answer to "who is on this
 * account".
 *
 * It renders nothing at all when there are no trends -- not an empty state,
 * not a placeholder axis. A chart frame with no line in it reads as "zero",
 * and "no alerts this month" and "we have no data for this month" are very
 * different things to say to a customer sitting next to you.
 */
function Trends({ tid }: { tid: string }) {
  const q = useQuery({
    queryKey: ["account-trends", tid],
    queryFn: () => accountTrends(tid),
    enabled: Boolean(tid),
  });

  if (q.isPending) return <p className="g-own-quiet" role="status">Reading the last 30 days…</p>;
  // A failure here is not the page failing. The configuration below still
  // answers most of what this screen is opened for, so it says so quietly
  // rather than taking over with an alert.
  if (q.isError || !q.data || q.data.trends.length === 0) {
    return (
      <p className="g-own-quiet">
        No trend to show for the last 30 days yet — that needs a sensor that has been
        reporting for a while.
      </p>
    );
  }

  const { days, trends, headline } = q.data;
  return (
    <>
      {headline && <p className="g-own-lede">{headline}</p>}
      <div className="g-own-charts">
        {trends.map((t: AccountTrend) => (
          <figure key={t.chart.key} className="g-own-chartbox">
            <figcaption className="g-own-charthead">
              <span className="g-own-chartlabel">
                {t.chart.label}
                {t.chart.unit && <span className="g-own-quiet"> ({t.chart.unit})</span>}
              </span>
              {t.direction && (
                <span className={`g-own-pill g-own-dir-${t.direction}`}>{t.direction}</span>
              )}
            </figcaption>
            <Chart
              series={t.chart.series}
              height={150}
              yMax={t.chart.max ?? undefined}
              aria-label={t.says || t.chart.label}
            />
            {/* The sentence is the point of the section: the owner reads this
                line out, and the chart is the evidence for it. */}
            {t.says && <p className="g-own-says">{t.says}</p>}
          </figure>
        ))}
      </div>
      <p className="g-own-note">
        The last {days} days, and the same figures this customer sees on their own History
        tab — not a separate calculation.
      </p>
    </>
  );
}

/** Cents to money, identical to the Billing roster's formatter so the same
 *  account reads the same on both screens. */
function money(cents: number): string {
  return (cents / 100).toLocaleString(undefined, {
    style: "currency", currency: "USD",
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  });
}

/**
 * What this customer pays, beside what they have.
 *
 * The owner opens this page sitting with the customer or just before phoning
 * them, and "what are they on, and are they current" is one of the first two
 * questions in that conversation. Sending them to the Billing roster and back
 * to answer it is the trip this card removes.
 *
 * Status leads, not the amount. A customer paying $49 whose card just bounced
 * needs the owner today; one paying $9 happily does not, and a card that led
 * with the money would rank them the wrong way round. Red is spent only on
 * `past_due` -- the console's rule is that red means somebody needs you now.
 */
//: The plans the owner may grant. Ids and names track `plans.py`; the hub
//: refuses anything else with a 422, so a stale entry here fails loudly.
const GRANTABLE = [
  ["home", "Vault Home"],
  ["business_essentials", "Business Essentials"],
  ["business_pro", "Business Pro"],
  ["partner", "Partner / MSP"],
] as const;

/** Extend a trial or grant a plan with no charge. Hidden for a customer
 *  Stripe is billing -- the hub would refuse anyway, and the right control
 *  for them is the Stripe link. Every write takes a second, explicit click:
 *  nothing on this page changes what a customer gets on one press. */
function Adjust({ tid, b, onDone }: { tid: string; b: AccountBilling; onDone: (p: AccountPayload) => void }) {
  const [err, setErr] = useState("");
  const [plan, setPlan] = useState<string>("home");
  const [interval, setInterval] = useState<"month" | "year">("month");
  const [arm, setArm] = useState<null | { action: "extend_trial"; days: number } | { action: "grant_plan"; plan: string; interval: "month" | "year" }>(null);
  const m = useMutation({
    mutationFn: (body: BillingAdjust) => adjustAccountBilling(tid, body),
    onSuccess: (p) => { setArm(null); setErr(""); onDone(p); },
    // The hub's message is written to be shown: for a Stripe-billed customer
    // it names the subscription and carries the dashboard link. Kept inline
    // beside the control, the way Settings keeps a failed save beside its
    // field -- a toast leaves and the reason should not.
    onError: (e: Error) => { setArm(null); setErr(e.message); },
  });
  const billedByStripe = Boolean(b.stripe_customer_id) && (b.status === "active" || b.status === "past_due");
  if (billedByStripe) return null;

  return (
    <div className="g-own-adjust">
      <div className="g-own-adjust-row">
        <button type="button" className="g-own-btn" disabled={m.isPending}
                onClick={() => setArm({ action: "extend_trial", days: 14 })}>
          Extend trial by 14 days
        </button>
      </div>
      <div className="g-own-adjust-row">
        <label>
          Grant plan{" "}
          <select value={plan} onChange={(e) => setPlan(e.target.value)}>
            {GRANTABLE.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
          </select>
        </label>
        <label>
          Billed{" "}
          <select value={interval} onChange={(e) => setInterval(e.target.value as "month" | "year")}>
            <option value="month">monthly</option>
            <option value="year">yearly</option>
          </select>
        </label>
        <button type="button" className="g-own-btn" disabled={m.isPending}
                onClick={() => setArm({ action: "grant_plan", plan, interval })}>
          Grant, no charge
        </button>
      </div>
      {err && <p className="g-own-crit" role="alert">{err}</p>}
      {arm && (
        <p className="g-own-note" role="status">
          {arm.action === "extend_trial"
            ? `Extend this customer's trial by ${arm.days} days?`
            : `Put this customer on ${GRANTABLE.find(([id]) => id === arm.plan)?.[1] ?? arm.plan}, billed ${arm.interval === "year" ? "yearly" : "monthly"}, with nothing to pay?`}{" "}
          <button type="button" className="g-own-btn" disabled={m.isPending} onClick={() => m.mutate(arm)}>Confirm</button>{" "}
          <button type="button" className="g-own-btn" onClick={() => setArm(null)}>Cancel</button>
        </p>
      )}
    </div>
  );
}

function Billing({ tid, b, onDone }: { tid: string; b: AccountBilling | null; onDone: (p: AccountPayload) => void }) {
  if (b === null) {
    return (
      <p className="g-own-quiet">
        No subscription on this account. Set up by hand before billing existed —
        they are watched, and nothing is being charged.
      </p>
    );
  }
  const failing = b.status === "past_due";
  const trialing = b.status === "trialing";
  const ending = b.trial_days_left !== null && b.trial_days_left <= 14;

  return (
    <>
      <dl className="g-own-facts">
        <div>
          <dt>Plan</dt>
          <dd>
            {b.plan_label}
            {b.per_site && b.mrr_cents > 0 && <span className="g-own-quiet"> · per site</span>}
          </dd>
        </div>
        <div>
          <dt>Status</dt>
          <dd className={failing ? "g-own-crit" : trialing && ending ? "g-own-warn" : undefined}>
            {failing ? "payment failed" : b.status || "—"}
          </dd>
        </div>
        <div>
          <dt>Billed</dt>
          <dd>{b.interval === "month" ? "monthly" : b.interval === "year" ? "yearly" : "—"}</dd>
        </div>
        <div>
          <dt>{b.per_site && b.mrr_cents > 0 ? "Monthly, at least" : "Monthly"}</dt>
          <dd>{b.mrr_cents > 0 ? money(b.mrr_cents) : "—"}</dd>
        </div>
        {b.trial_days_left !== null && (
          <div>
            <dt>Trial</dt>
            <dd className={ending ? "g-own-warn" : undefined}>
              {b.trial_days_left <= 0 ? "ended" : `${b.trial_days_left}d left`}
            </dd>
          </div>
        )}
        {b.period_end && (
          <div>
            {/* Named for what it MEANS rather than for the field: the same date
                is a renewal or a last day of service depending on one flag, and
                "period end" beside a cancelling account reads as neither. */}
            <dt>{b.cancel_at_period_end ? "Service ends" : "Renews"}</dt>
            <dd className={b.cancel_at_period_end ? "g-own-warn" : undefined}>
              {when(b.period_end).split(",")[0]}
            </dd>
          </div>
        )}
      </dl>
      {b.status === "active" && !b.stripe_customer_id && (
        <p className="g-own-quiet">
          {/* A plan granted from this console: Vault's own record, no Stripe
              subscription behind it. Said plainly so a partner deal never reads
              as a paying customer, and so the zero above is not a bug. */}
          Granted here, no charge — nothing is billed for this plan.
        </p>
      )}
      {b.stripe_customer_id && (
        <p className="g-own-note">
          {/* Invoices, refunds and what actually cleared live in Stripe; this
              console reads Vault's own records and says so rather than
              growing a second, laggier copy of the same ledger. Charged plans
              change there too, which is why the control below is absent. */}
          Invoices and payments for this customer are in Stripe, under{" "}
          <span className="g-own-mono">{b.stripe_customer_id}</span>.{" "}
          {b.stripe_dashboard_url && (
            <a href={b.stripe_dashboard_url} target="_blank" rel="noreferrer">Manage in Stripe →</a>
          )}
        </p>
      )}
      <Adjust tid={tid} b={b} onDone={onDone} />
    </>
  );
}

function Account() {
  const { tid = "" } = useParams();
  const queryClient = useQueryClient();
  const q = useQuery({
    queryKey: ["account", tid],
    queryFn: () => accountPayload(tid),
    enabled: Boolean(tid),
  });
  const a = q.data;

  if (q.isPending) return <p role="status">Loading the account…</p>;
  if (q.isError && !a) {
    return (
      <div role="alert">
        Could not read this account. <button onClick={() => void q.refetch()}>Retry</button>{" "}
        <Link to="/owner/ledger">Back to the ledger</Link>
      </div>
    );
  }

  const devicesOnNetworks = a!.networks.reduce((n, x) => n + x.devices, 0);

  return (
    <div className="g-own-page">
      <header className="g-own-head">
        <p className="g-own-crumb"><Link to="/owner/ledger">Ledger</Link></p>
        <p className="g-own-lede">
          <span className="g-own-mono">{a!.slug}</span>
          {a!.created && <> · customer since {when(a!.created).split(",")[0]}</>}
        </p>
      </header>

      <section aria-label="Billing" className="g-own-section">
        <h2>What they pay</h2>
        <Billing tid={tid} b={a!.billing} onDone={(p) => queryClient.setQueryData(["account", tid], p)} />
      </section>

      <section aria-label="How it has been going" className="g-own-section">
        <h2>How it has been going</h2>
        <Trends tid={tid} />
      </section>

      <section aria-label="Networks" className="g-own-section">
        <h2>Networks</h2>
        {a!.networks.length === 0 ? (
          <p className="g-own-quiet">
            No network named yet. Devices still arrive; they simply have nowhere to sit on the map.
          </p>
        ) : (
          <ul className="g-own-chips">
            {a!.networks.map((n) => (
              <li key={n.id} className="g-own-chip">
                <b>{n.devices}</b> {n.name} <span className="g-own-slug">{n.slug}</span>
              </li>
            ))}
          </ul>
        )}
        {a!.devices_unassigned > 0 && (
          <p className="g-own-quiet">
            {/* Not an error, but invisible on the map, which is exactly the
                shape of "why can't I see that device". */}
            {a!.devices_unassigned} device{a!.devices_unassigned === 1 ? " is" : "s are"} on no
            network, so {a!.devices_unassigned === 1 ? "it does" : "they do"} not appear on their map.
            {devicesOnNetworks > 0 && ` ${devicesOnNetworks} sit on a named network.`}
          </p>
        )}
      </section>

      <section aria-label="Sensors" className="g-own-section">
        <h2>Sensors</h2>
        {a!.sensors.length === 0 ? (
          <p className="g-own-quiet">
            Nothing enrolled. Every figure Vault could show this customer depends on a sensor,
            so until one reports there is nothing to show them.
          </p>
        ) : (
          <div className="g-own-tablewrap">
            <table className="g-own-table">
              <thead>
                <tr>
                  <th scope="col">Sensor</th>
                  <th scope="col">Platform</th>
                  <th scope="col">Version</th>
                  <th scope="col">Status</th>
                  <th scope="col">Last seen</th>
                </tr>
              </thead>
              <tbody>
                {a!.sensors.map((s) => {
                  const tone = sensorTone(s);
                  return (
                    <tr key={s.id} className={tone === "good" ? "" : `g-own-tr-${tone}`}>
                      <th scope="row"><span className="g-own-name">{s.name}</span></th>
                      <td>{s.platform || <span className="g-own-quiet">—</span>}</td>
                      <td className="g-own-mono">{s.version || "—"}</td>
                      <td><span className={`g-own-pill g-own-pill-${tone}`}>{s.status || "unknown"}</span></td>
                      <td className="g-own-mono">{ago(s.last_seen)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section aria-label="People" className="g-own-section">
        <h2>People</h2>
        <div className="g-own-tablewrap">
          <table className="g-own-table">
            <thead>
              <tr>
                <th scope="col">Who</th>
                <th scope="col">Email</th>
                <th scope="col">Role</th>
              </tr>
            </thead>
            <tbody>
              {a!.users.map((u) => (
                <tr key={u.username}>
                  <th scope="row">
                    <span className="g-own-name">{u.display_name || u.username}</span>
                    <span className="g-own-slug">{u.username}</span>
                  </th>
                  <td className="g-own-mono">{u.email || "—"}</td>
                  <td>
                    {u.role}
                    {/* Worth calling out: a superadmin is a member of every
                        tenant, so seeing one here does not mean this customer
                        invited them. */}
                    {u.superadmin && <span className="g-own-pill g-own-pill-warn"> platform owner</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section aria-label="Integrations" className="g-own-section">
        <h2>Where their alerts go</h2>
        {a!.integrations.length === 0 ? (
          <p className="g-own-quiet">
            No destination wired. Vault still records everything; nothing interrupts anyone.
            This is the usual answer to “why did nobody tell me?”.
          </p>
        ) : (
          <ul className="g-own-chips">
            {a!.integrations.map((i) => (
              <li key={i.kind} className="g-own-chip">{i.kind}</li>
            ))}
          </ul>
        )}
        <p className="g-own-note">
          Which channels are wired, never what they authenticate with — no token or webhook
          secret is readable from this console.
        </p>
      </section>

      <section aria-label="Recent activity" className="g-own-section">
        <h2>Recently, on this account</h2>
        {a!.recent.length === 0 ? (
          <p className="g-own-quiet">Nothing recorded yet.</p>
        ) : (
          <div className="g-own-tablewrap">
            <table className="g-own-table">
              <thead>
                <tr>
                  <th scope="col">When</th>
                  <th scope="col">Who</th>
                  <th scope="col">Did</th>
                  <th scope="col">To</th>
                </tr>
              </thead>
              <tbody>
                {a!.recent.map((r, i) => (
                  <tr key={`${r.ts}-${i}`}>
                    <th scope="row"><span className="g-own-mono">{when(r.ts)}</span></th>
                    <td>{r.user}{r.role && <span className="g-own-slug"> {r.role}</span>}</td>
                    <td className="g-own-mono">{r.action}</td>
                    <td className="g-own-mono">{r.target || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

export default Account;
