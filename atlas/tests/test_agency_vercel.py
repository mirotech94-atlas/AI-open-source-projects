"""`agency.publish_vercel`: a built preview (or a demo) published on Vercel
only after a card (Tier ASK), with a dry run and an honest rollback.

Every Vercel call is faked at `atlas.modules._http`; any real httpx client
raises, so nothing here can reach the network. Env values are dummies, long
enough that `_http.redact` treats them as secrets.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from atlas.kernel.contracts import Health, Tier
from atlas.kernel.executor import _validate_params
from atlas.kernel.permissions import classify
from atlas.modules import _http
from atlas.modules.agency import (
    AgencyModule,
    _store,
    demo_host,
    deploy,
    hosting,
    vercel,
)

TOKEN = "dummy-vercel-token-0123456789"
TEAM = "team_dummy0123456789"
SLUG = "joes-7"
HOST = "demo-site.vercel.example"


def _page(body: str = "", *, noindex: bool = True) -> str:
    robots = '<meta name="robots" content="noindex">' if noindex else ""
    return (f"<!doctype html><html><head><title>Joe's</title>{robots}</head>"
            f"<body>{body}</body></html>")


def _tree(root: Path, **files: str) -> Path:
    pages = {"index.html": _page("<h1>Joe's Plumbing</h1>"),
             "contact.html": _page("<form action=\"/api/enquiry\"></form>"),
             "assets/site.css": "body{margin:0}"}
    pages.update(files)
    for name, text in pages.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


# ------------------------------------------------------- step 2: the audit

def test_a_built_preview_passes_without_the_demo_banner(tmp_path):
    site = _tree(tmp_path / "s")
    assert demo_host.demo_audit(site, HOST, require_banner=False) == []
    # The demo intake keeps the banner requirement by default.
    assert any("banner" in p for p in demo_host.demo_audit(site, HOST))


def test_a_page_without_noindex_is_named(tmp_path):
    site = _tree(tmp_path / "s", **{"about.html": _page(noindex=False)})
    problems = demo_host.demo_audit(site, HOST, require_banner=False)
    assert len(problems) == 1 and "about.html" in problems[0]


@pytest.mark.parametrize("opts", [{}, {"require_banner": False},
                                  {"require_banner": False,
                                   "allow_external_scripts": True}])
def test_an_off_site_form_is_refused_whatever_the_options(tmp_path, opts):
    site = _tree(tmp_path / "s", **{
        "book.html": _page('<form action="https://other.example/x"></form>')})
    problems = demo_host.demo_audit(site, HOST, **opts)
    assert any("posts off-site" in p for p in problems)


def test_a_live_key_in_a_script_is_refused(tmp_path):
    site = _tree(tmp_path / "s", **{"assets/app.js": "const k='sk_live_abc';"})
    problems = demo_host.demo_audit(site, HOST, require_banner=False)
    assert any("live key" in p for p in problems)


def test_external_scripts_only_pass_when_allowed(tmp_path):
    site = _tree(tmp_path / "s", **{
        "x.html": _page('<script src="https://cdn.example/a.js"></script>')})
    assert any("external script" in p
               for p in demo_host.demo_audit(site, HOST, require_banner=False))
    assert demo_host.demo_audit(site, HOST, require_banner=False,
                                allow_external_scripts=True) == []


# ------------------------------------------------------ step 3: the module

class _Report:
    def __init__(self, ready: bool = True):
        self.ready = ready

    def summary(self) -> str:
        return "launch check: ready" if self.ready else "launch check: 1 ISSUE"


class FakeVercel:
    """Answers the Vercel endpoints vercel.py uses; records every call."""

    def __init__(self, *, project: dict | None = None, states=("READY",),
                 alias: list[str] | None = None,
                 page: tuple[int, str] | None = None):
        self.calls: list[tuple[str, str, dict]] = []
        self.project = project          # None -> 404
        self.states = list(states)
        self.alias = [HOST] if alias is None else alias
        self.page = page or (200, _page())

    async def get_json(self, url, *, headers=None, params=None, **_kw):
        self.calls.append(("GET", url, {"headers": headers, "params": params}))
        if "/v9/projects/" in url:
            if self.project is None:
                return None, "HTTP 404 from /v9/projects/x — not found"
            return self.project, None
        if "/v13/deployments/" in url:
            state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
            return {"id": "dpl_new1", "readyState": state, "aliasAssigned": True,
                    "alias": self.alias}, None
        if url.endswith("/v2/user"):
            return {"user": {"id": "u"}}, None
        raise AssertionError(url)

    async def post_json(self, url, *, headers=None, params=None, json_body=None, **_kw):
        self.calls.append(("POST", url, {"headers": headers, "params": params,
                                         "json": json_body}))
        if url.endswith("/v10/projects"):
            return {"id": "prj_1", "name": json_body["name"]}, None
        if url.endswith("/v13/deployments"):
            return {"id": "dpl_new1", "readyState": "QUEUED"}, None
        raise AssertionError(url)

    async def post_body(self, url, *, headers=None, params=None, content=None,
                        json_body=None, **_kw):
        self.calls.append(("POST", url, {"headers": headers, "params": params,
                                         "content": content, "json": json_body}))
        if url.endswith("/v2/files") or "/promote/" in url:
            return None, None
        raise AssertionError(url)

    async def fetch_page(self, url):
        self.calls.append(("PAGE", url, {}))
        return self.page[0], self.page[1], None

    def urls(self, method: str | None = None) -> list[str]:
        return [u for m, u, _ in self.calls if method is None or m == method]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    class _Refused:
        def __init__(self, *_a, **_kw):
            raise AssertionError("a real HTTP client was opened in a test")
    monkeypatch.setattr(httpx, "AsyncClient", _Refused)


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setenv("VERCEL_TOKEN", TOKEN)
    monkeypatch.setenv("VERCEL_TEAM_ID", TEAM)
    monkeypatch.setattr(vercel, "POLL_EVERY", 0)
    monkeypatch.setattr(vercel, "POLL_FOR", 5)
    monkeypatch.setattr(demo_host, "INTAKE_ROOT", tmp_path / "intake")
    built = _tree(tmp_path / "builds" / SLUG)
    rows = {SLUG: {"slug": SLUG, "kind": "built", "domain": None,
                   "path": str(built), "deployed_at": None},
            "royal": {"slug": "royal", "kind": "demo", "domain": None,
                      "path": str(tmp_path / "intake" / "royal"),
                      "deployed_at": None}}
    store: list[tuple] = []
    rings: list[tuple] = []

    async def get_site(slug):
        return rows.get(slug)

    async def deployed(slug, previous):
        store.append(("deployed", slug, previous))

    async def undeployed(slug):
        store.append(("undeployed", slug))

    async def domain(slug, value):
        store.append(("domain", slug, value))

    async def event(slug, what, detail="", release=None):
        store.append(("event", slug, what, detail, release))

    async def ring(title, line):
        rings.append((title, line))

    monkeypatch.setattr(_store, "get_site", get_site)
    monkeypatch.setattr(_store, "set_site_deployed", deployed)
    monkeypatch.setattr(_store, "set_site_undeployed", undeployed)
    monkeypatch.setattr(_store, "set_site_domain", domain)
    monkeypatch.setattr(_store, "record_site_event", event)
    monkeypatch.setattr(vercel.escalate, "ring_owner", ring)
    monkeypatch.setattr(vercel, "launch_audit", lambda _p: _Report(True))

    def install(fake: FakeVercel) -> FakeVercel:
        monkeypatch.setattr(_http, "get_json", fake.get_json)
        monkeypatch.setattr(_http, "post_json", fake.post_json)
        monkeypatch.setattr(_http, "post_body", fake.post_body)
        monkeypatch.setattr(vercel, "_fetch_page", fake.fetch_page)
        return fake

    class W:
        pass
    w = W()
    w.rows, w.store, w.rings, w.install, w.built = rows, store, rings, install, built
    return w


@pytest.mark.parametrize("missing", ["VERCEL_TOKEN", "VERCEL_TEAM_ID"])
async def test_missing_config_refuses_run_and_dry_without_a_call(world, monkeypatch,
                                                                 missing):
    fake = world.install(FakeVercel())
    monkeypatch.delenv(missing)
    assert vercel.config_problem() == f"{missing} is not set"
    for fn in (vercel.publish_vercel, vercel.publish_vercel_dry):
        result = await fn(SLUG)
        assert not result.ok and missing in result.detail
    assert fake.calls == [] and world.store == []


async def test_the_dry_run_uploads_nothing_and_writes_nothing(world):
    fake = world.install(FakeVercel(project={"id": "prj_1", "latestDeployments": [
        {"id": "dpl_a"}, {"id": "dpl_b"}], "targets": {"production": {"id": "dpl_a"}}}))
    result = await vercel.publish_vercel_dry(SLUG)
    assert result.ok, result.detail
    assert "would publish 3 file(s)" in result.detail
    assert f"https://{HOST}/" in result.detail and "audit: passed" in result.detail
    assert "project exists, 2 previous deployment(s)" in result.detail
    assert not any("/v2/files" in u or "/v13/deployments" in u for u in fake.urls())
    assert fake.urls("POST") == []
    assert world.store == [] and world.rings == []


async def test_the_dry_run_names_a_new_project(world):
    world.install(FakeVercel(project=None))
    result = await vercel.publish_vercel_dry(SLUG)
    assert result.ok and "new project" in result.detail


async def test_a_publish_uploads_every_file_deploys_once_and_records_the_host(world):
    fake = world.install(FakeVercel(project=None, states=("BUILDING", "READY")))
    result = await vercel.publish_vercel(SLUG)
    assert result.ok, result.detail
    uploads = [c for c in fake.calls if c[1].endswith("/v2/files")]
    assert len(uploads) == 3
    for _m, _u, kw in uploads:
        assert kw["headers"]["Authorization"] == f"Bearer {TOKEN}"
        assert len(kw["headers"]["x-vercel-digest"]) == 40
        assert kw["params"] == {"teamId": TEAM}
    [create] = [c for c in fake.calls if c[1].endswith("/v10/projects")]
    assert create[2]["json"] == {"name": "atlas-agency-joes-7", "framework": None}
    [dep] = [c for c in fake.calls if c[1].endswith("/v13/deployments")]
    body = dep[2]["json"]
    assert body["name"] == body["project"] == "atlas-agency-joes-7"
    assert body["target"] == "production"
    assert body["projectSettings"] == {"framework": None}
    assert sorted(f["file"] for f in body["files"]) == [
        "assets/site.css", "contact.html", "index.html"]
    assert ("domain", SLUG, HOST) in world.store
    assert ("deployed", SLUG, None) in world.store
    assert any(s[0] == "event" and s[2] == "deployed" for s in world.store)
    assert world.rings and HOST in world.rings[0][0]
    token = json.loads(result.rollback_token)
    assert token == {"mode": "vercel", "slug": SLUG, "deployment": "dpl_new1",
                     "previous": None}
    assert result.evidence["url"] == f"https://{HOST}/"
    flat = json.dumps(result.evidence) + result.detail + result.rollback_token
    assert TOKEN not in flat and TEAM not in flat


async def test_a_publish_over_a_live_deployment_keeps_it_for_the_rollback(world):
    world.install(FakeVercel(project={"id": "prj_1", "latestDeployments": [
        {"id": "dpl_old"}], "targets": {"production": {"id": "dpl_old"}}}))
    result = await vercel.publish_vercel(SLUG)
    assert result.ok, result.detail
    assert ("deployed", SLUG, "dpl_old") in world.store
    assert json.loads(result.rollback_token)["previous"] == "dpl_old"


@pytest.mark.parametrize("state", ["ERROR", "CANCELED"])
async def test_a_failed_deployment_is_not_recorded_as_live(world, state):
    world.install(FakeVercel(project=None, states=(state,)))
    result = await vercel.publish_vercel(SLUG)
    assert not result.ok and state in result.detail
    assert not any(s[0] in ("deployed", "domain") for s in world.store)
    assert result.rollback_token is None


async def test_a_deployment_that_never_gets_ready_times_out(world, monkeypatch):
    monkeypatch.setattr(vercel, "POLL_FOR", 0)
    world.install(FakeVercel(project=None, states=("BUILDING",)))
    result = await vercel.publish_vercel(SLUG)
    assert not result.ok and "not READY" in result.detail
    assert not any(s[0] == "deployed" for s in world.store)


async def test_a_host_vercel_did_not_assign_is_not_recorded(world):
    world.install(FakeVercel(project=None, alias=["demo-site.vercel.example"]))
    result = await vercel.publish_vercel(SLUG)
    assert not result.ok
    assert "demo-site.vercel.example" in result.detail
    assert not any(s[0] in ("deployed", "domain") for s in world.store)


async def test_a_live_page_without_noindex_is_not_recorded(world):
    world.install(FakeVercel(project=None, page=(200, _page(noindex=False))))
    result = await vercel.publish_vercel(SLUG)
    assert not result.ok and "noindex" in result.detail and HOST in result.detail
    assert not any(s[0] in ("deployed", "domain") for s in world.store)


async def test_an_audit_problem_refuses_before_any_http(world):
    fake = world.install(FakeVercel())
    (world.built / "about.html").write_text(_page(noindex=False))
    for fn in (vercel.publish_vercel, vercel.publish_vercel_dry):
        result = await fn(SLUG)
        assert not result.ok and "about.html" in result.detail
    assert fake.calls == []


async def test_a_failed_launch_check_refuses_before_any_http(world, monkeypatch):
    fake = world.install(FakeVercel())
    monkeypatch.setattr(vercel, "launch_audit", lambda _p: _Report(False))
    result = await vercel.publish_vercel(SLUG)
    assert not result.ok and "launch check" in result.detail
    assert fake.calls == []


async def test_a_demo_publishes_from_its_intake_with_the_banner_required(world):
    fake = world.install(FakeVercel(project=None))
    result = await vercel.publish_vercel("royal")
    assert not result.ok and "intake" in result.detail
    assert fake.calls == []


async def test_unknown_or_malformed_slugs_are_refused(world):
    fake = world.install(FakeVercel())
    assert "no such site" in (await vercel.publish_vercel("nobody")).detail
    assert not (await vercel.publish_vercel("Bad Slug")).ok
    assert fake.calls == []


def test_project_name_and_host_follow_the_slug_rules():
    assert vercel.project_name(SLUG) == "atlas-agency-joes-7"
    assert vercel.public_host(SLUG) == HOST
    for bad in ("www", "Bad", "-x", "", "a" * 41):
        with pytest.raises(ValueError):
            vercel.project_name(bad)


# ---------------------------------------------------------------- rollback

async def test_rollback_promotes_the_previous_deployment(world):
    fake = world.install(FakeVercel(project={"id": "prj_1", "targets": {
        "production": {"id": "dpl_new1"}}}))
    token = json.dumps({"mode": "vercel", "slug": SLUG, "deployment": "dpl_new1",
                        "previous": "dpl_old"})
    result = await vercel.publish_vercel_rollback(token)
    assert result.ok, result.detail
    assert any("/v10/projects/atlas-agency-joes-7/promote/dpl_old" in u
               for u in fake.urls("POST"))
    assert ("deployed", SLUG, None) in world.store
    assert any(s[0] == "event" and s[2] == "rolled_back" for s in world.store)


async def test_rollback_refuses_over_a_newer_deployment(world):
    fake = world.install(FakeVercel(project={"id": "prj_1", "targets": {
        "production": {"id": "dpl_newer"}}}))
    token = json.dumps({"mode": "vercel", "slug": SLUG, "deployment": "dpl_new1",
                        "previous": "dpl_old"})
    result = await vercel.publish_vercel_rollback(token)
    assert not result.ok and "dpl_newer" in result.detail
    assert fake.urls("POST") == []


async def test_a_first_publish_rollback_deletes_nothing(world):
    fake = world.install(FakeVercel(project={"id": "prj_1", "targets": {
        "production": {"id": "dpl_new1"}}}))
    token = json.dumps({"mode": "vercel", "slug": SLUG, "deployment": "dpl_new1",
                        "previous": None})
    result = await vercel.publish_vercel_rollback(token)
    assert result.ok and "by hand" in result.detail
    assert fake.urls("POST") == []
    assert ("undeployed", SLUG) in world.store
    assert ("domain", SLUG, None) in world.store


@pytest.mark.parametrize("token", [
    json.dumps({"mode": "demo", "slug": SLUG, "release": "x", "previous": None}),
    json.dumps({"mode": "vercel", "slug": "Bad Slug", "deployment": "dpl_1",
                "previous": None}),
    json.dumps({"mode": "vercel", "slug": SLUG, "deployment": "../x",
                "previous": None}),
    "not json"])
async def test_a_foreign_or_malformed_token_is_refused(world, token):
    fake = world.install(FakeVercel())
    result = await vercel.publish_vercel_rollback(token)
    assert not result.ok
    assert fake.calls == [] and world.store == []


def test_no_delete_endpoint_anywhere_in_the_module():
    source = Path(vercel.__file__).read_text()
    assert "DELETE" not in source and "delete(" not in source


# ------------------------------------------------------------------- check

async def test_the_check_is_unknown_without_config(monkeypatch):
    monkeypatch.delenv("VERCEL_TOKEN", raising=False)
    monkeypatch.delenv("VERCEL_TEAM_ID", raising=False)
    obs = await vercel.check()
    assert obs.health is Health.UNKNOWN and "VERCEL_TOKEN" in obs.detail


@pytest.mark.parametrize(("reason", "health"), [
    (None, Health.OK),
    ("HTTP 401 from /v2/user — credentials rejected", Health.FAILING),
    ("HTTP 403 from /v2/user — authenticated but not permitted", Health.FAILING),
    ("timed out", Health.UNKNOWN)])
async def test_the_check_reads_the_api(monkeypatch, reason, health):
    monkeypatch.setenv("VERCEL_TOKEN", TOKEN)
    monkeypatch.setenv("VERCEL_TEAM_ID", TEAM)

    async def get_json(url, **_kw):
        return ({"user": {}} if reason is None else None), reason
    monkeypatch.setattr(_http, "get_json", get_json)
    obs = await vercel.check()
    assert obs.health is health
    assert TOKEN not in obs.detail


# ------------------------------------------------- step 4: the registration

def _action(name: str):
    return next(a for a in AgencyModule().actions() if a.name == name)


def test_publish_vercel_is_a_card_with_a_dry_run_and_a_rollback():
    action = _action("agency.publish_vercel")
    assert action.tier is Tier.ASK
    assert action.run is vercel.publish_vercel
    assert action.dry_run is vercel.publish_vercel_dry
    assert action.rollback is vercel.publish_vercel_rollback
    assert set(action.params) == {"slug"}
    assert action.runs_in_background
    assert action.topic_for({"slug": SLUG}) == "agency_decisions"
    assert "demo-site.vercel.example" in action.card_detail({"slug": SLUG})
    decision = classify(action)
    assert decision.allowed and decision.tier is Tier.ASK


def test_an_undeclared_parameter_is_refused():
    action = _action("agency.publish_vercel")
    assert _validate_params(action, {"slug": "x", "extra": 1})
    assert _validate_params(action, {"slug": "x"}) is None


def test_the_check_is_registered():
    names = [c.name for c in AgencyModule().checks()]
    assert "vercel" in names
    assert {"VERCEL_TOKEN", "VERCEL_TEAM_ID"} <= set(AgencyModule.optional_env)


def test_the_qa_gate_reads_the_site_for_a_vercel_publish():
    from atlas.kernel import qa_gate
    assert "agency.publish_vercel" in qa_gate.GATED_NAMES
    assert qa_gate.RESOLVERS["agency.publish_vercel"] is qa_gate._site


def test_the_sites_agent_can_raise_the_card():
    from atlas.tools import builtin
    src = Path(builtin.__file__).read_text()
    assert '"agency_publish_vercel"' in src and '"agency.publish_vercel"' in src


def test_the_readback_covers_the_vercel_publish():
    from atlas.kernel import verify
    from atlas.modules.agency import readback  # noqa: F401  (registers)
    assert "agency.publish_vercel" in verify._VERIFIERS


def test_vercel_mode_is_not_local(monkeypatch):
    for value in ("vercel", "Vercel", " VERCEL "):
        monkeypatch.setenv("AGENCY_DEPLOY_HOST", value)
        assert hosting.vercel_mode() and not hosting.local_mode()
    monkeypatch.setenv("AGENCY_DEPLOY_HOST", "local")
    assert not hosting.vercel_mode() and hosting.local_mode()


async def test_the_local_deploy_refuses_in_vercel_mode(monkeypatch):
    monkeypatch.setenv("AGENCY_DEPLOY_HOST", "vercel")

    async def no_store(_slug):
        raise AssertionError("the store was read")
    monkeypatch.setattr(_store, "get_site", no_store)
    for fn in (deploy.deploy_dry, deploy.deploy_site):
        result = await fn("x")
        assert not result.ok and "agency.publish_vercel" in result.detail


def test_a_box_published_site_is_read_back_but_never_offered_to_a_lead(monkeypatch):
    # The read-back still checks an old box deploy where it is (no UNKNOWN
    # for a site that answers); a lead only ever gets the Vercel domain.
    monkeypatch.setenv("AGENCY_DEPLOY_HOST", "vercel")
    site = {"slug": "old-1", "domain": None, "deployed_at": "2026-10-01"}
    assert deploy.published_domain(site) == hosting.public_host("old-1")
    assert deploy.published_domain({**site, "domain": HOST}) == HOST
    from atlas.modules.agency import outreach
    assert outreach.preview_url({**site, "deployed": True}) is None
    assert outreach.preview_url({**site, "deployed": True, "domain": HOST}) == (
        f"https://{HOST}/")


# --------------------------------------------------------- step 5: the store

async def test_set_site_domain_updates_only_the_domain(monkeypatch):
    seen = []

    async def execute_sql(sql, *args):
        seen.append((" ".join(sql.split()), args))
    monkeypatch.setattr(_store, "execute_sql", execute_sql)
    await _store.set_site_domain(SLUG, HOST)
    await _store.set_site_domain(SLUG, None)
    assert seen == [("UPDATE agency_sites SET domain=$2 WHERE slug=$1", (SLUG, HOST)),
                    ("UPDATE agency_sites SET domain=$2 WHERE slug=$1", (SLUG, None))]


def test_the_shop_still_publishes_on_this_box_in_vercel_mode(monkeypatch):
    from atlas.modules.academy import publish
    monkeypatch.setenv("AGENCY_DEPLOY_HOST", "vercel")
    assert "another host" not in (publish._problem() or "")
    monkeypatch.setenv("AGENCY_DEPLOY_HOST", "box.example")
    assert "another host" in (publish._problem() or "")
