"""Task 64: the daily refill rotates image / video / poll, drafts stay
short, polls carry 2-4 options with a public 1-week duration, and media
generation runs outside the sweep's browser lock and time budget.
"""
from __future__ import annotations

import json

import pytest

from atlas.modules import linkedin
from atlas.modules.linkedin import media, store, sweep


@pytest.fixture(autouse=True)
def _flycheap_as_fixture_page(monkeypatch):
    """These tests drive the page mechanics with FlyCheap as their example
    page. FlyCheap is retired by default (Miro, 2026-10-09); that default is
    pinned in test_linkedin_retired.py, not here."""
    from atlas.modules import linkedin as _li
    monkeypatch.setitem(_li.PAGES, "guardian", _li.KNOWN_PAGES["guardian"])
    monkeypatch.setitem(_li.PAGES, "flycheap", _li.KNOWN_PAGES["flycheap"])


BATCH = """KIND: image
TEXT: Stop overpaying for flights.
We search 700+ airlines so you don't have to.
https://flycheap.example.com #travel #deals
IMAGE_PROMPT: a boarding pass dissolving into a route line
---
KIND: video
TEXT: Watch fares drop in real time.
FlyCheap finds the gap fares.
https://flycheap.example.com #flights
VIDEO_PROMPT: a route line animating across a map
---
KIND: poll
INTRO: Quick one for the frequent flyers.
QUESTION: What matters most when you book?
OPTIONS: Price | Flexibility | Direct routes | Loyalty points
"""


@pytest.fixture
def state(tmp_path, monkeypatch):
    def write(doc: dict) -> None:
        path = tmp_path / "state.json"
        path.write_text(json.dumps(doc))
        monkeypatch.setattr(linkedin, "STATE", path)
        monkeypatch.setattr(store, "RUNS", tmp_path / "runs")
    return write


def _doc(*entries: dict) -> dict:
    holder = {"post_bank": list(entries),
              "posting": {"enabled": True, "max_per_day": 1}}
    return {"last_run": "2026-09-27T00:00:00+00:00", "runs": 1, "improver": holder}


# ------------------------------------------------------------- draft_posts

async def test_rotation_covers_all_three_kinds(monkeypatch):
    class C:
        text = BATCH

    async def fake_think(*a, **k):
        return C()

    monkeypatch.setattr("atlas.kernel.llm.think", fake_think)
    items = await sweep.draft_posts(_doc(), "flycheap", 3)
    assert [i["kind"] for i in items] == ["image", "video", "poll"]


async def test_the_vault_name_appears_in_the_drafting_prompt(monkeypatch):
    seen = {}

    async def fake_think(need, system, messages, **k):
        seen["system"] = system

        class C:
            text = BATCH
        return C()

    monkeypatch.setattr("atlas.kernel.llm.think", fake_think)
    await sweep.draft_posts(_doc(), "guardian", 3)
    assert "Vault" in seen["system"]
    assert "Guardian" not in seen["system"]


async def test_image_and_video_text_is_short(monkeypatch):
    class C:
        text = BATCH

    async def fake_think(*a, **k):
        return C()

    monkeypatch.setattr("atlas.kernel.llm.think", fake_think)
    items = await sweep.draft_posts(_doc(), "flycheap", 3)
    for item in items:
        if item["kind"] in ("image", "video"):
            assert len(item["text"]) < 280, item["text"]


async def test_a_poll_carries_two_to_four_options_public_one_week(monkeypatch):
    class C:
        text = BATCH

    async def fake_think(*a, **k):
        return C()

    monkeypatch.setattr("atlas.kernel.llm.think", fake_think)
    items = await sweep.draft_posts(_doc(), "flycheap", 3)
    poll = next(i for i in items if i["kind"] == "poll")
    assert 2 <= len(poll["poll"]["options"]) <= 4
    assert poll["poll"]["duration"] == "1w"
    assert poll["poll"]["visibility"] == "public"
    assert poll["poll"]["question"]


async def test_a_poll_with_only_one_option_is_dropped(monkeypatch):
    bad = BATCH.replace("OPTIONS: Price | Flexibility | Direct routes | Loyalty points",
                        "OPTIONS: Price")

    class C:
        text = bad

    async def fake_think(*a, **k):
        return C()

    monkeypatch.setattr("atlas.kernel.llm.think", fake_think)
    items = await sweep.draft_posts(_doc(), "flycheap", 3)
    assert all(i["kind"] != "poll" for i in items)


async def test_an_image_block_with_a_banned_link_is_dropped(monkeypatch):
    bad = BATCH.replace("https://flycheap.example.com #travel #deals",
                        "https://flycheap.storefront.test #travel #deals")

    class C:
        text = bad

    async def fake_think(*a, **k):
        return C()

    monkeypatch.setattr("atlas.kernel.llm.think", fake_think)
    items = await sweep.draft_posts(_doc(), "flycheap", 3)
    assert all(i["kind"] != "image" for i in items)
    assert any(i["kind"] == "video" for i in items)


async def test_an_image_or_video_block_missing_the_link_is_dropped(monkeypatch):
    bad = BATCH.replace(
        "We search 700+ airlines so you don't have to.\nhttps://flycheap.example.com "
        "#travel #deals",
        "We search 700+ airlines so you don't have to. #travel #deals")

    class C:
        text = bad

    async def fake_think(*a, **k):
        return C()

    monkeypatch.setattr("atlas.kernel.llm.think", fake_think)
    items = await sweep.draft_posts(_doc(), "flycheap", 3)
    assert all(i["kind"] != "image" for i in items)


# ------------------------------------------------------------------ refill

async def test_refill_marks_image_and_video_as_needs_media(monkeypatch):
    class C:
        text = BATCH

    async def fake_think(*a, **k):
        return C()

    monkeypatch.setattr("atlas.kernel.llm.think", fake_think)

    async def fake_pending(key):
        return set()

    monkeypatch.setattr(sweep, "pending_draft_ids", fake_pending)
    s = sweep.Sweep(_doc())
    await s.refill("flycheap")
    entries, _, _ = store.bank(s.state, "flycheap")
    kinds = {e["kind"]: e["status"] for e in entries}
    assert kinds["image"] == "needs_media"
    assert kinds["video"] == "needs_media"
    assert kinds["poll"] == "draft"


async def test_refill_never_keeps_a_banned_link(monkeypatch):
    bad = BATCH.replace("https://flycheap.example.com #travel #deals",
                        "https://flycheap.storefront.test #travel #deals")

    class C:
        text = bad

    async def fake_think(*a, **k):
        return C()

    monkeypatch.setattr("atlas.kernel.llm.think", fake_think)
    items = await sweep.draft_posts(_doc(), "flycheap", 3)
    assert all("storefront" not in json.dumps(i) for i in items)


async def test_refill_keeps_the_bank_at_least_three_deep(monkeypatch):
    """Task 64: at least 3 approved-or-ready posts per page at all times."""
    class C:
        text = BATCH

    async def fake_think(*a, **k):
        return C()

    monkeypatch.setattr("atlas.kernel.llm.think", fake_think)

    async def fake_pending(key):
        return set()

    monkeypatch.setattr(sweep, "pending_draft_ids", fake_pending)
    s = sweep.Sweep(_doc())
    await s.refill("flycheap")
    entries, _, _ = store.bank(s.state, "flycheap")
    live = [e for e in entries if e.get("status") in ("approved", "draft", "needs_media")]
    assert len(live) >= 3


# ------------------------------------------------------------ fill_media

async def test_fill_media_attaches_the_image_and_flips_the_entry_to_draft(
        state, monkeypatch, tmp_path):
    entry = {"id": "B1", "status": "needs_media", "kind": "image",
             "text": "hook https://flycheap.example.com #x", "media_prompt": "a prompt",
             "created": store.today(), "drafted_by": "atlas", "card_rev": 1,
             "frozen": False, "media": None}
    state(_doc(entry))

    async def fake_make(kind, page, prompt):
        assert kind == "image"
        p = tmp_path / "out.png"
        p.write_bytes(b"x")
        return str(p), "made it"

    monkeypatch.setattr(media, "make", fake_make)
    await sweep.fill_media_for("flycheap")
    got = store.load()["improver"]["post_bank"][0]
    assert got["status"] == "draft"
    assert got["media"] == str(tmp_path / "out.png")


async def test_fill_media_leaves_the_entry_pending_on_a_refusal(state, monkeypatch):
    entry = {"id": "B1", "status": "needs_media", "kind": "video",
             "text": "hook https://flycheap.example.com #x", "media_prompt": "a prompt",
             "created": store.today(), "drafted_by": "atlas", "card_rev": 1,
             "frozen": False, "media": None}
    state(_doc(entry))

    async def fake_make(kind, page, prompt):
        return None, "ffmpeg not installed"

    monkeypatch.setattr(media, "make", fake_make)
    await sweep.fill_media_for("flycheap")
    got = store.load()["improver"]["post_bank"][0]
    assert got["status"] == "needs_media"
    assert "ffmpeg not installed" in got["media_note"]


async def test_fill_media_never_touches_a_poll(state, monkeypatch):
    entry = {"id": "B1", "status": "draft", "kind": "poll",
             "poll": {"question": "q", "options": ["a", "b"], "intro": "i",
                      "duration": "1w", "visibility": "public"},
             "text": "i", "created": store.today(), "drafted_by": "atlas",
             "card_rev": 1, "frozen": False, "media": None}
    state(_doc(entry))

    async def boom(kind, page, prompt):
        raise AssertionError("a poll needs no media")

    monkeypatch.setattr(media, "make", boom)
    await sweep.fill_media_for("flycheap")   # must not raise
    assert store.load()["improver"]["post_bank"][0]["status"] == "draft"


async def test_signals_offer_no_card_for_needs_media(state):
    entry = {"id": "B1", "status": "needs_media", "kind": "image",
             "text": "hook https://flycheap.example.com #x", "media_prompt": "p",
             "created": store.today(), "drafted_by": "atlas", "card_rev": 1,
             "frozen": False, "media": None}
    state(_doc(entry))
    sig = [s for s in await linkedin.MODULE.signals()
           if s.params.get("entry_id") == "B1"]
    assert sig == []


# ------------------------------------------------ cards: kind, media, poll

async def test_a_draft_card_carries_the_media_as_an_attachment(state, tmp_path):
    pic = tmp_path / "pic.png"
    pic.write_bytes(b"x")
    entry = {"id": "B1", "status": "draft", "kind": "image",
             "text": "hook https://flycheap.example.com #x", "media": str(pic),
             "created": store.today(), "drafted_by": "atlas", "card_rev": 1,
             "frozen": False}
    state(_doc(entry))
    sig, = [s for s in await linkedin.MODULE.signals()
           if s.params.get("entry_id") == "B1"]
    assert sig.attachment == str(pic)
    assert "[image" in sig.body


async def test_a_poll_draft_card_has_no_attachment_and_shows_the_options(state):
    entry = {"id": "B1", "status": "draft", "kind": "poll",
             "poll": {"intro": "Quick one", "question": "What matters?",
                      "options": ["Price", "Speed"], "duration": "1w",
                      "visibility": "public"},
             "text": "Quick one", "created": store.today(),
             "drafted_by": "atlas", "card_rev": 1, "frozen": False, "media": None}
    state(_doc(entry))
    sig, = [s for s in await linkedin.MODULE.signals()
           if s.params.get("entry_id") == "B1"]
    assert sig.attachment is None
    assert "What matters?" in sig.body
    assert "Price" in sig.body and "Speed" in sig.body
    assert "1w" in sig.body and "public" in sig.body


async def test_the_daily_publish_card_carries_the_media_too(state, tmp_path):
    pic = tmp_path / "pic.png"
    pic.write_bytes(b"x")
    entry = {"id": "B1", "status": "approved", "kind": "image",
             "text": "hook https://flycheap.example.com #x", "media": str(pic)}
    state(_doc(entry))
    sig, = [s for s in await linkedin.MODULE.signals()
           if s.suggests == "linkedin.publish_post"]
    assert sig.attachment == str(pic)


async def test_the_approval_hash_covers_the_poll_not_just_its_intro(state):
    """Task 64: the approval must fail if the sweep rewrote the poll's
    options after the card went out, even though the visible `text` (the
    intro) never changed."""
    from atlas.modules.linkedin import publish

    entry = {"id": "B1", "status": "draft", "kind": "poll",
             "poll": {"intro": "Quick one", "question": "What matters?",
                      "options": ["Price", "Speed"], "duration": "1w",
                      "visibility": "public"},
             "text": "Quick one", "created": store.today(),
             "drafted_by": "atlas", "card_rev": 1, "frozen": False, "media": None}
    state(_doc(entry))
    stale_hash = publish.payload_sha256(entry)

    st = store.load()
    st["improver"]["post_bank"][0]["poll"]["options"] = ["Price", "Loyalty"]
    store.save(st)

    r = await publish.approve_draft("flycheap", "B1", text_sha256=stale_hash)
    assert not r.ok
    assert "changed since you saw it" in r.detail


# --------------------------------------------------------------- media_fill

async def test_media_fill_check_generates_pending_media(state, monkeypatch):
    entry = {"id": "B1", "status": "needs_media", "kind": "image",
             "text": "hook https://flycheap.example.com #x", "media_prompt": "p",
             "created": store.today(), "drafted_by": "atlas", "card_rev": 1,
             "frozen": False, "media": None}
    state(_doc(entry))

    async def fake_fill(key):
        if key != "flycheap":
            return {"attempted": 0, "made": 0}
        st = store.load()
        st["improver"]["post_bank"][0]["status"] = "draft"
        st["improver"]["post_bank"][0]["media"] = "/tmp/x.png"
        store.save(st)
        return {"attempted": 1, "made": 1}

    monkeypatch.setattr(sweep, "fill_media_for", fake_fill)
    obs = await linkedin.media_fill_check()
    assert obs.health.value == "ok"
    assert "1 of 1" in obs.detail


async def test_media_fill_check_is_ok_with_nothing_pending(state):
    state(_doc())
    obs = await linkedin.media_fill_check()
    assert obs.health.value == "ok"
    assert "no draft is waiting" in obs.detail


async def test_media_fill_check_degrades_when_still_pending(state, monkeypatch):
    entry = {"id": "B1", "status": "needs_media", "kind": "video",
             "text": "hook https://flycheap.example.com #x", "media_prompt": "p",
             "created": store.today(), "drafted_by": "atlas", "card_rev": 1,
             "frozen": False, "media": None, "media_note": "GEMINI_API_KEY not set"}
    state(_doc(entry))

    async def fake_fill(key):
        return {"attempted": 1, "made": 0}

    monkeypatch.setattr(sweep, "fill_media_for", fake_fill)
    obs = await linkedin.media_fill_check()
    assert obs.health.value == "degraded"
    assert "still waiting" in obs.detail
    assert "GEMINI_API_KEY not set" in obs.detail


async def test_media_fill_check_is_unknown_without_a_state_file(tmp_path, monkeypatch):
    monkeypatch.setattr(linkedin, "STATE", tmp_path / "absent.json")
    obs = await linkedin.media_fill_check()
    assert obs.health.value == "unknown"


# ------------------------------------------------------------ academy page

ACADEMY_BATCH = """KIND: image
TEXT: Your AI agent should never hold your bank login.
One lesson from Build a Safe AI Operator.
https://agency.storefront.test #AI #SmallBusiness
IMAGE_PROMPT: a closed menu of named actions, abstract, warm light
---
KIND: video
TEXT: Make $5,000 a month with this course.
https://agency.storefront.test #AI
VIDEO_PROMPT: coins falling
---
KIND: poll
INTRO: A quick one for owners who use AI.
QUESTION: What does your AI tool get to touch?
OPTIONS: Nothing yet | Drafts only | Email | Money
"""


def test_the_academy_page_exists_only_with_its_id(monkeypatch):
    import importlib

    monkeypatch.delenv(linkedin.ACADEMY_PAGE_ENV, raising=False)
    assert linkedin._academy_page() is None
    monkeypatch.setenv(linkedin.ACADEMY_PAGE_ENV, "not-a-number")
    assert linkedin._academy_page() is None
    monkeypatch.setenv(linkedin.ACADEMY_PAGE_ENV, "123456789")
    spec = linkedin._academy_page()
    assert spec["id"] == "123456789" and spec["link"] == "https://agency.storefront.test/academy/"
    assert spec["name"] == "ATLAS Academy" and spec["bank_key"] == "academy_page"
    assert importlib  # the module-level join runs at boot; the function is what is pinned


async def test_academy_drafts_teach_one_thing_and_never_claim_income(monkeypatch):
    """Miro, 2026-10-06: advertise the course page on LinkedIn. The lane
    sells safety, never money: a block with an income claim is dropped
    before it is a draft (the reels' own rule)."""
    academy = {
        "page_key": "academy_page", "bank_key": "academy_page",
        "followers_field": "followers", "id": "123456789",
        "name": "ATLAS Academy", "link": "https://agency.storefront.test"}
    monkeypatch.setitem(linkedin.PAGES, "academy", academy)
    monkeypatch.setitem(linkedin.KNOWN_PAGES, "academy", academy)
    seen = {}

    async def fake_think(need, system, messages, **k):
        seen["system"] = system

        class C:
            text = ACADEMY_BATCH
        return C()

    monkeypatch.setattr("atlas.kernel.llm.think", fake_think)
    items = await sweep.draft_posts(_doc(), "academy", 3)
    assert "ATLAS Academy" in seen["system"] and "agency.storefront.test" in seen["system"]
    assert "Never an income or earnings claim" in seen["system"]
    assert [i["kind"] for i in items] == ["image", "poll"], "the $5,000 video is dropped"


MARKDOWN_BATCH = """**Hook: Unlock AI safety in minutes**
Learn the core check-list for a Safe AI Operator.
https://agency.storefront.test
#AItraining #SafetyFirst

*IMAGE_PROMPT: Abstract geometric network of glowing nodes forming a shield.*

---

**Hook: Boost sales with AI fast**
See a 5-step AI-powered pitch in action.
https://agency.storefront.test
#SellWithAI #Growth

*VIDEO_PROMPT: Animated slide sequence of sleek icons, no people.*

---

**Quick poll: Which AI skill will you master next?**
What should our next micro-lesson cover?
Options: Build a Safe AI Operator | Sell With AI | AI for Every Age

---"""


def test_a_markdown_batch_from_a_free_model_still_parses():
    """2026-10-06: the free bench answered the drafting prompt in markdown
    ('**Hook: ...**', '*IMAGE_PROMPT: ...*', 'Options: a | b') and every
    block was dropped. The kind is read off the prompt line or the options
    line; the words are the model's own, nothing is invented."""
    spec = {"name": "ATLAS Academy", "link": "https://agency.storefront.test"}
    items = sweep._parse_batch(MARKDOWN_BATCH, spec)
    assert [i["kind"] for i in items] == ["image", "video", "poll"]
    assert items[0]["text"].startswith("Unlock AI safety in minutes\nLearn the core")
    assert "https://agency.storefront.test" in items[0]["text"]
    assert items[0]["media_prompt"].startswith("Abstract geometric network")
    assert items[2]["poll"]["question"] == "What should our next micro-lesson cover?"
    assert items[2]["poll"]["options"] == ["Build a Safe AI Operator", "Sell With AI",
                                           "AI for Every Age"]
    assert sweep._parse_batch("just prose, no prompt, no options", spec) == []
