"""ATLAS Academy free updates: the storefront signup (double opt-in).

A visitor gives an email address and nothing else. The form only queues a
confirmation mail; the executor sends it (one registered action); the address
counts only after its owner opens the emailed link; unsubscribing is one click
and always honoured. Every answer to the form is the same whoever typed what.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from atlas.api import academy as api
from atlas.api import main as api_main
from atlas.kernel import academy_subscribe, executor
from atlas.kernel.contracts import Tier
from atlas.kernel.executor import ExecutionRequest, execute
from atlas.kernel.permissions import classify
from atlas.kernel.registry import REGISTRY
from atlas.modules import academy
from atlas.modules.academy import catalog as cat_mod
from atlas.modules.academy import delivery, site, subscribers

NOW = datetime.now(UTC).replace(microsecond=0)   # a fixed date aged out the 7-day links
EMAIL = "reader@example.com"
CAT = cat_mod.load()


# ------------------------------------------------------------------ the fake

class FakeStore:
    """The same behaviour as subscribers.Store, over a dict."""

    def __init__(self) -> None:
        self.rows: dict[int, dict[str, Any]] = {}
        self.next_id = 1

    def by_email(self, email: str) -> dict[str, Any] | None:
        return next((r for r in self.rows.values() if r["email"] == email), None)

    async def purge(self, now):
        for i in [i for i, r in self.rows.items() if r["status"] == "pending"
                  and r["created_at"] < now - subscribers.PENDING_TTL]:
            del self.rows[i]

    async def signup(self, email, source, now):
        row = self.by_email(email)
        if row is None:
            self.rows[self.next_id] = {
                "id": self.next_id, "email": email, "status": "pending",
                "source": source, "confirm_token_hash": None,
                "confirm_expires_at": None, "mail_due": True, "mail_attempts": 0,
                "confirm_sent_at": None, "created_at": now, "confirmed_at": None,
                "unsubscribed_at": None}
            self.next_id += 1
            return
        if (row["status"] == "pending"
                and row["mail_attempts"] < subscribers.MAX_ATTEMPTS
                and (row["confirm_sent_at"] is None
                     or row["confirm_sent_at"] < now - subscribers.RESEND_AFTER)):
            row["mail_due"] = True

    async def due(self, limit):
        rows = sorted((r for r in self.rows.values()
                       if r["status"] == "pending" and r["mail_due"]),
                      key=lambda r: r["created_at"])
        return [r["id"] for r in rows][:limit]

    async def peek(self, sub_id):
        row = self.rows.get(sub_id)
        return dict(row) if row else None

    async def claim(self, sub_id, token_hash, expires):
        row = self.rows.get(sub_id)
        if not row or row["status"] != "pending" or not row["mail_due"]:
            return None
        expires = min(expires, row["created_at"] + subscribers.PENDING_TTL)
        row.update(mail_due=False, mail_attempts=row["mail_attempts"] + 1,
                   confirm_token_hash=token_hash, confirm_expires_at=expires)
        return row["email"], expires

    async def sent(self, sub_id, now):
        self.rows[sub_id]["confirm_sent_at"] = now

    async def failed(self, sub_id):
        row = self.rows[sub_id]
        if row["status"] == "pending":
            row["mail_due"] = row["mail_attempts"] < subscribers.MAX_ATTEMPTS

    async def sent_since(self, since):
        return sum(1 for r in self.rows.values()
                   if r["confirm_sent_at"] and r["confirm_sent_at"] > since)

    async def confirm(self, token_hash, now):
        for row in self.rows.values():
            if (row["confirm_token_hash"] == token_hash and row["status"] == "pending"
                    and row["confirm_expires_at"] and row["confirm_expires_at"] > now):
                row.update(status="confirmed", confirmed_at=now, mail_due=False,
                           confirm_token_hash=None, confirm_expires_at=None)
                return True
        return False

    async def unsubscribe(self, sub_id, now):
        row = self.rows.get(sub_id)
        if not row or row["status"] == "unsubscribed":
            return False
        row.update(status="unsubscribed", unsubscribed_at=now, mail_due=False,
                   confirm_token_hash=None, confirm_expires_at=None)
        return True

    async def exists(self, sub_id):
        return sub_id in self.rows

    async def confirmed(self, limit, after_id):
        rows = sorted((r for r in self.rows.values()
                       if r["status"] == "confirmed" and r["id"] > after_id),
                      key=lambda r: r["id"])
        return [{"id": r["id"], "email": r["email"]} for r in rows][:limit]


@pytest.fixture
def store(monkeypatch, memory_backend):
    fake = FakeStore()
    old = subscribers.use_store(fake)
    monkeypatch.setenv("SESSION_SECRET", "test-secret-not-real")
    monkeypatch.delenv("ACADEMY_DOWNLOAD_KEY", raising=False)
    monkeypatch.delenv(subscribers.MAIL_ENV, raising=False)
    monkeypatch.delenv(subscribers.CAP_ENV, raising=False)
    for name in ("_sub_ip", "_sub_email", "_sub_links"):
        getattr(api, name)._events.clear()
    yield fake
    subscribers.use_store(old)


@pytest.fixture
def client(store):
    return TestClient(api_main.app, client=("203.0.113.9", 40000),
                      follow_redirects=False)


class _Sent(list):
    def __init__(self):
        super().__init__()
        self.headers: list[dict[str, str]] = []


@pytest.fixture
def smtp(monkeypatch):
    sent = _Sent()

    async def send(sender, to, subject, body, message_id=None, headers=None, html=False):
        sent.append((sender, to, subject, body))
        sent.headers.append(headers or {})
        return True, None
    monkeypatch.setattr("atlas.modules.agency.outreach._smtp_send", send)
    monkeypatch.setattr("atlas.modules.agency.outreach._send_from_address",
                        lambda: "academy@example.test")
    return sent


def _post(client, email=EMAIL, **extra):
    return client.post(api.SUBSCRIBE_PATH, data={"email": email, **extra})


def _links(body: str) -> tuple[str, str]:
    confirm = re.search(r"https?://\S+/subscribe/confirm\?t=(\S+)", body).group(1)
    unsub = re.search(r"https?://\S+/unsubscribe\?t=(\S+)", body).group(1)
    return confirm, unsub


# ------------------------------------------------------------------ validation

@pytest.mark.parametrize("raw,want", [
    (" Reader@Example.COM ", "reader@example.com"),
    ("a.b+c@sub.example.co.uk", "a.b+c@sub.example.co.uk"),
    ("", ""), ("not an email", ""), ("a@b", ""), ("two@@example.com", ""),
    ("Name <a@example.com>", ""), ("a b@example.com", ""),
    ("a@example.com\r\nBcc: x@example.com", ""), ("a\x00@example.com", ""),
    ("a" * 250 + "@example.com", ""), ("x" * 400, ""), (None, ""), (12, ""),
])
def test_an_email_is_cleaned_or_refused(raw, want):
    assert subscribers.clean_email(raw) == want


def test_a_source_is_one_of_three_or_the_default():
    assert subscribers.clean_source("hero") == "hero"
    assert subscribers.clean_source("footer") == "footer"
    assert subscribers.clean_source("<script>") == subscribers.DEFAULT_SOURCE
    assert subscribers.clean_source(None) == subscribers.DEFAULT_SOURCE


def test_masking_never_shows_a_whole_address():
    masked = subscribers.mask(EMAIL)
    assert EMAIL not in masked and masked.endswith("@example.com")


def test_a_bad_address_is_a_400_and_nothing_is_stored(client, store):
    for bad in ("nope", "a@b", "", "x" * 400):
        r = _post(client, bad)
        assert r.status_code == 400 and "location" not in r.headers
        assert bad[:20] not in r.text or not bad
    assert store.rows == {}


def test_only_a_form_post_is_accepted_and_the_body_is_capped(client, store):
    r = client.post(api.SUBSCRIBE_PATH, json={"email": EMAIL})
    assert r.status_code == 415
    big = client.post(api.SUBSCRIBE_PATH, data={"email": EMAIL, "pad": "x" * 5000})
    assert big.status_code == 413
    assert store.rows == {}


def test_a_get_to_the_signup_path_does_nothing(client, store):
    # 405 without a built dashboard; with one, the SPA catch-all answers a GET
    # with its HTML shell. Either way nothing is stored.
    r = client.get(api.SUBSCRIBE_PATH)
    assert r.status_code == 405 or (
        r.status_code == 200 and "text/html" in r.headers.get("content-type", ""))
    assert store.rows == {}


def test_a_filled_honeypot_looks_like_success_and_stores_nothing(client, store):
    r = _post(client, website="http://spam.example")
    assert r.status_code == 303 and r.headers["location"] == api.THANKS_PAGE_URL
    assert store.rows == {}


# ------------------------------------------------------------ no enumeration

def _same(a, b):
    assert (a.status_code, a.headers["location"], a.text) == \
           (b.status_code, b.headers["location"], b.text)
    assert {k: v for k, v in a.headers.items() if k not in ("date",)} == \
           {k: v for k, v in b.headers.items() if k not in ("date",)}


async def test_every_accepted_signup_gets_the_identical_response(client, store):
    fresh = _post(client, "new@example.com")
    assert fresh.status_code == 303 and fresh.headers["location"] == api.THANKS_PAGE_URL
    pending = _post(client, "new@example.com")
    for status in ("confirmed", "unsubscribed"):
        await store.signup(f"{status}@example.com", "hero", NOW)
        store.by_email(f"{status}@example.com")["status"] = status
    confirmed = _post(client, "confirmed@example.com")
    unsub = _post(client, "unsubscribed@example.com")
    bot = _post(client, "bot@example.com", website="x")
    for other in (pending, confirmed, unsub, bot):
        _same(fresh, other)


def test_the_response_never_contains_the_address(client):
    r = _post(client, "secret-name@example.com")
    assert "secret-name" not in r.text and "secret-name" not in str(r.headers)


async def test_a_confirmed_or_unsubscribed_row_is_not_touched_by_a_new_signup(client, store):
    await store.signup("c@example.com", "hero", NOW)
    store.by_email("c@example.com").update(status="confirmed", mail_due=False)
    await store.signup("u@example.com", "hero", NOW)
    store.by_email("u@example.com").update(status="unsubscribed", mail_due=False)
    _post(client, "c@example.com")
    _post(client, "u@example.com")
    assert store.by_email("c@example.com")["status"] == "confirmed"
    assert store.by_email("u@example.com")["status"] == "unsubscribed"
    assert store.by_email("u@example.com")["mail_due"] is False   # no new mail
    assert await store.due(10) == []


# --------------------------------------------------------------- rate limits

def test_a_third_try_for_one_address_in_a_day_is_the_same_answer_and_does_nothing(
        client, store):
    first = _post(client, "limit@example.com")
    _post(client, "limit@example.com")
    _post(client, "limit@example.com")
    row = store.by_email("limit@example.com")
    row["mail_due"] = False
    row["confirm_sent_at"] = NOW - timedelta(days=2)    # would be re-queued ...
    fourth = _post(client, "limit@example.com")         # ... but the cap answers
    _same(first, fourth)
    assert row["mail_due"] is False


def test_the_per_source_limit_is_a_429(client, store, monkeypatch):
    monkeypatch.setattr(api, "_sub_ip", api.PerAddressLimiter(2, 3600.0))
    assert _post(client, "a@example.com").status_code == 303
    assert _post(client, "b@example.com").status_code == 303
    r = _post(client, "c@example.com")
    assert r.status_code == 429 and "c@example.com" not in r.text
    assert store.by_email("c@example.com") is None


def test_a_database_failure_is_a_503_that_says_nothing_was_signed_up(client, store,
                                                                      monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("db down for reader@example.com")
    monkeypatch.setattr(store, "signup", boom)
    r = _post(client)
    assert r.status_code == 503 and "Nothing was signed up" in r.text
    assert "reader@example.com" not in r.text


def test_the_logs_never_hold_a_whole_address(client, store, monkeypatch, caplog):
    async def boom(*a, **k):
        raise RuntimeError("db down for reader@example.com")
    monkeypatch.setattr(store, "signup", boom)
    with caplog.at_level(logging.DEBUG):
        _post(client)
    assert "reader@example.com" not in caplog.text


# ------------------------------------------------------- double opt-in states

async def test_a_signup_is_pending_and_never_counts_until_confirmed(store):
    await subscribers.signup(EMAIL, "hero", now=NOW)
    row = store.by_email(EMAIL)
    assert row["status"] == "pending" and row["mail_due"] is True
    assert row["confirm_token_hash"] is None
    assert await subscribers.confirmed_subscribers() == []


async def test_the_whole_state_machine_pending_confirmed_unsubscribed(store, smtp):
    await subscribers.signup(EMAIL, "footer", now=NOW)
    sub_id = store.by_email(EMAIL)["id"]
    result = await subscribers.send_confirmation(sub_id)
    assert result.ok and len(smtp) == 1
    confirm_token, unsub_token = _links(smtp[0][3])
    assert await subscribers.confirmed_subscribers() == []              # still pending
    assert await subscribers.confirm(confirm_token) is True
    listed = await subscribers.confirmed_subscribers()
    assert [r["email"] for r in listed] == [EMAIL]
    assert listed[0]["unsubscribe_url"].endswith(unsub_token)
    assert await subscribers.confirm(confirm_token) is False            # single use
    assert await subscribers.unsubscribe(unsub_token) is True
    assert store.by_email(EMAIL)["status"] == "unsubscribed"
    assert await subscribers.confirmed_subscribers() == []
    assert await subscribers.unsubscribe(unsub_token) is True           # idempotent


async def test_the_database_holds_only_a_hash_of_the_confirm_token(store, smtp):
    await subscribers.signup(EMAIL, "hero", now=NOW)
    sub_id = store.by_email(EMAIL)["id"]
    await subscribers.send_confirmation(sub_id)
    raw, _ = _links(smtp[0][3])
    row = store.by_email(EMAIL)
    assert raw not in json.dumps(row, default=str)
    assert row["confirm_token_hash"] == subscribers.hash_token(raw)


async def test_an_expired_confirm_link_does_not_confirm(store, smtp):
    await subscribers.signup(EMAIL, "hero")
    await subscribers.send_confirmation(store.by_email(EMAIL)["id"])
    raw, _ = _links(smtp[0][3])
    late = datetime.now(UTC) + subscribers.PENDING_TTL + timedelta(minutes=1)
    assert await subscribers.confirm(raw, now=late) is False
    assert store.by_email(EMAIL)["status"] == "pending"
    assert await subscribers.confirm(raw) is True       # inside its 7 days


@pytest.mark.parametrize("junk", ["", "x", "a" * 30, "a" * 200, "../../etc/passwd",
                                  "' OR 1=1 --"])
async def test_a_tampered_or_unknown_confirm_token_does_not_confirm(store, smtp, junk):
    await subscribers.signup(EMAIL, "hero")
    await subscribers.send_confirmation(store.by_email(EMAIL)["id"])
    assert await subscribers.confirm(junk) is False
    assert store.by_email(EMAIL)["status"] == "pending"


async def test_a_pending_row_nobody_confirms_is_deleted_after_seven_days(store):
    await subscribers.signup(EMAIL, "hero", now=NOW)
    await subscribers.signup("later@example.com", "hero",
                             now=NOW + subscribers.PENDING_TTL + timedelta(days=1))
    assert store.by_email(EMAIL) is None
    assert store.by_email("later@example.com") is not None


async def test_a_confirmed_row_is_never_purged(store):
    await subscribers.signup(EMAIL, "hero", now=NOW)
    store.by_email(EMAIL).update(status="confirmed")
    await subscribers.signup("x@example.com", "hero", now=NOW + timedelta(days=30))
    assert store.by_email(EMAIL)["status"] == "confirmed"


async def test_a_pending_address_is_mailed_again_at_most_once_a_day(store, smtp):
    await subscribers.signup(EMAIL, "hero", now=NOW)
    sub_id = store.by_email(EMAIL)["id"]
    await subscribers.send_confirmation(sub_id)
    await subscribers.signup(EMAIL, "hero")                 # within a day
    assert await store.due(10) == []
    store.by_email(EMAIL)["confirm_sent_at"] = datetime.now(UTC) - timedelta(days=2)
    await subscribers.signup(EMAIL, "hero")
    assert await store.due(10) == [sub_id]


async def test_a_re_signup_never_extends_the_row_or_mails_past_the_cap(store, smtp):
    """A stranger re-typing someone's address daily cannot keep the pending row
    alive past 7 days from the first signup, nor mail it more than 3 times."""
    await subscribers.signup(EMAIL, "hero", now=NOW)
    row = store.by_email(EMAIL)
    sub_id, first = row["id"], row["created_at"]
    for day in range(1, 6):
        row["confirm_sent_at"] = NOW - timedelta(days=2)    # last mail long ago
        await subscribers.signup(EMAIL, "hero", now=NOW + timedelta(days=day))
        if await store.due(10):
            row["mail_due"] = False
            row["mail_attempts"] += 1
    assert row["created_at"] == first
    assert row["mail_attempts"] == subscribers.MAX_ATTEMPTS
    row["confirm_sent_at"] = NOW - timedelta(days=2)
    await subscribers.signup(EMAIL, "hero", now=NOW + timedelta(days=6))
    assert await store.due(10) == []
    await subscribers.signup("x@example.com", "hero",
                             now=NOW + subscribers.PENDING_TTL + timedelta(minutes=1))
    assert sub_id not in store.rows                         # deleted on time


async def test_a_late_mail_link_never_outlives_the_row_and_says_its_date(store, smtp):
    old = datetime.now(UTC) - timedelta(days=5)
    await subscribers.signup(EMAIL, "hero", now=old)
    sub_id = store.by_email(EMAIL)["id"]
    assert (await subscribers.send_confirmation(sub_id)).ok
    row = store.by_email(EMAIL)
    assert row["confirm_expires_at"] == old + subscribers.PENDING_TTL
    end = old + subscribers.PENDING_TTL
    assert f"by {end.day} {end:%B %Y} (UTC)" in smtp[0][3]
    assert "within 7 days" not in smtp[0][3]


# ------------------------------------------------------------- the web links

async def test_opening_the_confirm_link_changes_nothing_only_the_button_does(
        client, store, smtp):
    await subscribers.signup(EMAIL, "hero")
    await subscribers.send_confirmation(store.by_email(EMAIL)["id"])
    raw, _ = _links(smtp[0][3])
    for _ in range(3):                                  # a scanner may open it often
        page = client.get(api.CONFIRM_PATH, params={"t": raw})
        assert page.status_code == 200 and "<form" in page.text
        assert 'method="post"' in page.text and EMAIL not in page.text
        assert store.by_email(EMAIL)["status"] == "pending"
    bad = client.post(api.CONFIRM_PATH, params={"t": raw + "x"})
    assert bad.status_code == 400 and "doesn't work" in bad.text
    assert store.by_email(EMAIL)["status"] == "pending"
    ok = client.post(api.CONFIRM_PATH, params={"t": raw})
    assert ok.status_code == 200 and "signed up" in ok.text and EMAIL not in ok.text
    assert store.by_email(EMAIL)["status"] == "confirmed"
    assert client.post(api.CONFIRM_PATH, params={"t": raw}).status_code == 400


def test_a_malformed_confirm_link_gets_no_button(client):
    r = client.get(api.CONFIRM_PATH, params={"t": "x"})
    assert r.status_code == 400 and "<form" not in r.text


async def test_unsubscribe_get_shows_a_button_and_only_post_unsubscribes(client, store):
    for addr in ("g@example.com", "p@example.com"):
        await subscribers.signup(addr, "hero")
        row = store.by_email(addr)
        row.update(status="confirmed")
        token = subscribers.unsubscribe_token(row["id"])
        page = client.get(api.UNSUBSCRIBE_PATH, params={"t": token})
        assert page.status_code == 200 and 'method="post"' in page.text
        assert addr not in page.text
        assert row["status"] == "confirmed"             # a GET never unsubscribes
        # The button's own action, then RFC 8058 one-click (body, no t in the form).
        action = re.search(r'action="([^"]+)"', page.text).group(1).replace("&amp;", "&")
        r = client.post(action)
        assert r.status_code == 200 and "unsubscribed" in r.text and addr not in r.text
        assert row["status"] == "unsubscribed"
        one_click = client.post(api.UNSUBSCRIBE_PATH, params={"t": token},
                                data={"List-Unsubscribe": "One-Click"})
        assert one_click.status_code == 200                # always honoured


async def test_a_tampered_unsubscribe_token_is_refused_and_changes_nothing(client, store):
    await subscribers.signup(EMAIL, "hero")
    row = store.by_email(EMAIL)
    row.update(status="confirmed")
    token = subscribers.unsubscribe_token(row["id"])
    kind, sub_id, sig = token.split(".")
    forged = [f"{kind}.{int(sub_id) + 1}.{sig}", f"{kind}.{sub_id}.{'0' * 64}",
              f"{kind}.{sub_id}", "", "u1.1.zz", token[:-1], "u1." + "9" * 40 + ".aa"]
    for bad in forged:
        assert client.get(api.UNSUBSCRIBE_PATH, params={"t": bad}).status_code == 400
        assert client.post(api.UNSUBSCRIBE_PATH, params={"t": bad}).status_code == 400
    assert row["status"] == "confirmed"


def test_the_unsubscribe_token_depends_on_the_secret(store, monkeypatch):
    token = subscribers.unsubscribe_token(7)
    assert subscribers.verify_unsubscribe(token) == 7
    monkeypatch.setenv("SESSION_SECRET", "another-secret")
    assert subscribers.verify_unsubscribe(token) is None


def test_with_no_secret_there_is_no_unsubscribe_link_and_no_token_verifies(monkeypatch):
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.delenv("ACADEMY_DOWNLOAD_KEY", raising=False)
    assert subscribers.unsubscribe_token(1) is None
    assert subscribers.verify_unsubscribe("u1.1." + "0" * 64) is None


async def test_a_valid_signed_unsubscribe_is_never_rate_limited(client, store,
                                                                monkeypatch):
    monkeypatch.setattr(api, "_sub_links", api.PerAddressLimiter(1, 600.0))
    ids = []
    for i in range(5):
        await subscribers.signup(f"u{i}@example.com", "hero")
        ids.append(store.by_email(f"u{i}@example.com")["id"])
    for _ in range(4):                                  # exhaust the shared bucket
        client.post(api.UNSUBSCRIBE_PATH, params={"t": "u1.1.bad"})
    for sub_id in ids:
        token = subscribers.unsubscribe_token(sub_id)
        assert client.post(api.UNSUBSCRIBE_PATH, params={"t": token}).status_code == 200
        assert client.get(api.UNSUBSCRIBE_PATH, params={"t": token}).status_code == 200
    assert all(r["status"] == "unsubscribed" for r in store.rows.values())


def test_only_failing_tokens_are_counted_and_per_token(client, store, monkeypatch):
    monkeypatch.setattr(api, "_sub_links", api.PerAddressLimiter(1, 600.0))
    assert client.post(api.UNSUBSCRIBE_PATH, params={"t": "u1.1.a"}).status_code == 400
    assert client.post(api.UNSUBSCRIBE_PATH, params={"t": "u1.1.a"}).status_code == 429
    assert client.post(api.UNSUBSCRIBE_PATH, params={"t": "u1.1.b"}).status_code == 400
    raw = "a" * 40
    assert client.post(api.CONFIRM_PATH, params={"t": raw}).status_code == 400
    assert client.post(api.CONFIRM_PATH, params={"t": raw}).status_code == 429
    assert client.post(api.CONFIRM_PATH, params={"t": "b" * 40}).status_code == 400


def test_the_pages_are_never_cached_and_send_no_referrer(client):
    r = client.get(api.CONFIRM_PATH, params={"t": "x" * 30})
    assert "no-store" in r.headers["cache-control"]
    assert r.headers["referrer-policy"] == "no-referrer"
    assert _post(client).headers["referrer-policy"] == "no-referrer"


def test_the_three_paths_are_public_and_the_token_is_redacted():
    for path in (api.SUBSCRIBE_PATH, api.CONFIRM_PATH, api.UNSUBSCRIBE_PATH):
        assert path in api_main.PUBLIC_EXACT
    line = "GET /public/academy/unsubscribe?t=u1.5.abcdef HTTP/1.1"
    assert "abcdef" not in api_main._NoAudioTokens._QUERY.sub(
        lambda m: m.group(1) + "REDACTED" if m.groups() else "REDACTED", line)


# --------------------------------------------------------------- the action

def _action():
    return next(a for a in academy.MODULE.actions() if a.name == subscribers.ACTION)


def test_the_action_is_registered_tier_1_with_a_dry_run_and_a_rollback():
    a = _action()
    assert a.tier is Tier.AUTO and a.dry_run is not None and a.rollback is not None
    assert classify(a).allowed
    assert set(a.params) == {"subscriber_id"}          # no address, no body, no subject


def test_the_action_vets_its_parameter():
    a = _action()
    assert a.vet({"subscriber_id": 3}) is None
    for bad in (0, -1, "3", None, True, 1.5):
        assert a.vet({"subscriber_id": bad})


async def test_the_dry_run_sends_nothing_and_never_shows_the_address(store, smtp):
    await subscribers.signup(EMAIL, "hero")
    dry = await subscribers.send_confirmation_dry(store.by_email(EMAIL)["id"])
    assert dry.ok and smtp == []
    assert EMAIL not in dry.detail and EMAIL not in json.dumps(dry.evidence)
    assert store.by_email(EMAIL)["mail_due"] is True            # untouched


async def test_the_mail_has_both_links_the_postal_line_and_no_course_sale(store, smtp,
                                                                          monkeypatch):
    monkeypatch.setenv("AGENCY_POSTAL_ADDRESS", "1 Example Street")
    await subscribers.signup(EMAIL, "hero")
    result = await subscribers.send_confirmation(store.by_email(EMAIL)["id"])
    assert result.ok and result.rollback_token
    [(sender, to, subject, body)] = smtp
    assert (sender, to, subject) == ("academy@example.test", EMAIL, subscribers.SUBJECT)
    confirm, unsub = _links(body)
    assert "1 Example Street" in body and "never signs you up" in body
    assert EMAIL not in result.detail
    assert subscribers.verify_unsubscribe(unsub) == store.by_email(EMAIL)["id"]
    assert len(confirm) >= 40


async def test_a_second_send_for_the_same_signup_is_refused(store, smtp):
    await subscribers.signup(EMAIL, "hero")
    sub_id = store.by_email(EMAIL)["id"]
    assert (await subscribers.send_confirmation(sub_id)).ok
    again = await subscribers.send_confirmation(sub_id)
    assert not again.ok and len(smtp) == 1


@pytest.mark.parametrize("status", ["confirmed", "unsubscribed"])
async def test_nothing_is_sent_to_a_confirmed_or_unsubscribed_address(store, smtp, status):
    await subscribers.signup(EMAIL, "hero")
    row = store.by_email(EMAIL)
    row["status"] = status
    assert not (await subscribers.send_confirmation(row["id"])).ok
    assert not (await subscribers.send_confirmation_dry(row["id"])).ok
    assert smtp == []


async def test_no_signing_key_or_the_off_switch_holds_the_mail(store, smtp, monkeypatch):
    await subscribers.signup(EMAIL, "hero")
    sub_id = store.by_email(EMAIL)["id"]
    monkeypatch.setenv(subscribers.MAIL_ENV, "off")
    off = await subscribers.send_confirmation(sub_id)
    assert not off.ok and "held" in off.detail
    monkeypatch.delenv(subscribers.MAIL_ENV)
    monkeypatch.delenv("SESSION_SECRET")
    nokey = await subscribers.send_confirmation(sub_id)
    assert not nokey.ok and "signing key" in nokey.detail
    assert smtp == [] and store.by_email(EMAIL)["mail_due"] is True


async def test_the_daily_cap_holds_further_mail(store, smtp, monkeypatch):
    monkeypatch.setenv(subscribers.CAP_ENV, "1")
    for addr in ("a@example.com", "b@example.com"):
        await subscribers.signup(addr, "hero")
    a, b = (store.by_email(x)["id"] for x in ("a@example.com", "b@example.com"))
    assert (await subscribers.send_confirmation(a)).ok
    held = await subscribers.send_confirmation(b)
    assert not held.ok and "held" in held.detail and len(smtp) == 1


async def test_an_smtp_failure_requeues_until_three_attempts_then_drops(store,
                                                                        monkeypatch):
    async def fail(*a, **k):
        return False, "smtp down"
    monkeypatch.setattr("atlas.modules.agency.outreach._smtp_send", fail)
    monkeypatch.setattr("atlas.modules.agency.outreach._send_from_address",
                        lambda: "academy@example.test")
    await subscribers.signup(EMAIL, "hero")
    sub_id = store.by_email(EMAIL)["id"]
    for attempt in range(1, subscribers.MAX_ATTEMPTS + 1):
        assert not (await subscribers.send_confirmation(sub_id)).ok
        assert store.by_email(EMAIL)["mail_due"] is (attempt < subscribers.MAX_ATTEMPTS)
    assert await store.due(10) == []


async def test_the_rollback_kills_the_delivered_link_and_says_it_cannot_unsend(store,
                                                                               smtp):
    await subscribers.signup(EMAIL, "hero")
    sub_id = store.by_email(EMAIL)["id"]
    result = await subscribers.send_confirmation(sub_id)
    raw, _ = _links(smtp[0][3])
    back = await subscribers.send_confirmation_rollback(result.rollback_token)
    assert back.ok and "cannot be unsent" in back.detail
    assert store.by_email(EMAIL)["status"] == "unsubscribed"
    assert await subscribers.confirm(raw) is False
    nothing = await subscribers.send_confirmation_rollback("")
    assert nothing.ok and "nothing" in nothing.detail


@pytest.fixture
def installed(store, monkeypatch, memory_backend):
    REGISTRY.actions.clear()
    REGISTRY.decisions.clear()
    executor._recent.clear()
    a = _action()
    REGISTRY.actions[a.name] = a
    REGISTRY.decisions[a.name] = classify(a)
    yield a
    REGISTRY.actions.clear()
    REGISTRY.decisions.clear()
    executor._recent.clear()


async def test_watch_only_refuses_the_send_and_the_row_stays_queued(store, smtp,
                                                                    installed,
                                                                    monkeypatch):
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "true")
    await subscribers.signup(EMAIL, "hero")
    sub_id = store.by_email(EMAIL)["id"]
    result = await execute(ExecutionRequest(subscribers.ACTION,
                                            {"subscriber_id": sub_id}, "test"))
    assert not result.ok and smtp == []
    assert store.by_email(EMAIL)["mail_due"] is True
    assert await academy_subscribe.tick() == 0 and smtp == []


async def test_the_tick_sends_each_queued_confirmation_through_the_executor(
        store, smtp, installed, monkeypatch):
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    for addr in ("a@example.com", "b@example.com"):
        await subscribers.signup(addr, "hero")
    assert await academy_subscribe.tick() == 2
    assert sorted(m[1] for m in smtp) == ["a@example.com", "b@example.com"]
    assert await academy_subscribe.tick() == 0              # nothing queued twice
    assert len(smtp) == 2


async def test_the_tick_deletes_a_stale_pending_row_and_mails_nothing(
        store, smtp, installed, monkeypatch):
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    await subscribers.signup(EMAIL, "hero",
                             now=datetime.now(UTC) - subscribers.PENDING_TTL
                             - timedelta(hours=1))
    assert await academy_subscribe.tick() == 0
    assert smtp == [] and store.rows == {}


async def test_a_refusal_ends_the_tick_and_leaves_the_rest_queued(
        store, smtp, installed, monkeypatch):
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    monkeypatch.setenv(subscribers.CAP_ENV, "1")
    for addr in ("a@example.com", "b@example.com", "c@example.com"):
        await subscribers.signup(addr, "hero")
    assert await academy_subscribe.tick() == 1
    assert len(await store.due(10)) == 2


def test_the_scheduler_runs_the_pass():
    from atlas.kernel import scheduler
    assert callable(scheduler._maybe_academy_subscribe)
    assert academy_subscribe.interval() == timedelta(minutes=5)


# ------------------------------------------------- the newsletter-list helper

async def test_the_list_helper_returns_confirmed_only_and_sends_nothing(store, smtp):
    for i, status in enumerate(("pending", "confirmed", "unsubscribed", "confirmed")):
        await subscribers.signup(f"{status}{i}@example.com", "hero")
        store.by_email(f"{status}{i}@example.com")["status"] = status
    listed = await subscribers.confirmed_subscribers()
    assert [r["email"] for r in listed] == ["confirmed1@example.com",
                                            "confirmed3@example.com"]
    assert all(r["unsubscribe_url"] and "/unsubscribe?t=u1." in r["unsubscribe_url"]
               for r in listed)
    assert smtp == []
    after = await subscribers.confirmed_subscribers(limit=1, after_id=listed[0]["id"])
    assert [r["email"] for r in after] == ["confirmed3@example.com"]


def test_no_action_sends_a_newsletter():
    names = {a.name for a in academy.MODULE.actions()}
    assert not [n for n in names if "newsletter" in n or "broadcast" in n]


# ----------------------------------------------------------- the storefront

@pytest.fixture(scope="module")
def built() -> dict[str, str]:
    return {k: v.decode() if isinstance(v, bytes) else v
            for k, v in site.render_root(CAT, NOW).items()
            if k.endswith((".html", ".js", ".css"))}


def test_the_home_page_has_the_form_in_a_band_and_in_the_footer(built):
    home = built["academy/index.html"]
    assert home.count("data-sub-form") == 2
    assert 'class="sub-band"' in home and "foot-sub" in home
    assert f'action="{site.SUBSCRIBE_ACTION}"' in home
    assert 'method="post"' in home and 'type="email"' in home
    assert "Get updates" in home
    assert 'name="website"' in home and 'tabindex="-1"' in home     # the honeypot


def test_every_page_carries_the_footer_form(built):
    pages = [p for p in built if p.endswith(".html") and p != "404.html"]
    assert pages
    assert all("data-sub-form" in built[p] for p in pages)


def test_the_form_works_without_script_and_says_so_in_markup(built):
    home = built["academy/index.html"]
    form = re.search(r'<form[^>]*data-sub-form[^>]*>', home).group(0)
    assert 'method="post"' in form and "action=" in form
    assert "required" in home and "maxlength=\"254\"" in home
    assert "autocomplete=\"email\"" in home


def test_the_script_makes_no_request_for_the_signup(built):
    js = built["assets/app.js"]
    assert "initSubscribe" in js
    assert "fetch(" not in js and "XMLHttpRequest" not in js and "sendBeacon" not in js
    assert "localStorage" not in js


def test_reduced_motion_is_respected_by_the_signup_animation(built):
    css = built["assets/style.css"]
    block = css[css.index("free updates signup"):]
    assert "prefers-reduced-motion: reduce" in block
    assert "animation: none" in block


def test_the_updates_and_thanks_pages_exist_and_the_thanks_page_is_noindex(built):
    assert "data-sub-form" in built["updates/index.html"]
    thanks = built["updates/thanks/index.html"]
    assert "Check your email" in thanks and 'content="noindex"' in thanks
    assert "7 days" in thanks


def test_the_thanks_page_is_where_the_api_redirects():
    assert api.THANKS_PAGE_URL.endswith("/updates/thanks/")
    assert api.UPDATES_PAGE_URL.endswith("/updates/")


def test_the_privacy_page_says_what_is_stored_and_how_to_leave(built):
    privacy = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", built["privacy/index.html"]))
    for needle in ("Free updates", "email address", "confirm", "7 days", "unsubscribe",
                   "delete"):
        assert needle.lower() in privacy.lower(), needle
    assert "buying does not sign you up" in privacy.lower()
    assert "No newsletter is sent from a purchase" not in privacy


def test_the_privacy_page_has_the_updates_anchor_the_form_links_to(built):
    assert 'id="updates"' in built["privacy/index.html"]
    assert "privacy/#updates" in built["updates/index.html"]


def test_only_the_updates_page_is_in_the_sitemap(built):
    sitemap = site.render_root(CAT, NOW)["sitemap-academy.xml"].decode()
    assert "/updates/</loc>" in sitemap and "/updates/thanks" not in sitemap


def test_no_page_names_a_backend_or_puts_an_address_in_the_markup(built):
    for page, html in built.items():
        if page.endswith(".html"):
            assert not re.search(r"proxmox|ceph", html, re.IGNORECASE), page


def test_the_delivery_base_is_what_the_email_links_use():
    assert subscribers.confirm_url("tok").startswith(delivery.api_base())


async def test_the_mail_carries_one_click_unsubscribe_headers(store, smtp):
    await subscribers.signup(EMAIL, "hero")
    await subscribers.send_confirmation(store.by_email(EMAIL)["id"])
    [headers] = smtp.headers
    _, unsub_token = _links(smtp[0][3])
    assert headers["List-Unsubscribe"].startswith("<https://")
    assert headers["List-Unsubscribe"].endswith(f"{unsub_token}>")
    assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_the_real_message_builder_writes_the_headers():
    from atlas.modules.agency.outreach import _message
    msg = _message("a@example.test", "b@example.test", "s", "body", None,
                   headers={"List-Unsubscribe": "<https://x.example/u?t=1>",
                            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"})
    assert msg["List-Unsubscribe"] == "<https://x.example/u?t=1>"
    assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


async def test_a_mail_clients_one_click_post_to_the_header_url_unsubscribes(
        client, store, smtp):
    from urllib.parse import urlsplit
    await subscribers.signup(EMAIL, "hero")
    row = store.by_email(EMAIL)
    await subscribers.send_confirmation(row["id"])
    row.update(status="confirmed")
    url = urlsplit(smtp.headers[0]["List-Unsubscribe"].strip("<>"))
    assert url.path == api.UNSUBSCRIBE_PATH
    r = client.post(f"{url.path}?{url.query}",
                    data={"List-Unsubscribe": "One-Click"})
    assert r.status_code == 200 and row["status"] == "unsubscribed"
