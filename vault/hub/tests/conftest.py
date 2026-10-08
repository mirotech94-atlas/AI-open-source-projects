import os
import tempfile

# GUARDIAN_* env vars must be set BEFORE any guardian_hub import: config.settings
# and db.engine bind to them once, at first import, and are never reloaded.
_tmp_dir = tempfile.mkdtemp(prefix="guardian-test-")
os.environ["GUARDIAN_DATABASE_URL"] = f"sqlite:///{os.path.join(_tmp_dir, 't.db')}"
os.environ["GUARDIAN_DEV_CONSOLE_OTP"] = "1"
os.environ["GUARDIAN_SECRET_KEY"] = "test-secret"

import pytest
from fastapi.testclient import TestClient

from guardian_hub.db import Base, engine
from tests import scanfixture
from guardian_hub import models  # noqa: F401  (registers tables on Base.metadata)
from guardian_hub import demo_trickle, ratelimit
from guardian_hub.routers import public_feed
from guardian_hub.main import create_app

# No importlib.reload anywhere: this module imports guardian_hub.db/models exactly
# once, so test modules may safely do `from guardian_hub.db import SessionLocal` or
# `from guardian_hub import models as m` at module scope and get the same
# engine/Base/mapped classes used here.


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    # ratelimit's buckets are process-global (module-level state), so without
    # this a test earlier in the run that calls e.g. POST /auth/start several
    # times would leave hits on the clock for a later, unrelated test using
    # the same username/IP.
    ratelimit.reset()
    # Same reason, for the public feed's process-global payload cache: without
    # this a later test would be served the empty payload an earlier test's
    # unseeded hub cached, and it would read as an endpoint bug.
    public_feed.reset_cache()
    # Same reason, for `demo_trickle`'s process-global bookkeeping of which
    # rows it has inserted per tenant.
    demo_trickle.reset()
    # Same reason once more, for the rebased scan fixture: its delta is fixed
    # when `tests.scanfixture` is imported, so in a run long enough to matter
    # the "scan that just happened" it seeds is quietly an hour old, and every
    # assertion resting on a live device fails for tests late in the run. See
    # `scanfixture.refresh`.
    scanfixture.refresh()
    with TestClient(create_app()) as c:
        yield c
