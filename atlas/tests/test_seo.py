"""SEO & Growth (six agents phase 10): audit and draft only.

An unfetchable page is UNKNOWN, never OK; no free search provider means
"not measured"; blog posts and on-page changes are Tier 2 cards behind the
QA gate; the model's text is escaped by code (it never writes HTML); paths
are built in code from slugs that match `^[a-z0-9-]{1,60}$`; the paid AI
visibility check raises nothing while `SEO_PAID_CHECKS` is off; Search
Console reads UNKNOWN with the reason until the scope and property exist.
No test here reaches the network."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from atlas.kernel.contracts import Health, Tier
from atlas.modules import _http, _perplexity, _scrape, seo
from atlas.modules.agency import launchcheck, livecheck
from atlas.modules.seo import audit, blog, gsc, store

SHOP = "academy-store"


def _returns(value):
    async def fake(*_a, **_kw):
        return value
    return fake


class Fake:
    """An in-memory seo store; every function the package calls."""

    def __init__(self, root: Path) -> None:
        site = root / "joes"
        site.mkdir()
        self.sites = {
            "joes": {"slug": "joes", "domain": "joes.example", "path": str(site),
                     "client": "Joe's Bakery"},
            SHOP: {"slug": SHOP, "domain": "agency.example.com", "path": None,
                   "client": "ATLAS Academy"},
        }
        self.audits: dict[int, dict[str, Any]] = {}
        self.kws: dict[int, dict[str, Any]] = {}
        self.ranks: dict[int, dict[str, Any]] = {}
        self.drafts: dict[int, dict[str, Any]] = {}
        self.changes: dict[int, dict[str, Any]] = {}
        self.cards: list[tuple[str, dict[str, Any], str]] = []
        self.month_count = 0

    async def site(self, slug):
        return self.sites.get(slug)

    async def targets(self):
        return list(self.sites.values())

    async def insert_audit(self, slug, url, status, summary, items):
        aid = len(self.audits) + 1
        self.audits[aid] = {"id": aid, "slug": slug, "url": url, "status": status,
                            "summary": summary, "items": items}
        return aid

    async def withdraw_audit(self, aid):
        self.audits[int(aid)]["status"] = "withdrawn"

    async def add_keywords(self, slug, keywords):
        ids = []
        for k in keywords:
            if any(r["slug"] == slug and r["keyword"] == k for r in self.kws.values()):
                continue
            kid = len(self.kws) + 1
            self.kws[kid] = {"id": kid, "slug": slug, "keyword": k,
                             "volume": "not measured", "status": "active"}
            ids.append(kid)
        return ids

    async def withdraw_keywords(self, ids):
        for i in ids:
            self.kws[int(i)]["status"] = "withdrawn"

    async def keywords(self, slug, limit):
        return [r["keyword"] for r in self.kws.values()
                if r["slug"] == slug and r["status"] == "active"][:limit]

    async def insert_rank(self, slug, keyword, provider, position, url, measured,
                          reason):
        rid = len(self.ranks) + 1
        self.ranks[rid] = {"id": rid, "slug": slug, "keyword": keyword,
                           "provider": provider, "position": position, "url": url,
                           "measured": measured, "reason": reason}
        return rid

    async def withdraw_ranks(self, ids):
        for i in ids:
            self.ranks.pop(int(i), None)

    async def last_measured_rank_at(self):
        return None

    async def blog_count_month(self, slug):
        return self.month_count

    async def blog_counts_month(self):
        return {"joes": self.month_count}

    async def insert_draft(self, slug, post_slug, title, body):
        did = len(self.drafts) + 1
        self.drafts[did] = {"id": did, "kind": "seo_blog",
                            "source": f"seo:{slug}:{post_slug}", "title": title,
                            "body": body, "status": "draft"}
        return did

    async def draft(self, did):
        return self.drafts.get(int(did))

    async def set_draft(self, did, status):
        self.drafts[int(did)]["status"] = status

    async def insert_change(self, slug, page, spec):
        cid = len(self.changes) + 1
        self.changes[cid] = {"id": cid, "slug": slug, "page": page, "spec": spec,
                             "status": "proposed", "backup": None}
        return cid

    async def change(self, cid):
        return self.changes.get(int(cid))

    async def set_change(self, cid, status, backup=None):
        self.changes[int(cid)].update(status=status)
        if backup is not None:
            self.changes[int(cid)]["backup"] = backup

    async def raise_card(self, action, params, reason, client, text, dedup):
        self.cards.append((action, params, f"{reason}\n{text}"))
        return True


@pytest.fixture
def fake(monkeypatch, tmp_path):
    f = Fake(tmp_path)
    for name in dir(Fake):
        if not name.startswith("_"):
            monkeypatch.setattr(store, name, getattr(f, name))
    for key in ("BRAVE_API_KEY", "GOOGLE_CSE_KEY", "GOOGLE_CSE_CX", seo.PAID_ENV):
        monkeypatch.delenv(key, raising=False)
    from atlas.modules.projects import store as pstore
    tasks: list[dict[str, Any]] = []

    async def add_task(**kw):
        tasks.append(kw)
        return len(tasks), f"t{len(tasks)}"

    async def restore(token):
        return token
    monkeypatch.setattr(pstore, "add_task", add_task)
    monkeypatch.setattr(pstore, "restore", restore)
    f.tasks = tasks
    f.root = Path(f.sites["joes"]["path"])
    return f


def _acts():
    return {a.name: a for a in seo.MODULE.actions()}


def _page(title: str = "Joe's Bakery") -> str:
    return (f"<!doctype html><html><head><title>{title}</title>"
            '<meta name="description" content="Fresh bread in Queens">'
            '<script src="site.js"></script></head><body><h1>Fresh bread</h1>'
            '<img src="loaf.jpg" alt=""><p>Open daily.</p></body></html>')


# ------------------------------------------------------------- the catalog

def test_the_catalog_tiers_and_the_gate():
    from atlas.kernel import qa_gate
    acts = _acts()
    for name in ("seo.audit_site", "seo.keyword_research", "seo.rank_snapshot",
                 "seo.draft_blog_post", "seo.propose_change"):
        assert acts[name].tier is Tier.AUTO and acts[name].rollback, name
    for name in ("seo.publish_blog_post", "seo.apply_change", "seo.ai_visibility_check"):
        assert acts[name].tier is Tier.ASK and acts[name].card_only, name
    for name in ("seo.publish_blog_post", "seo.apply_change"):
        assert name in qa_gate.GATED_NAMES and name in qa_gate.RESOLVERS, name


def test_the_four_tables_exist():
    sql = Path("db/schema.sql").read_text()
    for table in ("seo_audits", "seo_keywords", "seo_ranks", "seo_changes"):
        assert f"CREATE TABLE IF NOT EXISTS {table} (" in sql, table


# ------------------------------------------------------------------ audit

async def test_an_unfetchable_page_is_never_ok(fake, monkeypatch):
    monkeypatch.setattr(livecheck, "fetch_site", _returns(launchcheck.LiveSite(
        "https://agency.example.com/", {}, {}, "was not probed",
        ("https://agency.example.com/: timed out",))))
    got = await audit.audit_site(SHOP)
    row = fake.audits[got.evidence["audit_id"]]
    assert row["status"] == "unknown" and "timed out" in row["summary"]
    assert "UNKNOWN" in got.detail and "OK" not in got.detail.replace("UNKNOWN", "")
    assert audit.health_of(row["status"]) is Health.UNKNOWN
    assert audit.health_of("ok") is Health.OK
    assert audit.health_of("issues") is Health.DEGRADED


async def test_a_read_page_is_audited_and_stamped(fake, monkeypatch):
    monkeypatch.setattr(livecheck, "fetch_site", _returns(launchcheck.LiveSite(
        "https://agency.example.com/", {"index.html": _page()}, {}, "404")))
    got = await audit.audit_site(SHOP)
    assert got.ok and "(read 20" in got.detail
    assert fake.audits[1]["status"] in ("ok", "issues")
    undo = await audit.withdraw_audit(got.rollback_token)
    assert undo.ok and fake.audits[1]["status"] == "withdrawn"


# ------------------------------------------------------- keywords and ranks

async def test_no_provider_means_not_measured(fake, monkeypatch):
    async def fetch(url, *, agent, client=""):
        return _scrape.Scraped(url, url, 200, "fresh bread queens bakery fresh bread "
                               "queens bakery sourdough loaves", datetime.now(UTC))
    monkeypatch.setattr(_scrape, "fetch", fetch)
    kw = await audit.keyword_research("joes")
    assert kw.ok and "not measured" in kw.detail
    assert all(r["volume"] == "not measured" for r in fake.kws.values())
    got = await audit.rank_snapshot("joes")
    assert got.ok and fake.ranks
    for row in fake.ranks.values():
        assert row["measured"] is False and row["position"] is None
        assert row["reason"].startswith("not measured: no free search provider")
    assert "not measured" in got.detail


async def test_a_rank_is_the_first_result_on_the_site(fake, monkeypatch):
    fake.kws[1] = {"id": 1, "slug": "joes", "keyword": "bakery queens",
                   "volume": "not measured", "status": "active"}
    monkeypatch.setenv("BRAVE_API_KEY", "x")

    async def search(query, *, agent, limit=10):
        assert agent == "seo"
        return [{"url": "https://other.example/"},
                {"url": "https://www.joes.example/menu"}], None
    monkeypatch.setattr(_scrape, "search", search)
    await audit.rank_snapshot("joes")
    (row,) = fake.ranks.values()
    assert row["measured"] is True and row["position"] == 2 and row["provider"] == "brave"


# --------------------------------------------------------------- blog posts

@pytest.mark.parametrize("bad", ["", "A", "a\n", "../x", "a/b", "a" * 61, "a_b", "é"])
def test_a_slug_outside_the_pattern_is_refused(bad):
    assert blog.valid_slug(bad) is False
    assert blog.valid_slug("fresh-bread-2") is True


def test_model_output_is_escaped_by_code():
    out = blog.render_post("Joe's <b>Bakery</b>", "Best <script>alert(1)</script> bread",
                           "# Our bread\n\nWe bake <script>alert(1)</script> daily.\n\n"
                           "- rye & spelt\n- sourdough", stylesheet="styles.css")
    assert "alert(1)</script>" not in out and "<b>" not in out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out
    assert "<h1>Our bread</h1>" in out and "<li>rye &amp; spelt</li>" in out


async def test_two_posts_a_month_per_client(fake, monkeypatch):
    fake.month_count = 2
    for fn in (blog.draft_blog_post_dry, blog.draft_blog_post):
        got = await fn("joes", "fresh bread")
        assert not got.ok and "2 blog posts" in got.detail


async def _draft(fake, monkeypatch, body: str):
    async def fetch(url, *, agent, client=""):
        return _scrape.Scraped(url, url, 200, "Joe's Bakery, fresh bread in Queens.",
                               datetime.now(UTC))
    monkeypatch.setattr(_scrape, "fetch", fetch)
    seen: dict[str, Any] = {}

    async def write(system, prompt):
        seen["prompt"] = prompt
        return f"Fresh bread in Queens\n\n{body}", None
    monkeypatch.setattr(blog, "_write", write)
    got = await blog.draft_blog_post("joes", "fresh bread")
    return got, seen


async def test_a_draft_wraps_the_site_text_and_raises_one_card(fake, monkeypatch):
    got, seen = await _draft(fake, monkeypatch, "We bake every morning.")
    assert got.ok and fake.drafts[1]["source"] == "seo:joes:fresh-bread"
    assert "UNTRUSTED" in seen["prompt"] or "untrusted" in seen["prompt"].lower()
    assert [c[0] for c in fake.cards] == ["seo.publish_blog_post"]
    undo = await blog.drop_draft(got.rollback_token)
    assert undo.ok and fake.drafts[1]["status"] == "dropped"


async def test_publish_without_a_qa_pass_raises_no_card(memory_backend, monkeypatch):
    from atlas.kernel import approvals, qa_gate
    from atlas.kernel.permissions import classify
    from atlas.kernel.registry import REGISTRY
    action = _acts()["seo.publish_blog_post"]
    monkeypatch.setitem(REGISTRY.actions, action.name, action)
    monkeypatch.setitem(REGISTRY.decisions, action.name, classify(action))
    memory_backend.responses["FROM content_drafts WHERE id"] = {
        "title": "Best bakery", "body": "We are the #1 bakery, guaranteed."}
    memory_backend.responses["FROM qa_results WHERE subject_ref"] = None
    memory_backend.responses["INSERT INTO qa_results"] = {"id": 3}
    memory_backend.responses["FROM project_tasks WHERE kind = 'remediation'"] = None
    memory_backend.responses["INSERT INTO project_tasks"] = {"id": 8}
    with pytest.raises(qa_gate.QaRequired):
        await approvals.propose("seo.publish_blog_post", {"draft_id": 4}, "publish")
    assert not [s for s, _ in memory_backend.sql_log if "INSERT INTO proposals" in s]


async def test_publish_writes_our_file_keeps_the_old_one_and_records_a_task(
        fake, monkeypatch):
    (fake.root / "styles.css").write_text("body{}")
    fake.drafts[1] = {"id": 1, "kind": "seo_blog", "source": "seo:joes:fresh-bread",
                      "title": "Fresh bread", "body": "Hello <script>x</script>",
                      "status": "draft"}
    path = fake.root / "blog" / "fresh-bread.html"
    path.parent.mkdir()
    path.write_text("old post")
    got = await blog.publish_blog_post(1)
    assert got.ok and "&lt;script&gt;" in path.read_text()
    assert fake.drafts[1]["status"] == "published"
    (task,) = fake.tasks
    assert task["kind"] == "site_change" and "qa.check_site" in task["suggested_fix"]
    undo = await blog.unpublish(got.rollback_token)
    assert undo.ok and path.read_text() == "old post"
    assert fake.drafts[1]["status"] == "draft"


async def test_publish_refuses_a_bad_post_slug_or_a_link(fake):
    fake.drafts[1] = {"id": 1, "kind": "seo_blog", "source": "seo:joes:../../etc",
                      "title": "t", "body": "b", "status": "draft"}
    assert not (await blog.publish_blog_post(1)).ok
    fake.drafts[2] = {"id": 2, "kind": "seo_blog", "source": "seo:joes:ok",
                      "title": "t", "body": "b", "status": "draft"}
    outside = fake.root.parent / "outside"
    outside.mkdir()
    (fake.root / "blog").symlink_to(outside)
    got = await blog.publish_blog_post(2)
    assert not got.ok and not list(outside.iterdir())


async def test_a_new_post_rollback_removes_only_our_file(fake):
    fake.drafts[1] = {"id": 1, "kind": "seo_blog", "source": "seo:joes:new-post",
                      "title": "t", "body": "b", "status": "draft"}
    got = await blog.publish_blog_post(1)
    path = fake.root / "blog" / "new-post.html"
    assert path.exists()
    assert (await blog.unpublish(got.rollback_token)).ok and not path.exists()


# ------------------------------------------------------------ page changes

async def test_a_spec_key_outside_the_allowlist_is_refused(fake):
    for spec in ({"script": "x"}, {"title": "x", "onload": "y"}, {}):
        got = await blog.propose_change("joes", "index.html", spec)
        assert not got.ok, spec
    assert not (await blog.propose_change("joes", "../index.html", {"title": "x"})).ok
    got = await blog.propose_change("joes", "index.html",
                                    {"title": "Joe's", "meta_description": "Bread"})
    assert got.ok and [c[0] for c in fake.cards] == ["seo.apply_change"]


async def test_apply_change_escapes_and_rolls_back(fake):
    page = fake.root / "index.html"
    page.write_text(_page())
    fake.changes[1] = {"id": 1, "slug": "joes", "page": "index.html", "status": "proposed",
                       "backup": None,
                       "spec": {"title": "Joe's <Bakery>", "meta_description": 'A "b"',
                                "h1": "Bread & rolls", "alt": {"loaf.jpg": "A loaf"}}}
    got = await blog.apply_change(1)
    html = page.read_text()
    assert got.ok and "<title>Joe&#x27;s &lt;Bakery&gt;</title>" in html
    assert 'content="A &quot;b&quot;"' in html and "<h1>Bread &amp; rolls</h1>" in html
    assert 'alt="A loaf"' in html
    assert fake.changes[1]["status"] == "applied" and fake.tasks
    assert not list(fake.root.glob("*prev*"))
    assert (await blog.revert_change(got.rollback_token)).ok
    assert page.read_text() == _page() and fake.changes[1]["status"] == "reverted"


# -------------------------------------------------------------- paid check

async def test_the_paid_check_off_raises_nothing_and_calls_nothing(fake, monkeypatch):
    calls: list[str] = []

    async def call(*a, **kw):
        calls.append("call")
        return None, "no"
    monkeypatch.setattr(_perplexity, "call", call)
    from atlas.kernel import approvals
    proposed: list[str] = []

    async def propose(*a, **kw):
        proposed.append(a[0])
        return 1
    monkeypatch.setattr(approvals, "propose", propose)
    assert not (await seo.offer_ai_visibility("joes")).ok
    assert not (await seo.ai_visibility_check_dry("joes")).ok
    assert not (await seo.ai_visibility_check("joes")).ok
    assert calls == [] and proposed == [] and fake.cards == []


async def test_the_paid_check_on_names_its_calls_on_the_card(fake, monkeypatch):
    monkeypatch.setenv(seo.PAID_ENV, "on")
    fake.kws[1] = {"id": 1, "slug": "joes", "keyword": "bakery queens",
                   "volume": "not measured", "status": "active"}
    got = await seo.offer_ai_visibility("joes")
    assert got.ok
    (card,) = fake.cards
    assert card[0] == "seo.ai_visibility_check" and "1 paid Perplexity call" in card[2]


def test_the_roster_reads_perplexity_only_with_the_switch(monkeypatch):
    from atlas.kernel.agents import PERPLEXITY_READS, ROSTER, build_roster
    from atlas.kernel.tools import TOOLS
    from atlas.tools.builtin import register_all
    register_all()
    monkeypatch.delenv(seo.PAID_ENV, raising=False)
    build_roster()
    agent = ROSTER["seo"]
    assert not (agent.tools & PERPLEXITY_READS)
    assert all(TOOLS[t].action is None for t in agent.tools if t in TOOLS)
    monkeypatch.setenv(seo.PAID_ENV, "on")
    build_roster()
    assert PERPLEXITY_READS <= ROSTER["seo"].tools
    monkeypatch.delenv(seo.PAID_ENV)
    build_roster()


# ----------------------------------------------------------- Search Console

async def test_search_console_without_credentials_is_unknown(fake, monkeypatch):
    for key in gsc.ENV:
        monkeypatch.delenv(key, raising=False)
    obs = await gsc.check()
    assert obs.health is Health.UNKNOWN and "GOOGLE_REFRESH_TOKEN" in obs.detail


class StubGoogle:
    def __init__(self, scope: str, sites: list[dict] | None,
                 sites_reason: str | None = None) -> None:
        self.scope, self.sites, self.sites_reason = scope, sites, sites_reason

    async def post(self, url, **kw):
        if "oauth2" in url:
            return {"access_token": "tok", "expires_in": 3600, "scope": self.scope}, None
        if "urlInspection" in url:
            return {"inspectionResult": {"indexStatusResult": {"verdict": "PASS"}}}, None
        return {"rows": [{"clicks": 7, "impressions": 90}]}, None

    async def get(self, url, **kw):
        if self.sites_reason:
            return None, self.sites_reason
        if url.endswith("/sitemaps"):
            return {"sitemap": [{"path": "/sitemap.xml", "errors": "0"}]}, None
        return {"siteEntry": self.sites}, None


@pytest.fixture
def google_env(monkeypatch):
    for key in gsc.ENV:
        monkeypatch.setenv(key, "x")
    monkeypatch.setattr(gsc, "_token", None)


async def test_search_console_without_the_scope_is_unknown(fake, google_env, monkeypatch):
    g = StubGoogle("https://www.googleapis.com/auth/gmail.modify", [])
    monkeypatch.setattr(_http, "post_json", g.post)
    monkeypatch.setattr(_http, "get_json", g.get)
    obs = await gsc.check()
    assert obs.health is Health.UNKNOWN and "webmasters.readonly" in obs.detail


async def test_search_console_a_403_reads_as_the_missing_scope(fake, google_env,
                                                               monkeypatch):
    g = StubGoogle("", None, sites_reason="HTTP 403 Forbidden")
    monkeypatch.setattr(_http, "post_json", g.post)
    monkeypatch.setattr(_http, "get_json", g.get)
    obs = await gsc.check()
    assert obs.health is Health.UNKNOWN and "webmasters.readonly" in obs.detail


async def test_search_console_an_unverified_property_is_unknown(fake, google_env,
                                                                monkeypatch):
    g = StubGoogle(gsc.SCOPE, [
        {"siteUrl": "sc-domain:joes.example", "permissionLevel": "siteUnverifiedUser"}])
    monkeypatch.setattr(_http, "post_json", g.post)
    monkeypatch.setattr(_http, "get_json", g.get)
    obs = await gsc.check()
    assert obs.health is Health.UNKNOWN
    assert "agency.example.com" in obs.detail and "joes.example" in obs.detail


async def test_search_console_all_verified_reads_clicks_with_the_time(
        fake, google_env, monkeypatch):
    g = StubGoogle(gsc.SCOPE, [
        {"siteUrl": "sc-domain:joes.example", "permissionLevel": "siteOwner"},
        {"siteUrl": "https://agency.example.com/", "permissionLevel": "siteFullUser"}])
    monkeypatch.setattr(_http, "post_json", g.post)
    monkeypatch.setattr(_http, "get_json", g.get)
    obs = await gsc.check()
    assert obs.health is Health.OK and obs.value == 14 and "(read 20" in obs.detail


# ------------------------------------------------------------------ wiring

def test_seo_sits_in_sales_with_its_job_and_owner():
    from atlas.kernel import departments, missions, scheduler
    dept = {d.key: d for d in departments.DEPARTMENTS}["sales"]
    assert "seo" in dept.agents and "seo" in dept.modules and "seo_rank" in dept.jobs
    assert "seo_rank" in departments.JOBS
    for ask in ("run an seo audit of joes", "check search console",
                "keyword research for the bakery"):
        assert missions.owner_for(ask).agent == "seo", ask
    assert missions.owner_for("build them a website").agent == "sites"
    assert "seo_rank" in scheduler.POLICIES and "seo_rank" in scheduler._DETACHED
    windows = scheduler._seo_rank_windows(datetime(2026, 11, 3, 15, tzinfo=UTC),
                                          scheduler.POLICIES["seo_rank"].horizon)
    at = windows[-1].scheduled_for
    assert at.hour == 7 and str(at.tzinfo) == "America/New_York"


async def test_the_daily_job_acts_only_through_the_executor(fake, monkeypatch):
    from atlas.kernel import executor
    seen: list[str] = []

    async def execute(req):
        seen.append(req.action)
        from atlas.kernel.contracts import ActionResult
        return ActionResult(True, "ok")
    monkeypatch.setattr(executor, "execute", execute)
    out = await seo.run_daily(datetime(2026, 11, 2, 12, tzinfo=UTC))   # a Monday
    assert set(seen) == {"seo.rank_snapshot", "seo.audit_site", "seo.weekly_report",
                         "seo.keyword_gaps"}
    assert out
