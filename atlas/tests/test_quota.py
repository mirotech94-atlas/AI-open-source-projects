"""The free allowance kept for the owner (kernel/quota.py).

Prod, 2026-10-06: every free deployment answered 429 within three hours,
spent by background work, and his voice turn had nothing left to answer
with. These pin the reading of what providers report and the rule that the
last of each allowance is his.
"""
from __future__ import annotations

import httpx
import pytest

from atlas.kernel import llm, quota
from atlas.kernel.llm import Completion, Message, Need

NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def _clock():
    quota.reset(clock=lambda: NOW)
    yield
    quota.reset()


class _Fake:
    optional = False

    def __init__(self, name: str, model: str, answer: str = "ok") -> None:
        self.name, self.model, self.answer = name, model, answer
        self.asked = 0

    async def complete(self, system, messages, tools=None, max_tokens=2048):
        self.asked += 1
        return Completion(text=self.answer, provider=self.name, model=self.model)


def _response(status: int, headers: dict[str, str] | None = None,
              text: str = "") -> httpx.Response:
    return httpx.Response(status, headers=headers or {}, text=text,
                          request=httpx.Request("POST", "https://x.test"))


GROQ_TPD_429 = (
    '{"error":{"message":"Rate limit reached for model `openai/gpt-oss-120b` in '
    'organization `org_x` service tier `on_demand` on tokens per day (TPD): '
    'Limit 200000, Used 199523, Requested 1167. Please try again in 4m56.7s. '
    'Need more tokens?","type":"tokens","code":"rate_limit_exceeded"}}')


# ------------------------------------------------------------------ reading

@pytest.mark.parametrize(("raw", "seconds"), [
    ("2m59.56s", 179.56), ("7.66s", 7.66), ("1h2m3s", 3723.0), ("250ms", 0.25),
    ("42", 42.0), ("", None), ("soon", None)])
def test_durations_are_read_as_the_provider_wrote_them(raw, seconds):
    got = quota.parse_duration(raw)
    assert (got is None) if seconds is None else got == pytest.approx(seconds)


def test_groq_request_headers_are_the_day_and_token_headers_are_not():
    """Groq's docs: -requests is requests per DAY, -tokens per MINUTE. A
    per-minute token count must never be read as what is left today."""
    p = _Fake("groq", "openai/gpt-oss-20b")
    quota.observe(p, _response(200, {
        "x-ratelimit-limit-requests": "1000",
        "x-ratelimit-remaining-requests": "640",
        "x-ratelimit-reset-requests": "2m59.56s",
        "x-ratelimit-limit-tokens": "8000",
        "x-ratelimit-remaining-tokens": "12"}))
    assert quota.requests_left(p) == 640
    # Tokens today: the documented 200K, nothing spent yet -- not the 12
    # tokens left in this minute.
    assert quota.tokens_left(p) == 200_000
    assert quota.verdict(p) == quota.OK


def test_a_daily_token_429_says_what_is_used_and_when_to_come_back():
    p = _Fake("groq", "openai/gpt-oss-120b")
    quota.observe(p, _response(429, text=GROQ_TPD_429))
    assert quota.tokens_left(p) == 200_000 - 199_523
    assert quota.verdict(p) == quota.EXHAUSTED
    reset = quota.next_reset([p])
    assert reset is not None and reset.timestamp() == pytest.approx(NOW + 296.7)


def test_tokens_counted_from_answers_draw_the_day_down():
    p = _Fake("groq", "openai/gpt-oss-20b")
    quota.spent(p, 150_000)
    assert quota.tokens_left(p) == 50_000
    assert quota.verdict(p) == quota.OK          # 50K is above the 40K reserve
    quota.spent(p, 15_000)
    assert quota.verdict(p) == quota.RESERVED    # 35K: only the owner now


def test_a_model_with_no_known_daily_figure_is_never_guessed_spent():
    """llama-3.1-8b-instant is not in Groq's published free table. Unknown
    is allowed, never treated as spent or as plenty."""
    p = _Fake("groq", "llama-3.1-8b-instant")
    quota.spent(p, 10_000_000)
    assert quota.tokens_left(p) is None
    assert quota.verdict(p) == quota.OK


def test_openrouter_free_models_share_one_pool_read_from_its_429():
    a = _Fake("openrouter", "a/one:free")
    b = _Fake("openrouter", "b/two:free")
    quota.observe(a, _response(429, {
        "X-RateLimit-Limit": "50", "X-RateLimit-Remaining": "0",
        "X-RateLimit-Reset": str(int((NOW + 3 * 3600) * 1000))}))
    assert quota.verdict(b) == quota.EXHAUSTED   # the same key's pool
    assert quota.next_reset([b]).timestamp() == pytest.approx(NOW + 3 * 3600)


def test_openrouter_per_minute_429_is_left_to_the_breaker():
    a = _Fake("openrouter", "a/one:free")
    quota.observe(a, _response(429, {
        "X-RateLimit-Limit": "20", "X-RateLimit-Remaining": "0",
        "X-RateLimit-Reset": str(int((NOW + 30) * 1000))}))
    assert quota.verdict(a) == quota.OK


def test_openrouter_key_endpoint_is_read_as_the_days_free_requests():
    assert quota.read_openrouter_key({"data": {"free_model_daily_requests": {
        "used": 42, "limit": 50, "remaining": 8}}})
    p = _Fake("openrouter", "x:free")
    assert quota.requests_left(p) == 8
    # 8 of 50 is under the 20% (10) kept for the owner.
    assert quota.verdict(p) == quota.RESERVED


# --------------------------------------------------------- the reserve rule

def _reserved_groq() -> _Fake:
    p = _Fake("groq", "openai/gpt-oss-20b", "from groq")
    quota.spent(p, 170_000)                     # 30K left of 200K
    return p


async def test_background_work_skips_a_reserved_deployment():
    reserved, other = _reserved_groq(), _Fake("cerebras", "c", "from cerebras")
    llm.ROUTES[Need.DRAFT] = [reserved, other]
    reply = await llm.think(Need.DRAFT, "s", [Message("user", "q")])
    assert reply.text == "from cerebras" and reserved.asked == 0


async def test_owner_may_spend_the_reserve():
    reserved, other = _reserved_groq(), _Fake("cerebras", "c", "from cerebras")
    llm.ROUTES[Need.DRAFT] = [reserved, other]
    with quota.interactive():
        reply = await llm.think(Need.DRAFT, "s", [Message("user", "q")])
    assert reply.text == "from groq" and reserved.asked == 1


async def test_the_explicit_flag_wins_over_the_context():
    reserved = _reserved_groq()
    llm.ROUTES[Need.DRAFT] = [reserved]
    reply = await llm.think(Need.DRAFT, "s", [Message("user", "q")], interactive=True)
    assert reply.text == "from groq"


async def test_background_that_finds_only_reserve_waits_without_asking():
    """Nothing is asked, no breaker trips and no outage is posted: this is
    not a failure, it is the allowance being kept."""
    from atlas.kernel import breaker, outage
    reserved = _reserved_groq()
    llm.ROUTES[Need.DRAFT] = [reserved]
    with pytest.raises(quota.ReservedForOwner):
        await llm.think(Need.DRAFT, "s", [Message("user", "q")])
    assert reserved.asked == 0
    assert breaker.snapshot() == {} and not outage.active()


async def test_open_breakers_never_readmit_the_reserve_for_background():
    from atlas.kernel import breaker
    reserved, other = _reserved_groq(), _Fake("cerebras", "c", "from cerebras")
    breaker.failure(breaker.key_of(other), RuntimeError("x"))
    breaker.failure(breaker.key_of(other), RuntimeError("x"))
    breaker.failure(breaker.key_of(other), RuntimeError("x"))
    assert breaker.is_open(breaker.key_of(other))
    llm.ROUTES[Need.DRAFT] = [reserved, other]
    reply = await llm.think(Need.DRAFT, "s", [Message("user", "q")])
    assert reply.text == "from cerebras" and reserved.asked == 0


async def test_a_spent_deployment_is_skipped_even_for_owner_when_another_answers():
    spent = _Fake("groq", "openai/gpt-oss-120b", "from groq")
    quota.observe(spent, _response(429, text=GROQ_TPD_429))
    other = _Fake("cerebras", "c", "from cerebras")
    llm.ROUTES[Need.DRAFT] = [spent, other]
    with quota.interactive():
        reply = await llm.think(Need.DRAFT, "s", [Message("user", "q")])
    assert reply.text == "from cerebras" and spent.asked == 0


async def test_when_everything_is_spent_owner_is_still_asked_for():
    """'No model answered' must mean the models were asked."""
    spent = _Fake("groq", "openai/gpt-oss-120b", "came back early")
    quota.observe(spent, _response(429, text=GROQ_TPD_429))
    llm.ROUTES[Need.DRAFT] = [spent]
    with quota.interactive():
        reply = await llm.think(Need.DRAFT, "s", [Message("user", "q")])
    assert reply.text == "came back early"


async def test_a_job_spawned_during_his_turn_is_background_again():
    from atlas.kernel.background import spawn
    seen: list[bool] = []

    async def job() -> None:
        seen.append(quota.is_interactive())

    with quota.interactive():
        assert quota.is_interactive()
        await spawn(job(), "test.quota")
    assert seen == [False]


async def test_the_provider_client_reads_the_headers_and_counts_the_tokens(
        monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")

    async def post(self, url, headers=None, json=None, **_kw):
        return httpx.Response(200, headers={
            "x-ratelimit-limit-requests": "1000",
            "x-ratelimit-remaining-requests": "999",
            "x-ratelimit-reset-requests": "1m26.4s"}, json={
            "choices": [{"message": {"role": "assistant", "content": "hi"}}],
            "usage": {"prompt_tokens": 300, "completion_tokens": 40,
                      "total_tokens": 340}},
            request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    p = llm.GroqProvider("openai/gpt-oss-20b")
    await p.complete("s", [Message("user", "q")])
    assert quota.requests_left(p) == 999
    assert quota.tokens_left(p) == 200_000 - 340


def test_reserve_sizes():
    assert quota.reserve_tokens(200_000) == 40_000
    assert quota.reserve_tokens(500_000) == 100_000
    assert quota.reserve_tokens(3_600) == 1_800      # never the whole pool
    assert quota.reserve_requests(50) == 10
    assert quota.reserve_requests(1000) == 200


def test_the_reserve_is_set_by_env(monkeypatch):
    monkeypatch.setenv("ATLAS_FREE_RESERVE_TOKENS", "60000")
    assert quota.reserve_tokens(200_000) == 60_000



def test_a_spent_openrouter_count_with_no_reset_does_not_shut_it_for_good():
    """/api/v1/key gives no reset time. A zero read there is a reading: once
    it is older than the refresh interval it is unknown, and the four :free
    models are asked again (and the count re-read) instead of vanishing
    from every chain until a restart."""
    now = [NOW]
    quota.reset(clock=lambda: now[0])
    assert quota.read_openrouter_key({"data": {"free_model_daily_requests": {
        "used": 50, "limit": 50, "remaining": 0}}})
    p = _Fake("openrouter", "x:free")
    assert quota.verdict(p) == quota.EXHAUSTED
    now[0] += quota.OPENROUTER_REFRESH_S + 1
    assert quota.verdict(p) != quota.EXHAUSTED


def test_spent_out_means_every_one_not_any_one():
    healthy = _Fake("cerebras", "c")
    spent = _Fake("groq", "openai/gpt-oss-120b")
    quota.observe(spent, _response(429, text=GROQ_TPD_429))
    assert not quota.spent_out([healthy, spent])
    assert quota.spent_out([spent])
    assert not quota.spent_out([])
