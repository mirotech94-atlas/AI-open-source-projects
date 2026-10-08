"""A credential-free local render of the portal and console, for LAYOUT checks.

Unit tests run in jsdom, which does not lay out: it will happily report a
passing test for a box that is 300px of empty space on a real screen. Every
layout bug in the redesign reached the owner as a screenshot because the only
checks were jsdom and a grep of the deployed bundle. This is the missing
step -- the real built app, really laid out, with nothing to log into.

Everything here is local and disposable: a temp SQLite file, a tenant and a
user this script creates itself, and a session row whose id it picks. Nothing
touches prod and no real account is involved -- the "login" is a row this
script inserts into its own database, which is why no OTP is needed and why
none should ever be entered to take a screenshot.

    cd hub && python3.10 scripts/preview.py      # http://127.0.0.1:8791

Then, once in the browser:

    document.cookie = "guardian_session=<the SID it printed>; path=/"
    location.href = "/overview"

The seeded tenant has no fixes, so charts render their empty state. To see a
populated chart, insert rows into `fix` (columns: tenant_id, ts, host, action,
label, arg, by, ok, out, reboot_needed; `by` is "auto" or a username) and
reload -- `payloads/overview.fixed_without_you` buckets them by day.
"""
import datetime as dt
import os
import pathlib
import sys
import tempfile

HUB = pathlib.Path(__file__).resolve().parent.parent
REPO = HUB.parent
TMP = tempfile.mkdtemp(prefix="guardian-preview-")

# Must be set before the first guardian_hub import: config binds once.
os.environ["GUARDIAN_DATABASE_URL"] = f"sqlite:///{TMP}/preview.db"
os.environ["GUARDIAN_SECRET_KEY"] = "local-preview-only"
os.environ["GUARDIAN_WEB_DIST"] = str(REPO / "web" / "dist")
os.environ["GUARDIAN_BASE_URL"] = "http://127.0.0.1:8791"

sys.path.insert(0, str(HUB))

from guardian_hub.db import Base, SessionLocal, engine  # noqa: E402
from guardian_hub import ingest, models as m  # noqa: E402
from guardian_hub.main import create_app  # noqa: E402
from tests import scanfixture  # noqa: E402

SID = "previewsession" + "0" * 18

Base.metadata.create_all(engine)
db = SessionLocal()
tenant = m.Tenant(slug="home", name="Preview Network")
# Superadmin so /owner is reachable too: the console is a different shell
# with its own rescoped tokens, so it needs looking at as much as the portal.
user = m.User(username="preview", email="preview@example.invalid", display_name="Preview",
              is_superadmin=True)
db.add_all([tenant, user])
db.flush()
db.add(m.Membership(user_id=user.id, tenant_id=tenant.id, role="admin"))
db.add(m.Session(id=SID, user_id=user.id, tenant_id=tenant.id, device_label="preview",
                 expires=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=8)))
db.commit()

scanfixture.refresh()
payload = scanfixture.load_last()
ingest.store_scan(db, tenant, None, payload, deliver=False)
db.commit()

# `--demo` also builds the Northwind demo tenant: two networks, ~35 devices,
# 30 days of history. The plain fixture has two devices, which is a fine
# check for "does this render" and a useless one for "does this look right
# when it is busy" -- the topology map's whole design problem is the
# hairball, and two nodes never show it. The preview user is a member of
# both, so the network chooser switches between them.
if "--demo" in sys.argv:
    from guardian_hub import seed  # noqa: PLC0415
    info = seed.seed_demo(db)
    demo = db.query(m.Tenant).filter_by(slug="demo").first()
    if demo is not None:
        db.add(m.Membership(user_id=user.id, tenant_id=demo.id, role="admin"))
        db.commit()
        print(f"DEMO_TENANT={demo.id} ({info.get('devices', '?')} devices)")

db.close()

print(f"SID={SID}")
print(f"DB={TMP}/preview.db")

import uvicorn  # noqa: E402

uvicorn.run(create_app(), host="127.0.0.1", port=8791, log_level="warning")
