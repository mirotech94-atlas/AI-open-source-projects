"""LinkedIn, run by ATLAS. The gate, the two actions, the daily cards.

What must hold: a draft never publishes; approved text is offered byte for
byte; the daily post is a Tier 2 card (except Vault's, which posts itself
since 2026-10-11 -- test_linkedin_autopublish.py); a banned link blocks
the gate; the sweep's failure modes are honest observations; and the Mac
side is gone from the repo.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from atlas.kernel.contracts import Health, Tier
from atlas.modules import linkedin
from atlas.modules.linkedin import browser, publish, store, sweep


@pytest.fixture(autouse=True)
def _flycheap_as_fixture_page(monkeypatch):
    """These tests drive the page mechanics with FlyCheap as their example
    page. FlyCheap is retired by default (Miro, 2026-10-09); that default is
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


def doc(*entries: dict, page: str = "flycheap", posting: dict | None = None) -> dict:
    holder = {"post_bank": list(entries),
              "posting": {"enabled": True, "max_per_day": 1,
                          "reshare_to_profile": True, **(posting or {})}}
    key = linkedin.PAGES[page]["bank_key"]
    return {"last_run": datetime.now(UTC).isoformat(), "runs": 1, key: holder}


APPROVED = {"id": "B13", "status": "approved",
            "text": "MIA to BOG for less.\nhttps://flycheap.example.com\n#travel"}
DRAFT = {"id": "B14", "status": "draft", "text": "Draft.\nhttps://flycheap.example.com"}


# ------------------------------------------------------------------ gate

def test_the_gate_offers_the_oldest_approved_entry(state):
    state(doc({"id": "B12", "status": "published"}, APPROVED,
              {"id": "B15", "status": "approved", "text": "later https://flycheap.example.com"}))
    entry, why = publish.gate(store.load(), "flycheap")
    assert entry["id"] == "B13" and why == "ok"


def test_a_draft_is_never_offered(state):
    state(doc(DRAFT))
    entry, why = publish.gate(store.load(), "flycheap")
    assert entry is None and "no approved" in why and "1 draft" in why


def test_a_second_post_the_same_day_is_refused(state):
    state(doc(APPROVED, posting={"last_post": store.today()}))
    entry, why = publish.gate(store.load(), "flycheap")
    assert entry is None and "already posted today" in why


def test_a_banned_link_blocks_the_gate(state):
    state(doc({"id": "B9", "status": "approved",
               "text": "Try https://flycheap.storefront.test"}))
    entry, why = publish.gate(store.load(), "flycheap")
    assert entry is None and "banned link" in why


def test_disabled_posting_blocks_the_gate(state):
    state(doc(APPROVED, posting={"enabled": False}))
    assert publish.gate(store.load(), "flycheap")[0] is None


# -------------------------------------------------------------- actions

def test_publishing_is_tier_two_and_irreversible():
    actions = {a.name: a for a in linkedin.MODULE.actions()}
    pub = actions["linkedin.publish_post"]
    assert pub.tier is Tier.ASK and pub.irreversible and pub.rollback is None
    assert pub.dry_run is not None
    appr = actions["linkedin.approve_draft"]
    # Tier 1 since 2026-10-12 (memory approval-policy): a bank change with a
    # rollback, never on the Vault page, which publishes itself.
    assert appr.tier is Tier.AUTO and appr.rollback is not None


# Miro, 2026-10-07: "make sure when requests come for either page you will
# see it and reply to it". The ONLY unattended LinkedIn writes: a reply to
# someone who wrote to the page first (requests.py). Nothing else is Tier 1.
PAGE_REPLIES = {"linkedin.draft_request_reply", "linkedin.send_request_reply"}
# Miro, 2026-10-11: post the Vault page's approved posts automatically "the
# way reels already do". Promoted in code with its gates (memory
# approval-policy), never by approving cards at runtime; pinned in
# test_linkedin_autopublish.py. Until then this test pinned every publish at
# ASK, and linkedin.publish_post itself still is.
AUTOPUBLISH = {"linkedin.autopublish_post"}
# Miro, 2026-10-12: "whatever you think should be done should be automatically
# approved, but whatever is really urgent should only need human approval"
# (memory approval-policy). The reshare of a post ATLAS itself published on
# the Vault page today (rollback: pause autoreshare), and approving a draft
# in the bank on a page that does not publish itself (rollback: back to
# draft) -- pinned in test_linkedin_autopublish.py. linkedin.reshare_post and
# linkedin.publish_post themselves stay ASK.
SINCE_2026_10_12 = {"linkedin.autoreshare_post", "linkedin.approve_draft"}


def test_no_action_here_is_tier_one():
    """A LinkedIn post cannot be rolled back without deleting it, and
    deletion is permanently denied. Nothing here may run unattended, except
    the two page-reply actions Miro asked for, the Vault autopublish and
    autoreshare (whose honest rollbacks pause the page), and approving a
    draft in the bank, with their rules in code."""
    actions = {a.name: a for a in linkedin.MODULE.actions()}
    auto = {name for name, a in actions.items() if a.tier is Tier.AUTO}
    assert auto == PAGE_REPLIES | AUTOPUBLISH | SINCE_2026_10_12
    for name in ("linkedin.publish_post", "linkedin.reshare_post",
                 "linkedin.update_page_identity"):
        assert actions[name].tier is Tier.ASK, name
    for name in PAGE_REPLIES | AUTOPUBLISH | SINCE_2026_10_12:
        a = actions[name]
        assert a.dry_run is not None and a.rollback is not None, name
        assert not a.irreversible, name


async def test_the_dry_run_shows_the_exact_text(state):
    state(doc(APPROVED))
    r = await publish.publish_dry("flycheap")
    assert r.ok and "B13" in r.detail and APPROVED["text"][:20] in r.detail
    assert r.evidence["chars"] == len(APPROVED["text"])


async def test_the_dry_run_refuses_when_the_gate_is_shut(state):
    state(doc(DRAFT))
    r = await publish.publish_dry("flycheap")
    assert not r.ok and "would not publish" in r.detail


async def test_the_dry_run_refuses_a_wrong_entry(state):
    state(doc(APPROVED))
    r = await publish.publish_dry("flycheap", "B99")
    assert not r.ok and "B13" in r.detail


async def test_publish_never_touches_a_browser_when_the_gate_is_shut(state, monkeypatch):
    state(doc(DRAFT))

    async def boom():
        raise AssertionError("no browser should open")

    monkeypatch.setattr(publish, "browser", boom)
    r = await publish.publish("flycheap")
    assert not r.ok


async def test_publish_without_a_session_says_so(state, monkeypatch):
    state(doc(APPROVED))
    monkeypatch.setattr(browser, "SESSION", Path("/nonexistent/session.json"))
    r = await publish.publish("flycheap")
    assert not r.ok and "linkedin-login.sh" in r.detail
    assert store.load()["improver"]["post_bank"][0]["status"] == "approved"


# ------------------------------------------------------- readback matching

def test_normalise_folds_nbsp_and_trailing_whitespace():
    """A Quill readback with NBSP (and its two cousins) standing in for
    plain spaces, and trailing whitespace per line, is the exact production
    failure: the screenshot showed the approved text, and the byte
    comparison still refused it. Built with `chr()` rather than escapes
    sitting in the source, so what is actually being compared does not
    depend on how a terminal or an editor chooses to render an invisible
    character."""
    nbsp, narrow_nbsp, figure_space = chr(0x00A0), chr(0x202F), chr(0x2007)
    approved = "MIA to BOG for less.\nhttps://flycheap.example.com\n#travel"
    readback = (f"MIA{nbsp}to{narrow_nbsp}BOG{figure_space}for{nbsp}less.   \n"
                f"https://flycheap.example.com \n#travel")
    assert nbsp in readback and narrow_nbsp in readback and figure_space in readback
    assert publish._normalise(approved) == publish._normalise(readback)
    assert publish._readback_diff(approved, readback) is None


def test_normalise_folds_paragraph_breaks_the_editor_adds():
    """G11, 2026-10-06: the approved text had one newline between lines,
    the editor read back a paragraph (two) for each, and the byte diff was
    three blank lines -- three expired cards and a failed one. A paragraph
    break is a paragraph break once posted."""
    approved = ("Ever wonder if your VPN is really protecting remote workers?  \n"
                "Guardian validates every connection.  \n#ZeroTrust #RemoteWork")
    readback = ("Ever wonder if your VPN is really protecting remote workers?\n\n"
                "Guardian validates every connection.\n\n#ZeroTrust #RemoteWork")
    assert publish._readback_diff(approved, readback) is None


def test_normalise_still_catches_a_real_difference():
    approved = "MIA to BOG for less.\nhttps://flycheap.example.com\n#travel"
    readback = "MIA to BOG for MORE.\nhttps://flycheap.example.com\n#travel"
    diff = publish._readback_diff(approved, readback)
    assert diff is not None and "MORE" in diff and "for less" in diff


def test_the_diff_is_short_never_the_whole_post():
    approved = "\n".join(f"line {i}" for i in range(20))
    readback = "\n".join(f"LINE {i}" for i in range(20))
    diff = publish._readback_diff(approved, readback)
    # Two file-header lines plus at most 3 differing lines -- not all 20.
    assert diff.count("\n") <= 4


class _FakeLocator:
    def __init__(self, calls: list[str] | None = None) -> None:
        self.calls = calls if calls is not None else []

    @property
    def first(self) -> _FakeLocator:
        return self

    async def wait_for(self, *, state: str = "", timeout: int = 0) -> None:
        return None

    async def click(self) -> None:
        self.calls.append("clicked")


class _FakeKeyboard:
    async def type(self, text: str, delay: int | None = None) -> None:
        return None


class _FakeTab:
    def __init__(self, got_text: str) -> None:
        self._got = got_text
        self.keyboard = _FakeKeyboard()
        self.locator_calls: list[str] = []

    def locator(self, selector: str) -> _FakeLocator:
        self.locator_calls.append(selector)
        return _FakeLocator()

    async def evaluate(self, script: str) -> str:
        return self._got


class _FakeCtx:
    def __init__(self, tab: _FakeTab) -> None:
        self._tab = tab

    async def new_page(self) -> _FakeTab:
        return self._tab

    async def close(self) -> None:
        return None


class _FakeHandle:
    async def close(self) -> None:
        return None

    async def stop(self) -> None:
        return None


async def test_a_genuine_readback_mismatch_never_reaches_post(state, monkeypatch):
    """A fake browser through the actual `_publish` path: the readback is
    genuinely different text (no NBSP, no trailing whitespace -- a real
    mismatch), and the Post button must never be located, let alone clicked,
    and the entry must stay approved for the next attempt."""
    state(doc(APPROVED))
    tab = _FakeTab("something completely different, not the approved post")

    async def fake_browser():
        return _FakeHandle(), _FakeHandle(), _FakeCtx(tab)

    async def fake_goto(page, url, **kw):
        return ""

    async def fake_pause(page, *a, **kw):
        return None

    async def fake_snapshot(page, label):
        return "shot.png"

    monkeypatch.setattr(publish, "browser", fake_browser)
    monkeypatch.setattr(publish, "goto", fake_goto)
    monkeypatch.setattr(publish, "pause", fake_pause)
    monkeypatch.setattr(publish, "snapshot", fake_snapshot)

    r = await publish.publish("flycheap")
    assert not r.ok
    assert "did not read back the approved text" in r.detail
    assert not any("share-actions__primary-action" in c for c in tab.locator_calls)
    assert store.load()["improver"]["post_bank"][0]["status"] == "approved"


# -------------------------------------------------------------- approve_draft

async def test_approving_a_draft_and_rolling_it_back(state):
    state(doc(DRAFT))
    r = await publish.approve_draft("flycheap", "B14",
                                    text_sha256=publish.text_sha256(DRAFT["text"]))
    assert r.ok and r.rollback_token == "flycheap:B14"
    assert store.load()["improver"]["post_bank"][0]["status"] == "approved"
    back = await publish.unapprove_draft(r.rollback_token)
    assert back.ok
    assert store.load()["improver"]["post_bank"][0]["status"] == "draft"


async def test_only_a_draft_can_be_approved(state):
    state(doc(APPROVED))
    r = await publish.approve_draft("flycheap", "B13", text_sha256="whatever")
    assert not r.ok


async def test_a_stale_hash_is_refused_and_leaves_the_draft_untouched(state):
    """The sweep rewrote the draft after the card went out (id reuse after a
    lost write, in production). Approving with the hash the OLD card showed
    must refuse rather than post text Miro never saw."""
    state(doc(DRAFT))
    stale_hash = publish.text_sha256(DRAFT["text"])
    st = store.load()
    st["improver"]["post_bank"][0]["text"] = "Rewritten under the same id."
    store.save(st)

    r = await publish.approve_draft("flycheap", "B14", text_sha256=stale_hash)
    assert not r.ok
    assert "changed since you saw it" in r.detail
    assert "fresh card is coming" in r.detail
    entry = store.load()["improver"]["post_bank"][0]
    assert entry["status"] == "draft"
    assert entry["text"] == "Rewritten under the same id."


async def test_a_correct_hash_still_approves(state):
    state(doc(DRAFT))
    r = await publish.approve_draft("flycheap", "B14",
                                    text_sha256=publish.text_sha256(DRAFT["text"]))
    assert r.ok


async def test_a_stale_approval_is_refused_and_the_next_card_is_fresh(state):
    """After the refusal, the next `signals()` pass must offer a card with a
    title the scheduler has not seen and params carrying the new hash --
    not silently re-use the stale one, and not stay silent forever either."""
    state(doc(DRAFT))
    sig_before, = [s for s in await linkedin.MODULE.signals()
                   if s.params.get("entry_id") == "B14"]
    stale_hash = sig_before.params["text_sha256"]

    st = store.load()
    st["improver"]["post_bank"][0]["text"] = "Rewritten under the same id."
    store.save(st)

    r = await publish.approve_draft("flycheap", "B14", text_sha256=stale_hash)
    assert not r.ok

    sig_after, = [s for s in await linkedin.MODULE.signals()
                  if s.params.get("entry_id") == "B14"]
    assert sig_after.title != sig_before.title
    assert "rev 2" in sig_after.title
    assert sig_after.params["text_sha256"] != stale_hash


# -------------------------------------------------------------- signals

async def test_the_daily_post_is_offered_as_a_card(state):
    state(doc(APPROVED))
    sig = [s for s in await linkedin.MODULE.signals()
           if s.suggests == "linkedin.publish_post"]
    assert len(sig) == 1
    assert sig[0].params == {"page": "flycheap", "entry_id": "B13"}
    assert store.today() in sig[0].title


async def test_drafts_become_approval_cards(state):
    state(doc(DRAFT, {"id": "B15", "status": "draft", "text": "x https://flycheap.example.com"}))
    sig = [s for s in await linkedin.MODULE.signals()
           if s.suggests == "linkedin.approve_draft"]
    assert [s.params["entry_id"] for s in sig] == ["B14", "B15"]


async def test_nothing_is_offered_after_todays_post(state):
    state(doc(APPROVED, posting={"last_post": store.today()}))
    assert [s for s in await linkedin.MODULE.signals()
            if s.suggests == "linkedin.publish_post"] == []


# ---------------------------------------------------------------- sweep

async def test_a_stalled_sweep_is_visible(state):
    d = doc(APPROVED)
    d["last_run"] = (datetime.now(UTC) - timedelta(hours=40)).isoformat()
    state(d)
    obs = await linkedin.agent_run()
    assert obs.health is Health.FAILING and "last ran 40" in obs.detail


async def test_a_bad_outcome_degrades_even_when_recent(state):
    d = doc(APPROVED)
    d["last_run_result"] = {"outcome": "signed out: session expired"}
    state(d)
    obs = await linkedin.agent_run()
    assert obs.health is Health.DEGRADED and "signed out" in obs.detail


async def test_the_sweep_check_without_a_session_is_unknown(state, monkeypatch):
    state(doc(APPROVED))
    monkeypatch.setattr(browser, "SESSION", Path("/nonexistent/session.json"))
    obs = await linkedin.sweep_check()
    assert obs.health is Health.UNKNOWN and "no session" in obs.detail
    assert store.load()["runs"] == 2, "the attempt is still counted"


async def test_a_hard_stop_is_failing_and_recorded(state, monkeypatch):
    state(doc(APPROVED))

    class S(sweep.Sweep):
        async def run(self):
            raise browser.HardStop("LinkedIn said 'unusual activity'")

    monkeypatch.setattr(sweep, "Sweep", S)
    obs = await linkedin.sweep_check()
    assert obs.health is Health.FAILING
    assert store.load()["alerts"][-1]["what"].startswith("LinkedIn said")


async def test_an_unread_surface_is_degraded_not_ok(state, monkeypatch):
    state(doc(APPROVED))

    class S(sweep.Sweep):
        async def run(self):
            self.unread.append("messages")
            self.note("messages: could not read")

    monkeypatch.setattr(sweep, "Sweep", S)
    obs = await linkedin.sweep_check()
    assert obs.health is Health.DEGRADED and "messages" in obs.detail


def test_hard_stop_phrases_are_detected_but_not_their_denials():
    assert browser.scan_for_hard_stop("We've restricted your account") is not None
    assert browser.scan_for_hard_stop("Please verify it's you") is not None
    assert browser.scan_for_hard_stop("NOT a LinkedIn restriction/CAPTCHA/limit warning") is None
    assert browser.scan_for_hard_stop("Feed loaded, all good") is None


def test_refill_never_keeps_a_banned_link(monkeypatch):
    """Task 64: `draft_posts` now returns kind-tagged dicts, one of each of
    image/video/poll -- the banned-link filter must still drop a bad block
    and keep the good ones, whichever kind they carry."""
    class C:
        text = ("KIND: image\nTEXT: Good one https://flycheap.example.com #x\n"
                "IMAGE_PROMPT: a route line\n---\n"
                "KIND: video\nTEXT: Bad one https://flycheap.storefront.test\n"
                "VIDEO_PROMPT: a route line\n---\n"
                "KIND: poll\nINTRO: quick one\nQUESTION: which?\n"
                "OPTIONS: a | b")

    async def fake_think(*a, **k):
        return C()

    monkeypatch.setattr("atlas.kernel.llm.think", fake_think)
    import asyncio
    posts = asyncio.run(sweep.draft_posts(doc(APPROVED), "flycheap", 3))
    kinds = [p["kind"] for p in posts]
    assert kinds == ["image", "poll"]
    assert "storefront" not in json.dumps(posts)


# ------------------------------------------------------------- the Mac side

def test_the_mac_side_agent_is_gone_from_the_repo():
    root = Path(__file__).resolve().parent.parent
    for gone in ("bootstrap/com.myron.claude-linkedin-wake.plist",
                 "bootstrap/com.atlas.linkedin-state-sync.plist",
                 "bootstrap/sync-linkedin-state.sh"):
        assert not (root / gone).exists(), f"{gone} is the Mac-side path"
    assert (root / "bootstrap/linkedin-login.sh").exists()


async def test_one_bounced_surface_does_not_end_the_sweep(state, monkeypatch):
    """/messaging/ bounced to sign-in on the first server run while the
    feed, invites and notifications all loaded. One bounce is an unread
    surface, not a dead session."""
    state(doc(APPROVED))
    s = sweep.Sweep(store.load())

    async def fine(page):
        return None

    async def bounce(page):
        raise browser.SignedOut("sent to /login/")

    await s.surface(None, "invites", fine)
    await s.surface(None, "messages", bounce)
    await s.surface(None, "dashboard", fine)
    assert s.unread == ["messages"] and s.bounced == ["messages"] and s.read == 2


async def test_every_surface_bouncing_is_signed_out(state, monkeypatch):
    state(doc(APPROVED))

    class S(sweep.Sweep):
        async def run(self):
            self.bounced += ["a", "b"]
            self.unread += ["a", "b"]

    monkeypatch.setattr(sweep, "Sweep", S)
    obs = await linkedin.sweep_check()
    assert obs.health is Health.UNKNOWN and "signed out" in obs.detail


def test_names_are_normalised_so_the_ledger_holds_each_person_once():
    assert sweep.clean_name("Yohana ”John” Khair") == 'Yohana "John" Khair'
    assert sweep.clean_name("  Derek   J. ") == "Derek J."


async def test_a_recent_clean_sweep_is_not_repeated_on_restart(state, monkeypatch):
    d = doc(APPROVED)
    d["last_run_result"] = {"outcome": "ok"}
    state(d)

    async def must_not_run():
        raise AssertionError("the sweep must not run again so soon")

    monkeypatch.setattr(sweep, "run_sweep", must_not_run)
    obs = await linkedin.sweep_check()
    assert obs.health is Health.OK and "not on restart" in obs.detail


async def test_a_recent_failed_sweep_is_retried(state, monkeypatch):
    d = doc(APPROVED)
    d["last_run_result"] = {"outcome": "signed out: x"}
    state(d)
    ran = []

    async def fake_run():
        ran.append(1)
        return {"outcome": "ok", "news": [], "unread": [], "log": []}

    monkeypatch.setattr(sweep, "run_sweep", fake_run)
    await linkedin.sweep_check()
    assert ran == [1]


def test_cards_go_to_the_pages_channel_and_carry_the_text():
    for a in linkedin.MODULE.actions():
        if a.name in PAGE_REPLIES:
            # No `page` param to route on (draft/request ids only; invariant
            # 2); their receipts and alerts name the page's topic in code.
            continue
        assert a.topic_param == "page" and a.verbose_card


async def test_two_writers_cannot_overlap(state):
    """A sweep by hand and the scheduler's own overlapped once; the later
    save threw away three fresh drafts. The state lock is exclusive."""
    import asyncio

    state(doc(APPROVED))
    order: list[str] = []

    async def writer(tag: str, hold: float):
        async with store.file_lock(timeout_s=5):
            order.append(f"{tag}:in")
            await asyncio.sleep(hold)
            order.append(f"{tag}:out")

    await asyncio.gather(writer("a", 0.3), writer("b", 0.1))
    assert order in (["a:in", "a:out", "b:in", "b:out"],
                     ["b:in", "b:out", "a:in", "a:out"])


async def test_two_sweeps_over_the_same_draft_share_one_title(state):
    """The dedup key -- the signal's title, both in the scheduler's own 24h
    check and in `propose()`'s pending lookup -- is the entry id, not a
    hash of the text (replaces test_a_rewritten_draft_is_a_new_card, which
    asserted the opposite: that an untouched draft's title drifted between
    sweeps just because it embedded a hash of the text. That was the bug --
    two sweeps over the same still-unapproved draft could each mint their
    own title and so their own card)."""
    state(doc({"id": "B14", "status": "draft", "text": "one https://flycheap.example.com"}))
    t1 = next(s.title for s in await linkedin.MODULE.signals() if "draft" in s.title)
    t2 = next(s.title for s in await linkedin.MODULE.signals() if "draft" in s.title)
    assert t1 == t2 == "Approve draft B14 for FlyCheap"


async def test_a_rewrite_alone_does_not_change_the_title(state):
    """A text change by itself is not what earns a fresh card -- only a
    refused, stale approval bumps `card_rev` (see
    test_a_stale_approval_is_refused_and_the_next_card_is_fresh). Keying the
    title on the text would re-introduce the duplicate-card bug for every
    sweep that runs while a draft is merely being edited."""
    state(doc({"id": "B14", "status": "draft", "text": "one https://flycheap.example.com"}))
    t1 = next(s.title for s in await linkedin.MODULE.signals() if "draft" in s.title)
    state(doc({"id": "B14", "status": "draft", "text": "two https://flycheap.example.com"}))
    t2 = next(s.title for s in await linkedin.MODULE.signals() if "draft" in s.title)
    assert t1 == t2 == "Approve draft B14 for FlyCheap"


# --------------------------------------------------------- freezing drafts

async def test_a_pending_card_freezes_its_draft(state, monkeypatch):
    state(doc(DRAFT))
    s = sweep.Sweep(store.load())

    async def fake_pending(key):
        return {"B14"}

    monkeypatch.setattr(sweep, "pending_draft_ids", fake_pending)
    await s.refill("flycheap")
    entries, _, _ = store.bank(s.state, "flycheap")
    e, = [x for x in entries if x["id"] == "B14"]
    assert e["frozen"] is True


async def test_a_draft_with_no_pending_card_is_not_frozen(state, monkeypatch):
    state(doc(DRAFT))
    s = sweep.Sweep(store.load())

    async def fake_pending(key):
        return set()

    monkeypatch.setattr(sweep, "pending_draft_ids", fake_pending)
    await s.refill("flycheap")
    entries, _, _ = store.bank(s.state, "flycheap")
    e, = [x for x in entries if x["id"] == "B14"]
    assert e["frozen"] is False


async def test_a_failed_pending_read_freezes_every_draft(state, monkeypatch):
    """Silence is never consent: a query that could not run must never be
    read as 'nothing is pending'."""
    state(doc(DRAFT))
    s = sweep.Sweep(store.load())

    async def boom(key):
        return None

    monkeypatch.setattr(sweep, "pending_draft_ids", boom)
    await s.refill("flycheap")
    entries, _, _ = store.bank(s.state, "flycheap")
    e, = [x for x in entries if x["id"] == "B14"]
    assert e["frozen"] is True
    assert any("could not read pending" in ln for ln in s.log)


async def test_refill_never_reuses_a_frozen_drafts_id(state, monkeypatch):
    """B14 has a pending card and an empty bank otherwise, so refill fires.
    Even though B14 is not itself in `entries` here (removed by hand, say),
    a freshly drafted post must not be handed its id -- a pending approval
    would then apply to different words than the ones on the card."""
    state(doc())
    s = sweep.Sweep(store.load())

    async def fake_pending(key):
        return {"B14"}

    async def fake_draft_posts(state, key, count):
        return [{"kind": "poll",
                 "poll": {"intro": "fresh one", "question": "q?",
                          "options": ["a", "b"], "duration": "1w",
                          "visibility": "public"}}]

    monkeypatch.setattr(sweep, "pending_draft_ids", fake_pending)
    monkeypatch.setattr(sweep, "draft_posts", fake_draft_posts)
    await s.refill("flycheap")
    entries, _, _ = store.bank(s.state, "flycheap")
    assert all(e["id"] != "B14" for e in entries)
    assert len(entries) == 1 and entries[0]["id"] == "B1"


# --------------------------------------------------- module import hygiene

def test_linkedin_module_never_imports_the_executor():
    """Invariant 1: only kernel/executor.py may act. A module with an
    import path to it would be a second way to reach the outside world."""
    import ast

    root = Path(__file__).resolve().parent.parent / "atlas" / "modules" / "linkedin"
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if node.level:
                    mod = f"atlas.{mod}" if mod else "atlas"
                names = [mod]
            for name in names:
                assert not name.startswith("atlas.kernel.executor"), \
                    f"{path.name} imports the executor via {name!r}"


def test_publishing_runs_in_the_background_so_a_click_is_answered_at_once():
    """2026-09-28: run inline, a minutes-long post outlasted Slack's 3-second
    button window, the card was clicked twice, and a deploy mid-run left #181
    and #182 at 'approved' forever with nothing posted."""
    actions = {a.name: a for a in linkedin.MODULE.actions()}
    assert actions["linkedin.publish_post"].runs_in_background is True
    assert actions["linkedin.approve_draft"].runs_in_background is False


def test_the_post_selector_can_only_mean_the_composers_post_button():
    """2026-09-28, read off the live composer: `button:has-text('Post')`
    first matched the audience button 'FlyCheap · Post to Anyone', so 'Post'
    opened settings and B5 and G8 were never published."""
    sel = publish.POST_BUTTON_SELECTOR
    parts = [s.strip() for s in sel.split(",")]
    assert all(p.startswith("[role='dialog']") for p in parts)
    assert "has-text('Post')" not in sel
    assert any("share-actions__primary-action" in p for p in parts)


async def test_post_waits_for_the_button_to_enable_and_refuses_if_it_never_does():
    class Btn:
        def __init__(self, enable_after):
            self.n, self.enable_after, self.clicked = 0, enable_after, False

        async def wait_for(self, **k):
            return None

        async def is_enabled(self):
            self.n += 1
            return self.n > self.enable_after

        async def click(self):
            self.clicked = True

    class Loc:
        def __init__(self, btn):
            self.first = btn

    class Tab:
        def __init__(self, btn):
            self.btn = btn

        def locator(self, sel):
            return Loc(self.btn)

        async def wait_for_timeout(self, ms):
            return None

    ok = Btn(enable_after=3)
    await publish._click_post(Tab(ok))
    assert ok.clicked
    never = Btn(enable_after=10_000)
    with pytest.raises(RuntimeError, match="never became enabled"):
        await publish._click_post(Tab(never))
    assert not never.clicked


# ------------------------------------------------------------- reshare

PUBLISHED = {"id": "B5", "status": "published", "published": None,
             "activity_url": "https://www.linkedin.com/feed/update/urn:li:share:1",
             "reshared": False, "text": "A team of four flew Boston to Austin"}


def test_a_reshare_is_tier_two_and_irreversible():
    a = {x.name: x for x in linkedin.MODULE.actions()}["linkedin.reshare_post"]
    assert a.tier is Tier.ASK and a.irreversible and a.runs_in_background


async def test_the_reshare_dry_run_says_exactly_what_it_would_do(state):
    state(doc(PUBLISHED))
    r = await publish.reshare_dry("flycheap", "B5")
    assert r.ok and "would reshare B5" in r.detail and "urn:li:share:1" in r.detail


@pytest.mark.parametrize("entry, why", [
    ({**PUBLISHED, "reshared": True}, "already reshared"),
    ({**PUBLISHED, "status": "approved"}, "not a published post"),
    ({**PUBLISHED, "activity_url": None}, "not a published post"),
])
async def test_a_reshare_is_refused_when_there_is_nothing_to_reshare(state, entry, why):
    state(doc(entry))
    r = await publish.reshare_dry("flycheap", "B5")
    assert not r.ok and why in r.detail


async def test_a_post_whose_reshare_did_not_verify_gets_a_reshare_card(state):
    """2026-09-28: 'I'll retry it next sweep' was a promise nothing kept."""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    state(doc({**PUBLISHED, "published": today}))
    sig = [s for s in await linkedin.MODULE.signals()
           if s.suggests == "linkedin.reshare_post"]
    assert len(sig) == 1 and sig[0].params == {"page": "flycheap", "entry_id": "B5"}
    state(doc({**PUBLISHED, "published": "2020-01-01"}))
    assert [s for s in await linkedin.MODULE.signals()
            if s.suggests == "linkedin.reshare_post"] == []


def test_the_receipt_no_longer_promises_a_retry_nothing_performs():
    import inspect
    assert "retry it next sweep" not in inspect.getsource(publish)


async def test_the_post_button_is_found_in_order_and_the_first_visible_wins():
    """The page composer is a dialog; the member reshare composer (newer
    layout) is not, and nests 'Post' in a span, so only the accessible name
    finds its button."""
    tried: list[str] = []

    class Loc:
        def __init__(self, sel):
            self.sel = sel

        @property
        def first(self):
            return self

        async def wait_for(self, **k):
            tried.append(self.sel)
            if self.sel != "role:button:Post:exact":
                raise TimeoutError("not here")

    class Tab:
        def locator(self, sel):
            return Loc(sel)

        def get_by_role(self, role, *, name, exact):
            assert (role, name, exact) == ("button", "Post", True)
            return Loc("role:button:Post:exact")

    btn = await publish._post_button(Tab())
    assert btn.sel == "role:button:Post:exact"
    assert tried == [*publish.POST_BUTTON_SELECTORS, "role:button:Post:exact"]
    assert all("has-text" not in s for s in publish.POST_BUTTON_SELECTORS)


class _ActivityTab:
    """Miro's activity, empty for the first `lag` reads (the feed lags)."""

    def __init__(self, lag: int, text: str = "A team of four flew Boston to Austin"):
        self.lag, self.text, self.reads = lag, text, 0

    async def evaluate(self, js):
        self.reads += 1
        return self.text if self.reads > self.lag else "nothing new yet"


@pytest.fixture
def quiet_browser(monkeypatch):
    async def fake_goto(page, url, **kw):
        return ""

    async def fake_pause(page, *a, **kw):
        return None

    monkeypatch.setattr(publish, "goto", fake_goto)
    monkeypatch.setattr(publish, "pause", fake_pause)


async def test_the_activity_is_read_again_when_the_feed_lags(quiet_browser):
    """G8, 2026-09-28: the repost went out, the one early read missed it."""
    tab = _ActivityTab(lag=2)
    assert await publish._seen_on_activity(tab, PUBLISHED["text"])
    assert tab.reads == 3
    never = _ActivityTab(lag=99)
    assert not await publish._seen_on_activity(never, PUBLISHED["text"])
    assert never.reads == publish.ACTIVITY_LOOKS


async def test_a_clicked_reshare_that_never_shows_is_unverified_not_failed(
        quiet_browser, monkeypatch):
    async def click(tab):
        return None

    async def no_button(tab):
        raise RuntimeError("no Post button in the composer")

    monkeypatch.setattr(publish, "_click_post", click)
    ok, why = await publish._reshare(_ActivityTab(lag=99), "https://x", PUBLISHED["text"])
    assert ok == publish.UNVERIFIED and "Post clicked" in why
    monkeypatch.setattr(publish, "_click_post", no_button)
    ok, why = await publish._reshare(_ActivityTab(lag=0), "https://x", PUBLISHED["text"])
    assert ok is False and "no Post button" in why


async def test_an_unverified_reshare_is_recorded_and_never_offered_again(
        state, monkeypatch):
    """A second click on a repost that may be live is a duplicate post."""
    import contextlib

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    state(doc({**PUBLISHED, "published": today}))

    @contextlib.asynccontextmanager
    async def no_lock():
        yield

    async def fake_browser():
        return _FakeHandle(), _FakeHandle(), _FakeCtx(object())

    async def fake_reshare(tab, activity, text):
        return publish.UNVERIFIED, "Post clicked, but not on Miro's recent activity"

    async def keep(ctx):
        return None

    monkeypatch.setattr(publish, "lock", no_lock)
    monkeypatch.setattr(publish, "browser", fake_browser)
    monkeypatch.setattr(publish, "_reshare", fake_reshare)
    monkeypatch.setattr(publish, "keep_session", keep)

    r = await publish.reshare("flycheap", "B5")
    assert not r.ok and "may be live" in r.detail
    assert store.load()["improver"]["post_bank"][0]["reshared"] == publish.UNVERIFIED
    assert [s for s in await linkedin.MODULE.signals()
            if s.suggests == "linkedin.reshare_post"] == []
    again = await publish.reshare_dry("flycheap", "B5")
    assert not again.ok and "check Miro's activity" in again.detail
