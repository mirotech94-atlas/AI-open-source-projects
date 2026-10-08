"""Support tickets: who may see a thread, and what a reply actually does.

Authorisation comes first and deliberately: a ticket is a customer telling
Vault something private about their network, so the interesting failures
are cross-tenant reads and a tenant admin reaching the operator's queue --
not the happy path.

The other thing pinned here is the promise made to the customer: a reply
lands in the in-app thread AND is emailed. Both halves, every time, and
neither one failing takes the other down.
"""
from guardian_hub import mailer, models as m, tickets as svc
from guardian_hub.db import SessionLocal
from tests.test_auth import login, seed_user


def _two_tenants():
    """`miro` (superadmin, tenant home) and `dana` (admin of Northwind)."""
    uid, tid = seed_user(username="miro", email="m@example.com", tenant_slug="home")
    db = SessionLocal()
    db.get(m.User, uid).is_superadmin = True
    other = m.Tenant(slug="northwind", name="Northwind Dental")
    dana = m.User(username="dana", email="dana@example.com", display_name="Dana")
    db.add_all([other, dana]); db.flush()
    db.add(m.Membership(user_id=dana.id, tenant_id=other.id, role="admin"))
    # A contact address, so a reply to this customer has somewhere to go.
    other.settings = {"profile": {"email": "dana@northwind.example"}}
    db.commit()
    other_id = other.id
    db.close()
    return tid, other_id


def _raise(client, subject="Sensor keeps dropping off", body="It goes offline every night around 2am.",
           priority="normal"):
    r = client.post("/api/v1/support/tickets",
                    json={"subject": subject, "body": body, "priority": priority})
    assert r.status_code == 200, r.text
    return r.json()["ticket"]["id"]


def _mailbox():
    """Start watching outbound mail, and hand back a reader for it.

    `mailer.SENT` is capped at 50 entries (`del SENT[:-50]`), so remembering
    an index and slicing from it silently misses letters once a long run has
    pushed past the cap -- which is exactly how these assertions passed alone
    and failed inside the wider suite. Clear it and read the whole thing
    instead."""
    mailer.SENT.clear()
    return lambda: list(mailer.SENT)


# --------------------------------------------------------------------------
# Authorisation
# --------------------------------------------------------------------------

def test_signed_out_can_neither_read_nor_raise(client):
    seed_user()
    assert client.get("/api/v1/support/tickets").status_code in (401, 403)
    assert client.post("/api/v1/support/tickets",
                       json={"subject": "x", "body": "y"}).status_code in (401, 403)


def test_a_tenant_admin_cannot_reach_the_operator_queue(client):
    seed_user(role="admin")
    login(client)
    assert client.get("/api/v1/platform/tickets").status_code == 403
    r = client.post("/api/v1/platform/tickets/whatever/reply", json={"body": "hi"})
    assert r.status_code == 403


def test_one_customer_cannot_read_another_customers_ticket(client):
    _tid, _other = _two_tenants()
    # Dana raises a ticket for Northwind.
    login(client, username="dana", email="dana@example.com")
    dana_ticket = _raise(client, subject="Camera offline")
    client.post("/api/v1/auth/logout")

    # miro is a superadmin, but the TENANT-scoped route is still scoped: it
    # answers for the tenant the session is in, and nobody else's.
    login(client)
    r = client.get(f"/api/v1/support/tickets/{dana_ticket}")
    assert r.status_code == 404, r.text          # not 403: existence is not theirs to learn
    mine = client.get("/api/v1/support/tickets").json()
    assert mine["tickets"] == []


def test_a_viewer_may_ask_for_help(client):
    """Noticing a problem is not an administrative act."""
    seed_user(role="viewer")
    login(client)
    assert client.post("/api/v1/support/tickets",
                       json={"subject": "Wi-Fi is slow", "body": "Since Tuesday."}).status_code == 200


# --------------------------------------------------------------------------
# Raising, answering, resolving
# --------------------------------------------------------------------------

def test_raising_a_ticket_emails_the_support_mailbox_and_opens_a_thread(client):
    seed_user()
    login(client)
    sent = _mailbox()
    tid = _raise(client)
    detail = client.get(f"/api/v1/support/tickets/{tid}").json()
    assert detail["ticket"]["status"] == "open"
    assert detail["ticket"]["opened_by"] == "miro"
    assert [msg["side"] for msg in detail["messages"]] == ["customer"]
    assert "2am" in detail["messages"][0]["body"]
    # The operator was told, at the support address.
    new = sent()
    assert any(to == "support@guarder.io" for to, _s, _t in new), new


def test_an_owner_reply_posts_to_the_thread_and_emails_the_customer(client):
    _tid, _other = _two_tenants()
    login(client, username="dana", email="dana@example.com")
    tid = _raise(client)
    client.post("/api/v1/auth/logout")

    login(client)            # the owner
    sent = _mailbox()
    r = client.post(f"/api/v1/platform/tickets/{tid}/reply",
                    json={"body": "Found it: the sensor's power strip is on a timer."})
    assert r.status_code == 200, r.text
    body = r.json()
    # In the thread...
    assert [msg["side"] for msg in body["messages"]] == ["customer", "owner"]
    assert "power strip" in body["messages"][-1]["body"]
    # ...and in the customer's inbox, at the address on their profile.
    new = sent()
    assert any(to == "dana@northwind.example" for to, _s, _t in new), new
    # The ball is now with them.
    assert body["ticket"]["status"] == "answered"


def test_the_owner_resolves_a_ticket_and_the_status_says_so(client):
    _tid, _other = _two_tenants()
    login(client, username="dana", email="dana@example.com")
    tid = _raise(client)
    client.post("/api/v1/auth/logout")

    login(client)
    sent = _mailbox()
    r = client.post(f"/api/v1/platform/tickets/{tid}/status",
                    json={"status": "resolved", "note": "Timer removed. Reporting since 03:10."})
    assert r.status_code == 200, r.text
    t = r.json()["ticket"]
    assert t["status"] == "resolved"
    assert t["resolved_by"] == "miro" and t["resolved_at"]
    # The closing note is part of the thread, not just a status change.
    assert "Timer removed" in r.json()["messages"][-1]["body"]
    # The customer is told, and the subject says resolved.
    new = sent()
    assert any(to == "dana@northwind.example" and s.lower().startswith("resolved")
               for to, s, _t in new), new


def test_a_customer_reply_reopens_a_resolved_ticket(client):
    """A ticket closed on a problem that is not actually fixed must be
    answerable in place, or the customer just opens a second one."""
    _tid, _other = _two_tenants()
    login(client, username="dana", email="dana@example.com")
    tid = _raise(client)
    client.post("/api/v1/auth/logout")
    login(client)
    client.post(f"/api/v1/platform/tickets/{tid}/status", json={"status": "resolved"})
    client.post("/api/v1/auth/logout")

    login(client, username="dana", email="dana@example.com")
    r = client.post(f"/api/v1/support/tickets/{tid}/reply",
                    json={"body": "It dropped again last night."})
    assert r.status_code == 200, r.text
    t = r.json()["ticket"]
    assert t["status"] == "open"
    assert t["resolved_at"] == "" and t["resolved_by"] == ""


def test_the_owner_can_put_a_ticket_back_in_the_queue(client):
    _tid, _other = _two_tenants()
    login(client, username="dana", email="dana@example.com")
    tid = _raise(client)
    client.post("/api/v1/auth/logout")
    login(client)
    client.post(f"/api/v1/platform/tickets/{tid}/status", json={"status": "resolved"})
    r = client.post(f"/api/v1/platform/tickets/{tid}/status", json={"status": "open"})
    assert r.json()["ticket"]["status"] == "open"
    assert client.post(f"/api/v1/platform/tickets/{tid}/status",
                       json={"status": "sideways"}).status_code == 422


def test_a_customer_with_no_address_on_file_still_gets_the_reply_in_the_thread(client):
    """The normal case for a demo tenant, and for anyone who never filled in
    their profile. It must not raise and must not lose the reply."""
    _tid, other = _two_tenants()
    db = SessionLocal()
    t = db.get(m.Tenant, other)
    t.settings = {}                      # no profile email
    db.commit(); db.close()
    # And the opener has no address either, so there is genuinely nowhere.
    db = SessionLocal()
    db.query(m.User).filter_by(username="dana").one().email = ""
    db.commit(); db.close()

    login(client, username="miro", email="m@example.com")
    db = SessionLocal()
    tk = m.Ticket(tenant_id=other, subject="Quiet one", opened_by="dana")
    db.add(tk); db.commit(); tid = tk.id; db.close()

    r = client.post(f"/api/v1/platform/tickets/{tid}/reply", json={"body": "Looking into it."})
    assert r.status_code == 200, r.text
    msgs = r.json()["messages"]
    assert msgs[-1]["body"] == "Looking into it."
    assert msgs[-1]["emailed"] is False     # honest: nothing was sent


def test_a_reserved_address_is_not_recorded_as_emailed(client):
    """Demo and seeded accounts live at `@guardian.invalid`, a domain RFC 2606
    reserves so that it can NEVER receive mail. A reply to one must not be
    stamped as delivered: the operator's view reads that stamp, and the first
    version of this recorded a letter to `demoadmin@guardian.invalid` exactly
    like one that arrived."""
    _tid, other = _two_tenants()
    db = SessionLocal()
    db.get(m.Tenant, other).settings = {}                     # no profile email
    db.query(m.User).filter_by(username="dana").one().email = "dana@guardian.invalid"
    tk = m.Ticket(tenant_id=other, subject="Demo thread", opened_by="dana")
    db.add(tk); db.commit(); tid = tk.id; db.close()

    login(client, username="miro", email="m@example.com")
    r = client.post(f"/api/v1/platform/tickets/{tid}/reply", json={"body": "Looking at it."})
    assert r.status_code == 200, r.text
    msgs = r.json()["messages"]
    assert msgs[-1]["body"] == "Looking at it."               # the thread still has it
    assert msgs[-1]["emailed"] is False                       # and says no letter went


def test_the_queue_puts_what_needs_answering_first(client):
    _tid, _other = _two_tenants()
    login(client, username="dana", email="dana@example.com")
    first = _raise(client, subject="One")
    second = _raise(client, subject="Two")
    client.post("/api/v1/auth/logout")

    login(client)
    # Answer the NEWER one, so recency alone would put it on top.
    client.post(f"/api/v1/platform/tickets/{second}/reply", json={"body": "On it."})
    rows = client.get("/api/v1/platform/tickets").json()["tickets"]
    assert rows[0]["id"] == first                  # still waiting on us
    assert rows[0]["status"] == "open"
    assert rows[0]["tenant_name"] == "Northwind Dental"
    assert client.get("/api/v1/platform/tickets").json()["open_count"] == 1


def test_empty_subject_or_body_is_refused(client):
    seed_user()
    login(client)
    assert client.post("/api/v1/support/tickets",
                       json={"subject": "   ", "body": "x"}).status_code == 422
    assert client.post("/api/v1/support/tickets",
                       json={"subject": "x", "body": "  \n "}).status_code == 422


def test_a_ticket_never_carries_a_credential_or_another_tenants_name(client):
    _tid, _other = _two_tenants()
    login(client, username="dana", email="dana@example.com")
    _raise(client)
    payload = client.get("/api/v1/support/tickets").json()
    blob = repr(payload)
    # The customer's own list never names other customers or their ids.
    assert "Miro Home Network Security" not in blob
    assert payload["tickets"][0]["tenant_id"] == ""
    assert payload["tickets"][0]["tenant_name"] == ""


# --------------------------------------------------------------------------
# Item 46: a ticket says what it is about
# --------------------------------------------------------------------------

def test_category_and_context_round_trip(client):
    """What the customer said the ticket was about survives to the operator.

    The label is stored rather than re-derived on read: the device it names
    may be gone by the time anyone opens the thread, and an id with no
    surviving row explains nothing to the person answering.
    """
    seed_user(username="miro", email="m@example.com", tenant_slug="home")
    login(client, "miro")
    r = client.post("/api/v1/support/tickets", json={
        "subject": "This camera was never flagged",
        "body": "It has been on the guest network for a week.",
        "category": "missed",
        "context_kind": "device", "context_id": "aa:bb:cc:dd:ee:ff",
        "context_label": "Hikvision camera · 192.168.1.64",
    })
    assert r.status_code == 200, r.text
    t = r.json()["ticket"]
    assert t["category"] == "missed"
    assert t["context_kind"] == "device"
    assert t["context_label"] == "Hikvision camera · 192.168.1.64"

    # And it is still there on the list read, which is the queue the
    # operator triages from.
    row = next(x for x in client.get("/api/v1/support/tickets").json()["tickets"]
               if x["id"] == t["id"])
    assert (row["category"], row["context_label"]) == ("missed", "Hikvision camera · 192.168.1.64")


def test_unknown_category_falls_back_rather_than_rejecting(client):
    """A taxonomy must never stop someone reporting a problem."""
    seed_user(username="miro", email="m@example.com", tenant_slug="home")
    login(client, "miro")
    r = client.post("/api/v1/support/tickets", json={
        "subject": "Something odd", "body": "Not sure how to classify this.",
        "category": "billing-ish",
    })
    assert r.status_code == 200
    assert r.json()["ticket"]["category"] == "other"


def test_incomplete_context_is_dropped_whole(client):
    """A kind with no label is an id nobody can read; a label with no kind is
    a sentence nobody can click. Neither half is stored on its own."""
    assert svc.clean_context("device", "aa:bb", "") == ("", "", "")
    assert svc.clean_context("", "aa:bb", "Camera") == ("", "", "")
    assert svc.clean_context("wormhole", "aa:bb", "Camera") == ("", "", "")
    assert svc.clean_context("device", "aa:bb", "Camera") == ("device", "aa:bb", "Camera")


def test_tickets_export_carries_the_ledger_not_the_prose(client):
    """The export is the ledger a client hands to an auditor. Message bodies
    stay out of it: a conversation in a spreadsheet cell is not evidence."""
    seed_user(username="miro", email="m@example.com", tenant_slug="home")
    login(client, "miro")
    _raise(client, subject="Sensor keeps dropping off",
           body="A whole paragraph the export must not contain.")
    text = client.get("/api/v1/export/tickets?fmt=csv").text
    assert "Sensor keeps dropping off" in text
    assert "A whole paragraph" not in text
    assert "ref,raised,subject,category,about" in text.replace("\r", "")
