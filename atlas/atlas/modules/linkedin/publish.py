"""The one LinkedIn write: publish an approved page post, byte for byte.

`linkedin.publish_post` is Tier 2. Its card IS the approval for that post;
the post-bank status `approved` is what makes an entry eligible to be
offered at all. A draft is never published, approved text is never edited,
and the only link allowed is the page's own. It has no rollback: a LinkedIn
post can only be undone by deleting it, and deletion is permanently denied.

**Vault posts publish themselves** (Miro, 2026-10-11: post the Vault page's
approved posts automatically "the way reels already do"; memory
approval-policy -- a standing rule is a tier set in code, never a card
approved at runtime). `linkedin.autopublish_post` is Tier AUTO, the shape of
`content.autopublish_reel`, and runs only past `autopublish_refusal`, in
code: the page is `guardian` (Vault) and still active (FlyCheap is retired
through LINKEDIN_PAGES_ACTIVE, and is never auto-posted even if named
again); LINKEDIN_AUTOPUBLISH is on (default); the page's autopublish is not
paused; the entry is the bank's next `approved` one and its payload is the
one approved (`approved_sha256`); a posting day from the bank, and an hour
inside the bank's `posting.hours` (default 9-18, America/New York); at most
one post on the page today; the saved session exists and the last sweep
was clean and recent; no restriction alert in the window. Its rollback is
honest, like `agency.send_reply`'s: a live post is not unpublished (that is
deletion, Tier 3) -- the rollback pauses that page's autopublish
(`posting.autopublish_paused`) and records why, so every next post is a
card again until the pause is cleared by hand. Anything refused is the
Tier 2 `linkedin.publish_post` card with "Held for you: <reason>"
(`kernel.linkedin_autopost`).

The reshare to Miro's profile is the distribution (a controlled pair: 1
impression without it, 10 with it), done through the durable deep link and
verified rather than assumed. A card-approved publish reshares as before.
An automatic one does not: a reshare posts on Miro's personal profile and
cannot be taken back, which is not the reels pattern (reels post to ATLAS's
own accounts), so it stays `linkedin.reshare_post`, Tier 2 -- the entry is
marked `reshared: False` and the module's reshare card follows.
"""
from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import logging
import os
import re
import unicodedata
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

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
from .store import (
    bank,
    file_lock,
    has_banned_link,
    load,
    log_run,
    page_obj,
    save,
    stamp,
    today,
)

log = logging.getLogger("atlas.linkedin.publish")



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
    if has_banned_link(text):
        return None, f"{entry.get('id')} carries a banned link"
    if link not in text and "http" in text:
        return None, f"{entry.get('id')} links somewhere other than {link}"
    poll = entry.get("poll") or {}
    if (left := leftover_label(" ".join(
            [text, str(poll.get("intro") or ""), str(poll.get("question") or "")]))):
        return None, (f"{entry.get('id')} still carries the drafting label "
                      f"{left!r}; it needs a clean re-draft")
    return entry, "ok"


# A drafting label the model left in the text (2026-10-13: A5 "INTRO: Which
# group...", G18 "Block 3 -- KIND: poll"). What was approved is what posts,
# so such an entry is refused here rather than cleaned at publish time.
_LEFTOVER = re.compile(
    r"(?im)(^|\n)\W*(intro|question|options|text|kind|hook|image_prompt|"
    r"video_prompt)\s*:|\bblock\s*\d+\s*\W+\s*kind\s*:")


def leftover_label(text: str) -> str:
    """The first drafting label left in `text`, or ""."""
    m = _LEFTOVER.search(text or "")
    return m.group(0).strip() if m else ""


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
    except Exception as exc:  # noqa: BLE001
        return ActionResult(False, f"cannot read the LinkedIn state: {exc}")
    entry, why = gate(state, page)
    if entry is None:
        return ActionResult(False, f"would not publish to {page}: {why}")
    if entry_id and str(entry.get("id")) != str(entry_id):
        return ActionResult(False, f"{entry_id} is not the next approved entry for "
                                   f"{page}; that is {entry.get('id')}")
    text = str(entry.get("text") or "")
    if PAGES[page].get("kind") == "profile":
        return ActionResult(
            True,
            f"would post {entry.get('id')} from {PAGES[page]['name']} "
            f"({len(text)} chars, text-only, no reshare):\n" + text[:400],
            evidence={"page": page, "entry": entry.get("id"), "chars": len(text)})
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
# The poll flow, read off the live page-admin composer 2026-10-13 (after
# cards #377/#378 failed at 'fill-poll'): the composer's toolbar row is a
# carousel of `button.share-promoted-detour-button`s labelled exactly "Add
# media", "Create an event", "Celebrate an occasion", "More". There is no
# poll button until "More" (the "+") is clicked; it does not open a menu, it
# replaces itself in the same row with "Share that you're hiring", "Create a
# poll" and "Add a document". The direct button is still tried first, for a
# composer that shows it unexpanded. Never `aria-label*='add'`: "Add media"
# opens the file picker.
POLL_BUTTON_SELECTOR = ("[role='dialog'] button[aria-label='Create a poll'], "
                        "[role='dialog'] button[aria-label*='poll' i], "
                        "[role='dialog'] button:has-text('Create a poll')")
POLL_DIRECT_TIMEOUT_MS = 2500
POLL_MORE_SELECTORS = (
    "[role='dialog'] button.share-promoted-detour-button[aria-label='More']",
    "[role='dialog'] button[aria-label='More']",
    "[role='dialog'] button[aria-label='Add more']",
)
# Last resort for "More": the toolbar's last detour button -- before it is
# expanded, that is the "+". Clicked only when its label is none of the
# toolbar's other known actions.
TOOLBAR_BUTTON_SELECTOR = "[role='dialog'] button.share-promoted-detour-button"
NOT_MORE_LABELS = ("media", "photo", "video", "event", "occasion", "celebrate",
                   "hiring", "document", "poll")
# The "Create a poll" form (same dialog, header "Create a poll"): a question
# textarea (0/140), Option 1 and Option 2 inputs with fixed ids (0/30 each),
# an "Add option" button for the 3rd and 4th, a duration <select> whose
# values are days ("7" = "1 week", selected by default), Back and Done (Done
# disabled until the required fields are filled). None of the fields carry
# an aria-label -- the old `[aria-label*=...]` selectors matched nothing.
POLL_QUESTION_SELECTOR = ("[role='dialog'] textarea.polls-detour__question-field, "
                          "[role='dialog'] textarea[aria-label*='question' i]")
POLL_OPTION_SELECTOR = "[role='dialog'] input#poll-option-{n}"
POLL_ADD_OPTION_SELECTOR = "[role='dialog'] button:has-text('Add option')"
POLL_DURATION_SELECTOR = "[role='dialog'] select"
POLL_ONE_WEEK_VALUE = "7"
POLL_DONE_SELECTOR = ("[role='dialog'] button[aria-label='Done'], "
                      "[role='dialog'] button:text-is('Done')")


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
    is public by construction, there being no other option in the composer.

    Every field is read back before Done: a poll whose question or an
    option is not exactly the approved one is refused here, before Post,
    the same as the editor's text."""
    await _open_poll_form(tab)
    question_text = str(poll.get("question") or "")
    question = tab.locator(POLL_QUESTION_SELECTOR).first
    await question.wait_for(state="visible", timeout=15000)
    await question.click()
    await tab.keyboard.type(question_text, delay=12)
    options = [str(o) for o in poll.get("options") or []]
    for n, option in enumerate(options, start=1):
        field = tab.locator(POLL_OPTION_SELECTOR.format(n=n)).first
        if n > 2:
            add = tab.locator(POLL_ADD_OPTION_SELECTOR).first
            await add.wait_for(state="visible", timeout=10000)
            await add.click()
            await pause(tab, 300, 700)
        await field.wait_for(state="visible", timeout=10000)
        await field.click()
        await tab.keyboard.type(option, delay=12)
    duration = tab.locator(POLL_DURATION_SELECTOR).first
    await duration.wait_for(state="visible", timeout=10000)
    await duration.select_option(POLL_ONE_WEEK_VALUE)
    await pause(tab, 300, 600)

    got = {"question": await question.input_value(),
           "options": [await tab.locator(POLL_OPTION_SELECTOR.format(n=n))
                       .first.input_value() for n in range(1, len(options) + 1)],
           "duration": await duration.input_value()}
    want = {"question": question_text, "options": options,
            "duration": POLL_ONE_WEEK_VALUE}
    if got != want:
        raise RuntimeError(f"the poll form did not read back as approved: "
                           f"wanted {want!r}, got {got!r}")

    done = tab.locator(POLL_DONE_SELECTOR).first
    await done.wait_for(state="visible", timeout=15000)
    for _ in range(20):
        if await done.is_enabled():
            break
        await tab.wait_for_timeout(500)
    else:
        raise RuntimeError("the poll form's Done button never became enabled")
    await done.click()
    # Done closes the form and returns to the composer; until the question
    # field is gone, nothing after this step can be trusted.
    await question.wait_for(state="hidden", timeout=15000)
    await pause(tab, 800, 1500)


def _poll_over_limits(poll: dict[str, Any]) -> str:
    """The form's own limits (live 2026-10-13: question 0/140, each option
    0/30; at most 4 options is LinkedIn's documented maximum, not read
    live). An approved poll over them would be cut or
    refused by the form; refused here instead, before any browser."""
    from .sweep import MAX_POLL_OPTION_CHARS, MAX_POLL_OPTIONS, MAX_POLL_QUESTION_CHARS
    options = [str(o) for o in poll.get("options") or []]
    if len(str(poll.get("question") or "")) > MAX_POLL_QUESTION_CHARS:
        return f"question over {MAX_POLL_QUESTION_CHARS} characters"
    if len(options) > MAX_POLL_OPTIONS:
        return f"more than {MAX_POLL_OPTIONS} options"
    if any(len(o) > MAX_POLL_OPTION_CHARS for o in options):
        return f"an option over {MAX_POLL_OPTION_CHARS} characters"
    return ""


async def _open_poll_form(tab) -> None:
    """The direct "Create a poll" button if the toolbar already shows it
    (briefly); otherwise "More" first, which expands the same row with the
    poll button in it. Raises -- caught as the 'fill-poll' step -- when
    neither path finds it."""
    direct = tab.locator(POLL_BUTTON_SELECTOR).first
    try:
        await direct.wait_for(state="visible", timeout=POLL_DIRECT_TIMEOUT_MS)
    except Exception:  # noqa: BLE001
        await _click_more(tab)
        direct = tab.locator(POLL_BUTTON_SELECTOR).first
        await direct.wait_for(state="visible", timeout=10000)
    await direct.click()
    await pause(tab, 800, 1500)


async def _click_more(tab) -> None:
    for selector in POLL_MORE_SELECTORS:
        more = tab.locator(selector).first
        if await more.count():
            await more.click()
            await pause(tab, 600, 1200)
            return
    last = tab.locator(TOOLBAR_BUTTON_SELECTOR).last
    if await last.count():
        label = (await last.get_attribute("aria-label") or "").lower()
        if label and not any(word in label for word in NOT_MORE_LABELS):
            await last.click()
            await pause(tab, 600, 1200)
            return
        raise RuntimeError(f"no poll button and no 'More' in the composer "
                           f"toolbar (its last button is {label!r})")
    raise RuntimeError("no poll button and no 'More' in the composer toolbar")


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
        except Exception as exc:  # noqa: BLE001
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
    evidence["posted_unverified"] = True
    _entries, posting, holder = bank(state, page)
    posting["last_post"] = today()
    posting["last_post_attempt"] = stamp()
    holder["posting"] = posting
    save(state)
    log_run([(f"Post clicked for {entry.get('id')} on {PAGES[page]['name']} "
              "but not confirmed; marked posted_unverified")], label="publish")
    where = ("on your recent activity" if PAGES[page].get("kind") == "profile"
             else f"on the {PAGES[page]['name']} page's published list")
    return ActionResult(
        False,
        f"Post was clicked for {entry.get('id')} but it could not be "
        f"confirmed {where}; "
        f"marked posted_unverified so the gate will not send it again -- "
        f"check LinkedIn by hand.", evidence=evidence)


async def _publish(page: str, entry_id: str, *, auto: bool = False) -> ActionResult:
    """`auto` (linkedin.autopublish_post): no reshare -- that stays a card."""
    state = load()
    entry, why = gate(state, page)
    if entry is None or (entry_id and str(entry.get("id")) != str(entry_id)):
        return ActionResult(False, f"not publishing to {page}: "
                                   f"{why if entry is None else entry_id + ' is not next'}")
    spec = PAGES[page]
    kind = entry.get("kind") or "text"
    text = str(entry.get("text") or "")
    if spec.get("kind") == "profile":
        return await _publish_profile(state, page, entry)
    if kind in ("image", "video") and not entry.get("media"):
        return ActionResult(False, f"{entry.get('id')} is a {kind} post with "
                                   f"no media attached; refusing rather than "
                                   f"post a bare link")
    poll = entry.get("poll") or {}
    if kind == "poll" and len(poll.get("options") or []) < 2:
        return ActionResult(False, f"{entry.get('id')} is a poll with fewer "
                                   f"than 2 options; refusing")
    if kind == "poll" and (why_not := _poll_over_limits(poll)):
        return ActionResult(False, f"{entry.get('id')} is a poll LinkedIn's form "
                                   f"cannot take as approved ({why_not}); refusing")
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
        if not auto and activity and (
                bank(state, page)[1].get("reshare_to_profile", True)):
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
    if auto:
        entry["autopublished"] = True
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
    if auto and autoreshare_owns(page):
        tail = (" on its own; the reshare to Miro's profile follows on its own "
                f"({AUTORESHARE_ENV}), or a 'Reshare {entry.get('id')}' card if "
                "it cannot")
    elif auto:
        tail = (" on its own; the reshare to Miro's profile stays his tap -- a "
                f"'Reshare {entry.get('id')}' card follows")
    elif reshared is True:
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
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {str(exc)[:120]}"
    try:
        await pause(tab, 4000, 6000)
        if await _seen_on_activity(tab, text):
            return True, ""
        why = f"Post clicked, but not on Miro's recent activity after {ACTIVITY_LOOKS} looks"
    except Exception as exc:  # noqa: BLE001
        why = f"Post clicked, then the check failed: {type(exc).__name__}: {str(exc)[:100]}"
    return UNVERIFIED, why


# The profile lane (Miro, 2026-11-23): the post is made from his personal
# profile through the feed composer, the same member composer `_reshare`
# opens through this URL (that one is verified live, 2026-09-28). What is
# NOT verified is typing into it: `.ql-editor` and the Post button are
# ASSUMED to be the same as the page composer's. The guards make a wrong
# guess fail closed, never post wrong text: a missing editor or button is a
# named step failure with nothing posted; the editor must read back the
# approved text byte for byte before Post is clicked; and the post must be
# seen on his own activity afterwards, else the entry is `posted_unverified`
# and the gate will not send it again.
PROFILE_COMPOSER = "https://www.linkedin.com/feed/?shareActive=true"


async def _publish_profile(state: dict[str, Any], page: str,
                           entry: dict[str, Any]) -> ActionResult:
    name = PAGES[page]["name"]
    kind = entry.get("kind") or "text"
    text = str(entry.get("text") or "")
    if kind != "text":
        return ActionResult(False, f"{entry.get('id')} is a {kind} post; the profile "
                                   "lane posts text only; refusing")
    if not text.strip():
        return ActionResult(False, f"{entry.get('id')} has no text; refusing")
    try:
        pw, b, ctx = await browser()
    except NoSession as exc:
        return ActionResult(False, str(exc))
    evidence: dict[str, Any] = {"page": page, "entry": entry.get("id"), "kind": kind}
    try:
        tab = await ctx.new_page()
        try:
            await goto(tab, PROFILE_COMPOSER)
        except (SignedOut, HardStop) as exc:
            if isinstance(exc, HardStop):
                state.setdefault("alerts", []).append({"when": stamp(), "what": str(exc)})
                save(state)
            return ActionResult(False, str(exc))
        try:
            await _guarded("type-text", lambda: _type_text(tab, text))
        except _StepFailed as exc:
            shot = await snapshot(tab, f"step-{exc.step}-failed")
            evidence["shot"] = shot
            return ActionResult(False, f"step {exc.step!r} failed: {exc.cause}"
                                       + (f" ({shot})" if shot else ""),
                                evidence=evidence)
        got = await _readback(tab)
        diff = _readback_diff(text, got)
        if diff is not None:
            shot = await snapshot(tab, "readback-mismatch")
            evidence.update({"readback": got[:400], "diff": diff, "shot": shot})
            return ActionResult(False,
                "the editor did not read back the approved text byte for "
                "byte; aborted before Post:\n" + diff, evidence=evidence)
        try:
            await _guarded("click-post", lambda: _click_post(tab))
        except _StepFailed as exc:
            shot = await snapshot(tab, f"step-{exc.step}-failed")
            evidence["shot"] = shot
            return ActionResult(False, f"step {exc.step!r} failed: {exc.cause}"
                                       + (f" ({shot})" if shot else ""),
                                evidence=evidence)
        await pause(tab, 4000, 6000)
        try:
            verified = await _seen_on_activity(tab, text)
        except (SignedOut, HardStop):
            verified = False
        evidence["verified"] = verified
        if not verified:
            return _mark_unverified(state, page, entry, evidence)
        await keep_session(ctx)
    finally:
        await ctx.close()
        await b.close()
        await pw.stop()
    entry["status"] = "published"
    entry["published"] = today()
    entry["published_at"] = stamp()
    entry["activity_url"] = ""
    _entries, posting, holder = bank(state, page)
    posting["last_post"] = today()
    posting["last_post_attempt"] = stamp()
    holder["posting"] = posting
    page_obj(state, page).setdefault("posts", []).append(
        {"id": entry.get("id"), "first_line": text.split("\n")[0][:120],
         "posted": today(), "activity_url": ""})
    save(state)
    log_run([f"posted {entry.get('id')} from {name}"], label="publish")
    return ActionResult(True, f"posted {entry.get('id')} from {name}; seen on "
                              "your recent activity", evidence=evidence)


def _reshare_target(state: dict[str, Any], page: str, entry_id: str
                    ) -> tuple[dict | None, str]:
    if page not in PAGES:
        return None, f"no such page {page!r}"
    if PAGES[page].get("kind") == "profile":
        return None, "the profile lane posts from the profile itself; nothing to reshare"
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
    except Exception as exc:  # noqa: BLE001
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
        return await _reshare_entry(state, page, entry_id, entry)


async def _reshare_entry(state: dict[str, Any], page: str, entry_id: str,
                         entry: dict[str, Any], *, auto: bool = False) -> ActionResult:
    """The reshare itself, under the caller's browser and state-file locks."""
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
    if auto:
        entry["autoreshared"] = True
    save(state)
    if ok == UNVERIFIED:
        log_run([f"reshare of {entry_id} ({PAGES[page]['name']}) unverified: {why}"],
                label="reshare")
        return ActionResult(False, f"the reshare of {entry_id} did not verify ({why}); "
                                   f"it may be live, so it is not offered again",
                            evidence={"reshare_unverified": True})
    log_run([f"reshared {entry_id} ({PAGES[page]['name']}) to Miro's profile"
             + (" on its own" if auto else "")], label="reshare")
    return ActionResult(True, f"reshared {entry_id} to Miro's profile, verified "
                              f"on his activity")


# --------------------------------------------------------- automatic reshare
#
# Miro, 2026-10-12 (memory approval-policy): "whatever you think should be
# done should be automatically approved, but whatever is really urgent should
# only need human approval." linkedin.autoreshare_post reshares to Miro's
# profile only a post ATLAS itself just published on the Vault page
# (`autopublished`, published today), the way autopublish_post posts it:
#
#   * Vault only (AUTOPUBLISH_PAGES); LINKEDIN_AUTORESHARE on (default);
#     not paused (posting.autoreshare_paused, set by the rollback);
#   * the entry is published with its link, never reshared or clicked once
#     unverified (`_reshare_target`), autopublished, published today;
#   * one automatic reshare a page a day;
#   * inside the bank's posting hours, a signed-in session and a clean sweep,
#     no restriction alert -- the same checks as the post itself.
#
# Anything else is the Tier 2 linkedin.reshare_post card, as before. The
# rollback is honest: a reshare is not deleted (Tier 3); it pauses automatic
# resharing for the page, so the next one is a card again.

AUTORESHARE_ENV = "LINKEDIN_AUTORESHARE"
RESHARE_PAUSED_KEY = "autoreshare_paused"


def autoreshare_enabled() -> bool:
    return os.getenv(AUTORESHARE_ENV, "on").strip().lower() not in (
        "0", "off", "false", "no")


def autoreshare_owns(page: str) -> bool:
    """Whether today's autopublished post on `page` is reshared by the tick,
    not offered as a card by the daily signal."""
    return page in AUTOPUBLISH_PAGES and page in PAGES and autoreshare_enabled()


def autoreshare_candidate(state: dict[str, Any], page: str) -> dict | None:
    """Today's autopublished, unreshared post on `page`, or None. Pure."""
    entries, _posting, _ = bank(state, page)
    day = today()
    for e in entries:
        if (e.get("status") == "published" and e.get("autopublished")
                and e.get("activity_url") and e.get("reshared") is False
                and str(e.get("published") or "") == day):
            return e
    return None


def autoreshare_refusal(state: dict[str, Any], page: str, entry_id: str,
                        now: datetime | None = None
                        ) -> tuple[dict | None, str, str]:
    """(the entry that may be reshared now, or None; why; kind): 'ok';
    'wait' outside the posting hours; 'held' for every other refusal -- the
    Tier 2 reshare_post card."""
    now = now or datetime.now(UTC)
    if page not in AUTOPUBLISH_PAGES:
        return None, f"only the Vault page reshares itself, not {page!r}", "held"
    if page not in PAGES:
        return None, f"{page} is not an active page (LINKEDIN_PAGES_ACTIVE)", "held"
    if not autoreshare_enabled():
        return None, f"automatic resharing is off ({AUTORESHARE_ENV})", "held"
    entries, posting, _ = bank(state, page)
    if paused := posting.get(RESHARE_PAUSED_KEY):
        return None, (f"automatic resharing for {PAGES[page]['name']} is paused "
                      f"({paused.get('why') if isinstance(paused, dict) else paused})"
                      f" -- clear posting.{RESHARE_PAUSED_KEY} to resume"), "held"
    entry, why = _reshare_target(state, page, entry_id)
    if entry is None:
        return None, why, "held"
    day = today()
    if not entry.get("autopublished") or str(entry.get("published") or "") != day:
        return None, (f"{entry_id} is not a post ATLAS published on its own today; "
                      f"its reshare is yours"), "held"
    if any(e.get("autoreshared") and str(e.get("reshared_at") or "").startswith(day)
           for e in entries):
        return None, "an automatic reshare already went out today", "held"
    start, end = posting_hours(posting)
    hour = now.astimezone(ZoneInfo(HOURS_TZ)).hour
    if not start <= hour < end:
        return None, f"outside the posting hours ({start}-{end} New York)", "wait"
    if (why := _session_refusal(state, now)) is not None:
        return None, why, "held"
    if (why := _restriction_refusal(state, now)) is not None:
        return None, why, "held"
    return entry, "ok", "ok"


async def autoreshare_dry(page: str = "", entry_id: str = "") -> ActionResult:
    try:
        state = load()
    except Exception as exc:  # noqa: BLE001
        return ActionResult(False, f"cannot read the LinkedIn state: {exc}")
    entry, why, _kind = autoreshare_refusal(state, page, entry_id)
    if entry is None:
        return ActionResult(False, why, evidence={"page": page, "entry": entry_id})
    return ActionResult(True, f"would reshare {entry_id} from the "
                              f"{PAGES[page]['name']} page to Miro's profile on its "
                              f"own ({entry['activity_url']})",
                        evidence={"page": page, "entry": entry_id})


async def autoreshare(page: str = "", entry_id: str = "") -> ActionResult:
    """Tier AUTO, only past `autoreshare_refusal`, checked again under the
    browser lock and the state-file lock."""
    async with lock(), file_lock():
        try:
            state = load()
        except Exception as exc:  # noqa: BLE001
            return ActionResult(False, f"cannot read the LinkedIn state: {exc}")
        entry, why, _kind = autoreshare_refusal(state, page, entry_id)
        if entry is None:
            return ActionResult(False, why, evidence={"page": page, "entry": entry_id})
        made = await _reshare_entry(state, page, entry_id, entry, auto=True)
    if made.ok:
        made = replace(made, rollback_token=f"{page}:{entry_id}")
    return made


async def pause_autoreshare(token: str) -> ActionResult:
    """The honest rollback: a reshare is not deleted (deletion is Tier 3).
    Automatic resharing for the page pauses, so every next one is a card."""
    page, _, entry_id = (token or "").partition(":")
    if not page:
        return ActionResult(True, "nothing was reshared for this run; nothing to undo")
    if page not in AUTOPUBLISH_PAGES:
        return ActionResult(False, f"{page!r} never reshares itself; nothing to pause")
    async with file_lock():
        state = load()
        _entries, posting, holder = bank(state, page)
        posting[RESHARE_PAUSED_KEY] = {"at": stamp(), "entry": entry_id,
                                       "why": f"the rollback of {entry_id}'s reshare"}
        holder["posting"] = posting
        save(state)
    name = PAGES.get(page, {}).get("name", page)
    log_run([(f"autoreshare for {name} paused by the rollback of {entry_id}; the "
              f"reshare stays live (deletion is Tier 3)")], label="reshare")
    return ActionResult(
        True, f"{entry_id}'s reshare is still on Miro's profile -- it is not deleted "
              f"(deletion is permanently denied). Automatic resharing for {name} is "
              f"paused, so each next reshare is a card again; clear "
              f"posting.{RESHARE_PAUSED_KEY} in the state file to resume.",
        evidence={"page": page, "entry": entry_id, "paused": True})


def _unquote(s: str) -> str:
    from urllib.parse import unquote
    return unquote(s)


# ----------------------------------------------------------------- automatic
#
# Miro, 2026-10-11 (memory approval-policy). See the module docstring.

AUTOPUBLISH_PAGES = ("guardian",)        # Vault only; never FlyCheap, whatever PAGES says
AUTOPUBLISH_ENV = "LINKEDIN_AUTOPUBLISH"
HOURS_DEFAULT = (9, 18)                  # local hours [start, end) in HOURS_TZ
HOURS_TZ = "America/New_York"
PAUSED_KEY = "autopublish_paused"


def autopublish_enabled() -> bool:
    """LINKEDIN_AUTOPUBLISH: on by default; off sends every post back to a card."""
    return os.getenv(AUTOPUBLISH_ENV, "on").strip().lower() not in (
        "0", "off", "false", "no")


def autopublish_owns(page: str) -> bool:
    """Whether the automatic path, not the daily signal, offers this page's post."""
    return page in AUTOPUBLISH_PAGES and page in PAGES and autopublish_enabled()


def posting_hours(posting: dict[str, Any]) -> tuple[int, int]:
    """The bank's `posting.hours` ([start, end), New York), or the default."""
    raw = posting.get("hours")
    if isinstance(raw, list | tuple) and len(raw) == 2:
        try:
            start, end = int(raw[0]), int(raw[1])
        except (TypeError, ValueError):
            return HOURS_DEFAULT
        if 0 <= start < end <= 24:
            return start, end
    return HOURS_DEFAULT


def posting_day_over(state: dict[str, Any], page: str,
                     now: datetime | None = None) -> bool:
    """Whether today's posting hours for `page` have ended (New York)."""
    _entries, posting, _ = bank(state, page)
    _start, end = posting_hours(posting)
    hour = (now or datetime.now(UTC)).astimezone(ZoneInfo(HOURS_TZ)).hour
    return hour >= end


def _session_refusal(state: dict[str, Any], now: datetime) -> str | None:
    """Signed in, as far as can be told without opening a browser: the saved
    session exists, and the last sweep (which opens it) was clean and recent."""
    from . import _parse_when, browser
    if not browser.SESSION.exists():
        return f"no saved LinkedIn session at {browser.SESSION}"
    when = _parse_when(state.get("last_run"))
    if when is None:
        return "the last sweep's time is unknown, so the session is not known to work"
    hours = (now - when).total_seconds() / 3600
    interval = float(os.getenv("LINKEDIN_AGENT_INTERVAL_HOURS", "12"))
    if hours > interval * 2:
        return f"the last sweep was {hours:.0f}h ago, so the session is not known to work"
    outcome = str((state.get("last_run_result") or {}).get("outcome") or "")
    if outcome != "ok":
        return f"the last sweep did not end clean ({outcome[:120] or 'no outcome'})"
    return None


def _restriction_refusal(state: dict[str, Any], now: datetime) -> str | None:
    from . import ALERT_WINDOW, _parse_when, _restriction_candidates
    recent = []
    for alert in state.get("alerts") or []:
        if not isinstance(alert, dict):
            continue
        when = _parse_when(alert.get("when"))
        if when is not None and when >= now - ALERT_WINDOW:
            recent.append(alert)
    found = _restriction_candidates(recent)
    if found:
        alert, word = found[-1]
        return (f"an alert from {alert.get('when')} mentions {word!r}, so the account "
                "may be restricted")
    return None


def autopublish_refusal(state: dict[str, Any], page: str, entry_id: str = "",
                        now: datetime | None = None
                        ) -> tuple[dict | None, str, str]:
    """(the entry that may post itself now, or None; why; and what kind of
    no it is): 'ok'; 'none' when there is nothing to post today (the gate
    is shut: nothing approved, not a posting day, posted today); 'wait'
    outside the posting hours; 'held' for every other refusal -- that one is
    the Tier 2 card."""
    now = now or datetime.now(UTC)
    if page not in AUTOPUBLISH_PAGES:
        return None, f"only the Vault page posts itself, not {page!r}", "held"
    if page not in PAGES:
        return None, f"{page} is not an active page (LINKEDIN_PAGES_ACTIVE)", "held"
    if not autopublish_enabled():
        return None, f"automatic posting is off ({AUTOPUBLISH_ENV})", "held"
    entries, posting, _ = bank(state, page)
    if paused := posting.get(PAUSED_KEY):
        return None, (f"automatic posting for {PAGES[page]['name']} is paused "
                      f"({paused.get('why') if isinstance(paused, dict) else paused})"
                      f" -- clear posting.{PAUSED_KEY} to resume"), "held"
    entry, why = gate(state, page)
    if entry is None:
        return None, why, "none"
    if entry_id and str(entry.get("id")) != str(entry_id):
        return None, f"{entry_id} is not the next approved entry ({entry.get('id')} is)", \
            "held"
    approved_sha = entry.get("approved_sha256")
    if approved_sha and approved_sha != payload_sha256(entry):
        return None, (f"{entry.get('id')} changed after it was approved; it is not the "
                      "post you approved"), "held"
    day = today()
    if any(str(e.get("published") or "") == day
           or str(e.get("published_attempt_at") or "").startswith(day) for e in entries):
        return None, "a post already went to this page today", "none"
    start, end = posting_hours(posting)
    hour = now.astimezone(ZoneInfo(HOURS_TZ)).hour
    if not start <= hour < end:
        return None, f"outside the posting hours ({start}-{end} New York)", "wait"
    if (why := _session_refusal(state, now)) is not None:
        return None, why, "held"
    if (why := _restriction_refusal(state, now)) is not None:
        return None, why, "held"
    return entry, "ok", "ok"


async def autopublish_dry(page: str = "", entry_id: str = "") -> ActionResult:
    try:
        state = load()
    except Exception as exc:  # noqa: BLE001
        return ActionResult(False, f"cannot read the LinkedIn state: {exc}")
    entry, why, _kind = autopublish_refusal(state, page, entry_id)
    if entry is None:
        return ActionResult(False, why, evidence={"page": page, "entry": entry_id})
    text = str(entry.get("text") or "")
    return ActionResult(
        True,
        f"would publish {entry.get('id')} to the {PAGES[page]['name']} page on its own "
        f"({len(text)} chars); the reshare to Miro's profile stays a card:\n"
        + text[:400],
        evidence={"page": page, "entry": entry.get("id"), "chars": len(text)})


async def autopublish(page: str = "", entry_id: str = "") -> ActionResult:
    """Tier AUTO, only past `autopublish_refusal`, checked again under the
    browser lock and the state-file lock so nothing moves between the check
    and the Post click."""
    async with lock(), file_lock():
        try:
            state = load()
        except Exception as exc:  # noqa: BLE001
            return ActionResult(False, f"cannot read the LinkedIn state: {exc}")
        entry, why, _kind = autopublish_refusal(state, page, entry_id)
        if entry is None:
            return ActionResult(False, why, evidence={"page": page, "entry": entry_id})
        made = await _publish(page, str(entry.get("id")), auto=True)
    if made.ok:
        made = replace(made, rollback_token=f"{page}:{entry.get('id')}")
    return made


async def pause_autopublish(token: str) -> ActionResult:
    """The honest rollback. A published post is not unpublished -- that is
    deletion, and deletion is Tier 3 -- and this does not pretend to: it
    pauses this page's automatic posting and records why, so every next
    post is a card again until the pause is cleared by hand."""
    page, _, entry_id = (token or "").partition(":")
    if not page:
        return ActionResult(True, "nothing was posted for this run; nothing to roll back")
    if page not in AUTOPUBLISH_PAGES:
        return ActionResult(False, f"{page!r} never posts itself; nothing to pause")
    async with file_lock():
        state = load()
        _entries, posting, holder = bank(state, page)
        posting[PAUSED_KEY] = {"at": stamp(), "entry": entry_id,
                               "why": f"the rollback of {entry_id}"}
        holder["posting"] = posting
        save(state)
    name = PAGES.get(page, {}).get("name", page)
    log_run([(f"autopublish for {name} paused by the rollback of {entry_id}; "
              f"{entry_id} stays live (deletion is Tier 3)")], label="publish")
    return ActionResult(
        True, f"{entry_id} is still live on the {name} page -- a published post is not "
              "deleted (deletion is permanently denied). Automatic posting for "
              f"{name} is paused, so each next post is a card again; clear "
              f"posting.{PAUSED_KEY} in the state file to resume.",
        evidence={"page": page, "entry": entry_id, "unpublished": False,
                  "paused": True})


# ----------------------------------------------- approving drafts on its own
#
# Miro, 2026-10-12 (memory approval-policy): "whatever you think should be
# done should be automatically approved, but whatever is really urgent should
# only need human approval." linkedin.approve_draft is Tier 1: a draft is
# marked approved in ATLAS's own bank, and the rollback flips it back. That
# only stays a bank change while a person still reads the post before it is
# public -- the page's Tier 2 linkedin.publish_post card. So, unless an
# approved card is running it:
#
#   * never a page that publishes itself (AUTOPUBLISH_PAGES, Vault): there
#     an approved entry goes out with no further read, so its drafts stay
#     Miro's card -- the 2026-10-11 rule covered already-approved entries;
#   * LINKEDIN_AUTOAPPROVE (on by default): off, every draft is a card;
#   * at most AUTOAPPROVE_PER_DAY drafts per page per day.

AUTOAPPROVE_ENV = "LINKEDIN_AUTOAPPROVE"
AUTOAPPROVE_PER_DAY = 2


def autoapprove_enabled() -> bool:
    return os.getenv(AUTOAPPROVE_ENV, "on").strip().lower() not in (
        "0", "off", "false", "no")


def _answered_by_card() -> bool:
    from ...kernel import idempotency
    return (idempotency.current_run() or "").startswith("proposal:")


def autoapprove_refusal(state: dict[str, Any], page: str) -> str | None:
    """Why this page's draft is Miro's card rather than approved by ATLAS,
    or None. Never asked of an approved card: his tap is the answer."""
    if _answered_by_card():
        return None
    name = PAGES.get(page, {}).get("name", page)
    from . import HUMAN_APPROVAL_PAGES
    if page in HUMAN_APPROVAL_PAGES:
        return f"{name} drafts are yours to read and approve"
    if page in AUTOPUBLISH_PAGES:
        return (f"the {name} page publishes its approved posts by itself, so "
                f"its drafts are yours to read and approve")
    if not autoapprove_enabled():
        return f"automatic draft approval is off ({AUTOAPPROVE_ENV})"
    entries, _posting, _holder = bank(state, page)
    done = sum(1 for e in entries
               if e.get("auto_approved") and e.get("approved") == today())
    if done >= AUTOAPPROVE_PER_DAY:
        return (f"{done} {name} draft(s) already approved automatically today "
                f"(at most {AUTOAPPROVE_PER_DAY})")
    return None


def _claim_refusal(entry: dict[str, Any]) -> str | None:
    """Sage's ExampleTutor drafts pass the fact-backed claim gate again here;
    every other drafter's entry is unaffected."""
    try:
        from ..tutorseo.claims import entry_refusal
    except ImportError:               # the SEO drafter is not part of this build
        return None
    return entry_refusal(entry)


async def approve_draft_dry(page: str = "", entry_id: str = "",
                            text_sha256: str = "") -> ActionResult:
    # Deliberately does not check `text_sha256` against the draft: this runs
    # as `preview()`, before Miro decides, and as the executor's own
    # pre-approval dry run -- returning ok=False here on a mismatch would
    # stop `execute()` before `run()` ever gets called, and it is `run()`
    # that has to notice the change and get a fresh card started.
    try:
        state = load()
    except Exception as exc:  # noqa: BLE001
        return ActionResult(False, f"cannot read the LinkedIn state: {exc}")
    e = _entry(state, page, entry_id)
    if e is None:
        return ActionResult(False, f"no entry {entry_id} in the {page} bank")
    if e.get("status") != "draft":
        return ActionResult(False, f"{entry_id} is {e.get('status')}, not a draft")
    if (why := autoapprove_refusal(state, page)) is not None:
        return ActionResult(False, why)
    if (why := _claim_refusal(e)) is not None:
        return ActionResult(False, why)
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
    # Checked again under the lock: the dry run read the bank before it.
    if (why := autoapprove_refusal(state, page)) is not None:
        return ActionResult(False, why)
    if (why := _claim_refusal(e)) is not None:
        return ActionResult(False, why)
    if not _answered_by_card():
        e["auto_approved"] = True
    e["status"] = "approved"
    e["approved"] = today()
    # What was approved, so an automatic post can prove it is the same payload.
    e["approved_sha256"] = current
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
    e.pop("approved_sha256", None)
    e.pop("auto_approved", None)
    save(state)
    return ActionResult(True, f"{entry_id} is a draft again")


if __name__ == "__main__":
    import sys
    print(asyncio.run(publish_dry(sys.argv[1] if len(sys.argv) > 1 else "flycheap")))
