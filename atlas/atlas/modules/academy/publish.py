"""`academy.publish_shop` (Tier 2): put the generated storefront live.

The site is built from the checked-in catalog inside the api container and
published exactly like a Mapfront client site (agency/hosting.py): copied
into `<publish root>/.releases/shop/<stamp>/`, then `<publish root>/shop`
is swapped to point at it. Caddy serves `/srv/sites/shop` for
shop.mapfront.io, and on-demand TLS is granted only to a slug whose path is
such a release link -- a plain directory there would never get a
certificate.

The build goes to `data/academy/build/`, never under the publish root
(`hosting.root_problem`), so nothing is public until the approved publish
swaps the link. Rollback points the link back at the release that was live
before, and refuses if something newer has been published since.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from ...kernel.contracts import ActionResult
from ..agency import hosting
from . import site
from .catalog import CatalogError, load

SLUG = "shop"
BUILD_ENV = "ACADEMY_BUILD_DIR"
DEFAULT_BUILD_DIR = "data/academy/build"


def build_root() -> Path:
    return Path(os.getenv(BUILD_ENV, DEFAULT_BUILD_DIR))


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _problem() -> str | None:
    if not hosting.local_mode():
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
        if rel.as_posix() == "catalog.json":
            continue
        if not other.is_file() or _digest(other) != _digest(p):
            changed += 1
    return len(files), changed


def _build(stamp: str, reviews: list[dict] | None = None) -> Path:
    out = build_root() / stamp
    site.build(out, load(), reviews=reviews)
    return out


async def _approved_reviews() -> list[dict]:
    """Approved verified-buyer reviews for the pages (reviews.py). An
    unreadable table publishes no review rather than a stale one."""
    from .reviews import approved
    try:
        return await approved()
    except Exception:                                   # noqa: BLE001
        return []


def _dry(reviews: list[dict] | None = None) -> ActionResult:
    if (why := _problem()) is not None:
        return ActionResult(False, why)
    try:
        cat = load()
    except CatalogError as exc:
        return ActionResult(False, f"the catalog is not publishable: {exc}")
    stamp = f"dry-{_stamp()}"
    try:
        out = _build(stamp, reviews)
        total, changed = _changes(out)
    finally:
        shutil.rmtree(build_root() / stamp, ignore_errors=True)
    live = [o.name for o in cat.offers() if o.buy_live]
    return ActionResult(True, (
        f"would publish {total} files ({changed} changed) to "
        f"{hosting.public_url(SLUG)}; on sale: {', '.join(live) or 'nothing yet'}"),
        evidence={"files": total, "changed": changed, "on_sale": live})


def _publish(reviews: list[dict] | None = None) -> ActionResult:
    if (why := _problem()) is not None:
        return ActionResult(False, why)
    stamp = _stamp()
    root = hosting.PUBLISH_ROOT
    root.mkdir(parents=True, exist_ok=True)
    try:
        out = _build(stamp, reviews)
    except (CatalogError, ValueError, OSError) as exc:
        shutil.rmtree(build_root() / stamp, ignore_errors=True)
        return ActionResult(False, f"build failed, nothing went live: {exc}")
    try:
        previous = hosting.live_release(root, SLUG)
        hosting.stage_release(out, root, SLUG, stamp)
        hosting.point_at(root, SLUG, stamp)
        hosting.prune_releases(root, SLUG,
                               keep={stamp} | ({previous} if previous else set()))
    except PermissionError as exc:
        return ActionResult(False, (
            f"publish failed, nothing went live: {exc}. The publish root must be "
            "writable by the api (uid 10001)"))
    except (ValueError, OSError) as exc:
        return ActionResult(False, f"publish failed, nothing went live: {exc}")
    finally:
        shutil.rmtree(out, ignore_errors=True)
    url = hosting.public_url(SLUG)
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
            return ActionResult(True, f"restored release {previous} of the shop")
        hosting.point_at(root, SLUG, None)
    except (ValueError, OSError) as exc:
        return ActionResult(False, f"rollback failed: {exc}")
    return ActionResult(True, "unpublished the shop; it was the first release")


async def publish_shop_dry() -> ActionResult:
    reviews = await _approved_reviews()
    return await asyncio.to_thread(_dry, reviews)


async def publish_shop() -> ActionResult:
    reviews = await _approved_reviews()
    return await asyncio.to_thread(_publish, reviews)


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
    except Exception as exc:                            # noqa: BLE001
        log.warning("store self-republish failed: %s", type(exc).__name__)
        return f"failed: {type(exc).__name__}"
