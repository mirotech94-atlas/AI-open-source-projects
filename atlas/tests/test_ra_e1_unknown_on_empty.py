"""RA-E1: a check whose source returned nothing it could read says UNKNOWN.

Invariant 7: a check that cannot run reports UNKNOWN, never OK. Each of these
checks used to turn an empty, absent or malformed payload into a clean bill
of health -- "all 0 subsystems healthy", "all 0 runners online", "0
disputes" from a response with no list in it, "inbox clear" off a page the
selectors no longer fit. Every fix here is paired with a positive control:
the well-formed (and, where it is real, the genuinely empty) answer still
reads OK, so a check that simply always said UNKNOWN could not pass.
"""
from __future__ import annotations

import pytest

from atlas.kernel.contracts import Health

# -------------------------------------------------------------------- gitlab

def _gitlab(responses):
    from atlas.modules import gitlab as g

    async def api(inst, path, **params):
        return responses[path]
    return g, api


async def test_gitlab_no_visible_runners_is_unknown_not_all_online(monkeypatch):
    g, api = _gitlab({"runners/all": []})
    monkeypatch.setattr(g, "_api", api)
    inst = g.Instance("business", "https://git.example", "GITLAB_TOKEN_TEST")
    obs = await g.runners_online(inst)
    assert obs.health is Health.UNKNOWN
    assert "all 0 runners" not in obs.detail


async def test_gitlab_runners_online_still_reads_ok(monkeypatch):
    g, api = _gitlab({"runners/all": [
        {"status": "online", "active": True, "description": "r1"}]})
    monkeypatch.setattr(g, "_api", api)
    inst = g.Instance("business", "https://git.example", "GITLAB_TOKEN_TEST")
    obs = await g.runners_online(inst)
    assert obs.health is Health.OK and "all 1 runners online" in obs.detail


async def test_gitlab_no_visible_projects_is_unknown_not_zero_failures(
        monkeypatch):
    g, api = _gitlab({"projects": []})
    monkeypatch.setattr(g, "_api", api)
    inst = g.Instance("business", "https://git.example", "GITLAB_TOKEN_TEST")
    obs = await g.failed_pipelines(inst)
    assert obs.health is Health.UNKNOWN
    assert "no projects" in obs.detail


# -------------------------------------------------------------------- stripe

@pytest.fixture
def stripe(monkeypatch):
    from atlas.modules import stripe_billing as s
    monkeypatch.setenv("STRIPE_GUARDIAN_KEY", "rk_test_placeholder")
    return s


def _stripe_get(s, monkeypatch, payload):
    async def get(business, path, **params):
        return payload
    monkeypatch.setattr(s, "_get", get)


@pytest.mark.parametrize("fn", ["failed_payments", "disputes",
                                "webhook_health", "mrr", "expiring_cards"])
async def test_stripe_list_with_no_data_is_unknown(stripe, monkeypatch, fn):
    _stripe_get(stripe, monkeypatch, {"object": "list"})
    obs = await getattr(stripe, fn)("guardian")
    assert obs.health is Health.UNKNOWN, obs
    assert "no data list" in obs.detail


@pytest.mark.parametrize("fn", ["failed_payments", "disputes",
                                "webhook_health", "mrr"])
async def test_stripe_well_formed_empty_list_is_still_a_real_zero(
        stripe, monkeypatch, fn):
    _stripe_get(stripe, monkeypatch, {"object": "list", "data": []})
    obs = await getattr(stripe, fn)("guardian")
    assert obs.health is Health.OK
    assert obs.value == 0


async def test_stripe_customers_with_no_readable_card_is_unknown(
        stripe, monkeypatch):
    # An unexpanded default_payment_method arrives as a bare id.
    _stripe_get(stripe, monkeypatch, {"data": [
        {"id": "cus_1", "invoice_settings": {"default_payment_method": "pm_1"}},
        {"id": "cus_2", "invoice_settings": {}}]})
    obs = await stripe.expiring_cards("guardian")
    assert obs.health is Health.UNKNOWN
    assert "2 customer(s)" in obs.detail


async def test_stripe_expiring_cards_positive_controls(stripe, monkeypatch):
    _stripe_get(stripe, monkeypatch, {"data": []})
    assert (await stripe.expiring_cards("guardian")).health is Health.OK
    _stripe_get(stripe, monkeypatch, {"data": [{"id": "cus_1", "invoice_settings": {
        "default_payment_method": {"card": {"exp_year": 2099, "exp_month": 1}}}}]})
    obs = await stripe.expiring_cards("guardian")
    assert obs.health is Health.OK and obs.evidence["examined"] == 1


# -------------------------------------------------------------------- fiverr

class _Locator:
    def __init__(self, n: int, texts: list[str] | None = None):
        self.n, self.texts = n, texts or []

    async def count(self):
        return self.n

    async def all_text_contents(self):
        return self.texts

    async def all(self):
        return [object()] * self.n


class _Page:
    def __init__(self, url: str, locators: dict[str, _Locator]):
        self.url, self.locators = url, locators

    async def goto(self, *_a, **_k):
        return None

    async def title(self):
        return "Fiverr"

    async def content(self):
        return "<html>a real page</html>"

    async def wait_for_timeout(self, _ms):
        return None

    def locator(self, selector: str):
        for key, loc in self.locators.items():
            if key in selector:
                return loc
        raise AssertionError(selector)


def _fiverr(monkeypatch, tmp_path, page):
    from atlas.modules import fiverr

    class Ctx:
        async def new_page(self):
            return page

    class Closer:
        async def close(self):
            return None

        async def stop(self):
            return None

    async def browser():
        return Closer(), Closer(), Ctx()
    monkeypatch.setattr(fiverr, "_browser", browser)
    monkeypatch.setattr(fiverr, "WALL", tmp_path / "wall.json")
    return fiverr


async def test_fiverr_inbox_matching_nothing_is_unknown(monkeypatch, tmp_path):
    page = _Page("https://www.fiverr.com/inbox",
                 {"unread": _Locator(0), "conversation-time": _Locator(0, [])})
    fiverr = _fiverr(monkeypatch, tmp_path, page)
    obs = await fiverr.check_unanswered_messages()
    assert obs.health is Health.UNKNOWN
    assert "inbox clear" not in obs.detail


async def test_fiverr_inbox_with_conversations_and_none_unread_is_clear(
        monkeypatch, tmp_path):
    page = _Page("https://www.fiverr.com/inbox",
                 {"unread": _Locator(0),
                  "conversation-time": _Locator(2, ["3 days", "5 days"])})
    fiverr = _fiverr(monkeypatch, tmp_path, page)
    obs = await fiverr.check_unanswered_messages()
    assert obs.health is Health.OK and obs.detail == "inbox clear"


async def test_fiverr_orders_matching_nothing_is_unknown(monkeypatch, tmp_path):
    page = _Page("https://www.fiverr.com/users/me/manage_orders",
                 {"order-id": _Locator(0)})
    fiverr = _fiverr(monkeypatch, tmp_path, page)
    obs = await fiverr.check_active_orders()
    assert obs.health is Health.UNKNOWN
    assert "0 active order" not in obs.detail


# --------------------------------------------------------------------- cloudco

async def test_cloudco_auth_proves_a_cached_token_before_saying_ok(monkeypatch):
    from atlas.modules import cloudco

    monkeypatch.setattr(cloudco, "_token", ("jwt", 9e12))
    asked: list[str] = []

    async def token():
        return "jwt"

    async def refused(path, **params):
        asked.append(path)
        raise cloudco.CloudCoRejected("token expired")
    monkeypatch.setattr(cloudco, "_access_token", token)
    monkeypatch.setattr(cloudco, "_get", refused)
    obs = await cloudco.check_auth()
    assert asked == ["/vm"]
    assert obs.health is Health.UNKNOWN
    assert "cached token" in obs.detail

    async def fine(path, **params):
        return {"data": []}
    monkeypatch.setattr(cloudco, "_get", fine)
    obs = await cloudco.check_auth()
    assert obs.health is Health.OK and "authenticated read" in obs.detail


async def test_cloudco_ledger_with_no_total_is_unknown(monkeypatch):
    from atlas.modules import cloudco

    async def get(path, **params):
        return {"data": [{"hostname": "a"}]}
    monkeypatch.setattr(cloudco, "_get", get)
    assert (await cloudco.check_resource_ledger()).health is Health.UNKNOWN

    async def with_total(path, **params):
        return {"data": [{"hostname": "a"}], "total": 1}
    monkeypatch.setattr(cloudco, "_get", with_total)
    assert (await cloudco.check_resource_ledger()).health is Health.OK


def _cloudco_protection(monkeypatch, *, vm, policy):
    from atlas.modules import cloudco

    async def get(path, **params):
        return {"/snapshot": {"data": [{"identifier": "s1", "status": "ok"}]},
                "/backups": {"data": [{"identifier": "b1"}]},
                "/vm": vm, "/backups/policy/all": policy}[path]
    monkeypatch.setattr(cloudco, "_get", get)
    return cloudco


POLICY = {"data": {"rows": [{"name": "daily", "vmsCount": 1, "planEvery": 1,
                             "backupPlan": "day", "keep": 7,
                             "created_on": "2026-01-01T00:00:00Z"}]}}


async def test_cloudco_protection_unreadable_policy_is_not_ok(monkeypatch):
    cloudco = _cloudco_protection(monkeypatch, vm={"data": [{"hostname": "a"}]},
                              policy={"data": "nope"})
    obs = await cloudco.check_protection()
    assert obs.health is Health.UNKNOWN
    assert "policy unreadable" in obs.detail
    assert "first scheduled backup" not in obs.detail


async def test_cloudco_protection_unreadable_vm_list_is_not_full_coverage(
        monkeypatch):
    cloudco = _cloudco_protection(monkeypatch, vm={"weird": 1}, policy=POLICY)
    obs = await cloudco.check_protection()
    assert obs.health is Health.UNKNOWN
    assert "all 0 instances" not in obs.detail


async def test_cloudco_protection_readable_policy_still_reads_ok(monkeypatch):
    cloudco = _cloudco_protection(monkeypatch, vm={"data": [{"hostname": "a"}]},
                              policy=POLICY)
    obs = await cloudco.check_protection()
    assert obs.health is Health.OK, obs.detail


# ------------------------------------------------------------------ exampleco

def _ebay(monkeypatch, payload):
    from atlas.modules import exampleco

    async def api(path, **params):
        return payload
    monkeypatch.setattr(exampleco, "_api", api)
    return exampleco


@pytest.mark.parametrize("fn", ["check_listings", "check_orders",
                                "check_stale_listings"])
async def test_ebay_response_without_its_list_is_unknown(monkeypatch, fn):
    exampleco = _ebay(monkeypatch, {"href": "x"})
    assert (await getattr(exampleco, fn)()).health is Health.UNKNOWN


@pytest.mark.parametrize("fn", ["check_listings", "check_orders",
                                "check_stale_listings"])
async def test_ebay_total_zero_is_a_real_empty(monkeypatch, fn):
    exampleco = _ebay(monkeypatch, {"total": 0})
    assert (await getattr(exampleco, fn)()).health is Health.OK


async def test_ebay_listing_age_that_is_never_present_is_unknown(monkeypatch):
    exampleco = _ebay(monkeypatch, {"offers": [
        {"sku": "a", "status": "PUBLISHED", "listing": {"listingId": "1"}}]})
    obs = await exampleco.check_stale_listings()
    assert obs.health is Health.UNKNOWN
    assert "no" in obs.detail and "listing age" in obs.detail


# -------------------------------------------------------------------- tiktok

class _Resp:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self.body


def _tiktok(monkeypatch, body):
    import httpx

    from atlas.modules import tiktok

    async def token():
        return "t"

    async def post(self, *_a, **_k):
        return _Resp(body)
    monkeypatch.setattr(tiktok, "_access_token", token)
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    return tiktok


async def test_tiktok_error_envelope_is_unknown_not_zero_views(monkeypatch):
    tiktok = _tiktok(monkeypatch, {"data": {}, "error": {
        "code": "access_token_invalid", "message": "x"}})
    assert (await tiktok.check_video_stats()).health is Health.UNKNOWN
    tiktok = _tiktok(monkeypatch, {"data": {}, "error": {"code": "ok"}})
    assert (await tiktok.check_video_stats()).health is Health.UNKNOWN


async def test_tiktok_well_formed_video_list_reads_ok(monkeypatch):
    tiktok = _tiktok(monkeypatch, {"data": {"videos": [
        {"view_count": 10, "create_time": 1}]}, "error": {"code": "ok"}})
    obs = await tiktok.check_video_stats()
    assert obs.health is Health.OK and obs.value == 10


# ---------------------------------------------------------- instagram DMs

async def test_instagram_thread_that_could_not_be_read_is_reported(monkeypatch):
    from atlas.modules.messages import instagram_dm

    async def token(page_id, user_token):
        return "pt", None

    async def get_json(url, **kw):
        if url.endswith("/conversations"):
            return {"data": [{"id": "c1"}, {"id": "c2"}]}, None
        if url.endswith("/c1"):
            return {"messages": {"data": [
                {"id": "m1", "from": {"id": "them"}, "message": "hi"}]}}, None
        return None, "HTTP 500"
    monkeypatch.setattr(instagram_dm, "_page_token", token)
    monkeypatch.setattr(instagram_dm, "get_json", get_json)
    rows, reason = await instagram_dm._recent_one("main", "me", "ut", "pg",
                                                  limit=25)
    assert [r["external_id"] for r in rows] == ["m1"]
    assert reason == "1 of 2 conversation(s) could not be read"


# ------------------------------------------------------------------ linkedin

async def test_linkedin_unreadable_page_raises_rather_than_reading_empty():
    from atlas.modules.linkedin import sweep

    class Broken:
        async def evaluate(self, _js):
            raise RuntimeError("page crashed")

    with pytest.raises(RuntimeError):
        await sweep._profile_links(Broken())
    with pytest.raises(RuntimeError):
        await sweep._conversations(Broken())


# ---------------------------------------------------------------- nightshift

def _docker(monkeypatch, out: bytes, err: bytes, code: int):
    from atlas.modules.nightshift import sandbox

    class Proc:
        returncode = code

        async def communicate(self):
            return out, err

    async def exec_(*_a, **_k):
        return Proc()
    monkeypatch.setattr(sandbox.asyncio, "create_subprocess_exec", exec_)
    return sandbox


async def test_docker_daemon_down_is_unavailable_not_missing(monkeypatch):
    sandbox = _docker(monkeypatch, b"", b"Cannot connect to the Docker daemon "
                      b"at unix:///var/run/docker.sock.", 1)
    with pytest.raises(sandbox.DockerUnavailable):
        await sandbox.status("nightshift-1")
    sandbox = _docker(monkeypatch, b"", b"Error: No such object: nightshift-1", 1)
    assert await sandbox.status("nightshift-1") == "missing"


async def test_a_paused_container_is_not_a_finished_job(monkeypatch):
    sandbox = _docker(monkeypatch, b"paused|0", b"", 0)
    assert await sandbox.status("nightshift-1") == "paused"


async def test_jobs_check_leaves_an_unsettled_job_open(monkeypatch):
    from datetime import UTC, datetime

    import atlas.modules.nightshift as ns

    finished: list[int] = []

    async def fetch_all(sql, *args):
        return [{"id": 7, "container": "nightshift-7", "at": datetime.now(UTC),
                 "task": "t", "repo": "r", "branch": "b"}]

    async def status(container):
        return "restarting"

    async def finish(job_id, *a, **k):
        finished.append(job_id)

    async def refresh(now):
        return None
    monkeypatch.setattr(ns, "fetch_all", fetch_all, raising=False)
    monkeypatch.setattr(ns.sandbox, "status", status)
    monkeypatch.setattr(ns, "_finish_job", finish)
    monkeypatch.setattr(ns, "_refresh_recent_lines", refresh)
    obs = await ns.check_jobs()
    assert finished == []
    assert obs.health is Health.UNKNOWN
    assert "#7 restarting" in obs.detail
