"""CSV leads the owner drops in `data/sales/inbox/`: parsed in code, never by a
model. Every cell is untrusted text stored as data; nothing here reaches a
prompt.

A row needs `name` and `category`; `website`, `email`, `phone`, `address`,
`city`, `country` are optional. The key is synthetic, like a Places id:
`csv:` + sha256(domain, else "name|city")[:16], so a second import of the
same row is a no-op (`agency_leads.place_id` is unique)."""
from __future__ import annotations

import csv
import io
from pathlib import Path, PurePosixPath
from typing import Any

from . import rules

INBOX = PurePosixPath("data/sales/inbox")
MAX_ROWS = 2000
FIELDS = ("name", "category", "website", "email", "phone", "address", "city",
          "country")


def path_refusal(path: str) -> str | None:
    """Only a .csv directly under data/sales/inbox/, relative, no `..`."""
    raw = (path or "").strip()
    p = PurePosixPath(raw)
    if not raw or p.is_absolute() or ".." in p.parts or "\\" in raw:
        return "the path must be relative to the repo, without '..'"
    if p.parent != INBOX:
        return f"only files directly in {INBOX}/ are imported"
    if p.suffix.lower() != ".csv":
        return "only .csv files are imported"
    return None


def resolve(path: str) -> Path:
    """The file, after path_refusal passed, refusing a symlink out."""
    full = Path(path).resolve()
    if full.parent != Path(INBOX).resolve():
        raise ValueError(f"{path} resolves outside {INBOX}/")
    return full


def _cell(row: dict[str, Any], name: str, limit: int = 300) -> str:
    return str(row.get(name) or "").strip()[:limit]


def parse(text: str, *, suppressed: set[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """(rows to insert, why each other row was skipped). Pure."""
    rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    seen: set[str] = set()
    reader = csv.DictReader(io.StringIO(text))
    reader.fieldnames = [f.strip().lower() for f in (reader.fieldnames or [])]
    for n, raw in enumerate(reader, start=2):
        if n - 1 > MAX_ROWS:
            skipped.append(f"rows past {MAX_ROWS}: not read")
            break
        name, category = _cell(raw, "name", 200), _cell(raw, "category", 80).lower()
        website, email = _cell(raw, "website"), _cell(raw, "email", 200).lower()
        city, country = _cell(raw, "city", 80), _cell(raw, "country", 60).lower()
        if not name:
            skipped.append(f"line {n}: name is required")
            continue
        if not category:
            skipped.append(f"line {n} ({name}): category is required")
            continue
        if rules.excluded(name, category):
            skipped.append(f"line {n} ({name}): a place of worship")
            continue
        if country not in rules.US_COUNTRY:
            skipped.append(f"line {n} ({name}): not in the US ({country})")
            continue
        if email and email in suppressed:
            skipped.append(f"line {n} ({name}): suppressed address")
            continue
        dom = rules.domain(website) if website else ""
        place_id = rules.key("csv", dom or f"{name.lower()}|{city.lower()}")
        if place_id in seen:
            skipped.append(f"line {n} ({name}): duplicate")
            continue
        seen.add(place_id)
        rows.append({
            "place_id": place_id, "name": name, "category": category,
            "website": website or None, "phone": _cell(raw, "phone", 40) or None,
            "address": _cell(raw, "address") or (city or None),
            "email": email or None, "source": "csv",
            "segment": "remote_us",
        })
    return rows, skipped
