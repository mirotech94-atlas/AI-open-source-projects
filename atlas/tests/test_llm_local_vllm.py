"""PRIVATE work runs on qwen3.8 on the Spark's vLLM, never on Ollama, never
on a cloud API.

The Spark serves an OpenAI-compatible API on its own loopback; the Mac
reaches it through an SSH tunnel and the api container through
host.docker.internal. `LOCAL_LLM_BASE_URL` selects it. A base URL whose
host is not an allow-listed name or a loopback/private address is refused
outright, so one typo cannot send personal mail to someone else's server:
PRIVATE then has no route and is refused (the mail check reads UNKNOWN).

The bearer key used here is a made-up marker, not a credential.
"""
from __future__ import annotations

import json

import httpx
import pytest

from atlas.kernel import llm
from atlas.kernel.contracts import Health
from atlas.kernel.llm import Message, Need

FAKE_KEY = "fake-local-key-not-a-secret"
BASE = "http://host.docker.internal:18000/v1"


@pytest.fixture(autouse=True)
def clean_routes():
    llm.ROUTES.clear()
    llm.DROPPED.clear()
    yield
    llm.ROUTES.clear()
    llm.DROPPED.clear()


def _env(monkeypatch, **kw) -> None:
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OLLAMA_URL",
                "OPENROUTER_API_KEY", "ATLAS_PRIVATE_ALLOW_CLOUD",
                "GROQ_API_KEY", "GEMINI_API_KEY", "COHERE_API_KEY",
                "CEREBRAS_API_KEY", "MISTRAL_API_KEY", "ATLAS_PREFER_FREE",
                "HF_TOKEN", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID",
                "LOCAL_LLM_BASE_URL", "LOCAL_LLM_MODEL", "LOCAL_LLM_API_KEY",
                "LOCAL_LLM_THINKING", "ATLAS_FREE_ONLY", "ATLAS_MODEL_LOCAL",
                "LOCAL_LLM_ALLOWED_HOSTS"):
        monkeypatch.delenv(key, raising=False)
    for key, value in kw.items():
        monkeypatch.setenv(key, value)


# ------------------------------------------------------------- routing

def test_local_base_url_routes_private_to_qwen_on_vllm(monkeypatch):
    _env(monkeypatch, LOCAL_LLM_BASE_URL=BASE, LOCAL_LLM_API_KEY=FAKE_KEY,
         ANTHROPIC_API_KEY="sk-test")
    llm.build_routes()
    chain = llm.ROUTES[Need.PRIVATE]
    assert [(p.name, p.model) for p in chain] == [("local", "qwen3.8:27b")]
    assert chain[0].local is True
    assert llm.local_model() == "qwen3.8:27b"


def test_local_base_url_wins_over_a_leftover_ollama_url(monkeypatch):
    _env(monkeypatch, LOCAL_LLM_BASE_URL=BASE, OLLAMA_URL="http://ollama:11434",
         ANTHROPIC_API_KEY="sk-test")
    llm.build_routes()
    names = {p.name for chain in llm.ROUTES.values() for p in chain}
    assert "ollama" not in names
    assert [p.name for p in llm.ROUTES[Need.PRIVATE]] == ["local"]


def test_ollama_is_not_a_default_any_more(monkeypatch):
    """No LOCAL_LLM_BASE_URL and no OLLAMA_URL: nothing local, PRIVATE refused."""
    _env(monkeypatch, ANTHROPIC_API_KEY="sk-test")
    llm.build_routes()
    assert Need.PRIVATE not in llm.ROUTES


def test_free_only_keeps_private_on_the_local_model(monkeypatch):
    _env(monkeypatch, LOCAL_LLM_BASE_URL=BASE, ANTHROPIC_API_KEY="sk-test",
         GROQ_API_KEY="gsk-test", ATLAS_FREE_ONLY="true")
    llm.build_routes()
    assert [p.name for p in llm.ROUTES[Need.PRIVATE]] == ["local"]


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:18000/v1", "http://localhost:18000/v1",
    "http://[::1]:18000/v1", "http://host.docker.internal:18000/v1",
    "http://192.0.2.1:18000/v1", "http://10.0.0.9:8000/v1",
    "http://172.17.0.1:8000/v1", "http://[::ffff:127.0.0.1]:18000/v1",
    "http://HOST.DOCKER.INTERNAL:18000/v1",
])
def test_private_hosts_are_accepted(url):
    assert llm.local_base_url_ok(url)


@pytest.mark.parametrize("url", [
    "https://api.openai.com/v1", "https://openrouter.ai/api/v1",
    "http://8.8.8.8:8000/v1", "http://host.docker.internal.evil.com/v1",
    "http://localhost.evil.com/v1", "ftp://127.0.0.1/v1", "", "not a url",
    "http://contact72@example.com/v1",
    # bare single-label names: DNS decides where they go
    "http://spark:8000/v1", "http://ollama:11434/v1",
    "http://host.orb.internal:18000/v1",
    # unspecified, reserved, multicast, link-local (cloud metadata)
    "http://0.0.0.0:18000/v1", "http://[::]:18000/v1",
    "http://240.0.0.1/v1", "http://224.0.0.1/v1",
    "http://169.254.169.254/v1", "http://[fe80::1]/v1",
    # IPv6 forms that carry a public IPv4 address
    "http://[::ffff:8.8.8.8]/v1", "http://[2002:808:808::1]/v1",
    "http://[2001:0:4136:e378:8000:63bf:f7f7:f7f7]/v1",
    "http://127.0.0.1:99999/v1",
])
def test_cloud_or_odd_hosts_are_refused(url):
    assert not llm.local_base_url_ok(url)


def test_the_allowed_host_list_comes_from_the_env(monkeypatch):
    monkeypatch.setenv("LOCAL_LLM_ALLOWED_HOSTS", "spark-gw.lan, localhost")
    assert llm.local_base_url_ok("http://spark-gw.lan:18000/v1")
    assert not llm.local_base_url_ok(BASE)       # replaced, not extended
    assert llm.local_base_url_ok("http://127.0.0.1:18000/v1")   # literal
    monkeypatch.setenv("LOCAL_LLM_ALLOWED_HOSTS", " , ")
    assert llm.local_base_url_ok(BASE)           # blank means the default


async def test_a_bare_hostname_fails_closed_and_the_check_reads_unknown(
        monkeypatch):
    """No route for PRIVATE: classify refuses, the mail check is UNKNOWN,
    and no request leaves the process."""
    from atlas.modules import mail
    _env(monkeypatch, LOCAL_LLM_BASE_URL="http://spark:8000/v1",
         LOCAL_LLM_API_KEY=FAKE_KEY, ANTHROPIC_API_KEY="sk-test",
         GROQ_API_KEY="gsk-test")
    llm.build_routes()
    assert Need.PRIVATE not in llm.ROUTES
    sent: list[str] = []

    async def post(self, url, *a, **kw):
        sent.append(str(url))
        raise AssertionError("PRIVATE text must not be sent anywhere")

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    obs = await mail.check_classify_local()
    assert obs.health is Health.UNKNOWN
    assert sent == []
    assert FAKE_KEY not in obs.detail


async def test_local_calls_ignore_proxy_env_and_redirects(monkeypatch):
    _env(monkeypatch, LOCAL_LLM_BASE_URL=BASE, LOCAL_LLM_API_KEY=FAKE_KEY)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.com:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example.com:3128")
    made: list[dict] = []
    real_init = httpx.AsyncClient.__init__

    def init(self, *a, **kw):
        made.append(kw)
        real_init(self, *a, **kw)

    async def get(self, url, *a, **kw):
        return _Resp(200, {"data": [{"id": "qwen3.8:27b"}]})

    async def post(self, url, *a, **kw):
        return _Resp(200, {"choices": [{"message": {"content": "WORK"}}]})

    monkeypatch.setattr(httpx.AsyncClient, "__init__", init)
    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    provider = llm.LocalOpenAIProvider()
    assert await provider.available() is True
    await provider.complete("s", [Message("user", "hi")])
    assert len(made) == 2
    for kw in made:
        assert kw["trust_env"] is False
        assert kw["follow_redirects"] is False


async def test_a_redirect_from_the_local_model_is_a_failure(monkeypatch):
    _env(monkeypatch, LOCAL_LLM_BASE_URL=BASE)

    def moved(method, url, code):
        return httpx.Response(code, headers={"location": "https://evil.example/v1"},
                              request=httpx.Request(method, url))

    async def get(self, url, *a, **kw):
        return moved("GET", url, 302)

    async def post(self, url, *a, **kw):
        return moved("POST", url, 307)

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    provider = llm.LocalOpenAIProvider()
    assert await provider.available() is False
    with pytest.raises(httpx.HTTPStatusError):
        await provider.complete("s", [Message("user", "hi")])


def test_a_cloud_base_url_fails_closed(monkeypatch):
    _env(monkeypatch, LOCAL_LLM_BASE_URL="https://api.openai.com/v1",
         LOCAL_LLM_API_KEY=FAKE_KEY, ANTHROPIC_API_KEY="sk-test")
    llm.build_routes()
    assert Need.PRIVATE not in llm.ROUTES
    assert all(p.name != "local" for c in llm.ROUTES.values() for p in c)


async def test_private_never_reaches_the_cloud_when_local_fails(monkeypatch):
    _env(monkeypatch, LOCAL_LLM_BASE_URL=BASE, LOCAL_LLM_API_KEY=FAKE_KEY,
         ANTHROPIC_API_KEY="sk-test", GROQ_API_KEY="gsk-test")
    llm.build_routes()
    hosts: list[str] = []

    async def refused(self, url, *a, **kw):
        hosts.append(httpx.URL(url).host)
        raise httpx.ConnectError("refused", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", refused)
    with pytest.raises(llm.LocalModelUnavailable) as err:
        await llm.think(Need.PRIVATE, "s", [Message("user", "my inbox")])
    assert hosts == ["host.docker.internal"]
    assert FAKE_KEY not in str(err.value)


def test_vision_never_routes_to_the_local_model(monkeypatch):
    _env(monkeypatch, LOCAL_LLM_BASE_URL=BASE,
         ATLAS_MODELS_VISION="local:qwen3.8:27b")
    assert llm.vision_chain() == []


# ------------------------------------------------------------- wire format

class _Resp:
    def __init__(self, status: int, body: dict) -> None:
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)

    def json(self) -> dict:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "bad", request=httpx.Request("POST", BASE),
                response=httpx.Response(self.status_code))


async def test_complete_speaks_openai_with_thinking_off(monkeypatch):
    _env(monkeypatch, LOCAL_LLM_BASE_URL=BASE, LOCAL_LLM_API_KEY=FAKE_KEY)
    seen: dict = {}

    async def post(self, url, *a, json=None, headers=None, **kw):
        seen.update(url=url, json=json, headers=headers)
        return _Resp(200, {
            "choices": [{"message": {"content": "WORK", "tool_calls": [
                {"id": "c1", "function": {"name": "mail.search",
                                          "arguments": '{"q": "x"}'}}]}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 2}})

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    provider = llm.LocalOpenAIProvider()
    out = await provider.complete(
        "sys", [Message("user", "hi")],
        tools=[{"name": "mail.search", "description": "d",
                "input_schema": {"type": "object"}}], max_tokens=32)
    assert seen["url"] == BASE + "/chat/completions"
    assert seen["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"
    assert seen["json"]["model"] == "qwen3.8:27b"
    assert seen["json"]["max_tokens"] == 32
    assert seen["json"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert seen["json"]["tools"][0]["function"]["name"] == "mail.search"
    assert out.text == "WORK" and out.provider == "local"
    assert out.tool_calls == [{"name": "mail.search", "input": {"q": "x"},
                               "id": "c1"}]
    assert (out.input_tokens, out.output_tokens) == (7, 2)


async def test_an_empty_answer_is_a_failure_not_an_answer(monkeypatch):
    _env(monkeypatch, LOCAL_LLM_BASE_URL=BASE)

    async def post(self, url, *a, **kw):
        return _Resp(200, {"choices": [{"message": {"content": None},
                                        "finish_reason": "length"}]})

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    with pytest.raises(RuntimeError, match="empty"):
        await llm.LocalOpenAIProvider().complete("s", [Message("user", "hi")])


async def test_available_reads_the_model_list(monkeypatch):
    _env(monkeypatch, LOCAL_LLM_BASE_URL=BASE, LOCAL_LLM_API_KEY=FAKE_KEY)
    listed = {"data": [{"id": "qwen3.8:27b"}]}

    async def get(self, url, *a, headers=None, **kw):
        assert url == BASE + "/models"
        assert headers["Authorization"] == f"Bearer {FAKE_KEY}"
        return _Resp(200, listed)

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    assert await llm.LocalOpenAIProvider().available() is True
    listed["data"] = [{"id": "something-else"}]
    assert await llm.LocalOpenAIProvider().available() is False


async def test_verify_local_drops_an_unreachable_local_model(monkeypatch):
    _env(monkeypatch, LOCAL_LLM_BASE_URL=BASE, ANTHROPIC_API_KEY="sk-test")
    llm.build_routes()

    async def get(self, url, *a, **kw):
        raise httpx.ConnectError("refused", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    assert await llm.verify_local() is False
    assert Need.PRIVATE not in llm.ROUTES
    assert not any(p.name == "local" for c in llm.ROUTES.values() for p in c)
    assert any(k[1] == "local" for k in llm.DROPPED)


async def test_verify_cloud_leaves_the_local_drop_record_alone(monkeypatch):
    _env(monkeypatch, LOCAL_LLM_BASE_URL=BASE, ANTHROPIC_API_KEY="sk-test")
    llm.build_routes()
    llm.DROPPED[("private", "local", "qwen3.8:27b")] = "not serving at boot"

    async def no_refresh(**kw):
        return None

    from atlas.kernel import quota
    monkeypatch.setattr(quota, "refresh_openrouter", no_refresh)
    await llm.verify_cloud()
    assert ("private", "local", "qwen3.8:27b") in llm.DROPPED


# ------------------------------------------------------------- mail check

async def test_mail_classify_check_is_ok_when_the_local_model_answers(monkeypatch):
    from atlas.modules import mail

    async def fake_classify(subject, sender, body):
        return {"category": "fyi", "urgency": 0}

    monkeypatch.setattr(mail, "classify", fake_classify)
    monkeypatch.setattr(mail, "local_model", lambda: "qwen3.8:27b")
    obs = await mail.check_classify_local()
    assert obs.health is Health.OK
    assert "qwen3.8:27b" in obs.detail


async def test_mail_classify_check_is_unknown_when_private_is_refused(monkeypatch):
    from atlas.modules import mail

    async def fake_classify(subject, sender, body):
        return {"unclassified": "local model unavailable: no local model"}

    monkeypatch.setattr(mail, "classify", fake_classify)
    monkeypatch.setattr(mail, "local_model", lambda: None)
    obs = await mail.check_classify_local()
    assert obs.health is Health.UNKNOWN
    assert "local model" in obs.detail


# ------------------------------------- free-only DRAFT falls back to the Spark
# the owner, 2026-10-29: background shift drafts failed every afternoon with
# ReservedForOwner / "all providers failed for draft". Under ATLAS_FREE_ONLY the
# local qwen3.8 on the Spark (free, no quota) is the LAST entry of the DRAFT
# chain -- never a paid model.

PAID = {"anthropic", "openai"}


def test_free_only_draft_ends_with_the_local_model(monkeypatch):
    _env(monkeypatch, LOCAL_LLM_BASE_URL=BASE, ANTHROPIC_API_KEY="sk-test",
         GROQ_API_KEY="gsk-test", ATLAS_FREE_ONLY="true")
    llm.build_routes()
    chain = llm.ROUTES[Need.DRAFT]
    assert chain[-1].name == "local" and chain[-1].local is True
    assert len(chain) > 1, "the free bench still goes first"
    assert not {p.name for p in chain} & PAID


def test_free_only_draft_without_a_local_url_is_unchanged(monkeypatch):
    _env(monkeypatch, ANTHROPIC_API_KEY="sk-test", GROQ_API_KEY="gsk-test",
         ATLAS_FREE_ONLY="true")
    llm.build_routes()
    chain = llm.ROUTES[Need.DRAFT]
    assert all(not getattr(p, "local", False) for p in chain)
    assert not {p.name for p in chain} & PAID


async def test_a_drained_free_bench_reaches_the_local_model(monkeypatch):
    """Background work with every free deployment spent or kept for the owner
    still gets an answer from the Spark instead of ReservedForOwner."""
    from atlas.kernel import quota
    _env(monkeypatch, LOCAL_LLM_BASE_URL=BASE, ANTHROPIC_API_KEY="sk-test",
         GROQ_API_KEY="gsk-test", ATLAS_FREE_ONLY="true")
    llm.build_routes()
    asked: list[str] = []

    def only_local(provider, *, interactive_call=None):
        return bool(getattr(provider, "local", False))

    async def answer(self, system, messages, tools=None, max_tokens=2048):
        asked.append(self.name)
        return llm.Completion("drafted", self.name, self.model)

    monkeypatch.setattr(quota, "allowed", only_local)
    monkeypatch.setattr(llm.LocalOpenAIProvider, "complete", answer)
    reply = await llm.think(Need.DRAFT, "sys", [Message("user", "draft it")],
                            interactive=False)
    assert asked == ["local"]
    assert reply.provider == "local"
