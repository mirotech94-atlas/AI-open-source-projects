"""The course products as data: each product's store link, lesson hooks, blog
sections and quotes, and the UTM scheme that lets Shopify attribute a sale to
the post that sent it.

The products are the closed list in `catalog.py`; each one's words live in
`products/<key>.json`, copied verbatim from Miro's vault (the server cannot
read the vault). The price is deliberately NOT here: it is read live from the
store every time (`price.py`), never written down.

The UTM scheme (Miro, 2026-09-29): `utm_source=<platform>&utm_medium=organic
&utm_campaign=<experiment id>`. `utm_medium` is always `organic` -- nothing in
this module can buy reach -- and the campaign is the `marketing_experiments`
id the post was filed under, so a visit or a sale reads back to exactly one
experiment.
"""
from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import catalog
from .catalog import OPERATOR

HERE = Path(__file__).parent
PRODUCTS = HERE / "products"

BUSINESS = "course"
MEDIUM = "organic"
_SOURCE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def _product(key: str) -> catalog.Product:
    found = catalog.get(key)
    if found is None:
        raise KeyError(f"no product {key!r} in the catalog")
    return found


@cache
def data(key: str = OPERATOR) -> dict[str, Any]:
    with open(PRODUCTS / _product(key).data, encoding="utf-8") as fh:
        return json.load(fh)


def product(key: str = OPERATOR) -> dict[str, str]:
    p = _product(key)
    return {"key": p.key, "title": p.title, "short": p.short,
            "store": catalog.STORE, "url": p.url, "handle": p.handle}


def store_host() -> str:
    return (urlsplit(catalog.STORE).hostname or "").lower()


def lessons(key: str = OPERATOR) -> list[dict[str, str]]:
    return [dict(x) for x in data(key)["lessons"]]


def lesson(lesson_id: str, key: str = OPERATOR) -> dict[str, str] | None:
    return next((x for x in lessons(key) if x["id"] == str(lesson_id)), None)


def modules(key: str = OPERATOR) -> list[dict[str, str]]:
    return [dict(x) for x in data(key).get("modules", [])]


def hashtags(key: str = OPERATOR) -> str:
    return str(data(key).get("hashtags") or "")


def blog(key: str = OPERATOR) -> dict[str, Any]:
    return dict(data(key)["blog"])


def claims(key: str = OPERATOR) -> list[dict[str, str]]:
    """What the product is, in its sales page's own words (for Nora's choice
    of product and hypothesis; no post or article carries them)."""
    return [dict(x) for x in data(key).get("claims", [])]


def quotes(key: str = OPERATOR) -> list[dict[str, str]]:
    return [dict(x) for x in data(key).get("quotes", [])]


def verbatim(key: str = OPERATOR) -> list[str]:
    """Every excerpt in a product file copied word for word from its LESSON
    files: hooks and lines, blog sections, sourced quotes. A number inside
    one of these is quoted, not made up (`honesty.py`). The sales-page
    claims are not lesson text and are not in this list."""
    d = data(key)
    out = [x[k] for x in d["lessons"] for k in ("hook", "line")]
    out += [s["text"] for s in d["blog"]["sections"]]
    out += [q["text"] for q in d.get("quotes", [])]
    return out


def utm_url(url: str, platform: str, experiment_id: int) -> str:
    """`url` tagged utm_source=<platform>, utm_medium=organic,
    utm_campaign=<experiment id>. Refuses (ValueError) a URL off the store,
    a platform that is not a plain lowercase word, or a non-positive id:
    a tag that could be anything reads back as nothing."""
    parts = urlsplit(url)
    if parts.scheme != "https" or (parts.hostname or "").lower() != store_host():
        raise ValueError(f"only the course store is tagged, not {url!r}")
    if not _SOURCE.match(platform or ""):
        raise ValueError(f"utm_source must be a plain lowercase word, not "
                         f"{platform!r}")
    if isinstance(experiment_id, bool) or int(experiment_id) <= 0:
        raise ValueError(f"utm_campaign needs a real experiment id, not "
                         f"{experiment_id!r}")
    query = [(k, v) for k, v in parse_qsl(parts.query)
             if not k.lower().startswith("utm_")]
    query += [("utm_source", platform), ("utm_medium", MEDIUM),
              ("utm_campaign", str(int(experiment_id)))]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def utm_of(url: str) -> dict[str, str]:
    """The utm_* parameters of a URL, for tests and read-backs."""
    return {k: v for k, v in parse_qsl(urlsplit(url).query)
            if k.lower().startswith("utm_")}
