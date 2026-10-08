"""The page rename + logo action: its offline checks and dry run. The dry run
never opens LinkedIn; the logo can only be a file in the brand folder."""
from __future__ import annotations

import struct
import zlib

import pytest

from atlas.modules.linkedin import identity


def _png(path, w, h):
    raw = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    raw += struct.pack(">I", 13) + b"IHDR" + ihdr + struct.pack(
        ">I", zlib.crc32(b"IHDR" + ihdr))
    path.write_bytes(raw)


@pytest.fixture
def brand(tmp_path, monkeypatch):
    monkeypatch.setattr(identity, "BRAND_DIR", tmp_path)
    _png(tmp_path / "vault-logo-400.png", 400, 400)
    _png(tmp_path / "wide.png", 800, 400)
    _png(tmp_path / "tiny.png", 100, 100)
    (tmp_path / "fake.png").write_bytes(b"not a png")
    return tmp_path


async def test_the_dry_run_describes_the_change_without_a_browser(brand, monkeypatch):
    import atlas.modules.linkedin.browser as b

    async def no_browser():
        raise AssertionError("the dry run must not open LinkedIn")

    monkeypatch.setattr(b, "browser", no_browser)
    out = await identity.update_identity_dry("guardian", "Vault", "vault-logo-400.png")
    assert out.ok
    assert "'Vault'" in out.detail and "400x400" in out.detail


@pytest.mark.parametrize("page,name,logo,why", [
    ("nope", "Vault", "", "no such page"),
    ("guardian", "", "", "nothing to change"),
    ("guardian", "x" * 101, "", "1-100 characters"),
    ("guardian", "Vault", "../../etc/passwd", "file name"),
    ("guardian", "Vault", "/app/.env", "file name"),
    ("guardian", "Vault", "fake.png", "not a PNG"),
    ("guardian", "Vault", "wide.png", "square"),
    ("guardian", "Vault", "tiny.png", "at least 300px"),
])
async def test_bad_parameters_are_refused(brand, page, name, logo, why):
    out = await identity.update_identity_dry(page, name, logo)
    assert not out.ok and why in out.detail


def test_the_action_is_tier_2_with_a_rollback():
    from atlas.kernel.contracts import Tier
    from atlas.modules import linkedin
    act = {a.name: a for a in linkedin.MODULE.actions()}["linkedin.update_page_identity"]
    assert act.tier is Tier.ASK and act.rollback is identity.restore_identity
    assert act.runs_in_background


async def test_a_change_reports_old_and_new_and_carries_its_rollback(brand, monkeypatch):
    async def fake_run(page, name, logo_path, *, keep_old):
        from atlas.kernel.contracts import ActionResult
        assert keep_old and logo_path.name == "vault-logo-400.png"
        return ActionResult(True, ""), "Guardian", "guardian-previous-logo-1.png"

    monkeypatch.setattr(identity, "_run", fake_run)
    monkeypatch.setattr("atlas.modules.linkedin.store.log_run", lambda *a, **k: None)
    out = await identity.update_identity("guardian", "Vault", "vault-logo-400.png")
    assert out.ok and "'Guardian' to 'Vault'" in out.detail
    import json
    assert json.loads(out.rollback_token) == {
        "page": "guardian", "name": "Guardian", "logo": "guardian-previous-logo-1.png"}


async def test_a_save_that_did_not_take_is_a_failure(brand, monkeypatch):
    async def fake_run(page, name, logo_path, *, keep_old):
        from atlas.kernel.contracts import ActionResult
        return ActionResult(False, "after Save the page name reads 'Guardian'"), "Guardian", ""

    monkeypatch.setattr(identity, "_run", fake_run)
    out = await identity.update_identity("guardian", "Vault", "")
    assert not out.ok and "not changed" in out.detail


class _Btn:
    def __init__(self, label, clicks):
        self.label, self.clicks = label, clicks

    async def count(self):
        return 1

    @property
    def first(self):
        return self

    async def click(self):
        self.clicks.append(self.label)


class _Dialog:
    def __init__(self, text, clicks):
        self.text, self.clicks = text, clicks

    async def count(self):
        return 1

    @property
    def first(self):
        return self

    async def inner_text(self):
        return self.text

    def get_by_role(self, role, name):
        label = next(b for b in ("No thanks", "Start a post") if name.search(b))
        return _Btn(label, self.clicks)


class _Tab:
    def __init__(self, text):
        self.clicks = []
        self.text = text

    def locator(self, _sel):
        return _Dialog(self.text, self.clicks)

    async def wait_for_timeout(self, _ms):
        return None


async def test_the_share_your_edits_prompt_is_always_declined():
    """#195: the prompt covered the form. 'Start a post' would publish."""
    tab = _Tab("Share your page edits\nShare your new changes in a post\n"
               "No thanks\nStart a post")
    assert await identity._decline_share_prompt(tab)
    assert tab.clicks == ["No thanks"]


async def test_another_dialog_is_left_alone():
    tab = _Tab("Discard your edits?\nDiscard\nCancel")
    assert not await identity._decline_share_prompt(tab)
    assert tab.clicks == []
