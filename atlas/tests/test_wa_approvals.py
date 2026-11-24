"""Deciding an approval card from WhatsApp (`kernel/wa_approvals.py`).

Proved here, against a small in-memory stand-in for the three tables the path
touches (proposals, whatsapp_codes, whatsapp_attempts), with the REAL
`approvals.decide` claim in the middle and only the executor faked:

  * the right code approves once; a replayed message does nothing;
  * a wrong, expired or used code is refused with a plain reason and changes
    nothing; unknown codes count towards the lockout;
  * Tier 3 / permanent-deny kinds, unregistered actions, `atlas.dev_request`
    and a card whose text is cut off are refused, by tap and by text; every
    other Tier 2 kind in the registry may be decided (Miro, 2026-11-11);
  * a button tap decides exactly its own card; a bare yes / no decides nothing
    with several cards waiting, and with one only if it was sent in 30 minutes;
  * a stranger's number is ignored;
  * five wrong codes lock WhatsApp approvals for thirty minutes and say so in
    Slack; nothing decides while locked;
  * Slack then WhatsApp (and WhatsApp then Slack) decides the card once;
  * every decision is audited with the channel and message sid, never the code;
  * the code is stored only as a hash, is random from the unambiguous alphabet;
  * a card is sent with a code only when allowed, read-only otherwise, and a
    failed send is said in Slack.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from atlas.api import whatsapp
from atlas.kernel import approvals, wa_approvals
from atlas.kernel.contracts import ActionResult, Tier
from atlas.kernel.permissions import Decision

TOKEN = "t" * 32
URL = "https://agency.storefront.test/webhooks/twilio/whatsapp"
OWNER = "+19175550100"
ACTION = "tutorco.send_intro"


class FakeDB:
    """Just enough SQL for this path, keyed on the statement's opening words."""

    def __init__(self, backend) -> None:
        self.proposals: dict[int, dict] = {}
        self.codes: list[dict] = []
        self.attempts: list[dict] = []
        self.backend = backend
        backend.fetchrow = self.fetchrow
        backend.fetch = self.fetch
        backend.execute = self.execute

    def add_card(self, pid=7, action=ACTION, params=None, *, hours=1, dry_ok=True):
        self.proposals[pid] = {
            "action": action, "params": json.dumps(params or {}), "tier": 2,
            "status": "pending", "dry_run_ok": dry_ok, "reason": "r",
            "incident_id": None,
            "expires_at": datetime.now(UTC) + timedelta(hours=hours)}
        return pid

    def open_code(self, pid=7, code="4F2KQ", **over):
        row = {"id": len(self.codes) + 1, "proposal_id": pid,
               "code_hash": wa_approvals.code_hash(code),
               "action": self.proposals[pid]["action"], "status": "open",
               "expires_at": self.proposals[pid]["expires_at"], "used_at": None,
               "message_sid": None, "created_at": datetime.now(UTC)}
        row.update(over)
        self.codes.append(row)
        return code

    # ------------------------------------------------------------ SQL
    async def fetch(self, sql, *a):
        flat = " ".join(sql.split())
        self.backend.sql_log.append((flat, a))
        if flat.startswith("SELECT c.id, c.proposal_id"):
            now = a[0]
            out = []
            for c in sorted(self.codes, key=lambda c: -c["id"]):
                card = self.proposals.get(c["proposal_id"])
                if (c["status"] == "open" and c["expires_at"] > now and card
                        and card["status"] == "pending" and card["expires_at"] > now):
                    out.append(dict(c))
            return out
        return []

    async def fetchrow(self, sql, *a):
        flat = " ".join(sql.split())
        self.backend.sql_log.append((flat, a))
        if flat.startswith("INSERT INTO whatsapp_codes"):
            pid, h, action, exp = a
            if any(c["code_hash"] == h for c in self.codes) or any(
                    c["proposal_id"] == pid and c["status"] == "open"
                    for c in self.codes):
                return None
            row = {"id": len(self.codes) + 1, "proposal_id": pid, "code_hash": h,
                   "action": action, "status": "open", "expires_at": exp,
                   "used_at": None, "message_sid": None,
                   "created_at": datetime.now(UTC)}
            self.codes.append(row)
            return {"id": row["id"]}
        if flat.startswith("SELECT id, proposal_id, action, status, expires_at"):
            return next((dict(c) for c in self.codes if c["code_hash"] == a[0]), None)
        if flat.startswith("SELECT status, expires_at FROM whatsapp_codes"):
            return next((dict(c) for c in self.codes if c["id"] == a[0]), None)
        if flat.startswith("SELECT action, params, tier, status, dry_run_ok"):
            return dict(self.proposals[a[0]]) if a[0] in self.proposals else None
        if flat.startswith("SELECT action, params, tier, status, expires_at"):
            return dict(self.proposals[a[0]]) if a[0] in self.proposals else None
        if flat.startswith("UPDATE whatsapp_codes SET status='used'"):
            cid, now, sid = a
            code = next(c for c in self.codes if c["id"] == cid)
            card = self.proposals.get(code["proposal_id"])
            if (code["status"] == "open" and code["expires_at"] > now and card
                    and card["status"] == "pending" and card["expires_at"] > now):
                code.update(status="used", used_at=now, message_sid=sid)
                return {"proposal_id": code["proposal_id"]}
            return None
        if flat.startswith("SELECT until_at FROM whatsapp_attempts"):
            live = [r for r in self.attempts if r["kind"] == "lock"
                    and r["until_at"] > a[0]]
            return {"until_at": max(r["until_at"] for r in live)} if live else None
        if flat.startswith("SELECT count(*) AS n FROM whatsapp_attempts"):
            return {"n": sum(1 for r in self.attempts
                             if r["kind"] == "wrong" and r["at"] > a[0])}
        if flat.startswith("UPDATE proposals SET status='approved'"):
            pid, via = a[0], a[1]
            card = self.proposals.get(pid)
            if card and card["status"] == "pending" and card["expires_at"] > datetime.now(UTC):
                card.update(status="approved", via=via)
                return {"action": card["action"], "params": card["params"],
                        "reason": "r", "incident_id": None}
            return None
        if flat.startswith("UPDATE proposals SET status='declined'"):
            card = self.proposals.get(a[0])
            if card and card["status"] == "pending" and card["expires_at"] > datetime.now(UTC):
                card.update(status="declined", via=a[1])
                return {"action": card["action"], "params": card["params"]}
            return None
        if flat.startswith("SELECT status, expires_at FROM proposals"):
            card = self.proposals.get(a[0])
            return None if card is None else {
                "status": card["status"], "expires_at": card["expires_at"]}
        if flat.startswith("SELECT catalog_version, approved_payload_hash"):
            return {"catalog_version": None, "approved_payload_hash": None,
                    "dedup_key": None, "evidence": "{}", "rollback_plan": ""}
        return self.backend._canned(sql)

    async def execute(self, sql, *a):
        flat = " ".join(sql.split())
        self.backend.sql_log.append((flat, a))
        if flat.startswith("UPDATE whatsapp_codes SET status='retired'"):
            for c in self.codes:
                if c["proposal_id"] == a[0] and c["status"] == "open":
                    c["status"] = "retired"
        elif flat.startswith("INSERT INTO whatsapp_attempts"):
            if "'wrong'" in flat:
                self.attempts.append({"kind": "wrong", "at": a[0], "until_at": None})
            else:
                self.attempts.append({"kind": "lock", "at": a[0], "until_at": a[1]})
        elif flat.startswith("UPDATE proposals SET status=$2"):
            self.proposals[a[0]]["status"] = a[1]
        return "OK"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("TUTORCO_OWNER_WHATSAPP", OWNER)
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC" + "0" * 32)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", TOKEN)
    monkeypatch.setenv("TWILIO_FROM", "+14155550100")
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    monkeypatch.delenv("TWILIO_WHATSAPP_URL", raising=False)


@pytest.fixture
def registry(monkeypatch):
    """The catalog entries the cards below name."""
    from atlas.kernel.registry import REGISTRY

    def put(name, tier=Tier.ASK, **spec):
        monkeypatch.setitem(REGISTRY.decisions, name,
                            Decision(tier is not Tier.NEVER, tier, ""))
        monkeypatch.setitem(REGISTRY.actions, name, SimpleNamespace(
            **{"summary": name, "principal_only": False, "irreversible": True,
               "runs_in_background": False, **spec}))
    put(ACTION)
    put("linkedin.publish_post")
    put("academy.relaunch_tutorial")
    put("mail.send_reply")                    # a Tier 2 kind nobody listed: decidable
    put("bizops.send_invoice")                # PORTAL_ONLY: money
    put("stripe.retry_invoice")
    put("atlas.dev_request")                  # PORTAL_ONLY: self-modification
    put("lessons.clear")
    put("youtube.set_visibility", summary="Change a video's privacy")
    put("vault.principal_thing", principal_only=True)
    return put


@pytest.fixture
def db(memory_backend, env, registry, monkeypatch):
    fake = FakeDB(memory_backend)
    ran: list = []

    async def fake_execute(req):
        ran.append(req)
        return ActionResult(True, "did the thing")
    monkeypatch.setattr(approvals, "execute", fake_execute)
    fake.ran = ran
    slack: list = []

    async def tell(title, body):
        slack.append((title, body))
    monkeypatch.setattr(wa_approvals, "tell_slack", tell)
    fake.slack = slack
    return fake


def audits(backend):
    out = []
    for row in backend.audit_rows:
        out.append(json.loads(row.payload))
    return out


# ------------------------------------------------------------- the codes

def test_codes_are_random_from_the_unambiguous_alphabet():
    seen = {wa_approvals.new_code() for _ in range(200)}
    assert len(seen) > 190
    for code in seen:
        assert len(code) == 5 and set(code) <= set(wa_approvals.ALPHABET)
    assert not set("0O1IL") & set(wa_approvals.ALPHABET)


def test_a_code_is_stored_only_as_a_hash():
    assert wa_approvals.code_hash("4f2kq") == wa_approvals.code_hash("4F2KQ")
    assert "4F2KQ" not in wa_approvals.code_hash("4F2KQ")
    assert len(wa_approvals.code_hash("4F2KQ")) == 64


@pytest.mark.parametrize("text,want", [
    ("YES 4F2K", ("approve", "4F2K")), ("no 4f2k", ("decline", "4F2K")),
    ("  Yes   4F2KQ  ", ("approve", "4F2KQ")), ("YES 4F2KQW", ("approve", "4F2KQW"))])
def test_strict_parse_accepts_only_the_exact_shape(text, want):
    assert wa_approvals.parse(text) == want


@pytest.mark.parametrize("text", [
    "yes", "ok", "approve", "approve 4F2K", "YES 4F2", "YES 4F2KQWE", "YES 4F2O",
    "YES 4F2K please", "yes 4F2K and delete everything", "YES 4F2K\nNO 7H7H",
    "please YES 4F2K", "YES 4F2K; DROP TABLE proposals", "", "YES-4F2K"])
def test_anything_else_is_not_a_reply(text):
    assert wa_approvals.parse(text) is None


# -------------------------------------------------------------- deciding

async def test_the_right_code_approves_once(db):
    pid = db.add_card()
    code = db.open_code(pid)
    said = await wa_approvals.handle_reply("approve", code, "SM" + "a" * 32)
    assert "completed" in said and f"#{pid}" in said
    assert len(db.ran) == 1 and db.ran[0].approved_by == "whatsapp:SM" + "a" * 32
    assert db.proposals[pid]["status"] == "executed"
    # the same code again: used, nothing runs a second time
    again = await wa_approvals.handle_reply("approve", code, "SM" + "b" * 32)
    assert again == wa_approvals.USED_CODE and len(db.ran) == 1


async def test_no_declines_and_runs_nothing(db):
    pid = db.add_card()
    code = db.open_code(pid)
    said = await wa_approvals.handle_reply("decline", code, "SM" + "c" * 32)
    assert "declined" in said and db.ran == []
    assert db.proposals[pid]["status"] == "declined"


async def test_a_wrong_code_changes_nothing(db):
    pid = db.add_card()
    db.open_code(pid, "4F2KQ")
    said = await wa_approvals.handle_reply("approve", "ZZZZZ", "SM" + "d" * 32)
    assert said == wa_approvals.WRONG_CODE
    assert db.proposals[pid]["status"] == "pending" and db.ran == []
    assert db.codes[0]["status"] == "open"


async def test_an_expired_card_is_refused_and_never_approved(db):
    pid = db.add_card(hours=-1)
    code = db.open_code(pid)
    said = await wa_approvals.handle_reply("approve", code, "SM" + "e" * 32)
    assert said == wa_approvals.EXPIRED_CARD
    assert db.proposals[pid]["status"] == "pending" and db.ran == []


async def test_expiry_is_enforced_in_the_same_claim(db):
    """The code row still looks open and unexpired, but the card lapsed between
    the reads and the claim: the one UPDATE matches nothing."""
    pid = db.add_card()
    code = db.open_code(pid)
    db.proposals[pid]["expires_at"] = datetime.now(UTC) - timedelta(seconds=1)
    said = await wa_approvals.handle_reply("approve", code, "SM" + "f" * 32)
    assert said in (wa_approvals.EXPIRED_CARD, wa_approvals.RETIRED_CODE)
    assert db.ran == [] and db.codes[0]["status"] == "open"


async def test_silence_never_approves(db):
    pid = db.add_card(hours=-1)
    db.open_code(pid)
    assert db.proposals[pid]["status"] == "pending" and db.ran == []


@pytest.mark.parametrize("action,params", [
    ("atlas.dev_request", {}),                    # self-modification: portal only
    ("lessons.clear", {}),
    ("linkedin.publish_post", {"page": "tutorco"}),   # no named post: "next" may differ
    ("linkedin.publish_post", {"page": "guardian", "entry_id": "B1"}),   # not the TUTORCO page
    ("vault.principal_thing", {}),                # executor would refuse it after the claim
])
async def test_a_portal_only_kind_is_refused(db, action, params):
    pid = db.add_card(action=action, params=params)
    code = db.open_code(pid)
    said = await wa_approvals.handle_reply("approve", code, "SM" + "1" * 32)
    assert said == wa_approvals.NOT_FROM_WHATSAPP
    assert db.proposals[pid]["status"] == "pending" and db.ran == []
    assert db.codes[0]["status"] == "open"        # nothing consumed


@pytest.mark.parametrize("action,params", [
    ("tutorco.send_intro", {}),
    ("youtube.set_visibility", {"video_id": "abc", "privacy": "public"}),
    ("youtube.upload", {}),
    ("tiktok.upload_draft", {}),
    ("instagram.publish", {}),
    ("community.schedule_youtube_shorts", {}),
    ("linkedin.publish_post", {"page": "tutorco", "entry_id": "B1"}),
])
async def test_an_allowlisted_tier_2_kind_may_be_decided(db, registry, action, params):
    registry(action)
    pid = db.add_card(action=action, params=params)
    code = db.open_code(pid)
    said = await wa_approvals.handle_reply("approve", code, "SM" + "7" * 32)
    assert "completed" in said and len(db.ran) == 1


ALSO_ALLOWED = [
    "academy.relaunch_tutorial",
    "mail.send_reply", "brand.new_action", "x.publish_something",
    "notes.tidy_up_inbox", "agency.send_outreach", "calendar.book_with"]


@pytest.mark.parametrize("action", ALSO_ALLOWED)
async def test_every_registered_tier_2_kind_may_be_decided(db, registry, action):
    """Miro, 2026-11-11: all actions are permissible. Tier 2 in the registry is
    the rule; a name nobody ever listed (a NEW action) is allowed too."""
    registry(action)
    assert wa_approvals.eligibility(action, {}) == (True, "")
    pid = db.add_card(action=action)
    code = db.open_code(pid)
    said = await wa_approvals.handle_reply("approve", code, "SM" + "8" * 32)
    assert "completed" in said and len(db.ran) == 1


async def test_a_tier_2_decline_is_decided_the_same_way(db, registry):
    registry("brand.new_action")
    pid = db.add_card(action="brand.new_action")
    code = db.open_code(pid)
    said = await wa_approvals.handle_reply("decline", code, "SM" + "d" * 32)
    assert db.ran == [] and db.proposals[pid]["status"] == "declined", said


@pytest.mark.parametrize("action", ["atlas.dev_request", "nightshift.merge",
                                    "delegate.claude_code", "agency.add_skill"])
async def test_dev_request_and_self_change_kinds_stay_portal_only(db, registry, action):
    registry(action)
    assert wa_approvals.portal_only(action)
    assert wa_approvals.eligibility(action, {}) == (False, wa_approvals.NOT_FROM_WHATSAPP)
    pid = db.add_card(action=action)
    code = db.open_code(pid)
    assert await wa_approvals.handle_reply("approve", code, "SM" + "8" * 32) == (
        wa_approvals.NOT_FROM_WHATSAPP)
    assert db.ran == [] and db.proposals[pid]["status"] == "pending"


@pytest.mark.parametrize("tier", [Tier.AUTO, Tier.NEVER])
def test_only_tier_2_is_decidable_from_whatsapp(registry, tier):
    registry("brand.new_action", tier=tier)
    ok, reply = wa_approvals.eligibility("brand.new_action", {})
    assert ok is False and reply in (wa_approvals.NEVER_REPLY,
                                     wa_approvals.NOT_FROM_WHATSAPP)


def test_an_unregistered_action_is_refused():
    assert wa_approvals.eligibility("ghost.never_registered", {}) == (
        False, wa_approvals.NEVER_REPLY)


def test_a_card_whose_text_would_be_cut_is_refused(registry, monkeypatch):
    from atlas.kernel import board
    registry("brand.new_action")
    monkeypatch.setitem(board.WHAT, "brand.new_action", lambda p: "word " * 80)
    assert wa_approvals.eligibility("brand.new_action", {}) == (
        False, wa_approvals.NOT_FROM_WHATSAPP)


def test_a_card_with_missing_or_unshown_params_is_refused(registry):
    async def run(title, note_id):                      # the required parameters
        return None
    registry("brand.new_action", run=run,
             params={"title": "a title", "note_id": "a row"})
    assert wa_approvals.eligibility("brand.new_action", {"note_id": "7"})[0] is False
    assert wa_approvals.eligibility(
        "brand.new_action", {"title": "x", "note_id": "7", "sneaky": "1"})[0] is False
    # a free-text parameter the card text does not show (summary is the action name)
    assert wa_approvals.eligibility(
        "brand.new_action", {"title": "Launch day", "note_id": "7"})[0] is False


def test_a_record_id_param_is_shown_by_reference(registry):
    async def run(note_id):
        return None
    registry("brand.new_action", run=run, params={"note_id": "a row"})
    wa_approvals.ID_SHOWN["brand.new_action"] = frozenset({"note_id"})
    try:
        assert wa_approvals.eligibility("brand.new_action", {"note_id": "7"}) == (True, "")
    finally:
        wa_approvals.ID_SHOWN.pop("brand.new_action")
    # An id is NOT exempt unless the action is listed, and is then shown by value.
    assert wa_approvals.eligibility("brand.new_action", {"note_id": "7"})[0] is False


def test_every_real_tier_2_action_follows_the_rule():
    """Read from the live registry: Tier 2 is decidable unless it is named
    never/deny or fails the parameter rule. New actions join on their own."""
    from atlas.kernel.executor import required_params
    from atlas.kernel.registry import REGISTRY
    REGISTRY.load_all()
    asks = [n for n, d in REGISTRY.decisions.items() if d.tier is Tier.ASK]
    assert asks
    allowed = 0
    for name in asks:
        params = {k: "7" for k in required_params(REGISTRY.actions[name])}
        if name == "linkedin.publish_post":
            params = {"page": "tutorco", "entry_id": "B1"}
        ok, _ = wa_approvals.eligibility(name, params)
        if wa_approvals.portal_only(name, REGISTRY.actions[name]):
            assert ok is False, name
        else:
            allowed += ok
    assert allowed >= 10


def test_the_deny_rule_is_exactly_the_agreed_kinds():
    assert wa_approvals.NEVER_FROM_WHATSAPP == frozenset({
        "atlas.dev_request", "nightshift.merge", "agency.add_skill", "lessons.clear",
        "delegate.claude_code", "agency.request_template", "agency.publish_doc",
        "agency.build_with_webild"})
    assert not hasattr(wa_approvals, "ALLOWED")          # no hand-written allowlist
    for name in ("x.set_dns_thing", "x.delete_note", "x.rotate_credentials",
                 "x.revoke_access", "billing.send_payment", "x.refund_customer",
                 "bizops.send_invoice", "stripe.retry_invoice", "x.create_checkout",
                 "x.rotate_token", "x.remove_member", "x.payout_now",
                 "bizops.send_payment_reminder", "x.wire_funds", "x.transfer_stock",
                 "billing.sendpayment_now"):
        assert wa_approvals.portal_only(name), name
    for name in ("tutorco.send_intro", "linkedin.publish_post", "youtube.upload",
                 "academy.relaunch_tutorial"):
        assert not wa_approvals.portal_only(name), name


async def test_the_allowlist_takes_the_tutorco_linkedin_post(db):
    pid = db.add_card(action="linkedin.publish_post",
                      params={"page": "tutorco", "entry_id": "B13"})
    code = db.open_code(pid)
    said = await wa_approvals.handle_reply("approve", code, "SM" + "2" * 32)
    assert "completed" in said and len(db.ran) == 1


@pytest.mark.parametrize("action", ["dns.update_record", "billing.send_payment",
                                    "mail.delete_thread", "vault.revoke_token",
                                    "kernel.set_tier"])
async def test_permanent_deny_kinds_are_never_approvable(db, registry, action):
    """Even a code row that somehow points at one (a card from before a rule
    changed) is refused: the live registry and the deny list win."""
    registry(action, tier=Tier.NEVER)
    pid = db.add_card(action=action)
    code = db.open_code(pid)
    said = await wa_approvals.handle_reply("approve", code, "SM" + "3" * 32)
    assert said == wa_approvals.NEVER_REPLY
    assert db.proposals[pid]["status"] == "pending" and db.ran == []


async def test_a_card_retiered_in_code_since_it_was_sent_is_refused(db, registry):
    pid = db.add_card()
    code = db.open_code(pid)
    registry(ACTION, tier=Tier.NEVER)
    said = await wa_approvals.handle_reply("approve", code, "SM" + "4" * 32)
    assert said == wa_approvals.NEVER_REPLY and db.ran == []


async def test_a_dry_run_that_refused_is_not_approvable_here(db):
    pid = db.add_card(dry_ok=False)
    code = db.open_code(pid)
    said = await wa_approvals.handle_reply("approve", code, "SM" + "5" * 32)
    assert said == wa_approvals.NOT_FROM_WHATSAPP and db.ran == []


async def test_watch_only_keeps_the_card_and_the_code(db, monkeypatch):
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "true")
    pid = db.add_card()
    code = db.open_code(pid)
    said = await wa_approvals.handle_reply("approve", code, "SM" + "6" * 32)
    assert said == wa_approvals.WATCH_ONLY_REPLY
    assert db.proposals[pid]["status"] == "pending"
    assert db.codes[0]["status"] == "open" and db.ran == []


# ----------------------------------------------------- one decision, two doors

async def test_slack_then_whatsapp_decides_once(db):
    pid = db.add_card()
    code = db.open_code(pid)
    first = await approvals.decide(pid, "approve", via="slack:U1")
    assert first.ok and len(db.ran) == 1
    assert db.codes[0]["status"] == "retired"     # the code died with the Slack tap
    said = await wa_approvals.handle_reply("approve", code, "SM" + "7" * 32)
    assert said == wa_approvals.RETIRED_CODE and len(db.ran) == 1


async def test_whatsapp_then_slack_decides_once(db):
    pid = db.add_card()
    code = db.open_code(pid)
    await wa_approvals.handle_reply("approve", code, "SM" + "8" * 32)
    second = await approvals.decide(pid, "approve", via="slack:U1")
    assert not second.ok and second.outcome == "already" and len(db.ran) == 1


async def test_a_slack_tap_that_the_claim_missed_still_blocks_the_code(db):
    """Even when the retire never ran, the claim needs the proposal pending."""
    pid = db.add_card()
    code = db.open_code(pid)
    db.proposals[pid]["status"] = "approved"
    said = await wa_approvals.handle_reply("approve", code, "SM" + "9" * 32)
    assert said == wa_approvals.RETIRED_CODE and db.ran == []


# ---------------------------------------------------------------- lockout

async def test_five_wrong_codes_lock_whatsapp_for_thirty_minutes(db):
    pid = db.add_card()
    code = db.open_code(pid)
    now = datetime.now(UTC)
    for i in range(4):
        said = await wa_approvals.handle_reply("approve", f"ZZZ{i}Z", f"SM{i}", now)
        assert said == wa_approvals.WRONG_CODE and db.slack == []
    said = await wa_approvals.handle_reply("approve", "ZZZ9Z", "SM9", now)
    assert "30 minutes" in said and len(db.slack) == 1
    assert "locked" in db.slack[0][0].lower()
    # the RIGHT code is refused while locked, and nothing runs
    locked = await wa_approvals.handle_reply("approve", code, "SMlocked", now + timedelta(minutes=29))
    assert "locked" in locked and db.ran == [] and db.proposals[pid]["status"] == "pending"
    # after thirty minutes it works again
    ok = await wa_approvals.handle_reply("approve", code, "SMafter", now + timedelta(minutes=31))
    assert "completed" in ok and len(db.ran) == 1


async def test_wrong_codes_outside_the_window_do_not_add_up(db):
    now = datetime.now(UTC)
    for i in range(4):
        await wa_approvals.handle_reply("approve", f"ZZZ{i}Z", f"SM{i}", now)
    later = now + timedelta(minutes=11)
    said = await wa_approvals.handle_reply("approve", "ZZZ8Z", "SM8", later)
    assert said == wa_approvals.WRONG_CODE and db.slack == []


async def test_a_used_code_is_not_a_guess(db):
    pid = db.add_card()
    code = db.open_code(pid, status="used")
    for i in range(6):
        said = await wa_approvals.handle_reply("approve", code, f"SM{i}")
        assert said == wa_approvals.USED_CODE
    assert db.slack == [] and db.attempts == []


# ------------------------------------------------------------------ audit

async def test_every_decision_is_audited_with_channel_and_sid_not_the_code(
        db, memory_backend):
    pid = db.add_card()
    code = db.open_code(pid)
    sid = "SM" + "z" * 32
    await wa_approvals.handle_reply("approve", code, sid)
    hits = [a for a in audits(memory_backend) if a["event"] == "whatsapp.decision"]
    assert len(hits) == 1
    assert hits[0]["data"]["channel"] == "whatsapp" and hits[0]["data"]["sid"] == sid
    assert hits[0]["data"]["outcome"] == "executed"
    blob = json.dumps(audits(memory_backend))
    assert code not in blob and OWNER not in blob


# ------------------------------------------------------------ the webhook

def sign(params: dict, url: str = URL, token: str = TOKEN) -> str:
    data = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    return base64.b64encode(hmac.new(token.encode(), data.encode(),
                                     hashlib.sha1).digest()).decode()


@pytest.fixture
def client(monkeypatch, db):
    whatsapp._LIMITER.clear()
    seen: set[tuple[str, str]] = set()

    async def claim(action: str, key: str) -> str:
        if (action, key) in seen:
            return "in doubt"
        seen.add((action, key))
        return "claimed"
    monkeypatch.setattr("atlas.kernel.idempotency.claim", claim)
    from atlas.api.main import app

    async def no_boot() -> None:
        return None
    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    with TestClient(app, client=("172.18.0.5", 4000)) as c:
        yield c


def post(client, body, sid="SM" + "0" * 32, sender=f"whatsapp:{OWNER}"):
    params = {"MessageSid": sid, "From": sender, "Body": body}
    return client.post("/webhooks/twilio/whatsapp", content=urlencode(params),
                       headers={"X-Twilio-Signature": sign(params),
                                "Content-Type": "application/x-www-form-urlencoded"})


def spoken(resp) -> str:
    """The reply text without its category tag (the tag has its own test)."""
    from atlas.kernel.wa_format import SEP, TAGS, Category
    text = " ".join(re.findall(r"<Message>(.*?)</Message>", resp.text, re.DOTALL))
    return text.replace(TAGS[Category.APPROVALS] + SEP, "")


def test_every_approval_reply_on_the_webhook_carries_the_approvals_tag(client, db):
    from atlas.kernel.wa_format import SEP, TAGS, Category
    pid = db.add_card()
    code = db.open_code(pid)
    for body in (f"YES {code}", "yes"):
        r = post(client, body, sid="SM" + ("1" if body != "yes" else "2") * 32)
        assert re.search(r"<Message>" + re.escape(TAGS[Category.APPROVALS] + SEP), r.text)


def test_the_webhook_approves_once_and_a_replay_does_nothing(client, db):
    pid = db.add_card()
    code = db.open_code(pid)
    first = post(client, f"YES {code}", sid="SM" + "1" * 32)
    assert first.status_code == 200 and "completed" in spoken(first)
    assert len(db.ran) == 1
    replay = post(client, f"YES {code}", sid="SM" + "1" * 32)   # same MessageSid
    assert spoken(replay) == "" and len(db.ran) == 1
    other = post(client, f"YES {code}", sid="SM" + "2" * 32)     # new message, used code
    assert "already used" in spoken(other) and len(db.ran) == 1


@pytest.mark.parametrize("text", ["ok", "approve", "Yes please", "do it", "go ahead"])
def test_a_bare_word_other_than_yes_no_never_approves(client, db, text):
    pid = db.add_card()
    db.open_code(pid)
    said = spoken(post(client, text))
    assert "Tap" in said or "tap" in said
    assert db.proposals[pid]["status"] == "pending" and db.ran == []


def test_a_bare_yes_with_several_cards_decides_nothing_and_lists_them(client, db):
    a, b = db.add_card(pid=7), db.add_card(pid=8)
    db.open_code(a, code="AAAAA")
    db.open_code(b, code="BBBBB")
    said = spoken(post(client, "yes"))
    assert "#7" in said and "#8" in said and "2 cards" in said
    assert "Nothing was changed" in said
    assert db.ran == [] and db.proposals[a]["status"] == "pending"
    assert db.proposals[b]["status"] == "pending"


@pytest.mark.parametrize("text", ["Yes!", "yes", "no", "ok", "approve", "Y", "nope"])
def test_a_bare_yes_or_no_never_decides_even_with_one_fresh_card(client, db, text):
    pid = db.add_card()
    db.open_code(pid)
    said = spoken(post(client, text))
    assert "decides nothing" in said and f"#{pid}" in said
    assert "button" in said and "followed by its code" in said and "Nothing was changed" in said
    assert db.ran == [] and db.proposals[pid]["status"] == "pending"
    assert db.codes[0]["status"] == "open"


def test_a_bare_yes_with_no_card_waiting_says_so(client, db):
    said = spoken(post(client, "yes"))
    assert "No card is waiting" in said and db.ran == []


def test_a_stranger_is_ignored(client, db):
    pid = db.add_card()
    code = db.open_code(pid)
    resp = post(client, f"YES {code}", sender="whatsapp:+19175550199")
    assert resp.status_code == 200 and spoken(resp) == ""
    assert db.proposals[pid]["status"] == "pending" and db.ran == []


def test_a_bad_signature_is_refused_before_anything(client, db):
    pid = db.add_card()
    code = db.open_code(pid)
    params = {"MessageSid": "SM" + "3" * 32, "From": f"whatsapp:{OWNER}",
              "Body": f"YES {code}"}
    resp = client.post("/webhooks/twilio/whatsapp", content=urlencode(params),
                       headers={"X-Twilio-Signature": "bad",
                                "Content-Type": "application/x-www-form-urlencoded"})
    assert resp.status_code == 403 and db.ran == []


def test_the_text_never_reaches_a_model(client, db, monkeypatch):
    async def boom(*_a, **_k):
        raise AssertionError("a model saw an approval message")
    monkeypatch.setattr("atlas.kernel.llm.think", boom)
    monkeypatch.setattr(whatsapp, "answer", boom)
    pid = db.add_card()
    code = db.open_code(pid)
    assert "completed" in spoken(post(client, f"YES {code}"))
    assert "nothing was changed" in spoken(
        post(client, "approve", sid="SM" + "4" * 32)).lower()


# -------------------------------------------------- sending the card out

async def test_an_allowed_card_goes_out_with_a_one_time_code(db, monkeypatch):
    from atlas.modules.tutorco import notify
    sent: list[str] = []

    async def send(to, body):
        sent.append(body)
        return True, "SM" + "9" * 32
    monkeypatch.setattr(notify, "send_whatsapp", send)

    async def claim(a, k):
        return "claimed"
    monkeypatch.setattr("atlas.kernel.idempotency.claim", claim)
    monkeypatch.setattr("atlas.kernel.idempotency.settle", lambda *a, **k: _none())
    pid = db.add_card()
    result = await notify.notify_card(pid)
    assert result.ok and "one-time code" in result.detail
    body = sent[0]
    code = re.search(r"YES ([A-Z0-9]{5})", body).group(1)
    assert f"NO {code}" in body and "Expires" in body and "Risk" in body
    assert code not in json.dumps(result.evidence)
    row = db.codes[0]
    assert row["code_hash"] == wa_approvals.code_hash(code) and code not in str(row)
    assert row["expires_at"] == db.proposals[pid]["expires_at"]


async def _none():
    return None


async def test_a_card_that_is_not_allowed_is_told_read_only(db, monkeypatch):
    from atlas.modules.tutorco import notify
    sent: list[str] = []

    async def send(to, body):
        sent.append(body)
        return True, "SM" + "8" * 32

    async def claim(a, k):
        return "claimed"
    monkeypatch.setattr(notify, "send_whatsapp", send)
    monkeypatch.setattr("atlas.kernel.idempotency.claim", claim)
    monkeypatch.setattr("atlas.kernel.idempotency.settle", lambda *a, **k: _none())
    pid = db.add_card(action="atlas.dev_request")
    result = await notify.notify_card(pid)
    assert result.ok and "read-only" in result.detail
    assert "YES" not in sent[0] and "Decide it on the portal" in sent[0]
    assert db.codes == []


async def test_a_failed_send_kills_the_code_and_is_said_in_slack(db, monkeypatch):
    from atlas.modules.tutorco import notify
    released: list = []

    async def send(to, body):
        return False, "HTTP 500 from api.twilio.com"

    async def claim(a, k):
        return "claimed"

    async def release(a, k):
        released.append((a, k))
    monkeypatch.setattr(notify, "send_whatsapp", send)
    monkeypatch.setattr("atlas.kernel.idempotency.claim", claim)
    monkeypatch.setattr("atlas.kernel.idempotency.release", release)
    pid = db.add_card()
    result = await notify.notify_card(pid)
    assert not result.ok and db.codes[0]["status"] == "retired" and released
    # and the card path does NOT go to Slack: it schedules one second attempt
    async def fake_execute(req):
        return result
    spawned: list = []
    monkeypatch.setattr(approvals, "execute", fake_execute)
    monkeypatch.setattr("atlas.kernel.background.spawn",
                        lambda coro, name: (spawned.append(name), coro.close()))
    await approvals._whatsapp_card(pid)
    assert db.slack == [] and spawned == [f"whatsapp card retry #{pid}"]


async def test_a_failed_send_tells_slack_only_when_approvals_slack_is_on(
        db, monkeypatch):
    async def fake_execute(req):
        return ActionResult(False, "send failed: HTTP 500")
    monkeypatch.setattr(approvals, "execute", fake_execute)
    monkeypatch.setattr("atlas.kernel.background.spawn",
                        lambda coro, name: coro.close())
    monkeypatch.setenv("APPROVALS_SLACK", "on")
    await approvals._whatsapp_card(7)
    assert len(db.slack) == 1 and "decide it here" not in db.slack[0][1].lower()
    assert "portal" in db.slack[0][1]
    db.slack.clear()
    monkeypatch.setenv("APPROVALS_SLACK", "off")
    await approvals._whatsapp_card(7)
    assert db.slack == []


def test_tell_slack_itself_is_silent_unless_on(monkeypatch):
    import asyncio
    sent: list = []

    async def comms_notify(out):
        sent.append(out)
        return True
    monkeypatch.setattr("atlas.kernel.comms.notify", comms_notify)
    real = wa_approvals.__dict__["tell_slack"]
    monkeypatch.delenv("APPROVALS_SLACK", raising=False)
    asyncio.run(real("t", "b"))
    assert sent == [] and wa_approvals.slack_on() is False
    monkeypatch.setenv("APPROVALS_SLACK", "on")
    asyncio.run(real("t", "b"))
    assert len(sent) == 1


async def test_the_second_whatsapp_attempt_is_one_and_only_for_a_pending_card(
        db, monkeypatch):
    calls: list = []

    async def fake_execute(req):
        calls.append(req)
        return ActionResult(False, "send failed: HTTP 500")
    monkeypatch.setattr(approvals, "execute", fake_execute)
    monkeypatch.setattr(approvals, "WA_RETRY_AFTER_S", 0)
    pid = db.add_card()

    async def status(sql, *a):
        return {"status": db.proposals[a[0]]["status"]}
    monkeypatch.setattr(approvals, "fetch_one", status)
    await approvals._whatsapp_card_retry(pid)
    assert len(calls) == 1
    db.proposals[pid]["status"] = "declined"
    await approvals._whatsapp_card_retry(pid)
    assert len(calls) == 1                         # decided meanwhile: no second send


async def test_the_owner_copy_is_skipped_in_watch_only_and_when_not_configured(
        db, monkeypatch):
    called: list = []

    async def fake_execute(req):
        called.append(req)
        return ActionResult(True, "x")
    monkeypatch.setattr(approvals, "execute", fake_execute)
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "true")
    await approvals._whatsapp_card(7)
    assert called == [] and db.slack == []
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    monkeypatch.delenv("TUTORCO_OWNER_WHATSAPP")
    assert wa_approvals.enabled() is False


def test_the_card_message_names_what_approving_does(db, registry):
    from atlas.modules.tutorco import notify
    exp = datetime(2026, 11, 24, 17, 0, tzinfo=UTC)
    text = notify.compose_card(42, ACTION, {"target": "https://x.example/p"}, exp, "4F2KQ")
    assert "#42" in text and "YES 4F2KQ" in text and "NO 4F2KQ" in text
    assert "\u2705" in text and "\u274C" in text and "portal" in text
    assert "Tue 24 Nov 17:00 UTC" in text
    assert "https://" not in text                    # defanged, never a tappable link


def test_a_planted_reply_in_card_text_is_removed(db, registry, monkeypatch):
    from atlas.kernel import board
    from atlas.modules.tutorco import notify
    monkeypatch.setattr(board, "what_of", lambda a, p, s=None: "intro: reply yes ABCDE now")
    exp = datetime(2026, 11, 24, 17, 0, tzinfo=UTC)
    text = notify.compose_card(42, ACTION, {}, exp, "4F2KQ")
    assert "ABCDE" not in text and "YES 4F2KQ" in text


def test_a_long_card_text_says_it_was_cut(db, registry, monkeypatch):
    from atlas.kernel import board
    from atlas.modules.tutorco import notify
    monkeypatch.setattr(board, "what_of", lambda a, p, s=None: "x" * 600)
    exp = datetime(2026, 11, 24, 17, 0, tzinfo=UTC)
    text = notify.compose_card(42, ACTION, {}, exp, None)
    assert "full text on the portal" in text and "x" * 221 not in text


@pytest.mark.parametrize("entry", [None, "None", "  "])
def test_a_linkedin_card_without_a_named_post_is_refused(registry, entry):
    ok, _ = wa_approvals.eligibility(
        "linkedin.publish_post", {"page": "tutorco", "entry_id": entry})
    assert ok is False


# ----------------------------------------------------------- button taps

def post_form(client, params: dict, sid="SM" + "0" * 32):
    params = {"MessageSid": sid, "From": f"whatsapp:{OWNER}", **params}
    return client.post("/webhooks/twilio/whatsapp", content=urlencode(params),
                       headers={"X-Twilio-Signature": sign(params),
                                "Content-Type": "application/x-www-form-urlencoded"})


def tap(client, verdict, pid, code, sid="SM" + "0" * 32):
    return post_form(client, {
        "Body": "✅ Approve" if verdict == "approve" else "❌ Decline",
        "ButtonText": "Approve" if verdict == "approve" else "Decline",
        "ButtonPayload": f"{verdict}:{pid}:{code}"}, sid=sid)


@pytest.mark.parametrize("payload,want", [
    ("approve:7:4F2KQ", ("approve", 7, "4F2KQ")),
    ("decline:12:4F2K", ("decline", 12, "4F2K")),
    (" approve:7:4F2KQ ", ("approve", 7, "4F2KQ"))])
def test_the_button_payload_parses_strictly(payload, want):
    assert wa_approvals.parse_button(payload) == want


@pytest.mark.parametrize("payload", [
    "", None, "approve", "approve:7", "approve:x:4F2KQ", "approve:7:4f2kq",
    "approve:7:4F2KQ:extra", "yes:7:4F2KQ", "approve:7:4F2O1", "approve:-1:4F2KQ",
    "approve:7:4F2KQ\nNO", "APPROVE:7:4F2KQ"])
def test_any_other_payload_is_not_a_tap(payload):
    assert wa_approvals.parse_button(payload) is None


def test_a_tap_decides_exactly_that_card(client, db):
    a, b = db.add_card(pid=7), db.add_card(pid=8)
    ca = db.open_code(a, code="AAAAA")
    db.open_code(b, code="BBBBB")
    said = spoken(tap(client, "approve", a, ca))
    assert "completed" in said and len(db.ran) == 1
    assert db.proposals[a]["status"] == "executed"
    assert db.proposals[b]["status"] == "pending"          # the other one untouched


def test_a_decline_tap_declines_and_runs_nothing(client, db):
    pid = db.add_card()
    code = db.open_code(pid)
    said = spoken(tap(client, "decline", pid, code))
    assert "declined" in said and db.ran == []


def test_a_tap_carrying_another_cards_code_is_wrong_and_changes_nothing(client, db):
    a, b = db.add_card(pid=7), db.add_card(pid=8)
    db.open_code(a, code="AAAAA")
    cb = db.open_code(b, code="BBBBB")
    said = spoken(tap(client, "approve", a, cb))           # card 7, card 8's code
    assert said.startswith(wa_approvals.WRONG_CODE)
    assert db.ran == [] and all(c["status"] == "open" for c in db.codes)
    assert len(db.attempts) == 1                           # and it counts to the lock


def test_a_tap_replay_does_nothing(client, db):
    pid = db.add_card()
    code = db.open_code(pid)
    sid = "SM" + "5" * 32
    assert "completed" in spoken(tap(client, "approve", pid, code, sid=sid))
    assert spoken(tap(client, "approve", pid, code, sid=sid)) == ""
    assert "already used" in spoken(tap(client, "approve", pid, code, sid="SM" + "6" * 32))
    assert len(db.ran) == 1


def test_a_late_tap_on_an_expired_card_says_so_and_does_nothing(client, db):
    pid = db.add_card(hours=-1)
    code = db.open_code(pid, expires_at=datetime.now(UTC) - timedelta(minutes=5))
    said = spoken(tap(client, "approve", pid, code))
    assert said == wa_approvals.EXPIRED_CARD
    assert "expired" in said and "nothing was done" in said
    assert db.ran == []


def test_a_late_tap_after_the_sweep_retired_and_expired_the_card(client, db):
    pid = db.add_card()
    code = db.open_code(pid, status="retired")
    db.proposals[pid]["status"] = "expired"
    assert spoken(tap(client, "approve", pid, code)) == wa_approvals.EXPIRED_CARD
    assert db.ran == []


def test_a_tap_on_a_card_decided_elsewhere_says_so(client, db):
    pid = db.add_card()
    code = db.open_code(pid)
    db.proposals[pid]["status"] = "declined"
    assert spoken(tap(client, "approve", pid, code)) == wa_approvals.RETIRED_CODE
    assert db.ran == []


def test_an_unrecognised_payload_changes_nothing_and_skips_the_model(
        client, db, monkeypatch):
    async def boom(*_a, **_k):
        raise AssertionError("a model saw a button payload")
    monkeypatch.setattr("atlas.kernel.llm.think", boom)
    monkeypatch.setattr(whatsapp, "answer", boom)
    pid = db.add_card()
    db.open_code(pid)
    said = spoken(post_form(client, {"Body": "x", "ButtonPayload": "delete:everything"}))
    assert said == wa_approvals.BAD_BUTTON and db.ran == []


def test_a_tap_from_a_stranger_or_with_a_bad_signature_is_ignored(client, db):
    pid = db.add_card()
    code = db.open_code(pid)
    params = {"MessageSid": "SM" + "7" * 32, "From": "whatsapp:+19175550199",
              "Body": "x", "ButtonPayload": f"approve:{pid}:{code}"}
    resp = client.post("/webhooks/twilio/whatsapp", content=urlencode(params),
                       headers={"X-Twilio-Signature": sign(params),
                                "Content-Type": "application/x-www-form-urlencoded"})
    assert spoken(resp) == "" and db.ran == []
    resp = client.post("/webhooks/twilio/whatsapp", content=urlencode(params),
                       headers={"X-Twilio-Signature": "bad",
                                "Content-Type": "application/x-www-form-urlencoded"})
    assert resp.status_code == 403 and db.ran == []


def test_a_tap_on_a_portal_only_card_is_refused(client, db):
    pid = db.add_card(action="atlas.dev_request")
    code = db.open_code(pid)
    assert spoken(tap(client, "approve", pid, code)) == wa_approvals.NOT_FROM_WHATSAPP
    assert db.ran == [] and db.proposals[pid]["status"] == "pending"


def test_five_wrong_taps_lock_taps_too(client, db):
    pid = db.add_card()
    code = db.open_code(pid)
    for i in range(5):
        tap(client, "approve", pid, "ZZZZZ", sid="SM" + str(i) * 32)
    said = spoken(tap(client, "approve", pid, code, sid="SM" + "8" * 32))
    assert "locked" in said and db.ran == []


# ------------------------------------- Tier 3 / PERMANENT_DENY: never, either way

TIER3 = ["dns.update_record", "billing.send_payment", "mail.delete_thread",
         "vault.revoke_token", "kernel.set_tier", "stripe.refund_charge",
         "x.transfer_funds", "x.withdraw_cash", "files.purge_all", "x.grant_access",
         "x.set_dns_record", "x.disable_audit", "x.rotate_own_credentials"]


@pytest.mark.parametrize("action", TIER3)
def test_every_tier_3_kind_is_rejected_by_tap_and_by_text(client, db, registry, action):
    registry(action, tier=Tier.NEVER)
    pid = db.add_card(action=action)
    code = db.open_code(pid)
    by_tap = spoken(tap(client, "approve", pid, code, sid="SM" + "a" * 32))
    by_text = spoken(post(client, f"YES {code}", sid="SM" + "b" * 32))
    by_bare = spoken(post(client, "yes", sid="SM" + "c" * 32))
    assert by_tap == by_text == wa_approvals.NEVER_REPLY
    assert "decides nothing" in by_bare
    assert db.ran == [] and db.proposals[pid]["status"] == "pending"
    assert wa_approvals.eligibility(action, {}) == (False, wa_approvals.NEVER_REPLY)


@pytest.mark.parametrize("action", TIER3)
def test_a_tier_3_kind_registered_as_tier_2_by_mistake_is_still_refused(registry, action):
    """The deny list judges the NAME: a wrong tier in a module cannot open it."""
    registry(action, tier=Tier.ASK)
    ok, reply = wa_approvals.eligibility(action, {})
    assert ok is False and reply in (wa_approvals.NEVER_REPLY,
                                     wa_approvals.NOT_FROM_WHATSAPP)


def test_no_permanent_deny_name_in_the_real_catalog_is_eligible():
    from atlas.kernel.permissions import denial_reason
    from atlas.kernel.registry import REGISTRY
    REGISTRY.load_all()
    for name in REGISTRY.decisions:
        if denial_reason(name) is not None or REGISTRY.decisions[name].tier is Tier.NEVER:
            ok, _ = wa_approvals.eligibility(name, {"entry_id": "B1", "page": "tutorco"})
            assert ok is False, name


def test_the_real_catalog_keeps_self_change_cards_on_the_portal():
    from atlas.kernel.registry import REGISTRY
    REGISTRY.load_all()
    for name in ("atlas.dev_request", "nightshift.merge", "delegate.claude_code",
                 "agency.add_skill", "lessons.clear", "agency.send_agreement",
                 "research.deep_dive", "seo.ai_visibility_check",
                 "agency.request_template", "agency.publish_doc",
                 "agency.build_with_webild"):
        if name in REGISTRY.decisions:
            assert wa_approvals.eligibility(name, {}) == (
                False, wa_approvals.NOT_FROM_WHATSAPP), name


# ----------------------------------- what approving does, built from the action

def test_publishing_visibility_and_relaunch_cards_state_what_they_do():
    from atlas.kernel.registry import REGISTRY
    REGISTRY.load_all()
    for name, decision in REGISTRY.decisions.items():
        if decision.tier is not Tier.ASK or wa_approvals.portal_only(name):
            continue
        if "check" in name:                  # a read-only report, not a publish kind
            continue
        if any(w in name for w in ("publish", "relaunch", "visibility", "upload",
                                   "submit", "schedule", "deploy")):
            assert name in wa_approvals.EFFECTS, f"{name} has no explicit effect line"
    line = wa_approvals.effect_line("academy.relaunch_tutorial", {})
    assert "PUBLIC" in line and "replaced" in line
    pub = wa_approvals.effect_line("youtube.set_visibility",
                                   {"video_id": "v", "privacy": "public"})
    assert "public" in pub and "LIVE" in pub
    priv = wa_approvals.effect_line("youtube.set_visibility",
                                    {"video_id": "v", "privacy": "private"})
    assert "LIVE" not in priv
    assert "PRIVATE" in wa_approvals.effect_line("youtube.upload", {})
    assert "tutorco page" in wa_approvals.effect_line(
        "linkedin.publish_post", {"page": "tutorco page", "entry_id": "B1"})
    generic = wa_approvals.effect_line("mail.send_reply", {}, SimpleNamespace(
        summary="Send a reply"))
    assert generic == "Approving runs: Send a reply."


def test_the_card_message_for_a_relaunch_says_what_goes_public(db, registry):
    from atlas.modules.tutorco import notify
    exp = datetime(2026, 11, 24, 17, 0, tzinfo=UTC)
    text = notify.compose_card(9, "academy.relaunch_tutorial", {}, exp, "4F2KQ",
                               buttons=True)
    assert "PUBLIC" in text and "Tap" in text and "YES" not in text
    assert "Approve" in text and "Decline" in text and "portal" in text


def test_the_cut_limit_is_the_one_the_message_really_uses(registry, monkeypatch):
    """What the owner reads in the message is `board.what_of(...)` cut at
    `CARD_WHAT_MAX`; a card whose words run past that is portal-only, so nobody
    approves text that was cut."""
    from atlas.kernel import board
    from atlas.modules.tutorco import notify
    registry("youtube.upload")
    for n, ok in ((notify.CARD_WHAT_MAX, True), (notify.CARD_WHAT_MAX + 1, False)):
        monkeypatch.setitem(board.WHAT, "youtube.upload", lambda p, n=n: "x" * n)
        assert wa_approvals.eligibility("youtube.upload", {}) == (
            (True, "") if ok else (False, wa_approvals.NOT_FROM_WHATSAPP))
    # and the message for the short one is not cut
    monkeypatch.setitem(board.WHAT, "youtube.upload", lambda p: "x" * notify.CARD_WHAT_MAX)
    text = notify.compose_card(3, "youtube.upload", {}, datetime.now(UTC), "4F2KQ",
                               buttons=True)
    assert "cut" not in text


def test_the_cut_check_reads_the_same_field_as_the_message(registry, monkeypatch):
    from atlas.kernel import board
    registry("youtube.upload", summary="s" * 400)       # no WHAT renderer: the summary
    monkeypatch.delitem(board.WHAT, "youtube.upload", raising=False)
    from atlas.kernel.registry import REGISTRY
    from atlas.modules.tutorco import notify
    shown = " ".join(board.what_of(
        "youtube.upload", {}, REGISTRY.actions["youtube.upload"]).split())
    cut = len(shown) > notify.CARD_WHAT_MAX
    assert (wa_approvals.eligibility("youtube.upload", {})[0]) is (not cut)


# --------------------------------------------- buttons on the outgoing card

def _fake_send(monkeypatch, notify, *, template_ok=True):
    plain: list[str] = []
    cards: list[tuple] = []

    async def send(to, body):
        plain.append(body)
        return True, "SM" + "9" * 32

    async def send_card(to, text, pid, code):
        cards.append((text, pid, code))
        return (True, "SM" + "a" * 32) if template_ok else (False, "HTTP 400 (code 63016)")

    async def claim(a, k):
        return "claimed"
    monkeypatch.setattr(notify, "send_whatsapp", send)
    monkeypatch.setattr(notify, "send_whatsapp_card", send_card)
    monkeypatch.setattr("atlas.kernel.idempotency.claim", claim)
    monkeypatch.setattr("atlas.kernel.idempotency.settle", lambda *a, **k: _none())
    return plain, cards


async def test_with_a_template_the_card_goes_out_with_buttons(db, monkeypatch):
    from atlas.modules.tutorco import notify
    monkeypatch.setenv("WHATSAPP_CONTENT_SID_CARD", "HX" + "a" * 32)
    plain, cards = _fake_send(monkeypatch, notify)
    pid = db.add_card()
    result = await notify.notify_card(pid)
    assert result.ok and "buttons" in result.detail and result.evidence["buttons"] is True
    assert plain == [] and len(cards) == 1
    text, cpid, code = cards[0]
    assert cpid == pid and "Tap" in text and "YES" not in text
    assert db.codes[0]["code_hash"] == wa_approvals.code_hash(code)


async def test_a_refused_template_falls_back_to_plain_yes_no_text(db, monkeypatch):
    from atlas.modules.tutorco import notify
    monkeypatch.setenv("WHATSAPP_CONTENT_SID_CARD", "HX" + "a" * 32)
    plain, cards = _fake_send(monkeypatch, notify, template_ok=False)
    pid = db.add_card()
    result = await notify.notify_card(pid)
    assert result.ok and result.evidence["buttons"] is False
    assert len(cards) == 1 and len(plain) == 1          # tried buttons, then text
    code = re.search(r"YES ([A-Z0-9]{5})", plain[0]).group(1)
    assert f"NO {code}" in plain[0] and "portal" in plain[0]
    assert db.codes[0]["status"] == "open"              # the card is not lost


async def test_no_template_configured_sends_plain_text_only(db, monkeypatch):
    from atlas.modules.tutorco import notify
    monkeypatch.delenv("WHATSAPP_CONTENT_SID_CARD", raising=False)
    plain, cards = _fake_send(monkeypatch, notify)
    result = await notify.notify_card(db.add_card())
    assert result.ok and cards == [] and len(plain) == 1


async def test_a_malformed_template_sid_is_ignored(db, monkeypatch):
    from atlas.modules.tutorco import notify
    monkeypatch.setenv("WHATSAPP_CONTENT_SID_CARD", "not-a-sid")
    assert notify.content_sid() is None


async def test_the_template_request_carries_the_payload_variables(db, monkeypatch):
    import httpx

    from atlas.modules.tutorco import notify
    monkeypatch.setenv("WHATSAPP_CONTENT_SID_CARD", "HX" + "b" * 32)
    seen: dict = {}

    class FakeClient:
        def __init__(self, **_k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def post(self, url, data=None, **_k):
            seen.update(url=url, data=data)
            return httpx.Response(201, json={"sid": "SM" + "d" * 32})
    monkeypatch.setattr(notify.httpx, "AsyncClient", FakeClient)
    ok, sid = await notify.send_whatsapp_card(OWNER, "line one\nline two", 12, "4F2KQ")
    assert ok and sid.startswith("SM")
    assert seen["data"]["ContentSid"] == "HX" + "b" * 32
    assert "Body" not in seen["data"]
    assert json.loads(seen["data"]["ContentVariables"]) == {
        "1": "\u2705 Approvals \u00B7 line one line two", "2": "12", "3": "4F2KQ"}


def _banner(monkeypatch, rows):
    from atlas.api import dashboard

    async def fetch_all(sql, *a):
        assert "effect_claims" in sql
        return rows
    monkeypatch.setattr(dashboard, "fetch_all", fetch_all)
    return dashboard


def test_the_portal_banner_counts_only_cards_that_were_really_eligible(
        db, registry, monkeypatch):
    import asyncio
    registry("youtube.upload")
    rows = [
        {"id": 1, "action": "youtube.upload", "params": "{}", "dry_run_ok": True},
        {"id": 2, "action": "youtube.upload", "params": "{}", "dry_run_ok": False},
        {"id": 3, "action": "atlas.dev_request", "params": "{}", "dry_run_ok": True},
        {"id": 4, "action": "tutorco.send_intro", "params": "{}", "dry_run_ok": True}]
    dashboard = _banner(monkeypatch, rows)
    assert asyncio.run(dashboard._whatsapp_undelivered()) == 2     # 1 and 4 only


def test_the_banner_is_zero_when_nothing_could_be_sent(db, monkeypatch):
    import asyncio
    dashboard = _banner(monkeypatch, [
        {"id": 1, "action": "tutorco.send_intro", "params": "{}", "dry_run_ok": True}])
    monkeypatch.setattr(dashboard, "watch_only", lambda: True)      # nothing is sent
    assert asyncio.run(dashboard._whatsapp_undelivered()) == 0
    monkeypatch.setattr(dashboard, "watch_only", lambda: False)
    assert asyncio.run(dashboard._whatsapp_undelivered()) == 1
    monkeypatch.delenv("TUTORCO_OWNER_WHATSAPP")                        # not configured
    assert asyncio.run(dashboard._whatsapp_undelivered()) == 0


def test_the_banner_is_none_when_it_cannot_be_read(db, monkeypatch):
    import asyncio

    from atlas.api import dashboard

    async def broken(sql, *a):
        raise RuntimeError("db down")
    monkeypatch.setattr(dashboard, "fetch_all", broken)
    assert asyncio.run(dashboard._whatsapp_undelivered()) is None   # never a guessed 0


async def test_a_config_refusal_is_not_retried(db, monkeypatch):
    from atlas.kernel.contracts import FailureClass

    async def fake_execute(req):
        return ActionResult(False, "not sent: TUTORCO_OWNER_WHATSAPP is not set",
                            failure_class=FailureClass.VALIDATION)
    spawned: list = []
    monkeypatch.setattr(approvals, "execute", fake_execute)
    monkeypatch.setattr("atlas.kernel.background.spawn",
                        lambda coro, name: (spawned.append(name), coro.close()))
    await approvals._whatsapp_card(7)
    assert spawned == []


async def test_a_slack_card_with_buttons_is_posted_only_with_approvals_slack_on(
        monkeypatch):
    """The Slack card at proposal time (and its Approve buttons) is behind
    APPROVALS_SLACK, default off."""
    import inspect
    src = inspect.getsource(approvals.propose)
    assert "slack_on()" in src.split("Button(")[0].rsplit("await notify(", 1)[0][-400:]
    monkeypatch.delenv("APPROVALS_SLACK", raising=False)
    assert wa_approvals.slack_on() is False


def test_a_money_or_credential_flag_on_the_spec_refuses(registry):
    async def run():
        return None
    registry("brand.neutral_name", run=run)
    from atlas.kernel.registry import REGISTRY
    assert wa_approvals.eligibility("brand.neutral_name", {}) == (True, "")
    spec = REGISTRY.actions["brand.neutral_name"]
    object.__setattr__(spec, "moves_money_or_credentials", True)
    assert wa_approvals.portal_only("brand.neutral_name", spec)
    assert wa_approvals.eligibility("brand.neutral_name", {})[0] is False


def test_every_given_param_must_be_shown_in_full_as_a_whole_token(registry):
    async def run(**kw):
        return None
    registry("brand.two_params", run=run, params={"to": "who", "note": "optional note"})
    shown = "send hello to bob@example.com now"
    ps = wa_approvals.params_shown
    from atlas.kernel.registry import REGISTRY
    spec = REGISTRY.actions["brand.two_params"]
    assert ps("brand.two_params", {"to": "bob@example.com"}, spec, shown)
    # an OPTIONAL param that is not shown refuses (only required ones were checked)
    assert not ps("brand.two_params", {"to": "bob@example.com", "note": "secret plan"},
                  spec, shown)
    # a loose substring is not a whole token
    assert not ps("brand.two_params", {"to": "bob@example.co"}, spec, shown)
    assert not ps("brand.two_params", {"to": "ob@example.com"}, spec, shown)
    # a long value cannot hide behind a shown prefix
    long = "bob@example.com " + "x" * 80
    assert not ps("brand.two_params", {"to": long}, spec, shown)


def test_every_money_action_in_code_stays_on_the_portal():
    """`board.MONEY` and the paid-call flag are code classifications: none of them
    is ever decided from WhatsApp, whatever the name looks like."""
    from atlas.kernel import board
    from atlas.kernel.registry import REGISTRY
    REGISTRY.load_all()
    for name in board.MONEY:
        assert wa_approvals.portal_only(name), name
    assert wa_approvals.portal_only("stripe.anything_new")
    flagged = [n for n, a in REGISTRY.actions.items()
               if getattr(a, "moves_money_or_credentials", False)]
    assert {"research.deep_dive", "seo.ai_visibility_check"} <= set(flagged)
    for name in [*board.MONEY, *flagged]:
        if name in REGISTRY.decisions:
            assert wa_approvals.eligibility(name, {})[0] is False, name


def test_a_param_matching_only_the_summary_is_not_shown(registry):
    """With no renderer the card shows the fixed summary: a value that happens to
    be one of its words was never shown, so the card is portal-only."""
    async def run(note):
        return None
    registry("brand.summary_words", run=run, params={"note": "a note"},
             summary="Post the weekly digest")
    assert wa_approvals.eligibility("brand.summary_words", {"note": "weekly"})[0] is False
    assert wa_approvals.eligibility("brand.summary_words", {})[0] is False   # required
