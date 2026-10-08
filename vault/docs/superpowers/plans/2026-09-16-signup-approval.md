# Signup Approval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A "Request access" submission on guarder.io emails the owner (and pings Slack as today); the owner approves or rejects it on the app's Leads page; approval provisions a tenant + admin user + 90-day trial and emails the applicant a sign-in link into my.guarder.io.

**Architecture:** Everything hangs off the existing `Lead` row. Three hub additions: decision columns on `Lead`, a general `mailer.send`, and `accounts.provision_from_lead` (used by a new superadmin route and a CLI verb). The web app grows Approve/Reject on the existing superadmin Leads page and a `?u=` pre-fill on Login. The public site gains one form field and new confirmation copy. No new auth mechanism: the applicant signs in with the emailed code exactly as everyone does.

**Tech Stack:** FastAPI + SQLAlchemy + Alembic (hub, Python 3.10, `hub/.venv`), pytest; React + TanStack Query + react-router (web), vitest + testing-library; static HTML/JS site.

**Spec:** `docs/superpowers/specs/2026-09-16-signup-approval-design.md`

## Global Constraints

- Work on branch `phase5-closeout` in `/Users/mac/Documents/Code/AI Projects/Guardian-main`. Commit after every task with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` as the last line.
- Hub tests: `cd hub && .venv/bin/pytest <file> -q -p no:cacheprovider`. Web tests: `cd web && npx vitest run <file>`. Web build: `cd web && npm run build`.
- Nothing an applicant typed is ever echoed back to the public site, and no free text from the owner is ever emailed to an applicant (emails to applicants are fixed copy).
- Emails to the owner go to `GUARDIAN_OWNER_EMAIL` (comma-separated), falling back to every superadmin's email. Production value: `you@example.com`.
- Both decision routes are superadmin-only (`Depends(require_superadmin)`), like `GET /api/v1/leads`.
- Lead status values: exactly `pending`, `approved`, `rejected`.
- Approval is idempotent (approving an approved lead returns it unchanged); approving or rejecting a `rejected`/`approved` lead the other way is `409`.
- The Leads page stays superadmin-only and keeps its existing three tests green.
- Do not deploy in any task; the deploy is the final section and needs the owner's go-ahead.

---

### Task 1: Lead decision columns + site name (model, migration, list schema)

**Files:**
- Modify: `hub/guardian_hub/models.py` (class `Lead`, ~line 460)
- Create: `hub/alembic/versions/0019_lead_decision.py`
- Modify: `hub/guardian_hub/schemas.py` (`LeadIn`, `LeadRow`)
- Modify: `hub/guardian_hub/routers/leads.py` (`submit_lead` stores `site_name`; `list_leads` emits the new fields)
- Test: `hub/tests/test_leads.py`

**Interfaces:**
- Produces on `m.Lead`: `site_name: str`, `status: str` (default `"pending"`), `decided_at: dt.datetime | None`, `decided_by: str`, `tenant_id: str | None`, `terms_accepted_at: dt.datetime | None`.
- Produces on `sc.LeadRow`: `site_name: str`, `status: str`, `decided_at: str` (ISO or `""`), `decided_by: str`, `tenant_slug: str` (`""` until approved).
- Produces on `sc.LeadIn`: `site_name: str = Field(default="", max_length=120)`, `accept_terms: bool = False`.

- [ ] **Step 1: Write the failing tests** (append to `hub/tests/test_leads.py`)

```python
# ------------------------------------------------------------ decision columns

def test_a_new_lead_is_pending_and_carries_its_site_name(client):
    r = client.post("/api/v1/public/lead", json={**GOOD, "site_name": "Okafor Dental — Main St"})
    assert r.status_code == 200
    row = _leads()[0]
    assert row.status == "pending"
    assert row.site_name == "Okafor Dental — Main St"
    assert row.decided_at is None and row.decided_by == "" and row.tenant_id is None
    assert row.terms_accepted_at is not None


def test_a_submission_without_accepting_the_terms_is_refused_and_not_stored(client):
    r = client.post("/api/v1/public/lead", json={**GOOD, "accept_terms": False})
    assert r.status_code == 422
    assert _leads() == []


def test_the_list_shows_status_site_name_and_no_tenant_yet(client):
    client.post("/api/v1/public/lead", json={**GOOD, "site_name": "Main St"})
    make_super(client)
    lead = client.get("/api/v1/leads").json()["leads"][0]
    assert lead["status"] == "pending"
    assert lead["site_name"] == "Main St"
    assert lead["decided_at"] == "" and lead["decided_by"] == "" and lead["tenant_slug"] == ""
```

Also change the module-level `GOOD` dict in `hub/tests/test_leads.py` to include `"accept_terms": True` (every existing test posts it; the terms are now required). In `hub/tests/test_leads.py::test_optional_fields_may_be_omitted`, keep `accept_terms` in the body -- it is not optional.

- [ ] **Step 2: Run to verify they fail**

Run: `cd hub && .venv/bin/pytest tests/test_leads.py -q -p no:cacheprovider -k "pending_and_carries or shows_status or without_accepting"`
Expected: FAIL (`AttributeError: 'Lead' object has no attribute 'status'` / `KeyError: 'status'`).

- [ ] **Step 3: Add the columns to the model**

In `hub/guardian_hub/models.py`, class `Lead`, after the `ip` column add:

```python
    # The applicant's own name for the site (the owner cannot know it); the
    # tenant-name default at approval.
    site_name: Mapped[str] = mapped_column(String(120), default="")
    # pending | approved | rejected. A decision is recorded once; see
    # `accounts.provision_from_lead` and `routers/leads.py`.
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    decided_at: Mapped[dt.datetime | None] = mapped_column(TZDateTime, nullable=True)
    decided_by: Mapped[str] = mapped_column(String(64), default="")
    # The tenant approval created. SET NULL if that tenant is later deleted so
    # the lead's history survives.
    tenant_id: Mapped[str | None] = mapped_column(
        ForeignKey("tenant.id", ondelete="SET NULL"), nullable=True)
    # When the applicant ticked "I agree to the Terms of Service" on the
    # sign-up page. Required at submission (`schemas.LeadIn.accept_terms`).
    terms_accepted_at: Mapped[dt.datetime | None] = mapped_column(TZDateTime, nullable=True)
```

- [ ] **Step 4: Write the migration** `hub/alembic/versions/0019_lead_decision.py`

```python
"""lead decision -- status, decided_at, decided_by, tenant_id, site_name, terms_accepted_at on `lead`

The public "Request access" form used to end at a row and a Slack ping. Now
the owner approves or rejects each lead from the app and approval provisions
a tenant, so the row records the decision and the tenant it produced.

Backfill: every existing row is `pending` (the column default), which is the
correct reading -- nothing before this had been decided.

Idempotent in the style of 0003/0018: each add is guarded on the inspector.

Revision ID: 0019_lead_decision
Revises: 0018_router
Create Date: 2026-09-16

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0019_lead_decision'
down_revision: Union[str, Sequence[str], None] = '0018_router'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


_ADD = [
    sa.Column("site_name", sa.String(120), nullable=False, server_default=""),
    sa.Column("status", sa.String(12), nullable=False, server_default="pending"),
    sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("decided_by", sa.String(64), nullable=False, server_default=""),
    sa.Column("tenant_id", sa.String(32), nullable=True),
    sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True),
]


def upgrade() -> None:
    have = _columns("lead")
    for col in _ADD:
        if col.name not in have:
            op.add_column("lead", col)
    # SQLite cannot add a FK to an existing table without a rebuild; the ORM
    # relationship is enforced by the hub (`ondelete="SET NULL"` in models.py)
    # and the column is nullable, so no constraint is added here.


def downgrade() -> None:
    have = _columns("lead")
    for col in reversed(_ADD):
        if col.name in have:
            op.drop_column("lead", col.name)
```

- [ ] **Step 5: Extend the schemas** in `hub/guardian_hub/schemas.py`

In `LeadIn`, after `network_size`:
```python
    site_name: str = Field(default="", max_length=120)
    # The sign-up page's "I agree to the Terms of Service". Refused (422) when
    # false: see `routers/leads.py::submit_lead`.
    accept_terms: bool = False
```
In `LeadRow`, after `created`:
```python
    site_name: str = ""
    status: str = "pending"
    decided_at: str = ""
    decided_by: str = ""
    tenant_slug: str = ""
```

- [ ] **Step 6: Store and emit the fields** in `hub/guardian_hub/routers/leads.py`

In `submit_lead`, add `site_name=body.site_name.strip(), terms_accepted_at=dt.datetime.now(dt.timezone.utc),` to the `m.Lead(...)` constructor (after `network_size=`), and at the very top of the function body, before the honeypot check, add:

```python
    if not body.accept_terms:
        raise HTTPException(422, "please accept the Terms of Service")
```

Replace the body of `list_leads` with:
```python
    rows = db.query(m.Lead).order_by(m.Lead.created.desc()).limit(500).all()
    slugs = {t.id: t.slug for t in db.query(m.Tenant).all()}
    return sc.LeadsOut(leads=[
        sc.LeadRow(
            id=r.id, name=r.name, email=r.email, company=r.company,
            network_size=r.network_size, note=r.note, ip=r.ip,
            created=(r.created or dt.datetime.now(dt.timezone.utc)).isoformat(),
            site_name=r.site_name or "", status=r.status or "pending",
            decided_at=r.decided_at.isoformat() if r.decided_at else "",
            decided_by=r.decided_by or "", tenant_slug=slugs.get(r.tenant_id or "", ""),
        )
        for r in rows
    ])
```

- [ ] **Step 7: Run the lead tests**

Run: `cd hub && .venv/bin/pytest tests/test_leads.py -q -p no:cacheprovider`
Expected: all pass (the two new ones and the existing ones).

- [ ] **Step 8: Prove the migration runs on an existing database**

Run:
```bash
cd hub && rm -f /tmp/lead-mig.db && GUARDIAN_DATABASE_URL=sqlite:////tmp/lead-mig.db .venv/bin/guardian migrate && GUARDIAN_DATABASE_URL=sqlite:////tmp/lead-mig.db .venv/bin/python -c "import sqlite3;print(sorted(r[1] for r in sqlite3.connect('/tmp/lead-mig.db').execute('pragma table_info(lead)')))"
```
Expected: the printed list includes `decided_at`, `decided_by`, `site_name`, `status`, `tenant_id`, `terms_accepted_at`.

- [ ] **Step 9: Commit**

```bash
git add hub/guardian_hub/models.py hub/alembic/versions/0019_lead_decision.py hub/guardian_hub/schemas.py hub/guardian_hub/routers/leads.py hub/tests/test_leads.py
git commit -m "hub: lead carries a decision (status/decided_*/tenant_id), the site name and terms acceptance

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: A repeat submission from the same email updates the pending lead

**Files:**
- Modify: `hub/guardian_hub/routers/leads.py` (`submit_lead`)
- Test: `hub/tests/test_leads.py`

**Interfaces:**
- Consumes: `m.Lead.status` from Task 1.
- Produces: `submit_lead` upserts on `(email lower-cased, status == "pending")`.

- [ ] **Step 1: Write the failing test**

```python
def test_a_second_pending_submission_from_the_same_email_updates_the_row(client):
    client.post("/api/v1/public/lead", json={**GOOD, "note": "first"})
    client.post("/api/v1/public/lead", json={**GOOD, "note": "second, with a typo fixed", "site_name": "Main St"})
    rows = _leads()
    assert len(rows) == 1
    assert rows[0].note == "second, with a typo fixed"
    assert rows[0].site_name == "Main St"
    assert rows[0].status == "pending"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd hub && .venv/bin/pytest tests/test_leads.py -q -p no:cacheprovider -k second_pending`
Expected: FAIL (`assert 2 == 1`).

- [ ] **Step 3: Upsert in `submit_lead`**

Replace the `db.add(m.Lead(...))` block with:

```python
    email = body.email.strip().lower()
    lead = (db.query(m.Lead)
              .filter(m.Lead.email == email, m.Lead.status == "pending")
              .order_by(m.Lead.created.desc()).first())
    if lead is None:
        lead = m.Lead(email=email)
        db.add(lead)
    # A resubmission (a typo fixed, a note added) replaces the pending row's
    # details rather than adding a second row for the owner to reconcile.
    lead.name = body.name.strip()
    lead.company = body.company.strip()
    lead.network_size = body.network_size.strip()
    lead.note = body.note.strip()
    lead.site_name = body.site_name.strip()
    lead.ip = ip
    lead.created = dt.datetime.now(dt.timezone.utc)
    lead.terms_accepted_at = lead.created
    db.commit()
```

Keep the `adminnotify.notify("lead", ...)` call after it unchanged.

- [ ] **Step 4: Run the lead tests**

Run: `cd hub && .venv/bin/pytest tests/test_leads.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add hub/guardian_hub/routers/leads.py hub/tests/test_leads.py
git commit -m "hub: a repeat access request from the same email updates its pending lead

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: General `mailer.send`, the owner address, and the owner's lead email

**Files:**
- Modify: `hub/guardian_hub/mailer.py`
- Modify: `hub/guardian_hub/config.py` (add `owner_email`)
- Modify: `hub/guardian_hub/routers/leads.py` (`submit_lead` emails the owner in the background)
- Modify: `docs/superpowers/specs/2026-09-16-signup-approval-design.md` (drop the "retried once by the scheduler" sentence; replace with "not retried: Slack and the Leads page are the fallback")
- Test: `hub/tests/test_mailer_leads.py` (new)

**Interfaces:**
- Produces `mailer.send(to: str, subject: str, text: str) -> None` (no-op with a log line when SMTP is unset; under `dev_console_otp` it appends `(to, subject, text)` to `mailer.SENT: list[tuple[str, str, str]]` instead of sending).
- Produces `mailer.owner_addresses(db) -> list[str]`.
- Produces `mailer.send_lead_notice(to: str, lead: m.Lead) -> None`.
- Produces `settings.owner_email: str = ""`.

- [ ] **Step 1: Write the failing tests** in `hub/tests/test_mailer_leads.py`

```python
"""The owner hears about an access request by email as well as on Slack."""
from guardian_hub import mailer, models as m
from guardian_hub.config import settings
from guardian_hub.db import SessionLocal
from tests.test_auth import seed_user
from tests.test_tenants import make_super

GOOD = {"name": "Dana Okafor", "email": "dana@example.com", "company": "Okafor Dental",
        "network_size": "one-site", "note": "Two servers.", "site_name": "Main St", "accept_terms": True}


def _sent_to(addr):
    return [s for s in mailer.SENT if s[0] == addr]


def test_owner_addresses_prefers_the_setting_then_falls_back_to_superadmins(client, monkeypatch):
    uid, _ = seed_user(username="owner", email="owner@example.com")
    db = SessionLocal(); db.get(m.User, uid).is_superadmin = True; db.commit(); db.close()
    monkeypatch.setattr(settings, "owner_email", "")
    assert mailer.owner_addresses(SessionLocal()) == ["owner@example.com"]
    monkeypatch.setattr(settings, "owner_email", "a@example.com, b@example.com")
    assert mailer.owner_addresses(SessionLocal()) == ["a@example.com", "b@example.com"]


def test_a_new_lead_emails_the_owner_with_the_details_and_the_decision_link(client, monkeypatch):
    monkeypatch.setattr(settings, "owner_email", "owner@example.com")
    mailer.SENT.clear()
    r = client.post("/api/v1/public/lead", json=GOOD)
    assert r.status_code == 200
    (to, subject, text), = _sent_to("owner@example.com")
    assert "Dana Okafor" in subject
    for needle in ("dana@example.com", "Okafor Dental", "Main St", "One office or shop", "Two servers."):
        assert needle in text
    db = SessionLocal(); lead_id = db.query(m.Lead).first().id; db.close()
    assert f"{settings.base_url}/settings/leads?lead={lead_id}&decide=approve" in text
    assert f"{settings.base_url}/settings/leads?lead={lead_id}&decide=reject" in text


def test_a_caught_bot_emails_nobody(client, monkeypatch):
    monkeypatch.setattr(settings, "owner_email", "owner@example.com")
    mailer.SENT.clear()
    client.post("/api/v1/public/lead", json={**GOOD, "website": "http://spam.example"})
    assert _sent_to("owner@example.com") == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd hub && .venv/bin/pytest tests/test_mailer_leads.py -q -p no:cacheprovider`
Expected: FAIL (`AttributeError: module 'guardian_hub.mailer' has no attribute 'SENT'` / `owner_addresses`).

- [ ] **Step 3: Add the setting** in `hub/guardian_hub/config.py`, after `smtp_from`:

```python
    # Where the OWNER hears about access requests (comma-separated). Empty:
    # every superadmin's email. Production sets this to the owner's inbox.
    owner_email: str = ""
```

- [ ] **Step 4: Generalise the mailer** -- replace the whole of `hub/guardian_hub/mailer.py` with:

```python
import logging
import smtplib, ssl
from email.message import EmailMessage

from sqlalchemy.orm import Session as DbSession

from . import models as m
from .config import settings

log = logging.getLogger(__name__)

# LAST_CODES / SENT are dev/test-only: populated ONLY when settings.dev_console_otp
# is set (local dev, and hub/tests/conftest.py sets GUARDIAN_DEV_CONSOLE_OTP=1),
# so tests can read what would otherwise have been emailed. A real deployment
# must never set dev_console_otp, and in that case both stay empty.
LAST_CODES: dict[str, str] = {}
SENT: list[tuple[str, str, str]] = []

# The site's own wording for its "What are you looking after?" answers, so the
# owner's email reads the way the person answered.
SIZE_LABEL = {"home": "Home", "one-site": "One office or shop",
              "few-sites": "A handful of sites", "msp": "Client sites (MSP or reseller)"}


def send(to: str, subject: str, text: str) -> None:
    """Send one plain-text email. Never raises into a request: SMTP trouble is
    logged, because a lost notification must not fail a sign-in or a form."""
    if settings.dev_console_otp:
        SENT.append((to, subject, text))
        print(f"[guardian] mail to {to}: {subject}", flush=True)
        return
    if not settings.smtp_host:
        log.warning("[guardian] SMTP not configured; mail to %s (%s) was not delivered", to, subject)
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg.set_content(text)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as s:
            s.starttls(context=ssl.create_default_context())
            if settings.smtp_user:
                s.login(settings.smtp_user, settings.smtp_password)
            s.send_message(msg)
    except Exception as e:  # noqa: BLE001 -- notification only
        log.warning("[guardian] mail to %s failed: %s", to, str(e)[:200])


def send_otp(email: str, code: str) -> None:
    if settings.dev_console_otp:
        LAST_CODES[email] = code
        print(f"[guardian] OTP for {email}: {code}", flush=True)
        return
    send(email, f"{code} is your Guardian sign-in code",
         f"Your Guardian sign-in code is {code}. It expires in {settings.otp_ttl_seconds//60} minutes.\n"
         f"If you did not request it, ignore this email.")


def owner_addresses(db: DbSession) -> list[str]:
    """`GUARDIAN_OWNER_EMAIL` (comma-separated), else every superadmin's email."""
    configured = [a.strip() for a in (settings.owner_email or "").split(",") if a.strip()]
    if configured:
        return configured
    return [u.email for u in db.query(m.User).filter_by(is_superadmin=True).order_by(m.User.username)
            if u.email]


def send_lead_notice(to: str, lead: m.Lead) -> None:
    """The owner's copy of one access request, with the two decision links.
    Both links open the app (a signed-in superadmin page) -- never a link that
    acts by itself, because mail scanners fetch links."""
    base = settings.base_url.rstrip("/")
    approve = f"{base}/settings/leads?lead={lead.id}&decide=approve"
    reject = f"{base}/settings/leads?lead={lead.id}&decide=reject"
    who = lead.name or lead.email
    text = (
        f"Someone asked for access to Guardian.\n\n"
        f"Name:      {lead.name}\n"
        f"Email:     {lead.email}\n"
        f"Company:   {lead.company or '—'}\n"
        f"Site name: {lead.site_name or '—'}\n"
        f"Looking after: {SIZE_LABEL.get(lead.network_size, lead.network_size or '—')}\n"
        f"Note:      {lead.note or '—'}\n\n"
        f"Approve (creates their account): {approve}\n"
        f"Reject: {reject}\n\n"
        f"Either link opens the Leads page in the app and asks you to confirm."
    )
    send(to, f"Access request: {who}", text)
```

- [ ] **Step 5: Email the owner from `submit_lead`**

In `hub/guardian_hub/routers/leads.py`: add `BackgroundTasks` to the fastapi import and `from .. import mailer`; change the signature to
`def submit_lead(body: sc.LeadIn, request: Request, bg: BackgroundTasks, db: DbSession = Depends(get_db)):`
and after the `adminnotify.notify(...)` call add:

```python
    for addr in mailer.owner_addresses(db):
        bg.add_task(mailer.send_lead_notice, addr, lead)
```

(`lead` is the row from Task 2's upsert. The bot branch returns before this, so a caught bot emails nobody.)

- [ ] **Step 6: Run the mailer, lead and auth tests**

Run: `cd hub && .venv/bin/pytest tests/test_mailer_leads.py tests/test_leads.py tests/test_auth.py -q -p no:cacheprovider`
Expected: all pass (`send_otp` still populates `LAST_CODES` under the test flag).

- [ ] **Step 7: Update the spec sentence and commit**

In the spec's "Errors and edge cases", replace `the email is retried once by the scheduler's existing retry pattern; the Leads page is the source of truth` with `the email is not retried; Slack and the Leads page are the fallback`.

```bash
git add hub/guardian_hub/mailer.py hub/guardian_hub/config.py hub/guardian_hub/routers/leads.py hub/tests/test_mailer_leads.py docs/superpowers/specs/2026-09-16-signup-approval-design.md
git commit -m "hub: email the owner about each access request (GUARDIAN_OWNER_EMAIL), general mailer.send

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: `accounts.provision_from_lead` and the approve / reject routes

**Files:**
- Modify: `hub/guardian_hub/accounts.py` (add `slugify`, `unique_slug`, `provision_from_lead`)
- Modify: `hub/guardian_hub/mailer.py` (add `send_account_ready`, `send_lead_declined`)
- Modify: `hub/guardian_hub/schemas.py` (add `LeadApproveIn`)
- Modify: `hub/guardian_hub/routers/leads.py` (add `POST /leads/{id}/approve`, `POST /leads/{id}/reject`)
- Test: `hub/tests/test_lead_decisions.py` (new)

**Interfaces:**
- Consumes: Task 1 columns, Task 3 `mailer.send`, `accounts.upsert_user/upsert_membership`, `billing.start_trial(db, tenant_id)`, `audit.log(db, tenant_id, user, role, action, target=, ip=, body=)`, `adminnotify.notify`.
- Produces `accounts.slugify(text: str) -> str`, `accounts.unique_slug(db, base: str) -> str`.
- Produces `accounts.provision_from_lead(db, lead: m.Lead, *, tenant_name: str, slug: str, username: str, display_name: str, by: str, ip: str = "") -> tuple[m.Tenant, m.User, bool]` (the bool is `user_existed`). Does NOT commit.
- Produces `sc.LeadApproveIn(tenant_name: str, slug: str, username: str, display_name: str = "")` -- all optional with defaults derived from the lead when omitted (`tenant_name: str = ""` etc.; empty means "derive").
- Produces routes returning `sc.LeadRow`.

- [ ] **Step 1: Write the failing tests** in `hub/tests/test_lead_decisions.py`

```python
"""Approve or reject an access request. Approval provisions the account."""
from guardian_hub import mailer, models as m
from guardian_hub.config import settings
from guardian_hub.db import SessionLocal
from tests.test_auth import login, seed_user
from tests.test_tenants import make_super

GOOD = {"name": "Dana Okafor", "email": "Dana@Example.com", "company": "Okafor Dental",
        "network_size": "one-site", "note": "Two servers.", "site_name": "Okafor Dental — Main St",
        "accept_terms": True}


def _submit(client, **over):
    assert client.post("/api/v1/public/lead", json={**GOOD, **over}).status_code == 200
    db = SessionLocal()
    try:
        return db.query(m.Lead).order_by(m.Lead.created.desc()).first().id
    finally:
        db.close()


def test_approve_creates_tenant_user_membership_trial_and_emails_the_applicant(client):
    lead_id = _submit(client)
    make_super(client)
    mailer.SENT.clear()
    r = client.post(f"/api/v1/leads/{lead_id}/approve", json={})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "approved" and out["decided_by"] == "miro" and out["tenant_slug"] == "okafor-dental-main-st"

    db = SessionLocal()
    try:
        t = db.query(m.Tenant).filter_by(slug="okafor-dental-main-st").one()
        assert t.name == "Okafor Dental — Main St"
        u = db.query(m.User).filter_by(username="dana@example.com").one()
        assert u.email == "dana@example.com" and u.display_name == "Dana Okafor" and not u.is_superadmin
        mb = db.query(m.Membership).filter_by(user_id=u.id, tenant_id=t.id).one()
        assert mb.role == "admin"
        sub = db.query(m.Subscription).filter_by(tenant_id=t.id).one()
        assert sub.is_trial
        assert db.query(m.Lead).get(lead_id).tenant_id == t.id
        actions = {a.action for a in db.query(m.Audit).filter_by(tenant_id=t.id)}
        assert {"tenant.create", "tenant.user_add", "lead.approve"} <= actions
    finally:
        db.close()

    (to, subject, text), = [s for s in mailer.SENT if s[0] == "dana@example.com"]
    assert "ready" in subject.lower()
    assert f"{settings.base_url}/login?u=dana%40example.com" in text


def test_approve_honours_edited_names_and_uniquifies_a_taken_slug(client):
    lead_id = _submit(client)
    tid = make_super(client)
    # `home` is taken by the superadmin's own tenant (seed_user).
    r = client.post(f"/api/v1/leads/{lead_id}/approve",
                    json={"tenant_name": "Dana's Clinic", "slug": "home", "username": "dana", "display_name": "D. Okafor"})
    assert r.status_code == 200, r.text
    assert r.json()["tenant_slug"] == "home-2"
    db = SessionLocal()
    try:
        assert db.query(m.Tenant).filter_by(slug="home-2").one().name == "Dana's Clinic"
        assert db.query(m.User).filter_by(username="dana").one().display_name == "D. Okafor"
    finally:
        db.close()


def test_approve_is_idempotent_and_a_rejected_lead_cannot_be_approved(client):
    lead_id = _submit(client)
    make_super(client)
    first = client.post(f"/api/v1/leads/{lead_id}/approve", json={}).json()
    second = client.post(f"/api/v1/leads/{lead_id}/approve", json={})
    assert second.status_code == 200 and second.json()["tenant_slug"] == first["tenant_slug"]
    db = SessionLocal()
    try:
        assert db.query(m.Tenant).filter_by(slug=first["tenant_slug"]).count() == 1
    finally:
        db.close()
    assert client.post(f"/api/v1/leads/{lead_id}/reject", json={}).status_code == 409

    other = _submit(client, email="sam@example.net")
    assert client.post(f"/api/v1/leads/{other}/reject", json={}).status_code == 200
    assert client.post(f"/api/v1/leads/{other}/approve", json={}).status_code == 409


def test_approve_for_an_email_that_already_has_an_account_adds_a_membership_only(client):
    seed_user(username="dana@example.com", email="dana@example.com", tenant_slug="dana-old")
    lead_id = _submit(client)
    make_super(client)
    mailer.SENT.clear()
    r = client.post(f"/api/v1/leads/{lead_id}/approve", json={})
    assert r.status_code == 200, r.text
    db = SessionLocal()
    try:
        assert db.query(m.User).filter_by(username="dana@example.com").count() == 1
        u = db.query(m.User).filter_by(username="dana@example.com").one()
        assert db.query(m.Membership).filter_by(user_id=u.id).count() == 2
    finally:
        db.close()
    (_, _, text), = [s for s in mailer.SENT if s[0] == "dana@example.com"]
    assert "existing sign-in" in text


def test_reject_records_the_decision_and_sends_the_fixed_decline(client):
    lead_id = _submit(client)
    make_super(client)
    mailer.SENT.clear()
    r = client.post(f"/api/v1/leads/{lead_id}/reject", json={})
    assert r.status_code == 200
    assert r.json()["status"] == "rejected" and r.json()["decided_by"] == "miro"
    (to, subject, text), = [s for s in mailer.SENT if s[0] == "dana@example.com"]
    assert text == mailer.DECLINE_TEXT
    db = SessionLocal()
    try:
        assert db.query(m.Tenant).count() == 1   # only the superadmin's own
    finally:
        db.close()


def test_decisions_are_superadmin_only(client):
    lead_id = _submit(client)
    assert client.post(f"/api/v1/leads/{lead_id}/approve", json={}).status_code == 401
    seed_user(); login(client)   # a plain tenant admin
    assert client.post(f"/api/v1/leads/{lead_id}/approve", json={}).status_code == 403
    assert client.post(f"/api/v1/leads/{lead_id}/reject", json={}).status_code == 403
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd hub && .venv/bin/pytest tests/test_lead_decisions.py -q -p no:cacheprovider`
Expected: FAIL with 404s (no such routes) and `AttributeError: DECLINE_TEXT`.

- [ ] **Step 3: Add the applicant emails** to `hub/guardian_hub/mailer.py` (append)

```python
DECLINE_TEXT = ("Thanks for your interest in Guardian. We can't set you up at the moment.\n"
                "If that changes we will be in touch.")


def send_account_ready(to: str, username: str, tenant_name: str, *, user_existed: bool) -> None:
    """Approval: where to sign in. Fixed copy; the only variable parts are
    the username and the tenant name the owner confirmed."""
    from urllib.parse import quote
    link = f"{settings.base_url.rstrip('/')}/login?u={quote(username, safe='')}"
    if user_existed:
        how = (f"Your existing sign-in ({username}) works here too -- {tenant_name} now appears in your "
               f"account list.")
    else:
        how = (f"Your username is {username}. Open the link, and we will email you a 6-digit code to "
               f"sign in -- there is no password to remember.")
    send(to, "Your Guardian account is ready",
         f"Your Guardian account for {tenant_name} is ready.\n\n{how}\n\nSign in: {link}\n")


def send_lead_declined(to: str) -> None:
    send(to, "About your Guardian access request", DECLINE_TEXT)
```

- [ ] **Step 4: Add the provisioning helper** to `hub/guardian_hub/accounts.py` (append)

```python
import datetime as _dt
import re as _re


def slugify(text: str) -> str:
    """`Tenant.slug` material: lower-case ASCII letters, digits and single
    hyphens; never empty."""
    s = _re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:48] or "site"


def unique_slug(db: DbSession, base: str) -> str:
    """`base`, or `base-2`, `base-3`... -- the first not already a tenant."""
    base = slugify(base)
    taken = {t.slug for t in db.query(m.Tenant).filter(m.Tenant.slug.like(f"{base}%"))}
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def provision_from_lead(db: DbSession, lead: m.Lead, *, tenant_name: str, slug: str,
                        username: str, display_name: str, by: str, ip: str = ""):
    """Turn an approved access request into an account: tenant, admin user,
    membership, 90-day trial, audit rows. Stamps the lead. Does NOT commit.
    Returns (tenant, user, user_existed)."""
    from . import audit, billing
    tenant = m.Tenant(slug=unique_slug(db, slug), name=tenant_name.strip() or slug)
    db.add(tenant)
    db.flush()
    existed = db.query(m.User).filter_by(username=username.strip().lower()).first() is not None
    user = upsert_user(db, username, lead.email, display_name)
    upsert_membership(db, user, tenant, "admin")
    billing.start_trial(db, tenant.id)
    audit.log(db, tenant.id, by, "superadmin", "tenant.create", target=tenant.slug, ip=ip,
              body={"slug": tenant.slug, "name": tenant.name, "lead": lead.id})
    audit.log(db, tenant.id, by, "superadmin", "tenant.user_add", target=user.username, ip=ip,
              body={"username": user.username, "role": "admin"})
    audit.log(db, tenant.id, by, "superadmin", "lead.approve", target=lead.email, ip=ip,
              body={"lead": lead.id, "existing_user": existed})
    lead.status = "approved"
    lead.decided_at = _dt.datetime.now(_dt.timezone.utc)
    lead.decided_by = by
    lead.tenant_id = tenant.id
    return tenant, user, existed
```

- [ ] **Step 5: Add the schema** in `hub/guardian_hub/schemas.py`, after `LeadsOut`:

```python
class LeadApproveIn(BaseModel):
    """What the owner confirmed in the approve dialog. Empty = derive from the
    lead (site name, then company, then "<Name>'s network"; email as username)."""
    tenant_name: str = Field(default="", max_length=120)
    slug: str = Field(default="", max_length=64)
    username: str = Field(default="", max_length=64)
    display_name: str = Field(default="", max_length=120)
```

- [ ] **Step 6: Add the routes** to `hub/guardian_hub/routers/leads.py`

Add `from ..accounts import provision_from_lead, slugify` to the imports. Move the `LeadRow` construction out of `list_leads` into a helper and add the two routes:

```python
def _row(db: DbSession, r: m.Lead) -> sc.LeadRow:
    slug = ""
    if r.tenant_id:
        t = db.get(m.Tenant, r.tenant_id)
        slug = t.slug if t else ""
    return sc.LeadRow(
        id=r.id, name=r.name, email=r.email, company=r.company,
        network_size=r.network_size, note=r.note, ip=r.ip,
        created=(r.created or dt.datetime.now(dt.timezone.utc)).isoformat(),
        site_name=r.site_name or "", status=r.status or "pending",
        decided_at=r.decided_at.isoformat() if r.decided_at else "",
        decided_by=r.decided_by or "", tenant_slug=slug,
    )


@router.get("/leads", response_model=sc.LeadsOut)
def list_leads(db: DbSession = Depends(get_db), _: m.User = Depends(require_superadmin)):
    """Newest first. No pagination: this is a hand-run funnel with tens of
    rows, and the global rate limit above bounds how fast it can grow."""
    rows = db.query(m.Lead).order_by(m.Lead.created.desc()).limit(500).all()
    return sc.LeadsOut(leads=[_row(db, r) for r in rows])


def _lead_or_404(db: DbSession, lead_id: str) -> m.Lead:
    lead = db.get(m.Lead, lead_id)
    if lead is None:
        raise HTTPException(404, "lead not found")
    return lead


@router.post("/leads/{lead_id}/approve", response_model=sc.LeadRow)
def approve_lead(lead_id: str, body: sc.LeadApproveIn, request: Request, bg: BackgroundTasks,
                 db: DbSession = Depends(get_db), user: m.User = Depends(require_superadmin)):
    lead = _lead_or_404(db, lead_id)
    if lead.status == "approved":
        return _row(db, lead)                      # idempotent
    if lead.status == "rejected":
        raise HTTPException(409, "this request was rejected")
    tenant_name = (body.tenant_name.strip() or lead.site_name or lead.company
                   or f"{lead.name}'s network")
    slug = body.slug.strip() or slugify(tenant_name)
    username = (body.username.strip() or lead.email).lower()
    display_name = body.display_name.strip() or lead.name
    tenant, u, existed = provision_from_lead(
        db, lead, tenant_name=tenant_name, slug=slug, username=username,
        display_name=display_name, by=user.username, ip=_client_ip(request))
    db.commit()
    adminnotify.notify("tenant", f"Approved: {lead.name or lead.email} → {tenant.slug}",
                       f"{u.username} · admin · trial started", tenant=tenant.slug)
    bg.add_task(mailer.send_account_ready, lead.email, u.username, tenant.name, user_existed=existed)
    return _row(db, lead)


@router.post("/leads/{lead_id}/reject", response_model=sc.LeadRow)
def reject_lead(lead_id: str, request: Request, bg: BackgroundTasks,
                db: DbSession = Depends(get_db), user: m.User = Depends(require_superadmin)):
    lead = _lead_or_404(db, lead_id)
    if lead.status == "rejected":
        return _row(db, lead)
    if lead.status == "approved":
        raise HTTPException(409, "this request was already approved")
    lead.status = "rejected"
    lead.decided_at = dt.datetime.now(dt.timezone.utc)
    lead.decided_by = user.username
    db.commit()
    bg.add_task(mailer.send_lead_declined, lead.email)
    return _row(db, lead)
```

(Delete the old `list_leads` body from Task 1 in favour of this one.)

- [ ] **Step 7: Run the decision, lead, tenant and billing tests**

Run: `cd hub && .venv/bin/pytest tests/test_lead_decisions.py tests/test_leads.py tests/test_mailer_leads.py tests/test_tenants.py tests/test_billing*.py -q -p no:cacheprovider`
Expected: all pass. If `test_approve_creates_...` fails on `sub.is_trial`, check the attribute name on `m.Subscription` (`grep -n "is_trial\|plan" hub/guardian_hub/models.py`) and use the model's own field.

- [ ] **Step 8: Commit**

```bash
git add hub/guardian_hub/accounts.py hub/guardian_hub/mailer.py hub/guardian_hub/schemas.py hub/guardian_hub/routers/leads.py hub/tests/test_lead_decisions.py
git commit -m "hub: approve/reject an access request; approval provisions tenant, admin user, trial and emails the applicant

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: CLI `guardian lead approve <id>` (the SMTP-is-down day)

**Files:**
- Modify: `hub/guardian_hub/cli.py`
- Test: `hub/tests/test_cli_lead.py` (new)

**Interfaces:**
- Consumes `accounts.provision_from_lead` (Task 4).
- Produces `guardian lead list` (prints `id status email site_name`) and `guardian lead approve <id>` (derives names exactly as the route does; prints the tenant slug and username).

- [ ] **Step 1: Read how the CLI dispatches** -- `grep -n "args.cmd\|def main\|== \"tenant\"\|tcmd" hub/guardian_hub/cli.py` to find the parser block (lines ~8-24) and the dispatch block. Mirror the `tenant` sub-command structure.

- [ ] **Step 2: Write the failing test** in `hub/tests/test_cli_lead.py`

```python
from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from guardian_hub.cli import main


def _lead(client):
    client.post("/api/v1/public/lead", json={"name": "Dana Okafor", "email": "dana@example.com",
                                             "site_name": "Main St", "accept_terms": True})
    db = SessionLocal()
    try:
        return db.query(m.Lead).one().id
    finally:
        db.close()


def test_cli_approve_provisions_like_the_route(client, capsys):
    lead_id = _lead(client)
    main(["lead", "approve", lead_id])
    out = capsys.readouterr().out
    assert "main-st" in out and "dana@example.com" in out
    db = SessionLocal()
    try:
        t = db.query(m.Tenant).filter_by(slug="main-st").one()
        u = db.query(m.User).filter_by(username="dana@example.com").one()
        assert db.query(m.Membership).filter_by(user_id=u.id, tenant_id=t.id).one().role == "admin"
        assert db.query(m.Lead).get(lead_id).status == "approved"
    finally:
        db.close()
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd hub && .venv/bin/pytest tests/test_cli_lead.py -q -p no:cacheprovider`
Expected: FAIL (argparse: invalid choice 'lead'). If `main` does not accept an argv list, check its signature first (`grep -n "def main" hub/guardian_hub/cli.py`) and adapt the test to how other CLI tests call it (`grep -rn "cli.main\|from guardian_hub.cli" hub/tests | head`).

- [ ] **Step 4: Add the sub-command** -- in the parser block, after the `tenant` parser:

```python
    ld = sub.add_parser("lead"); lds = ld.add_subparsers(dest="lcmd", required=True)
    lds.add_parser("list")
    la = lds.add_parser("approve"); la.add_argument("lead_id")
```

In the dispatch block, mirroring how `tenant` is handled:

```python
    elif args.cmd == "lead":
        from .accounts import provision_from_lead, slugify
        from . import mailer
        db = SessionLocal()
        try:
            if args.lcmd == "list":
                for r in db.query(m.Lead).order_by(m.Lead.created.desc()):
                    print(r.id, r.status, r.email, r.site_name or "-")
            else:
                lead = db.get(m.Lead, args.lead_id)
                if lead is None:
                    raise SystemExit(f"no lead {args.lead_id}")
                if lead.status != "pending":
                    raise SystemExit(f"lead is {lead.status}")
                tenant_name = lead.site_name or lead.company or f"{lead.name}'s network"
                tenant, user, existed = provision_from_lead(
                    db, lead, tenant_name=tenant_name, slug=slugify(tenant_name),
                    username=lead.email, display_name=lead.name, by="cli")
                db.commit()
                mailer.send_account_ready(lead.email, user.username, tenant.name, user_existed=existed)
                print(f"approved: tenant {tenant.slug} · user {user.username}")
        finally:
            db.close()
```

- [ ] **Step 5: Run the CLI test**

Run: `cd hub && .venv/bin/pytest tests/test_cli_lead.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add hub/guardian_hub/cli.py hub/tests/test_cli_lead.py
git commit -m "hub cli: guardian lead list / approve

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Login pre-fills the username from `?u=` and requests the code

**Files:**
- Modify: `web/src/pages/Login.tsx`
- Test: `web/tests/login-prefill.test.tsx` (new)

**Interfaces:**
- Consumes `authStart(username)` from `web/src/api`.
- Produces: `/login?u=<username>` renders the code step with that username after calling `authStart` once.

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import Login from "../src/pages/Login";

vi.mock("../src/api", async () => {
  const actual = await vi.importActual<typeof import("../src/api")>("../src/api");
  return { ...actual, authStart: vi.fn().mockResolvedValue({ sent: true }), authVerify: vi.fn() };
});
import { authStart } from "../src/api";

describe("Login with ?u=", () => {
  it("pre-fills the username, asks for the code once, and lands on the code step", async () => {
    render(
      <MemoryRouter initialEntries={["/login?u=dana%40example.com"]}>
        <Login />
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText(/We emailed a code to dana@example.com/)).toBeInTheDocument());
    expect(vi.mocked(authStart)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(authStart)).toHaveBeenCalledWith("dana@example.com");
    expect(screen.getByLabelText("6-digit code")).toBeInTheDocument();
  });

  it("does nothing special without ?u=", () => {
    render(
      <MemoryRouter initialEntries={["/login"]}>
        <Login />
      </MemoryRouter>,
    );
    expect(screen.getByLabelText("Username")).toHaveValue("");
    expect(vi.mocked(authStart)).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd web && npx vitest run tests/login-prefill.test.tsx`
Expected: FAIL (never reaches the code step).

- [ ] **Step 3: Implement** in `web/src/pages/Login.tsx`

Change the imports to `import { useEffect, useRef, useState, type FormEvent } from "react";` and `import { useNavigate, useSearchParams } from "react-router-dom";`. Inside the component, after the `useState` lines:

```tsx
  // `/login?u=<username>` is the link in the "your account is ready" email:
  // pre-fill the username and request the code straight away, so the person
  // goes email -> click -> code -> in. Once only, even under StrictMode.
  const [params] = useSearchParams();
  const prefill = (params.get("u") ?? "").trim();
  const started = useRef(false);
  useEffect(() => {
    if (!prefill || started.current) return;
    started.current = true;
    setUsername(prefill);
    setBusy(true);
    authStart(prefill)
      .then(() => setStep("code"))
      .catch((err) => setError(err instanceof ApiError ? err.message : "Could not send the code. Try again."))
      .finally(() => setBusy(false));
  }, [prefill]);
```

- [ ] **Step 4: Run the login tests and the full web suite**

Run: `cd web && npx vitest run tests/login-prefill.test.tsx && npx vitest run 2>&1 | tail -3`
Expected: PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/Login.tsx web/tests/login-prefill.test.tsx
git commit -m "web: /login?u= pre-fills the username and requests the code (the account-ready link)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Leads page — status, site name, Approve / Reject with a confirm drawer, `?lead=&decide=` arming

**Files:**
- Modify: `web/src/pages/Leads.tsx`
- Modify: `web/src/styles/tabs.css` (a few rules for the drawer form; follow the file's existing `.g-*` vocabulary)
- Test: `web/tests/leads.test.tsx`

**Interfaces:**
- Consumes `get`, `post` from `web/src/lib/api`; `Drawer`, `Badge`, `useToast` from `web/src/design`; `LeadRow` gains `site_name`, `status`, `decided_at`, `decided_by`, `tenant_slug` (Task 1).
- Produces: `POST /api/v1/leads/{id}/approve` with `{tenant_name, slug, username, display_name}`; `POST /api/v1/leads/{id}/reject` with `{}`.

- [ ] **Step 1: Extend the fixture and write the failing tests** in `web/tests/leads.test.tsx`

Add to the mock of `../src/lib/api`: `post: vi.fn()`, and `import { get, post } from "../src/lib/api";`. Add to every fixture lead: `site_name`, `status: "pending"`, `decided_at: ""`, `decided_by: ""`, `tenant_slug: ""` (give Dana `site_name: "Okafor Dental — Main St"`, Sam `site_name: ""`). Change `renderPage` to accept an optional path: `function renderPage(path = "/settings/leads")` and use it in `initialEntries`. Wrap the tree in `<ToastProvider>` from `../src/design`. Then append:

```tsx
  it("shows status and site name, and approves from a pre-filled confirm", async () => {
    const user = userEvent.setup();
    vi.mocked(me).mockResolvedValue(meFixture(true));
    vi.mocked(get).mockResolvedValue(LEADS);
    vi.mocked(post).mockResolvedValue({ ...LEADS.leads[0], status: "approved", decided_by: "miro", tenant_slug: "okafor-dental-main-st" });
    render(renderPage());
    await waitFor(() => expect(screen.getByText("Dana Okafor")).toBeInTheDocument());
    expect(screen.getAllByText("Pending")).toHaveLength(2);
    expect(screen.getByText("Okafor Dental — Main St")).toBeInTheDocument();

    await user.click(screen.getAllByRole("button", { name: "Approve" })[0]);
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByLabelText("Site name")).toHaveValue("Okafor Dental — Main St");
    expect(within(dialog).getByLabelText("Slug")).toHaveValue("okafor-dental-main-st");
    expect(within(dialog).getByLabelText("Username")).toHaveValue("dana@example.com");
    expect(within(dialog).getByLabelText("Display name")).toHaveValue("Dana Okafor");

    await user.click(within(dialog).getByRole("button", { name: "Create account" }));
    await waitFor(() => expect(vi.mocked(post)).toHaveBeenCalledWith("/api/v1/leads/l2/approve", {
      tenant_name: "Okafor Dental — Main St", slug: "okafor-dental-main-st",
      username: "dana@example.com", display_name: "Dana Okafor",
    }));
  });

  it("rejects after a plain confirm", async () => {
    const user = userEvent.setup();
    vi.mocked(me).mockResolvedValue(meFixture(true));
    vi.mocked(get).mockResolvedValue(LEADS);
    vi.mocked(post).mockResolvedValue({ ...LEADS.leads[1], status: "rejected", decided_by: "miro" });
    render(renderPage());
    await waitFor(() => expect(screen.getByText("Sam Price")).toBeInTheDocument());
    await user.click(screen.getAllByRole("button", { name: "Reject" })[1]);
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Reject request" }));
    await waitFor(() => expect(vi.mocked(post)).toHaveBeenCalledWith("/api/v1/leads/l1/reject", {}));
  });

  it("opens the confirm for the lead named in the URL", async () => {
    vi.mocked(me).mockResolvedValue(meFixture(true));
    vi.mocked(get).mockResolvedValue(LEADS);
    render(renderPage("/settings/leads?lead=l2&decide=approve"));
    await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
    expect(within(screen.getByRole("dialog")).getByLabelText("Username")).toHaveValue("dana@example.com");
  });

  it("shows a decided lead as decided, with no buttons", async () => {
    vi.mocked(me).mockResolvedValue(meFixture(true));
    vi.mocked(get).mockResolvedValue({ leads: [{ ...LEADS.leads[0], status: "approved", decided_by: "miro", decided_at: "2026-09-16T10:00:00Z", tenant_slug: "okafor-dental-main-st" }] });
    render(renderPage());
    await waitFor(() => expect(screen.getByText("Dana Okafor")).toBeInTheDocument());
    expect(screen.getByText(/Approved/)).toBeInTheDocument();
    expect(screen.getByText("okafor-dental-main-st")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  });
```

Add `import userEvent from "@testing-library/user-event";` and `within` to the testing-library import; `import { ToastProvider } from "../src/design";`.

- [ ] **Step 2: Run to verify they fail**

Run: `cd web && npx vitest run tests/leads.test.tsx`
Expected: the four new tests FAIL (no "Pending" text, no Approve button); the three old ones still pass.

- [ ] **Step 3: Implement the page** -- replace `web/src/pages/Leads.tsx` with:

```tsx
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Badge, Card, Drawer, Table, useToast } from "../design";
import type { Column } from "../design";
import { ApiError, get, post } from "../lib/api";
import { useMe } from "../lib/auth";

/** One row of `GET /api/v1/leads`.
 *
 *  Declared here rather than in `types.ts` on purpose: a lead is not a
 *  dashboard payload, its hub schema lives in `schemas.py` (not
 *  `schemas_dash.py`), and `scripts/check_types.py` mirrors only the latter. */
export type LeadRow = {
  id: string;
  name: string;
  email: string;
  company: string;
  network_size: string;
  note: string;
  ip: string;
  created: string;
  site_name: string;
  status: "pending" | "approved" | "rejected" | string;
  decided_at: string;
  decided_by: string;
  tenant_slug: string;
};

type LeadsOut = { leads: LeadRow[] };
type Decide = "approve" | "reject";
type ApproveForm = { tenant_name: string; slug: string; username: string; display_name: string };

const SIZE_LABEL: Record<string, string> = {
  home: "Home",
  "one-site": "One office or shop",
  "few-sites": "A handful of sites",
  msp: "Client sites (MSP)",
};

function when(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** Same rule as the hub's `accounts.slugify`, so the dialog's default matches
 *  what the hub would derive if the field were left blank. */
export function slugify(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48) || "site";
}

function defaultsFor(lead: LeadRow): ApproveForm {
  const tenantName = lead.site_name || lead.company || `${lead.name}'s network`;
  return { tenant_name: tenantName, slug: slugify(tenantName), username: lead.email.toLowerCase(), display_name: lead.name };
}

function StatusCell({ r }: { r: LeadRow }) {
  if (r.status === "approved") {
    return (
      <span>
        <Badge tone="good">Approved</Badge>{" "}
        <span className="hint">by {r.decided_by} · {when(r.decided_at)} · </span>
        <code>{r.tenant_slug}</code>
      </span>
    );
  }
  if (r.status === "rejected") {
    return (
      <span>
        <Badge tone="plain">Rejected</Badge> <span className="hint">by {r.decided_by} · {when(r.decided_at)}</span>
      </span>
    );
  }
  return <Badge tone="warn">Pending</Badge>;
}

export default function Leads() {
  const meQuery = useMe();
  const isSuperadmin = meQuery.data?.user.is_superadmin ?? false;
  const queryClient = useQueryClient();
  const toast = useToast();
  const [params, setParams] = useSearchParams();

  const leadsQuery = useQuery({
    queryKey: ["leads"],
    queryFn: () => get<LeadsOut>("/api/v1/leads"),
    enabled: isSuperadmin,
  });

  // The confirm drawer: which lead, which decision, and the editable fields.
  const [target, setTarget] = useState<{ lead: LeadRow; decide: Decide } | null>(null);
  const [form, setForm] = useState<ApproveForm>({ tenant_name: "", slug: "", username: "", display_name: "" });

  function open(lead: LeadRow, decide: Decide) {
    setForm(defaultsFor(lead));
    setTarget({ lead, decide });
  }
  function close() {
    setTarget(null);
    if (params.get("lead")) setParams({}, { replace: true });
  }

  // `?lead=<id>&decide=approve|reject` is the link in the owner's email: arm
  // that lead's confirm once the list is in. Only for a still-pending lead.
  const leads = leadsQuery.data?.leads ?? [];
  const armId = params.get("lead");
  const armDecide = params.get("decide");
  useEffect(() => {
    if (!armId || target !== null) return;
    const lead = leads.find((l) => l.id === armId);
    if (lead && lead.status === "pending" && (armDecide === "approve" || armDecide === "reject")) open(lead, armDecide);
  }, [armId, armDecide, leads, target]);

  const decision = useMutation({
    mutationFn: ({ lead, decide }: { lead: LeadRow; decide: Decide }) =>
      decide === "approve"
        ? post<LeadRow>(`/api/v1/leads/${encodeURIComponent(lead.id)}/approve`, form)
        : post<LeadRow>(`/api/v1/leads/${encodeURIComponent(lead.id)}/reject`, {}),
    onSuccess: async (row, { decide }) => {
      toast.show(decide === "approve" ? `Account created: ${row.tenant_slug}` : "Request rejected", "plain");
      await queryClient.invalidateQueries({ queryKey: ["leads"] });
      close();
    },
    onError: (err: Error) => toast.show(err instanceof ApiError ? err.message : "That did not go through.", "alert"),
  });

  const pending = useMemo(() => leads.filter((l) => l.status === "pending").length, [leads]);

  const columns: Column<LeadRow>[] = [
    { key: "created", header: "When", width: 170, render: (r) => when(r.created) },
    { key: "status", header: "Status", render: (r) => <StatusCell r={r} /> },
    { key: "name", header: "Name" },
    { key: "email", header: "Email", render: (r) => <a href={`mailto:${r.email}`}>{r.email}</a> },
    { key: "site_name", header: "Site", render: (r) => r.site_name || "—" },
    { key: "company", header: "Company", render: (r) => r.company || "—" },
    { key: "network_size", header: "Looking after", render: (r) => SIZE_LABEL[r.network_size] ?? (r.network_size || "—") },
    { key: "note", header: "Note", render: (r) => <span title={r.note}>{r.note || "—"}</span> },
    { key: "ip", header: "From", width: 130, render: (r) => <code>{r.ip || "—"}</code> },
    {
      key: "id",
      header: "",
      render: (r) =>
        r.status === "pending" ? (
          <span className="g-lead-actions">
            <button type="button" className="primary" onClick={() => open(r, "approve")}>Approve</button>
            <button type="button" onClick={() => open(r, "reject")}>Reject</button>
          </span>
        ) : null,
    },
  ];

  if (meQuery.isLoading) {
    return (
      <Card title="Access requests">
        <p className="hint">Loading…</p>
      </Card>
    );
  }
  if (!isSuperadmin) {
    return (
      <Card title="Access requests">
        <p className="hint">This page is for the Guardian account owner.</p>
      </Card>
    );
  }

  // Pending first, then newest first within each group.
  const rows = [...leads].sort((a, b) => Number(b.status === "pending") - Number(a.status === "pending"));
  const approving = target?.decide === "approve";

  return (
    <>
      <Card
        title="Access requests"
        subtitle={leadsQuery.isLoading ? "Loading…" : `${pending} pending · ${leads.length} in total. Approving creates the account and emails them a sign-in link.`}
      >
        <Table columns={columns} rows={rows} rowKey={(r) => r.id} dense empty="No access requests yet." />
      </Card>

      <Drawer
        open={target !== null}
        onClose={close}
        title={target ? (approving ? `Approve ${target.lead.name || target.lead.email}` : `Reject ${target.lead.name || target.lead.email}`) : ""}
        subtitle={target ? target.lead.email : undefined}
      >
        {target && (
          <form
            className="g-lead-form"
            onSubmit={(e) => {
              e.preventDefault();
              decision.mutate(target);
            }}
          >
            {approving ? (
              <>
                <p className="hint">Creates a tenant on the 90-day trial with this person as its admin, then emails them a sign-in link.</p>
                <label htmlFor="lead-site">Site name</label>
                <input id="lead-site" value={form.tenant_name}
                       onChange={(e) => setForm({ ...form, tenant_name: e.target.value, slug: slugify(e.target.value) })} />
                <label htmlFor="lead-slug">Slug</label>
                <input id="lead-slug" className="mono" value={form.slug} onChange={(e) => setForm({ ...form, slug: e.target.value })} />
                <label htmlFor="lead-username">Username</label>
                <input id="lead-username" className="mono" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
                <label htmlFor="lead-display">Display name</label>
                <input id="lead-display" value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
              </>
            ) : (
              <p className="hint">Marks the request rejected and sends them a short, fixed note. Nothing is created.</p>
            )}
            <div className="g-lead-form-actions">
              <button type="submit" className={approving ? "primary" : "g-danger"} disabled={decision.isPending}>
                {decision.isPending ? "Working…" : approving ? "Create account" : "Reject request"}
              </button>
              <button type="button" onClick={close}>Cancel</button>
            </div>
          </form>
        )}
      </Drawer>
    </>
  );
}
```

- [ ] **Step 4: Style the drawer form** -- append to `web/src/styles/tabs.css`:

```css
/* The Leads page's approve/reject confirm (design/Drawer). Labels over
   inputs, one column, the two actions on a row. */
.g-lead-actions { display: flex; gap: var(--space-2); }
.g-lead-form { display: flex; flex-direction: column; gap: var(--space-2); }
.g-lead-form label { font-size: var(--font-size-xs); color: var(--color-text-muted); margin-top: var(--space-2); }
.g-lead-form input { width: 100%; box-sizing: border-box; }
.g-lead-form-actions { display: flex; gap: var(--space-2); margin-top: var(--space-4); }
```

- [ ] **Step 5: Run the Leads tests, lint, typecheck**

Run: `cd web && npx vitest run tests/leads.test.tsx && npx oxlint src/pages/Leads.tsx && npx tsc -b --pretty false 2>&1 | grep -E "Leads|leads" || echo "types ok"`
Expected: 7 tests pass; no lint or type errors in these files. If `Badge` has no `tone="warn"`, check `web/src/design/Badge.tsx` for its tone union and use the nearest (`warn` exists on `g-tone-*` classes).

- [ ] **Step 6: Commit**

```bash
git add web/src/pages/Leads.tsx web/src/styles/tabs.css web/tests/leads.test.tsx
git commit -m "web: Leads page approves or rejects access requests (confirm drawer, ?lead=&decide= from the owner email)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: The sign-up page, the terms page, every CTA points there, "Log in"

**Files:**
- Create: `site/signup.html`, `site/terms.html`
- Modify: `site/index.html` (CTAs, the `#access` section, header/footer "Sign in")
- Modify: `site/styles.css` (append the sign-up panel rules)
- Modify: `site/app.js` (send `site_name` + `accept_terms`, validate both, new copy, success state)
- Modify: `site/i18n/build_locales.py` (tables follow the English source), then re-run it to regenerate the eight locale copies
- Modify: `hub/tests/test_site_csp.py` (add the two pages to `SITE_HTML_FILES`)
- Test: `web/tests/site-signup.test.ts` (new; the site has no harness of its own, so the test reads the files as text)

**Interfaces:**
- Produces: the form posts `{name, email, company, site_name, network_size, note, website, accept_terms: true}` to `/api/v1/public/lead` from `/signup.html` (same origin; nginx already proxies that exact path).

- [ ] **Step 1: Write the failing test** `web/tests/site-signup.test.ts`

```ts
import { readFileSync, existsSync } from "node:fs";
import { describe, expect, it } from "vitest";

const site = (p: string) => new URL(`../../site/${p}`, import.meta.url);
const read = (p: string) => readFileSync(site(p), "utf8");

describe("the sign-up page", () => {
  it("exists, carries the form with a required terms checkbox, and links to Log in", () => {
    expect(existsSync(site("signup.html"))).toBe(true);
    const html = read("signup.html");
    expect(html).toMatch(/<form id="lead-form"/);
    expect(html).toMatch(/name="site_name"[^>]*required/);
    expect(html).toMatch(/name="accept_terms"[^>]*type="checkbox"[^>]*required/);
    expect(html).toMatch(/href="\/terms\.html"/);
    expect(html).toMatch(/Already have an account\?/);
    expect(html).toMatch(/href="https:\/\/my\.guarder\.io\/login">Log in<\/a>/);
    // CSP: nothing inline, nothing third-party (mirrors hub/tests/test_site_csp.py)
    expect(html).not.toMatch(/<style|\sstyle="|fonts\.googleapis|cdn/);
  });

  it("has a terms page with the placeholders the owner must fill", () => {
    const html = read("terms.html");
    expect(html).toMatch(/Terms of Service/);
    expect(html).toMatch(/\[COMPANY LEGAL NAME\]/);
    expect(html).not.toMatch(/<style|\sstyle="/);
  });

  it("sends every CTA on the home page to the sign-up page and says Log in", () => {
    const html = read("index.html");
    expect(html).not.toMatch(/href="#access"/);
    expect((html.match(/href="\/signup\.html"/g) ?? []).length).toBeGreaterThanOrEqual(3);
    expect(html).not.toMatch(/<form id="lead-form"/);
    expect(html).not.toMatch(/>Sign in</);
    expect((html.match(/href="https:\/\/my\.guarder\.io\/login">Log in<\/a>/g) ?? []).length).toBe(2);
    for (const loc of ["ar", "es", "fr", "de", "pt", "it", "zh", "hi"]) {
      const l = read(`${loc}/index.html`);
      expect(l).not.toMatch(/href="#access"/);
      expect(l).toMatch(/href="\/signup\.html"/);
      expect(l).toMatch(/href="https:\/\/my\.guarder\.io\/login"/);
    }
  });

  it("posts site_name and accept_terms, validates both, and uses the new copy", () => {
    const js = read("app.js");
    expect(js).toMatch(/site_name:\s*siteName/);
    expect(js).toMatch(/accept_terms:\s*true/);
    expect(js).toMatch(/Please give the site a name\./);
    expect(js).toMatch(/Please accept the Terms of Service\./);
    expect(js).toMatch(/review it and email you when your account is ready/);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd web && npx vitest run tests/site-signup.test.ts`
Expected: FAIL (no signup.html).

- [ ] **Step 3: Create `site/signup.html`** -- the app's sign-in screen, in the site's own vocabulary. Copy the `<head>` of `site/index.html` (charset, viewport, title, description, `<link rel="stylesheet" href="/styles.css">`, `<link rel="icon" href="/favicon.svg">`) but with `<title>Sign up — Guardian</title>`, no hreflang links, and `<meta name="robots" content="noindex">`. Body:

```html
<body class="page-auth">
<a class="skip" href="#main">Skip to content</a>
<header class="top">
  <div class="wrap">
    <a class="brand" href="/">
      <svg viewBox="0 0 48 48" aria-hidden="true" focusable="false">
        <path d="M24 3 L42 10 V22 C42 34 34 42 24 45 C14 42 6 34 6 22 V10 Z" fill="var(--acc)"/>
        <path d="M15 24 L21 30 L33 17" fill="none" stroke="var(--accink)" stroke-width="3.6" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
      Guardian
    </a>
    <nav aria-label="Account">
      <a href="https://my.guarder.io/login">Log in</a>
    </nav>
  </div>
</header>

<main id="main" class="auth-main">
  <section class="auth-panel" aria-labelledby="signup-h">
    <h1 id="signup-h">Start your three months</h1>
    <p class="auth-lede">
      Tell us what you are looking after. We review every request by hand and
      email you when your account is ready &mdash; no card, nothing to pay.
    </p>

    <form id="lead-form" novalidate>
      <label class="field">
        <span>Your name</span>
        <input name="name" type="text" maxlength="120" autocomplete="name" required>
      </label>
      <label class="field">
        <span>Email</span>
        <input name="email" type="email" maxlength="200" autocomplete="email" required>
      </label>
      <label class="field">
        <span>What should we call this site?</span>
        <input name="site_name" type="text" maxlength="120" placeholder="e.g. Main Street clinic" required>
      </label>
      <label class="field">
        <span>Company <span class="hint">&mdash; optional</span></span>
        <input name="company" type="text" maxlength="120" autocomplete="organization">
      </label>
      <label class="field">
        <span>What are you looking after?</span>
        <select name="network_size">
          <option value="">Choose one</option>
          <option value="home">My home</option>
          <option value="one-site">One office or shop</option>
          <option value="few-sites">A handful of sites</option>
          <option value="msp">Client sites (MSP or reseller)</option>
        </select>
      </label>
      <label class="field">
        <span>Anything else <span class="hint">&mdash; optional</span></span>
        <textarea name="note" maxlength="2000"></textarea>
      </label>

      <!-- Honeypot: hidden from people, plain to a form-filling bot. -->
      <div class="hp" aria-hidden="true">
        <label for="lead-website">Website</label>
        <input id="lead-website" name="website" type="text" tabindex="-1" autocomplete="off">
      </div>

      <label class="check">
        <input name="accept_terms" type="checkbox" required>
        <span>I agree to the <a href="/terms.html">Terms of Service</a>.</span>
      </label>

      <button class="btn btn-wide" type="submit" id="lead-submit">Request my account</button>
      <p class="form-msg" id="lead-msg" role="status" aria-live="polite"></p>
    </form>

    <p class="auth-alt">Already have an account? <a href="https://my.guarder.io/login">Log in</a></p>
  </section>
</main>

<footer class="auth-foot">
  <div class="wrap">
    <a href="/">guarder.io</a> &middot; <a href="/terms.html">Terms</a> &middot; <a href="https://my.guarder.io/help">Help</a>
  </div>
</footer>
<script src="/app.js" defer></script>
</body>
</html>
```

(Check how `index.html` includes `app.js` -- `grep -n "app.js" site/index.html` -- and use the identical tag.)

- [ ] **Step 4: Create `site/terms.html`** -- same head (title "Terms of Service — Guardian", `noindex`), same header as Step 3, then:

```html
<main id="main" class="auth-main">
  <article class="auth-panel doc-panel">
    <h1>Terms of Service</h1>
    <p class="hint">Last updated: [DATE]. These terms are a first draft prepared for the owner to complete; the bracketed items are placeholders.</p>
    <h2>1. Who we are</h2>
    <p>Guardian is operated by [COMPANY LEGAL NAME], [REGISTERED ADDRESS] ("we"). You can reach us at [CONTACT EMAIL].</p>
    <h2>2. The service</h2>
    <p>Guardian monitors the networks, machines and websites you connect to it and, where you enable it, performs the automatic repairs described in the product. Access is granted by us after a request; we may decline a request without giving a reason.</p>
    <h2>3. Your account</h2>
    <p>You are responsible for the accuracy of what you submit, for keeping access to your sign-in email secure, and for the people you invite to your account. You must only connect networks and systems you own or are authorised to manage.</p>
    <h2>4. Trial and payment</h2>
    <p>New accounts start with a free trial of [TRIAL LENGTH]. Nothing is charged unless you choose a paid plan. Paid plans renew as described at checkout and can be cancelled at any time; service stops at the end of the period paid for.</p>
    <h2>5. Data</h2>
    <p>We store the data the sensor reports so the product can work, for as long as your account exists or as your retention settings require. On request we delete your data, and on request we give you an export first. Details of what is collected are in the product's help pages.</p>
    <h2>6. Acceptable use</h2>
    <p>You will not use Guardian to monitor or act on systems without authorisation, to attack third parties, or in breach of applicable law.</p>
    <h2>7. Availability and liability</h2>
    <p>Guardian is provided as is. We aim for high availability but do not guarantee it, and Guardian is a monitoring aid, not a substitute for your own security practice. To the extent permitted by law our liability is limited to the amount you paid us in the twelve months before the claim.</p>
    <h2>8. Changes and termination</h2>
    <p>We may update these terms; material changes are announced in the product or by email. Either side may end the agreement at any time; sections 5 and 7 survive.</p>
    <h2>9. Law</h2>
    <p>These terms are governed by the laws of [JURISDICTION].</p>
  </article>
</main>
```

- [ ] **Step 5: Append the styles** to `site/styles.css`:

```css
/* ── sign-up / terms pages (the app's sign-in screen, in the site's vocabulary) ─ */
.page-auth { min-height: 100vh; display: flex; flex-direction: column; }
.auth-main { flex: 1; display: flex; align-items: flex-start; justify-content: center; padding: clamp(24px, 6vw, 64px) var(--pad); }
.auth-panel { width: 100%; max-width: 520px; background: var(--panel); border: 1px solid var(--line); padding: clamp(22px, 4vw, 36px); }
.auth-panel h1 { font-size: clamp(24px, 3.2vw, 30px); font-weight: 300; margin: 0 0 8px; }
.auth-lede { color: var(--dim); margin: 0 0 22px; }
.check { display: flex; gap: 10px; align-items: flex-start; margin: 4px 0 18px; font-size: 14px; }
.check input { margin-top: 3px; width: 16px; height: 16px; accent-color: var(--acc); }
.check a { color: var(--acc); }
.btn-wide { display: block; width: 100%; text-align: center; }
.auth-alt { margin: 22px 0 0; font-size: 14px; color: var(--dim); text-align: center; }
.auth-alt a { color: var(--acc); }
.auth-foot { border-top: 1px solid var(--line); padding: 18px 0; font-size: 13px; color: var(--dim); text-align: center; }
.doc-panel { max-width: 720px; }
.doc-panel h2 { font-size: 16px; font-weight: 600; margin: 22px 0 6px; }
.doc-panel p { font-size: 15px; }
```

- [ ] **Step 6: Point every CTA at the page and rename Sign in** in `site/index.html`

- Replace each `href="#access"` (hero, pricing "Free trial" card, footer "Request access") with `href="/signup.html"`.
- Replace both `<a href="https://my.guarder.io">Sign in</a>` (header nav and footer "Account" column) with `<a href="https://my.guarder.io/login">Log in</a>`.
- Replace the whole `<section id="access" …>…</section>` with:

```html
  <!-- the sign-up call: the form itself lives on /signup.html -->
  <section id="access" aria-labelledby="access-h">
    <div class="wrap">
      <h2 id="access-h">Start your three months</h2>
      <p class="lede">
        Tell us what you are looking after and we will set you up &mdash; there is
        no card form and nothing to pay, because every deployment is still done
        by hand at this stage.
      </p>
      <p><a class="btn" href="/signup.html">Start three months free</a></p>
    </div>
  </section>
```

- [ ] **Step 7: Bring the locale tables along and regenerate**

`site/i18n/build_locales.py` asserts every `"en"` fragment exists in `site/index.html` (line ~2802), so:

1. In every table entry, in the `"en"` value AND each translated value, replace `href="#access"` with `href="/signup.html"` and `href="https://my.guarder.io">` with `href="https://my.guarder.io/login">`; in the `"en"` value of the Sign in entry change `Sign in` to `Log in` (translations such as `Anmelden`, `Iniciar sesión` already mean log in; leave them).
2. Delete the entries whose English fragment no longer appears on the home page (the form's labels/placeholders/button and the old `#access` lede: search the script for `Your name`, `Company <span`, `What are you looking after`, `Choose one`, `My home`, `One office or shop`, `A handful of sites`, `Client sites (MSP`, `Anything else`, `lead-submit`, `Tell us what you are looking after and we will come back`). Add one entry for the new lede paragraph if you want it translated; otherwise it stays English in the locale copies, which is acceptable for this task.
3. Run `python3 site/i18n/build_locales.py`; it must exit 0 and rewrite the eight `site/<locale>/index.html` files. Fix any remaining assertion it prints by editing the table entry it names.

- [ ] **Step 8: Send the new fields and copy** in `site/app.js`

After `var email = String(data.get("email") || "").trim();` add:
```js
    var siteName = String(data.get("site_name") || "").trim();
    var terms = form.elements.accept_terms;
```
After the email validation block add:
```js
    if (!siteName) {
      say("Please give the site a name.", "bad");
      form.elements.site_name.focus();
      return;
    }
    if (terms && !terms.checked) {
      say("Please accept the Terms of Service.", "bad");
      terms.focus();
      return;
    }
```
In the `body: JSON.stringify({...})` add `site_name: siteName,` after `company:` and `accept_terms: true,` after `website:` (the hub refuses the request otherwise, and the checkbox is required client-side).
Replace the success line with `say("Thanks — we'll review it and email you when your account is ready.", "ok");` and, just before it, hide the fields so the panel reads as a confirmation: `form.querySelectorAll(".field, .check, #lead-submit").forEach(function (el) { el.hidden = true; });`.

- [ ] **Step 9: Register the pages with the CSP test** -- in `hub/tests/test_site_csp.py` change
`SITE_HTML_FILES = ["index.html"] + [` to `SITE_HTML_FILES = ["index.html", "signup.html", "terms.html"] + [`.

- [ ] **Step 10: Run the tests**

Run: `cd web && npx vitest run tests/site-signup.test.ts && cd ../hub && .venv/bin/pytest tests/test_site_csp.py tests/test_billing_plans.py -q -p no:cacheprovider`
Expected: all pass (the billing-plans test parses the pricing cards' `data-plan` attributes in every locale; they are untouched).

- [ ] **Step 11: Look at it** -- `cd site && python3 -m http.server 8787` then open `http://127.0.0.1:8787/signup.html` and `/terms.html` in the browser pane; check phone width (375px) too: the panel fills the width with 18px gutters and the checkbox row wraps. Stop the server.

- [ ] **Step 12: Commit**

```bash
git add site/signup.html site/terms.html site/index.html site/styles.css site/app.js site/i18n/build_locales.py site/*/index.html hub/tests/test_site_csp.py web/tests/site-signup.test.ts
git commit -m "site: sign-up page with terms acceptance; every CTA leads there; Sign in reads Log in

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Full verification

**Files:** none new.

- [ ] **Step 1: Hub suite**

Run: `cd hub && .venv/bin/pytest tests -q -p no:cacheprovider --deselect tests/test_chat_sweep_mandate.py::test_the_sweep_mandate_is_read_only_from_the_users_own_message --deselect tests/test_reports_api.py::test_scheduler_is_idempotent_within_a_period 2>&1 | tail -3`
Expected: all pass (the two deselected fail before this work; see the session notes of 2026-09-15).

- [ ] **Step 2: Web suite, lint, build**

Run: `cd web && npx vitest run 2>&1 | tail -3 && npx oxlint && npm run build 2>&1 | grep -E "error|built in"`
Expected: all pass, `built in`.

- [ ] **Step 3: Live walk-through on a local hub** (the Playwright config starts one with a seeded demo):

```bash
cd web && find "${TMPDIR:-/tmp}" -maxdepth 1 -name 'guardian-e2e-session-*.json' -delete; npx playwright test e2e/smoke.spec.ts --project=dark 2>&1 | tail -3
```
Expected: pass (proves nothing in the app shell regressed). Then by hand against `http://127.0.0.1:8800` if the server is left up: submit a lead with `curl -s -X POST 127.0.0.1:8800/api/v1/public/lead -H 'content-type: application/json' -d '{"name":"Dana","email":"dana@example.com","site_name":"Main St"}'`, sign in as `demoadmin` (superadmin? if not, use `guardian user add` with `--superadmin`), open `/settings/leads?lead=<id>&decide=approve`, confirm, and check the printed "mail to dana@example.com: Your Guardian account is ready" line in the hub's stdout (`GUARDIAN_DEV_CONSOLE_OTP=1` in that config).

---

## Deploy (owner's go-ahead required)

0. Confirm `GUARDIAN_SMTP_HOST` is set and `GUARDIAN_DEV_CONSOLE_OTP` is absent/0 in `/opt/guardian/hub/.env`, otherwise no email leaves the hub.
1. On the VPS, add to `/opt/guardian/hub/.env`: `GUARDIAN_OWNER_EMAIL=you@example.com` (quote nothing; no spaces).
2. Deploy from a clean checkout of `phase5-closeout` HEAD: `git -C Guardian worktree add --detach <tmp> phase5-closeout && bash <tmp>/deploy/deploy.sh`. `deploy.sh` runs `guardian migrate` (adds the five `lead` columns) and restarts the hub. NOTE: this ships every commit on the branch since the last deploy, including the owner's own six from 2026-09-16; run Task 9 first.
3. Smoke on production: open guarder.io, click "Start three months free" (lands on /signup.html), submit a test lead with your own email, a site name and the terms box ticked; expect the Slack ping AND the email at you@example.com; click Approve; confirm; expect the "account is ready" email; open its link; sign in with the code. Then delete the test tenant (`guardian tenant list` / the superadmin tenants API) if you don't want it kept.

## Self-review notes

- Spec §1 (one extra click) → Task 3 links + Task 7 arming. §2 (provisioning fields, idempotency, 409s) → Task 4. §3 (`/login?u=`) → Task 6 + `send_account_ready`. §4 (fixed decline) → Task 4 `DECLINE_TEXT`. §5 (Slack + email, `GUARDIAN_OWNER_EMAIL`) → Task 3. §6 (duplicates) → Task 2. Site field + copy → Task 8. CLI → Task 5. Migration → Task 1. Edge cases: slug collision (Task 4 `unique_slug` + test), existing user (Task 4 test), bot (Task 3 test), superadmin-only (Task 4 test), SMTP down (Task 3 `send` never raises).
- Names used consistently: `provision_from_lead`, `slugify`, `unique_slug`, `send_lead_notice`, `send_account_ready`, `send_lead_declined`, `DECLINE_TEXT`, `SENT`, `owner_addresses`, `LeadApproveIn`, `LeadRow.{site_name,status,decided_at,decided_by,tenant_slug}`.
