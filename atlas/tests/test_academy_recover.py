"""ATLAS Academy self-service recovery (F17), the purchase library (F16/F47)
and the read-only Stripe lookups behind them.

A buyer proves a purchase with the address Stripe's receipt went to AND the
receipt number on it, both checked against Stripe on every attempt. A miss
looks exactly like every other miss, whatever was typed; Stripe is only ever
read (GET), and nothing typed reaches a log line.
"""
from __future__ import annotations

import logging
import pathlib
import re
import time

import httpx
import pytest

from atlas.api import academy as api
from atlas.modules.academy import delivery, stripe_read
from tests import academy_shop_fakes as fakes
from tests.academy_shop_fakes import (
    EMAIL,
    OTHER,
    RECEIPT,
    SID,
    SID2,
    _listed,
    _recover,
    _token_of,
)
from tests.test_academy_access import _session

fake, served = fakes.fake, fakes.served              # the shared fixtures

# ------------------------------------------------------------------ normalising

@pytest.mark.parametrize("raw,want", [
    ("1234-5678", "1234-5678"), ("#1234-5678", "1234-5678"),
    ("  #1234-5678 ", "1234-5678"), ("Receipt #1234-5678", "1234-5678"),
    ("1234 - 5678", "1234-5678"), ("abcd-1234", "ABCD-1234"),
    ("", ""), ("12", ""), ("1234-5678'; DROP", ""), ("1234_5678", ""),
    ("1234-5678-9012-3456-7890", ""), ("x" * 80, ""), (None, ""),
])
def test_a_receipt_number_is_normalised_or_refused(raw, want):
    assert stripe_read.normalise_receipt(raw) == want


@pytest.mark.parametrize("raw,want", [
    ("Buyer@Example.com ", "buyer@example.com"),
    ("a.b+c@sub.example.co.uk", "a.b+c@sub.example.co.uk"),
    ("not an email", ""), ("a@b", ""), ("a@b.c", ""), ("", ""),
    ("x@y.com' OR email:'z", ""), ("a" * 250 + "@x.com", ""), (None, ""),
])
def test_an_email_is_normalised_or_refused(raw, want):
    assert stripe_read.normalise_email(raw) == want


def test_the_email_hash_is_of_the_normalised_address():
    assert stripe_read.email_hash(" Buyer@Example.com") == stripe_read.email_hash(EMAIL)
    assert len(stripe_read.email_hash(EMAIL)) == 64 and EMAIL not in \
        stripe_read.email_hash(EMAIL)


# ------------------------------------------------------------------ the lookup

async def test_the_lookup_lists_sessions_by_email_and_matches_the_receipt(fake):
    fake.by_email[EMAIL] = [_listed(SID2, "9999-0000"), _listed()]
    look = await stripe_read.find_by_receipt(EMAIL, RECEIPT)
    assert look.status == "found" and look.session_ids == (SID,)
    first, second = fake.requests                  # as typed, then lowercased
    assert first.url.params["customer_details[email]"] == EMAIL
    req = second
    assert req.method == "GET" and req.url.path == "/v1/checkout/sessions"
    q = req.url.params
    assert q["customer_details[email]"] == EMAIL and q["status"] == "complete"
    assert q["limit"] == "100"
    assert q.get_list("expand[]") == ["data.payment_intent.latest_charge"]
    assert req.headers["authorization"] == "Bearer rk_test_SECRETKEY123"


async def test_a_receipt_on_stripe_with_a_hash_still_matches(fake):
    fake.by_email[EMAIL] = [_listed(receipt="#1234-5678")]
    assert (await stripe_read.find_by_receipt(EMAIL, RECEIPT)).session_ids == (SID,)


@pytest.mark.parametrize("email,receipt", [
    (EMAIL, "9999-0000"),            # known email, wrong receipt
    ("other@example.com", RECEIPT),  # receipt belongs to someone else
    (EMAIL, ""),                     # malformed receipt
    ("", RECEIPT),                   # malformed email
    ("", ""),
])
async def test_every_miss_makes_exactly_two_requests(fake, email, receipt):
    fake.by_email[EMAIL] = [_listed()]
    fake.by_email[stripe_read.SENTINEL_EMAIL] = [_listed()]   # never matches
    look = await stripe_read.find_by_receipt(email, receipt)
    assert look.status == "none" and look.session_ids == ()
    assert len(fake.requests) == 2 and all(r.method == "GET" for r in fake.requests)


@pytest.mark.parametrize("typed,stored", [
    ("Buyer@Example.com", "Buyer@Example.com"),   # Stripe kept the capitals
    ("BUYER@example.com", EMAIL),                 # Stripe holds it lowercased
    (" buyer@example.com ", EMAIL),
])
async def test_the_address_is_asked_as_typed_and_lowercased(fake, typed, stored):
    """Stripe's email filter may be case-sensitive: both forms are asked,
    always two calls, and a session found by both is listed once."""
    fake.by_email[stored] = [_listed()]
    look = await stripe_read.find_by_receipt(typed, RECEIPT)
    assert look.status == "found" and look.session_ids == (SID,)
    asked = [r.url.params["customer_details[email]"] for r in fake.requests]
    assert asked == [typed.strip(), typed.strip().lower()]


async def test_a_charge_without_a_receipt_number_never_matches(fake):
    fake.by_email[EMAIL] = [_listed(receipt=None)]
    assert (await stripe_read.find_by_receipt(EMAIL, "")).status == "none"


@pytest.mark.parametrize("fail,status", [
    (httpx.ConnectError("down"), "unreachable"), (500, "unreachable"),
    (400, "unreachable"), (401, "refused"), (403, "refused"),
])
async def test_stripe_failing_is_said_never_read_as_no_purchase(fake, fail, status):
    fake.fail = fail
    look = await stripe_read.find_by_receipt(EMAIL, RECEIPT)
    assert look.status == status and not look.readable


async def test_no_key_asks_nothing(fake, monkeypatch):
    monkeypatch.delenv(stripe_read.KEY_ENV)
    look = await stripe_read.find_by_receipt(EMAIL, RECEIPT)
    assert look.status == "unconfigured" and not fake.requests


async def test_a_payment_intent_resolves_to_its_checkout(fake):
    fake.by_pi["pi_12345678"] = [_listed()]
    look = await stripe_read.session_for_payment_intent("pi_12345678")
    assert look.status == "found" and look.session_ids == (SID,)
    assert fake.requests[0].url.params["payment_intent"] == "pi_12345678"
    assert (await stripe_read.session_for_payment_intent("pi_00000000")).status == "none"
    before = len(fake.requests)
    assert (await stripe_read.session_for_payment_intent("../x")).status == "none"
    assert len(fake.requests) == before


def test_the_stripe_reader_has_no_way_to_write():
    """Stripe stays read-only: no POST, PUT, PATCH or DELETE anywhere in the
    reader, and nothing that names a write endpoint."""
    src = pathlib.Path(stripe_read.__file__).read_text()
    assert not re.search(r"\.(post|put|patch|delete|request|stream)\(", src)
    assert not re.search(r"method\s*=", src)
    for word in ("/refunds", "/payouts", "/transfers", "capture"):
        assert word not in src



# ------------------------------------------------------------------ recover

def test_email_and_receipt_together_open_a_one_hour_library(served):
    before = time.time()
    r = _recover(served, email=" Buyer@Example.com ", receipt="#1234-5678")
    assert r.status_code == 303 and r.headers["cache-control"] == "no-store"
    token = _token_of(r)
    status, owner, ids = delivery.verify_library(token)
    assert status == "ok" and owner == stripe_read.email_hash(EMAIL) and ids == (SID,)
    expires = int(token.split(".")[2])
    assert before + 3600 - 2 <= expires <= time.time() + 3600
    assert delivery.verify_library(token, now=expires + 1)[0] == "expired"
    # A purchase whose webhook never arrived is recorded now, from Stripe's read.
    inserts = [a for s, a in served.db.sql_log if "INSERT INTO academy_purchases" in s]
    assert inserts and inserts[0][0] == SID and inserts[0][1] == "recover"


def _misses(served) -> list:
    served.fake.by_email[EMAIL] = [_listed()]
    served.verdicts[SID2] = stripe_read.judge(
        _session(refunded=True, amount_refunded=1900), served.cat)
    served.fake.by_email["refunded@example.com"] = [_listed(SID2, "2222-3333")]
    cases = [
        ("unknown@example.com", RECEIPT),        # no purchases at all
        (EMAIL, "9999-0000"),                    # known email, wrong receipt
        (EMAIL, "not a receipt!"),               # malformed receipt
        ("not an email", RECEIPT),               # malformed email
        ("", ""),                                # nothing typed
        (OTHER, RECEIPT),                        # someone else's receipt
        ("refunded@example.com", "2222-3333"),   # a match that isn't paid any more
    ]
    out = []
    for i, (email, receipt) in enumerate(cases):
        before, reads = len(served.fake.requests), len(served.reads)
        r = _recover(served, email=email, receipt=receipt, ip=f"198.51.100.{i}")
        out.append((email, receipt, r, len(served.fake.requests) - before,
                    len(served.reads) - reads))
    return out


def test_every_miss_is_the_same_bytes_and_the_same_stripe_call(served):
    misses = _misses(served)
    first = misses[0][2]
    assert first.status_code == 200
    for email, receipt, r, calls, reads in misses:
        assert r.status_code == first.status_code, (email, receipt)
        assert r.content == first.content, (email, receipt)
        for h in ("content-type", "cache-control", "x-robots-tag", "content-length"):
            assert r.headers.get(h) == first.headers.get(h), (email, receipt, h)
        assert "location" not in r.headers
        assert calls == 2, (email, receipt)       # two list GETs, every path
        # A receipt that matched a checkout no longer paid adds one session
        # read: reachable only with both the email and the receipt number.
        assert reads == (1 if email == "refunded@example.com" else 0), (email, reads)
        assert all(q.method == "GET" for q in served.fake.requests)


def test_the_miss_page_echoes_nothing_typed_and_names_no_product(served):
    text = _misses(served)[1][2].text
    assert "If that receipt matches a purchase" in text and "contact us" in text
    for word in (EMAIL, "buyer@", "9999-0000", RECEIPT, "1234-5678", "unknown@"):
        if word == "1234-5678":
            continue            # the page's own example format, not the input
        assert word not in text, word
    for o in served.cat.offers():
        assert o.name not in text, o.name
    assert "Checked with Stripe at" not in text      # no per-request time


def test_recovery_is_limited_per_address(served):
    for _ in range(5):
        assert _recover(served, receipt="9999-0000").status_code == 200
    assert _recover(served, receipt="9999-0000").status_code == 429


def test_recovery_is_limited_per_email_whatever_the_address(served):
    for i in range(5):
        r = _recover(served, email=f" {EMAIL.upper()}", receipt="9999-0000",
                     ip=f"192.0.2.{i}")
        assert r.status_code == 200
    r = _recover(served, receipt=RECEIPT, ip="192.0.2.99")
    assert r.status_code == 429 and "location" not in r.headers
    # The limiter holds a hash, never the address.
    assert all(EMAIL not in k for k in api._recover_email._events)


@pytest.mark.parametrize("fail", [httpx.ConnectError("down"), 500, 401])
def test_stripe_unreachable_is_a_503_never_no_purchase(served, fail):
    served.fake.fail = fail
    r = _recover(served)
    assert r.status_code == 503
    assert "couldn't check with the payment provider" in r.text.replace("&#x27;", "'")
    assert "If that receipt matches" not in r.text


def test_a_match_whose_checkout_cannot_be_read_is_a_503(served):
    served.verdicts[SID] = stripe_read.Verdict("unreachable", SID)
    assert _recover(served).status_code == 503


def test_only_form_posts_are_taken(served):
    r = served.client.post(api.RECOVER_PATH, json={"email": EMAIL, "receipt": RECEIPT})
    assert r.status_code == 415 and not served.fake.requests


# ------------------------------------------------------------------ library

def _library(served, token):
    return served.client.get(delivery.LIBRARY_PATH, params={"t": token})


def test_the_library_lists_the_buyers_purchases_read_from_stripe(served):
    token = _token_of(_recover(served))
    served.verdicts[SID2] = stripe_read.judge(
        _session(refunded=True, amount_refunded=1900), served.cat)
    served.db.responses["FROM academy_purchases WHERE email IS NOT NULL"] = [
        {"session_id": SID2, "first_seen_at": None}]
    served.reads.clear()
    r = _library(served, token)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert r.headers["x-robots-tag"] == "noindex"
    assert "Claude + Obsidian Vault Kit" in r.text and "19.00 USD" in r.text
    assert "Status: Paid." in r.text and "Status: Refunded." in r.text
    assert delivery.access_url(SID) in r.text.replace("&amp;", "&")
    assert delivery.access_url(SID2) not in r.text.replace("&amp;", "&")  # refunded
    assert set(served.reads) == {SID, SID2}                     # both read fresh
    [(sql, args)] = [(s, a) for s, a in served.db.sql_log
                     if "FROM academy_purchases WHERE email" in s]
    assert args[0] == stripe_read.email_hash(EMAIL) and EMAIL not in str(args)
    assert "sha256(convert_to(lower(btrim(email))" in sql


def test_the_library_shows_only_that_buyers_purchases(served):
    token = _token_of(_recover(served))
    # A row the query returned, but Stripe says it is someone else's: not shown.
    other = stripe_read.judge(_session(), served.cat)
    served.verdicts[SID2] = stripe_read.Verdict(**{**other.__dict__, "email": OTHER})
    served.db.responses["FROM academy_purchases WHERE email IS NOT NULL"] = [
        {"session_id": SID2, "first_seen_at": None}]
    r = _library(served, token)
    assert delivery.access_url(SID2) not in r.text.replace("&amp;", "&")
    assert r.text.count("<li>") == 1


def test_an_unreadable_purchase_record_is_said_not_hidden(served, monkeypatch):
    token = _token_of(_recover(served))

    async def broken(_owner, limit=20):
        raise ConnectionError("db down")
    monkeypatch.setattr(delivery, "purchases_for", broken)
    r = _library(served, token)
    assert r.status_code == 200 and "UNKNOWN" in r.text
    assert "Claude + Obsidian Vault Kit" in r.text


def test_a_purchase_stripe_cannot_answer_for_is_unknown_not_missing(served):
    token = _token_of(_recover(served))
    served.verdicts[SID] = stripe_read.Verdict("unreachable", SID)
    r = _library(served, token)
    assert "UNKNOWN" in r.text and "Status: Paid" not in r.text


def test_an_expired_library_link_says_recover_again(served):
    key = delivery.signing_key()
    token = delivery.mint_library(stripe_read.email_hash(EMAIL), [SID],
                                  now=time.time() - 3601, key=key)
    r = _library(served, token)
    assert r.status_code == 410 and "Recover again" in r.text


@pytest.mark.parametrize("tamper", [
    lambda t: t.replace(t.split(".")[1], stripe_read.email_hash(OTHER)),
    lambda t: t.replace(SID, SID2),
    lambda t: t.replace(t.split(".")[2], str(int(t.split(".")[2]) + 9999)),
    lambda t: t[:-1] + ("0" if t[-1] != "0" else "1"),
    lambda t: "lib2" + t[4:],
    lambda t: t + "~" + SID2,
    lambda t: "",
    lambda t: "x" * 3000,
])
def test_a_tampered_library_link_is_refused(served, tamper):
    token = _token_of(_recover(served))
    r = _library(served, tamper(token))
    assert r.status_code == 404 and "Your purchases" not in r.text


def test_download_and_library_tokens_never_pass_for_each_other():
    key = b"k" * 32
    lib = delivery.mint_library(stripe_read.email_hash(EMAIL), [SID], key=key)
    dl = delivery.mint(SID, "vault-kit-zip", days=7, key=key)
    assert delivery.verify(lib, key=key)[0] == "bad"
    assert delivery.verify_library(dl, key=key)[0] == "bad"
    # Same fields, signed as a download: still bad as a library link.
    owner, exp = lib.split(".")[1], lib.split(".")[2]
    forged = f"lib1.{owner}.{exp}.{SID}.{delivery._sig(key, owner, int(exp), SID)}"
    assert delivery.verify_library(forged, key=key)[0] == "bad"


def test_no_library_link_without_a_key_or_with_a_bad_owner(monkeypatch):
    monkeypatch.delenv(delivery.DOWNLOAD_KEY_ENV, raising=False)
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    assert delivery.mint_library(stripe_read.email_hash(EMAIL), [SID]) is None
    assert delivery.mint_library(EMAIL, [SID], key=b"k" * 32) is None
    assert delivery.mint_library(stripe_read.email_hash(EMAIL), [], key=b"k" * 32) is None


# ------------------------------------------------------------------ logs

def test_nothing_typed_and_no_token_reaches_a_log_line(served, caplog):
    """Every server-side logger at DEBUG, with httpx where boot() puts it
    (WARNING: its INFO line is the request URL, the email filter included;
    see the next test). The TestClient's own httpx lines are the browser's
    side, not the server's, and are left out the same way."""
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.WARNING, logger="httpx")
    caplog.set_level(logging.WARNING, logger="httpcore")
    token = _token_of(_recover(served))
    _recover(served, receipt="9999-0000")
    _library(served, token)
    served.fake.fail = 500
    _recover(served, ip="192.0.2.50")
    text = "\n".join(r.getMessage() for r in caplog.records)
    for secret in (EMAIL, "1234-5678", "9999-0000", token, token[:40], SID,
                   stripe_read.email_hash(EMAIL)):
        assert secret not in text, secret


def test_boot_keeps_httpx_request_lines_out_of_the_log():
    """The Stripe list URL carries the typed email (customer_details[email]);
    httpx logs request URLs at INFO. boot() raises httpx to WARNING."""
    from atlas.modules._http import install_log_redaction
    lg = logging.getLogger("httpx")
    level, filters = lg.level, list(lg.filters)
    try:
        install_log_redaction()
        assert lg.getEffectiveLevel() >= logging.WARNING
        assert not lg.isEnabledFor(logging.INFO)
    finally:
        lg.setLevel(level)
        lg.filters[:] = filters


def test_the_access_log_redacts_the_library_token():
    from atlas.api.main import _NoAudioTokens
    flt = _NoAudioTokens()
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0,
                            '%s - "%s %s HTTP/1.1" %d', (
                                "203.0.113.9", "GET",
                                "/public/academy/library?t=lib1.abc.123.cs_test_x.sig",
                                200), None)
    flt.filter(rec)
    assert "lib1.abc" not in rec.getMessage() and "t=<token>" in rec.getMessage()


def test_recover_and_library_are_public_and_nothing_near_them_is():
    from atlas.api import main
    assert main._is_public("/public/academy/recover")
    assert main._is_public("/public/academy/library")
    for path in ("/public/academy/recover/x", "/public/academy/library/",
                 "/public/academy/libraryx", "/public/academy/recover/../../api"):
        assert not main._is_public(path), path
    caddy = (pathlib.Path(__file__).parents[1] / "Caddyfile").read_text()
    assert "handle /public/academy/recover {" in caddy
    assert "handle /public/academy/library {" in caddy
    skip = next(line for line in caddy.splitlines() if "@secret_path path" in line)
    assert "/public/academy/library" in skip and "/public/academy/recover" in skip


def test_the_typed_fields_never_reach_slack_or_a_prompt(served):
    _recover(served)                       # a match: recorded and posted once
    _recover(served, receipt="9999-0000")
    for msg in served.slack:
        assert EMAIL not in msg.title + msg.body
        assert RECEIPT not in msg.title + msg.body



def test_a_session_stripe_names_another_owner_for_is_never_handed_over(served):
    """Defence in depth: if Stripe's list ever returned another buyer's
    checkout, the fresh read's email decides. It is not recorded, not put in
    a library link, and the answer is the ordinary miss."""
    theirs = stripe_read.judge(_session(), served.cat)
    served.verdicts[SID] = stripe_read.Verdict(**{**theirs.__dict__, "email": OTHER})
    miss = _recover(served, receipt="9999-0000", ip="192.0.2.70")
    r = _recover(served, ip="192.0.2.71")
    assert r.status_code == 200 and "location" not in r.headers
    assert r.content == miss.content
    assert not [a for s, a in served.db.sql_log if "academy_purchases" in s]
    assert not served.slack


def test_every_recover_answer_takes_at_least_the_floor(served, monkeypatch):
    """Match, miss, malformed, rate-limited and Stripe-down all take at least
    RECOVER_FLOOR_S from the request's start (1.5 s in production)."""
    assert api.RECOVER_FLOOR_S == 0.0                   # the fixture's setting
    monkeypatch.setattr(api, "RECOVER_FLOOR_S", 0.25)
    cases = [{"ip": "192.0.2.80"},                                 # match
             {"receipt": "9999-0000", "ip": "192.0.2.81"},            # miss
             {"email": "nope", "receipt": "!", "ip": "192.0.2.82"}]      # malformed
    for kw in cases:
        t0 = time.monotonic()
        _recover(served, **kw)
        assert time.monotonic() - t0 >= 0.25, kw
    for _ in range(5):
        _recover(served, receipt="9999-0000", ip="192.0.2.83")
    t0 = time.monotonic()
    assert _recover(served, receipt="9999-0000", ip="192.0.2.83").status_code == 429
    assert time.monotonic() - t0 >= 0.25
    served.fake.fail = 500
    t0 = time.monotonic()
    assert _recover(served, email=OTHER, ip="192.0.2.84").status_code == 503
    assert time.monotonic() - t0 >= 0.25


def test_the_production_floor_is_one_and_a_half_seconds():
    src = pathlib.Path(api.__file__).read_text()
    assert "RECOVER_FLOOR_S = 1.5" in src


def test_recovery_records_only_paid_purchases_and_posts_nothing(served):
    """A purchase whose webhook never arrived is recorded on recovery only
    while it is paid, and never as a "new purchase" Slack post."""
    r = _recover(served, ip="192.0.2.90")
    assert r.status_code == 303
    inserts = [a for s, a in served.db.sql_log if "INSERT INTO academy_purchases" in s]
    assert inserts and inserts[0][1] == "recover" and inserts[0][2] == "paid"
    assert not served.slack
    served.db.sql_log.clear()
    served.verdicts[SID2] = stripe_read.judge(
        _session(refunded=True, amount_refunded=1900), served.cat)
    served.fake.by_email[OTHER] = [_listed(SID2, "2222-3333")]
    _recover(served, email=OTHER, receipt="2222-3333", ip="192.0.2.91")
    assert not [a for s, a in served.db.sql_log if "academy_purchases" in s]
    assert not served.slack


def _many(n: int, hit: int) -> list[dict]:
    return [_listed(f"cs_test_page{i:016d}", RECEIPT if i == hit else "9999-0000")
            for i in range(n)]


async def test_the_lookup_follows_pages_up_to_three_per_query(fake):
    fake.page_size = 100
    fake.by_email[EMAIL] = _many(250, hit=240)
    look = await stripe_read.find_by_receipt(EMAIL, RECEIPT)
    assert look.session_ids == ("cs_test_page0000000000000240",)
    assert len(fake.requests) == 6                    # 3 pages x 2 forms
    afters = [r.url.params.get("starting_after") for r in fake.requests[:3]]
    assert afters == [None, "cs_test_page0000000000000099",
                      "cs_test_page0000000000000199"]


async def test_past_three_pages_the_lookup_stops(fake):
    fake.page_size = 100
    fake.by_email[EMAIL] = _many(400, hit=350)
    look = await stripe_read.find_by_receipt(EMAIL, RECEIPT)
    assert look.status == "none" and len(fake.requests) == 6
