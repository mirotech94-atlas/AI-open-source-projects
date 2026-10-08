"""agency.mockup — a designed preview site for one lead (AGENCY.md §2.4).

**Showing up with their site already rebuilt is the pitch.** Until
2026-09-30 this asked GetLayers' MCP server for layout material at run time
and had `think()` write the whole page from it. It never called
`getlayers_start` or committed a Style, passed a compositions blob where
materialize wanted an asset id, and wrote the model's answer to disk as the
page unchecked -- so a public preview's body copy was the model describing
a GetLayers validation error.

Now the page is designed once, at design time, with GetLayers (one Style,
`ridgeline-style`, and its compositions -- `templates/getlayers.json`), and
checked in as `templates/`. This action:

1. picks the lead's trade family from its category (`sitetemplate.family_for`);
2. asks `think()` for six short copy fields as JSON -- the lead's details go
   in through `wrap_untrusted` (invariant 4: a Places listing is third-party
   text) -- and validates every field on its own (`sitetemplate.parse_copy`:
   length, no markup, no tool vocabulary, no claim or figure the business
   did not give), falling back field by field to code-written defaults;
3. renders the page in code, every value HTML-escaped (the model never
   writes a tag), and refuses it if `pagegate` says it is not a whole page
   about the business;
4. writes the page, the validated copy beside it (`<id>.copy.json`, what
   `agency.build_site` re-renders from) and the self-hosted font.

Nothing here calls GetLayers at run time. `AGENCY_GETLAYERS_LICENCE_OK`
still gates it -- the templates are GetLayers-derived client work, and
it is the switch Miro uses to pause site builds -- read fresh every call.

The three GetLayers tool names and `_find_tools` stay: `build.py`'s
one-product store page still uses them.

Screenshot is best-effort: only attempted when `playwright` is importable,
and a missing one is reported in the result text, not treated as a failure.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from pathlib import Path

from ...kernel.contracts import ActionResult
from ...kernel.llm import Message, Need, think
from ...kernel.permissions import wrap_untrusted
from . import _store, pagegate, sitetemplate
from ._mcp import McpClient
from .client import ClientData, read_client_file
from .sitekit import faq_list
from .sitetemplate import SiteCopy

MOCKUP_DIR = Path(os.getenv("AGENCY_MOCKUP_DIR", "data/agency/mockups"))

SEARCH_TOOL = "getlayers_search"
COMPOSITIONS_TOOL = "getlayers_compositions"
MATERIALIZE_TOOL = "getlayers_materialize"
_REQUIRED_TOOLS = (SEARCH_TOOL, COMPOSITIONS_TOOL, MATERIALIZE_TOOL)

_LICENCE_MISSING = (
    "GetLayers commercial licence not confirmed: set "
    "AGENCY_GETLAYERS_LICENCE_OK=true after checking it covers client work")


def _licence_ok() -> bool:
    return os.getenv("AGENCY_GETLAYERS_LICENCE_OK", "").strip().lower() == "true"


async def _find_tools(client: McpClient) -> tuple[str | None, str | None]:
    """Confirm the three tools the store page needs exist, by exact name,
    before spending a call on any of them. Returns the first missing tool's
    reason, or None when all three are present."""
    tools, reason = await client.list_tools()
    if reason is not None:
        return None, reason
    names = {t.get("name") for t in tools if isinstance(t, dict)}
    missing = [name for name in _REQUIRED_TOOLS if name not in names]
    if missing:
        seen = ", ".join(sorted(n for n in names if n)) or "none"
        return None, f"GetLayers MCP exposes no {missing[0]}; tools seen: {seen}"
    return None, None


def _write_mockup_files(html_path: Path, html: str, record: dict) -> Path:
    """Blocking (mkdir + write); always run through `asyncio.to_thread` —
    "async throughout" (CLAUDE.md) rules out a synchronous filesystem call
    on the event loop. The page, its validated copy beside it, and the
    self-hosted font and media so the page opens as designed. Returns the
    copy path."""
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html, encoding="utf-8")
    copy_path = sitetemplate.copy_record_path(html_path)
    copy_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    sitetemplate.copy_assets(html_path.parent, str(record.get("family") or ""),
                             record.get("category"))
    return copy_path


def _unlink_mockup_files(*paths: str | None) -> None:
    """Blocking; always run through `asyncio.to_thread`."""
    for path in paths:
        if path:
            Path(path).unlink(missing_ok=True)


async def _screenshot(html_path: Path) -> tuple[str | None, str]:
    """PNG at a phone's width (390x844, where the owner will look first),
    only when playwright is importable. Returns (path, note) — note is empty
    on success, and explains why there is no screenshot otherwise."""
    if importlib.util.find_spec("playwright") is None:
        return None, "no screenshot: playwright not installed"
    from playwright.async_api import async_playwright

    png_path = html_path.with_suffix(".png")
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            try:
                page = await browser.new_page(viewport={"width": 390, "height": 844},
                                              device_scale_factor=2)
                await page.goto(html_path.resolve().as_uri())
                await page.screenshot(path=str(png_path))
            finally:
                await browser.close()
    except Exception as exc:                            # noqa: BLE001
        return None, f"no screenshot: {exc}"
    return str(png_path), ""


async def write_copy(client: ClientData, family: str) -> tuple[SiteCopy, list[str]]:
    """Six short fields from `think()`, each validated, each falling back to
    its code-written default. A model that cannot be reached means the
    defaults, said so in the notes -- the page never waits on a model to be
    honest."""
    facts = wrap_untrusted(sitetemplate.copy_facts(client), "places")
    try:
        completion = await think(Need.DRAFT, sitetemplate.COPY_SYSTEM,
                                 [Message("user", facts)], max_tokens=1200)
    except Exception as exc:                            # noqa: BLE001
        return (sitetemplate.default_copy(client, family),
                [f"copy model unavailable ({type(exc).__name__}); code defaults used"])
    return sitetemplate.parse_copy(completion.text, client, family)


async def make_mockup(lead_id: int) -> ActionResult:
    if not _licence_ok():
        return ActionResult(False, _LICENCE_MISSING)

    lead = await _store.get_lead(lead_id)
    if lead is None:
        return ActionResult(False, f"no such lead: {lead_id}")

    # build imports this module at load, so this import waits until now
    from . import build
    raw_client, reason = await asyncio.to_thread(
        read_client_file, build.UPLOADS_DIR / str(lead_id))
    if reason is not None:
        return ActionResult(False, reason)
    client, form_action = await build._client_for(lead_id, lead, raw_client, None, None)

    family = sitetemplate.family_for(lead.get("category"))
    client = sitetemplate.themed(client, family)
    copy, notes = await write_copy(client, family)
    faqs = faq_list(client, list(copy.faqs), form=bool(form_action))
    html = sitetemplate.render_home(client, copy, family, faqs, form_action=form_action)
    problem = pagegate.page_problem(html)
    if problem is not None:
        return ActionResult(False, f"refused to write the mockup for lead {lead_id}: the "
                                   f"page is {problem}")

    html_path = MOCKUP_DIR / f"{lead_id}.html"
    record = sitetemplate.copy_record(copy, family, notes, client.category)
    copy_path = await asyncio.to_thread(_write_mockup_files, html_path, html, record)

    screenshot_path, screenshot_note = await _screenshot(html_path)

    layers = [f"template:{family}", f"style:{sitetemplate.STYLE_ID}"]
    previous_status = lead.get("status")
    mockup_id = await _store.insert_mockup(lead_id, str(html_path), screenshot_path, layers)
    await _store.set_lead_status(lead_id, "mocked")

    detail = (f"mockup for lead {lead_id} written to {html_path}: the {family} template "
              f"({sitetemplate.STYLE_ID}), copy {copy.source}")
    if notes:
        detail += f" ({len(notes)} field(s) fell back to defaults: {notes[0]})"
    if screenshot_note:
        detail += f"; {screenshot_note}"

    token = json.dumps({
        "lead_id": lead_id, "mockup_id": mockup_id,
        "previous_status": previous_status, "html_path": str(html_path),
        "copy_path": str(copy_path), "screenshot_path": screenshot_path,
    })
    return ActionResult(
        True, detail,
        evidence={"path": str(html_path), "screenshot": screenshot_path,
                  "family": family, "copy_source": copy.source, "copy_notes": notes},
        rollback_token=token)


async def make_mockup_dry(lead_id: int) -> ActionResult:
    if not _licence_ok():
        return ActionResult(False, _LICENCE_MISSING)
    lead = await _store.get_lead(lead_id)
    if lead is None:
        return ActionResult(False, f"no such lead: {lead_id}")
    family = sitetemplate.family_for(lead.get("category"))
    return ActionResult(
        True,
        f"would render the designed {family} template ({sitetemplate.STYLE_ID}) for "
        f"lead {lead_id}, with copy from think() checked field by field, to "
        f"{MOCKUP_DIR / f'{lead_id}.html'}")


async def make_mockup_rollback(token: str) -> ActionResult:
    if not token:
        return ActionResult(True, "nothing was written for this run; nothing to roll back")
    try:
        data = json.loads(token)
    except ValueError:
        return ActionResult(False, "rollback token is not valid JSON")

    mockup_id = data.get("mockup_id")
    if mockup_id is not None:
        await _store.delete_mockup(mockup_id)

    await asyncio.to_thread(
        _unlink_mockup_files, data.get("html_path"), data.get("copy_path"),
        data.get("screenshot_path"))

    lead_id = data.get("lead_id")
    previous_status = data.get("previous_status")
    if lead_id is not None and previous_status is not None:
        await _store.set_lead_status(lead_id, previous_status)

    return ActionResult(True, f"withdrew mockup {mockup_id} for lead {lead_id}")
