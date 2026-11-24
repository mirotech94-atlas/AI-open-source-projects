"""The QA agent's module (atlas/modules/qa): Tier 1 checks that write only
ATLAS's own tables, UNKNOWN when they cannot run, never a body stored."""
from __future__ import annotations

import logging
import pathlib
from datetime import datetime, timedelta

import httpx
import pytest

from atlas.kernel.contracts import FailureClass, Health, Tier
from atlas.modules import qa
from atlas.modules.qa import browser, security, words


def inserts(fake, table):
    return [(s, a) for s, a in fake.sql_log if f"INSERT INTO {table}" in s]


def test_all_five_actions_are_tier_1_with_dry_run_and_rollback():
    acts = {a.name: a for a in qa.MODULE.actions()}
    assert set(acts) == {"qa.check_draft", "qa.check_site", "qa.security_scan",
                         "qa.verify_runs", "qa.create_remediation"}
    for a in acts.values():
        assert a.tier is Tier.AUTO and a.dry_run and a.rollback


def test_the_words_trailer_is_the_outreach_trailer():
    from atlas.modules.agency import outreach
    assert words.TRAILER == outreach.TRAILER


# ------------------------------------------------------------------ words

@pytest.fixture
def postal(monkeypatch):
    monkeypatch.setenv("AGENCY_POSTAL_ADDRESS", "1 Main St, Newark NJ")


SIGNED = "\n\nATLAS, assistant to the owner at Mapfront\n1 Main St, Newark NJ\nReply STOP"


def test_words_refuse_the_old_price_and_promises(postal):
    for text in ("A site for $100.", "Only $100.00 today", "Guaranteed results",
                 "We are #1 in Newark"):
        assert words.refusals(text + SIGNED, "email"), text
    assert words.refusals("Sites from $500 or $300." + SIGNED, "email") == []


def test_words_refuse_an_unsourced_number_but_not_a_sourced_one(postal):
    assert words.refusals("Sites like yours get 40% more calls." + SIGNED, "email")
    assert words.refusals("Sites like yours get 40% more calls (source: "
                          "https://example.org/study)." + SIGNED, "email") == []


def test_words_need_the_signature_stop_and_disclosure(postal, monkeypatch):
    assert words.refusals("Hello there.", "email")
    assert words.refusals("Hello there.\nOwner", "sms")
    assert words.refusals("Hello. Reply STOP to opt out.", "sms") == []
    assert words.refusals("A post about sites.", "reddit")
    assert words.refusals("A post about sites.\nDisclosure: I run Mapfront.",
                          "reddit") == []
    monkeypatch.delenv("AGENCY_POSTAL_ADDRESS")
    assert any("postal" in f.lower() or "address" in f.lower()
               for f in words.refusals("Hi." + SIGNED, "email"))


# ------------------------------------------------------------- the browser

async def test_screenshots_are_unknown_without_the_service(monkeypatch):
    monkeypatch.delenv("QA_BROWSER_URL", raising=False)
    shot = await browser.shot("https://agency.example.com/", browser.DESKTOP)
    assert not shot.ok and "UNKNOWN" in shot.detail and shot.data == b""


async def test_screenshots_are_unknown_when_the_service_is_down(monkeypatch):
    monkeypatch.setenv("QA_BROWSER_URL", "unix:/run/qa/qa-browser.sock")

    def refuse(request):
        raise httpx.ConnectError("refused")

    client = httpx.AsyncClient(transport=httpx.MockTransport(refuse))
    shot = await browser.shot("https://agency.example.com/", browser.IPHONE, client)
    assert not shot.ok and "UNKNOWN" in shot.detail


async def test_a_tcp_browser_url_is_not_used(monkeypatch):
    """The service has no TCP port: an http:// value is never dialled."""
    monkeypatch.setenv("QA_BROWSER_URL", "http://qa-browser:8091")
    assert browser.socket_path() is None
    shot = await browser.shot("https://agency.example.com/", browser.DESKTOP)
    assert not shot.ok and "UNKNOWN" in shot.detail
    monkeypatch.setenv("QA_BROWSER_URL", "unix:/run/qa/../etc/x")
    assert browser.socket_path() is None
    monkeypatch.setenv("QA_BROWSER_URL", "unix:/run/qa/qa-browser.sock")
    assert browser.socket_path() == "/run/qa/qa-browser.sock"


def test_the_service_allowlist_is_public_https_only():
    from atlas.modules.qa import browser_service as svc
    assert svc.allowed("https://agency.example.com/preview/joes/")
    for internal in ("http://api/x", "https://api/x", "http://shop/", "https://shop/",
                     "http://api:8080/api/health", "https://postgres/",
                     "https://10.0.0.5/", "https://agency.example.com:8443/"):
        assert not svc.allowed(internal), internal
    assert not hasattr(svc, "INTERNAL")
    assert not svc.allowed("http://agency.example.com/")
    assert not svc.allowed("https://evil.example/")
    assert not svc.allowed("https://user:pw@agency.example.com/")
    assert not svc.allowed("http://169.254.169.254/")
    assert svc.width_of("10") is None and svc.width_of("390") == 390


def test_the_service_refuses_redirects_off_the_allowlist():
    from atlas.modules.qa import browser_service as svc
    base = "https://agency.example.com/preview/joes/"
    assert svc.redirect_allowed(base, 302, "/menu")
    assert svc.redirect_allowed(base, 301, "https://agency.example.com/")
    assert not svc.redirect_allowed(base, 302, "http://api:8080/api/session")
    assert not svc.redirect_allowed(base, 307, "https://169.254.169.254/")
    assert not svc.redirect_allowed(base, 302, None)
    assert svc.redirect_allowed(base, 200, None)
    # The final page, every redirect hop and every request are re-checked.
    assert svc.verdict(base, [base], [base, "data:image/png;base64,AA"]) is None
    assert svc.verdict("http://api/x", [base], [base])
    assert svc.verdict(base, ["http://shop/", base], [base])
    assert svc.verdict(base, [base], [base, "http://api:8080/api/health"])


class _FakeContext:
    def __init__(self):
        self.handlers: dict[str, list] = {}
        self.routes: list = []
        self.ws_routes: list = []
        self.scripts: list[str] = []

    def add_init_script(self, script):
        self.scripts.append(script)

    def on(self, event, fn):
        self.handlers.setdefault(event, []).append(fn)

    def route(self, pattern, fn):
        self.routes.append((pattern, fn))

    def route_web_socket(self, pattern, fn):
        self.ws_routes.append((pattern, fn))


class _FakePage:
    closed = False

    def close(self):
        self.closed = True


def test_the_browser_rules_sit_on_the_context_before_any_page():
    """Popups and second pages are held to the same rules: the route, the
    request record and the WebSocket block are on the context."""
    from atlas.modules.qa import browser_service as svc
    guard, ctx = svc.Guard(), _FakeContext()
    guard.install(ctx)
    assert [p for p, _f in ctx.routes] == ["**/*"]
    assert [p for p, _f in ctx.ws_routes] == ["**/*"]
    assert "request" in ctx.handlers and "page" in ctx.handlers
    assert any("RTCPeerConnection" in s for s in ctx.scripts)
    main, popup = _FakePage(), _FakePage()
    for fn in ctx.handlers["page"]:
        fn(main)
        fn(popup)
    assert not main.closed and popup.closed
    assert "popup" in guard.verdict("https://agency.example.com/preview/joes/",
                                    ["https://agency.example.com/preview/joes/"])


def test_no_websocket_is_ever_connected():
    from atlas.modules.qa import browser_service as svc
    guard, ctx = svc.Guard(), _FakeContext()
    guard.install(ctx)

    class WS:
        closed = connected = False

        def close(self):
            self.closed = True

        def connect_to_server(self):
            self.connected = True

    ws = WS()
    ctx.ws_routes[0][1](ws)
    assert ws.closed and not ws.connected
    assert "WebSocket" in guard.verdict("https://agency.example.com/preview/joes/", [])


def test_webrtc_udp_is_disabled_and_a_proxy_always_applies(monkeypatch):
    from atlas.modules.qa import browser_service as svc
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in svc.LAUNCH_ARGS
    assert not any("WebRtcHideLocalIpsWithMdns" in a for a in svc.LAUNCH_ARGS)
    monkeypatch.delenv("QA_PROXY", raising=False)
    opts = svc.launch_options()
    assert opts["proxy"]["server"] == "socks5://127.0.0.1:1"
    assert "--proxy-server=socks5://127.0.0.1:1" in opts["args"]
    assert opts["proxy"]["bypass"] == "<-loopback>"
    for bad in ("", "direct://", "http://", "ftp://x:1", "http://u:p@qa-proxy:3128",
                "http://qa-proxy", "qa-proxy:3128"):
        monkeypatch.setenv("QA_PROXY", bad)
        assert svc.proxy_server() == "socks5://127.0.0.1:1", bad
    monkeypatch.setenv("QA_PROXY", "http://qa-proxy:3128")
    opts = svc.launch_options()
    assert opts["proxy"]["server"] == "http://qa-proxy:3128"
    assert "--proxy-server=http://qa-proxy:3128" in opts["args"]


def test_the_proxy_and_the_browser_share_one_allowlist():
    from atlas.modules.qa import browser_service as svc
    from atlas.modules.qa import egress_proxy as px
    assert px.ALLOWED == svc.ALLOWED


def test_the_proxy_forwards_only_connect_to_allowed_hosts_on_443():
    from atlas.modules.qa import egress_proxy as px
    assert px.target("CONNECT agency.example.com:443 HTTP/1.1") == ("agency.example.com", 443)
    for line in ("CONNECT agency.example.com:80 HTTP/1.1",
                 "CONNECT turn.example.com:443 HTTP/1.1",
                 "CONNECT 10.0.0.5:443 HTTP/1.1",
                 "CONNECT [::1]:443 HTTP/1.1",
                 "GET http://agency.example.com/ HTTP/1.1",
                 "CONNECT evilagency.example.com:443 HTTP/1.1",
                 "CONNECT agency.example.com:443"):
        assert px.target(line) is None, line


def test_the_proxy_dials_only_when_every_address_is_public():
    from atlas.modules.qa import egress_proxy as px
    assert px.public(["93.184.216.34"]) == "93.184.216.34"
    for addrs in ([], ["10.0.0.5"], ["93.184.216.34", "127.0.0.1"],
                  ["169.254.169.254"], ["100.64.0.1"], ["::1"], ["fe80::1"],
                  ["192.168.1.1"], ["172.17.0.1"]):
        assert px.public(addrs) is None, addrs


async def test_the_proxy_refuses_a_private_answer_without_dialling(monkeypatch):
    import asyncio

    from atlas.modules.qa import egress_proxy as px

    async def gai(host):
        return ["93.184.216.34", "10.0.0.5"]

    async def boom(*a, **k):
        raise AssertionError("dialled")

    dial = asyncio.open_connection
    monkeypatch.setattr(px, "resolve", gai)
    monkeypatch.setattr(px.asyncio, "open_connection", boom)
    server = await asyncio.start_server(px.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        for line in (b"CONNECT agency.example.com:443 HTTP/1.1\r\n\r\n",
                     b"GET http://10.0.0.5/ HTTP/1.1\r\n\r\n"):
            reader, writer = await dial("127.0.0.1", port)
            writer.write(line)
            await writer.drain()
            assert (await reader.read(64)).startswith(b"HTTP/1.1 403")
            writer.close()
    finally:
        server.close()
        await server.wait_closed()


def test_the_route_gate_aborts_off_allowlist_and_redirects_off_it():
    from atlas.modules.qa import browser_service as svc
    guard = svc.Guard()

    class Resp:
        def __init__(self, status, location=None):
            self.status, self.headers = status, ({"location": location} if location
                                                 else {})

    class Route:
        def __init__(self, url, resp=None):
            self.request = type("R", (), {"url": url})()
            self.resp, self.did = resp, None

        def continue_(self):
            self.did = "continue"

        def abort(self):
            self.did = "abort"

        def fetch(self, **kw):
            assert kw["max_redirects"] == 0
            return self.resp

        def fulfill(self, response):
            self.did = "fulfill"

    for url, resp, want in (
            ("http://api:8080/api/session", None, "abort"),
            ("data:image/png;base64,AA", None, "continue"),
            ("https://agency.example.com/preview/joes/", Resp(200), "fulfill"),
            ("https://agency.example.com/preview/joes/", Resp(302, "http://10.0.0.5/"), "abort"),
            ("https://agency.example.com/preview/joes/", Resp(302, "/menu"), "fulfill")):
        route = Route(url, resp)
        guard.gate(route)
        assert route.did == want, url


def test_the_egress_deny_is_in_compose_and_configure_says_how_to_check_it():
    import yaml
    root = pathlib.Path(__file__).resolve().parent.parent
    local = yaml.safe_load((root / "docker-compose.local.yml").read_text())
    net = local["networks"]["qa_egress"]
    assert net["internal"] is True
    assert net["driver_opts"]["com.docker.network.bridge.name"] == "atlas-qa"
    configure = (root / "CONFIGURE.md").read_text()
    assert "qa-proxy" in configure and "internal" in configure


async def test_gate_health_is_unknown_when_qa_results_cannot_be_read(memory_backend):
    obs = await qa.check_gate_health()
    assert obs.health is Health.UNKNOWN


async def test_gate_health_degrades_on_a_failure(memory_backend):
    memory_backend.responses["FROM qa_results WHERE superseded_at IS NULL AND"] = {
        "passed": 3, "failed": 1}
    obs = await qa.check_gate_health()
    assert obs.health is Health.DEGRADED and "read" in obs.detail


async def test_gate_health_never_ok_while_the_screenshot_service_is_off(
        memory_backend, monkeypatch):
    memory_backend.responses["FROM qa_results WHERE superseded_at IS NULL AND"] = {
        "passed": 3, "failed": 0}
    monkeypatch.delenv("QA_BROWSER_URL", raising=False)
    obs = await qa.check_gate_health()
    assert obs.health is Health.UNKNOWN and "off" in obs.detail
    monkeypatch.setenv("QA_BROWSER_URL", "unix:/run/qa/qa-browser.sock")
    assert (await qa.check_gate_health()).health is Health.OK


async def test_open_remediation_is_unknown_then_degraded(memory_backend):
    assert (await qa.check_open_remediation()).health is Health.UNKNOWN
    memory_backend.responses["SELECT count(*) AS n FROM project_tasks"] = {"n": 2}
    assert (await qa.check_open_remediation()).health is Health.DEGRADED
    memory_backend.responses["SELECT count(*) AS n FROM project_tasks"] = {"n": 0}
    assert (await qa.check_open_remediation()).health is Health.OK


# --------------------------------------------------------------- security

@pytest.fixture
def no_sockets(monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("a socket was opened")

    monkeypatch.setattr(security, "_tls", boom)
    monkeypatch.setattr(security._netguard, "assert_public", boom)
    monkeypatch.setattr(security._netguard, "resolve_public", boom)


async def test_security_refuses_a_site_not_built_and_not_consented(memory_backend,
                                                                   no_sockets):
    memory_backend.responses["FROM agency_sites WHERE slug"] = {
        "domain": "someone-else.com", "kind": "demo", "security_consent_at": None}
    result = await qa.security_scan("prospect")
    assert not result.ok and result.failure_class is FailureClass.VALIDATION
    assert "consented" in result.detail
    assert not inserts(memory_backend, "qa_results")


async def test_security_refuses_an_unknown_slug(memory_backend, no_sockets):
    memory_backend.responses["FROM agency_sites WHERE slug"] = None
    assert not (await qa.security_scan("nobody")).ok


async def test_a_scan_keeps_no_body_in_the_row_or_the_logs(memory_backend, monkeypatch,
                                                           caplog):
    secret = "BODY-SECRET-1234"

    async def public(host):
        return "93.184.216.34", None

    async def tls(host, ip):
        assert ip == "93.184.216.34"
        return "TLSv1.3", datetime.now().astimezone() + timedelta(days=60)

    def answer(request):
        assert request.url.host == "93.184.216.34"
        assert request.headers["host"] == "agency.example.com"
        assert request.extensions["sni_hostname"] == "agency.example.com"
        if request.url.path == "/":
            return httpx.Response(200, headers={"content-type": "text/html"},
                                  text=f"<html>{secret}<script src='/jquery-1.12.4.min.js'>"
                                       "</script></html>")
        if request.url.path == "/.env":
            return httpx.Response(200, headers={"content-type": "text/plain"},
                                  text=f"KEY={secret}")
        return httpx.Response(404, text="nope")

    monkeypatch.setattr(security._netguard, "resolve_public", public)
    monkeypatch.setattr(security, "_tls", tls)
    real = httpx.AsyncClient

    def client(*a, **k):
        k.pop("transport", None)
        return real(*a, transport=httpx.MockTransport(answer), **k)

    monkeypatch.setattr(security.httpx, "AsyncClient", client)
    memory_backend.responses["INSERT INTO qa_results"] = {"id": 8}
    memory_backend.responses["FROM project_tasks WHERE kind = 'remediation'"] = None
    memory_backend.responses["INSERT INTO project_tasks"] = {"id": 2}
    with caplog.at_level(logging.DEBUG):
        result = await qa.security_scan("shop")
    assert result.ok
    findings = result.evidence["findings"]
    assert any("/.env" in f for f in findings) and any("jquery" in f for f in findings)
    assert secret not in str(memory_backend.sql_log)
    assert secret not in str(result.evidence) and secret not in caplog.text
    stored = inserts(memory_backend, "qa_results")[0][1][5]
    assert '"sha256"' in stored and secret not in stored
    assert len(inserts(memory_backend, "project_tasks")) == 1


async def test_a_scan_resolves_once_and_dials_only_the_pinned_address(
        memory_backend, monkeypatch):
    """DNS rebinding: the host answers public once, private afterwards. The
    scan never asks again; every connection goes to the first answer."""
    from atlas.modules import _netguard
    answers = [["93.184.216.34"], ["10.0.0.5"], ["127.0.0.1"]]
    lookups: list[str] = []

    async def gai(host):
        lookups.append(host)
        return answers[min(len(lookups) - 1, 2)]

    monkeypatch.setattr(_netguard, "_getaddrinfo", gai)
    dialled: list[tuple[str, str]] = []

    async def tls(host, ip):
        dialled.append(("tls", ip))
        return "TLSv1.3", None

    def answer(request):
        dialled.append(("get", request.url.host))
        return httpx.Response(404, text="nope")

    monkeypatch.setattr(security, "_tls", tls)
    client = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    result = await security.scan("shop", client=client)
    await client.aclose()
    assert lookups == ["agency.example.com"]
    assert dialled and {ip for _k, ip in dialled} == {"93.184.216.34"}
    assert result.requests == 1 + len(security.EXPOSED_PATHS)


async def test_a_host_resolving_to_a_private_address_is_never_dialled(monkeypatch):
    from atlas.modules import _netguard

    async def gai(host):
        return ["93.184.216.34", "10.0.0.5"]

    def boom(*a, **k):
        raise AssertionError("dialled")

    async def aboom(*a, **k):
        raise AssertionError("dialled")

    monkeypatch.setattr(_netguard, "_getaddrinfo", gai)
    monkeypatch.setattr(security, "_tls", aboom)
    with pytest.raises(security.Refused, match="private"):
        await security.scan("shop", client=httpx.AsyncClient(
            transport=httpx.MockTransport(boom)))
    assert (await _netguard.resolve_public("169.254.169.254"))[0] is None
    assert await _netguard.resolve_public("8.8.8.8") == ("8.8.8.8", None)


def test_the_scan_never_exceeds_its_request_cap():
    assert 1 + len(security.EXPOSED_PATHS) <= security.MAX_REQUESTS


# ------------------------------------------------------------------- runs

async def test_verify_runs_raises_one_task_per_miss_and_never_retries(memory_backend,
                                                                      monkeypatch):
    due = datetime.now().astimezone() - timedelta(hours=5)
    monkeypatch.setattr(qa, "expected_windows", lambda now: [
        ("weekly_review", "2026-W44", due), ("daily_brief", "2026-10-27", due),
        ("reels", "2026-10-27", due)])

    async def rows(jobs):
        return {("weekly_review", "2026-W44"): "done",
                ("daily_brief", "2026-10-27"): "failed"}

    monkeypatch.setattr(qa, "_run_rows", rows)
    memory_backend.responses["INSERT INTO qa_results"] = {"id": 4}
    memory_backend.responses["FROM project_tasks WHERE kind = 'remediation'"] = None
    memory_backend.responses["INSERT INTO project_tasks"] = {"id": 11}
    result = await qa.verify_runs()
    assert result.ok and len(result.evidence["missed"]) == 2
    tasks = inserts(memory_backend, "project_tasks")
    assert len(tasks) == 2
    refs = {a[8] for _s, a in tasks}
    assert refs == {"daily_brief:2026-10-27", "reels:2026-10-27"}
    # No retry: nothing touches schedule_runs or any job.
    assert not any("schedule_runs" in s for s, _a in memory_backend.sql_log)


def test_expected_windows_are_due_in_the_last_day_and_late_enough():
    now = datetime.now().astimezone()
    for _job, _key, due in qa.expected_windows(now):
        assert now - timedelta(days=1) <= due <= now - qa.RUNS_LATE


def _utc(y, m, d, h, mi=0):
    from datetime import UTC
    return datetime(y, m, d, h, mi, tzinfo=UTC)


def test_expected_windows_ignore_jobs_switched_off(monkeypatch):
    now = _utc(2026, 11, 17, 23)
    monkeypatch.delenv("BRIEF_DAILY", raising=False)
    monkeypatch.delenv("YOUTUBE_LEARN", raising=False)
    off = {j for j, _k, _d in qa.expected_windows(now)}
    assert "brief" not in off and "youtube_learn" not in off
    monkeypatch.setenv("BRIEF_DAILY", "on")
    on = {j for j, _k, _d in qa.expected_windows(now)}
    assert "brief" in on                      # on and never ran: still a miss


def test_expected_windows_use_new_york_for_ideas_and_playlist():
    # 19:09 UTC is 14:09 New York: the 16:30 New York scan is not yet due
    # (it was reported "never ran" when the window was read in UTC).
    early = {(j, k) for j, k, _d in qa.expected_windows(_utc(2026, 11, 17, 19, 9))}
    assert ("ideas", "2026-11-17") not in early
    late = {(j, k): d for j, k, d in qa.expected_windows(_utc(2026, 11, 17, 23, 59))}
    assert late[("ideas", "2026-11-17")] == _utc(2026, 11, 17, 21, 30)
    sun = {(j, k): d for j, k, d in qa.expected_windows(_utc(2026, 11, 15, 23, 59))}
    assert sun[("playlist", "2026-11-15")] == _utc(2026, 11, 15, 20, 0)


# ------------------------------------------------------------ remediation

async def test_remediation_dedupes_while_a_task_is_open(memory_backend):
    memory_backend.responses["FROM project_tasks WHERE kind = 'remediation'"] = {"id": 5}
    result = await qa.create_remediation("seo", "Fix the title", {"url": "x"},
                                         "shorten it", "qa.check_site", "site:joes")
    assert result.ok and result.evidence == {"task_id": 5, "deduped": True}
    assert not inserts(memory_backend, "project_tasks")


async def test_remediation_adds_a_task_with_a_rollback(memory_backend):
    memory_backend.responses["FROM project_tasks WHERE kind = 'remediation'"] = None
    memory_backend.responses["INSERT INTO project_tasks"] = {"id": 6}
    result = await qa.create_remediation("seo", "Fix the title", None, "",
                                         "qa.check_site", "site:joes")
    assert result.ok and result.evidence["task_id"] == 6
    assert result.rollback_token.startswith("task:")


async def test_a_rollback_supersedes_never_deletes(memory_backend):
    memory_backend.responses["UPDATE qa_results SET superseded_at"] = {"id": 3}
    result = await qa.rollback("qa:3")
    assert result.ok and "superseded" in result.detail
    assert not any("DELETE" in s for s, _a in memory_backend.sql_log)
    assert not (await qa.rollback("nonsense")).ok


# ------------------------------------------------------------------ drafts

async def test_check_draft_refuses_a_kind_that_does_not_match(memory_backend):
    memory_backend.responses["FROM agency_outreach WHERE id"] = {
        "kind": "email", "to_address": "a@b.c", "subject": "Hi", "body": "Hello"}
    result = await qa.check_draft("sms", "agency_outreach:3")
    assert not result.ok and result.failure_class is FailureClass.VALIDATION
    assert not inserts(memory_backend, "qa_results")


async def test_check_draft_records_a_failure_and_raises_a_task(memory_backend, postal):
    memory_backend.responses["FROM agency_outreach WHERE id"] = {
        "kind": "email", "to_address": "a@b.c", "subject": "Hi",
        "body": "A site for $100, guaranteed."}
    memory_backend.responses["INSERT INTO qa_results"] = {"id": 9}
    memory_backend.responses["FROM project_tasks WHERE kind = 'remediation'"] = None
    memory_backend.responses["INSERT INTO project_tasks"] = {"id": 1}
    result = await qa.check_draft("outreach", "agency_outreach:3")
    assert result.ok and result.evidence["passed"] is False
    (_s, args), = inserts(memory_backend, "qa_results")
    assert args[0] == "draft" and args[3] is False
    assert "A site for" not in str(args)


async def test_check_draft_refuses_an_unknown_ref(memory_backend):
    result = await qa.check_draft("email", "something:1")
    assert not result.ok and result.failure_class is FailureClass.VALIDATION


async def test_gate_health_remediation_dedupes_whatever_the_ref(memory_backend):
    memory_backend.responses["FROM project_tasks WHERE kind = 'remediation'"] = {"id": 55}
    result = await qa.create_remediation("ops", "Set QA_BROWSER_URL", None, "",
                                         "qa.gate_health", "run-2026-11-20T19:55")
    assert result.evidence == {"task_id": 55, "deduped": True}
    assert not inserts(memory_backend, "project_tasks")
