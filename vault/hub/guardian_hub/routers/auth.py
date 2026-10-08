import datetime as dt
import hmac
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from sqlalchemy import update
from sqlalchemy.orm import Session as DbSession
from ..db import get_db
from .. import models as m, schemas as sc, security, mailer, ratelimit
from ..config import settings
from ..deps import COOKIE, current_session, current_user, memberships
from ..chat import providers
from .. import adminnotify

router = APIRouter(prefix="/api/v1", tags=["auth"])
_now = lambda: dt.datetime.now(dt.timezone.utc)

# Anti-brute-force limits on OTP requests: per-username caps a single
# account's inbox from being spammed with codes; per-IP caps one client from
# farming codes across many usernames.
AUTH_START_PER_USER = (5, 600)   # 5 requests / 10 min for one username
AUTH_START_PER_IP = (30, 600)    # 30 requests / 10 min for one client
AUTH_VERIFY_PER_IP = (20, 600)   # 20 requests / 10 min for one client -- code/OTP guessing guard
# Per account, whichever addresses the guesses come from: the per-IP cap alone
# lets a botnet spend every code's full attempt budget, code after code. Ten
# is two codes' worth of honest typos. Applied AFTER the demo-code branch --
# every public demo visitor signs in as the same `demo` username.
AUTH_VERIFY_PER_USER = (10, 600)

@router.post("/auth/start")
def start(body: sc.AuthStart, bg: BackgroundTasks, request: Request, db: DbSession = Depends(get_db)):
    username = body.username.strip().lower()
    ip = request.client.host if request.client else ""
    ok_user = ratelimit.allow(f"auth_start:user:{username}", *AUTH_START_PER_USER)
    ok_ip = ratelimit.allow(f"auth_start:ip:{ip}", *AUTH_START_PER_IP)
    if not ok_user or not ok_ip:
        raise HTTPException(429, "too many requests, try again later")
    u = db.query(m.User).filter(m.User.username == username).first()
    if u:
        db.query(m.Otp).filter(m.Otp.user_id == u.id, m.Otp.used == False).update({"used": True})
        code = security.new_code()
        db.add(m.Otp(user_id=u.id, code_hash=security.hash_code(code), expires=_now() + dt.timedelta(seconds=settings.otp_ttl_seconds)))
        db.commit()
        bg.add_task(mailer.send_otp, u.email, code)
    else:
        # Unknown user: still hash a fresh code so this branch costs about the
        # same as the known-user branch above (no user-enumeration timing tell).
        security.hash_code(security.new_code())
    return {"sent": True}

def _me(db, u, active_id):
    ts = [sc.TenantOut(id=t.id, slug=t.slug, name=t.name, role=r) for t, r in memberships(db, u)]
    act = next((t for t in ts if t.id == active_id), ts[0] if ts else None)
    return sc.MeOut(user=sc.UserOut(username=u.username, display_name=u.display_name, is_superadmin=u.is_superadmin), tenants=ts, active_tenant=act,
                    chat_providers=providers.configured_ladder())

@router.post("/auth/verify", response_model=sc.MeOut)
def verify(body: sc.AuthVerify, request: Request, response: Response, db: DbSession = Depends(get_db)):
    ip = request.client.host if request.client else ""
    if not ratelimit.allow(f"auth_verify:ip:{ip}", *AUTH_VERIFY_PER_IP):
        raise HTTPException(429, "too many requests, try again later")
    u = db.query(m.User).filter(m.User.username == body.username.strip().lower()).first()

    # Demo sign-in: a tenant flag, not a fourth role. If this user is a
    # member of a tenant whose settings carry a (non-empty) demo_code that
    # matches the submitted code, mint the session directly against that
    # tenant -- no OTP row involved at all. A user with no membership in a
    # demo-code tenant can never match here, since we only walk their own
    # memberships.
    # Only a DIRECT membership counts, and never a superadmin's: a superadmin
    # is a member of every tenant (demo included) and partner staff reach
    # tenants through their partner, so walking `memberships()` here let the
    # public demo code open the owner console (audit 2026-09-28, issue 1).
    if u is not None and not u.is_superadmin:
        direct = (db.query(m.Tenant).join(m.Membership, m.Membership.tenant_id == m.Tenant.id)
                  .filter(m.Membership.user_id == u.id).all())
        for t in direct:
            code = (t.settings or {}).get("demo_code")
            if code and hmac.compare_digest(str(code).encode(), body.code.encode()):
                ttl = dt.timedelta(days=settings.remember_days) if body.remember else dt.timedelta(hours=settings.session_hours)
                s = m.Session(id=security.new_token(), user_id=u.id, tenant_id=t.id,
                              device_label=(request.headers.get("user-agent") or "")[:200], expires=_now() + ttl,
                              via_demo=True)
                db.add(s); db.commit()
                response.set_cookie(COOKIE, s.id, max_age=int(ttl.total_seconds()), httponly=True, samesite="lax", secure=settings.base_url.startswith("https"), path="/")
                return _me(db, u, s.tenant_id)

    # Keyed on what was typed, not on `u`: an unknown username must cost the
    # same as a known one, or the limiter becomes an enumeration oracle.
    if not ratelimit.allow(f"auth_verify:user:{body.username.strip().lower()}", *AUTH_VERIFY_PER_USER):
        raise HTTPException(429, "too many requests, try again later")

    otp = db.query(m.Otp).filter(m.Otp.user_id == u.id, m.Otp.used == False).order_by(m.Otp.expires.desc()).first() if u else None
    if not otp or otp.expires < _now():
        raise HTTPException(401, "code expired or not requested")
    # Count the attempt in the database, not on the loaded row. `otp.attempts
    # += 1` was a read-modify-write: N guesses in flight together all read the
    # same count and each wrote count+1, so a burst got N tries for the price
    # of one and the five-attempt budget was only a budget for sequential
    # callers. The guarded UPDATE takes an attempt only while one is left; a
    # rowcount of 0 means the budget (or the code) is already spent.
    took = db.execute(
        update(m.Otp)
        .where(m.Otp.id == otp.id, m.Otp.used == False,  # noqa: E712
               m.Otp.attempts < settings.otp_max_attempts)
        .values(attempts=m.Otp.attempts + 1)
    ).rowcount
    if took != 1:
        db.execute(update(m.Otp).where(m.Otp.id == otp.id).values(used=True))
        db.commit(); raise HTTPException(401, "wrong code")
    if not hmac.compare_digest(otp.code_hash, security.hash_code(body.code)):
        db.execute(update(m.Otp).where(m.Otp.id == otp.id,
                                       m.Otp.attempts >= settings.otp_max_attempts).values(used=True))
        db.commit(); raise HTTPException(401, "wrong code")
    # Spend the code the same way: two requests carrying the right code at
    # once must not both open a session.
    if db.execute(update(m.Otp).where(m.Otp.id == otp.id, m.Otp.used == False)  # noqa: E712
                  .values(used=True)).rowcount != 1:
        db.commit(); raise HTTPException(401, "code expired or not requested")
    db.expire(otp)
    ttl = dt.timedelta(days=settings.remember_days) if body.remember else dt.timedelta(hours=settings.session_hours)
    ms = memberships(db, u)
    # Counted before this session is added: zero prior sessions, ever, is a
    # person using Vault for the first time -- the one sign-in an
    # operator wants to hear about.
    first_ever = db.query(m.Session).filter_by(user_id=u.id).count() == 0
    s = m.Session(id=security.new_token(), user_id=u.id, tenant_id=ms[0][0].id if ms else None,
                  device_label=(request.headers.get("user-agent") or "")[:200], expires=_now() + ttl)
    db.add(s); db.commit()
    if first_ever:
        adminnotify.notify("signin", f"First sign-in: {u.username}", f"{u.email or ''} · {ms[0][1] if ms else 'no membership'}",
                           tenant=ms[0][0].slug if ms else "")
    response.set_cookie(COOKIE, s.id, max_age=int(ttl.total_seconds()), httponly=True, samesite="lax", secure=settings.base_url.startswith("https"), path="/")
    return _me(db, u, s.tenant_id)

@router.post("/auth/logout")
def logout(response: Response, s: m.Session = Depends(current_session), db: DbSession = Depends(get_db)):
    db.delete(s); db.commit()
    response.delete_cookie(COOKIE, path="/", httponly=True, samesite="lax", secure=settings.base_url.startswith("https"))
    return {"ok": True}

@router.get("/me", response_model=sc.MeOut)
def me(s: m.Session = Depends(current_session), u: m.User = Depends(current_user), db: DbSession = Depends(get_db)):
    return _me(db, u, s.tenant_id)

@router.post("/me/tenant", response_model=sc.MeOut)
def switch(body: sc.SwitchTenant, s: m.Session = Depends(current_session), u: m.User = Depends(current_user), db: DbSession = Depends(get_db)):
    if body.tenant_id not in {t.id for t, _ in memberships(db, u)}: raise HTTPException(403, "not a member")
    s.tenant_id = body.tenant_id; db.commit(); return _me(db, u, s.tenant_id)
