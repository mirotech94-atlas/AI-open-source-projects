"""RA-B7 and RA-C5: a retried run never repeats its external effect, and a
Matrix reply cannot fall back to a recipient or a room nobody named.

What is load-bearing:

* The run an action executes under reaches `action.run` from the real
  approval path (`approvals.decide` -> `executor.execute` -> `run`), not only
  from a test that set it by hand.
* `stripe.retry_invoice` sends one Idempotency-Key per (invoice, run): two
  attempts of one run are one charge at a Stripe that honours the key.
* `messages.reply` and `matrix_rooms.send_reply` claim the
  send before attempting it: the same run twice is one send; a send whose
  outcome is unknown is never resent; a send the provider refused may be.
* `matrix_rooms.send_reply` refuses a missing `to`, and reads the room's real
  name at send time so a blank `room_name` cannot slip past the silent-room
  guard.

Transports are mocked; nothing reaches a network.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from atlas.kernel import approvals, idempotency, state
from atlas.kernel.contracts import Action, ActionResult, Tier
from atlas.kernel.permissions import classify
from atlas.kernel.registry import REGISTRY, payload_hash
from atlas.modules import matrix_rooms as mx
from atlas.modules import messages, stripe_billing
from atlas.modules.messages import instagram_dm


class ClaimsBackend(state.MemoryBackend):
    """MemoryBackend with a working `effect_claims` table, so the claim's
    SQL round trip -- insert, look up, settle, release -- is exercised."""

    def __init__(self) -> None:
        super().__init__()
        self.claims: dict[tuple[str, str], str] = {}

    async def fetchrow(self, sql: str, *args: Any) -> Any | None:
        flat = " ".join(sql.split())
        if "INSERT INTO effect_claims" in flat:
            key = (args[0], args[1])
            if key in self.claims:
                return None
            self.claims[key] = "sending"
            return {"key": args[1]}
        if "SELECT status FROM effect_claims" in flat:
            status = self.claims.get((args[0], args[1]))
            return None if status is None else {"status": status}
        return await super().fetchrow(sql, *args)

    async def execute(self, sql: str, *args: Any) -> Any:
        flat = " ".join(sql.split())
        key = (args[0], args[1]) if len(args) >= 2 else None
        if "UPDATE effect_claims SET status = 'sent'" in flat and key in self.claims:
            self.claims[key] = "sent"
        elif "DELETE FROM effect_claims" in flat and self.claims.get(key) == "sending":
            del self.claims[key]
        return await super().execute(sql, *args)


@pytest.fixture
def claims():
    fake = ClaimsBackend()
    previous = state.set_backend(fake)
    try:
        yield fake
    finally:
        state.set_backend(previous)


def _mock(monkeypatch, module, handler):
    real = httpx.AsyncClient

    def factory(*a, **k):
        k.pop("transport", None)
        return real(*a, transport=httpx.MockTransport(handler), **k)

    monkeypatch.setattr(module.httpx, "AsyncClient", factory)


# ------------------------------------------------------------ the run itself

def test_run_key_is_the_named_run_else_the_params():
    assert idempotency.current_run() is None
    by_params = idempotency.run_key("a.b", {"x": 1, "y": "z"})
    assert by_params == idempotency.run_key("a.b", {"y": "z", "x": 1})
    assert by_params != idempotency.run_key("a.b", {"x": 2, "y": "z"})
    assert by_params != idempotency.run_key("a.c", {"x": 1, "y": "z"})
    with idempotency.running("proposal:7"):
        assert idempotency.run_key("a.b", {"x": 1}) == "proposal:7"
    assert idempotency.current_run() is None


async def test_an_approved_card_runs_under_its_proposal_id(memory_backend,
                                                           monkeypatch):
    """Through the real executor: the key an action sees is the card's."""
    seen: list[str | None] = []

    async def run(n: int) -> ActionResult:
        seen.append(idempotency.current_run())
        return ActionResult(True, "done")

    async def dry(n: int) -> ActionResult:
        return ActionResult(True, "would")

    act = Action(name="ra_b7_probe.touch", tier=Tier.ASK, summary="probe",
                 run=run, dry_run=dry, irreversible=True, params={"n": "n"})
    monkeypatch.setitem(REGISTRY.actions, act.name, act)
    monkeypatch.setitem(REGISTRY.decisions, act.name, classify(act))
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    memory_backend.responses["UPDATE proposals SET status='approved'"] = {
        "action": act.name, "params": json.dumps({"n": 1}), "reason": "r",
        "incident_id": None}
    # What was recorded with the card, read beside the claim (P04, S08).
    memory_backend.responses["SELECT catalog_version, approved_payload_hash"] = {
        "catalog_version": REGISTRY.action_version(act.name),
        "approved_payload_hash": payload_hash({"n": 1}), "dedup_key": None,
        "evidence": "{}", "rollback_plan": ""}

    async def no_outcome(*a, **k):
        return None

    from atlas.kernel import judgment
    monkeypatch.setattr(judgment, "record_outcome", no_outcome)
    verdict = await approvals.decide(41, "approve", via="dashboard")
    assert verdict.executed and verdict.outcome == "executed", verdict
    assert seen == ["proposal:41"]
    assert idempotency.current_run() is None      # does not leak past the run


def test_uncertain_outcomes_are_told_apart_from_refusals():
    for reason in ("timed out", "HTTP 502 from /x", "unreachable: ReadError",
                   "expected JSON, got text/html"):
        assert idempotency.uncertain(reason), reason
    for reason in ("HTTP 400 from /x", "HTTP 429 from /x — rate limited",
                   "unreachable: ConnectError", "WHATSAPP_TOKEN not configured"):
        assert not idempotency.uncertain(reason), reason


# ------------------------------------------------------------ stripe

class FakeStripe:
    """Pays an invoice and updates a charge once per Idempotency-Key,
    replaying the first answer for a repeated key -- what Stripe does."""

    def __init__(self, *, email: str | None = "buyer@example.com",
                 pay_status: int = 200) -> None:
        self.email = email
        self.pay_status = pay_status
        self.charges: list[str] = []            # invoices actually paid
        self.receipts: list[str] = []           # receipt_email updates applied
        self.keys: list[str | None] = []        # Idempotency-Key per pay call
        self.receipt_email: str | None = None
        self._replay: dict[str, httpx.Response] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, key = request.url.path, request.headers.get("Idempotency-Key")
        if request.method == "POST" and path.endswith("/pay"):
            self.keys.append(key)
        if request.method == "POST" and key in self._replay:
            return self._replay[key]
        if request.method == "POST" and path.endswith("/pay"):
            if self.pay_status != 200:
                return httpx.Response(self.pay_status,
                                      json={"error": {"message": "card declined"}})
            self.charges.append(path)
            answer = httpx.Response(200, json={
                "id": "in_1", "status": "paid", "charge": "ch_1",
                "customer": "cus_1"})
        elif request.method == "GET" and path.endswith("/charges/ch_1"):
            return httpx.Response(200, json={
                "id": "ch_1", "customer": "cus_1",
                "receipt_email": self.receipt_email, "receipt_number": None})
        elif request.method == "GET" and path.endswith("/customers/cus_1"):
            return httpx.Response(200, json={"id": "cus_1", "email": self.email})
        elif request.method == "POST" and path.endswith("/charges/ch_1"):
            form = dict(httpx.QueryParams(request.content.decode()))
            self.receipt_email = form["receipt_email"]
            self.receipts.append(form["receipt_email"])
            answer = httpx.Response(200, json={"id": "ch_1",
                                               "receipt_email": self.receipt_email})
        else:
            return httpx.Response(404)
        if key:
            self._replay[key] = answer
        return answer


@pytest.fixture
def stripe(monkeypatch):
    # Stripe watches no business since 2026-10-29; the retry path is
    # business-generic, so the test declares a stand-in one.
    monkeypatch.setattr(stripe_billing, "BUSINESSES", ("flycheap",))
    monkeypatch.setitem(stripe_billing.ENV_FOR, "flycheap", "STRIPE_FLYCHEAP_KEY")
    monkeypatch.setenv(stripe_billing.ENV_FOR["flycheap"], "sk_test_x")
    fake = FakeStripe()
    _mock(monkeypatch, stripe_billing, fake.handler)
    return fake


async def test_a_retried_invoice_run_charges_once(stripe):
    with idempotency.running("proposal:12"):
        first = await stripe_billing.retry_invoice("flycheap", "in_1")
        again = await stripe_billing.retry_invoice("flycheap", "in_1")
    assert first.ok and again.ok
    assert stripe.charges == ["/v1/invoices/in_1/pay"]
    assert stripe.keys[0] and stripe.keys[0] == stripe.keys[1]
    assert stripe.keys[0] == stripe_billing.retry_key("in_1", "proposal:12")


async def test_a_new_card_is_a_new_retry(stripe):
    with idempotency.running("proposal:12"):
        await stripe_billing.retry_invoice("flycheap", "in_1")
    with idempotency.running("proposal:13"):
        await stripe_billing.retry_invoice("flycheap", "in_1")
    assert len(stripe.charges) == 2 and stripe.keys[0] != stripe.keys[1]


async def test_without_a_named_run_the_key_is_still_stable(stripe):
    await stripe_billing.retry_invoice("flycheap", "in_1")
    await stripe_billing.retry_invoice("flycheap", "in_1")
    assert len(stripe.charges) == 1 and stripe.keys[0] == stripe.keys[1]
    assert stripe_billing.retry_key("in_1", "a") != stripe_billing.retry_key("in_2", "a")


# the owner, 2026-09-28: "it should always charge the customer and give them a
# receipt".

async def test_a_paid_retry_emails_the_customer_a_receipt_once(stripe):
    with idempotency.running("proposal:12"):
        first = await stripe_billing.retry_invoice("flycheap", "in_1")
        again = await stripe_billing.retry_invoice("flycheap", "in_1")
    assert stripe.receipts == ["buyer@example.com"]
    assert "receipt emailed for ch_1" in first.detail
    assert again.ok and "receipt already sent" in again.detail


async def test_a_new_card_for_a_paid_charge_sends_no_second_receipt(stripe):
    with idempotency.running("proposal:12"):
        await stripe_billing.retry_invoice("flycheap", "in_1")
    with idempotency.running("proposal:13"):
        await stripe_billing.retry_invoice("flycheap", "in_1")
    assert stripe.receipts == ["buyer@example.com"]


async def test_a_customer_with_no_email_is_still_charged(stripe):
    stripe.email = None
    result = await stripe_billing.retry_invoice("flycheap", "in_1")
    assert result.ok and "no receipt: customer has no email" in result.detail
    assert stripe.receipts == [] and len(stripe.charges) == 1


async def test_a_failed_payment_sends_no_receipt(stripe):
    stripe.pay_status = 402
    result = await stripe_billing.retry_invoice("flycheap", "in_1")
    assert not result.ok and "retry failed" in result.detail
    assert stripe.receipts == [] and stripe.charges == []


def test_retry_invoice_stays_tier_2():
    """A charge cannot be undone, and contracts.py refuses an irreversible
    Tier 1 -- so 'always charge' is still one approval per retry."""
    action, = [a for a in stripe_billing.MODULE.actions()
               if a.name == "stripe.retry_invoice"]
    assert action.tier is Tier.ASK and action.irreversible


# ------------------------------------------------------------ messages.reply

@pytest.fixture
def instagram_inbound(monkeypatch):
    async def fake_fetch_one(sql, *args):
        return {"platform": "instagram", "thread_id": "convo1",
                "recipient_id": "IGSID1", "draft": "on it", "replied_at": None}

    async def fake_execute(sql, *args):
        return "OK"

    sent: list[tuple[str, str]] = []
    outcome = {"reason": None}

    async def fake_send(thread_id, recipient_id, text):
        sent.append((recipient_id, text))
        return ({} if outcome["reason"] is None else None), outcome["reason"]

    monkeypatch.setattr(messages, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(messages, "execute_sql", fake_execute)
    monkeypatch.setattr(instagram_dm, "send", fake_send)
    return sent, outcome


async def test_messages_reply_retried_twice_sends_once(claims, instagram_inbound):
    sent, _ = instagram_inbound
    with idempotency.running("proposal:5"):
        first = await messages.reply(3, "on it")
        again = await messages.reply(3, "on it")
    assert first.ok and again.ok
    assert sent == [("IGSID1", "on it")]
    assert "already sent" in again.detail and again.evidence["deduplicated"]


async def test_messages_reply_in_doubt_is_not_resent(claims, instagram_inbound):
    sent, outcome = instagram_inbound
    outcome["reason"] = "timed out"
    with idempotency.running("proposal:5"):
        first = await messages.reply(3, "on it")
        outcome["reason"] = None
        again = await messages.reply(3, "on it")
    assert not first.ok and not again.ok
    assert len(sent) == 1
    assert "never confirmed" in again.detail


async def test_messages_reply_refused_by_the_provider_may_retry(claims,
                                                               instagram_inbound):
    sent, outcome = instagram_inbound
    outcome["reason"] = "HTTP 400 from /me/messages"
    with idempotency.running("proposal:5"):
        first = await messages.reply(3, "on it")
        outcome["reason"] = None
        again = await messages.reply(3, "on it")
    assert not first.ok and again.ok
    assert len(sent) == 2


async def test_an_unreadable_claim_sends_nothing(memory_backend, instagram_inbound):
    sent, _ = instagram_inbound
    memory_backend.responses["INSERT INTO effect_claims"] = None
    memory_backend.responses["SELECT status FROM effect_claims"] = None

    async def broken(sql, *args):
        raise ConnectionError("db down")

    memory_backend.fetchrow = broken
    result = await messages.reply(3, "on it")
    assert not result.ok and sent == []


# ------------------------------------- messages.reply: the recipient (RA-D1)
#
# The stored `thread_id` is a CONVERSATION id; the Graph send addresses a
# person (IGSID). These run the real resolver and send against a mocked
# Graph API, so the request that would leave the box is what is asserted.

USER_TOKEN = "ig-user-token-SECRET-1234"
PAGE_TOKEN = "ig-page-token-SECRET-5678"


@pytest.fixture
def graph(monkeypatch):
    """A mocked Graph API: one conversation `convo1` whose participants are
    set per test. Returns (requests seen, state) and the row store."""
    from atlas.modules import _http

    monkeypatch.setenv("INSTAGRAM_ACCOUNT_ID", "IGACC1")
    monkeypatch.setenv("INSTAGRAM_TOKEN", USER_TOKEN)
    monkeypatch.setenv("INSTAGRAM_PAGE_ID", "PAGE1")
    monkeypatch.setattr(instagram_dm, "_PAGE_TOKEN", {})
    seen: list[httpx.Request] = []
    world: dict[str, Any] = {"participants": [
        {"id": "PAGE1"}, {"id": "IGACC1", "username": "atlas"},
        {"id": "IGSID777", "username": "friend"}]}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if request.method == "GET" and path.endswith("/PAGE1"):
            return httpx.Response(200, json={"access_token": PAGE_TOKEN})
        if request.method == "GET" and path.endswith("/convo1"):
            assert request.url.params.get("fields") == "participants"
            return httpx.Response(200, json={
                "participants": {"data": world["participants"]}, "id": "convo1"})
        if request.method == "POST" and path.endswith("/PAGE1/messages"):
            body = json.loads(request.content)
            return httpx.Response(200, json={
                "recipient_id": body["recipient"]["id"], "message_id": "mid.1"})
        return httpx.Response(404, json={"error": {"message": "no route"}})

    _mock(monkeypatch, _http, handler)

    row = {"platform": "instagram", "thread_id": "convo1", "recipient_id": None,
           "draft": "on it", "replied_at": None}
    writes: list[tuple[str, tuple]] = []

    async def fake_fetch_one(sql, *args):
        return dict(row)

    async def fake_execute(sql, *args):
        writes.append((" ".join(sql.split()), args))
        if "SET recipient_id" in sql:
            row["recipient_id"] = args[1]
        return "OK"

    monkeypatch.setattr(messages, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(messages, "execute_sql", fake_execute)
    return seen, world, row, writes


def _posts(seen: list[httpx.Request]) -> list[dict]:
    return [json.loads(r.content) for r in seen if r.method == "POST"]


def _no_token_in_urls(seen: list[httpx.Request]) -> None:
    for r in seen:
        assert USER_TOKEN not in str(r.url) and PAGE_TOKEN not in str(r.url)
        assert "access_token" not in r.url.params
        assert r.headers["Authorization"].startswith("Bearer ")


async def test_reply_sends_to_the_participant_igsid_not_the_conversation(
        claims, graph, caplog):
    seen, _world, row, writes = graph
    with idempotency.running("proposal:11"):
        result = await messages.reply(3, "")
    assert result.ok, result.detail
    posts = _posts(seen)
    assert posts == [{"recipient": {"id": "IGSID777"},
                      "message": {"text": "on it"}}]
    assert "convo1" not in json.dumps(posts)
    # cached with the stored conversation, for every row of the thread
    assert row["recipient_id"] == "IGSID777"
    cache, = [a for sql, a in writes if "SET recipient_id" in sql]
    assert cache == ("convo1", "IGSID777", "instagram")
    # read-back ids for the verifier
    assert result.evidence == {"platform": "instagram", "thread_id": "convo1",
                               "recipient_id": "IGSID777", "message_id": "mid.1"}
    _no_token_in_urls(seen)
    assert PAGE_TOKEN not in caplog.text and USER_TOKEN not in caplog.text
    assert PAGE_TOKEN not in result.detail


async def test_a_cached_recipient_skips_the_participants_read(claims, graph):
    seen, _world, row, _writes = graph
    row["recipient_id"] = "IGSID777"
    with idempotency.running("proposal:12"):
        assert (await messages.reply(3, "hi")).ok
    assert not any(r.url.path.endswith("/convo1") for r in seen)
    assert _posts(seen)[0]["recipient"] == {"id": "IGSID777"}


@pytest.mark.parametrize("participants", [
    [{"id": "PAGE1"}, {"id": "IGACC1"}],                   # only us
    [],                                                      # nobody
    [{"id": "IGACC1"}, {"id": "IGSID1"}, {"id": "IGSID2"}],  # ambiguous
])
async def test_no_resolvable_participant_is_refused_and_nothing_sent(
        claims, graph, participants):
    seen, world, row, writes = graph
    world["participants"] = participants
    with idempotency.running("proposal:13"):
        result = await messages.reply(3, "on it")
    assert not result.ok
    assert result.detail.startswith("refused: no recipient for conversation convo1")
    assert _posts(seen) == []
    assert claims.claims == {}          # refused before the claim: nothing held
    assert row["recipient_id"] is None and writes == []
    dry = await messages.reply_dry(3, "on it")
    assert not dry.ok and "no recipient" in dry.detail


async def test_an_unreadable_conversation_is_refused_and_nothing_sent(claims, graph):
    seen, _world, row, _writes = graph
    row["thread_id"] = "gone"           # the mock answers 404 for it
    with idempotency.running("proposal:14"):
        result = await messages.reply(3, "on it")
    assert not result.ok and "refused: no recipient" in result.detail
    assert _posts(seen) == [] and claims.claims == {}
    _no_token_in_urls(seen)
    assert PAGE_TOKEN not in result.detail and USER_TOKEN not in result.detail


async def test_the_claim_still_stops_a_double_send_to_the_participant(claims, graph):
    seen, _world, _row, _writes = graph
    with idempotency.running("proposal:15"):
        first = await messages.reply(3, "on it")
        again = await messages.reply(3, "on it")
    assert first.ok and again.ok
    assert _posts(seen) == [{"recipient": {"id": "IGSID777"},
                             "message": {"text": "on it"}}]
    assert again.evidence["deduplicated"]
    assert again.evidence["recipient_id"] == "IGSID777"


async def test_send_refuses_the_conversation_id_as_a_recipient(graph):
    seen, *_ = graph
    data, reason = await instagram_dm.send("convo1", "convo1", "hi")
    assert data is None and reason.startswith("refused")
    assert _posts(seen) == []


# ------------------------------------------------------------ matrix

@pytest.fixture
def homeserver(monkeypatch):
    monkeypatch.setattr(mx, "HOMESERVER", "https://hs.example")
    monkeypatch.setenv("MATRIX_TOKEN", "mx-token")
    state_ = {"name_status": 200, "name": "Project Falcon"}
    puts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path.endswith("/state/m.room.name"):
            if state_["name_status"] != 200:
                return httpx.Response(state_["name_status"],
                                      json={"errcode": "M_NOT_FOUND"})
            return httpx.Response(200, json={"name": state_["name"]})
        if request.method == "PUT" and "/send/m.room.message/" in path:
            puts.append(path.rsplit("/", 1)[1])
            return httpx.Response(200, json={"event_id": "$ev1"})
        return httpx.Response(500)

    # One patch covers both callers: `_http` and `send_reply` each reach
    # the same `httpx.AsyncClient`.
    import atlas.modules._http as http_mod
    _mock(monkeypatch, http_mod, handler)
    return state_, puts


async def test_matrix_reply_retried_twice_posts_once(claims, homeserver):
    _, puts = homeserver
    with idempotency.running("proposal:30"):
        first = await mx.send_reply("!a:internal.example", "on my way",
                                    room_name="Project Falcon", to="@sam:internal.example")
        again = await mx.send_reply("!a:internal.example", "on my way",
                                    room_name="Project Falcon", to="@sam:internal.example")
    assert first.ok and again.ok, (first, again)
    assert len(puts) == 1
    assert "already sent" in again.detail


async def test_the_matrix_txn_comes_from_the_run_not_the_clock(memory_backend,
                                                               homeserver):
    """Without the claim (the default MemoryBackend grants every one), the
    homeserver still sees the same txn id for the same run -- its own dedup."""
    _, puts = homeserver
    for _ in range(2):
        with idempotency.running("proposal:30"):
            assert (await mx.send_reply("!a:internal.example", "on my way",
                                        room_name="Project Falcon",
                                        to="@sam:internal.example")).ok
    with idempotency.running("proposal:31"):
        await mx.send_reply("!a:internal.example", "on my way", room_name="Project Falcon",
                            to="@sam:internal.example")
    assert puts[0] == puts[1] != puts[2]
    assert puts[0].startswith("atlas-") and puts[0][6:].isalnum()


@pytest.mark.parametrize("to", ["", "   "])
async def test_a_reply_with_no_recipient_is_refused(homeserver, to):
    """RA-C5: it used to default to `@unknown` on the internal homeserver."""
    _, puts = homeserver
    sent = await mx.send_reply("!a:internal.example", "hi", room_name="Project Falcon", to=to)
    dry = await mx.send_dry("!a:internal.example", "hi", room_name="Project Falcon", to=to)
    assert not sent.ok and "recipient" in sent.detail
    assert not dry.ok and "recipient" in dry.detail
    assert puts == []


async def test_a_blank_room_name_is_read_from_the_homeserver(memory_backend,
                                                             homeserver):
    """RA-C5: a card with no room name reaching an attendance room."""
    state_, puts = homeserver
    state_["name"] = "Devs-Attendance"
    sent = await mx.send_reply("!opaque:internal.example", "done", room_name="",
                               to="@sam:internal.example")
    dry = await mx.send_dry("!opaque:internal.example", "done", room_name="",
                            to="@sam:internal.example")
    assert not sent.ok and "attendance bot" in sent.detail
    assert not dry.ok and "attendance bot" in dry.detail
    # A card whose name disagrees with the room's is judged on the room's.
    lied = await mx.send_reply("!opaque:internal.example", "done", room_name="Project Falcon",
                               to="@sam:internal.example")
    assert not lied.ok and puts == []


async def test_an_unnamed_dm_still_posts(memory_backend, homeserver):
    """Most DMs have no m.room.name: 404 is 'unnamed', matched by id alone."""
    state_, puts = homeserver
    state_["name_status"] = 404
    result = await mx.send_reply("!dm:internal.example", "yes", to="@sam:internal.example")
    assert result.ok and len(puts) == 1


async def test_an_unreadable_room_name_refuses(memory_backend, homeserver):
    state_, puts = homeserver
    state_["name_status"] = 500
    result = await mx.send_reply("!a:internal.example", "yes", to="@sam:internal.example")
    assert not result.ok and "could not be read" in result.detail
    assert puts == []


async def test_a_2xx_with_an_unreadable_body_counts_as_sent(claims, monkeypatch):
    """The message is in the room; a retry must not post it again."""
    monkeypatch.setattr(mx, "HOMESERVER", "https://hs.example")
    monkeypatch.setenv("MATRIX_TOKEN", "mx-token")
    puts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"name": "Project Falcon"})
        puts.append(request.url.path.rsplit("/", 1)[1])
        return httpx.Response(200, text="<html>ok</html>")

    import atlas.modules._http as http_mod
    _mock(monkeypatch, http_mod, handler)
    with idempotency.running("proposal:40"):
        first = await mx.send_reply("!a:internal.example", "hi", room_name="Project Falcon",
                                    to="@sam:internal.example")
        again = await mx.send_reply("!a:internal.example", "hi", room_name="Project Falcon",
                                    to="@sam:internal.example")
    assert first.ok and first.rollback_token is None
    assert again.ok and "already sent" in again.detail
    assert len(puts) == 1
