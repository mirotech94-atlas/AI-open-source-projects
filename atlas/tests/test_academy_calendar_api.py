"""GET /api/academy/calendar: what went up, per series, and what is next.

Posted rows come from `academy_tutorial_posts` with their own time; "next
up" is today's `tutorials.lineup()` order and carries no date, because no
date exists for it. Read-only, behind the session, with a read time.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from atlas.api import auth
from atlas.api.academy import router
from atlas.kernel import state
from atlas.modules.academy import tutorials

AT = datetime(2026, 10, 26, 17, 5, tzinfo=UTC)


def _app(signed_in: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    if signed_in:
        app.dependency_overrides[auth.require] = lambda: auth.Session("owner", 0.0)
    return app


async def _get(app: FastAPI) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://127.0.0.1:8080") as c:
        return await c.get("/api/academy/calendar")


def _first_two_series() -> tuple[str, str]:
    keys = [s.key for s in tutorials.SERIES if s.episodes]
    return keys[0], keys[1]


@pytest.fixture
def fake_db(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    a, b = _first_two_series()
    posts = [
        {"series": a, "episode": 1, "video_id": "AAAAAAAAAAA", "at": AT,
         "public": True},
        {"series": b, "episode": 1, "video_id": "BBBBBBBBBBB", "at": AT,
         "public": False},
    ]
    seen: dict[str, Any] = {"sql": [], "posts": posts, "a": a, "b": b}

    async def fetch_all(sql: str, *args: Any) -> list[dict[str, Any]]:
        seen["sql"].append(sql)
        if "UNION ALL" in sql:          # tutorials._done
            return [{"kind": "post", "series": p["series"], "episode": p["episode"],
                     "at": p["at"], "public": p["public"], "withdrawn": False}
                    for p in posts]
        assert "academy_tutorial_posts" in sql
        return posts

    monkeypatch.setattr(state, "fetch_all", fetch_all)
    return seen


async def test_posted_rows_grouped_per_series_with_their_time(fake_db) -> None:
    r = await _get(_app())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["read_at"] and body["as_of"]
    by_key = {s["key"]: s for s in body["series"]}
    a, b = fake_db["a"], fake_db["b"]
    assert [p["episode"] for p in by_key[a]["posted"]] == [1]
    assert by_key[a]["posted"][0]["video_id"] == "AAAAAAAAAAA"
    assert by_key[a]["posted"][0]["at"].startswith("2026-10-26T17:05")
    assert by_key[a]["posted"][0]["public"] is True
    assert by_key[b]["posted"][0]["public"] is False
    # catalog order, every series listed even with nothing posted
    assert [s["key"] for s in body["series"]] == [s.key for s in tutorials.SERIES]
    # every SQL statement is a read
    for sql in fake_db["sql"]:
        assert sql.lstrip().upper().startswith("SELECT")


async def test_next_up_follows_lineup_and_carries_no_date(fake_db) -> None:
    body = (await _get(_app())).json()
    for s in body["series"]:
        for n in s["next_up"]:
            assert set(n) == {"episode", "title"}
        posted = {p["episode"] for p in s["posted"]}
        assert not posted & {n["episode"] for n in s["next_up"]}
    for n in body["next_up"]:
        assert set(n) == {"series", "episode", "title"}


async def test_next_up_keeps_the_lineup_order(fake_db, monkeypatch) -> None:
    a, b = fake_db["a"], fake_db["b"]
    monkeypatch.setattr(tutorials, "lineup",
                        lambda *_a, **_k: ([(b, 2), (a, 2)], ["a note"]))
    body = (await _get(_app())).json()
    assert [(n["series"], n["episode"]) for n in body["next_up"]] == [(b, 2), (a, 2)]
    assert body["notes"] == ["a note"]


async def test_an_unreadable_table_is_a_503_never_an_empty_calendar(monkeypatch) -> None:
    async def broken(sql: str, *args: Any) -> list[dict[str, Any]]:
        raise ConnectionError("db down")

    monkeypatch.setattr(state, "fetch_all", broken)
    r = await _get(_app())
    assert r.status_code == 503
    assert "calendar" in r.json()["detail"]


async def test_refused_without_a_session(fake_db) -> None:
    r = await _get(_app(signed_in=False))
    assert r.status_code == 401
