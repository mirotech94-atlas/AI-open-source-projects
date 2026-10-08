"""The launch basics every client site ships with.

2026-09-28 (the "10 things before launching your site" reel): robots.txt, a
sitemap when the site's own address is known, a plain privacy page, and a
custom 404 that points back home. 2026-09-29 (reel DdH5iyOzkZH, "20 things
every site must have"): the rest of the list — terms, a contact page, a
thank-you page behind the enquiry form, the share image, and the home-page
pieces `sitekit.enhance_index` adds — plus the launch check that reads all
of it back (`launchcheck.py`).

Pure text built from the client's own details (`client.ClientData`).
Nothing here invents a claim, a price or a policy the business didn't give:
the privacy page says only what the built site actually does (analytics on
or off, a form or none) and who to contact.

**Never over a client-edited file.** `write_launch_files` writes a file only
when it is absent, or when it is byte-for-byte what ATLAS wrote last time
(the sha recorded in the provenance file beside the site). A file someone
changed by hand — or one a mockup brought with it — is left alone, and the
launch check reports whatever state it is in.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from . import callme, sitechat, sitekit
from .client import ClientData, Faq, origin
from .launchcheck import provenance_path, read_provenance
from .ogimage import share_image


def launch_files(name: str, website: str | None, phone: str | None,
                 email: str | None, *, client: ClientData | None = None,
                 analytics: sitekit.Analytics | None = None,
                 form_action: str | None = None) -> dict[str, str]:
    """Filename -> content for the pages every site carries besides its home
    page. The four positional arguments are the lead's own details, used
    when no fuller `client` is given."""
    if client is None:
        client = ClientData(name=name or "This business", website=origin(website),
                            phone=phone, email=email)
    files: dict[str, str] = {
        "robots.txt": sitekit.robots_txt(client),
        sitekit.PRIVACY: sitekit.privacy_page(client, analytics, form_action),
        sitekit.TERMS: sitekit.terms_page(client, analytics),
        sitekit.CONTACT: sitekit.contact_page(client, analytics, form_action),
        sitekit.NOT_FOUND: sitekit.not_found_page(client, analytics),
    }
    if form_action:
        files[sitekit.THANKS] = sitekit.thanks_page(client, analytics)
    sitemap = sitekit.sitemap_xml(
        client, [sitekit.INDEX, sitekit.CONTACT, sitekit.PRIVACY, sitekit.TERMS])
    if sitemap:
        files["sitemap.xml"] = sitemap
    llms = sitekit.llms_txt(client)
    if llms:
        files[sitekit.LLMS_TXT] = llms
    return files


def sha(data: str | bytes) -> str:
    raw = data.encode() if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()


# ------------------------------------------------ the 2026-09-28 generator
# Sites built between 1fcb869 and the 20-item change got robots.txt,
# sitemap.xml, privacy.html and 404.html from the generator below, with no
# provenance record. Without recognising them, a rebuild would treat them as
# someone else's forever -- and they lack the meta description and
# breadcrumbs the launch check now requires, so the site could never deploy
# again. A file is ATLAS's if its bytes are exactly what this produced for
# the same lead (the sitemap compared with its <lastmod> dates taken out).

_LASTMOD = re.compile(rb"<lastmod>[^<]*</lastmod>")


def _legacy_page(title: str, body: str) -> str:
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{title}</title><meta name=\"robots\" content=\"noindex\">"
            "<style>body{font-family:system-ui,sans-serif;max-width:40rem;margin:4rem auto;"
            "padding:0 1rem;line-height:1.6;color:#1b2430}a{color:#0f6e8c}</style>"
            f"</head><body>{body}</body></html>\n")


def legacy_launch_files(name: str, website: str | None, phone: str | None,
                        email: str | None) -> dict[str, str]:
    """What `launch_files` produced on 2026-09-28, byte for byte."""
    biz = escape(name or "This business")
    contact = " or ".join(escape(c) for c in (phone, email) if c) or \
        "the details on our home page"
    site = origin(website)
    files: dict[str, str] = {}
    robots = "User-agent: *\nAllow: /\n"
    if site:
        robots += f"Sitemap: {site}/sitemap.xml\n"
        files["sitemap.xml"] = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"  <url><loc>{site}/</loc><lastmod>x</lastmod></url>\n"
            f"  <url><loc>{site}/privacy.html</loc><lastmod>x</lastmod></url>\n"
            "</urlset>\n")
    files["robots.txt"] = robots
    files["privacy.html"] = _legacy_page(
        f"Privacy | {biz}",
        f"<h1>Privacy</h1><p>{biz} runs this website to tell you about our business.</p>"
        "<p>This site does not use tracking cookies or analytics, and it has no forms "
        "that store what you type. If you phone or email us, we use what you send only "
        "to answer you, and we don't sell or share it.</p>"
        f"<p>Questions about your information: contact us at {contact}.</p>"
        '<p><a href="/">Back to the home page</a></p>')
    files["404.html"] = _legacy_page(
        f"Page not found | {biz}",
        "<h1>That page isn't here</h1><p>The link may be old or mistyped.</p>"
        f'<p><a href="/">Go to the {biz} home page</a>, or contact us at {contact}.</p>')
    return files


def _comparable(filename: str, data: bytes) -> str:
    if filename == "sitemap.xml":
        data = _LASTMOD.sub(b"", data)
    return sha(data)


def legacy_digests(lead: dict) -> dict[str, str]:
    files = legacy_launch_files(lead.get("name") or "", lead.get("website"),
                                lead.get("phone"), lead.get("email"))
    return {name: _comparable(name, content.encode()) for name, content in files.items()}


def write_launch_files(site_dir: Path, files: dict[str, str | bytes],
                       previous: dict[str, str] | None = None,
                       legacy: dict[str, str] | None = None) -> list[str]:
    """Write each file into `site_dir`, never over one someone else made:
    only a missing file, one whose bytes still match the sha ATLAS
    recorded when it last wrote it (`previous`), or -- with no record at
    all -- one identical to what the 2026-09-28 generator wrote (`legacy`)
    is (re)written. Returns the names written. Blocking; always run through
    `asyncio.to_thread`."""
    previous = previous or {}
    legacy = legacy or {}
    written = []
    for filename, content in files.items():
        target = site_dir / filename
        if target.exists():
            try:
                data = target.read_bytes()
            except OSError:
                continue
            ours = previous.get(filename) == sha(data) or (
                filename not in previous and filename in legacy
                and legacy[filename] == _comparable(filename, data))
            if not ours:
                continue
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content)
        written.append(filename)
    return written


@dataclass(frozen=True, slots=True)
class Finished:
    written: list[str]
    kept: list[str]              # existing files left alone (edited, or not ours)
    faqs: int


def finish_site(site_dir: Path, client: ClientData, *,
                analytics: sitekit.Analytics | None, form_action: str | None,
                faqs: list[Faq] | None = None, colours_given: bool = True,
                legacy_lead: dict | None = None) -> Finished:
    """Everything after the home page is on disk: enhance it, add the other
    pages and images, and record what ATLAS wrote (and what the client
    supplied) beside the site for the next rebuild and the launch check.
    Blocking; always run through `asyncio.to_thread`."""
    index_path = site_dir / sitekit.INDEX
    index = index_path.read_text(errors="replace")
    if not colours_given:
        hint = sitekit.colours_from_css(index)
        if hint:
            client = replace(client, colours={**client.colours, **hint})
    if faqs is None:
        faqs = sitekit.faq_list(client, form=bool(form_action))

    assets: dict[str, str | bytes] = {}
    team_src = None
    if client.team_photo is not None:
        team_src = "team" + client.team_photo.suffix.lower()
        assets[team_src] = client.team_photo.read_bytes()
    if client.og_image is not None:
        image_name = "og-image" + client.og_image.suffix.lower()
        assets[image_name] = client.og_image.read_bytes()
    else:
        image_name = sitekit.SHARE_IMAGE
        assets[image_name] = share_image(client.name, client.tagline, client.colours)

    from .sitebooking import path_for, reservations
    index_path.write_text(sitekit.enhance_index(
        index, client, analytics=analytics, faqs=faqs, team_src=team_src,
        image_name=image_name, reservations=reservations(client, form_action)))

    files: dict[str, str | bytes] = {
        **launch_files(client.name, client.website, client.phone, client.email,
                       client=client, analytics=analytics, form_action=form_action),
        **assets}
    if client.chat:
        files[sitekit.CHAT_WIDGET] = sitechat.widget_js(site_dir.name, client)
    if callme.wanted(client):
        files[callme.WIDGET] = callme.widget_js(site_dir.name, client)
    before = read_provenance(site_dir)
    previous = (before or {}).get("files")
    previous = previous if isinstance(previous, dict) else {}
    if not client.chat:
        _drop_our_widget(site_dir, previous)
    if not callme.wanted(client):
        _drop_our_widget(site_dir, previous, callme.WIDGET)
    legacy = legacy_digests(legacy_lead) if before is None and legacy_lead else None
    written = write_launch_files(site_dir, files, previous, legacy)
    recorded = {name: sha(files[name]) for name in written}
    recorded.update({name: digest for name, digest in previous.items()
                     if name not in recorded and name in files})
    kept = [name for name in files if name not in written]
    record: dict[str, object] = {
        "built_at": datetime.now(UTC).isoformat(),
        "website": client.website,
        "files": recorded,
        "client_reviews": [r.text for r in client.reviews],
        "case_studies": [c.title for c in client.case_studies],
        "team_photo": team_src,
        "form_action": form_action,
        "problems": list(client.problems),
    }
    if client.theme:
        # A designed template: which one, and whether it is still a preview
        # (no package bought), which launch item 22 holds the page to.
        record["theme"] = client.theme
        record["preview"] = client.package is None
        record["booking"] = path_for(client, form_action)
    if client.chat:
        # What the launch check's chat item reads: a $500/$300 package site
        # must carry the assistant and have an endpoint that serves it.
        record["chat"] = {"package": client.package, "widget": sitekit.CHAT_WIDGET}
    provenance_path(site_dir).write_text(json.dumps(record, indent=2))
    return Finished(written, kept, len(faqs))


def _drop_our_widget(site_dir: Path, previous: dict[str, str],
                     name: str = sitekit.CHAT_WIDGET) -> None:
    """A site that is no longer a chat package (or, for the call-me form, no
    longer a preview) loses the widget file ATLAS wrote -- only when it is
    byte-for-byte ours, like every launch file."""
    target = site_dir / name
    digest = previous.pop(name, None)
    try:
        if digest and target.is_file() and sha(target.read_bytes()) == digest:
            target.unlink()
    except OSError:
        pass
