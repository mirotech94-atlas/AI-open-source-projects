"""agency.contacts -- the email address a business publishes on its own site.

`agency.find_email` (Tier AUTO: a few polite GETs at a public page, rows on
ATLAS's own table). `draft_outreach` had nowhere to send: every draft was
written with no `to_address`, so no `send_outreach` card could ever pass its
guard. This finds the address a lead PUBLISHED, and only that:

  * **Where it looks.** The lead's own website (the Places `website`): the
    home page, then at most `MAX_PAGES - 1` pages it links to whose address or
    link text reads as contact / about / team, same site only (``www.`` or
    not), every redirect hop checked public (`_netguard.get_public`) and
    still on that site. robots.txt is honoured for our User-Agent; a
    robots.txt that cannot be read for a reason other than "there is none"
    (4xx) means nothing is fetched. `FETCH_TIMEOUT` per request, the first
    `MAX_BYTES` of each page read.
  * **What counts.** `mailto:` links, addresses written in the page text
    (HTML entities decoded first), and Cloudflare's email-protection
    encoding, which is the published address XOR-ed with a key in the same
    attribute. Each is checked by `plausible`: a real shape, not an image
    file name, not a platform's own address (Sentry, Wix, Squarespace,
    GoDaddy...), not a placeholder (you@example.com, name@domain.com, an
    email@ / your@ local part), not a no-reply box. A same-domain address is
    preferred over, say, a Gmail one, and both are kept with where they were
    seen.
  * **What never counts.** A guess. `info@<their domain>` that the page does
    not say is never written, and neither is anything a model wrote.
    `agency.verify` stores no email (its question asks for none), so there
    is nothing of Perplexity's to take in either.

Every address is stored with its provenance (`agency_lead_emails`: the URL it
was read on, the time, same-domain or not). Page text is untrusted
(invariant 4); nothing here puts it in a prompt, and the address itself only
ever lands in an email's To: line, after Miro approves the send.
"""
from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from ...kernel.contracts import ActionResult
from ...kernel.state import execute_sql, fetch_all, fetch_one
from .._http import explain

log = logging.getLogger("atlas.agency.contacts")

USER_AGENT = "ATLAS-contact/1.0 (+https://agency.storefront.test/)"
FETCH_TIMEOUT = 8.0
MAX_PAGES = 4
MAX_BYTES = 1_500_000
MAX_ADDRESSES = 5

_EMAIL = re.compile(
    r"(?<![\w.+-])([a-z0-9](?:[a-z0-9._%+-]{0,62}[a-z0-9])?@"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24})(?![\w-])",
    re.IGNORECASE)
_CONTACTISH = re.compile(r"contact|about|team|staff|reach|info|get-in-touch|location",
                         re.IGNORECASE)

# Files whose names look like addresses: logo@2x.png, hero@3x.webp.
_FILE_TAIL = re.compile(
    r"\.(?:png|jpe?g|gif|svg|webp|avif|ico|bmp|css|js|json|woff2?|ttf|mp4|webm|pdf)$",
    re.IGNORECASE)
# Platforms that put their own addresses in client pages.
_PLATFORM_DOMAINS = (
    "sentry.io", "sentry-next.wixpress.com", "wixpress.com", "wix.com",
    "squarespace.com", "godaddy.com", "secureserver.net", "weebly.com",
    "wordpress.com", "wordpress.org", "w3.org", "schema.org", "sentry.wixpress.com",
    "cloudflare.com", "shopify.com", "googlegroups.com", "domain.com", "email.com",
    "yourdomain.com", "yoursite.com", "mysite.com", "website.com", "company.com",
    "sitename.com", "yourcompany.com", "mail.com.invalid")
_RESERVED_TLDS = ("example", "test", "invalid", "localhost", "local")
_PLACEHOLDER_LOCALS = frozenset({
    "you", "your", "yourname", "your.name", "name", "email", "user", "username",
    "someone", "somebody", "john", "johndoe", "john.doe", "jane", "janedoe",
    "jane.doe", "firstname", "first.last", "firstname.lastname", "test", "example",
    "sample", "demo", "xxx", "abc", "me", "address", "mail"})
_NO_REPLY = re.compile(r"^(?:no-?reply|do-?not-?reply|mailer-daemon|postmaster|bounce)",
                       re.IGNORECASE)
_HEX_LOCAL = re.compile(r"^[0-9a-f]{16,}$", re.IGNORECASE)   # Sentry DSN keys


def site_key(host: str) -> str:
    host = (host or "").lower().rstrip(".")
    return host.removeprefix("www.")


def plausible(address: str) -> bool:
    """A published business address, not a file name, a platform's own
    address, or a placeholder."""
    address = (address or "").strip().lower()
    if len(address) > 254 or address.count("@") != 1:
        return False
    local, _, domain = address.partition("@")
    if not local or not domain or not _EMAIL.fullmatch(address):
        return False
    if _FILE_TAIL.search(address):
        return False
    if domain.rsplit(".", 1)[-1] in _RESERVED_TLDS:
        return False
    labels = domain.split(".")
    if "example" in labels:
        return False
    if any(domain == d or domain.endswith("." + d) for d in _PLATFORM_DOMAINS):
        return False
    if local in _PLACEHOLDER_LOCALS or _NO_REPLY.match(local) or _HEX_LOCAL.match(local):
        return False
    return "sentry" not in domain


def _cfemail(encoded: str) -> str | None:
    """Cloudflare's `data-cfemail`: the first byte is the key, the rest the
    address XOR-ed with it. Deterministic -- the published address, decoded."""
    try:
        data = bytes.fromhex(encoded)
    except ValueError:
        return None
    if len(data) < 4:
        return None
    key = data[0]
    try:
        return bytes(b ^ key for b in data[1:]).decode("ascii")
    except UnicodeDecodeError:
        return None


class _Page(HTMLParser):
    """mailto: hrefs, Cloudflare-protected addresses, the visible text, and
    the links out (with their text)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.mailtos: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.text: list[str] = []
        self._href: str | None = None
        self._label: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k.lower(): (v or "") for k, v in attrs}
        if tag in ("script", "style", "noscript", "template"):
            self._skip += 1
        if values.get("data-cfemail"):
            decoded = _cfemail(values["data-cfemail"])
            if decoded:
                self.mailtos.append(decoded)
        href = values.get("href", "")
        if href.lower().startswith("/cdn-cgi/l/email-protection#"):
            decoded = _cfemail(href.split("#", 1)[1])
            if decoded:
                self.mailtos.append(decoded)
        if tag == "a" and href:
            if href.lower().startswith("mailto:"):
                target = href[7:].split("?", 1)[0]
                self.mailtos.extend(p for p in target.split(",") if p)
            else:
                self._href, self._label = href, []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript", "template") and self._skip:
            self._skip -= 1
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join(self._label)))
            self._href = None

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        self.text.append(data)
        if self._href is not None:
            self._label.append(data)


@dataclass(frozen=True, slots=True)
class Found:
    address: str
    url: str
    same_domain: bool


def extract(page_html: str, url: str) -> tuple[list[str], list[tuple[str, str]]]:
    """(addresses, links) published on one page. Pure."""
    parser = _Page()
    try:
        parser.feed(page_html[:MAX_BYTES])
        parser.close()
    except Exception:  # noqa: BLE001
        log.debug("contacts: a page did not parse to the end; kept what did")
    from urllib.parse import unquote
    candidates = [unquote(m).strip() for m in parser.mailtos]
    text = html.unescape(" ".join(parser.text))
    candidates += [m.group(1) for m in _EMAIL.finditer(text)]
    out: list[str] = []
    for raw in candidates:
        address = raw.strip().strip(".,;:()<>[]\"'").lower()
        if plausible(address) and address not in out:
            out.append(address)
    links = [(urljoin(url, href), label) for href, label in parser.links]
    return out, links


def contact_pages(home: str, links: list[tuple[str, str]], limit: int) -> list[str]:
    """Same-site pages the home page links to that read as contact/about."""
    home_key = site_key(urlsplit(home).hostname or "")
    picked: list[str] = []
    for href, label in links:
        parts = urlsplit(href)
        if parts.scheme not in ("http", "https"):
            continue
        if site_key(parts.hostname or "") != home_key:
            continue
        if not (_CONTACTISH.search(parts.path) or _CONTACTISH.search(label or "")):
            continue
        clean = parts._replace(fragment="").geturl()
        if clean.rstrip("/") == home.rstrip("/") or clean in picked:
            continue
        picked.append(clean)
        if len(picked) >= limit:
            break
    return picked


def rank(found: list[Found]) -> list[Found]:
    """Same-domain first, then the order they were seen."""
    return sorted(found, key=lambda f: not f.same_domain)


def normal_site(website: str | None) -> str | None:
    raw = (website or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "https://" + raw
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    return parts._replace(fragment="", path=parts.path or "/").geturl()


# ---------------------------------------------------------------- fetching

async def _robots(client: httpx.AsyncClient, site: str) -> tuple[Any, str | None]:
    """(parser, None) or (None, why nothing may be fetched). 4xx: no rules.
    The shared scraper's rule (`_scrape.robots_for`), with this module's UA."""
    from .._scrape import robots_for
    return await robots_for(client, site, agent_ua=USER_AGENT)


async def _page(client: httpx.AsyncClient, url: str, home_key: str
                ) -> tuple[str | None, str, str | None]:
    """(html, final url, why not)."""
    from .._netguard import get_public
    try:
        response, why = await get_public(client, url, headers={"User-Agent": USER_AGENT})
    except Exception as exc:  # noqa: BLE001
        return None, url, explain(exc)
    if response is None:
        return None, url, why
    final = str(response.url)
    if site_key(urlsplit(final).hostname or "") != home_key:
        return None, final, "redirected off the site"
    if response.status_code != 200:
        return None, final, f"HTTP {response.status_code}"
    kind = response.headers.get("content-type", "text/html").lower()
    if "html" not in kind and "text" not in kind:
        return None, final, f"not a page ({kind.split(';')[0]})"
    return response.text[:MAX_BYTES], final, None


async def discover(website: str | None) -> tuple[list[Found], list[str]]:
    """Every published address on the site, ranked, and what was skipped."""
    home = normal_site(website)
    if home is None:
        return [], ["no website on record"]
    home_key = site_key(urlsplit(home).hostname or "")
    notes: list[str] = []
    found: list[Found] = []
    async with httpx.AsyncClient(follow_redirects=False, timeout=FETCH_TIMEOUT) as client:
        robots, why = await _robots(client, home)
        if robots is None:
            return [], [str(why)]
        queue, seen = [home], set()
        while queue and len(seen) < MAX_PAGES:
            url = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            if not robots.can_fetch(USER_AGENT, url):
                notes.append(f"{url}: disallowed by robots.txt")
                continue
            page, final, why = await _page(client, url, home_key)
            if page is None:
                notes.append(f"{url}: {why}")
                continue
            addresses, links = extract(page, final)
            for address in addresses:
                if all(f.address != address for f in found):
                    domain = address.partition("@")[2]
                    same = domain == home_key or domain.endswith("." + home_key)
                    found.append(Found(address, final, same))
            if url == home:
                queue.extend(contact_pages(final, links, MAX_PAGES - 1))
    return rank(found)[:MAX_ADDRESSES], notes


# ------------------------------------------------------------------ storage

async def store(lead_id: int, found: list[Found]) -> list[int]:
    ids: list[int] = []
    for item in found:
        row = await fetch_one(
            """INSERT INTO agency_lead_emails (lead_id, address, found_url, same_domain)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT (lead_id, address) DO NOTHING RETURNING id""",
            lead_id, item.address, item.url, item.same_domain)
        if row is not None:
            ids.append(int(row["id"]))
    return ids


async def published_email(lead_id: int) -> dict[str, Any] | None:
    """The address to write to: same-domain first, then the first seen."""
    row = await fetch_one(
        """SELECT address, found_url, found_at, same_domain FROM agency_lead_emails
            WHERE lead_id=$1 AND NOT withdrawn
            ORDER BY same_domain DESC, found_at, id LIMIT 1""", lead_id)
    return None if row is None else dict(row)


async def emails_for(lead_id: int) -> list[dict[str, Any]]:
    rows = await fetch_all(
        """SELECT address, found_url, found_at, same_domain FROM agency_lead_emails
            WHERE lead_id=$1 AND NOT withdrawn
            ORDER BY same_domain DESC, found_at, id""", lead_id)
    return [dict(r) for r in rows]


# ------------------------------------------------------------------- action

async def find_email(lead_id: int) -> ActionResult:
    """Tier AUTO: read the lead's own site for the address it publishes."""
    from . import _store
    lead_id = int(lead_id)
    lead = await _store.get_lead(lead_id)
    if lead is None:
        return ActionResult(False, f"no such lead: {lead_id}")
    found, notes = await discover(lead.get("website"))
    read_at = datetime.now(UTC).isoformat()
    ids = await store(lead_id, found)
    best = await published_email(lead_id)
    evidence = {"found": [{"address": f.address, "url": f.url,
                           "same_domain": f.same_domain} for f in found],
                "notes": notes[:10], "read_at": read_at,
                "email": (best or {}).get("address")}
    if not found:
        detail = (f"no published email on lead {lead_id}'s site"
                  + (f" ({notes[0]})" if notes else ""))
        return ActionResult(True, detail, evidence=evidence,
                            rollback_token=",".join(map(str, ids)))
    return ActionResult(True, f"lead {lead_id} publishes {found[0].address} "
                              f"(on {found[0].url})",
                        evidence=evidence, rollback_token=",".join(map(str, ids)))


async def find_email_dry(lead_id: int) -> ActionResult:
    from . import _store
    lead = await _store.get_lead(int(lead_id))
    if lead is None:
        return ActionResult(False, f"no such lead: {lead_id}")
    home = normal_site(lead.get("website"))
    if home is None:
        return ActionResult(False, f"lead {lead_id} has no website to read")
    return ActionResult(True, f"would read {home} and up to {MAX_PAGES - 1} of its "
                              "contact/about pages for a published email address")


async def find_email_rollback(token: str) -> ActionResult:
    ids = [int(t) for t in (token or "").split(",") if t.strip().isdigit()]
    if not ids:
        return ActionResult(True, "nothing new was stored; nothing to roll back")
    await execute_sql(
        "UPDATE agency_lead_emails SET withdrawn=TRUE WHERE id = ANY($1)", ids)
    return ActionResult(True, f"withdrew {len(ids)} found address(es)")
