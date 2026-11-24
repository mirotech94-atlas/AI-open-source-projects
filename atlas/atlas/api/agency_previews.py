"""Built demo sites, shown inside the portal's Previews panel.

`GET /api/agency/previews` lists the built sites under the agency sites
directory; `GET /api/agency/preview/{slug}/{path}` serves their files.

Generated HTML is built partly from other people's websites, so it must never
run as the dashboard origin. Every response -- files, 404s, all of it -- carries
a sandbox CSP (opaque origin, no network, no framing escape), `nosniff` and
`no-store`. `dashboard._VIEWABLE` deliberately excludes html and stays so.
Read-only: nothing here writes, runs an action or calls a model. An unreadable
sites directory is a 503, never an empty list (invariants 6 and 7).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response

from ..kernel.state import fetch_all
from . import auth

log = logging.getLogger("atlas.agency_previews")

router = APIRouter(prefix="/api/agency")
SignedIn = Annotated[auth.Session, Depends(auth.require)]

_SLUG = re.compile(r"[a-z0-9-]+")
_LEAD_ID = re.compile(r"-(\d+)$")
_TYPES = {
    ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml",
    ".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp",
    ".woff2": "font/woff2", ".json": "application/json",
}
CSP = ("sandbox allow-scripts allow-top-navigation-to-custom-protocols; default-src 'none'; style-src 'unsafe-inline' 'self'; "
       "img-src 'self' data:; font-src 'self' data:; script-src 'unsafe-inline' 'self'")
# A frame sandboxed without allow-same-origin has an opaque origin, so the
# browser sends no session cookie when a link inside it is followed. In-site
# links therefore carry a short-lived token in the path instead (below). The one
# extra sandbox keyword lets tel: and mailto: leave the frame; it grants no
# http(s) top navigation, no forms, no popups and no same-origin.
_HEADERS = {"Content-Security-Policy": CSP, "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store"}


_TOKEN_BUCKET = 3600


def mint_token(slug: str, now: float | None = None) -> str:
    """`<expiry>.<hmac>` scoped to one slug, valid for one to two hours.

    The expiry is a whole-hour boundary so the token (and the frame's src) is
    stable inside an hour: a list refresh does not reload an open frame."""
    t = time.time() if now is None else now
    exp = (int(t) // _TOKEN_BUCKET + 2) * _TOKEN_BUCKET
    return f"{exp}.{_sign(slug, exp)}"


def _sign(slug: str, exp: int) -> str:
    return hmac.new(auth._secret().encode(), f"atlas-preview:{slug}:{exp}".encode(),
                    hashlib.sha256).hexdigest()


def verify_token(token: str, slug: str) -> bool:
    try:
        exp_s, _, sig = token.partition(".")
        if not exp_s.isdigit() or not sig:
            return False
        exp = int(exp_s)
        ok = hmac.compare_digest(sig, _sign(slug, exp))
        return ok and exp > time.time()
    except Exception:  # noqa: BLE001
        return False


class RedactTokenFilter(logging.Filter):
    """Keeps the path token out of uvicorn's access log."""
    _RE = re.compile(r"/preview-t/[^/\s]+/")

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                self._RE.sub("/preview-t/[token]/", a) if isinstance(a, str) else a
                for a in record.args)
        return True


logging.getLogger("uvicorn.access").addFilter(RedactTokenFilter())


def sites_dir() -> Path:
    # Same default as modules/agency/build.SITES_DIR, read at call time.
    return Path(os.getenv("AGENCY_SITES_DIR", "data/agency/sites"))


def _not_found() -> Response:
    return Response("not found", status_code=404, media_type="text/plain",
                    headers=_HEADERS)


def _list_slugs(root: Path) -> list[str]:
    return sorted(p.name for p in root.iterdir()
                  if p.is_dir() and _SLUG.fullmatch(p.name))


@router.get("/previews")
async def previews(_s: SignedIn) -> Any:
    read_at = datetime.now(UTC).isoformat()
    root = sites_dir()
    try:
        slugs = await asyncio.to_thread(_list_slugs, root)
    except OSError as exc:
        log.warning("previews: sites directory unreadable: %s", exc)
        raise HTTPException(503, "the built sites could not be read") from None
    ids = [int(m.group(1)) for s in slugs if (m := _LEAD_ID.search(s))]
    names: dict[int, str] = {}
    if ids:
        try:
            rows = await fetch_all(
                "SELECT id, name FROM agency_leads WHERE id = ANY($1::bigint[])", ids)
            names = {int(r["id"]): str(r["name"]) for r in rows}
        except Exception as exc:  # noqa: BLE001
            log.warning("previews: lead names unreadable: %s", exc)
    out = []
    for s in slugs:
        m = _LEAD_ID.search(s)
        lid = int(m.group(1)) if m else None
        out.append({
            "lead_id": lid,
            "name": names.get(lid) if lid is not None and lid in names
            else s.rsplit("-", 1)[0].replace("-", " ").title() if m else s,
            "slug": s,
            "built": (root / s / "index.html").is_file(),
            "src": f"/api/agency/preview-t/{mint_token(s)}/{s}/",
        })
    return JSONResponse({"previews": out, "read_at": read_at}, headers=_HEADERS)


def _resolve(slug: str, path: str) -> Path | None:
    if not _SLUG.fullmatch(slug):
        return None
    base = (sites_dir() / slug)
    try:
        base = base.resolve(strict=True)
        target = (base / (path or "index.html")).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None
    if not target.is_relative_to(base) or not target.is_file():
        return None
    if target.suffix.lower() not in _TYPES:
        return None
    return target


@router.get("/preview/{slug}/{path:path}")
async def preview_file(slug: str, path: str, _s: SignedIn) -> Response:
    target = await asyncio.to_thread(_resolve, slug, path)
    if target is None:
        return _not_found()
    return FileResponse(target, media_type=_TYPES[target.suffix.lower()],
                        headers=_HEADERS)


@router.get("/preview-t/{token}/{slug}/{path:path}")
async def preview_file_token(token: str, slug: str, path: str) -> Response:
    """Same files as `preview_file`, reached by a frame that cannot send the
    session cookie. The token is only ever minted for a signed-in /previews
    read, is bound to this slug, and grants GET of that slug's files through the
    same `_resolve` -- nothing else."""
    if not verify_token(token, slug):
        return Response("not signed in", status_code=401, media_type="text/plain",
                        headers=_HEADERS)
    target = await asyncio.to_thread(_resolve, slug, path)
    if target is None:
        return _not_found()
    return FileResponse(target, media_type=_TYPES[target.suffix.lower()],
                        headers=_HEADERS)
