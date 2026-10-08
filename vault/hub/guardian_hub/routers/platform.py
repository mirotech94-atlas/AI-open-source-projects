"""The owner portal's read API.

Superadmin only, and separate from `routers/settings.py` on purpose: every
other route in the app answers "what is true of *this tenant*", and this one
answers "what is true of the platform". Keeping it in its own module means
the cross-tenant queries live in exactly one place that is easy to audit.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DbSession

from .. import audit, billing, models as m, platform_settings as ps, schemas_dash as sd
from ..config import settings
from ..db import get_db
from ..deps import require_superadmin
from ..live import bus
from ..payloads import account as account_payload, platform as platform_payload

router = APIRouter(prefix="/api/v1", tags=["platform"])


@router.get("/platform", response_model=sd.PlatformPayload, summary="The owner's ledger")
def get_platform(db: DbSession = Depends(get_db), _owner=Depends(require_superadmin)):
    """Every tenant, with the numbers that say whether they are healthy.

    A tenant admin gets 403 here even for their own row: the value of this
    page is the comparison across customers, which is the owner's business
    and nobody else's.
    """
    return platform_payload.build_payload(db)


# --------------------------------------------------------------------------
# Platform settings: the things the owner can change without a deploy.
#
# Two halves, and the split is the point. Everything in `SPEC` is editable
# and takes effect at once, because every consumer reads it through
# `platform_settings`' accessors rather than `config.settings`. Everything in
# `SECRETS` is reported as set/not-set and is never writable: a credential in
# a database is a credential in every backup, export and laptop copy of it.
# --------------------------------------------------------------------------

class SettingIn(BaseModel):
    key: str = ""
    value: str = ""


def _field(spec, row: "m.PlatformSetting | None") -> sd.PlatformSettingField:
    default = spec.default()
    return sd.PlatformSettingField(
        key=spec.key, label=spec.label, help=spec.help, kind=spec.kind,
        group=spec.group,
        value=str(row.value) if row is not None else str(default),
        overridden=row is not None,
        default=str(default),
        minimum=spec.minimum, maximum=spec.maximum, choices=list(spec.choices),
        updated=row.updated.isoformat(timespec="seconds") if row is not None and row.updated else "",
        updated_by=row.updated_by if row is not None else "",
    )


def _settings_payload(db: DbSession) -> sd.PlatformSettingsPayload:
    stored = {r.key: r for r in db.query(m.PlatformSetting).all()}
    return sd.PlatformSettingsPayload(
        fields=[_field(spec, stored.get(key)) for key, spec in ps.SPEC.items()],
        secrets=[sd.PlatformSecretRow(key=key, label=label, set=is_set())
                 for key, (label, is_set) in ps.SECRETS.items()],
        base_url=settings.base_url,
        version="0.1.0",   # the same string `GET /api/health` reports
    )


@router.get("/platform/tenants/{tid}", response_model=sd.AccountPayload,
            summary="One customer's own configuration")
def get_account(tid: str, db: DbSession = Depends(get_db), _owner=Depends(require_superadmin)):
    """What is actually set up for this customer: their networks, their
    sensors, who can sign in and as what, which integrations are wired, and
    what has recently been done to them.

    Credentials do not cross this boundary. The payload has no field for an
    integration secret, a sensor token or an enrol code -- see
    `payloads/account.py` for why they are absent rather than blanked.
    """
    tenant = db.get(m.Tenant, tid)
    if tenant is None:
        # 404 rather than an empty account: an id that does not exist and a
        # customer with nothing configured are different answers, and only one
        # of them is worth the owner's time.
        raise HTTPException(status_code=404, detail="No such customer")
    return account_payload.build_payload(db, tenant)


class BillingAdjustIn(BaseModel):
    action: str                          # extend_trial | grant_plan
    days: int = Field(14, ge=1, le=365)  # extend_trial
    plan: str = ""                       # grant_plan
    interval: str = ""                   # grant_plan: month | year


@router.post("/platform/tenants/{tid}/billing", response_model=sd.AccountPayload,
             summary="Extend a customer's trial, or grant them a plan with no charge")
def adjust_billing(tid: str, body: BillingAdjustIn, request: Request,
                   db: DbSession = Depends(get_db), owner=Depends(require_superadmin)):
    """The console's two billing writes, and the one thing it will not do.

    Trials and comped plans are Vault's own records (no Stripe objects),
    so the owner may set them here. A plan Stripe is charging for is not:
    `billing.apply_event` rewrites those rows from Stripe's events, and a
    console edit would hold only until the next one. That case is a 409
    carrying the customer's dashboard link, which is where the change
    belongs. Audited like every other platform act, and published to the
    customer's live feed so their Billing page picks it up without a reload.
    """
    tenant = db.get(m.Tenant, tid)
    if tenant is None:
        raise HTTPException(status_code=404, detail="No such customer")
    try:
        if body.action == "extend_trial":
            billing.extend_trial(db, tid, days=body.days)
            detail = {"days": body.days}
        elif body.action == "grant_plan":
            billing.grant_plan(db, tid, plan=body.plan, interval=body.interval)
            detail = {"plan": body.plan, "interval": body.interval}
        else:
            raise HTTPException(status_code=422, detail="action must be extend_trial or grant_plan")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except billing.StripeOwnsThis as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None

    audit.log(db, tid, owner.username, "superadmin", "billing.admin",
              target=body.action, ip=(request.client.host if request.client else ""), body=detail)
    db.commit()
    bus.publish(tid, "billing", {"action": body.action, **detail})
    return account_payload.build_payload(db, tenant)


@router.get("/platform/trends", response_model=sd.PlatformTrendsPayload,
            summary="One alert sparkline per customer")
def get_platform_trends(days: int = 30, db: DbSession = Depends(get_db),
                        _owner=Depends(require_superadmin)):
    """Daily new-alert counts for every customer, for the Ledger's trend column.

    A ledger row says what is true now; this says which way it is going, which
    is the half that decides who to call. Two customers both showing three open
    criticals are not the same customer when one is coming down from twenty.

    Served separately from `GET /platform` so the ledger renders on the first
    payload and the trend column fills in after -- the table is useful without
    it, and a month of history for every tenant should not be on the critical
    path to drawing the list.
    """
    return platform_payload.build_trends(db, days=days)


@router.get("/platform/tenants/{tid}/trends", response_model=sd.AccountTrendsPayload,
            summary="How one customer has actually been doing")
def get_account_trends(tid: str, days: int = 30, db: DbSession = Depends(get_db),
                       _owner=Depends(require_superadmin)):
    """The customer's own alerts, devices and score over the last `days`, with
    each chart's direction of travel stated in a sentence.

    This is a screen the owner opens WITH a customer, so the numbers on it are
    the customer's own: the series come from `history.build_charts`, which is
    what draws that customer's History tab. There is deliberately no second
    computation of "new alerts over 30 days" -- see that function's docstring.
    """
    tenant = db.get(m.Tenant, tid)
    if tenant is None:
        raise HTTPException(status_code=404, detail="No such customer")
    return account_payload.build_trends(db, tenant, days=days)


@router.get("/platform/work", response_model=sd.PlatformWorkPayload,
            summary="Who has been closing the work, per day")
def get_platform_work(days: int = 14, hours: int = 0, tenant: str = "",
                      db: DbSession = Depends(get_db), _owner=Depends(require_superadmin)):
    """Vault's share of the work against yours, across every customer --
    or on one customer's network with `?tenant=<id>`, and per hour over the
    last `?hours=<n>` instead of per day.

    Read off the same audit trail the Audit screen shows, so the chart and the
    list can never disagree about what happened -- the reason the Overview's
    activity feed reads `/platform/audit` rather than an endpoint of its own.
    """
    if tenant and db.get(m.Tenant, tenant) is None:
        raise HTTPException(status_code=404, detail="No such customer")
    return account_payload.build_work(db, days=days, tenant_id=tenant or None, hours=hours)


@router.get("/platform/audit", response_model=sd.PlatformAuditPayload,
            summary="What has been done, across every tenant")
def get_platform_audit(db: DbSession = Depends(get_db), _owner=Depends(require_superadmin)):
    """The newest 200 acts. Rows with no tenant are acts on the PLATFORM rather
    than on a customer, and the console says so rather than leaving the customer
    column blank.

    The audit `body` -- the free-form detail, which is where a changed setting's
    old and new values live -- is not returned. "What happened lately" and "what
    exactly changed in that one act" are different questions, and only the first
    one is this endpoint.
    """
    return account_payload.build_audit(db)


@router.get("/platform/settings", response_model=sd.PlatformSettingsPayload,
            summary="How the platform behaves")
def get_platform_settings(db: DbSession = Depends(get_db), _owner=Depends(require_superadmin)):
    return _settings_payload(db)


@router.post("/platform/settings", response_model=sd.PlatformSettingsPayload)
def set_platform_setting(body: SettingIn, request: Request,
                         db: DbSession = Depends(get_db), owner=Depends(require_superadmin)):
    """Change one setting. The validation message is written to be shown.

    A key that is not in `SPEC` is refused rather than stored: an accepted
    write that nothing reads is the failure mode this whole module exists to
    avoid.
    """
    try:
        stored = ps.set_value(db, body.key, body.value, by=owner.username)
    except ValueError as e:
        raise HTTPException(422, str(e))
    # Audited with NO tenant: this is a platform act, not something done to a
    # customer. `Audit.tenant_id` is nullable for exactly this, and the
    # tenant-scoped audit page in Settings filters on it -- attaching the row
    # to some arbitrary tenant (the first one, say) would publish the
    # operator's own platform changes into that customer's audit log.
    #
    # The VALUE is included on purpose: none of these are secrets, and "who
    # shortened the trial, to what" is precisely what an audit log is for.
    audit.log(db, None, owner.username, "superadmin", "platform.setting",
              target=body.key,
              ip=request.client.host if request.client else "",
              body={"key": body.key, "value": str(stored)})
    db.commit()
    return _settings_payload(db)
