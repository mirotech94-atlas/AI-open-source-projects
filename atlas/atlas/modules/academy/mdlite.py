"""A small Markdown subset to HTML, standard library only, for the blog.

Ported from the site pack's tools/build_blog.py (Miro, 2026-10-18), typed:

* `#`, `##`, `###` headings (a post's `#` becomes <h2>: the page's <h1> is
  the post title), paragraphs, `-` and `1.` lists, `- [ ]` checklist items,
  `>` quotes, ``` fences, pipe tables;
* inline `code`, **bold**, *em* and [text](url) links.

There is no HTML passthrough: every character of the source is escaped. A
link keeps its target only when it is site-relative (`/path`, resolved
against `root`), an in-page `#anchor`, `https://` or `mailto:`; any other
scheme (javascript:, data:, http:) renders as its plain text.
"""
from __future__ import annotations

import re
from html import escape

_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_EM = re.compile(r"(?<![*\w])\*(?![*\s])(.+?)(?<![*\s])\*(?![*\w])")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_HEADING = re.compile(r"^(#{1,3}) (.*)$")
_ITEM = re.compile(r"^(-|\d+\.) ")
_TASK = re.compile(r"^\[( |x|X)\] ")
_RULE = re.compile(r"^:?-+:?$")
_BLOCK_START = re.compile(r"^(#{1,3} |- |\d+\. |> |```|\|)")


def safe_href(url: str, root: str = "") -> str | None:
    """The link target as the page writes it, or None when it isn't allowed."""
    if url.startswith("/") and not url.startswith("//"):
        return root + url[1:]
    if url.startswith(("#", "https://", "mailto:")):
        return url
    return None


def inline(text: str, root: str = "") -> str:
    """One line of inline Markdown. Code spans are set aside first so that
    nothing inside them is read as emphasis or a link."""
    codes: list[str] = []

    def keep(m: re.Match[str]) -> str:
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"

    s = escape(_CODE.sub(keep, text), quote=False)
    s = _BOLD.sub(r"<strong>\1</strong>", s)
    s = _EM.sub(r"<em>\1</em>", s)

    def link(m: re.Match[str]) -> str:
        label, raw = m.group(1), m.group(2).replace("&amp;", "&")
        href = safe_href(raw, root)
        if href is None:
            return label
        rel = ' rel="noopener"' if href.startswith("https://") else ""
        return f'<a href="{escape(href, quote=True)}"{rel}>{label}</a>'

    s = _LINK.sub(link, s)
    return re.sub(r"\x00(\d+)\x00",
                  lambda m: f"<code>{escape(codes[int(m.group(1))], quote=False)}</code>", s)


def _list(lines: list[str], i: int, root: str) -> tuple[str, int]:
    ordered = lines[i][0].isdigit()
    items: list[str] = []
    task = False
    while i < len(lines) and _ITEM.match(lines[i]):
        text = _ITEM.sub("", lines[i], count=1)
        if (m := _TASK.match(text)) and not ordered:
            task = True
            done = m.group(1) != " "
            box = '<span class="md-box" aria-hidden="true"></span>'
            state = "md-task md-done" if done else "md-task"
            items.append(f'<li class="{state}">{box}{inline(text[m.end():], root)}</li>')
        else:
            items.append(f"<li>{inline(text, root)}</li>")
        i += 1
    tag = "ol" if ordered else "ul"
    cls = ' class="md-tasks"' if task else ""
    return f"<{tag}{cls}>{''.join(items)}</{tag}>", i


def _table(lines: list[str], i: int, root: str) -> tuple[str, int]:
    rows: list[list[str]] = []
    while i < len(lines) and lines[i].startswith("|"):
        rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
        i += 1
    rows = [r for r in rows if not all(_RULE.match(c) for c in r)]
    if not rows:
        return "", i
    head = "".join(f"<th>{inline(c, root)}</th>" for c in rows[0])
    body = "".join("<tr>" + "".join(f"<td>{inline(c, root)}</td>" for c in r) + "</tr>"
                   for r in rows[1:])
    return (f'<div class="md-table"><table><thead><tr>{head}</tr></thead>'
            f"<tbody>{body}</tbody></table></div>"), i


def to_html(md: str, root: str = "") -> str:
    """The Markdown subset as HTML. `root` prefixes a site-relative link."""
    out: list[str] = []
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            buf: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(lines[i])
                i += 1
            out.append(f"<pre><code>{escape(chr(10).join(buf), quote=False)}</code></pre>")
            i += 1
            continue
        if m := _HEADING.match(ln):
            level = max(2, len(m.group(1)))
            out.append(f"<h{level}>{inline(m.group(2), root)}</h{level}>")
            i += 1
            continue
        if ln.startswith("> "):
            quote: list[str] = []
            while i < len(lines) and lines[i].startswith("> "):
                quote.append(lines[i][2:])
                i += 1
            out.append(f"<blockquote><p>{inline(' '.join(quote), root)}</p></blockquote>")
            continue
        if _ITEM.match(ln):
            html, i = _list(lines, i, root)
            out.append(html)
            continue
        if ln.startswith("|"):
            html, i = _table(lines, i, root)
            out.append(html)
            continue
        if not ln.strip():
            i += 1
            continue
        para: list[str] = []
        while i < len(lines) and lines[i].strip() and not _BLOCK_START.match(lines[i]):
            para.append(lines[i])
            i += 1
        out.append(f"<p>{inline(' '.join(para), root)}</p>")
    return "\n".join(out)


def plain(md: str) -> str:
    """The words of a Markdown text, without its marks (for search and
    word counts)."""
    s = _LINK.sub(r"\1", md)
    s = re.sub(r"[`*#>|]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


__all__ = ["inline", "plain", "safe_href", "to_html"]
