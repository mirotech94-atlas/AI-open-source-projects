import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { platformPayload } from "../lib/api";
import type { PlatformTenantRow } from "../types";

/**
 * The money: what recurs every month, who it comes from, and what is at risk.
 *
 * This page used to be a plan-and-trial roster that said, in a footnote, that
 * it knew nothing about revenue. That was honest and it was also the wrong
 * shape for the only screen in the product whose subject is the business: an
 * owner opening "Billing" is asking "what am I earning", and a page that
 * answers "here is who is on which plan" makes them do the arithmetic in
 * their head, every time, from a table.
 *
 * So the figure is computed and shown. What it is NOT is cash collected.
 * Vault's own records know which plan each customer is on and at which
 * interval; Stripe knows which invoices actually cleared. Those two disagree
 * for a few days after every failed payment, and a page that blurred them
 * would be worse than one that showed neither. The note at the foot says
 * which of the two this is, and the `past_due` tile is exactly the population
 * where they diverge -- shown as its own number rather than quietly removed
 * from the total, because a customer whose card just bounced is still a
 * customer and usually still pays.
 *
 * The trial column keeps its place. A trial that lapses unnoticed is the one
 * failure on this screen that costs the business rather than the customer.
 */

/** Cents to money. Grouped thousands, always two decimals, so a column of
 *  these lines up under `font-variant-numeric: tabular-nums` and can be read
 *  down rather than one row at a time. */
function money(cents: number): string {
  return (cents / 100).toLocaleString(undefined, {
    style: "currency", currency: "USD",
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  });
}

/** Whole dollars, for the annualised figure where the cents are noise. */
function moneyWhole(cents: number): string {
  return (cents / 100).toLocaleString(undefined, {
    style: "currency", currency: "USD",
    minimumFractionDigits: 0, maximumFractionDigits: 0,
  });
}

/** Trials first and soonest-first inside them, because that is the order the
 *  work has to be done in. `null` means no trial, which sorts last. */
function trialRank(r: PlatformTenantRow): number {
  return r.trial_days_left === null ? Number.POSITIVE_INFINITY : r.trial_days_left;
}

function planLabel(r: PlatformTenantRow): string {
  if (!r.plan) return "no subscription";
  if (r.plan === "trial") return r.plan_status === "paused" ? "trial ended" : "trial";
  return r.plan.replace(/_/g, " ");
}

function intervalLabel(r: PlatformTenantRow): string {
  if (r.interval === "month") return "monthly";
  if (r.interval === "year") return "yearly";
  return "—";
}

function Billing() {
  const nav = useNavigate();
  const q = useQuery({ queryKey: ["platform"], queryFn: platformPayload, refetchInterval: 60_000 });
  const p = q.data;

  const rows = useMemo(
    () => [...(p?.tenants ?? [])].sort((a, b) =>
      // Paying customers first and biggest first inside them: this is the
      // money page, so the rows that make the money go at the top. Trials
      // follow in the order they expire, which is the order they need work.
      b.mrr_cents - a.mrr_cents || trialRank(a) - trialRank(b) || a.name.localeCompare(b.name)),
    [p],
  );

  /** Plan → how many customers, and what they are worth together. The count
   *  alone could not distinguish four trials from four Partner accounts. */
  const byPlan = useMemo(() => {
    const m = new Map<string, { n: number; cents: number }>();
    for (const r of p?.tenants ?? []) {
      const k = planLabel(r);
      const cur = m.get(k) ?? { n: 0, cents: 0 };
      m.set(k, { n: cur.n + 1, cents: cur.cents + r.mrr_cents });
    }
    return [...m.entries()].sort((a, b) => b[1].cents - a[1].cents || b[1].n - a[1].n);
  }, [p]);

  if (q.isPending) return <p role="status">Loading billing…</p>;
  if (q.isError && !p) {
    return (
      <div role="alert">
        Could not read billing. <button onClick={() => void q.refetch()}>Retry</button>
      </div>
    );
  }

  const t = p!.totals;

  // A fault at zero is drawn quiet; a fact keeps its weight at any value.
  // Revenue is a fact -- "$0.00 a month" is the single most important thing
  // on this page when it is true, and muting it would be the console lying
  // by typography. Past due and trials ending are faults.
  const tiles = [
    { label: t.mrr_is_floor ? "Monthly recurring, at least" : "Monthly recurring",
      text: money(t.mrr_cents), kind: "fact" as const, calm: false },
    { label: "Annualised", text: moneyWhole(t.mrr_cents * 12), kind: "fact" as const, calm: false },
    { label: t.paying === 1 ? "Paying customer" : "Paying customers", text: String(t.paying), kind: "fact" as const, calm: false },
    { label: "Payment failed", text: String(t.past_due), kind: "crit" as const, calm: t.past_due === 0 },
    { label: "Trials ending", text: String(t.trials_ending), kind: "warn" as const, calm: t.trials_ending === 0 },
  ];

  return (
    <div className="g-own-page">
      <header className="g-own-head">
        <p className="g-own-lede">
          {t.paying === 0
            ? "Nobody is being charged yet."
            : `${money(t.mrr_cents)} a month from ${t.paying} ${t.paying === 1 ? "customer" : "customers"}.`}
          {t.past_due > 0 && ` ${t.past_due} ${t.past_due === 1 ? "payment has" : "payments have"} failed.`}
          {t.trials_ending > 0 && ` ${t.trials_ending} ${t.trials_ending === 1 ? "trial ends" : "trials end"} within a fortnight.`}
          {t.paused > 0 && ` ${t.paused} ${t.paused === 1 ? "account is" : "accounts are"} paused.`}
        </p>
      </header>

      <section aria-label="Revenue" className="g-own-tiles">
        {tiles.map((tile) => (
          <div
            key={tile.label}
            className={`g-own-tile g-own-tile-static ${tile.calm ? "g-own-tile-calm" : `g-own-tile-${tile.kind}`}`}
          >
            <span className="g-own-tile-top">
              <span className="g-own-tile-l">{tile.label}</span>
              <span className={`g-own-tile-dot g-tone-${tile.calm ? "plain" : tile.kind === "crit" ? "alert" : tile.kind === "warn" ? "warn" : "good"}`} aria-hidden="true" />
            </span>
            <span className="g-own-tile-v g-own-tile-money">{tile.text}</span>
          </div>
        ))}
      </section>

      <section aria-label="Plans in use" className="g-own-section">
        <h2>Plans in use</h2>
        <ul className="g-own-chips">
          {byPlan.map(([plan, v]) => (
            <li key={plan} className="g-own-chip">
              <b>{v.n}</b> {plan}
              {v.cents > 0 && <span className="g-own-chip-sum"> · {money(v.cents)}/mo</span>}
            </li>
          ))}
        </ul>
      </section>

      <section aria-label="Accounts" className="g-own-section">
        <h2>Accounts <span className="g-own-meta">{rows.length} {rows.length === 1 ? "account" : "accounts"}</span></h2>
        <div className="g-own-tablewrap">
          <table className="g-own-table">
            <thead>
              <tr>
                <th scope="col">Customer</th>
                <th scope="col">Plan</th>
                <th scope="col">Billed</th>
                <th scope="col" className="g-own-num">Monthly</th>
                <th scope="col">Trial</th>
                <th scope="col" className="g-own-num">Users</th>
                <th scope="col" className="g-own-num">Devices</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const ending = r.trial_days_left !== null && r.trial_days_left <= 14;
                const over = r.trial_days_left !== null && r.trial_days_left <= 0;
                const failing = r.plan_status === "past_due";
                return (
                  <tr key={r.id} className={over || failing ? "g-own-tr-crit" : ending ? "g-own-tr-warn" : ""}>
                    <th scope="row">
                      <button type="button" className="g-own-linkish" onClick={() => nav(`/owner/accounts/${r.id}`)}>
                        <span className="g-own-name">{r.name}</span>
                      </button>
                      <span className="g-own-slug">{r.slug}</span>
                    </th>
                    <td>
                      {planLabel(r)}
                      {failing && <span className="g-own-crit"> · payment failed</span>}
                    </td>
                    <td className="g-own-quiet">{intervalLabel(r)}</td>
                    {/* A per-site figure is one site's worth, marked so, because
                        the hub does not know how many were bought. The mark is
                        on the row rather than only in the footnote: the person
                        reading a column of numbers is not reading the footnote. */}
                    <td className="g-own-num g-own-mono">
                      {r.mrr_cents > 0
                        ? <>{money(r.mrr_cents)}{r.per_site && <span className="g-own-quiet" title="per site — the hub does not meter sites, so this is one site's worth">+</span>}</>
                        : <span className="g-own-quiet">—</span>}
                    </td>
                    <td className="g-own-mono">
                      {r.trial_days_left === null
                        ? <span className="g-own-quiet">—</span>
                        : over
                          ? <span className="g-own-crit">ended</span>
                          : <span className={ending ? "g-own-warn" : undefined}>{r.trial_days_left}d left</span>}
                    </td>
                    <td className="g-own-num g-own-mono">{r.users}</td>
                    <td className="g-own-num g-own-mono">{r.devices}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <p className="g-own-note">
        {/* Said out loud rather than left to be discovered. */}
        These figures are what customers are signed up to pay, from Vault's
        own records — not what has cleared. A yearly plan is counted at a
        twelfth of its price, because a year here costs ten months.
        {" "}
        {t.mrr_is_floor
          ? "A plan priced per site is counted at one site and marked +: the quantity a customer bought lives on their Stripe subscription, and Vault does not meter sites, so the total is a floor."
          : "Every plan in use is priced per account, so the total is exact."}
        {" "}
        Invoices, refunds and the money actually banked live in Stripe.
        {p!.billing_mode ? ` Billing mode: ${p!.billing_mode}.` : " Billing is not configured."}
      </p>
    </div>
  );
}

export default Billing;
