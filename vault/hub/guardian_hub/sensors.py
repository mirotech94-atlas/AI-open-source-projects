"""Shared sensor-enrolment helper.

Both the superadmin tenants API (`routers/tenants.py`) and the tenant-admin
sensors API (`routers/sensors.py`) create a pending `Sensor` row with a
fresh enrol code the same way. This module is the single implementation;
callers are responsible for committing.
"""

import datetime as dt

from . import models as m, security

ENROL_CODE_TTL_MINUTES = 15
#: An e-mailed code has to survive the gap between "the manager signed up" and
#: "the person with server access opened their inbox" -- seven days covers a
#: weekend and a bank holiday. `security.new_install_code` pays for the longer
#: life in entropy rather than leaving the short code open.
INSTALL_CODE_TTL_DAYS = 7


def new_enrol_code_for(db, tenant: m.Tenant, network_id: str | None = None, *,
                       emailed: bool = False) -> m.Sensor:
    """Create a pending Sensor for `tenant` with a fresh enrol code.

    `network_id` (round 2 of Task 51) is the network the caller already
    validated as belonging to this tenant -- carried on the pending row so
    `routers/sensor.enrol` can bind the sensor onto it once the code is
    redeemed. `None` (the default, and every call site that predates this)
    binds nothing, same as before.

    `emailed=True` mints the long-lived, high-entropy variant instead, for a
    code that will be read out of an inbox rather than off a screen. The two
    are different credentials on purpose -- see `security.new_install_code` for
    why the window and the length move together.

    The code expires `ENROL_CODE_TTL_MINUTES` (or `INSTALL_CODE_TTL_DAYS`) from
    now. Caller commits.
    """
    if emailed:
        code = security.new_install_code()
        ttl = dt.timedelta(days=INSTALL_CODE_TTL_DAYS)
    else:
        code = security.new_enrol_code()
        ttl = dt.timedelta(minutes=ENROL_CODE_TTL_MINUTES)
    s = m.Sensor(
        tenant_id=tenant.id,
        enrol_code=code,
        enrol_expires=dt.datetime.now(dt.timezone.utc) + ttl,
        network_id=network_id,
    )
    db.add(s)
    return s
