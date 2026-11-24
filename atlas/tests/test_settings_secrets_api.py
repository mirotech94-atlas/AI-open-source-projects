"""GET /api/settings/secrets: a fixed allowlist of names, each set or not.

Never a value, never a name outside the list, never without a session.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from atlas.api import auth
from atlas.api.dashboard import SECRET_NAMES, router

VALUE = "xoxb-THIS-MUST-NEVER-APPEAR-1234567890"


def _app(signed_in: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    if signed_in:
        app.dependency_overrides[auth.require] = lambda: auth.Session("owner", 0.0)
    return app


async def _get(app: FastAPI) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://127.0.0.1:8080") as c:
        return await c.get("/api/settings/secrets")


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in SECRET_NAMES:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_the_allowlist_is_fixed_and_names_the_plan_secrets() -> None:
    for name in ("SLACK_APP_TOKEN", "SLACK_BOT_TOKEN", "SESSION_SECRET",
                 "DATABASE_URL", "POSTGRES_PASSWORD", "GOOGLE_REFRESH_TOKEN",
                 "YOUTUBE_CLIPS_REFRESH_TOKEN", "OPENROUTER_API_KEY",
                 "ACADEMY_STRIPE_READ_KEY", "MAC_AGENT_TOKEN", "HANDOFF_TOKEN",
                 "VERCEL_TOKEN", "VERCEL_TEAM_ID", "RESEND_API_KEY", "GMAIL_APP_PASSWORD"):
        assert name in SECRET_NAMES
    assert isinstance(SECRET_NAMES, tuple)
    assert len(set(SECRET_NAMES)) == len(SECRET_NAMES)


async def test_answers_booleans_only_and_never_a_value(clean_env) -> None:
    clean_env.setenv("SLACK_BOT_TOKEN", VALUE)
    clean_env.setenv("SLACK_APP_TOKEN", "")          # empty counts as unset
    clean_env.setenv("SOME_OTHER_SECRET", VALUE)    # outside the list
    r = await _get(_app())
    assert r.status_code == 200
    body = r.json()
    assert "as_of" in body
    secrets = body["secrets"]
    assert list(secrets) == list(SECRET_NAMES)
    assert all(isinstance(v, bool) for v in secrets.values())
    assert secrets["SLACK_BOT_TOKEN"] is True
    assert secrets["SLACK_APP_TOKEN"] is False
    assert "SOME_OTHER_SECRET" not in secrets
    assert VALUE not in r.text
    assert "xoxb" not in r.text


async def test_refused_without_a_session(clean_env) -> None:
    clean_env.setenv("SLACK_BOT_TOKEN", VALUE)
    r = await _get(_app(signed_in=False))
    assert r.status_code == 401
    assert VALUE not in r.text
