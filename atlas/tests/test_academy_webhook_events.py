"""Refund and dispute events on the ATLAS Academy webhook (plan F20): the
signature is checked first, the payment resolves to its checkout (a GET),
the checkout is read fresh and the purchase row updated. A duplicate
checkout event makes one row and one Slack post."""
from __future__ import annotations

import json

import httpx
import pytest

from atlas.api import academy as api
from atlas.modules.academy import stripe_read
from tests import academy_shop_fakes as fakes
from tests.academy_shop_fakes import SID, _listed
from tests.test_academy_access import _session, _signed

fake, served = fakes.fake, fakes.served              # the shared fixtures


def _charge_event(kind: str, pi="pi_12345678") -> bytes:
    obj = ({"id": "ch_1", "object": "charge", "payment_intent": pi, "refunded": True}
           if kind == "charge.refunded" else
           {"id": "dp_1", "object": "dispute", "charge": "ch_1", "payment_intent": pi})
    return json.dumps({"id": "evt_2", "type": kind, "data": {"object": obj}}).encode()


def _post_event(served, body, secret="whsec_x"):
    return served.client.post(api.WEBHOOK_PATH, content=body,
                              headers={"stripe-signature": _signed(body, secret)})


@pytest.mark.parametrize("kind,status", [
    ("charge.refunded", "refunded"),
    ("charge.dispute.created", "disputed"),
    # The row follows the fresh read: a lost dispute stays disputed (a won
    # one restoring access is tested in test_academy_disputes.py).
    ("charge.dispute.closed", "disputed"),
])
def test_refund_and_dispute_events_update_the_purchase(served, monkeypatch, kind,
                                                       status):
    monkeypatch.setenv(api.WEBHOOK_SECRET_ENV, "whsec_x")
    served.fake.by_pi["pi_12345678"] = [_listed()]
    served.db.responses["INSERT INTO academy_purchases"] = None     # already recorded
    served.verdicts[SID] = stripe_read.judge(
        _session(refunded=status == "refunded",
                 amount_refunded=1900 if status == "refunded" else 0,
                 disputed=status == "disputed"), served.cat)
    r = _post_event(served, _charge_event(kind))
    assert r.status_code == 200 and r.text == status
    [req] = served.fake.requests
    assert req.method == "GET" and req.url.params["payment_intent"] == "pi_12345678"
    updates = [a for s, a in served.db.sql_log if "UPDATE academy_purchases" in s]
    assert updates[0][0] == SID and updates[0][1] == status
    assert not served.slack


def test_an_unsigned_refund_event_is_refused(served, monkeypatch):
    monkeypatch.setenv(api.WEBHOOK_SECRET_ENV, "whsec_x")
    body = _charge_event("charge.refunded")
    assert served.client.post(api.WEBHOOK_PATH, content=body).status_code == 401
    assert _post_event(served, body, secret="whsec_other").status_code == 401
    assert not served.fake.requests and not served.db.sql_log


def test_a_refund_of_some_other_payment_is_ignored(served, monkeypatch):
    monkeypatch.setenv(api.WEBHOOK_SECRET_ENV, "whsec_x")
    r = _post_event(served, _charge_event("charge.refunded", pi="pi_NOTOURS0001"))
    assert r.status_code == 200 and r.text == "ignored" and not served.db.sql_log
    r = _post_event(served, _charge_event("charge.refunded", pi="../../x"))
    assert r.text == "ignored"


def test_a_refund_event_stripe_cannot_resolve_is_retried(served, monkeypatch):
    monkeypatch.setenv(api.WEBHOOK_SECRET_ENV, "whsec_x")
    served.fake.fail = httpx.ConnectError("down")
    r = _post_event(served, _charge_event("charge.refunded"))
    assert r.status_code == 503 and not served.db.sql_log
    served.fake.fail = None
    served.fake.by_pi["pi_12345678"] = [_listed()]
    served.verdicts[SID] = stripe_read.Verdict("unreachable", SID)
    assert _post_event(served, _charge_event("charge.refunded")).status_code == 503


def test_a_duplicate_checkout_event_makes_one_row_and_one_post(served, monkeypatch):
    """ON CONFLICT (session_id) DO NOTHING: the second INSERT returns no row,
    so the second event only refreshes the status and posts nothing."""
    monkeypatch.setenv(api.WEBHOOK_SECRET_ENV, "whsec_x")
    rows: set[str] = set()

    async def fetchrow(sql, *args):
        served.db.sql_log.append((" ".join(sql.split()), args))
        if "INSERT INTO academy_purchases" in sql:
            if args[0] in rows:
                return None
            rows.add(args[0])
            return {"session_id": args[0]}
        raise NotImplementedError(sql)
    monkeypatch.setattr(served.db, "fetchrow", fetchrow)
    body = json.dumps({"id": "evt_1", "type": "checkout.session.completed",
                       "data": {"object": {"id": SID, "object": "checkout.session"}}}
                      ).encode()
    first, second = _post_event(served, body), _post_event(served, body)
    assert first.text == "recorded" and second.text == "paid"
    assert rows == {SID} and len(served.slack) == 1

