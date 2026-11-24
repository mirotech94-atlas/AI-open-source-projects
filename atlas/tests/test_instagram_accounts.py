"""Instagram, multi-account: `main` (the owner's own) and `stories` (the
AI-stories account, Phase 18).

What is load-bearing here:

* Each account resolves its OWN env prefix; an unconfigured account reads
  as UNKNOWN and names which prefix is missing, never as OK, never as a
  crash on the other account.
* `instagram.hide_comment` is Tier ASK — REELS (`Da3bCbINuQf`) rules that
  hiding someone else's comment is a decision about another person — with
  a dry run and an unhide rollback kept, and the registry's own
  `classify()` agrees with that tier.
* `instagram.reply_comment` is Tier ASK and irreversible — a public reply
  cannot be unsent.
* `instagram.publish` keeps its name and gains an `account` param that
  defaults to `main`, so the stories account goes through the identical
  Tier ASK gate rather than a second path, and refuses to post to
  `stories` unless the caption already carries `content.drafts.AI_LABEL`.
* `brief()`'s overnight line never reports a zero nothing measured: before
  `check_overnight` has run for an account, it says "not read yet", not
  "0" — and once it has run, the figures come from `actions_log`, from
  `messages.check_unanswered`'s own cached observation, and from pending
  `instagram.hide_comment` proposals, never from a process counter.
"""
from __future__ import annotations

import ast
import json
import pathlib
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from atlas.kernel.contracts import Health, Observation, Tier
from atlas.kernel.permissions import classify
from atlas.modules import instagram

ROOT = pathlib.Path(__file__).resolve().parent.parent / "atlas"
PKG = ROOT / "modules" / "instagram"


# ------------------------------------------------------- no executor path

def _imports_of(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level:
                parts = ["atlas", "modules", "instagram"][: 3 - (node.level - 1)]
                mod = ".".join(parts + ([mod] if mod else []))
            names.add(mod)
    return names


def test_no_file_in_the_module_imports_the_executor():
    files = list(PKG.glob("*.py"))
    assert files
    for f in files:
        for mod in _imports_of(f):
            assert "executor" not in mod, f"{f.name} imports {mod}"


# ----------------------------------------------------------------- accounts

def test_accounts_map_is_exactly_main_stories_and_clips():
    assert instagram.ACCOUNTS == {
        "main": "INSTAGRAM_", "stories": "INSTAGRAM_STORIES_",
        "clips": "INSTAGRAM_CLIPS_"}


def test_module_requires_no_env_so_one_account_can_light_up_alone():
    assert instagram.MODULE.requires_env == ()
    assert set(instagram.MODULE.optional_env) == {
        "INSTAGRAM_ACCOUNT_ID", "INSTAGRAM_TOKEN",
        "INSTAGRAM_STORIES_ACCOUNT_ID", "INSTAGRAM_STORIES_TOKEN",
        "INSTAGRAM_CLIPS_ACCOUNT_ID", "INSTAGRAM_CLIPS_TOKEN",
    }


def test_cfg_resolves_each_accounts_own_env(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_ACCOUNT_ID", "main-id")
    monkeypatch.setenv("INSTAGRAM_TOKEN", "main-token")
    monkeypatch.setenv("INSTAGRAM_STORIES_ACCOUNT_ID", "stories-id")
    monkeypatch.setenv("INSTAGRAM_STORIES_TOKEN", "stories-token")

    assert instagram._cfg("main") == ("main-id", "main-token")
    assert instagram._cfg("stories") == ("stories-id", "stories-token")


def test_cfg_raises_naming_the_account_when_unset(monkeypatch):
    monkeypatch.delenv("INSTAGRAM_STORIES_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("INSTAGRAM_STORIES_TOKEN", raising=False)

    with pytest.raises(instagram.NoAccount, match="INSTAGRAM_STORIES_"):
        instagram._cfg("stories")


def test_unknown_account_name_is_a_clear_error():
    with pytest.raises(instagram.NoAccount, match="no such Instagram account"):
        instagram._cfg("nonexistent")


async def test_check_token_is_unknown_naming_the_prefix_when_unset(monkeypatch):
    monkeypatch.delenv("INSTAGRAM_STORIES_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("INSTAGRAM_STORIES_TOKEN", raising=False)

    obs = await instagram.check_token("stories")

    assert obs.check == "stories_token"
    assert obs.health is Health.UNKNOWN
    assert "INSTAGRAM_STORIES_" in obs.detail


async def test_check_comments_pending_is_unknown_naming_the_prefix(monkeypatch):
    monkeypatch.delenv("INSTAGRAM_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("INSTAGRAM_TOKEN", raising=False)

    obs = await instagram.check_comments_pending("main")

    assert obs.check == "main_comments_pending"
    assert obs.health is Health.UNKNOWN
    assert "INSTAGRAM_" in obs.detail


async def test_check_token_reports_the_configured_account(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_STORIES_ACCOUNT_ID", "sid")
    monkeypatch.setenv("INSTAGRAM_STORIES_TOKEN", "stok")

    async def fake_get(path, account="main", **params):
        assert account == "stories"
        return {"username": "atlas_ai_stories", "followers_count": 42}

    monkeypatch.setattr(instagram, "_get", fake_get)
    obs = await instagram.check_token("stories")

    assert obs.check == "stories_token"
    assert obs.health is Health.OK
    assert obs.value == 42.0


def test_checks_are_registered_per_account():
    names = {c.name for c in instagram.MODULE.checks()}
    for account in instagram.ACCOUNTS:
        assert f"{account}_token" in names
        assert f"{account}_engagement" in names
        assert f"{account}_comments_pending" in names
        assert f"{account}_overnight" in names


# ------------------------------------------------------------------ publish

def test_publish_action_has_an_account_param_defaulting_to_main():
    actions = {a.name: a for a in instagram.MODULE.actions()}
    action = actions["instagram.publish"]
    assert "account" in action.params
    import inspect
    sig = inspect.signature(instagram.publish)
    assert sig.parameters["account"].default == "main"


async def test_publish_dry_reports_the_missing_stories_account(monkeypatch):
    monkeypatch.delenv("INSTAGRAM_STORIES_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("INSTAGRAM_STORIES_TOKEN", raising=False)

    result = await instagram.publish_dry("a story", "http://x/img.jpg",
                                         account="stories", content_kind="ai_story")
    assert result.ok is False
    assert "stories" in result.detail


async def test_unpublish_uses_the_account_in_the_rollback_token(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_STORIES_ACCOUNT_ID", "sid")
    monkeypatch.setenv("INSTAGRAM_STORIES_TOKEN", "stok")

    class FakeResponse:
        status_code = 200

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def delete(self, url, params=None, headers=None):
            # ed4a247: the token rides in the Authorization header, never
            # the query string.
            assert params is None
            assert "stok" in (headers or {}).get("Authorization", "")
            assert "media-9" in url
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", lambda timeout=None: FakeClient())
    result = await instagram.unpublish("stories:media-9")
    assert result.ok is True


async def test_unpublish_refuses_a_malformed_token():
    result = await instagram.unpublish("no-colon-here")
    assert result.ok is False


# ---------------------------------------------------------------- moderation

def test_hide_comment_is_tier_ask_with_dry_run_and_rollback():
    """REELS `Da3bCbINuQf` rules that hiding someone else's comment is a
    decision about another person, even though it is reversible — so the
    rollback stays (that is what "reversible" means), but the tier moves
    to ASK."""
    actions = {a.name: a for a in instagram.MODULE.actions()}
    action = actions["instagram.hide_comment"]
    assert action.tier is Tier.ASK
    assert action.dry_run is not None
    assert action.rollback is not None
    assert action.irreversible is False


def test_hide_comment_tier_agrees_with_the_registrys_own_classify():
    actions = {a.name: a for a in instagram.MODULE.actions()}
    decision = classify(actions["instagram.hide_comment"])
    assert decision.allowed is True
    assert decision.tier is Tier.ASK


def test_reply_comment_is_tier_ask_and_irreversible():
    actions = {a.name: a for a in instagram.MODULE.actions()}
    action = actions["instagram.reply_comment"]
    assert action.tier is Tier.ASK
    assert action.irreversible is True


def test_reply_comment_tier_agrees_with_the_registrys_own_classify():
    actions = {a.name: a for a in instagram.MODULE.actions()}
    decision = classify(actions["instagram.reply_comment"])
    assert decision.allowed is True
    assert decision.tier is Tier.ASK


async def test_hide_comment_dry_refuses_an_already_hidden_comment(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_ACCOUNT_ID", "id")
    monkeypatch.setenv("INSTAGRAM_TOKEN", "tok")

    async def fake_get(path, account="main", **params):
        return {"id": path, "hidden": True}

    monkeypatch.setattr(instagram, "_get", fake_get)
    result = await instagram.hide_comment_dry("main", "c1")
    assert result.ok is False
    assert "already hidden" in result.detail


async def test_hide_comment_runs_and_returns_an_unhide_rollback_token(monkeypatch):
    calls = []

    async def fake_post(path, account="main", **params):
        calls.append((path, account, params))
        return {"success": True}

    monkeypatch.setattr(instagram, "_post", fake_post)
    result = await instagram.hide_comment("stories", "c42")

    assert result.ok is True
    assert result.rollback_token == "stories:c42"
    assert calls == [("c42", "stories", {"hide": "true"})]


async def test_unhide_comment_rollback_calls_hide_false(monkeypatch):
    calls = []

    async def fake_post(path, account="main", **params):
        calls.append((path, account, params))
        return {"success": True}

    monkeypatch.setattr(instagram, "_post", fake_post)
    result = await instagram.unhide_comment("main:c7")

    assert result.ok is True
    assert calls == [("c7", "main", {"hide": "false"})]


async def test_unhide_comment_refuses_a_malformed_token():
    result = await instagram.unhide_comment("no-colon")
    assert result.ok is False


async def test_reply_comment_dry_refuses_empty_text(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_ACCOUNT_ID", "id")
    monkeypatch.setenv("INSTAGRAM_TOKEN", "tok")
    result = await instagram.reply_comment_dry("main", "c1", "   ")
    assert result.ok is False


async def test_reply_comment_runs(monkeypatch):
    async def fake_post(path, account="main", **params):
        assert path == "c1/replies"
        return {"id": "reply-1"}

    monkeypatch.setattr(instagram, "_post", fake_post)

    result = await instagram.reply_comment("main", "c1", "thanks!")

    assert result.ok is True
    assert result.evidence == {"reply_id": "reply-1"}


# --------------------------------------------------------- publish (AI label)

async def test_publish_dry_refuses_stories_without_the_ai_label(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_STORIES_ACCOUNT_ID", "sid")
    monkeypatch.setenv("INSTAGRAM_STORIES_TOKEN", "stok")

    result = await instagram.publish_dry(
        "a story with no label", "http://x/v.mp4", kind="reel",
        account="stories", content_kind="ai_story")

    assert result.ok is False
    assert "content.drafts.AI_LABEL" in result.detail


async def test_publish_dry_allows_stories_with_the_ai_label(monkeypatch):
    from atlas.modules.content.drafts import AI_LABEL

    monkeypatch.setenv("INSTAGRAM_STORIES_ACCOUNT_ID", "sid")
    monkeypatch.setenv("INSTAGRAM_STORIES_TOKEN", "stok")

    result = await instagram.publish_dry(
        f"a story\n\n{AI_LABEL}", "http://x/v.mp4", kind="reel",
        account="stories", content_kind="ai_story")

    assert result.ok is True


async def test_publish_refuses_stories_without_the_ai_label(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_STORIES_ACCOUNT_ID", "sid")
    monkeypatch.setenv("INSTAGRAM_STORIES_TOKEN", "stok")

    async def boom(*a, **k):
        raise AssertionError("Graph API must not be called on a refused label")

    monkeypatch.setattr(httpx, "AsyncClient", boom)

    result = await instagram.publish(
        "no label here", "http://x/v.mp4", kind="reel", account="stories",
        content_kind="ai_story")

    assert result.ok is False
    assert "content.drafts.AI_LABEL" in result.detail


async def test_publish_the_main_account_needs_no_label(monkeypatch):
    """The rule is `stories`-only -- the owner's own posts are never refused for
    a label they were never asked to carry."""
    monkeypatch.setenv("INSTAGRAM_ACCOUNT_ID", "id")
    monkeypatch.setenv("INSTAGRAM_TOKEN", "tok")

    result = await instagram.publish_dry(
        "just a normal caption", "http://x/img.jpg", account="main",
        content_kind="travel")

    assert result.ok is True


# ----------------------------------------------------------- check_overnight

async def _finish_row(subject: str, account: str, at: datetime, *, ok: bool = True,
                      monkeypatch) -> dict:
    """One `actions_log` row built from the REAL `ActionResult.detail`
    `hide_comment`/`reply_comment` write — via a fake `_post`, not a
    hand-typed guess at the wording — so a future change to that wording
    breaks this test instead of silently breaking `_account_marker`'s
    parsing in production."""
    async def fake_post(path, account=account, **params):
        return {"success": True, "id": "reply-1"}

    monkeypatch.setattr(instagram, "_post", fake_post)
    if subject == "instagram.hide_comment":
        result = await instagram.hide_comment(account, "c1")
    else:
        result = await instagram.reply_comment(account, "c1", "thanks!")

    payload = json.dumps(
        {"event": "execute.finish", "subject": subject,
         "data": {"ok": ok, "detail": result.detail}})
    return {"subject": subject, "payload": payload, "at": at}


async def test_check_overnight_is_unknown_when_actions_log_cannot_be_read(
        monkeypatch):
    from atlas.kernel import state

    async def boom(sql, *args):
        raise RuntimeError("no database")

    async def no_brief_yet(sql, *args):
        return {"at": None}

    monkeypatch.setattr(state, "fetch_all", boom)
    monkeypatch.setattr(state, "fetch_one", no_brief_yet)

    obs = await instagram.check_overnight("main")

    assert obs.health is Health.UNKNOWN


async def test_since_last_brief_uses_the_last_stored_brief(monkeypatch):
    from atlas.kernel import state

    stamp = datetime(2026, 9, 24, 6, 0, tzinfo=UTC)

    async def fake_fetch_one(sql, *args):
        assert "reports" in sql
        assert "kind = 'brief'" in sql
        return {"at": stamp}

    monkeypatch.setattr(state, "fetch_one", fake_fetch_one)

    since = await instagram._since_last_brief()

    assert since == stamp


async def test_since_last_brief_falls_back_to_the_window_with_no_stored_brief(
        monkeypatch):
    from atlas.kernel import state

    async def fake_fetch_one(sql, *args):
        return {"at": None}

    monkeypatch.setattr(state, "fetch_one", fake_fetch_one)

    before = datetime.now(UTC) - instagram.OVERNIGHT_WINDOW
    since = await instagram._since_last_brief()

    assert since >= before


async def test_since_last_brief_falls_back_when_reports_is_unreadable(
        monkeypatch):
    from atlas.kernel import state

    async def boom(sql, *args):
        raise RuntimeError("no database")

    monkeypatch.setattr(state, "fetch_one", boom)

    before = datetime.now(UTC) - instagram.OVERNIGHT_WINDOW
    since = await instagram._since_last_brief()

    assert since >= before


def _messages_observation_fetch_one(*, health="ok", evidence=None, at=None,
                                    moderation_n=0):
    """A `fetch_one` stand-in that discriminates the three real queries
    `check_overnight` makes: the last stored brief (`reports`), the newest
    `messages`/`unanswered` observation (`observations` joined to `checks`
    and `properties`), and the pending-moderation count (`proposals`)."""
    at = at if at is not None else datetime.now(UTC)

    async def fake_fetch_one(sql, *args):
        flat = " ".join(sql.split())
        if "reports" in flat:
            return {"at": None}
        if "observations" in flat:
            return {"health": health, "evidence": evidence or {}, "at": at}
        if "proposals" in flat:
            return {"n": moderation_n}
        raise AssertionError(f"unexpected fetch_one: {flat}")

    return fake_fetch_one


async def test_check_overnight_counts_hide_and_reply_per_account(monkeypatch):
    from atlas.kernel import state

    now = datetime.now(UTC)
    rows = [
        await _finish_row("instagram.hide_comment", "main", now - timedelta(hours=1),
                          monkeypatch=monkeypatch),
        await _finish_row("instagram.hide_comment", "stories",
                          now - timedelta(hours=2), monkeypatch=monkeypatch),
        await _finish_row("instagram.reply_comment", "main",
                          now - timedelta(minutes=5), monkeypatch=monkeypatch),
        await _finish_row("instagram.hide_comment", "main", now, ok=False,
                          monkeypatch=monkeypatch),  # not counted
    ]

    async def fake_fetch_all(sql, *args):
        assert "actions_log" in sql
        return rows

    dm_at = now - timedelta(minutes=10)
    monkeypatch.setattr(state, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(state, "fetch_one", _messages_observation_fetch_one(
        evidence={"by_platform": {"instagram": 4}}, at=dm_at, moderation_n=2))

    obs = await instagram.check_overnight("main")

    assert obs.health is Health.OK
    assert obs.evidence["hidden"] == 1
    assert obs.evidence["answered"] == 1
    assert obs.evidence["dm_waiting"] == 4
    assert obs.evidence["moderation_pending"] == 2
    assert "1 hidden" in obs.detail
    assert f"DMs waiting: 4 as of {dm_at:%H:%M} UTC" in obs.detail
    assert "moderation awaiting approval: 2" in obs.detail
    assert "visits/reach: not in this line" in obs.detail   # the engagement check reads them
    assert instagram._overnight_cache["main"] is obs


async def test_check_overnight_says_dms_not_read_with_no_prior_observation(
        monkeypatch):
    from atlas.kernel import state

    async def fake_fetch_all(sql, *args):
        return []

    async def fake_fetch_one(sql, *args):
        flat = " ".join(sql.split())
        if "reports" in flat:
            return {"at": None}
        if "observations" in flat:
            return None
        return {"n": 0}

    monkeypatch.setattr(state, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(state, "fetch_one", fake_fetch_one)

    obs = await instagram.check_overnight("stories")

    assert "DMs waiting: not read" in obs.detail
    assert obs.evidence["dm_waiting"] is None
    assert obs.health is Health.UNKNOWN


async def test_check_overnight_says_dms_not_read_when_both_bridges_are_down(
        monkeypatch):
    """An UNKNOWN `messages.check_unanswered` row with no evidence at all
    (the check could not run). Reading the observation's own health (rather
    than `last_evidence`, which would keep returning the last reading that
    still had evidence, hours old, as if it were current) is what catches
    this."""
    from atlas.kernel import state

    async def fake_fetch_all(sql, *args):
        return []

    stamp = datetime.now(UTC) - timedelta(hours=5)
    monkeypatch.setattr(state, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(state, "fetch_one", _messages_observation_fetch_one(
        health="unknown", evidence={}, at=stamp))

    obs = await instagram.check_overnight("main")

    assert f"not read (check could not run, {stamp:%H:%M} UTC)" in obs.detail
    assert obs.evidence["dm_waiting"] is None
    assert obs.health is Health.UNKNOWN
    # The newest reading is UNKNOWN even though an earlier one might have
    # had a real count -- that older count must never appear here.
    assert "DMs waiting: 4" not in obs.detail


async def test_check_overnight_says_dms_not_read_when_the_bridge_is_down(
        monkeypatch):
    """`messages.check_unanswered` omits a platform from `by_platform` both
    when it measured zero and when its bridge was unreachable -- reading
    `by_platform.get("instagram", 0)` alone would turn "could not check"
    into a healthy zero. The `unreachable` list is what tells them apart."""
    from atlas.kernel import state

    async def fake_fetch_all(sql, *args):
        return []

    stamp = datetime.now(UTC)
    monkeypatch.setattr(state, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(state, "fetch_one", _messages_observation_fetch_one(
        evidence={"by_platform": {}, "unreachable": ["Instagram (bad token)"],
                 "truncated": []}, at=stamp))

    obs = await instagram.check_overnight("main")

    assert (f"DMs waiting: not read (Instagram bridge unreachable, "
           f"{stamp:%H:%M} UTC)") in obs.detail
    assert obs.evidence["dm_waiting"] is None
    assert obs.health is Health.UNKNOWN


async def test_check_overnight_says_at_least_n_when_the_dm_window_was_full(
        monkeypatch):
    from atlas.kernel import state

    async def fake_fetch_all(sql, *args):
        return []

    stamp = datetime.now(UTC)
    monkeypatch.setattr(state, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(state, "fetch_one", _messages_observation_fetch_one(
        evidence={"by_platform": {"instagram": 25}, "unreachable": [],
                 "truncated": ["Instagram"]}, at=stamp, moderation_n=0))

    obs = await instagram.check_overnight("main")

    assert f"DMs waiting: at least 25 as of {stamp:%H:%M} UTC" in obs.detail
    assert obs.evidence["dm_waiting"] == 25
    assert obs.health is Health.OK


# -------------------------------------------------------------------- brief

def test_brief_says_not_read_yet_before_any_check_has_run():
    instagram._overnight_cache.clear()

    line = instagram.MODULE.brief()

    assert "not read yet" in line
    assert "main" in line
    assert "stories" in line


def test_brief_reads_whatever_check_overnight_last_cached(monkeypatch):
    at = datetime(2026, 9, 24, 6, 30, tzinfo=UTC)
    obs = Observation("main_overnight", Health.OK, detail="3 hidden, all good", at=at)
    monkeypatch.setitem(instagram._overnight_cache, "main", obs)
    monkeypatch.delitem(instagram._overnight_cache, "stories", raising=False)

    line = instagram.MODULE.brief()

    assert "3 hidden, all good" in line
    assert "read 06:30 UTC" in line
    assert "stories: not read yet" in line


async def test_a_failed_run_replaces_the_cache_instead_of_leaving_it_stale(
        monkeypatch):
    """A good run followed by one where `actions_log` cannot be read must
    not keep showing the earlier, now-stale figures forever -- the brief
    reads whatever `check_overnight` last cached, so that cache has to be
    the honest "not read" observation, not the old OK one left in place."""
    from atlas.kernel import state

    async def fake_fetch_all(sql, *args):
        return []

    good_at = datetime.now(UTC) - timedelta(hours=6)
    monkeypatch.setattr(state, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(state, "fetch_one", _messages_observation_fetch_one(
        evidence={"by_platform": {"instagram": 2}}, at=good_at, moderation_n=0))
    good_obs = await instagram.check_overnight("main")
    assert good_obs.health is Health.OK
    assert instagram._overnight_cache["main"] is good_obs

    async def boom(sql, *args):
        raise RuntimeError("no database")

    monkeypatch.setattr(state, "fetch_all", boom)

    bad_obs = await instagram.check_overnight("main")

    assert bad_obs.health is Health.UNKNOWN
    assert instagram._overnight_cache["main"] is bad_obs

    line = instagram.MODULE.brief()
    assert "2 as of" not in line
    assert "not read" in line


# ------------------------- the overnight DM line reads a one-bridge-down UNKNOWN

@pytest.mark.parametrize(("evidence", "expected"), [
    (None, (None, "not read (check could not run, 07:30 UTC)")),
    # An older stored reading, from when a second (since removed) bridge
    # was also read: the Instagram half is still what was measured.
    ({"by_platform": {"instagram": 2}, "unreachable": ["iMessage (timed out)"]},
     (2, "2 as of 07:30 UTC")),
    ({"by_platform": {}, "unreachable": ["Instagram (timed out)"]},
     (None, "not read (Instagram bridge unreachable, 07:30 UTC)")),
])
def test_an_unknown_dm_reading_with_one_bridge_up_still_counts_that_bridge(
        evidence, expected):
    """messages.check_unanswered is UNKNOWN when only one bridge answered;
    the Instagram half of the overnight line still reads what was measured."""
    from datetime import UTC, datetime
    row = {"health": Health.UNKNOWN, "evidence": evidence,
           "at": datetime(2026, 9, 24, 7, 30, tzinfo=UTC)}
    assert instagram._dm_waiting_text(row) == expected
