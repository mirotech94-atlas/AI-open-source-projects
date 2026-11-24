"""`GET /api/models`: which model each Need asks, in order, with each
deployment's breaker and what the boot probes dropped; per agent, its Need,
its pin, what it would ask first now, and what actually answered its last
recorded run.

Read-only all the way down: looking at a half-open breaker must not take the
probe a real call needs, and nothing secret may reach the page.
"""
from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from atlas.api import auth
from atlas.kernel import breaker, llm
from atlas.kernel.llm import Need

SECRET = "s" * 40
PASSWORD = "a-long-enough-password"
GROQ_KEY = "gsk_live_" + "x" * 24


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """ROUTES, DROPPED, DEAD_PROVIDERS, the roster and the breakers are
    process globals: each test gets its own and leaves nothing behind."""
    from atlas.kernel import agents as agents_module
    monkeypatch.setattr(llm, "ROUTES", {})
    monkeypatch.setattr(llm, "DROPPED", {})
    monkeypatch.setattr(llm, "DEAD_PROVIDERS", set())
    monkeypatch.setattr(agents_module, "ROSTER", {})
    clock = [1000.0]
    breaker.reset(lambda: clock[0])
    yield clock
    breaker.reset()


@pytest.fixture
def signed_in(monkeypatch, memory_backend):
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", auth.hash_password(PASSWORD))
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "127.0.0.0/8,0.0.0.0/0")
    auth._attempts.clear()
    from atlas.api.main import app

    async def no_boot() -> None:
        return None

    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    with TestClient(app, client=("127.0.0.1", 51000)) as client:
        assert client.post("/api/login", json={"password": PASSWORD}).status_code == 200
        yield client


def _free_only(monkeypatch) -> None:
    for k in ("GEMINI_API_KEY", "OPENAI_API_KEY", "CEREBRAS_API_KEY", "MISTRAL_API_KEY",
              "COHERE_API_KEY", "ATLAS_MODELS_OPENROUTER_FREE", "CLOUDFLARE_API_TOKEN",
              "CLOUDFLARE_ACCOUNT_ID", "ATLAS_PRIVATE_ALLOW_CLOUD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-" + "a" * 30)
    monkeypatch.setenv("GROQ_API_KEY", GROQ_KEY)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-" + "o" * 30)
    monkeypatch.setenv("OLLAMA_URL", "http://ollama:11434")
    monkeypatch.setenv("ATLAS_FREE_ONLY", "true")
    llm.build_routes()
    llm.extend_routes_with_failover()


def _fail(status: int, retry_after: str | None = None) -> httpx.HTTPStatusError:
    headers = {"retry-after": retry_after} if retry_after else {}
    request = httpx.Request("POST", "https://example.test/v1")
    response = httpx.Response(status, headers=headers, request=request)
    return httpx.HTTPStatusError("refused", request=request, response=response)


def test_models_needs_a_session(monkeypatch, memory_backend):
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "127.0.0.0/8")
    from atlas.api.main import app

    async def no_boot() -> None:
        return None

    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    with TestClient(app, client=("127.0.0.1", 51000)) as client:
        assert client.get("/api/models").status_code == 401


def test_the_chains_are_the_free_bench_when_free_only_is_on(signed_in, monkeypatch,
                                                           memory_backend):
    """ATLAS_FREE_ONLY is on in prod: every cloud Need lists only the free
    bench (Groq's gpt-oss and qwen, OpenRouter :free), in the order asked,
    and PRIVATE stays local."""
    _free_only(monkeypatch)
    memory_backend.responses["FROM agent_runs"] = []
    body = signed_in.get("/api/models").json()
    assert body["free_only"] is True
    needs = {n["need"]: n for n in body["needs"]}
    assert set(needs) == {str(n) for n in Need}
    for need in ("trivial", "draft", "reason", "hard", "critical"):
        chain = needs[need]["chain"]
        assert chain, need
        assert [c["provider"] for c in chain] == [
            p.name for p in llm.ROUTES[Need(need)]]
        # DRAFT alone may end on the free local model (Miro 2026-10-29);
        # never a paid provider anywhere.
        last_local = chain[-1]["provider"] in ("ollama", "local")
        cloud = chain[:-1] if need == "draft" and last_local else chain
        assert all(c["provider"] in ("groq", "openrouter") for c in cloud), need
        assert all(c["model"].endswith(":free") for c in chain
                   if c["provider"] == "openrouter")
    assert [c["model"] for c in needs["reason"]["chain"]][:3] == [
        "openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b"]
    assert [c["provider"] for c in needs["private"]["chain"]] == ["ollama"]
    assert all(c["breaker"]["state"] == "closed" for n in body["needs"]
               for c in n["chain"])
    # The legend lists each deployment once, with every Need it serves.
    legend = {(d["provider"], d["model"]): d for d in body["deployments"]}
    assert len(legend) == len(body["deployments"])
    assert "reason" in legend[("groq", "openai/gpt-oss-120b")]["needs"]
    # Never a key, never a base URL.
    text = signed_in.get("/api/models").text
    assert GROQ_KEY not in text and "sk-ant-" not in text and "sk-or-" not in text
    assert "https://" not in text and "ollama:11434" not in text


def test_breaker_states_open_half_open_and_closed_are_reported(
        signed_in, monkeypatch, memory_backend, isolated):
    _free_only(monkeypatch)
    memory_backend.responses["FROM agent_runs"] = []
    groq = ("groq", "openai/gpt-oss-120b")
    rationed = ("groq", "qwen/qwen3.8-27b")
    breaker.failure(groq, _fail(402))                    # long cooldown, at once
    breaker.failure(rationed, _fail(429, retry_after="30"))
    isolated[0] += 45                                   # rationed: cooldown over

    body = signed_in.get("/api/models").json()
    reason = {(c["provider"], c["model"]): c["breaker"]
              for c in next(n for n in body["needs"] if n["need"] == "reason")["chain"]}
    assert reason[groq]["state"] == "open" and reason[groq]["why"] == "HTTP 402"
    until = datetime.fromisoformat(reason[groq]["until"])
    assert until > datetime.now(UTC)
    assert reason[rationed]["state"] == "half-open" and reason[rationed]["until"] is None
    assert reason[("groq", "openai/gpt-oss-20b")]["state"] == "closed"

    # Reading it took nothing: the half-open probe is still there for a call.
    assert breaker.allow(rationed) is True
    assert breaker.allow(rationed) is False             # and now it is taken


def test_state_of_never_claims_the_probe(isolated):
    key = ("groq", "m")
    for _ in range(breaker.FAILS_TO_OPEN):
        breaker.failure(key, RuntimeError("down"))
    assert breaker.state_of(key)["state"] == "open"
    isolated[0] += breaker.BASE_COOLDOWN_S + 1
    for _ in range(3):
        assert breaker.state_of(key)["state"] == "half-open"
    assert breaker.allow(key) is True
    breaker.success(key)
    assert breaker.state_of(key) == {"state": "closed", "why": "", "until": None,
                                     "fails": 0}


class _Refuses:
    optional = True

    def __init__(self, name: str, model: str, status: int) -> None:
        self.name, self.model, self.status = name, model, status

    async def complete(self, *a, **kw):
        request = httpx.Request("POST", "https://example.test")
        response = httpx.Response(self.status, text="acct_123 has no credit",
                                  request=request)
        raise httpx.HTTPStatusError("x", request=request, response=response)


class _Serves:
    optional = True
    name, model = "groq", "openai/gpt-oss-120b"

    async def complete(self, *a, **kw):
        return llm.Completion(text="ready", provider=self.name, model=self.model)


async def test_a_deployment_dropped_at_boot_is_kept_on_the_list_as_dropped():
    """verify_cloud takes a refusing provider out of every chain; the status
    screen must still show it -- unavailable, with the status code only,
    never the response body."""
    llm.ROUTES[Need.REASON] = [_Refuses("cerebras", "gpt-oss-120b", 402), _Serves()]
    llm.ROUTES[Need.DRAFT] = [_Refuses("cerebras", "gpt-oss-120b", 402)]
    await llm.verify_cloud()
    assert [p.name for p in llm.ROUTES[Need.REASON]] == ["groq"]
    assert llm.DROPPED == {
        ("reason", "cerebras", "gpt-oss-120b"): "refused at boot: HTTP 402",
        ("draft", "cerebras", "gpt-oss-120b"): "refused at boot: HTTP 402",
    }
    # A second boot probe replaces the record rather than piling up.
    llm.ROUTES[Need.REASON] = [_Serves()]
    llm.ROUTES[Need.DRAFT] = []
    await llm.verify_cloud()
    assert llm.DROPPED == {}


def test_dropped_deployments_and_agents_reach_the_payload(signed_in, memory_backend):
    from atlas.kernel import agents as agents_module
    from atlas.kernel.agents import Agent

    llm.ROUTES[Need.REASON] = [_Serves()]
    llm.DROPPED[("reason", "cerebras", "gpt-oss-120b")] = "refused at boot: HTTP 402"
    agents_module.ROSTER.update({
        "ops": Agent(name="ops", title="Ops", brief="", system="", tools=set(),
                     need=Need.REASON),
        "growth": Agent(name="growth", title="Growth", brief="", system="",
                        tools=set(), need=Need.DRAFT),
        "shop": Agent(name="shop", title="Shop", brief="", system="", tools=set(),
                      need=Need.REASON, model="cerebras:gpt-oss-120b"),
        "inbox": Agent(name="inbox", title="Inbox", brief="", system="", tools=set(),
                       need=Need.PRIVATE, model="groq:x"),
    })
    at = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)
    memory_backend.responses["FROM agent_runs"] = [
        {"agent": "ops", "at": at, "model": "openai/gpt-oss-20b", "provider": "groq"},
        {"agent": "shop", "at": at, "model": "", "provider": ""},
    ]
    body = signed_in.get("/api/models").json()

    reason = next(n for n in body["needs"] if n["need"] == "reason")
    assert reason["dropped"] == [{"provider": "cerebras", "model": "gpt-oss-120b",
                                  "why": "refused at boot: HTTP 402"}]
    legend = {(d["provider"], d["model"]): d for d in body["deployments"]}
    assert legend[("cerebras", "gpt-oss-120b")]["dropped"] == "refused at boot: HTTP 402"
    assert legend[("cerebras", "gpt-oss-120b")]["breaker"] is None
    assert "acct_123" not in signed_in.get("/api/models").text

    agents = {a["name"]: a for a in body["agents"]}
    assert agents["ops"]["last"] == "groq:openai/gpt-oss-20b"
    assert agents["ops"]["last_at"] == at.isoformat()
    assert agents["ops"]["next"] == {"model": "groq:openai/gpt-oss-120b", "kind": "chain"}
    # DRAFT has no chain of its own here: it borrows REASON's, as think() does.
    draft = next(n for n in body["needs"] if n["need"] == "draft")
    assert draft["borrowed_from"] == "reason"
    assert agents["growth"]["last"] is None and agents["growth"]["last_at"] is None
    assert agents["growth"]["next"]["model"] == "groq:openai/gpt-oss-120b"
    # A pin is tried first; this one cannot be (no key), and says why.
    assert agents["shop"]["pin"]["usable"] is False and agents["shop"]["pin"]["why"]
    assert agents["shop"]["last"] is None and agents["shop"]["last_at"] == at.isoformat()
    # PRIVATE never takes a pin, and never borrows a cloud chain.
    assert agents["inbox"]["pin"] is None and agents["inbox"]["next"] is None
    private = next(n for n in body["needs"] if n["need"] == "private")
    assert private["chain"] == [] and private["borrowed_from"] is None
