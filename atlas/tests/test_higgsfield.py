"""Higgsfield as the reels' overflow generator (the owner, 2026-09-29): Veo first;
past Veo's daily allowance, Higgsfield under a hard monthly dollar cap.
Every HTTP call is mocked; the credentials are fakes."""
from __future__ import annotations

import json
from collections import OrderedDict
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from atlas.kernel.contracts import Health
from atlas.modules import _netguard
from atlas.modules.content import calendar as cal
from atlas.modules.content import higgsfield as hf
from atlas.modules.content import veo

KEY_ID = "fake-key-id-1234"
SECRET = "fake-secret-abcdef-5678"
STATUS = "https://api.higgsfield.ai/requests/rid-1/status"
VIDEO = "https://cdn.higgsfield.example/out/rid-1.mp4"


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv(hf.KEY_ID_ENV, KEY_ID)
    monkeypatch.setenv(hf.SECRET_ENV, SECRET)
    for name in ("HIGGSFIELD_URL", "HIGGSFIELD_MONTHLY_USD",
                 "HIGGSFIELD_USD_PER_SECOND_MAX", "HIGGSFIELD_RESOLUTION",
                 "VEO_RESOLUTION"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(hf, "POLL_S", 0)
    _resolve(monkeypatch, {})


PRIVATE_HOSTS = {"inside.example": "127.0.0.1"}


def _resolve(monkeypatch, extra):
    """Every test host resolves to a public address, unless named here."""
    table = {**PRIVATE_HOSTS, **extra}

    async def resolve(host):
        return [table.get(host, "93.184.216.34")]

    monkeypatch.setattr(_netguard, "_getaddrinfo", resolve)
    monkeypatch.setattr(_netguard, "_CACHE", OrderedDict())


@pytest.fixture
def no_keys(monkeypatch):
    monkeypatch.delenv(hf.KEY_ID_ENV, raising=False)
    monkeypatch.delenv(hf.SECRET_ENV, raising=False)


def _transport(monkeypatch, handler):
    real = httpx.AsyncClient

    def factory(*a, **k):
        k.pop("transport", None)
        return real(*a, transport=httpx.MockTransport(handler), **k)

    monkeypatch.setattr(hf.httpx, "AsyncClient", factory)


def _spent(memory_backend, usd):
    memory_backend.responses["FROM higgsfield_usage"] = {"usd": Decimal(str(usd))}


# ================================================================ one clip

async def test_submits_with_the_key_header_polls_and_downloads_without_it(
        keys, monkeypatch, memory_backend):
    calls: list[tuple[str, str, str | None]] = []
    polls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization")
        calls.append((request.method, str(request.url), auth))
        assert KEY_ID not in str(request.url) and SECRET not in str(request.url)
        if request.url.path == f"/{hf.MODEL}":
            assert json.loads(request.content) == {
                "prompt": "a scene", "duration": 8, "resolution": "1080p",
                "aspect_ratio": "9:16", "generate_audio": True}
            return httpx.Response(200, json={
                "status": "queued", "request_id": "rid-1", "status_url": STATUS,
                "cancel_url": STATUS.replace("status", "cancel")})
        if str(request.url) == STATUS:
            polls["n"] += 1
            if polls["n"] == 1:
                return httpx.Response(200, json={"status": "queued"})
            if polls["n"] == 2:
                return httpx.Response(502)                    # retried, not fatal
            if polls["n"] == 3:
                return httpx.Response(200, json={"status": "in_progress"})
            return httpx.Response(200, json={"status": "completed",
                                             "request_id": "rid-1",
                                             "video": {"url": VIDEO}})
        if str(request.url) == VIDEO:
            return httpx.Response(200, content=b"MP4")
        return httpx.Response(500)

    _transport(monkeypatch, handler)
    assert await hf.clip("a scene") == b"MP4"
    submit, *rest = calls
    assert submit[2] == f"Key {KEY_ID}:{SECRET}"               # the documented form
    assert [c for c in rest if c[1] == STATUS] and all(
        c[2] == f"Key {KEY_ID}:{SECRET}" for c in rest if c[1] == STATUS)
    download = [c for c in calls if c[1] == VIDEO]
    assert download == [("GET", VIDEO, None)], "the CDN never gets the key"
    assert memory_backend.sql_log == [], "clip() charges nothing itself"


async def test_a_status_url_on_another_host_is_never_sent_the_key(keys, monkeypatch):
    seen = []

    def handler(request):
        seen.append(request.url.host)
        return httpx.Response(200, json={
            "status": "queued", "request_id": "rid-1",
            "status_url": "https://evil.example/requests/rid-1/status"})

    _transport(monkeypatch, handler)
    with pytest.raises(hf.HiggsfieldError) as info:
        await hf.clip("a scene")
    assert seen == ["api.higgsfield.ai"]
    assert info.value.charged is True, "accepted, so it may be billed"


async def test_an_error_never_carries_the_key_or_secret(keys, monkeypatch):
    _transport(monkeypatch, lambda r: httpx.Response(
        401, json={"detail": f"bad {KEY_ID}:{SECRET}"}))
    with pytest.raises(hf.HiggsfieldError) as info:
        await hf.clip("a scene")
    text = str(info.value)
    assert KEY_ID not in text and SECRET not in text
    assert "401" in text and "credentials rejected" in text
    assert info.value.charged is False and info.value.refused is False


@pytest.mark.parametrize("status,refused", [("failed", True), ("nsfw", True),
                                            ("canceled", False)])
async def test_an_uncharged_ending_says_so(keys, monkeypatch, status, refused):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"status": "queued", "request_id": "r",
                                             "status_url": STATUS})
        return httpx.Response(200, json={"status": status, "error": "no"})

    _transport(monkeypatch, handler)
    with pytest.raises(hf.HiggsfieldError) as info:
        await hf.clip("a scene")
    assert info.value.charged is False and info.value.refused is refused
    assert status in str(info.value)


async def test_a_poll_deadline_keeps_the_reservation(keys, monkeypatch):
    monkeypatch.setattr(hf, "DEADLINE_S", -1)

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"status": "queued", "request_id": "r",
                                             "status_url": STATUS})
        return httpx.Response(200, json={"status": "in_progress"})

    _transport(monkeypatch, handler)
    with pytest.raises(hf.HiggsfieldError) as info:
        await hf.clip("a scene")
    assert info.value.charged is True


async def test_a_submit_that_timed_out_is_not_repeated_and_stays_charged(
        keys, monkeypatch):
    posts = []

    def handler(request):
        posts.append(request)
        raise httpx.ReadTimeout("slow", request=request)

    _transport(monkeypatch, handler)
    with pytest.raises(hf.HiggsfieldError) as info:
        await hf.clip("a scene")
    assert len(posts) == 1 and info.value.charged is True


async def test_a_submit_5xx_is_ambiguous_so_it_stays_charged(keys, monkeypatch):
    _transport(monkeypatch, lambda r: httpx.Response(500, json={"detail": "oops"}))
    with pytest.raises(hf.HiggsfieldError) as info:
        await hf.clip("a scene")
    assert info.value.charged is True and info.value.status_code == 500


async def test_a_submit_that_never_connected_is_not_charged(keys, monkeypatch):
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    _transport(monkeypatch, handler)
    with pytest.raises(hf.HiggsfieldError) as info:
        await hf.clip("a scene")
    assert info.value.charged is False


async def test_the_same_guard_as_veo_runs_before_any_request(keys, monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("a refused prompt must never reach Higgsfield")

    monkeypatch.setattr(hf.httpx, "AsyncClient", no_network)
    with pytest.raises(hf.HiggsfieldError) as info:
        await hf.clip("Pixar-style talking cars")
    assert "refused before Higgsfield" in str(info.value)
    assert info.value.charged is False and info.value.refused is True


# ================================================================ the cap

def _estimate(usd: str | None, status: int = 200):
    def handler(request):
        assert request.url.path == f"/estimate/{hf.MODEL}"
        assert request.headers["authorization"] == f"Key {KEY_ID}:{SECRET}"
        return httpx.Response(status, json={"credits": "12.6", "usd": usd}
                              if status < 400 else {"detail": "nope"})
    return handler


async def test_a_clip_that_would_cross_the_cap_is_never_started(
        keys, monkeypatch, memory_backend):
    _spent(memory_backend, "19.50")
    _transport(monkeypatch, _estimate("0.79"))
    price, why = await hf.reserve("a scene", job_id="j", part="p")
    assert price is None and "$19.50 of $20.00" in why and "read" in why
    assert not any("INSERT INTO higgsfield_usage" in s
                   for s, _ in memory_backend.sql_log)


async def test_a_reservation_is_the_estimates_price_in_its_own_ledger(
        keys, monkeypatch, memory_backend):
    _spent(memory_backend, "5")
    _transport(monkeypatch, _estimate("0.79"))
    price, why = await hf.reserve("a scene", job_id="j", part="p")
    assert why is None and price == hf.Price(Decimal("0.79"), "estimate")
    inserts = [(s, a) for s, a in memory_backend.sql_log
               if "INSERT INTO higgsfield_usage" in s]
    assert inserts and inserts[0][1][0] == Decimal("0.79")
    assert not any("veo_usage" in s for s, _ in memory_backend.sql_log)


@pytest.mark.parametrize("res,usd", [("720p", "3.000"), ("1080p", "3.000"),
                                     ("4k", "12.000")])
async def test_no_estimate_books_the_worst_case_marked_not_read(
        keys, monkeypatch, memory_backend, res, usd):
    """Invariant 6: no price named is "price not read", never a figure
    presented as fact. What is booked is a worst case per second x the
    exact seconds x a resolution factor that never discounts (4k is 4x)."""
    monkeypatch.setenv("HIGGSFIELD_RESOLUTION", res)
    _spent(memory_backend, "0")
    _transport(monkeypatch, _estimate(None, status=404))
    price, why = await hf.reserve("a scene", job_id="j", part="p")
    assert why is None and price.read is False and price.usd == Decimal(usd)
    assert (price.seconds, price.resolution) == (8, res)
    assert "price not read" in price.said() and "worst case" in price.said()
    inserts = [a for s, a in memory_backend.sql_log
               if "INSERT INTO higgsfield_usage" in s]
    assert inserts == [(Decimal(usd), hf.MODEL, "j", "p", 8, res, False)]


@pytest.mark.parametrize("answer", [
    httpx.Response(200, json={"credits": "1"}),               # no usd
    httpx.Response(200, json=["0.79"]),                       # not an object
    httpx.Response(200, json={"usd": "0"}),                   # not a price
    httpx.Response(200, content=b"not json"),
    httpx.Response(302, headers={"location": "https://elsewhere.example/"}),
    httpx.Response(500)])
async def test_anything_but_a_named_price_is_not_read(keys, monkeypatch, answer):
    _transport(monkeypatch, lambda r: answer)
    price = await hf.estimate("a scene")
    assert price.read is False and price.usd == hf.worst_case_usd("1080p")


async def test_the_cap_refusal_says_price_not_read(keys, monkeypatch,
                                                    memory_backend):
    monkeypatch.setenv("HIGGSFIELD_RESOLUTION", "4k")
    _spent(memory_backend, "9")
    _transport(monkeypatch, _estimate(None, status=404))
    price, why = await hf.reserve("a scene")
    assert price is None and "price not read" in why and "$12.00" in why
    assert "$9.00 of $20.00" in why and "UTC" in why
    assert not any("INSERT" in s for s, _ in memory_backend.sql_log)


async def test_a_rejected_key_is_never_priced_by_guess(keys, monkeypatch,
                                                       memory_backend):
    _spent(memory_backend, "0")
    _transport(monkeypatch, _estimate(None, status=403))
    price, why = await hf.reserve("a scene")
    assert price is None and "insufficient credits" in why
    assert not any("INSERT" in s for s, _ in memory_backend.sql_log)


def test_the_cap_defaults_to_twenty_dollars(monkeypatch):
    monkeypatch.delenv("HIGGSFIELD_MONTHLY_USD", raising=False)
    assert hf.monthly_cap() == Decimal(20)
    monkeypatch.setenv("HIGGSFIELD_MONTHLY_USD", "junk")
    assert hf.monthly_cap() == Decimal(20)


# ================================================================ the check

async def test_the_check_is_unknown_without_keys(no_keys):
    obs = await hf.check_budget()
    assert obs.health is Health.UNKNOWN and hf.KEY_ID_ENV in obs.detail


async def test_the_check_is_unknown_when_the_api_is_unreachable(
        keys, monkeypatch, memory_backend):
    _spent(memory_backend, "1")

    def handler(request):
        raise httpx.ConnectError("down", request=request)

    _transport(monkeypatch, handler)
    obs = await hf.check_budget()
    assert obs.health is Health.UNKNOWN and "unreachable" in obs.detail


async def test_the_check_is_unknown_when_the_ledger_is_unreadable(
        keys, monkeypatch):
    async def broken():
        raise RuntimeError("db down")

    monkeypatch.setattr(hf, "ledger", broken)
    _transport(monkeypatch, _with_probe(_estimate("0.79")))
    obs = await hf.check_budget()
    assert obs.health is Health.UNKNOWN and "unreadable" in obs.detail


@pytest.mark.parametrize("status,reason", [(401, "credentials rejected"),
                                           (403, "insufficient credits")])
async def test_a_rejected_key_is_failing_with_the_reason_never_the_key(
        keys, monkeypatch, memory_backend, status, reason):
    _spent(memory_backend, "1")
    _transport(monkeypatch, lambda r: httpx.Response(
        status, json={"detail": f"key {KEY_ID} secret {SECRET}"}))
    obs = await hf.check_budget()
    assert obs.health is Health.FAILING and reason in obs.detail
    assert KEY_ID not in obs.detail and SECRET not in obs.detail


async def test_the_check_reports_spend_cap_and_read_time(keys, monkeypatch,
                                                         memory_backend):
    _spent(memory_backend, "4.2")
    _transport(monkeypatch, _with_probe(_estimate("0.79")))
    obs = await hf.check_budget()
    assert obs.health is Health.OK
    assert "$4.20 of $20.00" in obs.detail and "UTC" in obs.detail
    assert obs.evidence["read_at"]
    _spent(memory_backend, "19.9")
    assert (await hf.check_budget()).health is Health.DEGRADED


def test_the_check_is_registered():
    from atlas.modules import content
    names = [c.name for c in content.ContentModule().checks()]
    assert "higgsfield_budget" not in names   # the owner 2026-11-13: removed
    assert {"HIGGSFIELD_KEY_ID", "HIGGSFIELD_KEY_SECRET",
            "HIGGSFIELD_MONTHLY_USD"} <= set(content.ContentModule.optional_env)


# ================================================================ overflow

@pytest.fixture
def bank(monkeypatch, tmp_path, keys):
    """A calendar job with its Veo and Higgsfield doors faked."""
    monkeypatch.setattr(cal, "clip_dir", lambda job_id: str(tmp_path / job_id))
    log: dict[str, list] = {"veo": [], "hf": [], "hf_record": [], "veo_record": []}

    async def room():
        return None

    async def veo_clip(prompt):
        log["veo"].append(prompt)
        return b"veo"

    async def veo_record(seconds, media_id):
        log["veo_record"].append(seconds)

    async def reserve(prompt, *, job_id=None, part=None):
        return hf.Price(Decimal("0.80"), "estimate"), None

    async def hf_clip(prompt):
        log["hf"].append(prompt)
        return b"hf"

    async def hf_record(usd, *, job_id=None, part=None, **_kw):
        log["hf_record"].append(usd)

    async def run(argv, timeout=None):
        Path(argv[-1]).write_bytes(b"normalised")
        return b""

    monkeypatch.setattr(cal, "_reserve_clip", room)
    monkeypatch.setattr(veo, "clip", veo_clip)
    monkeypatch.setattr(veo, "record", veo_record)
    monkeypatch.setattr(hf, "reserve", reserve)
    monkeypatch.setattr(hf, "clip", hf_clip)
    monkeypatch.setattr(hf, "record", hf_record)
    monkeypatch.setattr(cal.ffmpeg, "run", run)
    j = cal.jobs()[0]
    assert len(cal.missing(j)) >= 4
    return j, log


async def test_higgsfield_makes_only_what_veos_daily_allowance_cannot(bank):
    j, log = bank
    want = len(cal.missing(j))
    made, stopped, refused = await cal._bank(j, 2)
    assert (made, refused) == (want, False)
    assert len(log["veo"]) == 2 and len(log["hf"]) == want - 2
    assert cal.missing(j) == []
    assert cal.higgsfield_count(j) == want - 2, "each clip records its backend"
    parts = cal.plan(j)
    assert not Path(cal.higgsfield_marker(j["job_id"], parts[0])).exists()
    marker = Path(cal.higgsfield_marker(j["job_id"], parts[2])).read_text()
    assert hf.MODEL in marker and "0.80" in marker
    assert Path(cal.raw_path(j["job_id"], parts[2])).read_bytes() == b"normalised"
    assert f"{want - 2} clip(s) made on Higgsfield" in stopped


async def test_no_overflow_while_veo_has_allowance_left(bank):
    j, log = bank
    want = len(cal.missing(j))
    made, _, _ = await cal._bank(j, 99)
    assert made == want and log["hf"] == []


async def test_a_daily_quota_429_overflows_but_a_plain_429_waits(bank, monkeypatch):
    j, log = bank

    async def plain(prompt):
        raise veo.VeoError("slow down", status_code=429)

    monkeypatch.setattr(veo, "clip", plain)
    made, stopped, _ = await cal._bank(j, 10)
    assert made == 0 and log["hf"] == [] and "resumes next tick" in stopped

    async def daily(prompt):
        raise veo.VeoError("per day", status_code=429, daily_quota=True)

    monkeypatch.setattr(veo, "clip", daily)
    made, stopped, _ = await cal._bank(j, 10)
    assert made == len(log["hf"]) > 0 and "daily quota" in stopped


async def test_veos_monthly_cap_is_not_an_overflow(bank, monkeypatch):
    j, log = bank

    async def capped():
        return "Veo cap: 600/600 s used this month"

    monkeypatch.setattr(cal, "_reserve_clip", capped)
    made, stopped, _ = await cal._bank(j, 10)
    assert made == 0 and log["hf"] == [] and "Veo cap" in stopped


async def test_no_overflow_without_keys(bank, monkeypatch):
    j, log = bank
    monkeypatch.delenv(hf.KEY_ID_ENV)
    made, stopped, _ = await cal._bank(j, 0)
    assert made == 0 and log["hf"] == [] and stopped is None


async def test_the_cap_stops_overflow_in_words(bank, monkeypatch):
    j, log = bank
    calls = {"n": 0}

    async def reserve(prompt, *, job_id=None, part=None):
        calls["n"] += 1
        if calls["n"] > 1:
            return None, "Higgsfield cap: $19.60 of $20.00 spent this month"
        return hf.Price(Decimal("0.80"), "estimate"), None

    monkeypatch.setattr(hf, "reserve", reserve)
    made, stopped, refused = await cal._bank(j, 0)
    assert made == 1 and len(log["hf"]) == 1 and refused is False
    assert "Higgsfield cap" in stopped


async def test_an_uncharged_failure_is_refunded_and_a_refusal_strikes(
        bank, monkeypatch):
    j, log = bank

    async def nsfw(prompt):
        raise hf.HiggsfieldError("Higgsfield ended nsfw", charged=False,
                                 refused=True)

    monkeypatch.setattr(hf, "clip", nsfw)
    made, stopped, refused = await cal._bank(j, 0)
    assert made == 0 and refused is True and "refused" in stopped
    assert log["hf_record"] == [Decimal("-0.80")]


async def test_a_charged_failure_keeps_the_reservation_and_does_not_strike(
        bank, monkeypatch):
    j, log = bank

    async def slow(prompt):
        raise hf.HiggsfieldError("did not finish", charged=True)

    monkeypatch.setattr(hf, "clip", slow)
    made, _, refused = await cal._bank(j, 0)
    assert made == 0 and refused is False and log["hf_record"] == []


async def test_the_dry_run_names_the_overflow(bank, monkeypatch):
    j, _ = bank

    async def spent(now=None):
        return cal.daily_clips()

    monkeypatch.setattr(cal, "clips_today", spent)
    result = await cal.render_job_dry(j["job_id"])
    assert result.ok and "on Higgsfield" in result.detail and "$20.00" in result.detail


def test_calendar_prompts_pass_the_higgsfield_door():
    """The packaged prompts already pass Veo's guard; Higgsfield uses the
    same one, so overflow never refuses what Veo would have made."""
    from atlas.modules.content import guard
    for j in cal.jobs():
        for part in cal.plan(j):
            assert guard.ip_refusal(cal.clip_prompt(part)) is None


# ================================================================ review fixes

PROBE = f"/requests/{hf.NIL_REQUEST}/status"


def _with_probe(inner, probe_status: int = 404):
    """The key probe answers `probe_status`; everything else goes to `inner`."""
    def handler(request):
        if request.method == "GET" and request.url.path == PROBE:
            assert request.headers["authorization"] == f"Key {KEY_ID}:{SECRET}"
            return httpx.Response(probe_status, json={"detail": "Not found"})
        return inner(request)
    return handler


@pytest.mark.parametrize("probe,health", [
    (200, Health.UNKNOWN), (302, Health.UNKNOWN), (500, Health.UNKNOWN),
    (422, Health.UNKNOWN), (401, Health.FAILING), (403, Health.FAILING)])
async def test_the_check_is_ok_only_when_the_probe_confirms_the_key(
        keys, monkeypatch, memory_backend, probe, health):
    """Invariant 7: a price and a ledger are not a confirmed key. Only the
    documented 404 for the nil request reads as "key accepted"."""
    _spent(memory_backend, "1")
    _transport(monkeypatch, _with_probe(_estimate("0.79"), probe))
    obs = await hf.check_budget()
    assert obs.health is health
    assert obs.evidence["key_confirmed"] is False
    assert KEY_ID not in obs.detail and SECRET not in obs.detail
    assert "UTC" in obs.detail


async def test_the_probe_confirms_the_key_then_the_check_is_ok(
        keys, monkeypatch, memory_backend):
    _spent(memory_backend, "1")
    _transport(monkeypatch, _with_probe(_estimate("0.79")))
    obs = await hf.check_budget()
    assert obs.health is Health.OK and obs.evidence["key_confirmed"] is True
    assert "key accepted" in obs.detail and "$0.79" in obs.detail


async def test_an_unread_price_makes_the_check_unknown_never_ok(
        keys, monkeypatch, memory_backend):
    _spent(memory_backend, "1")
    _transport(monkeypatch, _with_probe(_estimate(None, status=404)))
    obs = await hf.check_budget()
    assert obs.health is Health.UNKNOWN and "price not read" in obs.detail
    assert "clip_usd" not in obs.evidence and obs.evidence["price_read"] is False


async def test_the_check_names_spend_booked_without_a_price(
        keys, monkeypatch, memory_backend):
    memory_backend.responses["FROM higgsfield_usage"] = {
        "usd": Decimal("6.79"), "unread_usd": Decimal("6.00")}
    _transport(monkeypatch, _with_probe(_estimate("0.79")))
    obs = await hf.check_budget()
    assert "$6.79 of $20.00" in obs.detail
    assert "$6.00 is a worst case" in obs.detail and "not read" in obs.detail
    assert obs.evidence["unread_usd"] == "6.00"


async def test_a_plain_http_base_url_sends_nothing(keys, monkeypatch,
                                                   memory_backend):
    monkeypatch.setenv("HIGGSFIELD_URL", "http://api.higgsfield.ai")

    def no_network(*a, **k):
        raise AssertionError("the key must never travel over http")

    monkeypatch.setattr(hf.httpx, "AsyncClient", no_network)
    with pytest.raises(hf.HiggsfieldError, match="https") as info:
        await hf.clip("a scene")
    assert info.value.charged is False
    price, why = await hf.reserve("a scene")
    assert price is None and "https" in why
    _spent(memory_backend, "0")
    obs = await hf.check_budget()
    assert obs.health is Health.UNKNOWN and "https" in obs.detail


def _accepted_then(status_body, download=None):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"status": "queued", "request_id": "r",
                                             "status_url": STATUS})
        if str(request.url) == STATUS:
            return httpx.Response(200, json=status_body)
        if download is not None:
            return download(request)
        return httpx.Response(500)
    return handler


@pytest.mark.parametrize("status_body", [["not", "an", "object"], "completed", 7])
async def test_a_status_body_that_is_not_an_object_is_a_charged_error(
        keys, monkeypatch, status_body):
    _transport(monkeypatch, _accepted_then(status_body))
    with pytest.raises(hf.HiggsfieldError) as info:
        await hf.clip("a scene")
    assert info.value.charged is True, "accepted, so the reservation stays"


def _done():
    return {"status": "completed", "video": {"url": VIDEO}}


async def test_a_download_redirect_off_https_is_not_followed(keys, monkeypatch):
    seen = []

    def cdn(request):
        seen.append(str(request.url))
        if request.url.host == "cdn.higgsfield.example":
            return httpx.Response(302, headers={"location": "http://plain.example/c"})
        return httpx.Response(200, content=b"NOT-THE-CLIP")

    _transport(monkeypatch, _accepted_then(_done(), cdn))
    with pytest.raises(hf.HiggsfieldError, match="left https") as info:
        await hf.clip("a scene")
    assert info.value.charged is True
    assert seen == [VIDEO], "the http hop is never requested"
    assert "rid-1.mp4" not in str(info.value)


async def test_a_download_redirect_to_a_private_host_is_not_followed(
        keys, monkeypatch):
    seen = []

    def cdn(request):
        seen.append(request.url.host)
        if request.url.host == "cdn.higgsfield.example":
            return httpx.Response(307, headers={
                "location": "https://inside.example/api/memory"})
        return httpx.Response(200, content=b"SECRET")

    _transport(monkeypatch, _accepted_then(_done(), cdn))
    with pytest.raises(hf.HiggsfieldError, match="public web") as info:
        await hf.clip("a scene")
    assert info.value.charged is True and seen == ["cdn.higgsfield.example"]


async def test_a_download_follows_public_https_hops_without_the_key(
        keys, monkeypatch):
    seen = []

    def cdn(request):
        seen.append((request.url.host, request.headers.get("authorization")))
        if request.url.host == "cdn.higgsfield.example":
            return httpx.Response(302, headers={
                "location": "https://edge.cdn.example/signed?t=abc"})
        return httpx.Response(200, content=b"MP4")

    _transport(monkeypatch, _accepted_then(_done(), cdn))
    assert await hf.clip("a scene") == b"MP4"
    assert seen == [("cdn.higgsfield.example", None), ("edge.cdn.example", None)]


async def test_a_download_redirect_loop_stops(keys, monkeypatch):
    def cdn(request):
        return httpx.Response(302, headers={"location": VIDEO})

    _transport(monkeypatch, _accepted_then(_done(), cdn))
    with pytest.raises(hf.HiggsfieldError, match="redirected more") as info:
        await hf.clip("a scene")
    assert info.value.charged is True


@pytest.mark.parametrize("where", ["reserve", "clip", "bank"])
async def test_an_unexpected_error_in_overflow_is_a_failed_overflow(
        bank, monkeypatch, caplog, where):
    """calendar._overflow never lets a surprise from the Higgsfield path
    crash render_job: it is logged and reported, and nothing is refunded
    (whether Higgsfield charged is unknown)."""
    j, log = bank

    async def boom(*a, **k):
        raise AttributeError(f"'list' object has no attribute 'get' {SECRET}")

    target = {"reserve": (hf, "reserve"), "clip": (hf, "clip"),
              "bank": (cal, "_bank_foreign")}[where]
    monkeypatch.setattr(*target, boom)
    made, stopped, refused = await cal._bank(j, 1)
    assert made == 1 and refused is False
    assert "Higgsfield overflow failed (AttributeError)" in stopped
    assert log["hf_record"] == []
    assert "AttributeError" in caplog.text and SECRET not in caplog.text


async def test_an_unread_price_is_said_beside_the_clip(bank, monkeypatch):
    j, _ = bank

    async def unread(prompt, *, job_id=None, part=None):
        return hf.Price(Decimal("12.00"), "worst case", False, 8, "4k"), None

    monkeypatch.setattr(hf, "reserve", unread)
    await cal._bank(j, 0)
    part = cal.plan(j)[0]
    marker = Path(cal.higgsfield_marker(j["job_id"], part)).read_text()
    assert "price not read" in marker and "worst case" in marker
    assert cal.higgsfield_unpriced(j) == cal.higgsfield_count(j) > 0


async def test_an_uncharged_refund_carries_the_price_read_flag(bank, monkeypatch):
    j, _ = bank
    rows = []

    async def unread(prompt, *, job_id=None, part=None):
        return hf.Price(Decimal("3.00"), "worst case", False, 8, "1080p"), None

    async def failed(prompt):
        raise hf.HiggsfieldError("ended failed", charged=False)

    async def hf_record(usd, **kw):
        rows.append((usd, kw))

    monkeypatch.setattr(hf, "reserve", unread)
    monkeypatch.setattr(hf, "clip", failed)
    monkeypatch.setattr(hf, "record", hf_record)
    await cal._bank(j, 0)
    assert rows == [(Decimal("-3.00"), {"job_id": j["job_id"],
                                        "part": cal.plan(j)[0].key, "seconds": 8,
                                        "res": "1080p", "price_read": False})]


# ================================================================ a whole story

async def test_a_story_is_reserved_whole_or_not_at_all(keys, monkeypatch,
                                                       memory_backend):
    _spent(memory_backend, "18")
    _transport(monkeypatch, _estimate("0.79"))
    prices, why = await hf.reserve_all(["a", "b", "c"], job_id="media:1",
                                       parts=["beat-1", "beat-2", "beat-3"])
    assert prices is None and "$2.37" in why and "as a whole" in why
    assert not any("INSERT" in s for s, _ in memory_backend.sql_log)
    _spent(memory_backend, "1")
    prices, why = await hf.reserve_all(["a", "b"], job_id="media:1",
                                       parts=["beat-3", "beat-4"])
    assert why is None and [p.usd for p in prices] == [Decimal("0.79")] * 2
    rows = [a for s, a in memory_backend.sql_log if "INSERT INTO higgsfield" in s]
    assert [(r[0], r[3], r[6]) for r in rows] == [
        (Decimal("0.79"), "beat-3", True), (Decimal("0.79"), "beat-4", True)]


async def test_a_story_without_a_read_price_says_so_at_the_cap(
        keys, monkeypatch, memory_backend):
    _spent(memory_backend, "15")
    _transport(monkeypatch, _estimate(None, status=404))
    prices, why = await hf.reserve_all(["a", "b"], job_id="m", parts=["1", "2"])
    assert prices is None and "2 of them at worst case (price not read)" in why


async def test_an_unread_price_near_the_cap_is_unknown_not_degraded(
        keys, monkeypatch, memory_backend):
    """$18 booked, the worst case $3: whether a real clip fits is not known
    without its price, so the check never concludes "cap reached"."""
    _spent(memory_backend, "18")
    _transport(monkeypatch, _with_probe(_estimate(None, status=404)))
    obs = await hf.check_budget()
    assert obs.health is Health.UNKNOWN and "price not read" in obs.detail
    _spent(memory_backend, "20")
    assert (await hf.check_budget()).health is Health.DEGRADED


async def test_rows_from_before_the_flag_count_as_not_read():
    """A NULL price_read (a row booked before the column) is never read as
    a stated price."""
    import inspect
    assert "price_read IS NOT TRUE" in inspect.getsource(hf.ledger)


# ================================================================ seen live 2026-09-30

PRICING_TEXT = ("Token-metered pricing. Billable video tokens = ceil(generated video "
                "seconds × output width × output height × 24 fps / 1024). Image and "
                "audio references do not count as video input. Per 1,000 video "
                "tokens: 480p/720p/1080p $0.014, 4K $0.008. Rates shown are before "
                "any applicable customer discount.")


async def test_a_status_url_on_platform_higgsfield_ai_is_polled(keys, monkeypatch):
    """Seen live 2026-09-30: Higgsfield accepts on api.higgsfield.ai and
    names a status_url on platform.higgsfield.ai. That is Higgsfield's own
    host, named by Higgsfield's own answer: polled, with the key."""
    status = "https://platform.higgsfield.ai/requests/rid-1/status"
    calls = []

    def handler(request):
        calls.append((request.url.host, request.headers.get("authorization")))
        if request.url.path == f"/{hf.MODEL}":
            return httpx.Response(200, json={"status": "queued", "request_id": "rid-1",
                                             "status_url": status})
        if str(request.url) == status:
            return httpx.Response(200, json={"status": "completed",
                                             "video": {"url": VIDEO}})
        if str(request.url) == VIDEO:
            return httpx.Response(200, content=b"MP4")
        return httpx.Response(500)

    _transport(monkeypatch, handler)
    assert await hf.clip("a scene") == b"MP4"
    assert ("platform.higgsfield.ai", f"Key {KEY_ID}:{SECRET}") in calls
    assert hf.pinned("https://evil.example/requests/rid-1/status") is not None
    assert hf.pinned("http://platform.higgsfield.ai/x") is not None, "https only"


@pytest.mark.parametrize("res,usd", [("1080p", "5.4432"), ("720p", "2.4192"),
                                     ("4k", "12.4416")])
async def test_the_estimate_prices_a_clip_from_higgsfield_pricing_formula(
        keys, monkeypatch, res, usd):
    """Seen live 2026-09-30: the estimate endpoint answers a pricing
    DESCRIPTION, not a figure. The price is then Higgsfield's own formula
    applied to the exact clip asked for (8 s, 9:16 at the resolution): a
    figure read from Higgsfield, not a guess, and said so."""
    monkeypatch.setenv("HIGGSFIELD_RESOLUTION", res)

    def handler(request):
        assert request.url.path == f"/estimate/{hf.MODEL}"
        return httpx.Response(200, json={"type": "description",
                                         "pricing_description": PRICING_TEXT})

    _transport(monkeypatch, handler)
    price = await hf.estimate("a scene")
    assert price.read is True and price.source == "formula"
    assert price.usd == Decimal(usd) and price.resolution == res
    assert "pricing formula" in price.said()


async def test_a_pricing_description_without_rates_is_still_not_read(keys, monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"type": "description",
                                         "pricing_description": "Ask sales."})

    _transport(monkeypatch, handler)
    price = await hf.estimate("a scene")
    assert price.read is False and price.source == "worst case"


# ================================================================ mix (the owner, 2026-10-01)

async def test_mix_alternates_the_story_resolution_and_defaults_to_1080p(keys, monkeypatch):
    """the owner, 2026-10-01: "a mix of both 720 and 1080". HIGGSFIELD_RESOLUTION=mix
    gives each STORY one resolution -- odd media ids 1080p, even 720p -- so
    a reel is never half as sharp in some beats; outside a story (the
    check's probe) it reads 1080p, the dearer of the two."""
    monkeypatch.setenv("HIGGSFIELD_RESOLUTION", "mix")
    assert hf.resolution() == "1080p"
    assert hf.pick_resolution(41) == "1080p" and hf.pick_resolution(42) == "720p"
    token = hf.set_story(42)
    try:
        assert hf.resolution() == "720p"
        assert hf.body("a scene")["resolution"] == "720p"
    finally:
        hf.reset_story(token)
    assert hf.resolution() == "1080p"
    assert hf.mixing() is True
    monkeypatch.setenv("HIGGSFIELD_RESOLUTION", "720p")
    assert hf.mixing() is False and hf.resolution() == "720p"


# ================================================================ the day (2026-10-01)

async def test_the_daily_cap_is_a_thirtieth_of_the_month_unless_set(keys, monkeypatch):
    monkeypatch.setenv("HIGGSFIELD_MONTHLY_USD", "292")
    monkeypatch.delenv("HIGGSFIELD_DAILY_USD", raising=False)
    assert hf.daily_cap() == Decimal("9.73")
    monkeypatch.setenv("HIGGSFIELD_DAILY_USD", "25")
    assert hf.daily_cap() == Decimal(25)


async def test_a_clip_past_todays_allowance_waits_for_tomorrow(
        keys, monkeypatch, memory_backend):
    """Seen live 2026-10-01: $290 of a $292 month booked in one night. The
    month had room; the DAY had not, and nothing said so."""
    monkeypatch.setenv("HIGGSFIELD_MONTHLY_USD", "292")
    monkeypatch.delenv("HIGGSFIELD_DAILY_USD", raising=False)
    memory_backend.responses["FROM higgsfield_usage"] = {
        "usd": Decimal(40), "today_usd": Decimal("9.00")}
    _transport(monkeypatch, _estimate("0.80"))
    price, why = await hf.reserve("a scene", job_id="j", part="p")
    assert price is None and "daily cap" in why and "$9.00 of $9.73" in why
    assert "tomorrow" in why
    prices, why = await hf.reserve_all(["a", "b"], job_id="j", parts=["1", "2"])
    assert prices is None and "daily cap" in why and "refused as a whole" in why
    assert not [s for s, _ in memory_backend.sql_log if "INSERT INTO higgsfield_usage" in s]
    memory_backend.responses["FROM higgsfield_usage"] = {
        "usd": Decimal(40), "today_usd": Decimal("8.00")}
    price, why = await hf.reserve("a scene", job_id="j", part="p")
    assert why is None and price.usd == Decimal("0.80")


async def test_a_third_story_in_a_day_waits_for_tomorrow(keys, monkeypatch, memory_backend):
    """the owner, 2026-10-01: two videos a day on Higgsfield, no more. A story
    booked today may finish its own clips; a new one waits."""
    monkeypatch.setenv("HIGGSFIELD_MONTHLY_USD", "292")
    monkeypatch.setenv("HIGGSFIELD_DAILY_USD", "100")
    monkeypatch.delenv("HIGGSFIELD_DAILY_STORIES", raising=False)
    memory_backend.responses["FROM higgsfield_usage"] = {
        "usd": Decimal(40), "today_usd": Decimal(20),
        "today_jobs": ["media:41", "media:42"]}
    _transport(monkeypatch, _estimate("0.80"))
    prices, why = await hf.reserve_all(["a"], job_id="media:43", parts=["1"])
    assert prices is None and "2 of 2 stories a day" in why and "tomorrow" in why
    price, why = await hf.reserve("a scene", job_id="media:43", part="p")
    assert price is None and "stories a day" in why
    prices, why = await hf.reserve_all(["a"], job_id="media:42", parts=["3"])
    assert why is None and len(prices) == 1, "today's own story finishes"
    monkeypatch.setenv("HIGGSFIELD_DAILY_STORIES", "3")
    prices, why = await hf.reserve_all(["a"], job_id="media:43", parts=["1"])
    assert why is None
