from sqlalchemy.orm import Session as DbSession
from . import models as m


def log(db: DbSession, tenant_id: str | None, user: str, role: str, action: str, target: str = "", ip: str = "", body: dict | None = None) -> m.Audit:
    """Add an Audit row. Does NOT commit — caller commits alongside its own writes.

    `tenant_id=None` means a PLATFORM act, not something done to a customer
    (the owner changing a platform setting). The tenant-scoped audit page
    filters on this column, so a platform row must not borrow a customer's id
    or it shows up in that customer's log."""
    row = m.Audit(tenant_id=tenant_id, user=user, role=role, action=action, target=target, ip=ip, body=body or {})
    db.add(row)
    return row
