# Turning Stripe on for Guardian

Guardian's billing code is complete. What is missing is configuration: two
secrets on the hub, and eight prices in the Stripe dashboard. Nothing in the
codebase can create the prices — they are objects in your Stripe account — so
this is a checklist for you, plus a command that proves you got it right.

**Read this first.** This hub ships as `GUARDIAN_BILLING_MODE=trial_only`:
Guardian offers the 90-day free trial and nothing else, the plan tiers are off
the marketing site, and `routers/billing.py` refuses checkout before it ever
looks up a price. So none of the configuration below is *required* today, and
nothing here is a fault to be cleared.

It is worth doing anyway, and doing first. Switching to paid plans is one
setting (`GUARDIAN_BILLING_MODE=plans`), not a deploy, but only if the prices
already exist, and that is the part that cannot be done in a second. Set this
up now and the day you decide to charge is a one-line change.

Run `guardian billing-check` (step 4) at the end. It fails loudly if any price
is missing, priced wrong, or on the wrong interval.

## 1. The eight prices

Guardian looks a price up by its **lookup key**, never by a price id pasted
into config. That means you can rotate or re-create a price in Stripe and
Guardian keeps working, as long as the lookup key is right. It also means a
typo in a lookup key is invisible until a customer tries to pay.

Create four products, each with a monthly and a yearly price, all in **USD**,
all **recurring**:

| Product | Lookup key | Amount | Billing period | Quantity |
|---|---|---|---|---|
| Home | `guardian_home_month` | $9.00 | Monthly | one |
| Home | `guardian_home_year` | $90.00 | Yearly | one |
| Business Essentials | `guardian_business_essentials_month` | $49.00 | Monthly | **per site** |
| Business Essentials | `guardian_business_essentials_year` | $490.00 | Yearly | **per site** |
| Business Pro | `guardian_business_pro_month` | $149.00 | Monthly | one |
| Business Pro | `guardian_business_pro_year` | $1490.00 | Yearly | one |
| Partner / MSP | `guardian_partner_month` | $99.00 | Monthly | **per site** |
| Partner / MSP | `guardian_partner_year` | $990.00 | Yearly | **per site** |

The lookup key field is under "Advanced" when you add a price. Set it exactly
as written — Guardian builds it as `guardian_{plan}_{interval}`.

Two notes:

- **Yearly is ten months, not twelve.** That is deliberate and asserted in
  `plans.py` at import time: two months free for paying yearly.
- **The free trial needs no Stripe object.** It is 90 days, it is free, and
  Guardian records it locally. Do not create a price for it. This is why the
  signup approval flow works right now with Stripe switched off entirely.

## 2. The two secrets

These go in `/opt/guardian/hub/.env` on the VPS. **Add them yourself** — I do
not handle secret keys.

```
GUARDIAN_STRIPE_SECRET_KEY=sk_live_...
GUARDIAN_STRIPE_WEBHOOK_SECRET=whsec_...
```

Then restart the hub:

```
systemctl restart guardian-hub
```

`.env` on that box is already the home for `GUARDIAN_SMTP_*`, `GUARDIAN_GROQ_API_KEY`
and the Slack secrets, so this is the same file and the same format.

The secret key is what makes `billing_configured()` true; without it checkout
returns `503 billing is not configured on this hub`, which is the error you
saw.

Since then the hub moved to `trial_only`, so that same checkout now refuses
earlier and more plainly, with a 409 saying Guardian is free. To actually sell
the plans, add a third line, but only once `guardian billing-check` is green:

```
GUARDIAN_BILLING_MODE=plans
```

## 3. The webhook

Already reachable — no nginx work needed. Point a Stripe webhook endpoint at:

```
https://my.guarder.io/api/v1/billing/stripe/webhook
```

It answers today with `400 webhook signing secret is not configured`, which
confirms the route is live and only the secret is absent. Copy its signing
secret into `GUARDIAN_STRIPE_WEBHOOK_SECRET`.

Subscribe it to exactly these eight events — the ones `billing.apply_event`
acts on. Anything else is accepted and ignored:

```
checkout.session.completed
customer.subscription.created
customer.subscription.updated
customer.subscription.resumed
customer.subscription.deleted
invoice.payment_succeeded
invoice.paid
invoice.payment_failed
```

Without the webhook secret, checkout still completes but the subscription
status never comes back, so a paying customer stays "incomplete" forever.

## 4. Prove it

On the VPS, run it from the hub directory so it picks up `.env`:

```
cd /opt/guardian/hub && /opt/guardian/venv/bin/guardian billing-check
```

Eight OK rows and exit 0 means every plan a customer can pick resolves to a
real, correctly-priced Stripe price. Re-run it any time you change a price in
the dashboard.

On a `trial_only` hub it says so at the top and still checks the prices,
because checking them is the whole point of doing this before the switch. It
does not report a trial-only hub with no Stripe account as broken; that is the
shipped default, not a fault.

A run with two faults seeded looks like this:

```
lookup key                          expected       per-site  result
guardian_home_month                 $9.00/month    no        OK
guardian_home_year                  $90.00/year    no        OK
guardian_business_essentials_month  $49.00/month   yes       OK
guardian_business_essentials_year   $490.00/year   yes       OK
guardian_business_pro_month         $149.00/month  no        FAIL: amount is $129.00, expected $149.00
guardian_business_pro_year          $1490.00/year  no        OK
guardian_partner_month              $99.00/month   yes       OK
guardian_partner_year               $990.00/year   yes       FAIL: no active Stripe price with lookup key 'guardian_partner_year'

2 of 8 prices need fixing in the Stripe dashboard.
```

It checks every row before reporting, so one missing price does not hide the
state of the rest.

## 4b. Managed Payments (Stripe as merchant of record)

Guardian is set up to let Stripe be the **merchant of record**. Stripe then
carries the sales tax and VAT liability in 80-odd countries, handles fraud and
disputes, and sends the invoices and receipts, for an extra fee per
transaction on top of the normal processing fee.

That fee is the trade, and it is worth restating plainly: on the plan prices
above it is about $0.32/month on Home, $1.72 on Business Essentials, $3.47 on
Partner and $5.22 on Business Pro. What it buys is not having to register for
sales tax state by state, track economic nexus, file returns, or repeat all of
that for VAT the first time somebody in the EU subscribes.

**Three things have to be true, and none of them is the default.**

1. **In the dashboard:** enable Managed Payments and accept its terms of
   service at Settings > Managed Payments. Nobody can accept those terms on
   your behalf.
2. **On each product:** a tax code that Stripe marks `Eligible for Managed
   Payments`. Guardian's four products use
   `txcd_10103000` (SaaS, personal use) for Home and `txcd_10103001`
   (SaaS, business use) for the three business plans. A product's page shows
   `Managed Payments: Eligible` once it has one, and `Needs info` until then.
3. **On the hub:** `GUARDIAN_STRIPE_MANAGED_PAYMENTS=true`. That flag also
   moves the checkout call onto Stripe API version `2025-03-31.basil`, which
   is the oldest one that knows what `managed_payments` means. The rest of the
   integration stays on the pinned `2024-06-20`.

Leave step 3 until steps 1 and 2 are done. Sending the flag to an account that
has not enabled Managed Payments is an error from Stripe, which means a
customer who cannot check out at all -- so the hub ships with it off, and
`stripe_api.create_checkout_session` only adds the parameter when it is on.

**It splits the customer base, so decide before the first sale.** Stripe can
only put a NEW subscription under Managed Payments; existing ones are not
eligible. Turning this on later leaves everyone who already subscribed outside
it, and turning it off later leaves everyone already inside it there. Neither
direction migrates anybody.

Nothing else in the integration changes. Managed Payments forbids a list of
Checkout Session parameters, and this hub sends none of them --
`tests/test_stripe_managed_payments.py` asserts that, so a future parameter
that would break checkout fails the suite instead.

## 5. Getting the money out of Stripe

Everything above makes a customer's card succeed. It does **not** put the
money in your bank: a charge lands in your Stripe balance, and Stripe pays
that balance out to a bank account you have added and verified. An account
that has never completed this holds the money indefinitely — the charge shows
as succeeded, the balance grows, and nothing arrives.

This is the one part of Guardian's setup that is entirely outside the product.
No code reads it, `billing-check` cannot see it, and nobody can do it on your
behalf: adding a bank account means typing an account and routing number into
Stripe, which is exactly the kind of thing to enter yourself, on a page you
navigated to, and never to hand to anyone.

In the Stripe dashboard:

1. **Activate the account.** Settings > Business > Account details. Stripe
   will not pay out at all until the business details, the responsible
   person's identity and the tax details are accepted. This is the step that
   takes real time, so start it before the prices.
2. **Add the bank account.** Settings > Business > Bank accounts and
   scheduling > Add bank account. For a TD chequing or savings account you
   need the institution and transit numbers (TD Canada Trust) or the routing
   and account numbers (TD Bank, N.A.), from a cheque or from the account
   details screen in TD's own app.
3. **Check the currency match.** Guardian's prices are in **USD**
   (`plans.CURRENCY`). The Stripe account is registered in New York, so a US
   dollar account at TD Bank, N.A. receives exactly the invoice figures and
   there is nothing to think about here.

   It is worth knowing the rule anyway, because it bites the moment a payout
   account in another currency is added: Stripe converts to the currency of
   the account it pays into, at its own rate and with a conversion fee, so
   the amount that lands is not the amount on the invoice. Changing the
   payout account later does not re-settle anything already paid out.
4. **Set the schedule.** Same screen. Stripe defaults to a rolling daily
   payout after an initial hold of a few days on a new account. Leave it
   unless you want the money batched weekly or monthly.
5. **Confirm the first one.** Balances > Payouts shows each payout, its
   status, and the last four digits of the account it went to. A payout that
   says "paid" and the matching deposit in TD is the only proof that this
   whole chain works; until you have seen one, treat it as untested.

### A note on debit cards

Stripe pays out to a **bank account**, not to a debit card, in Canada and for
standard payouts in the US. If you have a TD debit card, what you want is the
chequing account behind it, not the card number. (Stripe's Instant Payouts do
use a debit card in some US cases, for a fee — not something to rely on for
normal revenue.)

### What "working" means, end to end

A subscription is only fully proven when all five of these are true:

1. `guardian billing-check` reports eight OK rows.
2. `GUARDIAN_BILLING_MODE=plans` on the hub.
3. A real card completes checkout and the customer's plan changes in
   Guardian — which only happens if the webhook secret is right.
4. The charge appears in Stripe under Payments.
5. A payout for it reaches TD.

Steps 1 and 2 are the hub's. Steps 3, 4 and 5 are Stripe's, and 5 is the one
that is easiest to assume and most expensive to get wrong.
