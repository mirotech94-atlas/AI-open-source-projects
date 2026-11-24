"""kernel/tasks.py: every request Miro makes is on the record, and a status
question is answered from it (Miro, 2026-10-06: "just check on him")."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from atlas.kernel import tasks


@pytest.mark.parametrize("text", [
    "what's the status of the vault export", "any update on the clips?",
    "what did you do with my request", "what are you working on",
    "where are we with the academy reel", "did you finish the bundle copy",
])
def test_status_questions_are_recognised(text):
    assert tasks.is_status_question(text)


@pytest.mark.parametrize("text", [
    "post the reel to clips", "how are you", "what is the capital of Peru",
    "restart the fare poller",
])
def test_plain_requests_are_not_status_questions(text):
    assert not tasks.is_status_question(text)


async def test_open_and_close_write_one_row(monkeypatch):
    written = []

    async def fetch_one(sql, *args):
        written.append(("insert", args))
        return {"id": 7}

    async def execute_sql(sql, *args):
        written.append(("update", args))
    monkeypatch.setattr(tasks, "fetch_one", fetch_one)
    monkeypatch.setattr(tasks, "execute_sql", execute_sql)
    tid = await tasks.open("  post   the reel ", source="slack", who="U1", thread="1.2")
    assert tid == 7 and written[0] == ("insert", ("slack", "U1", "1.2", "post the reel"))
    await tasks.close(tid, route="action", ok=True, outcome="posted\nnow", cards=(3,))
    assert written[1] == ("update", (7, "action", "done", "posted now", "[3]"))
    await tasks.close(tid, route="cards", ok=None, outcome="raised", cards=(9,))
    assert written[2][1][2] == "held"
    await tasks.close(tid, route="action", ok=False, outcome="no")
    assert written[3][1][2] == "failed"


async def test_open_never_raises_and_close_skips_none(monkeypatch):
    async def boom(sql, *args):
        raise RuntimeError("db down")
    monkeypatch.setattr(tasks, "fetch_one", boom)
    assert await tasks.open("x", source="phone") is None
    calls = []

    async def execute_sql(sql, *args):
        calls.append(args)
    monkeypatch.setattr(tasks, "execute_sql", execute_sql)
    await tasks.close(None, route="answer", ok=None, outcome="")
    assert calls == []


def _rows(now):
    return [
        {"id": 2, "at": now - timedelta(minutes=5), "source": "phone", "text": "post the academy reel",
         "route": "cards", "status": "held", "outcome": "raised a card", "cards": "[12]", "done_at": now},
        {"id": 1, "at": now - timedelta(hours=3), "source": "slack", "text": "restart the fare poller",
         "route": "action", "status": "done", "outcome": "restarted", "cards": "[]", "done_at": now},
    ]


async def test_status_text_lists_matches_first_and_counts_open(monkeypatch):
    now = datetime.now(UTC)

    async def fetch_all(sql, *args):
        return _rows(now)
    monkeypatch.setattr(tasks, "fetch_all", fetch_all)
    text = await tasks.status_text("any update on the academy reel")
    assert text.startswith("1 still open or waiting on you, 0 settled, of the last 1:")
    assert "#2 5 min ago from phone: held via cards: post the academy reel — raised a card (card #12)" in text
    text = await tasks.status_text("what are you working on")
    assert "#2 " in text and "#1 3 h ago from slack: done via action: restart the fare poller — restarted" in text


async def test_context_text_quotes_his_words_as_untrusted(monkeypatch):
    now = datetime.now(UTC)

    async def fetch_all(sql, *args):
        return _rows(now)
    monkeypatch.setattr(tasks, "fetch_all", fetch_all)
    text = await tasks.context_text()
    assert text.startswith("TRACKED REQUESTS") and "post the academy reel" in text
    from atlas.kernel.permissions import wrap_untrusted
    assert wrap_untrusted("post the academy reel", "tracked request") in text


async def test_context_text_is_honest_when_unreadable(monkeypatch):
    async def boom(sql, *args):
        raise RuntimeError("db")
    monkeypatch.setattr(tasks, "fetch_all", boom)
    assert await tasks.context_text() == "TRACKED REQUESTS: none recorded yet."


def test_cards_json_round_trips():
    now = datetime.now(UTC)
    r = {**_rows(now)[0], "cards": json.loads("[12, 13]")}
    assert "(cards #12, #13)" in tasks.line(r, now, quote=False)


# ---- a PRIVATE run's words and reply are never kept where a cloud prompt reads

async def test_a_private_run_stores_a_placeholder_not_the_question_or_reply(monkeypatch):
    written = []

    async def fetch_one(sql, *args):
        return {"id": 5}

    async def execute_sql(sql, *args):
        written.append((sql, args))
    monkeypatch.setattr(tasks, "fetch_one", fetch_one)
    monkeypatch.setattr(tasks, "execute_sql", execute_sql)
    tid = await tasks.open("summarize my unread email", source="slack", who="U1")
    await tasks.note_private()                # what the assistant loop does
    await tasks.close(tid, route="answer", ok=None,
                      outcome="Your landlord wrote: the rent is 4200", cards=())
    assert "private = true" in written[0][0] and "unread" not in str(written[0][1])
    sql, args = written[-1]
    flat = " ".join(str(a) for a in args)
    assert "landlord" not in flat and "4200" not in flat and "unread email" not in flat
    assert "private" in sql.lower() and tasks.PRIVATE_OUTCOME in args


async def test_note_private_reaches_a_copied_context(monkeypatch):
    """ask.answer may run converse in a child task; the mark must still land."""
    import asyncio
    written = []

    async def fetch_one(sql, *args):
        return {"id": 6}

    async def execute_sql(sql, *args):
        written.append(args)
    monkeypatch.setattr(tasks, "fetch_one", fetch_one)
    monkeypatch.setattr(tasks, "execute_sql", execute_sql)
    tid = await tasks.open("what is on my calendar", source="slack")
    await asyncio.create_task(tasks.note_private())
    await tasks.close(tid, route="answer", ok=None, outcome="dentist at 3pm")
    assert "dentist" not in " ".join(str(a) for a in written[-1])


async def test_a_plain_run_is_stored_as_before(monkeypatch):
    written = []

    async def fetch_one(sql, *args):
        return {"id": 8}

    async def execute_sql(sql, *args):
        written.append(args)
    monkeypatch.setattr(tasks, "fetch_one", fetch_one)
    monkeypatch.setattr(tasks, "execute_sql", execute_sql)
    tid = await tasks.open("restart the poller", source="slack")
    await tasks.close(tid, route="action", ok=True, outcome="restarted")
    assert "restarted" in written[-1]


async def test_private_rows_are_excluded_from_context_and_status(monkeypatch):
    now = datetime.now(UTC)
    seen = []

    async def fetch_all(sql, *args):
        seen.append(sql)
        rows = [{**_rows(now)[0], "private": True, "outcome": "SECRET-MAIL"},
                {**_rows(now)[1], "private": False}]
        return [r for r in rows if "NOT private" not in sql or not r["private"]]
    monkeypatch.setattr(tasks, "fetch_all", fetch_all)
    assert "SECRET-MAIL" not in await tasks.context_text()
    assert "SECRET-MAIL" not in await tasks.status_text("anything")
    assert all("NOT private" in s for s in seen)
