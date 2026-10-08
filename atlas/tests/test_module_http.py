"""The defensive HTTP layer. A secret must never reach an Observation, and an
unexpected response shape must be UNKNOWN rather than a confident number."""
from __future__ import annotations

import httpx
import pytest

from atlas.modules import _http


@pytest.fixture(autouse=True)
def secret_env(monkeypatch):
    monkeypatch.setenv("TRAVELPAYOUTS_TOKEN", "tp_live_SUPERSECRETVALUE123")
    monkeypatch.setenv("FLYCHEAP_API_TOKEN", "fc_abcdefghijklmnop")
    monkeypatch.setenv("ATLAS_PUBLIC_URL", "https://atlas.example.com")


# ------------------------------------------------------------- redaction

def test_a_token_in_a_url_is_redacted():
    leak = "https://api.travelpayouts.com/v1/statistics?token=tp_live_SUPERSECRETVALUE123&period=today"
    out = _http.redact(leak)
    assert "SUPERSECRET" not in out
    assert "<redacted>" in out


def test_any_environment_secret_is_redacted_wherever_it_appears():
    out = _http.redact("auth failed for fc_abcdefghijklmnop on retry")
    assert "fc_abcdefghijklmnop" not in out


def test_sensitive_query_parameters_are_redacted_by_name():
    """Covers a credential that did not come from our own environment."""
    out = _http.redact("GET /x?access_token=ABC123DEF456GHI&id=7")
    assert "ABC123DEF456GHI" not in out
    assert "id=7" in out


def test_authorization_headers_are_redacted():
    assert "abcdef123456" not in _http.redact("Authorization: Bearer abcdef123456")


def test_non_secret_values_survive():
    out = _http.redact("https://atlas.example.com/healthz returned 502")
    assert "atlas.example.com/healthz" in out and "502" in out


def test_explain_never_includes_the_query_string():
    request = httpx.Request(
        "GET", "https://api.x.com/v1/stats?token=tp_live_SUPERSECRETVALUE123")
    response = httpx.Response(401, request=request)
    reason = _http.explain(
        httpx.HTTPStatusError("x", request=request, response=response))
    assert "SUPERSECRET" not in reason
    assert "401" in reason and "credentials rejected" in reason


def test_explain_names_a_wrong_path_helpfully():
    request = httpx.Request("GET", "https://admin.flycheap.live/api/admin/nope")
    response = httpx.Response(404, request=request)
    reason = _http.explain(
        httpx.HTTPStatusError("x", request=request, response=response))
    assert "wrong path" in reason


# ----------------------------------------------------------- shape checks

def test_expect_keys_reports_what_was_actually_there():
    reason = _http.expect_keys({"foo": 1, "bar": 2}, "newest_row_age_minutes")
    assert "missing newest_row_age_minutes" in reason
    assert "foo" in reason and "bar" in reason


def test_expect_keys_rejects_a_list():
    assert "expected an object" in _http.expect_keys([1, 2], "a")


def test_expect_keys_passes_when_present():
    assert _http.expect_keys({"a": 1, "b": 2}, "a", "b") is None


def test_number_does_not_invent_a_default():
    """A missing field must not become a measurement."""
    value, reason = _http.number({}, "age")
    assert value is None and "no 'age'" in reason


def test_number_rejects_a_non_numeric_string():
    value, reason = _http.number({"age": "soon"}, "age")
    assert value is None and "not numeric" in reason


def test_number_rejects_a_bool():
    value, reason = _http.number({"age": True}, "age")
    assert value is None and "bool" in reason


def test_number_accepts_numeric_strings():
    assert _http.number({"age": "12.5"}, "age") == (12.5, None)


def test_number_error_is_redacted():
    _, reason = _http.number({"age": "tp_live_SUPERSECRETVALUE123"}, "age")
    assert "SUPERSECRET" not in reason


def test_first_list_finds_the_list():
    assert _http.first_list({"links": [1, 2]}, "links", "data") == ([1, 2], None)
    assert _http.first_list([1, 2], "links") == ([1, 2], None)


def test_first_list_explains_a_wrong_shape():
    items, reason = _http.first_list({"results": {"a": 1}}, "links", "data")
    assert items is None
    assert "no list under links or data" in reason
    assert "results" in reason


# -------------------------------------------------------------- get_json
#
# The doubles stub `client.request`, not `client.get`: get_json and post_json
# share one body that calls `AsyncClient.request(method, ...)`. A double that
# only answers `.get` makes the real code raise AttributeError, which
# `explain()` reports as an AttributeError -- a green-looking failure that
# says nothing about the transport it was meant to exercise.

async def test_get_json_returns_a_reason_instead_of_raising(monkeypatch):
    class Boom:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def request(self, *a, **k):
            raise httpx.ConnectTimeout("nope")

    monkeypatch.setattr(_http.httpx, "AsyncClient", Boom)
    data, reason = await _http.get_json("https://x/y")
    assert data is None and reason == "timed out"


async def test_get_json_flags_html_where_json_was_expected(monkeypatch):
    class Html:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def request(self, *a, **k):
            return httpx.Response(
                200, text="<html>login</html>",
                headers={"content-type": "text/html"},
                request=httpx.Request("GET", "https://x/y"))

    monkeypatch.setattr(_http.httpx, "AsyncClient", Html)
    data, reason = await _http.get_json("https://x/y")
    assert data is None
    assert "expected JSON" in reason and "assumed endpoint" in reason



# ------------------------------------------------ tracebacks in the log (9g)

@pytest.fixture
def captured_log():
    """A logger with its own handler and the redacting filter on it, as
    install_log_redaction puts it on the root's handlers."""
    import io
    import logging

    from atlas.modules._http import RedactingFilter

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    handler.addFilter(RedactingFilter())
    log = logging.getLogger("atlas.test.redaction")
    log.addHandler(handler)
    log.propagate = False
    yield log, stream
    log.removeHandler(handler)


def test_a_logged_traceback_carries_no_token(monkeypatch, captured_log):
    import httpx
    log, stream = captured_log
    monkeypatch.setenv("INSTAGRAM_TOKEN", "EAABsecretLongLivedToken123456")
    request = httpx.Request(
        "GET", "https://graph.facebook.com/v21.0/me?access_token="
               "EAABsecretLongLivedToken123456")
    try:
        raise httpx.HTTPStatusError(f"400 for url '{request.url}'", request=request,
                                    response=httpx.Response(400, request=request))
    except httpx.HTTPStatusError:
        log.exception("instagram call failed")
    out = stream.getvalue()
    assert "Traceback" in out and "HTTPStatusError" in out
    assert "EAABsecretLongLivedToken123456" not in out


def test_stack_info_is_redacted(monkeypatch, captured_log):
    import logging
    log, stream = captured_log
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-secret-slack-token-value")
    stack = "File x, line 1, in f\n  token = 'xoxb-secret-slack-token-value'"
    log.handle(logging.LogRecord("atlas.test.redaction", logging.WARNING,
                                 __file__, 1, "with a stack", None, None,
                                 sinfo=stack))
    out = stream.getvalue()
    assert "with a stack" in out and "xoxb-secret-slack-token-value" not in out
