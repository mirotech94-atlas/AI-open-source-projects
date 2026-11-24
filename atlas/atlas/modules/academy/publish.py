"""`academy.publish_shop` (Tier 1): put the generated storefront live.

The site is built from the checked-in catalog inside the api container and
published like a client site (agency/hosting.py): copied
into `<publish root>/.releases/shop/<stamp>/`, then `<publish root>/shop`
is swapped to point at it. The store is served at ACADEMY_PUBLIC_ORIGIN
(agency.storefront.test).

The build goes to `data/academy/build/`, never under the publish root
(`hosting.root_problem`), so nothing is public until the approved publish
swaps the link. Rollback points the link back at the release that was live
before, and refuses if something newer has been published since.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ...kernel.contracts import ActionResult
from .. import store_origin
from ..agency import hosting
from . import site
from .catalog import CatalogError, load

log = logging.getLogger("atlas.academy.publish")

SLUG = "shop"
THUMB_MAX = 2_000_000                 # bytes: a YouTube thumbnail is under 2 MB
BUILD_ENV = "ACADEMY_BUILD_DIR"
DEFAULT_BUILD_DIR = "data/academy/build"


def build_root() -> Path:
    return Path(os.getenv(BUILD_ENV, DEFAULT_BUILD_DIR))


def point_current(build: Path, stamp: str) -> None:
    """`<build root>/current` -> `<stamp>`, relative (it resolves inside the
    shop container's mount too), swapped atomically. Blocking."""
    tmp = build / f".current.{stamp}"
    if tmp.is_symlink():
        tmp.unlink()
    os.symlink(stamp, tmp)
    os.replace(tmp, build / "current")


def _current_stamp(build: Path) -> str | None:
    link = build / "current"
    return os.readlink(link) if link.is_symlink() else None


def _prune_builds(build: Path, keep: set[str]) -> None:
    """Drop old stamp directories of the build root, never one in `keep`."""
    for p in build.iterdir():
        if p.is_dir() and not p.is_symlink() and hosting.STAMP_RE.fullmatch(p.name) \
                and p.name not in keep:
            shutil.rmtree(p, ignore_errors=True)


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _problem() -> str | None:
    # Vercel takes client previews only; the shop stays on this box.
    if not (hosting.local_mode() or hosting.vercel_mode()):
        return ("AGENCY_DEPLOY_HOST points at another host; the shop is published "
                "only on this box")
    if (why := hosting.slug_problem(SLUG)) is not None:
        return why
    root = hosting.PUBLISH_ROOT
    if (why := hosting.root_problem(root, build_root())) is not None:
        return why
    try:
        hosting.live_release(root, SLUG)
    except ValueError as exc:
        return f"not publishing over something else: {exc}"
    return None


def _changes(build: Path) -> tuple[int, int]:
    """(files in the build, files that differ from the live release)."""
    root = hosting.PUBLISH_ROOT
    live = hosting.live_release(root, SLUG)
    files = [p for p in build.rglob("*") if p.is_file()]
    if live is None:
        return len(files), len(files)
    current = hosting.release_dir(root, SLUG, live)
    changed = 0
    for p in files:
        rel = p.relative_to(build)
        other = current / rel
        # catalog.json carries generated_at; it always differs, so skip it.
        if rel.as_posix() == "academy/catalog.json":
            continue
        if not other.is_file() or _digest(other) != _digest(p):
            changed += 1
    return len(files), changed


def _build(stamp: str, reviews: list[dict] | None = None,
           videos: list[site.Video] | None = None,
           lessons: list[dict] | None = None) -> Path:
    out = build_root() / stamp
    site.build(out, load(), reviews=reviews, videos=_with_thumbs(videos or []),
               lessons=lessons)
    return out


# ------------------------------------------------------------ the videos page

VIDEOS_UNREADABLE = ("academy_tutorial_posts is unreadable: not publishing the store "
                     "without its videos page")


async def public_videos() -> list[site.Video] | None:
    """Every tutorial that is public on YouTube and not withdrawn
    (academy_tutorial_posts), the latest row per episode. None when the
    table can't be read: a publish then refuses rather than replace the
    live /videos/ with an empty one."""
    from ...kernel.state import fetch_all
    try:
        rows = await fetch_all(
            """SELECT DISTINCT ON (series, episode) series, episode, video_id, at
               FROM academy_tutorial_posts
               WHERE public AND NOT withdrawn
               ORDER BY series, episode, id DESC""")
    except Exception:  # noqa: BLE001
        return None
    out = []
    for r in rows:
        at = r["at"]
        if not isinstance(at, datetime):
            continue
        out.append(site.Video(str(r["series"]), int(r["episode"]), str(r["video_id"]),
                              at if at.tzinfo else at.replace(tzinfo=UTC)))
    return out


def _jpeg(data: bytes) -> bytes | None:
    """A JPEG small enough for a page, or None."""
    return data if data[:3] == b"\xff\xd8\xff" and len(data) <= THUMB_MAX else None


def _fetch_thumb(video_id: str) -> bytes | None:
    """YouTube's own thumbnail, downloaded once at build time so the page
    serves it itself and a visitor's browser never asks YouTube. A public
    image read, nothing written anywhere. None on any failure (the page
    then draws a poster). Blocking: runs in the build's thread."""
    import httpx
    if not site.VIDEO_ID_RE.fullmatch(video_id):
        return None
    try:
        r = httpx.get(f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                      timeout=10.0, follow_redirects=False)
    except httpx.HTTPError:
        return None
    return _jpeg(r.content) if r.status_code == 200 else None


def _trusted(m: object, uploaded: datetime) -> bool:
    """A manifest that can be the published upload's: Fish-narrated, with a
    length, and rendered no later than the posts row's `at` -- the moment the
    video went public (tutorials._publish's last UPDATE sets
    `at = CASE WHEN public THEN now() ELSE at END`). A render made after the
    video went public cannot be the published one, nor can one that doesn't
    say when it was made."""
    from . import tutorials
    if not isinstance(m, tutorials.Manifest) or m.audio != "fish" or m.seconds <= 0:
        return False
    try:
        made = datetime.fromisoformat(m.rendered_at)
    except (TypeError, ValueError):
        return False
    if made.tzinfo is None:
        made = made.replace(tzinfo=UTC)
    return made <= uploaded


def _with_manifests(videos: list[site.Video]) -> list[site.Video]:
    """Each video with what its published render's manifest says: length,
    chapters, voiced opener and narrated lines -- only from a trusted
    manifest (_trusted), else none (never a guess). Blocking: reads the
    disk; callers on the event loop use asyncio.to_thread."""
    from dataclasses import replace

    from . import tutorials
    out = []
    for v in videos:
        s = site.ts.series(v.series)
        ep = s.episode(v.episode) if s is not None else None
        m = tutorials.read_manifest(tutorials.episode_dir(s, ep)) if s and ep else None
        if m is not None and _trusted(m, v.uploaded):
            v = replace(v, seconds=float(m.seconds), chapters=tuple(m.chapters),
                        opener=m.opener, timings=tuple(m.timings))
        else:
            v = replace(v, seconds=None, chapters=(), opener="", timings=())
        out.append(v)
    return out


def _with_thumbs(videos: list[site.Video]) -> list[site.Video]:
    """Each video with its own poster and its manifest's data
    (_with_manifests). The poster: the render's thumbnail on disk
    (tutorials.episode_dir), else YouTube's, downloaded here; the page draws
    one when neither is there. Blocking: runs in the build's thread."""
    from dataclasses import replace

    from . import tutorials
    out = []
    for v in _with_manifests(videos):
        thumb = None
        s = site.ts.series(v.series)
        ep = s.episode(v.episode) if s is not None else None
        if s is not None and ep is not None:
            try:
                thumb = _jpeg((tutorials.episode_dir(s, ep) / "thumb.jpg").read_bytes())
            except OSError:
                thumb = None
        if thumb is None:
            thumb = _fetch_thumb(v.video_id)
        out.append(replace(v, thumb=thumb))
    return out


def _live_manifest() -> dict | None:
    """academy/videos/videos.json of the live release, or None (nothing live, or a
    release from before the videos page)."""
    root = hosting.PUBLISH_ROOT
    try:
        live = hosting.live_release(root, SLUG)
        if live is None:
            return None
        path = (hosting.release_dir(root, SLUG, live) / site.HOME_SLUG / site.VIDEOS_SLUG
                / "videos.json")
        return json.loads(path.read_text())
    except (ValueError, OSError):
        return None


def refresh_enabled() -> bool:
    return os.getenv("ACADEMY_VIDEOS_REFRESH", "on").strip().lower() not in (
        "off", "0", "false", "no")


def refresh_interval() -> timedelta:
    try:
        minutes = int(os.getenv("ACADEMY_VIDEOS_REFRESH_MIN", "15"))
    except ValueError:
        minutes = 15
    return timedelta(minutes=max(5, minutes))


async def refresh_videos() -> str:
    """The scheduler's `academy_videos` pass: when the tutorials public on
    YouTube are not the ones the live /videos/ lists, republish the store
    through the executor (academy.publish_shop: dry run, staged release,
    rollback to the one before). Returns one line for the log. Never
    raises."""
    try:
        videos = await public_videos()
        if videos is None:
            return VIDEOS_UNREADABLE
        # With each render's trusted manifest data, so a new render (new
        # length, chapters or lines) republishes its lesson page too.
        want = site.videos_manifest(await asyncio.to_thread(_with_manifests, videos))
        have = await asyncio.to_thread(_live_manifest)
        if have == want:
            return "videos page current"
        from ...kernel.executor import ExecutionRequest, execute
        n = len(want["videos"])  # type: ignore[arg-type]
        done = await execute(ExecutionRequest(
            action="academy.publish_shop", params={},
            reason=(f"the store's videos page is out of date: {n} tutorial(s) are "
                    "public on YouTube")))
        log.info("videos page republish: %s", done.detail[:200])
        return done.detail
    except Exception as exc:  # noqa: BLE001
        log.warning("videos page refresh failed: %s", type(exc).__name__)
        return f"failed: {type(exc).__name__}"


async def _approved_reviews() -> list[dict]:
    """Approved verified-buyer reviews for the pages (reviews.py). An
    unreadable table publishes no review rather than a stale one."""
    from .reviews import approved
    try:
        return await approved()
    except Exception:  # noqa: BLE001
        return []


async def published_lessons() -> list[dict]:
    """The free tutorials live on YouTube, newest first, for the home page's
    featured lesson and the finder: public, not withdrawn, and still in the
    tutorial catalog (tutorial_series.py), whose title and summary they
    carry. An unreadable table publishes no lesson rather than a stale one."""
    from ...kernel.state import fetch_all
    from .tutorial_series import series
    try:
        rows = await fetch_all(
            """SELECT series, episode, video_id FROM academy_tutorial_posts
               WHERE public AND NOT withdrawn ORDER BY at DESC, id DESC LIMIT 40""")
    except Exception:  # noqa: BLE001
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for row in rows or []:
        s = series(str(row["series"]))
        ep = s.episode(int(row["episode"])) if s is not None else None
        vid = str(row["video_id"] or "")
        if s is None or ep is None or not vid or vid in seen:
            continue
        seen.add(vid)
        out.append({"series": s.key, "episode": ep.number, "video_id": vid,
                    "title": ep.title, "summary": ep.summary, "mark": s.mark})
    return out


def _dry(reviews: list[dict] | None = None,
         videos: list[site.Video] | None = None,
         lessons: list[dict] | None = None) -> ActionResult:
    if (why := _problem()) is not None:
        return ActionResult(False, why)
    if videos is None:
        return ActionResult(False, VIDEOS_UNREADABLE)
    try:
        cat = load()
    except CatalogError as exc:
        return ActionResult(False, f"the catalog is not publishable: {exc}")
    stamp = f"dry-{_stamp()}"
    try:
        out = _build(stamp, reviews, videos, lessons)
        total, changed = _changes(out)
    finally:
        shutil.rmtree(build_root() / stamp, ignore_errors=True)
    live = [o.name for o in cat.offers() if o.buy_live]
    shown = len(site.published(videos))
    return ActionResult(True, (
        f"would publish {total} files ({changed} changed) to "
        f"{store_origin.origin()}/{site.HOME_SLUG}/; "
        f"on sale: {', '.join(live) or 'nothing yet'}; "
        f"free videos listed: {shown}"),
        evidence={"files": total, "changed": changed, "on_sale": live,
                  "videos": shown})


def _publish(reviews: list[dict] | None = None,
             videos: list[site.Video] | None = None,
             lessons: list[dict] | None = None) -> ActionResult:
    if (why := _problem()) is not None:
        return ActionResult(False, why)
    if videos is None:
        return ActionResult(False, VIDEOS_UNREADABLE)
    stamp = _stamp()
    root = hosting.PUBLISH_ROOT
    root.mkdir(parents=True, exist_ok=True)
    served = False
    try:
        out = _build(stamp, reviews, videos, lessons)
    except (CatalogError, ValueError, OSError) as exc:
        shutil.rmtree(build_root() / stamp, ignore_errors=True)
        return ActionResult(False, f"build failed, nothing went live: {exc}")
    try:
        previous = hosting.live_release(root, SLUG)
        hosting.stage_release(out, root, SLUG, stamp)
        hosting.point_at(root, SLUG, stamp)
        hosting.prune_releases(root, SLUG,
                               keep={stamp} | ({previous} if previous else set()))
        # The shop container serves `<build root>/current`, so the publish
        # must move that link too (it is what makes the release public).
        served_before = _current_stamp(build_root())
        point_current(build_root(), stamp)
        served = True
        _prune_builds(build_root(),
                      {stamp} | ({served_before} if served_before else set()))
    except PermissionError as exc:
        return ActionResult(False, (
            f"publish failed, nothing went live: {exc}. The publish root must be "
            "writable by the api (uid 10001)"))
    except (ValueError, OSError) as exc:
        return ActionResult(False, f"publish failed, nothing went live: {exc}")
    finally:
        if not served:
            shutil.rmtree(out, ignore_errors=True)
    url = f"{store_origin.origin()}/{site.HOME_SLUG}/"
    return ActionResult(True, f"published the ATLAS Academy shop at {url} "
                              f"(release {stamp})",
                        evidence={"url": url, "release": stamp, "previous": previous},
                        rollback_token=json.dumps({"release": stamp,
                                                   "previous": previous}))


def _unpublish(token: str) -> ActionResult:
    try:
        data = json.loads(token)
        release, previous = data["release"], data.get("previous")
    except (ValueError, KeyError, TypeError):
        return ActionResult(False, "unreadable rollback token")
    if not isinstance(release, str) or not hosting.STAMP_RE.fullmatch(release) \
            or not (previous is None or (isinstance(previous, str)
                                         and hosting.STAMP_RE.fullmatch(previous))):
        return ActionResult(False, "malformed rollback token")
    root = hosting.PUBLISH_ROOT
    try:
        live = hosting.live_release(root, SLUG)
        if live != release:
            return ActionResult(False, (
                f"release {release} is no longer live (live: {live or 'nothing'}); "
                "not rolling back over it"))
        if previous:
            if not hosting.release_dir(root, SLUG, previous).is_dir():
                return ActionResult(False, f"release {previous} is no longer on disk")
            hosting.point_at(root, SLUG, previous)
            if not (build_root() / previous).is_dir():
                # The served copy was pruned: restore it from the release.
                shutil.copytree(hosting.release_dir(root, SLUG, previous),
                                build_root() / previous)
            point_current(build_root(), previous)
            return ActionResult(True, f"restored release {previous} of the shop")
        hosting.point_at(root, SLUG, None)
        (build_root() / "current").unlink(missing_ok=True)   # the served link too
        if os.path.lexists(build_root() / "current"):
            return ActionResult(False, "the served link could not be removed")
    except (ValueError, OSError) as exc:
        return ActionResult(False, f"rollback failed: {exc}")
    return ActionResult(True, "unpublished the shop; it was the first release")


async def publish_shop_dry() -> ActionResult:
    reviews = await _approved_reviews()
    videos = await public_videos()
    return await asyncio.to_thread(_dry, reviews, videos, await published_lessons())


async def publish_shop() -> ActionResult:
    reviews = await _approved_reviews()
    videos = await public_videos()
    return await asyncio.to_thread(_publish, reviews, videos, await published_lessons())


async def publish_shop_rollback(token: str) -> ActionResult:
    return await asyncio.to_thread(_unpublish, token)


async def republish_if_changed() -> str:
    """At boot: build the store once and, when any file but catalog.json
    differs from the live release, publish through the executor. Returns
    one line for the log. Never raises."""
    import logging
    log = logging.getLogger("atlas.academy.publish")
    try:
        dry = await publish_shop_dry()
        if not dry.ok:
            log.info("store not republished: %s", dry.detail[:200])
            return dry.detail
        changed = int((dry.evidence or {}).get("changed") or 0)
        if changed == 0:
            return "store unchanged"
        from ...kernel.executor import ExecutionRequest, execute
        done = await execute(ExecutionRequest(
            action="academy.publish_shop", params={},
            reason=f"{changed} file(s) differ from the live store after a deploy"))
        log.info("store republish: %s", done.detail[:200])
        return done.detail
    except Exception as exc:  # noqa: BLE001
        log.warning("store self-republish failed: %s", type(exc).__name__)
        return f"failed: {type(exc).__name__}"
