"""Part A0: video delivery behind the shared public edge.

Behind Caddy every visitor arrives from one address, so a limiter keyed by
address is store-wide. Paid file requests are limited per *verified session*
instead; only links that fail verification count against the shared bucket
and none of them reach Stripe.
"""
from __future__ import annotations

import copy
import json
import os
import pathlib
import re
import time
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient

from atlas.api import academy as api
from atlas.modules.academy import catalog as cat_mod
from atlas.modules.academy import delivery, stripe_read

SID = "cs_test_a1B2c3D4e5F6g7H8i9J0"
SID2 = "cs_test_Z9y8X7w6V5u4T3s2R1q0"
PRICE_OP = "price_OP123456"
MP4 = b"0123456789" * 100                    # 1000 bytes


def _catalog() -> cat_mod.Catalog:
    raw = copy.deepcopy(json.loads(cat_mod.CATALOG_PATH.read_text()))
    for p in raw["products"]:
        for o in p["offers"]:
            if o["id"] == "safe-ai-operator":
                o["stripe_price_id"] = PRICE_OP
    return cat_mod.parse(raw)


def _session(sid: str) -> dict:
    return {
        "id": sid, "object": "checkout.session", "livemode": False,
        "payment_status": "paid", "amount_total": 2000, "currency": "usd",
        "created": int(time.time()) - 60,
        "customer_details": {"email": "buyer@example.com"},
        "line_items": {"object": "list", "has_more": False,
                       "data": [{"price": {"id": PRICE_OP}, "quantity": 1}]},
        "payment_intent": {"id": "pi_1", "status": "succeeded",
                           "latest_charge": {"id": "ch_1", "refunded": False,
                                             "amount_refunded": 0, "disputed": False}},
    }


@pytest.fixture
def served(monkeypatch, tmp_path, memory_backend):
    cat = _catalog()
    monkeypatch.setattr(api, "_catalog", lambda: cat)
    root = tmp_path / "files"
    monkeypatch.setenv(delivery.FILES_ENV, str(root))
    monkeypatch.setenv(delivery.DOWNLOAD_KEY_ENV, "download-key")
    offer = next(o for p in cat.products for o in p.offers if o.id == "safe-ai-operator")
    for f in offer.files:
        (root / f.path).parent.mkdir(parents=True, exist_ok=True)
        (root / f.path).write_bytes(MP4)
    api._clear_limiters()

    class State:
        stripe_calls: ClassVar[list] = []
        paid = True

    async def fake_read(session_id, catalog, **_kw):
        State.stripe_calls.append(session_id)
        v = stripe_read.judge(_session(session_id), catalog)
        if not State.paid:
            v = stripe_read.Verdict(**{**v.__dict__, "status": "refunded"})
        return v
    monkeypatch.setattr(api, "read_session", fake_read)

    async def notify(msg):
        return ["slack"]
    monkeypatch.setattr("atlas.kernel.comms.notify", notify)
    memory_backend.responses["INSERT INTO academy_purchases"] = {"session_id": SID}
    State.stripe_calls = []
    State.cat = cat
    State.offer = offer
    from atlas.api.main import app
    # One address for everybody, as behind the public Caddy edge.
    State.client = TestClient(app, client=("203.0.113.9", 40000))
    return State


def _url(sid: str, key: str = "operator-video-1-1") -> str:
    return "/public/academy/file/" + delivery.mint(sid, key, days=7)


def test_forty_range_requests_on_one_link_all_answer_206(served):
    url = _url(SID)
    for _ in range(40):
        r = served.client.get(url, headers={"Range": "bytes=0-99"})
        assert r.status_code == 206
        assert r.headers["content-range"] == "bytes 0-99/1000"
        assert len(r.content) == 100


def test_two_paying_sessions_do_not_exhaust_each_other(served):
    for _ in range(api._SESSION_FILES_LIMIT):
        assert served.client.get(_url(SID)).status_code == 200
    assert served.client.get(_url(SID)).status_code == 429
    # The first buyer is out of requests; the second, same address, is not.
    assert served.client.get(_url(SID2)).status_code == 200


def test_bad_and_expired_tokens_stay_in_the_shared_bucket_and_skip_stripe(served):
    bad = "/public/academy/file/v1.operator-video-1-1.9999999999." + SID + "." + "0" * 64
    codes = [served.client.get(bad).status_code for _ in range(40)]
    assert codes[0] == 404 and codes[-1] == 429
    assert served.stripe_calls == []
    # A flood of junk never starves a valid buyer.
    assert served.client.get(_url(SID)).status_code == 200
    expired = "/public/academy/file/" + delivery.mint(SID, "operator-video-1-1", days=-1)
    assert served.client.get(expired).status_code in (410, 429)


def test_a_valid_link_over_its_session_limit_never_reaches_stripe(served):
    for _ in range(api._SESSION_FILES_LIMIT):
        served.client.get(_url(SID))
    served.stripe_calls.clear()
    assert served.client.get(_url(SID)).status_code == 429
    assert served.stripe_calls == []


def test_a_video_with_view_is_inline_and_without_it_is_a_download(served):
    r = served.client.get(_url(SID) + "?view=1")
    assert "inline" in r.headers["content-disposition"]
    assert "attachment" in served.client.get(_url(SID)).headers["content-disposition"]


def test_the_paid_page_never_preloads_every_video(served):
    page = served.client.get("/public/academy/access", params={"session_id": SID})
    assert page.status_code == 200
    assert 'preload="metadata"' not in page.text


# ------------------------------------------------------------------ A0c

def test_a_paid_check_is_remembered_two_minutes(served):
    url = _url(SID)
    served.stripe_calls.clear()
    for _ in range(5):
        assert served.client.get(url, headers={"Range": "bytes=0-9"}).status_code == 206
    assert len(served.stripe_calls) == 1


def test_a_refund_is_seen_once_the_memory_runs_out(served, monkeypatch):
    url = _url(SID)
    assert served.client.get(url).status_code == 200
    served.paid = False
    # Inside the two minutes the paid verdict stands (the owner's choice).
    assert served.client.get(url).status_code == 200
    clock = {"t": time.monotonic() + api.PAID_MEMORY_SECONDS + 1}
    monkeypatch.setattr(api, "_now", lambda: clock["t"])
    r = served.client.get(url)
    assert r.status_code == 403
    assert "refunded" in r.text.lower()
    # And a refused verdict is never remembered.
    served.stripe_calls.clear()
    served.client.get(url)
    served.client.get(url)
    assert len(served.stripe_calls) == 2


def test_the_memory_is_per_session(served):
    served.client.get(_url(SID))
    served.client.get(_url(SID2))
    assert served.stripe_calls == [SID, SID2]


def test_the_access_page_is_limited_per_session_not_per_store(served):
    for _ in range(api._SESSION_PAGES_LIMIT):
        assert served.client.get("/public/academy/access",
                                 params={"session_id": SID}).status_code == 200
    assert served.client.get("/public/academy/access",
                             params={"session_id": SID}).status_code == 429
    assert served.client.get("/public/academy/access",
                             params={"session_id": SID2}).status_code == 200


def test_malformed_session_ids_stay_in_a_small_shared_bucket(served):
    codes = [served.client.get("/public/academy/access",
                               params={"session_id": "nope"}).status_code
             for _ in range(30)]
    assert codes[0] == 404 and codes[-1] == 429
    assert served.stripe_calls == []


def test_the_files_dir_fixture_is_real(served):
    root = pathlib.Path(os.environ[delivery.FILES_ENV])
    assert re.search(r"lesson-1\.1\.mp4$",
                     str(next(p for p in root.rglob("lesson-1.1.mp4"))))
