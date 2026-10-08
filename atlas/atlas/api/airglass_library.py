"""Air Glass's free 3D library: search two open model collections and fetch a
model's files for the hologram. Read-only: every call here is a GET.

What was read, and when (all on 2026-10-07):

  * Poly Haven, https://api.polyhaven.com/assets?type=models -- 521 models,
    each with name, tags, categories, authors, download_count. Served with
    `access-control-allow-origin: *` and `cache-control: max-age=43200`.
  * https://api.polyhaven.com/files/<slug> -- per model, `gltf.<1k|2k|4k>.gltf`
    = {url, size, include: {"<relative uri>": {url, size}}}: a .gltf, its .bin
    and its textures, all on dl.polyhaven.org. The relative uris inside the
    .gltf are exactly the `include` keys (ArmChair_01: "ArmChair_01.bin",
    "textures/Armchair_01_diff_1k.jpg"). At 1k most models are 1-3 MB in all;
    a few are huge (jacaranda_tree: 214 MB), so the size is checked.
  * https://polyhaven.com/license -- every asset is CC0; no attribution needed.
  * https://github.com/Poly-Haven/Public-API/blob/master/ToS.md -- the API is
    free with no key, but (2.4) calls must carry a distinctive User-Agent and
    (2.5) an interface that surfaces its content must say it comes from Poly
    Haven. So every request sends USER_AGENT and every result names the source.
  * Khronos glTF-Sample-Assets, https://raw.githubusercontent.com/KhronosGroup/
    glTF-Sample-Assets/main/Models/model-index.json -- 150 models with their
    variants (glTF, glTF-Binary, Draco, KTX...) and a screenshot path; no
    license there. Each model's own `metadata.json` carries `legal[].spdx`:
    of 150, 62 are CC0-1.0, 45 CC-BY-4.0 and the rest mixed or restricted
    (DamagedHelmet includes CC-BY-NC-4.0; others are under EULAs). So a
    Khronos model is offered only when EVERY legal entry is CC0-1.0 or
    CC-BY-4.0, read fresh from its metadata.json; when that file cannot be
    read, its license is unknown and it is left out -- never assumed CC0.

**Why everything goes through ATLAS.** All four hosts send CORS `*`, but the
dashboard's CSP (Caddyfile) is `connect-src 'self'; img-src 'self' data:`, so
the page can fetch neither the files nor the thumbnails itself. These routes
are that same-origin path. The CSP is not loosened.

**Not an open proxy.** The page never sends a URL. It sends an id
("polyhaven:ArmChair_01", "khronos:ToyCar") and, for a model's other files,
the relative uri written in its .gltf. The upstream URL is built here from the
source's own listing; a Poly Haven part must be an exact key of that model's
`include` map, a Khronos part a plain relative path under the model's folder.
The host must then be on HOSTS, redirects are refused, every body is capped
(a file at MAX_FILE_BYTES, a thumbnail at MAX_THUMB_BYTES) and served with a
type chosen here from its extension, never the upstream Content-Type, plus
`nosniff`: these bytes are served from the ATLAS origin.

**What comes back is data.** Names, tags, descriptions and author names are
the sources' words: trimmed, capped, and shown. They are never instructions;
if one reaches a model it goes through the gesture context, which
airglass.py wraps as untrusted.

**When a source does not answer, it says so** (invariant 6): each result set
carries the state of each source, and when no source answered the route is a
502 naming the host -- never an empty list that looks like "nothing found".
A size is the source's own figure (the sum of the listed file sizes), or null
when the source does not list one.
"""
from __future__ import annotations

import asyncio
import json
import posixpath
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

USER_AGENT = "ATLAS-AirGlass/1.0 (private dashboard; Poly Haven API ToS 2.4)"
HOSTS = frozenset({"api.polyhaven.com", "dl.polyhaven.org", "cdn.polyhaven.com",
                   "raw.githubusercontent.com"})
MAX_FILE_BYTES = 40 * 1024 * 1024     # one file through the proxy
MAX_MODEL_BYTES = 40 * 1024 * 1024    # a whole model, by the source's listed sizes
MAX_THUMB_BYTES = 1024 * 1024
MAX_JSON_BYTES = 8 * 1024 * 1024      # the Poly Haven listing is ~550 KB
MAX_QUERY = 80
PER_SOURCE = 12                       # results (and per-model lookups) per source
TIMEOUT = httpx.Timeout(12.0, connect=5.0)

LISTING_TTL = 3600.0
DETAIL_TTL = 6 * 3600.0

PH_API = "https://api.polyhaven.com"
PH_THUMB = "https://cdn.polyhaven.com/asset_img/thumbs/{slug}.png?width=256&height=256"
PH_RES = ("1k", "2k")                 # smallest first; 4k is never offered
KH_BASE = "https://raw.githubusercontent.com/KhronosGroup/glTF-Sample-Assets/main/Models/"
KH_VARIANTS = ("glTF-Binary", "glTF")  # Draco/KTX/Meshopt need decoders the CSP has not got
OPEN_LICENSES = {"CC0-1.0": "CC0 1.0", "CC-BY-4.0": "CC BY 4.0"}

SOURCE_NAME = {"polyhaven": "Poly Haven", "khronos": "Khronos glTF Sample Assets"}
SOURCE_HOST = {"polyhaven": "api.polyhaven.com", "khronos": "raw.githubusercontent.com"}

TYPES = {
    ".gltf": "model/gltf+json", ".glb": "application/octet-stream",
    ".bin": "application/octet-stream", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".webp": "image/webp",
}
THUMB_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
               ".webp": "image/webp"}


def image_type(data: bytes) -> str | None:
    """A thumbnail's type from its own first bytes, or None if it is not one
    of the three pictures a thumbnail may be."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None

_SLUG = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_FILE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,120}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
STOP = frozenset({"a", "an", "the", "some", "me", "of", "model", "3d", "please", "show",
                  "find", "get", "bring", "pull", "up", "search", "for", "atla"})


class LibraryError(Exception):
    status = 500


class Unreachable(LibraryError):
    status = 502

    def __init__(self, host: str) -> None:
        super().__init__(f"the library at {host} did not answer")
        self.host = host


class Garbled(LibraryError):
    status = 502


class Refused(LibraryError):
    status = 400


class NotFound(LibraryError):
    status = 404


class TooLarge(LibraryError):
    status = 413


def _client() -> httpx.AsyncClient:
    """One client per request. Tests replace this with a MockTransport one."""
    return httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False,
                             headers={"User-Agent": USER_AGENT})


# ------------------------------------------------------------------ fetching

async def fetch(client: httpx.AsyncClient, url: str, cap: int) -> bytes:
    """GET `url` from an allow-listed host, refused past `cap` bytes (by the
    declared length first, then while reading). Anything but a 200 -- a
    redirect included -- is the host not answering."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme != "https" or host not in HOSTS:
        raise Refused(f"{host or 'that address'} is not a library host")
    try:
        async with client.stream("GET", url) as r:
            if r.status_code != 200:
                raise Unreachable(host)
            declared = r.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > cap:
                raise TooLarge(_too_large(cap))
            buf = bytearray()
            async for chunk in r.aiter_bytes():
                buf += chunk
                if len(buf) > cap:
                    raise TooLarge(_too_large(cap))
            return bytes(buf)
    except httpx.HTTPError as exc:
        raise Unreachable(host) from exc


def _too_large(cap: int) -> str:
    return f"that file is larger than {cap // (1024 * 1024)} MB"


async def _json(client: httpx.AsyncClient, url: str) -> Any:
    data = await fetch(client, url, MAX_JSON_BYTES)
    try:
        return json.loads(data)
    except ValueError as exc:
        raise Unreachable(urlsplit(url).hostname or "?") from exc


@dataclass
class _Cache:
    ttl: float
    items: dict[str, tuple[float, Any]] = field(default_factory=dict)

    def get(self, key: str) -> Any:
        hit = self.items.get(key)
        if hit and time.monotonic() - hit[0] < self.ttl:
            return hit[1]
        return None

    def put(self, key: str, value: Any) -> Any:
        self.items[key] = (time.monotonic(), value)
        return value


_LISTINGS = _Cache(LISTING_TTL)
_DETAILS = _Cache(DETAIL_TTL)


def clear_cache() -> None:
    _LISTINGS.items.clear()
    _DETAILS.items.clear()


# ------------------------------------------------------------------- words

def text(v: Any, cap: int) -> str:
    """A source's string, as data: control characters out, trimmed, capped."""
    if not isinstance(v, str):
        return ""
    return _CONTROL.sub(" ", v).strip()[:cap]


def words(s: str) -> list[str]:
    """Whole words of a name or tag: CamelCase, `_`, `-` and spaces split, a
    plain plural made singular. "ToyCar" -> toy car; "covered_car" -> covered
    car; "Chairs" -> chair. "carrot" never matches "car"."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)
    out = []
    for w in re.split(r"[^A-Za-z0-9]+", s.lower()):
        if not w:
            continue
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


def query_words(q: str) -> list[str]:
    return [w for w in words(q) if w not in STOP]


def score(q: list[str], name: list[str], other: list[str]) -> int:
    """0 unless every query word is a whole word of the name or tags; name
    hits count double."""
    if not q:
        return 1
    have_name, have_other = set(name), set(other)
    if not all(w in have_name or w in have_other for w in q):
        return 0
    return sum(2 if w in have_name else 1 for w in q)


def parse_id(raw: str) -> tuple[str, str]:
    source, _, slug = raw.partition(":")
    if source not in SOURCE_NAME or not _SLUG.match(slug):
        raise NotFound("no such library model")
    return source, slug


def safe_part(raw: str) -> str:
    """A relative path inside a model's folder, or Refused: no scheme, no
    absolute path, no `..`, no query, plain file names only."""
    if not raw or len(raw) > 200 or raw.startswith("/") or "\\" in raw:
        raise Refused("not a file of this model")
    segs = raw.split("/")
    if any(not _FILE.match(s) or s in (".", "..") for s in segs):
        raise Refused("not a file of this model")
    if posixpath.normpath(raw) != raw:
        raise Refused("not a file of this model")
    return raw


def _ext(name: str) -> str:
    return posixpath.splitext(name.lower())[1]


# --------------------------------------------------------------- Poly Haven

async def _ph_listing(client: httpx.AsyncClient) -> dict[str, Any]:
    hit = _LISTINGS.get("polyhaven")
    if hit is not None:
        return hit
    raw = await _json(client, f"{PH_API}/assets?type=models")
    if not isinstance(raw, dict):
        raise Unreachable("api.polyhaven.com")
    listing = {k: v for k, v in raw.items() if _SLUG.match(k) and isinstance(v, dict)}
    return _LISTINGS.put("polyhaven", listing)


@dataclass
class _PhModel:
    main: str                       # the .gltf's file name
    urls: dict[str, str]            # "" -> the .gltf; include key -> its url
    size: int | None


def _ph_pick(files: Any) -> _PhModel | None:
    """The smallest glTF on offer, with every url on an allow-listed host."""
    gltf = files.get("gltf") if isinstance(files, dict) else None
    if not isinstance(gltf, dict):
        return None
    for res in PH_RES:
        entry = gltf.get(res, {}).get("gltf") if isinstance(gltf.get(res), dict) else None
        if not isinstance(entry, dict) or not isinstance(entry.get("url"), str):
            continue
        urls = {"": entry["url"]}
        sizes: list[Any] = [entry.get("size")]
        include = entry.get("include") or {}
        if not isinstance(include, dict):
            continue
        for key, inc in include.items():
            if not isinstance(inc, dict) or not isinstance(inc.get("url"), str):
                break
            try:
                urls[safe_part(key)] = inc["url"]
            except Refused:
                break
            sizes.append(inc.get("size"))
        else:
            if any(urlsplit(u).hostname not in HOSTS for u in urls.values()):
                continue
            known = all(isinstance(s, int) and s >= 0 for s in sizes)
            main = posixpath.basename(urlsplit(entry["url"]).path)
            if _ext(main) != ".gltf":
                continue
            return _PhModel(main, urls, sum(sizes) if known else None)
    return None


async def _ph_model(client: httpx.AsyncClient, slug: str) -> _PhModel | None:
    key = f"polyhaven:{slug}"
    hit = _DETAILS.get(key)
    if hit is not None:
        return hit or None
    picked = _ph_pick(await _json(client, f"{PH_API}/files/{quote(slug)}"))
    _DETAILS.put(key, picked or False)
    return picked


def _authors(raw: Any) -> str:
    if not isinstance(raw, dict):
        return ""
    return ", ".join(text(k, 60) for k in list(raw)[:4] if text(k, 60))


def _ph_result(slug: str, meta: dict[str, Any], model: _PhModel) -> dict[str, Any]:
    by = _authors(meta.get("authors"))
    return {
        "id": f"polyhaven:{slug}", "name": text(meta.get("name"), 80) or slug,
        "source": SOURCE_NAME["polyhaven"], "license": "CC0-1.0", "license_name": "CC0 1.0",
        "attribution": (f"by {by} · " if by else "") + "Poly Haven · CC0",
        "credit_required": False,
        "description": text(meta.get("description"), 300),
        "thumb": f"/api/airglass/library/thumb?id=polyhaven:{slug}",
        "url": f"/api/airglass/library/file?id=polyhaven:{slug}",
        "main": model.main, "size_bytes": model.size,
        "page": f"https://polyhaven.com/a/{slug}",
    }


async def search_polyhaven(client: httpx.AsyncClient, q: list[str]) -> list[dict[str, Any]]:
    listing = await _ph_listing(client)
    ranked: list[tuple[int, int, str]] = []
    for slug, meta in listing.items():
        if meta.get("type") not in (2, None):        # 2 = model in the listing
            continue
        tags = [w for t in (meta.get("tags") or []) + (meta.get("categories") or [])
                if isinstance(t, str) for w in words(t)]
        s = score(q, words(text(meta.get("name"), 80) or slug), tags)
        if s:
            dl = meta.get("download_count")
            ranked.append((s, dl if isinstance(dl, int) else 0, slug))
    ranked.sort(key=lambda r: (-r[0], -r[1], r[2]))
    top = [(s, slug) for s, _d, slug in ranked[:PER_SOURCE]]
    gate = asyncio.Semaphore(4)

    async def one(s: int, slug: str) -> dict[str, Any] | None | Exception:
        async with gate:
            try:
                model = await _ph_model(client, slug)
            except LibraryError as exc:
                return exc
        if model is None or (model.size is not None and model.size > MAX_MODEL_BYTES):
            return None
        return {**_ph_result(slug, listing[slug], model), "score": s}

    got = await asyncio.gather(*(one(s, slug) for s, slug in top))
    failed = [g for g in got if isinstance(g, Exception)]
    if top and len(failed) == len(top):
        raise Unreachable("api.polyhaven.com")
    return [g for g in got if isinstance(g, dict)]


# ------------------------------------------------------------------ Khronos

async def _kh_index(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    hit = _LISTINGS.get("khronos")
    if hit is not None:
        return hit
    raw = await _json(client, KH_BASE + "model-index.json")
    if not isinstance(raw, list):
        raise Unreachable("raw.githubusercontent.com")
    index = [m for m in raw if isinstance(m, dict) and isinstance(m.get("name"), str)
             and _SLUG.match(m["name"]) and isinstance(m.get("variants"), dict)]
    return _LISTINGS.put("khronos", index)


@dataclass
class _KhModel:
    variant: str
    main: str
    license: str                    # "CC0-1.0" or "CC-BY-4.0" (the strictest)
    attribution: str
    screenshot: str | None


def _kh_variant(entry: dict[str, Any]) -> tuple[str, str] | None:
    variants = entry.get("variants") or {}
    for v in KH_VARIANTS:
        name = variants.get(v)
        if isinstance(name, str) and _FILE.match(name) and _ext(name) in (".glb", ".gltf"):
            return v, name
    return None


def _kh_legal(meta: Any) -> tuple[str, str] | None:
    """(license, credit) when every legal entry is CC0-1.0 or CC-BY-4.0."""
    legal = meta.get("legal") if isinstance(meta, dict) else None
    if not isinstance(legal, list) or not legal:
        return None
    spdx = [x.get("spdx") or x.get("license") for x in legal if isinstance(x, dict)]
    if len(spdx) != len(legal) or not all(s in OPEN_LICENSES for s in spdx):
        return None
    lic = "CC-BY-4.0" if "CC-BY-4.0" in spdx else "CC0-1.0"
    credits = []
    for x in legal:
        who = text(x.get("artist") or x.get("owner"), 60)
        year = text(x.get("year"), 8)
        part = " ".join(p for p in (f"© {year}" if year else "", who) if p)
        credits.append(f"{part} ({OPEN_LICENSES[x.get('spdx') or x.get('license')]})"
                       if part else OPEN_LICENSES[x.get("spdx") or x.get("license")])
    return lic, "; ".join(dict.fromkeys(credits))


async def _kh_model(client: httpx.AsyncClient, entry: dict[str, Any]) -> _KhModel | None:
    slug = entry["name"]
    key = f"khronos:{slug}"
    hit = _DETAILS.get(key)
    if hit is not None:
        return hit or None
    picked = None
    variant = _kh_variant(entry)
    if variant:
        legal = _kh_legal(await _json(client, f"{KH_BASE}{quote(slug)}/metadata.json"))
        if legal:
            shot = entry.get("screenshot")
            try:
                shot = safe_part(shot) if isinstance(shot, str) else None
            except Refused:
                shot = None
            if shot and _ext(shot) not in THUMB_TYPES:
                shot = None
            picked = _KhModel(variant[0], variant[1], legal[0], legal[1], shot)
    _DETAILS.put(key, picked or False)
    return picked


def _kh_result(entry: dict[str, Any], model: _KhModel) -> dict[str, Any]:
    slug = entry["name"]
    return {
        "id": f"khronos:{slug}", "name": text(entry.get("label"), 80) or slug,
        "source": SOURCE_NAME["khronos"], "license": model.license,
        "license_name": OPEN_LICENSES[model.license],
        "attribution": f"{model.attribution} · Khronos glTF Sample Assets",
        "credit_required": model.license == "CC-BY-4.0",
        "description": "",
        "thumb": (f"/api/airglass/library/thumb?id=khronos:{slug}"
                  if model.screenshot else None),
        "url": f"/api/airglass/library/file?id=khronos:{slug}",
        "main": model.main, "size_bytes": None,
        "page": ("https://github.com/KhronosGroup/glTF-Sample-Assets/tree/main/Models/"
                 + slug),
    }


async def search_khronos(client: httpx.AsyncClient, q: list[str]) -> list[dict[str, Any]]:
    index = await _kh_index(client)
    ranked: list[tuple[int, int, dict[str, Any]]] = []
    for i, entry in enumerate(index):
        name = words(entry["name"])
        tags = [w for t in entry.get("tags") or [] if isinstance(t, str) for w in words(t)]
        # Test scenes (by name, or tagged "testing" without being a core or
        # showcase model: ClearCoatCarPaint is a sphere), or Draco/KTX only.
        testing = "testing" in tags and not {"core", "showcase"} & set(tags)
        if "test" in name or testing or _kh_variant(entry) is None:
            continue
        s = score(q, name + words(text(entry.get("label"), 80)), tags)
        if s:
            ranked.append((s, -i, entry))
    ranked.sort(key=lambda r: (-r[0], -r[1]))
    top = [(s, e) for s, _i, e in ranked[:PER_SOURCE]]
    gate = asyncio.Semaphore(4)

    async def one(s: int, entry: dict[str, Any]) -> dict[str, Any] | None | Exception:
        async with gate:
            try:
                model = await _kh_model(client, entry)
            except LibraryError as exc:
                return exc
        return {**_kh_result(entry, model), "score": s} if model else None

    got = await asyncio.gather(*(one(s, e) for s, e in top))
    failed = [g for g in got if isinstance(g, Exception)]
    if top and len(failed) == len(top):
        raise Unreachable("raw.githubusercontent.com")
    return [g for g in got if isinstance(g, dict)]


# ------------------------------------------------------------------- routes

async def search(q: str) -> dict[str, Any]:
    """Both sources at once. A source that does not answer is named in
    `sources`; when none answers, Unreachable names them all."""
    terms = query_words(q[:MAX_QUERY])
    async with _client() as client:
        got = await asyncio.gather(search_polyhaven(client, terms),
                                   search_khronos(client, terms),
                                   return_exceptions=True)
    sources: dict[str, str] = {}
    results: list[dict[str, Any]] = []
    down: list[str] = []
    for name, res in zip(("polyhaven", "khronos"), got, strict=True):
        if isinstance(res, LibraryError):
            sources[name] = str(res)
            down.append(getattr(res, "host", SOURCE_HOST[name]))
        elif isinstance(res, BaseException):
            raise res
        else:
            sources[name] = "ok"
            results.extend(res)
    if len(down) == len(got):
        raise Unreachable(" and ".join(down))
    # Best match first across both sources (stable: Poly Haven, CC0, first on a tie).
    results.sort(key=lambda r: -int(r.pop("score", 0)))
    return {"query": " ".join(terms), "results": results, "sources": sources,
            "read_at": datetime.now(UTC).isoformat(timespec="seconds")}


@dataclass
class Served:
    body: bytes
    media_type: str


async def model_file(raw_id: str, part: str | None) -> Served:
    """One file of one library model: the main file when `part` is empty,
    otherwise a file the main one names, resolved from the source's listing."""
    source, slug = parse_id(raw_id)
    async with _client() as client:
        if source == "polyhaven":
            if slug not in await _ph_listing(client):
                raise NotFound("no such library model")
            model = await _ph_model(client, slug)
            if model is None:
                raise NotFound("that model has no glTF")
            key = safe_part(part) if part else ""
            url = model.urls.get(key)
            if url is None:
                raise Refused("not a file of this model")
            name = model.main if not key else key
        else:
            entry = next((m for m in await _kh_index(client) if m["name"] == slug), None)
            kh = await _kh_model(client, entry) if entry else None
            if kh is None:
                raise NotFound("no such library model")
            name = safe_part(part) if part else kh.main
            url = f"{KH_BASE}{quote(slug)}/{quote(kh.variant)}/{quote(name)}"
        media = TYPES.get(_ext(name))
        if media is None:
            raise Refused("that kind of file is not served")
        return Served(await fetch(client, url, MAX_FILE_BYTES), media)


async def thumb(raw_id: str) -> Served:
    source, slug = parse_id(raw_id)
    async with _client() as client:
        if source == "polyhaven":
            if slug not in await _ph_listing(client):
                raise NotFound("no such library model")
            url = PH_THUMB.format(slug=quote(slug))
        else:
            entry = next((m for m in await _kh_index(client) if m["name"] == slug), None)
            kh = await _kh_model(client, entry) if entry else None
            if kh is None or not kh.screenshot:
                raise NotFound("no picture for that model")
            url = f"{KH_BASE}{quote(slug)}/{quote(kh.screenshot)}"
        data = await fetch(client, url, MAX_THUMB_BYTES)
    media = image_type(data)
    if media is None:
        raise Garbled("the library sent something that is not a picture")
    return Served(data, media)
