"""The URL a Twilio signature is verified against.

Twilio signs the public https URL it requested. This process sits behind Caddy
with uvicorn running plain, so `request.url` carried scheme http and every
signature check failed — silently, and only in production, where the reply to
an SMS and a keypad approval on a CRITICAL page both 401 and vanish.

Asserting "a bad signature is refused" does not cover this: the bug and the
fix both 401 on a bad signature. These tests assert the exact string the HMAC
is computed over.
"""
from __future__ import annotations

import base64
import hashlib
import hmac

import pytest

from atlas.api import main


class FakeURL:
    def __init__(self, path: str, query: str = ""):
        self.path, self.query = path, query


class FakeRequest:
    def __init__(self, path: str, query: str = ""):
        self.url = FakeURL(path, query)


@pytest.fixture(autouse=True)
def host(monkeypatch):
    monkeypatch.setenv("ATLAS_HOST", "atlas.example.com")


def test_the_signed_url_is_https_on_the_public_host():
    """The whole bug in one assertion: uvicorn saw http, Twilio signed
    https."""
    url = main._public_url(FakeRequest("/webhooks/twilio/voice"))
    assert url == "https://atlas.example.com/webhooks/twilio/voice"


def test_a_query_string_is_preserved_exactly():
    """The voice callback carries ?say= and ?pid=, and Twilio signs the URL it
    fetched, query included."""
    url = main._public_url(
        FakeRequest("/webhooks/twilio/voice", "pid=41&say=Atlas%20alert"))
    assert url == ("https://atlas.example.com/webhooks/twilio/voice"
                   "?pid=41&say=Atlas%20alert")


def test_no_query_means_no_trailing_question_mark():
    """A trailing "?" with nothing after it is a different string, and a
    different string is a failed HMAC."""
    assert main._public_url(FakeRequest("/webhooks/twilio/voice", "")) == (
        "https://atlas.example.com/webhooks/twilio/voice")


def test_an_unset_host_fails_closed(monkeypatch):
    """Falling back to request.url would reintroduce the bug on exactly the
    box where it matters."""
    monkeypatch.delenv("ATLAS_HOST", raising=False)
    assert main._public_url(FakeRequest("/webhooks/twilio/voice")) is None


def test_the_header_scheme_cannot_be_forged(monkeypatch):
    """The API publishes on the mesh address, so X-Forwarded-Proto is a
    header any mesh peer could set. The URL is built from configuration and
    ignores the request entirely, apart from its path."""
    request = FakeRequest("/webhooks/twilio/voice")
    request.headers = {"X-Forwarded-Proto": "http", "Host": "evil.example"}
    assert main._public_url(request) == (
        "https://atlas.example.com/webhooks/twilio/voice")


def test_a_real_twilio_signature_over_the_public_url_verifies(monkeypatch):
    """End to end against Twilio's documented scheme: HMAC-SHA1 over the full
    URL with POST params appended sorted by key."""
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "the-auth-token")
    url = "https://atlas.example.com/webhooks/twilio/voice"
    params = {"From": "+15550002222", "Body": "status"}

    data = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    signature = base64.b64encode(
        hmac.new(b"the-auth-token", data.encode(), hashlib.sha1).digest()
    ).decode()

    assert main._verify_twilio(url, params, signature) is True
    # The pre-fix URL, which is what the handler used to pass in.
    assert main._verify_twilio(
        "http://atlas.example.com/webhooks/twilio/voice", params, signature) is False
