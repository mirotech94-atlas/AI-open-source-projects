"""A configured provider that fails (bad key, no credit, network error) must
leave a log line behind, not just a silent fallthrough -- that silence is
exactly what made a real no-credit Anthropic account indistinguishable from
`chat_providers: ["rules"]` (never configured) in production."""

import io
import logging
import urllib.error

from guardian_hub.chat import providers


def _http_error(status: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://api.anthropic.com/v1/messages", status, "error", {}, io.BytesIO(body)
    )


def test_describe_failure_reports_status_and_body_for_an_http_error():
    exc = _http_error(400, b'{"error":{"message":"Your credit balance is too low"}}')
    assert providers._describe_failure(exc) == (
        'HTTP 400: {"error":{"message":"Your credit balance is too low"}}'
    )


def test_describe_failure_never_includes_the_api_key():
    secret = "sk-ant-api03-thisIsNotARealKeyButShouldNeverAppear"
    exc = _http_error(401, b'{"error":{"message":"invalid x-api-key"}}')
    described = providers._describe_failure(exc)
    assert secret not in described
    assert "invalid x-api-key" in described


def test_describe_failure_reports_a_network_level_exception():
    exc = TimeoutError("timed out")
    assert providers._describe_failure(exc) == "TimeoutError: timed out"


def test_anthropic_failure_is_logged_and_falls_through_to_rules(monkeypatch, caplog):
    monkeypatch.setattr(providers, "_groq_available", lambda: False)
    monkeypatch.setattr(providers, "_anthropic_available", lambda: True)

    def boom(*a, **k):
        raise _http_error(400, b'{"error":{"message":"Your credit balance is too low"}}')

    monkeypatch.setattr(providers, "_call_anthropic", boom)
    monkeypatch.setattr(providers.rules, "answer", lambda *a, **k: "fallback text")

    with caplog.at_level(logging.WARNING, logger="guardian_hub.chat.providers"):
        reply = providers.chat([{"role": "user", "content": "hi"}], db=object(), tenant=object())

    assert reply.provider == "rules"
    assert any("anthropic provider failed" in r.message for r in caplog.records)
    assert any("credit balance" in r.message for r in caplog.records)


# --------------------------------------------------------------------------
# 429 back-off -- why roughly every other turn was answered by keyword matching
# --------------------------------------------------------------------------
# Groq's token-per-minute 429 sends no `retry-after` header; it puts the wait
# in the error body ("Please try again in 8.1525s"). The old code defaulted to
# a flat 8s, retried a fraction of a second early, was refused again, and fell
# through to `rules`. A turn that never reaches a model cannot understand
# anything, which is most of what "the bot does not understand" was.

import io
import urllib.error

from guardian_hub.chat.providers import RETRY_AFTER_CAP_S, RETRY_MARGIN_S, _retry_after


def _err(body: str = "", headers: dict | None = None) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://api.groq.com/x", 429, "Too Many Requests", headers or {},
        io.BytesIO(body.encode()),
    )


_TPM_BODY = (
    '{"error":{"message":"Rate limit reached for model `openai/gpt-oss-120b` in organization '
    '`org_x` service tier `on_demand` on tokens per minute (TPM): Limit 8000, Used 3842, '
    'Requested 5245. Please try again in 8.1525s.","type":"tokens","code":"rate_limit_exceeded"}}'
)


def test_the_wait_comes_from_the_body_when_there_is_no_header():
    # The exact payload seen in production, and the exact reason the old flat
    # 8.0 was wrong: 8.0 < 8.1525, so the retry landed inside the window.
    waited = _retry_after(_err(_TPM_BODY))
    assert waited > 8.1525
    assert waited == 8.1525 + RETRY_MARGIN_S


def test_a_retry_after_header_wins_when_one_is_sent():
    assert _retry_after(_err(_TPM_BODY, {"retry-after": "2"})) == 2 + RETRY_MARGIN_S


def test_the_wait_is_capped_so_one_turn_cannot_stall_behind_it():
    assert _retry_after(_err("try again in 600s")) == RETRY_AFTER_CAP_S
    assert _retry_after(_err("", {"retry-after": "900"})) == RETRY_AFTER_CAP_S


def test_an_unparseable_429_still_waits_a_sane_amount():
    for e in (_err(""), _err("no numbers here"), _err("", {"retry-after": "soon"})):
        waited = _retry_after(e)
        assert 0 < waited <= RETRY_AFTER_CAP_S
