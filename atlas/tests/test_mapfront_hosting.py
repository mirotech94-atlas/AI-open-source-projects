"""Mapfront hosting: `agency.deploy_site`'s local target and Caddy's TLS ask.

A site is published on this box under `hosting.PUBLISH_ROOT` (one release
per deploy, made live by swapping a relative symlink) and served by Caddy
at `https://<slug>.mapfront.io`. Everything here runs on `tmp_path`; the
store is monkeypatched, nothing touches a socket.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from atlas.modules.agency import _store, build, deploy, hosting


def _async_returns(value):
    async def fake(*args, **kwargs):
        return value
    return fake


def _ready_site(monkeypatch, site_dir: Path, heading: str = "Pipes, fixed") -> Path:
    """A built site with no launch-check ISSUE, made the way build_site does."""
    from atlas.modules.agency.client import ClientData
    from atlas.modules.agency.launch import finish_site
    monkeypatch.delenv("POSTHOG_PUBLIC_KEY", raising=False)
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "index.html").write_text(
        "<!doctype html><html><head><title>Joe's Plumbing</title></head><body>"
        f"<section id=\"hero\"><h1>{heading}</h1></section></body></html>")
    finish_site(site_dir, ClientData(name="Joe's Plumbing", phone="555-0100",
                                     address="1 Main St, Astoria"),
                analytics=None, form_action=None)
    return site_dir


@pytest.fixture
def local(monkeypatch, tmp_path):
    """Local target, publish root and build dir both under tmp_path."""
    monkeypatch.delenv("AGENCY_DEPLOY_HOST", raising=False)
    monkeypatch.delenv("AGENCY_SITES_DOMAIN", raising=False)
    root = tmp_path / "publish"
    monkeypatch.setattr(hosting, "PUBLISH_ROOT", root)
    monkeypatch.setattr(build, "SITES_DIR", tmp_path / "builds")
    calls: list[tuple] = []

    async def deployed(slug, previous):
        calls.append(("deployed", slug, previous))

    async def undeployed(slug):
        calls.append(("undeployed", slug))
    monkeypatch.setattr(_store, "set_site_deployed", deployed)
    monkeypatch.setattr(_store, "set_site_undeployed", undeployed)
    return root, calls


def _serve(monkeypatch, tmp_path, slug="joes-7", heading="Pipes, fixed") -> Path:
    site = _ready_site(monkeypatch, tmp_path / "builds" / slug, heading)
    monkeypatch.setattr(_store, "get_site",
                        _async_returns({"slug": slug, "path": str(site)}))
    return site


# ------------------------------------------------------------------ slugs

@pytest.mark.parametrize("slug", ["joes-7", "a", "7", "harbor-dental-12",
                                  "x" * 40, "a-b-c"])
def test_valid_slugs(slug):
    assert hosting.slug_problem(slug) is None


@pytest.mark.parametrize("slug", [
    "", "Joes", "-joes", "joes-", "x" * 41, "joes.evil", "../etc", "..", ".",
    "joes/7", "joes_7", "_landing", ".releases", "jo es", "xn--joes", "ab--c",
    "joes\n", "joé", None, 7,
])
def test_invalid_slugs_are_refused(slug):
    assert hosting.slug_problem(slug) is not None


@pytest.mark.parametrize("slug", ["www", "mail", "api", "admin", "app", "atlas",
                                  "smtp", "imap", "autodiscover", "autoconfig",
                                  "webmail", "mx", "hello"])
def test_reserved_slugs_are_refused(slug):
    problem = hosting.slug_problem(slug)
    assert problem is not None and "reserved" in problem


def test_local_mode_is_unset_or_the_word_local(monkeypatch):
    monkeypatch.delenv("AGENCY_DEPLOY_HOST", raising=False)
    assert hosting.local_mode()
    monkeypatch.setenv("AGENCY_DEPLOY_HOST", " Local ")
    assert hosting.local_mode()
    monkeypatch.setenv("AGENCY_DEPLOY_HOST", "deploy@web1")
    assert not hosting.local_mode()


def test_sites_domain_defaults_to_mapfront(monkeypatch):
    monkeypatch.delenv("AGENCY_SITES_DOMAIN", raising=False)
    assert hosting.sites_domain() == "mapfront.io"
    monkeypatch.setenv("AGENCY_SITES_DOMAIN", "Example.COM.")
    assert hosting.public_url("joes-7") == "https://joes-7.example.com/"


def test_the_default_publish_root_is_not_the_build_dir():
    assert hosting.root_problem(Path("data/sites"), Path("data/agency/sites")) is None
    assert hosting.root_problem(Path("data/agency"), Path("data/agency/sites"))
    assert hosting.root_problem(Path("data/agency/sites/x"), Path("data/agency/sites"))
    assert hosting.root_problem(Path("data/agency/sites"), Path("data/agency/sites"))


# ---------------------------------------------------------------- publish

async def test_publish_is_a_relative_link_to_a_copied_release(monkeypatch, tmp_path, local):
    root, calls = local
    _serve(monkeypatch, tmp_path)
    result = await deploy.deploy_site("joes-7")
    assert result.ok, result.detail
    assert result.evidence["url"] == "https://joes-7.mapfront.io/"
    link = root / "joes-7"
    assert link.is_symlink()
    target = os.readlink(link)
    assert not os.path.isabs(target) and target.startswith(".releases/joes-7/")
    assert "Pipes, fixed" in (link / "index.html").read_text()
    assert not (link / "index.html").is_symlink()
    assert calls == [("deployed", "joes-7", None)]
    token = json.loads(result.rollback_token)
    assert token["mode"] == "local" and token["previous"] is None


async def test_redeploy_swaps_and_rollback_restores_the_previous_release(
        monkeypatch, tmp_path, local):
    root, _ = local
    _serve(monkeypatch, tmp_path, heading="First version")
    first = await deploy.deploy_site("joes-7")
    _serve(monkeypatch, tmp_path, heading="Second version")
    second = await deploy.deploy_site("joes-7")
    assert second.ok
    assert "Second version" in (root / "joes-7" / "index.html").read_text()
    first_stamp = json.loads(first.rollback_token)["release"]
    assert json.loads(second.rollback_token)["previous"] == first_stamp

    back = await deploy.deploy_rollback(second.rollback_token)
    assert back.ok, back.detail
    assert "First version" in (root / "joes-7" / "index.html").read_text()


async def test_rolling_back_the_first_deploy_unpublishes(monkeypatch, tmp_path, local):
    root, calls = local
    _serve(monkeypatch, tmp_path)
    result = await deploy.deploy_site("joes-7")
    back = await deploy.deploy_rollback(result.rollback_token)
    assert back.ok and "unpublished" in back.detail
    assert not (root / "joes-7").exists() and not (root / "joes-7").is_symlink()
    assert calls[-1] == ("undeployed", "joes-7")


async def test_rollback_never_clobbers_a_newer_release(monkeypatch, tmp_path, local):
    root, _ = local
    _serve(monkeypatch, tmp_path, heading="First version")
    first = await deploy.deploy_site("joes-7")
    _serve(monkeypatch, tmp_path, heading="Second version")
    await deploy.deploy_site("joes-7")
    stale = await deploy.deploy_rollback(first.rollback_token)
    assert not stale.ok and "no longer live" in stale.detail
    assert "Second version" in (root / "joes-7" / "index.html").read_text()


async def test_rollback_to_a_vanished_release_keeps_the_site_up(
        monkeypatch, tmp_path, local):
    """Restore the previous release or, only after a first deploy, remove the
    site: a previous release that is gone is a refusal, never an outage."""
    import shutil
    root, calls = local
    _serve(monkeypatch, tmp_path, heading="First version")
    first = await deploy.deploy_site("joes-7")
    _serve(monkeypatch, tmp_path, heading="Second version")
    second = await deploy.deploy_site("joes-7")
    shutil.rmtree(root / ".releases" / "joes-7" / json.loads(first.rollback_token)["release"])
    back = await deploy.deploy_rollback(second.rollback_token)
    assert not back.ok and "no longer on disk" in back.detail
    assert "Second version" in (root / "joes-7" / "index.html").read_text()
    assert ("undeployed", "joes-7") not in calls


@pytest.mark.parametrize("token", [
    {"mode": "local", "slug": "../x", "release": "20260929T000000000000Z"},
    {"mode": "local", "slug": "joes-7", "release": "../../etc"},
    {"mode": "local", "slug": "joes-7", "release": "20260929T000000000000Z",
     "previous": "../../x"},
])
async def test_rollback_revalidates_the_stored_token(local, token):
    result = await deploy.deploy_rollback(json.dumps(token))
    assert not result.ok and "valid release" in result.detail


async def test_a_symlink_in_the_build_is_refused_and_nothing_goes_live(
        monkeypatch, tmp_path, local):
    root, calls = local
    site = _serve(monkeypatch, tmp_path)
    (site / "keys.pem").symlink_to("/etc/hosts")
    result = await deploy.deploy_site("joes-7")
    assert not result.ok and "symlink" in result.detail
    assert not (root / "joes-7").is_symlink()
    assert not list((root / ".releases" / "joes-7").iterdir())
    assert calls == []


async def test_reserved_and_bad_slugs_are_refused_before_the_store(
        monkeypatch, tmp_path, local):
    async def never(slug):
        raise AssertionError("the store must not be read for a refused slug")
    monkeypatch.setattr(_store, "get_site", never)
    for slug in ("www", "../../etc", "_landing", "Mail"):
        assert not (await deploy.deploy_site(slug)).ok
        assert not (await deploy.deploy_dry(slug)).ok


async def test_a_publish_root_inside_the_build_dir_is_refused(monkeypatch, tmp_path, local):
    _serve(monkeypatch, tmp_path)
    monkeypatch.setattr(hosting, "PUBLISH_ROOT", tmp_path / "builds" / "public")
    result = await deploy.deploy_site("joes-7")
    assert not result.ok and "overlaps the build directory" in result.detail


async def test_the_launch_check_reruns_on_the_release_copy(monkeypatch, tmp_path, local):
    """The bytes that go live are the bytes the check passed: a failure on
    the staged copy discards it and leaves the live link alone."""
    root, calls = local
    _serve(monkeypatch, tmp_path)
    real = deploy.launch_audit

    def fails_on_the_copy(path):
        report = real(path)
        if Path(path).is_relative_to(root):
            from atlas.modules.agency.launchcheck import unreadable
            return unreadable(str(path), "changed between check and publish",
                              report.read_at)
        return report
    monkeypatch.setattr(deploy, "launch_audit", fails_on_the_copy)
    result = await deploy.deploy_site("joes-7")
    assert not result.ok and "launch check failed" in result.detail
    assert not (root / "joes-7").is_symlink()
    assert not [p for p in (root / ".releases" / "joes-7").iterdir() if p.is_dir()]
    assert calls == []


async def test_the_build_provenance_travels_beside_the_release(
        monkeypatch, tmp_path, local):
    """The launch check reads `.<dir>.launch.json` beside the directory it
    audits; the release copy gets the build's, so both checks see the same."""
    root, _ = local
    site = _serve(monkeypatch, tmp_path)
    from atlas.modules.agency.launchcheck import provenance_path
    provenance_path(site).write_text('{"kind": "brochure"}')
    result = await deploy.deploy_site("joes-7")
    stamp = json.loads(result.rollback_token)["release"]
    beside = root / ".releases" / "joes-7" / f".{stamp}.launch.json"
    assert beside.read_text() == '{"kind": "brochure"}'


async def test_old_releases_are_pruned(monkeypatch, tmp_path, local):
    root, _ = local
    for n in range(hosting.KEEP_RELEASES + 3):
        _serve(monkeypatch, tmp_path, heading=f"Version {n}")
        assert (await deploy.deploy_site("joes-7")).ok
    kept = [p for p in (root / ".releases" / "joes-7").iterdir() if p.is_dir()]
    assert len(kept) == hosting.KEEP_RELEASES
    assert f"Version {hosting.KEEP_RELEASES + 2}" in (
        root / "joes-7" / "index.html").read_text()


async def test_deploy_dry_names_the_public_url(monkeypatch, tmp_path, local):
    root, _ = local
    _serve(monkeypatch, tmp_path)
    preview = await deploy.deploy_dry("joes-7")
    assert preview.ok, preview.detail
    assert "https://joes-7.mapfront.io/" in preview.detail
    assert "first release" in preview.detail and "index.html (" in preview.detail
    assert not root.exists() or not (root / "joes-7").exists()


async def test_rsync_target_still_refuses_without_a_root(monkeypatch):
    monkeypatch.setattr(_store, "get_site",
                        _async_returns({"slug": "joes", "path": "/local/sites/joes"}))
    monkeypatch.setenv("AGENCY_DEPLOY_HOST", "deploy@web1")
    monkeypatch.delenv("AGENCY_DEPLOY_ROOT", raising=False)
    result = await deploy.deploy_site("joes")
    assert not result.ok and "AGENCY_DEPLOY_ROOT" in result.detail


def test_published_domain_falls_back_to_the_mapfront_host(monkeypatch):
    monkeypatch.delenv("AGENCY_DEPLOY_HOST", raising=False)
    monkeypatch.delenv("AGENCY_SITES_DOMAIN", raising=False)
    assert deploy.published_domain({"slug": "joes-7", "deployed_at": "x"}) \
        == "joes-7.mapfront.io"
    assert deploy.published_domain({"slug": "joes-7", "deployed_at": None}) is None
    assert deploy.published_domain({"slug": "joes-7", "domain": "joes.com",
                                    "deployed_at": "x"}) == "joes.com"
    monkeypatch.setenv("AGENCY_DEPLOY_HOST", "deploy@web1")
    assert deploy.published_domain({"slug": "joes-7", "deployed_at": "x"}) is None


# ---------------------------------------------------------------- tls ask

async def test_tls_allowed_only_for_the_apex_www_and_published_slugs(
        monkeypatch, tmp_path, local):
    root, _ = local
    _serve(monkeypatch, tmp_path)
    assert (await deploy.deploy_site("joes-7")).ok
    for name in ("mapfront.io", "www.mapfront.io", "joes-7.mapfront.io",
                 "JOES-7.mapfront.io", "joes-7.mapfront.io."):
        assert hosting.tls_allowed(name, root), name
    for name in ("nope.mapfront.io", "a.joes-7.mapfront.io", "joes-7.mapfront.io.evil",
                 "evil.com", "", "mapfront.io.evil.com", "mail.mapfront.io",
                 "_landing.mapfront.io", ".releases.mapfront.io",
                 "..mapfront.io", "joes-7.example.com"):
        assert not hosting.tls_allowed(name, root), name


def test_tls_denies_a_real_directory_that_is_not_a_release(tmp_path):
    (tmp_path / "rogue").mkdir()
    assert not hosting.tls_allowed("rogue.mapfront.io", tmp_path)
