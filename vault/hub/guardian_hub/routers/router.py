"""The site gateway router card: register the router admin credential so the
enrolled sensor -- the only thing that can reach a LAN gateway -- can run a
credential Test against it.

Same credential discipline as `routers/recorders.py`:

* the password is write-only in (`RouterIn.password`), never returned, and only
  `router_store.sensor_payload` ever puts it on the wire, down the authenticated
  sensor channel;
* the audit log records that a credential was set, by NAME, never its value;
* the base URL must resolve to a private (RFC1918 / .local / CGNAT) host -- a
  router admin credential must never be aimable at a public address.

Nothing here mutates the router. The only action is `POST /router/test`, which
queues a login+logout credential check the sensor runs; the disable-remote-admin
flow stays unwired until a live Test proves the handshake.
"""
import re
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from .. import acttokens, audit, models as m, router_store
from ..db import get_db
from ..deps import require_role
from ..integrations import urlpolicy
from ..live import bus
from .write import queue_and_audit

router = APIRouter(prefix="/api/v1", tags=["dashboard-router"])

VIEWER = Depends(require_role("viewer"))
ADMIN = Depends(require_role("admin"))

_DEFAULT_BASE_URL = "https://192.168.1.1"

#: The single-use confirmation every router disable is bound to. Its own name,
#: mirroring `write.FIX_TOOL`: the mint side and the burn side share one
#: `acttokens.mint_chat`/`consume_chat` primitive so there is one confirmation
#: scheme to audit, not two.
ROUTER_DISABLE_TOOL = "router_disable_remote_admin"
ROUTER_DNS_TOOL = "router_set_dns"

#: Validated hub-side as well as in the sensor. A malformed address that
#: reached the router would either be refused with a confusing message or,
#: worse, accepted — and a router pointed at nothing takes the network with
#: it until somebody notices.
_IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")

#: Exactly what turning remote administration off changes, shown to the admin
#: before they confirm. Plain English, no credential, no vendor internals.
_DISABLE_SUMMARY = (
    "Vault's on-site sensor will log in to the router and turn OFF remote "
    "administration: remote management over IPv4 and IPv6, and HTTPS admin over "
    "IPv4 and IPv6, are all set to disabled. Nothing else on the router is "
    "changed. Local admin from your own network still works. This is the one "
    "router setting Vault writes, and only when you confirm it here."
)


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


class RouterIn(BaseModel):
    base_url: str | None = None
    username: str | None = None
    password: str | None = None        # write-only; "" clears, None keeps
    vendor: str | None = None
    enabled: bool | None = None


def _validate_base_url(url: str) -> str:
    """Return a normalised base URL or raise 422. Must be an http(s) URL on a
    private host -- the sensor logs into a LAN gateway, never a public host."""
    url = (url or "").strip().rstrip("/")
    if not url:
        raise HTTPException(422, "Router address is required")
    parts = urlsplit(url if "://" in url else "https://" + url)
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise HTTPException(422, "Router address must be an http(s) URL")
    host = parts.hostname or ""
    if not host:
        raise HTTPException(422, "Router address must include a hostname")
    if not urlpolicy.host_is_private(host):
        raise HTTPException(422, "Router address must be a private LAN address "
                                 "(e.g. https://192.168.1.1)")
    # Re-serialise from the parsed pieces so a scheme we added is kept.
    netloc = parts.netloc or host
    return f"{scheme}://{netloc}"


@router.get("/router")
def get_router(ctx=ADMIN, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    return router_store.out(router_store.get(db, tenant.id))


@router.put("/router")
def save_router(body: RouterIn, request: Request, ctx=ADMIN,
                db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    row = router_store.get(db, tenant.id)
    creating = row is None
    if creating:
        row = m.Router(tenant_id=tenant.id, base_url=_DEFAULT_BASE_URL)
        db.add(row)

    changed_secret_or_addr = False
    changed_addr = False
    if body.base_url is not None:
        new_base = _validate_base_url(body.base_url)
        changed_addr = new_base != row.base_url
        changed_secret_or_addr = changed_secret_or_addr or changed_addr
        row.base_url = new_base
    elif creating:
        row.base_url = _validate_base_url(_DEFAULT_BASE_URL)

    if body.vendor is not None:
        row.vendor = (body.vendor or "fios").strip().lower() or "fios"
    if body.username is not None:
        row.username = (body.username or "").strip()
    if body.enabled is not None:
        row.enabled = bool(body.enabled)
    if body.password is not None:
        router_store.set_password(row, body.password)
        changed_secret_or_addr = True

    # A changed address or credential invalidates the last Test result.
    if changed_secret_or_addr:
        row.status = "pending"
        row.last_test_ok = None
        row.last_test_msg = ""
        row.last_test = None
    # The "Vault turned remote administration off" stamp is NOT symmetric
    # with the Test result. A new password is the same gateway with a new
    # credential, and typing one does not switch remote administration back
    # on -- clearing the stamp there would put the nag the owner already
    # answered straight back on the card. A new ADDRESS is a different router,
    # about which Vault has done nothing.
    if changed_addr:
        row.remote_admin_off_at = None
        row.remote_admin_msg = ""

    audit.log(db, tenant.id, user.username, role,
              "router.create" if creating else "router.save",
              target=row.id, ip=_ip(request),
              body={"base_url": row.base_url, "username": row.username,
                    "credentials_set": bool(body.password)})
    db.commit()
    bus.publish(tenant.id, "router", {"action": "save"})
    return router_store.out(row)


@router.delete("/router")
def delete_router(request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    row = router_store.get(db, tenant.id)
    if row is not None:
        db.delete(row)
        audit.log(db, tenant.id, user.username, role, "router.delete",
                  target=row.id, ip=_ip(request), body=None)
        db.commit()
        bus.publish(tenant.id, "router", {"action": "delete"})
    return {"ok": True}


@router.post("/router/test")
def test_router(request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    row = router_store.get(db, tenant.id)
    if row is None or not router_store.has_credentials(row):
        raise HTTPException(400, "Add the router address and password first")
    if not row.enabled:
        raise HTTPException(400, "This router is disabled")
    # The queue body carries nothing sensitive: the sensor fetches the address
    # and credential itself from GET /api/v1/sensor/router at execution time.
    # NOTE: we deliberately do NOT stamp `row.status = "testing"`. That is a
    # transient the client owns: if no sensor is enrolled (or it is offline)
    # the queue row is never picked up, so a persisted "testing" would strand
    # the card in a disabled, self-polling state that survives a reload with
    # no way out but DB surgery. `Router.status` stays "last known result".
    out = queue_and_audit(db, tenant, user.username, role,
                          "/api/router/test", "router.test",
                          body={}, target=row.id, ip=_ip(request))
    bus.publish(tenant.id, "router", {"action": "test", "queue_id": out["id"]})
    return out


class StationIn(BaseModel):
    mac: str = ""


@router.post("/router/station")
def read_station(body: StationIn, request: Request, ctx=ADMIN,
                 db: DbSession = Depends(get_db)):
    """Ask the gateway what it knows about one device's Wi-Fi. READ-ONLY.

    This exists because Vault cannot otherwise answer its own question.
    Presence is a ping sweep plus ARP (`sensor/lan.py`), and a phone in Wi-Fi
    power save ignores pings -- so it looks offline, and repeatedly rejoining
    looks like "flapping" when the radio never actually left. Signal strength,
    band, link rate and association uptime live on the ROUTER. Reading them is
    the difference between telling someone their phone has a weak signal and
    telling them Vault's own probe missed it.

    Tier 1, not tier 3: nothing is written. It is admin-gated only because it
    rides the router credential, like the Test.
    """
    user, tenant, role = ctx
    row = router_store.get(db, tenant.id)
    if row is None or not router_store.has_credentials(row):
        raise HTTPException(400, "Add the router address and password first")
    if not row.enabled:
        raise HTTPException(400, "This router is disabled")
    out = queue_and_audit(db, tenant, user.username, role,
                          "/api/router/station", "router.station",
                          body={"mac": (body.mac or "").strip().lower()},
                          target=row.id, ip=_ip(request))
    bus.publish(tenant.id, "router", {"action": "station", "queue_id": out["id"]})
    return out


def _dns_summary(server: str) -> str:
    return (
        f"Vault's on-site sensor will log in to the router and set its DNS "
        f"server to {server}. It writes ONLY the DNS field, and clears any "
        f"second entry — a second server is a way around the filter, because "
        f"devices take whichever answers first and half your queries would "
        f"skip it. What the router had before is recorded, so this can be put "
        f"back in one click. Devices keep their old DNS until their lease "
        f"renews, so nothing changes for them until they rejoin."
    )


def _require_tested_router(db, tenant):
    """The same three gates the disable action uses, in one place.

    The last one matters most here: the first live login must never be a
    mutating one. A Test that has already passed proves the handshake
    before Vault writes a setting with that credential.
    """
    row = router_store.get(db, tenant.id)
    if row is None or not router_store.has_credentials(row):
        raise HTTPException(400, "Add the router address and password first")
    if not row.enabled:
        raise HTTPException(400, "This router is disabled")
    if row.last_test_ok is not True:
        raise HTTPException(409, "Run Test connection first and confirm it passes "
                                 "before Vault writes a setting to this router")
    return row


@router.post("/router/read-dns")
def read_router_dns(request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    """Ask the router what DNS it is actually handing out. READ-ONLY.

    No confirmation, because nothing changes — and this is the question the
    setup page needs answered before it can say anything true. Vault can
    only filter a query that reaches it, so "is the router pointed at us?"
    decides whether DNS filtering works at all.
    """
    user, tenant, role = ctx
    row = _require_tested_router(db, tenant)
    out = queue_and_audit(db, tenant, user.username, role,
                          "/api/router/read-dns", "router.read_dns",
                          body={}, target=row.id, ip=_ip(request))
    bus.publish(tenant.id, "router", {"action": "read-dns", "queue_id": out["id"]})
    return out


class DnsIn(BaseModel):
    server: str = ""
    confirm_token: str | None = None
    #: Only for a router that refuses a single entry — it then writes the
    #: SAME address twice rather than somebody else's.
    both_boxes: bool = False


@router.post("/router/set-dns/confirm")
def set_dns_confirm(body: DnsIn, request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    """Mint the one-shot, tier-3 confirmation the DNS change needs."""
    user, tenant, role = ctx
    row = _require_tested_router(db, tenant)
    server = (body.server or "").strip()
    if not _IPV4_RE.match(server):
        raise HTTPException(422, "server must be an IPv4 address")
    args = {"server": server, "both_boxes": bool(body.both_boxes)}
    token, expires = acttokens.mint_chat(
        db, tenant, ROUTER_DNS_TOOL, args, user.id,
        {"path": "/api/v1/router/set-dns"})
    audit.log(db, tenant.id, user.username, role, "router.set_dns.confirm",
              target=row.id, ip=_ip(request), body=args)
    db.commit()
    return {"token": token, "expires": expires.isoformat(timespec="seconds"),
            "tier": 3, "summary": _dns_summary(server)}


@router.post("/router/set-dns")
def set_router_dns(body: DnsIn, request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    """Queue the confirmed DNS change for the sensor to run."""
    user, tenant, role = ctx
    row = _require_tested_router(db, tenant)
    server = (body.server or "").strip()
    if not _IPV4_RE.match(server):
        raise HTTPException(422, "server must be an IPv4 address")
    args = {"server": server, "both_boxes": bool(body.both_boxes)}
    if not body.confirm_token:
        raise HTTPException(
            409, "Changing the router's DNS affects every device on the network: POST "
                 ".../set-dns/confirm first and send confirm_token back")
    if not acttokens.consume_chat(db, body.confirm_token, tenant_id=tenant.id,
                                  tool=ROUTER_DNS_TOOL, args=args, user_id=user.id):
        raise HTTPException(403, "that confirmation is not valid any more. It was already "
                                 "used, it expired, or it was for a different address")
    out = queue_and_audit(db, tenant, user.username, role,
                          "/api/router/set-dns", "router.set_dns",
                          body=args, target=row.id, ip=_ip(request))
    bus.publish(tenant.id, "router", {"action": "set-dns", "queue_id": out["id"]})
    return out


class DnsRestoreIn(BaseModel):
    previous: dict = {}


@router.post("/router/restore-dns")
def restore_router_dns(body: DnsRestoreIn, request: Request, ctx=ADMIN,
                       db: DbSession = Depends(get_db)):
    """Put the router's DNS back exactly as Vault found it.

    No confirmation on the UNDO. A confirmation exists to stop somebody
    changing something by accident; making it harder to put a setting back
    than to change it is the wrong way round, and the moment somebody wants
    this is the moment their network stopped working.
    """
    user, tenant, role = ctx
    row = _require_tested_router(db, tenant)
    if not body.previous:
        raise HTTPException(422, "nothing recorded to restore")
    out = queue_and_audit(db, tenant, user.username, role,
                          "/api/router/restore-dns", "router.restore_dns",
                          body={"previous": dict(body.previous)}, target=row.id,
                          ip=_ip(request))
    bus.publish(tenant.id, "router", {"action": "restore-dns", "queue_id": out["id"]})
    return out


@router.post("/router/disable-remote-admin/confirm")
def disable_remote_admin_confirm(request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    """Mint the one-shot, tier-3 confirmation the disable action needs.

    Disabling remote administration reconfigures the gateway — an outward,
    disruptive change — so it may never run on a bare POST. This mints a token
    bound to (tenant, tool, {}, user) that expires and can be spent once; the
    caller shows `summary` and posts the token back to
    `POST /router/disable-remote-admin`.
    """
    user, tenant, role = ctx
    row = router_store.get(db, tenant.id)
    if row is None or not router_store.has_credentials(row):
        raise HTTPException(400, "Add the router address and password first")
    if not row.enabled:
        raise HTTPException(400, "This router is disabled")
    if row.last_test_ok is not True:
        # Never let the first live login be a mutating one: the credential Test
        # must have proved the handshake before we write a setting with it.
        raise HTTPException(409, "Run Test connection first and confirm it passes "
                                 "before turning remote administration off")
    token, expires = acttokens.mint_chat(
        db, tenant, ROUTER_DISABLE_TOOL, {}, user.id,
        {"path": "/api/v1/router/disable-remote-admin"})
    audit.log(db, tenant.id, user.username, role, "router.disable_remote_admin.confirm",
              target=row.id, ip=_ip(request), body=None)
    db.commit()
    return {"token": token, "expires": expires.isoformat(timespec="seconds"),
            "tier": 3, "summary": _DISABLE_SUMMARY}


@router.post("/router/disable-remote-admin")
def disable_remote_admin(body: dict, request: Request, ctx=ADMIN,
                         db: DbSession = Depends(get_db)):
    """Queue the confirmed, mutating disable action for the sensor to run.

    Requires a fresh `confirm_token` from `.../confirm`, burnt through the same
    atomic single-use primitive chat's tier-3 flow uses. A token bound to a
    different user or tenant, an expired one, and a replayed one are all refused
    with nothing queued. The queue body is empty: the sensor fetches the
    credential itself from `GET /api/v1/sensor/router` at execution time.
    """
    user, tenant, role = ctx
    row = router_store.get(db, tenant.id)
    if row is None or not router_store.has_credentials(row):
        raise HTTPException(400, "Add the router address and password first")
    if not row.enabled:
        raise HTTPException(400, "This router is disabled")
    token = (body or {}).get("confirm_token")
    if not token:
        raise HTTPException(
            409, "Turning remote administration off is disruptive: POST "
                 ".../disable-remote-admin/confirm first and send confirm_token back")
    if not acttokens.consume_chat(db, token, tenant_id=tenant.id,
                                  tool=ROUTER_DISABLE_TOOL, args={}, user_id=user.id):
        raise HTTPException(403, "that confirmation is not valid any more. It was already "
                                 "used, it expired, or it was for a different action")
    out = queue_and_audit(db, tenant, user.username, role,
                          "/api/router/disable-remote-admin", "router.disable_remote_admin",
                          body={}, target=row.id, ip=_ip(request))
    bus.publish(tenant.id, "router", {"action": "disable-remote-admin", "queue_id": out["id"]})
    return out
