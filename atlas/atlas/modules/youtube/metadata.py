"""youtube.metadata — draft upload metadata (title, description, tags,
optional chapters) from raw source material.

Nothing here talks to Google. It reuses `atlas.modules.content.sources.gather`
for the four source kinds already established there (`voice:<n>`, `weekly`,
`slack:<thread_ts>`, `note:<text>`) — every one of them already wraps its
result with `wrap_untrusted` before this module ever sees it (invariant 4),
and duplicating that logic here would just be a second, driftable copy of the
same four cases. `youtube.draft_metadata` (the Tier AUTO action in
`__init__.py`) is what turns the dict this module returns into a
`content_drafts` row; this module only shapes the metadata.

Two things are enforced in code after the model answers, not left to the
prompt, because a prompt instruction is not a control:
  * the business's own link is appended to the description if the model left
    it out (never invented for a business this module does not know)
  * a plain, one-line disclosure is appended whenever the SOURCE material
    itself mentions an affiliate or referral link, whether or not the model
    noticed
"""
from __future__ import annotations

import re

from ...kernel.llm import Message, Need, think
from ..content.sources import gather

TITLE_MAX = 100
DESCRIPTION_MAX = 5000
TAGS_MAX = 15

# The site link appended to a business's description. FlyCheap and Guardian
# were removed on 2026-10-29 (the owner: "we don't use them anymore"); a business
# added here gets its link on every draft.
BUSINESS_LINKS: dict[str, str] = {}

DISCLOSURE = (
    "Disclosure: some of the links above are affiliate links — I may earn a "
    "small commission at no extra cost to you.")

# Deliberately short and named, not inferred: a marker list this narrow will
# under-flag before it over-flags, and under-flagging is a decision to
# revisit with real source material rather than widen blindly.
_AFFILIATE_MARKERS = re.compile(
    r"affiliate|\bref=|\baffid=|\baff_id=|\bpartner link\b", re.IGNORECASE)

SYSTEM = (
    "You write YouTube upload metadata for the owner's channel, from the source "
    "material below (a script, a voice note, a call, a thread). Reply with "
    "exactly four lines, in this order, and nothing else:\n"
    "TITLE: <at most 100 characters>\n"
    "DESCRIPTION: <at most 5000 characters; use \\n for a line break>\n"
    "TAGS: <comma-separated, at most 15>\n"
    "CHAPTERS: <'HH:MM Label' pairs separated by ' | ', or 'none'>\n"
    "Use only facts present in the material below. Never invent a statistic, "
    "a price, a date or a claim that is not there."
)


def _parse(text: str) -> dict:
    fields: dict = {"title": "", "description": "", "tags": [], "chapters": None}
    for line in text.splitlines():
        if line.startswith("TITLE:"):
            fields["title"] = line[len("TITLE:"):].strip()
        elif line.startswith("DESCRIPTION:"):
            fields["description"] = (
                line[len("DESCRIPTION:"):].strip().replace("\\n", "\n"))
        elif line.startswith("TAGS:"):
            fields["tags"] = [
                t.strip() for t in line[len("TAGS:"):].split(",") if t.strip()]
        elif line.startswith("CHAPTERS:"):
            raw = line[len("CHAPTERS:"):].strip()
            fields["chapters"] = None if raw.lower() in ("", "none") else raw
    return fields


async def draft_metadata(business: str, source: str) -> dict:
    """Returns `{title, description, tags, chapters, business, source}`, or
    `{"error": "..."}`. Never raises: a gather failure or an empty model
    answer is reported, not guessed around."""
    wrapped, reason = await gather(source)
    if reason is not None:
        return {"error": f"could not gather {source!r}: {reason}"}

    completion = await think(
        Need.DRAFT, SYSTEM, [Message("user", wrapped)], max_tokens=800)
    fields = _parse(completion.text)
    if not fields["title"] or not fields["description"]:
        return {"error": "the model returned incomplete metadata"}

    title = fields["title"][:TITLE_MAX]
    description = fields["description"]

    link = BUSINESS_LINKS.get(business)
    if link and link not in description:
        description = f"{description}\n\n{link}"

    if _AFFILIATE_MARKERS.search(wrapped) and DISCLOSURE not in description:
        description = f"{description}\n\n{DISCLOSURE}"

    description = description[:DESCRIPTION_MAX]
    tags = fields["tags"][:TAGS_MAX]

    return {
        "title": title,
        "description": description,
        "tags": tags,
        "chapters": fields["chapters"],
        "business": business,
        "source": source,
    }
