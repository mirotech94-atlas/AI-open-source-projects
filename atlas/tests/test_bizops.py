"""BizOps (six agents phase 9): onboarding after a won/paid deal, Stripe
TEST-mode invoices, payment reminders, automation ideas and the digest.

Stripe is stubbed at `_http`: no test here reaches the network. A live key
is refused before any call; every Stripe call carries
`Idempotency-Key: bizops-inv-<id>-<step>`; the key never appears in a
result, a card or a log line."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from atlas.kernel.contracts import ActionResult, Health, Tier
from atlas.modules import _http, bizops
from atlas.modules.bizops import onboarding, repeats, store, stripe_invoice

TEST_KEY = "sk_test_51Abcdefghijklmnopqrstuvwxyz0123456789"
POSTAL = "1 Main St, New York, NY 10001"


def _returns(value):
    async def fake(*_a, **_kw):
        return value
    return fake


class Fake:
    """An in-memory bizops store; every function the package calls."""

    def __init__(self) -> None:
        self.deals: dict[int, list[dict[str, Any]]] = {}
        self.onboardings: dict[int, dict[str, Any]] = {}
        self.invoices: dict[int, dict[str, Any]] = {}
        self.reminders: dict[int, dict[str, Any]] = {}
        self.ideas: dict[int, dict[str, Any]] = {}
        self.texts: list[tuple[str, str]] = []
        self.agreements: set[int] = set()
        self.cards: list[tuple[str, dict[str, Any], str]] = []
        self.reports: dict[int, str] = {}

    async def lead_name(self, lead_id):
        return f"Bakery {lead_id}"

    async def first_closed_deal(self, lead_id):
        rows = [d for d in self.deals.get(lead_id, []) if d["status"] in ("won", "paid")]
        return rows[0] if rows else None

    async def deal_paid(self, lead_id):
        return any(d["status"] == "paid" for d in self.deals.get(lead_id, []))

    async def won_amount(self, lead_id):
        rows = [d for d in self.deals.get(lead_id, []) if d["status"] == "won"]
        return rows[-1].get("amount") if rows else None

    async def has_won(self, lead_id):
        return any(d["status"] == "won" for d in self.deals.get(lead_id, []))

    async def paid_invoice_for(self, lead_id):
        for i in self.invoices.values():
            if i["lead_id"] == lead_id and i["status"] == "paid":
                return i["id"]
        return None

    async def onboarding_for_ref(self, ref):
        for o in self.onboardings.values():
            if o["payment_ref"] == ref:
                return o
        return None

    async def insert_onboarding(self, **kw):
        if await self.onboarding_for_ref(kw["payment_ref"]) is not None:
            return None
        oid = len(self.onboardings) + 1
        self.onboardings[oid] = {"id": oid, "status": "drafted", "project_id": None,
                                 "history_tokens": [], **kw}
        return oid

    async def attach_project(self, oid, project_id, tokens):
        self.onboardings[oid].update(project_id=project_id, history_tokens=tokens)

    async def onboarding(self, oid):
        return self.onboardings.get(int(oid))

    async def set_onboarding(self, oid, status):
        self.onboardings[int(oid)]["status"] = status

    async def agreement_sent(self, lead_id):
        return lead_id in self.agreements

    async def open_invoice_for(self, lead_id):
        for i in self.invoices.values():
            if i["lead_id"] == lead_id and i["status"] in ("drafted", "sent"):
                return i["id"]
        return None

    async def insert_invoice(self, lead_id, amount, to_address, memo):
        assert amount in (300, 500)
        iid = len(self.invoices) + 1
        self.invoices[iid] = {"id": iid, "lead_id": lead_id, "amount": amount,
                              "to_address": to_address, "memo": memo,
                              "status": "drafted", "livemode": False,
                              "idempotency_key": f"bizops-inv-{iid}",
                              "stripe_invoice": None, "hosted_url": None,
                              "due_at": None, "sent_at": None}
        return iid

    async def invoice(self, iid):
        return self.invoices.get(int(iid))

    async def update_invoice(self, iid, **fields):
        self.invoices[int(iid)].update(fields)

    async def open_invoices(self):
        return [i for i in self.invoices.values() if i["status"] == "sent"]

    async def overdue_invoices(self):
        now = datetime.now(UTC)
        return [i for i in self.invoices.values()
                if i["status"] == "sent" and i["due_at"] and i["due_at"] < now]

    async def last_reminder_at(self, invoice_id, *, sent_only):
        times = [r["at"] for r in self.reminders.values()
                 if r["invoice_id"] == invoice_id and r["status"] != "withdrawn"
                 and (r["status"] == "sent" or not sent_only)]
        return max(times) if times else None

    async def insert_reminder(self, invoice_id, to_address, subject, body):
        rid = len(self.reminders) + 1
        self.reminders[rid] = {"id": rid, "invoice_id": invoice_id, "at": datetime.now(UTC),
                               "to_address": to_address, "subject": subject,
                               "body": body, "status": "drafted"}
        return rid

    async def reminder(self, rid):
        return self.reminders.get(int(rid))

    async def set_reminder(self, rid, status):
        self.reminders[int(rid)]["status"] = status

    async def recent_texts(self, days):
        return list(self.texts)

    async def upsert_idea(self, pattern, samples, count):
        for i in self.ideas.values():
            if i["pattern"] == pattern:
                i.update(count=count, samples=samples)
                return i["id"], False
        iid = len(self.ideas) + 1
        self.ideas[iid] = {"id": iid, "pattern": pattern, "samples": samples,
                           "count": count, "status": "new"}
        return iid, True

    async def idea(self, iid):
        return self.ideas.get(int(iid))

    async def set_idea(self, iid, status, task_id=None, task_token=None):
        self.ideas[int(iid)].update(status=status, task_id=task_id, task_token=task_token)

    async def set_ideas_status(self, ids, status):
        for i in ids:
            self.ideas[int(i)]["status"] = status

    async def leads_to_onboard(self):
        return [lead for lead in self.deals
                if not any(o["lead_id"] == lead for o in self.onboardings.values())]

    async def stalled_onboardings(self, days):
        return 0

    async def digest_reads(self):
        return {"open": 1, "open_total": 500, "overdue": 0, "paid_day": 0,
                "onboard_waiting": 1, "ideas_new": 0}

    async def insert_report(self, title, body, sections):
        rid = len(self.reports) + 1
        self.reports[rid] = body
        return rid

    async def withdraw_report(self, rid):
        self.reports.pop(int(rid), None)

    async def raise_card(self, action, params, reason, client, text, dedup):
        self.cards.append((action, params, f"{reason}\n{text}"))
        return True


@pytest.fixture
def fake(monkeypatch):
    f = Fake()
    for name in dir(Fake):
        if not name.startswith("_"):
            monkeypatch.setattr(store, name, getattr(f, name))
    monkeypatch.setenv("AGENCY_POSTAL_ADDRESS", POSTAL)
    monkeypatch.delenv(stripe_invoice.KEY_ENV, raising=False)
    from atlas.modules.agency import meetings, pricing, replies
    monkeypatch.setattr(replies, "_recipient",
                        _returns(("owner@bakery.example", None)))
    monkeypatch.setattr(meetings, "free_times", _returns((
        [datetime(2026, 11, 3, 15, tzinfo=UTC), datetime(2026, 11, 4, 16, tzinfo=UTC)],
        None)))

    class Terms:
        price = 500
    monkeypatch.setattr(pricing, "terms", _returns(Terms()))
    from atlas.modules.projects import store as pstore
    board: dict[str, list] = {"projects": [], "tasks": [], "restored": []}

    async def create_project(client_lead_id, client_name, name):
        board["projects"].append(name)
        return len(board["projects"]), f"p{len(board['projects'])}"

    async def add_task(**kw):
        board["tasks"].append(kw)
        return len(board["tasks"]), f"t{len(board['tasks'])}"

    async def restore(token):
        board["restored"].append(token)
        return token

    monkeypatch.setattr(pstore, "create_project", create_project)
    monkeypatch.setattr(pstore, "add_task", add_task)
    monkeypatch.setattr(pstore, "restore", restore)
    f.board = board
    return f


class StubStripe:
    """Records every call; answers like Stripe test mode."""

    def __init__(self, *, invoice_status: str = "open", livemode: bool = False) -> None:
        self.calls: list[tuple[str, str, dict[str, str], dict | None]] = []
        self.invoice_status = invoice_status
        self.livemode = livemode

    async def get(self, url, *, headers=None, params=None, **_kw):
        self.calls.append(("GET", url, dict(headers or {}), params))
        if url.endswith("/customers"):
            return {"object": "list", "data": []}, None
        return {"object": "invoice", "id": "in_1", "status": self.invoice_status,
                "livemode": self.livemode}, None

    async def post(self, url, *, headers=None, data=None, **_kw):
        self.calls.append(("POST", url, dict(headers or {}), data))
        if url.endswith("/customers"):
            return {"object": "customer", "id": "cus_1", "livemode": self.livemode}, None
        if url.endswith("/invoiceitems"):
            return {"object": "invoiceitem", "id": "ii_1", "livemode": self.livemode}, None
        status = ("open" if url.endswith(("/finalize", "/send"))
                  else "void" if url.endswith("/void") else "draft")
        return {"object": "invoice", "id": "in_1", "status": status,
                "livemode": self.livemode, "due_date": 1_800_000_000,
                "hosted_invoice_url": "https://invoice.stripe.com/i/test_1"}, None


@pytest.fixture
def stripe(monkeypatch):
    s = StubStripe()
    monkeypatch.setattr(_http, "get_json", s.get)
    monkeypatch.setattr(_http, "post_json", s.post)
    return s


@pytest.fixture
def no_http(monkeypatch):
    import httpx

    class Boom:
        def __init__(self, *a, **kw):
            raise AssertionError("no HTTP may happen here")
    monkeypatch.setattr(httpx, "AsyncClient", Boom)


def _acts():
    return {a.name: a for a in bizops.MODULE.actions()}


# ------------------------------------------------------------- the catalog

def test_sends_are_tier_2_card_only_and_qa_gated():
    from atlas.kernel import qa_gate
    acts = _acts()
    for name in ("bizops.send_onboarding_email", "bizops.send_invoice",
                 "bizops.send_payment_reminder"):
        a = acts[name]
        assert a.tier is Tier.ASK and a.card_only, name
        assert name in qa_gate.GATED_NAMES and name in qa_gate.RESOLVERS, name
    assert acts["bizops.propose_automation"].tier is Tier.ASK
    for name in ("bizops.start_onboarding", "bizops.draft_invoice",
                 "bizops.draft_payment_reminder", "bizops.find_repeats",
                 "bizops.daily_digest"):
        assert acts[name].tier is Tier.AUTO and acts[name].rollback, name


def test_the_invoice_amount_is_held_in_the_schema():
    sql = Path("db/schema.sql").read_text()
    assert "amount          INTEGER NOT NULL CHECK (amount IN (300, 500))" in sql
    assert "payment_ref    TEXT NOT NULL UNIQUE" in sql
    assert "livemode        BOOLEAN NOT NULL DEFAULT false CHECK (livemode = false)" in sql
    assert "idempotency_key TEXT NOT NULL UNIQUE" in sql


# -------------------------------------------------------------- onboarding

async def test_a_won_deal_onboards_once_with_five_tasks_and_one_card(fake):
    fake.deals[1] = [{"id": 7, "status": "won", "amount": 500}]
    got = await onboarding.start_onboarding(1)
    assert got.ok, got.detail
    assert len(fake.onboardings) == 1 and fake.onboardings[1]["payment_ref"] == "deal:7"
    assert len(fake.board["projects"]) == 1
    titles = " | ".join(t["title"].lower() for t in fake.board["tasks"])
    for word in ("intake", "kickoff", "build", "qa", "launch"):
        assert word in titles, word
    assert len(fake.board["tasks"]) == 5
    assert [c[0] for c in fake.cards] == ["bizops.send_onboarding_email"]
    body = fake.onboardings[1]["body"]
    assert POSTAL in body and "STOP" in body and "Nov" in body
    again = await onboarding.start_onboarding(1)
    assert again.ok and "already" in again.detail
    assert len(fake.onboardings) == 1 and len(fake.board["projects"]) == 1
    assert len(fake.board["tasks"]) == 5 and len(fake.cards) == 1


async def test_no_won_or_paid_deal_means_no_onboarding(fake):
    fake.deals[2] = [{"id": 8, "status": "lost", "amount": None}]
    got = await onboarding.start_onboarding(2)
    assert not got.ok and fake.onboardings == {} and fake.board["projects"] == []


async def test_onboarding_rollback_drops_it_and_restores_the_board(fake):
    fake.deals[1] = [{"id": 7, "status": "paid", "amount": 500}]
    got = await onboarding.start_onboarding(1)
    undone = await onboarding.drop_onboarding(got.rollback_token)
    assert undone.ok and fake.onboardings[1]["status"] == "dropped"
    assert fake.board["restored"] == ["p1", "t1", "t2", "t3", "t4", "t5"]


async def test_the_onboarding_email_sends_once(fake, monkeypatch):
    from atlas.kernel import idempotency
    from atlas.modules.agency import outreach, replies
    fake.deals[1] = [{"id": 7, "status": "won", "amount": 500}]
    await onboarding.start_onboarding(1)
    monkeypatch.setattr(replies, "_email_problem", lambda *_a: None)
    sent: list[str] = []

    async def smtp(frm, to, subject, body, message_id=None):
        sent.append(to)
        return True, None

    states = iter([idempotency.CLAIMED, idempotency.SENT])
    monkeypatch.setattr(idempotency, "claim", lambda *_a: _returns(next(states))())
    monkeypatch.setattr(idempotency, "finish", _returns(None))
    monkeypatch.setattr(outreach, "_smtp_send", smtp)
    monkeypatch.setattr(outreach, "_send_from_address", lambda: "owner@example.com")
    run = _acts()["bizops.send_onboarding_email"].run
    got = await run(onboarding_id=1)
    assert got.ok and sent == ["owner@bakery.example"]
    assert fake.onboardings[1]["status"] == "sent"
    fake.onboardings[1]["status"] = "drafted"
    again = await run(onboarding_id=1)
    assert not again.ok and sent == ["owner@bakery.example"]


# ---------------------------------------------------------------- invoices

async def test_an_amount_outside_the_two_prices_is_impossible(fake, monkeypatch):
    from atlas.modules.agency import pricing

    class Odd:
        price = 450
    monkeypatch.setattr(pricing, "terms", _returns(Odd()))
    fake.deals[1] = [{"id": 7, "status": "won", "amount": 999}]
    got = await stripe_invoice.draft_invoice(1)
    assert not got.ok and fake.invoices == {} and fake.cards == []


async def test_the_won_deal_amount_wins_over_the_quote(fake):
    fake.deals[1] = [{"id": 7, "status": "won", "amount": 300}]
    got = await stripe_invoice.draft_invoice(1)
    assert got.ok and fake.invoices[1]["amount"] == 300
    action, params, text = fake.cards[0]
    assert action == "bizops.send_invoice" and params == {"invoice_id": 1}
    assert "TEST MODE" in text


async def test_a_sent_agreement_or_a_paid_deal_means_no_invoice(fake):
    fake.deals[1] = [{"id": 7, "status": "won", "amount": 500}]
    fake.agreements.add(1)
    got = await stripe_invoice.draft_invoice(1)
    assert not got.ok and "agreement" in got.detail and fake.invoices == {}
    fake.agreements.clear()
    fake.deals[1].append({"id": 9, "status": "paid", "amount": 500})
    got = await stripe_invoice.draft_invoice(1)
    assert not got.ok and "paid" in got.detail and fake.invoices == {}


async def test_without_a_key_the_send_is_refused_and_nothing_is_called(fake, no_http):
    fake.deals[1] = [{"id": 7, "status": "won", "amount": 500}]
    await stripe_invoice.draft_invoice(1)
    acts = _acts()
    dry = await acts["bizops.send_invoice"].dry_run(invoice_id=1)
    run = await acts["bizops.send_invoice"].run(invoice_id=1)
    for got in (dry, run):
        assert not got.ok and stripe_invoice.KEY_ENV in got.detail
    assert fake.invoices[1]["status"] == "drafted"


@pytest.mark.parametrize("prefix", ["sk_live_", "rk_live_"])
async def test_a_live_key_is_refused_in_the_dry_run(fake, no_http, monkeypatch, prefix):
    live = prefix + "51Zyxwvutsrqponmlkjihgfedcba"
    monkeypatch.setenv(stripe_invoice.KEY_ENV, live)
    fake.deals[1] = [{"id": 7, "status": "won", "amount": 500}]
    await stripe_invoice.draft_invoice(1)
    dry = await _acts()["bizops.send_invoice"].dry_run(invoice_id=1)
    assert not dry.ok and "live keys are not enabled in this build" in dry.detail
    assert live not in repr(dry)
    obs = await bizops.check_stripe_key()
    assert obs.health is Health.FAILING and live not in obs.detail


async def test_a_test_invoice_goes_create_finalize_send_then_polled_paid(
        fake, stripe, monkeypatch, caplog):
    from atlas.kernel import idempotency
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv(stripe_invoice.KEY_ENV, TEST_KEY)
    monkeypatch.setattr(idempotency, "claim", _returns(idempotency.CLAIMED))
    monkeypatch.setattr(idempotency, "finish", _returns(None))
    fake.deals[1] = [{"id": 7, "status": "won", "amount": 500}]
    drafted = await stripe_invoice.draft_invoice(1)
    got = await _acts()["bizops.send_invoice"].run(invoice_id=1)
    assert got.ok, got.detail
    paths = [(m, u.removeprefix(stripe_invoice.API)) for m, u, _h, _d in stripe.calls]
    assert paths == [("GET", "/customers"), ("POST", "/customers"),
                     ("POST", "/invoices"), ("POST", "/invoiceitems"),
                     ("POST", "/invoices/in_1/finalize"), ("POST", "/invoices/in_1/send")]
    keys = [h["Idempotency-Key"] for _m, _u, h, _d in stripe.calls]
    assert all(k.startswith("bizops-inv-1-") for k in keys) and len(set(keys)) == len(keys)
    item = stripe.calls[3][3]
    assert item["invoice"] == "in_1" and item["amount"] == 50000
    row = fake.invoices[1]
    assert row["status"] == "sent" and row["livemode"] is False
    assert row["hosted_url"].startswith("https://invoice.stripe.com/")
    stripe.invoice_status = "paid"
    obs = await stripe_invoice.check_invoice_status()
    assert obs.health is Health.OK and fake.invoices[1]["status"] == "paid"
    leak = repr([drafted, got, obs, fake.cards]) + caplog.text
    assert TEST_KEY not in leak


async def test_a_live_mode_answer_is_refused(fake, stripe, monkeypatch):
    from atlas.kernel import idempotency
    monkeypatch.setenv(stripe_invoice.KEY_ENV, TEST_KEY)
    monkeypatch.setattr(idempotency, "claim", _returns(idempotency.CLAIMED))
    monkeypatch.setattr(idempotency, "finish", _returns(None))
    stripe.livemode = True
    fake.deals[1] = [{"id": 7, "status": "won", "amount": 500}]
    await stripe_invoice.draft_invoice(1)
    got = await _acts()["bizops.send_invoice"].run(invoice_id=1)
    assert not got.ok and "live mode" in got.detail
    assert fake.invoices[1]["status"] != "sent"
    assert not [c for c in stripe.calls if "/invoices" in c[1]]


async def test_void_only_when_unpaid(fake, stripe, monkeypatch):
    monkeypatch.setenv(stripe_invoice.KEY_ENV, TEST_KEY)
    fake.deals[1] = [{"id": 7, "status": "won", "amount": 500}]
    await stripe_invoice.draft_invoice(1)
    fake.invoices[1].update(status="sent", stripe_invoice="in_1")
    stripe.invoice_status = "paid"
    got = await stripe_invoice.void_invoice("1")
    assert not got.ok and "paid" in got.detail
    assert not [c for c in stripe.calls if c[1].endswith("/void")]
    stripe.invoice_status = "open"
    got = await stripe_invoice.void_invoice("1")
    assert got.ok and fake.invoices[1]["status"] == "void"
    voids = [c for c in stripe.calls if c[1].endswith("/void")]
    assert len(voids) == 1 and voids[0][2]["Idempotency-Key"] == "bizops-inv-1-void"


async def test_the_key_check(monkeypatch):
    monkeypatch.delenv(stripe_invoice.KEY_ENV, raising=False)
    assert (await bizops.check_stripe_key()).health is Health.UNKNOWN
    monkeypatch.setenv(stripe_invoice.KEY_ENV, TEST_KEY)
    obs = await bizops.check_stripe_key()
    assert obs.health is Health.OK and TEST_KEY not in obs.detail


async def test_the_invoice_poll_is_unknown_without_a_key(fake, no_http):
    obs = await stripe_invoice.check_invoice_status()
    assert obs.health is Health.UNKNOWN


# --------------------------------------------------------------- reminders

async def test_one_reminder_per_seven_days(fake):
    fake.deals[1] = [{"id": 7, "status": "won", "amount": 500}]
    await stripe_invoice.draft_invoice(1)
    fake.invoices[1].update(status="sent", hosted_url="https://invoice.stripe.com/i/x",
                            due_at=datetime.now(UTC) - timedelta(days=2))
    first = await stripe_invoice.draft_payment_reminder(1)
    assert first.ok and fake.cards[-1][0] == "bizops.send_payment_reminder"
    second = await stripe_invoice.draft_payment_reminder(1)
    assert not second.ok and "7 days" in second.detail
    fake.reminders[1]["at"] -= timedelta(days=8)
    assert (await stripe_invoice.draft_payment_reminder(1)).ok


async def test_a_paid_invoice_gets_no_reminder(fake):
    fake.deals[1] = [{"id": 7, "status": "won", "amount": 500}]
    await stripe_invoice.draft_invoice(1)
    fake.invoices[1]["status"] = "paid"
    got = await stripe_invoice.draft_payment_reminder(1)
    assert not got.ok and fake.reminders == {}


# ------------------------------------------------------------ automation

async def test_an_idea_needs_three_matches(fake):
    fake.texts = [("mail_followups", "Re: Invoice copy request 12"),
                  ("mail_followups", "invoice copy request 14")]
    got = await repeats.find_repeats()
    assert got.ok and fake.ideas == {} and fake.cards == []
    fake.texts.append(("project_tasks", "Fwd: Invoice copy request #15"))
    got = await repeats.find_repeats()
    assert got.ok and len(fake.ideas) == 1
    idea = fake.ideas[1]
    assert idea["count"] == 3 and all("UNTRUSTED" in s.upper() for s in idea["samples"])
    assert [c[0] for c in fake.cards] == ["bizops.propose_automation"]


async def test_approving_an_idea_writes_a_build_task_for_you(fake):
    fake.ideas[1] = {"id": 1, "pattern": "invoice copy request", "samples": [],
                     "count": 3, "status": "new"}
    got = await repeats.propose_automation(1)
    assert got.ok and fake.ideas[1]["status"] == "proposed"
    task = fake.board["tasks"][0]
    assert task["kind"] == "build" and task["owner"] == "you"
    await repeats.undo_proposal(got.rollback_token)
    assert fake.ideas[1]["status"] == "new" and fake.board["restored"] == ["t1"]


# ------------------------------------------------------------------ digest

async def test_the_digest_has_no_figure_without_a_read_time(fake, monkeypatch):
    lines = await bizops.digest_lines(datetime(2026, 11, 2, 13, tzinfo=UTC))
    for line in lines:
        if any(ch.isdigit() for ch in line):
            assert "read 2026-11-02 13:00 UTC" in line or "UNKNOWN" in line, line

    async def boom():
        raise OSError("db down")
    monkeypatch.setattr(store, "digest_reads", boom)
    lines = await bizops.digest_lines(datetime(2026, 11, 2, 13, tzinfo=UTC))
    assert lines[0].startswith("UNKNOWN") and "OSError" in lines[0]


async def test_the_daily_job_acts_only_through_the_executor(fake, monkeypatch):
    from atlas.kernel import executor
    called: list[tuple[str, dict]] = []

    async def execute(req):
        called.append((req.action, req.params))
        return ActionResult(False, "watch-only")

    monkeypatch.setattr(executor, "execute", execute)
    fake.deals[4] = [{"id": 11, "status": "won", "amount": 500}]
    fake.invoices[1] = {"id": 1, "lead_id": 3, "status": "sent",
                        "due_at": datetime.now(UTC) - timedelta(days=1)}
    await bizops.run_daily()
    assert called == [("bizops.start_onboarding", {"lead_id": 4}),
                      ("bizops.draft_payment_reminder", {"invoice_id": 1}),
                      ("bizops.daily_digest", {})]
    called.clear()
    await bizops.run_find_repeats()
    assert called == [("bizops.find_repeats", {})]


# ----------------------------------------------------------------- wiring

def test_bizops_sits_in_sales_with_its_jobs_and_owner():
    from atlas.kernel import departments, missions, scheduler
    dept = {d.key: d for d in departments.DEPARTMENTS}["sales"]
    assert "bizops" in dept.agents and "bizops" in dept.modules
    assert {"bizops_digest", "bizops_find_repeats"} <= set(dept.jobs)
    assert {"bizops_digest", "bizops_find_repeats"} <= set(departments.JOBS)
    for ask in ("send the client an invoice", "start onboarding for the bakery",
                "any automation ideas from bizops"):
        assert missions.owner_for(ask).agent == "bizops", ask
    for job in ("bizops_digest", "bizops_find_repeats"):
        assert job in scheduler.POLICIES and job in scheduler._DETACHED
    windows = scheduler._bizops_digest_windows(datetime(2026, 11, 3, 15, tzinfo=UTC),
                                               scheduler.POLICIES["bizops_digest"].horizon)
    at = windows[-1].scheduled_for
    assert at.hour == 8 and str(at.tzinfo) == "America/New_York"


def test_the_roster_has_bizops_with_read_only_tools():
    from atlas.kernel.agents import ROSTER, build_roster
    from atlas.kernel.tools import TOOLS
    from atlas.tools.builtin import register_all
    register_all()
    build_roster()
    agent = ROSTER["bizops"]
    assert all(TOOLS[t].action is None for t in agent.tools if t in TOOLS)
