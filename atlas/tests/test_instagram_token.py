"""The Instagram access token never leaves the process.

The Graph API used to take its token as `?access_token=` (now a header), and httpx's own
exceptions print the full URL. Before this, that string reached
`observations.detail` (and from there the reasoner's prompt, through
`diagnose`) and `actions_log`, which is a hash chain: a row there can never
be scrubbed. The token is removed where the error is made, and the audit and
the observation writes redact again as a second net.
"""
from __future__ import annotations

import traceback

import httpx
import pytest

from atlas.kernel import executor
from atlas.kernel.contracts import Action, ActionResult, Tier
from atlas.kernel.executor import ExecutionRequest, execute, preview
from atlas.kernel.permissions import classify
from atlas.kernel.registry import REGISTRY
from atlas.modules import instagram

TOKEN = "EAABsbCS1iHgBAKzExampleLongLivedToken0123456789"
SHORT = "tok123"          # shorter than redact()'s eight-character env floor


def graph(monkeypatch, body: dict | str, status: int = 400,
          token: str = TOKEN) -> list[str]:
    """Every Graph call answers `status` with `body`. Returns the URLs asked."""
    monkeypatch.setenv("INSTAGRAM_ACCOUNT_ID", "17841400000000000")
    monkeypatch.setenv("INSTAGRAM_TOKEN", token)
    asked: list[str] = []
    headers: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        headers.append(request.headers.get("authorization", ""))
        if isinstance(body, dict):
            return httpx.Response(status, json=body)
        return httpx.Response(status, text=body)

    real = httpx.AsyncClient

    def client(*_a, **_k):
        return real(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(httpx, "AsyncClient", client)
    graph.headers = headers  # type: ignore[attr-defined]
    return asked


def error_body(token: str = TOKEN) -> dict:
    # Graph's error envelope, with the token echoed back in the message --
    # the worst case: it is in the body as well as the URL.
    return {"error": {"message": f"Invalid OAuth access token - Cannot parse "
                                 f"access token {token}",
                      "type": "OAuthException", "code": 190,
                      "fbtrace_id": "AbCdEf"}}


@pytest.mark.parametrize("check", [instagram.check_engagement,
                                   instagram.check_comments_pending,
                                   instagram.check_token])
@pytest.mark.parametrize("token", [TOKEN, SHORT])
async def test_a_graph_error_body_with_the_token_yields_a_detail_without_it(
        monkeypatch, check, token):
    asked = graph(monkeypatch, error_body(token), token=token)
    obs = await check("main")
    # ed4a247: the token rides in the Authorization header, never the URL --
    # httpx prints the URL in every HTTPStatusError, and that message lands
    # in observations.detail. The body still echoes it (the worst case).
    assert asked and token not in asked[0], "the token must not be in the URL"
    assert any(token in h for h in graph.headers), "the token rides in the header"
    assert token not in (obs.detail or "")
    assert token not in str(obs.evidence)
    assert "access_token=" not in (obs.detail or "")
    # What Graph said still reaches the detail, which is the useful part.
    if check is not instagram.check_token:
        assert "Invalid OAuth access token" in obs.detail
        assert "400" in obs.detail


@pytest.mark.parametrize("token", [TOKEN, SHORT])
async def test_a_raised_graph_error_carries_no_token_even_in_its_traceback(
        monkeypatch, token):
    graph(monkeypatch, error_body(token), status=500, token=token)
    with pytest.raises(instagram.GraphError) as info:
        await instagram.hide_comment("main", "178000000001")
    rendered = "".join(traceback.format_exception(info.value))
    assert token not in rendered
    assert info.value.status_code == 500


async def test_an_unreachable_graph_carries_no_token(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_ACCOUNT_ID", "17841400000000000")
    monkeypatch.setenv("INSTAGRAM_TOKEN", TOKEN)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot connect to {request.url}",
                                 request=request)

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda *a, **k: real(transport=httpx.MockTransport(handler)))
    obs = await instagram.check_engagement("main")
    assert TOKEN not in obs.detail and obs.health == "unknown"
    with pytest.raises(instagram.GraphError) as info:
        await instagram.reply_comment("main", "1780", "thanks")
    assert TOKEN not in "".join(traceback.format_exception(info.value))


async def test_a_publish_error_carries_no_token(monkeypatch):
    graph(monkeypatch, error_body(), status=400)
    with pytest.raises(instagram.GraphError) as info:
        await instagram.publish("a caption", "https://x/i.jpg",
                                content_kind="flycheap")
    assert TOKEN not in "".join(traceback.format_exception(info.value))


# ------------------------------------------------ the second net, in the kernel

@pytest.fixture
def registry(memory_backend, monkeypatch):
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    REGISTRY.actions.clear()
    REGISTRY.decisions.clear()
    executor._recent.clear()
    yield memory_backend
    REGISTRY.actions.clear()
    REGISTRY.decisions.clear()
    executor._recent.clear()


def _raising_action(monkeypatch, *, dry_raises: bool) -> None:
    """An action whose run (and, if asked, dry run) raises a raw httpx error
    with the token in its URL -- what the old `_get`/`_post` raised."""
    monkeypatch.setenv("INSTAGRAM_TOKEN", TOKEN)
    request = httpx.Request(
        "POST", f"https://graph.facebook.com/v21.0/1780?access_token={TOKEN}")
    response = httpx.Response(400, request=request)

    async def boom(**_kw):
        raise httpx.HTTPStatusError(f"400 for url '{request.url}'",
                                    request=request, response=response)

    async def ok_dry(**_kw):
        return ActionResult(True, "fine")

    async def undo(_token):
        return ActionResult(True, "undone")

    action = Action(name="probe.leak", tier=Tier.ASK, summary="s", run=boom,
                    dry_run=boom if dry_raises else ok_dry, rollback=undo,
                    params={})
    REGISTRY.actions[action.name] = action
    REGISTRY.decisions[action.name] = classify(action)


async def test_an_action_that_raises_writes_no_token_to_the_audit_chain(
        registry, monkeypatch):
    _raising_action(monkeypatch, dry_raises=False)
    result = await execute(ExecutionRequest("probe.leak", {}, "why",
                                            approved_by="miro"))
    assert result.ok is False
    rows = registry.audit_rows
    assert any(r.event == "execute.error" for r in rows)
    assert all(TOKEN not in r.payload for r in rows)


async def test_a_raising_dry_run_puts_no_token_on_the_card(registry, monkeypatch):
    _raising_action(monkeypatch, dry_raises=True)
    result = await preview("probe.leak", {})
    assert result.ok is False and TOKEN not in result.detail
    assert "HTTP 400" in result.detail


async def test_the_audit_redacts_whatever_reaches_it(registry, monkeypatch):
    from atlas.kernel.audit import audit
    monkeypatch.setenv("INSTAGRAM_TOKEN", TOKEN)
    await audit("x", "y", {"error": f"GET /me?access_token={TOKEN} failed",
                           "note": "basic health check"})
    payload = registry.audit_rows[-1].payload
    assert TOKEN not in payload
    assert "basic health check" in payload, "prose is not a header"


async def test_an_observation_is_redacted_before_it_is_stored(monkeypatch):
    from atlas.kernel import state
    from atlas.kernel.contracts import Health, Observation
    monkeypatch.setenv("INSTAGRAM_TOKEN", TOKEN)
    written: list = []

    async def capture(_sql, *args):
        written.append(args)

    monkeypatch.setattr(state, "execute_sql", capture)
    await state.record_observation(1, Observation(
        "main_engagement", Health.UNKNOWN, detail=f"url had {TOKEN}",
        evidence={"url": f"https://g/?access_token={TOKEN}"}))
    assert TOKEN not in str(written)


# ------------------------------------ a field Graph did not send is not a zero

async def test_a_missing_reach_is_unknown_not_zero(monkeypatch):
    graph(monkeypatch, {"data": [{"name": "profile_views",
                                  "values": [{"value": 12}]}]}, status=200)
    obs = await instagram.check_engagement("main")
    assert obs.health == "unknown" and obs.value is None


async def test_a_reported_reach_is_read(monkeypatch):
    graph(monkeypatch, {"data": [{"name": "reach", "values": [{"value": 340}]}]},
          status=200)
    obs = await instagram.check_engagement("main")
    assert obs.health == "ok" and obs.value == 340.0


async def test_engagement_asks_for_total_value_and_reads_it(monkeypatch):
    """Verified live 2026-09-25: without metric_type=total_value Graph answers
    400 (#100) for profile_views, and with it the number is under
    total_value, not values[]."""
    asked = graph(monkeypatch, {"data": [
        {"name": "reach", "period": "day", "total_value": {"value": 57}},
        {"name": "profile_views", "period": "day", "total_value": {"value": 23}},
    ]}, status=200)
    obs = await instagram.check_engagement("main")
    assert obs.health == "ok" and obs.value == 57.0
    assert "metric_type=total_value" in asked[0] and "period=day" in asked[0]
    # The dashboard shows the detail: never blank, and a zero is a real zero.
    assert obs.detail.startswith("reach 57/day, profile views 23 (read ")
    assert obs.detail.endswith("UTC)")
    assert obs.evidence["profile_views"] == 23.0


async def test_a_zero_reach_reads_as_zero_with_the_time(monkeypatch):
    graph(monkeypatch, {"data": [
        {"name": "reach", "period": "day", "total_value": {"value": 0}}]},
        status=200)
    obs = await instagram.check_engagement("main")
    assert obs.health == "ok" and obs.value == 0.0
    assert obs.detail.startswith("reach 0/day, profile views not reported (read ")


def test_insight_value_never_invents_a_zero():
    assert instagram.insight_value({"name": "reach", "total_value": {"value": 3}}) == 3.0
    assert instagram.insight_value({"name": "reach", "values": [{"value": "9"}]}) == 9.0
    assert instagram.insight_value({"name": "reach", "values": []}) is None
    assert instagram.insight_value({"name": "reach"}) is None
    assert instagram.insight_value("junk") is None


async def test_a_missing_follower_count_is_no_count(monkeypatch):
    graph(monkeypatch, {"id": "1", "username": "miro"}, status=200)
    obs = await instagram.check_token("main")
    assert obs.health == "ok", "the token works; that is what it measures"
    assert obs.value is None and "follower count not read" in obs.detail


async def test_comments_with_no_list_are_unknown(monkeypatch):
    graph(monkeypatch, {"paging": {}}, status=200)
    obs = await instagram.check_comments_pending("main")
    assert obs.health == "unknown" and obs.value is None



async def test_a_finding_is_redacted_before_it_is_stored(monkeypatch):
    """A finding copies the observation's detail; the page and the signal
    read it from findings. Same second net as record_observation (M-2)."""
    from atlas.kernel import state
    from atlas.kernel.contracts import Health, Observation, Severity
    from atlas.kernel.detect import Finding
    monkeypatch.setenv("INSTAGRAM_TOKEN", TOKEN)
    written: list = []

    async def capture(_sql, *args):
        written.append(args)
        return {"id": 1}

    monkeypatch.setattr(state, "fetch_one", capture)
    obs = Observation("main_engagement", Health.UNKNOWN,
                      detail=f"GET /me?access_token={TOKEN}",
                      evidence={"url": f"https://g/?access_token={TOKEN}"})
    await state.record_finding(
        Finding("instagram.main_engagement", Severity.NORMAL,
                f"title {TOKEN}", obs.detail, obs, "rule"),
        "instagram.main_engagement")
    assert written and TOKEN not in str(written)
