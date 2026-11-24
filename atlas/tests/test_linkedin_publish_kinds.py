"""Task 64: publishing an image, a video or a poll drives the right
composer steps, a failed step is named rather than swallowed, and a post
that cannot be confirmed on the page's published list is never claimed as
a success.

Most composer selectors here are ASSUMED (never exercised against a live
LinkedIn session, per the brief) -- these tests fix what `publish.py`
itself asks of a Playwright page, not what LinkedIn's real DOM looks like.
The poll flow is the exception: its tests at the bottom answer against an
outline read off the live composer on 2026-10-13.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from atlas.modules import linkedin
from atlas.modules.linkedin import publish, store


@pytest.fixture(autouse=True)
def _flycheap_as_fixture_page(monkeypatch):
    """These tests drive the page mechanics with FlyCheap as their example
    page. FlyCheap is retired by default (the owner, 2026-10-09); that default is
    pinned in test_linkedin_retired.py, not here."""
    from atlas.modules import linkedin as _li
    monkeypatch.setitem(_li.PAGES, "flycheap", _li.KNOWN_PAGES["flycheap"])



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
              "text": "Stop overpaying for flights.\nhttps://flycheap.example #travel",
              "media": "/tmp/does-not-need-to-exist.png"}
VIDEO_ENTRY = {"id": "B2", "status": "approved", "kind": "video",
              "text": "Watch fares drop.\nhttps://flycheap.example #travel",
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
        self.tab.focused = self.selector
        self._maybe_fail("click")

    async def is_enabled(self) -> bool:
        # A real composer enables Post once the text is in; _click_post
        # waits for exactly this before clicking.
        return True

    @property
    def last(self) -> _Locator:
        return self

    async def count(self) -> int:
        return 0 if self.selector in self.tab.fail_at else 1

    async def get_attribute(self, name: str) -> str | None:
        return None

    async def select_option(self, value: str) -> None:
        self.tab.selected.append((self.selector, value))
        self.tab.values[self.selector] = value

    async def input_value(self) -> str:
        return self.tab.values.get(self.selector, "")

    async def set_input_files(self, path: str) -> None:
        self.tab.uploaded.append((self.selector, path))
        self._maybe_fail("set_input_files")


class _Keyboard:
    def __init__(self, tab: _Tab) -> None:
        self.tab = tab

    async def type(self, text: str, delay: int | None = None) -> None:
        self.tab.typed.append(text)
        if self.tab.focused is not None:
            self.tab.values[self.tab.focused] = (
                self.tab.values.get(self.tab.focused, "") + text)


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
        self.selected: list[tuple[str, str]] = []
        self.values: dict[str, str] = {}
        self.focused: str | None = None

    async def wait_for_timeout(self, ms: int) -> None:
        return None

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
    assert (publish.POLL_DURATION_SELECTOR, publish.POLL_ONE_WEEK_VALUE) in tab.selected
    assert publish.POLL_DONE_SELECTOR in tab.clicked


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


# ------------------------------------------- the live toolbar (2026-10-13)
#
# Cards #377 and #378 failed at 'fill-poll': the page-admin composer no
# longer shows a poll button until its "More" ("+") is clicked. The fake
# below answers selectors against the outline read off the live composer
# (tests/fixtures/linkedin_composer_poll.json), so these tests exercise
# what that page actually holds rather than "every selector is visible".

COMPOSER = json.loads((Path(__file__).parent / "fixtures"
                       / "linkedin_composer_poll.json").read_text())
_TOKEN = re.compile(r"\[[^\]]+\]|:[a-z-]+\('[^']*'\)|[.#][\w-]+")


def _matches(selector: str, el: dict) -> bool:
    """A small subset of Playwright selectors: tag, .class, #id,
    [aria-label='x'], [aria-label*='x' i], :has-text('x'), :text-is('x').
    Anything else matches nothing, which is the honest answer."""
    for part in selector.split(", "):
        part = part.removeprefix("[role='dialog'] ").strip()
        m = re.match(r"^([a-z]+)?(.*)$", part)
        tag, rest = m.group(1), m.group(2)
        if tag and tag != el.get("tag"):
            continue
        if _TOKEN.sub("", rest):
            continue                         # something this matcher cannot read
        classes = (el.get("class") or "").split()
        label = el.get("aria_label") or ""
        text = el.get("text") or ""
        ok = True
        for token in _TOKEN.findall(rest):
            if token.startswith("."):
                ok &= token[1:] in classes
            elif token.startswith("#"):
                ok &= token[1:] == el.get("id")
            elif g := re.fullmatch(r"\[aria-label='([^']*)'\]", token):
                ok &= label == g.group(1)
            elif g := re.fullmatch(r"\[aria-label\*='([^']*)' i\]", token):
                ok &= g.group(1).lower() in label.lower()
            elif g := re.fullmatch(r":has-text\('([^']*)'\)", token):
                ok &= g.group(1).lower() in (text or label).lower()
            elif g := re.fullmatch(r":text-is\('([^']*)'\)", token):
                ok &= text == g.group(1)
            else:
                ok = False
        if ok:
            return True
    return False


_COMPOSER_BASE = [{"tag": "div", "class": "ql-editor", "aria_label": "", "text": ""},
                  {"tag": "button", "class": "share-actions__primary-action",
                   "aria_label": "", "text": "Post"}]


class _FixtureLocator(_Locator):
    def __init__(self, tab: _ComposerTab, selector: str, index: int = 0) -> None:
        super().__init__(tab, selector)
        self.index = index

    @property
    def first(self) -> _FixtureLocator:
        return _FixtureLocator(self.tab, self.selector, 0)

    @property
    def last(self) -> _FixtureLocator:
        return _FixtureLocator(self.tab, self.selector, -1)

    def _el(self) -> dict | None:
        hits = [el for el in self.tab.elements() if _matches(self.selector, el)]
        return hits[self.index] if hits else None

    async def count(self) -> int:
        return 0 if self._el() is None else 1

    async def wait_for(self, *, state: str = "", timeout: int = 0) -> None:
        present = self._el() is not None
        if state == "hidden" and present:
            raise RuntimeError(f"Timeout {timeout}ms: {self.selector!r} still visible")
        if state != "hidden" and not present:
            raise RuntimeError(f"Timeout {timeout}ms exceeded, waiting for "
                               f"{self.selector!r} to be visible")

    async def click(self) -> None:
        el = self._el()
        if el is None:
            raise RuntimeError(f"nothing to click for {self.selector!r}")
        self.tab.clicked.append(self.selector)
        self.tab.clicked_names.append(el.get("aria_label") or el.get("text") or "")
        self.tab.focused = el.get("id") or el.get("class")
        self.tab.on_click(el)

    async def get_attribute(self, name: str) -> str | None:
        el = self._el()
        return (el or {}).get("aria_label") if name == "aria-label" else None

    async def select_option(self, value: str) -> None:
        el = self._el()
        if el is None or value not in [o[0] for o in el.get("options") or []]:
            raise RuntimeError(f"no option {value!r} in {self.selector!r}")
        self.tab.selected.append((self.selector, value))
        self.tab.values["<select>"] = value

    async def input_value(self) -> str:
        el = self._el() or {}
        if el.get("tag") == "select":
            default = next(o[0] for o in el["options"] if o[2])
            return self.tab.values.get("<select>", default)
        return self.tab.values.get(el.get("id") or el.get("class") or "", "")


class _ComposerTab(_Tab):
    """The composer as read live: the toolbar before "More", the toolbar
    after it, and the "Create a poll" form, whose "Add option" reveals
    Option 3 and 4."""

    def __init__(self, *, toolbar: list[dict] | None = None, **kw) -> None:
        super().__init__(**kw)
        self.toolbar = list(COMPOSER["toolbar_before_more"] if toolbar is None
                            else toolbar)
        self.form_open = False
        self.options_added = 0
        self.clicked_names: list[str] = []

    def elements(self) -> list[dict]:
        if not self.form_open:
            return _COMPOSER_BASE + self.toolbar
        form = [dict(el) for el in COMPOSER["poll_form"]]
        for el in form:
            if el.get("aria_label") == "Done":
                el.pop("disabled", None)
        form += [{"tag": "input", "class": "polls-detour__form-fields",
                  "aria_label": "", "text": "", "id": f"poll-option-{n}"}
                 for n in range(3, 3 + self.options_added)]
        return form

    def on_click(self, el: dict) -> None:
        label = el.get("aria_label") or ""
        if label == "More":
            self.toolbar = list(COMPOSER["toolbar_after_more"])
        elif label == "Create a poll":
            self.form_open = True
        elif el.get("text") == "Add option":
            self.options_added += 1
        elif label == "Done":
            self.form_open = False

    def locator(self, selector: str) -> _FixtureLocator:
        self.locator_calls.append(selector)
        return _FixtureLocator(self, selector)


class _LossyKeyboard(_Keyboard):
    """Drops the last character of every poll option, the way a field
    with a shorter limit than expected would."""

    async def type(self, text: str, delay: int | None = None) -> None:
        options = POLL_ENTRY["poll"]["options"]
        await super().type(text[:-1] if text in options else text, delay)


def test_the_fixture_is_the_toolbar_that_broke():
    before = [b["aria_label"] for b in COMPOSER["toolbar_before_more"]]
    after = [b["aria_label"] for b in COMPOSER["toolbar_after_more"]]
    assert before == ["Add media", "Create an event", "Celebrate an occasion", "More"]
    assert "Create a poll" in after and "More" not in after
    assert not any(_matches(publish.POLL_BUTTON_SELECTOR, b)
                   for b in COMPOSER["toolbar_before_more"])
    assert any(_matches(publish.POLL_BUTTON_SELECTOR, b)
               for b in COMPOSER["toolbar_after_more"])
    # "More" is found by its exact label, and nothing that finds it can
    # ever land on "Add media" (the file picker).
    more, media = COMPOSER["toolbar_before_more"][-1], COMPOSER["toolbar_before_more"][0]
    assert any(_matches(s, more) for s in publish.POLL_MORE_SELECTORS)
    assert not any(_matches(s, media) for s in publish.POLL_MORE_SELECTORS)


async def test_a_poll_is_found_behind_more_on_the_live_toolbar(state, monkeypatch):
    state(doc(POLL_ENTRY))
    tab = _ComposerTab(readback=POLL_ENTRY["text"], published_body=POLL_ENTRY["text"])
    _patch(monkeypatch, tab)

    r = await publish.publish("flycheap", "B3")
    assert r.ok, r.detail
    names = tab.clicked_names
    assert names.index("More") < names.index("Create a poll")
    assert "Add media" not in names
    assert names.count("Add option") == 1            # the 3rd of 3 options
    assert tab.values["poll-option-1"] == "Price"
    assert tab.values["poll-option-2"] == "Flexibility"
    assert tab.values["poll-option-3"] == "Direct routes"
    assert tab.selected == [(publish.POLL_DURATION_SELECTOR, "7")]
    assert names.index("Done") < names.index("Post")
    assert names.count("Post") == 1
    assert store.load()["improver"]["post_bank"][0]["status"] == "published"


async def test_a_toolbar_already_showing_the_poll_button_skips_more(state, monkeypatch):
    state(doc(POLL_ENTRY))
    tab = _ComposerTab(toolbar=COMPOSER["toolbar_after_more"],
                       readback=POLL_ENTRY["text"], published_body=POLL_ENTRY["text"])
    _patch(monkeypatch, tab)

    r = await publish.publish("flycheap", "B3")
    assert r.ok, r.detail
    assert "More" not in tab.clicked_names
    assert "Create a poll" in tab.clicked_names


async def test_no_poll_and_no_more_fails_named_and_never_posts(state, monkeypatch):
    state(doc(POLL_ENTRY))
    toolbar = COMPOSER["toolbar_before_more"][:-1]     # no "+" at all
    tab = _ComposerTab(toolbar=toolbar, readback=POLL_ENTRY["text"],
                       published_body=POLL_ENTRY["text"])
    _patch(monkeypatch, tab)

    r = await publish.publish("flycheap", "B3")
    assert not r.ok
    assert "step 'fill-poll' failed" in r.detail
    assert "shot-step-fill-poll-failed.png" in r.detail
    # The last toolbar button is then "Celebrate an occasion": it is never
    # clicked as a stand-in for "More", and nothing else is either.
    assert tab.clicked_names == []
    assert store.load()["improver"]["post_bank"][0]["status"] == "approved"


async def test_a_poll_option_that_does_not_read_back_is_never_posted(
        state, monkeypatch):
    state(doc(POLL_ENTRY))
    tab = _ComposerTab(readback=POLL_ENTRY["text"], published_body=POLL_ENTRY["text"])
    tab.keyboard = _LossyKeyboard(tab)
    _patch(monkeypatch, tab)

    r = await publish.publish("flycheap", "B3")
    assert not r.ok
    assert "step 'fill-poll' failed" in r.detail
    assert "did not read back as approved" in r.detail
    assert "Done" not in tab.clicked_names
    assert "Post" not in tab.clicked_names
    assert store.load()["improver"]["post_bank"][0]["status"] == "approved"


async def test_a_poll_over_the_forms_limits_is_refused_before_any_browser(
        state, monkeypatch):
    entry = json.loads(json.dumps(POLL_ENTRY))
    entry["poll"]["options"] = ["Price", "x" * 31]
    state(doc(entry))

    async def boom():
        raise AssertionError("no browser should open")

    monkeypatch.setattr(publish, "browser", boom)
    r = await publish.publish("flycheap", "B3")
    assert not r.ok
    assert "over 30 characters" in r.detail
