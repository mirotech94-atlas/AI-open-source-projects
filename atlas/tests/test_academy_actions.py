"""ATLAS Academy's two actions and its check.

`academy.resend_access` emails a buyer their access link: Tier 2, never
Tier 1 (an email can't be unsent), with a dry run that sends nothing, to
the address Stripe holds and never one given as a parameter.
`academy.publish_shop` puts the storefront live as a release link under the
publish root, served at agency.example.com.
"""
from __future__ import annotations

import copy
import json
import os

import pytest

from atlas.kernel import idempotency
from atlas.kernel.contracts import Health, Tier
from atlas.kernel.permissions import classify
from atlas.modules import academy
from atlas.modules.academy import catalog as cat_mod
from atlas.modules.academy import publish, resend, stripe_read
from atlas.modules.academy import tutorial_series as ts
from atlas.modules.academy import tutorials as tut
from atlas.modules.agency import hosting
from atlas.modules.youtube import api as yt

SID = "cs_live_a1B2c3D4e5F6g7H8i9J0"


def _actions():
    return {a.name: a for a in academy.MODULE.actions()}


# ------------------------------------------------------------------ tiers

def test_resend_access_is_tier_2_irreversible_with_a_dry_run():
    a = _actions()["academy.resend_access"]
    assert a.tier is Tier.ASK and a.irreversible and a.dry_run is not None
    assert a.rollback is None
    decision = classify(a)
    assert decision.allowed and decision.tier is Tier.ASK
    assert set(a.params) == {"session_id"}


def test_resend_access_can_never_be_made_tier_1():
    from atlas.kernel.contracts import Action
    with pytest.raises(ValueError):
        Action(name="academy.resend_access", tier=Tier.AUTO, summary="x",
               run=resend.resend_access, dry_run=resend.resend_access_dry,
               irreversible=True)


def test_publish_shop_is_tier_1_with_a_dry_run_and_a_rollback():
    # the owner, 2026-09-30: "anything that is not urgent, you approve it".
    a = _actions()["academy.publish_shop"]
    assert a.tier is Tier.AUTO and a.dry_run is not None and a.rollback is not None
    assert classify(a).allowed


@pytest.mark.parametrize("bad", ["", "cs_live_x", "../x", "pi_123", None,
                                 "cs_live_a1B2c3D4e5; DROP TABLE"])
def test_a_card_is_never_raised_for_a_malformed_session(bad):
    assert resend.vet({"session_id": bad}) is not None
    assert resend.vet({"session_id": SID}) is None


# ------------------------------------------------------------------ resend

def _verdict(status="paid", email="buyer@example.com"):
    cat = cat_mod.load()
    return stripe_read.Verdict(status, SID, offers=(cat.offers()[2],),
                               amount_minor=1900, currency="usd", email=email)


@pytest.fixture
def mail(monkeypatch):
    sent: list = []

    async def smtp(sender, to, subject, body, message_id=None, html=False):
        sent.append((sender, to, subject, body))
        return True, None
    monkeypatch.setattr("atlas.modules.agency.outreach._smtp_send", smtp)

    async def audit(*_a, **_kw):
        return None
    monkeypatch.setattr("atlas.kernel.audit.audit", audit)
    return sent


def _stripe_says(monkeypatch, verdict):
    async def fake(session_id, cat, **_kw):
        return verdict
    monkeypatch.setattr(resend, "read_session", fake)


async def test_the_dry_run_names_the_recipient_and_sends_nothing(monkeypatch, mail):
    _stripe_says(monkeypatch, _verdict())
    out = await resend.resend_access_dry(SID)
    assert out.ok and "bu***@example.com" in out.detail
    assert "Claude + Obsidian Vault Kit" in out.detail
    assert "buyer@example.com" not in out.detail
    assert mail == []


async def test_it_sends_the_access_link_to_the_address_stripe_holds(monkeypatch, mail):
    _stripe_says(monkeypatch, _verdict())
    out = await resend.resend_access(SID)
    assert out.ok
    [(sender, to, subject, body)] = mail
    assert to == "buyer@example.com" and "@" in sender
    assert f"/public/academy/access?session_id={SID}" in body
    assert subject == resend.SUBJECT


@pytest.mark.parametrize("status", ["unpaid", "refunded", "disputed", "unknown_price",
                                    "unconfigured", "unreachable", "not_found"])
async def test_nothing_is_sent_unless_stripe_says_paid(monkeypatch, mail, status):
    _stripe_says(monkeypatch, _verdict(status))
    assert not (await resend.resend_access_dry(SID)).ok
    assert not (await resend.resend_access(SID)).ok
    assert mail == []


async def test_no_email_on_the_checkout_means_no_send(monkeypatch, mail):
    _stripe_says(monkeypatch, _verdict(email=""))
    assert not (await resend.resend_access(SID)).ok
    assert mail == []


# ------------------------------------------------------------------ publish

@pytest.fixture
def roots(monkeypatch, tmp_path):
    pub = tmp_path / "sites"
    monkeypatch.setattr(hosting, "PUBLISH_ROOT", pub)
    monkeypatch.setenv(publish.BUILD_ENV, str(tmp_path / "build"))
    monkeypatch.delenv("AGENCY_DEPLOY_HOST", raising=False)

    async def no_videos():
        return []
    monkeypatch.setattr(publish, "public_videos", no_videos)
    return pub


async def test_the_publish_dry_run_writes_nothing_public(roots, monkeypatch):
    cat = cat_mod.parse(_off_raw())
    monkeypatch.setattr(publish, "load", lambda: cat, raising=False)
    monkeypatch.setattr(academy, "load", lambda: cat)
    out = await publish.publish_shop_dry()
    assert out.ok and "agency.example.com" in out.detail
    assert "nothing yet" in out.detail
    assert not (roots / "shop").exists()


async def test_publish_makes_a_release_link(roots):
    out = await publish.publish_shop()
    assert out.ok, out.detail
    assert (roots / "shop").is_symlink()
    assert (roots / "shop" / "academy" / "index.html").is_file()
    assert (roots / "shop" / "academy" / "catalog.json").is_file()
    first = json.loads(out.rollback_token)
    assert first["previous"] is None

    second = await publish.publish_shop()
    assert second.ok
    token = json.loads(second.rollback_token)
    assert token["previous"] == first["release"]
    back = await publish.publish_shop_rollback(second.rollback_token)
    assert back.ok and hosting.live_release(roots, "shop") == first["release"]
    # Rolling the second back again refuses: it is no longer the live one.
    assert not (await publish.publish_shop_rollback(second.rollback_token)).ok


async def test_publish_moves_the_served_link_and_rollback_moves_it_back(roots):
    """The shop container serves <build root>/current, so a publish that
    only wrote the publish root would never go live."""
    build = publish.build_root()
    first = await publish.publish_shop()
    t1 = json.loads(first.rollback_token)["release"]
    assert os.readlink(build / "current") == t1
    assert (build / "current" / "academy" / "index.html").is_file()
    second = await publish.publish_shop()
    t2 = json.loads(second.rollback_token)["release"]
    assert os.readlink(build / "current") == t2
    assert (build / t1).is_dir()            # kept for the rollback
    assert (await publish.publish_shop_rollback(second.rollback_token)).ok
    assert os.readlink(build / "current") == t1


async def test_publish_refuses_to_replace_a_directory_it_did_not_make(roots):
    (roots / "shop").mkdir(parents=True)
    out = await publish.publish_shop()
    assert not out.ok and "not publishing over" in out.detail


async def test_publish_refuses_when_the_build_would_be_public(roots, monkeypatch):
    monkeypatch.setenv(publish.BUILD_ENV, str(roots / "build"))
    out = await publish.publish_shop_dry()
    assert not out.ok and "overlaps" in out.detail


async def test_the_first_publish_rolls_back_to_nothing(roots):
    out = await publish.publish_shop()
    back = await publish.publish_shop_rollback(out.rollback_token)
    assert back.ok and not os.path.lexists(roots / "shop")
    assert not os.path.lexists(publish.build_root() / "current")


# ------------------------------------------------------------------ the check

def _off_raw() -> dict:
    """The checked-in catalog with nothing on sale: the baseline these
    tests reason from, whatever is live in the real file."""
    raw = copy.deepcopy(json.loads(cat_mod.CATALOG_PATH.read_text()))
    for p in raw["products"]:
        p["refund"] = None
        for o in p["offers"]:
            o["payment_link"] = ""
            o["stripe_price_id"] = ""
    raw["bundle"]["payment_link"] = ""
    raw["bundle"]["stripe_price_id"] = ""
    return raw


def _off_catalog(monkeypatch):
    cat = cat_mod.parse(_off_raw())
    monkeypatch.setattr(academy, "load", lambda: cat)
    return cat


def _live_catalog(monkeypatch):
    raw = _off_raw()
    o = raw["products"][2]["offers"][0]
    o["payment_link"] = "https://buy.stripe.com/test_abc"
    o["stripe_price_id"] = "price_KIT123456"
    raw["products"][2]["refund"] = "Test terms."
    cat = cat_mod.parse(raw)
    monkeypatch.setattr(academy, "load", lambda: cat)
    return cat


async def test_nothing_on_sale_is_ok_and_says_so(monkeypatch, tmp_path):
    _off_catalog(monkeypatch)
    monkeypatch.setenv("ACADEMY_FILES_DIR", str(tmp_path))
    obs = await academy.check_delivery()
    assert obs.health is Health.OK and "nothing on sale yet" in obs.detail


async def test_an_item_on_sale_without_a_key_or_its_file_is_failing(monkeypatch,
                                                                    tmp_path):
    _live_catalog(monkeypatch)
    monkeypatch.setenv("ACADEMY_FILES_DIR", str(tmp_path))
    monkeypatch.delenv(stripe_read.KEY_ENV, raising=False)
    obs = await academy.check_delivery()
    assert obs.health is Health.FAILING
    assert stripe_read.KEY_ENV in obs.detail and "vault-kit-zip" in obs.detail


async def test_an_item_on_sale_with_key_and_file_is_ok(monkeypatch, tmp_path):
    _live_catalog(monkeypatch)
    (tmp_path / "vault-kit").mkdir()
    (tmp_path / "vault-kit" / "claude-obsidian-vault-kit.zip").write_bytes(b"PK")
    monkeypatch.setenv("ACADEMY_FILES_DIR", str(tmp_path))
    monkeypatch.setenv(stripe_read.KEY_ENV, "rk_test_x")
    monkeypatch.setenv("ACADEMY_DOWNLOAD_KEY", "k")
    obs = await academy.check_delivery()
    assert obs.health is Health.OK and "Claude + Obsidian Vault Kit" in obs.detail


async def test_an_unreadable_catalog_is_unknown_never_ok(monkeypatch):
    def broken():
        raise cat_mod.CatalogError("bad")
    monkeypatch.setattr(academy, "load", broken)
    obs = await academy.check_delivery()
    assert obs.health is Health.UNKNOWN


# ------------------------------------------- the pinned comment (task 6)
#
# 2026-10-13 (growth plan, task 6): the quiz and the free lab file go in a
# comment under a public tutorial. Tier 1 by the owner's standing policy, so it
# has a real dry run and a real rollback -- one that edits the comment to
# "(withdrawn)", never deletes it (deletion is Tier 3).


def test_the_pinned_comment_is_tier_1_with_the_quiz_and_the_lab():
    a = _actions()["academy.pin_comment"]
    assert a.tier is Tier.AUTO and not a.card_only
    assert a.dry_run is not None and a.rollback is not None and a.vet is not None
    assert set(a.params) == {"series", "episode"}
    assert classify(a).allowed
    s = ts.series("claude")
    ep = s.episodes[1]
    text = tut.pinned_comment(s, ep)
    assert "comment your answer." in text
    assert "https://agency.example.com/academy/free/" in text
    assert "Free lab file (no sign-up): " in text and "A. " in text


def test_the_teaching_short_render_is_tier_1_with_a_dry_run_and_rollback():
    """2026-10-13 (growth plan task 7): a teaching Short is a file on our own
    disk and a content_media row; posting it is clips.autopublish_promo's."""
    a = _actions()["academy.render_teaching_short"]
    assert a.tier is Tier.AUTO and not a.card_only
    assert a.dry_run is not None and a.rollback is not None and a.vet is not None
    assert set(a.params) == {"series", "episode", "kind"}
    assert a.runs_in_background and a.max_per_hour == 3
    assert classify(a).allowed
    assert a.vet({"series": "claude", "episode": 1, "kind": "reel"})
    assert a.vet({"series": "claude", "episode": 1, "kind": "stuck"}) is None


async def test_the_teaching_short_rollback_withdraws_the_render(monkeypatch, tmp_path):
    from atlas.modules import content
    from atlas.modules.academy import reel
    sql = []

    async def execute_sql(q, *a):
        sql.append((q, a))
    monkeypatch.setattr(content, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(reel, "execute_sql", execute_sql)
    (tmp_path / "41.mp4").write_bytes(b"x")
    r = await _actions()["academy.render_teaching_short"].rollback("41")
    assert r.ok and not (tmp_path / "41.mp4").exists()
    assert sql == [("UPDATE content_media SET status = 'failed' WHERE id = $1", (41,))]


@pytest.fixture
def comment(monkeypatch):
    state = {"posted": [], "updated": [], "sql": [], "claimed": [],
             "claim": idempotency.CLAIMED,
             "row": {"video_id": "abcdefghijk", "public": True},
             "comment_row": {"id": 3, "comment_id": "Ugx1", "video_id": "abcdefghijk"}}

    async def posted(series, episode):
        return state["row"]

    async def post_comment(ch, vid, text):
        state["posted"].append((vid, text))
        return {"id": "Ugx1"}, None

    async def update_comment(ch, cid, text):
        state["updated"].append((cid, text))
        return {"id": cid}, None

    async def claim(action, key):
        state["claimed"].append((action, key))
        return state["claim"]

    async def settle(*a, **k):
        return None

    async def fetch_one(sql, *args):
        state["sql"].append((sql, args))
        return state["comment_row"] if "SELECT" in sql else {"id": 3}

    async def execute_sql(sql, *args):
        state["sql"].append((sql, args))

    monkeypatch.setattr(tut, "posted", posted)
    monkeypatch.setattr(yt, "post_comment", post_comment)
    monkeypatch.setattr(yt, "update_comment", update_comment)
    monkeypatch.setattr(idempotency, "claim", claim)
    monkeypatch.setattr(idempotency, "settle", settle)
    monkeypatch.setattr("atlas.kernel.state.fetch_one", fetch_one)
    monkeypatch.setattr("atlas.kernel.state.execute_sql", execute_sql)
    return state


async def test_the_dry_run_says_the_text_and_the_video_and_posts_nothing(comment):
    r = await tut.pin_comment_dry("claude", 1)
    assert r.ok and "abcdefghijk" in r.detail
    assert tut.pinned_comment(ts.series("claude"), ts.series("claude").episodes[1]) in r.detail
    assert comment["posted"] == [] and comment["claimed"] == []


async def test_pin_comment_posts_once_and_records_the_comment(comment):
    r = await tut.pin_comment("claude", 1)
    assert r.ok, r.detail
    assert r.detail == "posted comment Ugx1; pin it in Studio (no API for pinning)"
    assert comment["claimed"] == [(tut.ACTION_PIN, "pin:claude:1:abcdefghijk")]
    assert [vid for vid, _ in comment["posted"]] == ["abcdefghijk"]
    insert = next(a for sql, a in comment["sql"] if "academy_tutorial_comments" in sql)
    assert insert[:4] == ("claude", 1, "abcdefghijk", "Ugx1")
    undo = json.loads(r.rollback_token)
    assert undo == {"series": "claude", "episode": 1, "video_id": "abcdefghijk",
                    "comment_id": "Ugx1", "channel": tut.channel()}


async def test_a_second_pin_for_the_same_video_is_refused(comment):
    comment["claim"] = idempotency.SENT
    r = await tut.pin_comment("claude", 1)
    assert not r.ok and comment["posted"] == []


async def test_nothing_is_posted_under_a_video_that_is_not_public(comment):
    comment["row"] = {"video_id": "abcdefghijk", "public": False}
    assert not (await tut.pin_comment("claude", 1)).ok
    assert not (await tut.pin_comment_dry("claude", 1)).ok
    assert comment["posted"] == []


async def test_the_rollback_edits_the_comment_and_never_deletes(comment):
    r = await tut.unpin_comment(json.dumps({"series": "claude", "episode": 1,
                                            "comment_id": "Ugx1"}))
    assert r.ok, r.detail
    assert comment["updated"] == [("Ugx1", "(withdrawn)")]
    assert any("withdrawn = true" in sql for sql, _ in comment["sql"])
    assert not any(n.startswith("delete") for n in dir(yt) if "comment" in n)
    assert not any("DELETE" in sql for sql, _ in comment["sql"])


async def test_the_rollback_of_a_comment_whose_row_failed_still_withdraws_it(comment):
    """Final review M3: pin_comment settles even when the row's INSERT fails,
    so the rollback works from the token: it edits the comment and records a
    withdrawn row, so the video is never posted under again."""
    comment["comment_row"] = None
    r = await tut.unpin_comment(json.dumps({"series": "claude", "episode": 1,
                                            "video_id": "abcdefghijk",
                                            "comment_id": "Ugx1", "channel": "clips"}))
    assert r.ok, r.detail
    assert comment["updated"] == [("Ugx1", "(withdrawn)")]
    insert = next(a for sql, a in comment["sql"]
                  if "INSERT INTO academy_tutorial_comments" in sql)
    assert insert[:4] == ("claude", 1, "abcdefghijk", "Ugx1")
    assert not any("DELETE" in sql for sql, _ in comment["sql"])


async def test_a_token_without_its_video_still_withdraws_and_says_no_row(comment):
    comment["comment_row"] = None
    r = await tut.unpin_comment(json.dumps({"comment_id": "Ugx1"}))
    assert r.ok and "no row recorded" in r.detail, r.detail
    assert comment["updated"] == [("Ugx1", "(withdrawn)")]
    assert not any("INSERT" in sql for sql, _ in comment["sql"])


async def test_a_comment_already_withdrawn_is_not_edited_again(comment):
    comment["comment_row"] = {"id": 3, "comment_id": "Ugx1", "video_id": "abcdefghijk",
                              "withdrawn": True}
    r = await tut.unpin_comment(json.dumps({"comment_id": "Ugx1"}))
    assert r.ok and "already" in r.detail
    assert comment["updated"] == []
    assert not any("UPDATE" in sql or "INSERT" in sql for sql, _ in comment["sql"])


async def test_the_rollback_finds_the_comment_by_its_id(comment):
    await tut.unpin_comment(json.dumps({"comment_id": "Ugx1"}))
    select = next(a for sql, a in comment["sql"] if "SELECT" in sql)
    assert select == ("Ugx1",)


async def test_a_failed_row_still_settles_the_claim(comment, monkeypatch):
    settled = []

    async def settle(action, key, detail=""):
        settled.append(key)

    async def fetch_one(sql, *args):
        raise RuntimeError("db down")
    monkeypatch.setattr(idempotency, "settle", settle)
    monkeypatch.setattr("atlas.kernel.state.fetch_one", fetch_one)
    with pytest.raises(RuntimeError):
        await tut.pin_comment("claude", 1)
    assert settled == ["pin:claude:1:abcdefghijk"]       # never posted twice


async def test_an_unknown_outcome_keeps_the_claim(comment, monkeypatch):
    finished = []

    async def post_comment(ch, vid, text):
        return None, "timed out after 25s"

    async def finish(action, key, reason, detail=""):
        finished.append(idempotency.uncertain(reason))
    monkeypatch.setattr(yt, "post_comment", post_comment)
    monkeypatch.setattr(idempotency, "finish", finish)
    assert not (await tut.pin_comment("claude", 1)).ok
    assert finished == [True]


async def test_a_withdrawn_comment_still_counts_as_one(monkeypatch):
    """Fix round 2: a rolled-back episode is never posted under again, so it
    cannot hold the day's slots: the check matches any comment row for the
    video, withdrawn or not."""
    seen = []

    async def fetch_all(sql, *args):
        seen.append(" ".join(sql.split()))
        return []
    monkeypatch.setattr("atlas.kernel.state.fetch_all", fetch_all)
    assert await tut.unpinned() == []
    sql = seen[0]
    inner = sql[sql.index("NOT EXISTS"):]
    assert "c.video_id = p.video_id" in inner and "withdrawn" not in inner

