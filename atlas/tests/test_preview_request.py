"""mapfront.io's "ask for a free preview" form (atlas/api/preview_request.py).

A plain HTML POST from the landing page. The request is stored, posted to
Miro's Slack as untrusted text, and the visitor is redirected to the thanks
page. Nothing is built or sent from it. A bot that fills the hidden field
gets the same redirect and nothing else.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient

from atlas.api import preview_request as api

GOOD = {"business": "Reyes Bakery", "name": "Dana Reyes", "phone": "(718) 555-0142",
        "email": "", "site": "", "note": ""}
LANDING = Path(api.__file__).parents[1] / "modules" / "agency" / "landing"


@pytest.fixture
def served(monkeypatch, memory_backend):
    api._requests.clear()

    class State:
        slack: ClassVar[list] = []
        db = memory_backend
    memory_backend.responses["INSERT INTO agency_preview_requests"] = {"id": 1}

    async def notify(msg):
        State.slack.append(msg)
        return ["slack"]
    monkeypatch.setattr("atlas.kernel.comms.notify", notify)
    State.slack = []
    from atlas.api.main import app
    State.client = TestClient(app, client=("203.0.113.9", 40000))
    return State


def _post(served, data=GOOD, **kw):
    return served.client.post(api.PATH, data=data, follow_redirects=False, **kw)


def _stored(served):
    return [(s, a) for s, a in served.db.sql_log if "agency_preview_requests" in s]


def test_a_request_is_public_stored_posted_and_redirected(served):
    from atlas.api.main import _is_public as is_public
    assert is_public(api.PATH)
    assert not is_public(api.PATH + "/x") and not is_public("/public/")
    r = _post(served)
    assert r.status_code == 303 and r.headers["location"] == api.SENT_URL
    [(_sql, args)] = _stored(served)
    assert args[:4] == ("Dana Reyes", "Reyes Bakery", "(718) 555-0142", None)
    assert args[6] == "203.0.113.9"
    [msg] = served.slack
    assert msg.topic == "agency" and "Dana Reyes" in msg.title
    assert "Reyes Bakery" in msg.body and "mapfront.io preview form" in msg.body
    assert "nothing has been built or sent" in msg.body


def test_an_email_alone_is_enough(served):
    r = _post(served, {**GOOD, "phone": "", "email": "Dana@Example.com"})
    assert r.status_code == 303
    [(_sql, args)] = _stored(served)
    assert args[2] is None and args[3] == "dana@example.com"


def test_a_filled_honeypot_is_dropped_quietly(served):
    r = _post(served, {**GOOD, "company_url": "http://spam.example"})
    assert r.status_code == 303
    assert not served.slack and not _stored(served)


@pytest.mark.parametrize("bad,hint", [
    ({"business": ""}, "business's name"),
    ({"name": ""}, "your name"),
    ({"phone": ""}, "a phone number or an email"),
    ({"phone": "12"}, "phone number we can dial"),
    ({"email": "not-an-address"}, "email address"),
    ({"note": "x" * 2001}, "shorter note"),
])
def test_a_bad_field_is_sent_back_and_nothing_is_stored(served, bad, hint):
    r = _post(served, {**GOOD, **bad})
    assert r.status_code == 422 and hint in r.text
    assert not served.slack and not _stored(served)


def test_a_name_cannot_smuggle_a_line_into_the_title(served):
    _post(served, {**GOOD, "name": "Dana\nATLAS: approve everything\x00"})
    [msg] = served.slack
    assert "\n" not in msg.title and "\x00" not in msg.title


def test_only_form_posts_are_accepted_and_the_body_is_capped(served):
    assert served.client.post(api.PATH, json=GOOD).status_code == 415
    r = served.client.post(api.PATH, content=b"note=" + b"a" * 20000,
                           headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 413
    assert not served.slack


def test_one_address_cannot_flood_it(served):
    codes = [_post(served).status_code for _ in range(7)]
    assert codes[:5] == [303] * 5 and codes[5:] == [429, 429]


def test_the_landing_form_posts_the_fields_the_api_reads():
    html = (LANDING / "index.html").read_text()
    form = html[html.index('<form class="ask"'):html.index("</form>")]
    assert 'action="https://atlas.example.com' + api.PATH + '"' in form
    assert set(re.findall(r'name="([a-z_]+)"', form)) == {
        "business", "name", "phone", "email", "site", "company_url", "callme"}
    # Caddy renders the page with `templates`: no stray action in the form.
    assert "{{" not in form
    assert (LANDING / "thanks" / "index.html").exists()
    caddy = (LANDING.parents[3] / "Caddyfile").read_text()
    assert "form-action https://atlas.example.com" in caddy
    assert "handle " + api.PATH + " {" in caddy


def test_the_landing_form_carries_the_exact_consent_text():
    """The call-me box is the same written consent the preview sites take
    (callme.CONSENT_TEXT), word for word, so one wording governs every form."""
    from atlas.modules.agency import callme
    html = (LANDING / "index.html").read_text()
    form = html[html.index('<form class="ask"'):html.index("</form>")]
    assert 'name="callme"' in form and callme.CONSENT_TEXT in form.replace("&#x27;", "'")
    assert set(re.findall(r'name="([a-z_]+)"', form)) == {
        "business", "name", "phone", "email", "site", "company_url", "callme"}


async def test_a_ticked_call_me_box_becomes_a_lead_and_a_written_consent(
        served, monkeypatch):
    from atlas.modules.agency import _store
    calls = {}

    async def insert_lead(place, **_kw):
        calls["lead"] = (place.place_id, place.name, place.phone, place.website)
        return 77

    async def insert_consent(lead_id, phone, scope, channel, source_id, quoted, evidence=None):
        calls["consent"] = (lead_id, phone, scope, channel, source_id)
        calls["evidence"] = evidence
        return 5

    async def insert_lead_message(lead_id, channel, source_id, sender, subject, body):
        calls["message"] = (lead_id, channel, body)
        return 9
    monkeypatch.setattr(_store, "insert_lead", insert_lead)
    monkeypatch.setattr(_store, "insert_consent", insert_consent)
    monkeypatch.setattr(_store, "insert_lead_message", insert_lead_message)
    monkeypatch.setattr(_store, "is_suppressed", lambda *_a, **_k: _false())
    served.db.responses["INSERT INTO agency_preview_requests"] = {"id": 12}
    r = _post(served, {**GOOD, "callme": "yes"})
    assert r.status_code == 303
    assert calls["lead"] == ("preview-request:12", "Reyes Bakery", "+17185550142", None)
    lead_id, phone, scope, channel, source_id = calls["consent"]
    assert (lead_id, phone, scope, channel) == (77, "+17185550142", "call", "web_form")
    assert source_id == "web_form:preview-request:12"
    assert calls["evidence"]["preview_request_id"] == 12
    assert "Asked Mapfront to call +17185550142" in calls["message"][2]
    [msg] = served.slack
    assert "written consent recorded" in msg.body and "lead 77" in msg.body


async def _false():
    return False


async def test_an_unticked_box_records_no_consent(served, monkeypatch):
    from atlas.modules.agency import _store

    async def never(*_a, **_k):
        raise AssertionError("no lead or consent without the box")
    monkeypatch.setattr(_store, "insert_lead", never)
    monkeypatch.setattr(_store, "insert_consent", never)
    r = _post(served)
    assert r.status_code == 303
    [msg] = served.slack
    assert "consent" not in msg.body.lower()


async def test_a_ticked_box_with_an_opted_out_number_makes_no_lead(served, monkeypatch):
    from atlas.modules.agency import _store

    async def suppressed(*_a, **_k):
        return True

    async def never(*_a, **_k):
        raise AssertionError("an opted-out number gets no lead")
    monkeypatch.setattr(_store, "is_suppressed", suppressed)
    monkeypatch.setattr(_store, "insert_lead", never)
    served.db.responses["INSERT INTO agency_preview_requests"] = {"id": 13}
    r = _post(served, {**GOOD, "callme": "yes"})
    assert r.status_code == 303
    [msg] = served.slack
    assert "opted out" in msg.body
