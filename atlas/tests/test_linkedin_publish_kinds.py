"""Task 64: publishing an image, a video or a poll drives the right
composer steps, a failed step is named rather than swallowed, and a post
that cannot be confirmed on the page's published list is never claimed as
a success.

The composer selectors here are ASSUMED (never exercised against a live
LinkedIn session, per the brief) -- these tests fix what `publish.py`
itself asks of a Playwright page, not what LinkedIn's real DOM looks like.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from atlas.modules import linkedin
from atlas.modules.linkedin import publish, store


@pytest.fixture
def state(tmp_path, monkeypatch):
    def write(doc: dict) -> Path:
        path = tmp_path / "linkedin_state.json"
        path.write_text(json.dumps(doc))
        monkeypatch.setattr(linkedin, "STATE", path)
        monkeypatch.setattr(store, "RUNS", tmp_path / "runs")
        return path
    return write


def doc(entry: dict, *, page: str = "flycheap") -> dict:
    holder = {"post_bank": [entry],
              "posting": {"enabled": True, "max_per_day": 1,
                          "reshare_to_profile": False}}
    key = linkedin.PAGES[page]["bank_key"]
    return {"last_run": "2026-09-27T00:00:00+00:00", "runs": 1, key: holder}


IMAGE_ENTRY = {"id": "B1", "status": "approved", "kind": "image",
              "text": "Stop overpaying for flights.\nhttps://flycheap.live #travel",
              "media": "/tmp/does-not-need-to-exist.png"}
VIDEO_ENTRY = {"id": "B2", "status": "approved", "kind": "video",
              "text": "Watch fares drop.\nhttps://flycheap.live #travel",
              "media": "/tmp/does-not-need-to-exist.mp4"}
POLL_ENTRY = {"id": "B3", "status": "approved", "kind": "poll",
             "text": "Quick one for you.",
             "poll": {"intro": "Quick one for you.", "question": "What matters most?",
                      "options": ["Price", "Flexibility", "Direct routes"],
                      "duration": "1w", "visibility": "public"}}


class _Locator:
    def __init__(self, tab: _Tab, selector: str) -> None:
        self.tab = tab
        self.selector = selector

    @property
    def first(self) -> _Locator:
        return self

    def _maybe_fail(self, action: str) -> None:
        if self.selector in self.tab.fail_at:
            raise RuntimeError(f"{action} failed on {self.selector!r}")

    async def wait_for(self, *, state: str = "", timeout: int = 0) -> None:
        self._maybe_fail("wait_for")

    async def click(self) -> None:
        self.tab.clicked.append(self.selector)
        self._maybe_fail("click")

    async def is_enabled(self) -> bool:
        # A real composer enables Post once the text is in; _click_post
        # waits for exactly this before clicking.
        return True

    async def set_input_files(self, path: str) -> None:
        self.tab.uploaded.append((self.selector, path))
        self._maybe_fail("set_input_files")


class _Keyboard:
    def __init__(self, tab: _Tab) -> None:
        self.tab = tab

    async def type(self, text: str, delay: int | None = None) -> None:
        self.tab.typed.append(text)


class _Tab:
    def __init__(self, *, readback: str, published_body: str = "",
                fail_at: tuple[str, ...] = ()) -> None:
        self._readback = readback
        self.published_body = published_body
        self.fail_at = set(fail_at)
        self.keyboard = _Keyboard(self)
        self.locator_calls: list[str] = []
        self.clicked: list[str] = []
        self.uploaded: list[tuple[str, str]] = []
        self.typed: list[str] = []

    def locator(self, selector: str) -> _Locator:
        self.locator_calls.append(selector)
        return _Locator(self, selector)

    async def evaluate(self, script: str) -> str:
        if "shareActive=true" in script:
            return ""
        if "urn:li:activity" in script:
            return "https://www.linkedin.com/feed/update/urn:li:activity:999"
        if "document.body.innerText" in script:
            return self.published_body
        return self._readback


class _Ctx:
    def __init__(self, tab: _Tab) -> None:
        self._tab = tab

    async def new_page(self) -> _Tab:
        return self._tab

    async def close(self) -> None:
        return None


class _Handle:
    async def close(self) -> None:
        return None

    async def stop(self) -> None:
        return None


def _patch(monkeypatch, tab: _Tab) -> None:
    async def fake_browser():
        return _Handle(), _Handle(), _Ctx(tab)

    async def fake_goto(page, url, **kw):
        return ""

    async def fake_pause(page, *a, **kw):
        return None

    async def fake_snapshot(page, label):
        return f"shot-{label}.png"

    monkeypatch.setattr(publish, "browser", fake_browser)
    monkeypatch.setattr(publish, "goto", fake_goto)
    monkeypatch.setattr(publish, "pause", fake_pause)
    monkeypatch.setattr(publish, "snapshot", fake_snapshot)


# ------------------------------------------------------------------- image

async def test_image_publish_attaches_the_file_and_succeeds(state, monkeypatch):
    state(doc(IMAGE_ENTRY))
    tab = _Tab(readback=IMAGE_ENTRY["text"], published_body=IMAGE_ENTRY["text"])
    _patch(monkeypatch, tab)

    r = await publish.publish("flycheap", "B1")
    assert r.ok, r.detail
    assert tab.uploaded == [(publish.FILE_INPUT_SELECTOR, IMAGE_ENTRY["media"])]
    assert any("share-actions__primary-action" in c or "Post" in c
              for c in tab.locator_calls)
    assert store.load()["improver"]["post_bank"][0]["status"] == "published"


async def test_a_failed_media_attach_names_its_step_and_never_posts(state, monkeypatch):
    state(doc(IMAGE_ENTRY))
    tab = _Tab(readback=IMAGE_ENTRY["text"], published_body=IMAGE_ENTRY["text"],
              fail_at=(publish.MEDIA_BUTTON_SELECTOR,))
    _patch(monkeypatch, tab)

    r = await publish.publish("flycheap", "B1")
    assert not r.ok
    assert "step 'attach-image' failed" in r.detail
    assert not any("share-actions__primary-action" in c for c in tab.clicked)
    assert store.load()["improver"]["post_bank"][0]["status"] == "approved"


async def test_an_image_post_with_no_media_is_refused_before_any_browser_call(
        state, monkeypatch):
    entry = dict(IMAGE_ENTRY)
    entry["media"] = None
    state(doc(entry))

    async def boom():
        raise AssertionError("no browser should open")

    monkeypatch.setattr(publish, "browser", boom)
    r = await publish.publish("flycheap", "B1")
    assert not r.ok
    assert "no media attached" in r.detail


# ------------------------------------------------------------------- video

async def test_video_publish_attaches_the_file_and_succeeds(state, monkeypatch):
    state(doc(VIDEO_ENTRY))
    tab = _Tab(readback=VIDEO_ENTRY["text"], published_body=VIDEO_ENTRY["text"])
    _patch(monkeypatch, tab)

    r = await publish.publish("flycheap", "B2")
    assert r.ok, r.detail
    assert tab.uploaded == [(publish.FILE_INPUT_SELECTOR, VIDEO_ENTRY["media"])]


async def test_a_failed_video_attach_names_its_step(state, monkeypatch):
    state(doc(VIDEO_ENTRY))
    tab = _Tab(readback=VIDEO_ENTRY["text"], published_body=VIDEO_ENTRY["text"],
              fail_at=(publish.FILE_INPUT_SELECTOR,))
    _patch(monkeypatch, tab)

    r = await publish.publish("flycheap", "B2")
    assert not r.ok
    assert "step 'attach-video' failed" in r.detail


# -------------------------------------------------------------------- poll

async def test_poll_publish_fills_question_options_and_succeeds(state, monkeypatch):
    state(doc(POLL_ENTRY))
    tab = _Tab(readback=POLL_ENTRY["text"], published_body=POLL_ENTRY["text"])
    _patch(monkeypatch, tab)

    r = await publish.publish("flycheap", "B3")
    assert r.ok, r.detail
    assert POLL_ENTRY["poll"]["question"] in tab.typed
    for option in POLL_ENTRY["poll"]["options"]:
        assert option in tab.typed
    assert publish.POLL_ONE_WEEK_SELECTOR in tab.locator_calls
    assert publish.POLL_DONE_SELECTOR in tab.locator_calls


async def test_a_failed_poll_setup_names_its_step_and_never_posts(state, monkeypatch):
    state(doc(POLL_ENTRY))
    tab = _Tab(readback=POLL_ENTRY["text"], published_body=POLL_ENTRY["text"],
              fail_at=(publish.POLL_BUTTON_SELECTOR,))
    _patch(monkeypatch, tab)

    r = await publish.publish("flycheap", "B3")
    assert not r.ok
    assert "step 'fill-poll' failed" in r.detail
    assert not any("share-actions__primary-action" in c for c in tab.clicked)
    assert store.load()["improver"]["post_bank"][0]["status"] == "approved"


async def test_a_poll_with_one_option_is_refused_before_any_browser_call(
        state, monkeypatch):
    entry = json.loads(json.dumps(POLL_ENTRY))
    entry["poll"]["options"] = ["only one"]
    state(doc(entry))

    async def boom():
        raise AssertionError("no browser should open")

    monkeypatch.setattr(publish, "browser", boom)
    r = await publish.publish("flycheap", "B3")
    assert not r.ok
    assert "fewer than 2 options" in r.detail


# --------------------------------------------------------- never claim what
#                                                            was not seen

async def test_a_post_not_found_on_the_published_list_is_never_a_success(
        state, monkeypatch):
    state(doc(IMAGE_ENTRY))
    tab = _Tab(readback=IMAGE_ENTRY["text"], published_body="nothing matches here")
    _patch(monkeypatch, tab)

    r = await publish.publish("flycheap", "B1")
    assert not r.ok
    assert "could not be confirmed" in r.detail
    entry = store.load()["improver"]["post_bank"][0]
    assert entry["status"] == "posted_unverified"
    # The gate must not offer it again -- Post was clicked once already, and
    # deletion is Tier 3, so a second click would double-post.
    assert entry["status"] != "approved"
