"""The site gateway router credential store -- ONE router per tenant.

This mirrors `recorders` deliberately, for the same reason: a home gateway
lives on the customer LAN where only the enrolled sensor can reach it, so the
hub keeps the admin credential and hands it to that one sensor in the clear
over the authenticated sensor channel, and nowhere else.

The invariants, identical to a recorder credential:

1. **A password goes in and never comes out.** `set_password` is the only way
   in; `password()` is read ONLY by `sensor_payload`, the single code path that
   puts the credential on the wire (down to the enrolled sensor). `out()` -- the
   browser view -- carries `has_credentials`, never the credential.
2. **The audit log sees field NAMES, never contents.** Callers log
   `{"credentials_set": bool(...)}`, never the password itself.
3. **Nothing here mutates the router.** The only sensor-side action wired today
   is a login+logout credential Test; the disable-remote-admin flow stays
   unwired until a live Test proves the handshake.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session as DbSession

from . import crypto, models as m

#: Router vendors whose sensor handshake is implemented.
VENDORS = {"fios"}
ALL_VENDORS = {"fios": "Verizon Fios (CR1000A / CR1000B)"}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def get(db: DbSession, tenant_id: str) -> m.Router | None:
    """The one router row for a tenant, or None."""
    return db.query(m.Router).filter_by(tenant_id=tenant_id).one_or_none()


def password(row: m.Router) -> str:
    if not row or not row.secret_enc:
        return ""
    return (crypto.decrypt_json(row.secret_enc or "") or {}).get("password") or ""


def set_password(row: m.Router, plain: str) -> None:
    """Store (or, with an empty string, clear) the admin password."""
    row.secret_enc = crypto.encrypt_json({"password": plain}) if plain else ""


def has_credentials(row: m.Router | None) -> bool:
    return bool(row and row.secret_enc)


def out(row: m.Router | None) -> dict:
    """The router card a browser sees: never the password, only whether one is
    stored and how the last credential Test went."""
    if row is None:
        return {
            "configured": False, "vendor": "fios",
            "vendor_label": ALL_VENDORS["fios"],
            "base_url": "", "username": "", "has_credentials": False,
            "enabled": True, "status": "unconfigured",
            "last_test_ok": None, "last_test_msg": "", "last_test": "",
            "remote_admin_off_at": "", "remote_admin_msg": "",
        }
    return {
        "configured": True,
        "vendor": (row.vendor or "fios").lower(),
        "vendor_label": ALL_VENDORS.get((row.vendor or "fios").lower(), row.vendor or ""),
        "base_url": row.base_url or "",
        "username": row.username or "",
        "has_credentials": has_credentials(row),
        "enabled": bool(row.enabled),
        "status": row.status or "pending",
        "last_test_ok": row.last_test_ok,
        "last_test_msg": row.last_test_msg or "",
        "last_test": row.last_test.isoformat(timespec="seconds") if row.last_test else "",
        # What Vault DID, not what the router is: there is no getter for the
        # live value, so an empty stamp means "Vault has never turned this
        # off", never "remote administration is on".
        "remote_admin_off_at": (row.remote_admin_off_at.isoformat(timespec="seconds")
                                if row.remote_admin_off_at else ""),
        "remote_admin_msg": row.remote_admin_msg or "",
    }


def sensor_payload(db: DbSession, tenant_id: str) -> dict | None:
    """The router handed to an enrolled sensor, credential included. The ONLY
    code path that puts a router password on the wire. Returns None when the
    tenant has no router or it is disabled or has no stored credential -- the
    sensor then has nothing to test and does nothing."""
    row = get(db, tenant_id)
    if row is None or not row.enabled or not has_credentials(row):
        return None
    return {
        "id": row.id,
        "vendor": (row.vendor or "fios").lower(),
        "base_url": row.base_url or "",
        "username": row.username or "",
        "password": password(row),
    }
