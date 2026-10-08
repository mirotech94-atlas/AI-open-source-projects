"""The hard gate every client page passes before it is written or deployed.

2026-09-30: a preview published at eco-electrical-services-llc-38.mapfront.io
had, as its body copy, a model's summary of the raw design-tool output it had
been handed ("The data contains a JSON object ... an error message from a
tool called GetLayers, indicating an input validation issue"). Nothing between
the model and the public URL read the page as a page. This does, in three
places: `mockup.make_mockup` before it writes, `build.build_site` before it
copies, and `launchcheck` item 22, which `agency.deploy_site` obeys.

Two questions, both answered from the markup alone:

* is it a whole HTML document -- a doctype, <html>, <head>, <title>, <body>,
  and enough visible words to be a page rather than a fragment;
* does its VISIBLE text read like tool output. Only visible text: every page
  carries `<script type="application/ld+json">`, and "json" in an attribute
  or a script is not something a visitor reads. Whole words, any case.

No dependencies beyond the stdlib, so the builder, the launch check and the
tests can all import it without an import cycle.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

# What a page about a plumber never says to its visitors, and what a model
# describing its own inputs always does. "tool" is whole-word: "tools" (a
# hardware shop's stock) is not matched.
TOOL_VOCAB = re.compile(
    r"\bjson\b|\bgetlayers\b|\bdata contains\b|\bvalidation\b|\berror messages?\b|"
    r"\bmcp\b|\btool\b|\buntrusted[_ ]data\b|\bas an ai\b|\blanguage model\b|"
    r"\bstack ?trace\b|\btraceback\b",
    re.IGNORECASE)

MIN_WORDS = 12          # a real 404 page is short; an empty shell is shorter
REQUIRED_TAGS = ("html", "head", "title", "body")
_INVISIBLE = {"script", "style", "template", "noscript", "head"}


def text_problem(text: str) -> str | None:
    """Why this visible text reads like tool output, or None."""
    match = TOOL_VOCAB.search(text or "")
    if match:
        start = max(0, match.start() - 30)
        context = " ".join((text or "")[start:match.end() + 30].split())
        return f"reads like tool output ({match.group(0)!r} in \"{context}\")"
    return None


def structure_problem(doctype: bool, tags: set[str], words: int) -> str | None:
    """Why this is not a whole page, or None."""
    if not doctype:
        return "not a full HTML document: it does not start with <!doctype html>"
    missing = [t for t in REQUIRED_TAGS if t not in tags]
    if missing:
        return f"not a full HTML document: no <{missing[0]}>"
    if words < MIN_WORDS:
        return f"not a page: only {words} visible word(s)"
    return None


def has_doctype(markup: str) -> bool:
    return (markup or "").lstrip()[:15].lower().startswith("<!doctype html")


class _Reader(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: set[str] = set()
        self.parts: list[str] = []
        self._hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.add(tag)
        if tag in _INVISIBLE:
            self._hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _INVISIBLE and self._hidden:
            self._hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self._hidden:
            self.parts.append(data)


def visible_text(markup: str) -> tuple[str, set[str]]:
    reader = _Reader()
    reader.feed(markup or "")
    reader.close()
    return " ".join(" ".join(reader.parts).split()), reader.tags


def page_problem(markup: str) -> str | None:
    """Why `markup` must not be published as a page, or None when it may."""
    text, tags = visible_text(markup)
    return (structure_problem(has_doctype(markup), tags, len(text.split()))
            or text_problem(text))
