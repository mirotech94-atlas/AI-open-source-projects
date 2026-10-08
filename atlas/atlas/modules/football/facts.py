"""Fact sets for the football (soccer) reels, built only from API-Football.

A topic is a question the API can answer, chosen in code -- never a
question the model makes up. Each topic reads a known number of requests
(`Topic.cost`, the most it can spend) and turns the answer into `Fact`s:
an id (`f1`...), what it is, its value exactly as the API gave it, and when
it was read. The drafting model sees only these facts (wrapped as untrusted
data) and refers to a number by its id; code puts the value in.

Football here is association football -- soccer: players, clubs and
leagues from API-Football's soccer data. Never American football.

Ids used as configuration (league and club ids) are checked against the
name the API returns with them: a mismatch refuses the topic instead of
putting a wrong name on screen. The season is read, not assumed: the
current season by the calendar unless `FOOTBALL_SEASON` says otherwise, and
when the plan refuses it the API's own message names the seasons it allows
("try from 2021 to 2023") and the newest of those is read once instead.
"""
from __future__ import annotations

import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from . import api

# (id, the name API-Football gives it). Checked on every read.
LEAGUES: tuple[tuple[int, str], ...] = (
    (39, "Premier League"),
    (140, "La Liga"),
    (135, "Serie A"),
    (78, "Bundesliga"),
    (61, "Ligue 1"),
)
CLUBS: tuple[tuple[int, str], ...] = (
    (541, "Real Madrid"),
    (529, "Barcelona"),
    (50, "Manchester City"),
    (40, "Liverpool"),
    (42, "Arsenal"),
    (157, "Bayern"),
)
# The star comparison: (API-Football player id, the full name shown, a word
# the API's own `player.name` must contain). The id is configuration, checked
# on every read against the name the API returns with it: a profile's
# `lastname` is the legal surname (Neymar's is "da Silva Santos Júnior"), so a
# search by the football name cannot be trusted to find the player.
STARS: tuple[tuple[tuple[int, str, str], tuple[int, str, str]], ...] = (
    ((874, "Cristiano Ronaldo", "Ronaldo"), (154, "Lionel Messi", "Messi")),
)
PROFILE_MAX_AGE_S = 30 * 86400          # a player's id does not change


@dataclass(frozen=True, slots=True)
class Fact:
    id: str
    label: str              # what the number is, in words
    value: str              # exactly as read (numbers as their digits)
    read_at: str            # ISO time it was read
    source: str             # endpoint + params, for the record
    subject: str = ""       # the player or club it is about (never to Veo)

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FactSet:
    topic: str
    title: str
    facts: tuple[Fact, ...]

    def names(self) -> list[str]:
        """Every player, club and league named in the set: the visual
        check's deny-list, built from the API's own answer."""
        return sorted({f.subject for f in self.facts if f.subject})

    def by_id(self) -> dict[str, Fact]:
        return {f.id: f for f in self.facts}

    def read_at(self) -> str:
        return min((f.read_at for f in self.facts), default="")


class Unanswerable(Exception):
    """The API gave nothing usable for this topic (results 0, a name that
    does not match its id, a player with no statistics)."""


def season_now(now: datetime | None = None) -> int:
    """API-Football names a season by the year it starts: August on is the
    new season. `FOOTBALL_SEASON` overrides it."""
    raw = os.getenv("FOOTBALL_SEASON", "").strip()
    if raw.isdigit():
        return int(raw)
    now = now or datetime.now(UTC)
    return now.year if now.month >= 7 else now.year - 1


_ALLOWED = re.compile(r"from\s+(\d{4})\s+to\s+(\d{4})", re.IGNORECASE)


def season_label(season: int | str) -> str:
    s = int(season)
    return f"{s}/{(s + 1) % 100:02d}"


# (API day, season asked) -> the newest season the plan's refusal named, so
# a refused season is asked once a day, not once per read.
_REFUSED: dict[tuple[Any, int], int] = {}


async def _season_read(endpoint: str, params: dict[str, Any], **kw: Any) -> api.Read:
    """A read for a season; when the plan refuses that season, once more
    with the newest season the API's own refusal names -- and, for the rest
    of the day, straight to that season."""
    if "season" in params:
        known = _REFUSED.get((api.api_day(), int(params["season"])))
        if known is not None:
            params = {**params, "season": known}
    try:
        return await api.get(endpoint, params, **kw)
    except api.ApiError as exc:
        found = _ALLOWED.search(str(exc))
        if exc.auth or not found or "season" not in params:
            raise
        newest = int(found.group(2))
        if newest == int(params["season"]):
            raise
        _REFUSED[(api.api_day(), int(params["season"]))] = newest
        return await api.get(endpoint, {**params, "season": newest}, **kw)


class _Ids:
    def __init__(self) -> None:
        self.n = 0
        self.facts: list[Fact] = []

    def add(self, label: str, value: Any, read: api.Read, subject: str = "") -> str:
        if value is None or value == "":
            raise Unanswerable(f"the API gave no value for {label}")
        self.n += 1
        fid = f"f{self.n}"
        src = f"{read.endpoint} {api._canonical(read.params)}"
        self.facts.append(Fact(fid, label, str(value), read.read_at.isoformat(),
                               src, subject))
        return fid


def _check_name(expected: str, got: Any, what: str) -> None:
    if expected.lower() not in str(got or "").lower():
        raise Unanswerable(f"{what}: expected {expected!r}, the API named "
                           f"{got!r}; refused rather than shown")


def _leaders(read: api.Read, league: str, ids: _Ids, n: int, stat: str) -> None:
    rows = read.response if isinstance(read.response, list) else []
    if not rows:
        raise Unanswerable(f"{read.endpoint} returned no players for {league}")
    first = (rows[0].get("statistics") or [{}])[0]
    _check_name(league, (first.get("league") or {}).get("name"), "league")
    season = (first.get("league") or {}).get("season")
    ids.add(f"{league} season", season_label(season) if season else None, read)
    for rank, row in enumerate(rows[:n], 1):
        name = (row.get("player") or {}).get("name")
        st = (row.get("statistics") or [{}])[0]
        goals = st.get("goals") or {}
        value = goals.get("total") if stat == "goals" else goals.get("assists")
        team = (st.get("team") or {}).get("name")
        ids.add(f"#{rank} in {league} {stat}: player name", name, read, subject=name)
        ids.add(f"#{rank} in {league}: {name}'s {stat}", value, read, subject=name)
        ids.add(f"#{rank} in {league}: {name}'s club", team, read, subject=team or "")
        apps = (st.get("games") or {}).get("appearences")
        if apps is not None:
            ids.add(f"#{rank} in {league}: {name}'s appearances", apps, read,
                    subject=name)


def _league(index: int) -> tuple[int, str]:
    return LEAGUES[index % len(LEAGUES)]


async def season_leaders(turn: int) -> FactSet:
    lid, name = _league(turn)
    read = await _season_read("players/topscorers",
                              {"league": lid, "season": season_now()},
                              max_age_s=20 * 3600)
    ids = _Ids()
    _leaders(read, name, ids, 3, "goals")
    return FactSet("season_leaders", f"{name} top scorers", tuple(ids.facts))


async def assist_kings(turn: int) -> FactSet:
    lid, name = _league(turn)
    read = await _season_read("players/topassists",
                              {"league": lid, "season": season_now()},
                              max_age_s=20 * 3600)
    ids = _Ids()
    _leaders(read, name, ids, 3, "assists")
    return FactSet("assist_kings", f"{name} assist leaders", tuple(ids.facts))


async def golden_boot_race(turn: int) -> FactSet:
    """The scoring leader of each of the five leagues, side by side: a
    selection of fetched values, no number computed."""
    ids = _Ids()
    for lid, name in LEAGUES:
        read = await _season_read("players/topscorers",
                                  {"league": lid, "season": season_now()},
                                  max_age_s=20 * 3600)
        _leaders(read, name, ids, 1, "goals")
    return FactSet("golden_boot_race", "the golden boot race across five leagues",
                   tuple(ids.facts))


async def _player_name(pid: int, full: str, must: str) -> str:
    """The configured id, checked: its profile's name must contain `must`."""
    read = await api.get("players/profiles", {"player": pid},
                         max_age_s=PROFILE_MAX_AGE_S)
    rows = read.response if isinstance(read.response, list) else []
    name = str(((rows[0] if rows else {}).get("player") or {}).get("name") or "")
    if must.lower() not in name.lower():
        raise Unanswerable(f"player {pid} is {name or 'unknown'!r} at API-Football, "
                           f"not {full}; refused rather than shown")
    return name


def _main_line(stats: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The competition a player played most in, as the API lists it."""
    played = [s for s in stats if ((s.get("games") or {}).get("appearences") or 0) > 0]
    if not played:
        return None
    return max(played, key=lambda s: (s.get("games") or {}).get("appearences") or 0)


async def star_comparison(turn: int) -> FactSet:
    pair = STARS[turn % len(STARS)]
    ids = _Ids()
    title = []
    for pid, full, must in pair:
        name = await _player_name(pid, full, must)
        read = await _season_read("players", {"id": pid, "season": season_now()},
                                  max_age_s=20 * 3600)
        rows = read.response if isinstance(read.response, list) else []
        if not rows:
            raise Unanswerable(f"players returned no season for {name}")
        line = _main_line(rows[0].get("statistics") or [])
        if line is None:
            raise Unanswerable(f"{name} has no appearances in the season read")
        league = line.get("league") or {}
        goals = line.get("goals") or {}
        ids.add(f"{full}: name", full, read, subject=full)
        ids.add(f"{full}: club", (line.get("team") or {}).get("name"), read,
                subject=(line.get("team") or {}).get("name") or "")
        ids.add(f"{full}: competition", league.get("name"), read,
                subject=league.get("name") or "")
        # As the API names it: a league that runs on the calendar year
        # (MLS, the Saudi league's listing) is not a "2025/26".
        ids.add(f"{full}: season", league.get("season"), read)
        ids.add(f"{full}: appearances", (line.get("games") or {}).get("appearences"),
                read, subject=full)
        if goals.get("total") is not None:
            ids.add(f"{full}: goals", goals.get("total"), read, subject=full)
        if goals.get("assists") is not None:
            ids.add(f"{full}: assists", goals.get("assists"), read, subject=full)
        title.append(full)
    return FactSet("star_comparison", " vs ".join(title) + " by the numbers",
                   tuple(ids.facts))


def _team_form(read: api.Read, club: str, ids: _Ids) -> None:
    rows = read.response if isinstance(read.response, list) else []
    if not rows:
        raise Unanswerable(f"fixtures returned no recent match for {club}")
    for row in rows:
        teams = row.get("teams") or {}
        goals = row.get("goals") or {}
        home, away = teams.get("home") or {}, teams.get("away") or {}
        if club.lower() not in f"{home.get('name')} {away.get('name')}".lower():
            raise Unanswerable(f"fixtures for {club} named {home.get('name')} v "
                               f"{away.get('name')}; refused")
        if goals.get("home") is None or goals.get("away") is None:
            continue
        date = str((row.get("fixture") or {}).get("date") or "")[:10]
        ids.add(f"{date}: home side", home.get("name"), read,
                subject=home.get("name") or "")
        ids.add(f"{date}: away side", away.get("name"), read,
                subject=away.get("name") or "")
        ids.add(f"{date}: score", f"{goals['home']}-{goals['away']}", read)
        ids.add(f"{date}: competition", (row.get("league") or {}).get("name"), read,
                subject=(row.get("league") or {}).get("name") or "")
    if not ids.facts:
        raise Unanswerable(f"none of {club}'s recent fixtures had a final score")


async def weekly_form(turn: int) -> FactSet:
    tid, club = CLUBS[turn % len(CLUBS)]
    read = await api.get("fixtures", {"team": tid, "last": 5},
                         max_age_s=6 * 3600)
    ids = _Ids()
    _team_form(read, club, ids)
    return FactSet("weekly_form", f"{club}'s last results", tuple(ids.facts))


@dataclass(frozen=True, slots=True)
class Topic:
    key: str
    name: str               # the reel_formats row's name
    cost: int               # the most requests one fact set can spend
    build: Callable[[int], Awaitable[FactSet]]


# The rotation, soccer only. Records have no endpoint of their own, so
# "records" is the golden-boot race: each league's leader, as read.
TOPICS: tuple[Topic, ...] = (
    Topic("star_comparison", "football (soccer): star comparison by the numbers",
          6, star_comparison),
    Topic("season_leaders", "football (soccer): season top scorers", 2,
          season_leaders),
    Topic("golden_boot_race", "football (soccer): golden boot race across Europe",
          2 * len(LEAGUES), golden_boot_race),
    Topic("weekly_form", "football (soccer): a big club's recent form", 1,
          weekly_form),
    Topic("assist_kings", "football (soccer): season assist leaders", 2,
          assist_kings),
)


def topic(key: str) -> Topic | None:
    return next((t for t in TOPICS if t.key == key), None)


def topic_for_format(name: str) -> Topic | None:
    return next((t for t in TOPICS if t.name.lower() == (name or "").lower()), None)
