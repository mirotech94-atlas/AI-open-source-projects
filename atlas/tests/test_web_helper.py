import json
from collections import OrderedDict

import httpx
import pytest

from atlas.modules import _netguard, _web


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    """fetch_text now resolves the host before fetching (see _netguard); no
    network in tests, so every name answers with a public address."""
    async def resolve(host):
        return ["93.184.216.34"]
    monkeypatch.setattr(_netguard, "_getaddrinfo", resolve)
    monkeypatch.setattr(_netguard, "_CACHE", OrderedDict())


class _Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status
        self.headers = {"content-type": "text/html"}
    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad", request=httpx.Request("GET", "http://x"),
                                        response=httpx.Response(self.status_code))
    def json(self):
        return json.loads(self.text)

async def test_fetch_text_strips_markup(monkeypatch):
    class C:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): ...
        async def get(self, url, headers=None): return _Resp("<h1>Hi</h1><script>x()</script> there")
    monkeypatch.setattr(_web.httpx, "AsyncClient", C)
    text, reason = await _web.fetch_text("http://example.com")
    assert reason is None and text == "Hi there"

async def test_fetch_text_never_raises(monkeypatch):
    class C:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): ...
        async def get(self, url, headers=None): raise httpx.ConnectError("nope")
    monkeypatch.setattr(_web.httpx, "AsyncClient", C)
    text, reason = await _web.fetch_text("http://example.com")
    assert text is None and "unreachable" in reason

async def test_search_without_key_says_so(monkeypatch):
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    results, reason = await _web.search("anything")
    assert results == []
    assert "BRAVE_API_KEY" in reason
    assert "PERPLEXITY_API_KEY" in reason


# ------------------------------------------------------------- perplexity
#
# `_web.search` reaches Perplexity through `_perplexity.search_hits` (Task 42:
# the MCP path with the cap, the scrub and the name rule), so these tests fake
# `_perplexity.call` and check the hit shape `_web` promises its callers.

def _fake_answer(text: str, citations: list[str]):
    from datetime import UTC, datetime

    from atlas.modules import _perplexity
    return _perplexity.Answer(tool="perplexity_search", text=text,
                              citations=tuple(citations),
                              read_at=datetime.now(UTC), via="mcp",
                              unwrapped=text)


async def test_search_uses_perplexity_when_no_brave_key(monkeypatch):
    from atlas.modules import _perplexity
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.setenv("PERPLEXITY_API_KEY", "pplx-not-a-real-key")
    seen = {}

    async def fake_call(tool, text, *, caller, **kw):
        seen.update(tool=tool, text=text, caller=caller)
        return _fake_answer("The sky looks blue from Rayleigh scattering.",
                            ["http://example.com/a", "http://example.com/b"]), None
    monkeypatch.setattr(_perplexity, "call", fake_call)

    hits, reason = await _web.search("why is the sky blue", limit=5)

    assert reason is None
    assert seen == {"tool": "perplexity_search", "text": "why is the sky blue",
                    "caller": "search"}
    assert hits[0]["url"] == ""
    assert hits[0]["snippet"] == "The sky looks blue from Rayleigh scattering."
    assert hits[1] == {"title": "http://example.com/a",
                       "url": "http://example.com/a", "snippet": ""}
    assert hits[2]["url"] == "http://example.com/b"


async def test_search_perplexity_never_exceeds_the_limit(monkeypatch):
    from atlas.modules import _perplexity
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.setenv("PERPLEXITY_API_KEY", "x")

    async def fake_call(tool, text, *, caller, **kw):
        return _fake_answer("an answer",
                            [f"http://example.com/{i}" for i in range(10)]), None
    monkeypatch.setattr(_perplexity, "call", fake_call)

    hits, reason = await _web.search("anything", limit=3)
    assert reason is None
    assert len(hits) == 3
    assert hits[0]["url"] == ""
    assert [h["url"] for h in hits[1:]] == [
        "http://example.com/0", "http://example.com/1"]


async def test_search_prefers_brave_when_both_keys_are_set(monkeypatch):
    from atlas.modules import _perplexity
    monkeypatch.setenv("BRAVE_API_KEY", "a-brave-key")
    monkeypatch.setenv("PERPLEXITY_API_KEY", "a-pplx-key")

    called = {"perplexity": False}

    async def fake_call(tool, text, *, caller, **kw):
        called["perplexity"] = True
        return None, "should not have been called"
    monkeypatch.setattr(_perplexity, "call", fake_call)

    class C:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): ...
        async def get(self, url, headers=None, params=None):
            return _Resp(json.dumps(
                {"web": {"results": [{"title": "T", "url": "http://x",
                                      "description": "d"}]}}))
    monkeypatch.setattr(_web.httpx, "AsyncClient", C)

    hits, reason = await _web.search("anything")
    assert reason is None
    assert hits == [{"title": "T", "url": "http://x", "snippet": "d"}]
    assert called["perplexity"] is False


async def test_search_perplexity_refusal_is_a_reason_not_a_crash(monkeypatch):
    """A refusal inside `_perplexity.call` (cap reached, a name in the
    question, a bad response shape) reaches `_web.search` as `([], reason)`;
    the malformed-response cases themselves live in tests/test_perplexity.py."""
    from atlas.modules import _perplexity
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.setenv("PERPLEXITY_API_KEY", "x")

    async def fake_call(tool, text, *, caller, **kw):
        return None, "perplexity cap reached: 300/300 this month"
    monkeypatch.setattr(_perplexity, "call", fake_call)

    hits, reason = await _web.search("anything")
    assert hits == []
    assert "cap reached" in reason


async def test_perplexity_key_never_leaks_into_the_reason(monkeypatch, memory_backend):
    """`_web.search`'s Perplexity backend is `_perplexity.search_hits` (the
    MCP path; Task 50 removed the chat-completions fallback that used to
    call `_http.post_json`), so the transport to fail here is the MCP
    client's own `httpx.AsyncClient.post` -- with a budget the test lets it
    reach, or `calls_this_month()` would refuse before any request is sent
    and this would pass without checking anything."""
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    secret = "pplx-topsecretvalue123456"
    monkeypatch.setenv("PERPLEXITY_API_KEY", secret)
    memory_backend.responses["FROM perplexity_calls"] = {"n": 0}

    async def fake_post(_client, url, json=None, headers=None, **_kw):
        token = headers["Authorization"].split(" ", 1)[1]
        raise RuntimeError(f"connection reset while sending token {token}")
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    hits, reason = await _web.search("anything")
    assert hits == []
    assert secret not in reason


# ------------------------------------------------------------- firecrawl
# Phase 20: Firecrawl is a fallback fetch for JavaScript-heavy pages, not a
# research oracle. It is consulted only when the plain fetch came back with
# fewer than 200 characters of text AND `FIRECRAWL_API_KEY` is set.

def _client_returning(html):
    class C:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): ...
        async def get(self, url, headers=None): return _Resp(html)
    return C


def _recording_firecrawl(markdown="# Menu\n\n" + "Fresh bread baked daily. " * 20,
                         reason=None):
    calls = []

    async def fake_post_json(url, *, headers=None, params=None, json_body=None,
                             data=None, timeout=25.0, base_url=""):
        calls.append({"url": url, "headers": headers, "json_body": json_body})
        if reason is not None:
            return None, reason
        return {"success": True, "data": {"markdown": markdown}}, None
    return calls, fake_post_json


async def test_firecrawl_is_not_consulted_when_the_plain_fetch_has_enough_text(
        monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-not-a-real-key-123")
    long_page = "<p>" + ("word " * 100) + "</p>"
    monkeypatch.setattr(_web.httpx, "AsyncClient", _client_returning(long_page))
    calls, fake = _recording_firecrawl()
    monkeypatch.setattr(_web, "post_json", fake)

    text, reason = await _web.fetch_text("http://example.com")

    assert reason is None
    assert len(text) >= 200
    assert calls == []


async def test_firecrawl_is_not_consulted_without_the_key(monkeypatch):
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    monkeypatch.setattr(_web.httpx, "AsyncClient",
                        _client_returning("<div id='root'></div><script>app()</script>"))
    calls, fake = _recording_firecrawl()
    monkeypatch.setattr(_web, "post_json", fake)

    text, reason = await _web.fetch_text("http://example.com")

    assert reason is None
    assert text == ""
    assert calls == []


async def test_firecrawl_fills_in_a_javascript_heavy_page(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-not-a-real-key-123")
    monkeypatch.setattr(_web.httpx, "AsyncClient",
                        _client_returning("<div id='root'></div><script>app()</script>"))
    calls, fake = _recording_firecrawl()
    monkeypatch.setattr(_web, "post_json", fake)

    text, reason = await _web.fetch_text("http://example.com/menu")

    assert reason is None
    assert len(calls) == 1
    assert calls[0]["url"] == _web.FIRECRAWL_URL == "https://api.firecrawl.dev/v1/scrape"
    assert calls[0]["headers"]["Authorization"] == "Bearer fc-not-a-real-key-123"
    assert calls[0]["json_body"]["url"] == "http://example.com/menu"
    assert calls[0]["json_body"]["formats"] == ["markdown"]
    assert "Fresh bread baked daily." in text


async def test_firecrawl_result_is_a_plain_str_the_caller_wraps(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-not-a-real-key-123")
    monkeypatch.setattr(_web.httpx, "AsyncClient", _client_returning("<p>hi</p>"))
    _calls, fake = _recording_firecrawl()
    monkeypatch.setattr(_web, "post_json", fake)

    text, _reason = await _web.fetch_text("http://example.com", limit=50)

    assert isinstance(text, str)
    assert len(text) <= 50                           # the caller's limit still holds
    assert "untrusted_data" not in text.lower()      # never wrapped here


async def test_firecrawl_failure_never_leaks_the_key_and_keeps_the_plain_text(
        monkeypatch, caplog):
    key = "fc-not-a-real-key-123"
    monkeypatch.setenv("FIRECRAWL_API_KEY", key)
    monkeypatch.setattr(_web.httpx, "AsyncClient", _client_returning("<p>hi there</p>"))
    _calls, fake = _recording_firecrawl(
        reason=f"HTTP 401 from /v1/scrape — Authorization: Bearer {key}")
    monkeypatch.setattr(_web, "post_json", fake)

    scraped, reason = await _web._firecrawl_scrape("http://example.com", key)
    assert scraped is None
    assert reason and key not in reason

    with caplog.at_level("DEBUG"):
        text, reason = await _web.fetch_text("http://example.com")
    assert reason is None
    assert text == "hi there"          # a failed fallback never turns success into failure
    assert key not in caplog.text


async def test_firecrawl_refuses_a_response_without_markdown(monkeypatch):
    key = "fc-not-a-real-key-123"

    async def fake_post_json(*a, **kw):
        return {"success": False, "error": f"bad key {key}"}, None
    monkeypatch.setenv("FIRECRAWL_API_KEY", key)
    monkeypatch.setattr(_web, "post_json", fake_post_json)

    scraped, reason = await _web._firecrawl_scrape("http://example.com", key)
    assert scraped is None
    assert reason and key not in reason


async def test_firecrawl_never_receives_a_url_that_carries_a_credential(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-not-a-real-key-123")
    monkeypatch.setattr(_web.httpx, "AsyncClient", _client_returning("<p>hi</p>"))
    calls, fake = _recording_firecrawl()
    monkeypatch.setattr(_web, "post_json", fake)

    text, reason = await _web.fetch_text("https://feeds.example/cal.ics?token=abcdef123456")

    assert reason is None and text == "hi"
    assert calls == []
