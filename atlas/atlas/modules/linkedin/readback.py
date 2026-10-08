"""Read-back for `linkedin.publish_post` (RA-D1), and the reconciler for its
unfinished starts (three in prod with no finish, FINDINGS F §7.2). Also
`linkedin.reshare_post` (Miro's recent activity) and
`linkedin.update_page_identity` (the page's public name; the logo cannot be
compared, so a logo change reads unknown).
`linkedin.approve_draft` reads only the post-bank entry in the state file:
approved (or since posted) with the payload hash the card showed. No
browser, no file lock, no save.

The authority is the page's own published list, the same one `_publish`
reads before it claims success: the post's first 40 characters on that list
mean it is there. It is read in a real browser under the module's one-at-a-
time `lock()`, and nothing is written back -- no `keep_session`, no `save`,
no `log_run`, no snapshot. The post-bank entry (the state file) is read too:
it names the entry, its status and the activity URL the run recorded.

Reads only. See docs/verification.md.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any

from ...kernel.contracts import ActionResult
from ...kernel.verify import (
    FailureClass,
    Verification,
    combine,
    contradicted,
    reconciler,
    unknown,
    verified,
    verifier,
)
from . import PAGES


class Unreadable(Exception):
    """The published list could not be read; carries the failure class."""

    def __init__(self, message: str, failure_class: FailureClass) -> None:
        super().__init__(message)
        self.failure_class = failure_class


async def published_text(page: str) -> str:
    """The page's published list, as text. Read only: the browser context is
    closed without saving the session back."""
    spec = PAGES[page]
    return await _read(f"https://www.linkedin.com/company/{spec['id']}"
                       f"/admin/page-posts/published/")


async def activity_text() -> str:
    """Miro's recent activity, as text: where a reshare shows. Read only."""
    from .publish import PROFILE_ACTIVITY
    return await _read(PROFILE_ACTIVITY)


async def page_name(page: str) -> str:
    """The page's public name: its heading on the public company page, or
    the tab title without the " | LinkedIn" suffix. Read only."""
    spec = PAGES[page]
    return await _read(f"https://www.linkedin.com/company/{spec['id']}/",
                       script=_NAME_JS)


# The public page's first heading; the tab title as the fallback.
_NAME_JS = """() => {
  const h = document.querySelector('main h1, h1');
  const t = (h && h.innerText || '').trim();
  if (t) return t;
  return (document.title || '').replace(/^\\(\\d+\\)\\s*/, '')
                               .replace(/\\s*\\|\\s*LinkedIn\\s*$/, '').trim();
}"""


async def _read(url: str, *, script: str | None = None) -> str:
    """Open `url` in the module's browser under its one-at-a-time `lock()`
    and return the page text (or `script`'s answer). Never saves the
    session, never clicks, never types."""
    from .browser import HardStop, NoSession, SignedOut, browser, goto, lock
    async with lock():
        try:
            pw, b, ctx = await browser()
        except NoSession as exc:
            raise Unreadable(str(exc), FailureClass.AUTH) from None
        except ImportError:
            raise Unreadable("no browser available to read LinkedIn",
                             FailureClass.DISABLED) from None
        try:
            tab = await ctx.new_page()
            try:
                text = await goto(tab, url)
                if script is None:
                    return text
                return str(await tab.evaluate(script) or "")
            except SignedOut as exc:
                raise Unreadable(str(exc), FailureClass.AUTH) from None
            except HardStop as exc:
                raise Unreadable(str(exc), FailureClass.SECURITY) from None
        finally:
            await ctx.close()
            await b.close()
            await pw.stop()


def _entry(page: str, entry_id: str) -> dict[str, Any] | None:
    from .store import bank, load
    entries, _, _ = bank(load(), page)
    return next((e for e in entries if str(e.get("id")) == str(entry_id)), None)


def _needle(text: str) -> str:
    return str(text or "").strip()[:40].lower()


async def _on_page(page: str, entry: dict[str, Any]) -> Verification:
    evidence = {"page": page, "entry": entry.get("id"), "status": entry.get("status"),
                "activity_url": entry.get("activity_url") or None,
                "published_at": entry.get("published_at")
                or entry.get("published_attempt_at")}
    needle = _needle(entry.get("text") or "")
    if not needle:
        return unknown("the entry has no text to look for", **evidence)
    try:
        listed = await published_text(page)
    except Unreadable as exc:
        return unknown(str(exc), exc.failure_class, **evidence)
    if needle in (listed or "").lower():
        return verified(on_published_list=True, **evidence)
    return contradicted(f"{entry.get('id')} is not on the {PAGES[page]['name']} "
                        "page's published list", **evidence)


@verifier("linkedin.publish_post")
async def verify_publish(params: dict[str, Any], result: ActionResult) -> Verification:
    page = str(params.get("page") or (result.evidence or {}).get("page") or "")
    entry_id = str((result.evidence or {}).get("entry") or params.get("entry_id") or "")
    if page not in PAGES or not entry_id:
        return unknown("the run recorded no page and entry")
    try:
        entry = _entry(page, entry_id)
    except Exception as exc:                            # noqa: BLE001
        from .._http import explain
        return unknown(f"the post bank could not be read: {explain(exc)}")
    if entry is None:
        return contradicted(f"{entry_id} is not in the {page} post bank", entry=entry_id)
    return await _on_page(page, entry)


def _stamped_after(entry: dict[str, Any], started: datetime) -> bool:
    for key in ("published_attempt_at", "published_at"):
        raw = entry.get(key)
        try:
            when = datetime.fromisoformat(str(raw))
        except (TypeError, ValueError):
            continue
        if when.tzinfo is not None and when >= started.replace(second=0, microsecond=0):
            return True
    return False


@reconciler("linkedin.publish_post")
async def reconcile_publish(params: dict[str, Any], started: datetime) -> Verification:
    """`execute.start` with no finish. The crash window is the Post click
    (`publish.py`) before the entry is marked, so an entry still `approved`
    may be live: only the page's list says. Nothing is posted again."""
    page = str(params.get("page") or "")
    if page not in PAGES:
        return unknown("the start row names no page", FailureClass.VALIDATION)
    try:
        from .store import bank, load
        entries, _, _ = bank(load(), page)
    except Exception as exc:                            # noqa: BLE001
        from .._http import explain
        return unknown(f"the post bank could not be read: {explain(exc)}")
    entry_id = str(params.get("entry_id") or "")
    if entry_id:
        entry = next((e for e in entries if str(e.get("id")) == entry_id), None)
    else:
        touched = [e for e in entries if _stamped_after(e, started)]
        entry = touched[0] if len(touched) == 1 else None
    if entry is None:
        return unknown("the run cannot be pinned to one post-bank entry; "
                       "check the page by hand", page=page)
    return await _on_page(page, entry)


# --------------------------------------------------------------- reshare

@verifier("linkedin.reshare_post")
async def verify_reshare(params: dict[str, Any], result: ActionResult) -> Verification:
    """The bank must record the reshare as verified, and the post's first
    40 characters must be on Miro's recent activity. One read, not the
    run's three looks: the run only returns ok after it saw it there."""
    page = str(params.get("page") or (result.evidence or {}).get("page") or "")
    entry_id = str(params.get("entry_id") or (result.evidence or {}).get("entry") or "")
    if page not in PAGES or not entry_id:
        return unknown("the run names no page and entry", FailureClass.VALIDATION)
    try:
        entry = _entry(page, entry_id)
    except Exception as exc:                            # noqa: BLE001
        from .._http import explain
        return unknown(f"the post bank could not be read: {explain(exc)}")
    if entry is None:
        return contradicted(f"{entry_id} is not in the {page} post bank", entry=entry_id)
    evidence = {"page": page, "entry": entry_id, "reshared": entry.get("reshared"),
                "reshared_at": entry.get("reshared_at"),
                "activity_url": entry.get("activity_url") or None}
    if entry.get("reshared") is not True:
        return contradicted(f"the bank does not record {entry_id} as reshared "
                            f"(reshared={entry.get('reshared')!r})", **evidence)
    needle = _needle(entry.get("text") or "")
    if not needle:
        return unknown("the entry has no text to look for", **evidence)
    try:
        listed = await activity_text()
    except Unreadable as exc:
        return unknown(str(exc), exc.failure_class, **evidence)
    if needle in (listed or "").lower():
        return verified(on_miros_activity=True, **evidence)
    return contradicted(f"{entry_id} is not on Miro's recent activity", **evidence)


# --------------------------------------------------------------- identity

async def _name_leg(page: str, name: str) -> Verification:
    try:
        now = (await page_name(page)).strip()
    except Unreadable as exc:
        return unknown(str(exc), exc.failure_class)
    if not now:
        return unknown("the page showed no name to read")
    if now == name:
        return verified(name=now)
    return contradicted(f"the page is named {now!r}, not {name!r}", name=now,
                        approved=name)


@verifier("linkedin.update_page_identity")
async def verify_identity(params: dict[str, Any], result: ActionResult) -> Verification:
    """The name is read back off the public page. The logo is not
    comparable (LinkedIn re-encodes and resizes it), so a logo leg is
    unknown and a change that includes one never reads verified."""
    page = str(params.get("page") or (result.evidence or {}).get("page") or "")
    name = str(params.get("name") or "").strip()
    logo = str(params.get("logo") or "").strip()
    if page not in PAGES:
        return unknown("the run names no page", FailureClass.VALIDATION)
    legs: dict[str, Verification] = {}
    if name:
        legs["name"] = await _name_leg(page, name)
    if logo:
        legs["logo"] = unknown("LinkedIn re-encodes a logo, so it cannot be "
                               "compared with the file; check the page by eye",
                               logo=logo)
    if not legs:
        return unknown("the run changed neither the name nor the logo", page=page)
    got = combine(legs)
    return replace(got, evidence={"page": page, **got.evidence})


# --------------------------------------------------------------- approve_draft

APPROVED_OR_LATER = frozenset({"approved", "posted_unverified", "published"})


def _media_unreadable(entry: dict[str, Any]) -> bool:
    """`payload_sha256` hashes an image/video entry's media file too, and
    falls back to a fixed marker when it cannot open it: a hash compared
    then would be a false contradiction."""
    import os
    kind = entry.get("kind") or "text"
    media = entry.get("media")
    return kind in ("image", "video") and bool(media) and not os.access(
        str(media), os.R_OK)


async def _approved(page: str, entry_id: str, text_sha256: str) -> Verification:
    """The post-bank entry, read from the state file without the file lock
    (which writes a lock file) and without saving anything back."""
    from .publish import payload_sha256
    try:
        entry = _entry(page, entry_id)
    except Exception as exc:                            # noqa: BLE001
        from .._http import explain
        return unknown(f"the post bank could not be read: {explain(exc)}")
    if entry is None:
        return contradicted(f"{entry_id} is not in the {page} post bank",
                            page=page, entry=entry_id)
    status = str(entry.get("status") or "")
    evidence = {"page": page, "entry": entry_id, "status": status,
                "approved": entry.get("approved"), "kind": entry.get("kind") or "text"}
    if status not in APPROVED_OR_LATER:
        return contradicted(f"{entry_id} is {status or 'unmarked'}, not approved",
                            **evidence)
    if not text_sha256:
        return unknown("the approval named no payload hash to compare", **evidence)
    if _media_unreadable(entry):
        return unknown(f"{entry_id}'s media cannot be read, so its payload hash "
                       "cannot be compared", **evidence)
    current = payload_sha256(entry)
    evidence["payload_sha256"] = current[:12]
    if current != text_sha256:
        return contradicted(f"{entry_id}'s payload is not the one approved",
                            FailureClass.SECURITY, **evidence)
    return verified(**evidence)


@verifier("linkedin.approve_draft")
async def verify_approve(params: dict[str, Any], result: ActionResult) -> Verification:
    tok_page, _, tok_entry = str(result.rollback_token or "").partition(":")
    page = str(params.get("page") or tok_page or "")
    entry_id = str(params.get("entry_id") or tok_entry or "")
    if page not in PAGES or not entry_id:
        return unknown("the run names no page and entry", FailureClass.VALIDATION)
    return await _approved(page, entry_id, str(params.get("text_sha256") or ""))


@reconciler("linkedin.approve_draft")
async def reconcile_approve(params: dict[str, Any], started: datetime) -> Verification:
    """Approved with the hash he saw, or not. Nothing is approved again."""
    page = str(params.get("page") or "")
    entry_id = str(params.get("entry_id") or "")
    if page not in PAGES or not entry_id:
        return unknown("the start row names no page and entry", FailureClass.VALIDATION)
    return await _approved(page, entry_id, str(params.get("text_sha256") or ""))
