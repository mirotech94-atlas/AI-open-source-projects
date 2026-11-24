"""The Vault LinkedIn page posts itself (the owner, 2026-10-11: post the Vault
page's approved posts automatically "the way reels already do").

Promoted the only way that keeps invariant 3 (memory approval-policy): a new
Tier AUTO action, `linkedin.autopublish_post`, its gates in code. Until this
change `test_linkedin_server.test_no_action_here_is_tier_one` pinned every
LinkedIn publish at ASK; `linkedin.publish_post` itself still is.

What must hold: only the Vault (`guardian`) page, never FlyCheap even when it
is active; only the bank's next approved entry with the payload approved; a
posting day and hour from the bank; one post a day; a signed-in session and
no restriction alert; LINKEDIN_AUTOPUBLISH (on by default) and the page's
pause; the reshare to the owner's profile stays a Tier 2 card; the rollback
deletes nothing and pauses the page; a refusal is the Tier 2 publish card
with "Held for you: <reason>"; a card raised before this change cannot post
the same entry twice.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from atlas.kernel.contracts import ActionResult, FailureClass, Tier
from atlas.modules import linkedin
from atlas.modules.linkedin import browser, publish, store

# Today's date, so the fixture's "last sweep an hour ago" stays within a day.
_TODAY = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
NY_NOON = _TODAY.replace(hour=16)           # 11:00-12:00 New York
NY_NIGHT = _TODAY.replace(hour=7)           # 02:00-03:00 New York

B13 = {"id": "B13", "status": "approved", "text": "Your vault, your keys.\n"
                                                  "https://vault.example"}
B14 = {"id": "B14", "status": "approved", "text": "Second post. https://vault.example"}


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A healthy Vault page: a saved session, a clean sweep an hour ago, no
    alerts, two approved entries, every hour a posting hour."""
    session = tmp_path / "session.json"
    session.write_text("{}")
    monkeypatch.setattr(browser, "SESSION", session)
    monkeypatch.setattr(store, "RUNS", tmp_path / "runs")
    monkeypatch.delenv(publish.AUTOPUBLISH_ENV, raising=False)
    monkeypatch.setitem(linkedin.PAGES, "guardian", linkedin.KNOWN_PAGES["guardian"])
    path = tmp_path / "state.json"
    monkeypatch.setattr(linkedin, "STATE", path)

    def write(*entries: dict, posting: dict | None = None, **top: Any) -> Path:
        doc = {"last_run": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
               "last_run_result": {"outcome": "ok"}, "runs": 9, "alerts": [],
               "guardian_page": {
                   "post_bank": [dict(e) for e in (entries or (B13, B14))],
                   "posting": {"enabled": True, "max_per_day": 1,
                               "reshare_to_profile": True, "hours": [0, 24],
                               **(posting or {})}}}
        doc.update(top)
        path.write_text(json.dumps(doc))
        return path
    write()
    return SimpleNamespace(write=write, session=session, path=path)


def _refusal(now: datetime | None = None, page: str = "guardian", entry_id: str = ""):
    return publish.autopublish_refusal(store.load(), page, entry_id, now=now)


# ---------------------------------------------------------------- contract

def test_autopublish_is_tier_one_with_a_dry_run_and_an_honest_rollback():
    actions = {a.name: a for a in linkedin.MODULE.actions()}
    auto = actions["linkedin.autopublish_post"]
    assert auto.tier is Tier.AUTO
    assert auto.dry_run is not None and auto.rollback is not None
    assert not auto.irreversible            # contracts refuse an irreversible Tier 1
    assert set(auto.params) == {"page", "entry_id"}
    assert auto.max_per_hour == 1
    # The card path is unchanged: still Tier 2, still irreversible.
    pub = actions["linkedin.publish_post"]
    assert pub.tier is Tier.ASK and pub.irreversible and pub.rollback is None
    assert actions["linkedin.reshare_post"].tier is Tier.ASK


# ------------------------------------------------------------------- gates

def test_a_healthy_vault_page_posts_its_next_approved_entry(world):
    entry, why, kind = _refusal(NY_NOON)
    assert (entry or {}).get("id") == "B13" and why == "ok" and kind == "ok"


def test_flycheap_never_posts_itself_even_when_active(world, monkeypatch):
    monkeypatch.setitem(linkedin.PAGES, "flycheap", linkedin.KNOWN_PAGES["flycheap"])
    entry, why, kind = _refusal(NY_NOON, page="flycheap")
    assert entry is None and kind == "held" and "only the Vault page" in why
    assert not publish.autopublish_owns("flycheap")


def test_a_retired_vault_page_does_not_post(world, monkeypatch):
    monkeypatch.delitem(linkedin.PAGES, "guardian")
    entry, why, kind = _refusal(NY_NOON)
    assert entry is None and kind == "held" and "LINKEDIN_PAGES_ACTIVE" in why


def test_the_switch_sends_it_back_to_a_card(world, monkeypatch):
    monkeypatch.setenv(publish.AUTOPUBLISH_ENV, "off")
    entry, why, kind = _refusal(NY_NOON)
    assert entry is None and kind == "held" and "LINKEDIN_AUTOPUBLISH" in why
    assert not publish.autopublish_owns("guardian")


def test_only_an_approved_entry_and_only_the_next_one(world):
    world.write({"id": "B12", "status": "draft", "text": "draft https://vault.example"})
    entry, why, kind = _refusal(NY_NOON)
    assert entry is None and kind == "none" and "no approved entry" in why
    world.write()
    entry, why, kind = _refusal(NY_NOON, entry_id="B14")
    assert entry is None and kind == "held" and "not the next" in why


def test_an_entry_changed_after_approval_is_held(world):
    world.write({**B13, "approved_sha256": publish.text_sha256("what he approved")})
    entry, why, kind = _refusal(NY_NOON)
    assert entry is None and kind == "held" and "changed after it was approved" in why
    world.write({**B13, "approved_sha256": publish.payload_sha256(B13)})
    assert _refusal(NY_NOON)[2] == "ok"


def test_posting_days_and_hours_come_from_the_bank(world):
    world.write(posting={"hours": [9, 18]})
    entry, why, kind = _refusal(NY_NIGHT)
    assert entry is None and kind == "wait" and "9-18 New York" in why
    assert _refusal(NY_NOON)[2] == "ok"
    world.write(posting={"hours": "nonsense"})              # unreadable: the default
    assert publish.posting_hours({"hours": "nonsense"}) == publish.HOURS_DEFAULT
    other = [d for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
             if d != publish.today_name()]
    world.write(posting={"days": other})
    entry, why, kind = _refusal(NY_NOON)
    assert entry is None and kind == "none" and "not a posting day" in why


def test_one_post_a_page_a_day(world):
    world.write(posting={"last_post": store.today()})
    assert _refusal(NY_NOON)[2] == "none"
    world.write({"id": "B12", "status": "posted_unverified",
                 "published_attempt_at": store.stamp(), "text": "x"}, B13)
    entry, why, kind = _refusal(NY_NOON)
    assert entry is None and kind == "none" and "already went" in why


def test_a_paused_page_is_held(world):
    world.write(posting={publish.PAUSED_KEY: {"why": "the rollback of B12"}})
    entry, why, kind = _refusal(NY_NOON)
    assert entry is None and kind == "held" and "paused" in why


def test_the_session_must_be_signed_in(world):
    world.session.unlink()
    assert "no saved LinkedIn session" in _refusal(NY_NOON)[1]
    world.session.write_text("{}")
    world.write(last_run_result={"outcome": "signed out: re-run the login"})
    entry, why, kind = _refusal(NY_NOON)
    assert entry is None and kind == "held" and "did not end clean" in why
    world.write(last_run=(datetime.now(UTC) - timedelta(days=3)).isoformat())
    assert "not known to work" in _refusal(NY_NOON)[1]


def test_a_restriction_alert_holds_it_but_a_denial_does_not(world):
    now = datetime.now(UTC)
    world.write(alerts=[{"when": now.isoformat(),
                         "what": "LinkedIn says: we restrict this account"}])
    entry, why, kind = _refusal(now)
    assert entry is None and kind == "held" and "restricted" in why
    world.write(alerts=[{"when": now.isoformat(),
                         "what": "browser crashed, NOT a LinkedIn restriction"}])
    assert _refusal(now)[2] == "ok"


# -------------------------------------------------------------- the action

async def test_the_dry_run_refuses_and_names_why(world, monkeypatch):
    monkeypatch.setenv(publish.AUTOPUBLISH_ENV, "off")
    dry = await publish.autopublish_dry("guardian", "B13")
    assert not dry.ok and "LINKEDIN_AUTOPUBLISH" in dry.detail
    monkeypatch.delenv(publish.AUTOPUBLISH_ENV)
    dry = await publish.autopublish_dry("guardian", "B13")
    assert dry.ok and "stays a card" in dry.detail and B13["text"][:20] in dry.detail


async def test_a_refused_run_never_opens_a_browser(world, monkeypatch):
    async def no_browser():
        raise AssertionError("a browser was opened")
    monkeypatch.setattr(publish, "browser", no_browser)
    world.session.unlink()
    made = await publish.autopublish("guardian", "B13")
    assert not made.ok and "no saved LinkedIn session" in made.detail


class _Tab:
    """The composer reads back the approved text; the page's published list
    shows it; the toast carries the activity deep link."""

    def __init__(self, text: str) -> None:
        self.text = text

    def locator(self, selector: str):
        class Loc:
            first = property(lambda self: self)

            async def wait_for(self, **_):
                return None

            async def click(self):
                return None
        return Loc()

    @property
    def keyboard(self):
        class K:
            async def type(self, text, delay=None):
                return None
        return K()

    async def evaluate(self, script: str) -> str:
        if "shareActive" in script:
            return ("https://www.linkedin.com/feed/?shareActive=true&shareUrl="
                    "https%3A%2F%2Fwww.linkedin.com%2Ffeed%2Fupdate%2Furn%3Ali%3A"
                    "activity%3A1")
        return self.text


async def test_an_automatic_post_publishes_without_the_reshare(world, monkeypatch):
    tab = _Tab(B13["text"])

    class Handle:
        async def close(self):
            return None

        async def stop(self):
            return None

        async def new_page(self):
            return tab

    async def fake_browser():
        return Handle(), Handle(), Handle()

    async def nothing(*_a, **_k):
        return None

    async def no_reshare(*_a, **_k):
        raise AssertionError("an automatic post reshared to the owner's profile")

    for name, fake in (("browser", fake_browser), ("goto", nothing), ("pause", nothing),
                       ("keep_session", nothing), ("_click_post", nothing),
                       ("_reshare", no_reshare)):
        monkeypatch.setattr(publish, name, fake)
    made = await publish.autopublish("guardian", "B13")
    assert made.ok, made.detail
    assert made.rollback_token == "guardian:B13"
    # The post itself never reshares; since 2026-10-12 the tick's own
    # linkedin.autoreshare_post does, or the card if it cannot.
    assert "reshare to the owner's profile follows on its own" in made.detail
    entry = store.bank(store.load(), "guardian")[0][0]
    assert entry["status"] == "published" and entry["autopublished"] is True
    assert entry["reshared"] is False and entry["activity_url"]
    # Today it is the tick's to reshare (2026-10-12), so the signal offers
    # no card; with LINKEDIN_AUTORESHARE off it is the module's own Tier 2
    # card, as for any unshared post.
    def offered():
        return [s for s in sigs if s.suggests == "linkedin.reshare_post"]
    sigs = await linkedin.MODULE.signals()
    assert offered() == []
    monkeypatch.setenv(publish.AUTORESHARE_ENV, "off")
    sigs = await linkedin.MODULE.signals()
    sig = offered()
    assert [s.params for s in sig] == [{"page": "guardian", "entry_id": "B13"}]
    assert "posted itself" in sig[0].body and "did not verify" not in sig[0].body


async def test_the_rollback_deletes_nothing_and_pauses_the_page(world):
    rolled = await publish.pause_autopublish("guardian:B13")
    assert rolled.ok and "still live" in rolled.detail
    assert rolled.evidence["unpublished"] is False
    posting = store.bank(store.load(), "guardian")[1]
    assert posting[publish.PAUSED_KEY]["entry"] == "B13"
    assert _refusal(NY_NOON)[2] == "held"
    assert not (await publish.pause_autopublish("flycheap:B1")).ok
    assert (await publish.pause_autopublish("")).ok


# --------------------------------------------------------- the daily card

async def test_vaults_daily_card_is_no_longer_a_signal_unless_switched_off(
        world, monkeypatch):
    def offered():
        return [s for s in sigs if s.suggests == "linkedin.publish_post"]
    sigs = await linkedin.MODULE.signals()
    assert offered() == []
    monkeypatch.setenv(publish.AUTOPUBLISH_ENV, "off")
    sigs = await linkedin.MODULE.signals()
    assert [s.params for s in offered()] == [{"page": "guardian", "entry_id": "B13"}]


async def test_a_post_still_waiting_after_hours_becomes_the_card(world, monkeypatch):
    """Held all day, or the tick never ran: silence must not read as a quiet
    day. Past the posting hours the daily card comes back, same title."""
    state = store.load()
    assert not publish.posting_day_over(state, "guardian", NY_NOON)  # hours 0-24
    world.write(posting={"hours": [9, 18]})
    state = store.load()
    assert not publish.posting_day_over(state, "guardian", NY_NOON)
    assert publish.posting_day_over(state, "guardian", _TODAY.replace(hour=23))
    monkeypatch.setattr(publish, "posting_day_over", lambda *_a, **_k: True)
    [sig] = [s for s in await linkedin.MODULE.signals()
             if s.suggests == "linkedin.publish_post"]
    assert sig.title == f"Publish B13 to Vault ({store.today()})"
    assert sig.body.startswith("It did not post itself today")
    world.write(posting={"last_post": store.today()})       # it went: no card
    assert [s for s in await linkedin.MODULE.signals()
            if s.suggests == "linkedin.publish_post"] == []


@pytest.fixture
def kernel(world, monkeypatch):
    """The executor runs the dry run then the action (no watch-only), and
    cards and Slack posts are recorded."""
    actions = {a.name: a for a in linkedin.MODULE.actions()}
    ran: list[tuple[str, dict]] = []
    cards: list[dict[str, Any]] = []
    posts: list[Any] = []

    async def execute(req):
        ran.append((req.action, req.params))
        spec = actions[req.action]
        dry = await spec.dry_run(**req.params)
        if not dry.ok:
            return ActionResult(False, f"dry run refused: {dry.detail}",
                                failure_class=FailureClass.VALIDATION, dispatched=False)
        return await spec.run(**req.params)

    async def propose(action, params, reason, **kw):
        cards.append({"action": action, "params": params, "reason": reason, **kw})
        return len(cards)

    async def notify(msg):
        posts.append(msg)
        return ["slack"]

    monkeypatch.setattr("atlas.kernel.executor.execute", execute)
    monkeypatch.setattr("atlas.kernel.approvals.propose", propose)
    monkeypatch.setattr("atlas.kernel.comms.notify", notify)
    return SimpleNamespace(ran=ran, cards=cards, posts=posts)


async def test_the_tick_posts_and_sends_a_receipt(kernel, monkeypatch):
    from atlas.kernel import linkedin_autopost

    async def published(page, entry_id, *, auto=False):
        assert auto is True
        return ActionResult(True, f"published {entry_id} to the Vault page on its own",
                            evidence={"page": page, "entry": entry_id})
    monkeypatch.setattr(publish, "_publish", published)
    assert await linkedin_autopost.tick() == 0
    assert kernel.ran == [("linkedin.autopublish_post",
                           {"page": "guardian", "entry_id": "B13"})]
    assert kernel.cards == []
    [post] = kernel.posts
    assert post.kind == "receipt" and post.topic == "guardian"


async def test_a_held_post_becomes_the_publish_card_with_the_reason(kernel, world):
    from atlas.kernel import linkedin_autopost
    world.session.unlink()
    assert await linkedin_autopost.tick() == 1
    [card] = kernel.cards
    assert card["action"] == "linkedin.publish_post"
    assert card["params"] == {"page": "guardian", "entry_id": "B13"}
    assert card["reason"].startswith("Held for you: no saved LinkedIn session")
    # The daily signal's own title, so one card a day at most.
    assert card["dedup_key"] == f"linkedin.publish_post@Publish B13 to Vault ({store.today()})"
    assert kernel.posts == []


async def test_nothing_to_post_or_outside_the_hours_is_silent(kernel, world, monkeypatch):
    from atlas.kernel import linkedin_autopost
    world.write({"id": "B12", "status": "draft", "text": "d https://vault.example"})
    assert await linkedin_autopost.tick() == 0
    hour = datetime.now(UTC).astimezone(publish.ZoneInfo(publish.HOURS_TZ)).hour
    world.write(posting={"hours": [(hour + 1) % 24, (hour + 1) % 24 + 1]}
                if hour < 22 else {"hours": [0, 1]})
    assert await linkedin_autopost.tick() == 0
    assert kernel.ran == [] and kernel.cards == [] and kernel.posts == []


async def test_a_clicked_but_unseen_post_is_an_alert_not_a_second_card(
        kernel, monkeypatch):
    from atlas.kernel import linkedin_autopost

    async def unseen(page, entry_id, *, auto=False):
        return ActionResult(False, f"Post was clicked for {entry_id} but not confirmed",
                            evidence={"posted_unverified": True})
    monkeypatch.setattr(publish, "_publish", unseen)
    assert await linkedin_autopost.tick() == 0
    assert kernel.cards == []
    [post] = kernel.posts
    assert post.kind == "alert"


# ------------------------------------------------- cards raised before this

async def test_a_pending_card_cannot_post_an_entry_that_already_went(world):
    """Cards #321/#336/#346 (linkedin.publish_post, Vault) stay cards. Once
    the automatic path has posted their entry, approving one is refused --
    'already posted today', then 'not next' -- never a second post."""
    world.write({**B13, "status": "published", "published": store.today()}, B14,
                posting={"last_post": store.today()})
    made = await publish.publish(page="guardian", entry_id="B13")
    assert not made.ok and "already posted today" in made.detail
    world.write({**B13, "status": "published", "published": "2026-10-01"}, B14,
                posting={"last_post": "2026-10-01"})
    made = await publish.publish(page="guardian", entry_id="B13")
    assert not made.ok and "not next" in made.detail


async def test_a_pending_flycheap_card_is_refused_and_never_auto_posted(world):
    assert "flycheap" not in linkedin.PAGES
    made = await publish.publish(page="flycheap", entry_id="B13")
    assert not made.ok and "no such page" in made.detail
    auto = await publish.autopublish("flycheap", "B13")
    assert not auto.ok and "only the Vault page" in auto.detail


# ------------------------------------------- the reshare, on its own (10-12)
#
# the owner, 2026-10-12: "whatever you think should be done should be
# automatically approved, but whatever is really urgent should only need
# human approval" (memory approval-policy). linkedin.autoreshare_post
# reshares only the Vault post ATLAS itself published today.

LIVE = {**B13, "status": "published", "autopublished": True, "reshared": False,
        "activity_url": "https://www.linkedin.com/feed/update/urn:li:activity:1/"}


def _live(**changes: Any) -> dict:
    return {**LIVE, "published": store.today(), **changes}


def test_autoreshare_is_tier_one_and_reshare_post_stays_a_card():
    actions = {a.name: a for a in linkedin.MODULE.actions()}
    auto = actions["linkedin.autoreshare_post"]
    assert auto.tier is Tier.AUTO and not auto.irreversible
    assert auto.rollback is publish.pause_autoreshare
    assert set(auto.params) == {"page", "entry_id"} and auto.max_per_hour == 1
    assert actions["linkedin.reshare_post"].tier is Tier.ASK


def test_todays_own_post_may_reshare_itself(world):
    world.write(_live(), B14)
    entry, why, kind = publish.autoreshare_refusal(store.load(), "guardian", "B13",
                                                   now=NY_NOON)
    assert (entry or {}).get("id") == "B13" and kind == "ok", why


@pytest.mark.parametrize(("changes", "says"), [
    ({"autopublished": False}, "not a post ATLAS published on its own"),
    ({"published": "2026-10-01"}, "not a post ATLAS published on its own"),
    ({"reshared": True}, "already reshared"),
    ({"reshared": "unverified"}, "did not verify"),
])
def test_anything_else_is_the_card(world, changes, says):
    world.write(_live(**changes), B14)
    entry, why, kind = publish.autoreshare_refusal(store.load(), "guardian", "B13",
                                                   now=NY_NOON)
    assert entry is None and kind == "held" and says in why


def test_one_a_day_the_switch_the_pause_and_the_session(world, monkeypatch):
    world.write(_live(), {**B14, "status": "published", "autoreshared": True,
                          "reshared_at": store.stamp()})
    _e, why, kind = publish.autoreshare_refusal(store.load(), "guardian", "B13",
                                                now=NY_NOON)
    assert kind == "held" and "already went out today" in why
    world.write(_live(), B14, posting={publish.RESHARE_PAUSED_KEY: {"why": "x"}})
    _e, why, kind = publish.autoreshare_refusal(store.load(), "guardian", "B13",
                                                now=NY_NOON)
    assert kind == "held" and "paused" in why
    world.write(_live(), B14)
    world.session.unlink()
    _e, why, kind = publish.autoreshare_refusal(store.load(), "guardian", "B13",
                                                now=NY_NOON)
    assert kind == "held" and "no saved LinkedIn session" in why
    monkeypatch.setenv(publish.AUTORESHARE_ENV, "off")
    assert not publish.autoreshare_owns("guardian")


def test_flycheap_never_reshares_itself(world, monkeypatch):
    monkeypatch.setitem(linkedin.PAGES, "flycheap", linkedin.KNOWN_PAGES["flycheap"])
    _e, why, kind = publish.autoreshare_refusal(store.load(), "flycheap", "B13")
    assert kind == "held" and "only the Vault page" in why


async def test_the_rollback_pauses_and_deletes_nothing(world):
    made = await publish.pause_autoreshare("guardian:B13")
    assert made.ok and made.evidence["paused"] is True
    _entries, posting, _ = store.bank(store.load(), "guardian")
    assert posting[publish.RESHARE_PAUSED_KEY]["entry"] == "B13"


async def test_todays_own_post_is_not_the_signals_card_but_yesterdays_is(world):
    world.write(_live(), B14)
    sigs = await linkedin.MODULE.signals()
    assert [s for s in sigs if s.suggests == "linkedin.reshare_post"] == []
    yesterday = (store.now_local() - timedelta(days=1)).strftime("%Y-%m-%d")
    world.write(_live(published=yesterday), B14)
    old = [s for s in await linkedin.MODULE.signals()
           if s.suggests == "linkedin.reshare_post"]
    assert [s.params for s in old] == [{"page": "guardian", "entry_id": "B13"}]


async def test_the_tick_reshares_with_a_receipt(kernel, world, monkeypatch):
    from atlas.kernel import linkedin_autopost
    world.write(_live(), B14, posting={"last_post": store.today()})

    async def reshared(state, page, entry_id, entry, *, auto=False):
        assert auto is True
        return ActionResult(True, f"reshared {entry_id} to the owner's profile, verified")
    monkeypatch.setattr(publish, "_reshare_entry", reshared)
    assert await linkedin_autopost.tick() == 0
    assert kernel.ran == [("linkedin.autoreshare_post",
                           {"page": "guardian", "entry_id": "B13"})]
    [post] = kernel.posts
    assert post.kind == "receipt" and "LINKEDIN_AUTORESHARE=off" in post.body
    assert kernel.cards == []


async def test_a_held_reshare_is_the_reshare_card_once(kernel, world):
    from atlas.kernel import linkedin_autopost
    world.write(_live(), B14, posting={"last_post": store.today()})
    world.session.unlink()
    assert await linkedin_autopost.tick() == 1
    [card] = kernel.cards
    assert card["action"] == "linkedin.reshare_post"
    assert card["reason"].startswith("Held for you: no saved LinkedIn session")
    assert card["dedup_key"] == "linkedin.reshare_post@Reshare B13 to your profile"


async def test_an_unverified_reshare_is_an_alert_not_a_card(kernel, world, monkeypatch):
    from atlas.kernel import linkedin_autopost
    world.write(_live(), B14, posting={"last_post": store.today()})

    async def unseen(state, page, entry_id, entry, *, auto=False):
        return ActionResult(False, "did not verify", evidence={"reshare_unverified": True})
    monkeypatch.setattr(publish, "_reshare_entry", unseen)
    assert await linkedin_autopost.tick() == 0
    assert kernel.cards == []
    [post] = kernel.posts
    assert post.kind == "alert"


# ------------------------------------------- approving drafts on its own (10-12)

DRAFT = {"id": "B20", "status": "draft", "text": "A draft. https://vault.example"}


@pytest.fixture
def other(world, monkeypatch, tmp_path):
    """A page that does not publish itself (FlyCheap's bank shape) active,
    with one draft; Vault has the same draft in its own bank."""
    monkeypatch.setitem(linkedin.PAGES, "flycheap", linkedin.KNOWN_PAGES["flycheap"])
    monkeypatch.delenv(publish.AUTOAPPROVE_ENV, raising=False)
    doc = json.loads(world.path.read_text())
    doc["improver"] = {"post_bank": [dict(DRAFT)], "posting": {}}
    doc["guardian_page"]["post_bank"].append(dict(DRAFT))
    world.path.write_text(json.dumps(doc))
    return world


async def test_a_draft_on_a_page_that_does_not_publish_itself_is_approved(other):
    entry = store.bank(store.load(), "flycheap")[0][0]
    sha = publish.payload_sha256(entry)
    assert (await publish.approve_draft_dry("flycheap", "B20", sha)).ok
    made = await publish.approve_draft("flycheap", "B20", text_sha256=sha)
    assert made.ok and made.rollback_token == "flycheap:B20"
    entry = store.bank(store.load(), "flycheap")[0][0]
    assert entry["status"] == "approved" and entry["auto_approved"] is True
    undone = await publish.unapprove_draft(made.rollback_token)
    assert undone.ok
    entry = store.bank(store.load(), "flycheap")[0][0]
    assert entry["status"] == "draft" and "auto_approved" not in entry


async def test_a_vault_draft_stays_the_owners_card_unless_he_approves_it(other):
    from atlas.kernel import idempotency
    got = await publish.approve_draft_dry("guardian", "B20")
    assert not got.ok and "yours to read and approve" in got.detail
    with idempotency.running("proposal:77"):        # his approved card
        assert (await publish.approve_draft_dry("guardian", "B20")).ok


async def test_the_switch_and_the_daily_cap(other, monkeypatch):
    monkeypatch.setenv(publish.AUTOAPPROVE_ENV, "off")
    got = await publish.approve_draft_dry("flycheap", "B20")
    assert not got.ok and publish.AUTOAPPROVE_ENV in got.detail
    monkeypatch.delenv(publish.AUTOAPPROVE_ENV)
    doc = json.loads(other.path.read_text())
    doc["improver"]["post_bank"] += [
        {"id": f"B{n}", "status": "approved", "auto_approved": True,
         "approved": store.today(), "text": "x"} for n in (21, 22)]
    other.path.write_text(json.dumps(doc))
    got = await publish.approve_draft_dry("flycheap", "B20")
    assert not got.ok and "already approved automatically today" in got.detail
