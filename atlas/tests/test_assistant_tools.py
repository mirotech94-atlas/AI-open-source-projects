"""The assistant's tools: each reads, each says where its figure came from,
and none can be made to do anything else. No network: every outside call is
faked at the seam it goes through."""
from __future__ import annotations

import inspect

import httpx
import pytest

from atlas.kernel.tools import TOOLS, Tool, validate_params
from atlas.modules.assistant import tools


@pytest.fixture(autouse=True)
def _registered():
    tools.register_tools()


# ---------------------------------------------------------------- the set

def test_every_assistant_tool_is_a_registered_read_and_never_an_action():
    for name in tools.ASSISTANT_TOOLS:
        tool = TOOLS[name]
        assert tool.read is not None and tool.action is None, name


def test_the_local_set_is_a_subset_with_no_outside_read():
    assert tools.LOCAL_TOOLS <= tools.ASSISTANT_TOOLS
    from atlas.modules.assistant.budget import NETWORK_TOOLS
    assert not (tools.LOCAL_TOOLS & NETWORK_TOOLS)


def test_descriptions_are_the_docstrings_the_model_sees():
    assert "robots.txt" in TOOLS["read_page"].description
    assert "Never" in TOOLS["search_web"].description


def test_nothing_in_the_package_imports_the_executor():
    import ast
    import pathlib
    pkg = pathlib.Path(tools.__file__).parent
    for path in pkg.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] + [a.name for a in node.names]
                     if isinstance(node, ast.ImportFrom) else [])
            assert not any("executor" in n for n in names), f"{path.name} imports {names}"


# --------------------------------------------------------- validate_params

def test_validate_params_refuses_unknown_missing_and_wrong_types():
    tool = TOOLS["convert_units"]
    assert validate_params(tool, {"value": 5, "from_unit": "mi", "to_unit": "km"}) is None
    assert "missing" in validate_params(tool, {"value": 5, "from_unit": "mi"})
    assert "undeclared" in validate_params(
        tool, {"value": 5, "from_unit": "mi", "to_unit": "km", "shell": "rm"})
    assert "must be a number" in validate_params(
        tool, {"value": "five", "from_unit": "mi", "to_unit": "km"})
    assert "must be a number" in validate_params(
        tool, {"value": True, "from_unit": "mi", "to_unit": "km"})
    assert "JSON object" in validate_params(tool, ["value"])


# ---------------------------------------------------------------- calculate

@pytest.mark.parametrize("expression, result", [
    ("2 + 2", 4), ("(3 + 4) * 2", 14), ("2 ** 10", 1024), ("10 / 4", 2.5),
    ("sqrt(144)", 12), ("round(10 / 3, 2)", 3.33), ("17 % 5", 2), ("-3 + 5", 2),
    ("2 ^ 3", 8), ("pi * 2", 2 * 3.141592653589793),
])
def test_calculate_does_arithmetic_without_eval(expression, result):
    got = tools.calculate(expression)
    assert got["result"] == pytest.approx(result)
    assert "computed_at" in got


@pytest.mark.parametrize("expression", [
    "__import__('os').system('ls')", "open('/etc/passwd')", "x = 1", "a.b",
    "lambda: 1", "[1, 2]", "'abc'", "True", "exec('1')", "9 ** 9 ** 9",
    "2 ** 100000", "1 / 0", "", "1 +",
])
def test_calculate_refuses_everything_that_is_not_arithmetic(expression):
    got = tools.calculate(expression)
    assert "error" in got and "result" not in got, expression


def test_calculate_source_never_calls_eval():
    src = inspect.getsource(tools)
    assert "eval(" not in src.replace("_eval(", "").replace("ast.literal_eval", "")


# ----------------------------------------------------------------- time_now

def test_time_now_reports_the_zone_and_refuses_an_unknown_one():
    got = tools.time_now("Asia/Tokyo")
    assert got["timezone"] == "Asia/Tokyo" and got["utc_offset"] == "+0900"
    assert got["weekday"] and got["date"] and got["utc"]
    assert "unknown timezone" in tools.time_now("Mars/Olympus")["error"]
    assert tools.time_now("")["timezone"] == "UTC"


# ------------------------------------------------------------ convert_units

@pytest.mark.parametrize("value, src, dst, expected", [
    (5, "miles", "km", 8.04672), (72, "F", "C", 22.222222), (100, "C", "K", 373.15),
    (3, "GiB", "MB", 3221.225472), (1, "hour", "seconds", 3600), (10, "kg", "lb",
                                                                   22.046226),
    (1, "acre", "m2", 4046.8564224), (60, "mph", "km/h", 96.56064),
])
def test_convert_units_across_categories(value, src, dst, expected):
    got = tools.convert_units(value, src, dst)
    assert got["result"] == pytest.approx(expected, rel=1e-5), got


def test_convert_units_refuses_a_mismatch_and_an_unknown_unit():
    assert "not the same kind" in tools.convert_units(1, "kg", "km")["error"]
    assert "not the same kind" in tools.convert_units(1, "C", "km")["error"]
    assert "unknown unit" in tools.convert_units(1, "furlong", "km")["error"]
    assert "must be a number" in tools.convert_units("x", "kg", "lb")["error"]


# --------------------------------------------------------- convert_currency

async def test_convert_currency_reads_the_rate_fresh_and_cites_its_date(monkeypatch):
    asked: list[dict] = []

    async def fake_get_json(url, *, headers=None, params=None, timeout=25.0, base_url=""):
        asked.append({"url": url, "params": params})
        return {"amount": 1.0, "base": "USD", "date": "2026-10-03",
                "rates": {"EUR": 0.85}}, None

    monkeypatch.setattr(tools, "get_json", fake_get_json)
    got = await tools.convert_currency(100, "usd", "eur")
    assert got["converted"] == 85.0 and got["rate"] == 0.85
    assert got["rate_date"] == "2026-10-03"
    assert got["sources"][0]["url"].startswith(tools.FX_URL_DEFAULT)
    assert got["sources"][0]["read_at"]
    assert asked[0]["params"] == {"base": "USD", "symbols": "EUR"}


async def test_convert_currency_never_invents_a_rate_when_the_source_fails(monkeypatch):
    async def fake_get_json(url, **_kw):
        return None, "timed out"

    monkeypatch.setattr(tools, "get_json", fake_get_json)
    got = await tools.convert_currency(100, "USD", "EUR")
    assert "could not be read" in got["error"] and "converted" not in got
    assert "ISO codes" in (await tools.convert_currency(1, "dollars", "EUR"))["error"]


# -------------------------------------------------------------- weather_now

async def test_weather_now_geocodes_then_reads_the_current_block(monkeypatch):
    async def fake_get_json(url, *, headers=None, params=None, timeout=25.0, base_url=""):
        if url == tools.GEOCODE_URL:
            return {"results": [{"name": "Paris", "country": "France",
                                 "latitude": 48.85, "longitude": 2.35}]}, None
        assert params["latitude"] == 48.85
        return {"current": {"time": "2026-10-06T15:00", "temperature_2m": 18.3,
                            "apparent_temperature": 17.1, "relative_humidity_2m": 60,
                            "precipitation": 0.0, "weather_code": 2,
                            "wind_speed_10m": 12.4}}, None

    monkeypatch.setattr(tools, "get_json", fake_get_json)
    got = await tools.weather_now("Paris")
    assert got["place"] == "Paris, France" and got["temperature_c"] == 18.3
    assert got["sky"] == "partly cloudy"
    assert got["sources"][0]["url"].startswith(tools.FORECAST_URL)


async def test_weather_now_says_when_the_place_is_unknown(monkeypatch):
    async def fake_get_json(url, **_kw):
        return {"results": []}, None

    monkeypatch.setattr(tools, "get_json", fake_get_json)
    assert "no place called" in (await tools.weather_now("Nowhereville"))["error"]


# -------------------------------------------------------- wikipedia / define

async def test_wikipedia_summary_returns_the_extract_and_the_page_url(monkeypatch):
    async def fake_get_json(url, **_kw):
        assert url.endswith("/Marie_Curie")
        return {"title": "Marie Curie", "description": "Physicist",
                "extract": "Marie Curie was a physicist and chemist.",
                "content_urls": {"desktop": {
                    "page": "https://en.wikipedia.org/wiki/Marie_Curie"}}}, None

    monkeypatch.setattr(tools, "get_json", fake_get_json)
    got = await tools.wikipedia_summary("Marie Curie")
    assert got["extract"].startswith("Marie Curie was")
    assert got["sources"] == [{"url": "https://en.wikipedia.org/wiki/Marie_Curie",
                               "read_at": got["read_at"]}]


async def test_define_word_folds_the_dictionary_shape(monkeypatch):
    async def fake_get_json(url, **_kw):
        return [{"word": "serendipity", "phonetic": "/ˌsɛɹənˈdɪpɪti/",
                 "meanings": [{"partOfSpeech": "noun", "definitions": [
                     {"definition": "A happy accident.", "example": "pure serendipity"},
                     {"definition": "Luck."}, {"definition": "ignored third"}]}]}], None

    monkeypatch.setattr(tools, "get_json", fake_get_json)
    got = await tools.define_word("Serendipity")
    assert got["meanings"][0]["part_of_speech"] == "noun"
    assert len(got["meanings"][0]["senses"]) == 2
    assert got["sources"][0]["url"].endswith("/serendipity")
    assert "single English word" in (await tools.define_word("rm -rf /"))["error"]


# ---------------------------------------------------------------- read_page

class _Resp:
    def __init__(self, status: int, body: bytes, url: str):
        self.status_code = status
        self.content = body
        self.url = url
        self.encoding = "utf-8"
        self.headers: dict[str, str] = {}


async def test_read_page_strips_markup_caps_text_and_cites_the_final_url(monkeypatch):
    page = (b"<html><head><title>A Page</title><script>evil()</script></head>"
            b"<body><h1>Hello</h1><p>" + b"word " * 5000 + b"</p></body></html>")

    async def fake_get_public(client, url, *, headers=None):
        if url.endswith("/robots.txt"):
            return _Resp(200, b"User-agent: *\nDisallow: /private\n", url), None
        return _Resp(200, page, "https://example.org/final"), None

    monkeypatch.setattr(tools, "get_public", fake_get_public)
    got = await tools.read_page("https://example.org/page")
    assert got["title"] == "A Page" and "Hello word word" in got["text"]
    assert "evil" not in got["text"]
    assert got["truncated"] is True and len(got["text"]) <= tools.MAX_PAGE_CHARS
    assert got["sources"][0]["url"] == "https://example.org/final"


async def test_read_page_respects_robots_and_the_netguard(monkeypatch):
    async def fake_get_public(client, url, *, headers=None):
        if url.endswith("/robots.txt"):
            return _Resp(200, b"User-agent: *\nDisallow: /private\n", url), None
        raise AssertionError("the page must not be fetched")

    monkeypatch.setattr(tools, "get_public", fake_get_public)
    got = await tools.read_page("https://example.org/private/x")
    assert "robots.txt disallows" in got["error"]

    async def refused(client, url, *, headers=None):
        return None, "127.0.0.1 is a loopback address, not the public web"

    monkeypatch.setattr(tools, "get_public", refused)
    got = await tools.read_page("http://127.0.0.1/api/memory")
    assert "loopback" in got["error"]
    assert "http(s)" in (await tools.read_page("file:///etc/passwd"))["error"]


async def test_read_page_reports_a_connection_failure_without_the_url_in_it(monkeypatch):
    async def boom(client, url, *, headers=None):
        raise httpx.ConnectError("refused", request=httpx.Request("GET", url))

    monkeypatch.setattr(tools, "get_public", boom)
    got = await tools.read_page("https://example.org/?token=secret123")
    assert "not fetched" in got["error"] and "secret123" not in got["error"]


# --------------------------------------------------------------- search_web

async def test_search_web_is_honest_when_no_provider_is_configured(monkeypatch):
    from atlas.modules import _web

    async def fake_search(query, limit=5):
        return [], "no search provider configured (BRAVE_API_KEY or PERPLEXITY_API_KEY)"

    monkeypatch.setattr(_web, "search", fake_search)
    got = await tools.search_web("capital of Peru")
    assert got["error"].startswith("search unavailable")


async def test_search_web_returns_hits_as_sources_with_one_read_time(monkeypatch):
    from atlas.modules import _web

    async def fake_search(query, limit=5):
        return [{"title": "Lima", "url": "https://example.org/lima", "snippet": "x" * 900},
                {"title": "Perplexity", "url": "", "snippet": "an answer"}], None

    monkeypatch.setattr(_web, "search", fake_search)
    got = await tools.search_web("capital of Peru", limit=50)
    assert [s["url"] for s in got["sources"]] == ["https://example.org/lima"]
    assert len(got["hits"][0]["snippet"]) == 400
    assert got["hits"][1]["url"] == ""           # a synthetic hit is never a source


# ------------------------------------------------------------- ATLAS's own

def test_atlas_capabilities_lists_names_and_summaries_and_never_tier_3(monkeypatch):
    from atlas.kernel import intent
    from atlas.kernel.contracts import Tier

    class A:
        def __init__(self, name, summary):
            self.name, self.summary, self.params = name, summary, {}

    class D:
        def __init__(self, tier):
            self.tier, self.allowed = tier, tier is not Tier.NEVER

    monkeypatch.setattr(intent.REGISTRY, "modules", {"flycheap": type("M", (), {
        "title": "FlyCheap"})()}, raising=False)
    monkeypatch.setattr(intent.REGISTRY, "actions", {
        "flycheap.rerun": A("flycheap.rerun", "rerun the scraper"),
        "billing.payout": A("billing.payout", "send money")}, raising=False)
    monkeypatch.setattr(intent.REGISTRY, "decisions", {
        "flycheap.rerun": D(Tier.AUTO), "billing.payout": D(Tier.NEVER)}, raising=False)
    got = tools.atlas_capabilities()
    assert got["modules"] == [{"name": "flycheap", "title": "FlyCheap"}]
    assert [a["name"] for a in got["actions"]] == ["flycheap.rerun"]
    assert got["actions"][0]["tier"] == "runs by itself"
    assert "search_web" in got["assistant_tools"]


async def test_atlas_module_status_refuses_a_private_module_for_the_assistant():
    """`mail` holds his personal content; the assistant is not a local-model
    agent, so `kernel.private_data` refuses it before any read."""
    from atlas.kernel.tools import CURRENT_AGENT
    token = CURRENT_AGENT.set("assistant")
    try:
        got = await tools.atlas_module_status("mail")
    finally:
        CURRENT_AGENT.reset(token)
    assert got.get("refused") == "private_module"


def test_register_tools_is_idempotent_and_refuses_an_acting_tool():
    before = dict(TOOLS)
    tools.register_tools()
    assert {k: v for k, v in TOOLS.items() if k in tools.ASSISTANT_TOOLS} == {
        k: v for k, v in before.items() if k in tools.ASSISTANT_TOOLS}
    from atlas.kernel.tools import register
    with pytest.raises(ValueError):
        register(Tool("x", "x", {}, read=tools.atlas_status, action="x.y"))
    assert "x" not in TOOLS
