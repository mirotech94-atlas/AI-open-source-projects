"""agency.launch_audit — the 20-item launch check, read off the built files.

`launch_audit(site_dir)` reads what is actually on disk — every HTML page
parsed with the stdlib `html.parser` into a small tree, every JSON-LD block
through `json.loads`, the CSS only where it matters (the sticky bar's media
query) — and reports each of the 20 items as:

* **OK** — present and well-formed, with the evidence (file + what was found);
* **ISSUE** — missing, malformed, or dishonest (a testimonial nobody gave, a
  team photo the client never sent, a tracker the privacy page doesn't
  mention). Any ISSUE blocks `agency.deploy_site`;
* **NEEDS_CLIENT** — ATLAS will not make this up and the client hasn't given
  it yet (reviews, case studies, a team photo, a stated response time,
  opening hours, a form service, the site's domain). Listed on the approval
  card; it does not block a deploy Miro approves;
* **N/A** — does not apply (analytics when PostHog is not configured).

Read-only and blocking: run it through `asyncio.to_thread`. It never
reports OK for something it could not read — a missing site directory or an
unreadable page is an ISSUE (invariant 7's rule, applied to a checklist).
What the client supplied is read from the build's provenance record beside
the site (`.<slug>.launch.json`, written by `launch.finish_site`, never
deployed): without it, any testimonial on the page counts as invented.
"""
from __future__ import annotations

import hashlib
import json
import posixpath
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import pagegate, sitechat
from .ogimage import png_size
from .sitekit import (
    CHAT_WIDGET,
    CONTACT,
    INDEX,
    NOT_FOUND,
    PRIVACY,
    TERMS,
    THANKS,
    analytics_from_env,
)


class Status(StrEnum):
    OK = "OK"
    ISSUE = "ISSUE"
    NEEDS_CLIENT = "NEEDS_CLIENT"
    NA = "N/A"


ITEMS = (
    "Custom 404 page", "Clear CTA above the fold", "Internal links between pages",
    "Thank-you page after an enquiry", "Breadcrumbs on sub-pages", "Case studies",
    "Five FAQs (FAQPage JSON-LD)", "Response-time promise near the CTA",
    "Sticky mobile CTA", "robots.txt", "Unique <title> per page",
    "Unique meta description per page", "Social share image", "Map and directions",
    "Real reviews", "Alt text on every image", "LocalBusiness JSON-LD",
    "Privacy policy and terms", "Privacy-respecting analytics", "Team photo",
)

# Not one of the 20: only a site whose client bought the $500 or $300 package
# carries the AI chat assistant, so only its report has this 21st item (the
# provenance record says which). ISSUE, never OK, when the widget is missing
# from a page, its file is missing or points elsewhere, the privacy page does
# not say what the chat does with what is typed, or the endpoint would not
# serve the slug.
CHAT_NUMBER = 21
CHAT_ITEM = "AI chat assistant"

# Not one of the 20 either, and on every report: each page is a whole HTML
# document whose visible text is about the business, not a model's account
# of the tool output it was given (the 2026-09-30 Eco Electrical preview).
# A preview (no package bought) must also say so and stay out of search.
# ISSUE blocks the deploy like any other. The test itself is `pagegate`,
# shared with the builder.
CONTENT_NUMBER = 22
CONTENT_ITEM = "A real page, not tool output"

# Also on every report (2026-09-30, the marketing templates): every image,
# picture source and video a page loads is a file of the site's own. A
# hotlink to a stock site, a GetLayers bucket or any third-party host is an
# ISSUE, and so is a referenced file that is not there. A live site is held
# to the hotlink rule only: its files past the fetch cap were not read, and
# what was not read is never reported missing or OK.
MEDIA_NUMBER = 23
MEDIA_ITEM = "Self-hosted media"

# Also on every report (Miro, 2026-10-06, "AI search visibility"): what an
# AI assistant can read about the business in one fetch. A sold site built
# from 2026-10-06 carries llms.txt, a LocalBusiness record with a name,
# address and phone, and a robots.txt that names and allows the assistants'
# crawlers. It is a readiness check, not a ranking: nothing here claims the
# business will be recommended. A preview is N/A (out of search by design);
# an older build is NEEDS_CLIENT, never OK, until it is rebuilt.
AI_SEARCH_NUMBER = 24
AI_SEARCH_ITEM = "Readable by AI assistants"


@dataclass(frozen=True, slots=True)
class Item:
    number: int
    name: str
    status: Status
    evidence: str


@dataclass(frozen=True, slots=True)
class LaunchReport:
    site_dir: str
    read_at: datetime
    items: tuple[Item, ...]

    def _with(self, status: Status) -> list[Item]:
        return [i for i in self.items if i.status == status]

    @property
    def issues(self) -> list[Item]:
        return self._with(Status.ISSUE)

    @property
    def needs_client(self) -> list[Item]:
        return self._with(Status.NEEDS_CLIENT)

    @property
    def ready(self) -> bool:
        """Deployable: no ISSUE. NEEDS_CLIENT does not block."""
        return not self.issues

    def counts(self) -> dict[str, int]:
        return {s.value: len(self._with(s)) for s in Status}

    def summary(self) -> str:
        c = self.counts()
        line = (f"launch check (read {self.read_at:%Y-%m-%d %H:%M} UTC): "
                f"{c['OK']} OK, {c['ISSUE']} ISSUE, {c['NEEDS_CLIENT']} NEEDS_CLIENT, "
                f"{c['N/A']} N/A")
        if self.issues:
            line += "; ISSUES: " + "; ".join(
                f"{i.number} {i.name}: {i.evidence}" for i in self.issues)
        if self.needs_client:
            line += "; needed from client: " + ", ".join(
                f"{i.number} {i.name}" for i in self.needs_client)
        return line

    def as_dict(self) -> dict[str, Any]:
        return {"read_at": self.read_at.isoformat(), "ready": self.ready,
                "counts": self.counts(),
                "issues": [f"{i.number} {i.name}" for i in self.issues],
                "needs_client": [f"{i.number} {i.name}" for i in self.needs_client]}

    def markdown(self, name: str) -> str:
        c = self.counts()

        def cell(text: str) -> str:
            return text.replace("|", "\\|").replace("\n", " ")

        verdict = ("**Ready to deploy: yes.** Miro approves the deploy; the "
                   "NEEDS_CLIENT items travel on the approval card." if self.ready else
                   f"**Ready to deploy: no.** {c['ISSUE']} ISSUE(s) must be fixed first; "
                   "`agency.deploy_site` refuses until they are.")
        tally = (f"Read {self.read_at:%Y-%m-%d %H:%M:%S} UTC from `{self.site_dir}`. "
                 f"{c['OK']} OK · {c['ISSUE']} ISSUE · {c['NEEDS_CLIENT']} NEEDS_CLIENT · "
                 f"{c['N/A']} N/A")
        lines = [f"# Launch check: {name}", "", tally, "", verdict, "",
                 "| # | Item | Status | Evidence |", "|---|---|---|---|"]
        lines += [f"| {i.number} | {cell(i.name)} | {i.status.value} | {cell(i.evidence)} |"
                  for i in self.items]
        if self.needs_client:
            lines += ["", "## Needed from the client", ""]
            lines += [f"- **{i.number}. {i.name}**: {i.evidence}" for i in self.needs_client]
        if self.issues:
            lines += ["", "## Issues", ""]
            lines += [f"- **{i.number}. {i.name}**: {i.evidence}" for i in self.issues]
        return "\n".join(lines) + "\n"


# --------------------------------------------------------------------- tree

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
         "source", "track", "wbr"}


@dataclass(eq=False)
class Node:
    tag: str
    attrs: dict[str, str]
    parent: Node | None = None
    children: list[Node | str] = field(default_factory=list)

    def iter(self) -> Iterator[Node]:
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.iter()

    def find_all(self, *tags: str) -> list[Node]:
        return [n for n in self.iter() if n.tag in tags]

    def text(self, skip: tuple[str, ...] = ("script", "style")) -> str:
        parts: list[str] = []

        def walk(node: Node) -> None:
            if node.tag in skip:
                return
            for child in node.children:
                if isinstance(child, str):
                    parts.append(child)
                else:
                    walk(child)
        walk(self)
        return " ".join(" ".join(parts).split())

    def raw(self) -> str:
        return "".join(c for c in self.children if isinstance(c, str))

    def get(self, key: str) -> str:
        return self.attrs.get(key, "")

    def classes(self) -> set[str]:
        return set(self.get("class").split())

    def ancestors(self) -> Iterator[Node]:
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def inside(self, pred: Any) -> bool:
        return any(pred(a) for a in self.ancestors())


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#document", {})
        self._cur = self.root

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, {k.lower(): (v or "") for k, v in attrs}, self._cur)
        self._cur.children.append(node)
        if tag not in _VOID:
            self._cur = node

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, {k.lower(): (v or "") for k, v in attrs}, self._cur)
        self._cur.children.append(node)

    def handle_endtag(self, tag: str) -> None:
        node: Node | None = self._cur
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self._cur = node.parent

    def handle_data(self, data: str) -> None:
        self._cur.children.append(data)


def parse(markup: str) -> Node:
    builder = _Builder()
    builder.feed(markup)
    builder.close()
    return builder.root


@dataclass
class Page:
    name: str
    root: Node
    ld: list[Any]
    ld_errors: list[str]
    doctype: bool = True

    def title(self) -> str:
        titles = self.root.find_all("title")
        return titles[0].text() if titles else ""

    def meta(self, key: str) -> str | None:
        for m in self.root.find_all("meta"):
            if (m.get("name") or m.get("property")).lower() == key:
                return m.get("content").strip()
        return None

    def ld_objects(self) -> list[dict]:
        out: list[dict] = []

        def walk(value: Any) -> None:
            if isinstance(value, list):
                for v in value:
                    walk(v)
            elif isinstance(value, dict):
                out.append(value)
                if isinstance(value.get("@graph"), list):
                    walk(value["@graph"])
        walk(self.ld)
        return out

    def ld_of(self, kind: str) -> list[dict]:
        def types(obj: dict) -> list[str]:
            t = obj.get("@type")
            return t if isinstance(t, list) else [t]
        return [o for o in self.ld_objects() if kind in types(o)]


def page_from_markup(name: str, markup: str) -> Page:
    """One page parsed, its JSON-LD blocks decoded (a bad one is recorded,
    never repaired)."""
    root = parse(markup)
    ld: list[Any] = []
    errors: list[str] = []
    for script in root.find_all("script"):
        if script.get("type").strip().lower() != "application/ld+json":
            continue
        try:
            ld.append(json.loads(script.raw()))
        except ValueError as exc:
            errors.append(f"{name}: invalid JSON-LD ({exc})")
    return Page(name, root, ld, errors, pagegate.has_doctype(markup))


def _load_page(site_dir: Path, path: Path) -> Page:
    return page_from_markup(str(path.relative_to(site_dir)),
                            path.read_text(errors="replace"))


# ------------------------------------------------------------------ helpers

def _norm(text: str) -> str:
    return " ".join((text or "").split()).strip().lower()


def _resolve(page: str, href: str, site_host: str | None) -> str | None:
    """The site-relative file an href points at, or None when it points off
    the site (tel:, mailto:, another host) or only at a fragment."""
    href = (href or "").strip()
    if not href or href.startswith("#"):
        return None
    parsed = urlparse(href)
    if parsed.scheme and parsed.scheme not in ("http", "https"):
        return None
    if (parsed.scheme or parsed.netloc) and (
            not site_host or parsed.netloc.lower() != site_host):
        return None
    path = parsed.path
    if path.startswith("/"):
        rel = path.lstrip("/")
    else:
        rel = posixpath.join(posixpath.dirname(page), path)
    if rel == "" or rel.endswith("/"):
        rel += INDEX
    rel = posixpath.normpath(rel)
    return rel if rel != "." else INDEX


def _is_cta(node: Node, resolve: Callable[[str, str], str | None], page: str) -> bool:
    if node.tag not in ("a", "button"):
        return False
    if not (node.text() or node.get("aria-label")):
        return False
    href = node.get("href").strip().lower()
    if href.startswith(("tel:", "mailto:")) or href.endswith("#contact"):
        return True
    if node.get("data-atlas") == "cta" or any("cta" in c for c in node.classes()):
        return True
    return resolve(page, href) == CONTACT


_TEAM_WORDS = re.compile(r"\b(?:our|the|meet the) team\b|\bteam photo\b|\bstaff\b|"
                         r"\bemployees\b", re.IGNORECASE)
_STARS = re.compile(r"[★☆]|\b\d(?:\.\d)?\s*(?:/\s*5\b|out of 5\b|-?\s*stars?\b)",
                    re.IGNORECASE)
_TESTIMONIAL_CLASS = re.compile(r"\b(?:testimonial|review-item|rating)", re.IGNORECASE)
_TRACKERS = re.compile(r"googletagmanager|google-analytics|\bgtag\(|\bfbq\(|"
                       r"connect\.facebook\.net|hotjar|clarity\.ms", re.IGNORECASE)
_RESPONSE = re.compile(r"\b(?:reply|respond|get back to you)\w*\b", re.IGNORECASE)
_WITHIN_N = re.compile(r"\bwithin\s+\d", re.IGNORECASE)


def _preview_form_problem(form: Node) -> str | None:
    """Why a preview booking form could send what a visitor types, or None.
    It must have no action, be method="dialog" (a no-op outside a dialog)
    and give no typed control a name: only radio groups, whose values are
    the page's own words, may carry one."""
    if form.get("action"):
        return f'has an action ({form.get("action")[:60]}), so it would send'
    if form.get("method").lower() != "dialog":
        return 'is not method="dialog"'
    for node in form.find_all("input", "textarea", "select"):
        if node.get("name") and node.get("type").lower() != "radio":
            return f'names a field ("{node.get("name")}"), so it could be sent'
    return None


def _media_blocks(css: str) -> list[tuple[str, str]]:
    """(condition, body) for every @media block, braces balanced."""
    out = []
    for match in re.finditer(r"@media([^{]*)\{", css):
        depth, i = 1, match.end()
        while i < len(css) and depth:
            depth += {"{": 1, "}": -1}.get(css[i], 0)
            i += 1
        out.append((match.group(1), css[match.end():i - 1]))
    return out


def _rules(css: str) -> list[tuple[str, str]]:
    return [(sel.strip(), decl) for sel, decl in re.findall(r"([^{}]+)\{([^{}]*)\}", css)]


# ------------------------------------------------------------------- checks

class _Audit:
    """The 20 checks over a set of parsed pages. Where the files come from
    is the subclass's business: `_DiskAudit` reads a built site directory,
    `_LiveAudit` what `livecheck` fetched from a published URL."""

    live = False

    def __init__(self, pages: dict[str, Page], read_errors: list[str],
                 provenance: dict[str, Any] | None) -> None:
        self.prov = provenance
        self.pages = pages
        self.read_errors = read_errors
        self.index = self.pages.get(INDEX)
        website = (provenance or {}).get("website") or ""
        if not website and self.index:
            website = self.index.meta("og:url") or ""
        parsed = urlparse(website)
        self.site_host = parsed.netloc.lower() or None
        self.website = f"https://{self.site_host}" if self.site_host else None

    # -- where the files come from (overridden)
    def resolve(self, page: str, href: str) -> str | None:
        return _resolve(page, href, self.site_host)

    def exists(self, rel: str) -> bool:
        raise NotImplementedError

    def read_bytes(self, rel: str) -> bytes | None:
        raise NotImplementedError

    def offsite_bytes(self, url: str) -> bytes | None:
        """An absolute URL off the site's own host: never read from disk."""
        return None

    def is_client_photo(self, src: str, rel: str | None) -> bool:
        raise NotImplementedError

    def unchecked(self, rel: str) -> bool:
        """A link target that was not read, so neither found nor missing."""
        return False

    def expects_analytics(self) -> bool:
        return analytics_from_env() is not None

    # -- small shared pieces
    def links(self, page: Page) -> list[tuple[str, str]]:
        """(href, resolved file) for every on-site link in `page`."""
        out = []
        for a in page.root.find_all("a"):
            rel = self.resolve(page.name, a.get("href"))
            if rel is not None:
                out.append((a.get("href"), rel))
        return out

    def no_index(self, n: int) -> Item:
        return Item(n, ITEMS[n - 1], Status.ISSUE, "index.html is missing or unreadable")

    # -- 1
    def custom_404(self) -> Item:
        page = self.pages.get(NOT_FOUND)
        if page is None:
            return Item(1, ITEMS[0], Status.ISSUE, "no 404.html in the site")
        home = [h for h, rel in self.links(page) if rel == INDEX]
        if not home:
            return Item(1, ITEMS[0], Status.ISSUE, "404.html has no link back home")
        return Item(1, ITEMS[0], Status.OK, f'404.html links home (href="{home[0]}")')

    # -- 2
    def cta_above_fold(self) -> Item:
        if self.index is None:
            return self.no_index(2)
        order = list(self.index.root.iter())
        fold = next((n for n in order if n.tag in ("header", "section")), None)
        if fold is not None:
            last = list(fold.iter())[-1]
            order = order[:order.index(last) + 1]
        else:
            order = order[:60]
        ctas = [n for n in order if _is_cta(n, self.resolve, INDEX)]
        # A button-styled action (or a tel:/mailto: link) says more than a
        # plain "Contact" nav link, so it is the one named as evidence.
        ctas.sort(key=lambda n: not (n.get("data-atlas") == "cta"
                                     or any("cta" in c for c in n.classes())
                                     or n.get("href").startswith(("tel:", "mailto:"))))
        if ctas:
            node = ctas[0]
            where = f"first <{fold.tag}>" if fold is not None else "top of the page"
            label = node.text() or node.get("aria-label")
            return Item(2, ITEMS[1], Status.OK,
                        f'index.html: <{node.tag} href="{node.get("href")}"> '
                        f'"{label}" in the {where}')
        return Item(2, ITEMS[1], Status.ISSUE,
                    "index.html: no call/book/contact link before the end of the first "
                    "header or section")

    # -- 3
    def internal_links(self) -> Item:
        if not self.pages:
            return Item(3, ITEMS[2], Status.ISSUE, "no HTML pages in the site")
        broken, lonely, unread = [], [], []
        linked_to: set[str] = set()
        for page in self.pages.values():
            targets = set()
            for href, rel in self.links(page):
                if not self.exists(rel) and self.unchecked(rel):
                    unread.append(href)
                elif not self.exists(rel):
                    broken.append(f"{page.name} -> {href}")
                else:
                    targets.add(rel)
            targets.discard(page.name)
            linked_to |= targets
            if not targets:
                lonely.append(page.name)
        orphans = [p for p in self.pages if p not in linked_to
                   and p not in (INDEX, NOT_FOUND, THANKS)]
        problems = []
        if broken:
            problems.append("broken: " + ", ".join(broken[:6]))
        if unread:
            problems.append("not fetched, so not checked: "
                            + ", ".join(sorted(set(unread))[:6]))
        if lonely:
            problems.append("no links to other pages: " + ", ".join(lonely))
        if orphans:
            problems.append("not linked from any page: " + ", ".join(orphans))
        if problems:
            return Item(3, ITEMS[2], Status.ISSUE, "; ".join(problems))
        return Item(3, ITEMS[2], Status.OK,
                    f"{len(self.pages)} pages, every one links to another and every "
                    "link resolves")

    # -- 4
    def thank_you(self) -> Item:
        every = [(p, f) for p in self.pages.values() for f in p.root.find_all("form")]
        # A preview's booking form (sitebooking.PREVIEW) sends nothing and so
        # needs no thank-you page -- as long as it provably cannot send.
        previews = [(p, f) for p, f in every if f.get("data-mode") == "preview"]
        forms = [(p, f) for p, f in every if f.get("data-mode") != "preview"]
        problems = [f"{p.name}: the preview booking form {why}"
                    for p, f in previews for why in [_preview_form_problem(f)] if why]
        if not forms:
            if problems:
                return Item(4, ITEMS[3], Status.ISSUE, "; ".join(problems))
            return Item(4, ITEMS[3], Status.NEEDS_CLIENT,
                        "no enquiry form: needs the client's form service "
                        "(client.json form_action, or AGENCY_FORM_ACTION); phone/email "
                        "CTAs stand in until then"
                        + ("; the preview's booking form sends nothing" if previews
                           else ""))
        for page, form in forms:
            nexts = [i.get("value") for i in form.find_all("input")
                     if i.get("name") in ("_next", "_redirect", "redirect", "redirect_to")]
            if not any(urlparse(v).path.endswith(THANKS) for v in nexts):
                problems.append(f"{page.name}: the form does not send people to {THANKS}")
            action = form.get("action")
            if not action or urlparse(action).scheme != "https":
                problems.append(f"{page.name}: the form has no https action")
        thanks = self.pages.get(THANKS)
        if thanks is None:
            problems.append(f"no {THANKS}")
        elif not any(rel == INDEX for _h, rel in self.links(thanks)):
            problems.append(f"{THANKS} has no link back home")
        if problems:
            return Item(4, ITEMS[3], Status.ISSUE, "; ".join(problems))
        return Item(4, ITEMS[3], Status.OK,
                    f"{forms[0][0].name}: the form redirects to {THANKS}, which exists "
                    "and links home")

    # -- 5
    def breadcrumbs(self) -> Item:
        subs = [p for n, p in self.pages.items() if n not in (INDEX, NOT_FOUND)]
        if not subs:
            return Item(5, ITEMS[4], Status.ISSUE, "the site has no sub-pages")
        problems = []
        for page in subs:
            navs = [n for n in page.root.find_all("nav")
                    if n.get("aria-label").strip().lower() == "breadcrumb"]
            if not navs or not any(
                    self.resolve(page.name, a.get("href")) == INDEX
                    for a in navs[0].find_all("a")):
                problems.append(f"{page.name}: no breadcrumb trail back home")
                continue
            lists = page.ld_of("BreadcrumbList")
            if not lists:
                problems.append(f"{page.name}: no BreadcrumbList JSON-LD")
                continue
            elements = lists[0].get("itemListElement")
            if not isinstance(elements, list) or len(elements) < 2:
                problems.append(f"{page.name}: BreadcrumbList has fewer than 2 items")
                continue
            for i, el in enumerate(elements, start=1):
                ok = (isinstance(el, dict) and el.get("position") == i
                      and isinstance(el.get("name"), str) and el["name"].strip()
                      and (i == len(elements) or isinstance(el.get("item"), str)))
                if not ok:
                    problems.append(f"{page.name}: BreadcrumbList item {i} needs "
                                    "position, name and item")
                    break
        if problems:
            return Item(5, ITEMS[4], Status.ISSUE, "; ".join(problems))
        return Item(5, ITEMS[4], Status.OK,
                    f"{len(subs)} sub-page(s) with a trail and valid BreadcrumbList: "
                    + ", ".join(p.name for p in subs))

    # -- 6
    def case_studies(self) -> Item:
        found = [(p, s) for p in self.pages.values() for s in p.root.iter()
                 if s.get("id") == "case-studies"]
        if not found:
            return Item(6, ITEMS[5], Status.NEEDS_CLIENT,
                        "no case studies on record; add case_studies to client.json "
                        "(ATLAS never writes one)")
        page, section = found[0]
        titles = [a.find_all("h2", "h3", "h4") for a in section.find_all("article")]
        real = [t[0].text() for t in titles if t and t[0].text()]
        if not real:
            return Item(6, ITEMS[5], Status.ISSUE,
                        f"{page.name}: a case-studies section with no case study in it")
        supplied = {_norm(t) for t in (self.prov or {}).get("case_studies", [])}
        invented = [t for t in real if _norm(t) not in supplied]
        if invented:
            return Item(6, ITEMS[5], Status.ISSUE,
                        f"{page.name}: case study not supplied by the client: "
                        f"{invented[0][:60]!r}")
        return Item(6, ITEMS[5], Status.OK,
                    f"{page.name}: {len(real)} client case stud"
                    f"{'y' if len(real) == 1 else 'ies'}")

    # -- 7
    def faqs(self) -> Item:
        blocks = [(p, o) for p in self.pages.values() for o in p.ld_of("FAQPage")]
        visible = []
        for p in self.pages.values():
            for s in p.root.iter():
                if s.get("id") == "faq":
                    visible.append((p, len(s.find_all("dt")) or len(s.find_all("summary"))))
        if not blocks:
            if visible:
                return Item(7, ITEMS[6], Status.ISSUE,
                            f"{visible[0][0].name}: FAQs on the page but no FAQPage "
                            "JSON-LD")
            return Item(7, ITEMS[6], Status.NEEDS_CLIENT,
                        "no FAQs; add faqs to client.json")
        page, block = blocks[0]
        entities = block.get("mainEntity")
        if not isinstance(entities, list) or not entities:
            return Item(7, ITEMS[6], Status.ISSUE, f"{page.name}: FAQPage has no mainEntity")
        text = _norm(page.root.text())
        for i, q in enumerate(entities, start=1):
            answer = q.get("acceptedAnswer") if isinstance(q, dict) else None
            if not (isinstance(q, dict) and q.get("@type") == "Question"
                    and isinstance(q.get("name"), str) and q["name"].strip()
                    and isinstance(answer, dict) and answer.get("@type") == "Answer"
                    and isinstance(answer.get("text"), str) and answer["text"].strip()):
                return Item(7, ITEMS[6], Status.ISSUE,
                            f"{page.name}: FAQPage question {i} needs name and an "
                            "Answer with text")
            if _norm(q["name"]) not in text:
                return Item(7, ITEMS[6], Status.ISSUE,
                            f"{page.name}: FAQPage question {q['name'][:50]!r} is not "
                            "on the page")
        n = len(entities)
        if n < 5:
            return Item(7, ITEMS[6], Status.NEEDS_CLIENT,
                        f"{page.name}: {n} FAQ(s) with valid FAQPage JSON-LD; needs "
                        f"{5 - n} more from the client (client.json faqs)")
        return Item(7, ITEMS[6], Status.OK,
                    f"{page.name}: {n} FAQs, FAQPage JSON-LD valid and matching the page")

    # -- 8
    def response_time(self) -> Item:
        for name in (INDEX, CONTACT):
            page = self.pages.get(name)
            if page is None:
                continue
            for node in page.root.iter():
                if node.tag not in ("p", "span", "small", "strong", "em"):
                    continue
                own = node.text()
                if not own or not _RESPONSE.search(own) or len(own) > 200:
                    continue
                context = node.parent
                near = context is not None and (
                    any(_is_cta(n, self.resolve, name) for n in context.iter())
                    or node.inside(lambda a: a.tag == "form"))
                if not near:
                    continue
                if _WITHIN_N.search(own):
                    return Item(8, ITEMS[7], Status.OK, f'{name}: "{own}" beside the CTA')
                return Item(8, ITEMS[7], Status.NEEDS_CLIENT,
                            f'{name}: "{own}" — no number until the client states their '
                            "response time (client.json response_time_hours)")
        return Item(8, ITEMS[7], Status.ISSUE,
                    "no response-time line next to a CTA or form on index.html or "
                    "contact.html")

    # -- 9
    def sticky_cta(self) -> Item:
        if self.index is None:
            return self.no_index(9)
        css = " ".join(s.raw() for s in self.index.root.find_all("style"))
        fixed: set[str] = set()
        padded = False
        for condition, body in _media_blocks(css):
            if "max-width" not in condition:
                continue
            for selector, decl in _rules(body):
                d = decl.replace(" ", "").lower()
                if "position:fixed" in d and re.search(r"(?:^|;)bottom:0", d):
                    fixed |= set(re.findall(r"\.([\w-]+)", selector))
                if re.search(r"(?:^|,)\s*body\s*$", selector) and "padding-bottom" in d:
                    padded = True
        if not fixed:
            return Item(9, ITEMS[8], Status.ISSUE,
                        "index.html: no element is fixed to the bottom in a small-screen "
                        "(max-width) media query")
        for node in self.index.root.iter():
            if not node.classes() & fixed:
                continue
            ctas = [n for n in node.iter() if _is_cta(n, self.resolve, INDEX)]
            if not ctas:
                continue
            if not node.get("aria-label"):
                return Item(9, ITEMS[8], Status.ISSUE,
                            "index.html: the sticky bar has no aria-label")
            if not padded:
                return Item(9, ITEMS[8], Status.ISSUE,
                            "index.html: the sticky bar can cover content (no body "
                            "padding-bottom in the same media query)")
            return Item(9, ITEMS[8], Status.OK,
                        f'index.html: <{node.tag} aria-label="{node.get("aria-label")}"> '
                        f"fixed at the bottom under max-width, {len(ctas)} action(s), "
                        "body padded")
        return Item(9, ITEMS[8], Status.ISSUE,
                    "index.html: the fixed element holds no call/book/contact link")

    # -- 10
    def robots(self) -> Item:
        raw = self.read_bytes("robots.txt") if self.exists("robots.txt") else None
        if raw is None:
            return Item(10, ITEMS[9], Status.ISSUE, "no robots.txt")
        text = raw.decode(errors="replace")
        lines = [ln.strip().lower() for ln in text.splitlines()]
        if not any(ln.startswith("user-agent:") for ln in lines):
            return Item(10, ITEMS[9], Status.ISSUE, "robots.txt names no User-agent")
        if "disallow: /" in lines and "allow: /" not in lines:
            return Item(10, ITEMS[9], Status.ISSUE, "robots.txt blocks the whole site")
        if self.exists("sitemap.xml") and not any(
                ln.startswith("sitemap:") for ln in lines):
            return Item(10, ITEMS[9], Status.ISSUE,
                        "sitemap.xml exists but robots.txt doesn't point at it")
        return Item(10, ITEMS[9], Status.OK, "robots.txt allows crawling"
                    + (" and names the sitemap" if any(ln.startswith("sitemap:")
                                                       for ln in lines) else ""))

    # -- 11 / 12
    def unique(self, n: int, values: dict[str, str | None], what: str) -> Item:
        if not values:
            return Item(n, ITEMS[n - 1], Status.ISSUE, "no HTML pages in the site")
        missing = [p for p, v in values.items() if not v]
        seen: dict[str, list[str]] = {}
        for p, v in values.items():
            if v:
                seen.setdefault(_norm(v), []).append(p)
        dupes = [ps for ps in seen.values() if len(ps) > 1]
        problems = []
        if missing:
            problems.append(f"no {what}: " + ", ".join(missing))
        if dupes:
            problems.append(f"same {what}: " + "; ".join(" = ".join(d) for d in dupes))
        if problems:
            return Item(n, ITEMS[n - 1], Status.ISSUE, "; ".join(problems))
        return Item(n, ITEMS[n - 1], Status.OK,
                    f"{len(values)} pages, {len(values)} distinct {what}s")

    # -- 13
    def share_image(self) -> Item:
        if self.index is None:
            return self.no_index(13)
        image = self.index.meta("og:image")
        card = self.index.meta("twitter:card")
        if not image or not card:
            missing = [k for k, v in (("og:image", image), ("twitter:card", card)) if not v]
            return Item(13, ITEMS[12], Status.ISSUE,
                        f"index.html: no {' or '.join(missing)}")
        parsed = urlparse(image)
        if parsed.scheme and parsed.netloc.lower() != (self.site_host or ""):
            data = self.offsite_bytes(image)
            if data is None:
                return Item(13, ITEMS[12], Status.ISSUE,
                            f"index.html: og:image points off the site ({image}); it "
                            "can't be checked")
            rel = image
        else:
            rel = self.resolve(INDEX, image)
            if rel is None or not self.exists(rel):
                return Item(13, ITEMS[12], Status.ISSUE,
                            f"index.html: og:image {image} is not a file in the site")
            data = self.read_bytes(rel)
            if data is None:
                return Item(13, ITEMS[12], Status.ISSUE,
                            f"index.html: og:image {image} could not be read")
        size = png_size(data[:32])
        if rel.endswith(".png") and size is None:
            return Item(13, ITEMS[12], Status.ISSUE, f"{rel} is not a valid PNG")
        if size and (size[0] < 600 or size[1] < 315):
            return Item(13, ITEMS[12], Status.ISSUE,
                        f"{rel} is {size[0]}x{size[1]}, below 600x315")
        dims = f" ({size[0]}x{size[1]})" if size else ""
        if not parsed.scheme and self.live:
            return Item(13, ITEMS[12], Status.ISSUE,
                        f"index.html: og:image {image} is a relative URL; social sites "
                        "need an absolute one")
        if not parsed.scheme:
            return Item(13, ITEMS[12], Status.NEEDS_CLIENT,
                        f"index.html: og:image {rel}{dims} and twitter:card {card} are "
                        "set, but the image URL is relative; social sites need the "
                        "client's domain to make it absolute")
        return Item(13, ITEMS[12], Status.OK,
                    f"index.html: og:image {image}{dims}, twitter:card {card}")

    # -- 14
    def map_directions(self) -> Item:
        for name in (INDEX, CONTACT):
            page = self.pages.get(name)
            if page is None:
                continue
            anchors = page.root.find_all("a")
            directions = [a for a in anchors
                          if a.get("href").startswith("https://www.google.com/maps/dir/")
                          and "direction" in a.text().lower()]
            maps = [a for a in anchors
                    if a.get("href").startswith("https://www.google.com/maps")]
            maps += [f for f in page.root.find_all("iframe")
                     if "google.com/maps" in f.get("src")]
            if directions and maps:
                return Item(14, ITEMS[13], Status.OK,
                            f"{name}: map link and \"{directions[0].text()}\" "
                            f"({directions[0].get('href')[:80]})")
        if any(p.root.find_all("address") for p in self.pages.values()):
            return Item(14, ITEMS[13], Status.ISSUE,
                        "an address is shown but there is no map and Get directions link")
        return Item(14, ITEMS[13], Status.NEEDS_CLIENT,
                    "no address on record; add address to client.json")

    # -- 15
    def reviews(self) -> Item:
        supplied = {_norm(t) for t in (self.prov or {}).get("client_reviews", [])}
        shown = 0
        for page in self.pages.values():
            for q in page.root.find_all("blockquote"):
                footer_text = " ".join(f.text() for f in q.find_all("footer"))
                body = _norm(q.text(skip=("script", "style", "footer")))
                if body not in supplied:
                    return Item(15, ITEMS[14], Status.ISSUE,
                                f"{page.name}: a testimonial no client supplied: "
                                f"{q.text()[:60]!r}")
                if not footer_text.strip():
                    return Item(15, ITEMS[14], Status.ISSUE,
                                f"{page.name}: a review with no author")
                shown += 1
            if not supplied:
                marked = [n for n in page.root.iter()
                          if _TESTIMONIAL_CLASS.search(n.get("class"))]
                if marked:
                    return Item(15, ITEMS[14], Status.ISSUE,
                                f"{page.name}: a testimonial/rating block "
                                f"(class=\"{marked[0].get('class')}\") with no client "
                                "reviews behind it")
            for section in (n for n in page.root.iter() if n.get("id") == "reviews"):
                outside = " ".join(
                    c if isinstance(c, str) else ("" if c.tag == "blockquote" else c.text())
                    for c in section.children)
                if _STARS.search(outside):
                    return Item(15, ITEMS[14], Status.ISSUE,
                                f"{page.name}: a star rating in the reviews section "
                                "that no review carries")
            for obj in page.ld_objects():
                if obj.get("@type") in ("Review", "AggregateRating") or \
                        "aggregateRating" in obj or "review" in obj:
                    return Item(15, ITEMS[14], Status.ISSUE,
                                f"{page.name}: review/rating JSON-LD ATLAS never writes")
        if not shown:
            return Item(15, ITEMS[14], Status.NEEDS_CLIENT,
                        "no reviews on record; add real reviews (author, text, rating, "
                        "source_url) to client.json — ATLAS never writes one")
        return Item(15, ITEMS[14], Status.OK,
                    f"{shown} review(s), each word for word from the client's file")

    # -- 16
    def alt_text(self) -> Item:
        count, bad = 0, []
        for page in self.pages.values():
            for img in page.root.find_all("img"):
                count += 1
                if img.get("role").strip().lower() in ("presentation", "none"):
                    continue
                if "alt" not in img.attrs or not img.get("alt").strip():
                    bad.append(f"{page.name}: {img.get('src') or '(no src)'}")
        if bad:
            return Item(16, ITEMS[15], Status.ISSUE,
                        f"{len(bad)} image(s) with missing or empty alt: "
                        + ", ".join(bad[:5]))
        return Item(16, ITEMS[15], Status.OK,
                    f"{count} image(s), every one has alt text or is marked decorative")

    # -- 17
    def local_business(self) -> Item:
        errors = [e for p in self.pages.values() for e in p.ld_errors]
        if errors:
            return Item(17, ITEMS[16], Status.ISSUE, "; ".join(errors[:3]))
        if self.index is None:
            return self.no_index(17)
        found = [o for o in self.index.ld_objects()
                 if "LocalBusiness" in str(o.get("@type"))]
        if not found:
            return Item(17, ITEMS[16], Status.ISSUE, "index.html: no LocalBusiness JSON-LD")
        ld = found[0]
        if "schema.org" not in str(ld.get("@context", "")) or not str(
                ld.get("name") or "").strip():
            return Item(17, ITEMS[16], Status.ISSUE,
                        "index.html: LocalBusiness JSON-LD needs @context schema.org "
                        "and a name")
        if "acceptsReservations" in ld and not any(
                n.get("data-atlas") in ("book-form", "book-link")
                for n in self.index.root.iter()):
            return Item(17, ITEMS[16], Status.ISSUE,
                        "index.html: the JSON-LD says the business takes reservations, "
                        "but the page has no booking link or form that reaches it")
        geo = ld.get("geo")
        if geo is not None:
            try:
                lat, lng = float(geo["latitude"]), float(geo["longitude"])
                assert -90 <= lat <= 90 and -180 <= lng <= 180
            except (TypeError, KeyError, ValueError, AssertionError):
                return Item(17, ITEMS[16], Status.ISSUE,
                            "index.html: LocalBusiness geo is not a valid lat/lng")
        missing = [k for k in ("address", "telephone", "openingHours", "url")
                   if not ld.get(k) and not (k == "openingHours"
                                             and ld.get("openingHoursSpecification"))]
        present = [k for k in ("name", "address", "telephone", "openingHours", "url", "geo")
                   if ld.get(k)]
        if missing:
            return Item(17, ITEMS[16], Status.NEEDS_CLIENT,
                        f"index.html: valid LocalBusiness with {', '.join(present)}; "
                        f"not on record: {', '.join(missing)}")
        return Item(17, ITEMS[16], Status.OK,
                    f"index.html: valid LocalBusiness with {', '.join(present)}")

    # -- 18
    def privacy_terms(self) -> Item:
        problems = []
        for name in (PRIVACY, TERMS):
            if name not in self.pages:
                problems.append(f"no {name}")
            elif self.index is not None and not any(
                    rel == name for _h, rel in self.links(self.index)):
                problems.append(f"index.html doesn't link to {name}")
        privacy = self.pages.get(PRIVACY)
        if privacy is not None:
            text = privacy.root.text().lower()
            tracked = any(self._posthog(p) for p in self.pages.values())
            if tracked and "posthog" not in text:
                problems.append("the site runs PostHog but privacy.html doesn't say so")
            if tracked and "does not use tracking" in text:
                problems.append("privacy.html says there is no tracking, but there is")
            has_form = any(p.root.find_all("form") for p in self.pages.values())
            if has_form and "no forms" in text:
                problems.append("privacy.html says there are no forms, but there is one")
        if problems:
            return Item(18, ITEMS[17], Status.ISSUE, "; ".join(problems))
        return Item(18, ITEMS[17], Status.OK,
                    "privacy.html and terms.html exist, are linked from the home page, "
                    "and privacy.html matches what the site does")

    @staticmethod
    def _posthog(page: Page) -> bool:
        return any("posthog.init" in s.raw() for s in page.root.find_all("script"))

    # -- 19
    def analytics(self) -> Item:
        for page in self.pages.values():
            for s in page.root.find_all("script"):
                blob = s.get("src") + " " + s.raw()
                if _TRACKERS.search(blob):
                    return Item(19, ITEMS[18], Status.ISSUE,
                                f"{page.name}: a third-party tracker "
                                f"({_TRACKERS.search(blob).group(0)}) the privacy page "
                                "doesn't cover")
                if "phx_" in blob:
                    return Item(19, ITEMS[18], Status.ISSUE,
                                f"{page.name}: a PostHog personal key (phx_) is in the "
                                "page — that is a secret")
        tracked = [p.name for p in self.pages.values() if self._posthog(p)]
        configured = self.expects_analytics()
        if not tracked:
            if configured:
                return Item(19, ITEMS[18], Status.ISSUE,
                            "POSTHOG_PUBLIC_KEY is set but no page carries the snippet; "
                            "rebuild the site")
            return Item(19, ITEMS[18], Status.NA,
                        "analytics off: POSTHOG_PUBLIC_KEY is not set, and the site "
                        "loads no tracker")
        untracked = [p for p in self.pages if p not in tracked]
        problems = []
        if untracked:
            problems.append("no snippet on " + ", ".join(untracked))
        for page in self.pages.values():
            for s in page.root.find_all("script"):
                raw = s.raw().replace(" ", "")
                if "posthog.init" in raw and not (
                        '"persistence":"memory"' in raw
                        and '"disable_session_recording":true' in raw):
                    problems.append(f"{page.name}: PostHog is not set to memory "
                                    "persistence with session recording off")
        if problems:
            return Item(19, ITEMS[18], Status.ISSUE, "; ".join(problems[:3]))
        return Item(19, ITEMS[18], Status.OK,
                    f"PostHog on {len(tracked)} page(s): memory persistence (no cookies), "
                    "no session recording, named in privacy.html")

    # -- 20
    def team(self) -> Item:
        for page in self.pages.values():
            for img in page.root.find_all("img"):
                in_team = img.inside(lambda a: a.get("id") == "team")
                label = " ".join((img.get("alt"), img.get("class"), img.get("src")))
                if not in_team and _TEAM_WORDS.search(label):
                    return Item(20, ITEMS[19], Status.ISSUE,
                                f"{page.name}: a team image ({img.get('src')}) the client "
                                "didn't supply")
        sections = [(p, n) for p in self.pages.values() for n in p.root.iter()
                    if n.get("id") == "team"]
        if not sections:
            return Item(20, ITEMS[19], Status.NEEDS_CLIENT,
                        "no team photo on record; add team_photo to client.json (never a "
                        "stock photo)")
        page, section = sections[0]
        imgs = section.find_all("img")
        if not imgs:
            return Item(20, ITEMS[19], Status.ISSUE, f"{page.name}: a team section with "
                        "no photo")
        src = imgs[0].get("src")
        rel = self.resolve(page.name, src)
        if not self.is_client_photo(src, rel):
            return Item(20, ITEMS[19], Status.ISSUE,
                        f"{page.name}: team photo {src} is not the one the client supplied")
        if rel is not None and not self.exists(rel):
            return Item(20, ITEMS[19], Status.ISSUE, f"{page.name}: {src} is missing")
        return Item(20, ITEMS[19], Status.OK,
                    f"{page.name}: the client's team photo {src} with alt "
                    f"\"{imgs[0].get('alt')}\"")

    def run(self) -> list[Item]:
        titles = {n: p.title() for n, p in self.pages.items()}
        metas = {n: p.meta("description") for n, p in self.pages.items()}
        items = [
            self.custom_404(), self.cta_above_fold(), self.internal_links(),
            self.thank_you(), self.breadcrumbs(), self.case_studies(), self.faqs(),
            self.response_time(), self.sticky_cta(), self.robots(),
            self.unique(11, titles, "title"), self.unique(12, metas, "meta description"),
            self.share_image(), self.map_directions(), self.reviews(), self.alt_text(),
            self.local_business(), self.privacy_terms(), self.analytics(), self.team(),
        ]
        if self.read_errors:
            items[2] = Item(3, ITEMS[2], Status.ISSUE,
                            "unreadable page(s): " + "; ".join(self.read_errors))
        if (self.prov or {}).get("chat"):
            items.append(self.chat())
        items.append(self.content())
        items.append(self.media())
        items.append(self.ai_search())
        return items

    # -- 24, every report
    def ai_search(self) -> Item:
        from . import sitekit
        if self.index is not None and "noindex" in (
                self.index.meta("robots") or "").lower():
            return Item(AI_SEARCH_NUMBER, AI_SEARCH_ITEM, Status.NA,
                        "a preview stays out of search; nothing is published for "
                        "AI assistants until the client buys")
        robots = (self.read_bytes("robots.txt") or b"").decode("utf-8", "replace") \
            if self.exists("robots.txt") else ""
        named = [b for b in sitekit.AI_CRAWLERS if f"User-agent: {b}" in robots]
        has_llms = self.exists(sitekit.LLMS_TXT)
        if not has_llms and not named:
            # A build from before 2026-10-06: nothing to judge yet. Said so,
            # never OK; the next agency.build_site publishes both.
            return Item(AI_SEARCH_NUMBER, AI_SEARCH_ITEM, Status.NEEDS_CLIENT,
                        "not on this build: a rebuild (agency.build_site) publishes "
                        "llms.txt and allows the AI assistants' crawlers by name")
        problems, wanted = [], []
        if not has_llms:
            problems.append("no llms.txt")
        else:
            text = (self.read_bytes(sitekit.LLMS_TXT) or b"").decode("utf-8", "replace")
            if not text.startswith("# "):
                problems.append("llms.txt does not open with the business's name")
            if "Phone:" not in text and "Email:" not in text:
                wanted.append("a phone or email")
        if len(named) < 3:
            problems.append("robots.txt does not name and allow the AI assistants' "
                            "crawlers (GPTBot, ClaudeBot, PerplexityBot...)")
        if problems:
            return Item(AI_SEARCH_NUMBER, AI_SEARCH_ITEM, Status.ISSUE, "; ".join(problems))
        ld = None
        if self.index is not None:
            found = [o for o in self.index.ld_objects()
                     if "LocalBusiness" in str(o.get("@type"))]
            ld = found[0] if found else None
        missing = [k for k in ("name", "address", "telephone") if not (ld or {}).get(k)]
        if ld is None:
            wanted.append("the business's address and phone (LocalBusiness record)")
        elif missing:
            wanted.append("the LocalBusiness record's " + ", ".join(missing))
        if wanted:
            return Item(AI_SEARCH_NUMBER, AI_SEARCH_ITEM, Status.NEEDS_CLIENT,
                        "llms.txt and the crawler rules are published; still needed "
                        "from the client (client.json): " + "; ".join(wanted))
        return Item(AI_SEARCH_NUMBER, AI_SEARCH_ITEM, Status.OK,
                    f"llms.txt, a LocalBusiness record with name, address and phone, and "
                    f"robots.txt allowing {len(named)} AI crawlers by name (readiness, "
                    "not a ranking)")

    # -- 23, every report
    def media(self) -> Item:
        refs: list[tuple[str, str, str]] = []
        for page in self.pages.values():
            for node in page.root.find_all("img", "video"):
                for key in ("src", "data-src", "poster"):
                    if node.get(key):
                        refs.append((page.name, node.tag, node.get(key)))
            for node in page.root.find_all("source"):
                if node.get("src"):
                    refs.append((page.name, "source", node.get("src")))
                for candidate in node.get("srcset").split(","):
                    parts = candidate.split()
                    if parts:
                        refs.append((page.name, "source", parts[0]))
        problems = []
        for name, tag, src in refs:
            if src.startswith("data:"):
                continue
            rel = self.resolve(name, src)
            if rel is None:
                problems.append(f"{name}: <{tag}> loads {src[:70]} from another host")
            elif not self.live and not self.exists(rel):
                problems.append(f"{name}: <{tag}> {src[:70]} is missing from the site")
        if problems:
            return Item(MEDIA_NUMBER, MEDIA_ITEM, Status.ISSUE, "; ".join(problems[:4]))
        if not refs:
            return Item(MEDIA_NUMBER, MEDIA_ITEM, Status.NA,
                        "the pages load no images or video")
        return Item(MEDIA_NUMBER, MEDIA_ITEM, Status.OK,
                    f"{len(refs)} image and video reference(s), every one on this site"
                    + (" (hotlinks only: files past the fetch cap were not read)"
                       if self.live else " and present"))

    # -- 22, every report
    def content(self) -> Item:
        def issue(why: str) -> Item:
            return Item(CONTENT_NUMBER, CONTENT_ITEM, Status.ISSUE, why)

        if not self.pages:
            return issue("no HTML pages in the site")
        for page in self.pages.values():
            text = page.root.text(skip=("script", "style", "head", "template",
                                        "noscript"))
            tags = {n.tag for n in page.root.iter()}
            problem = (pagegate.structure_problem(page.doctype, tags, len(text.split()))
                       or pagegate.text_problem(text))
            if problem:
                return issue(f"{page.name}: {problem}")
        if (self.prov or {}).get("preview") and self.index is not None:
            robots = (self.index.meta("robots") or "").lower()
            if "noindex" not in robots:
                return issue("index.html: a preview must carry "
                             '<meta name="robots" content="noindex">')
            if "preview made by mapfront" not in _norm(self.index.root.text()):
                return issue('index.html: a preview must say "Preview made by Mapfront '
                             'for" the business')
        return Item(CONTENT_NUMBER, CONTENT_ITEM, Status.OK,
                    f"{len(self.pages)} page(s), each a whole HTML document about the "
                    "business" + (", marked as a preview and noindex"
                                  if (self.prov or {}).get("preview") else ""))

    # -- 21, chat packages only
    def chat_slug(self) -> str | None:
        """The slug the chat endpoint knows this site by; None when it cannot
        be told from where the files came from."""
        return None

    def chat(self) -> Item:
        def issue(why: str) -> Item:
            return Item(CHAT_NUMBER, CHAT_ITEM, Status.ISSUE, why)

        slug = self.chat_slug()
        if slug is None:
            return issue("the chat endpoint's slug for this site is not known, so "
                         "it cannot be checked")
        if not self.pages:
            return issue("no HTML pages in the site")
        without = sorted(
            name for name, page in self.pages.items()
            if not any(s.get("src").rsplit("/", 1)[-1] == CHAT_WIDGET
                       for s in page.root.find_all("script")))
        if without:
            return issue(f"no chat widget on {', '.join(without[:5])}")
        widget = self.read_bytes(CHAT_WIDGET)
        if widget is None:
            return issue(f"{CHAT_WIDGET} is missing")
        wanted = sitechat.endpoint(slug)
        if wanted.encode() not in widget:
            return issue(f"{CHAT_WIDGET} does not post to {wanted}")
        privacy = self.pages.get(PRIVACY)
        if privacy is None or "ai assistant" not in privacy.root.text().lower():
            return issue(f"{PRIVACY} does not say the chat is an AI assistant and "
                         "where what is typed goes")
        problems = sitechat.launch_problems(slug)
        if problems:
            return issue("; ".join(problems))
        return Item(CHAT_NUMBER, CHAT_ITEM, Status.OK,
                    f"widget on all {len(self.pages)} page(s), posting to {wanted}; "
                    f"the endpoint has this site's facts; {PRIVACY} names the assistant")


class _DiskAudit(_Audit):
    """A built site directory, as `agency.build_site` left it."""

    def __init__(self, site_dir: Path, provenance: dict[str, Any] | None) -> None:
        self.dir = site_dir
        pages: dict[str, Page] = {}
        read_errors: list[str] = []
        for path in sorted(site_dir.rglob("*.html")):
            if any(part.startswith(".") for part in path.relative_to(site_dir).parts):
                continue
            try:
                page = _load_page(site_dir, path)
            except OSError as exc:
                read_errors.append(f"{path.name}: {exc}")
                continue
            pages[page.name] = page
        super().__init__(pages, read_errors, provenance)

    def exists(self, rel: str) -> bool:
        target = (self.dir / rel).resolve()
        return target.is_relative_to(self.dir.resolve()) and target.is_file()

    def read_bytes(self, rel: str) -> bytes | None:
        if not self.exists(rel):
            return None
        try:
            return (self.dir / rel).read_bytes()
        except OSError:
            return None

    def is_client_photo(self, src: str, rel: str | None) -> bool:
        prov_photo = (self.prov or {}).get("team_photo")
        return bool(prov_photo) and rel == prov_photo

    def chat_slug(self) -> str | None:
        return self.dir.name


# ----------------------------------------------------------- a live site
# A published site (Webild, 2026-09-29) is not laid out like one of ours:
# `/privacy-policy` rather than privacy.html, `/about` with no extension,
# images on a CDN. Its pages are named by role so the same 20 checks read
# it, and what cannot be verified stays an ISSUE -- never an OK.

_ROLES = {
    "privacy": PRIVACY, "privacy-policy": PRIVACY, "privacypolicy": PRIVACY,
    "privacy-notice": PRIVACY,
    "terms": TERMS, "terms-of-service": TERMS, "terms-and-conditions": TERMS,
    "terms-of-use": TERMS, "tos": TERMS, "legal": TERMS,
    "contact": CONTACT, "contact-us": CONTACT,
    "thank-you": THANKS, "thanks": THANKS, "thankyou": THANKS,
}
_PAGE_EXTS = ("", ".html", ".htm")


def live_name(rel: str) -> str:
    """The page (or file) name a site-relative path stands for on a live
    site: `/` and `/x/` are index pages, `/about` is about.html, and the
    usual spellings of the privacy, terms, contact and thank-you pages are
    the names the checks look for. A file with another extension
    (robots.txt, an image) keeps its path."""
    rel = (rel or "").strip("/")
    if rel in ("", INDEX):
        return INDEX
    if rel.endswith("/" + INDEX):
        rel = rel[:-len(INDEX) - 1]
    stem, ext = posixpath.splitext(rel)
    if ext.lower() not in _PAGE_EXTS:
        return rel
    return _ROLES.get(stem.lower(), f"{stem}.html")


def is_page_path(rel: str) -> bool:
    """Whether a site-relative path names a page (not an asset)."""
    return posixpath.splitext((rel or "").strip("/"))[1].lower() in _PAGE_EXTS


@dataclass(frozen=True, slots=True)
class LiveSite:
    """What `livecheck.fetch_site` read off a published URL."""
    url: str                                   # the home page, as fetched
    pages: dict[str, str]                      # live_name -> HTML
    files: dict[str, bytes]                    # live_name or absolute URL -> bytes
    not_found: str                             # what the missing-page probe got
    errors: tuple[str, ...] = ()
    present: frozenset[str] = frozenset()      # same-host files that answered 200
    unfetched: frozenset[str] = frozenset()    # same-host links past the fetch cap


class _LiveAudit(_Audit):
    live = True

    def __init__(self, site: LiveSite, provenance: dict[str, Any] | None) -> None:
        self.site = site
        pages = {name: page_from_markup(name, html) for name, html in site.pages.items()}
        super().__init__(pages, list(site.errors), provenance)

    def resolve(self, page: str, href: str) -> str | None:
        rel = _resolve(page, href, self.site_host)
        return None if rel is None else live_name(rel)

    def exists(self, rel: str) -> bool:
        return (rel in self.site.pages or rel in self.site.files
                or rel in self.site.present)

    def unchecked(self, rel: str) -> bool:
        return rel in self.site.unfetched

    def read_bytes(self, rel: str) -> bytes | None:
        if rel in self.site.files:
            return self.site.files[rel]
        html = self.site.pages.get(rel)
        return None if html is None else html.encode()

    def offsite_bytes(self, url: str) -> bytes | None:
        return self.site.files.get(url)

    def is_client_photo(self, src: str, rel: str | None) -> bool:
        """The photo on the page is byte for byte the one the client gave
        (its sha256 is in the provenance). A photo that could not be
        fetched, or with no client photo on record, is not theirs."""
        wanted = (self.prov or {}).get("team_photo_sha")
        if not wanted:
            return False
        data = self.read_bytes(rel) if rel is not None else None
        if data is None:
            data = self.site.files.get(src)
        return data is not None and hashlib.sha256(data).hexdigest() == wanted

    def expects_analytics(self) -> bool:
        # ATLAS's PostHog key belongs to the sites ATLAS builds, not to a
        # site another builder published.
        return False

    def custom_404(self) -> Item:
        if NOT_FOUND not in self.pages:
            return Item(1, ITEMS[0], Status.ISSUE,
                        f"a missing page on {self.site.url} {self.site.not_found}, "
                        "not a 404 page")
        return super().custom_404()


def live_audit(site: LiveSite, provenance: dict[str, Any] | None) -> LaunchReport:
    """The 20 items over what was fetched from a published site. Blocking
    (parsing); run it through `asyncio.to_thread`. Never raises on a bad
    page -- whatever cannot be read is an ISSUE."""
    now = datetime.now(UTC)
    if INDEX not in site.pages:
        why = "; ".join(site.errors) or "the home page could not be read"
        return unreadable(site.url, why, now)
    audit = _LiveAudit(site, provenance)
    return LaunchReport(site.url, now, tuple(audit.run()))


def unreadable(where: str, why: str, now: datetime | None = None) -> LaunchReport:
    """Every item an ISSUE: nothing could be read, so nothing is OK."""
    return LaunchReport(where, now or datetime.now(UTC), tuple(
        Item(i, name, Status.ISSUE, why) for i, name in enumerate(ITEMS, start=1)))


def provenance_path(site_dir: Path) -> Path:
    return site_dir.parent / f".{site_dir.name}.launch.json"


def read_provenance(site_dir: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(provenance_path(site_dir).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def launch_audit(site_dir: Path | str) -> LaunchReport:
    """Audit the built site at `site_dir`. Blocking; never raises on a bad
    site — whatever cannot be read is an ISSUE."""
    site_dir = Path(site_dir)
    now = datetime.now(UTC)
    if not site_dir.is_dir():
        return unreadable(str(site_dir), f"site directory not found: {site_dir}", now)
    audit = _DiskAudit(site_dir, read_provenance(site_dir))
    return LaunchReport(str(site_dir), now, tuple(audit.run()))


def report_path(site_dir: Path) -> Path:
    """LAUNCH_CHECK.md sits BESIDE the site directory, never in it: deploy
    publishes the directory whole, and what the client still owes is not
    something for the client's visitors to read."""
    return site_dir.parent / f"{site_dir.name}.LAUNCH_CHECK.md"


def write_report(site_dir: Path, report: LaunchReport) -> Path:
    """Blocking."""
    path = report_path(site_dir)
    path.write_text(report.markdown(site_dir.name))
    return path
