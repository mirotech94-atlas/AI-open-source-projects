"""The words around each Academy promo Short: its hooks, title, tags and
hashtags, one entry per product, in code.

the owner, 2026-10-11: "make the animated shorts better using good tags and
hashtags that bring people in -- make the shorts actually wanting to watch
it." Until then each Short was titled with the bare product name and tagged
['AI', 'ATLAS Academy']. On 2026-10-11 the best of five had 71 views.

Why the copy is shaped this way. The evidence is from the YouTube Data API,
read on 2026-10-11 through the clips channel's token: search.list with
type=video, videoDuration=short, order=viewCount, publishedAfter=2026-06-01,
for "claude ai", "n8n automation", "ai agent business" and "obsidian claude",
then videos.list for the tags.

  * The top Shorts open on a claim or a question about the viewer's own
    work, never on a product name. Examples: "Stop wasting tokens on Claude
    Code" (45k), "How to build a second brain with Claude" (36k), "How To
    Set Up A Second Brain With Claude and Obsidian In 30 Seconds" (46k),
    "Claude Just Replaced n8n Workflow Building". So the first frame and the
    first line say what the viewer gets, and the product comes last.
  * Titles put the search word early (Claude, n8n, Obsidian, AI agent) and
    stay short. Every title here is 70 characters or fewer and is a single
    sentence, because `clips.promo.youtube_title` takes the first one.
  * Tagged Shorts carry 15 to 30 tags, mixing the broad word and long tails:
    "claude ai", "ai tools", "ai automation", "claude code tutorial",
    "n8n automation", "workflow automation", "no code ai", "claude
    obsidian", "second brain". YouTube puts the tags field at 500
    characters, and a tag with a space counts its two quotes.
  * Hashtags: three topical ones per post, plus #Shorts on YouTube. Lower
    case, so the brand guard (content.guard: a capital after a lower-case
    letter reads as a product name) passes them. None of these captions uses
    more than 4 hashtags. That keeps them inside the tightest per-post cap
    reported for Instagram (5, not verified here: the web search was
    unreachable on 2026-10-11).

What it never says: an income, a result, a number the catalog does not
hold, or that something is free when it is sold. Nor an absolute about
safety or money ("can't touch your money", "never sends"): the copy says
what the mechanism does -- it asks first, it drafts and waits -- because a
promise of "never" is one a buyer's own setup can break (the owner, 2026-10-11).
`tests/test_academy_reel` holds every entry to that.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .. import store_origin

log = logging.getLogger("academy.example.promo_copy")

STORE_URL = store_origin.url().rstrip("/")     # https://<host>/academy
TITLE_MAX = 70
TAGS_MAX_CHARS = 500
COMMON_TAGS = ("ATLAS Academy", "ai tools", "ai for beginners")


@dataclass(frozen=True)
class Copy:
    hooks: tuple[str, ...]     # the first beat, shown and said; one per day in turn
    title: str                 # the Short's title and the caption's first line
    tags: tuple[str, ...]      # the YouTube tags (COMMON_TAGS are added)
    hashtags: tuple[str, ...]  # 3 topical, lower case, without #Shorts
    series: str = ""           # tutorial_series key whose playlist the Short links


COPY: dict[str, Copy] = {
    "safe-ai-operator": Copy(
        hooks=("An AI that asks before it moves money.",
               "An AI agent that asks before it acts.",
               "Give AI the work. Keep the final say."),
        title="Build an AI agent that asks before it moves money (Claude + Python)",
        tags=("ai agent", "build an ai agent", "ai agent tutorial", "safe ai agent",
              "ai agent for business", "claude ai", "claude", "claude code",
              "anthropic claude", "python ai agent", "ai automation",
              "ai automation for business", "human in the loop", "ai safety",
              "ai operator", "agentic ai", "ai agents explained"),
        hashtags=("aiagents", "claudeai", "aiautomation"), series="claude"),
    "30-business-lessons": Copy(
        hooks=("Start a small AI service, one honest step at a time.",
               "Want a business that uses AI? Start small.",
               "One lesson, one step you can try today."),
        title="Start a small AI service business, one honest step at a time",
        tags=("small business", "start a business", "ai business", "ai side business",
              "ai service business", "business with ai", "ai for small business",
              "solopreneur", "business lessons", "ai automation", "claude ai",
              "chatgpt for business", "online business for beginners",
              "freelance with ai", "offer worksheet"),
        hashtags=("smallbusiness", "aitools", "entrepreneur"), series="business"),
    "claude-obsidian-vault-kit": Copy(
        hooks=("Let Claude run your week from Obsidian.",
               "Your whole business, in one Obsidian vault.",
               "A weekly review Claude can actually read."),
        title="Let Claude run your week with Obsidian (starter vault kit)",
        tags=("claude obsidian", "obsidian", "obsidian claude", "claude ai",
              "claude", "second brain", "obsidian second brain", "obsidian template",
              "obsidian vault", "obsidian for business", "claude prompts",
              "claude tutorial", "claude projects", "productivity system",
              "weekly review", "markdown notes", "anthropic claude"),
        hashtags=("claudeai", "obsidian", "secondbrain"), series="claude"),
    "ai-for-every-age": Copy(
        hooks=("Teach your parents AI. Safely.",
               "AI at home, without falling for the scams.",
               "Large-print AI lessons for readers over 60."),
        title="Teach your parents AI safely: large-print lessons for 60+",
        tags=("ai for seniors", "ai for beginners", "ai for parents",
              "how to use ai safely", "ai safety at home", "large print",
              "seniors and technology", "ai scams", "claude ai", "claude for beginners",
              "how to use claude", "ai explained simply", "ai for adults",
              "everyday ai", "ai for older adults"),
        hashtags=("aiforbeginners", "seniors", "claudeai"), series="claude"),
    "local-business-automation-pack": Copy(
        hooks=("Four n8n automations that draft and wait for you.",
               "Missed a call? Your draft reply is ready.",
               "Automate the busywork. Keep the last click."),
        title="4 n8n automations for a local business that draft and wait for you",
        tags=("n8n", "n8n automation", "n8n workflow", "n8n tutorial",
              "n8n for beginners", "workflow automation", "ai automation",
              "ai automation for small business", "small business automation",
              "local business", "missed call text back", "review request automation",
              "lead form automation", "no code automation", "n8n templates",
              "self hosted n8n", "docker"),
        hashtags=("n8n", "aiautomation", "smallbusiness"), series="n8n"),
    "family-ai-scam-defence-workbook": Copy(
        hooks=("Does your family have a safe word?",
               "AI can copy a voice. Make your plan first.",
               "A plan before the phone rings."),
        title="Make a family safe word before an AI voice scam calls",
        tags=("ai voice scam", "voice cloning scam", "family safe word",
              "ai scams", "phone scam", "scam prevention", "protect your parents",
              "elder fraud", "grandparent scam", "deepfake voice", "family safety",
              "ai safety", "scam awareness", "printable workbook", "callback rule"),
        hashtags=("scamawareness", "aisafety", "familysafety")),
    "sell-with-ai": Copy(
        hooks=("Sell online with AI doing the repetitive work.",
               "Three ways one person sells with AI.",
               "Selling online alone? Let AI do the busywork."),
        title="Sell online with AI doing the repetitive work (no hype)",
        tags=("sell with ai", "ai for selling", "digital products", "sell digital products",
              "online store", "clipping", "ai side hustle", "one person business",
              "solopreneur", "ai tools", "claude ai", "ai automation",
              "ai for small business", "make a digital product", "start an online store"),
        hashtags=("aitools", "digitalproducts", "solopreneur"), series="business"),
}


def for_slug(slug: str) -> Copy | None:
    return COPY.get(slug)


def hook_for(slug: str, day: int) -> str | None:
    """The day's hook: the product's hooks in turn, so its next Short opens
    differently."""
    c = COPY.get(slug)
    return c.hooks[day % len(c.hooks)] if c else None


def tags_for(slug: str) -> list[str]:
    """The YouTube tags: the product's own, then the common ones. Each is
    kept only while the whole set fits YouTube's 500-character count."""
    c = COPY.get(slug)
    out: list[str] = []
    for tag in (c.tags if c else ()) + COMMON_TAGS:
        if tag.lower() in (t.lower() for t in out):
            continue
        if tags_chars(out + [tag]) <= TAGS_MAX_CHARS:
            out.append(tag)
    return out


def tags_chars(tags: list[str]) -> int:
    """YouTube's count for the tags field: the commas between tags, and two
    quotes around any tag with a space in it."""
    return sum(len(t) + (2 if " " in t else 0) for t in tags) + max(0, len(tags) - 1)


def hashtag_line(slug: str) -> str:
    c = COPY.get(slug)
    return " ".join(f"#{h}" for h in (c.hashtags if c else ("aitools", "learnai")))


def product_url(slug: str) -> str:
    return f"{STORE_URL}/{slug}/"


async def tutorial_url(slug: str) -> str | None:
    """The matching tutorial playlist, only when the channel really has
    one: a public episode of the series with its playlist, read now from
    academy_tutorial_posts (never a guessed link). None otherwise."""
    c = COPY.get(slug)
    if c is None or not c.series:
        return None
    from ...kernel.state import fetch_one
    try:
        row = await fetch_one(
            """SELECT playlist_id FROM academy_tutorial_posts
                WHERE series = $1 AND public AND NOT withdrawn
                  AND coalesce(playlist_id, '') <> ''
                ORDER BY at DESC LIMIT 1""", c.series)
    except Exception as exc:                            # noqa: BLE001
        log.warning("tutorial playlist for %s unreadable (%s)", slug, type(exc).__name__)
        return None
    if row is None:
        return None
    return f"https://www.youtube.com/playlist?list={row['playlist_id']}"
