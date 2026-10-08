# First-Run Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A customer who signs up can get every network they own watched, without a support call — progress derived from real rows, one enrol code and one email per network, and nothing about the first scan that they are forced to do.

**Architecture:** The hub stops storing a wizard cursor and starts answering "what does this tenant still need?" from rows that are already the truth (`Network`, `Sensor`, `Scan`, `DnsWizard`). Enrol codes are minted per network and delivered by email, because whoever can install a sensor is rarely the person at the browser. Nothing automatic writes `label` or `trusted`.

**Tech Stack:** FastAPI + SQLAlchemy (hub), React + Vite + TypeScript (web), pytest, vitest.

**Spec:** `docs/superpowers/specs/2026-09-20-first-run-onboarding-design.md`

## Status: implemented 2026-09-20

All ten tasks done, each committed separately on `codex/guardian-improvements`
and merged to `main`. Hub suite 1466 passed / 8 failed — the 8 being the
pre-existing `test_router_card` set that predates this work. Web 653 passed,
0 lint errors, build clean.

**Where the implementation departed from this plan, and why.** Recorded because
a plan read later is worse than useless if it describes something that was not
built:

- **Task 3's query was wrong here.** This plan assumed `Scan.network_id`. That
  column does not exist — `Scan` carries `sensor_id`, and the link to a network
  runs through `Sensor.network_id`. The implemented version joins that way and
  compares timestamps as parsed instants, because `Scan.ts` carries whatever
  UTC offset the writing sensor used and a SQL `MIN()` over it is lexicographic.
- **Task 6's test signature was wrong here.** `DeviceGrid` takes a
  `DevicesPayload`, not a device array. Worth noting that the first version of
  that test passed *before* the fix — the page renders vendor-breakdown bars
  containing the same word, so the query matched those instead of the card. The
  shipped test scopes to `device-card`.
- **Task 7 derives only the install half.** `name`/`networks`/`sensor`/`waiting`
  come from the hub, because that is the half that can disagree with the rows.
  The post-scan tail stays local: once a scan has landed those steps are
  linear and carry no tenant state to be wrong about.
- **Task 9's DNS step is an offer, not a step.** The plan implied embedding
  `DnsWizard` mid-flow. What shipped is a link beside Done, shown only while
  `dns` is not working. The persistent dashboard prompt in spec §4 was built
  afterwards, in `web/src/shell/DnsPrompt.tsx`: a line rather than a modal,
  with no dismiss (a dismiss turns "keeps asking" into "asked once"), silent
  while the wizard is still asking for the same router password, and retiring
  itself on the hub's live `dns: "working"` signal.
- **Task 5 gained a failure path not in the plan.** The invite endpoint 502s
  when the relay refuses the letter. The wizard renders "we have e-mailed
  whoever looks after your computers" from a 200, and saying that when nothing
  left the building is the one failure this flow cannot survive.

## Global Constraints

- **No Alembic migration.** Every piece of state is derivable from existing rows. If a task seems to need a column, stop and re-read the spec — another session holds `0025`, and adding `0026` here means this design went wrong.
- **The warrant rule.** `kind` and `name` are machine inference and may be written automatically. `label` and `trusted` are human warrants that suppress detections (`alerts.py:289`, `alerts.py:299`, `lan.py:207`, `home.py:281`) and no automatic path may write them. A name the *client types* is written to `label` — that is a person, and permitted.
- **Enrol codes are bearer credentials.** They go only to the account's own e-mail address, never to an address supplied in a request body. They must not appear in any payload; `hub/tests/test_account_payload.py` holds the existing field-name guard.
- **Never assert wall-clock.** Guards about work that scales with rows count statements via `tests/querycount.py`.
- **Each Python package runs from its own directory** with its own venv: `cd hub && .venv/bin/pytest`, `cd web && npm test`.
- **A `schemas_dash.py` change and its `web/src/types.ts` mirror are ONE task.**
  `hub/tests/test_contracts.py` pins every model there to a field-for-field
  mirror, so splitting them leaves the suite red across every commit in
  between — and a suite that is expected to be red is a suite nobody reads.
  This plan originally put the mirror in Task 7, five tasks after the schema
  change in Task 2, and the contract was broken for three commits before a
  peer session pointed it out. Adding a required field also breaks every TS
  fixture that builds that payload inline; there were eight.
- **Commit subjects** are a sentence describing the user-visible behaviour change, present tense, sentence case.

---

### Task 1: A longer-lived code for e-mail delivery

The existing `ENROL_CODE_TTL_MINUTES = 15` is correct for a code read off the screen and useless for one read out of an inbox two hours later. But `routers/sensor.enrol` looks up `filter_by(enrol_code=...)` with **no tenant filter**, so any guess matches any live code anywhere: lengthening the window shrinks the effective search space by however many codes are outstanding across all customers at once.

An emailed code is never typed — it is embedded in the one-line install command — so its length costs the human nothing. Pay for the longer life with entropy.

**Files:**
- Modify: `hub/guardian_hub/security.py:10`
- Modify: `hub/guardian_hub/sensors.py:13-35`
- Test: `hub/tests/test_enrol_code.py` (create)

**Interfaces:**
- Consumes: `security.new_enrol_code()`, `sensors.new_enrol_code_for(db, tenant, network_id)`
- Produces: `security.new_install_code() -> str` (16 chars); `sensors.new_enrol_code_for(db, tenant, network_id=None, *, emailed: bool = False) -> m.Sensor`

- [x] **Step 1: Write the failing test**

```python
# hub/tests/test_enrol_code.py
"""Two enrolment credentials with two threat models.

A code shown on screen is read by someone standing at it and lives fifteen
minutes. A code e-mailed to whoever can actually reach the server is read
hours later, and the whole point of the e-mail is that delay.

Lengthening the window is not free. `routers/sensor.enrol` matches on the code
alone -- there is no tenant in the lookup -- so every unredeemed code across
every customer is simultaneously guessable, and a longer life means more of
them live at once. The emailed code therefore gets far more entropy, which
costs nothing because nobody types it: it is embedded in the install command.
"""
import datetime as dt

from guardian_hub import models as m, security, sensors
from guardian_hub.db import SessionLocal
from tests.test_auth import seed_user


def test_the_emailed_code_is_much_harder_to_guess_than_the_screen_one():
    assert len(security.new_enrol_code()) == 8
    assert len(security.new_install_code()) == 16
    # Same unambiguous alphabet: no O/0 or I/1 to mistype when read aloud.
    assert set(security.new_install_code()) <= set("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")


def test_two_install_codes_are_never_the_same():
    assert len({security.new_install_code() for _ in range(200)}) == 200


def test_an_emailed_code_outlives_the_inbox_it_is_sent_to(client):
    _uid, tid = seed_user()
    db = SessionLocal()
    tenant = db.get(m.Tenant, tid)
    now = dt.datetime.now(dt.timezone.utc)

    screen = sensors.new_enrol_code_for(db, tenant)
    emailed = sensors.new_enrol_code_for(db, tenant, emailed=True)
    db.commit()

    assert (screen.enrol_expires - now) < dt.timedelta(hours=1)
    assert (emailed.enrol_expires - now) > dt.timedelta(days=6)
    assert len(emailed.enrol_code) == 16
    db.close()
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd hub && .venv/bin/pytest tests/test_enrol_code.py -q`
Expected: FAIL — `module 'guardian_hub.security' has no attribute 'new_install_code'`

- [x] **Step 3: Write minimal implementation**

```python
# hub/guardian_hub/security.py — beside new_enrol_code
#: The alphabet both codes use: no O/0 and no I/1, so a code read down a phone
#: line cannot be mistyped into a different valid-looking one.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

def new_enrol_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))


def new_install_code() -> str:
    """The code that goes in an e-mail, and lives days rather than minutes.

    Sixteen characters rather than eight, and the reason is the lookup in
    `routers/sensor.enrol`: it matches on the code ALONE, with no tenant, so
    every unredeemed code on the platform is guessable at once. A fifteen-minute
    window keeps that set nearly empty; a seven-day window does not. 32**16 is
    about 1.2e24, which absorbs the difference with room to spare.

    Nobody types this. It is embedded in the install command the e-mail
    carries, so the extra length costs the reader nothing.
    """
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(16))
```

```python
# hub/guardian_hub/sensors.py — replace the constant and the helper
ENROL_CODE_TTL_MINUTES = 15
#: An e-mailed code has to survive the gap between "the manager signed up" and
#: "the person with server access read their inbox". Seven days covers a
#: weekend and a holiday; `new_install_code` pays for it in entropy.
INSTALL_CODE_TTL_DAYS = 7


def new_enrol_code_for(db, tenant: m.Tenant, network_id: str | None = None, *,
                       emailed: bool = False) -> m.Sensor:
    """Create a pending Sensor for `tenant` with a fresh enrol code.

    `network_id` (round 2 of Task 51) is the network the caller already
    validated as belonging to this tenant -- carried on the pending row so
    `routers/sensor.enrol` can bind the sensor onto it once the code is
    redeemed. `None` (the default, and every call site that predates this)
    binds nothing, same as before.

    `emailed=True` mints the long-lived, high-entropy variant -- see
    `security.new_install_code` for why the two differ. Caller commits.
    """
    if emailed:
        code, expires = security.new_install_code(), dt.timedelta(days=INSTALL_CODE_TTL_DAYS)
    else:
        code, expires = security.new_enrol_code(), dt.timedelta(minutes=ENROL_CODE_TTL_MINUTES)
    s = m.Sensor(
        tenant_id=tenant.id,
        enrol_code=code,
        enrol_expires=dt.datetime.now(dt.timezone.utc) + expires,
        network_id=network_id,
    )
    db.add(s)
    return s
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd hub && .venv/bin/pytest tests/test_enrol_code.py -q`
Expected: PASS (3 tests)

- [x] **Step 5: Check the column is wide enough**

`Sensor.enrol_code` is `String(16)` (`models.py:64`). A 16-character code fits exactly. Confirm with `grep -n "enrol_code" hub/guardian_hub/models.py`; if it reads `String(16)` no change is needed. **Do not widen it** — that would be a migration, which this plan forbids.

- [x] **Step 6: Commit**

```bash
git add hub/guardian_hub/security.py hub/guardian_hub/sensors.py hub/tests/test_enrol_code.py
git commit -m "An enrolment code sent by e-mail outlives the inbox it lands in"
```

---

### Task 2: The hub answers what the tenant still needs

**Files:**
- Modify: `hub/guardian_hub/schemas_dash.py` (beside `OnboardingState`, ~line 2364)
- Modify: `hub/guardian_hub/onboarding.py`
- Test: `hub/tests/test_onboarding_progress.py` (create)

**Interfaces:**
- Consumes: `onboarding.state(db, user, tenant)`, `models.Network`, `models.Sensor`, `networks.SENSOR_STALE_S`
- Produces: `schemas_dash.OnboardingNetwork`, `schemas_dash.OnboardingNext`; `OnboardingState.networks: list[OnboardingNetwork]`, `OnboardingState.site_named: bool`, `OnboardingState.next: OnboardingNext | None`; `onboarding.progress(db, tenant) -> list[OnboardingNetwork]`

- [x] **Step 1: Write the failing test**

```python
# hub/tests/test_onboarding_progress.py
"""What the tenant still needs, derived rather than remembered.

The wizard used to walk a fixed array and store its position. That cannot
describe three LANs behind one WAN, which is the shape a real practice has:
two sensors enrolled, one not, one reporting and one silent. Worse, a stored
cursor can DISAGREE with the rows -- saying "install a sensor" while two are
already pushing scans -- and every disagreement is a support call.

So these tests are about the reading being a function of the data. There is no
state to set up wrongly, which is the property worth having.
"""
import datetime as dt

from guardian_hub import models as m, onboarding
from guardian_hub.db import SessionLocal
from tests.test_auth import seed_user


def _tenant():
    uid, tid = seed_user()
    return uid, tid


def _network(db, tid: str, slug: str, primary: bool = False) -> m.Network:
    n = m.Network(tenant_id=tid, name=slug.title(), slug=slug, is_primary=primary)
    db.add(n); db.flush()
    return n


def test_a_tenant_with_no_networks_is_asked_for_one(client):
    uid, tid = _tenant()
    db = SessionLocal()
    user, tenant = db.get(m.User, uid), db.get(m.Tenant, tid)
    st = onboarding.state(db, user, tenant)
    assert st.networks == []
    assert st.next is not None and st.next.action == "declare_network"
    db.close()


def test_a_declared_network_with_no_sensor_asks_for_one(client):
    uid, tid = _tenant()
    db = SessionLocal()
    net = _network(db, tid, "clinic", primary=True); db.commit()
    st = onboarding.state(db, db.get(m.User, uid), db.get(m.Tenant, tid))
    assert [n.sensor for n in st.networks] == ["none"]
    assert st.next.action == "enrol_sensor" and st.next.network_id == net.id
    db.close()


def test_a_minted_code_reads_as_pending_not_installed(client):
    uid, tid = _tenant()
    db = SessionLocal()
    net = _network(db, tid, "clinic", primary=True)
    db.add(m.Sensor(tenant_id=tid, network_id=net.id, enrol_code="ABCD2345", status="pending"))
    db.commit()
    st = onboarding.state(db, db.get(m.User, uid), db.get(m.Tenant, tid))
    assert [n.sensor for n in st.networks] == ["pending"]
    db.close()


def test_a_sensor_that_enrolled_but_never_spoke_is_not_the_same_as_one_missing(client):
    """"Connected but silent" and "never installed" need different advice, so
    they must not collapse into one state."""
    uid, tid = _tenant()
    db = SessionLocal()
    net = _network(db, tid, "clinic", primary=True)
    db.add(m.Sensor(tenant_id=tid, network_id=net.id, status="active",
                    last_seen=dt.datetime.now(dt.timezone.utc)))
    db.commit()
    st = onboarding.state(db, db.get(m.User, uid), db.get(m.Tenant, tid))
    assert st.networks[0].sensor == "active"
    assert st.networks[0].first_scan_at == ""
    assert st.next.action == "await_first_scan"
    db.close()


def test_a_sensor_that_worked_and_stopped_reads_as_quiet(client):
    uid, tid = _tenant()
    db = SessionLocal()
    net = _network(db, tid, "clinic", primary=True)
    stale = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)
    db.add(m.Sensor(tenant_id=tid, network_id=net.id, status="active", last_seen=stale))
    db.commit()
    st = onboarding.state(db, db.get(m.User, uid), db.get(m.Tenant, tid))
    assert st.networks[0].sensor == "quiet"
    db.close()


def test_the_second_network_is_what_is_next_once_the_first_is_covered(client):
    """The property a cursor cannot have: adding a network a week later
    re-opens setup at THAT network, because "needs a sensor" is a fact about
    the network and not a position someone forgot to rewind."""
    uid, tid = _tenant()
    db = SessionLocal()
    a = _network(db, tid, "clinic", primary=True)
    b = _network(db, tid, "lab")
    db.add(m.Sensor(tenant_id=tid, network_id=a.id, status="active",
                    last_seen=dt.datetime.now(dt.timezone.utc)))
    db.commit()
    st = onboarding.state(db, db.get(m.User, uid), db.get(m.Tenant, tid))
    assert st.next.action == "enrol_sensor" and st.next.network_id == b.id
    db.close()


def test_a_redundant_second_sensor_does_not_make_a_network_uncovered(client):
    """`Sensor.network_id` is not unique and a spare sensor is legitimate. A
    network counts as covered when ANY bound sensor is active."""
    uid, tid = _tenant()
    db = SessionLocal()
    net = _network(db, tid, "clinic", primary=True)
    db.add(m.Sensor(tenant_id=tid, network_id=net.id, status="active",
                    last_seen=dt.datetime.now(dt.timezone.utc)))
    db.add(m.Sensor(tenant_id=tid, network_id=net.id, enrol_code="WXYZ6789", status="pending"))
    db.commit()
    st = onboarding.state(db, db.get(m.User, uid), db.get(m.Tenant, tid))
    assert st.networks[0].sensor == "active"
    db.close()


def test_completion_is_still_per_user_and_still_stored(client):
    """Derived state replaces the TENANT cursor only. Whether THIS person has
    been shown around is a fact about them that nothing can derive."""
    uid, tid = _tenant()
    db = SessionLocal()
    user, tenant = db.get(m.User, uid), db.get(m.Tenant, tid)
    onboarding.save(db, user, tenant, completed=True); db.commit()
    assert onboarding.state(db, user, tenant).show is False
    db.close()
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd hub && .venv/bin/pytest tests/test_onboarding_progress.py -q`
Expected: FAIL — `OnboardingState` has no attribute `networks`

- [x] **Step 3: Add the schemas**

```python
# hub/guardian_hub/schemas_dash.py — immediately BEFORE class OnboardingState
class OnboardingNetwork(_Model):
    """One declared network and how far along it is.

    `sensor` is four states and not a boolean, because the advice differs for
    each: `none` means install one, `pending` means a code was minted and never
    redeemed, `active` means it is talking, and `quiet` means it talked and
    stopped. Collapsing `active` and `quiet` would tell someone to reinstall a
    sensor that is sitting there correctly installed and unplugged.
    """
    id: str = ""
    name: str = ""
    slug: str = ""
    sensor: str = "none"            # none | pending | active | quiet
    first_scan_at: str = ""
    devices: int = 0
    #: Group name -> count, from `payloads.assets.KIND_GROUPS`. Empty until the
    #: first scan lands.
    by_group: dict[str, int] = Field(default_factory=dict)
    dns: str = "off"                # off | working


class OnboardingNext(_Model):
    """The single next thing to do, or absent when there is nothing.

    One action rather than a list: the wizard shows one screen at a time, and
    letting the hub pick keeps "what comes next" from being re-derived in the
    browser where it can disagree.
    """
    network_id: str = ""
    action: str = ""   # declare_network | enrol_sensor | await_first_scan | offer_dns
```

Then add to `OnboardingState`:

```python
    site_named: bool = False
    networks: list[OnboardingNetwork] = Field(default_factory=list)
    next: OnboardingNext | None = None
```

- [x] **Step 4: Derive the progress**

```python
# hub/guardian_hub/onboarding.py — new function, and state() calls it
import datetime as dt

from .networks import SENSOR_STALE_S
from .schemas_dash import OnboardingNetwork, OnboardingNext, OnboardingState


def _sensor_state(sensors: list["m.Sensor"]) -> str:
    """The best state any sensor bound to one network is in.

    Best, not first: a network with a working sensor and a spare pending code
    is covered. `Sensor.network_id` is not unique and a redundant sensor is a
    legitimate thing to own.
    """
    if not sensors:
        return "none"
    now = dt.datetime.now(dt.timezone.utc)
    best = "none"
    for s in sensors:
        if s.status == "active":
            fresh = s.last_seen is not None and (now - s.last_seen).total_seconds() <= SENSOR_STALE_S
            state_ = "active" if fresh else "quiet"
        elif s.status == "pending":
            state_ = "pending"
        else:
            continue                                  # revoked: as good as absent
        order = ("none", "pending", "quiet", "active")
        if order.index(state_) > order.index(best):
            best = state_
    return best


def progress(db: DbSession, tenant: "m.Tenant") -> list[OnboardingNetwork]:
    """Every declared network and how far along it is.

    Grouped queries, never one per network: a tenant with a dozen networks must
    not cost a dozen round trips, and `tests/test_onboarding_progress.py` counts
    the statements to keep it that way.
    """
    nets = db.query(m.Network).filter_by(tenant_id=tenant.id).order_by(m.Network.name).all()
    if not nets:
        return []
    ids = [n.id for n in nets]

    by_net: dict[str, list] = {i: [] for i in ids}
    for s in db.query(m.Sensor).filter(m.Sensor.tenant_id == tenant.id,
                                       m.Sensor.network_id.in_(ids)).all():
        by_net.setdefault(s.network_id, []).append(s)

    counts = dict(
        db.query(m.Device.network_id, func.count(m.Device.id))
        .filter(m.Device.tenant_id == tenant.id, m.Device.network_id.in_(ids))
        .group_by(m.Device.network_id).all()
    )

    out = []
    for n in nets:
        out.append(OnboardingNetwork(
            id=n.id, name=n.name, slug=n.slug,
            sensor=_sensor_state(by_net.get(n.id) or []),
            devices=int(counts.get(n.id, 0)),
        ))
    return out


def _next(site_named: bool, nets: list[OnboardingNetwork]) -> OnboardingNext | None:
    """The one thing to do now. Order is the order a network becomes useful in:
    it must exist, then be watched, then have reported."""
    if not nets:
        return OnboardingNext(action="declare_network")
    for n in nets:
        if n.sensor in ("none", "pending"):
            return OnboardingNext(network_id=n.id, action="enrol_sensor")
    for n in nets:
        if not n.first_scan_at:
            return OnboardingNext(network_id=n.id, action="await_first_scan")
    for n in nets:
        if n.dns != "working":
            return OnboardingNext(network_id=n.id, action="offer_dns")
    return None
```

Wire both into `state()`, keeping every existing field exactly as it is:

```python
    nets = progress(db, tenant)
    return OnboardingState(
        path=PATH_TOUR if ready else PATH_SETUP,
        step=(row.step if row else "") or "",
        completed=completed, skipped=skipped,
        show=not (completed or skipped),
        tenant_ready=ready,
        site_named=bool((tenant.name or "").strip()),
        networks=nets,
        next=_next(bool((tenant.name or "").strip()), nets),
    )
```

Add `from sqlalchemy import func` to the imports.

- [x] **Step 5: Run test to verify it passes**

Run: `cd hub && .venv/bin/pytest tests/test_onboarding_progress.py -q`
Expected: PASS (8 tests). `first_scan_at` stays `""` for now — Task 3 fills it.

- [x] **Step 6: Commit**

```bash
git add hub/guardian_hub/onboarding.py hub/guardian_hub/schemas_dash.py hub/tests/test_onboarding_progress.py
git commit -m "The wizard knows where a customer is by looking, not by remembering"
```

---

### Task 3: The first scan, and the cost of asking

**Files:**
- Modify: `hub/guardian_hub/onboarding.py` (`progress`)
- Test: `hub/tests/test_onboarding_progress.py` (append)

**Interfaces:**
- Consumes: `models.Scan`, `payloads.assets.kind_of`, `payloads.assets.group_of`, `tests/querycount.queries_for`
- Produces: `OnboardingNetwork.first_scan_at`, `OnboardingNetwork.by_group` populated

- [x] **Step 1: Write the failing test**

```python
# hub/tests/test_onboarding_progress.py — append
from tests.querycount import queries_for
from tests.test_auth import login


def test_the_group_summary_is_what_the_client_is_shown_after_the_scan(client):
    """"12 cameras, 8 phones" is the whole first view, so it is computed here
    rather than by the browser counting a device list it would have to fetch."""
    uid, tid = _tenant()
    db = SessionLocal()
    net = _network(db, tid, "clinic", primary=True)
    for i in range(3):
        db.add(m.Device(tenant_id=tid, network_id=net.id, mac=f"aa:bb:cc:00:00:0{i}",
                        vendor="Reolink", kind="camera", first_seen="2026-09-01T00:00:00Z"))
    db.add(m.Device(tenant_id=tid, network_id=net.id, mac="aa:bb:cc:00:00:09",
                    vendor="Brother", kind="printer", first_seen="2026-09-01T00:00:00Z"))
    db.commit()
    st = onboarding.state(db, db.get(m.User, uid), db.get(m.Tenant, tid))
    assert st.networks[0].devices == 4
    assert st.networks[0].by_group.get("Cameras") == 3
    db.close()


def test_reading_progress_does_not_cost_a_query_per_network(client):
    """The ledger's rule, applied here: a tenant with a dozen networks must not
    cost a dozen round trips. Counted at two sizes and compared, because a
    ceiling loose enough not to be brittle sits above a real N+1 on a small
    fixture -- which is how the wall-clock versions of these guards failed."""
    uid, tid = _tenant()
    login(client)
    db = SessionLocal()
    _network(db, tid, "clinic", primary=True)
    _network(db, tid, "lab")
    db.commit(); db.close()
    few = queries_for(client, "/api/v1/onboarding")

    db = SessionLocal()
    for i in range(10):
        _network(db, tid, f"site{i}")
    db.commit(); db.close()
    many = queries_for(client, "/api/v1/onboarding")

    assert few == many, f"{few} statements for 2 networks but {many} for 12"
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd hub && .venv/bin/pytest tests/test_onboarding_progress.py -q -k "group_summary or query_per_network"`
Expected: FAIL — `by_group` empty

- [x] **Step 3: Fill both fields in `progress()`**

Add before the `for n in nets` loop, keeping everything grouped:

```python
    # Earliest scan per network. `Scan.ts` is a string carrying the sensor's own
    # UTC offset, so MIN() over it is lexicographic and can pick the wrong row
    # when two sensors write different offsets -- see `demo_trickle._tenant_offset`
    # for the same trap. Parsed and compared in Python instead.
    first: dict[str, str] = {}
    for net_id, ts in db.query(m.Scan.network_id, m.Scan.ts).filter(
            m.Scan.tenant_id == tenant.id, m.Scan.network_id.in_(ids)).all():
        when = parse_ts(ts)
        if when is None:
            continue
        prev = first.get(net_id)
        if prev is None or when < parse_ts(prev):
            first[net_id] = ts

    # Group counts, from the SAME classifier the Assets page uses. A summary
    # that grouped devices differently from the screen it links to would be
    # discovered by the customer.
    groups: dict[str, dict[str, int]] = {i: {} for i in ids}
    for d in db.query(m.Device).filter(m.Device.tenant_id == tenant.id,
                                       m.Device.network_id.in_(ids)).all():
        g = groups.setdefault(d.network_id, {})
        name = group_of(kind_of(d))
        g[name] = g.get(name, 0) + 1
```

and set `first_scan_at=first.get(n.id, "")`, `by_group=groups.get(n.id) or {}` in the `OnboardingNetwork(...)` call. Import `from ..scans import parse_ts` is wrong here — `onboarding.py` is top-level, so use `from .scans import parse_ts` and `from .payloads.assets import group_of, kind_of`.

- [x] **Step 4: Run test to verify it passes**

Run: `cd hub && .venv/bin/pytest tests/test_onboarding_progress.py -q`
Expected: PASS (10 tests)

- [x] **Step 5: Commit**

```bash
git add hub/guardian_hub/onboarding.py hub/tests/test_onboarding_progress.py
git commit -m "The wizard reports what the first scan found, grouped the way Assets groups it"
```

---

### Task 4: The sensor-install e-mail

**Files:**
- Modify: `hub/guardian_hub/mailer.py` (beside `send_account_ready`, ~line 217)
- Test: `hub/tests/test_sensor_install_mail.py` (create)

**Interfaces:**
- Consumes: `mailer.send(to, subject, text, html=...)`, `mailer.SENT`, `installer` for the command
- Produces: `mailer.send_sensor_install(to, *, tenant_name, network_name, code, expires_on, install_cmd) -> None`

- [x] **Step 1: Write the failing test**

```python
# hub/tests/test_sensor_install_mail.py
"""The e-mail that carries an enrolment code.

It exists because the person who can put a sensor on the LAN is usually not
the person who signed up -- it is the IT contractor, or the owner's son, or
nobody yet. A code on a screen only helps if the right person is standing at
it; an e-mail is forwardable, and forwarding IS the delivery mechanism.

Which makes what the e-mail contains a security question. The code is a bearer
credential: whoever holds it can attach a sensor to this tenant.
"""
from guardian_hub import mailer


def _sent(**over):
    mailer.SENT.clear()
    kwargs = dict(to="owner@example.com", tenant_name="Northwind Dental",
                  network_name="Clinic Wi-Fi", code="ABCD2345EFGH6789",
                  expires_on="27 September 2026",
                  install_cmd="curl -fsSL https://my.guarder.io/install | sh -s ABCD2345EFGH6789")
    kwargs.update(over)
    mailer.send_sensor_install(**kwargs)
    return mailer.SENT[-1]


def test_the_email_names_which_network_it_is_for():
    """A customer with three LANs gets three of these. One that did not say
    which network it was for would be worse than useless -- the sensor would
    bind to the wrong one and report the wrong estate."""
    to, subject, text = _sent()
    assert "Clinic Wi-Fi" in subject or "Clinic Wi-Fi" in text
    assert "Northwind Dental" in text


def test_the_email_carries_the_command_and_the_expiry():
    _to, _subject, text = _sent()
    assert "curl -fsSL https://my.guarder.io/install" in text
    assert "27 September 2026" in text


def test_the_code_appears_exactly_where_it_is_needed_and_nowhere_else():
    """The code belongs in the body, which the recipient acts on. It must not
    be in the SUBJECT, which is the part that shows in notification popups,
    lock screens and mail-server logs."""
    _to, subject, text = _sent()
    assert "ABCD2345EFGH6789" not in subject
    assert "ABCD2345EFGH6789" in text
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd hub && .venv/bin/pytest tests/test_sensor_install_mail.py -q`
Expected: FAIL — `module 'guardian_hub.mailer' has no attribute 'send_sensor_install'`

- [x] **Step 3: Write the implementation**

Follow `send_account_ready`'s existing shape exactly (same text/html pair, same `send()` call). The docstring must record why the subject omits the code:

```python
def send_sensor_install(to: str, *, tenant_name: str, network_name: str,
                        code: str, expires_on: str, install_cmd: str) -> None:
    """How to put a sensor on one network, to whoever can actually do it.

    One of these per declared network, each naming its own -- a customer with a
    clinic Wi-Fi and a lab VLAN gets two, and an e-mail that did not say which
    was which would bind the sensor to the wrong network and report the wrong
    estate.

    The code is NOT in the subject line. A subject is the part that appears on
    a lock screen, in a notification popup and in every mail server's logs
    along the way; a bearer credential belongs in the body, which only the
    recipient opens.
    """
    subject = f"Set up Guardian on {network_name}"
    text = (...)
    send(to, subject, text, html=...)
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd hub && .venv/bin/pytest tests/test_sensor_install_mail.py -q`
Expected: PASS (3 tests)

- [x] **Step 5: Commit**

```bash
git add hub/guardian_hub/mailer.py hub/tests/test_sensor_install_mail.py
git commit -m "Guardian e-mails setup instructions to whoever can reach the server"
```

---

### Task 5: Declaring a network mints a code and sends the e-mail

**Files:**
- Modify: `hub/guardian_hub/routers/write.py` (the networks write path)
- Test: `hub/tests/test_onboarding_enrol_mail.py` (create)
- Modify: `hub/tests/test_account_payload.py` (extend the existing guard)

**Interfaces:**
- Consumes: `sensors.new_enrol_code_for(db, tenant, network_id, emailed=True)`, `mailer.send_sensor_install`, `installer` command builder
- Produces: `POST /api/v1/onboarding/networks/{network_id}/invite` returning `OnboardingState`

- [x] **Step 1: Write the failing test**

```python
# hub/tests/test_onboarding_enrol_mail.py
"""Declaring a network gets somebody the means to watch it.

The security constraint is the whole test file. The enrol code is a bearer
credential -- whoever holds it can attach a sensor to this tenant -- so the
address it goes to is not a parameter. A wizard field that mails a credential
to an address supplied in the request body is a credential-exfiltration feature
with a friendly label on it.
"""
from guardian_hub import mailer, models as m
from guardian_hub.db import SessionLocal
from tests.test_auth import login, seed_user


def test_the_invite_goes_to_the_account_address_and_cannot_be_redirected(client):
    uid, tid = seed_user(email="owner@example.com", role="admin")
    login(client)
    db = SessionLocal()
    net = m.Network(tenant_id=tid, name="Clinic", slug="clinic", is_primary=True)
    db.add(net); db.commit(); nid = net.id; db.close()

    mailer.SENT.clear()
    r = client.post(f"/api/v1/onboarding/networks/{nid}/invite",
                    json={"email": "attacker@example.invalid"})
    assert r.status_code == 200, r.text
    assert len(mailer.SENT) == 1
    to, _subject, _text = mailer.SENT[0]
    assert to == "owner@example.com"
    assert "attacker@example.invalid" not in to


def test_the_code_is_bound_to_the_network_it_was_minted_for(client):
    uid, tid = seed_user(email="owner@example.com", role="admin")
    login(client)
    db = SessionLocal()
    net = m.Network(tenant_id=tid, name="Lab", slug="lab", is_primary=True)
    db.add(net); db.commit(); nid = net.id; db.close()

    client.post(f"/api/v1/onboarding/networks/{nid}/invite", json={})
    db = SessionLocal()
    s = db.query(m.Sensor).filter_by(tenant_id=tid).one()
    assert s.network_id == nid and s.status == "pending" and len(s.enrol_code) == 16
    db.close()


def test_a_network_from_another_tenant_is_refused(client):
    uid, tid = seed_user(email="owner@example.com", role="admin")
    login(client)
    db = SessionLocal()
    other = m.Tenant(slug="other", name="Other")
    db.add(other); db.flush()
    net = m.Network(tenant_id=other.id, name="Theirs", slug="theirs", is_primary=True)
    db.add(net); db.commit(); nid = net.id; db.close()

    assert client.post(f"/api/v1/onboarding/networks/{nid}/invite", json={}).status_code == 404


def test_the_onboarding_payload_never_carries_the_code(client):
    """The same rule `test_account_payload.py` holds for the owner console: a
    code in a payload is a code in a browser cache, a proxy log and a screen
    share."""
    from guardian_hub import schemas_dash as sd
    for model in (sd.OnboardingState, sd.OnboardingNetwork, sd.OnboardingNext):
        bad = [f for f in model.model_fields if any(
            w in f.lower() for w in ("secret", "token", "enrol", "code", "password"))]
        assert bad == [], f"{model.__name__} exposes {bad}"
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd hub && .venv/bin/pytest tests/test_onboarding_enrol_mail.py -q`
Expected: FAIL — 404, the route does not exist

- [x] **Step 3: Add the endpoint**

In `routers/write.py`, admin-role (not viewer — this mints a credential, unlike `POST /onboarding`):

```python
@router.post("/onboarding/networks/{network_id}/invite", response_model=sd.OnboardingState)
def invite_sensor(network_id: str, ctx=ADMIN, db: DbSession = Depends(get_db)):
    """Mint an enrol code for this network and e-mail the setup instructions.

    Deliberately takes NO address. The code is a bearer credential and goes to
    the account's own e-mail; accepting a recipient here would turn the wizard
    into a way to mail a credential anywhere, and the request body is the
    least trustworthy place to learn who should hold one.

    Admin-role, unlike `POST /onboarding` beside it, which is viewer-level
    because every user walks the wizard. Minting a credential is not walking
    a wizard.
    """
    user, tenant, _role = ctx
    net = db.query(m.Network).filter_by(id=network_id, tenant_id=tenant.id).first()
    if net is None:
        raise HTTPException(404, "No such network")
    sensor = sensors.new_enrol_code_for(db, tenant, net.id, emailed=True)
    mailer.send_sensor_install(
        user.email, tenant_name=tenant.name, network_name=net.name,
        code=sensor.enrol_code,
        expires_on=sensor.enrol_expires.strftime("%d %B %Y"),
        install_cmd=installer.command_for(sensor.enrol_code),
    )
    audit.log(db, tenant, user, "sensor.invite", target=net.slug)
    db.commit()
    return onboarding_mod.state(db, user, tenant)
```

If `installer` has no `command_for`, add one returning the same string `GET /install` advertises — do not hardcode a hostname, for the reason `installer.py`'s module docstring gives.

- [x] **Step 4: Run test to verify it passes**

Run: `cd hub && .venv/bin/pytest tests/test_onboarding_enrol_mail.py tests/test_account_payload.py -q`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add hub/guardian_hub/routers/write.py hub/tests/test_onboarding_enrol_mail.py
git commit -m "Declaring a network e-mails whoever can install its sensor"
```

---

### Task 6: A device with no hostname reads the same everywhere

**Files:**
- Modify: `web/src/components/DeviceGrid.tsx:124`
- Test: `web/tests/deviceFallback.test.tsx` (create)

**Interfaces:**
- Consumes: `DeviceCard` from `web/src/types.ts`
- Produces: nothing downstream

- [x] **Step 1: Write the failing test**

```tsx
// web/tests/deviceFallback.test.tsx
// `DevicesTable` renders label -> name -> vendor -> mac; `DeviceGrid` skipped
// vendor, so the same nameless printer read "Brother" in one view and
// "aa:bb:cc:dd:ee:ff" in the other. The grid is what a new customer sees
// first, which is a real part of why a fresh estate looked unorganised.
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DeviceGrid } from "../src/components/DeviceGrid";

const nameless = { mac: "aa:bb:cc:dd:ee:ff", label: "", name: "", vendor: "Brother" };

describe("a device with no hostname", () => {
  it("shows its vendor rather than its MAC", () => {
    render(<DeviceGrid devices={[nameless as never]} />);
    expect(screen.getByText("Brother")).toBeTruthy();
    expect(screen.queryByText("aa:bb:cc:dd:ee:ff")).toBeNull();
  });
});
```

Check `DeviceGrid`'s real props with `grep -n "export function DeviceGrid" -A8 web/src/components/DeviceGrid.tsx` and match the test to them before running.

- [x] **Step 2: Run test to verify it fails**

Run: `cd web && npx vitest run tests/deviceFallback.test.tsx`
Expected: FAIL — the MAC renders

- [x] **Step 3: Add the fallback**

```tsx
{device.label || device.name || device.vendor || device.mac}
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd web && npx vitest run tests/deviceFallback.test.tsx`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add web/src/components/DeviceGrid.tsx web/tests/deviceFallback.test.tsx
git commit -m "A device with no hostname shows who made it instead of its MAC address"
```

---

### Task 7: The wizard renders the hub's answer

**Files:**
- Modify: `web/src/pages/Onboarding.tsx`
- Modify: `web/src/types.ts` (add `OnboardingNetwork`, `OnboardingNext`, extend `OnboardingState`)
- Modify: `web/src/lib/api.ts` (add `inviteSensor(networkId)`)
- Test: `web/tests/onboardingFlow.test.tsx` (create)

**Interfaces:**
- Consumes: `GET /api/v1/onboarding` → `OnboardingState` with `networks`/`next`; `POST /api/v1/onboarding/networks/{id}/invite`
- Produces: nothing downstream

- [x] **Step 1: Write the failing test**

Cover four behaviours, each a sentence in the test name:
- the screen shown is chosen by `next.action`, not by a local cursor;
- `localStorage` having a stale step does not override the hub;
- a `quiet` sensor says the sensor stopped talking, not that none is installed;
- the naming step's "Done" is enabled with nothing named (Task 8 adds the naming itself; assert the gate here).

- [x] **Step 2: Run test to verify it fails**

Run: `cd web && npx vitest run tests/onboardingFlow.test.tsx`

- [x] **Step 3: Rewrite the step selection**

Delete `STEP_ORDER`, `storageKey`, `readResumedStep`, `writeStep` and the `step` state for the **setup** path only. Render from `state.next.action`. Leave `TourWizard` untouched — it is genuinely linear.

- [x] **Step 4: Run test to verify it passes**

Run: `cd web && npm test`
Expected: the full suite passes, not only the new file.

- [x] **Step 5: Commit**

```bash
git add web/src/pages/Onboarding.tsx web/src/types.ts web/src/lib/api.ts web/tests/onboardingFlow.test.tsx
git commit -m "Setup resumes wherever you left it, on any device, with any number of networks"
```

---

### Task 8: Naming is offered, never required

**Files:**
- Modify: `web/src/pages/Onboarding.tsx` (the post-scan screen)
- Test: `web/tests/onboardingNaming.test.tsx` (create)

**Interfaces:**
- Consumes: the existing device label mutation already in `Onboarding.tsx`
- Produces: nothing downstream

- [x] **Step 1: Write the failing test**

```tsx
// Two halves of one rule. A machine must never write `label` -- it suppresses
// the unverified alert. A PERSON writing it is the whole point, and a test
// that simply forbade `label` would forbid the feature.
describe("the post-scan screen", () => {
  it("lets you finish having named nothing", () => { /* Done enabled at mount */ });
  it("sorts unrecognised devices first", () => { /* the ones a human adds value to */ });
  it("writes a typed name to the device", () => { /* the mutation fires */ });
});
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd web && npx vitest run tests/onboardingNaming.test.tsx`

- [x] **Step 3: Implement**

Summary first (`devices` and `by_group` from Task 3), naming list beneath it, unrecognised sorted first, "Done" always enabled.

- [x] **Step 4: Run test to verify it passes**

Run: `cd web && npm test`

- [x] **Step 5: Commit**

```bash
git add web/src/pages/Onboarding.tsx web/tests/onboardingNaming.test.tsx
git commit -m "Naming a device during setup is an offer, and skipping it finishes setup"
```

---

### Task 9: The blank minute, and DNS

**Files:**
- Modify: `web/src/pages/Onboarding.tsx` (`await_first_scan`, `offer_dns`)
- Test: `web/tests/onboardingWait.test.tsx` (create)

- [x] **Step 1: Write the failing test**

- the waiting screen explains what is happening rather than showing a bare spinner;
- the DNS step can be skipped and setup still completes;
- a tenant with `dns: "off"` still shows the dashboard prompt after completion.

- [x] **Step 2: Run to verify it fails**

- [x] **Step 3: Implement**

Copy paced against the poll, not a fixed timer, so it describes something true. Reuse `DnsWizard` unchanged.

- [x] **Step 4: Run to verify it passes** — `cd web && npm test`

- [x] **Step 5: Commit**

```bash
git commit -m "The wait for a first scan explains itself, and DNS is offered without blocking"
```

---

### Task 10: Write the warrant rule down

**Files:**
- Modify: `CLAUDE.md` (§ Where things live)
- Test: `hub/tests/test_warrant_rule.py` (create)

- [x] **Step 1: Write the failing test**

```python
"""`label` and `trusted` suppress detections. Only a person may set them."""
def test_no_automatic_path_writes_a_warrant_field():
    import pathlib, re
    for p in ("onboarding.py", "sensors.py"):
        src = pathlib.Path("guardian_hub", p).read_text()
        assert not re.search(r"\.trusted\s*=|\.label\s*=", src), f"{p} writes a warrant field"
```

- [x] **Step 2: Run it** — passes immediately if Tasks 1-5 were done correctly. If it fails, the implementation went wrong, not the test.

- [x] **Step 3: Add the CLAUDE.md paragraph**

Under § Where things live, stating: `kind`/`name` are machine inference; `label`/`trusted` are human warrants that suppress the rogue-uplink check, risky-port alerts, risk scoring and `lan:unverified`; trust is sticky via `ingest.py:349`; the first-scan cohort is already exempt from `unverified` via the baseline, so nothing automatic ever needs to write one.

- [x] **Step 4: Run the full suites**

Run: `cd hub && .venv/bin/pytest -q` and `cd web && npm test`
Expected: the 8 pre-existing `test_router_card.py` failures remain (another branch's in-flight work); nothing else fails.

- [x] **Step 5: Commit**

```bash
git add CLAUDE.md hub/tests/test_warrant_rule.py
git commit -m "The fields that suppress a detection are named, and only a person may set them"
```

---

## Self-Review

**Spec coverage.** §1 derived state → Tasks 2, 3, 7. §2 per-network enrol + e-mail → Tasks 1, 4, 5. §3 first view → Tasks 6, 8. §4 teaching and DNS → Task 9. §5 error handling → Task 2 (`pending`/`active`/`quiet`, redundant sensors), Task 5 (cross-tenant network), Task 7 (`quiet` copy). §6 testing → distributed through every task. The warrant rule → Task 10.

**Two spec items deliberately deferred**, both needing a product decision rather than code: deleting an abandoned network (§5 row 4) and the "one machine, three networks" warning copy (§5 row 6). Neither blocks any task here; both should become their own small plan.

**One spec amendment this plan makes.** §2 says the e-mailed code "keeps its short expiry". Implementation showed that is wrong — a 15-minute code read from an inbox is dead on arrival. Task 1 replaces it with a 7-day, 16-character code, and the entropy increase is not decoration: `routers/sensor.enrol` matches on the code alone with no tenant, so a longer window means more codes simultaneously guessable. The spec should be updated to match.

**Type consistency.** `OnboardingNetwork.sensor` is the same four strings in the schema, `_sensor_state`, the tests and the UI. `OnboardingNext.action` is the same four verbs in `_next`, the tests and Task 7. `new_enrol_code_for`'s `emailed` keyword is spelled identically in Tasks 1 and 5.
