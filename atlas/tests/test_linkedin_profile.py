"""The profile lane: Miro's personal LinkedIn profile, one approval card per
post, no company page, no reshare, never auto-approved."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from atlas.modules import linkedin
from atlas.modules.linkedin import profile_cli, publish, store

TEXT = "A short note from me.\nSecond line."


@pytest.fixture
def lane(tmp_path, monkeypatch):
    monkeypatch.setitem(linkedin.PAGES, "profile", linkedin.KNOWN_PAGES["profile"]
                        if "profile" in linkedin.KNOWN_PAGES else
                        {**_spec()})
    monkeypatch.setitem(linkedin.KNOWN_PAGES, "profile", linkedin.PAGES["profile"])
    path = tmp_path / "state.json"
    path.write_text(json.dumps({}))
    monkeypatch.setattr(linkedin, "STATE", path)
    monkeypatch.setattr(store, "RUNS", tmp_path / "runs")
    return path


def _spec() -> dict[str, str]:
    monkey = pytest.MonkeyPatch()
    try:
        monkey.setenv(linkedin.PROFILE_ENV, "on")
        spec = linkedin._profile_page()
    finally:
        monkey.undo()
    assert spec is not None
    return spec


def _bank(path: Path) -> list[dict]:
    return json.loads(path.read_text())["profile_page"]["post_bank"]


def test_lane_needs_the_env_and_no_page_id(monkeypatch):
    monkeypatch.delenv(linkedin.PROFILE_ENV, raising=False)
    assert linkedin._profile_page() is None
    monkeypatch.setenv(linkedin.PROFILE_ENV, "on")
    spec = linkedin._profile_page()
    assert spec and spec["bank_key"] == "profile_page" and spec["kind"] == "profile"
    assert spec["id"] == ""


def test_profile_is_human_approval_only_and_never_autopublished():
    assert "profile" in linkedin.HUMAN_APPROVAL_PAGES
    assert "profile" not in publish.AUTOPUBLISH_PAGES


def test_company_loops_never_see_the_profile(lane):
    assert "profile" in linkedin.PAGES
    assert "profile" not in linkedin.COMPANY_PAGES
    assert "profile" not in list(linkedin.COMPANY_PAGES)


async def test_add_draft_writes_a_draft_and_nothing_else(lane):
    assert await profile_cli.add_draft("P1", TEXT) == ""
    entry = _bank(lane)[0]
    assert entry["id"] == "P1" and entry["status"] == "draft"
    assert entry["text"] == TEXT and entry["kind"] == "text"
    assert "already exists" in await profile_cli.add_draft("P1", "other")
    assert "banned link" in await profile_cli.add_draft("P2", "see my.guarder.example.com")


async def test_add_draft_refuses_when_the_lane_is_off(lane, monkeypatch):
    monkeypatch.delitem(linkedin.PAGES, "profile")
    assert "off" in await profile_cli.add_draft("P1", TEXT)


async def test_a_draft_never_publishes_and_autoapprove_refuses(lane):
    await profile_cli.add_draft("P1", TEXT)
    state = store.load()
    entry, why = publish.gate(state, "profile")
    assert entry is None and "no approved entry" in why
    assert publish.autoapprove_refusal(state, "profile")


async def test_only_a_card_approves_and_the_hash_is_checked(lane, monkeypatch):
    await profile_cli.add_draft("P1", TEXT)
    sha = publish.payload_sha256(_bank(lane)[0])
    # Not answered by a card: refused.
    r = await publish.approve_draft("profile", "P1", text_sha256=sha)
    assert not r.ok and _bank(lane)[0]["status"] == "draft"
    monkeypatch.setattr(publish, "_answered_by_card", lambda: True)
    r = await publish.approve_draft("profile", "P1", text_sha256="0" * 64)
    assert not r.ok and _bank(lane)[0]["status"] == "draft"
    r = await publish.approve_draft("profile", "P1", text_sha256=sha)
    assert r.ok and _bank(lane)[0]["status"] == "approved"
    entry, _ = publish.gate(store.load(), "profile")
    assert entry and entry["id"] == "P1"


def test_publish_card_has_no_reshare_line(lane):
    title, body = linkedin.publish_card("profile", {"id": "P1", "text": TEXT}, "d")
    assert "P1" in title and "reshare" not in body.lower() and TEXT in body


async def test_reshare_is_refused_for_the_profile(lane):
    state = {"profile_page": {"post_bank": [
        {"id": "P1", "status": "published", "activity_url": "https://x/y"}]}}
    entry, why = publish._reshare_target(state, "profile", "P1")
    assert entry is None and "nothing to reshare" in why


# ---- the posting runner, against a fake browser -------------------------

class _Loc:
    def __init__(self, tab, sel):
        self.tab, self.sel = tab, sel

    @property
    def first(self):
        return self

    async def wait_for(self, **kw):
        if self.sel in self.tab.fail_at:
            raise RuntimeError(f"no {self.sel}")

    async def click(self):
        self.tab.clicked.append(self.sel)

    async def is_enabled(self):
        return True


class _Kb:
    def __init__(self, tab):
        self.tab = tab

    async def type(self, text, delay=None):
        self.tab.typed.append(text)


class _Tab:
    def __init__(self, readback, activity_body="", fail_at=()):
        self.readback, self.activity_body = readback, activity_body
        self.fail_at = set(fail_at)
        self.clicked: list[str] = []
        self.typed: list[str] = []
        self.keyboard = _Kb(self)
        self.urls: list[str] = []

    def locator(self, sel):
        return _Loc(self, sel)

    async def wait_for_timeout(self, ms):
        return None

    async def evaluate(self, script):
        if "innerText" in script and "body" in script:
            return self.activity_body
        return self.readback


class _Ctx:
    def __init__(self, tab):
        self.tab = tab

    async def new_page(self):
        return self.tab

    async def close(self):
        return None


class _H:
    async def close(self):
        return None

    async def stop(self):
        return None


def _patch(monkeypatch, tab):
    async def fake_browser():
        return _H(), _H(), _Ctx(tab)

    async def fake_goto(page, url, **kw):
        page.urls.append(url)
        return ""

    async def noop(*a, **kw):
        return None

    async def shot(page, label):
        return f"{label}.png"

    monkeypatch.setattr(publish, "browser", fake_browser)
    monkeypatch.setattr(publish, "goto", fake_goto)
    monkeypatch.setattr(publish, "pause", noop)
    monkeypatch.setattr(publish, "snapshot", shot)
    monkeypatch.setattr(publish, "keep_session", noop)
    monkeypatch.setattr(publish, "ACTIVITY_LOOKS", 1)


def _approved(lane):
    lane.write_text(json.dumps({"profile_page": {
        "posting": {"enabled": True},
        "post_bank": [{"id": "P1", "status": "approved", "kind": "text",
                       "text": TEXT}]}}))


async def test_profile_post_goes_through_the_feed_composer_and_is_verified(
        lane, monkeypatch):
    _approved(lane)
    tab = _Tab(readback=TEXT, activity_body=TEXT)
    _patch(monkeypatch, tab)
    r = await publish.publish("profile", "P1")
    assert r.ok, r.detail
    assert tab.urls[0] == publish.PROFILE_COMPOSER
    assert not any("company" in u for u in tab.urls)
    assert tab.typed == [TEXT]
    e = _bank(lane)[0]
    assert e["status"] == "published" and "reshared" not in e


async def test_a_readback_mismatch_aborts_before_post(lane, monkeypatch):
    _approved(lane)
    tab = _Tab(readback="something else entirely")
    _patch(monkeypatch, tab)
    r = await publish.publish("profile", "P1")
    assert not r.ok and "byte for byte" in r.detail
    assert tab.clicked == [] or not any("Post" in c or "primary" in c
                                        for c in tab.clicked)
    assert _bank(lane)[0]["status"] == "approved"


async def test_a_missing_editor_fails_closed_and_names_the_step(lane, monkeypatch):
    _approved(lane)
    tab = _Tab(readback=TEXT, fail_at=(".ql-editor",))
    _patch(monkeypatch, tab)
    r = await publish.publish("profile", "P1")
    assert not r.ok and "step 'type-text' failed" in r.detail
    assert tab.typed == [] and _bank(lane)[0]["status"] == "approved"


async def test_an_unseen_post_is_posted_unverified_never_published(lane, monkeypatch):
    _approved(lane)
    tab = _Tab(readback=TEXT, activity_body="nothing relevant")
    _patch(monkeypatch, tab)
    r = await publish.publish("profile", "P1")
    assert not r.ok and r.evidence.get("posted_unverified")
    assert _bank(lane)[0]["status"] == "posted_unverified"


async def test_a_non_text_profile_entry_is_refused_before_any_browser(lane, monkeypatch):
    lane.write_text(json.dumps({"profile_page": {"post_bank": [
        {"id": "P1", "status": "approved", "kind": "image", "text": TEXT,
         "media": "/x.png"}]}}))

    async def boom():
        raise AssertionError("no browser")

    monkeypatch.setattr(publish, "browser", boom)
    r = await publish.publish("profile", "P1")
    assert not r.ok and "text only" in r.detail


async def test_card_script_refuses_a_non_draft_and_raises_for_a_draft(lane, monkeypatch):
    await profile_cli.add_draft("P1", TEXT)
    raised: list[tuple] = []

    async def fake_propose(action, params, reason, **kw):
        raised.append((action, params, kw.get("dedup_key")))
        return 1

    from atlas.kernel import approvals
    from atlas.kernel.registry import REGISTRY
    monkeypatch.setattr(approvals, "propose", fake_propose)
    monkeypatch.setattr(REGISTRY, "load_all", lambda *a, **k: None)
    assert await profile_cli.raise_card("P1") == ""
    action, params, _key = raised[0]
    assert action == "linkedin.approve_draft" and params["page"] == "profile"
    assert params["text_sha256"] == publish.payload_sha256(_bank(lane)[0])
    assert "no entry" in await profile_cli.raise_card("P9")
