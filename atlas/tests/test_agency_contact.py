"""The website contact form: stored first, delivered by the executor, honest status.

Validation, honeypot, rate limits, escaping, storage, the fan-out over email /
WhatsApp / confirmation with fakes, one channel failing without losing the other,
status reporting, and no secret or visitor text in a log line.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any

import pytest
from fastapi.testclient import TestClient

from atlas.api import agency_contact as api
from atlas.api import main as api_main
from atlas.kernel import agency_contact as tick_mod
from atlas.kernel import executor
from atlas.kernel.contracts import Health, Tier
from atlas.kernel.permissions import classify
from atlas.kernel.registry import REGISTRY
from atlas.modules.agency import contact

EMAIL = "client@example.com"
GOOD = {"name": "Ada Client", "email": EMAIL, "phone": "+1 917 555 0100",
        "message": "I would like a free preview of a site for my bakery.",
        "topic": "preview", "source": "landing"}
SECRET_TOKEN = "SECRET-TOKEN-VALUE-1234567890"
SECRET_PASS = "gmailapppass1234"


# ------------------------------------------------------------------ the fake

class FakeStore:
    """The same behaviour as contact.Store, over a dict."""

    def __init__(self) -> None:
        self.rows: dict[int, dict[str, Any]] = {}
        self.next_id = 1

    async def insert(self, sub, ip_hash, now):
        key = contact.dedupe_key(sub["email"], sub["message"])
        if any(r["dedupe_key"] == key for r in self.rows.values()):
            return None
        i = self.next_id
        self.next_id += 1
        self.rows[i] = {**sub, "id": i, "ip_hash": ip_hash, "dedupe_key": key,
                        "created_at": now, "updated_at": now,
                        "whatsapp_sid": None, "whatsapp_delivered_at": None}
        for ch in contact.CHANNELS:
            self.rows[i].update({f"{ch}_status": "pending", f"{ch}_attempts": 0,
                                 f"{ch}_at": None, f"{ch}_error": None})
        return i

    async def peek(self, cid):
        return dict(self.rows[cid]) if cid in self.rows else None

    async def due(self, limit, now):
        out = [(r["id"], ch) for r in self.rows.values()
               for ch in contact.active_channels() if r[f"{ch}_status"] == "pending"]
        return sorted(out)[:limit]

    async def claim(self, cid, channel, now):
        row = self.rows.get(cid)
        if not row or row[f"{channel}_status"] != "pending":
            return None
        row[f"{channel}_status"] = "sending"
        row[f"{channel}_attempts"] += 1
        return dict(row)

    async def sent(self, cid, channel, now, sid=None):
        row = self.rows[cid]
        row.update({f"{channel}_status": "sent", f"{channel}_at": now,
                    f"{channel}_error": None})
        if sid and channel == "whatsapp":
            row["whatsapp_sid"] = sid

    async def failed(self, cid, channel, reason, now):
        row = self.rows[cid]
        row[f"{channel}_error"] = reason
        row[f"{channel}_status"] = ("failed" if row[f"{channel}_attempts"]
                                    >= contact.MAX_ATTEMPTS else "pending")

    async def skipped(self, cid, channel, reason, now):
        self.rows[cid][f"{channel}_status"] = "skipped"

    async def wa_open(self, limit, now):
        return [dict(r) for r in self.rows.values()
                if r["whatsapp_status"] == "sent" and r["whatsapp_sid"]][:limit]

    async def wa_result(self, cid, status, detail, now):
        row = self.rows[cid]
        if row["whatsapp_status"] == "sent":
            row.update(whatsapp_status=status, whatsapp_error=detail or None)

    async def sent_in_last_hour(self, now):
        return sum(1 for r in self.rows.values()
                   if r["whatsapp_at"] and r["whatsapp_at"] > now - timedelta(hours=1))

    async def health_counts(self, now):
        rows = [r for r in self.rows.values() if r["created_at"] < now - contact.LATE_AFTER]
        bad = ("pending", "sending", "failed")
        return {"email_late": sum(r["email_status"] in bad for r in rows),
                "wa_late": sum(r["whatsapp_status"] in bad for r in rows),
                "slack_late": sum(r["slack_status"] in bad for r in rows),
                "wa_unconfirmed": sum(r["whatsapp_status"] == "sent" for r in rows),
                "total": len(rows)}


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore()
    old = contact.use_store(fake)
    for name in ("_ip", "_email"):
        getattr(api, name)._events.clear()
    monkeypatch.setenv("GMAIL_APP_PASSWORD", SECRET_PASS)
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", SECRET_TOKEN)
    monkeypatch.setenv("TUTORCO_WHATSAPP_FROM", "whatsapp:+14155550000")
    monkeypatch.delenv(contact.OWNER_WA_ENV, raising=False)
    monkeypatch.setenv(contact.WA_ENABLE_ENV, "1")      # the legacy tests cover WhatsApp
    yield fake
    contact.use_store(old)


@pytest.fixture
def client(store):
    return TestClient(api_main.app, client=("203.0.113.9", 40000),
                      follow_redirects=False,
                      headers={"X-Atlas-Public": "1"})


class Outbox:
    def __init__(self) -> None:
        self.mail: list[EmailMessage] = []
        self.wa: list[tuple[str, str]] = []
        self.mail_ok = True
        self.wa_ok = True
        self.ack_ok = True


@pytest.fixture
def out(monkeypatch):
    box = Outbox()

    async def deliver(message, host, port, user, password):
        to = message["To"]
        if (to == contact.OWNER_EMAIL and not box.mail_ok) or (
                to != contact.OWNER_EMAIL and not box.ack_ok):
            return False, "smtp down"
        box.mail.append(message)
        return True, None

    async def send_wa(to, body, media_url=None):
        if not box.wa_ok:
            return False, "HTTP 400 from api.twilio.com (code 63015)"
        box.wa.append((to, body))
        return True, "SM" + "a" * 32

    monkeypatch.setattr("atlas.modules.agency.outreach._deliver", deliver)
    monkeypatch.setattr("atlas.modules.tutorco.notify.send_whatsapp", send_wa)
    return box


@pytest.fixture
def installed(store, monkeypatch, memory_backend):
    REGISTRY.actions.clear()
    REGISTRY.decisions.clear()
    executor._recent.clear()
    for a in contact.actions():
        REGISTRY.actions[a.name] = a
        REGISTRY.decisions[a.name] = classify(a)
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    yield
    REGISTRY.actions.clear()
    REGISTRY.decisions.clear()
    executor._recent.clear()


def _post(client, **extra):
    return client.post(contact.PATH, data={**GOOD, **extra})


# ------------------------------------------------------------------ validation

def test_a_good_form_validates_and_is_cleaned():
    sub, err, _ = contact.validate({**GOOD, "email": " Client@Example.COM "})
    assert err is None and sub["email"] == EMAIL and sub["topic"] == "preview"


@pytest.mark.parametrize("field,value", [
    ("name", ""), ("name", "   "), ("email", "nope"), ("email", "a@b"),
    ("email", "a@example.com\r\nBcc: x@example.com"), ("email", "Name <a@example.com>"),
    ("email", "a" * 300 + "@example.com"), ("message", ""), ("message", "hi"),
    ("message", "x" * 4001), ("phone", "call me maybe"),
])
def test_bad_fields_are_refused_with_the_field_named(field, value):
    sub, err, name = contact.validate({**GOOD, field: value})
    assert sub is None and err and name == field


def test_header_and_bidi_characters_never_survive_in_a_single_line_field():
    sub, _, _ = contact.validate({**GOOD, "name": "Ada\r\nBcc: evil@x.com\u202e"})
    assert "\n" not in sub["name"] and "\r" not in sub["name"]
    assert "\u202e" not in sub["name"]


def test_unknown_topic_and_source_fall_back():
    sub, _, _ = contact.validate({**GOOD, "topic": "<script>", "source": "evil"})
    assert sub["topic"] == contact.DEFAULT_TOPIC and sub["source"] == contact.DEFAULT_SOURCE


def test_phone_is_optional():
    sub, err, _ = contact.validate({**GOOD, "phone": ""})
    assert err is None and sub["phone"] == ""


# ------------------------------------------------------------------ the endpoint

def test_a_post_stores_a_row_and_sends_nothing(client, store, out):
    r = _post(client)
    assert r.status_code == 303 and r.headers["location"] == "/thanks/"
    assert len(store.rows) == 1
    row = store.rows[1]
    assert row["email_status"] == row["whatsapp_status"] == row["ack_status"] == "pending"
    assert out.mail == [] and out.wa == []


def test_fetch_gets_json_and_the_store_page_gets_html(client, store):
    r = client.post(contact.PATH, data={**GOOD, "source": "store-contact"},
                    headers={"Accept": "application/json"})
    assert r.status_code == 200 and r.json()["ok"] is True
    r = client.post(contact.PATH, data={**GOOD, "message": "another question here",
                                        "source": "store-contact"})
    assert r.status_code == 200 and "Message received" in r.text


def test_a_json_post_works_too(client, store):
    r = client.post(contact.PATH, json=GOOD, headers={"Accept": "application/json"})
    assert r.status_code == 200 and len(store.rows) == 1


def test_validation_errors_are_400_with_the_field(client, store):
    r = client.post(contact.PATH, data={**GOOD, "email": "bad"},
                    headers={"Accept": "application/json"})
    assert r.status_code == 400 and r.json()["field"] == "email" and store.rows == {}


def test_the_honeypot_gets_the_same_answer_and_no_row(client, store):
    r = _post(client, website="http://spam.example")
    assert r.status_code == 303 and store.rows == {}


def test_only_form_and_json_posts_and_a_body_cap(client, store):
    assert client.post(contact.PATH, content="x", headers={
        "content-type": "text/plain"}).status_code == 415
    assert client.post(contact.PATH, data={**GOOD, "message": "x" * 20000}
                       ).status_code == 413
    assert store.rows == {}


def test_the_same_message_twice_is_one_row(client, store):
    _post(client)
    r = _post(client)
    assert r.status_code == 303 and len(store.rows) == 1


def test_one_address_is_limited_per_day_but_answered_alike(client, store):
    for i in range(5):
        r = _post(client, message=f"a different message number {i}")
        assert r.status_code == 303
    assert len(store.rows) == 3


def test_a_source_flood_is_stopped(client, store):
    for _ in range(40):
        api._ip.allow("public")
    r = _post(client)
    assert r.status_code == 429 and store.rows == {}


def test_an_unwritable_store_says_nothing_was_saved(client, store, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(store, "insert", boom)
    r = client.post(contact.PATH, data=GOOD, headers={"Accept": "application/json"})
    assert r.status_code == 503 and "Nothing was sent" in r.json()["error"]


def test_the_route_is_public_and_mesh_gating_still_covers_the_rest(client):
    assert api_main._is_public(contact.PATH)
    assert not api_main._is_public("/public/agency/contact/../x")


def test_the_visitors_text_is_escaped_in_the_html_answer(client, store):
    r = client.post(contact.PATH, data={**GOOD, "email": "bad<script>"})
    assert "<script>" not in r.text


# ------------------------------------------------------------------ messages

def _row(**over):
    base = {**GOOD, "id": 7, "created_at": datetime(2026, 11, 14, 12, 0, tzinfo=UTC)}
    return {**base, **over}


def test_the_owner_email_subject_and_body():
    row = _row()
    assert contact.owner_subject(row) == "New message from Ada Client via agency site"
    body = contact.owner_email_body(row)
    assert EMAIL in body and "+1 917 555 0100" in body and "#7" in body
    assert "bakery" in body and "Reply-To" in body


def test_the_whatsapp_text_carries_reply_details_and_is_truncated_and_defanged():
    row = _row(message="see https://evil.example/x " + "y" * 600)
    text = contact.owner_whatsapp_body(row)
    assert EMAIL in text and "+1 917 555 0100" in text and "Reply" in text
    assert "https://" not in text and "…" in text and len(text) < 700


def test_the_ack_repeats_nothing_the_visitor_wrote():
    row = _row(name="Ada", message="SECRET-LINE do evil http://x.example")
    body = contact.ack_body(row)
    assert "Hi Ada," in body and "SECRET-LINE" not in body and "http://x" not in body


def test_a_suspicious_name_gets_a_generic_hello():
    assert contact.ack_body(_row(name="http://x.example now"))[:6] == "Hello,"


# ------------------------------------------------------------------ the action

def test_the_action_is_tier_one_with_a_dry_run_and_rollback():
    (a,) = contact.actions()
    assert classify(a).allowed and classify(a).tier == Tier.AUTO
    assert a.dry_run is not None and a.rollback is not None
    assert a.vet({"contact_id": 1, "channel": "email"}) is None
    assert a.vet({"contact_id": 0, "channel": "email"})
    assert a.vet({"contact_id": 1, "channel": "sms"})


async def _submit():
    sub, _, _ = contact.validate(GOOD)
    return await contact.submit(sub, "h")


async def test_the_email_goes_to_the_owner_with_reply_to_the_visitor(store, out):
    cid = await _submit()
    res = await contact.deliver(cid, "email")
    assert res.ok
    (msg,) = out.mail
    assert msg["To"] == "owner@example.com" and msg["Reply-To"] == EMAIL
    assert msg["Subject"] == "New message from Ada Client via agency site"
    assert store.rows[cid]["email_status"] == "sent"


async def test_the_whatsapp_goes_to_the_owner_number_and_waits_for_delivery(store, out):
    cid = await _submit()
    res = await contact.deliver(cid, "whatsapp")
    assert res.ok and out.wa[0][0] == "+15550100142"
    assert store.rows[cid]["whatsapp_status"] == "sent"          # accepted, not delivered
    assert store.rows[cid]["whatsapp_sid"].startswith("SM")


async def test_the_ack_goes_to_the_visitor_from_the_owner_address(store, out):
    cid = await _submit()
    assert (await contact.deliver(cid, "ack")).ok
    (msg,) = out.mail
    assert msg["To"] == EMAIL and "We got your message" in msg["Subject"]


async def test_one_channel_failing_does_not_lose_the_other(store, out, installed):
    out.wa_ok = False
    await _submit()
    await tick_mod.tick()
    row = store.rows[1]
    assert row["email_status"] == "sent" and row["ack_status"] == "sent"
    assert row["whatsapp_status"] == "pending" and "63015" in row["whatsapp_error"]
    assert len(out.mail) == 2


async def test_a_failing_channel_is_retried_then_marked_failed_with_its_cause(
        store, out, installed):
    out.mail_ok = False
    await _submit()
    for _ in range(contact.MAX_ATTEMPTS + 2):
        await tick_mod.tick()
    row = store.rows[1]
    assert row["email_status"] == "failed" and row["email_attempts"] == contact.MAX_ATTEMPTS
    assert "smtp down" in row["email_error"]
    assert row["whatsapp_status"] == "sent"                  # unaffected


async def test_a_second_tick_sends_nothing_twice(store, out, installed):
    await _submit()
    await tick_mod.tick()
    sent = (len(out.mail), len(out.wa))
    await tick_mod.tick()
    assert (len(out.mail), len(out.wa)) == sent == (2, 1)


async def test_watch_only_refuses_everything_and_the_row_stays_queued(
        store, out, installed, monkeypatch):
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "true")
    await _submit()
    assert await tick_mod.tick() == 0
    assert out.mail == [] and out.wa == []
    assert store.rows[1]["email_status"] == "pending"


async def test_a_channel_that_cannot_be_configured_refuses_and_stays_queued(
        store, out, monkeypatch):
    monkeypatch.delenv("GMAIL_APP_PASSWORD")
    cid = await _submit()
    res = await contact.deliver(cid, "email")
    assert not res.ok and "GMAIL_APP_PASSWORD" in res.detail
    assert store.rows[cid]["email_status"] == "pending" and out.mail == []


async def test_the_whatsapp_hourly_cap_defers_without_losing(store, out, monkeypatch):
    monkeypatch.setenv(contact.WA_CAP_ENV, "1")
    a = await _submit()
    sub, _, _ = contact.validate({**GOOD, "message": "second message for the cap"})
    b = await contact.submit(sub, "h")
    assert (await contact.deliver(a, "whatsapp")).ok
    res = await contact.deliver(b, "whatsapp")
    assert not res.ok and store.rows[b]["whatsapp_status"] == "pending"


async def test_a_dry_run_sends_and_changes_nothing(store, out):
    cid = await _submit()
    res = await contact.deliver_dry(cid, "whatsapp")
    assert res.ok and out.wa == [] and store.rows[cid]["whatsapp_status"] == "pending"


async def test_the_rollback_is_honest_that_nothing_can_be_unsent():
    res = await contact.deliver_rollback("1:email")
    assert res.ok and "cannot be unsent" in res.detail


# ------------------------------------------------------------------ WhatsApp status

async def _accepted(store, out):
    cid = await _submit()
    await contact.deliver(cid, "whatsapp")
    return cid


async def test_delivered_only_when_twilio_says_so(store, out, monkeypatch):
    cid = await _accepted(store, out)

    async def status(sid):
        return "delivered", ""
    monkeypatch.setattr(contact, "twilio_status", status)
    assert await contact.poll_whatsapp() == 1
    assert store.rows[cid]["whatsapp_status"] == "delivered"


async def test_undelivered_is_failed_with_the_twilio_code(store, out, monkeypatch):
    cid = await _accepted(store, out)

    async def status(sid):
        return "undelivered", "Twilio error 63016"
    monkeypatch.setattr(contact, "twilio_status", status)
    await contact.poll_whatsapp()
    row = store.rows[cid]
    assert row["whatsapp_status"] == "failed" and "63016" in row["whatsapp_error"]


async def test_a_message_still_queued_at_twilio_stays_accepted(store, out, monkeypatch):
    cid = await _accepted(store, out)

    async def status(sid):
        return "queued", ""
    monkeypatch.setattr(contact, "twilio_status", status)
    assert await contact.poll_whatsapp() == 0
    assert store.rows[cid]["whatsapp_status"] == "sent"


async def test_an_unreadable_twilio_status_changes_nothing(store, out, monkeypatch):
    cid = await _accepted(store, out)

    async def status(sid):
        return "unknown", "HTTP 500"
    monkeypatch.setattr(contact, "twilio_status", status)
    assert await contact.poll_whatsapp() == 0
    assert store.rows[cid]["whatsapp_status"] == "sent"


# ------------------------------------------------------------------ health

async def test_health_is_unknown_when_a_channel_cannot_be_configured(store, monkeypatch):
    monkeypatch.delenv("TWILIO_AUTH_TOKEN")
    obs = await contact.check_contact_delivery()
    assert obs.health == Health.UNKNOWN and "whatsapp" in obs.detail


async def test_health_is_ok_only_when_recent_messages_reached_both(store, out, slack,
                                                                    monkeypatch):
    monkeypatch.setenv(contact.WA_ENABLE_ENV, "1")
    cid = await _submit()
    await contact.deliver(cid, "email")
    await contact.deliver(cid, "slack")
    await contact.deliver(cid, "whatsapp")
    store.rows[cid]["created_at"] -= timedelta(hours=1)
    assert (await contact.check_contact_delivery()).health == Health.FAILING  # unconfirmed
    store.rows[cid]["whatsapp_status"] = "delivered"
    assert (await contact.check_contact_delivery()).health == Health.OK


async def test_health_fails_when_an_old_message_has_not_been_emailed(store, out, slack):
    cid = await _submit()
    store.rows[cid]["created_at"] -= timedelta(hours=1)
    assert (await contact.check_contact_delivery()).health == Health.FAILING


# ------------------------------------------------------------------ no leaks

async def test_no_secret_and_no_visitor_text_reach_the_log(store, out, installed, caplog):
    caplog.set_level(logging.DEBUG)
    out.mail_ok = False
    out.wa_ok = False
    await _submit()
    await tick_mod.tick()
    text = caplog.text
    for needle in (SECRET_TOKEN, SECRET_PASS, EMAIL, "bakery", "+1 917 555 0100"):
        assert needle not in text


def test_the_scheduler_runs_the_pass():
    from atlas.kernel import scheduler
    assert callable(scheduler._maybe_agency_contact)
    assert tick_mod.interval() == timedelta(minutes=1)


def test_the_action_and_check_are_registered_with_the_agency_module():
    from atlas.modules.agency import MODULE
    assert contact.ACTION in {a.name for a in MODULE.actions()}
    assert "contact_delivery" in {c.name for c in MODULE.checks()}


# ------------------------------------------------------------------ the pages

def test_every_form_posts_to_the_route_and_keeps_the_mail_address_as_text():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    landing = root / "atlas" / "modules" / "agency" / "landing"
    for page in (landing / "index.html", landing / "thanks" / "index.html"):
        html = page.read_text()
        assert 'action="/public/agency/contact"' in html, page
        assert 'name="website"' in html and "owner@example.com" in html, page
    js = (landing / "contact.js").read_text()
    assert "credentials: 'omit'" in js and "application/json" in js
    caddy = (root / "Caddyfile.public").read_text()
    assert "path /public/agency/contact" in caddy and "max_size 16KB" in caddy
    assert "form-action 'self'" in caddy and "script-src 'self'" in caddy


def test_no_form_file_names_the_old_domain():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    files = [root / "atlas/modules/agency/landing/index.html",
             root / "atlas/modules/agency/landing/thanks/index.html",
             root / "atlas/modules/agency/landing/contact.js",
             root / "atlas/modules/agency/contact.py",
             root / "atlas/api/agency_contact.py",
             root / "atlas/modules/academy/assets/contact.js", root / "Caddyfile.public"]
    for f in files:
        assert "mapfront.io" not in f.read_text(), f



# ------------------------------------------------------------------ Slack, not WhatsApp

class Slack:
    """A stand-in for `channels.slack.post_notice`: records what would be posted."""

    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []
        self.ok = True
        self.problem: str | None = None


@pytest.fixture
def slack(store, monkeypatch):
    box = Slack()

    async def post_notice(*, topic, title, body, severity=None):
        if not box.ok:
            return None, "channel_not_found"
        box.posts.append({"topic": topic, "title": title, "body": body})
        return "1763400000.000100", None

    monkeypatch.setattr("atlas.channels.slack.post_notice", post_notice)
    monkeypatch.setattr("atlas.channels.slack.notice_problem", lambda topic: box.problem)
    monkeypatch.delenv(contact.WA_ENABLE_ENV)            # the default: no WhatsApp
    return box


def test_slack_is_a_channel_and_whatsapp_is_off_by_default(monkeypatch):
    monkeypatch.delenv(contact.WA_ENABLE_ENV, raising=False)
    assert "slack" in contact.CHANNELS
    assert contact.active_channels() == ("email", "slack", "ack")
    monkeypatch.setenv(contact.WA_ENABLE_ENV, "1")
    assert "whatsapp" in contact.active_channels()


def test_the_slack_text_is_literal_and_defanged():
    row = {"id": 7, "name": "Ada <!channel>", "email": EMAIL, "phone": "",
           "message": "hi <!here> <@U123> <https://evil.example|click> & more "
                      "see https://evil.example/x"}
    text = contact.owner_slack_body(row)
    for live in ("<!channel>", "<!here>", "<@U123>", "<https://"):
        assert live not in text
    assert "&lt;!channel&gt;" in text and "&amp; more" in text
    assert "https://evil.example" not in text and EMAIL in text and "#7" in text


async def test_a_message_is_posted_to_slack_without_whatsapp(store, slack, out):
    cid = await _submit()
    res = await contact.deliver(cid, "slack")
    assert res.ok and store.rows[cid]["slack_status"] == "sent"
    (post,) = slack.posts
    assert post["topic"] == contact.SLACK_TOPIC and "Ada Client" in post["body"]
    assert out.wa == [] and store.rows[cid]["whatsapp_status"] == "pending"


async def test_a_slack_failure_is_retried_then_failed_and_email_is_untouched(
        store, slack, out, installed):
    slack.ok = False
    cid = await _submit()
    for _ in range(contact.MAX_ATTEMPTS):
        await tick_mod.tick()
    row = store.rows[cid]
    assert row["slack_status"] == "failed" and "channel_not_found" in row["slack_error"]
    assert row["email_status"] == "sent" and out.wa == []


async def test_slack_not_configured_refuses_and_the_message_waits(store, slack, out):
    slack.problem = "SLACK_BOT_TOKEN is not set"
    cid = await _submit()
    res = await contact.deliver(cid, "slack")
    assert not res.ok and store.rows[cid]["slack_status"] == "pending"
    assert slack.posts == []


async def test_the_tick_never_touches_whatsapp_when_it_is_off(store, slack, out, installed):
    await _submit()
    await tick_mod.tick()
    assert out.wa == [] and len(slack.posts) == 1 and len(out.mail) == 2


async def test_a_disabled_whatsapp_channel_is_refused_by_the_action(store, slack, out):
    cid = await _submit()
    res = await contact.deliver(cid, "whatsapp")
    assert not res.ok and out.wa == []


async def test_health_needs_email_and_slack_only(store, slack, out, monkeypatch):
    monkeypatch.delenv("TWILIO_AUTH_TOKEN")              # WhatsApp is not set up at all
    cid = await _submit()
    await contact.deliver(cid, "email")
    await contact.deliver(cid, "slack")
    store.rows[cid]["created_at"] -= timedelta(hours=1)
    obs = await contact.check_contact_delivery()
    assert obs.health == Health.OK and "Slack" in obs.detail


async def test_health_is_unknown_when_slack_cannot_post(store, slack, out):
    slack.problem = "SLACK_BOT_TOKEN is not set"
    obs = await contact.check_contact_delivery()
    assert obs.health == Health.UNKNOWN and "slack" in obs.detail


async def test_health_fails_when_an_old_message_never_reached_slack(store, slack, out):
    cid = await _submit()
    await contact.deliver(cid, "email")
    store.rows[cid]["created_at"] -= timedelta(hours=1)
    assert (await contact.check_contact_delivery()).health == Health.FAILING


async def test_the_slack_dry_run_posts_nothing(store, slack, out):
    cid = await _submit()
    res = await contact.deliver_dry(cid, "slack")
    assert res.ok and slack.posts == [] and store.rows[cid]["slack_status"] == "pending"
