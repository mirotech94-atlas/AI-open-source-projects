// Billing — the tenant's plan, what it costs, when it renews or ends, and the
// three buttons that change it. Admin-only, matching the hub
// (`routers/billing.py` gates every route on `require_role("admin")`): an
// admin is who buys and cancels, and a page an operator can see but never act
// on is a page that only frustrates them.
//
// Two rules this file follows without exception:
//
// 1. **No price is written here.** Every number and every label arrives on
//    `BillingPayload`, derived hub-side from `plans.py`. A price duplicated
//    into the UI is a price that goes stale the day it changes, and it goes
//    stale silently.
// 2. **Nothing is "saved" that the backend did not save.** The upgrade and
//    portal buttons hand off to Stripe's hosted pages by redirect; the state
//    they produce comes back through the webhook, not from this component
//    optimistically deciding it worked.

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Badge, useToast } from "../../design";
import { billingCheckout, billingPayload, billingPortal, startTrial } from "../../lib/api";
import type { Role } from "../../lib/auth";
import type { Tone } from "../../lib/format";
import { atLeast } from "../../lib/auth";
import type { BillingPayload, BillingPlanCard, BillingSubscription } from "../../types";
import { ADMIN_TOOLTIP, ConfirmDialog, RoleNotice } from "./shared";

export type BillingProps = { role: Role | null };

type Interval = "month" | "year";

// Vault's own status vocabulary (see `models.Subscription`), mapped to the
// badge tone and the words an admin reads. `past_due` is deliberately a
// warning and not an alert: nothing about the monitoring has degraded.
const STATUS: Record<string, { label: string; tone: Tone }> = {
  trialing: { label: "Free trial", tone: "plain" },
  active: { label: "Active", tone: "good" },
  past_due: { label: "Payment failed", tone: "warn" },
  paused: { label: "Paused", tone: "warn" },
  canceled: { label: "Cancelled", tone: "warn" },
};

function formatDate(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" });
}

function priceOf(plan: BillingPlanCard, interval: Interval): string {
  if (plan.is_trial) return "Free";
  return interval === "year" ? plan.yearly_label : plan.monthly_label;
}

/** What the current subscription entitles the tenant to right now, said
 *  plainly. This is the only place the product talks about money, so it says
 *  what happens next rather than pressing. */
function CurrentPlan({ sub }: { sub: BillingSubscription }) {
  const status = STATUS[sub.status] ?? { label: sub.status, tone: "plain" as const };
  return (
    <dl className="g-billing-current">
      <div className="g-settings-summary-row">
        <dt>Plan</dt>
        <dd>
          {sub.plan_name} <Badge tone={status.tone}>{status.label}</Badge>
        </dd>
      </div>
      <div className="g-settings-summary-row">
        <dt>Price</dt>
        <dd>
          {sub.price_label || "—"}
          {sub.interval === "year" ? " (billed yearly)" : sub.interval === "month" ? " (billed monthly)" : ""}
        </dd>
      </div>
      {sub.trial_end ? (
        <div className="g-settings-summary-row">
          <dt>Trial ends</dt>
          <dd>
            {formatDate(sub.trial_end)}
            {sub.trial_days_left !== null
              ? ` — ${sub.trial_days_left} day${sub.trial_days_left === 1 ? "" : "s"} left`
              : ""}
          </dd>
        </div>
      ) : null}
      {sub.current_period_end ? (
        <div className="g-settings-summary-row">
          <dt>{sub.cancel_at_period_end ? "Ends" : "Renews"}</dt>
          <dd>{formatDate(sub.current_period_end)}</dd>
        </div>
      ) : null}
      <div className="g-settings-summary-row">
        <dt>Sensors</dt>
        <dd>
          {sub.ingesting
            ? "Reporting normally."
            : "Not storing new data. Everything already collected is still here."}
        </dd>
      </div>
    </dl>
  );
}

/** The plan and interval a Subscribe link on guarder.io asked for.
 *
 *  Nothing here trusts the values: the plan id is matched against the loaded
 *  catalogue before it is used, and an interval that is not one of the two is
 *  ignored. A URL cannot name a price, only a plan the hub already sells. */
function readOpenedPlan(): { plan: string | null; interval: Interval | null } {
  if (typeof window === "undefined") return { plan: null, interval: null };
  const q = new URLSearchParams(window.location.search);
  const raw = q.get("interval");
  return {
    plan: q.get("plan"),
    interval: raw === "month" || raw === "year" ? raw : null,
  };
}

export function Billing({ role }: BillingProps) {
  const toast = useToast();
  const canWrite = atLeast(role, "admin");
  // The marketing site's Subscribe links arrive here as
  // `?plan=business_pro&interval=year`, so the visitor lands on the plan and
  // the interval they were reading about rather than on a page that has
  // forgotten what they clicked. Read once, at mount: after that the page's
  // own controls own the state.
  const opened = readOpenedPlan();
  const [interval, setInterval] = useState<Interval>(opened.interval ?? "month");
  // Kept as the raw string the field holds, not a number: coercing on every
  // keystroke means the field can never be cleared to retype (an empty box
  // snaps back to 1, and the next digit lands after it). It is clamped once,
  // below, where the value is actually used.
  const [sites, setSites] = useState("1");
  const [confirming, setConfirming] = useState<BillingPlanCard | null>(null);
  const preselected = useRef(false);

  const siteCount = Math.min(500, Math.max(1, Number.parseInt(sites, 10) || 1));

  const query = useQuery<BillingPayload>({ queryKey: ["billing"], queryFn: billingPayload });
  const data = query.data;

  // Open the confirmation for the plan named in the URL, once the plan cards
  // have actually loaded. It stops at the confirmation rather than going
  // straight to Stripe: a link on a public page must not be able to start a
  // payment on its own, and the reader still has to see the price and the site
  // count before they commit.
  useEffect(() => {
    if (preselected.current || !opened.plan || !data || !canWrite) return;
    const card = data.plans.find((p) => p.id === opened.plan && !p.is_trial);
    if (!card) return;
    preselected.current = true;
    setConfirming(card);
  }, [data, opened.plan, canWrite]);

  const trialMutation = useMutation({
    mutationFn: () => startTrial(),
    onSuccess: () => {
      void query.refetch();
      toast.show("Your three free months have started. No card was taken.", "good");
    },
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  // Both of these end in a redirect to Stripe rather than a state change here:
  // the subscription only really changes when Stripe says so, over the webhook.
  const checkoutMutation = useMutation({
    mutationFn: (plan: BillingPlanCard) =>
      billingCheckout(plan.id, interval, plan.per_site ? siteCount : 1),
    onSuccess: (res) => {
      setConfirming(null);
      window.location.assign(res.url);
    },
    onError: (err: Error) => {
      setConfirming(null);
      toast.show(err.message, "alert");
    },
  });

  const portalMutation = useMutation({
    mutationFn: () => billingPortal(),
    onSuccess: (res) => window.location.assign(res.url),
    onError: (err: Error) => toast.show(err.message, "alert"),
  });

  if (query.isLoading) return <p className="hint">Loading billing…</p>;
  if (query.isError || !data) {
    return <p className="g-settings-role-notice">Billing could not be loaded. Try again shortly.</p>;
  }

  const sub = data.subscription;
  const busy = checkoutMutation.isPending || portalMutation.isPending || trialMutation.isPending;
  const saving = data.yearly_months_charged > 0 ? 12 - data.yearly_months_charged : 0;

  // Trial-only hub: one card, no plans, no interval toggle. The hub decides
  // this (`config.billing_mode`), so offering paid plans later is a setting
  // rather than a change to this page. The trial itself is unchanged -- it
  // still runs `trial_days` and still pauses at the end, and this branch says
  // so rather than implying the product is free forever.
  if (data.trial_only) {
    const left = sub?.trial_days_left ?? null;
    const status = sub ? STATUS[sub.status] ?? { label: sub.status, tone: "plain" as const } : null;
    return (
      <div className="g-settings-billing" data-testid="billing-trial-only">
        {!canWrite ? <RoleNotice role={role} required="admin" /> : null}
        {data.notice ? (
          <p className="g-billing-notice" role="status">
            {data.notice}
          </p>
        ) : null}
        <section className="g-billing-section">
          <h3>Your plan</h3>
          <article className="g-billing-plan is-current">
            <h4>
              Free trial
              {status ? <Badge tone={status.tone}>{status.label}</Badge> : null}
            </h4>
            <p className="g-billing-price">Free</p>
            <p className="g-billing-blurb">
              Everything Vault does — the sensor, the dashboard, the alerts and the automatic
              repairs — for {data.trial_days} days, with no payment method on file.
            </p>
            {sub ? (
              <>
                {left !== null ? (
                  <p className="hint">
                    {left} day{left === 1 ? "" : "s"} left
                    {sub.trial_end ? ` — ends ${formatDate(sub.trial_end)}` : ""}.
                  </p>
                ) : null}
                <p className="hint">
                  Nothing is charged at the end, because no card was ever taken. The account pauses:
                  what has been collected stays readable and exportable, and carrying on is arranged
                  with us directly.
                </p>
              </>
            ) : (
              <>
                <button
                  type="button"
                  disabled={!canWrite || busy}
                  title={canWrite ? undefined : ADMIN_TOOLTIP}
                  onClick={() => trialMutation.mutate()}
                >
                  Start the free trial
                </button>
                <p className="hint">No card is asked for, now or at the end.</p>
              </>
            )}
          </article>
          <p className="hint">
            There are no paid plans to choose from on this hub yet. If that changes you will see it
            here first — nothing converts on its own.
          </p>
        </section>
      </div>
    );
  }

  return (
    <div className="g-settings-billing">
      {!canWrite ? <RoleNotice role={role} required="admin" /> : null}

      {data.notice ? (
        <p className="g-billing-notice" role="status">
          {data.notice}
        </p>
      ) : null}

      {!data.configured ? (
        <p className="g-billing-notice" role="status">
          Card payments are not switched on for this hub yet, so the plans below
          cannot be bought from here — the free trial works as normal, and a plan
          is arranged with you directly. Nothing about your monitoring depends on
          this.
        </p>
      ) : null}

      <section className="g-billing-section">
        <h3>Current plan</h3>
        {sub ? (
          <CurrentPlan sub={sub} />
        ) : (
          <>
            <p className="hint">
              This account is not on a subscription. Start the free trial — three
              months of everything, and no payment method is asked for.
            </p>
            <button
              type="button"
              disabled={!canWrite || busy}
              title={canWrite ? undefined : ADMIN_TOOLTIP}
              onClick={() => trialMutation.mutate()}
            >
              Start {data.trial_days} free days
            </button>
          </>
        )}
        {data.portal_available ? (
          <p className="g-billing-portal">
            <button
              type="button"
              disabled={!canWrite || busy}
              title={canWrite ? undefined : ADMIN_TOOLTIP}
              onClick={() => portalMutation.mutate()}
            >
              Manage, change or cancel in the billing portal
            </button>
            <span className="hint">
              Opens Stripe, where your card and your invoices live. Vault never
              sees a card number.
            </span>
          </p>
        ) : null}
      </section>

      <section className="g-billing-section">
        <h3>Plans</h3>
        <div className="g-billing-interval" role="group" aria-label="Billing interval">
          <button
            type="button"
            aria-pressed={interval === "month"}
            onClick={() => setInterval("month")}
          >
            Monthly
          </button>
          <button
            type="button"
            aria-pressed={interval === "year"}
            onClick={() => setInterval("year")}
          >
            Yearly
            {saving > 0 ? <span className="g-billing-save"> — {saving} months free</span> : null}
          </button>
        </div>

        <div className="g-billing-plans">
          {data.plans.map((plan) => {
            const isCurrent = plan.current;
            return (
              <article key={plan.id} className={`g-billing-plan${isCurrent ? " is-current" : ""}`}>
                <h4>
                  {plan.name}
                  {isCurrent ? <Badge tone="good">Current</Badge> : null}
                </h4>
                <p className="g-billing-price">{priceOf(plan, interval)}</p>
                {plan.per_site ? <p className="hint">Per monitored site.</p> : null}
                {plan.is_trial ? <p className="hint">Three months. No card.</p> : null}
                <p className="g-billing-blurb">{plan.blurb}</p>
                {plan.is_trial ? (
                  <button
                    type="button"
                    disabled={!canWrite || busy || sub !== null}
                    title={
                      sub !== null
                        ? "This account already has a subscription."
                        : canWrite
                          ? undefined
                          : ADMIN_TOOLTIP
                    }
                    onClick={() => trialMutation.mutate()}
                  >
                    Start the free trial
                  </button>
                ) : (
                  <button
                    type="button"
                    disabled={!canWrite || busy || !data.configured || isCurrent}
                    title={
                      !data.configured
                        ? "Card payments are not switched on for this hub yet."
                        : canWrite
                          ? undefined
                          : ADMIN_TOOLTIP
                    }
                    onClick={() => {
                      setSites("1");
                      setConfirming(plan);
                    }}
                  >
                    {isCurrent ? "Current plan" : `Choose ${plan.name}`}
                  </button>
                )}
              </article>
            );
          })}
        </div>

      </section>

      <ConfirmDialog
        open={confirming !== null}
        danger={false}
        title={confirming ? `Continue to checkout for ${confirming.name}?` : ""}
        body={
          confirming ? (
            <>
              You will be taken to Stripe to pay {priceOf(confirming, interval)}
              {confirming.per_site ? ` for ${siteCount} site${siteCount === 1 ? "" : "s"}` : ""}.
              Nothing is charged until you complete it there, and you can cancel at
              any time from the billing portal afterwards.
              {/* The site count lives INSIDE the dialog, not beside it: the
                  dialog traps focus between its own controls, so a field
                  rendered behind the backdrop could never be reached — and a
                  per-site plan would silently check out at one site. */}
              {confirming.per_site ? (
                <label className="g-billing-sites">
                  <span>How many sites?</span>
                  <input
                    type="number"
                    min={1}
                    max={500}
                    value={sites}
                    onChange={(e) => setSites(e.target.value)}
                  />
                </label>
              ) : null}
            </>
          ) : (
            ""
          )
        }
        confirmLabel="Continue to Stripe"
        onCancel={() => setConfirming(null)}
        onConfirm={() => {
          if (confirming) checkoutMutation.mutate(confirming);
        }}
      />
    </div>
  );
}

export default Billing;
