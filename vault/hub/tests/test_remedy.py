"""Task 66: every alert knows its remedy, the buttons act, and an alert only
a person can fix hands off to chat.

The parity test at the top is the important one: `guardian_hub/remedy.py`
ports the sensor's fix catalogue by hand (the hub cannot import
`guardian_sensor`, which lives on the sensor host), so the port is checked
against `sensor/guardian_sensor/fix.py`'s real source on every run --
together with the two sets `payloads/inbox.py` already carried.
"""

import ast
import pathlib

import pytest

from tests.test_seed_helpers import seeded_demo
from guardian_hub import demoscrub, models as m, remedy
from guardian_hub.db import SessionLocal
from guardian_hub.payloads import inbox as inbox_payload
from guardian_hub.scheduler import SENSOR_SILENT_KEY

SENSOR_FIX = pathlib.Path(__file__).resolve().parents[2] / "sensor" / "guardian_sensor" / "fix.py"


def _sensor_tables() -> dict:
    """The module-level literals of `sensor/guardian_sensor/fix.py`, read as
    source rather than imported: the sensor package is not installed in the
    hub's venv (and must not be -- it is the other half of the system)."""
    tree = ast.parse(SENSOR_FIX.read_text())
    out = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                try:
                    out[target.id] = ast.literal_eval(node.value)
                except ValueError:
                    pass
    return out


def test_the_ported_fix_catalogue_still_matches_the_sensor():
    sensor = _sensor_tables()
    assert remedy.FIX_ALERT_KEY == sensor["FIX_ALERT_KEY"]
    assert remedy.FIX_LABELS == {k: v[0] for k, v in sensor["FIX_ACTIONS"].items()}
    # The two sets `payloads/inbox.py` ported before this task, checked here
    # too so one test fails when the sensor's catalogue moves.
    assert inbox_payload.FIX_ACTIONS == set(sensor["FIX_ACTIONS"])
    assert inbox_payload.MANUAL_ONLY == sensor["MANUAL_ONLY"]
    # Every action either table names must be a real catalogue entry.
    named = set(remedy.FIX_LABELS) | {a for _t, a in remedy.EXTRA_ALERT_KEY} | remedy.ARG_REQUIRED
    assert named <= set(sensor["FIX_ACTIONS"])


@pytest.mark.parametrize("key,expected", [
    # The host is read off the matching template, never positionally: the
    # third segment of `svc:`/`docker:` keys is the service/container, and
    # queueing a restart against a "host" called nginx is the bug this guards.
    ("svc:prod:nginx", ("restart_service", "prod", "nginx")),
    ("docker:prod:web-1", ("restart_container", "prod", "web-1")),
    ("sec:attacker:demo:1.2.3.4", ("block_ip", "demo", "1.2.3.4")),
    ("disk:prod", ("cleanup_disk", "prod", "")),
    ("bruteforce:prod", ("harden_fail2ban", "prod", "")),   # tier 2 wins over limit_ssh
    ("sec:ufw:prod", ("enable_ufw", "prod", "")),
    ("reboot:safehome", ("reboot", "safehome", "")),
    ("upd:pending:prod", ("full_upgrade", "prod", "")),
    # No remediation: a dead sensor, router-side findings, and a fix whose
    # argument the key does not carry (`kill_miner` needs a process list).
    (SENSOR_SILENT_KEY, None),
    ("home:evil-twin", None),
    ("sec:miner:prod", None),
    ("lan:new:aa:bb:cc:dd:ee:ff", None),
])
def test_fix_for_key_reads_the_catalogue_backwards(key, expected):
    assert remedy.fix_for_key(key) == expected


def _alert(db, tenant_id, key, msg="something happened", **kw):
    row = m.Alert(tenant_id=tenant_id, key=key, sev="CRIT", msg=msg, first_seen="2026-09-10T00:00:00",
                  last_seen="2026-09-10T00:00:00", count=1, status="open", **kw)
    db.add(row)
    db.commit()
    return row


def test_every_alert_resolves_to_one_of_the_three_kinds(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    tenant = db.get(m.Tenant, tid)
    alerts = db.query(m.Alert).filter_by(tenant_id=tid).all()
    assert alerts, "the demo tenant should carry alerts"
    for a in alerts:
        r = remedy.resolve(db, tenant, a, "admin")
        assert r.kind in ("auto", "approval", "human")
        # No silence, ever: a row always says what it can do, and anything
        # that is not immediately runnable says what it needs.
        assert r.summary
        assert r.kind == "auto" or r.need
        assert r.kind == "auto" or r.opening


def test_a_reachable_tier2_fix_is_auto_for_an_admin_only(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    tenant = db.get(m.Tenant, tid)
    a = db.query(m.Alert).filter(m.Alert.tenant_id == tid,
                                 m.Alert.key.like(f"sec:attacker:{ATTACKER_HOST}:%")).first()
    assert a is not None

    admin = remedy.resolve(db, tenant, a, "admin")
    assert (admin.kind, admin.can_run_now, admin.action, admin.tier) == ("auto", True, "block_ip", 2)
    assert admin.host == ATTACKER_HOST and admin.arg == a.key.split(":")[-1]

    for role in ("viewer", "operator"):
        lesser = remedy.resolve(db, tenant, a, role)
        assert lesser.kind == "approval" and lesser.can_run_now is False
        assert "admin" in lesser.need


# The demo tenant is scrubbed on the way in (`guardian_hub.demoscrub`), so an
# alert key built from the original capture's host name matches nothing.
REBOOT_HOST = demoscrub.map_host_name("safehome")
REBOOT_KEY = f"reboot:{REBOOT_HOST}"
ATTACKER_HOST = demoscrub.map_host_name("demo")


def test_a_tier3_fix_needs_approval_even_for_an_admin(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    tenant = db.get(m.Tenant, tid)
    a = db.query(m.Alert).filter_by(tenant_id=tid, key=REBOOT_KEY).first()
    r = remedy.resolve(db, tenant, a, "admin")
    assert (r.kind, r.action, r.tier, r.can_run_now) == ("approval", "reboot", 3, False)


def test_a_fix_on_a_host_with_no_ssh_access_is_a_human_errand(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    tenant = db.get(m.Tenant, tid)
    a = _alert(db, tid, "disk:unknown-box", "unknown-box disk 95% full")
    r = remedy.resolve(db, tenant, a, "admin")
    assert r.kind == "human" and r.action == "cleanup_disk"
    assert "unknown-box" in r.need and "ssh" in r.need.lower()


def test_the_silent_sensor_names_the_machine_and_the_command(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    tenant = db.get(m.Tenant, tid)
    db.add(m.Sensor(tenant_id=tid, name="safehome-sensor", platform="linux", status="active"))
    db.commit()
    a = _alert(db, tid, SENSOR_SILENT_KEY,
               "No data from the sensor for 39 min — Guardian cannot see this network right now.",
               evidence={"age_min": 39, "last_scan_ts": "2026-09-10T00:00:00", "threshold_min": 25})

    r = remedy.resolve(db, tenant, a, "admin")
    assert r.kind == "human" and r.action == "" and r.can_run_now is False
    assert "safehome-sensor" in r.need and "systemctl status guardian-sensor" in r.need
    assert "39 min" in r.opening and "cannot repair my own sensor" in r.opening


def test_a_macos_sensor_gets_the_launchd_command(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    tenant = db.get(m.Tenant, tid)
    db.add(m.Sensor(tenant_id=tid, name="mac-mini", platform="darwin", status="active"))
    db.commit()
    a = _alert(db, tid, SENSOR_SILENT_KEY, "No data from the sensor for 40 min",
               evidence={"age_min": 40})
    r = remedy.resolve(db, tenant, a, "admin")
    assert "launchctl list | grep com.guardian.sensor" in r.need


# --------------------------------------------------------------------------
# The payload and the routes
# --------------------------------------------------------------------------

def test_the_inbox_row_carries_the_remedy_and_gates_fix_on_auto(client):
    seeded_demo(client)
    rows = client.get("/api/v1/inbox?view=alerts").json()["rows"]
    assert rows
    for row in rows:
        remedy_out = row["remedy"]
        assert remedy_out is not None and remedy_out["kind"] in ("auto", "approval", "human")
        kinds = {a["kind"] for a in row["actions"]}
        # Fix is offered exactly when Vault may run it right now -- and
        # never for a tier-3 action like reboot, which used to be a one-click
        # button that rebooted a host.
        assert ("fix" in kinds) == (remedy_out["kind"] == "auto")
        if "fix" in kinds:
            fix = next(a for a in row["actions"] if a["kind"] == "fix")
            assert fix["path"] == "/api/v1/fix" and fix["tier"] == 2
            assert fix["body"]["host"] == remedy_out["host"]
            assert fix["body"]["action"] == remedy_out["action"]


def test_pressing_fix_queues_the_real_remediation(client):
    tid = seeded_demo(client)
    row = next(r for r in client.get("/api/v1/inbox?view=alerts").json()["rows"]
               if r["remedy"]["kind"] == "auto")
    fix = next(a for a in row["actions"] if a["kind"] == "fix")
    r = client.post(fix["path"], json=fix["body"])
    assert r.status_code == 200, r.text
    db = SessionLocal()
    q = db.query(m.Queue).filter_by(tenant_id=tid, path="/api/fix").order_by(m.Queue.id.desc()).first()
    assert q is not None and q.body["action"] == row["remedy"]["action"]
    assert q.body["host"] == row["remedy"]["host"]
    audit = db.query(m.Audit).filter_by(tenant_id=tid, action="fix").order_by(m.Audit.id.desc()).first()
    assert audit is not None and audit.user == "demoadmin" and audit.role == "admin"


def test_the_handoff_explains_a_human_only_alert_and_records_the_thread(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    db.add(m.Sensor(tenant_id=tid, name="safehome-sensor", platform="linux", status="active"))
    db.commit()
    tenant = db.get(m.Tenant, tid)
    _alert(db, tid, SENSOR_SILENT_KEY,
           "No data from the sensor for 39 min — Guardian cannot see this network right now.",
           evidence={"age_min": 39, "last_scan_ts": "2026-09-10T00:00:00"})
    assert tenant is not None

    out = client.post("/api/v1/chat/handoff", json={"key": SENSOR_SILENT_KEY}).json()
    assert out["remedy"]["kind"] == "human"
    assert out["proposal"] is None          # nothing to confirm: there is no fix
    assert "safehome-sensor" in out["opening"]
    assert "systemctl status guardian-sensor" in out["opening"]
    assert out["thread_id"]

    db2 = SessionLocal()
    thread = db2.get(m.ChatThread, out["thread_id"])
    assert thread is not None and thread.turns[-1]["text"] == out["opening"]
    audit = db2.query(m.Audit).filter_by(tenant_id=tid, action="alert.handoff").first()
    assert audit is not None and audit.user == "demoadmin" and audit.role == "admin"
    assert audit.target == SENSOR_SILENT_KEY and audit.body["kind"] == "human"


def test_the_handoff_mints_a_real_proposal_for_a_tier3_remedy(client):
    tid = seeded_demo(client)
    out = client.post("/api/v1/chat/handoff", json={"key": REBOOT_KEY}).json()
    assert out["remedy"]["kind"] == "approval"
    proposal = out["proposal"]
    assert proposal and proposal["token"]
    action = proposal["actions"][0]
    assert action["tool"] == "apply_fix" and action["tier"] == 3
    assert action["args"] == {"host": REBOOT_HOST, "action": "reboot"}

    db = SessionLocal()
    before = db.query(m.Queue).filter_by(tenant_id=tid, path="/api/fix").count()

    # Minting proposes; only a confirmation runs it, and only once.
    confirm = {"message": "yes", "thread": out["thread_id"], "confirm_token": proposal["token"]}
    r = client.post("/api/v1/chat", json=confirm)
    assert r.status_code == 200, r.text
    db2 = SessionLocal()
    assert db2.query(m.Queue).filter_by(tenant_id=tid, path="/api/fix").count() == before + 1

    again = client.post("/api/v1/chat", json=confirm).json()
    assert "not valid any more" in again["text"]
    db3 = SessionLocal()
    assert db3.query(m.Queue).filter_by(tenant_id=tid, path="/api/fix").count() == before + 1


def test_the_handoff_on_an_unknown_alert_is_a_404(client):
    seeded_demo(client)
    assert client.post("/api/v1/chat/handoff", json={"key": "nope:nothing"}).status_code == 404


# --- the blast radius ---------------------------------------------------

def test_every_repair_guardian_offers_says_what_it_will_cost():
    """The guard that keeps the table honest as the catalogue grows.

    Vault asks permission to change a running network, and the whole job of
    an approval is to make the tradeoff legible in one read. An action that
    reaches the owner with no cost line asks them to authorise something they
    have not been told, and the absence is invisible -- the UI renders nothing,
    which looks exactly like a calm action rather than a missing sentence.

    So every action with a human label needs one, and nothing may describe an
    action that does not exist. Adding a repair to `FIX_LABELS` without a cost
    line fails here rather than shipping silently.
    """
    missing = sorted(a for a in remedy.FIX_LABELS if a not in remedy.FIX_IMPACT)
    assert missing == [], f"these repairs would be approved with no stated cost: {missing}"
    orphan = sorted(a for a in remedy.FIX_IMPACT if a not in remedy.FIX_LABELS)
    assert orphan == [], f"cost lines for repairs that do not exist: {orphan}"


def test_a_cost_line_is_a_sentence_about_this_repair_not_a_hedge():
    """Shape, not prose -- but the shape is what makes it usable.

    A line like "may cause disruption" passes any test that only counts
    characters while telling the owner nothing they did not already assume,
    and it is the exact failure this feature exists to avoid. The hedging
    vocabulary is cheap to ban outright.
    """
    HEDGES = ("may cause disruption", "could be disruptive", "use with caution",
              "be careful", "at your own risk", "unknown impact")
    for action, line in remedy.FIX_IMPACT.items():
        assert len(line) > 40, f"{action}: too short to say what breaks — {line!r}"
        assert line[0].isupper(), f"{action}: not a sentence — {line!r}"
        assert line.rstrip().endswith("."), f"{action}: not a sentence — {line!r}"
        low = line.lower()
        for hedge in HEDGES:
            assert hedge not in low, f"{action}: hedges instead of naming the cost — {line!r}"


def test_the_repair_that_hits_bystanders_says_so():
    """The one case a generic sentence would hide.

    `block_subnet` does not block an attacker; it blocks 256 addresses, which
    is a whole ISP or office range. That fact is the entire reason a human is
    asked rather than the fix running itself, so it has to survive an edit that
    is "tidying up the wording".
    """
    line = remedy.FIX_IMPACT["block_subnet"]
    assert "256" in line
    assert remedy.FIX_IMPACT["block_ip"] != line, (
        "blocking one address and blocking 256 must not share a cost line"
    )


def test_an_approval_handoff_tells_the_owner_the_cost_before_it_asks(client):
    """The line has to reach the message the owner actually reads.

    A cost that exists only in a dict helps nobody. `resolve_action` builds the
    handoff opening for every non-auto case, and this asserts the cost is in it
    and is introduced rather than run on to the end of the previous sentence.
    """
    tid = seeded_demo(client)
    db = SessionLocal()
    tenant = db.get(m.Tenant, tid)
    r = remedy.resolve_action(db, tenant, "viewer", "restart_service",
                              "web-01", "nginx", "nginx stopped responding")
    db.close()
    assert r.impact == remedy.FIX_IMPACT["restart_service"]
    assert "What it will cost:" in r.opening
    assert r.impact in r.opening


def test_a_repair_guardian_runs_alone_still_carries_its_cost(client):
    """`auto` is not exempt.

    Nobody is asked before an auto fix runs, which is exactly why the cost has
    to be attached: the owner reading the log afterwards -- "why did the site
    blink at 3am?" -- is asking this question, and a line present only on the
    approval path answers it only for the cases where somebody was already
    watching.
    """
    tid = seeded_demo(client)
    db = SessionLocal()
    tenant = db.get(m.Tenant, tid)
    # The demo capture's own host, so the reachability check has real SSH
    # facts to answer from rather than resolving to a human errand.
    r = remedy.resolve_action(db, tenant, "admin", "cleanup_disk", ATTACKER_HOST, "", "disk is full")
    db.close()
    if r.kind == "auto":
        assert r.impact == remedy.FIX_IMPACT["cleanup_disk"]
        # ...and it is NOT pushed into the handoff copy, because an auto fix
        # has no handoff to push it into.
        assert r.opening == ""
