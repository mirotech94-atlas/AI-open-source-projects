"""`fixed_without_you` and the console's self-healing chart must split the
same `Fix` rows the same way -- see the bug that motivated this file."""
import datetime as dt

from guardian_hub.db import SessionLocal
from guardian_hub import models as m
from guardian_hub.payloads import overview as overview_payload
from tests.test_auth import seed_user


def _fix(db, tenant_id, by, days_ago=0):
    db.add(m.Fix(tenant_id=tenant_id, host="vps1", action="restart-service", label="x",
                 arg="", by=by, ok=True, out="", reboot_needed=False,
                 ts=(dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)).isoformat(timespec="seconds")))


def test_an_unknown_stamp_is_guardians_own_work_not_an_approval(client):
    """A `Fix` the sensor stamped itself is not something the owner approved.

    The old rule was `by != "auto"`, which reported 61 rows stamped "test" on
    the live hub as 61 things the owner had personally said yes to.
    """
    _user_id, tenant_id = seed_user()
    db = SessionLocal()
    for by in ("auto", "test", "sensor-7"):
        _fix(db, tenant_id, by)
    for by in ("hub:miro", "hub:chat:miro", "accepted-recommendation"):
        _fix(db, tenant_id, by)
    db.commit()

    out = overview_payload.fixed_without_you(db, tenant_id)
    assert out.automatic == 3, "auto, test and sensor-7 are all Guardian acting alone"
    assert out.approved == 3, "hub:/chat:/dashboard/accepted-recommendation are a person's yes"
    assert sum(d.automatic for d in out.daily) == 3
    assert sum(d.approved for d in out.daily) == 3
