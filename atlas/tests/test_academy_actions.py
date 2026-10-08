"""ATLAS Academy's two actions and its check.

`academy.resend_access` emails a buyer their access link: Tier 2, never
Tier 1 (an email can't be unsent), with a dry run that sends nothing, to
the address Stripe holds and never one given as a parameter.
`academy.publish_shop` puts the storefront live as a Mapfront release, so
on-demand TLS will issue shop.mapfront.io a certificate.
"""
from __future__ import annotations

import copy
import json
import os

import pytest

from atlas.kernel.contracts import Health, Tier
from atlas.kernel.permissions import classify
from atlas.modules import academy
from atlas.modules.academy import catalog as cat_mod
from atlas.modules.academy import publish, resend, stripe_read
from atlas.modules.agency import hosting

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
    # Miro, 2026-09-30: "anything that is not urgent, you approve it".
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

    async def smtp(sender, to, subject, body, message_id=None):
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
    return pub


async def test_the_publish_dry_run_writes_nothing_public(roots, monkeypatch):
    cat = cat_mod.parse(_off_raw())
    monkeypatch.setattr(publish, "load", lambda: cat, raising=False)
    monkeypatch.setattr(academy, "load", lambda: cat)
    out = await publish.publish_shop_dry()
    assert out.ok and "shop.mapfront.io" in out.detail
    assert "nothing yet" in out.detail
    assert not (roots / "shop").exists()


async def test_publish_makes_a_release_link_that_tls_will_accept(roots):
    assert not hosting.tls_allowed("shop.mapfront.io", roots)
    out = await publish.publish_shop()
    assert out.ok, out.detail
    assert (roots / "shop").is_symlink()
    assert (roots / "shop" / "index.html").is_file()
    assert (roots / "shop" / "catalog.json").is_file()
    assert hosting.tls_allowed("shop.mapfront.io", roots)
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
