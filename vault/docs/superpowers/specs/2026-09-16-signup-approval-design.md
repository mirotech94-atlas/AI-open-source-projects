# Signup approval: from "Request access" to a working account

**Status:** approved by owner 2026-09-16 · **Date:** 2026-09-16 · **Branch:** phase5-closeout

## The ask

When someone signs up on guarder.io, email the owner. The owner approves or
rejects. On approval the person ends up inside my.guarder.io with their own
account.

## What exists today (and is reused, not replaced)

| Piece | Where | State |
|---|---|---|
| "Request access" form on guarder.io | `site/index.html`, `site/app.js` | Posts `POST /api/v1/public/lead` through nginx from the site origin. Honeypot, length caps, per-IP and global rate limits. |
| Lead storage | `models.Lead`, `routers/leads.py` | Hub-global rows (name, email, company, network_size, note, ip, created). No status, no decision. |
| Owner notification | `adminnotify.notify("lead", …)` | Slack admin channel only. No email. |
| Owner's list | `web/src/pages/Leads.tsx` at `/settings/leads` | Superadmin-only read-only table. "No email is sent — reply from here." |
| Account creation | `routers/tenants.py`, `accounts.upsert_user/upsert_membership`, `guardian tenant add` / `user add` | By hand, superadmin or CLI. |
| Sign-in | `routers/auth.py` | Username → emailed one-time code (`mailer.send_otp`, SMTP is configured in production) → session cookie. First-run wizard per `(user, tenant)` via `UserOnboarding`. |
| Trial | `billing.start_trial` (90 days) | Started from the app by an admin. |

Nothing here is thrown away. The design adds a decision to a lead, an email to
the owner, provisioning on approval, and an email back to the applicant.

## The flow

```
Visitor                guarder.io            Hub                         Owner                    Applicant's inbox
  |  fills form ------->  POST /public/lead --> Lead(status=pending)
  |                                             |-- Slack: "Access request" (as today)
  |                                             |-- Email owner: details + Approve / Reject links
  |                                                                       |
  |                                        opens link -> my.guarder.io/settings/leads?lead=<id>
  |                                        (signed in on a remembered device; superadmin)
  |                                        clicks Approve  ------------->  POST /leads/{id}/approve
  |                                             |-- Tenant + User + Membership(admin) + 90-day trial + audit
  |                                             |-- Lead(status=approved, tenant_id)
  |                                             |-- Email applicant: "Your Guardian account is ready" + sign-in link
  |                                                                                                   |
  |  <------------------------------------------------------------------ clicks link -> /login?u=<username>
  |  enters emailed code -> lands in the app on their new tenant -> first-run wizard
```

Reject is the same shape without provisioning: `Lead(status=rejected)`, and a
short fixed-copy email to the applicant.

## Decisions, with reasons

### 1. The owner decides inside the app, reached from the email — not by one-click links

The email carries two buttons, **Approve** and **Reject**, but both open
`https://my.guarder.io/settings/leads?lead=<id>&decide=approve|reject`. The
Leads page (superadmin session, already remembered on your devices) opens
that lead with the chosen decision pre-armed and asks for one confirming
click.

Why not links that act on their own: mail security scanners and link
previews fetch URLs in emails. A one-click approve link would be clicked by
Gmail's scanner before you ever read the message, creating accounts for
whoever submits the form. A signed-in POST behind a confirm button cannot be
triggered by a prefetch. It costs you one extra click and no new auth surface.

### 2. Provisioning on approval

`POST /api/v1/leads/{id}/approve` (superadmin) with an editable body the
confirm dialog pre-fills:

- `tenant_name`: the `site_name` the applicant typed (new required form field, see Site); falls back to company, then "<Name>'s network"
- `slug`: slugified tenant name, uniquified with `-2`, `-3`… (`Tenant.slug` is unique)
- `username`: the lead's email, lower-cased (`User.username` is unique; matches how `auth/start` looks users up)
- `display_name`: the lead's name
- `role`: `admin` (fixed — the applicant owns their tenant)

It creates the tenant (`tenants.create` logic), upserts the user
(`accounts.upsert_user`; an email that already has an account simply gains
a second tenant, which the app already supports), adds the membership,
starts the 90-day trial (`billing.start_trial`), writes an audit row
(`tenant.create` + `tenant.user_add`, as the superadmin routes do), stamps
the lead `approved` with `tenant_id`, and fires `adminnotify.notify("tenant", …)`.
Idempotent: approving an already-approved lead returns it unchanged (409 if
rejected).

### 3. The applicant's way in

No new login mechanism. The approval email links to
`https://my.guarder.io/login?u=<username>`. The login page pre-fills the
username and requests the code immediately (`auth/start`), so the person
opens the email, clicks, reads the code from the next email, and is in. The
first-run wizard fires because `UserOnboarding` has no row for them.

Why not a magic sign-in link: it would be a second way to mint a session,
with its own token table and expiry, and the same prefetch hazard as (1).
The emailed-code flow already proves they control the address.

### 4. Reject sends a fixed, short decline email

"Thanks for your interest in Guardian. We can't set you up at the moment."
No free text (the mailer must not become a way to send arbitrary prose to
an address someone else typed). The lead stays in the list as `rejected`.

### 5. Owner notification: Slack and email, both

The existing Slack admin ping stays. In addition the hub emails the owner.
New setting `GUARDIAN_OWNER_EMAIL` (comma-separated allowed), set to
`you@example.com` on the VPS in `/opt/guardian/hub/.env`. When unset,
fall back to the email of every superadmin user, so a fresh install still
works.

### 6. Duplicates

A new submission from an email that already has a `pending` lead updates
that row (name/company/note/ip, `created` refreshed) instead of adding a
second, and re-notifies. Existing rate limits are unchanged.

## Changes by component

**Hub**
- `models.Lead`: `status` (pending|approved|rejected, default pending, indexed), `decided_at`, `decided_by`, `tenant_id` (nullable FK, SET NULL on delete). Migration `0019_lead_decision`.
- `mailer.py`: a general `send(to, subject, text)`; `send_otp` calls it. Three templates: `lead_notice` (to owner), `account_ready` (to applicant), `lead_declined` (to applicant). Background tasks, never raise into the request.
- `config.py`: `owner_email: str = ""`.
- `routers/leads.py`: `submit_lead` upserts on pending email and emails the owner; `GET /leads` adds `status`, `decided_at`, `decided_by`, `tenant_slug`; new `POST /leads/{id}/approve` and `POST /leads/{id}/reject` (superadmin). Approval logic lives in a new `accounts.provision_from_lead(db, lead, body, by)` so the CLI can reuse it (`guardian lead approve <id>` for the SMTP-is-down day).
- `schemas.py`: `LeadRow` fields above; `LeadApproveIn`.

**Web (superadmin Leads page)**
- Status column and a Pending-first sort; Approve / Reject buttons per pending row.
- Confirm dialog with the four editable fields for approve, plain confirm for reject.
- `?lead=<id>&decide=…` opens that row's dialog pre-armed.
- Login page: honour `?u=<username>` — pre-fill and auto-request the code.

**Site**
- New required field **"What should we call this site?"** (`site_name`, max 120) between Company and "What are you looking after?" -- the owner cannot know the applicant's network name, so the applicant supplies it. `LeadIn.site_name`, `Lead.site_name`, shown on the Leads page and used as the tenant-name default at approval.
- Confirmation copy: "Thanks — we'll review it and email you when your account is ready."
- No new proxied path (approval happens on the app host).

**Ops**
- `GUARDIAN_OWNER_EMAIL` on the VPS; migration runs in `deploy.sh` as usual.

## Errors and edge cases

- SMTP down when a lead arrives: the lead is stored, Slack still fires, the email is not retried; Slack and the Leads page are the fallback.
- Approve when slug collides: the dialog pre-uniquifies its default, and the route uniquifies again on submit (`home` -> `home-2`, ...) rather than 409ing -- the toast that confirms the account was created names the resulting slug.
- Approve when the email already has a user: no new user; membership added; the "account ready" email says "your existing sign-in works here too".
- Someone submits with another person's email: nothing is created until you approve; the decline/ready emails carry no submitted prose.
- The owner clicks Approve on a lead already handled by a colleague: 409, page refreshes to the current state.

## Testing

Hub: approve creates tenant/user/membership/trial and one audit row each; is idempotent; rejects a rejected lead; slug uniquified; existing-user path adds membership only; owner email goes to `GUARDIAN_OWNER_EMAIL` else superadmins; duplicate pending lead updates in place; reject email fixed copy; superadmin-only on both routes. Web: pending row shows buttons, dialog pre-fills, `?lead=&decide=` arms it, login pre-fills from `?u=`. Site: confirmation copy.

## Out of scope (say if you want them)

Self-serve signup without approval; magic-link sign-in; editing the decline
text; a sidebar badge for pending leads (the email is the prompt).

## The sign-up page (owner addition, 2026-09-16)

Every "Start three months free" / "Request access" call to action on
guarder.io -- in the hero, the pricing card and the footer, in all nine
locales -- now leads to a dedicated page, `/signup.html`, instead of the
form section at the foot of the home page. That section shrinks to a short
lede and the same button. The page:

- is styled as the app's own sign-in screen is (the site already shares the
  app's palette and type): the site header with the brand and a **Log in**
  link, then one centred panel, max-width 520px, on the same dark ground;
- carries the form (name, email, site name, company, what you look after,
  note, honeypot), plus a required **"I agree to the Terms of Service"**
  checkbox linking to `/terms.html`; the hub refuses a submission without
  it (`accept_terms` must be true; 422 otherwise) and records
  `terms_accepted_at` on the lead;
- ends with "Already have an account? **Log in**" pointing at
  `https://my.guarder.io/login`;
- on success replaces the form with the confirmation copy.

`/terms.html` is a first draft with **[bracketed placeholders]** for the
legal specifics (company name, jurisdiction, contact) -- the owner fills
those in; nothing in it is legal advice.

The site's "Sign in" links (header and footer) read **Log in** and point at
`https://my.guarder.io/login`. The sign-up and terms pages are English-only
for now; the localised home pages link to them.

Both new pages obey the site's CSP (no inline CSS, no third-party assets)
and are added to `hub/tests/test_site_csp.py`'s file list.

## Owner decisions (2026-09-16)

1. One-extra-click model confirmed. Notify on Slack admin AND by email to you@example.com.
2. Ask the applicant for a site name on the form; it becomes the tenant name default.
3. Reject sends the fixed decline copy.
4. "Get early access" everywhere leads to a dedicated sign-up page with terms acceptance; "Sign in" reads "Log in".
