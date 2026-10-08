"""Shared account lifecycle helpers: user/membership upserts, tenant purge.

Three call sites (the tenants admin API, the `guardian user add` CLI, and
the Nexus importer) all need the same "find-or-create a User by username,
then find-or-create a Membership for a tenant" logic. This module is the
single implementation; callers are responsible for committing.

`purge_tenant` is the other end of that lifecycle -- the rows a tenant owns,
removed when the tenant is.
"""

import datetime as _dt
import re as _re

from sqlalchemy import text
from sqlalchemy.orm import Session as DbSession

from . import models as m, tenant_defaults


# Tables that carry a `tenant_id` column but are not the tenant's own data,
# so purging a tenant must not delete their rows -- only unlink them. `lead`
# is the product owner's sales pipeline: `tenant_id` merely records which
# tenant a lead's approval produced, the lead itself belongs to the person
# who submitted it and outlives the tenant (see `models.Lead`).
NOT_TENANT_SCOPED = frozenset({"lead"})

# Every mapped table that scopes its rows by tenant, read off the metadata
# rather than hand-listed. A hand-written list is a list that goes stale: a
# table added later would keep its rows after its tenant was deleted, and
# nothing would fail to say so.
TENANT_SCOPED_TABLES: tuple[str, ...] = tuple(
    t.name for t in m.Base.metadata.sorted_tables
    if "tenant_id" in t.c and t.name not in NOT_TENANT_SCOPED
)


def purge_tenant(db: DbSession, tenant_id: str) -> dict[str, int]:
    """Delete every row belonging to a tenant. Does NOT delete the Tenant row
    itself, and does NOT commit -- the caller does both.

    The models declare `ForeignKey("tenant.id", ondelete="CASCADE")` on each
    of these tables and `db.py` sets `PRAGMA foreign_keys=ON`, so deleting
    the Tenant *looks* like it should take its rows with it. It does for some
    tables and not others: only 12 of these 32 carry that foreign key in the
    deployed schema. The rest were added by later migrations that declared
    `tenant_id` as a plain column, so no constraint exists to cascade and a
    bare `db.delete(tenant)` simply left their rows behind -- which is how
    deleting one demo tenant stranded 70,604 rows across 14 tables that no
    view could reach and no query would ever clean up.

    A test would not have caught it: tests build their schema with
    `create_all`, which emits every declared foreign key, so the cascade that
    is missing in production works there. Deleting in bulk by `tenant_id`
    depends on no constraint at all, and so behaves the same in both.

    Returns the per-table deleted counts, omitting tables that had none, so
    the caller can report and audit what actually went.
    """
    out: dict[str, int] = {}
    for table in TENANT_SCOPED_TABLES:
        # `table` comes from SQLAlchemy metadata, never from a request.
        res = db.execute(
            text(f"DELETE FROM {table} WHERE tenant_id = :tid"), {"tid": tenant_id}
        )
        if res.rowcount:
            out[table] = res.rowcount
    # `lead` is excluded from the delete loop above (see NOT_TENANT_SCOPED)
    # but still points at the doomed tenant, so unlink rather than delete it.
    # The model declares `ondelete="SET NULL"`, but the migrated production
    # schema has no such foreign key in the actual database, so nothing else
    # would clear it.
    db.execute(text("UPDATE lead SET tenant_id = NULL WHERE tenant_id = :tid"), {"tid": tenant_id})
    return out


def upsert_user(
    db: DbSession,
    username: str,
    email: str,
    display_name: str = "",
    superadmin: bool | None = None,
) -> m.User:
    """Find a User by (lowercased) username, or create one.

    A brand-new user gets `email`/`display_name`/`is_superadmin` from the
    arguments given. An existing user's `email`/`display_name` are left
    alone (callers that import/administer users do not silently overwrite
    what's on record); `is_superadmin` is only ever turned on, never off,
    and only when `superadmin` is explicitly passed (not None).
    """
    username = username.strip().lower()
    user = db.query(m.User).filter_by(username=username).first()
    if not user:
        user = m.User(
            username=username,
            email=email,
            display_name=display_name,
            is_superadmin=bool(superadmin),
        )
        db.add(user)
        db.flush()
    elif superadmin is not None:
        user.is_superadmin = user.is_superadmin or superadmin
    return user


def upsert_membership(db: DbSession, user: m.User, tenant: m.Tenant, role: str) -> m.Membership:
    """Find-or-create the (user, tenant) Membership and set its role."""
    mb = db.query(m.Membership).filter_by(user_id=user.id, tenant_id=tenant.id).first()
    if mb:
        mb.role = role
    else:
        mb = m.Membership(user_id=user.id, tenant_id=tenant.id, role=role)
        db.add(mb)
        db.flush()
    return mb


def slugify(value: str) -> str:
    """`Tenant.slug` material: lower-case ASCII letters, digits and single
    hyphens; never empty."""
    s = _re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
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
    membership, 90-day trial, audit rows. Stamps the lead.

    Does NOT commit itself -- the trial helper's own commit is the boundary,
    which is why it runs last. `billing.start_trial` commits internally, so
    everything staged above it (tenant, user, membership, the audit rows and
    the lead's own decision stamp) is only persisted once the trial start
    succeeds too: a failure anywhere before that point leaves nothing
    committed, and a retry of the same approval starts clean rather than
    creating a second tenant for one lead.

    Returns (tenant, user, user_existed)."""
    from . import audit, billing
    # The shared policy every organization starts on. This is the path almost
    # every real customer tenant is created through, so a bare config here
    # would mean the defaults applied everywhere except where it matters most.
    tenant = m.Tenant(slug=unique_slug(db, slug), name=tenant_name.strip() or slug,
                      config=tenant_defaults.new_tenant_config())
    db.add(tenant)
    db.flush()
    existed = db.query(m.User).filter_by(username=username.strip().lower()).first() is not None
    user = upsert_user(db, username, lead.email, display_name)
    upsert_membership(db, user, tenant, "admin")
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
    billing.start_trial(db, tenant.id)
    return tenant, user, existed
