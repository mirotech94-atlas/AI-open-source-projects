"""The lead-call and lead-text webhooks: signed, admitted per call, and
never the routes that ring Miro."""
from __future__ import annotations

import hashlib

from atlas.api import lead_call
from atlas.modules.agency import _store, leads
from tests.test_phone import _signed, calling, client  # noqa: F401  (fixtures)

LEAD = {"id": 7, "name": "Joe's Plumbing", "category": "plumber"}


OPENING = "Hi, this is Miro's assistant from ATLAS Academy, calling about Joe's Plumbing."


def _row(token: str, **over):
    row = {"id": 5, "lead_id": 7, "channel": "call", "phone": "+17185550101",
           "opening": OPENING,
           "token_sha": hashlib.sha256(token.encode()).hexdigest(),
           "status": "placed", "sid": "CA1", "ended_at": None}
    row.update(over)
    return row


def _db(monkeypatch, row):
    async def get_contact(cid):
        return row if row and cid == row["id"] else None

    async def get_lead(lid):
        return dict(LEAD)

    async def nothing(*a, **k):
        return None

    async def links(_lid):
        return {"domain": None, "slug": "joes-plumbing-7", "deployed": True}
    monkeypatch.setattr(_store, "get_contact", get_contact)
    monkeypatch.setattr(_store, "get_lead", get_lead)
    monkeypatch.setattr(_store, "set_contact", nothing)
    monkeypatch.setattr(_store, "set_lead_meta", nothing)
    monkeypatch.setattr(_store, "lead_links", links)
    lead_call._CALLS.clear()


def test_an_unsigned_lead_call_is_refused(client, calling):  # noqa: F811
    r = client.post("/webhooks/twilio/agency/voice?c=5&t=x",
                    data={"CallSid": "CA1", "To": "+17185550101"})
    assert r.status_code == 401


def test_a_wrong_secret_is_refused(client, calling, monkeypatch):  # noqa: F811
    _db(monkeypatch, _row("right"))
    r = _signed(client, "/webhooks/twilio/agency/voice?c=5&t=wrong",
                {"CallSid": "CA1", "To": "+17185550101", "AnsweredBy": "human"})
    assert r.status_code == 403


def test_another_number_is_refused(client, calling, monkeypatch):  # noqa: F811
    _db(monkeypatch, _row("right"))
    r = _signed(client, "/webhooks/twilio/agency/voice?c=5&t=right",
                {"CallSid": "CA1", "To": "+12125550199", "AnsweredBy": "human"})
    assert r.status_code == 403


def test_the_opening_is_the_identification_inside_the_gather(
        client, calling, monkeypatch):  # noqa: F811
    _db(monkeypatch, _row("right"))
    r = _signed(client, "/webhooks/twilio/agency/voice?c=5&t=right",
                {"CallSid": "CA1", "To": "+17185550101", "AnsweredBy": "human"})
    assert r.status_code == 200
    text = r.text.replace("&apos;", "'")
    assert "Hi, this is Miro's assistant from ATLAS Academy, calling about Joe's" in text
    assert "free preview at joes-plumbing-7 dot mapfront dot io" in text
    assert lead_call.ASK_OWNER in text and "I'm an AI" not in text
    assert "/webhooks/twilio/agency/turn" in r.text
    assert "/webhooks/twilio/voice/turn" not in r.text
    # The TwiML shape the turn-taking relies on: the spoken line nested in
    # a speech Gather (barge-in), auto endpointing, a silence fallback, no
    # partial-result path that could speak over a half-finished sentence.
    gather = r.text.split("<Gather", 1)[1].split("</Gather>", 1)[0]
    assert gather.startswith(' input="speech" speechTimeout="auto"')
    assert 'actionOnEmptyResult="true"' in gather
    assert "<Say" in gather and lead_call.ASK_OWNER in gather.replace("&apos;", "'")
    assert "partialResultCallback" not in r.text


def _transferring(monkeypatch, token="right"):
    """A call admitted and mid-transfer, the way the turn route leaves it."""
    _db(monkeypatch, _row(token))
    call = lead_call.LeadCall(5, dict(LEAD), "+17185550101", OPENING,
                              hashlib.sha256(token.encode()).hexdigest())
    call.transferring = True
    lead_call._CALLS["CA1"] = call
    return call


def test_yes_to_the_owner_turns_the_turn_into_a_dial(client, calling, monkeypatch):  # noqa: F811
    _db(monkeypatch, _row("right"))
    monkeypatch.setenv("AGENCY_ESCALATION_PHONE", "+19175550199")
    monkeypatch.setenv("TWILIO_FROM", "+17185550000")
    call = lead_call.LeadCall(5, dict(LEAD), "+17185550101", OPENING,
                              hashlib.sha256(b"right").hexdigest())
    call.turns.append(("atlas", lead_call.opening_line(call)))
    lead_call._CALLS["CA1"] = call
    r = _signed(client, "/webhooks/twilio/agency/turn?c=5&t=right&turn=2",
                {"CallSid": "CA1", "To": "+17185550101", "SpeechResult": "yes please"})
    assert r.status_code == 200
    assert '<Dial action="/webhooks/twilio/agency/dial?c=5&amp;t=right"' in r.text
    assert 'timeout="25" callerId="+17185550000"' in r.text
    assert ('<Number url="https://atlas.example.com/webhooks/twilio/agency/whisper?c=5'
            '&amp;t=right" method="POST">+19175550199</Number>') in r.text
    assert "<Gather" not in r.text and "<Hangup/>" not in r.text


def test_the_whisper_is_admitted_on_the_parent_call_and_miros_number(
        client, calling, monkeypatch):  # noqa: F811
    monkeypatch.setenv("AGENCY_ESCALATION_PHONE", "+19175550199")
    _transferring(monkeypatch)
    r = _signed(client, "/webhooks/twilio/agency/whisper?c=5&t=right",
                {"CallSid": "CA2", "ParentCallSid": "CA1", "To": "+19175550199",
                 "From": "+17185550000"})
    assert r.status_code == 200
    assert "Joe's Plumbing, from the preview call." in r.text
    assert "<Hangup/>" not in r.text and "<Gather" not in r.text   # then the bridge


def test_the_whisper_refuses_any_other_number_or_secret(client, calling, monkeypatch):  # noqa: F811
    monkeypatch.setenv("AGENCY_ESCALATION_PHONE", "+19175550199")
    _transferring(monkeypatch)
    other = _signed(client, "/webhooks/twilio/agency/whisper?c=5&t=right",
                    {"CallSid": "CA2", "ParentCallSid": "CA1", "To": "+12125550123"})
    assert other.status_code == 403
    wrong = _signed(client, "/webhooks/twilio/agency/whisper?c=5&t=wrong",
                    {"CallSid": "CA2", "ParentCallSid": "CA1", "To": "+19175550199"})
    assert wrong.status_code == 403
    unsigned = client.post("/webhooks/twilio/agency/whisper?c=5&t=right",
                           data={"CallSid": "CA2", "ParentCallSid": "CA1",
                                 "To": "+19175550199"})
    assert unsigned.status_code == 401


def test_miro_not_answering_ends_with_the_call_back_line_and_an_alert(
        client, calling, monkeypatch):  # noqa: F811
    call = _transferring(monkeypatch)
    posted = []

    async def notify(out):
        posted.append(out)
    monkeypatch.setattr("atlas.kernel.comms.notify", notify)
    r = _signed(client, "/webhooks/twilio/agency/dial?c=5&t=right",
                {"CallSid": "CA1", "To": "+17185550101", "DialCallStatus": "no-answer"})
    assert r.status_code == 200
    assert lead_call.MIRO_CALLBACK in r.text
    assert "<Hangup/>" in r.text and not call.transferring
    assert call.outcome == "callback"
    assert [p.title[:40] for p in posted] == ["CALL BACK: Joe's Plumbing +17185550101 a"]


def test_a_completed_transfer_just_says_goodbye(client, calling, monkeypatch):  # noqa: F811
    call = _transferring(monkeypatch)
    r = _signed(client, "/webhooks/twilio/agency/dial?c=5&t=right",
                {"CallSid": "CA1", "To": "+17185550101", "DialCallStatus": "completed",
                 "DialCallDuration": "212"})
    assert r.status_code == 200 and lead_call.AFTER_TRANSFER in r.text
    assert call.outcome == "transferred"


def test_a_lead_call_ending_never_counts_as_miro_declining(
        client, calling, monkeypatch):  # noqa: F811
    _db(monkeypatch, _row("right"))
    recorded = []
    monkeypatch.setattr("atlas.kernel.escalation.record_outcome",
                        lambda answered, **k: recorded.append(answered))

    async def finish(sid, **kw):
        return None
    monkeypatch.setattr(lead_call, "finish", finish)
    r = _signed(client, "/webhooks/twilio/agency/status?c=5&t=right",
                {"CallSid": "CA1", "To": "+17185550101", "CallStatus": "no-answer"})
    assert r.status_code == 200 and recorded == []


def test_a_lead_text_is_taken_in_before_the_reception_gate(
        client, calling, monkeypatch):  # noqa: F811
    seen = []

    async def intake(frm, body, *, sid, opt_out_type="", answered=False):
        seen.append((frm, body, sid, opt_out_type))
        return True
    monkeypatch.setattr(leads, "intake_sms", intake)
    r = _signed(client, "/webhooks/twilio/sms",
                {"From": "+17185550101", "Body": "STOP", "MessageSid": "SM1",
                 "OptOutType": "STOP"})
    assert r.status_code == 200 and "<Message>" not in r.text
    assert seen == [("+17185550101", "STOP", "SM1", "STOP")]
