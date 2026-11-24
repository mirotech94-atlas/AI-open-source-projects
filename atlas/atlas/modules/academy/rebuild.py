"""Rebuild the posted-tutorial record from YouTube (read-only), 2026-10-27.

The restored database ends at the last backup; tutorials posted after it
are on YouTube but not in `academy_tutorial_posts`, and the comments under
them are not in `academy_tutorial_comments`. Without those rows `lineup`
would render and post an episode again and `pin_pass` would comment a
second time under a video. This reads what the channel shows and inserts
only the rows the database lacks:

* uploads: channels.list(mine) -> the uploads playlist -> playlistItems
  (paginated, 1 unit a page; never search.list) -> videos.list(status,
  snippet) for privacy and publishedAt;
* series: each `tutorial_series.PLAYLISTS` title -> find_playlist ->
  playlistItems, for `playlist_id` and `item_id`;
* a video already on record keeps the (series, episode) of its row;
* any other maps to (series, episode) only when it is in that series'
  playlist AND its title is that episode's: the written title, or the
  title as uploaded (`tutorials.final_title`: the level's formula with the
  measured minutes, `TITLE_PATTERNS`) with this episode's hook and
  benefit; one match exactly, never a best guess. The channel welcome (no
  playlist) maps by title alone, only when it is in no series playlist.
  Anything else is listed for the owner, never inserted;
* private uploads are listed, never inserted (plan step 5.1); `lineup`
  treats their episodes as not posted;
* comments: commentThreads.list under every mapped public video, only
  those whose author is this channel (channels.list(mine) id). If that id
  cannot be read, no comment row is inserted and the report says UNKNOWN.

`thumb_set` and `captions_set` keep their defaults (false): nothing read
here proves either. Any failed read refuses the whole rebuild: a partial
picture would be a guess. `--apply` writes database rows only; nothing
here writes to YouTube or anywhere else. Every line says when it was read.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from ...kernel import state
from ..youtube import api
from . import tutorial_series as ts

POSTS_INSERT = """
INSERT INTO academy_tutorial_posts
       (at, series, episode, video_id, playlist_id, item_id, public, detail)
SELECT $1::timestamptz, $2::text, $3::int, $4::text, $5::text, $6::text,
       $7::boolean, $8::text
 WHERE NOT EXISTS (SELECT 1 FROM academy_tutorial_posts WHERE video_id = $4::text)"""

COMMENTS_INSERT = """
INSERT INTO academy_tutorial_comments
       (at, series, episode, video_id, comment_id, withdrawn, text)
SELECT $1::timestamptz, $2::text, $3::int, $4::text, $5::text, $6::boolean, $7::text
 WHERE NOT EXISTS (SELECT 1 FROM academy_tutorial_comments WHERE comment_id = $5::text)"""



@dataclass
class Report:
    ok: bool
    read_at: datetime | None
    lines: list[str] = field(default_factory=list)
    posts: list[dict] = field(default_factory=list)       # rows to insert / inserted
    comments: list[dict] = field(default_factory=list)
    unmapped: list[dict] = field(default_factory=list)
    private: list[dict] = field(default_factory=list)


def _when(text: object) -> datetime | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        at = datetime.fromisoformat(text)
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def _stamp(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _is_title_of(ep: ts.Episode, title: str) -> bool:
    """`title` is this episode's: as written, or as uploaded by formula
    (tutorials.final_title -> tutorial_series.title_for)."""
    if title == ep.title:
        return True
    if not ep.title_hook:
        return False
    got = ts.TITLE_PATTERNS[ep.formula_level].match(title)
    if got is None or got["x"] != ep.title_hook:
        return False
    return ep.formula_level != 3 or got["benefit"] == ep.title_benefit


def _episodes_titled(s: ts.Series, title: str) -> list[int]:
    return [ep.number for ep in s.episodes if _is_title_of(ep, title)]


async def _series_items(channel: str
                        ) -> tuple[dict[str, tuple[str, dict[str, str]]] | None,
                                   str | None]:
    """{series key: (playlist id, {video id: item id})} for every series
    whose playlist exists; a series without one yet is left out."""
    out: dict[str, tuple[str, dict[str, str]]] = {}
    for s in ts.SERIES:
        if not s.playlist:
            continue
        found, why = await api.find_playlist(channel, s.playlist)
        if found is None:
            return None, f"playlist {s.playlist!r}: {why}"
        if not found.get("id"):
            continue
        items, why = await api.playlist_items(channel, str(found["id"]))
        if why is not None:
            return None, f"playlist {s.playlist!r}: {why}"
        out[s.key] = (str(found["id"]),
                      {str(i["video_id"]): str(i.get("item_id") or "") for i in items})
    return out, None


def _map(video: dict, series_items: dict[str, tuple[str, dict[str, str]]]
         ) -> tuple[str, int, str | None, str | None] | None:
    """(series, episode, playlist id, item id), or None: exact title match
    inside the series' own playlist; the welcome by title, outside them all."""
    vid, title = str(video["video_id"]), str(video.get("title") or "")
    hits: list[tuple[str, int, str | None, str | None]] = []
    for key, (pl, items) in series_items.items():
        if vid not in items:
            continue
        s = ts.series(key)
        for n in _episodes_titled(s, title) if s is not None else []:
            hits.append((key, n, pl, items[vid] or None))
    if not hits and not any(vid in items for _pl, items in series_items.values()):
        for s in ts.SERIES:
            if not s.playlist:
                hits.extend((s.key, n, None, None) for n in _episodes_titled(s, title))
    return hits[0] if len(hits) == 1 else None


def _known(vid: str, row: dict, series_items: dict[str, tuple[str, dict[str, str]]]
           ) -> tuple[str, int, str | None, str | None]:
    """A video on record: its row's (series, episode), with the series'
    playlist ids when it is in that playlist."""
    key, n = str(row["series"]), int(row["episode"])
    pl, items = series_items.get(key, (None, {}))
    return (key, n, pl, items.get(vid) or None) if vid in items else (key, n, None, None)


async def run(apply: bool, channel: str | None = None) -> Report:
    """Read YouTube, compare with the database, and with `apply` insert
    the missing rows. Never raises for a read failure: the report says it."""
    from . import tutorials
    ch = channel or tutorials.channel()
    rep = Report(ok=False, read_at=None)

    def refuse(why: str) -> Report:
        rep.lines.append(f"REFUSED, nothing inserted: {why}")
        return rep

    try:
        have_posts = {str(r["video_id"]): dict(r) for r in await state.fetch_all(
            "SELECT video_id, series, episode FROM academy_tutorial_posts")}
        have_comments = {str(r["comment_id"]) for r in await state.fetch_all(
            "SELECT comment_id FROM academy_tutorial_comments")}
    except Exception as exc:                            # noqa: BLE001
        return refuse(f"the database could not be read ({type(exc).__name__})")
    db_read = datetime.now(UTC)

    uploads_id, why = await api.uploads_playlist(ch)
    if uploads_id is None:
        return refuse(f"uploads playlist: {why}")
    uploaded, why = await api.playlist_items(ch, uploads_id)
    if why is not None:
        return refuse(f"uploads: {why}")
    videos, why = await api.video_details(ch, [i["video_id"] for i in uploaded])
    if why is not None:
        return refuse(f"videos.list: {why}")
    series_items, why = await _series_items(ch)
    if series_items is None:
        return refuse(why or "series playlists unreadable")
    our_id, id_why = await api.own_channel_id(ch)
    read_at = datetime.now(UTC)
    rep.read_at = read_at
    when = _stamp(read_at)
    rep.lines.append(f"channel {ch}: {len(videos)} upload(s) read {when}; "
                     f"database read {_stamp(db_read)}")

    mapped: list[dict] = []
    for v in sorted(videos, key=lambda v: str(v.get("published_at") or "")):
        vid = str(v.get("video_id") or "")
        at = _when(v.get("published_at"))
        privacy = str(v.get("privacy") or "")
        known = vid in have_posts
        hit = (_known(vid, have_posts[vid], series_items) if known
               else _map(v, series_items))
        if hit is None or at is None:
            rep.unmapped.append(v)
            rep.lines.append(f"unmapped  {vid} {privacy} {v.get('published_at')} "
                             f"{str(v.get('title') or '')[:70]!r} (read {when})")
            continue
        key, n, pl, item = hit
        if privacy == "private":
            rep.private.append({**v, "series": key, "episode": n})
            rep.lines.append(f"private   {vid} -> {key} #{n}: listed, not inserted; "
                             f"lineup treats {key} #{n} as not posted (read {when})")
            continue
        row = {"at": at, "series": key, "episode": n, "video_id": vid,
               "playlist_id": pl, "item_id": item, "public": privacy == "public",
               "detail": f"rebuilt from YouTube read {read_at.isoformat()}"}
        mapped.append(row)
        if not known:
            rep.posts.append(row)
        rep.lines.append(f"{'on record' if known else 'NEW':9} {vid} -> {key} #{n} "
                         f"{privacy} published {_stamp(at)} playlist {pl or '-'} "
                         f"item {item or '-'} (read {when})")

    if our_id is None:
        rep.lines.append(f"comments UNKNOWN: this channel's id could not be read "
                         f"({id_why}); no comment row inserted")
    else:
        for row in mapped:
            if not row["public"]:
                continue
            got, why = await api.comment_threads(ch, row["video_id"])
            if why is not None:
                rep.lines.append(f"comments UNKNOWN under {row['video_id']}: {why}; "
                                 "pin_pass may comment there again")
                continue
            for c in got:
                cid = str(c.get("comment_id") or "")
                if c.get("author_channel_id") != our_id or not cid \
                        or cid in have_comments:
                    continue
                text = str(c.get("text") or "")
                rep.comments.append({
                    "at": _when(c.get("published_at")) or read_at,
                    "series": row["series"], "episode": row["episode"],
                    "video_id": row["video_id"], "comment_id": cid,
                    "withdrawn": text.strip() == tutorials.WITHDRAWN,
                    "text": text})
                rep.lines.append(f"NEW comment {cid} under {row['video_id']} "
                                 f"({row['series']} #{row['episode']}, read "
                                 f"{_stamp(datetime.now(UTC))})")

    verb = "inserting" if apply else "would insert"
    rep.lines.append(f"{verb} {len(rep.posts)} post row(s) and {len(rep.comments)} "
                     f"comment row(s); {len(rep.unmapped)} unmapped, "
                     f"{len(rep.private)} private, listed only (read {when})")
    if apply:
        for p in rep.posts:
            await state.execute_sql(POSTS_INSERT, p["at"], p["series"], p["episode"],
                                    p["video_id"], p["playlist_id"], p["item_id"],
                                    p["public"], p["detail"])
        for c in rep.comments:
            await state.execute_sql(COMMENTS_INSERT, c["at"], c["series"],
                                    c["episode"], c["video_id"], c["comment_id"],
                                    c["withdrawn"], c["text"])
        rep.lines.append(f"inserted at {_stamp(datetime.now(UTC))}")
    rep.ok = True
    return rep
