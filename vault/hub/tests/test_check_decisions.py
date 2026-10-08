"""Wi-Fi guard / Security posture drill-down (owner request): clicking a
failing check opens detail, and offers exactly two decisions -- resolve it,
or leave it. "Leave it" is recorded and reversible; "resolve" either runs
the repair Vault owns, or hands off to chat explaining what it needs --
and never claims a fix ran when nothing did.
"""

import pytest

from tests.test_seed_helpers import seeded_demo


def _force_evil_twin(db, tid):
    """Rewrite the latest scan's raw Wi-Fi facts so `wifi:evil_twin` fails,
    the same direct-mutation pattern `test_security.py` already uses
    (`test_admin_panel_catch_all_alone_passes` et al.) -- documentation-range
    addresses only (RFC 5737 / RFC 5735), never anything from a real
    network."""
    from guardian_hub.scans import latest_scan
    from guardian_hub import models as m

    scan = latest_scan(db, tid)
    raw = dict(scan.raw)
    home = dict(raw.get("home") or {})
    wifi = dict(home.get("wifi") or {})
    wifi["channel"] = 100
    wifi["ssid"] = "Northwind-Staff"
    home["wifi"] = wifi
    home["gateway"] = "203.0.113.1"
    raw["home"] = home
    rogue = dict(raw.get("rogue") or {})
    # A REAL evil twin: same name, weaker encryption than ours. Channels alone
    # no longer raise this check -- see test_evil_twin.py for why (it flagged
    # every dual-band router and mesh, including the owner's own).
    rogue["twin_channels"] = [6, 100]
    rogue["twin_security"] = ["Open"]
    rogue["default_gateway"] = "203.0.113.1"
    raw["rogue"] = rogue
    scan.raw = raw
    db.add(scan)
    db.commit()
    return db.query(m.Membership).filter_by(tenant_id=tid).first()


def test_evil_twin_detail_names_the_encryption_it_actually_saw(client):
    """This check used to fire on "the SSID is on another channel", and its
    detail copy hedged accordingly -- it had to, because that signal cannot
    tell a mesh node from an attacker. It now fires only on a twin advertising
    DIFFERENT encryption, which is not ambiguous, so the detail says what it
    saw plainly instead of hedging."""
    tid = seeded_demo(client)
    from guardian_hub.db import SessionLocal
    db = SessionLocal()
    _force_evil_twin(db, tid)

    detail = client.get("/api/v1/security/checks/detail", params={"key": "wifi:evil_twin"}).json()
    assert detail["passed"] is False
    assert detail["confidence"] == "confirmed"
    # The evidence restated as a sentence, not a raw field dump.
    assert "twin_security" not in detail["what_we_saw"]
    assert "twin_channels" not in detail["what_we_saw"]
    saw = detail["what_we_saw"].lower()
    assert "open" in saw and "encryption" in saw
    # And it must say what the owner should do, without hedging it into
    # nothing. That copy rides on the remedy's `need`, as it does everywhere.
    todo = (detail["remedy"]["need"] or "").lower()
    assert "do not connect" in todo and "password" in todo
    # It still has to explain the thing being ruled out, so a reader with a
    # mesh understands why THIS one is different from their own kit.
    why = detail["why_it_matters"].lower()
    assert "mesh" in why
    assert "rogue" in why or "eavesdrop" in why
    # No repair Vault can run for this one.
    assert detail["remedy"]["kind"] == "human"
    assert detail["decision"]["status"] == "open"


def test_resolve_on_evil_twin_hands_off_and_runs_nothing(client):
    """The failing test the task calls out by name: resolving a no-autofix
    check must never claim it fixed something. Assert the absence of a run,
    not just the shape of the response."""
    tid = seeded_demo(client)
    from guardian_hub.db import SessionLocal
    from guardian_hub import models as m
    db = SessionLocal()
    _force_evil_twin(db, tid)

    fix_before = db.query(m.Fix).count()
    queue_before = db.query(m.Queue).count()

    out = client.post("/api/v1/chat/checks/handoff", json={"key": "wifi:evil_twin"}).json()
    assert out["remedy"]["kind"] == "human"
    assert out["remedy"]["can_run_now"] is False
    assert out["proposal"] is None
    assert "evil twin" in out["opening"].lower() or "access point" in out["opening"].lower()
    assert out["thread_id"]

    db2 = SessionLocal()
    assert db2.query(m.Fix).count() == fix_before
    assert db2.query(m.Queue).count() == queue_before


def test_leave_it_is_recorded_and_shows_on_the_row(client):
    tid = seeded_demo(client)
    from guardian_hub.db import SessionLocal
    db = SessionLocal()
    _force_evil_twin(db, tid)

    r = client.post("/api/v1/security/checks/leave",
                    json={"key": "wifi:evil_twin", "reason": "it's our own mesh AP"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "left" and out["decided_by"] == "demoadmin" and out["decided_at"]

    body = client.get("/api/v1/security").json()
    row = next(r for r in body["wifi"]["rows"] if r["key"] == "wifi:evil_twin")
    assert row["decision"]["status"] == "left"
    assert row["decision"]["reason"] == "it's our own mesh AP"
    # Still failing -- "leave it" records a decision, it does not pretend the
    # check passed.
    assert row["passed"] is False


def test_leave_it_is_reversible(client):
    tid = seeded_demo(client)
    from guardian_hub.db import SessionLocal
    db = SessionLocal()
    _force_evil_twin(db, tid)

    # The reason is required (422 without one), and the status is asserted
    # here rather than left to the row read below: a rejected write and a
    # write that never changed the row both surface as "open" three lines
    # down, and only one of them is this test's subject.
    left = client.post("/api/v1/security/checks/leave",
                       json={"key": "wifi:evil_twin", "reason": "it's our own mesh AP"})
    assert left.status_code == 200, left.text

    body = client.get("/api/v1/security").json()
    row = next(r for r in body["wifi"]["rows"] if r["key"] == "wifi:evil_twin")
    assert row["decision"]["status"] == "left"

    undo = client.post("/api/v1/security/checks/undo", json={"key": "wifi:evil_twin"}).json()
    assert undo["status"] == "open"

    body2 = client.get("/api/v1/security").json()
    row2 = next(r for r in body2["wifi"]["rows"] if r["key"] == "wifi:evil_twin")
    assert row2["decision"]["status"] == "open"


def test_leave_it_requires_operator(client):
    tid = seeded_demo(client)
    from guardian_hub.db import SessionLocal
    from guardian_hub import mailer, models as m
    db = SessionLocal()
    _force_evil_twin(db, tid)

    # A second, ordinary (non-superadmin) user with only a viewer membership
    # on this same tenant -- `memberships()` short-circuits to "admin" for a
    # superadmin regardless of its `Membership.role`, so demoting the seeded
    # demo admin in place (as an earlier version of this test did) silently
    # tests nothing.
    u = m.User(username="ann", email="ann@example.com", display_name="Ann")
    db.add(u)
    db.flush()
    db.add(m.Membership(user_id=u.id, tenant_id=tid, role="viewer"))
    db.commit()

    client.post("/api/v1/auth/logout")
    client.post("/api/v1/auth/start", json={"username": "ann"})
    code = mailer.LAST_CODES["ann@example.com"]
    signed_in = client.post("/api/v1/auth/verify", json={"username": "ann", "code": code})
    assert signed_in.status_code == 200, signed_in.text

    r = client.post("/api/v1/security/checks/leave", json={"key": "wifi:evil_twin"})
    assert r.status_code == 403


def test_detail_404s_for_an_unknown_check(client):
    seeded_demo(client)
    r = client.get("/api/v1/security/checks/detail", params={"key": "wifi:nope"})
    assert r.status_code == 404


def test_a_repairable_host_check_resolves_through_the_ordinary_fix_route(client):
    """`ssh` checks are autofix-backed (`disable_ssh_password`) -- resolve
    must go through the same tier machinery as every other fix, and an
    admin's handoff mints a real, single-use tier-3 proposal rather than
    running anything unattended."""
    tid = seeded_demo(client)
    from guardian_hub.db import SessionLocal
    from guardian_hub import demoscrub, models as m
    db = SessionLocal()
    # The one host `test_remedy.py` already relies on being SSH-reachable in
    # the demo tenant's config (`ATTACKER_HOST`) -- any other host may have
    # no SSH capability configured, which would make this a "human" remedy
    # instead of the tier machinery this test is about.
    host = db.query(m.Host).filter_by(tenant_id=tid, name=demoscrub.map_host_name("demo")).first()
    assert host is not None
    last = dict(host.last or {})
    sec = dict(last.get("sec") or {})
    sec["ssh_password"] = True
    last["sec"] = sec
    host.last = last
    db.add(host)
    db.commit()

    key = f"ssh:{host.name}"
    detail = client.get("/api/v1/security/checks/detail", params={"key": key}).json()
    assert detail["passed"] is False
    assert detail["remedy"]["action"] == "disable_ssh_password"
    assert detail["remedy"]["host"] == host.name

    fix_before = db.query(m.Fix).count()
    queue_before = db.query(m.Queue).count()

    out = client.post("/api/v1/chat/checks/handoff", json={"key": key}).json()
    # disable_ssh_password is disruptive (tier 3) -- a real proposal is
    # minted, never an unattended run.
    assert out["remedy"]["kind"] == "approval"
    assert out["proposal"] is not None and out["proposal"]["token"]

    db2 = SessionLocal()
    assert db2.query(m.Fix).count() == fix_before
    assert db2.query(m.Queue).count() == queue_before


def test_a_non_disruptive_resolve_dispatches_immediately_no_second_confirm(client):
    """The owner's other half of the same rule: "if I say yes, then do it" --
    a check whose fix is tier 2 (`enable_ufw` is not in `chat.tiers.
    TIER3_FIXES`) must queue on the FIRST `POST /api/v1/fix`, with no
    `confirm_token` and no 409 asking for one. Only a disruptive fix (see
    `test_a_repairable_host_check_resolves_through_the_ordinary_fix_route`
    above) may ever demand a second step."""
    tid = seeded_demo(client)
    from guardian_hub.db import SessionLocal
    from guardian_hub import demoscrub, models as m
    db = SessionLocal()
    host = db.query(m.Host).filter_by(tenant_id=tid, name=demoscrub.map_host_name("demo")).first()
    assert host is not None
    last = dict(host.last or {})
    sec = dict(last.get("sec") or {})
    sec["ufw"] = "inactive"
    last["sec"] = sec
    host.last = last
    db.add(host)
    db.commit()

    key = f"firewall:{host.name}"
    detail = client.get("/api/v1/security/checks/detail", params={"key": key}).json()
    assert detail["passed"] is False
    assert detail["remedy"]["action"] == "enable_ufw"
    assert detail["remedy"]["kind"] == "auto"
    assert detail["remedy"]["can_run_now"] is True

    queue_before = db.query(m.Queue).count()
    r = client.post("/api/v1/fix", json={"host": host.name, "action": "enable_ufw", "arg": None})
    assert r.status_code == 200, r.text          # never a 409 demanding a confirm_token
    assert db.query(m.Queue).count() == queue_before + 1


def _force_weak_wifi(db, tid, security="open"):
    """Rewrite the latest scan so the sensor reports the primary network's
    own Wi-Fi as `security` -- "open" by default, a genuinely unencrypted
    network, so the general checklist's `wifi:home` ("Wi-Fi encryption")
    row FAILS. This used to write an empty string, which was folded in
    with "open" until `wifi_verdict` made the check tri-state: an empty
    reading now means the sensor could not read the field (on Ethernet,
    or macOS withheld it), which is "can't tell", not a failing row. The
    test for that state is `test_wifi_encryption_unread_is_not_a_finding`."""
    from guardian_hub.scans import latest_scan

    scan = latest_scan(db, tid)
    raw = dict(scan.raw)
    home = dict(raw.get("home") or {})
    wifi = dict(home.get("wifi") or {})
    wifi["ssid"] = "Northwind-Guest"
    wifi["security"] = security
    home["wifi"] = wifi
    raw["home"] = home
    scan.raw = raw
    db.add(scan)
    db.commit()


def test_wifi_encryption_detail_loads(client):
    """`wifi:home` is the checklist's "Wi-Fi encryption" row, not one of the
    Wi-Fi guard's own rows -- but `check_detail` used to route every
    `wifi:`-prefixed key to the guard rows, where it is never found, so the
    row 404'd and the drawer said "This check could not be loaded -- it may
    no longer apply" on every click, refresh or not (seen in production)."""
    tid = seeded_demo(client)
    from guardian_hub.db import SessionLocal
    db = SessionLocal()
    _force_weak_wifi(db, tid)

    r = client.get("/api/v1/security/checks/detail", params={"key": "wifi:home", "target": "Home network"})
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["key"] == "wifi:home"
    assert detail["title"] == "Wi-Fi encryption"
    assert detail["passed"] is False
    assert "open" in detail["evidence"].lower()

    # And "leave it" -- which resolves the check the same way -- records.
    r = client.post("/api/v1/security/checks/leave", json={"key": "wifi:home", "target": "Home network", "reason": "guest net"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "left"


def test_wifi_encryption_unread_is_not_a_finding(client):
    """An empty `security` reading is the sensor not seeing the field, not
    an open network. The row is tri-state on purpose (`wifi_verdict`): the
    drawer still loads, says it cannot tell, and does not send an owner on
    WPA2 off to "set WPA2"."""
    tid = seeded_demo(client)
    from guardian_hub.db import SessionLocal
    db = SessionLocal()
    _force_weak_wifi(db, tid, security="")

    r = client.get("/api/v1/security/checks/detail", params={"key": "wifi:home", "target": "Home network"})
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["passed"] is None
    assert "could not be read" in detail["evidence"]
    assert detail["confidence"] == "uncertain"
    assert "could not read" in detail["why_it_matters"]
    need = detail["remedy"].get("need") or ""
    assert "set your Wi-Fi" not in need and "WPA2 or WPA3" not in need
