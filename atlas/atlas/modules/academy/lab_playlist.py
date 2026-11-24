"""The one "Hands-On Labs & Live Builds" playlist.

the owner, 2026-10-22: every hands-on lab and live demo stays in its own course
playlist and is ALSO added to one playlist, grouped by course so a viewer
sees which lab belongs to which course. Which videos are labs is the
reviewed tuple tutorial_series.LABS, never inferred.

* `academy.add_to_lab_playlist` (Tier 1 by the owner's approval policy: the
  promotion is made here, in code) adds a lab lesson's NEWEST public, not
  withdrawn post on record at the place that keeps the playlist ordered by
  (course, episode). The video id comes from academy_tutorial_posts, never
  from the params. The rollback takes out exactly the item it added; the
  playlist is never deleted, nor made private (other labs share it).
* `academy.take_out_of_lab_playlist` (Tier 1, same policy; security review
  of 3108ed1e) takes out one of OUR posts of that lesson that is no longer
  its current post (withdrawn, not public, or superseded by a relaunch).
  It never touches an item it cannot map to our record. The rollback adds
  the video back at its sorted place.
* `lab_playlist_backfill()` runs from the daily tutorial tick after the
  day's posts (YouTube quota: an upload ~1600 units of 10 000, an add ~53):
  removals first, then adds, all through the executor, at most PER_PASS.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ...kernel.contracts import ActionResult, FailureClass
from . import tutorials as tut
from .tutorial_series import (
    LAB_COURSE_ORDER,
    LAB_PLAYLIST,
    is_lab,
    lab_playlist_description,
)

log = logging.getLogger(__name__)

ACTION = "academy.add_to_lab_playlist"
TAKE_OUT_ACTION = "academy.take_out_of_lab_playlist"
MAX_PER_HOUR = 12
TAKE_OUT_MAX_PER_HOUR = 6
PER_PASS = 3                       # removals and adds together
CREATED_FILE = "lab-playlist-created.json"
CREATE_GRACE = timedelta(minutes=10)

Lesson = tuple[str, int]


def lab_key(item: Lesson) -> tuple[int, int]:
    series, episode = item
    return LAB_COURSE_ORDER.index(series), int(episode)


def insert_position(new: Lesson, existing: list[Lesson | None]) -> int:
    """Where `new` goes so the playlist stays in (course, episode) order:
    after the last item at or before it; an item that is no known lab (None)
    counts as last, so it never pulls a lab after it."""
    key = lab_key(new)
    pos = 0
    for i, item in enumerate(existing):
        if item is not None and lab_key(item) <= key:
            pos = i + 1
    return pos


def vet(params: dict) -> str | None:
    if (why := tut.vet(params)) is not None:
        return why
    if not is_lab(str(params.get("series")), int(params.get("episode"))):
        return (f"{params.get('series')} #{params.get('episode')} is not a lab "
                "(tutorial_series.LABS)")
    return None


def vet_take_out(params: dict) -> str | None:
    if (why := tut.vet_video(params)) is not None:
        return why
    return vet(params)


# ------------------------------------------------------------------ the record

class Record:
    """Our lab posts: `owner` maps every lab video id we posted (any state)
    to its lesson; `current` maps each lab lesson to the video id of its
    newest public, not withdrawn post."""

    def __init__(self, rows: list) -> None:
        self.owner: dict[str, Lesson] = {}
        self.current: dict[Lesson, str] = {}
        for r in sorted(rows, key=lambda r: -int(r["id"])):      # newest first
            key = (str(r["series"]), int(r["episode"]))
            vid = r["video_id"]
            if not vid or not is_lab(*key):
                continue
            self.owner.setdefault(str(vid), key)
            if r["public"] and not r["withdrawn"]:
                self.current.setdefault(key, str(vid))

    def stale(self, vid: str) -> Lesson | None:
        """The lesson of our post `vid` when it is no longer that lesson's
        current post; None when it is current or not ours."""
        key = self.owner.get(vid)
        if key is None or self.current.get(key) == vid:
            return None
        return key


async def record() -> Record:
    from ...kernel.state import fetch_all
    return Record(await fetch_all(
        """SELECT id, series, episode, video_id, public, withdrawn
             FROM academy_tutorial_posts WHERE video_id IS NOT NULL ORDER BY id DESC"""))


# ------------------------------------------------- the created-playlist marker
#
# find_playlist may not list a playlist made seconds earlier. A run that made
# one writes its id here; any run within CREATE_GRACE that cannot find the
# playlist refuses rather than make a second (a direct call or a retry, not
# only the daily pass, which already adds just one when it makes it).

def _created_path() -> Path:
    return tut.root() / CREATED_FILE


def _recently_created(now: datetime) -> dict | None:
    try:
        got = json.loads(_created_path().read_text())
        at = datetime.fromisoformat(str(got["at"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return got if now - at < CREATE_GRACE else None


def _mark_created(playlist_id: str, now: datetime) -> None:
    path = _created_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"id": playlist_id, "at": now.isoformat()}))


# -------------------------------------------------------------- shared steps

async def _add_item(ch: str, playlist_id: str, vid: str,
                  pos: int) -> tuple[dict | None, str]:
    """Insert at `pos`; a 400 (YouTube's manualSortRequired when the playlist
    is not sorted by hand) is tried once more appended at the end."""
    from ..youtube import api
    item, why = await api.add_playlist_item(ch, playlist_id, vid, position=pos)
    if item is not None:
        return item, f"at position {pos}"
    if "400" not in str(why):
        return None, str(why)
    item, again = await api.add_playlist_item(ch, playlist_id, vid, position=None)
    if item is None:
        return None, f"{why}; appended too: {again}"
    return item, (f"appended at the end: position {pos} was refused ({why}; likely "
                  "manualSortRequired, the playlist is not sorted by hand)")


async def _items(ch: str) -> tuple[dict | None, list[dict] | None, str | None]:
    """The lab playlist ({} when there is none) and its items, or why not."""
    from ..youtube import api
    pl, why = await api.find_playlist(ch, LAB_PLAYLIST)
    if pl is None:
        return None, None, why
    if pl == {}:
        return {}, [], None
    items, why = await api.list_playlist_items(ch, pl["id"])
    return pl, items, why


# ------------------------------------------------------------------- the add

async def add_to_lab_playlist_dry(series: str, episode: int) -> ActionResult:
    if (why := vet({"series": series, "episode": episode})) is not None:
        return ActionResult(False, why)
    vid = (await record()).current.get((series, int(episode)))
    if vid is None:
        return ActionResult(False, f"{series} #{episode} has no public post on record")
    return ActionResult(True, f"would add {vid} ({series} #{episode}) to \"{LAB_PLAYLIST}\" "
                              f"on the {tut.channel()} channel at its course-order place "
                              "(the playlist made public if missing); nothing if it is "
                              "already there")


async def add_to_lab_playlist(series: str, episode: int) -> ActionResult:
    from ..youtube import api
    if (why := vet({"series": series, "episode": episode})) is not None:
        return ActionResult(False, why)
    episode = int(episode)
    rec = await record()
    vid = rec.current.get((series, episode))
    if vid is None:
        return ActionResult(False, f"{series} #{episode} has no public post on record")
    ch = tut.channel()
    pl, items, why = await _items(ch)
    if pl is None:
        return ActionResult(False, f"no lab playlist: {why}")
    if items is None:                                  # never guess a position
        return ActionResult(False, f"the lab playlist could not be read: {why}")
    if pl == {}:
        now = datetime.now(UTC)
        if (made := await asyncio.to_thread(_recently_created, now)) is not None:
            return ActionResult(False, f"the lab playlist was made at {made['at']} "
                                       f"({made.get('id')}) and is not listed yet; not "
                                       "making a second, try again later")
        pl, why = await api.create_playlist(ch, LAB_PLAYLIST, lab_playlist_description())
        if not pl:
            return ActionResult(False, f"no lab playlist: {why}")
        try:
            await asyncio.to_thread(_mark_created, str(pl["id"]), now)
        except OSError as exc:
            log.warning("lab playlist: the created marker was not written (%s)",
                        type(exc).__name__)
    if any(i.get("video_id") == vid for i in items):
        return ActionResult(True, f"{vid} is already in \"{LAB_PLAYLIST}\"")
    pos = insert_position((series, episode),
                          [rec.owner.get(str(i.get("video_id"))) for i in items])
    item, said = await _add_item(ch, pl["id"], vid, pos)
    if item is None:
        return ActionResult(False, f"not added: {said}")
    return ActionResult(True, f"{vid} ({series} #{episode}) added to \"{LAB_PLAYLIST}\" "
                              f"{said}",
                        rollback_token=json.dumps({"channel": ch,
                                                   "item_id": item["item_id"]},
                                                  sort_keys=True))


async def undo_lab_playlist(token: str) -> ActionResult:
    """Take out exactly the item the run added. The playlist stays as it is:
    other labs share it, so it is never made private or deleted here."""
    from ..youtube import api
    try:
        undo = json.loads(token)
        ch, item_id = str(undo["channel"]), str(undo["item_id"])
    except (ValueError, KeyError, TypeError):
        return ActionResult(False, "unreadable rollback token")
    done, why = await api.take_out_of_playlist(ch, item_id)
    return ActionResult(done, "taken out of the lab playlist" if done
                        else f"still in the lab playlist: {why}")


# ---------------------------------------------------------------- the removal

def _take_out_refusal(rec: Record, series: str, episode: int, vid: str) -> str | None:
    if rec.owner.get(vid) != (series, episode):
        return f"{vid} is not one of our posts of {series} #{episode}; not touched"
    if rec.current.get((series, episode)) == vid:
        return f"{vid} is the current post of {series} #{episode}; not removed"
    return None


async def take_out_of_lab_playlist_dry(series: str, episode: int,
                                       video_id: str) -> ActionResult:
    params = {"series": series, "episode": episode, "video_id": video_id}
    if (why := vet_take_out(params)) is not None:
        return ActionResult(False, why)
    if (why := _take_out_refusal(await record(), series, int(episode), video_id)):
        return ActionResult(False, why)
    return ActionResult(True, f"would take {video_id} ({series} #{episode}, withdrawn or "
                              f"superseded) out of \"{LAB_PLAYLIST}\"; the video itself "
                              "is not changed")


async def take_out_of_lab_playlist(series: str, episode: int,
                                   video_id: str) -> ActionResult:
    from ..youtube import api
    params = {"series": series, "episode": episode, "video_id": video_id}
    if (why := vet_take_out(params)) is not None:
        return ActionResult(False, why)
    episode = int(episode)
    if (why := _take_out_refusal(await record(), series, episode, video_id)):
        return ActionResult(False, why)
    ch = tut.channel()
    pl, items, why = await _items(ch)
    if not pl or items is None:
        return ActionResult(False, f"the lab playlist could not be read: {why or 'none'}")
    mine = [str(i["item_id"]) for i in items if i.get("video_id") == video_id]
    if not mine:
        return ActionResult(True, f"{video_id} is not in \"{LAB_PLAYLIST}\"")
    out: list[str] = []
    for item_id in mine:
        done, why = await api.take_out_of_playlist(ch, item_id)
        if not done:
            token = (_undo_token(ch, pl["id"], series, episode, video_id)
                     if out else None)
            return ActionResult(False, f"{video_id} still in the lab playlist: {why}",
                                rollback_token=token)
        out.append(item_id)
    return ActionResult(True, f"{video_id} ({series} #{episode}) taken out of "
                              f"\"{LAB_PLAYLIST}\"",
                        rollback_token=_undo_token(ch, pl["id"], series, episode,
                                                   video_id))


def _undo_token(ch: str, playlist_id: str, series: str, episode: int, vid: str) -> str:
    return json.dumps({"channel": ch, "playlist_id": playlist_id, "series": series,
                       "episode": episode, "video_id": vid}, sort_keys=True)


async def undo_take_out_of_lab_playlist(token: str) -> ActionResult:
    """Add the video back at its course-order place."""
    from ..youtube import api
    try:
        undo = json.loads(token)
        ch, pl = str(undo["channel"]), str(undo["playlist_id"])
        key = (str(undo["series"]), int(undo["episode"]))
        vid = str(undo["video_id"])
    except (ValueError, KeyError, TypeError):
        return ActionResult(False, "unreadable rollback token")
    if not is_lab(*key):
        return ActionResult(False, "the rollback token names no lab")
    items, why = await api.list_playlist_items(ch, pl)
    if items is None:
        return ActionResult(False, f"the lab playlist could not be read: {why}")
    if any(i.get("video_id") == vid for i in items):
        return ActionResult(True, f"{vid} is already back in the lab playlist")
    rec = await record()
    pos = insert_position(key, [rec.owner.get(str(i.get("video_id"))) for i in items])
    item, said = await _add_item(ch, pl, vid, pos)
    if item is None:
        return ActionResult(False, f"{vid} not put back: {said}")
    return ActionResult(True, f"{vid} back in the lab playlist {said}")


# -------------------------------------------------------------------- the tick

async def lab_playlist_backfill(limit: int = PER_PASS) -> list[str]:
    """Take out our withdrawn / superseded lab posts, then add each lab's
    current post not yet in it, through the executor, in course order, at
    most `limit` in all. Never raises."""
    try:
        return await _backfill(limit)
    except Exception:
        log.exception("academy lab playlist: the pass failed")
        return ["the lab playlist pass failed (see the log)"]


async def _backfill(limit: int) -> list[str]:
    from ...kernel.executor import ExecutionRequest, execute
    from ..youtube import api
    ch = tut.channel()
    scopes, why = await api.token_scopes(ch)
    if scopes is None:
        return [(f"lab playlist UNKNOWN: the {ch} token's scopes could not be read "
                 f"({why}); nothing tried")]
    if not scopes & set(api.PLAYLIST_SCOPES):
        return [f"lab playlist: the {ch} token cannot manage playlists; nothing tried"]
    rec = await record()
    pl, items, why = await _items(ch)
    if pl is None or items is None:
        return [(f"lab playlist UNKNOWN: it could not be read ({why}); "
                 "nothing tried")]
    present = {str(i.get("video_id")) for i in items}
    removals: dict[str, Lesson] = {}
    for vid in present:
        if (key := rec.stale(vid)) is not None:
            removals[vid] = key
    reqs: list[tuple[str, dict, str]] = [
        (TAKE_OUT_ACTION, {"series": k[0], "episode": k[1], "video_id": vid},
         f"{vid} is a withdrawn or superseded post of {k[0]} #{k[1]}")
        for vid, k in sorted(removals.items(), key=lambda kv: (lab_key(kv[1]), kv[0]))]
    adds = sorted((k for k, v in rec.current.items() if v not in present), key=lab_key)
    reqs += [(ACTION, {"series": k[0], "episode": k[1]},
              f"{k[0]} #{k[1]} is a public lab not yet in \"{LAB_PLAYLIST}\"")
             for k in adds]
    if not reqs:
        return ["lab playlist: in order, nothing to add or take out"]
    # A playlist made this pass may not be listed at once: one add makes it,
    # the rest wait for the next pass (the marker guards a second make too).
    cap = limit if pl else 1
    said: list[str] = []
    for action, params, reason in reqs[:cap]:
        label = f"{params['series']} #{params['episode']}"
        try:
            done = await execute(ExecutionRequest(action=action, params=params,
                                                  reason=reason))
        except Exception:
            log.exception("academy lab playlist: %s %s raised", action, label)
            said.append(f"{label}: {action} raised (see the log)")
            continue
        said.append(f"{label}: {done.detail}")
        if done.failure_class is FailureClass.BUDGET:
            said.append("the hourly ceiling held the rest; next pass")
            return said
    if len(reqs) > cap:
        said.append(f"{len(reqs) - cap} more change(s) wait for the next pass")
    return said
