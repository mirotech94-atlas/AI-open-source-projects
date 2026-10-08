"""Rename a LinkedIn page and change its logo. Miro, 2026-09-27/28: "change
the LinkedIn pages' name from Guardian to Vault and the logo" (the Vault mark,
docs/brand/vault-logo-400.png in the Guardian repo, 400x400).

Tier 2: a page's public name and logo are its identity, so one card, one
approval. The rollback puts the previous name back and, when the old logo
could be saved before the change, the previous logo too.

The dry run never opens LinkedIn. It runs twice per decision (the card's
preview and the executor's pre-run check), and two extra browser sessions
per card are exactly what LinkedIn's automation detection watches for. It
checks what can be checked offline: the page, the name, the logo file.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import struct
from pathlib import Path

from ...kernel.contracts import ActionResult
from . import PAGES

BRAND_DIR = Path(os.getenv("LINKEDIN_BRAND_DIR", "/app/data/linkedin-agent/brand"))
MIN_LOGO_PX = 300
NAME_MAX = 100


def png_size(path: Path) -> tuple[int, int] | None:
    """(width, height) from a PNG header, or None when it is not a PNG."""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
    except OSError:
        return None
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    w, h = struct.unpack(">II", head[16:24])
    return int(w), int(h)


def logo_file(logo: str) -> tuple[Path | None, str]:
    """The logo inside BRAND_DIR, or None and why. Only a bare file name is
    accepted, so a parameter can never point the upload at another file."""
    if not logo:
        return None, ""
    if "/" in logo or "\\" in logo or logo.startswith("."):
        return None, f"logo must be a file name in {BRAND_DIR}, not a path"
    path = BRAND_DIR / logo
    size = png_size(path)
    if size is None:
        return None, f"{logo} is not a PNG in {BRAND_DIR}"
    w, h = size
    if w != h or w < MIN_LOGO_PX:
        return None, (f"{logo} is {w}x{h}; a page logo must be square and at "
                      f"least {MIN_LOGO_PX}px")
    return path, ""


def check(page: str, name: str, logo: str) -> str | None:
    if page not in PAGES:
        return f"no such page {page!r}; one of {', '.join(PAGES)}"
    name = name.strip()
    if not name and not logo:
        return "nothing to change: give a name, a logo, or both"
    if name and (len(name) > NAME_MAX or not re.search(r"\w", name)):
        return f"a page name is 1-{NAME_MAX} characters with a letter or digit"
    if logo:
        _path, why = logo_file(logo)
        if why:
            return why
    return None


async def update_identity_dry(page: str = "", name: str = "",
                              logo: str = "") -> ActionResult:
    if (why := check(page, name, logo)) is not None:
        return ActionResult(False, why)
    parts = []
    if name:
        parts.append(f"rename the {PAGES[page]['id']} page to {name.strip()!r}")
    if logo:
        w, _ = png_size(BRAND_DIR / logo) or (0, 0)
        parts.append(f"replace its logo with {logo} ({w}x{w})")
    return ActionResult(
        True, "would " + " and ".join(parts) + ", saving the current name and "
              "logo first so the rollback can put them back",
        evidence={"page": page, "name": name.strip(), "logo": logo})


def rollback_token(page: str, old_name: str, old_logo: str) -> str:
    return json.dumps({"page": page, "name": old_name, "logo": old_logo})


# Probed read-only on 2026-09-28 (screenshots probe-identity-*, probe2-*): the
# admin's "Edit page" link opens Page info with these fields; a changed field
# shows a top bar with "Discard edits" and "Save"; picking a logo swaps the
# preview in place, no crop step.
NAME_FIELD = "#organization-name-field"
LOGO_FIELD = "#organization-logo-field"


def _admin(page: str) -> str:
    return f"https://www.linkedin.com/company/{PAGES[page]['id']}/admin/dashboard/"


# After a saved edit LinkedIn offers "Share your page edits -- share your new
# changes in a post" with "No thanks" / "Start a post" (2026-09-28, #195: it
# covered the form, so the name was never typed). The answer is always "No
# thanks": "Start a post" would publish something nobody approved.
_SHARE_PROMPT = re.compile(r"share your (?:page edits|new changes)", re.IGNORECASE)
_NO_THANKS = re.compile(r"^(?:no thanks|not now|dismiss|skip)$", re.IGNORECASE)


async def _decline_share_prompt(tab) -> bool:
    """Close the share-your-edits prompt if it is up. True when it was."""
    from .browser import pause
    dialog = tab.locator("[role=dialog]:visible, [role=alertdialog]:visible")
    if not await dialog.count():
        return False
    text = (await dialog.first.inner_text()).strip()
    if not _SHARE_PROMPT.search(text):
        return False
    no = dialog.first.get_by_role("button", name=_NO_THANKS)
    if await no.count():
        await no.first.click()
    else:
        await tab.keyboard.press("Escape")
    await pause(tab, 1500, 2500)
    return True


async def _open_editor(tab, page: str) -> str:
    """Open Page info and return the current name."""
    from .browser import goto, pause
    await goto(tab, _admin(page))
    await _decline_share_prompt(tab)
    await tab.get_by_role("link", name=re.compile(r"^edit page$", re.IGNORECASE)).first.click()
    await pause(tab, 3000, 5000)
    await _decline_share_prompt(tab)
    field = tab.locator(NAME_FIELD)
    await field.wait_for(state="visible", timeout=20000)
    return (await field.input_value()).strip()


async def _save_current_logo(tab, ctx, page: str) -> str:
    """Download the logo shown now into BRAND_DIR; '' when it cannot be."""
    from .store import stamp
    try:
        src = await _logo_src(tab)
        if not src:
            return ""
        r = await ctx.request.get(src)
        body = await r.body()
        if not r.ok or (png_size_bytes(body) is None and body[:3] != b"\xff\xd8\xff"):
            return ""
        ext = "png" if body[:4] == b"\x89PNG" else "jpg"
        name = f"{page}-previous-logo-{re.sub(r'[^0-9]', '', stamp())[:14]}.{ext}"
        BRAND_DIR.mkdir(parents=True, exist_ok=True)
        (BRAND_DIR / name).write_bytes(body)
        return name
    except Exception:                                   # noqa: BLE001
        return ""


def png_size_bytes(body: bytes) -> tuple[int, int] | None:
    if len(body) < 24 or body[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    w, h = struct.unpack(">II", body[16:24])
    return int(w), int(h)


_SAVE = re.compile(r"^save$", re.IGNORECASE)
_CONFIRM = re.compile(r"^(save|confirm|yes|continue|change|update|done|apply)\b",
                      re.IGNORECASE)


async def _toasts(tab) -> list[str]:
    try:
        return await tab.evaluate(
            """() => [...document.querySelectorAll(
                   '.artdeco-toast-item, [role=alert], [data-test-artdeco-toast-item-type]')]
                   .map(e => (e.innerText || '').trim()).filter(Boolean).slice(0, 4)""")
    except Exception:                                   # noqa: BLE001
        return []


async def _save_step(tab, label: str, change, heard: list[str]) -> str | None:
    """Make one change, press Save, accept a confirmation if LinkedIn asks.
    None when the form is saved; else what LinkedIn showed. #188
    (2026-09-28) read 'Guardian' after Save with no trace of why, so every
    step now leaves a screenshot, the toasts, and any failed API answer."""
    from .browser import pause, snapshot
    heard.clear()
    await _decline_share_prompt(tab)
    await change()
    await pause(tab, 2500, 4000)
    save = tab.get_by_role("button", name=_SAVE)
    if not await save.count():
        shot = await snapshot(tab, f"identity-{label}-no-save")
        return f"{label}: no Save button appeared after the change ({shot})"
    await save.first.click()
    await pause(tab, 2500, 4000)
    # The share prompt means the save went through; decline it and go on.
    await _decline_share_prompt(tab)
    dialog = tab.locator("[role=dialog]:visible, [role=alertdialog]:visible")
    if await dialog.count():
        text = (await dialog.first.inner_text()).strip()[:200]
        ok = dialog.first.get_by_role("button", name=_CONFIRM)
        if await ok.count():
            await ok.last.click()
            await pause(tab, 3000, 5000)
        else:
            shot = await snapshot(tab, f"identity-{label}-dialog")
            return f"{label}: LinkedIn asked {text!r} with no confirm button ({shot})"
    toasts = await _toasts(tab)
    shot = await snapshot(tab, f"identity-{label}-after-save")
    held = await tab.get_by_role("button", name=_SAVE).count()
    bad = [h for h in heard if not h.startswith("2")]
    if held or bad or any(re.search(r"error|wrong|couldn|can.t|unable|try again",
                                    t, re.IGNORECASE) for t in toasts):
        return (f"{label}: not saved -- toasts {toasts or 'none'}; failed API "
                f"answers {bad[:3] or 'none'}; Save still showing: {bool(held)} "
                f"({shot})")
    return None


async def _logo_src(tab) -> str:
    try:
        return await tab.evaluate(
            """() => { const i = [...document.querySelectorAll('img')]
                         .find(i => /company-logo/.test(i.src) && i.offsetParent);
                       return i ? i.src : ''; }""")
    except Exception:                                   # noqa: BLE001
        return ""


async def _run(page: str, name: str, logo_path: Path | None,
               *, keep_old: bool) -> tuple[ActionResult, str, str]:
    from .browser import NoSession, browser, keep_session, lock
    async with lock():
        try:
            pw, b, ctx = await browser()
        except NoSession as exc:
            return ActionResult(False, str(exc)), "", ""
        old_name = old_logo = ""
        why: str | None = None
        done: list[str] = []
        heard: list[str] = []

        def on_response(r) -> None:
            if "/voyager/api/" in r.url and r.request.method in ("POST", "PUT",
                                                                 "PATCH"):
                heard.append(f"{r.status} {r.request.method} "
                             f"{r.url.split('?')[0].rsplit('/', 2)[-2:]}")
        try:
            tab = await ctx.new_page()
            tab.on("response", on_response)
            # Reading back navigates away; an unsaved form raises a
            # "leave page?" prompt that would abort the navigation. Accept
            # it -- the read-back then shows what LinkedIn really kept.
            tab.on("dialog", lambda d: asyncio.ensure_future(d.accept()))
            old_name = await _open_editor(tab, page)
            before_logo = await _logo_src(tab)
            if keep_old and logo_path is not None:
                old_logo = await _save_current_logo(tab, ctx, page)
            if logo_path is not None:
                path = logo_path

                async def pick() -> None:
                    await tab.locator(LOGO_FIELD).set_input_files(str(path))
                why = await _save_step(tab, "logo", pick, heard)
                if why is None:
                    await _open_editor(tab, page)
                    if await _logo_src(tab) in ("", before_logo):
                        why = "logo: after Save the page still shows the old logo"
                    else:
                        done.append("logo")
            if why is None and name:
                async def fill() -> None:
                    await tab.locator(NAME_FIELD).fill(name)
                why = await _save_step(tab, "name", fill, heard)
                if why is None:
                    now = await _open_editor(tab, page)
                    if now != name:
                        why = (f"name: after Save the page name reads {now!r}, "
                               f"not {name!r}; API answers {heard[:3] or 'none'}")
                    else:
                        done.append("name")
            await keep_session(ctx)
        finally:
            await ctx.close()
            await b.close()
            await pw.stop()
    if why is not None:
        partial = f" ({' and '.join(done)} did change)" if done else ""
        return ActionResult(False, why + partial), old_name, old_logo
    return ActionResult(True, ""), old_name, old_logo


async def update_identity(page: str = "", name: str = "",
                          logo: str = "") -> ActionResult:
    if (why := check(page, name, logo)) is not None:
        return ActionResult(False, why)
    logo_path, _ = logo_file(logo)
    name = name.strip()
    done, old_name, old_logo = await _run(page, name, logo_path, keep_old=True)
    if not done.ok:
        return ActionResult(False, f"the {PAGES[page]['id']} page was not changed "
                                   f"as asked: {done.detail}")
    said = []
    if name:
        said.append(f"renamed from {old_name!r} to {name!r} (read back after Save)")
    if logo:
        said.append(f"logo replaced with {logo}"
                    + ("" if old_logo else "; the previous logo could not be "
                       "saved, so a rollback restores the name only"))
    from .store import log_run
    log_run([f"page {PAGES[page]['id']}: " + "; ".join(said)], label="identity")
    return ActionResult(True, f"LinkedIn page {PAGES[page]['id']}: " + "; ".join(said),
                        evidence={"page": page, "old_name": old_name,
                                  "old_logo": old_logo, "name": name, "logo": logo},
                        rollback_token=rollback_token(page, old_name, old_logo))


async def restore_identity(token: str) -> ActionResult:
    """Put the previous name back, and the previous logo when it was saved."""
    try:
        t = json.loads(token)
        page, name, logo = str(t["page"]), str(t.get("name") or ""), str(t.get("logo") or "")
    except (ValueError, KeyError, TypeError):
        return ActionResult(False, "unreadable rollback token")
    if page not in PAGES:
        return ActionResult(False, f"no such page {page!r}")
    logo_path = BRAND_DIR / logo if logo and "/" not in logo else None
    if logo_path is not None and not logo_path.is_file():
        logo_path = None
    done, _, _ = await _run(page, name, logo_path, keep_old=False)
    if not done.ok:
        return ActionResult(False, f"the rollback did not complete: {done.detail}")
    return ActionResult(True, f"LinkedIn page {PAGES[page]['id']} set back to {name!r}"
                              + (" with its previous logo" if logo_path else ""))
