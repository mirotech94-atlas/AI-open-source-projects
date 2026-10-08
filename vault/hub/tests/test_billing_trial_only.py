"""`billing_mode = "trial_only"`: the free trial is the only thing on offer.

The owner asked for a site and a product with no subscription plans on it,
just the free trial. That is a *presentation* decision, so it is one setting
and the whole catalogue in `plans.py` stays intact behind it -- these tests
pin both halves: what a trial-only hub offers, and that the trial itself is
completely unchanged by the mode (still bounded, still pauses, still counts
down). A mode that silently made the trial unending would be a product
promise nobody made.
"""

import datetime as dt

import pytest

from guardian_hub import billing, plans
from guardian_hub.config import settings
from tests.test_auth import login, seed_user


@pytest.fixture
def trial_only(monkeypatch):
    monkeypatch.setattr(settings, "billing_mode", "trial_only")


def test_trial_only_is_the_default_mode():
    assert settings.trial_only() is True


def test_the_payload_offers_the_trial_and_nothing_else(client, trial_only):
    seed_user(); login(client)
    body = client.get("/api/v1/billing").json()
    assert body["trial_only"] is True
    assert [c["id"] for c in body["plans"]] == ["trial"]
    assert body["plans"][0]["is_trial"] is True
    # The trial's own terms are untouched by the mode.
    assert body["trial_days"] == plans.TRIAL_DAYS == 90


def test_checkout_and_portal_refuse_instead_of_half_working(client, trial_only):
    seed_user(); login(client)
    r = client.post("/api/v1/billing/checkout",
                    json={"plan": "home", "interval": "month", "sites": 1})
    assert r.status_code == 409, r.text
    assert "free trial only" in r.json()["detail"]
    assert client.post("/api/v1/billing/portal").status_code == 409


def test_the_trial_still_ends_and_still_pauses(trial_only):
    """The mode hides plans; it does not make the product free forever."""
    class _Sub:
        status = "trialing"
        trial_end = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=5)
    assert billing.trial_days_left(_Sub()) in (4, 5)

    class _Ended:
        status = "paused"
        trial_end = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)
    # A paused account stops ingesting exactly as it did before this mode
    # existed -- the trial is bounded in every mode.
    assert billing.ingest_allowed(_Ended()) is False


def test_the_paid_catalogue_survives_for_the_day_it_is_switched_on(monkeypatch):
    monkeypatch.setattr(settings, "billing_mode", "plans")
    assert settings.trial_only() is False
    assert len(plans.PAID_PLANS) == 4
