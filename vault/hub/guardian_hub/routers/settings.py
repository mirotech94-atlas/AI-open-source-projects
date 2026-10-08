"""Settings API: profile, users & roles, targets, auto-fix policy,
thresholds, appearance, sensors and tenants -- plus the tenant-scoped
audit log and the maintenance-window toggle.

Everything here lives under `/api/v1` (same prefix as `write.py`) so that
`/api/v1/config` and `/api/v1/profile` -- the two routes the sensor's edit
contract already replays -- can be relocated into this module while
keeping their exact existing paths as aliases. The `/api/v1/settings/*`
routes are the new, wider surface the web Settings area drives; `targets`,
`autofix` and `thresholds` all funnel into the same `/api/config` Edit path
`update_config` already used, so a sensor only ever needs to know one
config-replay contract.
"""

import datetime as dt
import secrets
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from ..accounts import upsert_membership, upsert_user
from ..config import settings as config_settings
from ..db import get_db
from .. import models as m, audit, accounts, tenant_defaults, maintenance
from ..deps import ROLE_ORDER, current_session, require_role, require_superadmin
from .. import schemas_dash as sd
from .. import adminnotify

router = APIRouter(prefix="/api/v1", tags=["settings"])

VIEWER = Depends(require_role("viewer"))
OPERATOR = Depends(require_role("operator"))
ADMIN = Depends(require_role("admin"))

DEMO_CODE = "424242"

# Every key here must be one the SENSOR actually reads out of
# `Tenant.config["thresholds"]`, because that is the only thing a value
# written through this route can affect -- the hub stores it and hands it
# straight to the sensor. `test_threshold_keys.py` parses the sensor's
# `DEFAULTS` and fails when a key here is not one of them.
#
# That guard exists because this table used to offer `latency_warn_s`, which
# nothing has ever read: the sensor alerts on `http_latency_warn_s`
# (`alerts.py`, `runner.py`). An owner could set "Latency warn" to any value
# and latency alerts kept firing at the untouched default.
THRESHOLD_RANGES = {
    "cert_days_warn": (1, 120),
    "disk_pct_warn": (50, 99),
    "mem_pct_warn": (50, 99),
    "load_per_core_warn": (0.5, 8),
    "http_latency_warn_s": (0.2, 30),
}


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# --------------------------------------------------------------------------
# GET /settings — the whole payload
# --------------------------------------------------------------------------

def _profile_settings(tenant: m.Tenant) -> sd.ProfileSettings:
    return sd.ProfileSettings(**(tenant.settings or {}).get("profile", {}))


def _user_rows(db: DbSession, tenant: m.Tenant) -> list[sd.UserRow]:
    rows = (
        db.query(m.Membership, m.User)
        .join(m.User, m.User.id == m.Membership.user_id)
        .filter(m.Membership.tenant_id == tenant.id)
        .all()
    )
    out = []
    for mb, u in rows:
        out.append(sd.UserRow(username=u.username, display_name=u.display_name, email=u.email, role=mb.role,
                               is_superadmin=u.is_superadmin, last_seen=None))
    return out


def _coerce_expect(expect) -> list[int]:
    """Coerce a stored `expect` value into the list of status-code ints
    `SiteTarget` expects, tolerating every shape it has ever been written
    in: the sensor's own list, the single int an earlier router version
    saved, and a stringy digit (`"200"`) or a list containing one.

    422 validation belongs on the write path (`SiteIn.expect: list[int]`
    in `update_targets` below) only. The reader must never raise -- a
    malformed stored value here used to 422 `GET /settings` and break the
    whole Settings tab for that tenant, which is the same failure class
    the write-side fix in the previous round already fixed for writes.
    `None` / `[]` (absent or explicitly cleared) means "no opinion" and
    stays `[]`; anything else that has no usable int left after coercion
    (a non-digit string, a dict, an all-garbage list, ...) falls back to
    `[200]`, the sensor's own default (`sensor/guardian_sensor/sites.py`:
    `s.get("expect", [200])`)."""

    def _to_int(v):
        if isinstance(v, bool):
            return None
        if isinstance(v, int):
            return v
        if isinstance(v, str) and v.strip().isdigit():
            return int(v.strip())
        return None

    if expect in (None, []):
        return []
    if isinstance(expect, list):
        coerced = [c for c in (_to_int(e) for e in expect) if c is not None]
    else:
        single = _to_int(expect)
        coerced = [single] if single is not None else []
    return coerced or [200]


def _site_targets(tenant: m.Tenant) -> list[sd.SiteTarget]:
    """Tolerant of every shape `expect` has been written in -- see
    `_coerce_expect`. A config carrying any of them must render rather
    than 500 or 422 the whole Settings tab -- which is exactly what the
    live `home` tenant, imported from a real config with `expect: [200]`,
    used to do, and what a malformed stored value (`"200"`, `["abc"]`)
    used to do after that."""
    out = []
    for raw in (tenant.config or {}).get("sites") or []:
        site = {k: v for k, v in raw.items() if k in sd.SiteTarget.model_fields}
        site["expect"] = _coerce_expect(raw.get("expect"))
        out.append(sd.SiteTarget(**site))
    return out


def _host_targets(tenant: m.Tenant) -> list[sd.HostTarget]:
    """`expect_docker` is the sensor's field name; `expect_containers` is
    the deprecated one this router used to write before the rename below.
    Tolerate a stored config still carrying the old key for one release
    -- it maps onto `expect_docker` only when the new key is absent, so a
    config already carrying both (mid-migration) prefers the new one."""
    out = []
    for h in (tenant.config or {}).get("ssh_hosts") or []:
        host = {k: v for k, v in h.items() if k in sd.HostTarget.model_fields}
        if not host.get("expect_docker") and h.get("expect_containers"):
            host["expect_docker"] = h["expect_containers"]
        out.append(sd.HostTarget(**host))
    return out


def _autofix_policy(tenant: m.Tenant) -> sd.AutoFixPolicy:
    return sd.AutoFixPolicy(**(tenant.config or {}).get("auto_fix") or {})


def _thresholds(tenant: m.Tenant) -> dict:
    return dict((tenant.config or {}).get("thresholds") or {})


def _appearance(tenant: m.Tenant) -> sd.Appearance:
    return sd.Appearance(**(tenant.settings or {}).get("appearance") or {})


def _sensor_rows(db: DbSession, tenant: m.Tenant) -> list[sd.SensorRow]:
    now = _now()
    out = []
    for s in db.query(m.Sensor).filter_by(tenant_id=tenant.id).order_by(m.Sensor.name):
        age_s = int((now - s.last_seen).total_seconds()) if s.last_seen else None
        out.append(sd.SensorRow(id=s.id, name=s.name, platform=s.platform, version=s.version, status=s.status,
                                 last_seen=s.last_seen.isoformat() if s.last_seen else None, age_s=age_s))
    return out


def _tenant_row(db: DbSession, t: m.Tenant) -> sd.TenantRow:
    users = db.query(m.Membership).filter_by(tenant_id=t.id).count()
    sensors = db.query(m.Sensor).filter_by(tenant_id=t.id).count()
    demo = bool((t.settings or {}).get("demo_code"))
    return sd.TenantRow(id=t.id, slug=t.slug, name=t.name, brand=t.brand or {}, ntfy_topic=t.ntfy_topic,
                         users=users, sensors=sensors, demo=demo)


def _tenant_rows(db: DbSession) -> list[sd.TenantRow]:
    return [_tenant_row(db, t) for t in db.query(m.Tenant).order_by(m.Tenant.name)]


#: Ports a web interface answers on.
#:
#: This list was briefly narrowed to 443/80 to keep appliance admin panels
#: out, on the theory that they were wrong suggestions. They were not: a
#: printer or an extender serving an unauthenticated admin page IS part of
#: the customer's estate and IS worth watching. What was wrong was calling
#: them "websites Vault is not watching". The sentence changed; the
#: ports came back.
_WEB_PORTS = ("443", "80", "8443", "8080")

#: The one kind never offered here. Cameras have their own tab, their own
#: checks and their own "is it answering" test, so a second place to add
#: one is two sources for one fact.
_NOT_A_SITE = frozenset({"camera"})


def _suggested_sites(db: DbSession, tenant: m.Tenant, configured: list[sd.SiteTarget],
                     servers: list[sd.HostTarget], limit: int = 8) -> list[sd.SuggestedTarget]:
    """Devices on this network answering on a web port that nobody watches.

    Deliberately NOT called "websites Vault is not watching". The first
    version was, and on Miro's own network it offered his router, two Wi-Fi
    extenders and a smart TV -- every one a true observation under a false
    heading. Narrowing the ports did not fix it either: Vault classifies
    49 of his 61 devices as `unknown`, so there is no honest way to tell an
    appliance's admin page from a small web server.

    So the sentence changed instead. A printer serving an unauthenticated
    admin page on port 80 IS part of the estate and IS worth watching; what
    was wrong was the word "website". These are OFFERS: nothing reaches the
    config until a person presses Add, which is the same rule the firewall
    and traffic work follows.

    Everything already configured is masked, by EVERY name it is known
    under: a site's URL host, its `host` field, its own name, and every
    server's name and ssh target. Miro's standing rule -- do not mix hosts
    and servers, "as some might have the same name" -- cuts both ways here:
    a box watched as a server must not be re-offered as an unwatched web
    server just because the site above it is configured by domain and the
    device answers on an IP.

    What this deliberately cannot do is resolve a configured domain to a
    LAN address. Where that leaves a genuine duplicate, it is one the
    customer can see and ignore -- a wrong OFFER costs a glance, and
    Vault writes nothing either way.
    """
    taken = set()

    def mask(value: str) -> None:
        v = (value or "").strip().lower()
        for prefix in ("https://", "http://", "ssh://"):
            if v.startswith(prefix):
                v = v[len(prefix):]
        v = v.split("/")[0]
        if not v:
            return
        taken.add(v)
        # An ssh target is frequently "user@host:port"; mask the host too.
        taken.add(v.split("@")[-1].split(":")[0])

    for site in configured:
        mask(site.url)
        mask(site.host)
        mask(site.name)
    for server in servers:
        mask(server.name)
        mask(server.target)

    # The gateway is never a target, whatever its kind says: it is the way
    # out, and every network has exactly one.
    for row in db.query(m.Network).filter(m.Network.tenant_id == tenant.id).all():
        mask(row.expected_gateway or "")

    out: list[sd.SuggestedTarget] = []
    for d in (db.query(m.Device)
              .filter(m.Device.tenant_id == tenant.id)
              .order_by(m.Device.last_seen.desc()).limit(400).all()):
        ip = (d.ip or "").strip()
        names = {n.strip().lower() for n in (ip, d.name or "", d.label or "") if n.strip()}
        if not ip or names & taken or (d.kind or "").lower() in _NOT_A_SITE:
            continue
        ports = d.ports or {}
        port = next((p for p in _WEB_PORTS if p in ports), "")
        if not port:
            continue
        taken.add(ip.lower())
        since = str(ports.get(port) or "")[:10]
        out.append(sd.SuggestedTarget(
            name=d.label or d.name or ip,
            url=f"{'https' if port in ('443', '8443') else 'http'}://{ip}"
                + ("" if port in ("443", "80") else f":{port}"),
            why=f"port {port} has been open here" + (f" since {since}" if since else ""),
        ))
        if len(out) >= limit:
            break
    return out


@router.get("/settings", response_model=sd.SettingsPayload)
def get_settings(ctx=VIEWER, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    tenants = _tenant_rows(db) if user.is_superadmin else []
    sites, hosts = _site_targets(tenant), _host_targets(tenant)
    return sd.SettingsPayload(
        profile=_profile_settings(tenant), users=_user_rows(db, tenant), sites=sites,
        hosts=hosts, autofix=_autofix_policy(tenant), thresholds=_thresholds(tenant),
        appearance=_appearance(tenant), sensors=_sensor_rows(db, tenant), tenants=tenants,
        suggested_sites=_suggested_sites(db, tenant, sites, hosts),
    )


# --------------------------------------------------------------------------
# Config-replay helper: every one of targets/autofix/thresholds/config
# writes the tenant's FULL config back into a single "/api/config" Edit, the
# shape the sensor's `sync.apply_edit` replays wholesale.
# --------------------------------------------------------------------------

def _write_config(db: DbSession, tenant: m.Tenant, user: m.User, role: str, request: Request,
                   new_config: dict, action: str, audit_body: dict) -> dict:
    tenant.config = new_config
    edit_body = {"config": new_config}
    db.add(m.Edit(tenant_id=tenant.id, who=user.username, path="/api/config", body=edit_body))
    audit.log(db, tenant.id, user.username, role, action, ip=_ip(request), body=audit_body)
    db.commit()
    return new_config


# --------------------------------------------------------------------------
# /config, /profile — existing sensor-contract aliases (moved from write.py)
# --------------------------------------------------------------------------

@router.post("/config")
def update_config(body: dict, request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    return _write_config(db, tenant, user, role, request, body, "config.update", body)


class ProfileIn(BaseModel):
    owner: str = ""
    email: str = ""
    address: str = ""


@router.post("/profile")
def update_profile(body: ProfileIn, request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    settings = dict(tenant.settings or {})
    # Keep whatever "company" the wider `/settings/profile` route may have
    # stored -- this alias only ever carries owner/email/address, and must
    # not silently drop a field it doesn't know about.
    profile = dict(settings.get("profile") or {})
    profile.update(body.model_dump())
    settings["profile"] = profile
    tenant.settings = settings
    # sync.apply_edit reads b.get("owner") / b.get("profile") at path
    # "/api/profile" -- the Edit body nests email/address under "profile"
    # while Tenant.settings.profile keeps the flat shape it already had.
    edit_body = {"owner": body.owner, "profile": {"email": body.email, "address": body.address}}
    db.add(m.Edit(tenant_id=tenant.id, who=user.username, path="/api/profile", body=edit_body))
    audit.log(db, tenant.id, user.username, role, "profile.update", ip=_ip(request), body=edit_body)
    db.commit()
    return tenant.settings


# --------------------------------------------------------------------------
# POST /settings/profile — the wider profile shape (adds `company`)
# --------------------------------------------------------------------------

class ProfileSettingsIn(BaseModel):
    owner: str = ""
    email: str = ""
    address: str = ""
    company: str = ""


@router.post("/settings/profile", response_model=sd.ProfileSettings)
def update_profile_settings(body: ProfileSettingsIn, request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    settings = dict(tenant.settings or {})
    profile = body.model_dump()
    settings["profile"] = profile
    tenant.settings = settings
    # `company` is a Vault-only field -- the sensor's "/api/profile" edit
    # contract only knows owner/email/address, so it is not carried there.
    edit_body = {"owner": body.owner, "profile": {"email": body.email, "address": body.address}}
    db.add(m.Edit(tenant_id=tenant.id, who=user.username, path="/api/profile", body=edit_body))
    audit.log(db, tenant.id, user.username, role, "profile.update", ip=_ip(request), body=edit_body)
    db.commit()
    return sd.ProfileSettings(**profile)


# --------------------------------------------------------------------------
# /settings/users — list, upsert, remove
# --------------------------------------------------------------------------

class UserUpsertIn(BaseModel):
    username: str
    email: str = ""
    display_name: str = ""
    role: str = "viewer"


class UserRemoveIn(BaseModel):
    username: str


@router.get("/settings/users", response_model=list[sd.UserRow])
def list_users(ctx=ADMIN, db: DbSession = Depends(get_db)):
    _, tenant, _ = ctx
    return _user_rows(db, tenant)


@router.post("/settings/users", response_model=sd.UserRow)
def upsert_user_route(body: UserUpsertIn, request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    if body.role not in ROLE_ORDER:
        raise HTTPException(422, "bad role")
    _refuse_a_stranger(db, tenant, body)
    u = upsert_user(db, body.username, body.email, body.display_name)
    mb = upsert_membership(db, u, tenant, body.role)
    audit.log(db, tenant.id, user.username, role, "user.upsert", target=u.username, ip=_ip(request),
              body={"username": u.username, "role": body.role})
    db.commit()
    # Never the superadmin flag: it describes the platform, not this tenant,
    # and a customer admin has no business learning who holds it.
    return sd.UserRow(username=u.username, display_name=u.display_name, email=u.email, role=mb.role,
                       is_superadmin=False, last_seen=None)


#: One answer for every account an admin may not attach, so the refusal does
#: not tell them WHICH kind of account the name belongs to.
_TAKEN = "That username is already in use. Pick another, or use the e-mail address that account signs in with."


def _refuse_a_stranger(db: DbSession, tenant: m.Tenant, body: "UserUpsertIn") -> None:
    """Refuse to attach an existing account the admin has not shown they know.

    Usernames are global, and `upsert_user` finds-or-creates by username
    alone -- so "add user carol" on one customer's Settings page attached
    whoever already held that name on ANY tenant, and the reply (and the
    user list after it) handed back that stranger's e-mail address. Typing a
    superadmin's username attached the platform owner as a direct member,
    which the demo-code sign-in treats as a real grant (audit 2026-09-28).

    So: a person already on this tenant may have their role changed freely;
    a brand-new username is created as before; anyone else is attached only
    when the admin supplies the e-mail address that account already has --
    the proof that they are adding someone they actually know, and nothing
    they did not already have. A superadmin is never attached. Every refusal
    reads the same.
    """
    username = body.username.strip().lower()
    existing = db.query(m.User).filter_by(username=username).first()
    if existing is None:
        return
    if db.query(m.Membership).filter_by(user_id=existing.id, tenant_id=tenant.id).first():
        return
    typed = (body.email or "").strip().lower()
    on_file = (existing.email or "").strip().lower()
    if existing.is_superadmin or not on_file or typed != on_file:
        raise HTTPException(409, _TAKEN)


@router.delete("/settings/users")
def remove_user_route(body: UserRemoveIn, request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    username = body.username.strip().lower()
    target = db.query(m.User).filter_by(username=username).first()
    if not target:
        raise HTTPException(404, "not found")
    mb = db.query(m.Membership).filter_by(user_id=target.id, tenant_id=tenant.id).first()
    if not mb:
        raise HTTPException(404, "not a member of this tenant")

    if mb.role == "admin":
        other_admins = (
            db.query(m.Membership)
            .filter_by(tenant_id=tenant.id, role="admin")
            .filter(m.Membership.id != mb.id)
            .count()
        )
        if other_admins == 0:
            raise HTTPException(422, "cannot remove the last admin of a tenant")

    db.delete(mb)
    # End every session that person has open ON THIS TENANT, in the same
    # commit. Deleting the membership alone left their cookie valid: REST
    # calls would 403, but the session stayed signed in to a tenant it no
    # longer belongs to, and the live socket (see `routers/live._still_member`)
    # is only one of the places that would have to notice. Sessions they hold
    # on other tenants are theirs and are left alone.
    db.query(m.Session).filter_by(user_id=target.id, tenant_id=tenant.id).delete(
        synchronize_session=False)
    audit.log(db, tenant.id, user.username, role, "user.remove", target=target.username, ip=_ip(request))
    db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------
# /settings/targets — sites + hosts, validated, replayed to the sensor
# --------------------------------------------------------------------------

class SiteIn(BaseModel):
    name: str = ""
    url: str = ""
    # A list of acceptable status codes -- see `schemas_dash.SiteTarget`.
    # `None` (the key absent from the request) means "the caller has no
    # opinion, leave the stored value alone"; `[]` (the key present and
    # empty) means "clear it" -- see `update_targets` below, which is the
    # only place these two are told apart.
    expect: list[int] | None = None
    public: bool = False
    host: str = ""
    ssh_host: str = ""


class HostIn(BaseModel):
    name: str = ""
    target: str = ""
    optional: bool = False
    expect_services: list[str] = []
    expect_docker: list[str] = []


class TargetsIn(BaseModel):
    sites: list[SiteIn] = []
    hosts: list[HostIn] = []


def _validate_targets(sites: list[SiteIn], hosts: list[HostIn]) -> None:
    from ..netguard import literal_problem
    for s in sites:
        if not s.name.strip() or urlparse(s.url).scheme not in ("http", "https"):
            raise HTTPException(422, "every site needs a name and an http(s) url")
        # The hub probes these itself too (`routers/live._probe`), from the
        # VPS, where loopback and link-local are the hub's own machine and
        # the cloud metadata service -- never a customer's site, so refused
        # here without DNS. A private LAN address stays allowed: the sensor
        # checks it from inside the network, and Settings suggests exactly
        # such sites. `_probe` re-checks the resolved address before every
        # fetch and never sends the hub to a private one.
        if (why := literal_problem(s.url, allow_lan=True)) is not None:
            raise HTTPException(422, f"{s.name}: {why}")
    from ..fixargs import target_problem
    for h in hosts:
        if not h.name.strip() or not h.target.strip():
            raise HTTPException(422, "every host needs a name and a target")
        if (why := target_problem(h.target)) is not None:
            raise HTTPException(422, f"{h.name}: {why}")


def _sync_site_public(db: DbSession, tenant: m.Tenant, sites: list[SiteIn]) -> None:
    """Mirror each target's `public` tick onto its `Site` row.

    `Site.public` is what the public status page reads
    (`routers/public.py`'s `status_page` filters on it), but `ingest`
    only ever upserts a site's `url`/`last` -- it has no opinion about
    publishing, and the tick itself lives in `Tenant.config["sites"]`.
    Without this sync the column had three readers and no writer, so
    ticking "Show on public status page" saved fine and then changed
    nothing the visitor could see. Matched by name first (the identity
    `ingest._upsert` keys on), falling back to url for a site renamed in
    the sensor's config since its row was created.

    A `Site` row matching neither -- because the editor's Save removed
    that row entirely -- must unpublish, not skip: `want is None` used to
    mean "leave it alone", so a site removed from the editor stayed on
    the public status page forever with no way to un-list it."""
    by_name = {(s.name or "").strip(): bool(s.public) for s in sites}
    by_url = {(s.url or "").strip(): bool(s.public) for s in sites if s.url}
    for row in db.query(m.Site).filter_by(tenant_id=tenant.id):
        want = by_name.get((row.name or "").strip())
        if want is None:
            want = by_url.get((row.url or "").strip())
        if want is None:
            want = False
        if bool(row.public) != want:
            row.public = want
    db.commit()


def _merge_targets(stored: list[dict], incoming: list[dict], keys: tuple[str, ...]) -> list[dict]:
    """Merge an edited target row onto the one already in the config,
    instead of replacing it.

    Same merge-not-replace discipline the integrations config got in
    `f141fb7`, and for the same reason: `SiteIn`/`HostIn` only carry the
    fields this dashboard edits, while a real sensor config carries more
    that nothing here knows about -- `expect_body` on a site, and
    `expect_docker` / `key` on a host (read by `sensor/alerts.py` and
    `sensor/fix.py`). Replacing the dict wholesale silently dropped them
    on the first Save, weakening the checks the admin never touched.

    Matched on any of `keys` (url before name for a site: a rename is the
    common edit, a re-point is not). A row matching nothing is new. A
    stored row nobody sent is deleted -- that is what removing a row in
    the editor means."""
    index: dict[tuple[str, str], dict] = {}
    for row in stored:
        for key in keys:
            value = str(row.get(key) or "").strip()
            if value:
                index.setdefault((key, value), row)

    out = []
    for row in incoming:
        base: dict = {}
        for key in keys:
            value = str(row.get(key) or "").strip()
            if value and (key, value) in index:
                base = index[(key, value)]
                break
        out.append({**base, **row})
    return out


@router.post("/settings/targets")
def update_targets(body: TargetsIn, request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    _validate_targets(body.sites, body.hosts)
    new_config = dict(tenant.config or {})
    # An empty `expect` means "no opinion" and must stay ABSENT from the
    # stored config: the sensor defaults to `[200]` only when the key is
    # missing (`sites.py`'s `s.get("expect", [200])`), so writing `[]`
    # would make `code in []` false forever and report every healthy site
    # as down. Every demo-tenant site is in exactly that state.
    #
    # But that "stay absent" rule must not also make `expect` uneditable.
    # `_merge_targets` does `{**base, **row}`, so a `row` that never
    # carries the key at all falls back to whatever `base` (the stored
    # row) already had -- which is correct when the caller sent no
    # opinion (`site.expect is None`, the key was absent from the
    # request), but wrong when the caller explicitly cleared it
    # (`site.expect == []`, the key was present and empty): that clear
    # must win, not silently revert to the stored value. So an explicit
    # empty list is tracked here and the stored `expect` is force-removed
    # from the merged row afterwards, instead of ever writing `[]`.
    sites_in = []
    explicit_clears: set[int] = set()
    for i, site in enumerate(body.sites):
        row = site.model_dump()
        if site.expect is None:
            row.pop("expect", None)
        elif not site.expect:
            row.pop("expect", None)
            explicit_clears.add(i)
        sites_in.append(row)
    new_config["sites"] = _merge_targets(list(new_config.get("sites") or []), sites_in, ("url", "name"))
    for i in explicit_clears:
        new_config["sites"][i].pop("expect", None)
    new_config["ssh_hosts"] = _merge_targets(list(new_config.get("ssh_hosts") or []),
                                              [h.model_dump() for h in body.hosts], ("name", "target"))
    audit_body = {"sites": new_config["sites"], "hosts": new_config["ssh_hosts"]}
    _write_config(db, tenant, user, role, request, new_config, "targets.update", audit_body)
    _sync_site_public(db, tenant, body.sites)
    return {"sites": new_config["sites"], "hosts": new_config["ssh_hosts"]}


# --------------------------------------------------------------------------
# /settings/autofix — kill switch, cap, attacker method, maintenance window
# --------------------------------------------------------------------------

class MaintenanceWindowIn(BaseModel):
    start: str
    end: str


class AutoFixIn(BaseModel):
    # A WRITE, unlike `AutoFixPolicy`: an omitted field here means the form
    # sent nothing, so the conservative default is the safe one. The read
    # model deliberately differs -- see its comment.
    enabled: bool = False
    max_per_day: int = 0
    attacker_method: str = "auto"
    # Object, not the legacy "HH:MM-HH:MM" string -- see the docstring on
    # `schemas_dash.AutoFixPolicy.maintenance_window`.
    maintenance_window: MaintenanceWindowIn | None = None


@router.post("/settings/autofix", response_model=sd.AutoFixPolicy)
def update_autofix(body: AutoFixIn, request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    # Enabled with a cap of 0 is "off" without saying so: the sensor reads
    # the cap as already reached and repairs nothing (`fix.remediate`). The
    # switch exists for off, and asks for confirmation; a number field that
    # does the same thing silently is how a tenant went a week with no
    # unattended repairs after a form saved its loading placeholder.
    if body.enabled and body.max_per_day < 1:
        raise HTTPException(422, "A daily limit of 0 would stop every automatic repair. "
                                 "Set at least 1, or turn auto-fix off with the switch.")
    new_config = dict(tenant.config or {})
    existing = dict(new_config.get("auto_fix") or {})
    existing["enabled"] = body.enabled
    existing["max_per_day"] = body.max_per_day
    existing["attacker_method"] = body.attacker_method
    existing["maintenance_window"] = body.maintenance_window.model_dump() if body.maintenance_window else None
    new_config["auto_fix"] = existing
    _write_config(db, tenant, user, role, request, new_config, "autofix.update", existing)
    return sd.AutoFixPolicy(**existing)


# --------------------------------------------------------------------------
# /settings/thresholds — validated numeric ranges
# --------------------------------------------------------------------------

@router.post("/settings/thresholds")
def update_thresholds(body: dict[str, float], request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    for key, value in body.items():
        rng = THRESHOLD_RANGES.get(key)
        if rng is None:
            continue
        lo, hi = rng
        if not (lo <= value <= hi):
            raise HTTPException(422, f"{key} must be between {lo} and {hi}")

    new_config = dict(tenant.config or {})
    thresholds = dict(new_config.get("thresholds") or {})
    thresholds.update(body)
    new_config["thresholds"] = thresholds
    _write_config(db, tenant, user, role, request, new_config, "thresholds.update", body)
    return thresholds


# --------------------------------------------------------------------------
# /settings/appearance — theme only round-trips server-side (admin+)
# --------------------------------------------------------------------------

class AppearanceIn(BaseModel):
    theme: str
    accent: str = "blue"


@router.post("/settings/appearance", response_model=sd.Appearance)
def update_appearance(body: AppearanceIn, request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    if body.theme not in ("system", "light", "dark"):
        raise HTTPException(422, "theme must be system|light|dark")
    settings = dict(tenant.settings or {})
    appearance = {"theme": body.theme, "accent": "blue"}   # accent is fixed
    settings["appearance"] = appearance
    tenant.settings = settings
    audit.log(db, tenant.id, user.username, role, "appearance.update", ip=_ip(request), body=appearance)
    db.commit()
    return sd.Appearance(**appearance)


# --------------------------------------------------------------------------
# /settings/sensors — list + revoke
# --------------------------------------------------------------------------

@router.get("/settings/sensors", response_model=list[sd.SensorRow])
def list_sensors(ctx=ADMIN, db: DbSession = Depends(get_db)):
    _, tenant, _ = ctx
    return _sensor_rows(db, tenant)


@router.post("/settings/sensors/{sensor_id}/revoke", response_model=sd.SensorRow)
def revoke_sensor(sensor_id: str, request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    s = db.query(m.Sensor).filter_by(id=sensor_id, tenant_id=tenant.id).first()
    if not s:
        raise HTTPException(404, "sensor not found")
    s.status = "revoked"
    s.token_hash = None
    audit.log(db, tenant.id, user.username, role, "sensor.revoke", target=s.id, ip=_ip(request))
    db.commit()
    age_s = int((_now() - s.last_seen).total_seconds()) if s.last_seen else None
    return sd.SensorRow(id=s.id, name=s.name, platform=s.platform, version=s.version, status=s.status,
                         last_seen=s.last_seen.isoformat() if s.last_seen else None, age_s=age_s)


# --------------------------------------------------------------------------
# /settings/tenants — thin superadmin wrapper (plus demo toggling)
# --------------------------------------------------------------------------

class TenantCreateIn(BaseModel):
    name: str
    slug: str


class BrandPatch(BaseModel):
    name: str | None = None
    accent: str | None = None
    domain: str | None = None


class TenantPatchIn(BaseModel):
    name: str | None = None
    ntfy_topic: str | None = None
    brand: BrandPatch | None = None
    demo: bool | None = None


def _404():
    raise HTTPException(404, "not found")


@router.get("/settings/tenants", response_model=list[sd.TenantRow])
def list_settings_tenants(user: m.User = Depends(require_superadmin), db: DbSession = Depends(get_db)):
    return _tenant_rows(db)


@router.post("/settings/tenants", response_model=sd.TenantRow)
def create_settings_tenant(body: TenantCreateIn, request: Request,
                            user: m.User = Depends(require_superadmin), db: DbSession = Depends(get_db)):
    if db.query(m.Tenant).filter_by(slug=body.slug).first():
        raise HTTPException(409, "slug exists")
    t = m.Tenant(slug=body.slug, name=body.name, config=tenant_defaults.new_tenant_config())
    db.add(t); db.flush()
    adminnotify.notify("tenant", f"New tenant: {t.name}", f"created by {user.username}", tenant=t.slug)
    audit.log(db, t.id, user.username, "superadmin", "tenant.create", target=t.slug, ip=_ip(request),
              body={"slug": t.slug, "name": t.name})
    db.commit()
    return _tenant_row(db, t)


@router.patch("/settings/tenants/{tid}", response_model=sd.TenantRow)
def patch_settings_tenant(tid: str, body: TenantPatchIn, request: Request,
                           user: m.User = Depends(require_superadmin), db: DbSession = Depends(get_db)):
    t = db.get(m.Tenant, tid) or _404()
    payload = body.model_dump(exclude_unset=True, exclude_none=True)
    audit_body: dict = {}

    if "name" in payload:
        t.name = payload["name"]
        audit_body["name"] = t.name
    if "ntfy_topic" in payload:
        t.ntfy_topic = payload["ntfy_topic"]
        audit_body["ntfy_topic"] = t.ntfy_topic
    if "brand" in payload:
        brand = dict(t.brand or {})
        brand.update({k: v for k, v in payload["brand"].items() if v is not None})
        t.brand = brand
        audit_body["brand"] = brand
    if "demo" in payload:
        settings = dict(t.settings or {})
        if payload["demo"]:
            settings["demo_code"] = DEMO_CODE
        else:
            settings.pop("demo_code", None)
        t.settings = settings
        audit_body["demo"] = payload["demo"]

    audit.log(db, t.id, user.username, "superadmin", "tenant.update", target=t.slug, ip=_ip(request), body=audit_body)
    db.commit()
    return _tenant_row(db, t)


@router.delete("/settings/tenants/{tid}")
def delete_settings_tenant(tid: str, request: Request, user: m.User = Depends(require_superadmin),
                            s: m.Session = Depends(current_session), db: DbSession = Depends(get_db)):
    t = db.get(m.Tenant, tid) or _404()
    if s.tenant_id == t.id:
        raise HTTPException(422, "cannot delete your own active tenant -- switch tenants first")
    purged = accounts.purge_tenant(db, t.id)
    # Logged against the ACTING superadmin's tenant, not the one being deleted.
    # The audit row used to be written under `t.id`, which meant the only
    # record that the deletion happened was scoped to a tenant that no longer
    # existed -- unreachable from every view, including the audit log itself.
    audit.log(db, s.tenant_id, user.username, "superadmin", "tenant.delete",
              target=t.slug, ip=_ip(request), body={"tenant_id": t.id, "purged": purged})
    db.delete(t)
    db.commit()
    return {"ok": True, "purged": purged}


# --------------------------------------------------------------------------
# /maintenance — operator-triggerable, suppresses non-CRIT delivery
# --------------------------------------------------------------------------

class MaintenanceIn(BaseModel):
    hours: int


@router.post("/maintenance")
def set_maintenance(body: MaintenanceIn, request: Request, ctx=OPERATOR, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    if body.hours < 0:
        raise HTTPException(422, "hours cannot be negative")
    # `0` ends the window now. Without it a window could only ever be started,
    # never cancelled, so an operator who typed 8 when they meant 1 had to wait
    # seven hours with non-critical alerts suppressed -- the opposite of what a
    # watchdog should do. Clearing the key rather than back-dating it keeps
    # `maintenance_active` a plain presence check.
    now = _now()
    if body.hours == 0:
        until = ""
        settings = dict(tenant.settings or {})
        settings.pop("maintenance_until", None)
        # Pull the recorded window's end back to now as well. Leaving it at
        # the time it would have expired would let a cancelled window go on
        # excusing downtime from the uptime figures for hours after the
        # operator stopped working -- see maintenance.truncate_open.
        settings = maintenance.truncate_open(settings, now)
    else:
        until = (now + dt.timedelta(hours=body.hours)).isoformat()
        settings = dict(tenant.settings or {})
        settings["maintenance_until"] = until
        # `maintenance_until` alone is a mute: it says when suppression
        # stops, never when it started, so nothing downstream can tell which
        # checks fell inside the window. The recorded interval is what lets
        # planned downtime stay out of the availability figures.
        settings = maintenance.record(settings, now, now + dt.timedelta(hours=body.hours))
    tenant.settings = settings
    # Sensor-side edit contract already has a "/api/maintenance" branch;
    # body shape mirrors the one the alert/config edits use ({"hours": n}).
    db.add(m.Edit(tenant_id=tenant.id, who=user.username, path="/api/maintenance", body={"hours": body.hours}))
    audit.log(db, tenant.id, user.username, role, "maintenance", ip=_ip(request), body={"hours": body.hours})
    db.commit()
    return {"until": until}


# --------------------------------------------------------------------------
# /audit — tenant-scoped, admin-only, newest first
# --------------------------------------------------------------------------

@router.get("/audit", response_model=sd.AuditPayload)
def get_audit(limit: int = 200, action: str = "", user: str = "", ctx=ADMIN, db: DbSession = Depends(get_db)):
    _, tenant, _ = ctx
    q = db.query(m.Audit).filter_by(tenant_id=tenant.id)
    if action:
        q = q.filter(m.Audit.action == action)
    if user:
        q = q.filter(m.Audit.user == user)
    rows = q.order_by(m.Audit.id.desc()).limit(min(limit, 1000)).all()
    return sd.AuditPayload(rows=[
        sd.AuditRow(ts=r.ts.isoformat(), user=r.user, role=r.role, action=r.action, target=r.target, ip=r.ip,
                    body=r.body or {})
        for r in rows
    ])


# --------------------------------------------------------------------------
# /heartbeats/{name}/setup + /rotate — the one place the token is rendered
# --------------------------------------------------------------------------
#
# A heartbeat's token is a WRITE credential: whoever holds it can tell
# Vault that last night's backup ran when it did not, which is precisely
# the failure a dead-man switch exists to catch. The sensor used to paste
# that token into the `hb:<name>:setup` alert message, which is how it
# reached push notifications, chat integrations and shareable reports; the
# sensor now points here instead.
#
# "Here" is admin-only and audited. `setup` is a GET but it is a disclosure,
# so it is logged exactly like the rotation is — the audit trail is what
# makes an exposed token traceable afterwards.

def _hb_url(name: str, token: str) -> str:
    return f"{(config_settings.base_url or '').rstrip('/')}/hb/{name}/{token}"


def _hb_setup(hb: m.Heartbeat, rotated: bool) -> sd.HeartbeatSetup:
    url = _hb_url(hb.name, hb.token)
    return sd.HeartbeatSetup(name=hb.name, url=url, command=f"curl -fsS {url}", rotated=rotated)


def _hb_or_404(db: DbSession, tenant_id: str, name: str) -> m.Heartbeat:
    hb = db.query(m.Heartbeat).filter_by(tenant_id=tenant_id, name=name).first()
    if hb is None:
        raise HTTPException(404, "unknown heartbeat")
    return hb


@router.get("/heartbeats/{name}/setup", response_model=sd.HeartbeatSetup)
def heartbeat_setup(name: str, request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    """The command to add to the job, token included. Admin-only, audited."""
    user, tenant, role = ctx
    hb = _hb_or_404(db, tenant.id, name)
    # The audit body names the heartbeat, never the token: the audit log is
    # itself readable in the app, so writing the credential there would just
    # move the leak.
    audit.log(db, tenant.id, user.username, role, "heartbeat.reveal", target=hb.name, ip=_ip(request),
              body={"name": hb.name})
    db.commit()
    return _hb_setup(hb, rotated=False)


@router.post("/heartbeats/{name}/rotate", response_model=sd.HeartbeatSetup)
def heartbeat_rotate(name: str, request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    """Issue a new token for this heartbeat and invalidate the old one.

    The invalidation is immediate and total: `GET /hb/<name>/<token>` in
    `routers/public.py` compares against exactly this column, so the moment
    this commit lands the previous URL 404s. There is no grace period on
    purpose — the reason to rotate is that the old token is believed
    exposed, and a grace period would keep the exposure live. The job that
    used it will start failing its check-in, which is the correct and
    visible outcome: it has to be given the new line.
    """
    user, tenant, role = ctx
    hb = _hb_or_404(db, tenant.id, name)
    hb.token = secrets.token_hex(16)
    audit.log(db, tenant.id, user.username, role, "heartbeat.rotate", target=hb.name, ip=_ip(request),
              body={"name": hb.name})
    db.commit()
    return _hb_setup(hb, rotated=True)


class SavedViewIn(BaseModel):
    name: str
    url: str

class PersonalPreferencesIn(BaseModel):
    view: str = "owner"
    density: str = "compact"
    show_ticker: bool = True
    theme: str = "system"
    saved_views: list[SavedViewIn] = []

@router.get("/preferences")
def personal_preferences(ctx=VIEWER):
    user, tenant, role = ctx
    default_view = "customer" if role == "viewer" else "technician" if role == "operator" else "owner"
    return {"view": default_view, "density": "comfortable" if default_view == "customer" else "compact",
            "show_ticker": default_view != "customer", "theme": "system", "saved_views": [], **(user.preferences or {})}

@router.post("/preferences")
def save_personal_preferences(body: PersonalPreferencesIn, ctx=VIEWER, db: DbSession = Depends(get_db)):
    user, _, _ = ctx
    if body.view not in ("owner", "customer", "technician") or body.density not in ("compact", "comfortable") or body.theme not in ("system", "light", "dark"):
        raise HTTPException(422, "Invalid personal preference")
    from urllib.parse import urlsplit
    if len(body.saved_views) > 20:
        raise HTTPException(422, "Save at most 20 views")
    for view in body.saved_views:
        parsed = urlsplit(view.url)
        if not view.name.strip() or len(view.name) > 60 or len(view.url) > 1000 or parsed.scheme or parsed.netloc or not view.url.startswith("/") or "\\" in view.url:
            raise HTTPException(422, "Use a short name and an app page for each saved view")
    user.preferences = body.model_dump()
    db.commit()
    return user.preferences

class TimezoneIn(BaseModel):
    timezone: str
    network: str = ""

@router.get("/timezone")
def get_timezone(network: str = "", ctx=VIEWER, db: DbSession = Depends(get_db)):
    tenant = ctx[1]
    prefs = tenant.settings or {}
    default = prefs.get("timezone") or "UTC"
    if network:
        net = db.query(m.Network).filter_by(tenant_id=tenant.id, id=network).first()
        if not net:
            raise HTTPException(404, "Network not found")
        override = (prefs.get("network_timezones") or {}).get(net.id)
        return {"timezone": override or default, "inherited": not bool(override)}
    return {"timezone": default, "inherited": False}

@router.post("/timezone")
def save_timezone(body: TimezoneIn, request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        ZoneInfo(body.timezone or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, "Use a timezone such as America/New_York")
    user, tenant, role = ctx
    prefs = dict(tenant.settings or {})
    if body.network:
        net = db.query(m.Network).filter_by(tenant_id=tenant.id, id=body.network).first()
        if not net:
            raise HTTPException(404, "Network not found")
        overrides = dict(prefs.get("network_timezones") or {})
        if body.timezone:
            overrides[net.id] = body.timezone
        else:
            overrides.pop(net.id, None)
        prefs["network_timezones"] = overrides
    else:
        prefs["timezone"] = body.timezone or "UTC"
    tenant.settings = prefs
    audit.log(db, tenant.id, user.username, role, "timezone.update", ip=_ip(request), body=body.model_dump())
    db.commit()
    return body.model_dump()

class AutoFixOverrideIn(BaseModel):
    host: str
    action: str
    mode: str

def _policy_hosts(db: DbSession, tenant: m.Tenant) -> list[str]:
    """Every host this policy could name.

    It used to be `config["ssh_hosts"]` alone, which no tenant on the live
    hub has ever set -- so the Host dropdown was permanently empty and the
    whole control unusable. Vault knows its hosts from what it has
    actually seen (`m.Host`), and those are the machines it runs repairs on,
    so they belong in the list too. The union, deduplicated and sorted.
    """
    names = {h["name"] for h in (tenant.config or {}).get("ssh_hosts") or [] if h.get("name")}
    names |= {h.name for h in db.query(m.Host).filter_by(tenant_id=tenant.id).all() if h.name}
    return sorted(names)


@router.get("/settings/autofix/overrides")
def get_autofix_overrides(ctx=VIEWER, db: DbSession = Depends(get_db)):
    from ..remedy import FIX_LABELS
    cfg = ctx[1].config or {}
    return {"overrides": (cfg.get("auto_fix") or {}).get("overrides") or {},
            "hosts": _policy_hosts(db, ctx[1]),
            "actions": FIX_LABELS}

@router.post("/settings/autofix/overrides")
def save_autofix_override(body: AutoFixOverrideIn, request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    from ..remedy import FIX_LABELS
    user, tenant, role = ctx
    cfg = dict(tenant.config or {})
    # The same list the dropdown offered: validating against a narrower set
    # than was offered means a person can pick a host and then be told it is
    # not a host.
    if body.host not in _policy_hosts(db, tenant) or body.action not in FIX_LABELS:
        raise HTTPException(422, "Choose a configured host and supported action")
    if body.mode not in ("inherit", "manual"):
        raise HTTPException(422, "Choose inherited policy or manual approval")
    policy = dict(cfg.get("auto_fix") or {})
    overrides = dict(policy.get("overrides") or {})
    host = dict(overrides.get(body.host) or {})
    if body.mode == "inherit":
        host.pop(body.action, None)
    else:
        host[body.action] = "manual"
    overrides[body.host] = host
    policy["overrides"] = overrides
    cfg["auto_fix"] = policy
    _write_config(db, tenant, user, role, request, cfg, "autofix.override", body.model_dump())
    return {"ok": True}


class SiteContextIn(BaseModel):
    context: str

@router.get("/site-context")
def get_site_context(ctx=VIEWER):
    return {"context": (ctx[1].settings or {}).get("site_context") or "general"}

@router.post("/site-context")
def save_site_context(body: SiteContextIn, request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    if body.context not in ("general", "home", "business"):
        raise HTTPException(422, "Choose general, home or business")
    user, tenant, role = ctx
    tenant.settings = {**(tenant.settings or {}), "site_context":body.context}
    audit.log(db,tenant.id,user.username,role,"site.context",ip=_ip(request),body=body.model_dump())
    db.commit()
    return body.model_dump()


class ExceptionPolicyIn(BaseModel):
    require_expiry: bool = False

@router.get("/settings/exception-policy")
def get_exception_policy(ctx=VIEWER):
    return {"require_expiry": bool((ctx[1].settings or {}).get("require_exception_expiry"))}

@router.post("/settings/exception-policy")
def save_exception_policy(body: ExceptionPolicyIn, request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    tenant.settings = {**(tenant.settings or {}), "require_exception_expiry": body.require_expiry}
    audit.log(db, tenant.id, user.username, role, "security.exception-policy", ip=_ip(request), body=body.model_dump())
    db.commit()
    return body.model_dump()
