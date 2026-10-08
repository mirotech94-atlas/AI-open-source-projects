"""The Yahoo chart feed, faked: closed bars only, the forming bar and null
holes dropped, gaps inside CME hours found (the maintenance hour and the
weekend are not gaps), every request a GET to query1.finance.yahoo.com,
and UNKNOWN (Unreadable) on a bad status, bad JSON or a dead network.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from futures_support import TUESDAY, bars_from, et, random_walk, yahoo_payload

from atlas.modules.futures import data
from atlas.modules.futures.engines import Bar


def test_parse_keeps_closed_priced_bars_only_with_the_read_time():
    bars = bars_from([100, 101, 102, 103], et(TUESDAY, 9, 30))
    read_at = bars[-1].ts + timedelta(minutes=6)                 # last bar just closed
    payload = yahoo_payload(bars, partial_at=read_at, holes=[1])
    feed = data.parse(payload, "MNQ=F", "5m", read_at)
    assert [b.ts for b in feed.bars] == [bars[0].ts, bars[2].ts, bars[3].ts]
    assert feed.dropped == 2                                     # the hole and the forming bar
    assert feed.read_at == read_at and feed.status == 200
    assert feed.bars[0].volume == 1000 and feed.bars[0].minutes == 5
    # A bar still forming when read is not a bar.
    early = data.parse(yahoo_payload(bars), "MNQ=F", "5m", bars[-1].ts + timedelta(minutes=2))
    assert [b.ts for b in early.bars] == [b.ts for b in bars[:-1]]


def test_parse_refuses_what_it_cannot_read():
    now = datetime.now(UTC)
    with pytest.raises(data.Unreadable, match="unknown interval"):
        data.parse({}, "MNQ=F", "3m", now)
    with pytest.raises(data.Unreadable, match="chart shape"):
        data.parse({"chart": {"result": []}}, "MNQ=F", "5m", now)
    with pytest.raises(data.Unreadable, match="answered an error"):
        data.parse({"chart": {"result": None, "error": {"code": "Not Found"}}},
                   "MNQ=F", "5m", now)


def test_gaps_are_holes_inside_cme_hours_only():
    # 16:50, 16:55, then the maintenance hour, then 18:00: no gap.
    a = bars_from([100, 100], et(TUESDAY, 16, 50))
    b = bars_from([100, 100], et(TUESDAY, 18, 0))
    assert data.gaps(a + b, 5) == []
    # A missing 10:05 bar on a Tuesday is a gap.
    c = bars_from([100, 100], et(TUESDAY, 9, 55)) + bars_from([100], et(TUESDAY, 10, 10))
    assert data.gaps(c, 5) == [(et(TUESDAY, 10, 5), et(TUESDAY, 10, 10))]
    # Friday 16:55 to Sunday 18:00: the weekend, not a gap.
    friday = TUESDAY + timedelta(days=3)
    d = bars_from([100], et(friday, 16, 55)) + bars_from([100], et(friday + timedelta(days=2), 18, 0))
    assert data.gaps(d, 5) == []
    # A whole missing Monday (a holiday) is a gap, and is said as one.
    e = bars_from([100], et(friday, 16, 55)) + bars_from([100], et(friday + timedelta(days=3), 18, 0))
    assert len(data.gaps(e, 5)) == 1


def test_a_full_walk_has_no_gaps_and_the_payload_round_trips():
    bars = random_walk(sessions=2, seed=3)
    read_at = bars[-1].ts + timedelta(minutes=5)
    feed = data.parse(yahoo_payload(bars), "MNQ=F", "5m", read_at)
    assert len(feed.bars) == len(bars) and feed.gaps == []
    assert feed.bars[10] == bars[10]


class FakeYahoo:
    def __init__(self, bars: list[Bar], status: int = 200, body: str | None = None,
                 raise_: Exception | None = None) -> None:
        self.bars, self.status, self.body, self.raise_ = bars, status, body, raise_
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.method == "GET", f"a non-GET left ATLAS: {request.method}"
        assert request.url.host in data.HOSTS, request.url
        assert request.url.path.startswith("/v8/finance/chart/")
        if self.raise_ is not None:
            raise self.raise_
        if self.body is not None:
            return httpx.Response(self.status, text=self.body)
        if self.status != 200:
            return httpx.Response(self.status, json={"chart": {"result": None,
                                                              "error": {"code": "x"}}})
        return httpx.Response(200, json=yahoo_payload(self.bars))


@pytest.fixture
def yahoo(monkeypatch):
    def install(**kw) -> FakeYahoo:
        fake = FakeYahoo(kw.pop("bars", bars_from([100, 101], et(TUESDAY, 9, 30))), **kw)
        monkeypatch.setattr(data, "transport", httpx.MockTransport(fake.handler))
        return fake
    return install


async def test_fetch_is_one_get_to_yahoo(yahoo):
    bars = bars_from([100, 101, 102], et(TUESDAY, 9, 30) - timedelta(days=1))
    fake = yahoo(bars=bars)
    feed = await data.fetch("MNQ=F", "5m", "5d")
    assert len(fake.requests) == 1
    url = str(fake.requests[0].url)
    assert url == "https://query1.finance.yahoo.com/v8/finance/chart/MNQ=F?range=5d&interval=5m"
    assert [b.close for b in feed.bars] == [100, 101, 102]
    assert feed.read_at <= datetime.now(UTC)
    await data.fetch("NQ=F", "1h")
    assert "range=730d&interval=1h" in str(fake.requests[-1].url)


@pytest.mark.parametrize("kw", [{"status": 404}, {"status": 429}, {"body": "<html>no"},
                                {"raise_": httpx.ConnectError("down")},
                                {"raise_": httpx.ReadTimeout("slow")}])
async def test_an_unreadable_feed_is_unreadable_never_a_guess(yahoo, kw):
    yahoo(**kw)
    with pytest.raises(data.Unreadable):
        await data.fetch("MNQ=F", "5m", "5d")


async def test_an_unknown_interval_never_reaches_the_network(yahoo):
    fake = yahoo()
    with pytest.raises(data.Unreadable):
        await data.fetch("MNQ=F", "2m")
    assert fake.requests == []


def test_intervals_and_ranges_match_what_yahoo_serves():
    assert data.INTERVALS == {"5m": 5, "15m": 15, "1h": 60, "1d": 1440}
    assert data.MAX_RANGE["5m"] == "60d" and data.MAX_RANGE["1h"] == "730d"
    assert data.SYMBOLS == ("MNQ=F", "NQ=F")
