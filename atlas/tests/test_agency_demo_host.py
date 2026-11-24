"""`agency.deploy_demo`: a client demo, built elsewhere and dropped into the
intake directory, published on this box after a card (Tier ASK).

Everything runs on `tmp_path`; the store and the owner's phone are faked,
nothing touches a socket.
"""
from __future__ import annotations

import inspect
import json
import os
from pathlib import Path

import pytest

from atlas.kernel.contracts import Tier
from atlas.modules.agency import AgencyModule, _store, build, demo_host, deploy, hosting


@pytest.fixture(autouse=True)
def _box_deploy_path(monkeypatch):
    """The box deploy path is kept for rollback and tested here; since
    2026-10-30 it refuses live (Vercel only, test_agency_gmail_brand.py)."""
    from atlas.modules.agency import deploy as _deploy
    monkeypatch.setattr(_deploy, "VERCEL_ONLY", False)

BANNER = ("Preview made by ATLAS Agency for Royal HairStyles. Not the official "
          "Royal HairStyles website. No real bookings or payments.")
HOST = "examplesalon.storefront.test"


def _page(body: str = "", *, noindex: bool = True, banner: bool = True) -> str:
    robots = '<meta name="robots" content="noindex,nofollow">' if noindex else ""
    return (f"<!doctype html><html><head><title>Royal</title>{robots}</head><body>"
            f"{'<div class=demo>' + BANNER + '</div>' if banner else ''}{body}"
            "</body></html>")


def _intake(root: Path, slug: str = "examplesalon", **files: str) -> Path:
    site = root / slug
    site.mkdir(parents=True, exist_ok=True)
    pages = {"index.html": _page("<h1>Royal HairStyles</h1>"),
             "book.html": _page("<form action=\"/api/book\" method=post></form>"),
             "assets/app.js": "fetch('/api/slots')",
             "assets/mock-api.js": "localStorage.setItem('b', '1')"}
    pages.update(files)
    for name, text in pages.items():
        path = site / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return site


@pytest.fixture
def demo(monkeypatch, tmp_path):
    """Intake and publish roots under tmp_path, a registry row for the
    Royal demo, the store's writes recorded, the phone recorded."""
    intake, publish = tmp_path / "intake", tmp_path / "demos"
    monkeypatch.setattr(demo_host, "INTAKE_ROOT", intake)
    monkeypatch.setattr(demo_host, "PUBLISH_ROOT", publish)
    monkeypatch.setattr(hosting, "PUBLISH_ROOT", tmp_path / "sites")
    monkeypatch.setattr(build, "SITES_DIR", tmp_path / "builds")
    rows = {"examplesalon": {"slug": "examplesalon", "kind": "demo",
                                "domain": HOST, "path": str(intake / "examplesalon"),
                                "deployed_at": None},
            "joes-7": {"slug": "joes-7", "kind": "built", "domain": None,
                       "path": str(tmp_path / "builds" / "joes-7"), "deployed_at": None}}
    calls: list[tuple] = []
    rings: list[tuple] = []

    async def get_site(slug):
        return rows.get(slug)

    async def deployed(slug, previous):
        calls.append(("deployed", slug, previous))

    async def undeployed(slug):
        calls.append(("undeployed", slug))

    async def event(slug, what, detail="", release=None):
        calls.append(("event", slug, what, release))

    async def ring_owner(title, line):
        rings.append((title, line))
        return "rang Miro"

    monkeypatch.setattr(_store, "get_site", get_site)
    monkeypatch.setattr(_store, "set_site_deployed", deployed)
    monkeypatch.setattr(_store, "set_site_undeployed", undeployed)
    monkeypatch.setattr(_store, "record_site_event", event)
    monkeypatch.setattr(demo_host.escalate, "ring_owner", ring_owner)
    return intake, publish, rows, calls, rings


def _events(calls):
    return [c[2] for c in calls if c[0] == "event"]


# ---------------------------------------------------------------- the action

def _action():
    return next(a for a in AgencyModule().actions() if a.name == "agency.deploy_demo")


def test_deploy_demo_is_tier_ask_with_a_dry_run_and_a_rollback():
    action = _action()
    assert action.tier is Tier.ASK
    assert action.dry_run is not None and action.rollback is not None
    assert set(action.params) == {"slug"}
    assert action.max_per_hour <= 6
    assert action.verbose_card


def test_deploy_site_keeps_its_tier():
    site = next(a for a in AgencyModule().actions() if a.name == "agency.deploy_site")
    assert site.tier is Tier.AUTO


def test_call_on_rings_for_both_outcomes():
    assert demo_host.CALL_ON == ("deployed", "deploy_failed")


# ------------------------------------------------------------------ refusals

@pytest.mark.parametrize("slug", ["", "www", "../etc", "Royal", "_x", ".releases"])
async def test_bad_slugs_are_refused_before_the_store(demo, monkeypatch, slug):
    async def never(_slug):
        raise AssertionError("the store must not be read for a refused slug")
    monkeypatch.setattr(_store, "get_site", never)
    assert not (await demo_host.deploy_demo(slug)).ok
    assert not (await demo_host.deploy_demo_dry(slug)).ok


async def test_an_unknown_row_is_refused(demo):
    result = await demo_host.deploy_demo("nobody")
    assert not result.ok and "no such site" in result.detail


async def test_a_built_site_cannot_go_through_the_demo_path(demo):
    result = await demo_host.deploy_demo("joes-7")
    assert not result.ok and "not a demo" in result.detail


async def test_a_demo_row_cannot_go_through_deploy_site(demo, monkeypatch):
    monkeypatch.delenv("AGENCY_DEPLOY_HOST", raising=False)
    result = await deploy.deploy_site("examplesalon")
    assert not result.ok and "agency.deploy_demo" in result.detail


@pytest.mark.parametrize("mode", ["local", "remote"])
@pytest.mark.parametrize("row", [
    {"slug": "examplesalon", "kind": "demo", "path": "/srv/x"},
    {"slug": "examplesalon", "kind": "app", "path": "/srv/x"},
    {"slug": "examplesalon", "kind": "built", "path": ""},
])
async def test_deploy_site_refuses_non_built_rows_on_every_path(demo, monkeypatch,
                                                                 mode, row):
    """Tier AUTO deploy_site never publishes a demo, an app or a row
    without a build path: local, remote, run and dry run."""
    ran: list = []

    async def get_site(_slug):
        return dict(row, domain=None, deployed_at=None)

    async def no_rsync(argv):
        ran.append(argv)
        return True, ""

    def no_audit(path):
        raise AssertionError("the launch check must not run for a refused row")
    monkeypatch.setattr(_store, "get_site", get_site)
    monkeypatch.setattr(deploy, "_run", no_rsync)
    monkeypatch.setattr(deploy, "launch_audit", no_audit)
    if mode == "local":
        monkeypatch.delenv("AGENCY_DEPLOY_HOST", raising=False)
    else:
        monkeypatch.setenv("AGENCY_DEPLOY_HOST", "deploy@example.invalid")
        monkeypatch.setenv("AGENCY_DEPLOY_ROOT", "/srv/sites")
    for call in (deploy.deploy_site, deploy.deploy_dry):
        result = await call("examplesalon")
        assert not result.ok
        assert ("deploy_demo" in result.detail if row["kind"] != "built"
                else "no build path" in result.detail), result.detail
    assert ran == []


async def test_missing_intake_is_refused_and_nothing_is_staged(demo):
    _intake_root, publish, _rows, calls, rings = demo
    result = await demo_host.deploy_demo("examplesalon")
    assert not result.ok and "intake" in result.detail
    assert not publish.exists() or not any(publish.iterdir())
    assert "deploy_failed" in _events(calls) and len(rings) == 1


async def test_a_symlink_in_the_intake_is_refused_and_the_live_link_stays(demo):
    intake, publish, *_ = demo
    _intake(intake)
    first = await demo_host.deploy_demo("examplesalon")
    assert first.ok, first.detail
    live = os.readlink(publish / "examplesalon")
    (intake / "examplesalon" / "keys.pem").symlink_to("/etc/hosts")
    result = await demo_host.deploy_demo("examplesalon")
    assert not result.ok and "symlink" in result.detail
    assert os.readlink(publish / "examplesalon") == live


@pytest.mark.parametrize("name", [".env", "assets/.git-keep", ".well-known/x"])
async def test_a_dotfile_is_refused(demo, name):
    intake, publish, *_ = demo
    _intake(intake, **{name: "x"})
    result = await demo_host.deploy_demo("examplesalon")
    assert not result.ok and "dotfile" in result.detail
    assert not (publish / "examplesalon").is_symlink()


async def test_a_file_over_the_cap_is_refused(demo, monkeypatch):
    intake, *_ = demo
    monkeypatch.setattr(demo_host, "MAX_FILE_BYTES", 100)
    _intake(intake, **{"assets/big.bin": "x" * 101})
    result = await demo_host.deploy_demo("examplesalon")
    assert not result.ok and "over" in result.detail


async def test_a_total_over_the_cap_is_refused(demo, monkeypatch):
    intake, *_ = demo
    monkeypatch.setattr(demo_host, "MAX_TOTAL_BYTES", 300)
    _intake(intake, **{f"assets/{n}.txt": "x" * 90 for n in range(5)})
    result = await demo_host.deploy_demo("examplesalon")
    assert not result.ok and "total" in result.detail


def test_the_caps_are_the_documented_ones():
    assert demo_host.MAX_TOTAL_BYTES == 50 * 1024 * 1024
    assert demo_host.MAX_FILE_BYTES <= demo_host.MAX_TOTAL_BYTES


# --------------------------------------------------------------------- audit

def test_a_good_demo_passes_the_audit(tmp_path):
    assert demo_host.demo_audit(_intake(tmp_path), HOST) == []


@pytest.mark.parametrize("files, expect", [
    ({"about.html": _page(noindex=False)}, "noindex"),
    ({"index.html": _page(banner=False)}, "Preview made by ATLAS Agency"),
    ({"assets/app.js": "const k = 'pk_live_abc'"}, "live key"),
    ({"assets/app.js": "const k = 'sk_live_abc'"}, "live key"),
    ({"x.html": _page("rk_live_1")}, "live key"),
    ({"book.html": _page('<form method="post" action="https://evil.example/c">')},
     "form"),
    ({"book.html": _page("<FORM ACTION='//evil.example/c'>")}, "form"),
    ({"index.html": _page('<script src="https://cdn.example.com/x.js"></script>')},
     "script"),
    ({"index.html": _page("<script src=//cdn.example.com/x.js></script>")}, "script"),
])
def test_the_audit_refuses(tmp_path, files, expect):
    problems = demo_host.demo_audit(_intake(tmp_path, **files), HOST)
    assert problems and any(expect in p for p in problems), problems


def test_a_missing_index_is_refused(tmp_path):
    site = _intake(tmp_path)
    (site / "index.html").unlink()
    assert any("index.html" in p for p in demo_host.demo_audit(site, HOST))


def test_own_origin_scripts_and_relative_ones_pass(tmp_path):
    site = _intake(tmp_path, **{"index.html": _page(
        f'<script src="https://{HOST}/assets/app.js"></script>'
        '<script src="/assets/mock-api.js"></script>'
        '<script src="assets/app.js"></script>')})
    assert demo_host.demo_audit(site, HOST) == []


# ------------------------------------------------------------------- publish

async def test_a_good_intake_goes_live_as_a_relative_link(demo):
    intake, publish, _rows, calls, rings = demo
    _intake(intake)
    result = await demo_host.deploy_demo("examplesalon")
    assert result.ok, result.detail
    link = publish / "examplesalon"
    assert link.is_symlink()
    target = os.readlink(link)
    assert not os.path.isabs(target) and target.startswith(".releases/examplesalon/")
    assert "Royal HairStyles" in (link / "index.html").read_text()
    assert ("deployed", "examplesalon", None) in calls
    assert "deployed" in _events(calls)
    assert len(rings) == 1 and HOST in rings[0][1]
    token = json.loads(result.rollback_token)
    assert token["mode"] == "demo" and token["previous"] is None
    assert result.evidence["url"] == f"https://{HOST}/"


async def test_redeploy_swaps_and_rollback_restores(demo):
    intake, publish, _rows, calls, _rings = demo
    _intake(intake, **{"index.html": _page("<h1>First</h1>")})
    first = await demo_host.deploy_demo("examplesalon")
    _intake(intake, **{"index.html": _page("<h1>Second</h1>")})
    second = await demo_host.deploy_demo("examplesalon")
    assert second.ok, second.detail
    assert "Second" in (publish / "examplesalon" / "index.html").read_text()
    assert json.loads(second.rollback_token)["previous"] == \
        json.loads(first.rollback_token)["release"]
    back = await demo_host.deploy_demo_rollback(second.rollback_token)
    assert back.ok, back.detail
    assert "First" in (publish / "examplesalon" / "index.html").read_text()
    assert "rolled_back" in _events(calls)


async def test_rolling_back_the_first_deploy_unpublishes(demo):
    intake, publish, _rows, calls, _rings = demo
    _intake(intake)
    result = await demo_host.deploy_demo("examplesalon")
    back = await demo_host.deploy_demo_rollback(result.rollback_token)
    assert back.ok and "unpublished" in back.detail
    assert not (publish / "examplesalon").is_symlink()
    assert ("undeployed", "examplesalon") in calls


async def test_rollback_never_clobbers_a_newer_release(demo):
    intake, publish, *_ = demo
    _intake(intake, **{"index.html": _page("<h1>First</h1>")})
    first = await demo_host.deploy_demo("examplesalon")
    _intake(intake, **{"index.html": _page("<h1>Second</h1>")})
    await demo_host.deploy_demo("examplesalon")
    stale = await demo_host.deploy_demo_rollback(first.rollback_token)
    assert not stale.ok and "no longer live" in stale.detail
    assert "Second" in (publish / "examplesalon" / "index.html").read_text()


@pytest.mark.parametrize("token", [
    "", "not json",
    json.dumps({"mode": "demo", "slug": "../x", "release": "20261027T000000000000Z"}),
    json.dumps({"mode": "demo", "slug": "examplesalon", "release": "../../etc"}),
    json.dumps({"mode": "demo", "slug": "examplesalon",
                "release": "20261027T000000000000Z", "previous": "../x"}),
])
async def test_rollback_revalidates_the_token(demo, token):
    assert not (await demo_host.deploy_demo_rollback(token)).ok


async def test_at_most_five_releases_are_kept(demo):
    intake, publish, *_ = demo
    for n in range(hosting.KEEP_RELEASES + 2):
        _intake(intake, **{"index.html": _page(f"<h1>Version {n}</h1>")})
        assert (await demo_host.deploy_demo("examplesalon")).ok
    kept = [p for p in (publish / ".releases" / "examplesalon").iterdir() if p.is_dir()]
    assert len(kept) == hosting.KEEP_RELEASES


async def test_the_audit_reruns_on_the_release_copy(demo, monkeypatch):
    intake, publish, _rows, calls, _rings = demo
    _intake(intake)
    real = demo_host.demo_audit

    def fails_on_the_copy(path, hostname=None):
        if Path(path).is_relative_to(publish):
            return ["changed between check and publish"]
        return real(path, hostname)
    monkeypatch.setattr(demo_host, "demo_audit", fails_on_the_copy)
    result = await demo_host.deploy_demo("examplesalon")
    assert not result.ok and "changed between" in result.detail
    assert not (publish / "examplesalon").is_symlink()
    assert not [p for p in (publish / ".releases" / "examplesalon").iterdir()
                if p.is_dir()]
    assert not [c for c in calls if c[0] == "deployed"]


@pytest.mark.parametrize("where", ["intake", "sites", "builds", "intake/sub"])
async def test_a_publish_root_overlapping_another_tree_is_refused(demo, monkeypatch,
                                                                  tmp_path, where):
    intake, *_ = demo
    _intake(intake)
    monkeypatch.setattr(demo_host, "PUBLISH_ROOT", tmp_path / where)
    result = await demo_host.deploy_demo("examplesalon")
    assert not result.ok and "overlaps" in result.detail
    assert not (await demo_host.deploy_demo_dry("examplesalon")).ok


async def test_the_dry_run_names_what_would_happen_and_touches_nothing(demo):
    intake, publish, _rows, calls, rings = demo
    _intake(intake)
    preview = await demo_host.deploy_demo_dry("examplesalon")
    assert preview.ok, preview.detail
    for words in (f"https://{HOST}/", "4 file(s)", "audit: passed", "first release"):
        assert words in preview.detail, words
    assert not publish.exists()
    assert calls == [] and rings == []


async def test_the_dry_run_shows_the_audit_failure(demo):
    intake, *_ = demo
    _intake(intake, **{"index.html": _page(banner=False)})
    preview = await demo_host.deploy_demo_dry("examplesalon")
    assert not preview.ok and "Preview made by ATLAS Agency" in preview.detail


async def test_a_store_that_cannot_record_events_does_not_fail_the_deploy(demo,
                                                                         monkeypatch):
    intake, *_ = demo
    _intake(intake)

    async def broken(*_a, **_k):
        raise ConnectionRefusedError("db down")
    monkeypatch.setattr(_store, "record_site_event", broken)
    assert (await demo_host.deploy_demo("examplesalon")).ok


def test_ring_owner_takes_no_phone_number():
    from atlas.modules.agency import escalate
    params = set(inspect.signature(escalate.ring_owner).parameters)
    assert not params & {"to", "phone", "number", "recipient"}


# ------------------------------------------------------------ signal (card)

async def test_a_fresh_intake_raises_one_card(demo, monkeypatch):
    intake, *_ = demo
    _intake(intake)

    async def rows():
        return [{"slug": "examplesalon", "kind": "demo", "domain": HOST},
                {"slug": "joes-7", "kind": "built", "domain": None}]

    async def events(_slug, _limit=20):
        return []
    monkeypatch.setattr(_store, "list_sites", rows)
    monkeypatch.setattr(_store, "site_events", events)
    signals = await demo_host.signals()
    assert [s.suggests for s in signals] == ["agency.deploy_demo"]
    assert signals[0].params == {"slug": "examplesalon"}
    assert HOST in signals[0].title


async def test_an_intake_already_live_raises_no_card(demo, monkeypatch):
    intake, *_ = demo
    _intake(intake)
    assert (await demo_host.deploy_demo("examplesalon")).ok
    fp = await demo_host._fingerprint_async(intake / "examplesalon")

    async def rows():
        return [{"slug": "examplesalon", "kind": "demo", "domain": HOST}]

    async def events(_slug, _limit=20):
        return [{"event": "deployed", "detail": f"intake {fp}", "release": "x"}]
    monkeypatch.setattr(_store, "list_sites", rows)
    monkeypatch.setattr(_store, "site_events", events)
    assert await demo_host.signals() == []


# -------------------------------------------------------------- watch-only

async def test_watch_only_refuses_the_action(monkeypatch, memory_backend, demo):
    from atlas.kernel import executor
    from atlas.kernel.executor import ExecutionRequest, execute
    from atlas.kernel.permissions import classify
    from atlas.kernel.registry import REGISTRY
    intake, publish, *_ = demo
    _intake(intake)
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "true")
    action = _action()
    saved = dict(REGISTRY.actions), dict(REGISTRY.decisions)
    REGISTRY.actions[action.name] = action
    REGISTRY.decisions[action.name] = classify(action)
    executor._recent.clear()
    try:
        result = await execute(ExecutionRequest(
            "agency.deploy_demo", {"slug": "examplesalon"}, "x", approved_by="miro"))
    finally:
        REGISTRY.actions.clear()
        REGISTRY.actions.update(saved[0])
        REGISTRY.decisions.clear()
        REGISTRY.decisions.update(saved[1])
        executor._recent.clear()
    assert not result.ok and "watch-only" in result.detail
    assert not publish.exists()


def test_promotion_cannot_change_the_tier():
    """The tier is a constant in code: building the module twice, whatever
    was recorded in between, yields the same Tier.ASK."""
    assert _action().tier is Tier.ASK and _action().tier is Tier.ASK
    from atlas.kernel.permissions import classify
    assert classify(_action()).tier is Tier.ASK


# --------------------------------------------------------------- ring_owner

@pytest.fixture
def phone(monkeypatch):
    from atlas.kernel import comms, escalation
    from atlas.modules.agency import escalate
    escalate.reset()
    escalation.reset()
    placed: list = []
    posted: list = []

    class Voice:
        async def available(self):
            return True

        async def send(self, msg):
            placed.append(msg)
            return "CA1"
    monkeypatch.setitem(comms.CHANNELS, "voice", Voice())
    monkeypatch.setattr(comms, "_quiet_now", lambda: False)

    async def notify(msg):
        posted.append(msg)
        return ["slack"]
    monkeypatch.setattr(comms, "notify", notify)
    yield escalate, placed, posted
    escalate.reset()
    escalation.reset()


async def test_ring_owner_rings_with_its_own_title_and_posts(phone):
    escalate, placed, posted = phone
    out = await escalate.ring_owner("Demo live: x.example", "Agency: the x demo is live.")
    assert out == "rang Miro"
    assert placed[0].title == "Demo live: x.example"
    assert "emergency" not in placed[0].title.lower()
    assert posted and "Phone: rang Miro" in posted[0].body


async def test_ring_owner_obeys_the_gap_and_does_not_hold(phone):
    escalate, placed, posted = phone
    await escalate.ring_owner("Demo live: a", "one")
    second = await escalate.ring_owner("Demo live: b", "two")
    assert second.startswith("not rung") and len(placed) == 1
    assert escalate._PENDING == [] and len(posted) == 2


async def test_ring_owner_in_quiet_hours(phone, monkeypatch):
    escalate, placed, _posted = phone
    from atlas.kernel import comms
    monkeypatch.setattr(comms, "_quiet_now", lambda: True)
    assert await escalate.ring_owner("t", "l") == "not rung: quiet hours"
    assert placed == []


async def test_ring_owner_over_the_call_budget_still_posts(phone, monkeypatch):
    escalate, placed, posted = phone
    from atlas.kernel import escalation
    monkeypatch.setattr(escalation, "may_call",
                        lambda *_a, **_k: (False, "three calls today"))
    out = await escalate.ring_owner("Demo live: a", "one")
    assert out == "not rung: three calls today" and placed == []
    assert posted and "not rung" in posted[0].body


async def test_emergency_titles_are_unchanged(phone):
    escalate, placed, _posted = phone
    await escalate.emergency("Joe's", "a legal threat", "details")
    assert placed[0].title == "Agency emergency"


# ------------------------------------------------------------ sites-add CLI

@pytest.mark.parametrize("args", [
    ["examplesalon", "examplesalon.storefront.test", "built", "x@1"],
    ["Royal", "examplesalon.example.com", "demo", "x@1"],
    ["examplesalon", "not a host", "demo", "x@1"],
    ["examplesalon", "localhost", "demo", "x@1"],
    ["examplesalon", "10.0.0.1", "demo", "x@1"],
    ["examplesalon", "a.example.com", "demo", "line\nbreak"],
    ["examplesalon", "a.example.com", "demo"],
])
def test_sites_add_refuses(args):
    from atlas.cli import parse_sites_add
    with pytest.raises(ValueError):
        parse_sites_add(args)


def test_sites_add_parses():
    from atlas.cli import parse_sites_add
    assert parse_sites_add(["examplesalon", "ExampleSalon.example.com.", "demo",
                            "royal-hairstyles-site-demo@176c272"]) == (
        "examplesalon", "examplesalon.example.com", "demo",
        "royal-hairstyles-site-demo@176c272")


async def test_add_site_row_writes_a_demo_row(memory_backend):
    memory_backend.responses["INSERT INTO agency_sites (slug, domain, path, kind"] = \
        {"id": 7}
    got = await _store.add_site_row("examplesalon", HOST, "demo", "x@1",
                                    "data/demos-intake/examplesalon")
    assert got == 7
    sql, args = memory_backend.sql_log[-1]
    assert "ON CONFLICT (slug) DO NOTHING" in sql
    assert args == ("examplesalon", HOST, "data/demos-intake/examplesalon",
                    "demo", "x@1", True)


async def test_site_events_are_recorded_and_read_newest_first(memory_backend):
    memory_backend.responses["FROM agency_site_events"] = []
    await _store.record_site_event("examplesalon", "deployed", "intake ab",
                                   release="20261027T000000000000Z")
    assert await _store.site_events("examplesalon", 5) == []
    insert, read = memory_backend.sql_log[-2], memory_backend.sql_log[-1]
    assert "INSERT INTO agency_site_events" in insert[0]
    assert "ORDER BY at DESC" in read[0] and read[1] == ("examplesalon", 5)


def test_the_schema_adds_kind_and_the_events_table():
    schema = (Path(__file__).resolve().parent.parent / "db" / "schema.sql").read_text()
    assert "ALTER TABLE agency_sites ADD COLUMN IF NOT EXISTS kind" in schema
    assert "CREATE TABLE IF NOT EXISTS agency_site_events" in schema
    assert "agency_site_events (slug, at DESC)" in schema
