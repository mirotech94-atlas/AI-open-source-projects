import pytest

from atlas.kernel import comms, draft_notice, state


@pytest.mark.asyncio
async def test_new_drafts_are_sent_once_and_the_mark_moves(monkeypatch):
    saved, sent = [], []
    rows = [{"id": 5, "kind": "post", "source": "x", "title": "T", "body": "B"},
            {"id": 6, "kind": "post", "source": "x", "title": "U", "body": "C"}]

    async def mark():
        return 4

    async def save(i):
        saved.append(i)

    async def fetch_all(sql, *a):
        return rows

    async def notify(msg):
        sent.append(msg.title)
        return ["slack"]

    monkeypatch.setattr(draft_notice, "_mark", mark)
    monkeypatch.setattr(draft_notice, "_save", save)
    monkeypatch.setattr(state, "fetch_all", fetch_all)
    monkeypatch.setattr(comms, "notify", notify)
    out = await draft_notice.tick()
    assert out == {"sent": 2} and saved == [5, 6]
    assert sent[0].startswith("Draft #5")


@pytest.mark.asyncio
async def test_an_undelivered_draft_is_retried_not_marked(monkeypatch):
    saved = []

    async def mark():
        return 0

    async def save(i):
        saved.append(i)

    async def fetch_all(sql, *a):
        return [{"id": 1, "kind": "k", "source": "s", "title": "t", "body": "b"}]

    async def notify(msg):
        return []

    monkeypatch.setattr(draft_notice, "_mark", mark)
    monkeypatch.setattr(draft_notice, "_save", save)
    monkeypatch.setattr(state, "fetch_all", fetch_all)
    monkeypatch.setattr(comms, "notify", notify)
    assert await draft_notice.tick() == {"sent": 0} and saved == []
