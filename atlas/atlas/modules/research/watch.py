"""Research watches: the daily scan (six agents phase 11).

A watch is one public URL. The scan reads it through `_scrape` (robots.txt,
deny list, daily cap, cache), strips dates and times in code and compares a
hash of what is left, so a date-only edit is not a change. A change keeps
its excerpt, source URL and read time; prices on the page are parsed in code
with their currency. Three failed reads in a row raise exactly one
remediation task. Nothing here writes outside our own tables."""
from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime

from ...kernel.contracts import ActionResult, FailureClass
from .. import _scrape
from . import profiles, store

MAX_WATCHES = 40
FAIL_LIMIT = 3
_MONTHS = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|"
           r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_NOISE = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[t ]\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?"
               r"(?:z|[+-]\d{2}:?\d{2})?)?", re.IGNORECASE),
    re.compile(rf"\b{_MONTHS}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}", re.IGNORECASE),
    re.compile(rf"\b\d{{1,2}}\s+{_MONTHS}\.?,?\s+\d{{4}}", re.IGNORECASE),
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"),
    re.compile(r"\b(?:at\s+)?\d{1,2}:\d{2}(?::\d{2})?\s*(?:[ap]\.?m\.?)?", re.IGNORECASE),
]
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _refused(why: str) -> ActionResult:
    return ActionResult(False, why, failure_class=FailureClass.VALIDATION)


def normalize(text: str) -> str:
    out = text or ""
    for pattern in _NOISE:
        out = pattern.sub(" ", out)
    return " ".join(out.split()).lower()


def text_sha(text: str) -> str:
    return hashlib.sha256(normalize(text).encode()).hexdigest()


def excerpt(old: str, new: str, limit: int = 600) -> str:
    """The sentences of the new text that the old text does not have."""
    seen = {normalize(s) for s in _SENTENCE.split(old or "")}
    fresh = [s.strip() for s in _SENTENCE.split(new or "")
             if s.strip() and normalize(s) not in seen]
    return (" … ".join(fresh) or (new or "")[:limit])[:limit]


def stamp(at: datetime | None = None) -> str:
    return f"(read {(at or datetime.now(UTC)).astimezone(UTC):%Y-%m-%d %H:%M} UTC)"


# --------------------------------------------------------------- add / undo

def _url_refusal(url: str) -> str | None:
    if not str(url or "").startswith(("http://", "https://")):
        return "a watch needs an http(s) URL"
    return _scrape.denied(url)


async def add_watch_dry(url: str, label: str = "",
                        competitor_id: int | None = None) -> ActionResult:
    if (why := _url_refusal(url)) is not None:
        return _refused(why)
    return ActionResult(True, f"would watch {url}")


async def add_watch(url: str, label: str = "",
                    competitor_id: int | None = None) -> ActionResult:
    if (why := _url_refusal(url)) is not None:
        return _refused(why)
    wid = await store.add_watch(url, (label or url)[:120],
                                int(competitor_id) if competitor_id else None)
    if wid is None:
        return _refused(f"{url} is already watched")
    return ActionResult(True, f"watching {url}", evidence={"watch_id": wid},
                        rollback_token=str(wid))


async def archive_watch(token: str) -> ActionResult:
    if not str(token).isdigit():
        return _refused("not a watch id")
    await store.archive_watch(int(token))
    return ActionResult(True, f"watch {token} archived")


# -------------------------------------------------------------------- scan

async def daily_scan_dry() -> ActionResult:
    rows = await store.watches(MAX_WATCHES)
    return ActionResult(True, f"would read {len(rows)} watched page(s)")


async def daily_scan() -> ActionResult:
    from ...kernel import qa_gate
    rows = await store.watches(MAX_WATCHES)
    changes: list[int] = []
    prices: list[int] = []
    failed: list[str] = []
    for w in rows:
        page = await _scrape.fetch(str(w["url"]), agent="research",
                                   client=str(w.get("label") or ""))
        if not page.ok:
            why = page.blocked or f"HTTP {page.status}"
            failed.append(f"{w['url']}: {why}")
            streak = await store.watch_failed(int(w["id"]), why)
            if streak == FAIL_LIMIT:
                await qa_gate.remediate("research.daily_scan",
                                        f"research_watches:{w['id']}", "",
                                        [(f"{w['url']} failed {streak} reads "
                                          f"in a row: {why}")])
            continue
        source = page.final_url or page.url
        sha = text_sha(page.text)
        first, moved = w.get("last_sha") is None, w.get("last_sha") not in (None, sha)
        if moved:
            changes.append(await store.insert_change(
                int(w["id"]), w.get("last_sha"), sha,
                excerpt(str(w.get("last_text") or ""), page.text), source,
                page.fetched_at))
        if first or moved:
            for it in profiles.parse_prices(page.text, source, page.fetched_at):
                v = it["value"]
                prices.append(await store.insert_price(
                    w.get("competitor_id"), int(w["id"]), v["amount"], v["currency"],
                    v["period"], v["label"], source, page.fetched_at))
        await store.watch_ok(int(w["id"]), sha, page.text[:20000], page.fetched_at)
    detail = (f"read {len(rows) - len(failed)} of {len(rows)} watched page(s): "
              f"{len(changes)} change(s), {len(prices)} price(s)"
              + (f"; failed: {'; '.join(failed[:5])}" if failed else "")
              + f" {stamp()}")
    return ActionResult(not rows or len(failed) < len(rows), detail,
                        evidence={"changes": changes, "prices": prices},
                        rollback_token=f"c{','.join(map(str, changes))};"
                                       f"p{','.join(map(str, prices))}")


async def withdraw_scan(token: str) -> ActionResult:
    c, _, p = str(token).partition(";")
    if not c.startswith("c") or not p.startswith("p"):
        return _refused("not a scan token")
    cids = [int(i) for i in c[1:].split(",") if i.isdigit()]
    pids = [int(i) for i in p[1:].split(",") if i.isdigit()]
    if cids:
        await store.withdraw("research_changes", cids)
    if pids:
        await store.withdraw("research_prices", pids)
    return ActionResult(True, f"withdrew {len(cids)} change(s), {len(pids)} price(s)")
