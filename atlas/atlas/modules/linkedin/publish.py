"""The one LinkedIn write: publish an approved page post, byte for byte.

Tier 2. The card ATLAS raises each day IS the approval for that post; the
post-bank status `approved` is what makes an entry eligible to be offered at
all. A draft is never published, approved text is never edited, and the
only link allowed is the page's own.

There is no rollback. A LinkedIn post can only be undone by deleting it, and
deletion is permanently denied, so this cannot be Tier 1 whatever the
standing instruction says about "without re-asking". One tap a day is the
version that does not break the rule.

The reshare to Miro's profile is the distribution (a controlled pair: 1
impression without it, 10 with it), so it is part of publishing, done
through the durable deep link, and verified rather than assumed.
"""
from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import logging
import re
import unicodedata
from typing import Any
from urllib.parse import quote

from ...kernel.contracts import ActionResult
from . import PAGES
from .browser import (
    HardStop,
    NoSession,
    SignedOut,
    browser,
    goto,
    keep_session,
    lock,
    pause,
    snapshot,
)
from .store import bank, file_lock, load, log_run, page_obj, save, stamp, today

log = logging.getLogger("atlas.linkedin.publish")

BANNED = ("example.com", "pulsedemo", "my.guarder.io", "admin.guarder.io")


def gate(state: dict[str, Any], key: str) -> tuple[dict | None, str]:
    """(the entry that may go out now, or None, and why)."""
    if key not in PAGES:
        return None, f"no such page {key!r}"
    entries, posting, _ = bank(state, key)
    if not posting.get("enabled", True):
        return None, "posting is disabled for this page"
    days = posting.get("days")
    weekday = today_name()
    if days and weekday not in [str(d)[:3].lower() for d in days] \
            and weekday not in [str(d).lower() for d in days]:
        return None, f"{weekday} is not a posting day"
    if posting.get("last_post") == today():
        return None, "already posted today"
    approved = [e for e in entries if e.get("status") == "approved"]
    if not approved:
        drafts = sum(1 for e in entries if e.get("status") == "draft")
        return None, (f"no approved entry ({drafts} draft(s) waiting on approval)")
    entry = approved[0]
    text = str(entry.get("text") or "")
    link = PAGES[key]["link"]
    if any(b in text for b in BANNED):
        return None, f"{entry.get('id')} carries a banned link"
    if link not in text and "http" in text:
        return None, f"{entry.get('id')} links somewhere other than {link}"
    return entry, "ok"


def today_name() -> str:
    from .store import now_local
    return now_local().strftime("%a").lower()


def _entry(state: dict[str, Any], key: str, entry_id: str) -> dict | None:
    entries, _, _ = bank(state, key)
    for e in entries:
        if str(e.get("id")) == str(entry_id):
            return e
    return None


async def publish_dry(page: str = "", entry_id: str = "") -> ActionResult:
    try:
        state = load()
    except Exception as exc:                            # noqa: BLE001
        return ActionResult(False, f"cannot read the LinkedIn state: {exc}")
    entry, why = gate(state, page)
    if entry is None:
        return ActionResult(False, f"would not publish to {page}: {why}")
    if entry_id and str(entry.get("id")) != str(entry_id):
        return ActionResult(False, f"{entry_id} is not the next approved entry for "
                                   f"{page}; that is {entry.get('id')}")
    text = str(entry.get("text") or "")
    return ActionResult(
        True,
        f"would publish {entry.get('id')} to the {PAGES[page]['name']} page "
        f"({len(text)} chars, text-only), then reshare it to Miro's profile:\n"
        + text[:400],
        evidence={"page": page, "entry": entry.get("id"), "chars": len(text)})


# NBSP, narrow no-break space, figure space -- built with chr() rather than
# literal characters sitting in the source, so an editor or a tool that
# normalises whitespace on save cannot silently turn one back into a plain
# space. Quill's innerText readback has rendered a plain typed space as any
# of these; a comparison that does not fold them together aborts a post
# that is, to the eye and to LinkedIn, identical to the one Miro approved.
_SPACE_LIKE = chr(0x00A0) + chr(0x202F) + chr(0x2007)


def _normalise(s: str) -> str:
    """The form the approved text and the editor's readback are BOTH put
    through before they are compared. NFC-normalised, space-like Unicode
    folded to ' ', each line's trailing whitespace dropped, blank lines
    dropped, and the whole thing stripped.

    This must never get looser than "the same rendered text": the invariant
    is that a real difference still refuses, so it only folds forms that are
    genuinely indistinguishable once posted, never content.
    """
    text = unicodedata.normalize("NFC", (s or "").replace("\r", ""))
    for ch in _SPACE_LIKE:
        text = text.replace(ch, " ")
    # Blank lines are dropped entirely: LinkedIn's editor stores every
    # Enter as its own paragraph, so one newline in the approved text reads
    # back as two (G11, 2026-10-06: "+\n+\n+" -- three expired cards and a
    # failed one over blank lines). Rendered, a paragraph break is a
    # paragraph break; the words on each line are what is compared.
    lines = [line.rstrip() for line in text.split("\n") if line.strip()]
    return "\n".join(lines).strip()


def _escape(line: str) -> str:
    """Non-printables made visible, so a diff line does not show two
    identical-looking strings when the actual difference is an invisible
    character that normalisation did not fold."""
    return line.encode("unicode_escape").decode("ascii")


def _readback_diff(approved: str, got: str) -> str | None:
    """None when the two normalise to the same text. Otherwise a short
    unified diff -- the first 3 differing lines, never the whole post."""
    norm_a = _normalise(approved)
    norm_b = _normalise(got)
    if norm_a == norm_b:
        return None
    diff = list(difflib.unified_diff(
        [_escape(ln) for ln in norm_a.splitlines()],
        [_escape(ln) for ln in norm_b.splitlines()],
        fromfile="approved", tofile="readback", lineterm=""))
    head = [ln for ln in diff if ln[:3] in ("---", "+++")]
    changed = [ln for ln in diff if ln[:1] in "+-" and ln[:3] not in ("---", "+++")]
    return "\n".join(head + changed[:3])


def text_sha256(text: str) -> str:
    """The hash a card shows Miro alongside a draft's text, and the one
    `approve_draft` is asked to confirm against before it approves anything.
    A mismatch means the sweep rewrote the draft after the card went out."""
    return hashlib.sha256((text or "").encode()).hexdigest()


def payload_sha256(entry: dict[str, Any]) -> str:
    """The hash a card shows Miro, and the one `approve_draft` confirms
    before it approves anything -- covering the WHOLE payload a kind
    carries, not only `text` (Task 64). Before this, only the text was
    hashed: the sweep could have rewritten a poll's options, or a draft's
    generated image could have been swapped, and an approval that only
    ever looked at the caption would wave either through. A `text`-kind
    entry (or one whose kind is missing entirely, from before Task 64)
    still hashes to plain `text_sha256(text)`, so an already-pending card
    from before this change keeps approving."""
    kind = entry.get("kind") or "text"
    if kind == "poll":
        blob = json.dumps({"kind": "poll", "poll": entry.get("poll") or {}},
                          sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()
    text = str(entry.get("text") or "")
    media = entry.get("media")
    if kind in ("image", "video") and media:
        digest = hashlib.sha256(text.encode())
        try:
            with open(media, "rb") as fh:
                digest.update(fh.read())
        except OSError:
            digest.update(b"<media unreadable>")
        return digest.hexdigest()
    return text_sha256(text)


async def publish(page: str = "", entry_id: str = "") -> ActionResult:
    async with lock(), file_lock():
        return await _publish(page, entry_id)


# Task 64: an approved post carries one of three kinds, and each drives a
# different corner of LinkedIn's own composer. Every selector below is
# ASSUMED -- read off the existing text-only flow's own style and LinkedIn's
# publicly documented composer, never exercised against a live session (the
# brief: "where a step cannot be verified without a live session, make it
# fail loudly with the step named"). `_StepFailed` is exactly that: any
# unexpected exception inside a named composer step is re-raised with the
# step's name attached, caught once in `_publish`, snapshotted, and returned
# as a plain refusal -- never reported as a success it did not see.
# The composer's own Post button, and only it. `button:has-text('Post')` is
# a substring match, and in the composer (2026-09-28, read off the live
# page) its FIRST match is the audience button "FlyCheap · Post to Anyone";
# "Post" opened post settings and nothing was published (B5, G8). The real
# button carries share-actions__primary-action; the exact-text fallback is
# scoped to the dialog so a "Repost" or "Start a post" can never match.
POST_BUTTON_SELECTOR = ("[role='dialog'] button.share-actions__primary-action, "
                        "[role='dialog'] button:text-is('Post')")
# Tried in order, first visible wins. The page composer is a dialog; the
# member reshare composer (feed deep link, LinkedIn's newer layout, read off
# the live page 2026-09-28) is not, and its Post button has obfuscated
# classes -- only an exact "Post" finds it. Exact text never matches "Post to
# Anyone", "Start a post" or "Repost".
POST_BUTTON_SELECTORS = (
    "[role='dialog'] button.share-actions__primary-action",
    "[role='dialog'] button:text-is('Post')",
)
# Last resort, by accessible name, exact: the reshare composer nests "Post"
# in a span, so an exact-text selector matches the span and never the
# button (0 matches, probed live 2026-09-28); the button whose accessible
# name is exactly "Post" is the one match there, and "Post to Anyone" is not
# it.
POST_BUTTON_NAME = "Post"
MEDIA_BUTTON_SELECTOR = ("button[aria-label*='media' i], "
                         "button[aria-label*='Add a photo' i], "
                         "button[aria-label*='Add media' i]")
FILE_INPUT_SELECTOR = "input[type='file']"
POLL_BUTTON_SELECTOR = ("button[aria-label*='poll' i], "
                        "button:has-text('Create a poll')")
POLL_QUESTION_SELECTOR = "textarea[aria-label*='question' i]"
POLL_OPTION_SELECTOR = "input[aria-label*='option' i]"
POLL_DURATION_SELECTOR = "button[aria-label*='duration' i]"
POLL_ONE_WEEK_SELECTOR = "text=/1\\s*week/i"
POLL_DONE_SELECTOR = "button:has-text('Done')"


class _StepFailed(Exception):
    """One named composer step failed. Carries its name so the refusal --
    and the snapshot -- says exactly where, never just that publishing
    failed somewhere."""

    def __init__(self, step: str, cause: Exception) -> None:
        super().__init__(f"{step}: {cause}")
        self.step = step
        self.cause = cause


async def _guarded(step: str, coro) -> None:
    try:
        await coro()
    except (SignedOut, HardStop):
        raise
    except Exception as exc:
        raise _StepFailed(step, exc) from exc


async def _type_text(tab, text: str) -> None:
    editor = tab.locator(".ql-editor").first
    await editor.wait_for(state="visible", timeout=20000)
    await editor.click()
    await pause(tab, 600, 1200)
    await tab.keyboard.type(text, delay=12)
    await pause(tab, 1200, 2000)


async def _readback(tab) -> str:
    return await tab.evaluate(
        "(document.querySelector('.ql-editor') || {}).innerText || ''")


async def _attach_file(tab, path: str, *, kind: str) -> None:
    """Click the composer's media button and hand it the file through the
    file input it reveals. `kind='video'` waits considerably longer: a
    photo previews almost at once, LinkedIn transcodes a video before it
    can be posted (both timings ASSUMED, Task 64)."""
    button = tab.locator(MEDIA_BUTTON_SELECTOR).first
    await button.wait_for(state="visible", timeout=15000)
    await button.click()
    await pause(tab, 400, 900)
    file_input = tab.locator(FILE_INPUT_SELECTOR).first
    await file_input.wait_for(state="attached", timeout=15000)
    await file_input.set_input_files(path)
    lo, hi = (2000, 4000) if kind == "image" else (8000, 15000)
    await pause(tab, lo, hi)


async def _fill_poll(tab, poll: dict[str, Any]) -> None:
    """LinkedIn's own "Create a poll" flow: question, 2-4 options, then the
    duration fixed to one week regardless of what the bank entry carries
    (Task 64's standing instruction, not the model's to vary) -- visibility
    is public by construction, there being no other option in the composer
    this module has ever seen documented."""
    button = tab.locator(POLL_BUTTON_SELECTOR).first
    await button.wait_for(state="visible", timeout=15000)
    await button.click()
    await pause(tab, 500, 1000)
    question = tab.locator(POLL_QUESTION_SELECTOR).first
    await question.wait_for(state="visible", timeout=15000)
    await question.click()
    await tab.keyboard.type(str(poll.get("question") or ""), delay=12)
    for option in poll.get("options") or []:
        field = tab.locator(POLL_OPTION_SELECTOR).first
        await field.wait_for(state="visible", timeout=10000)
        await field.click()
        await tab.keyboard.type(str(option), delay=12)
    duration = tab.locator(POLL_DURATION_SELECTOR).first
    await duration.click()
    await pause(tab, 300, 600)
    one_week = tab.locator(POLL_ONE_WEEK_SELECTOR).first
    await one_week.click()
    done = tab.locator(POLL_DONE_SELECTOR).first
    await done.wait_for(state="visible", timeout=15000)
    await done.click()
    await pause(tab, 800, 1500)


async def _post_button(tab):
    candidates = [tab.locator(s).first for s in POST_BUTTON_SELECTORS]
    if hasattr(tab, "get_by_role"):
        candidates.append(tab.get_by_role("button", name=POST_BUTTON_NAME,
                                          exact=True).first)
    last: Exception | None = None
    for button in candidates:
        try:
            await button.wait_for(state="visible", timeout=8000)
            return button
        except Exception as exc:                        # noqa: BLE001
            last = exc
    raise RuntimeError(f"no Post button in the composer ({last})")


async def _click_post(tab) -> None:
    """Click the composer's Post button once it is enabled -- it stays
    disabled until LinkedIn has taken the text, and a click on a disabled
    button does nothing and raises nothing."""
    post_btn = await _post_button(tab)
    for _ in range(30):
        if await post_btn.is_enabled():
            break
        await tab.wait_for_timeout(500)
    else:
        raise RuntimeError("the Post button never became enabled")
    await post_btn.click()


def _mark_unverified(state: dict[str, Any], page: str, entry: dict[str, Any],
                     evidence: dict[str, Any]) -> ActionResult:
    """Post was clicked, but the page's own published list does not show
    it. The entry must not stay `approved` (the next gate would click Post
    again for the same words) and must not become `published` either (that
    claims a success nobody saw) -- `posted_unverified` is neither, and a
    person has to look."""
    entry["status"] = "posted_unverified"
    entry["published_attempt_at"] = stamp()
    _entries, posting, holder = bank(state, page)
    posting["last_post"] = today()
    posting["last_post_attempt"] = stamp()
    holder["posting"] = posting
    save(state)
    log_run([(f"Post clicked for {entry.get('id')} on {PAGES[page]['name']} "
              "but not confirmed; marked posted_unverified")], label="publish")
    return ActionResult(
        False,
        f"Post was clicked for {entry.get('id')} but it could not be "
        f"confirmed on the {PAGES[page]['name']} page's published list; "
        f"marked posted_unverified so the gate will not send it again -- "
        f"check LinkedIn by hand.", evidence=evidence)


async def _publish(page: str, entry_id: str) -> ActionResult:
    state = load()
    entry, why = gate(state, page)
    if entry is None or (entry_id and str(entry.get("id")) != str(entry_id)):
        return ActionResult(False, f"not publishing to {page}: "
                                   f"{why if entry is None else entry_id + ' is not next'}")
    spec = PAGES[page]
    kind = entry.get("kind") or "text"
    text = str(entry.get("text") or "")
    if kind in ("image", "video") and not entry.get("media"):
        return ActionResult(False, f"{entry.get('id')} is a {kind} post with "
                                   f"no media attached; refusing rather than "
                                   f"post a bare link")
    poll = entry.get("poll") or {}
    if kind == "poll" and len(poll.get("options") or []) < 2:
        return ActionResult(False, f"{entry.get('id')} is a poll with fewer "
                                   f"than 2 options; refusing")
    try:
        pw, b, ctx = await browser()
    except NoSession as exc:
        return ActionResult(False, str(exc))
    evidence: dict[str, Any] = {"page": page, "entry": entry.get("id"), "kind": kind}
    try:
        tab = await ctx.new_page()
        try:
            await goto(tab, f"https://www.linkedin.com/company/{spec['id']}"
                            f"/admin/page-posts/published/?share=true")
        except (SignedOut, HardStop) as exc:
            if isinstance(exc, HardStop):
                state.setdefault("alerts", []).append({"when": stamp(), "what": str(exc)})
                save(state)
            return ActionResult(False, str(exc))

        try:
            if kind == "poll":
                await _guarded("fill-poll", lambda: _fill_poll(tab, poll))
        except _StepFailed as exc:
            shot = await snapshot(tab, f"step-{exc.step}-failed")
            evidence["shot"] = shot
            return ActionResult(False, f"step {exc.step!r} failed: {exc.cause}"
                                       + (f" ({shot})" if shot else ""),
                                evidence=evidence)

        await _type_text(tab, text)
        got = await _readback(tab)
        diff = _readback_diff(text, got)
        if diff is not None:
            shot = await snapshot(tab, "readback-mismatch")
            evidence["readback"] = got[:400]
            evidence["diff"] = diff
            evidence["shot"] = shot
            return ActionResult(False,
                "the editor did not read back the approved text byte for "
                "byte; aborted before Post:\n" + diff,
                evidence=evidence)

        if kind in ("image", "video"):
            try:
                await _guarded(f"attach-{kind}",
                               lambda: _attach_file(tab, entry["media"], kind=kind))
            except _StepFailed as exc:
                shot = await snapshot(tab, f"step-{exc.step}-failed")
                evidence["shot"] = shot
                return ActionResult(False, f"step {exc.step!r} failed: {exc.cause}"
                                           + (f" ({shot})" if shot else ""),
                                    evidence=evidence)
            # A sub-modal (the photo/video attach panel) has been seen to
            # drop the editor's own text; the byte-for-byte guarantee is
            # re-checked right before Post, not only before the first side
            # effect.
            got2 = await _readback(tab)
            diff2 = _readback_diff(text, got2)
            if diff2 is not None:
                shot = await snapshot(tab, "readback-mismatch-after-media")
                evidence["readback"] = got2[:400]
                evidence["diff"] = diff2
                evidence["shot"] = shot
                return ActionResult(False,
                    f"the editor's text changed after attaching the {kind}; "
                    "aborted before Post:\n" + diff2, evidence=evidence)

        try:
            await _guarded("click-post", lambda: _click_post(tab))
        except _StepFailed as exc:
            shot = await snapshot(tab, f"step-{exc.step}-failed")
            evidence["shot"] = shot
            return ActionResult(False, f"step {exc.step!r} failed: {exc.cause}"
                                       + (f" ({shot})" if shot else ""),
                                evidence=evidence)
        await pause(tab, 4000, 6000)

        # The toast's Continue is an <a> carrying the durable deep link.
        share_href = await tab.evaluate(
            "(() => { const a = Array.from(document.querySelectorAll('a'))"
            ".find(a => (a.href||'').includes('shareActive=true')); return a ? a.href : ''; })()")
        activity = ""
        if share_href:
            m = re.search(r"shareUrl=([^&]+)", share_href)
            activity = _unquote(m.group(1)) if m else ""

        # Never claim what was not seen (Task 64): the published list, not
        # the toast alone, is what confirms the post is really there.
        await goto(tab, f"https://www.linkedin.com/company/{spec['id']}"
                        f"/admin/page-posts/published/")
        published_text = await tab.evaluate("document.body.innerText")
        if not activity:
            activity = await tab.evaluate(
                "(() => { const a = Array.from(document.querySelectorAll('a'))"
                ".find(a => (a.href||'').includes('/feed/update/urn:li:activity')); "
                "return a ? a.href.split('?')[0] : ''; })()")
        needle = text.strip()[:40].lower()
        verified = bool(needle) and needle in (published_text or "").lower()
        evidence["activity_url"] = activity
        evidence["verified"] = verified
        if not verified:
            return _mark_unverified(state, page, entry, evidence)

        reshared: bool | str = False
        if activity and (bank(state, page)[1].get("reshare_to_profile", True)):
            reshared, why = await _reshare(tab, activity, text)
            if why:
                evidence["reshare_error"] = why
        evidence["reshared"] = reshared
        await keep_session(ctx)
    finally:
        await ctx.close()
        await b.close()
        await pw.stop()

    # Mark it. The entry is published whether or not the reshare verified;
    # the reshare can be repaired days later from the activity URL.
    entry["status"] = "published"
    entry["published"] = today()
    entry["published_at"] = stamp()
    entry["activity_url"] = activity
    entry["reshared"] = reshared
    _entries, posting, holder = bank(state, page)
    posting["last_post"] = today()
    posting["last_post_attempt"] = stamp()
    holder["posting"] = posting
    page_obj(state, page).setdefault("posts", []).append(
        {"id": entry.get("id"), "first_line": text.split("\n")[0][:120],
         "posted": today(), "impressions": 0, "reactions": 0, "comments": 0,
         "activity_url": activity})
    save(state)
    log_run([f"published {entry.get('id')} to {spec['name']}",
             f"activity: {activity or 'unknown'}",
             f"reshare verified: {reshared}"], label="publish")
    # No promise of a retry that nothing performs: the reshare card is raised
    # by the module's signals, and `linkedin.reshare_post` redoes it.
    if reshared is True:
        tail = ", reshared to Miro's profile"
    elif reshared == UNVERIFIED:
        tail = ("; the reshare to Miro's profile was clicked but did not verify -- "
                "check his activity")
    else:
        tail = ("; the reshare to Miro's profile did not go out -- a "
                f"'Reshare {entry.get('id')}' card follows")
    detail = f"published {entry.get('id')} to the {spec['name']} page" + tail
    return ActionResult(True, detail, evidence=evidence)


PROFILE_ACTIVITY = ("https://www.linkedin.com/in/myron-mekhael-841196242/"
                    "recent-activity/all/")


# How a reshare ended, as stored in the entry's `reshared`: True verified on
# Miro's activity; UNVERIFIED Post was clicked but never seen there, so it may
# well be live and is never offered again (a second click is a duplicate
# post); False Post was not clicked, safe to offer again.
UNVERIFIED = "unverified"
ACTIVITY_LOOKS = 3


async def _seen_on_activity(tab, text: str) -> bool:
    """Miro's activity lags a fresh repost by seconds to a minute (G8,
    2026-09-28: missed on the first read, there five minutes later), so look
    more than once before saying it is not there."""
    needle = text.strip()[:40].lower()
    if not needle:
        return False
    for look in range(ACTIVITY_LOOKS):
        if look:
            await pause(tab, 8000, 12000)
        await goto(tab, PROFILE_ACTIVITY)
        page_text = await tab.evaluate("document.body.innerText")
        if needle in (page_text or "").lower():
            return True
    return False


async def _reshare(tab, activity: str, text: str) -> tuple[bool | str, str]:
    """Reshare a published page post to Miro's profile through the durable
    deep link, then verify it on his activity. Returns (True | UNVERIFIED |
    False, why-not)."""
    deep = (f"https://www.linkedin.com/feed/?shareActive=true"
            f"&shareUrl={quote(activity, safe='')}&shareActorType=MEMBER")
    try:
        await goto(tab, deep)
        await _click_post(tab)
    except (SignedOut, HardStop) as exc:
        return False, str(exc)
    except Exception as exc:                            # noqa: BLE001
        return False, f"{type(exc).__name__}: {str(exc)[:120]}"
    try:
        await pause(tab, 4000, 6000)
        if await _seen_on_activity(tab, text):
            return True, ""
        why = f"Post clicked, but not on Miro's recent activity after {ACTIVITY_LOOKS} looks"
    except Exception as exc:                            # noqa: BLE001
        why = f"Post clicked, then the check failed: {type(exc).__name__}: {str(exc)[:100]}"
    return UNVERIFIED, why


def _reshare_target(state: dict[str, Any], page: str, entry_id: str
                    ) -> tuple[dict | None, str]:
    if page not in PAGES:
        return None, f"no such page {page!r}"
    entry = _entry(state, page, entry_id)
    if entry is None:
        return None, f"no entry {entry_id} in the {page} bank"
    if entry.get("status") != "published" or not entry.get("activity_url"):
        return None, f"{entry_id} is not a published post with a link to reshare"
    if entry.get("reshared") == UNVERIFIED:
        return None, (f"{entry_id}'s reshare was clicked once and did not verify; it may "
                      f"be live -- check Miro's activity before resharing again")
    if entry.get("reshared"):
        return None, f"{entry_id} is already reshared to Miro's profile"
    return entry, ""


async def reshare_dry(page: str = "", entry_id: str = "") -> ActionResult:
    try:
        state = load()
    except Exception as exc:                            # noqa: BLE001
        return ActionResult(False, f"cannot read the LinkedIn state: {exc}")
    entry, why = _reshare_target(state, page, entry_id)
    if entry is None:
        return ActionResult(False, f"would not reshare: {why}")
    return ActionResult(True, f"would reshare {entry_id} from the "
                              f"{PAGES[page]['name']} page to Miro's profile "
                              f"({entry['activity_url']})",
                        evidence={"page": page, "entry": entry_id})


async def reshare(page: str = "", entry_id: str = "") -> ActionResult:
    async with lock(), file_lock():
        state = load()
        entry, why = _reshare_target(state, page, entry_id)
        if entry is None:
            return ActionResult(False, f"not resharing: {why}")
        try:
            pw, b, ctx = await browser()
        except NoSession as exc:
            return ActionResult(False, str(exc))
        try:
            tab = await ctx.new_page()
            ok, why = await _reshare(tab, str(entry["activity_url"]),
                                     str(entry.get("text") or ""))
            await keep_session(ctx)
        finally:
            await ctx.close()
            await b.close()
            await pw.stop()
        if ok is False:
            return ActionResult(False, f"the reshare of {entry_id} did not go out: {why}")
        entry["reshared"] = ok
        entry["reshared_at"] = stamp()
        save(state)
        if ok == UNVERIFIED:
            log_run([f"reshare of {entry_id} ({PAGES[page]['name']}) unverified: {why}"],
                    label="reshare")
            return ActionResult(False, f"the reshare of {entry_id} did not verify ({why}); "
                                       f"it may be live, so it is not offered again")
        log_run([f"reshared {entry_id} ({PAGES[page]['name']}) to Miro's profile"],
                label="reshare")
        return ActionResult(True, f"reshared {entry_id} to Miro's profile, verified "
                                  f"on his activity")


def _unquote(s: str) -> str:
    from urllib.parse import unquote
    return unquote(s)


async def approve_draft_dry(page: str = "", entry_id: str = "",
                            text_sha256: str = "") -> ActionResult:
    # Deliberately does not check `text_sha256` against the draft: this runs
    # as `preview()`, before Miro decides, and as the executor's own
    # pre-approval dry run -- returning ok=False here on a mismatch would
    # stop `execute()` before `run()` ever gets called, and it is `run()`
    # that has to notice the change and get a fresh card started.
    try:
        state = load()
    except Exception as exc:                            # noqa: BLE001
        return ActionResult(False, f"cannot read the LinkedIn state: {exc}")
    e = _entry(state, page, entry_id)
    if e is None:
        return ActionResult(False, f"no entry {entry_id} in the {page} bank")
    if e.get("status") != "draft":
        return ActionResult(False, f"{entry_id} is {e.get('status')}, not a draft")
    return ActionResult(True, f"would mark {entry_id} approved for the "
                              f"{PAGES[page]['name']} page:\n{str(e.get('text'))[:400]}")


async def approve_draft(page: str = "", entry_id: str = "", *,
                        text_sha256: str) -> ActionResult:
    async with file_lock():
        return await _approve_draft(page, entry_id, text_sha256)


async def _approve_draft(page: str, entry_id: str, text_sha256: str) -> ActionResult:
    state = load()
    e = _entry(state, page, entry_id)
    if e is None or e.get("status") != "draft":
        return ActionResult(False, f"{entry_id} is not a draft in the {page} bank")
    current = payload_sha256(e)
    if text_sha256 != current:
        # The card he saw no longer matches the bank -- the sweep rewrote
        # this draft after it went out. Approving it would post text he
        # never read. Bump the card's revision so the NEXT sweep's signal
        # carries a title the scheduler has not seen and `propose()` has no
        # pending proposal for, and refuse this one outright.
        e["card_rev"] = int(e.get("card_rev") or 1) + 1
        save(state)
        return ActionResult(False, "the draft changed since you saw it; a "
                                   "fresh card is coming")
    e["status"] = "approved"
    e["approved"] = today()
    save(state)
    return ActionResult(True, f"{entry_id} approved; it goes out on the next open "
                              f"posting day for {PAGES[page]['name']}",
                        rollback_token=f"{page}:{entry_id}")


async def unapprove_draft(token: str) -> ActionResult:
    page, _, entry_id = (token or "").partition(":")
    async with file_lock():
        return await _unapprove_draft(page, entry_id)


async def _unapprove_draft(page: str, entry_id: str) -> ActionResult:
    state = load()
    e = _entry(state, page, entry_id)
    if e is None or e.get("status") != "approved":
        return ActionResult(False, f"{entry_id} is not an approved entry")
    e["status"] = "draft"
    e.pop("approved", None)
    save(state)
    return ActionResult(True, f"{entry_id} is a draft again")


if __name__ == "__main__":
    import sys
    print(asyncio.run(publish_dry(sys.argv[1] if len(sys.argv) > 1 else "flycheap")))
