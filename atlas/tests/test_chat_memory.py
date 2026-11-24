"""Per-channel chat memory (Miro 2026-11-19): separate windows, private taint,
wrap source = channel, additive schema, hand-run rollback."""
from __future__ import annotations

import pathlib

import pytest

from atlas.kernel import chat_memory

ROOT = pathlib.Path(__file__).resolve().parents[1]


class Rows:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.next_id = 1
        self.down = False

    async def execute_sql(self, sql, *args):
        if self.down:
            raise RuntimeError("db down")
        if sql.startswith("INSERT"):
            self.rows.append({"id": self.next_id, "role": args[0], "body": args[1],
                              "channel": args[2], "private": args[3], "age_h": 0})
            self.next_id += 1
        elif sql.startswith("DELETE"):
            ttl, keep, channel = args
            mine = sorted((r for r in self.rows if r["channel"] == channel),
                          key=lambda r: -r["id"])[:keep]
            ids = {r["id"] for r in mine}
            self.rows = [r for r in self.rows if r["channel"] != channel
                         or (r["id"] in ids and r["age_h"] < ttl)]

    async def fetch_all(self, sql, *args):
        if self.down:
            raise RuntimeError("db down")
        channel, ttl, limit = args
        live = [r for r in self.rows if r["channel"] == channel and r["age_h"] < ttl]
        return sorted(live, key=lambda r: -r["id"])[:limit]


@pytest.fixture
def table(monkeypatch):
    t = Rows()
    monkeypatch.setattr(chat_memory, "execute_sql", t.execute_sql)
    monkeypatch.setattr(chat_memory, "fetch_all", t.fetch_all)
    return t


async def test_default_channel_is_whatsapp(table):
    await chat_memory.remember("hi", "hey")
    assert {r["channel"] for r in table.rows} == {"whatsapp"}


async def test_channels_do_not_evict_each_other(table):
    for i in range(9):
        await chat_memory.remember(f"s{i}", f"a{i}", channel="slack")
    await chat_memory.remember("wa", "wa reply")
    assert sum(r["channel"] == "slack" for r in table.rows) == chat_memory.KEEP
    assert sum(r["channel"] == "whatsapp" for r in table.rows) == 2


async def test_recent_is_never_replayed_across_channels(table):
    await chat_memory.remember("slack secret", "ok", channel="slack")
    await chat_memory.remember("dash q", "dash a", channel="dashboard")
    assert await chat_memory.recent() == []
    msgs = await chat_memory.recent(channel="dashboard")
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert "slack secret" not in " ".join(m.content for m in msgs)


async def test_wrap_source_is_the_channel(table):
    await chat_memory.remember("hello", "hi", channel="slack")
    msgs = await chat_memory.recent(channel="slack")
    assert "slack" in msgs[0].content
    assert "whatsapp" not in msgs[0].content


async def test_taint_follows_private_rows(table):
    await chat_memory.remember("q1", "a1", channel="dashboard")
    _, tainted = await chat_memory.recent_with_taint(channel="dashboard")
    assert tainted is False
    await chat_memory.remember("my mail?", "two unread", channel="dashboard", private=True)
    _, tainted = await chat_memory.recent_with_taint(channel="dashboard")
    assert tainted is True
    _, other = await chat_memory.recent_with_taint(channel="slack")
    assert other is False


async def test_taint_expires_with_the_window(table):
    await chat_memory.remember("my mail?", "x", channel="slack", private=True)
    for r in table.rows:
        r["age_h"] = 30
    assert await chat_memory.recent_with_taint(channel="slack") == ([], False)


async def test_last_user_text_is_miros_own_words(table):
    await chat_memory.remember("restart the bridge", "ok", channel="slack")
    await chat_memory.remember("something else", "fine", channel="whatsapp")
    assert await chat_memory.last_user_text("slack") == "restart the bridge"
    assert await chat_memory.last_user_text("dashboard") == ""


async def test_unreadable_table_is_empty_and_untainted(table):
    table.down = True
    await chat_memory.remember("x", "y", channel="slack")        # does not raise
    assert await chat_memory.recent_with_taint(channel="slack") == ([], False)
    assert await chat_memory.last_user_text("slack") == ""


def test_schema_is_additive_and_idempotent():
    sql = (ROOT / "db/schema.sql").read_text()
    assert "ADD COLUMN IF NOT EXISTS channel TEXT NOT NULL DEFAULT 'whatsapp'" in sql
    assert "ADD COLUMN IF NOT EXISTS private BOOLEAN NOT NULL DEFAULT false" in sql
    assert "CREATE INDEX IF NOT EXISTS whatsapp_chat_memory_channel_at" in sql
    for line in sql.splitlines():
        if "whatsapp_chat_memory" in line and line.upper().startswith("ALTER"):
            assert "IF NOT EXISTS" in line


def test_rollback_sql_exists_and_is_by_hand():
    sql = (ROOT / "db/rollback-assistant-memory.sql").read_text()
    assert "Run by hand" in sql
    assert "DROP COLUMN IF EXISTS channel" in sql
    assert "DROP COLUMN IF EXISTS private" in sql


async def test_plain_recent_never_returns_private_rows(table):
    await chat_memory.remember("q1", "a1", channel="dashboard")
    await chat_memory.remember("my mail?", "two unread", channel="dashboard",
                               private=True)
    plain = await chat_memory.recent(channel="dashboard")
    text = " ".join(m.content for m in plain)
    assert "a1" in text and "two unread" not in text and "my mail" not in text


async def test_a_private_dm_row_is_not_replayed_into_another_slack_conversation(table):
    await chat_memory.remember("my mail?", "two unread", channel="slack:D111",
                               private=True)
    msgs, tainted = await chat_memory.recent_with_taint(channel="slack:C222")
    assert msgs == [] and tainted is False
    msgs, tainted = await chat_memory.recent_with_taint(channel="slack:D111")
    assert tainted is True and msgs


async def test_a_conversation_key_is_not_truncated_to_the_platform(table):
    await chat_memory.remember("hi", "hey", channel="slack:C0123456789:1700000000.1")
    assert {r["channel"] for r in table.rows} == {"slack:c0123456789:1700000000.1"}
