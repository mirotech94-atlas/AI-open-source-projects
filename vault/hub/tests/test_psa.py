"""The PSA sync: HaloPSA / Autotask / ConnectWise off one class.

Two layers. The lifecycle tests replace the vendor seams wholesale and
check WHAT Vault decides to file, note, bill and resolve; the shape
tests replace `urlopen` and check the three vendors' requests carry the
documented fields. Nothing here talks to a real PSA, and the module
docstring says as much.
"""
import io
import json

import pytest

from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from guardian_hub.integrations import psa, registry
from guardian_hub.integrations.base import SendContext
from tests.test_seed_helpers import seeded_demo
from tests.test_integrations_new import configure

BASE = {"flavour": "halopsa", "base_url": "https://msp.halopsa.com", "client_id": "cid",
        "secret": "s3cret-" + "x" * 20, "company_ref": "42"}

TS = "2026-09-10T09:00:00"


def _tenant(db):
    return db.query(m.Tenant).filter_by(slug="demo").one()


def _alert(db, t, key, sev="CRIT", status="open", msg="Attacker seen"):
    a = m.Alert(tenant_id=t.id, key=key, sev=sev, msg=msg, first_seen=TS, last_seen=TS, status=status)
    db.add(a); db.commit()
    return a


class Fake:
    """The five seams as one recorder; a fresh instance per test."""

    def __init__(self, monkeypatch, status="open"):
        self.created, self.noted, self.timed, self.resolved, self.asked = [], [], [], [], []
        self.status = status
        self.n = 0

        def create(ctx, summary, body):
            self.n += 1
            self.created.append((summary, body))
            return str(1000 + self.n)

        monkeypatch.setattr(psa, "_create", create)
        monkeypatch.setattr(psa, "_note", lambda ctx, key, body: self.noted.append((key, body)))
        monkeypatch.setattr(psa, "_time_entry", lambda ctx, key, minutes, note: self.timed.append((key, minutes)))
        monkeypatch.setattr(psa, "_resolve", lambda ctx, key: self.resolved.append(key))
        monkeypatch.setattr(psa, "_status", lambda ctx, key: self.asked.append(key) or self.status)


def _seeded_alert_count(db, t, bar="CRIT"):
    return sum(1 for a in db.query(m.Alert).filter_by(tenant_id=t.id) if psa._escalated(a, bar))


# ------------------------------------------------------------- lifecycle

def test_one_ticket_per_escalated_finding_stamped_on_the_alert_and_never_refiled(client, monkeypatch):
    seeded_demo(client)
    configure(client, "halopsa", BASE)
    fake = Fake(monkeypatch)
    db = SessionLocal(); t = _tenant(db)
    _alert(db, t, "sec:attacker:demo:1.2.3.4")
    _alert(db, t, "sec:cron:demo", sev="WARN", msg="cron drift")        # below the bar
    expected = _seeded_alert_count(db, t)
    registry.watchdog_job()
    assert len(fake.created) == expected
    assert any("Attacker seen" in s for s, _ in fake.created)
    assert not any("cron drift" in s for s, _ in fake.created)
    db = SessionLocal()
    a = db.query(m.Alert).filter_by(key="sec:attacker:demo:1.2.3.4").one()
    assert a.ticket_key and a.ticket_key.isdigit()
    # a second tick, and a re-saved card that wipes nothing durable, file nothing new
    registry.watchdog_job()
    configure(client, "halopsa", BASE)
    registry.watchdog_job()
    assert len(fake.created) == expected


def test_the_bar_can_be_widened_to_warn(client, monkeypatch):
    seeded_demo(client)
    configure(client, "autotask", {**BASE, "flavour": "autotask", "base_url": "https://webservices.autotask.net",
                                   "username": "api@msp", "escalate": "CRIT+WARN"})
    fake = Fake(monkeypatch)
    db = SessionLocal(); t = _tenant(db)
    _alert(db, t, "sec:cron:demo", sev="WARN", msg="cron drift")
    registry.watchdog_job()
    assert any("cron drift" in s for s, _ in fake.created)


def test_a_snoozed_or_resolved_finding_without_a_ticket_is_not_filed(client, monkeypatch):
    seeded_demo(client)
    configure(client, "connectwise", {**BASE, "flavour": "connectwise", "base_url": "https://cw.example",
                                      "username": "msp+pub"})
    fake = Fake(monkeypatch)
    db = SessionLocal(); t = _tenant(db)
    _alert(db, t, "sec:attacker:demo:9.9.9.9", status="snoozed")
    _alert(db, t, "sec:attacker:demo:8.8.8.8", status="resolved")
    before = _seeded_alert_count(db, t)
    registry.watchdog_job()
    assert len(fake.created) == before
    assert not any("9.9.9.9" in b or "8.8.8.8" in b for _, b in fake.created)


def test_the_catalogued_fix_is_noted_once_and_time_is_booked_only_when_switched_on(client, monkeypatch):
    seeded_demo(client)
    configure(client, "halopsa", BASE)                       # time_entries default: off
    fake = Fake(monkeypatch)
    db = SessionLocal(); t = _tenant(db)
    _alert(db, t, "sec:attacker:demo:1.2.3.4")
    # remedy.fix_for_key("sec:attacker:demo:1.2.3.4") -> block_ip on demo with arg 1.2.3.4
    db.add(m.Fix(tenant_id=t.id, ts="2026-09-10T09:30:00", host="demo", action="block_ip", arg="1.2.3.4",
                 label="Blocked attacker IP", by="auto", ok=True))
    db.add(m.Fix(tenant_id=t.id, ts="2026-09-10T09:31:00", host="demo", action="block_ip", arg="5.5.5.5",
                 label="Other block", by="auto", ok=True))    # a different attacker: not this alert's fix
    db.commit()
    registry.watchdog_job()
    mine = [n for n in fake.noted if "Blocked attacker IP" in n[1]]
    assert len(mine) == 1 and not any("Other block" in n[1] for n in fake.noted)
    assert fake.timed == []
    registry.watchdog_job()
    assert len([n for n in fake.noted if "Blocked attacker IP" in n[1]]) == 1   # noted once

    # switch time on: the same, already-noted fix is NOT re-billed; a new one is
    configure(client, "halopsa", {**BASE, "time_entries": "on", "minutes": "20"})
    registry.watchdog_job()
    assert fake.timed == []
    _alert(db, t, "sec:attacker:demo:7.7.7.7")
    db.add(m.Fix(tenant_id=t.id, ts="2026-09-10T09:40:00", host="demo", action="block_ip", arg="7.7.7.7",
                 label="Blocked 7", by="auto", ok=True)); db.commit()
    registry.watchdog_job()
    assert len(fake.timed) == 1 and fake.timed[0][1] == 20


def test_a_failed_fix_is_not_a_fix(client, monkeypatch):
    seeded_demo(client)
    configure(client, "halopsa", {**BASE, "time_entries": "on"})
    fake = Fake(monkeypatch)
    db = SessionLocal(); t = _tenant(db)
    _alert(db, t, "sec:attacker:demo:1.2.3.4")
    db.add(m.Fix(tenant_id=t.id, ts="2026-09-10T09:30:00", host="demo", action="block_ip", arg="1.2.3.4",
                 label="Blocked attacker IP", by="auto", ok=False)); db.commit()
    registry.watchdog_job()
    assert not any("Blocked attacker IP" in n[1] for n in fake.noted) and fake.timed == []


def test_a_resolved_finding_resolves_its_ticket_once(client, monkeypatch):
    seeded_demo(client)
    configure(client, "halopsa", BASE)
    fake = Fake(monkeypatch)
    db = SessionLocal(); t = _tenant(db)
    a = _alert(db, t, "sec:attacker:demo:1.2.3.4")
    registry.watchdog_job()
    key = SessionLocal().query(m.Alert).filter_by(key=a.key).one().ticket_key
    assert key and key not in fake.resolved
    db2 = SessionLocal(); db2.query(m.Alert).filter_by(key=a.key).update({"status": "resolved"}); db2.commit()
    registry.watchdog_job(); registry.watchdog_job()
    assert fake.resolved.count(key) == 1


def test_a_ticket_closed_on_the_psa_acks_the_finding_here_and_is_audited_as_a_person_would_be(client, monkeypatch):
    seeded_demo(client)
    configure(client, "connectwise", {**BASE, "flavour": "connectwise", "base_url": "https://cw.example",
                                      "username": "msp+pub"})
    fake = Fake(monkeypatch, status="closed")
    db = SessionLocal(); t = _tenant(db)
    a = _alert(db, t, "sec:attacker:demo:1.2.3.4")
    registry.watchdog_job()
    db = SessionLocal()
    row = db.query(m.Alert).filter_by(key=a.key).one()
    assert row.status == "acked"           # acked, never resolved: only the sensor can say it is gone
    ed = db.query(m.Audit).filter_by(tenant_id=t.id, action="alert.ack", target=a.key).all()
    assert ed and ed[-1].user == "psa:connectwise"
    # and it is not re-acked every hour
    n = len(fake.asked)
    registry.watchdog_job()
    assert row.status == "acked" and len(fake.asked) < n + 5


def test_an_unreadable_psa_changes_nothing(client, monkeypatch):
    seeded_demo(client)
    configure(client, "halopsa", BASE)
    fake = Fake(monkeypatch, status=None)
    db = SessionLocal(); t = _tenant(db)
    a = _alert(db, t, "sec:attacker:demo:1.2.3.4")
    registry.watchdog_job()
    assert SessionLocal().query(m.Alert).filter_by(key=a.key).one().status == "open"


def test_send_is_a_no_op_so_severity_routing_cannot_file_noise(client):
    ctx = SendContext(tenant=None, secret="", config={}, db=None, base_url="")
    for kind in ("halopsa", "autotask", "connectwise"):
        assert registry.REGISTRY[kind].send(ctx, registry.Message(title="t", body="b", severity="CRIT")).ok
        assert registry.REGISTRY[kind].direction == "out+status sync"


def test_validation_refuses_a_plain_http_psa_and_silly_minutes(client):
    seeded_demo(client)
    r = client.post("/api/v1/integrations", json={"kind": "halopsa", "enabled": True, "routes": {},
                                                 "fields": {**BASE, "base_url": "http://msp.halopsa.com"}})
    assert r.status_code in (400, 422), r.text
    r = client.post("/api/v1/integrations", json={"kind": "halopsa", "enabled": True, "routes": {},
                                                 "fields": {**BASE, "minutes": "900"}})
    assert r.status_code in (400, 422), r.text
    r = client.post("/api/v1/integrations", json={"kind": "halopsa", "enabled": True, "routes": {},
                                                 "fields": {**BASE, "flavour": "kaseya"}})
    assert r.status_code in (400, 422), r.text


def test_the_card_says_it_is_unverified(client):
    seeded_demo(client)
    cards = {c["kind"]: c for c in client.get("/api/v1/integrations").json()["integrations"]}
    for kind in ("halopsa", "autotask", "connectwise"):
        assert "not yet verified" in cards[kind]["help"]


# ---------------------------------------------------------- HTTP shapes

class _Http:
    """Replaces `urlopen`: records every request, answers from a script.
    An answer that is an int is served as that HTTP error status."""

    def __init__(self, monkeypatch, answers):
        self.calls = []
        self.answers = list(answers)

        def urlopen(req, timeout=None):
            body = json.loads(req.data.decode()) if req.data and req.get_header("Content-type") == "application/json" \
                else (dict(x.split("=", 1) for x in req.data.decode().split("&")) if req.data else None)
            self.calls.append((req.get_method(), req.full_url, dict(req.header_items()), body))
            ans = self.answers.pop(0) if self.answers else {}
            if isinstance(ans, int):
                import urllib.error
                raise urllib.error.HTTPError(req.full_url, ans, "nope", {}, io.BytesIO(b'{"message": "bad board"}\nmore'))
            return io.BytesIO(json.dumps(ans).encode())

        monkeypatch.setattr(psa.urllib.request, "urlopen", urlopen)

    def by(self, method, needle):
        return [c for c in self.calls if c[0] == method and needle in c[1]]


BASES = {"halopsa": "https://msp.halopsa.com", "autotask": "https://webservices.autotask.net",
         "connectwise": "https://cw.example"}


def _ctx(flavour, resolved=True, **cfg):
    """A context whose names are already resolved (the cache the Test
    button or a first tick fills), so the shape tests see only the calls
    under test. `resolved=False` leaves the lookups to happen."""
    config = {"flavour": flavour, "base_url": BASES[flavour], "client_id": "cid", "username": "msp+pub",
              "company_ref": "42", "board": "7", **cfg}
    if resolved:
        config["_sync"] = {"resolved": {
            "company_ref": {"typed": config["company_ref"], "id": "42", "name": "Northwind Dental"},
            "board": {"typed": config["board"], "id": "7", "name": "Guardian"},
        }}
    return SendContext(tenant=None, secret="sek", config=config, db=None, base_url="https://hub")


def test_halopsa_fetches_a_bearer_once_and_files_notes_time_and_close_in_its_list_shape(monkeypatch):
    http = _Http(monkeypatch, [{"access_token": "tok", "expires_in": 3600}, [{"id": 55}], {}, {}, {}, {"status_id": 9}])
    ctx = _ctx("halopsa")
    assert psa._create(ctx, "S", "B") == "55"
    psa._note(ctx, "55", "note"); psa._time_entry(ctx, "55", 30, "n"); psa._resolve(ctx, "55")
    assert psa._status(ctx, "55") == "closed"
    tok = http.by("POST", "/auth/token")
    assert len(tok) == 1 and tok[0][3]["grant_type"] == "client_credentials" and tok[0][3]["client_secret"] == "sek"
    create = http.by("POST", "/api/Tickets")[0]
    assert create[2]["Authorization"] == "Bearer tok"
    assert create[3] == [{"summary": "S", "details": "B", "client_id": 42, "tickettype_id": 7}]
    actions = http.by("POST", "/api/Actions")
    assert actions[0][3][0]["ticket_id"] == 55 and actions[1][3][0]["timetaken"] == 0.5
    assert http.by("POST", "/api/Tickets")[1][3] == [{"id": 55, "status_id": 9}]
    assert ctx.config["_sync"]["halo_token"]["token"] == "tok"


def test_autotask_uses_the_three_auth_headers_and_needs_a_resource_to_book_time(monkeypatch):
    http = _Http(monkeypatch, [{"itemId": 77}, {}, {}, {}, {"item": {"status": 5}}])
    ctx = _ctx("autotask", resource_ref="9")
    assert psa._create(ctx, "S", "B") == "77"
    psa._note(ctx, "77", "note"); psa._time_entry(ctx, "77", 15, "n"); psa._resolve(ctx, "77")
    assert psa._status(ctx, "77") == "closed"
    create = http.by("POST", "/ATServicesRest/V1.0/Tickets")[0]
    assert {create[2]["Apiintegrationcode"], create[2]["Username"], create[2]["Secret"]} == {"cid", "msp+pub", "sek"}
    assert create[3]["companyID"] == 42 and create[3]["queueID"] == 7 and create[3]["title"] == "S"
    assert http.by("POST", "/Tickets/77/Notes")[0][3]["ticketID"] == 77
    te = http.by("POST", "/TimeEntries")[0][3]
    assert te["hoursWorked"] == 0.25 and te["resourceID"] == 9
    assert http.by("PATCH", "/Tickets")[0][3] == {"id": 77, "status": 5}
    # no resource: the entry goes without one and Autotask is the one to refuse it
    http2 = _Http(monkeypatch, [{}])
    psa._time_entry(_ctx("autotask"), "77", 15, "n")
    assert "resourceID" not in http2.by("POST", "/TimeEntries")[0][3]


def test_connectwise_uses_basic_auth_plus_clientid_and_patches_status_as_json_patch(monkeypatch):
    http = _Http(monkeypatch, [{"id": 88}, {}, {}, {}, {"closedFlag": True, "status": {"name": "Closed"}}])
    ctx = _ctx("connectwise", board="Guardian")
    assert psa._create(ctx, "S", "B") == "88"
    psa._note(ctx, "88", "note"); psa._time_entry(ctx, "88", 15, "n"); psa._resolve(ctx, "88")
    assert psa._status(ctx, "88") == "closed"
    create = http.by("POST", "/service/tickets")[0]
    assert create[2]["Authorization"].startswith("Basic ") and create[2]["Clientid"] == "cid"
    assert create[3]["company"] == {"id": 42} and create[3]["board"] == {"id": 7}   # the name resolved to its id
    assert http.by("POST", "/service/tickets/88/notes")[0][3]["internalAnalysisFlag"] is True
    assert http.by("POST", "/time/entries")[0][3]["chargeToType"] == "ServiceTicket"
    assert http.by("PATCH", "/service/tickets/88")[0][3] == [{"op": "replace", "path": "status", "value": {"name": "Closed"}}]


# ------------------------------------------------ names, ids and errors

def test_a_typed_company_and_board_name_resolve_to_the_psas_ids_once(monkeypatch):
    http = _Http(monkeypatch, [
        {"access_token": "tok", "expires_in": 3600},
        {"clients": [{"id": 41, "name": "Northwind Motors"}, {"id": 42, "name": "Northwind Dental"}]},
        [{"id": 7, "name": "Guardian"}, {"id": 8, "name": "Projects"}],
        [{"id": 55}], [{"id": 56}],
    ])
    ctx = _ctx("halopsa", resolved=False, company_ref="northwind dental", board="guardian")
    assert psa._create(ctx, "S", "B") == "55"
    assert http.by("POST", "/api/Tickets")[0][3][0]["client_id"] == 42
    assert http.by("POST", "/api/Tickets")[0][3][0]["tickettype_id"] == 7
    lookups = len(http.by("GET", "/api/Client")) + len(http.by("GET", "/api/TicketType"))
    assert lookups == 2
    psa._create(ctx, "S2", "B2")
    assert len(http.by("GET", "/api/Client")) + len(http.by("GET", "/api/TicketType")) == 2   # cached
    assert ctx.config["_sync"]["resolved"]["company_ref"] == {"typed": "northwind dental", "id": "42", "name": "Northwind Dental"}


def test_an_id_the_psa_knows_is_taken_as_is_and_a_unique_partial_name_is_enough(monkeypatch):
    rows = [("41", "Northwind Motors"), ("42", "Northwind Dental")]
    assert psa._match("42", rows, "company") == ("42", "Northwind Dental")
    assert psa._match("dental", rows, "company") == ("42", "Northwind Dental")


def test_an_ambiguous_or_unknown_name_lists_the_nearest_candidates():
    rows = [("41", "Northwind Motors"), ("42", "Northwind Dental"), ("43", "Acme")]
    with pytest.raises(psa.PsaError) as e:
        psa._match("northwind", rows, "company")
    assert "matches 2 companys" in str(e.value) or "matches 2" in str(e.value)
    assert "Northwind Motors" in str(e.value) and "Northwind Dental" in str(e.value)
    with pytest.raises(psa.PsaError) as e:
        psa._match("Nortwind Dentl", rows, "company")
    assert "matches 0" in str(e.value) and "Acme" in str(e.value)
    with pytest.raises(psa.PsaError) as e:
        psa._match("x", [], "board")
    assert "permissions" in str(e.value)


def test_autotask_picklists_and_connectwise_closed_flags_are_read_from_the_vendor(monkeypatch):
    _Http(monkeypatch, [{"fields": [
        {"name": "status", "picklistValues": [{"value": 1, "label": "New", "isActive": True},
                                              {"value": 5, "label": "Complete", "isActive": True},
                                              {"value": 9, "label": "Old", "isActive": False}]},
        {"name": "queueID", "picklistValues": [{"value": 29683, "label": "Service Desk"}]}]}])
    assert psa._statuses(_ctx("autotask")) == [("1", "New", None), ("5", "Complete", None)]
    _Http(monkeypatch, [{"fields": [{"name": "queueID", "picklistValues": [{"value": 29683, "label": "Service Desk"}]}]}])
    assert psa._boards(_ctx("autotask")) == [("29683", "Service Desk")]
    http = _Http(monkeypatch, [[{"id": 7, "name": "Guardian"}],
                               [{"id": 500, "name": "New", "closedFlag": False}, {"id": 509, "name": "Done", "closedFlag": True}]])
    assert psa._statuses(_ctx("connectwise", resolved=False, board="Guardian")) == [("500", "New", False), ("509", "Done", True)]
    assert "/service/boards/7/statuses" in http.calls[-1][1]


def test_a_vendor_error_carries_the_call_status_and_first_line_never_the_headers(monkeypatch):
    _Http(monkeypatch, [400])
    with pytest.raises(psa.PsaError) as e:
        psa._note(_ctx("connectwise"), "1", "n")
    msg = str(e.value)
    assert msg.startswith("POST /v4_6_release/apis/3.0/service/tickets/1/notes -> 400")
    assert "bad board" in msg and "more" not in msg and "Basic" not in msg and "sek" not in msg


def test_a_create_reply_without_an_id_says_which_keys_came_back(monkeypatch):
    _Http(monkeypatch, [{"result": "queued", "ticketNumber": "T-1"}])
    with pytest.raises(psa.PsaError) as e:
        psa._create(_ctx("autotask"), "S", "B")
    assert "reply keys: result, ticketNumber" in str(e.value)


def test_a_transport_failure_is_a_psa_error_and_a_missing_ticket_is_simply_unknown(monkeypatch):
    def boom(req, timeout=None):
        raise OSError("down")
    monkeypatch.setattr(psa.urllib.request, "urlopen", boom)
    with pytest.raises(psa.PsaError) as e:
        psa._create(_ctx("connectwise"), "S", "B")
    assert "no response: down" in str(e.value)
    _Http(monkeypatch, [404])
    assert psa._status(_ctx("connectwise"), "1") is None


def test_one_bad_ticket_reaches_the_card_and_does_not_stall_the_others(client, monkeypatch):
    seeded_demo(client)
    configure(client, "halopsa", BASE)
    created = []

    def create(ctx, summary, body):
        if "poison" in summary:
            raise psa.PsaError("POST /api/Tickets", 400, "bad summary")
        created.append(summary)
        return str(100 + len(created))

    monkeypatch.setattr(psa, "_create", create)
    monkeypatch.setattr(psa, "_status", lambda ctx, key: "open")
    db = SessionLocal(); t = _tenant(db)
    _alert(db, t, "sec:attacker:demo:1.1.1.1", msg="poison one")
    _alert(db, t, "sec:attacker:demo:2.2.2.2", msg="fine one")
    registry.watchdog_job()
    assert any("fine one" in s for s in created)
    row = SessionLocal().query(m.Integration).filter_by(kind="halopsa").one()
    assert "sec:attacker:demo:1.1.1.1: POST /api/Tickets -> 400: bad summary" in row.last_error


def test_a_name_that_does_not_resolve_stops_the_tick_with_the_candidates_on_the_card(client, monkeypatch):
    seeded_demo(client)
    configure(client, "halopsa", {**BASE, "company_ref": "Northwind"})
    calls = []
    monkeypatch.setattr(psa, "_halo_token", lambda ctx: "tok")
    monkeypatch.setattr(psa, "_companies", lambda ctx: calls.append(1) or [("41", "Northwind Motors"), ("42", "Northwind Dental")])
    db = SessionLocal(); t = _tenant(db)
    _alert(db, t, "sec:attacker:demo:1.1.1.1"); _alert(db, t, "sec:attacker:demo:2.2.2.2")
    registry.watchdog_job()
    row = SessionLocal().query(m.Integration).filter_by(kind="halopsa").one()
    assert "Northwind Motors" in row.last_error and "Northwind Dental" in row.last_error
    assert len(calls) == 1          # not once per alert
    assert SessionLocal().query(m.Alert).filter(m.Alert.tenant_id == t.id, m.Alert.ticket_key != "").count() == 0


def test_the_test_button_resolves_the_names_reports_them_and_keeps_what_it_learned(client, monkeypatch):
    seeded_demo(client)
    configure(client, "connectwise", {**BASE, "flavour": "connectwise", "base_url": "https://cw.example",
                                      "username": "msp+pub", "company_ref": "Northwind Dental", "board": "Guardian",
                                      "closed_status": "Done"})
    monkeypatch.setattr(psa, "_companies", lambda ctx: [("42", "Northwind Dental")])
    monkeypatch.setattr(psa, "_boards", lambda ctx: [("7", "Guardian")])
    monkeypatch.setattr(psa, "_statuses", lambda ctx: [("500", "New", False), ("509", "Done", True)])
    r = client.post("/api/v1/integrations/connectwise/test").json()
    assert r["ok"] is True and r["error"] == ""
    assert r["detail"] == ("Connected to ConnectWise. Company 'Northwind Dental' is id 42. "
                           "Board 'Guardian' is id 7. Resolved tickets move to status Done (509).")
    row = SessionLocal().query(m.Integration).filter_by(kind="connectwise").one()
    assert row.config["_sync"]["resolved"]["closed_status"] == {"typed": "Done", "id": "509", "name": "Done"}
    # and a wrong name is the error, with candidates, not an "OK"
    configure(client, "connectwise", {**BASE, "flavour": "connectwise", "base_url": "https://cw.example",
                                      "username": "msp+pub", "company_ref": "Northwnd", "board": "Guardian"})
    r = client.post("/api/v1/integrations/connectwise/test").json()
    assert r["ok"] is False and "Northwind Dental" in r["error"]
