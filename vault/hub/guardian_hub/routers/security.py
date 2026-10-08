import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from ..db import get_db
from ..deps import require_role
from ..payloads import firewall as firewall_payload
from ..payloads import security as security_payload
from .. import audit, emailcheck, models as m, networks, platform_settings, schemas_dash as sd

router = APIRouter(prefix="/api/v1", tags=["dashboard-security"])

VIEWER = Depends(require_role("viewer"))
OPERATOR = Depends(require_role("operator"))

_now = lambda: dt.datetime.now(dt.timezone.utc)


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""




@router.get("/security", response_model=sd.SecurityPayload)
def security(network: str = "", ctx=VIEWER, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    return security_payload.build(db, tenant, net=networks.resolve(db, tenant.id, network))


@router.get("/security/email-check", response_model=sd.EmailVerdictOut)
def email_check(address: str, ctx=VIEWER, db: DbSession = Depends(get_db)):
    """"Someone just emailed me — is this legit?"

    A GET and a viewer route on purpose: it reads DNS and changes nothing,
    and the person who most needs it is often the one with the least
    access. `own_domains` comes from the tenant's own configured sites,
    which is what lets Vault make the one check a generic tool cannot —
    is this a near-miss of a domain that belongs to YOU.
    """
    user, tenant, role = ctx
    own = []
    for site in (tenant.config or {}).get("sites") or []:
        host = str(site.get("host") or site.get("url") or "")
        for prefix in ("https://", "http://"):
            if host.startswith(prefix):
                host = host[len(prefix):]
        host = host.split("/")[0].split(":")[0].strip().lower()
        if host:
            own.append(".".join(host.split(".")[-2:]))
    v = emailcheck.check(address, own_domains=sorted(set(own)),
                         reputation_note=platform_settings.email_reputation_note())
    return sd.EmailVerdictOut(
        address=v.address, domain=v.domain, verdict=v.verdict, headline=v.headline,
        findings=[sd.EmailFinding(tone=f.tone, text=f.text) for f in v.findings],
        limits=v.limits, reputation_note=v.reputation_note,
    )


# --------------------------------------------------------------------------
# Item 21: gateway firewall rules Vault PROPOSES.
#
# Miro's ruling: propose with one-click apply and a timed auto-rollback,
# never write to a router silently. So there is a read and a decline here,
# and `apply` refuses with a reason rather than pretending -- Vault has
# no write path to a gateway, and acquiring one is the router owner's call.
# --------------------------------------------------------------------------

def _rule_out(r: m.FirewallProposal) -> sd.FirewallRule:
    return sd.FirewallRule(
        id=r.id, key=r.key, action=r.action, proto=r.proto, port=r.port, target=r.target,
        title=r.title, because=r.because, evidence=r.evidence, risk=r.risk,
        status=r.status, decided_by=r.decided_by, decided_at=r.decided_at,
        reason=r.reason, applied_at=r.applied_at, rollback_at=r.rollback_at,
        first_seen=r.first_seen, last_seen=r.last_seen,
    )


@router.get("/security/firewall", response_model=sd.FirewallPayload)
def firewall(ctx=VIEWER, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    rules = firewall_payload.refresh(db, tenant)
    ok, why = firewall_payload.can_apply(db, tenant)
    gw = db.query(m.Network).filter_by(tenant_id=tenant.id, is_primary=True).first()
    return sd.FirewallPayload(
        rules=[_rule_out(r) for r in rules], can_apply=ok, why_not=why,
        gateway=(gw.expected_gateway if gw else "") or "",
    )


class FirewallDecision(BaseModel):
    reason: str = ""


@router.post("/security/firewall/{rule_id}/decline", response_model=sd.FirewallRule)
def decline_rule(rule_id: str, body: FirewallDecision, request: Request,
                 ctx=OPERATOR, db: DbSession = Depends(get_db)):
    """A person saying "I know, and no".

    It sticks: `firewall.refresh` never resurrects a decided proposal, so
    the rule does not come back on the next scan. Re-proposing something
    somebody has already answered is how a product trains people to stop
    reading it.
    """
    user, tenant, role = ctx
    row = (db.query(m.FirewallProposal)
           .filter_by(tenant_id=tenant.id, id=rule_id).first())
    if row is None:
        raise HTTPException(404, "no such proposal")
    row.status = "declined"
    row.decided_by, row.decided_at = user.username, _now().isoformat(timespec="seconds")
    row.reason = (body.reason or "")[:300]
    audit.log(db, tenant.id, user.username, role, "firewall.decline", target=row.key,
              ip=_ip(request), body={"reason": row.reason})
    db.commit()
    return _rule_out(row)


@router.post("/security/firewall/{rule_id}/apply", response_model=sd.FirewallRule)
def apply_rule(rule_id: str, request: Request, ctx=OPERATOR, db: DbSession = Depends(get_db)):
    """Refuses, with the reason, until a router write path exists.

    Deliberately a 409 and not a silent no-op: a customer who pressed Apply
    has to learn that nothing changed. The alternative -- marking it applied
    and hoping -- would be Vault claiming a firewall rule exists when it
    does not, which is the worst thing this product could tell somebody.
    """
    user, tenant, role = ctx
    row = (db.query(m.FirewallProposal)
           .filter_by(tenant_id=tenant.id, id=rule_id).first())
    if row is None:
        raise HTTPException(404, "no such proposal")
    ok, why = firewall_payload.can_apply(db, tenant)
    if not ok:
        audit.log(db, tenant.id, user.username, role, "firewall.apply_refused",
                  target=row.key, ip=_ip(request), body={"why": why})
        db.commit()
        raise HTTPException(409, why)
    # Reached the day somebody builds the router write path and flips
    # `can_apply`. It must not fall through to a 409 with an empty message:
    # applying a rule has to stamp `applied_at` AND `rollback_at` together,
    # and until that code exists this says so in its own words.
    raise HTTPException(
        501,
        "This gateway can be written to, but Vault's apply-with-rollback step "
        "has not been built yet. A rule must never be applied without its own "
        "automatic undo deadline.",
    )


# --------------------------------------------------------------------------
# The Wi-Fi guard / checklist / posture drill-down (owner request: clicking
# a failing check like "evil twin" should open detail and offer resolve or
# leave it, same for Security posture).
# --------------------------------------------------------------------------

@router.get("/security/checks/detail", response_model=sd.CheckDetail)
def check_detail(key: str, network: str = "", target: str = "", ctx=VIEWER, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    net = networks.resolve(db, tenant.id, network) if network else None
    detail = security_payload.check_detail(db, tenant, role, key, net=net, target=target)
    if detail is None:
        raise HTTPException(404, "check not found")
    return detail


@router.get("/security/threats/{key}", response_model=sd.ThreatDetail)
def threat_detail(key: str, days: int = 7, ctx=VIEWER, db: DbSession = Depends(get_db)):
    """One attack class from the Threats and Protection matrix, opened up.

    Same shape of drill-down as `/security/checks/detail` above, and here for
    the same reason: a card that states a verdict should be able to show the
    evidence for it.

    The card itself cannot. `payloads.security.threats` builds its `signals`
    from OPEN alerts only, capped at four, so "what did you find and how was it
    settled" is unanswerable from that data -- a settled alert is by definition
    not open. This reads settled alerts too and carries what settled them.

    404 for a key that is not a class, rather than an empty attack type: an
    invented card would claim Vault has a stance on something it has never
    heard of.
    """
    _user, tenant, _role = ctx
    detail = security_payload.threat_detail(db, tenant, key, days=days)
    if detail is None:
        raise HTTPException(404, "no such threat class")
    return detail


#: "leave" records a deliberate "seen it, leaving it as-is"; "undo" reopens
#: it. There is no "resolved" op here on purpose -- see `m.CheckDecision`'s
#: docstring: an autofix-backed check resolves itself the moment the next
#: scan shows it passing, and this table would only be able to disagree with
#: that live fact.
CHECK_DECISION_OPS = {"leave": "left", "undo": "open"}


class CheckDecisionIn(BaseModel):
    key: str
    network: str = ""
    target: str = ""
    reason: str = ""
    expires_at: str = ""


@router.post("/security/checks/{op}", response_model=sd.CheckDecision)
def check_decision_op(op: str, body: CheckDecisionIn, request: Request, ctx=OPERATOR,
                      db: DbSession = Depends(get_db)):
    """Record (or undo) the owner's own call on one failing check -- the
    other half of the "resolve or leave it" pair from `check_detail` above.
    `check_key`/`network_id`/`target` are the check's own natural key (see
    `m.CheckDecision`'s docstring); this never checks that key against a real
    check, the same way `POST /alerts/{key}/{op}` never re-derives the alert
    it is asked to touch -- if the owner left a check that has since
    disappeared entirely, undoing it is still a plain, safe write.

    Same write shape as `write.apply_alert_op`: an `m.Edit` row (replayable),
    an `m.Audit` row, and a live-bus publish -- no write in this app should
    be the only one with no trail.
    """
    if op not in CHECK_DECISION_OPS:
        raise HTTPException(404, "unknown decision op")
    user, tenant, role = ctx
    if op == "leave" and not body.reason.strip():
        raise HTTPException(422, "Add a short reason for this exception")
    if op == "leave" and (tenant.settings or {}).get("require_exception_expiry") and not body.expires_at:
        raise HTTPException(422, "This organization requires an exception expiry")

    # The check's own target is resolved server-side, from the same
    # `check_detail` builder `GET /security/checks/detail` uses, rather than
    # trusted from the request body: for a Wi-Fi guard check `target` is
    # always "Home network" (or a secondary network's name) and the caller
    # need not know that, and this way a decision can never be filed under a
    # target that doesn't match the row it was recorded from.
    net = networks.resolve(db, tenant.id, body.network) if body.network else None
    detail = security_payload.check_detail(db, tenant, role, body.key, net=net, target=body.target)
    if detail is None:
        raise HTTPException(404, "check not found")
    network_id, target = detail.network_id, detail.target

    row = (
        db.query(m.CheckDecision)
        .filter_by(tenant_id=tenant.id, check_key=body.key, network_id=network_id, target=target)
        .first()
    )
    if row is None:
        row = m.CheckDecision(tenant_id=tenant.id, check_key=body.key, network_id=network_id,
                              target=target)
        db.add(row)
    from ..scans import parse_ts
    expiry = parse_ts(body.expires_at) if body.expires_at and op == "leave" else None
    if body.expires_at and op == "leave" and (expiry is None or expiry <= _now()):
        raise HTTPException(422, "Choose a future exception expiry")
    row.expires_at = expiry
    row.status = CHECK_DECISION_OPS[op]
    row.reason = (body.reason + (f" (expires {expiry.isoformat()})" if expiry else "")) if op == "leave" else ""
    row.decided_by = user.username
    row.decided_role = role
    row.decided_at = _now()

    edit_body = {"key": body.key, "network": network_id, "target": target, "action": op,
                "reason": row.reason}
    db.add(m.Edit(tenant_id=tenant.id, who=user.username, path="/api/security-check", body=edit_body))
    audit.log(db, tenant.id, user.username, role, f"check.{op}", target=body.key, ip=_ip(request),
              body=edit_body)
    db.commit()
    return sd.CheckDecision(status=row.status, reason=row.reason, decided_by=row.decided_by,
                            decided_at=row.decided_at.isoformat(timespec="seconds"))
