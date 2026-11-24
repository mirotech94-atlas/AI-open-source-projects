"""Campaign records, and the content rule no campaign gets past.

A campaign is one brand's clipping job on Clipping Culture (Whop Content
Rewards): its official footage, its rules doc, its pay per 1,000 views.
The structured fields below were read out of each public rules doc and
reviewed by hand on 2026-09-28; the doc itself is kept in `rules/` and in
`clip_campaigns.rules_text` as untrusted DATA (invariant 4) -- it reaches a
prompt only through `wrap_untrusted`, and nothing is ever parsed out of it
at runtime.

**The content rule** (Miro, 2026-09-28): gaming, trends, business and
streamer clips only; no dating, nothing edgy. `refusal()` is that rule in
code. It is asked when a campaign is seeded and again when one is
activated, and `cut` asks it a third time, so a campaign that touches
dating, gambling, betting, crypto or adult content can never be clipped
whatever its row says. It reads the campaign's own structured words (name,
category, caption samples, on-screen lines, hashtags, handles), never the
raw doc: both live docs forbid "NSFW" and "sexual content", and a scan of
the prohibitions would refuse the very campaigns that obey the rule.

**Permission** (Miro, 2026-09-29): a campaign is clipped only with the
footage owner's say-so, named in `permission_source` -- `whop:<program or
url>` for a paid clipping program, `written:<who, date>` for a written OK.
A campaign without one is never seeded, activated, registered or cut. There
is no path here that fetches a creator's video: footage is only ever a
registered file in a campaign's own folder.

**Format and accounts**: `preset` is how a clip fills 9:16 (`blur`, `crop`
or `split`, a closed set), within the `presets_allowed` its rules leave;
`max_accounts` is how many accounts on EACH platform may post its clips.
`cross_post_allowed` is whether ONE clip may go up on more than one
account (the lane's Instagram, TikTok and YouTube are three): False unless
a reviewed doc allows it. Both live docs forbid it ("do not copy the same
video across multiple accounts; post fresh content on each page"), so each
of their clips posts on one platform only -- the one that pays the most
(`cut.choose_platform`).

**Numbers** carry where they came from and when (invariant 6). The budgets
seeded here are Miro's own reading of the Whop dashboard on 28 Sep, stored
with that date and that source; `status` stops quoting one once it is a
week old, and says it is unknown instead.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from ...kernel.contracts import ActionResult
from ...kernel.state import execute_sql, fetch_all, fetch_one

RULES_DIR = Path(__file__).resolve().parent / "rules"
STATUSES = ("active", "paused", "ended")
PLATFORMS = ("instagram", "tiktok", "youtube")
PRESETS = ("blur", "crop", "split")          # db CHECK says the same
# The brand assets a campaign's rules may require on every clip, a closed
# set: `logo` (bottom-right) and `banner` (a top strip), each a PNG Miro
# placed in data/clips/<slug>/brand/ -- never fetched from a brand's site.
BRAND_ASSETS = ("logo", "banner")
# Whop's draft upload takes an MP4 of at most this many MB (Irubyana,
# 2026-10-01: her clip files were refused over it); a campaign may set its
# own `max_file_mb`, and the cut's encode is sized to land under it.
DEFAULT_MAX_FILE_MB = 256
MAX_FILE_MB_CEILING = 1024
# Who allowed ATLAS to clip the footage: a paid program, or a written OK.
PERMISSION = re.compile(r"^(whop|written):\S.*$")

# Miro's allowlist. A category not named here is refused, not waved through.
ALLOWED_CATEGORIES = frozenset({"gaming", "trends", "business", "streamer"})

# What the content rule refuses, as whole words: "update" is not "date",
# "better" is not "bet", "Warhammer" is not "war" (and war is not refused).
FORBIDDEN: tuple[tuple[str, str], ...] = (
    (r"dating|double[\s-]*date|tinder|bumble|hinge|matchmak\w*|hook[\s-]?ups?",
     "dating"),
    ((r"gambl\w*|casinos?|slot[\s-]?machines?|poker|roulette|blackjack|lotter\w*|"
      r"jackpots?"), "gambling"),
    ((r"bet|bets|betting|bettor\w*|sportsbooks?|wager\w*|odds[\s-]?boost\w*|"
      r"parlays?"), "betting"),
    ((r"crypto\w*|bitcoin|btc|ethereum|eth|nfts?|memecoins?|altcoins?|"
      r"blockchain|web3|airdrops?|token[\s-]?sale"), "crypto"),
    ((r"adult|nsfw|porn\w*|onlyfans|fansly|xxx|18\+|nude\w*|nudity|sexy|"
      r"sexual\w*|erotic\w*|escorts?|strip[\s-]?clubs?"), "adult content"),
)
_FORBIDDEN = tuple((re.compile(rf"(?<![\w])(?:{pat})(?![\w])", re.IGNORECASE), why)
                   for pat, why in FORBIDDEN)


def _norm_text(text: str) -> str:
    """The doc as reviewed: BOM, CRLF and trailing spaces out, so the hash
    is of the words, not of Google's line endings."""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip() + "\n"


def rules_hash(text: str) -> str:
    return hashlib.sha256(_norm_text(text).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Campaign:
    """One campaign, as the code reads it. Built from a seed or a row."""

    slug: str
    name: str
    category: str
    status: str = "paused"
    whop_url: str | None = None
    rules_doc_id: str | None = None
    source_folder: str | None = None
    platforms: tuple[str, ...] = ()
    rates: dict[str, float] = field(default_factory=dict)
    tag_handles: dict[str, str] = field(default_factory=dict)
    disclosure: str = "#Ad"
    # {"samples": {key: text}, "require": [regex], "forbid": [regex],
    #  "hashtags": ["#Tag", ...], "wording": "samples" | "free"} -- see
    #  captions.py; a missing "wording" is "samples" (no hooks)
    caption_rules: dict[str, Any] = field(default_factory=dict)
    # {"mode": "none" | "approved_lines", "lines": [...], "forbid": [regex]}
    onscreen_rules: dict[str, Any] = field(default_factory=dict)
    music_allowed: bool = False
    min_seconds: int | None = None
    max_seconds: int = 60
    audience: str = ""
    rules_text: str | None = None
    rules_sha256: str | None = None
    rules_reviewed_on: date | None = None
    budget_usd: float | None = None
    budget_read_at: datetime | None = None
    budget_source: str | None = None
    permission_source: str | None = None
    max_accounts: int = 1
    preset: str = "blur"
    presets_allowed: tuple[str, ...] = PRESETS
    # May one clip go up on more than one account? Read from the reviewed
    # doc by hand; False (one platform per clip) unless it says so.
    cross_post_allowed: bool = False
    # The brand assets its rules require on every clip (`BRAND_ASSETS`):
    # the cut composites them from the campaign's brand/ folder and makes
    # nothing without them; a clip cut without them never posts.
    brand_assets: tuple[str, ...] = ()
    # Its rules want the brand's approval of a DRAFT on Whop before a clip
    # is posted (Irubyana): the cut lands at draft_pending, the Mac agent
    # uploads it there, and only a draft_approved clip posts.
    approval_before_post: bool = False
    # The largest file its drafts and posts may be, in MB.
    max_file_mb: int = DEFAULT_MAX_FILE_MB

    @property
    def rules_pending(self) -> bool:
        """No reviewed rules doc: nothing about this campaign may be cut."""
        return not (self.rules_doc_id and self.rules_text and self.min_seconds)

    def words(self) -> list[str]:
        """Everything the content rule reads: the campaign's own words."""
        rules = self.caption_rules or {}
        onscreen = self.onscreen_rules or {}
        return [self.name, self.category, self.slug,
                *(str(v) for v in (rules.get("samples") or {}).values()),
                *(str(t) for t in rules.get("hashtags") or ()),
                *(str(v) for v in (onscreen.get("lines") or ())),
                *(str(v) for v in self.tag_handles.values())]


def refusal(campaign: Campaign) -> str | None:
    """Why the content rule refuses this campaign, or None. Pure."""
    if campaign.category not in ALLOWED_CATEGORIES:
        return (f"category {campaign.category!r} is outside Miro's rule "
                f"(only {', '.join(sorted(ALLOWED_CATEGORIES))})")
    for text in campaign.words():
        for pattern, why in _FORBIDDEN:
            if (hit := pattern.search(text or "")) is not None:
                return (f"it touches {why} ({hit.group(0)!r}); Miro's rule: "
                        f"gaming, trends, business or streamer clips only")
    return None


def permission_refusal(campaign: Campaign) -> str | None:
    """Why this campaign has no usable permission to clip, or None. Pure."""
    source = (campaign.permission_source or "").strip()
    if not PERMISSION.match(source):
        return (f"{campaign.name} names no permission source ('whop:<program>' "
                f"or 'written:<who, date>'); nothing of it is clipped without "
                f"the footage owner's say-so")
    return None


def format_refusal(campaign: Campaign) -> str | None:
    """Why this campaign's preset or account limit is not one the code
    knows, or None. Pure."""
    allowed = tuple(campaign.presets_allowed)
    if not allowed or any(p not in PRESETS for p in allowed):
        return (f"{campaign.name}'s allowed presets {list(allowed)} are not a "
                f"subset of {', '.join(PRESETS)}")
    if campaign.preset not in PRESETS:
        return f"preset {campaign.preset!r} is not one of {', '.join(PRESETS)}"
    if campaign.preset not in allowed:
        return (f"{campaign.name}'s rules allow only {', '.join(allowed)}, not "
                f"{campaign.preset!r}")
    if isinstance(campaign.max_accounts, bool) or int(campaign.max_accounts) < 1:
        return f"max_accounts must be at least 1, not {campaign.max_accounts!r}"
    if any(a not in BRAND_ASSETS for a in campaign.brand_assets):
        return (f"{campaign.name}'s brand assets {list(campaign.brand_assets)} are "
                f"not a subset of {', '.join(BRAND_ASSETS)}")
    if (isinstance(campaign.max_file_mb, bool)
            or not 1 <= int(campaign.max_file_mb) <= MAX_FILE_MB_CEILING):
        return (f"max_file_mb must be 1 to {MAX_FILE_MB_CEILING}, not "
                f"{campaign.max_file_mb!r}")
    return None


def activation_refusal(campaign: Campaign) -> str | None:
    """Why this campaign may not be set active, or None."""
    if (why := refusal(campaign)) is not None:
        return why
    if (why := permission_refusal(campaign)) is not None:
        return why
    if (why := format_refusal(campaign)) is not None:
        return why
    if campaign.rules_pending:
        return (f"{campaign.name}'s rules are pending (no reviewed rules doc); "
                f"it stays paused until they are parsed and reviewed")
    return None


# ------------------------------------------------------------------ seeds

def _rules(slug: str) -> str:
    return _norm_text((RULES_DIR / f"{slug}.txt").read_text(encoding="utf-8"))


REVIEWED_ON = date(2026, 9, 28)
BUDGET_READ = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
BUDGET_SOURCE = "Miro's reading of the Whop campaign page (Clipping lane plan)"
# All three are Clipping Culture campaigns on Whop (Content Rewards): the
# brand pays for clips of its own footage. whop_url is the campaign's own
# share link, copied from its Whop page (2026-09-29); the Mac agent submits
# to that page only.
CLIPPING_CULTURE = "whop:clipping-culture"

ATLUS = Campaign(
    slug="atlus-persona-metaphor",
    whop_url="https://contentrewards.com/discover/97baa7f1-f953-4c09-b608-929ad0b48dbe",
    name="Atlus: Persona & Metaphor (ATLUS Developer Sale)",
    category="gaming",
    status="active",
    rules_doc_id="1Gfm6w8IpPOBSs1185WsduRruZ4t_2XfkMGQNWeRxC0Y",
    source_folder=("https://next.frame.io/share/c8b33ced-1cd1-48fc-9bb5-8eba03cb146b/"
                   "c0f4a86e-3d8c-44e3-aadc-eba7c84dcfaa"),
    platforms=("instagram", "tiktok", "youtube"),
    rates={"instagram": 2.60, "tiktok": 3.40, "youtube": 3.40},
    tag_handles={"instagram": "@atlus_west", "tiktok": "@atlus_west",
                 "youtube": "@atlus"},
    caption_rules={
        # The doc's own samples. "general" suits any clip; the three others
        # only a clip that visibly shows that game (the doc's rule).
        "samples": {
            "general": ("Save up to 75% on select ATLUS titles during the "
                        "ATLUS Developer Sale on Steam."),
            "persona3": ("Enter the Dark Hour and seal your fate with PERSONA 3 "
                         "RELOAD, now 70% off during the ATLUS Developer Sale on "
                         "Steam for the first time ever!"),
            "persona5": ("Don the mask and change the world with PERSONA 5 "
                         "ROYAL, now 70% off during the ATLUS Developer Sale on "
                         "Steam!"),
            "metaphor": ("Awaken your Archetype and explore a fantasy world "
                         "unlike any you've ever seen in METAPHOR: REFANTAZIO, "
                         "now 60% off during the ATLUS Developer Sale on Steam! "
                         "Also coming soon to the Nintendo Switch 2 - pre-order "
                         "today!"),
        },
        # Must mention the sale and the discount.
        "require": [r"ATLUS Developer Sale", r"\d+\s?%"],
        "forbid": [],
        "hashtags": ["#ATLUS", "#Persona", "#Persona3Reload", "#Persona5Royal",
                     "#MetaphorReFantazio", "#SteamSale"],
        # What a clip gets unless Miro picks otherwise: the line that is
        # true of any clip in the folder, and tags that fit every one.
        "default_sample": "general",
        "default_hashtags": ["#ATLUS", "#SteamSale"],
        # The doc: "Exact wording is not required -- use the samples below as
        # a starting point and adjust naturally". So a hook may open one.
        "wording": "free",
    },
    # On-screen text is optional; ATLAS adds none, and never a logo.
    onscreen_rules={"mode": "none", "lines": [], "forbid": []},
    music_allowed=True,             # copyright-free only; ATLAS adds none
    min_seconds=10,
    max_seconds=60,                 # the doc sets none; ATLAS's own ceiling
    audience=("at least 50% US or Canada audience, on the page or on the post "
              "itself; a screen recording goes to the campaign's form"),
    rules_reviewed_on=REVIEWED_ON,
    budget_usd=18300.0, budget_read_at=BUDGET_READ, budget_source=BUDGET_SOURCE,
    permission_source=CLIPPING_CULTURE,
    # The doc names no account limit, only "post fresh content on each
    # page": one account per platform until it says more.
    max_accounts=1,
    # "do not copy the same video across multiple accounts; post fresh
    # content on each page": one platform per clip.
    cross_post_allowed=False,
)

COD = Campaign(
    slug="cod-mw4-warzone-operator",
    whop_url="https://contentrewards.com/discover/91c4990a-b37e-4c59-8232-8bb1ae8d9a94",
    name="Call of Duty MW4: Warzone Operator Skin toggle",
    category="gaming",
    status="active",
    rules_doc_id="1zxrsCRCZWqE-RpAGTMzGMKfQ2oMrWYkBwsnXf99fcVE",
    source_folder="https://app.mediasilo.com/review/6ab59118da53275cf5f972c8",
    platforms=("instagram", "tiktok"),
    rates={"instagram": 2.00, "tiktok": 2.00},
    tag_handles={"instagram": "@callofduty", "tiktok": "@callofduty"},
    caption_rules={
        "samples": {
            "official": ("Starting with Season 1 of Modern Warfare 4, a new "
                         "Operator Skin toggle gives players more control over "
                         "the Operators they see in Call of Duty: Warzone only, "
                         "while Carry Forward continues to provide access to "
                         "previously earned and purchased content."),
            "season1": ("Call of Duty: Warzone is adding a new Operator Skin "
                        "toggle starting Season 1"),
            "control": ("New Operator Skin toggle gives players more control "
                        "over the Operators they see in Warzone"),
            "your_way": ("See Warzone your way with the new Operator Skin "
                         "toggle, starting Season 1"),
            "choice": ("More player choice in Warzone with the new Operator "
                       "Skin toggle"),
        },
        # Must describe the toggle, in Warzone; must not put it in MW4
        # multiplayer or frame it as removing or banning skins.
        "require": [r"Operator Skin toggle", r"Warzone"],
        "forbid": [r"multiplayer", r"\bremov\w*", r"\bban\w*", r"\bcriticiz\w*"],
        "hashtags": ["#Warzone", "#CallofDuty", "#CODWarzone"],
        "default_sample": "official",
        "default_hashtags": ["#Warzone", "#CallofDuty"],
        # Word for word, one of the samples: no hook, no "mog".
        "wording": "samples",
    },
    # Word for word, one of these; ATLAS never writes its own.
    onscreen_rules={
        "mode": "approved_lines",
        "lines": ["Operator Toggle Introduced for Call of Duty: Warzone",
                  "Call of Duty: Warzone Season 1 Adds a New Operator Skin Toggle",
                  ("See Call of Duty: Warzone Your Way with the New Operator "
                   "Skin Toggle")],
        # Carry Forward is caption-only; burned captions are on-screen text.
        "forbid": [r"carry\s*forward"],
    },
    music_allowed=False,
    min_seconds=5,
    max_seconds=60,                 # the doc sets none; ATLAS's own ceiling
    audience=("mostly Tier 1-2 countries; a page with 30%+ Tier 3 audience may "
              "be removed"),
    rules_reviewed_on=REVIEWED_ON,
    budget_usd=15600.0, budget_read_at=BUDGET_READ, budget_source=BUDGET_SOURCE,
    permission_source=CLIPPING_CULTURE,
    max_accounts=1,                 # the doc names no limit; see ATLUS
    cross_post_allowed=False,       # the same "do not copy" rule as ATLUS
)

# Rules doc not yet known: recorded, paused, and never cut until its doc is
# parsed and reviewed (a code change, like the two above).
WARHAMMER = Campaign(
    slug="total-war-warhammer-3",
    name="Total War: Warhammer III",
    category="gaming",
    status="paused",
    platforms=("instagram", "tiktok", "youtube"),
    rates={"instagram": 2.00, "tiktok": 2.00, "youtube": 2.00},
    caption_rules={"samples": {}, "require": [], "forbid": [], "hashtags": []},
    onscreen_rules={"mode": "none", "lines": [], "forbid": []},
    budget_usd=7700.0, budget_read_at=BUDGET_READ, budget_source=BUDGET_SOURCE,
    permission_source=CLIPPING_CULTURE,
)


# A streamer's own Content Rewards program on Whop (Miro approved it
# 2026-09-29): she pays for clips of her own Twitch streams. There is no
# separate rules doc -- the campaign page's brief and facts, as Miro read
# them that day, ARE its rules, kept verbatim in rules/irubyana-twitch.txt;
# rules_doc_id names that page. The permission is the campaign itself.
IRUBYANA_URL = ("https://contentrewards.com/discover/"
                "d8f10d9f-5207-4268-8069-b6da45a23b67")
# Where the Mac submitter files each post: the Whop campaign Miro joined
# (2026-10-01, "make sure the irubyana clips are coming and never paused").
IRUBYANA_WHOP = "https://whop.com/rubyanaclips/exp_qOpYZgW2DMo0F9/app/"
IRUBYANA = Campaign(
    slug="irubyana-twitch",
    whop_url=IRUBYANA_WHOP,
    name="Irubyana Twitch Clips",
    category="streamer",
    status="active",
    rules_doc_id=IRUBYANA_URL,
    # Her own VODs, which Miro downloads into source/; never scraped.
    source_folder="https://www.twitch.tv/irubyana",
    # The page pays on TikTok, Instagram, YouTube and X; the lane posts no X.
    platforms=("instagram", "tiktok", "youtube"),
    rates={"instagram": 1.00, "tiktok": 1.00, "youtube": 1.00},
    tag_handles={"instagram": "@irubyana", "tiktok": "@irubyana",
                 "youtube": "@irubyana"},
    caption_rules={
        # "make sure to tag @Irubyana": the post text tags her itself, and
        # the handle line tags her again on every platform.
        "samples": {"general": "Best moments from @irubyana live on Twitch"},
        "require": [r"@irubyana\b"],
        "forbid": [],
        # "use hashtags like irubyana, irubyanaclips, twitch, twitchlive":
        # all four, on every clip (re-read 2026-10-01), so her layout
        # carries four besides #Ad where the lane's default is three.
        "hashtags": ["#irubyana", "#irubyanaclips", "#twitch", "#twitchlive"],
        "required_hashtags": ["#irubyana", "#irubyanaclips", "#twitch",
                              "#twitchlive"],
        "max_hashtags": 4,
        "default_sample": "general",
        "default_hashtags": ["#irubyana", "#irubyanaclips", "#twitch",
                             "#twitchlive"],
    },
    # Captions are burned in (the page recommends text overlays); no line
    # of ATLAS's own on screen.
    onscreen_rules={"mode": "none", "lines": [], "forbid": []},
    music_allowed=False,
    # "15-60 second clips". The lane's own floor is 20 s (cut.MIN_WINDOW),
    # so its clips run 20-60 s: inside her range.
    min_seconds=15,
    max_seconds=60,
    audience="",                    # the page states no region or audience
    rules_reviewed_on=date(2026, 10, 1),
    budget_usd=1971.0,
    budget_read_at=datetime(2026, 9, 29, 12, 0, tzinfo=UTC),
    budget_source=("Miro's reading of the Content Rewards campaign page "
                   "($1,971 of $2,000 left)"),
    permission_source=f"whop:{IRUBYANA_URL}",
    max_accounts=1,
    # blur: the whole 16:9 frame -- her facecam, the stream overlay and its
    # Twitch branding, which the page asks for -- over a blurred fill;
    # crop would keep a 405 px slice of a 720p frame, upscaled 2.7x.
    preset="blur",
    # "post on tiktok and instagram reels": her brief asks for both and
    # forbids nothing about the same clip on each (unlike ATLUS and COD),
    # and Miro asks for both (2026-10-01) -- so each clip goes to both.
    cross_post_allowed=True,
    # "include brand logo", "twitch banner in the background": her logo.png
    # and banner.png, placed by Miro in data/clips/irubyana-twitch/brand/.
    brand_assets=("logo", "banner"),
    # "Submit a draft for review; drafts need brand approval before you
    # post" (Whop, verified 2026-10-01): her clips are drafts first.
    approval_before_post=True,
    # Whop's draft upload refuses an MP4 over 256 MB.
    max_file_mb=256,
)


# The sha256 of each doc as it read when its fields above were reviewed.
# A doc file that no longer hashes to this is not the doc that was reviewed,
# and its campaign is not seeded from it.
REVIEWED_SHA256 = {
    "atlus-persona-metaphor":
        "19c5817e3002e685d46ca93f8f42ea158645738eaf39ffee85a885ffd5f07103",
    "cod-mw4-warzone-operator":
        "55c126174e19126c5e7cb154d21aade0436190b4be191c78317cf0bf5bbade66",
    "irubyana-twitch":
        "ad4d677012ed9fcff3a870148059902fca522d1c92b731378959a534d99bc8b1",
}


def seeds() -> tuple[Campaign, ...]:
    """The live campaigns, each with rules carrying its doc. Insert-only:
    a slug not yet on file is written on the next start; one on file is
    left as it is."""
    out = []
    for c in (ATLUS, COD, WARHAMMER, IRUBYANA):
        if c.rules_doc_id:
            text = _rules(c.slug)
            digest = rules_hash(text)
            if digest != REVIEWED_SHA256.get(c.slug):
                raise ValueError(f"rules/{c.slug}.txt is not the reviewed doc")
            c = _replace(c, rules_text=text, rules_sha256=digest)
        out.append(c)
    return tuple(out)


def _replace(c: Campaign, **changes: Any) -> Campaign:
    from dataclasses import replace
    return replace(c, **changes)


# ------------------------------------------------------------------- rows

COLUMNS = ("slug, name, category, status, whop_url, rules_doc_id, source_folder, "
           "platforms, rates, tag_handles, disclosure, caption_rules, "
           "onscreen_rules, music_allowed, min_seconds, max_seconds, audience, "
           "rules_text, rules_sha256, rules_reviewed_on, budget_usd, "
           "budget_read_at, budget_source, permission_source, max_accounts, "
           "preset, presets_allowed, cross_post_allowed, brand_assets, "
           "approval_before_post, max_file_mb")
# The fields read out of the rules doc, re-applied from the seed when the
# doc on file is not the one reviewed (`seed`): never status, whop_url,
# budget, permission or the format choices, which are Miro's.
DOC_FIELDS = ("platforms", "rates", "tag_handles", "disclosure", "caption_rules",
              "onscreen_rules", "music_allowed", "min_seconds", "max_seconds",
              "audience", "cross_post_allowed", "brand_assets", "rules_text",
              "rules_sha256", "rules_reviewed_on", "approval_before_post",
              "max_file_mb")


def _json(value: Any, empty: Any) -> Any:
    if value is None:
        return empty
    if isinstance(value, str | bytes):
        try:
            return json.loads(value)
        except ValueError:
            return empty
    return value


def from_row(row: Any) -> Campaign:
    r = dict(row)
    budget = r.get("budget_usd")
    return Campaign(
        slug=str(r["slug"]), name=str(r["name"]), category=str(r["category"]),
        status=str(r["status"]), whop_url=r.get("whop_url"),
        rules_doc_id=r.get("rules_doc_id"), source_folder=r.get("source_folder"),
        platforms=tuple(_json(r.get("platforms"), [])),
        rates={k: float(v) for k, v in _json(r.get("rates"), {}).items()},
        tag_handles=dict(_json(r.get("tag_handles"), {})),
        disclosure=str(r.get("disclosure") or "#Ad"),
        caption_rules=dict(_json(r.get("caption_rules"), {})),
        onscreen_rules=dict(_json(r.get("onscreen_rules"), {})),
        music_allowed=bool(r.get("music_allowed")),
        min_seconds=r.get("min_seconds"),
        max_seconds=int(r.get("max_seconds") or 60),
        audience=str(r.get("audience") or ""),
        rules_text=r.get("rules_text"), rules_sha256=r.get("rules_sha256"),
        rules_reviewed_on=r.get("rules_reviewed_on"),
        budget_usd=float(budget) if budget is not None else None,
        budget_read_at=r.get("budget_read_at"), budget_source=r.get("budget_source"),
        permission_source=r.get("permission_source"),
        max_accounts=int(r.get("max_accounts") or 1),
        preset=str(r.get("preset") or "blur"),
        presets_allowed=tuple(_json(r.get("presets_allowed"), list(PRESETS))),
        cross_post_allowed=bool(r.get("cross_post_allowed")),
        brand_assets=tuple(_json(r.get("brand_assets"), [])),
        approval_before_post=bool(r.get("approval_before_post")),
        max_file_mb=int(r.get("max_file_mb") or DEFAULT_MAX_FILE_MB),
    )


async def seed(campaign: Campaign) -> str | None:
    """Insert one campaign unless it is already on file. The content rule
    is asked first, then its permission and its format: a refused campaign
    is never written. Returns why it was refused, or None."""
    for check in (refusal, permission_refusal, format_refusal):
        if (why := check(campaign)) is not None:
            return why
    if campaign.status == "active" and (why := activation_refusal(campaign)):
        return why
    await execute_sql(
        f"""INSERT INTO clip_campaigns ({COLUMNS})
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,
                    $18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29,$30,$31)
            ON CONFLICT (slug) DO NOTHING""",
        campaign.slug, campaign.name, campaign.category, campaign.status,
        campaign.whop_url, campaign.rules_doc_id, campaign.source_folder,
        json.dumps(list(campaign.platforms)), json.dumps(campaign.rates),
        json.dumps(campaign.tag_handles), campaign.disclosure,
        json.dumps(campaign.caption_rules), json.dumps(campaign.onscreen_rules),
        campaign.music_allowed, campaign.min_seconds, campaign.max_seconds,
        campaign.audience, campaign.rules_text, campaign.rules_sha256,
        campaign.rules_reviewed_on, campaign.budget_usd, campaign.budget_read_at,
        campaign.budget_source, campaign.permission_source, campaign.max_accounts,
        campaign.preset, json.dumps(list(campaign.presets_allowed)),
        campaign.cross_post_allowed, json.dumps(list(campaign.brand_assets)),
        campaign.approval_before_post, int(campaign.max_file_mb))
    # A row seeded before permission_source existed gets its seed's, once;
    # a source already on file (or set by hand) is never overwritten.
    await execute_sql(
        """UPDATE clip_campaigns SET permission_source = $2, updated_at = now()
           WHERE slug = $1 AND permission_source IS NULL""",
        campaign.slug, campaign.permission_source)
    # A doc re-read and re-reviewed (a new sha256 in REVIEWED_SHA256): the
    # fields read out of it follow, on the row already on file. Miro's own
    # columns -- status, whop_url, budget, permission, format -- do not.
    await execute_sql(
        f"""UPDATE clip_campaigns
               SET {', '.join(f'{col} = ${n}' for n, col in enumerate(DOC_FIELDS, 3))},
                   updated_at = now()
             WHERE slug = $1 AND rules_sha256 IS DISTINCT FROM $2""",
        campaign.slug, campaign.rules_sha256,
        json.dumps(list(campaign.platforms)), json.dumps(campaign.rates),
        json.dumps(campaign.tag_handles), campaign.disclosure,
        json.dumps(campaign.caption_rules), json.dumps(campaign.onscreen_rules),
        campaign.music_allowed, campaign.min_seconds, campaign.max_seconds,
        campaign.audience, campaign.cross_post_allowed,
        json.dumps(list(campaign.brand_assets)), campaign.rules_text,
        campaign.rules_sha256, campaign.rules_reviewed_on,
        campaign.approval_before_post, int(campaign.max_file_mb))
    return None


# Miro, 2026-10-01 and again 2026-10-05: "make sure the irubyana clips are
# coming and never paused". A pause card he tapped at 14:55 UTC on 2026-10-01
# -- before he joined the campaign, when its clips met none of its rules --
# was still holding it on 2026-10-05. `ended` is left alone: a campaign that
# is over is not a pause.
KEEP_ACTIVE = ("irubyana-twitch",)


async def keep_active() -> list[str]:
    """Lift a pause off every KEEP_ACTIVE campaign that may be active (the
    same `activation_refusal` an approval is held to). Returns the slugs
    resumed."""
    resumed: list[str] = []
    for slug in KEEP_ACTIVE:
        campaign = await load(slug)
        if campaign is None or campaign.status != "paused":
            continue
        if activation_refusal(_replace(campaign, status="active")) is not None:
            continue
        await execute_sql(
            "UPDATE clip_campaigns SET status = 'active', updated_at = now() "
            "WHERE slug = $1 AND status = 'paused'", slug)
        resumed.append(slug)
    return resumed


async def ensure_seeds() -> dict[str, str]:
    """Every seed, once. Idempotent. {slug: why} for any the rule refused."""
    refused: dict[str, str] = {}
    for campaign in seeds():
        if (why := await seed(campaign)) is not None:
            refused[campaign.slug] = why
    return refused


async def load(slug: str) -> Campaign | None:
    row = await fetch_one(f"SELECT {COLUMNS} FROM clip_campaigns WHERE slug = $1",
                          slug)
    return from_row(row) if row is not None else None


async def load_all() -> list[Campaign]:
    rows = await fetch_all(f"SELECT {COLUMNS} FROM clip_campaigns ORDER BY slug")
    return [from_row(r) for r in rows]


# ------------------------------------------------ clips.set_campaign_status

def vet_status(params: dict[str, Any]) -> str | None:
    status = str(params.get("status") or "")
    if status not in STATUSES:
        return f"status must be one of {', '.join(STATUSES)}"
    return None


async def _status_change(slug: str, status: str) -> tuple[Campaign | None, str | None]:
    if (why := vet_status({"status": status})) is not None:
        return None, why
    campaign = await load(slug)
    if campaign is None:
        return None, f"no campaign {slug!r}"
    if status == "active" and (why := activation_refusal(campaign)):
        return None, f"refused: {why}"
    return campaign, None


async def set_status_dry(campaign: str, status: str) -> ActionResult:
    found, why = await _status_change(campaign, status)
    if found is None:
        return ActionResult(False, why or "refused")
    return ActionResult(True, f"would set {found.name} from {found.status} to {status}")


async def set_status(campaign: str, status: str) -> ActionResult:
    found, why = await _status_change(campaign, status)
    if found is None:
        return ActionResult(False, why or "refused")
    await execute_sql(
        "UPDATE clip_campaigns SET status = $2, updated_at = now() WHERE slug = $1",
        campaign, status)
    return ActionResult(True, f"{found.name}: {found.status} -> {status}",
                        rollback_token=json.dumps({"slug": campaign,
                                                   "status": found.status}))


async def set_status_rollback(token: str) -> ActionResult:
    data = json.loads(token)
    await execute_sql(
        "UPDATE clip_campaigns SET status = $2, updated_at = now() WHERE slug = $1",
        str(data["slug"]), str(data["status"]))
    return ActionResult(True, f"{data['slug']} back to {data['status']}")


# ------------------------------------------------------ clips.record_budget

async def record_budget_dry(campaign: str, usd: float) -> ActionResult:
    found = await load(campaign)
    if found is None:
        return ActionResult(False, f"no campaign {campaign!r}")
    if float(usd) < 0:
        return ActionResult(False, "a budget left cannot be negative")
    return ActionResult(True, f"would record ${float(usd):,.0f} left on {found.name}, "
                              f"read now, as Miro said it")


async def record_budget(campaign: str, usd: float) -> ActionResult:
    """Miro read the Whop dashboard and says what is left. Stamped with now
    and his name; ATLAS never estimates one."""
    found = await load(campaign)
    if found is None:
        return ActionResult(False, f"no campaign {campaign!r}")
    if float(usd) < 0:
        return ActionResult(False, "a budget left cannot be negative")
    now = datetime.now(UTC)
    await execute_sql(
        """UPDATE clip_campaigns SET budget_usd = $2, budget_read_at = $3,
                  budget_source = $4, updated_at = now() WHERE slug = $1""",
        campaign, float(usd), now, "Miro, from the Whop dashboard")
    prior = {"slug": campaign, "usd": found.budget_usd,
             "at": found.budget_read_at.isoformat() if found.budget_read_at else None,
             "source": found.budget_source}
    return ActionResult(True, f"{found.name}: ${float(usd):,.0f} left "
                              f"(read {now:%d %b %H:%M} UTC)",
                        rollback_token=json.dumps(prior))


async def record_budget_rollback(token: str) -> ActionResult:
    data = json.loads(token)
    at = datetime.fromisoformat(data["at"]) if data.get("at") else None
    await execute_sql(
        """UPDATE clip_campaigns SET budget_usd = $2, budget_read_at = $3,
                  budget_source = $4, updated_at = now() WHERE slug = $1""",
        str(data["slug"]), data.get("usd"), at, data.get("source"))
    return ActionResult(True, f"{data['slug']}: budget restored to what it was")
